"""Mechanical checks that run after build_resume.py builds the files.

Each check prints lines the agent must act on (fix the resume, or carry the
item into "Verify before sending"). None of them changes the exit code; only
build_resume.py --strict turns failures into exit 6.

    PARSE     the built PDF read back as text: name, contact, headings, entries
    FACT      numbers, titles, employers and dates against the --sources files
              (v1.2 flags: numbers written as words too, each role's title,
              employer and dates together, "Present" only where a source
              says so, certification names as written and never "Not held")
    EVIDENCE  (v1.2) every bullet cites library IDs that exist, every number
              in it appears in a cited entry, and a cited entry filed under a
              role heading belongs to the role the bullet sits under
    TECHNIQUE (v1.2) every Skills/Techniques item is hands-on in
              techniques.md, or named in the sources, and never "Not held"
    CITE      (v1.2) every printed citation equals its publications.md line or
              its approved ascii: rendering
    LETTER    (v1.2) a letter names the posting's company and role, and has
              no unfilled {slot}
    KEYWORD   posting terms (--terms) found on the resume, in sources, or neither
              (v1.2 flags: and a fourth line, terms the user said they lack)

Under v1.2 flags a job's notes.md counts as a source only for its
`## Answers` section, and EVIDENCE reads the achievement library alone.

After them build_resume.py prints VERDICT ok|fail (v1.2 flags only), from
strict_failures() and verdict() below.

Usage from build_resume.py: checks.run_checks(data, pdf_path, txt_path,
sources=[files], terms="terms.json", pubs=..., evidence=..., posting=...).
Needs pypdf (or pdfplumber) only for PARSE and the CITE read-back; both are
imported lazily.
"""
import difflib
import re
from decimal import Decimal
from pathlib import Path

EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
CURLY = {chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'}
# Hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar, minus.
DASHES = [chr(c) for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)]

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
# v1.2: numbers written as words on the resume side too ("twelve", "twenty-four",
# "a dozen", "a decade"). "one" is left out there: "one of 8", "one-on-one" and
# "no one" are rarely a count, and one is never an inflated figure.
_UNITS = "one two three four five six seven eight nine".split()
COMPOUND_RE = re.compile(r"\btwenty[\s-](?P<u>" + "|".join(_UNITS) + r")\b(?P<pct>[\s-]*percent\b)?", re.I)
DECADE_RE = re.compile(r"\b(?P<w>" + _WORDS + r"|a)[\s-]+decades?\b", re.I)


def numbers(text, words=False):
    """[(token as written, (kind, value))] for the digit numbers in text.

    kind is "percent", "times" ("3x") or "plain" ($ and suffixes fold into plain).
    With words (v1.2) it also reads two..twenty-nine, "a dozen" and "a decade"
    as written words, so "twelve" is checked exactly like "12".
    """
    out = []
    for m in NUM_RE.finditer(text):
        value = Decimal(m.group("num").replace(",", ""))
        if m.group("suf"):
            value *= SUFFIX[m.group("suf").strip().lower()]
        kind = "percent" if m.group("pct") else "times" if m.group("times") else "plain"
        out.append((m.group(0), (kind, value)))
    if words:
        out += word_numbers(text, skip_one=True)
    return out


def word_numbers(text, skip_one=False):
    """[(token, (kind, value))] for numbers written as words, each word read once."""
    out, taken = [], []
    for m in COMPOUND_RE.finditer(text):
        kind = "percent" if m.group("pct") else "plain"
        out.append((m.group(0), (kind, Decimal(20 + NUMBER_WORDS[m.group("u").lower()]))))
        taken.append(m.span())
    for rx, scale in ((DOZEN_RE, 12), (DECADE_RE, 10)):
        for m in rx.finditer(text):
            w = (m.group("w") or "a").lower()
            out.append((m.group(0), ("plain", Decimal(scale * NUMBER_WORDS.get(w, 1)))))
            taken.append(m.span())
    for m in WORD_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        w = m.group("w").lower()
        if skip_one and w == "one":
            continue
        kind = "percent" if m.group("pct") else "plain"
        out.append((m.group(0), (kind, Decimal(NUMBER_WORDS[w]))))
    return out


