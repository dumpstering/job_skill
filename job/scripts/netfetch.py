"""Network policy and polite fetching for job_skill (runner and posting.py).

Usage:
    python netfetch.py check <url> [--method POST] [--root R] [--attended]
                             [--allow-host H ...] [--robots] [--offline DIR]

check prints ALLOW <host> (exit 0) or DENY <reason> (exit 7). Reasons:
job-board <domain>, r3-list <host>, non-public <host>, bad-url [<detail>],
not-https <host>, host-not-allowed <host>, post-not-allowed <host>,
robots <host>, robots-unreachable <host>, crawl-delay <host>. The static rules
need no network; --robots also reads robots.txt (live, or from --offline
fixtures).

What it enforces (spec 8.2, 3.3, 3.14):
- One refusal for links (web_refusal, R1): every check that decides whether
  a link may be stored, shown, opened or fetched asks it. It answers None or
  job-board <domain>, bad-url <why> (no http(s) link, a backslash, space or
  control character, userinfo, a port other than 80/443, a host canon_host
  refuses, an IP literal in any form, a single-label host) or non-public
  <host> (localhost, .local, .internal, .lan, .home.arpa, a private IP, or
  at connect time a name that resolves to a non-public address).
- One canonical host (host_of, canon_host): urlparse's hostname, lowercased,
  one trailing dot removed. A host with any character outside a-z 0-9 . -,
  an empty label or a label over 63 characters is refused (bad-url), so a
  spelling the socket layer would map to another name (a soft hyphen, a
  fullwidth letter, an ideographic dot) never reaches a check or a socket.
- Public addresses only. Every address a name resolves to is checked and the
  socket connects to one of those same addresses (no DNS rebinding); every
  redirect is re-checked; with a proxy configured nothing is fetched. The
  socket layer re-checks the name it resolves (canonical, not a job board).
- A hard deny for job boards (any host with a label linkedin, indeed,
  glassdoor, simplyhired, ziprecruiter or remotive, whatever the top-level
  domain, plus lnkd.in and licdn.com) and the R3 list endpoints, before every
  request and redirect, even when robots.txt cannot be read. Paths are
  compared after decoding percent-escaped unreserved characters (RFC 9309
  2.2.2); unattended, a path still holding %2F or %5C is refused.
- Unattended runs: https only, and only allowlisted hosts (ATS and aggregator
  APIs, watch-list careers_url hosts, hosts a validated request named).
- robots.txt for our User-Agent and *, cached 24 h in state/robots_cache.json.
  Rules over 256 characters or with more than 10 wildcards are ignored, and
  matching never backtracks.
- At least max(1 s, Crawl-delay) between requests to one host. A Crawl-delay
  that is not a finite number from 0 to 10 s means the host is skipped for
  the run (crawl-delay <host>). Retry-After on 429/503 up to 120 s, a 20 MB
  cap, a 20 s timeout per socket read and 60 s wall-clock per fetch.

Offline mode (tests and `--offline`): DIR/index.json maps "URL" or "POST URL"
to {"file": name, "status": 200, "headers": {}, "url": final_url} or to
{"error": "reason"}; a list of those is served in order (the last repeats).
A robots.txt with no entry counts as 404 (no rules).
Stdlib only.
"""
import argparse
import contextlib
import csv
import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import sys
import threading
import time
import unicodedata
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import (HTTPCookieProcessor, HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener, getproxies)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402  (needs the script folder on sys.path)

USER_AGENT = "JobSearchRunner/1.2 (personal job search; github.com/dumpstering/job_skill)"
UA_TOKEN = "jobsearchrunner"
TIMEOUT = 20              # per socket read
FETCH_DEADLINE = 60.0     # wall-clock seconds for one whole fetch (connect, headers, body)
MAX_BYTES = 20_000_000
MIN_INTERVAL = 1.0
MAX_CRAWL_DELAY = 10.0    # a robots Crawl-delay above this (or not a finite number) means: skip that host this run
MAX_RETRY_AFTER = 120
ROBOTS_TTL = 24 * 3600
ROBOTS_MAX = 512_000
ROBOTS_RULE_MAX = 256     # longer robots rules are ignored
ROBOTS_WILDCARDS = 10     # rules with more wildcards are ignored
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
    """Raise Denied (a FetchError) with web_refusal's reason for a link that
    may not be fetched; the static half of the guard (connect_public is the
    other)."""
    reason = web_refusal(url)
    if reason:
        raise Denied(reason)


