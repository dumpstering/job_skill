"""Writes the synthetic pool fixtures used by tests/test_jobqueue.py:
pool_71.json (71 gated postings), pool_12.json (12 of them, all distinct
companies on no-account boards) and new4.json (4 late finds for an addendum).

Usage: python tests/fixtures/pool/make_pool.py
Every company, title and place is made up. Deterministic (no randomness).
"""
import json
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAY = date(2026, 11, 6)
COMPANIES = ["Acme Bio", "Globex Therapeutics", "Initech Labs", "Umbrella Biosciences", "Hooli Health",
             "Vandelay Diagnostics", "Stark Biologics", "Wayne Genomics", "Tyrell Bio", "Cyberdyne Labs",
             "Soylent Sciences", "Wonka Bioworks", "Gringotts Pharma", "Monarch Bio", "Oscorp Therapeutics",
             "Piedpiper Bio", "Massive Dynamic", "Aperture Sciences", "Blackmesa Labs", "Dunder Diagnostics",
             "Prestige Bio", "Sterling Genomics", "Bluth Biologics", "Kramerica Labs", "Nakatomi Pharma",
             "Weyland Bio", "Yoyodyne Sciences", "Zorg Therapeutics", "Duff Biotech", "Krusty Labs",
             "Ollivander Bio", "Spacely Sciences", "Cogswell Labs", "Rekall Bio", "Virtucon Pharma",
             "Ghostbusters Bio", "Planet Express Labs", "Mom Corp Bio", "Slurm Sciences", "Buy N Large Bio",
             "Encom Genomics", "Gekko Bio", "Hammer Labs", "Ingen Bio", "Jupiter Mining Bio"]
FAMILIES = ["qc_micro", "assay_dev", "research_assoc", "postdoc"]
TITLES = {"qc_micro": "QC Microbiologist II", "assay_dev": "Scientist, Assay Development",
          "research_assoc": "Research Associate II", "postdoc": "Postdoctoral Fellow, Microbiology",
          "gov_micro": "Microbiologist"}


def slug(name):
    return "".join(c for c in name.lower() if c.isalnum())


def item(i, company, family, ats, age, fit, tenant=None):
    s = slug(company)
    posted = (DAY - timedelta(days=age)).isoformat()
    if ats == "greenhouse":
        jid = str(910000 + i)
        url, key = f"https://job-boards.greenhouse.io/{s}/jobs/{jid}", f"greenhouse:{jid}"
    elif ats == "lever":
        jid = f"00000000-0000-4000-8000-{i:012d}"
        url, key = f"https://jobs.lever.co/{s}/{jid}", f"lever:{jid}"
    elif ats == "ashby":
        jid = f"11111111-0000-4000-8000-{i:012d}"
        url, key = f"https://jobs.ashbyhq.com/{s}/{jid}", f"ashby:{jid}"
    elif ats == "workable":
        jid = f"A{i:05d}B"
        url, key = f"https://apply.workable.com/{s}/j/{jid}/", f"workable:{s}:{jid}"
    elif ats == "bamboohr":
        url, key = f"https://{s}.bamboohr.com/careers/{100 + i}", f"bamboohr:{s}:{100 + i}"
    elif ats == "breezy":
        jid = f"{i:012x}"
        url, key = f"https://{s}.breezy.hr/p/{jid}-job", f"breezy:{s}:{jid}"
    elif ats == "workday":
        rid = f"R-{20000 + i}"
        url = f"https://{tenant}.wd5.myworkdayjobs.com/en-US/External/job/Springfield/Job_{rid}"
        key = f"workday:{tenant}:{rid}"
    elif ats == "oracle":
        url = f"https://{tenant}.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/{3000 + i}"
        key = f"oracle:{tenant}:{3000 + i}"
    else:  # usajobs
        url, key = f"https://www.usajobs.gov/job/{800000 + i}", f"usajobs:{800000 + i}"
    title = TITLES[family]
    text = (f"{company} is hiring a {title}. Requirements: aseptic technique, PCR and cell culture. "
            f"This synthetic posting {i} exists for tests only.")
    return {"job_key": key, "ats": ats, "board": s, "company": company,
            "company_norm": " ".join(company.lower().split()), "title": title, "title_norm": title.lower(),
            "location": "Springfield, IL", "city": "Springfield", "state": "IL", "country": "US",
            "remote": "onsite", "us_eligible": True, "posted": posted, "posted_field": "fixture",
            "first_seen": posted, "url": url, "apply_url": url, "req_ids": [f"REQ-{i:04d}"], "text": text,
            "salary": None, "questions": False, "employer_site": "", "lane": "usajobs" if ats == "usajobs" else "watch",
            "lead_ids": [], "simhash": f"{(i * 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF:016x}", "closed": False,
            "family": "gov_micro" if ats == "usajobs" else family,
            "mode": "usajobs" if ats == "usajobs" else ("cv" if family == "postdoc" else "industry"),
            "family_priority": {"qc_micro": 1, "assay_dev": 1, "research_assoc": 3, "postdoc": 2}.get(family, 2),
            "title_match": 1.0, "fit": fit, "coverage": fit, "level_match": 1.0, "flags": [],
            "band": "", "age_days": age, "date_basis": "posted", "repost_of": None, "segment": "biotech",
            "posting_status": "read", "markdown": "", "ai_text": [], "hidden": [], "pooled_on": posted,
            "folder": ""}


