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
    assert p.reason("https://indeed.acmebio.example/jobs") == "host-not-allowed indeed.acmebio.example"
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
        assert p.reason(bad) == "bad-url", bad
    for private in ("http://127.0.0.1/", "http://localhost/x", "http://[::1]/", "http://printer.local/",
                    "http://169.254.169.254/latest/meta-data"):
        assert p.reason(private).startswith("non-public "), private
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
    socket.getaddrinfo, socket.socket = resolver, FakeSocket
    try:
        # a resolver answering public, then private: the socket uses the checked
        # public answer and the name is resolved once
        netfetch.connect_public("rebind.example", 443, 5)
        assert connected == [("93.184.216.34", 443)] and calls == ["rebind.example"], (connected, calls)
        netfetch.connect_public("rebind.example", 443, 5)  # second lookup now says 127.0.0.1
        raise AssertionError("private answer not refused")
    except netfetch.FetchError as e:
        assert "non-public" in str(e) and connected == [("93.184.216.34", 443)], (e, connected)
    finally:
        socket.getaddrinfo, socket.socket = real
    # one public and one private address in a single answer: refused before connecting
    socket.getaddrinfo = lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
    try:
        netfetch.connect_public("mixed.example", 443, 5)
        raise AssertionError("mixed answer not refused")
    except netfetch.FetchError as e:
        assert "10.0.0.5" in str(e), e
    finally:
        socket.getaddrinfo = real[0]

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
    too (host-not-ascii); the punycode spelling stays an ordinary host."""
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
        assert netfetch.job_board(netfetch.host_of(url)), ascii(url)
    odd = "https://exam" + shy + "ple.com/jobs/1"
    assert netfetch.hard_deny(odd) == "host-not-ascii exam\\xadple.com", netfetch.hard_deny(odd)
    f, _ = offline_fetcher(robots=False, unattended=False)
    try:
        f.get(odd)
        raise AssertionError("fetched a non-ASCII host")
    except netfetch.Denied as e:
        assert e.reason.startswith("host-not-ascii"), e.reason
    assert f.log == []
    assert netfetch.hard_deny("https://xn--exmple-cua.com/jobs/1") is None


def test_crawl_delay_limits():
    """A Crawl-delay over 30 s (or inf, nan, negative) leaves the host alone
    (robots <host>, nothing fetched, nothing slept); a sane one is honored."""
    for value in ("86400", "inf", "nan", "-5", "31"):
        robots = netfetch.Robots(lambda url, v=value: (200, f"User-agent: *\nCrawl-delay: {v}\n"))
        ok, delay, why = robots.check("https://careers.example.com/jobs/1")
        assert not ok and why == "robots careers.example.com", (value, ok, delay, why)
    robots = netfetch.Robots(lambda url: (200, "User-agent: *\nCrawl-delay: 5\n"))
    assert robots.check("https://careers.example.com/jobs/1") == (True, 5.0, None)
    clock = FakeClock()
    t = netfetch.Throttle(clock=clock.now, sleep=clock.sleep)
    t.wait("a.example", float("inf"))
    t.wait("a.example", float("inf"))
    assert clock.slept == [netfetch.MAX_CRAWL_DELAY], clock.slept


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


def main():
    for t in (test_cli_policy, test_policy_rules, test_public_address_guard, test_proxy_means_no_fetch,
              test_robots_rules, test_robots_fetching_and_cache, test_politeness, test_size_cap,
              test_lookalike_hosts, test_crawl_delay_limits, test_robots_redirect_loop):
        t()
        print("ok", t.__name__)
    if os.environ.get("JOB_SKILL_LIVE") == "1":
        test_live()
        print("ok test_live")
    print("PASS")


if __name__ == "__main__":
    main()