# One wall-clock deadline per fetch (FETCH_DEADLINE): a server that answers one
# byte every 19 s never trips the 20 s socket timeout, so every socket read
# (headers and body, TLS included) waits at most until the deadline. A
# Fetcher sets one deadline for the whole request, redirects included; any
# other connection made through these handlers gets its own.
_deadline = threading.local()


@contextlib.contextmanager
def fetch_deadline(seconds=None):
    """Every socket read inside this block shares one deadline (nested blocks
    keep the outer one)."""
    outer = getattr(_deadline, "at", None)
    if outer is None:
        _deadline.at = time.monotonic() + (FETCH_DEADLINE if seconds is None else seconds)
    try:
        yield
    finally:
        if outer is None:
            _deadline.at = None


def check_deadline():
    at = getattr(_deadline, "at", None)
    if at is not None and time.monotonic() >= at:
        raise TimeoutError(f"no complete answer within {FETCH_DEADLINE:.0f} s")


class _DeadlineReads:
    """Arms each socket read with min(socket timeout, time left)."""

    deadline = None
    base_timeout = None

    def _arm(self):
        if self.deadline is None:
            return
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"no complete answer within {FETCH_DEADLINE:.0f} s")
        self.settimeout(left if self.base_timeout is None else min(self.base_timeout, left))

    def _late(self):
        if self.deadline is not None and time.monotonic() >= self.deadline - 0.05:  # a timer may fire a tick early
            return TimeoutError(f"no complete answer within {FETCH_DEADLINE:.0f} s")
        return None

    def recv(self, *args, **kwargs):
        self._arm()
        try:
            return super().recv(*args, **kwargs)
        except TimeoutError:
            raise self._late() or TimeoutError("timed out") from None

    def recv_into(self, *args, **kwargs):
        self._arm()
        try:
            return super().recv_into(*args, **kwargs)
        except TimeoutError:
            raise self._late() or TimeoutError("timed out") from None


class DeadlineSocket(_DeadlineReads, socket.socket):
    pass


class DeadlineSSLSocket(_DeadlineReads, ssl.SSLSocket):
    pass


def connect_public(host, port, timeout):
    # The name the socket resolves is checked again here: the same canonical
    # form and job-board test as the policy, whatever parsed the URL upstream.
    name = canon_host(host)
    board = job_board(name)
    if board:
        raise Denied(f"job-board {board}")
    deadline = getattr(_deadline, "at", None) or time.monotonic() + FETCH_DEADLINE
    base = None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"cannot resolve {name}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            # a refusal, not a fetch failure (R1): posting.py exits 4 and prints no TRY
            raise Denied(f"non-public {name}")
    last = None
    for family, socktype, proto, _, addr in infos:
        s = DeadlineSocket(family, socktype, proto)
        s.deadline, s.base_timeout = deadline, base
        left = deadline - time.monotonic()
        if left <= 0:
            s.close()
            raise TimeoutError(f"no complete answer within {FETCH_DEADLINE:.0f} s")
        s.settimeout(left if base is None else min(base, left))
        try:
            s.connect(addr)
            return s
        except OSError as e:
            last = e
            s.close()
    raise last or OSError(f"cannot connect to {name}")


class PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = connect_public(self.host, self.port, self.timeout)


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = connect_public(self.host, self.port, self.timeout)
        ctx = self._context  # the handler's own context (made per handler), so this class choice stays ours
        if ctx.sslsocket_class is not DeadlineSSLSocket:
            ctx.sslsocket_class = DeadlineSSLSocket
        ssock = ctx.wrap_socket(sock, server_hostname=self.host)
        ssock.deadline = getattr(sock, "deadline", None)
        ssock.base_timeout = getattr(sock, "base_timeout", None)
        self.sock = ssock


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