def source_numbers(text, words=False):
    """Set of (kind, value) in the sources: digit numbers plus one..twenty and dozens
    (with words, v1.2: also twenty-one..twenty-nine and decades)."""
    keys = {key for _, key in numbers(text)}
    for m in WORD_RE.finditer(text):
        kind = "percent" if m.group("pct") else "plain"
        keys.add((kind, Decimal(NUMBER_WORDS[m.group("w").lower()])))
    for m in DOZEN_RE.finditer(text):
        w = (m.group("w") or "a").lower()
        keys.add(("plain", Decimal(12 * NUMBER_WORDS.get(w, 1))))
    if words:
        keys |= {key for _, key in word_numbers(text)}
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

def term_regex(term, stems=False):
    """The term as a whole word, plural allowed. With stems (v1.2 KEYWORD), a
    single alphabetic word of 4+ letters also matches its -ed, -ing and -er
    forms, so "mentor" is found in "Mentored" and "validate" in "validating"."""
    words = [re.escape(w) for w in term.split()]
    core = r"\s+".join(words)
    plural = r"(?:es|s)?" if term[-1:].isalpha() else ""
    if stems and len(term.split()) == 1 and term.isalpha() and len(term) >= 4:
        if term[-1:].lower() == "e":
            core, plural = re.escape(term[:-1]), r"(?:e|es|ed|er|ers|ing)"
        else:
            plural = r"(?:s|es|ed|er|ers|ing)?"
    return re.compile(r"(?<![A-Za-z0-9])" + core + plural + r"(?![A-Za-z0-9])", re.I)


def term_found(text, term, aliases=(), stems=False):
    return any(term_regex(t, stems).search(text) for t in [term, *aliases] if t.strip())


def load_terms(path):
    import jobfs  # the safe JSON loader (shared decision R3); build_resume puts its folder on sys.path
    try:
        raw = jobfs.read_json(path, jobfs.MAX_FILE)
    except (OSError, ValueError) as e:  # BadJson: too big, nested deeper than 64, not JSON
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


def keyword_check(resume_text, source_text, terms, sources_given, v12=False):
    """The three KEYWORD lines. v12: -ed/-ing forms count, the library's "Not
    held:" lines are not a source, and a fourth line names the posting terms
    the user said they do not have (gaps for Block B, never worked in)."""
    on, in_src, none, not_held = [], [], [], []
    held_text, refused = source_text, ""
    if v12 and source_text is not None:
        held_text, refused = split_not_held(source_text)
    for term, aliases in terms:
        if term_found(resume_text, term, stems=v12):
            on.append(term)
        elif term_found(resume_text, term, aliases, stems=v12):
            # The rules say to use the posting's own term for a renamed product.
            used = next(a for a in aliases if term_found(resume_text, a, stems=v12))
            on.append(f'{term} (only as "{used}"; use the posting\'s term)')
        elif held_text is not None and term_found(held_text, term, aliases, stems=v12):
            in_src.append(term)
        elif v12 and refused and term_found(refused, term, aliases, stems=v12):
            not_held.append(term)
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
    lines = [f"KEYWORD on resume: {join(on)}", second, third]
    if v12 and source_text is not None:
        lines.append(f"KEYWORD not held (you said you lack these): {join(not_held)}")
    return lines


NOT_HELD_LINE = re.compile(r"^[ \t]*[-*][ \t]+(?:\[L-\d{4,}\][ \t]*)?not held\b:?[ \t]*(?P<what>[^\n]*?)[ \t]*$",
                           re.I | re.M)


def split_not_held(text):
    """(text without the library's "Not held:" lines, their subjects one per line).
    "Not held: QIIME 2, shotgun metagenomics." gives two subjects."""
    subjects = []
    for m in NOT_HELD_LINE.finditer(text):
        subjects += [s.strip().rstrip(".").strip() for s in re.split(r"[;,]", m.group("what"))]
    return NOT_HELD_LINE.sub("", text), "\n".join(s for s in subjects if s)


# ---------- resume content ----------

# Library and publication IDs in source text: [L-0001], [P-001], and the
# library's <!-- last-id: L-0042 --> line. Their digits are labels, never
# facts, so FACT blanks them before reading numbers.
ID_MARK = re.compile(r"\[(?:L-\d{4,}|P-\d{3,})\]|<!--\s*last-id:[^>]*-->")


def strip_ids(text):
    return ID_MARK.sub(" ", text)


def bullet_text(b):
    """A bullet is a string (v1.1) or {"text", "ev", "keep"} (v1.2)."""
    return b.get("text", "") if isinstance(b, dict) else b


