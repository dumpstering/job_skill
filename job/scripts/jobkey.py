"""Shared helpers for identifying a job posting: job keys, requisition IDs,
name normalization, canonical URLs and text similarity (shingles, SimHash).
Imported by tracker.py, posting.py and the v1.2 runner; stdlib only.
"""
import hashlib
import math
import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

# v1.1 words, dropped wherever they appear (tracker matching depends on this).
ANYWHERE_SUFFIXES = {"inc", "incorporated", "llc", "pllc", "ltd", "limited", "corp", "corporation",
                     "co", "company", "plc", "pbc", "gmbh", "ag", "sa", "sas", "sarl", "srl", "spa",
                     "bv", "nv", "pty", "kk", "oy", "ab", "lp", "llp", "the", "group", "holdings"}
# v1.2 legal forms, dropped only at the end of a name ("SE" or "AS" mid-name stays).
TRAILING_SUFFIXES = {"lllp", "ulc", "pc", "pa", "psc", "dba", "se", "kg", "kgaa", "ohg", "ug", "gbr",
                     "as", "asa", "aps", "oyj", "sl", "slu", "ltda", "sprl", "bvba", "pte", "sdn", "bhd",
                     "pvt", "private", "spolka", "zoo", "kft", "zrt", "doo", "ooo", "sapi", "cv", "srls",
                     "nl", "lda", "ehf", "hf", "ky", "ks", "sro", "dac", "intl", "international"}
COMPANY_SUFFIXES = ANYWHERE_SUFFIXES | TRAILING_SUFFIXES
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
    """Lowercase words without legal forms: 'Acme Bio, Inc.' and 'Acme Bio' -> 'acme bio'."""
    s = re.sub(r"\b(?:[a-z]\.){2,}", lambda m: m.group(0).replace(".", ""), (name or "").lower())
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", s).split() if w not in ANYWHERE_SUFFIXES]
    while len(words) > 1 and words[-1] in TRAILING_SUFFIXES:
        words.pop()
    return " ".join(words)


def norm_title(title):
    words = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split()
    return " ".join(TITLE_ABBREV.get(w, w) for w in words)


def parse_url(url):
    u = urlparse((url or "").strip())
    host = (u.netloc or "").lower().removeprefix("www.")
    parts = [p for p in u.path.split("/") if p]
    query = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
    return host, parts, query