HOST_CHARS = re.compile(r"[a-z0-9.-]+")


def shown(text, limit=80):
    """A host or URL as it may appear in a reason or log line: ASCII only."""
    return str(text)[:limit].encode("ascii", "backslashreplace").decode("ascii")


def canon_host(host):
    """The one host form every allow and deny decision uses: lowercase, one
    trailing dot removed, only a-z 0-9 . - (so a soft hyphen, a sharp s, a
    fullwidth letter or an ideographic dot, which the socket layer would map
    to another name, is refused), no empty label, no label over 63 characters.
    Raises Denied('bad-url ...') for anything else."""
    h = str(host or "").lower()
    if h.endswith("."):
        h = h[:-1]
    if not h or len(h) > 253 or not HOST_CHARS.fullmatch(h):
        raise Denied(f"bad-url host {shown(host)}")
    labels = h.split(".")
    if any(not label or len(label) > 63 for label in labels):
        raise Denied(f"bad-url host {shown(host)}")
    return h


def host_of(url):
    """The canonical host of url (canon_host of urlparse's hostname); '' when
    the URL names no host at all. Raises Denied('bad-url ...') for a host it
    refuses, or a URL urlparse cannot read."""
    try:
        raw = urlparse(str(url or "")).hostname
    except ValueError:
        raise Denied(f"bad-url {shown(url)}")
    return canon_host(raw) if raw else ""


def host_or_blank(url):
    """host_of for display and grouping only: '' instead of raising. Never use
    it for an allow or deny decision."""
    try:
        return host_of(url)
    except Denied:
        return ""


def idna_host(host):
    """The ASCII name the socket layer would resolve for a non-ASCII host
    (http.client spells it with the IDNA codec, which drops soft hyphens and
    maps fullwidth letters, sharp s and ideographic dots). Used only to name
    the job board such a spelling points at; the host itself is refused.
    '' when the name has no such spelling."""
    host = (host or "").lower().rstrip(".")
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, ValueError):
        return ""


BOARD_NAMES = ("linkedin", "indeed", "glassdoor", "simplyhired", "ziprecruiter", "remotive")
BOARD_DOMAINS = ("lnkd.in", "licdn.com")  # LinkedIn's link shortener and media host


def job_board(host):
    """The job-board domain a host belongs to, else None. These are never
    fetched (R2). A host with a label linkedin, indeed, glassdoor, simplyhired,
    ziprecruiter or remotive is one whatever follows it (www.linkedin.com ->
    linkedin.com, uk.indeed.com -> indeed.com, indeed.jobs, indeed.co.uk), and
    so is lnkd.in or licdn.com. A spelling the socket layer would map (a soft
    hyphen, fullwidth letters) is judged by the name it maps to."""
    raw = str(host or "")
    try:
        h = canon_host(raw)
    except Denied:
        h = idna_host(raw) or raw.lower().rstrip(".")
    for domain in BOARD_DOMAINS:
        if on_domain(h, domain):
            return domain
    labels = h.split(".")
    for i, label in enumerate(labels):
        if label in BOARD_NAMES:
            return ".".join(labels[i:])
    return None


UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
PCT = re.compile(r"%([0-9A-Fa-f]{2})")


def norm_path(path):
    """A URL path (and query) as RFC 9309 2.2.2 compares it: percent-escaped
    unreserved characters decoded (%4A -> J), other escapes in upper case,
    non-ASCII characters percent-encoded as UTF-8. One pass, so %2541 stays."""
    def one(m):
        ch = chr(int(m.group(1), 16))
        return ch if ch in UNRESERVED else "%" + m.group(1).upper()
    s = PCT.sub(one, str(path or ""))
    if not s.isascii():
        s = "".join(c if c.isascii() else "".join(f"%{b:02X}" for b in c.encode("utf-8", "replace")) for c in s)
    return s