def is_citation(section):
    """A Publications-style section: citations by ID, or (with --publications)
    bullets under a Publications, Presentations or Patents heading."""
    return "citations" in section or bool(section.get("_cite"))


def resume_texts(data):
    """[(where, text)] for every place a number can hide. Names match the lint's.
    Citations are left out: CITE checks them against publications.md."""
    out = []
    if "letter" in data:
        for i, p in enumerate(data["letter"]["paragraphs"], 1):
            out.append((f"letter paragraph {i}", p))
        return out
    for s in data["sections"]:
        h = s["heading"]
        if is_citation(s):
            continue
        if "paragraph" in s:
            out.append(("Summary" if h.lower() == "summary" else h, s["paragraph"]))
        for ln in s.get("lines", []):
            out.append((f'{h} line "{ln["label"]}"', ln["text"]))
        for e in s.get("entries", []):
            out.append((f'{h} / {e["title"]} title', e["title"]))
            for i, b in enumerate(e.get("bullets", []), 1):
                out.append((f'{h} / {e["title"]} bullet {i}', bullet_text(b)))
        for i, b in enumerate(s.get("bullets", []), 1):
            out.append((f"{h} bullet {i}", bullet_text(b)))
    return out


def fact_check(data, source_text, v12=False):
    """Numbers, titles, employers and dates against the sources. v12 adds: numbers
    written as words; each entry's title, employer and dates as one role (one
    source line names them together, "Present" only where a source says so);
    and certification and license names copied from the sources, never one the
    library lists as "Not held"."""
    out, n = [], 0
    have = source_numbers(source_text, words=v12)
    flat = norm(source_text).lower()
    for where, text in resume_texts(data):
        seen = set()
        for token, key in numbers(text, words=v12):
            if token in seen:
                continue
            seen.add(token)
            n += 1
            if key not in have:
                out.append(f'FACT number "{token}" in {where} is not in your sources (if derived, list it under Verify)')
    for s in data.get("sections", []):
        for e in s.get("entries", []):
            n += 1
            title_ok = norm(e["title"]).lower() in flat
            if not title_ok:
                out.append(f'FACT title "{e["title"]}" not found in your sources')
            org_ok = True
            if e.get("org"):
                n += 1
                org_ok = norm(e["org"]).lower() in flat
                if not org_ok:
                    out.append(f'FACT employer "{e["org"]}" not found in your sources')
            for part in date_parts(e.get("dates")):
                found = date_in_sources(part, source_text)
                if found is None:
                    continue
                n += 1
                if not found:
                    out.append(f'FACT date "{part}" of {e["title"]} not found in your sources in any format')
            if v12 and title_ok and org_ok:
                n += 1
                out += role_problems(e, source_text)
    if v12:
        lines, k = credential_problems(data, source_text)
        out += lines
        n += k
    return out or [f"FACT ok ({n} checked)"]


PRESENT_RE = re.compile(r"(?<![A-Za-z])(?:present|current|now)(?![A-Za-z])", re.I)


def role_problems(e, source_text):
    """v12: the entry's title, employer and dates are one role in the sources.

    For a role with bullets, some source line (the library's `### Title |
    Company | Mon YYYY - Mon YYYY` heading, or the master resume's role line)
    names the title and the employer together, with every date part. "Present"
    (or "Current") needs such a line that says so, and fails when one gives
    this role's start with a real end date; that last rule also covers entries
    without bullets (education, a role listed alone), whose layout in a master
    resume varies too much for the rest."""
    title = norm(e["title"]).lower()
    org = norm(e.get("org") or "").lower()
    lines = [ln for ln in source_text.splitlines()
             if title in norm(ln).lower() and (not org or org in norm(ln).lower())]
    who = f'{e["title"]} | {e["org"]}' if e.get("org") else e["title"]
    role = bool(e.get("bullets"))
    if not lines:
        if not role:
            return []
        return [f'FACT "{who}" is not one role in your sources (no line names this title with this employer)']
    parts = date_parts(e.get("dates"))
    if not parts:
        return []
    ongoing = parts[-1].lower() in ("present", "current")
    fixed = [p for p in parts if p.lower() not in ("present", "current")]
    dated = [ln for ln in lines if all(date_in_sources(p, ln) for p in fixed)]
    if not dated:
        if not role:
            return []
        return [f'FACT dates "{e.get("dates")}" of {who} are not the dates your sources give for that role']
    if ongoing:
        current = [ln for ln in dated if PRESENT_RE.search(ln)]
        ended = [ln for ln in dated if not PRESENT_RE.search(ln) and len(date_parts(role_dates(ln))) > len(fixed)]
        if not current or ended:
            shown = norm(ended[0])[:90] if ended else norm(dated[0])[:90]
            return [f'FACT "{parts[-1]}" for {who}: your sources do not show this role as current ("{shown}")']
    return []


