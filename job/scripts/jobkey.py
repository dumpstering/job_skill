"""Shared helpers for identifying a job posting: job keys, requisition IDs,
name normalization and text similarity. Imported by tracker.py and posting.py;
stdlib only.
"""
import re
from urllib.parse import parse_qs, urlparse

COMPANY_SUFFIXES = {"inc", "incorporated", "llc", "pllc", "ltd", "limited", "corp", "corporation",
                    "co", "company", "plc", "pbc", "gmbh", "ag", "sa", "sas", "sarl", "srl", "spa",
                    "bv", "nv", "pty", "kk", "oy", "ab", "lp", "llp", "the", "group", "holdings"}
TITLE_ABBREV = {"sr": "senior", "jr": "junior", "mgr": "manager", "eng": "engineer",
                "dev": "developer", "admin": "administrator", "assoc": "associate", "spec": "specialist"}
ID_PARAMS = ("gh_jid", "jk", "currentjobid", "jobid", "job_id", "jobreqid", "reqid", "req", "id")
REQ_PATTERNS = [
    # "Req ID: 123", "Job #123", and the bare "Requisition: REQ-26-1145-01" that posting.py writes.
    re.compile(r"\b(?:req(?:uisition)?|job|posting)\s*(?:(?:id|#|no\.?|number)\s*[:#]?|[:#])\s*([A-Z0-9][A-Z0-9_-]{2,})", re.I),
    re.compile(r"\b(JR-?\d{4,})\b", re.I),
    re.compile(r"\b(R-?\d{5,})\b"),
]
WORKDAY_REQ = re.compile(r"_((?:JR|R|REQ)?-?\d[\w-]*)$", re.I)


def norm_company(name):
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in COMPANY_SUFFIXES)


def norm_title(title):
    words = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split()
    return " ".join(TITLE_ABBREV.get(w, w) for w in words)


def parse_url(url):
    u = urlparse((url or "").strip())
    host = (u.netloc or "").lower().removeprefix("www.")
    parts = [p for p in u.path.split("/") if p]
    query = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
    return host, parts, query


def job_key(url):
    """A stable key for one posting, the same whichever link to it was pasted.

    Greenhouse, Lever and Ashby IDs are unique across companies, so their keys
    carry no company; Workday, iCIMS and SmartRecruiters IDs are per tenant.
    Anything else falls back to the URL without tracking parameters.
    """
    if not url:
        return ""
    host, parts, query = parse_url(url)

    if "greenhouse.io" in host:
        if "jobs" in parts and parts.index("jobs") + 1 < len(parts):
            return f"greenhouse:{parts[parts.index('jobs') + 1]}"
        if "token" in query:
            return f"greenhouse:{query['token']}"
    if "gh_jid" in query:
        return f"greenhouse:{query['gh_jid']}"
    if host.endswith("lever.co") and len(parts) >= 2:
        return f"lever:{parts[1].lower()}"
    if host.endswith("ashbyhq.com") and len(parts) >= 2:
        return f"ashby:{parts[1].lower()}"
    if "myworkdayjobs.com" in host:
        tenant = host.split(".")[0]
        for seg in reversed(parts):
            m = WORKDAY_REQ.search(seg)
            if m:
                return f"workday:{tenant}:{m.group(1).upper()}"
    if host.endswith("smartrecruiters.com") and len(parts) >= 2:
        return f"smartrecruiters:{parts[0].lower()}:{parts[1].split('-')[0]}"
    if host.endswith("icims.com") and "jobs" in parts and parts.index("jobs") + 1 < len(parts):
        return f"icims:{host.split('.')[0].removeprefix('careers-')}:{parts[parts.index('jobs') + 1]}"
    if "linkedin.com" in host:
        if "currentjobid" in query:
            return f"linkedin:{query['currentjobid']}"
        if "view" in parts and parts.index("view") + 1 < len(parts):
            m = re.search(r"(\d{6,})$", parts[parts.index("view") + 1])
            if m:
                return f"linkedin:{m.group(1)}"
    if "indeed." in host and "jk" in query:
        return f"indeed:{query['jk']}"
    keep = [f"{k}={query[k]}" for k in ID_PARAMS if k in query]
    return f"url:{host}/{'/'.join(parts).lower()}" + (f"?{'&'.join(keep)}" if keep else "")


def req_ids(text):
    """Requisition-style IDs found in posting text (each has at least one digit)."""
    found = set()
    for pat in REQ_PATTERNS:
        for m in pat.finditer(text or ""):
            rid = m.group(1).upper().replace("_", "-")
            if any(c.isdigit() for c in rid):
                found.add(rid)
    return found


def shingles(text, n=5):
    words = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0