def dot_free(path):
    """A path with repeated slashes collapsed and . and .. segments resolved,
    as a server would read it (for the R3 list test only)."""
    out = []
    for seg in re.sub(r"/{2,}", "/", path or "/").split("/"):
        if seg == ".":
            continue
        if seg == "..":
            if len(out) > 1:
                out.pop()
            continue
        out.append(seg)
    return "/".join(out) or "/"


def r3_list(host, path):
    """True for the R3 list endpoints: never called, whatever robots says (3.3).
    The path is compared decoded (norm_path, and an encoded slash read as a
    slash, since a server may decode it) with dot segments resolved."""
    path_l = dot_free(re.sub(r"%(?:2f|5c)", "/", norm_path(path or "/").lower()))
    if host == "api.smartrecruiters.com" or on_domain(host, "recruitingbypaycor.com"):
        return True
    if "/jobboardview" in path_l:  # UKG search (its robots: Disallow */JobBoardView)
        return True
    if on_domain(host, "icims.com") and re.match(r"/jobs(?:;[^/]{0,200})?/search", path_l):
        return True
    if re.fullmatch(r"portal-applicant-tracking\.[a-z0-9-]+\.paycomonline\.net", host) and "search" in path_l:
        return True
    return host == "careers-page.clearcompany.com" and path_l.startswith("/api/")


# ---------- the one link refusal (R1) ----------

LINK_MAX = 2048
WEB_PORTS = (80, 443)  # no employer or ATS link in the live pools or the preset names another port
SPECIAL_USE = ("localhost", "local", "internal", "lan", "home.arpa")  # never a public site (RFC 6761, 8375, ...)
NUMERIC_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")  # a browser reads a host ending in one as an IPv4 address
SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]{0,30}):")


def _odd_char(url):
    """Why url holds a character no plain link has, else ''."""
    if "\\" in url:
        return "backslash in the link"
    for ch in url:
        if ch.isspace() or unicodedata.category(ch)[0] in "CZ":  # controls, format marks, spaces, separators
            return "space or control character in the link"
    return ""


def _board_readings(url):
    """The job board any reader of url would reach, else None: Python's
    urlparse host, and the host a browser reads (WHATWG: tabs and newlines
    dropped, any run of slashes or backslashes after the scheme skipped, the
    authority ending at the first / \\ ? or #, userinfo up to the last @,
    percent-escapes decoded), each judged on its canonical or IDNA form."""
    seen = []
    try:
        seen.append(urlparse(url).hostname or "")
    except ValueError:
        pass
    m = SCHEME.match(url)
    rest = re.sub(r"[\t\r\n]", "", url[m.end():] if m else url).lstrip("/\\")
    auth = re.split(r"[/\\?#]", rest, maxsplit=1)[0].rpartition("@")[2]
    if not auth.startswith("["):
        auth = auth.partition(":")[0]
    seen += [auth, unquote(auth)]
    for raw in seen:
        if raw:
            board = job_board(raw) or job_board(idna_host(raw))
            if board:
                return board
    return None


def _ip_literal(host):
    """'non-public <host>' or 'bad-url ip-address <host>' for an IP-literal
    host (dotted, integer, hex or octal IPv4, or IPv6 in brackets), else ''."""
    inner = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    if not (host.startswith("[") or NUMERIC_LABEL.fullmatch(inner.split(".")[-1] or "-")):
        return ""
    try:
        if not ipaddress.ip_address(inner.split("%")[0]).is_global:
            return f"non-public {shown(host)}"
    except ValueError:
        pass  # 0x7f.1, 2130706433, 0177.0.0.1: still an address to a browser
    return f"bad-url ip-address {shown(host)}"