def role_dates(line):
    """The dates part of a source role line ("... | 06/2023 - 10/2026"), else the line."""
    parts = [p.strip() for p in line.split("|")]
    return parts[-1] if len(parts) > 1 else line


CREDENTIAL_HEADING = re.compile(r"certif|licen", re.I)
PARENS = re.compile(r"\([^()]*\)")


def credential_items(data):
    """[(where, name)] for each item under a Certifications or Licenses heading:
    the bullet or line text with parenthesized parts removed."""
    out = []
    for s in data.get("sections", []):
        if is_citation(s) or not CREDENTIAL_HEADING.search(s["heading"]):
            continue
        texts = [(f'{s["heading"]} bullet {i}', bullet_text(b)) for i, b in enumerate(s.get("bullets", []), 1)]
        texts += [(f'{s["heading"]} line "{ln["label"]}"', ln["text"]) for ln in s.get("lines", [])]
        for where, text in texts:
            name = norm(PARENS.sub(" ", text)).strip(" .,;")
            if name:
                out.append((where, name))
    return out


def credential_problems(data, source_text):
    """v12: every certification or license name is in the sources (outside the
    library's "Not held:" lines) and is not one of those lines."""
    held, refused = split_not_held(source_text)
    flat = norm(held).lower()
    out, n = [], 0
    for where, name in credential_items(data):
        n += 1
        hit = next((r for r in refused.splitlines() if r.strip() and
                    (term_found(name, r.strip()) or term_found(r.strip(), name))), None)
        if hit:
            out.append(f'FACT "{name}" in {where}: your library says "Not held: {hit.strip()}"')
        elif name.lower() not in flat:
            out.append(f'FACT certification "{name}" in {where} is not in your sources as written '
                       "(copy the name from master_resume.md, or ask)")
    return out, n


# ---------- evidence (v1.2) ----------

# Credential and list sections carry no ev tags: their lines come from the
# master resume and FACT checks them. Every other bullet cites the library.
EV_EXEMPT = {
    "education", "certifications", "certification", "licenses", "licensure",
    "licenses and certifications", "certifications and licenses", "education and certifications",
    "awards", "honors", "awards and honors", "honors and awards", "memberships",
    "professional memberships", "service", "professional service", "references", "languages",
    "grants", "fellowships", "grants and fellowships", "interests",
}


def evidence_units(data, with_entry=False):
    """[(where, bullet)] for every bullet EVIDENCE covers; with_entry adds the
    resume entry the bullet sits under (None for a section's own bullets)."""
    out = []
    for s in data.get("sections", []):
        h = s["heading"]
        if is_citation(s) or norm(h).lower() in EV_EXEMPT:
            continue
        for e in s.get("entries", []):
            for i, b in enumerate(e.get("bullets", []), 1):
                out.append((f'{h} / {e["title"]} bullet {i}', b, e) if with_entry else (f'{h} / {e["title"]} bullet {i}', b))
        for i, b in enumerate(s.get("bullets", []), 1):
            out.append((f"{h} bullet {i}", b, None) if with_entry else (f"{h} bullet {i}", b))
    return out


def has_ev(data):
    return any(isinstance(b, dict) and "ev" in b for _, b in evidence_units(data))


def library_entries(paths, headings=False):
    """{L-id: entry text} from every source file that holds [L-] IDs, or None.
    With headings, {L-id: (entry text, the ### heading above it)}."""
    import library  # same folder; imported here so checks.py stays standalone
    found = {}
    for p in paths or []:
        try:
            text = Path(p).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for entry in library.parse(text).entries:
            if entry.id:
                found.setdefault(entry.id, (entry.text, entry.heading) if headings else entry.text)
    return found or None


