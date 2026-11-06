"""Network policy and polite fetching for job_skill (runner and posting.py).

Usage:
    python netfetch.py check <url> [--method POST] [--root R] [--attended]
                             [--allow-host H ...] [--robots] [--offline DIR]

check prints ALLOW <host> (exit 0) or DENY <reason> (exit 7). Reasons:
job-board <domain>, r3-list <host>, non-public <host>, bad-url, not-https <host>,
host-not-allowed <host>, post-not-allowed <host>, robots <host>,
robots-unreachable <host>. The static rules need no network; --robots also
reads robots.txt (live, or from --offline fixtures).

What it enforces (spec 8.2, 3.3, 3.14):
- Public addresses only. Every address a name resolves to is checked and the
  socket connects to one of those same addresses (no DNS rebinding); every
  redirect is re-checked; with a proxy configured nothing is fetched.
- A hard deny for job boards (LinkedIn, Indeed, Glassdoor, SimplyHired,
  ZipRecruiter) and the R3 list endpoints, before every request and redirect,
  even when robots.txt cannot be read.
- Unattended runs: https only, and only allowlisted hosts (ATS and aggregator
  APIs, watch-list careers_url hosts, hosts a validated request named).
- robots.txt for our User-Agent and *, cached 24 h in state/robots_cache.json.
- At least max(1 s, Crawl-delay) between requests to one host, Retry-After on
  429/503, a 20 MB cap and a 20 s timeout.

Offline mode (tests and `--offline`): DIR/index.json maps "URL" or "POST URL"
to {"file": name, "status": 200, "headers": {}, "url": final_url} or to
{"error": "reason"}; a list of those is served in order (the last repeats).
A robots.txt with no entry counts as 404 (no rules).
Stdlib only.
"""
import argparse
import csv
import http.client
import ipaddress
import json
import re
import socket
import sys
import math
import threading
import time
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import (HTTPCookieProcessor, HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener, getproxies)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402  (needs the script folder on sys.path)

USER_AGENT = "JobSearchRunner/1.2 (personal job search; github.com/dumpstering/job_skill)"
UA_TOKEN = "jobsearchrunner"
TIMEOUT = 20
MAX_BYTES = 20_000_000
MIN_INTERVAL = 1.0
MAX_CRAWL_DELAY = 30.0    # a robots Crawl-delay above this (or not a finite number) means: do not crawl that host
MAX_RETRY_AFTER = 120
ROBOTS_TTL = 24 * 3600
ROBOTS_MAX = 512_000
EXIT_DENY = 7


class FetchError(Exception):
    """Nothing usable from this route; the message is the reason. .status is
    the HTTP status when there was one."""

    status = None


class Denied(FetchError):
    """Refused by policy before any request; .reason is the DENY reason."""

    def __init__(self, reason):
        super().__init__(f"denied: {reason}")
        self.reason = reason


# ---------- public addresses only (v1.1 posting.py, unchanged in meaning) ----------

# A pasted posting link, or a page it redirects to, must never reach into the
# user's machine or network. check_public refuses odd URLs up front; the real
# guard is connect_public, which checks every address a name resolves to and
# connects to one of those same addresses, so a second DNS answer (rebinding)
# cannot steer the socket. With a proxy configured nothing is fetched at all,
# because the proxy would resolve the name where it cannot be checked; the
# agent then fetches with its own tool (exit 3, TRY).

def check_public(url):
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        raise FetchError(f"refused {url[:80]}: only public http(s) links are fetched")
    if any(ch in u.netloc for ch in "@%\\"):
        raise FetchError(f"refused {url[:80]}: unusual characters in the host part")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise FetchError(f"refused {host}: not a public site")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if not ip.is_global:
        raise FetchError(f"refused {host}: not a public address")


def connect_public(host, port, timeout):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"cannot resolve {host}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise FetchError(f"refused {host}: resolves to a non-public address ({ip})")
    last = None
    for family, socktype, proto, _, addr in infos:
        s = socket.socket(family, socktype, proto)
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            s.settimeout(timeout)
        try:
            s.connect(addr)
            return s
        except OSError as e:
            last = e
            s.close()
    raise last or OSError(f"cannot connect to {host}")


class PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = connect_public(self.host, self.port, self.timeout)


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = connect_public(self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class PublicHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(PublicHTTPConnection, req)


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPSConnection, req, context=self._context)


class PublicRedirects(HTTPRedirectHandler):
    """Re-checks every redirect: public address and the hard deny list."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        reason = hard_deny(newurl)
        if reason:
            raise Denied(reason)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = build_opener(ProxyHandler({}), PublicHTTPHandler, PublicHTTPSHandler, PublicRedirects)


# ---------- host policy ----------

def on_domain(host, domain):
    return host == domain or host.endswith("." + domain)


def host_of(url):
    return (urlparse(url).hostname or "").lower().rstrip(".")


def idna_host(host):
    """The ASCII name the socket layer resolves for host: http.client spells a
    non-ASCII name with the IDNA codec, which drops soft hyphens and maps
    fullwidth letters, sharp s and ideographic dots ('www.link<soft hyphen>edin.com'
    reaches www.linkedin.com). '' when the name has no such spelling."""
    host = (host or "").lower().rstrip(".")
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, ValueError):
        return ""


BOARD_NAMES = ("linkedin", "indeed", "glassdoor", "simplyhired", "ziprecruiter")
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov"}


def job_board(host):
    """The job-board domain a host belongs to (linkedin.com, uk.indeed.com ->
    indeed.com, indeed.co.uk), else None. These are never fetched (R2). A
    non-ASCII spelling is judged by the name it resolves to (idna_host)."""
    labels = (idna_host(host) or (host or "").lower().rstrip(".")).split(".")
    for i, label in enumerate(labels[:-1]):
        if label not in BOARD_NAMES:
            continue
        rest = labels[i + 1:]
        if len(rest) == 1 and 2 <= len(rest[0]) <= 3:
            return ".".join(labels[i:])
        if len(rest) == 2 and rest[0] in SECOND_LEVEL and len(rest[1]) == 2:
            return ".".join(labels[i:])
    return None


def r3_list(host, path):
    """True for the R3 list endpoints: never called, whatever robots says (3.3)."""
    path_l = (path or "/").lower()
    if host == "api.smartrecruiters.com" or on_domain(host, "recruitingbypaycor.com"):
        return True
    if "/jobboardview" in path_l:  # UKG search (its robots: Disallow */JobBoardView)
        return True
    if on_domain(host, "icims.com") and path_l.startswith("/jobs/search"):
        return True
    if re.fullmatch(r"portal-applicant-tracking\.[a-z0-9-]+\.paycomonline\.net", host) and "search" in path_l:
        return True
    return host == "careers-page.clearcompany.com" and path_l.startswith("/api/")


def hard_deny(url):
    """The DENY reason that holds for every fetch, attended or not, else None.
    A host spelled with anything but ASCII is refused (host-not-ascii): the
    policy checks ASCII names, and a lookalike spelling resolves elsewhere."""
    raw = host_of(url)
    host = idna_host(raw)
    board = job_board(host)
    if board:
        return f"job-board {board}"
    if raw and not raw.isascii():
        return "host-not-ascii " + raw.encode("ascii", "backslashreplace").decode("ascii")
    if r3_list(host, urlparse(url).path):
        return f"r3-list {host}"
    return None


# Hosts the unattended runner may call: the ATS endpoints in 3.2, the single-
# posting hosts reached through leads, and the aggregators in 3.4. "*." means
# any subdomain.
ALLOW_HOSTS = (
    "boards-api.greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io",
    "api.lever.co", "api.eu.lever.co", "jobs.lever.co", "jobs.eu.lever.co",
    "api.ashbyhq.com", "jobs.ashbyhq.com",
    "*.myworkdayjobs.com", "*.myworkdaysite.com",
    "*.bamboohr.com", "*.breezy.hr", "apply.workable.com", "api.rippling.com", "ats.rippling.com",
    "*.pinpointhq.com", "*.teamtailor.com", "*.jobs.personio.de", "*.jobs.personio.com", "*.recruitee.com",
    "workforcenow.adp.com", "myjobs.adp.com", "*.oraclecloud.com", "recruiting.paylocity.com",
    "*.dayforcehcm.com", "jobs.jobvite.com", "*.applytojob.com",
    "www.comeet.com", "www.careers-page.com", "api.hireology.com", "careers.hireology.com",
    "jobs.smartrecruiters.com", "careers.smartrecruiters.com",
    "*.icims.com", "recruiting.ultipro.com", "recruiting2.ultipro.com", "*.paycomonline.net",
    "data.usajobs.gov", "www.usajobs.gov", "api.adzuna.com", "jobs.workable.com", "www.themuse.com",
)
# Hosts that take a POST (Workday CXS lists; Dayforce geo search).
POST_HOSTS = ("*.myworkdayjobs.com", "*.myworkdaysite.com", "*.dayforcehcm.com")


def host_matches(host, patterns):
    for p in patterns:
        if p.startswith("*."):
            if host.endswith(p[1:]):
                return True
        elif host == p:
            return True
    return False


def watchlist_hosts(root):
    """careers_url hosts in config/watchlist.csv and state/watchlist_auto.csv."""
    folder = jobfs.Folder(root)
    hosts = set()
    for path in (folder.watchlist_csv, folder.watchlist_auto_csv):
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    h = host_of((row.get("careers_url") or "").strip())
                    if h:
                        hosts.add(h)
        except OSError:
            continue
    return hosts


class Policy:
    """Which URLs may be fetched. unattended adds https-only and the allowlist."""

    def __init__(self, root=None, unattended=True, extra_hosts=()):
        self.unattended = unattended
        self.extra = {h.lower() for h in extra_hosts}
        if root is not None:
            self.extra |= watchlist_hosts(root)

    def allow_host(self, host):
        """A host a validated request named (spec 8.2)."""
        self.extra.add(host.lower())

    def reason(self, url, method="GET"):
        """The DENY reason for url, or None when it may be fetched."""
        try:
            check_public(url)
        except FetchError:
            host = host_of(url)
            u = urlparse(url)
            if u.scheme in ("http", "https") and host and not any(ch in u.netloc for ch in "@%\\"):
                return hard_deny(url) or f"non-public {host}"
            return "bad-url"
        deny = hard_deny(url)
        if deny or not self.unattended:
            return deny
        host = host_of(url)
        if urlparse(url).scheme != "https":
            return f"not-https {host}"
        if not (host_matches(host, ALLOW_HOSTS) or host in self.extra):
            return f"host-not-allowed {host}"
        if method.upper() == "POST" and not host_matches(host, POST_HOSTS):
            return f"post-not-allowed {host}"
        return None


# ---------- robots.txt (RFC 9309) ----------
# urllib.robotparser matches path prefixes only, so it cannot read UKG's
# "Allow: */JobBoard/"; this follows RFC 9309: the group for our token, else *,
# the longest matching rule wins, Allow wins a tie, and * and $ are wildcards.

def _rule_regex(path):
    anchored = path.endswith("$")
    body = re.escape(path[:-1] if anchored else path).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))


class RobotRules:
    def __init__(self, text=""):
        self.groups = []  # (agents, rules, crawl_delay)
        self.signals = []
        self._parse(text or "")

    def _parse(self, text):
        agents, rules, delay, last_was_agent = [], [], None, False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "content-signal":
                self.signals.append(value)
            elif key == "user-agent":
                if not last_was_agent and agents:
                    self.groups.append((agents, rules, delay))
                    agents, rules, delay = [], [], None
                agents.append(value.lower())
                last_was_agent = True
                continue
            elif key in ("allow", "disallow") and agents:
                if value:
                    rules.append((len(value), key == "allow", _rule_regex(value)))
            elif key == "crawl-delay" and agents:
                try:
                    delay = float(value)
                except ValueError:
                    pass
                else:  # 'inf', 'nan' or negative: no usable delay, so the host is not crawled
                    if not math.isfinite(delay) or delay < 0:
                        delay = float("inf")
            last_was_agent = False
        if agents:
            self.groups.append((agents, rules, delay))

    def _group(self):
        for want in (UA_TOKEN, "*"):
            hits = [g for g in self.groups if want in g[0]]
            if hits:
                rules = [r for g in hits for r in g[1]]
                delays = [g[2] for g in hits if g[2] is not None]
                return rules, (max(delays) if delays else None)
        return [], None

    def allowed(self, path):
        rules, _ = self._group()
        best = None
        for length, allow, rx in rules:
            if rx.match(path) and (best is None or length > best[0] or (length == best[0] and allow)):
                best = (length, allow)
        return True if best is None else best[1]

    def crawl_delay(self):
        return self._group()[1]


class Robots:
    """robots.txt per host, cached for 24 h in cache_path (when given).

    status per host: rules (a file was read), none (4xx: no policy readable,
    as on api.ashbyhq.com's 401), unreachable (5xx or no answer: deny for now).
    """

    def __init__(self, fetch_text, cache_path=None, ttl=ROBOTS_TTL, clock=time.time):
        self.fetch_text = fetch_text  # url -> (status, text); raises FetchError
        self.cache_path = Path(cache_path) if cache_path else None
        self.ttl, self.clock = ttl, clock
        self.mem = {}
        self._loaded = False

    def _load(self):
        if self._loaded or not self.cache_path:
            self._loaded = True
            return
        self._loaded = True
        try:
            data = jobfs.read_json_verified(self.cache_path, retries=0)
            self.mem.update(data.get("hosts") or {})
        except jobfs.NotReady:
            pass

    def _save(self):
        if self.cache_path:
            fresh = {h: e for h, e in self.mem.items() if e.get("status") != "unreachable"}
            jobfs.atomic_write_json(self.cache_path, {"v": 1, "hosts": fresh})

    def entry(self, scheme, host):
        self._load()
        e = self.mem.get(host)
        if e and self.clock() - e.get("at", 0) < self.ttl:
            return e
        try:
            status, text = self.fetch_text(f"{scheme}://{host}/robots.txt")
        except Denied as d:
            # A robots.txt that redirects off the allowlist or to http (ADP:
            # workforcenow -> online.adp.com, a login page) has no readable
            # policy: same as a 4xx. The hard deny and non-public stay refusals.
            if not str(d.reason).startswith(("host-not-allowed ", "not-https ")):
                raise
            status, text = 404, ""
        except FetchError:
            status, text = None, ""
        if status is not None and 200 <= status < 300:
            e = {"at": self.clock(), "status": "rules", "text": (text or "")[:ROBOTS_MAX]}
        elif status is not None and 400 <= status < 500:
            e = {"at": self.clock(), "status": "none", "text": ""}
        else:
            e = {"at": self.clock(), "status": "unreachable", "text": ""}
        e["signals"] = RobotRules(e["text"]).signals
        self.mem[host] = e
        if e["status"] != "unreachable":
            self._save()
        return e

    def check(self, url):
        """(allowed, crawl_delay, reason). reason is robots <host> or
        robots-unreachable <host> when not allowed."""
        u = urlparse(url)
        host = (u.hostname or "").lower()
        e = self.entry(u.scheme or "https", host)
        if e["status"] == "unreachable":
            return False, None, f"robots-unreachable {host}"
        if e["status"] == "none":
            return True, None, None
        rules = RobotRules(e["text"])
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        delay = rules.crawl_delay()
        if not rules.allowed(path):
            return False, delay, f"robots {host}"
        if delay is not None and delay > MAX_CRAWL_DELAY:
            return False, delay, f"robots {host}"  # one request a minute or slower: leave the host alone
        return True, delay, None


# ---------- politeness ----------

class Throttle:
    """At least max(MIN_INTERVAL, crawl_delay) seconds between requests per host."""

    def __init__(self, min_interval=MIN_INTERVAL, clock=time.monotonic, sleep=time.sleep, enabled=True):
        self.min_interval, self.clock, self.sleep, self.enabled = min_interval, clock, sleep, enabled
        self.last = {}

    def wait(self, host, crawl_delay=None):
        if not self.enabled:
            return 0.0
        gap = max(self.min_interval, min(crawl_delay or 0, MAX_CRAWL_DELAY))
        waited = 0.0
        if host in self.last:
            waited = max(0.0, self.last[host] + gap - self.clock())
            if waited:
                self.sleep(waited)
        self.last[host] = self.clock()
        return waited


def retry_after_seconds(value, now=None):
    """Seconds from a Retry-After header (delta-seconds or an HTTP date), else None."""
    if value is None:
        return None
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    now = now or jobfs.utcnow()
    return max(0, int((when - now).total_seconds()))


# ---------- responses and the fetcher ----------

class Response:
    def __init__(self, url, status, headers, body, truncated=False):
        self.url, self.status, self.body, self.truncated = url, status, body, truncated
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    @property
    def ok(self):
        return 200 <= self.status < 300

    def text(self):
        m = re.search(r"charset=([\w.-]+)", self.headers.get("content-type", ""), re.I)
        try:
            return self.body.decode(m.group(1) if m else "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text(), strict=False)

    def check(self):
        """Raise FetchError (with .status) unless 2xx; returns self."""
        if not self.ok:
            e = FetchError(f"HTTP {self.status} from {host_of(self.url)}")
            e.status = self.status
            raise e
        return self


def read_capped(fp, limit=MAX_BYTES):
    raw = fp.read(limit + 1) if fp is not None else b""
    return (raw[:limit], True) if len(raw) > limit else (raw, False)


class OfflineSource:
    """Serves DIR/index.json entries instead of the network (see module doc)."""

    def __init__(self, folder):
        self.folder = Path(folder)
        try:
            self.index = json.loads((self.folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.index = {}
        self.served = {}
        self.requests = []  # (method, url) in order, for tests

    def respond(self, method, url, data=None):
        self.requests.append((method, url))
        key = url if method == "GET" else f"{method} {url}"
        entry = self.index.get(key)
        if entry is None and method == "GET" and url.endswith("/robots.txt"):
            entry = {"status": 404}
        if entry is None:
            raise FetchError(f"no offline fixture for {method} {url}")
        if isinstance(entry, list):
            i = self.served.get(key, 0)
            self.served[key] = i + 1
            entry = entry[min(i, len(entry) - 1)]
        if isinstance(entry, str):
            entry = {"file": entry}
        if "error" in entry:
            raise FetchError(entry["error"])
        body = b""
        if entry.get("file"):
            with open(self.folder / entry["file"], "rb") as f:
                body, truncated = read_capped(f)
        else:
            truncated = False
        return Response(entry.get("url", url), int(entry.get("status", 200)), entry.get("headers") or {},
                        body, truncated)


class PolicyRedirects(PublicRedirects):
    """Redirects re-run the whole gate (policy and robots) for the new URL."""

    def __init__(self, gate):
        super().__init__()
        self.gate = gate

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        self.gate(newurl, "GET" if code in (301, 302, 303) else req.get_method())
        return new


class Fetcher:
    """Polite, policy-checked HTTP for the runner.

    f = Fetcher(root=R)              # unattended: allowlist, https, robots, cache
    r = f.get(url); r.check().json()
    r = f.post_json(url, {"limit": 20})
    Fetcher(offline=DIR) serves fixtures; Fetcher(unattended=False) is the
    attended mode (hard deny and public addresses only).
    """

    def __init__(self, root=None, policy=None, robots=True, offline=None, unattended=True, cookies=False,
                 user_agent=USER_AGENT, throttle=None, clock=time.time, sleep=time.sleep, extra_hosts=()):
        self.policy = policy or Policy(root, unattended=unattended, extra_hosts=extra_hosts)
        self.offline = OfflineSource(offline) if offline else None
        self.user_agent = user_agent
        self.sleep = sleep
        self.throttle = throttle or Throttle(enabled=not offline, sleep=sleep)
        self.cookies = CookieJar() if cookies else None
        handlers = [ProxyHandler({}), PublicHTTPHandler, PublicHTTPSHandler, PolicyRedirects(self._gate)]
        if self.cookies is not None:
            handlers.append(HTTPCookieProcessor(self.cookies))
        self.opener = build_opener(*handlers)
        cache = jobfs.Folder(root).robots_cache_json if root is not None else None
        self.robots = Robots(self._robots_text, cache, clock=clock) if robots else None
        self.log = []  # (method, url, status) per request made
        self._in_robots = threading.local()  # set while this thread fetches a robots.txt

    def _gate(self, url, method="GET"):
        """Policy, then robots; returns the crawl delay. Raises Denied."""
        reason = self.policy.reason(url, method)
        if reason:
            raise Denied(reason)
        if self.robots is None or urlparse(url).path == "/robots.txt" or getattr(self._in_robots, "on", False):
            # A redirect hop of a robots.txt fetch is that fetch: its robots
            # check would fetch robots.txt again, without end (W bug 1).
            return None
        ok, delay, why = self.robots.check(url)
        if not ok:
            raise Denied(why)
        return delay

    def _robots_text(self, url):
        self._in_robots.on = True
        try:
            r = self._send("GET", url, None, {"Accept": "text/plain,*/*;q=0.5"}, None)
        finally:
            self._in_robots.on = False
        return r.status, r.text()

    def _send(self, method, url, data, headers, crawl_delay):
        host = host_of(url)
        self.throttle.wait(host, crawl_delay)
        if self.offline:
            r = self.offline.respond(method, url, data)
            self.log.append((method, url, r.status))
            return r
        if getproxies():
            raise FetchError("a web proxy is configured, so this script does not fetch directly")
        hdrs = {"User-Agent": self.user_agent, "Accept-Language": "en-US,en;q=0.9"}
        hdrs.update(headers or {})
        req = Request(url, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as resp:
                body, truncated = read_capped(resp)
                r = Response(resp.geturl(), resp.status, dict(resp.headers), body, truncated)
        except HTTPError as e:
            body, truncated = read_capped(e)
            r = Response(e.geturl() or url, e.code, dict(e.headers or {}), body, truncated)
        except Denied:
            raise
        except (OSError, ValueError, http.client.HTTPException) as e:
            raise FetchError(f"{host}: {e}")
        self.log.append((method, url, r.status))
        return r

    def request(self, method, url, data=None, headers=None):
        """One request through the gate; a 429/503 with Retry-After up to 120 s
        waits and retries once. Returns a Response for any HTTP status."""
        method = method.upper()
        delay = self._gate(url, method)
        r = self._send(method, url, data, headers, delay)
        if r.status in (429, 503):
            wait = retry_after_seconds(r.headers.get("retry-after"))
            if wait is not None and wait <= MAX_RETRY_AFTER:
                self.sleep(wait)
                r = self._send(method, url, data, headers, delay)
        return r

    def get(self, url, accept=None, headers=None):
        h = {"Accept": accept or "application/json,text/html;q=0.9,*/*;q=0.8"}
        h.update(headers or {})
        return self.request("GET", url, None, h)

    def post_json(self, url, obj, headers=None):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        h.update(headers or {})
        return self.request("POST", url, json.dumps(obj).encode("utf-8"), h)


# ---------- CLI ----------

class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        sys.exit(1)


def cmd_check(args):
    if "://" not in args.url:
        print("ERROR url must start with http:// or https://")
        return 1
    policy = Policy(args.root, unattended=not args.attended, extra_hosts=args.allow_host or ())
    reason = policy.reason(args.url, args.method)
    if not reason and args.robots:
        f = Fetcher(root=args.root, policy=policy, offline=args.offline)
        try:
            f._gate(args.url, args.method)
        except Denied as e:
            reason = e.reason
    if reason:
        print(f"DENY {reason}")
        return EXIT_DENY
    print(f"ALLOW {host_of(args.url)}")
    return 0


def main(argv=None):
    ap = Parser(description="Network policy check for job_skill.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="ALLOW or DENY a URL under the runner policy")
    c.add_argument("url")
    c.add_argument("--method", default="GET", type=str.upper, choices=("GET", "POST"))
    c.add_argument("--root", help="JobSearch folder: adds watch-list careers_url hosts, robots cache")
    c.add_argument("--attended", action="store_true", help="a pasted link: hard deny and public only")
    c.add_argument("--allow-host", action="append", help="a host a validated request named")
    c.add_argument("--robots", action="store_true", help="also read robots.txt for the host")
    c.add_argument("--offline", help="fixture folder with index.json instead of the network")
    args = ap.parse_args(argv)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