def web_refusal(url):
    """None when url may be stored, shown, opened or fetched as a web link,
    else one reason (R1): 'job-board <domain>', 'bad-url <why>' or
    'non-public <host>'. Needs no network and never raises.

    Refused: anything but an http(s) link with '//' and a host; a job board by
    any reading (Python's or a browser's: a backslash, userinfo or lookalike
    spelling that leads to one); a backslash, whitespace, control or format
    character anywhere; userinfo (an '@' in the authority); a port other than
    80 or 443; a host canon_host refuses; an IP-literal host in any form; a
    single-label host; and the special-use names localhost, .localhost,
    .local, .internal, .home.arpa and .lan. A name that resolves to a
    non-public address is refused when connecting (connect_public, same
    'non-public <host>' reason). Percent-escapes in a path or query are fine."""
    if not isinstance(url, str) or not url:
        return "bad-url no link"
    if len(url) > LINK_MAX:
        return f"bad-url longer than {LINK_MAX} characters"
    m = SCHEME.match(url)
    if not m or m.group(1).lower() not in ("http", "https"):
        return "bad-url not an http(s) link"
    board = _board_readings(url)
    if board:
        return f"job-board {board}"
    odd = _odd_char(url)
    if odd:
        return f"bad-url {odd}"
    if url[m.end():m.end() + 2] != "//":
        return "bad-url no host"
    auth = re.split(r"[/?#]", url[m.end() + 2:], maxsplit=1)[0]
    if "@" in auth:
        return "bad-url user name in the link"
    if auth.startswith("["):
        if "]" not in auth:
            return "bad-url not a well-formed host"
        return _ip_literal(auth.partition("]")[0] + "]") or "bad-url ip-address"
    raw, colon, port = auth.partition(":")
    if not raw:
        return "bad-url no host"
    if colon and port:
        if not re.fullmatch(r"[0-9]{1,5}", port) or int(port) not in WEB_PORTS:
            return f"bad-url port {shown(port, 12)}"
    try:
        host = canon_host(raw)
    except Denied as d:
        return d.reason
    board = job_board(host)
    if board:
        return f"job-board {board}"
    if any(host == s or host.endswith("." + s) for s in SPECIAL_USE):
        return f"non-public {host}"
    ip = _ip_literal(host)
    if ip:
        return ip
    if "." not in host:
        return f"bad-url single-label host {host}"
    return None


# Pages that are about an employer but are not its own site (SV-09): social and
# reference sites. Job boards (LinkedIn, Glassdoor, ...) come from job_board.
NOT_EMPLOYER_SITES = ("facebook.com", "fb.com", "fb.me", "twitter.com", "x.com", "t.co", "instagram.com",
                      "youtube.com", "youtu.be", "crunchbase.com", "wikipedia.org", "wikimedia.org",
                      "wikidata.org", "tiktok.com", "threads.net", "bsky.app")
EMPLOYER_SITE_MAX = 200


def employer_site(*values):
    """The employer's own website among values (each a string or a list of
    them, in order: JSON-LD url, then sameAs): the first http(s) link of at
    most 200 characters that web_refusal passes and whose host is neither a
    job board nor a social or reference site (Facebook, X or Twitter,
    Instagram, YouTube, Crunchbase, Wikipedia), else ''."""
    for v in values:
        for s in (v if isinstance(v, list) else [v]):
            s = s.strip() if isinstance(s, str) else ""
            if not s or len(s) > EMPLOYER_SITE_MAX or web_refusal(s):
                continue
            host = host_of(s)
            if any(on_domain(host, d) for d in NOT_EMPLOYER_SITES):
                continue
            return s
    return ""


def hard_deny(url):
    """The DENY reason that holds for every fetch, attended or not, else None;
    never raises: web_refusal's reason (job-board, bad-url, non-public), then
    the R3 list endpoints (r3-list <host>)."""
    reason = web_refusal(url)
    if reason:
        return reason
    host = host_of(url)  # web_refusal passed, so this neither raises nor is blank
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
                    h = host_or_blank((row.get("careers_url") or "").strip())
                    if h and not job_board(h):
                        hosts.add(h)
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    return hosts


def canon_hosts(hosts):
    out = set()
    for h in hosts:
        try:
            out.add(canon_host(h))
        except Denied:
            continue  # a host canon_host refuses is never allowed
    return out