def month_index(part):
    """year*12 + month-1 for "Mar 2021", "03/2021", "2021-03"; "y<year>" for a
    bare year; "present" for Present/Current; None when unreadable."""
    p = norm(part).strip().rstrip(".").lower()
    if p in ("present", "current", "now"):
        return "present"
    m = MONTH_YEAR.match(p)
    if m and m.group(1).lower() in MONTH_NUM:
        return int(m.group(2)) * 12 + MONTH_NUM[m.group(1).lower()] - 1
    m = re.fullmatch(r"(\d{1,2})\s*[/.]\s*(\d{4})", p) or re.fullmatch(r"(\d{4})\s*-\s*(\d{1,2})", p)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        year, month = (b, a) if a <= 12 and b > 12 else (a, b)
        return year * 12 + month - 1
    if re.fullmatch(r"\d{4}", p):
        return f"y{p}"
    return None


def same_dates(a, b):
    """The same span, written in any format; a year alone ("2016") matches any
    month of that year, so a role shown in years only still binds."""
    pa, pb = date_parts(a), date_parts(b)
    ka, kb = [month_index(x) for x in pa], [month_index(x) for x in pb]
    if None in ka or None in kb:
        return norm(a).lower() == norm(b).lower()
    if len(ka) != len(kb):
        return False

    def year(k):
        return k if k == "present" else int(k[1:]) if isinstance(k, str) else k // 12

    return all(x == y or (isinstance(x, str) or isinstance(y, str)) and x != "present" and y != "present"
               and year(x) == year(y) for x, y in zip(ka, kb))


def same_role(heading, e):
    """True when a library role heading names the resume entry's title,
    employer and dates; None when the heading is not a role heading."""
    import library
    role = library.role_of(heading)
    if role is None or e is None:
        return None
    title, org, dates = role
    return (norm(title).lower() == norm(e.get("title", "")).lower()
            and norm(org).lower() == norm(e.get("org") or "").lower()
            and same_dates(dates, e.get("dates") or ""))


def evidence_check(data, entries, v12=False):
    """Every covered bullet cites existing IDs, and its numbers are in those entries.

    v12: entries is {id: (text, heading)}; numbers written as words count, and
    a cited entry filed under a role heading must be that same role (title,
    employer and dates), so a bullet never moves to another job or stint."""
    if entries is None:
        return ["EVIDENCE skipped: no achievement library with [L-] IDs in --sources"]
    out, n, cited = [], 0, set()
    for where, b, entry in evidence_units(data, with_entry=True):
        n += 1
        ev = b.get("ev") if isinstance(b, dict) else None
        if not isinstance(ev, list) or not ev:
            out.append(f"EVIDENCE missing ev: {where}")
            continue
        unknown = [str(i) for i in ev if i not in entries]
        if unknown:
            out.append(f"EVIDENCE unknown {', '.join(unknown)} in {where}")
            continue
        cited.update(ev)
        have = set()
        for i in ev:
            text = entries[i][0] if isinstance(entries[i], tuple) else entries[i]
            have |= source_numbers(text, words=v12)
        if v12:
            for i in ev:
                heading = entries[i][1] if isinstance(entries[i], tuple) else ""
                if same_role(heading, entry) is False:
                    out.append(f'EVIDENCE {i} in {where} is filed under "### {heading}" in your library, not this role')
        seen = set()
        for token, key in numbers(bullet_text(b), words=v12):
            if key not in have and token not in seen:
                seen.add(token)
                out.append(f'EVIDENCE number "{token}" in {where} is not in {", ".join(ev)}')
    return out or [f"EVIDENCE ok ({n} bullets, {len(cited)} entries cited)"]


# ---------- techniques (v1.2) ----------

SKILLS_HEADING = re.compile(r"skill|techni", re.I)


def split_items(text):
    """Comma-separated items at parenthesis depth 0; a trailing period is dropped."""
    text = text.strip()
    if text.endswith("."):
        text = text[:-1]
    items, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur).strip())
    return [i for i in items if i]


def skill_items(data):
    """[(where, item as written)] for every item of a Skills or Techniques section."""
    out = []
    for s in data.get("sections", []):
        if is_citation(s) or not SKILLS_HEADING.search(s["heading"]):
            continue
        for ln in s.get("lines", []):
            out += [(f'{s["heading"]} line "{ln["label"]}"', i) for i in split_items(ln["text"])]
        for k, b in enumerate(s.get("bullets", []), 1):
            out += [(f'{s["heading"]} bullet {k}', i) for i in split_items(bullet_text(b))]
    return out


