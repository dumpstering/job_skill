"""Check job/scripts/aggregators.py: USAJOBS headers and records, Adzuna as
leads only (no URL fields, redirect_url never requested), lanes behind
settings, The Muse's 14-day cut, and the keys file location.

Usage: python tests/test_aggregators.py
Fixtures: tests/fixtures/boards (agg_*.json, synthetic: the real APIs need
the user's keys). A recording fetcher keeps every request's headers.
Stdlib only. Exits non-zero on the first failure and prints PASS at the end.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "boards"
SCRIPTS = HERE.parent / "job" / "scripts"
SCRIPT = SCRIPTS / "aggregators.py"
sys.path.insert(0, str(SCRIPTS))
import aggregators  # noqa: E402
import netfetch  # noqa: E402

PROFILE = """version = 1
[home]
label = "Springfield, IL"
radius_miles = 40
[[family]]
id = "lab"
status = "active"
mode = "industry"
core_titles = ["Laboratory Scientist"]
title_any = ["scientist", "laboratory"]
"""
KEYS = """[usajobs]
email = "jordan.avery@example.com"
key = "usajobs-test-key"
[adzuna]
app_id = "test-id"
app_key = "test-key"
"""


class Recording(netfetch.Fetcher):
    """An offline Fetcher that also records each request's headers."""

    def __init__(self, folder=FIX):
        super().__init__(offline=folder)
        self.sent = []

    def _send(self, method, url, data, headers, crawl_delay):
        self.sent.append((method, url, dict(headers or {})))
        return super()._send(method, url, data, headers, crawl_delay)


def make_root(sources):
    root = Path(tempfile.mkdtemp())
    (root / "config").mkdir()
    flags = "\n".join(f"{k} = {'true' if v else 'false'}" for k, v in sources.items())
    (root / "config" / "settings.toml").write_text(f"[sources]\n{flags}\n", encoding="ascii")
    (root / "config" / "search_profile.toml").write_text(PROFILE, encoding="ascii")
    (root / "keys.toml").write_text(KEYS, encoding="ascii")
    return root


def run(root, fetcher):
    lines = []
    lanes = aggregators.run(root, fetcher=fetcher, keys_file=root / "keys.toml", emit=lines.append)
    return {ln.name: ln for ln in lanes}, lines


def test_usajobs_headers_and_records():
    root = make_root({"usajobs": True, "adzuna": False})
    f = Recording()
    lanes, lines = run(root, f)
    calls = [(u, h) for m, u, h in f.sent if netfetch.host_of(u) == "data.usajobs.gov" and "/api/search" in u]
    assert len(calls) == 1, f.sent
    url, h = calls[0]
    assert h["Host"] == "data.usajobs.gov", h
    assert h["User-Agent"] == "jordan.avery@example.com", h
    assert h["Authorization-Key"] == "usajobs-test-key", h
    q = parse_qs(urlparse(url).query)
    assert q["DatePosted"] == ["3"] and q["ResultsPerPage"] == ["500"] and q["LocationName"] == ["Springfield, IL"], q
    lane = lanes["usajobs"]
    assert lane.line() == "SOURCE usajobs OK 2 postings requests=1", lane.line()
    p = lane.postings[0].posting
    assert p.job_key == "usajobs:800001" and p.lane == "usajobs" and p.ats == "usajobs", p
    assert p.posted == "2026-11-04" and p.posted_field == "PublicationStartDate", p
    assert p.salary and p.salary["min"] == 72000.0, p.salary
    assert "SOURCE adzuna OFF" in lines and "SOURCE muse OFF" in lines and "SOURCE workable_search OFF" in lines
    shutil.rmtree(root)