ENCODED_SLASH = re.compile(r"%(?:2f|5c)", re.I)


class Policy:
    """Which URLs may be fetched. unattended adds https-only and the allowlist."""

    def __init__(self, root=None, unattended=True, extra_hosts=()):
        self.unattended = unattended
        self.extra = canon_hosts(extra_hosts)
        if root is not None:
            self.extra |= watchlist_hosts(root)

    def allow_host(self, host):
        """A host a validated request named (spec 8.2); a host canon_host
        refuses is never added."""
        self.extra |= canon_hosts([host])

    def reason(self, url, method="GET"):
        """The DENY reason for url, or None when it may be fetched: hard_deny
        (web_refusal, then the R3 lists), then, unattended, https only, no
        encoded slash in the path and the allowlist."""
        deny = hard_deny(url)
        if deny:
            return deny
        if not self.unattended:
            return None
        u = urlparse(url)
        host = host_of(url)
        if u.scheme != "https":
            return f"not-https {host}"
        if ENCODED_SLASH.search(u.path or ""):
            return f"bad-url encoded slash in the path ({host})"
        if not (host_matches(host, ALLOW_HOSTS) or host in self.extra):
            return f"host-not-allowed {host}"
        if method.upper() == "POST" and not host_matches(host, POST_HOSTS):
            return f"post-not-allowed {host}"
        return None


# ---------- robots.txt (RFC 9309) ----------
# urllib.robotparser matches path prefixes only, so it cannot read UKG's
# "Allow: */JobBoard/"; this follows RFC 9309: the group for our token, else *,
# the longest matching rule wins, Allow wins a tie, and * and $ are wildcards.

def robots_rule(value):
    """(pieces, anchored) for one Allow/Disallow path, or None when the rule is
    ignored: over ROBOTS_RULE_MAX characters or more than ROBOTS_WILDCARDS
    wildcards. '*' splits the pieces; a final '$' anchors the end."""
    if len(value) > ROBOTS_RULE_MAX or value.count("*") > ROBOTS_WILDCARDS:
        return None
    value = norm_path(value)
    anchored = value.endswith("$")
    return (value[:-1] if anchored else value).split("*"), anchored


def rule_matches(pieces, anchored, path):
    """Glob match with no backtracking: the first piece is a prefix, each
    middle piece is found leftmost after the previous one (leftmost is always
    safe for '*'), and an anchored last piece must end the path."""
    first = pieces[0]
    if not path.startswith(first):
        return False
    pos = len(first)
    if len(pieces) == 1:
        return pos == len(path) if anchored else True
    for mid in pieces[1:-1]:
        i = path.find(mid, pos)
        if i < 0:
            return False
        pos = i + len(mid)
    last = pieces[-1]
    if anchored:
        return len(path) - len(last) >= pos and path.endswith(last)
    return path.find(last, pos) >= 0