def technique_check(data, techniques, source_text):
    """Every Skills/Techniques item is a techniques.md name or alias at level
    hands-on (another level only with that level stated in parentheses:
    "confocal microscopy (familiar)"), or, not in techniques.md, a skill your
    sources name; never one the library lists as "Not held". A qualifier in
    parentheses ("SQL (basic queries)") is ignored for the match."""
    held, refused = split_not_held(source_text or "")
    known = {}
    for t in techniques or []:
        for form in t.forms:
            known.setdefault(norm(form).lower(), t)
    out, n = [], 0
    for where, item in skill_items(data):
        name = norm(PARENS.sub(" ", item)).strip(" .,;:")
        if not name:
            continue
        n += 1
        hit = next((r for r in refused.splitlines() if r.strip() and
                    (term_found(name, r.strip()) or term_found(r.strip(), name))), None)
        tech = known.get(name.lower())
        if hit:
            out.append(f'TECHNIQUE "{name}" in {where}: your library says "Not held: {hit.strip()}"')
        elif tech is not None:
            level = tech.level or "(none)"
            stated = any(level.lower() in q.lower() for q in PARENS.findall(item))
            if tech.level != "hands-on" and not stated:
                out.append(f'TECHNIQUE "{name}" in {where}: techniques.md marks it level: {level}; '
                           f'list it as "{name} ({level})" or leave it out')
        elif not term_found(held, name):
            out.append(f'TECHNIQUE "{name}" in {where} is not in techniques.md or your sources')
    return out or [f"TECHNIQUE ok ({n} item{'s' if n != 1 else ''})"]


# ---------- citations (v1.2) ----------

PUB_TYPES = ("journal", "preprint", "conference", "patent", "thesis")
PUB_LINE = re.compile(r"^[-*]\s+(P-\d{3,})\s*\|\s*([A-Za-z]+)\s*\|\s*(.+?)\s*$")  # top-level bullets only
CITE_HEADING = re.compile(r"\b(publications?|presentations?|patents?|preprints?)\b", re.I)


def _cp1252(c):
    try:
        c.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def font_ok(text):
    """True when the PDF font (Helvetica, WinAnsi) can show every character."""
    return all(_cp1252(c) for c in text)