def test_adzuna_leads_only():
    root = make_root({"adzuna": True, "usajobs": False})
    f = Recording()
    lanes, _ = run(root, f)
    lane = lanes["adzuna"]
    assert lane.status == "OK" and lane.requests == 1, lane.line()
    url = [u for _, u, _ in f.sent if netfetch.host_of(u) == "api.adzuna.com" and "/search/" in u][0]
    q = parse_qs(urlparse(url).query)
    assert q["what_or"] == ["scientist laboratory"] and q["where"] == ["Springfield"], q
    assert (q["distance"], q["max_days_old"], q["sort_by"]) == (["40"], ["3"], ["date"]), q
    # Two valid cards; the instruction-like one is dropped (fail closed).
    assert len(lane.leads) == 2, lane.leads
    for ld in lane.leads:
        assert set(ld) == {"v", "lead_id", "source", "company", "title", "location", "seen"}, ld
        assert ld["source"] == "adzuna" and len(ld["lead_id"]) == 16, ld
        assert not any("http" in str(v) or "adzuna.com" in str(v) for v in ld.values()), ld
    assert not any("/land/" in u or netfetch.host_of(u) == "www.adzuna.com" for _, u, _ in f.sent), f.sent
    # The app key travels only in the API query, never in a lead.
    assert "test-key" not in json.dumps(lane.leads)
    shutil.rmtree(root)


def test_lanes_behind_settings_and_keys():
    root = make_root({"usajobs": False, "adzuna": False, "muse": False, "workable_search": False})
    f = Recording()
    lanes, lines = run(root, f)
    assert not f.sent, f.sent  # nothing on: no request at all, Muse included
    assert lines == ["SOURCE usajobs OFF", "SOURCE adzuna OFF", "SOURCE workable_search OFF", "SOURCE muse OFF"], lines
    root2 = make_root({"usajobs": True, "adzuna": True})
    (root2 / "keys.toml").write_text("", encoding="ascii")
    f = Recording()
    lanes, lines = run(root2, f)
    assert lanes["usajobs"].status == "NOKEY" and lanes["adzuna"].status == "NOKEY", lines
    assert not f.sent
    # The default keys path is %LOCALAPPDATA%\JobRunner\keys.toml (JOBRUNNER_HOME for tests).
    os.environ["JOBRUNNER_HOME"] = str(root)
    try:
        assert aggregators.keys_path() == root / "keys.toml"
        assert aggregators.load_keys()["usajobs"]["key"] == "usajobs-test-key"
    finally:
        del os.environ["JOBRUNNER_HOME"]
    shutil.rmtree(root)
    shutil.rmtree(root2)


def test_muse_drops_stale():
    tmp = Path(tempfile.mkdtemp())
    data = {"results": [
        {"name": "Lab Scientist", "publication_date": "2026-11-01T00:00:00Z", "company": {"name": "Acme Bio"},
         "locations": [{"name": "Springfield, IL"}], "refs": {"landing_page": "https://www.themuse.com/jobs/acme/1"}},
        {"name": "Old Scientist", "publication_date": "2025-08-01T00:00:00Z", "company": {"name": "Acme Bio"},
         "locations": [{"name": "Springfield, IL"}], "refs": {"landing_page": "https://www.themuse.com/jobs/acme/2"}}]}
    (tmp / "muse.json").write_text(json.dumps(data), encoding="ascii")
    url = "https://www.themuse.com/api/public/jobs?page=0&descending=true&location=Springfield%2C+IL"
    (tmp / "index.json").write_text(json.dumps({url: "muse.json"}), encoding="ascii")
    root = make_root({"muse": True})
    os.environ["JOB_SKILL_NOW"] = "2026-11-05T18:00:00Z"
    try:
        lanes, _ = run(root, Recording(tmp))
    finally:
        del os.environ["JOB_SKILL_NOW"]
    leads = lanes["muse"].leads
    assert [ld["title"] for ld in leads] == ["Lab Scientist"] and leads[0]["source"] == "muse", leads
    shutil.rmtree(tmp)
    shutil.rmtree(root)