def job_key(url, ats=None):
    """A stable key for one posting, the same whichever link to it was pasted.

    Greenhouse, Lever and Ashby IDs are unique across companies, so their keys
    carry no company; Workday, iCIMS and SmartRecruiters IDs are per tenant.
    v1.2 adds BambooHR, Breezy, Workable, Rippling, Pinpoint, Teamtailor,
    Personio, ADP, Oracle, Paylocity, Dayforce, Jobvite, JazzHR and USAJOBS by
    host, and Phenom or Teamtailor on an employer's own domain when the caller
    passes ats ("phenom", "teamtailor"). Anything else falls back to the URL
    without tracking parameters.
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
    key = v12_key(host, parts, query, (ats or "").lower())
    if key:
        return key
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


LOCALE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.I)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def seg_after(parts, name):
    """The path segment after `name` (case-insensitive), or ''."""
    low = [p.lower() for p in parts]
    if name in low and low.index(name) + 1 < len(parts):
        return parts[low.index(name) + 1]
    return ""


def v12_key(host, parts, query, ats=""):
    """Keys for the ATSs added in v1.2 (spec 3.11), or '' when none applies."""
    sub = host.split(".")[0]
    if host.endswith(".bamboohr.com"):
        jid = seg_after(parts, "careers") or (query.get("id", "") if "view.php" in parts else "")
        if jid.isdigit():
            return f"bamboohr:{sub}:{jid}"
    if host.endswith(".breezy.hr"):
        m = re.match(r"([0-9a-f]{8,})", seg_after(parts, "p"), re.I)
        if m:
            return f"breezy:{sub}:{m.group(1).lower()}"
    if host == "apply.workable.com" and len(parts) >= 3 and parts[1].lower() == "j":
        return f"workable:{parts[0].lower()}:{parts[2].upper()}"
    if host == "ats.rippling.com":
        m = UUID.search(seg_after(parts, "jobs"))
        if m:
            return f"rippling:{m.group(0).lower()}"
    if host.endswith(".pinpointhq.com"):
        jid = seg_after(parts, "postings") or seg_after(parts, "jobs")
        m = UUID.search(jid) or re.match(r"\d+", jid)
        if m:
            return f"pinpoint:{sub}:{m.group(0).lower()}"
    if host.endswith(".teamtailor.com") or ats == "teamtailor":
        m = re.match(r"(\d+)", seg_after(parts, "jobs"))
        if m:
            return f"teamtailor:{sub if host.endswith('.teamtailor.com') else host}:{m.group(1)}"
    if re.search(r"\.jobs\.personio\.(de|com)$", host):
        m = re.match(r"(\d+)", seg_after(parts, "job"))
        if m:
            return f"personio:{sub}:{m.group(1)}"
    if host == "workforcenow.adp.com" and query.get("cid") and query.get("jobid"):
        return f"adp:{query['cid'].lower()}:{query['jobid']}"
    if host == "myjobs.adp.com" and parts and (query.get("reqid") or query.get("jobid")):
        return f"adp:{parts[0].lower()}:{query.get('reqid') or query.get('jobid')}"
    m = re.fullmatch(r"([a-z0-9-]+)\.fa\.[a-z0-9-]+\.oraclecloud\.com", host)
    if m:
        jid = seg_after(parts, "job") or seg_after(parts, "preview")
        if jid.isdigit():
            return f"oracle:{m.group(1)}:{jid}"
    if host == "recruiting.paylocity.com":
        jid = seg_after(parts, "details")
        if jid.isdigit():
            return f"paylocity:{jid}"
    if host.endswith("dayforcehcm.com"):
        jid = seg_after(parts, "jobs")
        rest = parts[1:] if parts and LOCALE.match(parts[0]) else parts
        if jid.isdigit() and rest:
            return f"dayforce:{rest[0].lower()}:{jid}"
    if host == "jobs.jobvite.com" and len(parts) >= 3 and parts[1].lower() == "job":
        return f"jobvite:{parts[0].lower()}:{parts[2]}"
    if host.endswith(".applytojob.com"):
        jid = seg_after(parts, "details") or seg_after(parts, "apply")
        if jid and jid.lower() != "jobs":
            return f"jazzhr:{sub}:{jid}"
    if host == "usajobs.gov":
        jid = seg_after(parts, "job") or seg_after(parts, "viewdetails")
        if jid.isdigit():
            return f"usajobs:{jid}"
    if ats == "phenom":
        jid = seg_after(parts, "job")
        if jid:
            return f"phenom:{host}:{jid.upper()}"
    return ""


# Query parameters that track a click rather than name a job. gh_src is also a
# referral marker, so canonical_url hands it back separately (contacts A17).
TRACKING = re.compile(
    r"^(utm_.*|gh_src|gclid|gbraid|wbraid|fbclid|msclkid|dclid|yclid|igshid|mc_cid|mc_eid|_hs.*|hsa_.*"
    r"|_ga|_gl|ref|referrer|refid|ref_id|src|source|lever-source.*|lever-origin|trk|trkinfo|trackingid"
    r"|tracking_id|ccuid|codes|iis|iisn|campaign|campaignid|cmpid|adid|ad_id|rx_.*)$", re.I)


def canonical_url(url):
    """(url_norm, gh_src). url_norm is https, lowercase host without www. or a
    default port, no fragment or trailing slash, tracking parameters dropped and
    the rest sorted. gh_src is '' when absent; it is never part of identity."""
    u = urlparse((url or "").strip())
    host = (u.hostname or "").lower().removeprefix("www.")
    if u.port and u.port not in (80, 443):
        host = f"{host}:{u.port}"
    path = re.sub(r"/{2,}", "/", u.path or "")
    path = path.rstrip("/") if path != "/" else ""
    gh_src, kept = "", []
    for k, v in parse_qsl(u.query, keep_blank_values=True):
        if k.lower() == "gh_src" and not gh_src:
            gh_src = v
        if not TRACKING.match(k):
            kept.append((k, v))
    query = urlencode(sorted(kept))
    return (f"https://{host}{path}" + (f"?{query}" if query else "")) if host else "", gh_src


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


STOPWORDS = set("a an and are as at be by for from has have in is it of on or our that the this to we "
                "will with you your".split())


def simhash_features(text):
    """Word weights 1 + ln(count), stopwords dropped. Single words, not word
    pairs: an agency's changed intro and footer then move fewer bits (on a
    fixture with about 2.5% new vocabulary, pairs averaged 4.8 bits and single
    words 3.4)."""
    counts = {}
    for w in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split():
        if w not in STOPWORDS and len(w) > 1:
            counts[w] = counts.get(w, 0) + 1
    return {w: 1 + math.log(c) for w, c in counts.items()}


def simhash64(text):
    """64-bit SimHash of a description as 16 hex characters (oss A14). Lightly
    reworded reposts land within a few bits; unrelated postings land far apart.
    Empty text gives sixteen zeros."""
    v = [0] * 64
    for feat, weight in simhash_features(text).items():
        h = int.from_bytes(hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(64):
            v[i] += weight if (h >> i) & 1 else -weight
    return f"{sum(1 << i for i in range(64) if v[i] > 0):016x}"


def hamming(a, b):
    """Differing bits between two SimHashes (hex strings or ints)."""
    a = int(a, 16) if isinstance(a, str) else int(a)
    b = int(b, 16) if isinstance(b, str) else int(b)
    return bin(a ^ b).count("1")