def pool_71():
    plan = (["greenhouse"] * 30 + ["lever"] * 10 + ["ashby"] * 8 + ["workable"] * 4 + ["bamboohr"] * 3
            + ["breezy"] * 2 + ["workday"] * 10 + ["oracle"] * 2 + ["usajobs"] * 2)
    tenants = ["tyrellbio", "cyberdyne", "soylent", "wonka", "gringotts"]
    items = []
    for i, ats in enumerate(plan):
        company = COMPANIES[i % len(COMPANIES)]
        family = FAMILIES[(i // 3) % len(FAMILIES)]
        tenant = None
        if ats == "workday":
            tenant = tenants[i % len(tenants)]
            company = {"tyrellbio": "Tyrell Bio", "cyberdyne": "Cyberdyne Labs", "soylent": "Soylent Sciences",
                       "wonka": "Wonka Bioworks", "gringotts": "Gringotts Pharma"}[tenant]
        if ats == "oracle":
            tenant, company = "ekmx", "Ingen Bio"
        if ats == "usajobs":
            company = "Department of Examples"
        age = (i * 7) % 26
        fit = round(0.35 + ((i * 13) % 55) / 100, 2)
        if ats == "workday":
            fit, age = 0.9, 1  # strong enough to be picked despite the account cost
        items.append(item(i + 1, company, family, ats, age, fit, tenant))
    return items


def main():
    items = pool_71()
    counts = {"seen": 212, "passed": 71, "discarded": 141}
    (HERE / "pool_71.json").write_text(json.dumps({"v": 1, "date": DAY.isoformat(), "counts": counts,
                                                   "needs_you": [], "items": items}, indent=1) + "\n",
                                       encoding="ascii")
    small, seen = [], set()
    for it in items:
        if it["ats"] in ("greenhouse", "lever", "ashby") and it["company"] not in seen:
            seen.add(it["company"])
            small.append(it)
        if len(small) == 12:
            break
    (HERE / "pool_12.json").write_text(json.dumps({"v": 1, "date": DAY.isoformat(), "counts": {},
                                                   "needs_you": [], "items": small}, indent=1) + "\n",
                                       encoding="ascii")
    late = [item(200 + k, name, "qc_micro", ats, 0, 0.8) for k, (name, ats) in
            enumerate([("Late Find Bio", "greenhouse"), ("Evening Labs", "lever"), ("Dusk Therapeutics", "ashby"),
                       ("Twilight Sciences", "greenhouse")])]
    (HERE / "new4.json").write_text(json.dumps({"v": 1, "date": DAY.isoformat(), "counts": {}, "needs_you": [],
                                                "items": late}, indent=1) + "\n", encoding="ascii")
    print(f"WROTE pool_71.json ({len(items)}), pool_12.json ({len(small)}), new4.json ({len(late)})")


if __name__ == "__main__":
    main()