def test_cli():
    root = make_root({"usajobs": True, "adzuna": True})
    out_p, out_l = root / "p.jsonl", root / "l.jsonl"
    r = subprocess.run([sys.executable, str(SCRIPT), "run", "--root", str(root), "--offline", str(FIX),
                        "--keys", str(root / "keys.toml"), "--out-postings", str(out_p), "--out-leads", str(out_l)],
                       capture_output=True, text=True, encoding="utf-8")
    lines = r.stdout.splitlines()
    assert r.returncode == 0 and lines[:2] == ["SOURCE usajobs OK 2 postings requests=1",
                                               "SOURCE adzuna OK 2 leads requests=1"], (lines, r.stderr)
    assert "usajobs-test-key" not in r.stdout and "test-key" not in r.stdout
    assert len(out_p.read_text(encoding="utf-8").splitlines()) == 2
    assert len(out_l.read_text(encoding="utf-8").splitlines()) == 2
    r = subprocess.run([sys.executable, str(SCRIPT), "run", "--root", str(root / "missing")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1 and r.stdout.startswith("ERROR"), r.stdout
    shutil.rmtree(root)


class Fuzzed(netfetch.Fetcher):
    """Answers every request with HTTP 200 and one body; nothing is sent."""

    def __init__(self, body):
        super().__init__(robots=False, throttle=netfetch.Throttle(enabled=False))
        self.body = body

    def _send(self, method, url, data, headers, crawl_delay):
        return netfetch.Response(url, 200, {"content-type": "application/json"},
                                 self.body.encode("utf-8", "surrogatepass"))


def test_odd_answers_stay_in_their_lane():
    """D2: every lane, against every odd-but-valid answer of the boards fuzz
    fixture (null, wrong types, JSON nested 200,000 deep, HTML), ends as one
    OK or ERROR line and never raises; one odd item in a good answer is
    skipped and counted (skipped=1) while the rest are kept."""
    bodies = json.loads((FIX / "fuzz" / "bodies.json").read_text(encoding="ascii"))
    n = bodies["generated"]["deep"]["nest"]
    cases = dict(bodies["bodies"], deep="[" * n + "]" * n)
    root = make_root({"usajobs": True, "adzuna": True, "muse": True, "workable_search": True})
    os.environ["JOB_SKILL_NOW"] = "2026-11-05T18:00:00Z"
    try:
        for name, body in cases.items():
            lines = []
            try:
                lanes = aggregators.run(root, fetcher=Fuzzed(body), keys_file=root / "keys.toml",
                                        emit=lines.append)
            except Exception as e:  # noqa: BLE001
                raise AssertionError(f"{name}: RAISED {type(e).__name__}")
            assert len(lanes) == 4 and all(ln.status in ("OK", "ERROR") for ln in lanes), (name, lines)
        good = json.loads((FIX / "agg_usajobs.json").read_text(encoding="utf-8"))
        items = good["SearchResult"]["SearchResultItems"]
        good["SearchResult"]["SearchResultItems"] = [None, {"MatchedObjectDescriptor": [1]}] + items
        lines = []
        lanes = {ln.name: ln for ln in aggregators.run(root, fetcher=Fuzzed(json.dumps(good)),
                                                       keys_file=root / "keys.toml", emit=lines.append)}
        assert len(lanes["usajobs"].postings) == 2 and lines[0].startswith("SOURCE usajobs OK 2 postings"), lines
        assert lines[0].endswith("skipped=1"), lines  # the null row; the list descriptor reads as no posting
    finally:
        del os.environ["JOB_SKILL_NOW"]
        shutil.rmtree(root)


def main():
    for t in (test_usajobs_headers_and_records, test_adzuna_leads_only, test_lanes_behind_settings_and_keys,
              test_muse_drops_stale, test_cli, test_odd_answers_stay_in_their_lane):
        t()
        print("ok", t.__name__)
    print("PASS")


if __name__ == "__main__":
    main()
