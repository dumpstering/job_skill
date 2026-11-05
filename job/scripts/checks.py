"""Mechanical checks that run after build_resume.py builds the files.

Three checks, each printing lines the agent must act on (fix the resume, or
carry the item into "Verify before sending"). None of them changes the exit
code:

    PARSE    the built PDF read back as text: name, contact, headings, entries
    FACT     numbers, titles, employers and dates against the --sources files
    KEYWORD  posting terms (--terms) found on the resume, in sources, or neither

Usage from build_resume.py: checks.run_checks(data, pdf_path, txt_path,
sources=[files], terms="terms.json"). Needs pypdf (or pdfplumber) only for
PARSE; both are imported lazily.
"""
import json
import re
from decimal import Decimal
from pathlib import Path

EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
CURLY = {chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'}

CID_RE = re.compile(r"\(cid:\d+\)")
CID_WARN, CID_FAIL = 0.005, 0.05
MIN_TEXT = 50
CONTACT_FRACTION = 0.25
PARSE_SKIPPED = "PARSE skipped: pip install pypdf to check how the PDF reads back"


def norm(text):
    """Dashes and quotes made plain, whitespace collapsed."""
    text = text.replace(EN_DASH, "-").replace(EM_DASH, "-")
    for k, v in CURLY.items():
        text = text.replace(k, v)
    return re.sub(r"\s+", " ", text).strip()


# ---------- numbers ----------

NUMBER_WORDS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(), 1)}
SUFFIX = {"k": 10**3, "thousand": 10**3, "m": 10**6, "million": 10**6,
          "b": 10**9, "bn": 10**9, "billion": 10**9}

# One numeric token. Not inside a word (so "S3", "EC2", "3rd", "24x7" are
# skipped), not followed by ".5pt"-style tails. Suffix letters k/m/b/bn must
# touch the digits ("$550K"); the words thousand/million/billion may follow a space.
NUM_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?P<cur>\$)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:(?P<pct>\s?%|[\s-]?percent)"
    r"|(?P<suf>bn|[kmb]|\s?(?:thousand|million|billion))"
    r"|(?P<times>x))?"
    r"(?P<plus>\+)?(?![A-Za-z0-9])(?!\.\d)",
    re.I)
_WORDS = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
WORD_RE = re.compile(r"\b(?P<w>" + _WORDS + r")\b(?P<pct>[\s-]*percent\b)?", re.I)
DOZEN_RE = re.compile(r"\b(?:(?P<w>" + _WORDS + r"|a)[\s-]+)?dozen\b", re.I)


def numbers(text):
    """[(token as written, (kind, value))] for the digit numbers in text.

    kind is "percent", "times" ("3x") or "plain" ($ and suffixes fold into plain).
    """
    out = []
    for m in NUM_RE.finditer(text):
        value = Decimal(m.group("num").replace(",", ""))
        if m.group("suf"):
            value *= SUFFIX[m.group("suf").strip().lower()]
        kind = "percent" if m.group("pct") else "times" if m.group("times") else "plain"
        out.append((m.group(0), (kind, value)))
    return out


def source_numbers(text):
    """Set of (kind, value) in the sources: digit numbers plus one..twenty and dozens."""
    keys = {key for _, key in numbers(text)}
    for m in WORD_RE.finditer(text):
        kind = "percent" if m.group("pct") else "plain"
        keys.add((kind, Decimal(NUMBER_WORDS[m.group("w").lower()])))
    for m in DOZEN_RE.finditer(text):
        w = (m.group("w") or "a").lower()
        keys.add(("plain", Decimal(12 * NUMBER_WORDS.get(w, 1))))
    return keys


# ---------- dates ----------

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
MONTH_NUM = {}
for _i, _name in enumerate(MONTHS, 1):
    MONTH_NUM[_name] = MONTH_NUM[_name[:3]] = _i
MONTH_NUM["sept"] = 9
DATE_SPLIT = re.compile(r"\s+-\s+|\s*[" + EN_DASH + EM_DASH + r"]\s*|(?<=\d{4})\s*-\s*(?=\d{4}\b)")
MONTH_YEAR = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$")


def date_parts(dates):
    return [p.strip() for p in DATE_SPLIT.split(dates or "") if p.strip()]


def month_year_in(month, year, text):
    """True if month/year is in text as Mar 2021, March 2021, Mar. 2021, 03/2021, 3/2021 or 2021-03."""
    full = MONTHS[month - 1]
    names = {full, full[:3]} | ({"sept"} if month == 9 else set())
    pats = [
        r"(?<![A-Za-z])(?:" + "|".join(sorted(names)) + r")\.?[\s,]+" + str(year) + r"(?!\d)",
        r"(?<!\d)0?" + str(month) + r"\s*[/.-]\s*" + str(year) + r"(?!\d)",
        r"(?<!\d)" + str(year) + r"\s*[/-]\s*0?" + str(month) + r"(?!\d)",
    ]
    return any(re.search(p, text, re.I) for p in pats)