def load_publications(path):
    """{P-id: {"type", "text", "ascii", "doi"}} from publications.md lines
    `- P-001 | journal | <citation> [| ascii: <rendering>] [| doi:<doi>]`.
    Raises ValueError on a bad type or a repeated ID."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e}")
    pubs = {}
    for n, line in enumerate(text.splitlines(), 1):
        m = PUB_LINE.match(line)
        if not m:
            continue
        pid, kind, rest = m.group(1), m.group(2).lower(), m.group(3)
        if kind not in PUB_TYPES:
            raise ValueError(f"{path} line {n}: type {kind!r} is not one of {', '.join(PUB_TYPES)}")
        if pid in pubs:
            raise ValueError(f"{path} line {n}: {pid} appears twice")
        cite, ascii_text, doi = [], "", ""
        for part in rest.split(" | "):
            low = part.strip().lower()
            if low.startswith("ascii:"):
                ascii_text = part.strip()[6:].strip()
            elif low.startswith("doi:"):
                doi = part.strip()[4:].strip()
            else:
                cite.append(part.strip())
        pubs[pid] = {"type": kind, "text": " | ".join(cite), "ascii": ascii_text, "doi": doi}
    return pubs


def printable(pub):
    """The text a build prints: the citation, or its approved ascii: rendering
    when the citation holds characters the PDF font cannot show."""
    if pub["ascii"] and not font_ok(pub["text"]):
        return pub["ascii"]
    return pub["text"]


def cite_norm(text):
    """Citation text with * markup, whitespace and dash differences removed."""
    for d in DASHES:
        text = text.replace(d, "-")
    return re.sub(r"\s+", "", text.replace("*", ""))


def cite_items(section, pubs):
    """[(P-id or None, text as printed)] for one citation section."""
    if "citations" in section:
        return [(cid, printable(pubs[cid]) if cid in pubs else None) for cid in section["citations"]]
    return [(None, bullet_text(b)) for b in section.get("bullets", [])]


def first_difference(printed, wanted):
    """'"<printed words>" where publications.md has "<wanted words>"' for the first change."""
    a, b = norm(printed.replace("*", "")).split(), norm(wanted.replace("*", "")).split()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            return f'"{" ".join(a[i1:i2]) or "(nothing)"}" where publications.md has "{" ".join(b[j1:j2]) or "(nothing)"}"'
    return "spacing only"


def match_publication(text, pubs):
    """(id, pub, None) for an exact match, else (id or None, None, failure line)."""
    want = cite_norm(text)
    best, best_id = 0.0, None
    for pid, pub in pubs.items():
        forms = [pub["text"]] + ([pub["ascii"]] if pub["ascii"] else [])
        if want in [cite_norm(f) for f in forms]:
            return pid, pub, None
        ratio = difflib.SequenceMatcher(None, want, cite_norm(pub["text"]), autojunk=False).ratio()
        if ratio > best:
            best, best_id = ratio, pid
    if best_id and best >= 0.6:
        return best_id, None, f"CITE mismatch {best_id}: {first_difference(text, pubs[best_id]['text'])}"
    return None, None, f'CITE not in publications.md: "{norm(text)[:60]}"'


def cite_check(data, pubs, pdf_text=None):
    """Every printed citation equals its publications.md line or ascii: rendering,
    shows in the PDF font, and (when the PDF reads back) is in the PDF text."""
    out, n = [], 0
    pubs = pubs or {}
    flat_pdf = cite_norm(pdf_text) if pdf_text else None
    for s in data.get("sections", []):
        if not is_citation(s):
            continue
        for cid, printed in cite_items(s, pubs):
            n += 1
            if cid is not None and printed is None:
                out.append(f"CITE missing {cid} (not in publications.md)")
                continue
            if cid is None:
                cid, pub, failure = match_publication(printed, pubs)
                if failure:
                    out.append(failure)
                    continue
            else:
                pub = pubs[cid]
            forms = [cite_norm(pub["text"])] + ([cite_norm(pub["ascii"])] if pub["ascii"] else [])
            if cite_norm(printed) not in forms:
                out.append(f"CITE mismatch {cid}: {first_difference(printed, pub['text'])}")
            elif not font_ok(printed):
                out.append(f"CITE {cid} has characters the PDF font cannot show; "
                           "approve an ascii: rendering in publications.md")
            elif flat_pdf is not None and cite_norm(printed) not in flat_pdf:
                out.append(f"CITE mismatch {cid}: not found as printed in the PDF text")
    return out or [f"CITE ok ({n} citation{'s' if n != 1 else ''})"]


# ---------- letters (v1.2) ----------

SLOT_RE = re.compile(r"\{[A-Za-z_]+\}")


def posting_header(text):
    """{"Company": ..., "Role": ...} from the header lines of a posting.md."""
    head = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        m = re.match(r"^([A-Za-z][A-Za-z ]*):\s?(.*)$", line)
        if m:
            head.setdefault(m.group(1).strip(), m.group(2).strip())
    return head


def letter_check(data, posting_text=None):
    """No unfilled {slot}; with posting.md, the letter's company and role equal
    its Company and Role lines and both are named in the letter."""
    L = data["letter"]
    parts = [L.get("date", ""), L.get("greeting", ""), L.get("closing", "")]
    parts += list(L.get("recipient", [])) + list(L["paragraphs"])
    out = []
    for slot in sorted({m.group(0) for p in parts for m in SLOT_RE.finditer(p)}):
        out.append(f"LETTER unfilled slot {slot}")
    if posting_text is None:
        return out or ["LETTER ok (no posting.md given)"]
    head = posting_header(posting_text)
    company, role = head.get("Company", ""), head.get("Role", "")
    body = norm(" ".join(parts)).lower()
    for key, want in (("company", company), ("role", role)):
        if not want:
            out.append(f"LETTER posting.md has no {key.capitalize()} line")
            continue
        given = L.get(key)
        if given is not None and norm(given) != norm(want):
            out.append(f'LETTER {key} "{given}" is not posting.md\'s "{want}"')
        if norm(want).lower() not in body:
            out.append(f'LETTER {key} "{want}" (posting.md) is not named in the letter')
    focus = L.get("focus")
    if focus and norm(focus).lower() not in norm(posting_text).lower():
        out.append(f'LETTER focus "{focus}" is not a phrase from posting.md')
    return out or [f'LETTER ok (company "{company}", role "{role}")']


# ---------- strict verdict (v1.2) ----------

STRICT_TAGS = ("PARSE", "FACT", "EVIDENCE", "TECHNIQUE", "CITE", "LETTER")


def strict_failures(check_lines):
    """The check lines a strict build fails on: PARSE other than ok, any FACT
    line other than ok, and any EVIDENCE, TECHNIQUE, CITE or LETTER problem."""
    bad = []
    for ln in check_lines:
        tag = ln.split(" ", 1)[0]
        if tag in STRICT_TAGS and not ln.startswith(tag + " ok"):
            bad.append(ln)
    return bad


def verdict(failures, strict):
    """STRICT-FAIL lines (strict only), then the final VERDICT line."""
    lines = [f"STRICT-FAIL {f}" for f in failures] if strict else []
    return lines + ["VERDICT fail" if failures else "VERDICT ok"]


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

ANSWERS_HEADING = re.compile(r"^##\s+answers\b", re.I)


def answers_section(text):
    """The text under notes.md's `## Answers` heading(s), up to the next `## `."""
    keep, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = bool(ANSWERS_HEADING.match(line))
            continue
        if inside:
            keep.append(line)
    return "\n".join(keep)


