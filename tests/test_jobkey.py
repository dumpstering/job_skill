"""Check job/scripts/jobkey.py: v1.1 keys unchanged (as raw_key; job_key is
their R2 form), keys safe by construction for hostile links and from every
board reader, the v1.2 ATS keys, canonical URLs, company names and SimHash.

Usage: python tests/test_jobkey.py
Stdlib only. Exits non-zero on the first failure and prints PASS at the end.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
V12 = HERE / "fixtures" / "posting_v12"
sys.path.insert(0, str(HERE.parent / "job" / "scripts"))
import jobkey  # noqa: E402

# Keys computed with the v1.1 jobkey.py (git 6b9b127); v1.2 must give the same.
V11_KEYS = {
    "https://job-boards.greenhouse.io/acme/jobs/1234567": "greenhouse:1234567",
    "https://boards.greenhouse.io/acme/jobs/1234567?gh_src=abc123": "greenhouse:1234567",
    "https://boards.greenhouse.io/embed/job_app?for=acme&token=1234567": "greenhouse:1234567",
    "https://careers.acme.example/open?gh_jid=1234567": "greenhouse:1234567",
    "https://jobs.lever.co/leverco/AD208490-4052-4f91-a57e-433f2d1e484b/apply":
        "lever:ad208490-4052-4f91-a57e-433f2d1e484b",
    "https://jobs.ashbyhq.com/ashbyco/7458d4e9-da2e-47bd-98cb-adfda43d42b2/application":
        "ashby:7458d4e9-da2e-47bd-98cb-adfda43d42b2",
    "https://acme.wd5.myworkdayjobs.com/en-US/AcmeExternalCareerSite/job/US-CA-Santa-Clara/"
    "Platform-Engineer--Safety_JR2021886/apply": "workday:acme:JR2021886",
    "https://jobs.smartrecruiters.com/acmerecruiting/744000158320111-senior-lead":
        "smartrecruiters:acmerecruiting:744000158320111",
    "https://careers-acme.icims.com/jobs/12345/qc-analyst/job": "icims:acme:12345",
    "https://www.linkedin.com/jobs/view/4012345678/": "linkedin:4012345678",
    "https://www.linkedin.com/jobs/search/?currentJobId=4012345678": "linkedin:4012345678",
    "https://www.indeed.com/viewjob?jk=abc123def": "indeed:abc123def",
    "https://careers.acme.example/Jobs/IT-Support?jobId=77&utm_source=x": "url:careers.acme.example/jobs/it-support?jobid=77",
    "https://careers.acme.example/jobs/it-support-lead": "url:careers.acme.example/jobs/it-support-lead",
}

# Two links to one posting, per v1.2 ATS: same key.
V12_PAIRS = [
    ("bamboohr:acmebio:42", "https://acmebio.bamboohr.com/careers/42", "https://acmebio.bamboohr.com/careers/42/detail"),
    ("bamboohr:acmebio:42", "https://acmebio.bamboohr.com/jobs/view.php?id=42", "https://acmebio.bamboohr.com/careers/42?source=x"),
    ("breezy:acme:3f2b1c0d9e8a", "https://acme.breezy.hr/p/3f2b1c0d9e8a-associate-scientist",
     "https://acme.breezy.hr/p/3F2B1C0D9E8A-associate-scientist/apply"),
    ("workable:acmebio:A1B2C3D4E5", "https://apply.workable.com/acmebio/j/A1B2C3D4E5/",
     "https://apply.workable.com/acmebio/j/a1b2c3d4e5/apply"),
    ("rippling:0a1b2c3d-1111-2222-3333-444455556666",
     "https://ats.rippling.com/acme-bio/jobs/0a1b2c3d-1111-2222-3333-444455556666",
     "https://ats.rippling.com/acme-bio/jobs/0A1B2C3D-1111-2222-3333-444455556666/apply?step=application"),
    ("pinpoint:acme:0a1b2c3d-1111-2222-3333-444455556666",
     "https://acme.pinpointhq.com/postings/0a1b2c3d-1111-2222-3333-444455556666",
     "https://acme.pinpointhq.com/en/postings/0a1b2c3d-1111-2222-3333-444455556666/applications/new"),
    ("teamtailor:acmebio:4567890", "https://acmebio.teamtailor.com/jobs/4567890-qc-analyst",
     "https://acmebio.teamtailor.com/jobs/4567890-qc-analyst/applications/new"),
    ("personio:acme:1234567", "https://acme.jobs.personio.de/job/1234567?display=en",
     "https://acme.jobs.personio.com/job/1234567"),
    ("adp:0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0:9200712345678_1",
     "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
     "?cid=0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0&ccId=19000101_000001&jobId=9200712345678_1&lang=en_US",
     "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
     "?jobId=9200712345678_1&cid=0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0&type=MP"),
    ("oracle:initech:300000123456",
     "https://initech.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/300000123456",
     "https://initech.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/300000123456/apply/email"),
    ("paylocity:765432", "https://recruiting.paylocity.com/Recruiting/Jobs/Details/765432",
     "https://recruiting.paylocity.com/recruiting/jobs/Details/765432?source=x"),
    ("dayforce:acmebio:12345", "https://jobs.dayforcehcm.com/en-US/acmebio/CANDIDATEPORTAL/jobs/12345",
     "https://jobs.dayforcehcm.com/acmebio/CANDIDATEPORTAL/jobs/12345?src=mail"),
    ("jobvite:acmebio:oAbC123", "https://jobs.jobvite.com/acmebio/job/oAbC123",
     "https://jobs.jobvite.com/acmebio/job/oAbC123/apply?nl=0"),
    ("jazzhr:acme:AbCdEf123", "https://acme.applytojob.com/apply/AbCdEf123/Associate-Scientist",
     "https://acme.applytojob.com/apply/jobs/details/AbCdEf123"),
    ("usajobs:812345600", "https://www.usajobs.gov/job/812345600", "https://www.usajobs.gov/GetJob/ViewDetails/812345600"),
]


def test_v11_keys_unchanged():
    """v1.1 keys stand as raw_key; job_key is their R2 form (safe_key), the
    same as before for every key already in the R2 character set."""
    for url, key in V11_KEYS.items():
        assert jobkey.raw_key(url) == key, (url, jobkey.raw_key(url), key)
        assert jobkey.job_key(url) == jobkey.safe_key(key), (url, jobkey.job_key(url), key)
        if jobkey.SAFE_KEY.fullmatch(key):
            assert jobkey.job_key(url) == key, (url, jobkey.job_key(url), key)
    assert jobkey.job_key("") == ""
    # a stored v1.1 key with '?' and '=' compares equal to this version's key for the same link
    old = V11_KEYS["https://careers.acme.example/Jobs/IT-Support?jobId=77&utm_source=x"]
    new = jobkey.job_key("https://careers.acme.example/Jobs/IT-Support?jobId=77&utm_source=x")
    assert new != old and jobkey.same_key(old, new) and jobkey.same_key(new, new), (old, new)
    assert not jobkey.same_key(old, jobkey.job_key("https://careers.acme.example/Jobs/IT-Support?jobId=78"))
    assert not jobkey.same_key("", "") and not jobkey.same_key(None, "url:x")


# Hostile links (R2): shell syntax, quotes, spaces, control and non-ASCII
# characters and overlong paths in every place a key copies from a link.
BAD = "$(id){a,b}>x;`y`|z&w\"q'r " + chr(0x0A) + chr(0x00E9) + chr(0x202E) + "%2F\\"
HOSTILE = [
    "https://job-boards.greenhouse.io/acme/jobs/" + BAD,
    "https://careers.acme.example/open?gh_jid=" + BAD,
    "https://boards.greenhouse.io/embed/job_app?token=" + BAD,
    "https://jobs.lever.co/acme/" + BAD,
    "https://jobs.ashbyhq.com/acme/" + BAD,
    "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/x/Title_JR" + BAD,
    "https://jobs.smartrecruiters.com/" + BAD + "/" + BAD,
    "https://careers-acme.icims.com/jobs/" + BAD + "/x/job",
    "https://acme.bamboohr.com/careers/" + BAD,
    "https://apply.workable.com/" + BAD + "/j/" + BAD,
    "https://acme.teamtailor.com/jobs/12" + BAD,
    "https://workforcenow.adp.com/x?cid=" + BAD + "&jobId=" + BAD,
    "https://myjobs.adp.com/" + BAD + "/cx?reqId=" + BAD,
    "https://recruiting.paylocity.com/Recruiting/Jobs/Details/12" + BAD,
    "https://jobs.dayforcehcm.com/en-US/" + BAD + "/x/jobs/12",
    "https://jobs.jobvite.com/" + BAD + "/job/" + BAD,
    "https://acme.applytojob.com/apply/" + BAD,
    "https://www.linkedin.com/jobs/search/?currentJobId=" + BAD,
    "https://www.indeed.com/viewjob?jk=" + BAD,
    "https://careers.acme.example/" + BAD + "?jobId=" + BAD + "&id=" + BAD,
    "https://careers.acme.example/" + "a" * 400,
    "https://careers.acme.example/" + "a" * 195 + BAD,
    "https://" + BAD + ".example/x",
]


def test_keys_safe_by_construction():
    """R2: every key job_key produces holds only A-Z a-z 0-9 . _ : / - and is
    at most 200 characters, for hostile links in every key form and with
    every ats hint; a key that had to change carries a hash, so distinct links
    keep distinct keys; safe_key is idempotent; inline_ok accepts exactly the
    key characters (paths: plus spaces) and never a leading '-'."""
    keys = {}
    for url in HOSTILE:
        for ats in (None, "phenom", "teamtailor", "workday", "greenhouse"):
            k = jobkey.job_key(url, ats)
            assert jobkey.SAFE_KEY.fullmatch(k) and len(k) <= jobkey.KEY_MAX, (ascii(url), ats, ascii(k))
            assert jobkey.safe_key(k) == k and jobkey.inline_ok(k), ascii(k)
            if jobkey.raw_key(url, ats) != k:
                assert re.search(r"-[0-9a-f]{12}$", k), ascii(k)
            keys.setdefault(k, set()).add(jobkey.raw_key(url, ats))
    assert all(len(raws) == 1 for raws in keys.values()), [ascii(k) for k, r in keys.items() if len(r) > 1]
    # two links that differ only in a replaced character, or past the cut, keep distinct keys
    pairs = [("https://careers.acme.example/a$b", "https://careers.acme.example/a{b"),
             ("https://careers.acme.example/" + "a" * 300 + "1", "https://careers.acme.example/" + "a" * 300 + "2")]
    for a, b in pairs:
        assert jobkey.job_key(a) != jobkey.job_key(b), (a[:60], b[:60])
    assert jobkey.safe_key(None) == "" and jobkey.safe_key("greenhouse:1") == "greenhouse:1"
    for v, path, ok in (("url:a.example/b-1", False, True), ("a b", False, False), ("a b", True, True),
                        ("--root", False, False), ("x$y", True, False), ("", False, False), ("", True, False),
                        ("a" * 201, False, False), ("2026-11-06", False, True), (7, False, True),
                        ("applications/2026-11-06 Johnson & Johnson - Scientist (2)", True, True),
                        ("applications/Caf" + chr(0xE9) + " Bio", True, True), ("applications/../x", True, False),
                        ("/x", True, False), ("-x", True, False), ("C:/x", True, False), ("a`b", True, False),
                        ("a\"b", True, False), ("a\\b", True, False), ("a\nb", True, False), ("a//b", True, False)):
        assert jobkey.inline_ok(v, path=path) is ok, (v, path)
    # every runner job folder name passes the path rule (jobfs.job_folder_name drops $ ` " \ and controls)
    sys.path.insert(0, str(HERE.parent / "job" / "scripts"))
    import jobfs
    for company, role in (("Johnson & Johnson", "Scientist (Bio/Chem)"), ("A$b`c\"d\\e", "R&D: 100% {x}"),
                          ("Caf" + chr(0xE9) + " Bio", "QC!#=@[]^~;,'+")):
        name = jobfs.job_folder_name("2026-11-06", company, role)
        assert jobkey.path_ok(f"applications/{name}") and jobkey.path_ok(f"applications/{name} (2)"), name


def test_reader_keys_safe():
    """R2, reader side: every board reader, on its own fixture and on the same
    answers with every link in them made hostile (shell syntax and quotes
    appended), produces only keys in the R2 set. Readers build keys through
    posting.to_record -> jobkey.job_key, so this covers them all."""
    tests_dir = str(HERE)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    import boards
    import netfetch
    import test_boards as tb

    class Hostile(netfetch.OfflineSource):
        def respond(self, method, url, data=None):
            r = super().respond(method, url, data)
            if not url.endswith("/robots.txt"):
                r.body = LINK.sub(lambda m: m.group(1) + b"$(id){a,b}`x`;y&z", r.body)
            return r
    LINK = re.compile(rb"(https?:(?:\\?/){2}[A-Za-z0-9.-]+(?:\\?/)[^\"'\s<>\\]{0,300})")
    for hostile in (False, True):
        n, changed = 0, 0
        for name, text in sorted(tb.SPECS.items()):
            spec = boards.parse_spec(text)
            f = tb.fetcher(extra_hosts=(spec.host,) if spec.host else ())
            if hostile:
                f.offline = Hostile(tb.FIX)
            res = boards.read_board(spec, f, first_seen="2026-11-05")
            for rd in res.readings:
                k = rd.posting.job_key
                assert jobkey.SAFE_KEY.fullmatch(k) and len(k) <= jobkey.KEY_MAX, (name, hostile, ascii(k))
                n += 1
                changed += "_id_a_b_x" in k  # the hostile text reached the key and was made safe
        assert n > 50 and (changed > 5 if hostile else changed == 0), (hostile, n, changed)


def test_v12_keys():
    for key, a, b in V12_PAIRS:
        assert jobkey.job_key(a) == key and jobkey.job_key(b) == key, (key, jobkey.job_key(a), jobkey.job_key(b))
    # employer domains need the ATS named by the caller
    phenom = "https://careers.acmebio.example/us/en/job/R-012345/QC-Analyst"
    assert jobkey.job_key(phenom).startswith("url:")
    assert jobkey.job_key(phenom, ats="phenom") == "phenom:careers.acmebio.example:R-012345"
    assert jobkey.job_key("https://careers.acmebio.example/jobs/4567890-qc-analyst", ats="teamtailor") == \
        "teamtailor:careers.acmebio.example:4567890"
    # lists and odd paths fall back to the URL key
    for url in ("https://acme.applytojob.com/apply/jobs", "https://acmebio.bamboohr.com/careers/list",
                "https://apply.workable.com/acmebio/"):
        assert jobkey.job_key(url).startswith("url:"), (url, jobkey.job_key(url))


def test_canonical_url():
    url = ("https://www.Job-Boards.Greenhouse.io/acme/jobs/123/?utm_source=linkedin&gh_src=abc123"
           "&utm_medium=social&utm_campaign=x#app")
    norm, gh_src = jobkey.canonical_url(url)
    assert (norm, gh_src) == ("https://job-boards.greenhouse.io/acme/jobs/123", "abc123"), (norm, gh_src)
    a = jobkey.canonical_url("http://careers.acme.example:443/jobs?id=7&fbclid=X&lever-source=LinkedIn")[0]
    b = jobkey.canonical_url("https://careers.acme.example/jobs/?gclid=Y&id=7")[0]
    assert a == b == "https://careers.acme.example/jobs?id=7", (a, b)
    assert jobkey.canonical_url("https://acme.example/jobs?b=2&a=1")[0] == "https://acme.example/jobs?a=1&b=2"
    assert jobkey.canonical_url("https://acme.example/JobID/AbC")[0].endswith("/JobID/AbC")  # path case kept
    assert jobkey.canonical_url("not a url") == ("", "")


def test_company_names():
    assert jobkey.norm_company("Acme Bio, Inc.") == jobkey.norm_company("Acme Bio") == "acme bio"
    for name in ("Acme Bio GmbH & Co. KG", "Acme Bio L.L.C.", "Acme Bio SE", "ACME BIO, S.A.", "Acme Bio Pte. Ltd.",
                 "Acme Bio Pvt Ltd", "Acme Bio ApS", "Acme Bio Oyj", "Acme Bio, LLC (DBA)"):
        assert jobkey.norm_company(name) == "acme bio", (name, jobkey.norm_company(name))
    # new legal forms are dropped only at the end of a name
    assert jobkey.norm_company("AS Watson Group") == "as watson"
    assert jobkey.norm_company("SE Labs") == "se labs" and jobkey.norm_company("PA Consulting") == "pa consulting"
    assert jobkey.norm_company("ASA") == "asa"  # a name is never emptied by trailing rules
    # v1.1 results stand
    assert jobkey.norm_company("The Acme Group") == "acme" and jobkey.norm_company("Amazon.com") == "amazon com"
    assert {"inc", "llc", "gmbh", "kg", "se", "aps"} <= jobkey.COMPANY_SUFFIXES


def test_simhash():
    text = {n: (V12 / f"simhash_{n}.txt").read_text(encoding="utf-8") for n in ("original", "agency", "unrelated")}
    h = {n: jobkey.simhash64(t) for n, t in text.items()}
    assert all(len(v) == 16 and int(v, 16) >= 0 for v in h.values()), h
    assert jobkey.simhash64(text["original"]) == h["original"]  # stable across runs (no hash())
    assert jobkey.hamming(h["original"], h["agency"]) <= 3, h
    assert jobkey.hamming(h["original"], h["unrelated"]) > 10, h
    assert jobkey.hamming(h["agency"], h["unrelated"]) > 10, h
    assert jobkey.simhash64("") == "0" * 16
    assert jobkey.hamming(0b1011, "0") == 3 and jobkey.hamming("ff", "0f") == 4


def main():
    for t in (test_v11_keys_unchanged, test_v12_keys, test_canonical_url, test_company_names, test_simhash,
              test_keys_safe_by_construction, test_reader_keys_safe):
        t()
        print("ok", t.__name__)
    print("PASS")


if __name__ == "__main__":
    main()