def date_in_sources(part, text):
    """None for "Present"; else True/False for whether the date part is in text."""
    if part.lower() in ("present", "current"):
        return None
    if re.fullmatch(r"\d{4}", part):
        return re.search(r"(?<!\d)" + part + r"(?!\d)", text) is not None
    m = MONTH_YEAR.match(part)
    if m and m.group(1).lower() in MONTH_NUM:
        return month_year_in(MONTH_NUM[m.group(1).lower()], int(m.group(2)), text)
    return norm(part).lower() in norm(text).lower()


# ---------- keywords ----------

def term_regex(term):
    words = [re.escape(w) for w in term.split()]
    core = r"\s+".join(words)
    plural = r"(?:es|s)?" if term[-1:].isalpha() else ""
    return re.compile(r"(?<![A-Za-z0-9])" + core + plural + r"(?![A-Za-z0-9])", re.I)


def term_found(text, term, aliases=()):
    return any(term_regex(t).search(text) for t in [term, *aliases] if t.strip())


def load_terms(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read {path}: {e}")
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON list")
    terms = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            terms.append((item.strip(), []))
        elif isinstance(item, dict) and isinstance(item.get("term"), str) and item["term"].strip():
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
                raise ValueError(f'aliases of "{item["term"]}" must be a list of strings')
            terms.append((item["term"].strip(), aliases))
        else:
            raise ValueError(f"bad item in {path}: {item!r}")
    return terms


def keyword_check(resume_text, source_text, terms, sources_given):
    on, in_src, none = [], [], []
    for term, aliases in terms:
        if term_found(resume_text, term):
            on.append(term)
        elif term_found(resume_text, term, aliases):
            # The rules say to use the posting's own term for a renamed product.
            used = next(a for a in aliases if term_found(resume_text, a))
            on.append(f'{term} (only as "{used}"; use the posting\'s term)')
        elif source_text is not None and term_found(source_text, term, aliases):
            in_src.append(term)
        else:
            none.append(term)
    join = lambda xs: ", ".join(xs) if xs else "(none)"
    if source_text is None:
        why = "(no --sources given)" if not sources_given else "(no readable --sources)"
        second = f"KEYWORD in your sources, not on this resume: {why}"
        third = f"KEYWORD not evidenced: {join(none)}" + (" (sources not searched)" if none else "")
    else:
        second = f"KEYWORD in your sources, not on this resume: {join(in_src)}"
        third = f"KEYWORD not evidenced: {join(none)}"
    return [f"KEYWORD on resume: {join(on)}", second, third]


# ---------- resume content ----------

def resume_texts(data):
    """[(where, text)] for every place a number can hide. Names match the lint's."""
    out = []
    if "letter" in data:
        for i, p in enumerate(data["letter"]["paragraphs"], 1):
            out.append((f"letter paragraph {i}", p))
        return out
    for s in data["sections"]:
        h = s["heading"]
        if "paragraph" in s:
            out.append(("Summary" if h.lower() == "summary" else h, s["paragraph"]))
        for ln in s.get("lines", []):
            out.append((f'{h} line "{ln["label"]}"', ln["text"]))
        for e in s.get("entries", []):
            out.append((f'{h} / {e["title"]} title', e["title"]))
            for i, b in enumerate(e.get("bullets", []), 1):
                out.append((f'{h} / {e["title"]} bullet {i}', b))
        for i, b in enumerate(s.get("bullets", []), 1):
            out.append((f"{h} bullet {i}", b))
    return out


def fact_check(data, source_text):
    out, n = [], 0
    have = source_numbers(source_text)
    flat = norm(source_text).lower()
    for where, text in resume_texts(data):
        seen = set()
        for token, key in numbers(text):
            if token in seen:
                continue
            seen.add(token)
            n += 1
            if key not in have:
                out.append(f'FACT number "{token}" in {where} is not in your sources (if derived, list it under Verify)')
    for s in data.get("sections", []):
        for e in s.get("entries", []):
            n += 1
            if norm(e["title"]).lower() not in flat:
                out.append(f'FACT title "{e["title"]}" not found in your sources')
            if e.get("org"):
                n += 1
                if norm(e["org"]).lower() not in flat:
                    out.append(f'FACT employer "{e["org"]}" not found in your sources')
            for part in date_parts(e.get("dates")):
                found = date_in_sources(part, source_text)
                if found is None:
                    continue
                n += 1
                if not found:
                    out.append(f'FACT date "{part}" of {e["title"]} not found in your sources in any format')
    return out or [f"FACT ok ({n} checked)"]


# ---------- parse-back ----------

def read_pdf_text(path):
    """PDF text, or None when neither pypdf nor pdfplumber is installed."""
    try:
        from pypdf import PdfReader
    except ImportError:
        pass
    else:
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
    try:
        import pdfplumber
    except ImportError:
        return None
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((pg.extract_text() or "") for pg in pdf.pages)


def parse_back(data, pdf_path):
    text = read_pdf_text(pdf_path)
    if text is None:
        return [PARSE_SKIPPED]
    if len(text.strip()) < MIN_TEXT:
        return ["PARSE FAIL no text layer"]
    flat = norm(text)
    fails, warns, n = [], [], 1

    n += 1
    cids = len(CID_RE.findall(text))
    rest = len(CID_RE.sub("", re.sub(r"\s", "", text)))
    ratio = cids / (cids + rest) if cids + rest else 0.0
    if ratio > CID_FAIL:
        fails.append(f"PARSE FAIL {ratio:.1%} of the PDF text is unreadable (cid:) glyphs; an ATS will not read it")
    elif ratio > CID_WARN:
        warns.append(f"PARSE WARN {ratio:.1%} of the PDF text is unreadable (cid:) glyphs")

    n += 1
    if norm(data["name"]) not in flat:
        fails.append(f'PARSE name "{data["name"]}" not found in the PDF text')

    emails = [c for c in data["contact"] if "@" in c]
    for c in emails:
        n += 1
        pos = flat.find(norm(c))
        if pos < 0:
            fails.append(f'PARSE contact "{c}" not found in the PDF text')
        elif "letter" not in data and pos > len(flat) * CONTACT_FRACTION:
            fails.append(f'PARSE contact "{c}" is at {pos / len(flat):.0%} of the PDF text, not in the first 25%')

    if "letter" in data:
        for i, p in enumerate(data["letter"]["paragraphs"], 1):
            n += 1
            start = norm(" ".join(p.split()[:6]))
            if start not in flat:
                fails.append(f'PARSE letter paragraph {i} start "{start}" not found in the PDF text')
    else:
        cursor = 0

        def locate(label, what, part, start):
            """Position of part at or after start; records a failure and returns -1 if not there."""
            part = norm(part)
            pos = flat.find(part, start)
            if pos < 0:
                kind = "is out of order in" if flat.find(part) >= 0 else "not found in"
                fails.append(f'PARSE {label} "{part}"{what} {kind} the PDF text')
            return pos

        for s in data["sections"]:
            n += 1
            pos = locate("heading", "", s["heading"], cursor)
            if pos >= 0:
                cursor = pos + len(norm(s["heading"]))
            for e in s.get("entries", []):
                n += 1
                tpos = locate("entry title", "", e["title"], cursor)
                if tpos < 0:
                    continue
                cursor = tpos + len(norm(e["title"]))
                if e.get("org"):
                    n += 1
                    locate("entry org", f' of "{e["title"]}"', e["org"], tpos)
                if e.get("dates"):
                    n += 1
                    locate("entry dates", f' of "{e["title"]}"', e["dates"], tpos)

    out = warns + fails
    if not fails:
        out.append(f"PARSE ok ({n} checks)")
    return out


# ---------- entry point ----------

def read_sources(paths):
    """(concatenated text or None, [error lines])."""
    chunks, errors = [], []
    for p in paths:
        try:
            chunks.append(Path(p).read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            errors.append(f"FACT source not read, checked without it: {p} ({type(e).__name__})")
    return ("\n".join(chunks) if chunks else None), errors


def guarded(tag, fn):
    try:
        return fn()
    except Exception as e:  # a check must never break the build
        return [f"{tag} skipped: {type(e).__name__}: {e}"]


def run_checks(data, pdf_path=None, txt_path=None, sources=None, terms=None):
    """Lines to print after the WARN lines. pdf_path is None when no PDF was built."""
    out = []
    if pdf_path:
        out += guarded("PARSE", lambda: parse_back(data, pdf_path))
    source_text = None
    if sources:
        source_text, errors = read_sources(sources)
        out += errors
        if source_text is not None:
            out += guarded("FACT", lambda: fact_check(data, source_text))
    if terms:
        def keywords():
            resume_text = Path(txt_path).read_text(encoding="utf-8")
            return keyword_check(resume_text, source_text, load_terms(terms), bool(sources))
        out += guarded("KEYWORD", keywords)
    return out