def read_sources(paths, v12=False):
    """(concatenated text or None, [lines to print]).

    v12: a job's notes.md counts only for its `## Answers` section (the user's
    own answers). Its fit table and match report quote the posting, and the
    posting's numbers and titles are never a source."""
    chunks, errors = [], []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"FACT source not read, checked without it: {p} ({type(e).__name__})")
            continue
        if v12 and Path(p).name.lower() == "notes.md":
            text = answers_section(text)
            errors.append(f"SOURCES {Path(p).name}: only its ## Answers section is a source "
                          f"({len([ln for ln in text.splitlines() if ln.strip()])} lines)")
        chunks.append(text)
    return ("\n".join(chunks) if chunks else None), errors


def library_paths(sources, library=None):
    """v12: the achievement library alone holds evidence; --library, else the
    --sources file named achievement_library.md."""
    if library:
        return [library]
    return [p for p in sources or [] if Path(p).name.lower() == "achievement_library.md"]


def guarded(tag, fn):
    try:
        return fn()
    except Exception as e:  # a check must never break the build
        return [f"{tag} skipped: {type(e).__name__}: {e}"]


def run_checks(data, pdf_path=None, txt_path=None, sources=None, terms=None, *,
               pubs=None, evidence="auto", posting=None, letter=False, v12=False,
               library=None, techniques=None):
    """Lines to print after the WARN lines. pdf_path is None when no PDF was built.

    v1.2 keywords (all off for a v1.1 call):
    - pubs: publications.md as load_publications() returns it; CITE runs on
      every citation section.
    - evidence: True always runs EVIDENCE (strict), "auto" only when a bullet
      carries an ev list, False never.
    - posting: posting.md text for the letter check; letter=True runs the
      letter check even without it (unfilled slots).
    - v12 (any v1.2 flag): notes.md counts only for its ## Answers section;
      EVIDENCE reads the library alone (library, else the achievement_library.md
      in sources) and binds each cited entry to its role; FACT and EVIDENCE
      read numbers written as words; FACT binds title, employer and dates and
      checks certification names; TECHNIQUE checks Skills/Techniques items
      against techniques (library.load_techniques) and the sources; KEYWORD
      adds its not-held line.
    """
    out = []
    if pdf_path:
        out += guarded("PARSE", lambda: parse_back(data, pdf_path))
    source_text = None
    if sources:
        source_text, errors = read_sources(sources, v12=v12)
        out += errors
        if source_text is not None:
            source_text = strip_ids(source_text)
            out += guarded("FACT", lambda: fact_check(data, source_text, v12=v12))
            if v12 and "letter" not in data:
                out += guarded("TECHNIQUE", lambda: technique_check(data, techniques, source_text))
    if "letter" not in data and (evidence is True or (evidence == "auto" and has_ev(data))):
        if v12:
            out += guarded("EVIDENCE", lambda: evidence_check(
                data, library_entries(library_paths(sources, library), headings=True), v12=True))
        else:
            out += guarded("EVIDENCE", lambda: evidence_check(data, library_entries(sources)))
    if any(is_citation(s) for s in data.get("sections", [])):
        pdf_text = None
        if pdf_path:
            try:
                pdf_text = read_pdf_text(pdf_path)
            except Exception:  # PARSE already reports an unreadable PDF
                pdf_text = None
        out += guarded("CITE", lambda: cite_check(data, pubs, pdf_text))
    if "letter" in data and (letter or posting is not None):
        out += guarded("LETTER", lambda: letter_check(data, posting))
    if terms:
        def keywords():
            resume_text = Path(txt_path).read_text(encoding="utf-8")
            return keyword_check(resume_text, source_text, load_terms(terms), bool(sources), v12=v12)
        out += guarded("KEYWORD", keywords)
    return out