def crawl_delay_value(value):
    """Seconds from a Crawl-delay line; inf when it is not a finite number of
    0 or more (inf, nan, negative, words), which skips the host."""
    try:
        delay = float(value)
    except ValueError:
        return float("inf")
    return delay if math.isfinite(delay) and delay >= 0 else float("inf")


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
                self.signals.append(value[:500])
            elif key == "user-agent":
                if not last_was_agent and agents:
                    self.groups.append((agents, rules, delay))
                    agents, rules, delay = [], [], None
                agents.append(value.lower())
                last_was_agent = True
                continue
            elif key in ("allow", "disallow") and agents:
                rule = robots_rule(value) if value else None
                if rule:
                    rules.append((len(value), key == "allow", rule))
            elif key == "crawl-delay" and agents:
                delay = crawl_delay_value(value)
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
        path = norm_path(path)
        best = None
        for length, allow, (pieces, anchored) in rules:
            if rule_matches(pieces, anchored, path) and (best is None or length > best[0]
                                                         or (length == best[0] and allow)):
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
        self.parsed = {}  # host -> (entry time, RobotRules), so a big robots.txt is parsed once
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

    def rules(self, host, e):
        got = self.parsed.get(host)
        if got and got[0] == e.get("at"):
            return got[1]
        rules = RobotRules(e.get("text") or "")
        self.parsed[host] = (e.get("at"), rules)
        return rules

    def check(self, url):
        """(allowed, crawl_delay, reason). reason is robots <host>,
        robots-unreachable <host>, crawl-delay <host> (a Crawl-delay that is
        not a finite number from 0 to MAX_CRAWL_DELAY: the host is skipped)
        or a bad-url reason when not allowed. Never raises for an odd URL."""
        try:
            host = host_of(url)
            u = urlparse(url)
        except (Denied, ValueError) as d:
            return False, None, getattr(d, "reason", "bad-url")
        if not host or u.scheme not in ("http", "https"):
            return False, None, "bad-url"
        e = self.entry(u.scheme, host)
        if e["status"] == "unreachable":
            return False, None, f"robots-unreachable {host}"
        if e["status"] == "none":
            return True, None, None
        rules = self.rules(host, e)
        delay = rules.crawl_delay()
        if delay is not None and not 0 <= delay <= MAX_CRAWL_DELAY:
            return False, delay, f"crawl-delay {host}"  # slower than one request per 10 s: skip the host this run
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        if not rules.allowed(path):
            return False, delay, f"robots {host}"
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
        m = re.search(r"charset=([\w.-]{1,40})", str(self.headers.get("content-type", ""))[:500], re.I)
        try:
            return self.body.decode(m.group(1) if m else "utf-8", errors="replace")
        except (LookupError, UnicodeError, ValueError):  # charset=idna has no 'replace'; rot13 is no text codec
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text(), strict=False)

    def check(self):
        """Raise FetchError (with .status) unless 2xx; returns self."""
        if not self.ok:
            e = FetchError(f"HTTP {self.status} from {host_or_blank(self.url) or 'the site'}")
            e.status = self.status
            raise e
        return self


def read_capped(fp, limit=MAX_BYTES):
    """(body, truncated): at most limit bytes, read in pieces with the fetch
    deadline checked between them."""
    if fp is None:
        return b"", False
    chunks, total = [], 0
    while total <= limit:
        check_deadline()
        chunk = fp.read(min(1 << 20, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    raw = b"".join(chunks)
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
        try:
            req = Request(url, data=data, headers=hdrs, method=method)
        except ValueError as e:
            raise FetchError(f"{host}: {str(e)[:120]}")
        with fetch_deadline():  # one wall-clock limit for the request, its redirects and its body
            try:
                with self.opener.open(req, timeout=TIMEOUT) as resp:
                    body, truncated = read_capped(resp)
                    r = Response(resp.geturl(), resp.status, dict(resp.headers), body, truncated)
            except HTTPError as e:
                try:
                    body, truncated = read_capped(e)
                except (OSError, ValueError, http.client.HTTPException):
                    body, truncated = b"", False  # the status is the answer; a broken error body is not
                r = Response(e.geturl() or url, e.code, dict(e.headers or {}), body, truncated)
            except Denied:
                raise
            except (OSError, ValueError, http.client.HTTPException) as e:
                raise FetchError(f"{host}: {str(e)[:200]}")
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
    ap = Parser(description="Network policy check for job_skill.", allow_abbrev=False)  # R4: exact option names
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="ALLOW or DENY a URL under the runner policy", allow_abbrev=False)
    c.add_argument("url")
    c.add_argument("--method", default="GET", type=str.upper, choices=("GET", "POST"))
    c.add_argument("--root", help="JobSearch folder: adds watch-list careers_url hosts, robots cache")
    c.add_argument("--attended", action="store_true", help="a pasted link: hard deny and public only")
    c.add_argument("--allow-host", action="append", help="a host a validated request named")
    c.add_argument("--robots", action="store_true", help="also read robots.txt for the host")
    c.add_argument("--offline", help="fixture folder with index.json instead of the network")
    import argfile  # outside text (the URL) arrives in a JSON file, never inline
    args = argfile.parse(ap, argv)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
