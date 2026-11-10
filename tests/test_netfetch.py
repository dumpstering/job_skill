"""Check job/scripts/netfetch.py: host policy, public-address guard, robots,
politeness and limits. Offline: fixtures in tests/fixtures/robots and a mocked
clock; nothing touches the network.

Usage: python tests/test_netfetch.py
Stdlib only. Exits non-zero on the first failure and prints PASS at the end.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROBOTS_FIX = HERE / "fixtures" / "robots"
SCRIPTS = HERE.parent / "job" / "scripts"
SCRIPT = SCRIPTS / "netfetch.py"
sys.path.insert(0, str(SCRIPTS))
import jobfs  # noqa: E402
import netfetch  # noqa: E402


def run_cli(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout.splitlines(), r.stderr


class FakeClock:
    """time.monotonic and time.sleep that only move when slept."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def offline_fetcher(clock=None, **kw):
    clock = clock or FakeClock()
    throttle = netfetch.Throttle(clock=clock.now, sleep=clock.sleep)
    return netfetch.Fetcher(offline=ROBOTS_FIX, throttle=throttle, sleep=clock.sleep, **kw), clock


def test_cli_policy():
    for url, board in (("https://www.linkedin.com/jobs/view/1", "linkedin.com"),
                       ("https://uk.indeed.com/x", "indeed.com"), ("https://www.glassdoor.com/x", "glassdoor.com"),
                       ("https://www.simplyhired.com/x", "simplyhired.com"),
                       ("https://www.ziprecruiter.com/x", "ziprecruiter.com")):
        code, lines, _ = run_cli("check", url)
        assert (code, lines) == (7, [f"DENY job-board {board}"]), (url, code, lines)
    # R3 static deny holds even when robots.txt cannot be read (the fixture errors)
    sr = "https://api.smartrecruiters.com/v1/companies/Acme/postings"
    for extra in ((), ("--robots", "--offline", str(ROBOTS_FIX))):
        code, lines, _ = run_cli("check", sr, *extra)
        assert (code, lines) == (7, ["DENY r3-list api.smartrecruiters.com"]), (extra, code, lines)
    ukg = "https://recruiting.ultipro.com/ACME1000ACME/JobBoard/0a1b2c3d-1111-2222-3333-444455556666/JobBoardView/LoadSearchResults"
    code, lines, _ = run_cli("check", ukg, "--method", "POST")
    assert (code, lines) == (7, ["DENY r3-list recruiting.ultipro.com"]), lines
    code, lines, _ = run_cli("check", "https://careers-acme.icims.com/jobs/search")
    assert (code, lines) == (7, ["DENY r3-list careers-acme.icims.com"]), lines
    code, lines, _ = run_cli("check", "http://10.0.0.1/")
    assert (code, lines) == (7, ["DENY non-public 10.0.0.1"]), lines
    code, lines, _ = run_cli("check", "https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    assert (code, lines) == (0, ["ALLOW boards-api.greenhouse.io"]), lines
    code, lines, _ = run_cli("check", "not a url")
    assert code == 1 and lines[0].startswith("ERROR"), lines


def test_policy_rules():
    p = netfetch.Policy()
    assert p.reason("https://indeed.co.uk/viewjob") == "job-board indeed.co.uk"
    assert p.reason("https://jobs.lever.co/acme/1") is None
    # D1: any label 'indeed' (or linkedin, glassdoor, ...) is the board, whatever follows it
    assert p.reason("https://indeed.acmebio.example/jobs") == "job-board indeed.acmebio.example"
    assert p.reason("http://api.lever.co/v0/postings/acme") == "not-https api.lever.co"
    assert p.reason("https://careers.acme.example/jobs") == "host-not-allowed careers.acme.example"
    assert p.reason("https://api.lever.co/v0/postings/acme", "POST") == "post-not-allowed api.lever.co"
    assert p.reason("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs", "POST") is None
    assert p.reason("https://wd1.myworkdaysite.com/wday/cxs/acme/acme/jobs", "POST") is None
    assert p.reason("https://myworkdayjobs.com.evil.example/x") == "host-not-allowed myworkdayjobs.com.evil.example"
    assert p.reason("https://portal-applicant-tracking.us-cent.paycomonline.net/api/ats/job-posting-previews/search") \
        == "r3-list portal-applicant-tracking.us-cent.paycomonline.net"
    assert p.reason("https://careers-page.clearcompany.com/api/v1/jobs") == "r3-list careers-page.clearcompany.com"
    assert p.reason("https://www.recruitingbypaycor.com/career/x") == "r3-list www.recruitingbypaycor.com"
    assert p.reason("https://recruiting.ultipro.com/ACME/JobBoard/abc/OpportunityDetail?opportunityId=1") is None
    for bad in ("ftp://example.com/x", "http://user@evil.example/job", "file:///etc/passwd"):
        assert p.reason(bad).startswith("bad-url "), bad
    for private in ("http://127.0.0.1/", "http://localhost/x", "http://printer.local/",
                    "http://169.254.169.254/latest/meta-data"):
        assert p.reason(private).startswith("non-public "), private
    # an IP literal in brackets: refused before any lookup (non-public, or bad-url ip-address)
    for v6 in ("http://[::1]/", "http://[64:ff9b::a00:1]/", "http://[::127.0.0.1]/"):
        assert p.reason(v6).startswith(("bad-url ip-address", "non-public [")), v6
    # attended (a pasted link): hard deny and public only, any host and http
    a = netfetch.Policy(unattended=False)
    assert a.reason("http://careers.acme.example/jobs") is None
    assert a.reason("https://www.linkedin.com/jobs/view/1") == "job-board linkedin.com"
    assert a.reason("http://10.1.2.3/") == "non-public 10.1.2.3"
    # watch-list careers_url hosts and validated request hosts join the allowlist
    with tempfile.TemporaryDirectory(prefix="netfetch_") as tmp:
        cfg = Path(tmp) / "config"
        cfg.mkdir()
        (cfg / "watchlist.csv").write_text("employer,careers_url\nAcme Bio,https://careers.acmebio.example/jobs\n",
                                           encoding="utf-8")
        w = netfetch.Policy(root=tmp, extra_hosts=["jobs.initech.example"])
        assert w.reason("https://careers.acmebio.example/jobs/42") is None
        assert w.reason("https://jobs.initech.example/1") is None
        assert w.reason("https://other.example/1") == "host-not-allowed other.example"


def test_public_address_guard():
    calls = []

    def resolver(host, port, **kw):
        calls.append(host)
        ip = "93.184.216.34" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    connected = []

    class FakeSocket:
        def __init__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def connect(self, addr):
            connected.append(addr)

        def close(self):
            pass

    real = (socket.getaddrinfo, socket.socket)
    real_cls = netfetch.DeadlineSocket
    socket.getaddrinfo, socket.socket = resolver, FakeSocket
    netfetch.DeadlineSocket = FakeSocket
    try:
        # a resolver answering public, then private: the socket uses the checked
        # public answer and the name is resolved once
        netfetch.connect_public("rebind.example", 443, 5)
        assert connected == [("93.184.216.34", 443)] and calls == ["rebind.example"], (connected, calls)
        netfetch.connect_public("rebind.example", 443, 5)  # second lookup now says 127.0.0.1
        raise AssertionError("private answer not refused")
    except netfetch.Denied as e:  # R1: a refusal (Denied), not a fetch failure
        assert e.reason == "non-public rebind.example" and connected == [("93.184.216.34", 443)], (e, connected)
    finally:
        socket.getaddrinfo, socket.socket = real
    # one public and one private address in a single answer: refused before connecting
    socket.getaddrinfo = lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
    try:
        netfetch.connect_public("mixed.example", 443, 5)
        raise AssertionError("mixed answer not refused")
    except netfetch.Denied as e:
        assert e.reason == "non-public mixed.example", e.reason
    finally:
        socket.getaddrinfo = real[0]
        netfetch.DeadlineSocket = real_cls

    # a redirect to 127.0.0.1 is refused mid-flight, by the plain and the policy handlers
    req = netfetch.Request("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    f = netfetch.Fetcher(robots=False)
    for handler in (netfetch.PublicRedirects(), netfetch.PolicyRedirects(f._gate)):
        try:
            handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/admin")
            raise AssertionError("redirect to 127.0.0.1 not refused")
        except netfetch.FetchError:
            pass
    # and a redirect to a job board, or to a host the runner may not call
    for target, why in (("https://www.linkedin.com/jobs/view/1", "job-board linkedin.com"),
                        ("https://evil.example/x", "host-not-allowed evil.example")):
        try:
            netfetch.PolicyRedirects(f._gate).redirect_request(req, None, 302, "Found", {}, target)
            raise AssertionError(f"redirect to {target} not refused")
        except netfetch.Denied as e:
            assert e.reason == why, e.reason


def test_proxy_means_no_fetch():
    seen = []
    real = netfetch.connect_public
    netfetch.connect_public = lambda *a: seen.append(a)
    saved = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy")}
    os.environ["HTTPS_PROXY"] = "http://proxy.example:8080"
    try:
        netfetch.Fetcher(robots=False).get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
        raise AssertionError("fetched with a proxy configured")
    except netfetch.FetchError as e:
        assert "proxy" in str(e) and not seen, (e, seen)
    finally:
        netfetch.connect_public = real
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_robots_rules():
    ukg = netfetch.RobotRules((ROBOTS_FIX / "ukg.txt").read_text(encoding="utf-8"))
    assert ukg.allowed("/ACME1000ACME/JobBoard/abc/OpportunityDetail?opportunityId=7")
    assert not ukg.allowed("/ACME1000ACME/JobBoard/abc/JobBoardView/LoadSearchResults")
    assert not ukg.allowed("/ACME1000ACME/JobBoard/abc/Styles/site.css") and not ukg.allowed("/")
    sr = netfetch.RobotRules((ROBOTS_FIX / "smartrecruiters_api.txt").read_text(encoding="utf-8"))
    assert not sr.allowed("/v1/companies/Acme/postings")  # only LinkedInBot may
    lever = netfetch.RobotRules((ROBOTS_FIX / "lever.txt").read_text(encoding="utf-8"))
    assert lever.allowed("/v0/postings/acme") and lever.crawl_delay() == 1.0
    ours = netfetch.RobotRules((ROBOTS_FIX / "slow_ours.txt").read_text(encoding="utf-8"))
    assert ours.allowed("/postings.json") and not ours.allowed("/private/x") and ours.crawl_delay() == 3.0
    work = netfetch.RobotRules((ROBOTS_FIX / "workable.txt").read_text(encoding="utf-8"))
    assert work.signals == ["search=yes, ai-input=yes, ai-train=no"] and not work.allowed("/search?q=x")
    assert work.allowed("/api/v1/widget/accounts/acme")
    end = netfetch.RobotRules("User-agent: *\nDisallow: /*.pdf$\n")
    assert not end.allowed("/files/a.pdf") and end.allowed("/files/a.pdf?x=1")
    assert netfetch.RobotRules("").allowed("/anything")


def test_robots_fetching_and_cache():
    f, _ = offline_fetcher()
    assert f.robots.check("https://api.lever.co/v0/postings/acme")[:2] == (True, 1.0)
    assert f.robots.check("https://api.ashbyhq.com/posting-api/job-board/acme")[0] is True  # 401: no policy readable
    ok, _, why = f.robots.check("https://acme.bamboohr.com/careers/list")  # 503: unreachable
    assert not ok and why == "robots-unreachable acme.bamboohr.com", why
    try:
        f.get("https://careers-acme.icims.com/jobs/123/job")
        raise AssertionError("robots Disallow / not honored")
    except netfetch.Denied as e:
        assert e.reason == "robots careers-acme.icims.com", e.reason
    # the cache under state/robots_cache.json: 24 h, then a fresh read
    with tempfile.TemporaryDirectory(prefix="netfetch_") as tmp:
        now = [1_000_000.0]
        clock = FakeClock()
        throttle = netfetch.Throttle(clock=clock.now, sleep=clock.sleep)
        f = netfetch.Fetcher(root=tmp, offline=ROBOTS_FIX, throttle=throttle, clock=lambda: now[0])
        f.get("https://api.lever.co/v0/postings/acme?mode=json&skip=0&limit=100")
        cache = jobfs.Folder(tmp).robots_cache_json
        data = jobfs.read_json_verified(cache, retries=0)
        assert data["hosts"]["api.lever.co"]["status"] == "rules" and "_sha256" in data, data
        f2 = netfetch.Fetcher(root=tmp, offline=ROBOTS_FIX, throttle=throttle, clock=lambda: now[0])
        f2.get("https://api.lever.co/v0/postings/acme?mode=json&skip=0&limit=100")
        assert [u for _, u in f2.offline.requests] == ["https://api.lever.co/v0/postings/acme?mode=json&skip=0&limit=100"]
        now[0] += 24 * 3600 + 1
        f3 = netfetch.Fetcher(root=tmp, offline=ROBOTS_FIX, throttle=throttle, clock=lambda: now[0])
        f3.get("https://api.lever.co/v0/postings/acme?mode=json&skip=0&limit=100")
        assert f3.offline.requests[0][1] == "https://api.lever.co/robots.txt", f3.offline.requests
        f3.robots.check("https://acme.bamboohr.com/careers/list")
        assert "acme.bamboohr.com" not in jobfs.read_json_verified(cache, retries=0)["hosts"]  # unreachable: not kept
    # a robots.txt that redirects off the allowlist (ADP -> online.adp.com, a login page) means no policy;
    # a redirect into the hard deny list stays a refusal

    def redirected(reason):
        def fetch_text(url):
            raise netfetch.Denied(reason)
        return netfetch.Robots(fetch_text)
    ok, _, why = redirected("host-not-allowed online.adp.com").check("https://workforcenow.adp.com/mascsr/x")
    assert ok and why is None, why
    try:
        redirected("job-board linkedin.com").check("https://careers.acme.example/x")
        raise AssertionError("a robots redirect to a job board was not refused")
    except netfetch.Denied as e:
        assert e.reason == "job-board linkedin.com", e.reason


def test_politeness():
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    f, clock = offline_fetcher(robots=False)
    start = clock.now()
    for _ in range(3):
        assert f.get(url).ok
    assert clock.now() - start >= 2.0, clock.slept
    # robots on: the robots.txt read counts as a request to the host too
    f, clock = offline_fetcher()
    start = clock.now()
    for _ in range(3):
        f.get(url)
    assert clock.now() - start >= 3.0 and [m for m, _, _ in f.log] == ["GET"] * 4, (clock.slept, f.log)
    # Lever's Crawl-delay 1 keeps the 1 s floor; a Crawl-delay 3 for our agent is honored
    f, clock = offline_fetcher()
    for _ in range(2):
        f.get("https://api.lever.co/v0/postings/acme?mode=json&skip=0&limit=100")
    assert clock.slept == [1.0, 1.0], clock.slept
    f, clock = offline_fetcher()
    for _ in range(2):
        f.get("https://acme.pinpointhq.com/postings.json")
    assert clock.slept[-1] == 3.0, clock.slept
    # 429 with Retry-After 30: wait 30 s, then once more
    f, clock = offline_fetcher(robots=False)
    r = f.get("https://acme.breezy.hr/json")
    assert r.ok and 30 in clock.slept and [s for _, _, s in f.log] == [429, 200], (clock.slept, f.log)
    assert netfetch.retry_after_seconds("120") == 120 and netfetch.retry_after_seconds("soon") is None
    # POST with a JSON body
    r = f.post_json("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs", {"limit": 20, "offset": 0})
    assert r.ok and r.json() == {"ok": True} and f.log[-1][0] == "POST", f.log


def test_size_cap():
    with tempfile.TemporaryDirectory(prefix="netfetch_") as tmp:
        big = Path(tmp) / "big.json"
        with open(big, "wb") as fh:
            fh.write(b"x" * (21 * 1024 * 1024))
        (Path(tmp) / "index.json").write_text(json.dumps({"https://api.lever.co/v0/postings/big": {"file": "big.json"}}),
                                              encoding="utf-8")
        r = netfetch.Fetcher(offline=tmp, robots=False).get("https://api.lever.co/v0/postings/big")
        assert r.truncated and len(r.body) == netfetch.MAX_BYTES == 20_000_000, (r.truncated, len(r.body))
        small = netfetch.Response("https://x.example/", 200, {}, b"{}")
        assert not small.truncated and small.json() == {}
    try:
        netfetch.Response("https://api.lever.co/x", 404, {}, b"").check()
        raise AssertionError("404 passed check()")
    except netfetch.FetchError as e:
        assert e.status == 404, e
    assert netfetch.TIMEOUT == 20 and netfetch.USER_AGENT.startswith("JobSearchRunner/1.2 (personal job search;")


def test_live():
    """JOB_SKILL_LIVE=1 only: robots.txt and one list call to api.lever.co, then
    one posting through posting.read_posting on the same (polite) fetcher."""
    import posting
    f = netfetch.Fetcher()
    r = f.get("https://api.lever.co/v0/postings/leverdemo?mode=json&limit=1")
    assert r.ok and isinstance(r.json(), list), (r.status, r.body[:200])
    assert f.robots.check("https://api.lever.co/v0/postings/leverdemo")[:2] == (True, 1.0)
    reading = posting.read_posting("https://jobs.lever.co/leverdemo/ad208490-4052-4f91-a57e-433f2d1e484b",
                                   fetcher=f)
    assert reading.posting.job_key.startswith("lever:") and reading.posting.title, reading.posting
    hosts = [netfetch.host_of(u) for _, u, _ in f.log]
    assert set(hosts) == {"api.lever.co"} and len(hosts) == 3, f.log  # one robots read, two calls
    print("live lever ok:", reading.posting.title, reading.posting.posted)


def test_lookalike_hosts():
    """A job-board host spelled with a soft hyphen, a sharp s, fullwidth
    letters, an ideographic dot or a zero-width space resolves to the board
    itself (the IDNA codec maps them), so it is the board: denied attended
    and unattended, before any request. Any other non-ASCII host is refused
    too (bad-url); the punycode spelling stays an ordinary host."""
    shy, zwsp, ideo, fwdot = chr(0xAD), chr(0x200B), chr(0x3002), chr(0xFF0E)
    fw = "".join(chr(0xFF41 + ord(c) - ord("a")) for c in "linkedin")
    cases = {"https://www.link" + shy + "edin.com/jobs/view/1": "job-board linkedin.com",
             "https://uk.in" + shy + "deed.com/viewjob?jk=1": "job-board indeed.com",
             "https://www.gla" + chr(0xDF) + "door.com/job-listing/x": "job-board glassdoor.com",
             "https://www.glass" + zwsp + "door.com/x": "job-board glassdoor.com",
             "https://www.linkedin" + ideo + "com/jobs/view/1": "job-board linkedin.com",
             "https://uk.indeed" + fwdot + "com/viewjob?jk=1": "job-board indeed.com",
             "https://www." + fw + ".com/jobs/view/1": "job-board linkedin.com"}
    for url, want in cases.items():
        assert netfetch.hard_deny(url) == want, (ascii(url), netfetch.hard_deny(url))
        for unattended in (True, False):
            assert netfetch.Policy(unattended=unattended).reason(url) == want, ascii(url)
        try:
            netfetch.host_of(url)
            raise AssertionError(f"host_of accepted {ascii(url)}")
        except netfetch.Denied as e:
            assert e.reason.startswith("bad-url"), e.reason
    odd = "https://exam" + shy + "ple.com/jobs/1"
    assert netfetch.hard_deny(odd) == "bad-url space or control character in the link", netfetch.hard_deny(odd)
    f, _ = offline_fetcher(robots=False, unattended=False)
    try:
        f.get(odd)
        raise AssertionError("fetched a non-ASCII host")
    except netfetch.Denied as e:
        assert e.reason.startswith("bad-url"), e.reason
    assert f.log == []
    assert netfetch.hard_deny("https://xn--exmple-cua.com/jobs/1") is None


def test_canonical_host():
    """D1: one canonical host for every check. Mixed case and one trailing dot
    fold to the plain name; any character outside a-z 0-9 . -, an empty label
    or a label over 63 characters is refused with bad-url (host_of raises,
    hard_deny and the policy answer bad-url or the board the spelling maps
    to). LinkedIn's lnkd.in and licdn.com hosts and indeed.jobs are boards."""
    assert netfetch.host_of("https://WWW.LinkedIn.COM./jobs/view/1") == "www.linkedin.com"
    assert netfetch.host_of("https://Boards-API.Greenhouse.io/v1/boards/acme/jobs") == "boards-api.greenhouse.io"
    assert netfetch.host_of("not a url") == "" and netfetch.host_of("") == ""
    assert netfetch.canon_host("Careers.Example.com.") == "careers.example.com"
    for url in ("https://WWW.LINKEDIN.COM/jobs/view/1", "https://www.linkedin.com./jobs/view/1",
                "https://LinkedIn.com/in/x"):
        assert netfetch.hard_deny(url) == "job-board linkedin.com", url
    boards = {"https://lnkd.in/abc": "job-board lnkd.in", "https://media.licdn.com/x.png": "job-board licdn.com",
              "https://indeed.jobs/x": "job-board indeed.jobs", "https://www.indeed.careers/x": "job-board indeed.careers",
              "https://remotive.com/remote-jobs/x": "job-board remotive.com",
              "https://www.ziprecruiter.co.uk/jobs/x": "job-board ziprecruiter.co.uk",
              "https://LNKD.IN./x": "job-board lnkd.in"}
    for url, want in boards.items():
        for unattended in (True, False):
            assert netfetch.Policy(unattended=unattended).reason(url) == want, (url, want)
        assert netfetch.hard_deny(url) == want, url
    assert netfetch.hard_deny("https://www.linkedin.com../x") == "job-board linkedin.com"  # refused host, named
    refused = ["https://exa_mple.com/x", "https://a..b.example/x", "https://" + "a" * 64 + ".example/x",
               "https://exa%6Dple.com/x", "https://[::1/x"]
    for url in refused:
        why = netfetch.hard_deny(url)
        assert why and why.startswith("bad-url"), (url, why)
        assert netfetch.Policy(unattended=False).reason(url).startswith("bad-url"), url
        try:
            netfetch.host_of(url)
            raise AssertionError(f"host_of accepted {url}")
        except netfetch.Denied as e:
            assert e.reason.startswith("bad-url") and str(e).startswith("denied: bad-url"), e
    for host in ("exa mple.com", "ex\\ample.com", "", "a" * 64 + ".com", "x." * 130 + "com"):
        try:
            netfetch.canon_host(host)
            raise AssertionError(f"canon_host accepted {host!r}")
        except netfetch.Denied as e:
            assert e.reason.startswith("bad-url"), e
    assert netfetch.host_or_blank("https://exam" + chr(0xAD) + "ple.com/") == ""
    # every spelling the IDNA codec maps, named in D1: soft hyphen, sharp s, fullwidth, ideographic dot
    shy, ideo = chr(0xAD), chr(0x3002)
    fw = "".join(chr(0xFF41 + ord(c) - ord("a")) for c in "indeed")
    for url, board in (("https://www.link" + shy + "edin.com/x", "linkedin.com"),
                       ("https://www.gla" + chr(0xDF) + "door.com/x", "glassdoor.com"),
                       ("https://" + fw + ".jobs/x", "indeed.jobs"),
                       ("https://lnkd" + ideo + "in/abc", "lnkd.in")):
        assert netfetch.hard_deny(url) == f"job-board {board}", (ascii(url), netfetch.hard_deny(url))
    # allowlist and watch-list hosts are kept in the same form
    pol = netfetch.Policy(extra_hosts=["Jobs.Initech.Example.", "exam" + shy + "ple.com"])
    assert pol.extra == {"jobs.initech.example"}, pol.extra
    assert pol.reason("https://JOBS.initech.example/1") is None
    pol.allow_host("www.link" + shy + "edin.com")
    assert pol.extra == {"jobs.initech.example"}, pol.extra
    # the socket layer refuses a non-canonical or job-board name whatever parsed the URL upstream
    for name in ("www.link" + shy + "edin.com", "www.linkedin.com", "lnkd.in"):
        try:
            netfetch.connect_public(name, 443, 5)
            raise AssertionError(f"connect_public accepted {ascii(name)}")
        except netfetch.Denied as e:
            assert e.reason.startswith(("bad-url", "job-board")), e.reason


def test_encoded_paths():
    """SEC-06: R3 list paths and robots rules are compared after decoding
    percent-escaped unreserved characters (RFC 9309 2.2.2) and resolving dot
    segments, so '%4AobBoardView' and '/jobs/%73earch' are the R3 lists; an
    unattended path still holding %2F or %5C is refused."""
    p = netfetch.Policy()
    for url, host in (("https://recruiting.ultipro.com/ACME1000/%4AobBoardView/LoadSearchResults",
                       "recruiting.ultipro.com"),
                      ("https://careers-acme.icims.com/jobs/%73earch?ss=1", "careers-acme.icims.com"),
                      ("https://careers-acme.icims.com//jobs/./search?ss=1", "careers-acme.icims.com"),
                      ("https://careers-acme.icims.com/x/../jobs/search", "careers-acme.icims.com")):
        assert netfetch.hard_deny(url) == f"r3-list {host}", (url, netfetch.hard_deny(url))
        assert p.reason(url) == f"r3-list {host}", url
    # attended too (a pasted link): an encoded slash a server may decode still names the list endpoint
    assert netfetch.Policy(unattended=False).reason("https://careers-acme.icims.com/jobs%2Fsearch?ss=1") \
        == "r3-list careers-acme.icims.com"
    for url in ("https://acme.wd5.myworkdayjobs.com/en-US/x%2F..%2Fy", "https://jobs.lever.co/acme/a%5cb"):
        assert p.reason(url).startswith("bad-url encoded slash"), url
        assert netfetch.Policy(unattended=False).reason(url) is None, url  # attended: the site reads it
    ukg = netfetch.RobotRules((ROBOTS_FIX / "ukg.txt").read_text(encoding="utf-8"))
    assert not ukg.allowed("/ACME1000ACME/JobBoard/abc/JobBoardView/x")
    assert not ukg.allowed("/ACME1000ACME/JobBoard/abc/%4AobBoardView/x")
    assert ukg.allowed("/ACME1000ACME/JobBoard/abc/OpportunityDetail?opportunityId=7")
    enc = netfetch.RobotRules("User-agent: *\nDisallow: /p%61th\nAllow: /%7Euser/\n")
    assert not enc.allowed("/path") and not enc.allowed("/p%61th/x") and enc.allowed("/~user/a")
    assert netfetch.norm_path("/a%2541%2fb%7e") == "/a%2541%2Fb~", netfetch.norm_path("/a%2541%2fb%7e")


def test_robots_bounded():
    """D3: robots rules match without backtracking. Rules over 256 characters
    or with more than 10 wildcards are ignored; ten-wildcard rules against a
    long path, and a 512 KB robots.txt, take well under a second."""
    import time
    long_rule = "/" + "a" * 300
    many = "/" + "*a" * 11
    r = netfetch.RobotRules(f"User-agent: *\nDisallow: {long_rule}\nDisallow: {many}\nDisallow: /private\n")
    assert r.allowed(long_rule) and r.allowed("/" + "a" * 40) and not r.allowed("/private/x")
    ten = "/" + "*a" * 9 + "*b"  # ten wildcards: the most a rule may hold
    rules = netfetch.RobotRules("User-agent: *\n" + "".join(f"Disallow: {ten}\n" for _ in range(200)))
    path = "/" + "a" * 5000
    t = time.perf_counter()
    for _ in range(20):
        assert rules.allowed(path)
    assert time.perf_counter() - t < 2.0, time.perf_counter() - t
    assert not rules.allowed(path + "b")
    big = "User-agent: *\n" + "".join(f"Disallow: /x{i}/*a*b*c$\n" for i in range(20000))
    t = time.perf_counter()
    rb = netfetch.RobotRules(big[:netfetch.ROBOTS_MAX])
    assert rb.allowed("/y/" + "a" * 2000) and not rb.allowed("/x7/zaZbZc")
    assert time.perf_counter() - t < 2.0, time.perf_counter() - t
    # the glob matcher agrees with the regex form on ordinary rules
    for rule, path, want in (("/*.pdf$", "/files/a.pdf", True), ("/*.pdf$", "/files/a.pdf?x=1", False),
                             ("/a*b*c", "/aXbYcZ", True), ("/a*b*c", "/aXcYb", False), ("*$", "/", True),
                             ("/fish*", "/fish.html", True), ("/fish*", "/Fish", False), ("/x$", "/x/", False)):
        pieces, anchored = netfetch.robots_rule(rule)
        assert netfetch.rule_matches(pieces, anchored, path) is want, (rule, path)


def test_fetch_deadline():
    """D3: one fetch has a 60 s wall-clock deadline (FETCH_DEADLINE), checked
    on every socket read. A server sending one byte at a time, each well
    inside the 20 s socket timeout, used to hold a fetch for as long as it
    liked. Local server on 127.0.0.1; the public-address check is bypassed
    for the test only."""
    import threading
    import time

    def drip_server(headers_first):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)

        def serve():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                with conn:
                    try:
                        conn.recv(4096)
                        if headers_first:
                            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
                        else:
                            conn.sendall(b"HTTP/1.1 200 OK\r\n")
                        for _ in range(60):
                            time.sleep(0.1)
                            conn.sendall(b"X-A: b\r\n" if not headers_first else b"x")
                    except OSError:
                        pass
        threading.Thread(target=serve, daemon=True).start()
        return srv

    saved = (netfetch.web_refusal, netfetch.FETCH_DEADLINE, socket.getaddrinfo)
    real_gai = socket.getaddrinfo
    netfetch.web_refusal = lambda url: None  # the drip server listens on another port (R1 refuses one)
    netfetch.FETCH_DEADLINE = 1.0
    try:
        for headers_first in (True, False):
            srv = drip_server(headers_first)
            port = srv.getsockname()[1]
            # the policy and the socket layer would refuse 127.0.0.1; this test names a
            # public-looking host and answers its lookup with the local server
            socket.getaddrinfo = lambda host, p, **kw: real_gai("127.0.0.1", p, **kw)
            real_ip = netfetch.ipaddress.ip_address
            netfetch.ipaddress.ip_address = lambda s: real_ip("93.184.216.34")
            f = netfetch.Fetcher(robots=False, unattended=False, throttle=netfetch.Throttle(enabled=False))
            t = time.perf_counter()
            try:
                f.get(f"http://drip.example:{port}/")
                raise AssertionError("the drip answer was read to the end")
            except netfetch.FetchError as e:
                took = time.perf_counter() - t
                assert took < 4.0 and "within" in str(e), (headers_first, took, e)
            finally:
                netfetch.ipaddress.ip_address = real_ip
                socket.getaddrinfo = real_gai
                srv.close()
    finally:
        netfetch.web_refusal, netfetch.FETCH_DEADLINE, socket.getaddrinfo = saved
    assert netfetch.FETCH_DEADLINE == 60.0 and netfetch.TIMEOUT == 20


def test_odd_charset():
    """A Content-Type naming a codec that cannot replace errors (idna) or is no
    text codec (rot13) falls back to UTF-8 instead of raising."""
    for cs in ("idna", "rot13", "bogus", "utf-8"):
        r = netfetch.Response("https://x.example/", 200, {"Content-Type": f"application/json; charset={cs}"},
                              b'{"a": "\xff"}')
        assert r.text().startswith('{"a"'), cs


def test_crawl_delay_limits():
    """D2: a Crawl-delay that is not a finite number from 0 to 10 s (86400,
    inf, nan, negative, 11, a word) skips the host for the run (crawl-delay
    <host>, nothing fetched, nothing slept); 10 s and less is honored."""
    for value in ("86400", "inf", "nan", "-5", "11", "10.5", "1e309", "soon"):
        robots = netfetch.Robots(lambda url, v=value: (200, f"User-agent: *\nCrawl-delay: {v}\n"))
        ok, delay, why = robots.check("https://careers.example.com/jobs/1")
        assert not ok and why == "crawl-delay careers.example.com", (value, ok, delay, why)
    for value, want in (("10", 10.0), ("5", 5.0), ("0", 0.0)):
        robots = netfetch.Robots(lambda url, v=value: (200, f"User-agent: *\nCrawl-delay: {v}\n"))
        assert robots.check("https://careers.example.com/jobs/1") == (True, want, None), value
    clock = FakeClock()
    t = netfetch.Throttle(clock=clock.now, sleep=clock.sleep)
    t.wait("a.example", float("inf"))
    t.wait("a.example", float("inf"))
    assert clock.slept == [netfetch.MAX_CRAWL_DELAY] and netfetch.MAX_CRAWL_DELAY == 10.0, clock.slept
    # through a Fetcher: the host is refused with that reason and nothing but robots.txt is requested
    with tempfile.TemporaryDirectory(prefix="netfetch_") as tmp:
        (Path(tmp) / "slow.txt").write_text("User-agent: *\nCrawl-delay: 3600\n", encoding="ascii")
        (Path(tmp) / "index.json").write_text(json.dumps({"https://slow.example/robots.txt": {"file": "slow.txt"},
                                                          "https://slow.example/jobs": {"file": "slow.txt"}}),
                                              encoding="ascii")
        clock = FakeClock()
        f = netfetch.Fetcher(offline=tmp, extra_hosts=["slow.example"], sleep=clock.sleep,
                             throttle=netfetch.Throttle(clock=clock.now, sleep=clock.sleep))
        for _ in range(2):
            try:
                f.get("https://slow.example/jobs")
                raise AssertionError("a host asking for an hour between requests was fetched")
            except netfetch.Denied as e:
                assert e.reason == "crawl-delay slow.example", e.reason
        assert [u for _, u in f.offline.requests] == ["https://slow.example/robots.txt"], f.offline.requests
        assert sum(clock.slept) <= netfetch.MAX_CRAWL_DELAY, clock.slept


def test_robots_redirect_loop():
    """A robots.txt that redirects to another path on its own host: the
    redirect hop re-ran the gate, whose robots check fetched robots.txt again,
    until RecursionError (about one request a second; W's live run hit it on 4
    careers hosts). A hop of a robots.txt fetch is that fetch: the policy still
    runs on it, the robots check does not."""
    host = "www.example-bio.com"
    f = netfetch.Fetcher(extra_hosts=[host], throttle=netfetch.Throttle(enabled=False))
    calls = []

    def fake_send(method, url, data, headers, crawl_delay):
        calls.append(url)
        assert len(calls) < 10, calls
        if url.endswith("/robots.txt"):
            f._gate(f"https://{host}/robots-file", "GET")  # the redirect hop, as PolicyRedirects runs it
            return netfetch.Response(f"https://{host}/robots-file", 200, {}, b"User-agent: *\nDisallow: /private\n")
        return netfetch.Response(url, 200, {}, b"ok")
    f._send = fake_send
    assert f.get(f"https://{host}/careers").status == 200
    assert calls == [f"https://{host}/robots.txt", f"https://{host}/careers"], calls
    try:
        f.get(f"https://{host}/private/x")
        raise AssertionError("the robots rules read through the redirect were not honored")
    except netfetch.Denied as e:
        assert e.reason == f"robots {host}", e.reason
    # the policy still holds on a robots redirect hop: a job board is refused, never fetched
    f2 = netfetch.Fetcher(extra_hosts=["careers.example-two.com"], throttle=netfetch.Throttle(enabled=False))

    def to_board(method, url, data, headers, crawl_delay):
        f2._gate("https://www.linkedin.com/robots.txt", "GET")
        raise AssertionError("not reached")
    f2._send = to_board
    try:
        f2.get("https://careers.example-two.com/jobs")
        raise AssertionError("a robots redirect to a job board was not refused")
    except netfetch.Denied as e:
        assert e.reason == "job-board linkedin.com", e.reason


BS = chr(92)
# (label, link, kind): kind is the web_refusal kind every call site must agree on
# ("job-board", "bad-url", "non-public") or None for a link that passes. Round 3's
# 50 spellings (r3/V/v_hosts.py) plus R1's forms.
LINK_MATRIX = [
    ("plain board", "https://www.linkedin.com/jobs/view/1", "job-board"),
    ("backslash before @ (browser host is the board)", "https://www.linkedin.com" + BS + "@careers.example-bio.com/jobs/1",
     "job-board"),
    ("backslash after @", "https://careers.example-bio.com" + BS + "@www.linkedin.com/jobs/1", "job-board"),
    ("backslash before @, no board", "https://careers.example-bio.com" + BS + "@jobs.example-two.com/x", "bad-url"),
    ("backslashes for slashes", "https:" + BS + BS + "www.linkedin.com/jobs/view/1", "job-board"),
    ("backslash in the path", "https://careers.example-bio.com/jobs" + BS + "1", "bad-url"),
    ("one slash", "https:/www.linkedin.com/jobs/view/1", "job-board"),
    ("one slash, employer", "https:/careers.example-bio.com/jobs/1", "bad-url"),
    ("no slash", "https:www.linkedin.com/jobs/view/1", "job-board"),
    ("three slashes", "https:///careers.example-bio.com/jobs/1", "bad-url"),
    ("scheme-relative", "//www.linkedin.com/jobs/view/1", "bad-url"),
    ("tab inside host", "https://www.linked\tin.com/jobs/view/1", "job-board"),
    ("CR LF inside host", "https://www.linked\r\nin.com/jobs/view/1", "job-board"),
    ("leading space", "  https://careers.example-bio.com/jobs/1", "bad-url"),
    ("trailing newline", "https://careers.example-bio.com/jobs/1\n", "bad-url"),
    ("space in the path", "https://careers.example-bio.com/jobs/a b", "bad-url"),
    ("no-break space in the path", "https://careers.example-bio.com/jobs/a" + chr(0xA0) + "b", "bad-url"),
    ("NUL in the path", "https://careers.example-bio.com/jobs/1" + chr(0), "bad-url"),
    ("DEL in the query", "https://careers.example-bio.com/jobs?x=" + chr(0x7F), "bad-url"),
    ("bidi override in the path", "https://careers.example-bio.com/" + chr(0x202E) + "x", "bad-url"),
    ("userinfo, board is the user", "https://www.linkedin.com@careers.example-bio.com/jobs/1", "bad-url"),
    ("userinfo, board is the host", "https://user:pw@www.linkedin.com/jobs/1", "job-board"),
    ("userinfo, employer", "https://user@careers.example-bio.com/jobs/1", "bad-url"),
    ("userinfo with password", "https://a:b@careers.example-bio.com/jobs/1", "bad-url"),
    ("percent-encoded letter", "https://www.linked%69n.com/jobs/1", "job-board"),
    ("percent-encoded dot", "https://www.linkedin%2Ecom/jobs/1", "job-board"),
    ("percent in an employer host", "https://careers.example%2Dbio.com/jobs/1", "bad-url"),
    ("fullwidth dot", "https://www.linkedin" + chr(0xFF0E) + "com/jobs/1", "job-board"),
    ("ideographic dot", "https://www.linkedin" + chr(0x3002) + "com/jobs/1", "job-board"),
    ("halfwidth ideographic dot", "https://www.linkedin" + chr(0xFF61) + "com/jobs/1", "job-board"),
    ("soft hyphen", "https://www.link" + chr(0xAD) + "edin.com/jobs/1", "job-board"),
    ("zero-width joiner", "https://www.link" + chr(0x200D) + "edin.com/jobs/1", "job-board"),
    ("fullwidth letters", "https://www." + "".join(chr(0xFF41 + ord(c) - 97) for c in "linkedin") + ".com/jobs/1",
     "job-board"),
    ("non-ASCII employer host", "https://ex" + chr(0xE0) + "mple.com/jobs/1", "bad-url"),
    ("mixed case trailing dot", "https://WWW.LinkedIn.COM./jobs/1", "job-board"),
    ("two trailing dots", "https://www.linkedin.com../jobs/1", "job-board"),
    ("port 443 on a board", "https://www.linkedin.com:443/jobs/1", "job-board"),
    ("empty port on a board", "https://www.linkedin.com:/jobs/1", "job-board"),
    ("port 8443", "https://careers.example-bio.com:8443/jobs/1", "bad-url"),
    ("port 8080 on http", "http://careers.example-bio.com:8080/jobs/1", "bad-url"),
    ("port 0", "https://careers.example-bio.com:0/jobs/1", "bad-url"),
    ("port out of range", "https://careers.example-bio.com:99999/jobs/1", "bad-url"),
    ("port not a number", "https://careers.example-bio.com:x/jobs/1", "bad-url"),
    ("semicolon in authority", "https://www.linkedin.com;x/jobs/1", "job-board"),
    ("fragment-at", "https://www.linkedin.com#@careers.example-bio.com/", "job-board"),
    ("lnkd.in upper", "https://LNKD.IN/abc", "job-board"),
    ("licdn subdomain", "https://static.licdn.com/x.js", "job-board"),
    ("indeed long tld", "https://www.indeed.careers/x", "job-board"),
    ("glassdoor co.uk", "https://www.glassdoor.co.uk/x", "job-board"),
    ("ziprecruiter subdomain", "https://jobs.ziprecruiter.com/x", "job-board"),
    ("remotive.io", "https://remotive.io/x", "job-board"),
    ("simplyhired.ca", "https://www.simplyhired.ca/x", "job-board"),
    ("underscore", "https://www_linkedin.com/x", "bad-url"),
    ("empty label", "https://jobs..example-bio.com/x", "bad-url"),
    ("label over 63", "https://" + "a" * 64 + ".example-bio.com/x", "bad-url"),
    ("IPv6 literal", "https://[2620:109:c003:104::215]/jobs/1", "bad-url"),
    ("IPv6 loopback", "http://[::1]/x", "non-public"),
    ("IPv4-mapped IPv6", "https://[::ffff:13.107.42.14]/jobs/1", "bad-url"),
    ("unclosed bracket", "https://[::1/x", "bad-url"),
    ("IPv4 literal (public)", "https://13.107.42.14/jobs/1", "bad-url"),
    ("IPv4 loopback", "http://127.0.0.1/admin", "non-public"),
    ("IPv4 private with port", "http://192.168.1.1:8080/", "bad-url"),
    ("IPv4 link-local", "http://169.254.169.254/latest/meta-data", "non-public"),
    ("integer IPv4 (public)", "https://218770958/jobs/1", "bad-url"),
    ("hex IPv4 (public)", "https://0x0d.0x6b.0x2a.0x0e/jobs/1", "bad-url"),
    ("short hex loopback", "http://0x7f.1/x", "bad-url"),
    ("octal loopback", "http://0177.0.0.1/x", "bad-url"),
    ("integer loopback", "http://2130706433/x", "bad-url"),
    ("short dotted", "http://127.1/x", "bad-url"),
    ("single label", "https://intranet/jobs", "bad-url"),
    ("single label with port", "http://printer:631/", "bad-url"),
    ("localhost", "http://localhost/x", "non-public"),
    ("localhost trailing dot", "http://localhost./x", "non-public"),
    ("a .localhost name", "http://app.localhost/x", "non-public"),
    (".local", "https://router.local/", "non-public"),
    (".internal", "https://hr.corp.internal/jobs", "non-public"),
    (".home.arpa", "https://nas.home.arpa/", "non-public"),
    (".lan", "https://files.lan/x", "non-public"),
    ("not http", "ftp://careers.example-bio.com/x", "bad-url"),
    ("file", "file:///etc/passwd", "bad-url"),
    ("javascript", "javascript:alert(1)", "bad-url"),
    ("empty", "", "bad-url"),
    ("R3 UKG list (passes R1; fetch refuses)", "https://recruiting.ultipro.com/ACME/%6aOBBOARDVIEW/x", None),
    ("percent-escapes in path and query", "https://careers.example-bio.com/jobs/a%20b?q=%3Cx%3E&id=7", None),
    ("query-at, employer host", "https://careers.example-bio.com?@www.linkedin.com/", None),
    ("port 443", "https://careers.example-bio.com:443/jobs/1", None),
    ("empty port", "https://careers.example-bio.com:/jobs/1", None),
    ("upper case, trailing dot", "HTTPS://Careers.Example-Bio.COM./jobs/1?src=x#top", None),
    ("non-ASCII letter in the path", "https://careers.example-bio.com/jobs/z" + chr(0xFC) + "rich", None),
    ("plain employer", "https://careers.example-bio.com/jobs/1", None),
]


def test_one_link_refusal():
    """R1 regression (round 3's host matrix, 50 spellings at 10 call sites,
    extended with R1's forms): netfetch.web_refusal is the one refusal, and
    every check that decides whether a link may be stored, shown, opened or
    fetched agrees with it: hard_deny and both policies, check_public,
    posting.link_check and posting.py's exit (4 and no TRY for every refusal),
    gates.url_refusal (G1, G12), events.url_refusal, jobrunner.check_arg,
    boards.web_link (which keeps a job-board link only so G1 names it) and
    today_view.web_link (the OPEN line). No refused spelling reaches
    getaddrinfo, attended or not."""
    import argparse
    import io
    from contextlib import redirect_stdout
    import boards
    import events
    import gates
    import jobrunner
    import posting
    import today_view
    asked = []

    def recorder(host, port, *a, **kw):
        asked.append(host)
        raise socket.gaierror("recorder: no connection in this test")
    real_gai = socket.getaddrinfo
    socket.getaddrinfo = recorder
    tmp = Path(tempfile.mkdtemp(prefix="netfetch_r1_"))
    root = tmp / "JobSearch"
    (root / "applications").mkdir(parents=True)
    run = argparse.Namespace(root=root.resolve(), folder=jobfs.Folder(root))

    def check_arg(u):
        try:
            jobrunner.check_arg(run, "probe_url", "url", "url", u)
            return None
        except jobrunner.RequestError as e:
            return str(e)

    def posting_exit(u):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                code = posting.main([u, "--out", str(tmp / "p.md"), "--root", str(root)])
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue().splitlines()

    def kind_of(reason):
        return reason.split(" ", 1)[0] if reason else None
    try:
        for label, u, kind in LINK_MATRIX:
            why = netfetch.web_refusal(u)
            assert kind_of(why) == kind, (label, ascii(u), why)
            deny = netfetch.hard_deny(u)
            assert deny == why or (kind is None and deny.startswith("r3-list ")), (label, deny)
            assert netfetch.Policy(unattended=False).reason(u) == deny, label
            r = netfetch.Policy(unattended=True).reason(u)
            assert r == deny if deny else (r is None or r.startswith(("host-not-allowed ", "not-https "))), (label, r)
            if why:
                try:
                    netfetch.check_public(u)
                    raise AssertionError(f"check_public passed {label}")
                except netfetch.Denied as d:
                    assert d.reason == why, (label, d.reason)
            g = gates.url_refusal(u)  # G1 and G12 ('not-web' names another scheme, as before)
            try:
                scheme = urlparse(u).scheme.lower()
            except ValueError:
                scheme = "unreadable"
            if scheme == "unreadable":
                assert g == "bad-url", (label, g)
            elif scheme not in ("http", "https"):
                assert g == "not-web", (label, g)
            else:
                assert g == ((why.split(" ", 1)[1] if kind == "job-board" else kind) if why else None), (label, g)
            assert events.url_refusal(u) == why, (label, events.url_refusal(u))
            # callers that strip the value first judge the stripped value they keep
            s = u.strip()
            assert (check_arg(u) is None) == (netfetch.hard_deny(s) is None), (label, check_arg(u))
            kept = netfetch.web_refusal(s)
            assert bool(boards.web_link(u)) == (kept is None or kept.startswith("job-board ")), (label, kept)
            assert bool(today_view.web_link(u)) == (why is None), (label, today_view.web_link(u))
            if kind is not None and u:
                code, lines = posting_exit(u)
                assert not any(ln.startswith("TRY") for ln in lines), (label, lines)
                if netfetch.SCHEME.match(u):
                    assert code == 4 and lines[0].startswith("JOB BOARD" if kind == "job-board" else "REFUSED"), \
                        (label, code, lines[:1])
                else:  # no scheme at all: an argument error (write the link with https://), no TRY
                    assert code == 1 and lines[0].startswith("ERROR url must start"), (label, code, lines[:1])
            for unattended in (False, True):
                before = len(asked)
                f = netfetch.Fetcher(unattended=unattended, robots=False, throttle=netfetch.Throttle(enabled=False))
                try:
                    f.get(u)
                except netfetch.Denied as d:
                    assert d.reason == (deny or d.reason) and (deny or d.reason.startswith(
                        ("host-not-allowed ", "not-https "))), (label, d.reason)
                except netfetch.FetchError:
                    assert why is None, label  # only a link R1 passes may reach the (recording) resolver
                if why is not None:
                    assert asked[before:] == [], (label, asked[before:])
        # a link refused only while connecting (the name resolves to a private address): exit 4, no TRY
        socket.getaddrinfo = lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
        code, lines = posting_exit("https://careers.example-bio.com/jobs/2")
        assert code == 4 and lines[0].startswith("REFUSED non-public careers.example-bio.com"), (code, lines)
        assert not any(ln.startswith("TRY") for ln in lines), lines
    finally:
        socket.getaddrinfo = real_gai


def test_json_lone_surrogate():
    """V4-01: a board answer whose JSON holds half of a UTF-16 pair (a lone
    surrogate escape) comes back from Response.json with U+FFFD in its place,
    a whole pair as its one character, so the posting never stops a later
    UTF-8 write (posting.md, discards.csv)."""
    bs = "\\"
    body = ('[{"text": "QC ' + bs + 'ud83d", "t' + bs + 'udc00": "' + bs + 'ud83d' + bs + 'ude00"}]').encode("ascii")
    r = netfetch.Response("https://api.lever.co/v0/postings/x", 200, {"Content-Type": "application/json"}, body)
    assert r.json() == [{"text": "QC " + chr(0xFFFD), "t" + chr(0xFFFD): chr(0x1F600)}], ascii(r.json())
    r = netfetch.Response("https://x.example/", 200, {"Content-Type": "application/json; charset=utf-7"},
                          b'{"a": "+2D0-"}')  # utf-7 for a lone 0xD83D
    assert "\ud83d" not in r.text() and r.json() == {"a": chr(0xFFFD)}, ascii(r.text())


def test_special_use_names():
    """V4-07: the private-use and special-use names (RFC 6761 test and
    invalid, RFC 6762 Appendix G home, corp, intranet, private and lan, RFC
    7686 onion, localdomain) are non-public to web_refusal, so no OPEN line,
    hyperlink, TRY or fetch is ever made for one; a public name that only
    starts or contains those words is not."""
    for host in ("hr.corp", "router.home", "wiki.intranet", "files.private", "nas.localdomain",
                 "localhost.localdomain", "x.test", "x.invalid", "abc.onion", "printer.local", "a.home.arpa",
                 "HR.CORP", "hr.corp."):
        assert netfetch.web_refusal(f"https://{host}/jobs/1") == f"non-public {host.lower().rstrip('.')}", host
        assert netfetch.Policy(unattended=False).reason(f"https://{host}/jobs/1").startswith("non-public "), host
    for host in ("jobs.homedepot.com", "careers.corporate.com", "jobs.private-equity.com", "testbio.com",
                 "www.onion-labs.com", "careers.example.com"):
        assert netfetch.web_refusal(f"https://{host}/jobs/1") is None, host


def main():
    for t in (test_cli_policy, test_policy_rules, test_public_address_guard, test_proxy_means_no_fetch,
              test_robots_rules, test_robots_fetching_and_cache, test_politeness, test_size_cap,
              test_lookalike_hosts, test_canonical_host, test_encoded_paths, test_robots_bounded,
              test_fetch_deadline, test_odd_charset, test_crawl_delay_limits, test_robots_redirect_loop,
              test_one_link_refusal, test_json_lone_surrogate, test_special_use_names):
        t()
        print("ok", t.__name__)
    if os.environ.get("JOB_SKILL_LIVE") == "1":
        test_live()
        print("ok test_live")
    print("PASS")


if __name__ == "__main__":
    main()
