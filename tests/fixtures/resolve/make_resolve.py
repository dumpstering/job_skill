"""Writes the offline netfetch fixtures in tests/fixtures/resolve/ for
tests/test_resolve.py: one known Greenhouse board (acmebio), one known Lever
board (globexbio), every probe of an unknown company "Betabio" (404s, a
BambooHR redirect to www.bamboohr.com, a JazzHR redirect to www.jazzhr.com, a
Jobvite ?invalid=1 page, an empty Personio board), two careers pages for
probe_url detection, and a robots.txt that blocks a SmartRecruiters page.

Usage: python tests/fixtures/resolve/make_resolve.py
Every company, title and place is made up.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def gh_job(jid, title, loc, req):
    content = ("&lt;p&gt;Acme Biosciences grows bacteria for a living.&lt;/p&gt;&lt;h3&gt;Requirements&lt;/h3&gt;"
               "&lt;ul&gt;&lt;li&gt;Aseptic technique and PCR.&lt;/li&gt;&lt;/ul&gt;")
    return {"id": jid, "title": title, "location": {"name": loc}, "requisition_id": req,
            "absolute_url": f"https://job-boards.greenhouse.io/acmebio/jobs/{jid}", "company_name": "Acme Biosciences",
            "first_published": "2026-11-10T09:00:00-08:00", "updated_at": "2026-11-10T09:00:00-08:00",
            "content": content + f"&lt;p&gt;Opening {jid}.&lt;/p&gt;"}


def lever_job(uid, title, loc, workplace="onsite"):
    return {"id": uid, "text": title, "categories": {"location": loc, "commitment": "Full-time", "allLocations": [loc]},
            "createdAt": 1794700800000, "country": "US", "workplaceType": workplace,
            "hostedUrl": f"https://jobs.lever.co/globexbio/{uid}", "applyUrl": f"https://jobs.lever.co/globexbio/{uid}/apply",
            "description": f"<p>{title} at Globex Bio: culture cells and run ELISA. Opening {uid[-4:]}.</p>",
            "descriptionPlain": "", "lists": [], "additional": ""}


def main():
    files = {
        "gh_acmebio_list.json": {"jobs": [gh_job(900101, "Scientist II, Microbiology", "South San Francisco, CA", "R-101"),
                                          gh_job(900103, "Research Associate, Cell Biology", "South San Francisco, CA",
                                                 "R-103")], "meta": {"total": 2}},
        "gh_acmebio_900101.json": dict(gh_job(900101, "Scientist II, Microbiology", "South San Francisco, CA", "R-101"),
                                       questions=[]),
        "lever_globexbio.json": [lever_job("00000000-0000-4000-8000-00000000a001", "Research Associate II", "Fremont, CA"),
                                 lever_job("00000000-0000-4000-8000-00000000a002", "Research Associate II", "Newark, CA"),
                                 lever_job("00000000-0000-4000-8000-00000000a003", "Senior Scientist", "Fremont, CA")],
    }
    for name, data in files.items():
        (HERE / name).write_text(json.dumps(data, indent=1) + "\n", encoding="ascii")
    (HERE / "empty.html").write_text("<html><body>Nothing here.</body></html>\n", encoding="ascii")
    (HERE / "personio_empty.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<workzag-jobs>\n</workzag-jobs>\n',
                                             encoding="ascii")
    (HERE / "careers_deltabio.html").write_text(
        "<html><body><h1>Careers at Delta Bio</h1>\n"
        '<iframe src="https://boards.greenhouse.io/embed/job_board?for=deltabio&amp;b=x" width="100%"></iframe>\n'
        "</body></html>\n", encoding="ascii")
    (HERE / "careers_epsilonbio.html").write_text(
        "<html><body><h1>Join Epsilon Bio</h1>\n"
        '<a href="https://epsilonbio.wd5.myworkdayjobs.com/en-US/EpsilonCareers">Search open roles</a>\n'
        "</body></html>\n", encoding="ascii")
    (HERE / "robots_block_all.txt").write_text("User-agent: *\nDisallow: /\n", encoding="ascii")
    gone = {"status": 404}
    index = {
        "https://boards-api.greenhouse.io/v1/boards/acmebio/jobs?content=true": "gh_acmebio_list.json",
        "https://boards-api.greenhouse.io/v1/boards/acmebio/jobs/900101?questions=true": "gh_acmebio_900101.json",
        "https://api.lever.co/v0/postings/globexbio?mode=json&skip=0&limit=100": "lever_globexbio.json",
        "https://boards-api.greenhouse.io/v1/boards/betabio/jobs?content=true": gone,
        "https://api.lever.co/v0/postings/betabio?mode=json&skip=0&limit=100": gone,
        "https://api.ashbyhq.com/posting-api/job-board/betabio?includeCompensation=true": gone,
        "https://apply.workable.com/api/v1/widget/accounts/betabio?details=true": gone,
        "https://betabio.bamboohr.com/careers/list": {"file": "empty.html", "url": "https://www.bamboohr.com/"},
        "https://betabio.breezy.hr/json": gone,
        "https://betabio.applytojob.com/apply/jobs": {"file": "empty.html", "url": "https://www.jazzhr.com/"},
        "https://betabio.pinpointhq.com/postings.json": gone,
        "https://betabio.jobs.personio.de/xml?language=en": "personio_empty.xml",
        "https://betabio.teamtailor.com/jobs.json": gone,
        "https://api.rippling.com/platform/api/ats/v1/board/betabio/jobs": gone,
        "https://jobs.jobvite.com/betabio/jobs": {"file": "empty.html",
                                                  "url": "https://jobs.jobvite.com/betabio/jobs?invalid=1"},
        "https://careers.deltabio.example/jobs": "careers_deltabio.html",
        "https://www.epsilonbio.example/careers": "careers_epsilonbio.html",
        "https://jobs.smartrecruiters.com/robots.txt": "robots_block_all.txt",
    }
    (HERE / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="ascii")
    print(f"WROTE {len(files) + 6} files and index.json ({len(index)} entries)")


if __name__ == "__main__":
    main()
