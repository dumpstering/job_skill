"""Screening answers: the answer bank (answers.md), per-job fill plans,
computed years, the daily question batch, and unparking (spec 4.6-4.7).

Usage:
    python answers.py plan      --root R --job <job folder> [--family F] [--dry-run]
    python answers.py years     --root R --skill X
    python answers.py questions --root R --date YYYY-MM-DD [--json]
    python answers.py add       --root R --question Q --answer A [--kind text|yesno|number|choice]
                                [--scope any|US|Canada] [--sensitive yes|no] [--source S] [--also "a; b"]
    python answers.py list      --root R
    python answers.py unpark    --root R --date YYYY-MM-DD [--session ID]
    python answers.py migrate   --root R

answers.md lines (one per answer):
    - ANS-012 | <question> | <answer> | <kind> | <scope> | <sensitive yes|no> | <source> | <last confirmed> [| also: <a>; <b>]
source is profile:<field>, library:L-0031, computed:years(<term>),
template:<family> (an approved free-text answer with {company} {role}
{focus} slots) or user:YYYY-MM-DD. scope is any, US or Canada (any case);
add refuses CA, which a California user would read as the state, with
REFUSED and a line saying to write US or Canada. A CA already in answers.md
(v1.1 wrote it for Canada) is still read as Canada, and list names each such
line in a WARN.

list prints the bank, one line per answer, then ANSWERS n=<count>:
    ANS-012 | <scope> | sensitive yes|no | <question> = <answer>
    WARN answers.md line <n>: scope "CA" is read as Canada; write US or Canada

plan writes fill_plan.json in the job folder from posting.md's application
questions (or, for other forms, the profile's contact and eligibility
fields): every filled value names its answer_source. Criminal-history,
attestation, consent and ID questions are never filled or stored (the user's part).
Salary, pay rate, visa and sponsorship questions, any answer that is a pay
figure, and every stored answer marked sensitive (bank, profile Standard
answers, a job's JOB-n lines) are sensitive_confirm. A question about
experience never takes an eligibility or EEO profile value. A yes/no, number
or choice answer settles only a question with the same content words (one
added qualifier is a different question). An unknown required answer is
needs_you, which parks the job; the question joins the batch. Never a guess.
A required cover letter with none built in the job folder (final.json's
letter, or cover_B.pdf) prints a LETTER line.

years adds up the dated roles whose library entries techniques.md cites as
evidence for X (count both end months, overlaps once, gaps out, rounded
down), only when techniques.md marks X years_ok: yes. plan uses it only for a
question about X alone ("at least 5 years of qPCR"), never one that adds a
setting or a second skill, and records the arithmetic in the field's note.

questions merges the day's unknown required answers, Tier A questions
(questions.json in a Tier A job folder, at most 2 each) and variant gaps
(variants/<family>/v<N>/questions.json), dedupes them (same text, a listed
also: alias, or token similarity 0.8), ranks them by queued jobs unblocked
(Tier A first on ties) and keeps at most daily.questions_per_day (6). It also
prints the sensitive-answers line.

unpark writes an `unparked` event for each queued job whose needs are now
answered and whose last state today is parked or none: an applied, later,
skipped, blocked, started or handed-over job never comes back. migrate copies
the v1.1 profile.md Standard answers into the bank.

Exit codes: 0 ok, 1 bad input or a refusal (REFUSED).
"""
import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks  # noqa: E402
import jobfs  # noqa: E402
import library  # noqa: E402

KINDS = ("text", "yesno", "number", "choice")
SIMILAR = 0.8
TIER_A_QUESTIONS = 2
SOURCE_RE = re.compile(r"^(profile:[a-z_]+|library:L-\d{4,}|computed:years\(.+\)|template:[a-z0-9_]+|user:\d{4}-\d{2}-\d{2})$")
ANS_LINE = re.compile(r"^[-*]\s+((?:ANS|JOB)-\d+)\s*\|(.*)$")  # top-level bullets only
QLINE = re.compile(r"^\s*\d+\.\s+(.*\S)\s*$")
QTYPED = re.compile(r"^(?P<label>.+?)(?P<req> \(required\))? - (?P<type>[a-z][a-z_]*(?:/[a-z][a-z_]*)*)(?:: (?P<opts>.*))?$")
QPLAIN = re.compile(r"^(?P<label>.+?)(?P<req> \(required\))?$")

# Never filled, never stored, never asked in chat: the user's part on the form (v1.1).
NEVER = re.compile(
    r"convict|criminal|felon|misdemean|arrest|pending charge|plead(?:ed)? guilty|offen[cs]e record|"
    r"\bcertify\b|\battest|\backnowledg|\bi agree\b|\bi understand\b|signature|\be-?sign|electronically sign|"
    r"true,? (?:and )?(?:complete|correct|accurate)|terms (?:and|&) conditions|privacy (?:policy|notice)|\bconsent",
    re.I)
PII = re.compile(r"social security|\bssn\b|date of birth|birth ?date|\bdob\b|bank account|bank name|"
                 r"banking (?:details|information)|routing number|account number|driver'?s licen[cs]e|passport|"
                 r"tax id|\bpassword", re.I)
SENSITIVE = re.compile(
    r"salary|compensation|\bpay\b|\bwages?\b|\bvisa\b|sponsor|immigration|citizen|remunerat|earnings|"
    r"\bhourly\b|\b(?:pay|bill|day|hourly|desired|expected|target|contract) rate\b|\brate (?:expectation|requirement)s?\b|"
    r"\bbase (?:pay|salary)\b|\bote\b|\bbonus|\bequity\b|\bstock options?\b|\bh-?1b\b|work permit|green card|"
    r"permanent resident|\bopt\b|\bcpt\b|\btn (?:visa|status)\b", re.I)
# An answer that is itself a pay figure ("$55 per hour", "105k", "USD 90,000") is sensitive too.
SENSITIVE_ANSWER = re.compile(r"\$\s?\d|\b\d[\d,.]*\s?k\b|\busd\b|\bper (?:hour|hr|year|annum|month)\b|/\s?(?:hr|hour|yr|year)\b", re.I)
YEARS_Q = re.compile(r"how many years|years of (?:hands-on |professional |relevant )?(?:experience|exp)|number of years", re.I)
AT_LEAST = re.compile(r"(?:at least|a minimum of|minimum of)\s+(\d+)\+?\s+years?|\b(\d+)\+\s*years?", re.I)
# A question about what the user has done is never an eligibility field: "18
# months of experience" is not "18 or older", "drug testing by LC-MS/MS" is not
# a drug screen, "worked with trial sponsors" is not visa sponsorship.
EXPERIENCE_Q = re.compile(
    r"\bexperienced?\b|\bworked\b|\bhands-on\b|\bfamiliar(?:ity)?\b|\bproficien|\bknowledge of\b|\bhow many\b|"
    r"\bhave you (?:ever )?(?:used|run|performed|led|managed|conducted|supervised|trained|developed|designed)\b", re.I)
# The words a computed-years question may hold besides the technique itself.
YEARS_WORDS = set("how many years year yrs experience exp of with at least minimum do you have hands on "
                  "professional relevant total number in using performing working a an the".split())

PROFILE_FIELDS = [
    ("first_name", "legal first name"), ("last_name", "legal last name"), ("preferred_name", "preferred first name"),
    ("email", "email"), ("phone", "phone"), ("street", "street address"), ("city", "city"),
    ("state", "state / province"), ("zip", "zip / postal code"), ("country", "country"),
    ("linkedin", "linkedin url"), ("website", "portfolio / github / website"),
    ("authorized", "countries i am authorized to work in"), ("sponsorship", "need visa sponsorship"),
    ("adult", "18 or older"), ("relocate", "willing to relocate"), ("arrangement", "work arrangement"),
    ("commute", "longest commute"), ("travel", "willing to travel"), ("start", "earliest start date"),
    ("salary", "desired salary"), ("background", "willing to complete a background check"),
    ("gender", "gender"), ("race", "race / ethnicity"), ("hispanic", "hispanic or latino"),
    ("veteran", "veteran status"), ("disability", "disability status"), ("pronouns", "pronouns"),
    ("licenses", "licenses held"), ("claim", "unemployment claim"), ("claim_state", "claim state"),
]
# Question label -> profile field, first match wins.
PROFILE_RULES = [(re.compile(p, re.I), f) for p, f in [
    (r"\bpreferred (?:first )?name\b", "preferred_name"),
    (r"\bfirst name\b|\bgiven name\b", "first_name"),
    (r"\blast name\b|\bsurname\b|\bfamily name\b", "last_name"),
    (r"\be-?mail\b", "email"),
    (r"\bphone\b|\bmobile number\b", "phone"),
    (r"\blinkedin\b", "linkedin"),
    (r"\bwebsite\b|\bportfolio\b|\bgithub\b", "website"),
    (r"^street|\baddress line\b|^address$", "street"),
    (r"\bzip\b|\bpostal code\b", "zip"),
    (r"^city\b", "city"),
    (r"^(?:state|province)\b", "state"),
    (r"^country\b", "country"),
    (r"^location\b|current location|where are you (?:located|based)", "location"),
    (r"\bsponsorship\b|\b(?:require|need)s?\b[^?]*\bsponsor|\bvisa\b", "sponsorship"),
    (r"authori[sz]ed to work|legally (?:authori[sz]ed|eligible)|eligible to work|right to work|work authori[sz]ation", "authorized"),
    (r"\b(?:18|eighteen)\s*(?:\+|years? (?:of age|old|or older)|or older|or over)|\blegal (?:working )?age\b", "adult"),
    (r"relocat", "relocate"),
    (r"willing to travel|travel up to|\btravel requirement", "travel"),
    (r"start date|available to start|when can you start|notice period|earliest start", "start"),
    (r"salary|compensation|pay expectation|expected pay|desired pay|pay range", "salary"),
    (r"background (?:check|screen|investigation)|\b(?:pass|undergo|complete|submit to|consent to)\b[^?]*\bdrug (?:screen|test)", "background"),
    (r"gender", "gender"), (r"\brace\b|ethnicity", "race"), (r"hispanic|latin[oax]", "hispanic"),
    (r"veteran", "veteran"), (r"disabilit", "disability"), (r"pronoun", "pronouns"),
]]
EEO_FIELDS = {"gender", "race", "hispanic", "veteran", "disability", "pronouns"}
# Fields a label names directly; every other profile field (eligibility, EEO)
# is never matched to a question about experience (EXPERIENCE_Q).
NAME_FIELDS = {"preferred_name", "first_name", "last_name", "email", "phone", "linkedin", "website",
               "street", "zip", "city", "state", "country", "location"}
SENSITIVE_FIELDS = {"salary", "sponsorship"}
CONTACT_FIELDS = ["first_name", "last_name", "email", "phone", "location", "linkedin", "website"]
ELIGIBILITY_FIELDS = ["authorized", "sponsorship", "adult", "relocate", "start", "salary", "background"]
DECLINE = re.compile(r"decline|don'?t wish|do not wish|prefer not|not to (?:answer|say|disclose)|choose not|rather not", re.I)
COUNTRY_CODES = {"united states": "US", "united states of america": "US", "usa": "US", "us": "US",
                 "america": "US", "canada": "CA", "united kingdom": "GB", "uk": "GB",
                 "mexico": "MX", "germany": "DE", "france": "FR", "india": "IN", "ireland": "IE"}
US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
                "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split())
# Answer scopes a new answer may take (any case), stored in this spelling.
SCOPES = {"any": "any", "us": "US", "canada": "Canada"}
LEGACY_CA = "CA"  # v1.1's scope for Canada; read as Canada, refused for new answers
STOP = set("a an the of to in on for with and or is are do does did you your have has had be been any at as "
           "please this that what which".split())


class Refused(Exception):
    pass


# ---------- text helpers ----------

def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP]


def similarity(a, b):
    """Token overlap (Dice) of two questions, stopwords dropped. Questions whose
    numbers differ ("3 years" and "5 years", "BSL-2" and "BSL-3") score 0."""
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb or {t for t in ta if t.isdigit()} != {t for t in tb if t.isdigit()}:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


def same_question(a, b):
    return " ".join(tokens(a)) == " ".join(tokens(b)) or similarity(a, b) >= SIMILAR


def stem_set(text):
    """The question's content words, a plural s dropped ("labs" is "lab")."""
    return {t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in tokens(text)}


def equivalent(a, b):
    """The same content words, in any order: a rewording, never an added
    qualifier ("as the lab supervisor") or a dropped one."""
    return stem_set(a) == stem_set(b) and bool(stem_set(a))


SHORT_ANSWER = re.compile(r"^\s*(?:yes|no)\b|^\s*\$?\d", re.I)


def strict_answer(a):
    """A yes/no, number or choice answer settles only the exact question it was
    given for: one added word can turn a true "Yes" into a false claim."""
    return a.kind in ("yesno", "number", "choice") or bool(SHORT_ANSWER.match(a.answer or ""))


def question_id(text):
    """Q-<6 hex>: a stable ID from the normalized question text."""
    return "Q-" + hashlib.sha256(" ".join(tokens(text)).encode("utf-8")).hexdigest()[:6]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "field"


# ---------- the bank ----------

@dataclass
class Answer:
    id: str
    question: str
    answer: str
    kind: str = "text"
    scope: str = "any"
    sensitive: bool = False
    source: str = ""
    confirmed: str = ""
    also: list = field(default_factory=list)
    line: int = 0

    def forms(self):
        return [self.question] + self.also


def parse_bank(text):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = ANS_LINE.match(line)
        if not m:
            continue
        parts = [p.strip() for p in m.group(2).split("|")]
        also = []
        for p in list(parts):
            if p.lower().startswith("also:"):
                also = [a.strip() for a in p[5:].split(";") if a.strip()]
                parts.remove(p)
        parts += [""] * (7 - len(parts))
        q, a, kind, scope, sens, source, confirmed = parts[:7]
        out.append(Answer(m.group(1), q, a, kind.lower() or "text", scope or "any",
                          sens.lower() == "yes", source, confirmed, also, n))
    return out


def load_bank(path):
    try:
        return parse_bank(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return []


def standard_answers(profile_text):
    """v1.1 profile.md Standard answers: `- question | answer | scope | sensitive`."""
    out, inside = [], False
    for n, line in enumerate(profile_text.splitlines(), 1):
        if line.startswith("## "):
            inside = line.strip().lower() == "## standard answers"
            continue
        m = re.match(r"^\s*-\s+(.*)$", line)
        if inside and m and m.group(1).count("|") >= 3:
            q, a, scope, sens = [p.strip() for p in m.group(1).split("|")[:4]]
            if q:
                out.append(Answer("profile", q, a, "text", scope or "any", sens.lower() == "yes",
                                  "profile:standard_answers", "", [], n))
    return out


def format_line(a):
    line = (f"- {a.id} | {a.question} | {a.answer} | {a.kind} | {a.scope} | "
            f"{'yes' if a.sensitive else 'no'} | {a.source} | {a.confirmed}")
    if a.also:
        line += " | also: " + "; ".join(a.also)
    return line


def scope_country(scope):
    """"any", or the country code a scope covers: US, Canada and the legacy CA
    are US and CA; another hand-written code is compared as it is."""
    s = (scope or "any").strip()
    if s.lower() == "any":
        return "any"
    if s.lower() == "canada" or s.upper() == LEGACY_CA:
        return "CA"
    return s.upper()


def check_scope(scope):
    """The stored spelling of a new answer's scope (any, US, Canada), or Refused."""
    s = (scope or "any").strip()
    if s.upper() == LEGACY_CA:
        raise Refused(f'REFUSED scope "{s}" could mean California or Canada: write --scope US or --scope Canada')
    if s.lower() not in SCOPES:
        raise Refused(f'REFUSED scope "{s}": write any, US or Canada')
    return SCOPES[s.lower()]


def find_answer(label, bank, country="", slots=None):
    """The bank entry for a question: same text, an also: alias, or similarity
    0.8 for free text. A yes/no, number or choice answer (strict_answer) needs
    the same content words as one of its forms. slots ({"{company}": "Acme
    Bio", ...}) fill template questions first."""
    best, best_score = None, 0.0
    for a in bank:
        if not a.answer.strip():
            continue
        covers = scope_country(a.scope)
        if covers != "any" and covers != (country or "").upper():
            continue
        strict = strict_answer(a)
        for form in a.forms():
            for slot, value in (slots or {}).items():
                if value:
                    form = form.replace(slot, value)
            if " ".join(tokens(form)) == " ".join(tokens(label)):
                score = 1.0
            elif strict:
                score = 0.99 if equivalent(form, label) and similarity(form, label) > 0 else 0.0
            else:
                score = similarity(form, label)
            if score >= SIMILAR and score > best_score:
                best, best_score = a, score
    return best


# ---------- the profile ----------

def load_profile(path):
    """{field id: value} from profile.md `- Key: value` lines."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, ""
    values = {}
    for line in text.splitlines():
        m = re.match(r"^\s*-\s+([^:|]+):\s*(.*)$", line)
        if not m:
            continue
        key = re.sub(r"\(.*?\)", "", m.group(1)).strip().lower()
        for fid, prefix in PROFILE_FIELDS:
            if key.startswith(prefix) and fid not in values:
                values[fid] = m.group(2).strip()
                break
    if values.get("city") or values.get("state"):
        values["location"] = ", ".join(v for v in (values.get("city"), values.get("state")) if v)
    return values, text


def country_code(text):
    t = (text or "").strip().lower().replace(".", "")
    return COUNTRY_CODES.get(t, t.upper() if len(t) == 2 and t.isalpha() else "")


def authorized_countries(value):
    return {country_code(p) for p in re.split(r"[,;/]| and ", value or "") if country_code(p)}


# ---------- posting.md ----------

@dataclass
class Question:
    label: str
    required: bool
    qtype: str
    options: list
    voluntary: bool = False


def parse_questions(posting_text):
    """The numbered lines under ## Application questions (and its Voluntary part)."""
    out, inside, voluntary = [], False, False
    for line in posting_text.splitlines():
        if line.startswith("## "):
            inside, voluntary = line.strip().lower() == "## application questions", False
            continue
        if line.startswith("### ") and inside:
            voluntary = "voluntary" in line.lower()
            continue
        m = QLINE.match(line) if inside else None
        if not m:
            continue
        body = m.group(1)
        q = QTYPED.match(body) or QPLAIN.match(body)
        opts = (q.groupdict().get("opts") or "")
        options = [o.strip() for o in opts.split("; ") if o.strip() and not o.strip().startswith("...(+")]
        out.append(Question(q.group("label").strip(), bool(q.group("req")), q.groupdict().get("type") or "",
                            options, voluntary))
    return out


def letter_required(posting_text):
    return any(q.required and re.search(r"cover letter", q.label, re.I) for q in parse_questions(posting_text))


def letter_built(job):
    """True when the job folder holds a built letter: the one final.json names
    (a passing strict build), or the runner's cover_B.pdf."""
    try:
        name = json.loads((Path(job) / "final.json").read_text(encoding="utf-8")).get("letter")
        if name and (Path(job) / str(name)).exists():
            return True
    except (OSError, ValueError, AttributeError):
        pass
    return (Path(job) / "cover_B.pdf").exists()


def field_type(qtype):
    t = qtype or ""
    if "input_file" in t:
        return "file"
    if "multi_select" in t:
        return "multi"
    if "single_select" in t or "boolean" in t:
        return "choice"
    if "textarea" in t:
        return "textarea"
    return "text" if t else "unknown"


def job_country(job, head):
    """US, CA, ... from posting.json, else the posting's Location line."""
    try:
        data = json.loads((job / "posting.json").read_text(encoding="utf-8-sig"))
        if country_code(data.get("country", "")):
            return country_code(data["country"])
    except (OSError, ValueError, AttributeError):
        pass
    loc = head.get("Location", "")
    if re.search(r"united states|\busa\b|\bu\.s\.", loc, re.I):
        return "US"
    m = re.search(r",\s*([A-Z]{2})\b", loc)
    if m and m.group(1) in US_STATES:
        return "US"
    if "canada" in loc.lower():
        return "CA"
    return ""


# ---------- years ----------

def month_of(part, today, end):
    """Month index (year*12 + month-1); year-only dates are taken shortest
    (December of a start year, January of an end year)."""
    p = part.strip().rstrip(".")
    if p.lower() in ("present", "current", "now"):
        return today.year * 12 + today.month - 1
    m = checks.MONTH_YEAR.match(p)
    if m and m.group(1).lower() in checks.MONTH_NUM:
        return int(m.group(2)) * 12 + checks.MONTH_NUM[m.group(1).lower()] - 1
    m = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{4})", p)
    if m:
        return int(m.group(2)) * 12 + int(m.group(1)) - 1
    m = re.fullmatch(r"(\d{4})\s*-\s*(\d{1,2})", p)
    if m:
        return int(m.group(1)) * 12 + int(m.group(2)) - 1
    m = re.fullmatch(r"(\d{4})", p)
    if m:
        return int(m.group(1)) * 12 + (0 if end else 11)
    return None


def span_of(dates, today):
    parts = checks.date_parts(dates)
    if len(parts) == 1:
        parts = parts * 2
    if len(parts) != 2:
        return None
    a, b = month_of(parts[0], today, False), month_of(parts[1], today, True)
    return (a, b) if a is not None and b is not None and a <= b else None


def years(root, skill):
    """{"skill", "years", "months", "spans", "ids", "roles"}; raises Refused."""
    f = jobfs.Folder(root)
    tech = library.find_technique(library.load_techniques(f.techniques), skill)
    if tech is None:
        raise Refused(f"REFUSED years {skill}: not in techniques.md")
    if not tech.years_ok:
        raise Refused(f"REFUSED years {skill}: techniques.md marks years_ok: no")
    lib = library.load(f.library).by_id
    today = jobfs.today(root)
    spans, roles, ids = [], [], []
    for eid in tech.evidence:
        entry = lib.get(eid)
        role = library.role_of(entry.heading) if entry else None
        span = span_of(role[2], today) if role else None
        if span is None:
            continue
        ids.append(eid)
        if role[:2] not in roles:
            roles.append(role[:2])
        spans.append(span)
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    months = sum(b - a + 1 for a, b in merged)
    return {"skill": tech.name, "years": months // 12, "months": months, "spans": merged, "ids": ids,
            "roles": [f"{t} ({o})" for t, o in roles]}


def years_residue(label, tech):
    """The label's content words that are neither the technique (any form) nor
    the years phrase: empty when computed years answer the whole question."""
    known = set(YEARS_WORDS)
    for form in tech.forms:
        known |= set(tokens(form))
    return [t for t in tokens(label) if t not in known and not t.isdigit()]


def show_month(i):
    return f"{checks.MONTHS[i % 12][:3].capitalize()} {i // 12}"


def years_line(y):
    spans = ", ".join(f"{show_month(a)} - {show_month(b)}" for a, b in y["spans"]) or "no dated roles"
    return f"YEARS {y['skill']} {y['years']} ({y['months']} months: {spans}; from {', '.join(y['ids']) or 'none'})"


# ---------- the fill plan ----------

class Planner:
    """Answers one form's questions from the profile, the bank, computed years and templates."""

    def __init__(self, root, job, family=None):
        self.root, self.job = Path(root), Path(job)
        f = jobfs.Folder(root)
        self.profile, ptext = load_profile(f.profile)
        notes = self.job / "notes.md"
        self.job_answers = load_bank(notes) if notes.exists() else []
        self.bank = load_bank(f.answers) + standard_answers(ptext)
        self.techniques = library.load_techniques(f.techniques)
        try:
            self.posting = (self.job / "posting.md").read_text(encoding="utf-8-sig")
        except OSError as e:
            raise Refused(f"ERROR cannot read {self.job / 'posting.md'}: {e}")
        self.head = checks.posting_header(self.posting)
        self.country = job_country(self.job, self.head)
        self.family = family
        self.log = []

    # one field
    def field(self, label, qtype="", required=False, options=(), voluntary=False):
        f = {"label": label, "name": slug(label), "type": field_type(qtype) if qtype else "text",
             "required": bool(required), "answer": "", "answer_source": "", "sensitive": False,
             "user_part": False, "status": "ok"}
        if NEVER.search(label) or PII.search(label):
            f.update(user_part=True, status="check")
            self.log.append(f"\"{label[:60]}\": the user's part (never filled or stored)")
            return f
        if f["type"] == "file":
            f["user_part"] = True
            if required and re.search(r"cover letter", label, re.I) and not letter_built(self.job):
                f["missing"] = "cover letter"  # printed as LETTER by plan; the form cannot go without it
            return f
        f["sensitive"] = bool(SENSITIVE.search(label))
        self.matched, self.note = None, ""
        value, source, status = self.resolve(label, list(options), voluntary)
        if value:
            f["answer"], f["answer_source"] = value, source
            # Every stored answer's own flag counts (bank, Standard answers, a
            # job's JOB-n lines), and so does a pay figure in the answer.
            if (self.matched is not None and self.matched.sensitive) or SENSITIVE_ANSWER.search(value):
                f["sensitive"] = True
            f["status"] = "sensitive_confirm" if f["sensitive"] else status
            if self.note:
                f["note"] = self.note  # the arithmetic behind a computed answer, for the Check line
        elif required:
            f["status"] = "needs_you"
            f["qid"] = question_id(label)
        return f

    def entry_sensitive(self, source):
        aid = source.split(":", 1)[1]
        return any(a.id == aid and a.sensitive for a in self.bank)

    def resolve(self, label, options, voluntary):
        """(answer, answer_source, status) or ("", "", ""); self.matched is the
        stored Answer the value came from, if any."""
        for finder in (self.from_job_notes, self.from_years, self.from_profile, self.from_bank):
            self.matched = None
            got = finder(label, options, voluntary)
            if got and got[0]:
                return got
        self.matched = None
        return "", "", ""

    def from_job_notes(self, label, options, voluntary):
        a = find_answer(label, self.job_answers, self.country, self.slots())
        if a:
            value = self.fit(label, a.answer, options)
            if value:
                self.matched = a
                return value, a.source if SOURCE_RE.match(a.source or "") else f"user:{a.confirmed}", "ok"
        return None

    def from_years(self, label, options, voluntary):
        if not YEARS_Q.search(label) and not AT_LEAST.search(label):
            return None
        tech = next((t for t in self.techniques if checks.term_found(label, t.name, t.aliases)), None)
        if tech is None:
            return None
        rest = years_residue(label, tech)
        if rest:
            # "10 years of qPCR in a GMP-regulated lab", "qPCR and flow cytometry":
            # the computed span says nothing about the rest, so the user answers.
            self.log.append(f'"{label[:60]}": computed years cover {tech.name} only, not "{" ".join(rest)}"')
            return None
        try:
            y = years(self.root, tech.name)
        except Refused as e:
            self.log.append(f'"{label[:60]}": {e}')
            return None
        source = f"computed:years({tech.name})"
        self.note = years_line(y)
        floor = AT_LEAST.search(label)
        bar = floor and (floor.group(1) or floor.group(2))
        if bar:
            if y["years"] >= int(bar):
                return self.fit(label, "Yes", options), source, "check"
            return None  # a computed floor below the bar settles nothing
        if options:
            return self.fit(label, str(y["years"]), options), source, "check"
        return str(y["years"]), source, "check"

    def from_profile(self, label, options, voluntary):
        fid = next((fid for pat, fid in PROFILE_RULES if pat.search(label)), None)
        if fid is None:
            return None
        if fid not in NAME_FIELDS and EXPERIENCE_Q.search(label):
            return None  # a question about experience is never an eligibility or EEO field
        value = self.profile.get(fid, "")
        source = f"profile:{fid}"
        if fid in EEO_FIELDS:
            if not value:  # blank means decline (v1.1)
                opt = next((o for o in options if DECLINE.search(o)), "")
                if opt:
                    self.log.append(f'"{label[:60]}": profile blank, chose "{opt}"')
                return opt, source, "ok"
            return self.fit(label, value, options), source, "ok"
        if fid == "authorized":
            named = {country_code(m) for m in re.findall(r"(?:in|for) (?:the )?([A-Z][A-Za-z. ]+?)(?:\?|$|,)", label)}
            named.discard("")
            want = named or ({self.country} if self.country else set())
            if not want or not want <= authorized_countries(value):
                return None  # authorization elsewhere is a question, never a guess
            return self.fit(label, "Yes", options), source, "ok"
        if not value:
            return None
        return self.fit(label, value, options), source, "ok"

    def from_bank(self, label, options, voluntary):
        a = find_answer(label, self.bank, self.country, self.slots())
        if a is None:
            return None
        value = a.answer
        if a.source.startswith("template:"):
            value = self.template(a, label)
            if not value:
                return None
        value = self.fit(label, value, options)
        source = "profile:standard_answers" if a.id == "profile" else f"answers:{a.id}"
        self.matched = a
        return value, source, "ok"

    def slots(self):
        return {"{company}": self.head.get("Company", ""), "{role}": self.head.get("Role", "")}

    def template(self, a, label):
        fam = a.source.split(":", 1)[1]
        if fam != self.family:
            return ""
        text = a.answer
        for slot, v in self.slots().items():
            if v:
                text = text.replace(slot, v)
        if checks.SLOT_RE.search(text):
            self.log.append(f'"{label[:60]}": template {a.id} has a slot the posting cannot fill')
            return ""
        return text

    def fit(self, label, value, options):
        """The value, or for a choice question the option that means it (logged)."""
        if not options:
            return value
        v = value.strip().lower()
        for o in options:
            if o.strip().lower() == v:
                return o
        word = v.split()[0].strip(".,") if v.split() else ""
        if word in ("yes", "no"):
            hit = next((o for o in options if o.strip().lower().split()[0].strip(".,") == word), None)
            if hit:
                self.log.append(f'"{label[:60]}": "{value}" -> option "{hit}"')
                return hit
            return ""
        scored = sorted(((similarity(o, value), k, o) for k, o in enumerate(options)), key=lambda x: (-x[0], x[1]))
        if scored and scored[0][0] >= 0.5:
            self.log.append(f'"{label[:60]}": "{value}" -> option "{scored[0][2]}"')
            return scored[0][2]
        self.log.append(f'"{label[:60]}": no option means "{value}"')
        return ""

    def plan(self):
        questions = parse_questions(self.posting)
        source = (self.head.get("Source") or "").lower()
        fields = []
        if questions:
            for q in questions:
                fields.append(self.field(q.label, q.qtype, q.required, q.options, q.voluntary))
        else:
            # Not a Greenhouse form: contact and eligibility fields, filled by meaning in the session.
            labels = {"first_name": "First name", "last_name": "Last name", "email": "Email", "phone": "Phone",
                      "location": "Location", "linkedin": "LinkedIn profile", "website": "Website",
                      "authorized": "Are you legally authorized to work in this country?",
                      "sponsorship": "Will you now or in the future require visa sponsorship?",
                      "adult": "Are you 18 or older?", "relocate": "Are you willing to relocate?",
                      "start": "Earliest start date", "salary": "Desired salary",
                      "background": "Are you willing to complete a background check?"}
            for fid in CONTACT_FIELDS + ELIGIBILITY_FIELDS:
                f = self.field(labels[fid])
                if f["answer"]:
                    fields.append(f)
        return {"form_source": "greenhouse_api" if questions and source.startswith("greenhouse") else "page",
                "fields": fields, "log": self.log}


def plan(root, job, family=None, write=True):
    """The fill plan dict; written to <job>/fill_plan.json unless write=False."""
    job = Path(job) if Path(job).is_absolute() else Path(root) / job
    if family is None:
        item = queue_item(root, job)
        family = item.get("family") if item else None
    p = Planner(root, job, family).plan()
    if write:
        jobfs.atomic_write_json(job / "fill_plan.json", p)
    return p


def plan_counts(p):
    counts = {"ok": 0, "needs_you": 0, "sensitive_confirm": 0, "check": 0}
    for f in p["fields"]:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return counts


# ---------- the queue ----------

def queue_items(root, day):
    """queue.json items plus every addendum's, in number order."""
    qdir = jobfs.Folder(root).queue_dir(day)
    items = []
    for path in [qdir / "queue.json"] + sorted(x for x in qdir.glob("addendum_*.json") if not x.name.endswith(".prev.json")):
        if not path.exists():
            continue
        try:
            data = jobfs.read_json_verified(path, retries=0)
        except jobfs.NotReady:
            continue
        items += [it for it in data.get("items", []) if isinstance(it, dict)]
    return sorted(items, key=lambda it: it.get("n", 0))


def queue_item(root, job):
    """The newest queue item whose folder is this job folder, or None."""
    try:
        rel = Path(job).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None
    for q in sorted(jobfs.Folder(root).queue.glob("*/*.json"), reverse=True):
        try:
            data = json.loads(q.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for it in data.get("items", []) if isinstance(data, dict) else []:
            if str(it.get("folder", "")).replace("\\", "/").rstrip("/") == rel:
                return it
    return None


def read_plan(folder):
    try:
        return jobfs.read_json_verified(folder / "fill_plan.json", retries=0)
    except jobfs.NotReady:
        return None


def read_list(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    return [q for q in data if isinstance(q, dict) and isinstance(q.get("q"), str)] if isinstance(data, list) else []


# ---------- questions ----------

class Batch:
    def __init__(self):
        self.groups = []

    def add(self, text, also=(), n=None, tier_a=False, qid=None, sensitive=False, origin="job"):
        forms = [text, *also]
        for g in self.groups:
            if any(same_question(x, y) for x in forms for y in g["forms"]):
                g["forms"] += [x for x in forms if x not in g["forms"]]
                break
        else:
            g = {"text": text, "forms": forms, "jobs": set(), "tier_a": False, "qids": [],
                 "sensitive": False, "origin": origin, "order": len(self.groups)}
            self.groups.append(g)
        if n is not None:
            g["jobs"].add(n)
        g["tier_a"] |= tier_a
        g["sensitive"] |= sensitive
        if qid and qid not in g["qids"]:
            g["qids"].append(qid)

    def ranked(self, cap):
        order = sorted(self.groups, key=lambda g: (-len(g["jobs"]), not g["tier_a"], g["order"]))
        return order[:cap]


def collect(root, day):
    """(batch, sensitive {(kind, answer): set(n)}, items with plans, every item)."""
    f = jobfs.Folder(root)
    items = queue_items(root, day)
    batch, sensitive, planned = Batch(), {}, set()
    bank = load_bank(f.answers)
    for it in items:
        folder = f.root / str(it.get("folder", ""))
        p = read_plan(folder) if it.get("folder") else None
        if p:
            planned.add(it.get("n"))
            for fld in p.get("fields", []):
                if fld.get("status") == "needs_you" and not find_answer(fld["label"], bank):
                    batch.add(fld["label"], n=it.get("n"), tier_a=it.get("tier") == "A",
                              qid=fld.get("qid") or question_id(fld["label"]), sensitive=bool(fld.get("sensitive")))
                elif fld.get("status") == "sensitive_confirm":
                    kind = fld.get("answer_source", "").split(":", 1)[-1] if fld.get("answer_source", "").startswith("profile:") else fld["label"]
                    sensitive.setdefault((kind, fld.get("answer", "")), set()).add(it.get("n"))
        if it.get("tier") == "A" and it.get("folder"):
            for q in read_list(folder / "questions.json")[:TIER_A_QUESTIONS]:
                batch.add(q["q"], q.get("also") or [], n=it.get("n"), tier_a=True, qid=question_id(q["q"]))
    for fam in sorted({it.get("family") for it in items if it.get("family")}):
        v = current_family_version(root, fam)
        if v is not None:
            for q in read_list(f.variant(fam, v) / "questions.json"):
                batch.add(q["q"], q.get("also") or [], qid=question_id(q["q"]), origin="variant")
    return batch, sensitive, planned, items


def current_family_version(root, family):
    try:
        text = jobfs.Folder(root).variant_current(family).read_text(encoding="utf-8-sig").strip()
        return int(text.lstrip("v"))
    except (OSError, ValueError):
        return None


def sensitive_line(sensitive, planned):
    parts = []
    for (kind, answer), ns in sorted(sensitive.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        where = "all" if ns == planned and len(ns) > 1 else ", ".join(f"#{n}" for n in sorted(ns))
        parts.append(f'{kind} "{answer}" on {where}')
    return "; ".join(parts)


def questions(root, day, cap=None):
    """{"questions": [...], "waiting": jobs waiting, "sensitive": line}."""
    if cap is None:
        cap = int(jobfs.settings(root)["daily"].get("questions_per_day", 6))
    batch, sensitive, planned, items = collect(root, day)
    top = batch.ranked(cap)
    waiting = set()
    for g in batch.groups:
        waiting |= g["jobs"]
    n_all = len(items)
    out = []
    for g in top:
        jobs = sorted(g["jobs"])
        out.append({"id": g["qids"][0] if g["qids"] else question_id(g["text"]), "text": g["text"],
                    "jobs": jobs, "all_jobs": bool(jobs) and len(jobs) == n_all and n_all > 1,
                    "tier_a": g["tier_a"], "sensitive": g["sensitive"], "origin": g["origin"],
                    "also": g["forms"][1:]})
    return {"questions": out, "waiting": len(waiting), "sensitive": sensitive_line(sensitive, planned)}


def print_questions(result):
    qs = result["questions"]
    head = f"QUESTIONS {len(qs)}"
    if result["waiting"]:
        head += f" ({result['waiting']} job{'s' if result['waiting'] != 1 else ''} wait)"
    print(head)
    for i, q in enumerate(qs, 1):
        if q["all_jobs"]:
            who = " (all jobs)"
        elif q["jobs"]:
            who = f" ({len(q['jobs'])} job{'s' if len(q['jobs']) != 1 else ''}: {', '.join(f'#{n}' for n in q['jobs'])})"
        else:
            who = " (variant)" if q["origin"] == "variant" else ""
        print(f"{i}. {q['text']}{who} [{q['id']}]")
    if result["sensitive"]:
        print(f"SENSITIVE {result['sensitive']}")


# ---------- add, migrate ----------

def backup(path, root):
    if Path(path).exists():
        return library.backup(path, root)
    return None


def bank_file(root):
    path = jobfs.Folder(root).answers
    if not path.exists():
        template = Path(__file__).resolve().parent.parent / "templates" / "answers.md"
        text = template.read_text(encoding="utf-8") if template.exists() else "# Screening answers\n\n"
        jobfs.atomic_write_text(path, text)
    return path


def add(root, question, answer, kind="text", scope="any", sensitive=None, source=None, also=()):
    """("ADDED"|"UPDATED", Answer). Refuses what is never stored."""
    if NEVER.search(question) or PII.search(question):
        raise Refused("REFUSED never stored: criminal history, attestations, consent and ID numbers are the user's part")
    for text in [question, answer, *also]:
        if "|" in text or "\n" in text:
            raise Refused("ERROR answers cannot contain | or line breaks")
    if kind not in KINDS:
        raise Refused(f"ERROR kind must be one of {', '.join(KINDS)}")
    scope = check_scope(scope)
    today = jobfs.today(root).isoformat()
    source = source or f"user:{today}"
    if not SOURCE_RE.match(source):
        raise Refused(f"ERROR source {source!r} is not profile:<field>, library:L-0001, computed:years(<term>), "
                      "template:<family> or user:YYYY-MM-DD")
    # Pay, visa and the like are always sensitive, whatever --sensitive says:
    # the question, an also: form or the answer itself ("$105,000") decides.
    sens = bool(sensitive) or any(SENSITIVE.search(x) for x in [question, *also]) or bool(SENSITIVE_ANSWER.search(answer))
    path = bank_file(root)
    text = path.read_text(encoding="utf-8-sig")
    bank = parse_bank(text)
    mine = [question, *also]
    probe = Answer("new", question, answer, kind)

    def same(a):
        # A yes/no, number or choice answer updates only the same question;
        # "... as the lab supervisor" is a new entry, never an overwrite.
        if strict_answer(probe) or strict_answer(a):
            return any(" ".join(tokens(x)) == " ".join(tokens(y)) or equivalent(x, y) for x in mine for y in a.forms())
        return any(same_question(x, y) for x in mine for y in a.forms())

    hit = next((a for a in bank if same(a)), None)
    backup(path, root)
    lines = text.splitlines()
    if hit:
        forms = hit.also + [x for x in mine if x and x != hit.question and x not in hit.also]
        new = Answer(hit.id, hit.question, answer, kind, scope, sens or hit.sensitive, source, today,
                     list(dict.fromkeys(forms)), hit.line)
        lines[hit.line - 1] = format_line(new)
        verb = "UPDATED"
    else:
        nums = [int(a.id.split("-")[1]) for a in bank if a.id.startswith("ANS-")]
        new = Answer(f"ANS-{(max(nums) + 1) if nums else 1:03d}", question, answer, kind, scope, sens,
                     source, today, [a for a in also if a])
        lines.append(format_line(new))
        verb = "ADDED"
    jobfs.atomic_write_text(path, "\n".join(lines) + "\n")
    return verb, new


def listing(root):
    """The bank as lines, a WARN for each legacy CA scope, then ANSWERS n=<count>."""
    path = jobfs.Folder(root).answers
    bank = load_bank(path)
    out = []
    for a in bank:
        out.append(f"{a.id} | {a.scope} | sensitive {'yes' if a.sensitive else 'no'} | {a.question} = {a.answer}")
        if a.scope.strip().upper() == LEGACY_CA:
            out.append(f'WARN {path.name} line {a.line}: scope "{a.scope.strip()}" is read as Canada; '
                       "write US or Canada")
    out.append(f"ANSWERS n={len(bank)}")
    return out


def migrate(root):
    f = jobfs.Folder(root)
    try:
        ptext = f.profile.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise Refused(f"ERROR cannot read {f.profile}: {e}")
    done, skipped = [], []
    for a in standard_answers(ptext):
        if not a.answer.strip():
            skipped.append(a.question)
            continue
        scope = "Canada" if a.scope.strip().upper() == LEGACY_CA else a.scope  # v1.1 wrote CA for Canada
        verb, new = add(root, a.question, a.answer, "text", scope, a.sensitive)
        done.append(f"{verb} {new.id} {a.question}")
    return done, skipped


# ---------- unpark ----------

def session_id(root):
    try:
        return str(json.loads(jobfs.Folder(root).session_json.read_text(encoding="utf-8")).get("task") or "answers.py")
    except (OSError, ValueError, AttributeError):
        return "answers.py"


def day_states(root, day):
    """job_key -> the last queue state today (events.fold over every valid event,
    pending and done): applied, skipped, later, blocked, handed_over, ..."""
    import events  # same folder; read only
    state = {}
    for it in events.fold(events.recent(root), str(day)).values():
        state[it.get("job_key")] = it.get("state")
    return state


def unpark(root, day, session=None):
    """Write an unparked event for each queued job whose needs are now answered.
    Only a job whose last state today is `parked`, or that has no state yet,
    comes back: an applied, skipped, later, blocked, started or handed-over job
    never does."""
    f = jobfs.Folder(root)
    state = day_states(root, day)
    done = []
    for it in queue_items(root, day):
        folder = f.root / str(it.get("folder", ""))
        old = read_plan(folder) if it.get("folder") else None
        needs = [fld for fld in (old or {}).get("fields", []) if fld.get("status") == "needs_you"]
        last = state.get(it.get("job_key"))
        parked = last == "parked"
        if last not in (None, "parked") or not (needs or parked):
            continue
        try:
            new = plan(root, folder, it.get("family"), write=False)
        except Refused:
            continue
        if any(fld["status"] == "needs_you" for fld in new["fields"]):
            continue
        ev_id = jobfs.new_id()
        event = {"v": jobfs.EVENT_SCHEMA, "id": ev_id, "type": "unparked", "at": jobfs.now_iso(root),
                 "session": session or session_id(root), "job_key": it.get("job_key", ""), "n": it.get("n"),
                 "date": str(day), "payload": {"reason": "unknown_required",
                                               "question_ids": [fld.get("qid") or question_id(fld["label"]) for fld in needs],
                                               "confirmation": "none", "proof": ""}}
        jobfs.create_only(f.event(ev_id, "unparked"), event)
        done.append((it, event))
    return done


# ---------- main ----------

class Parser(argparse.ArgumentParser):
    """A bad argument is bad input: ERROR and exit 1 (exit 2 is reserved)."""

    def error(self, message):
        print(f"ERROR {message}")
        sys.exit(1)


def main(argv=None):
    ap = Parser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "years", "questions", "add", "list", "unpark", "migrate"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True, help="the JobSearch folder")
    sub.choices["plan"].add_argument("--job", required=True, help="the job folder")
    sub.choices["plan"].add_argument("--family")
    sub.choices["plan"].add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    sub.choices["years"].add_argument("--skill", required=True)
    for name in ("questions", "unpark"):
        sub.choices[name].add_argument("--date", required=True, help="YYYY-MM-DD")
    sub.choices["questions"].add_argument("--json", action="store_true")
    sub.choices["unpark"].add_argument("--session")
    a = sub.choices["add"]
    a.add_argument("--question", required=True)
    a.add_argument("--answer", required=True)
    a.add_argument("--kind", default="text")
    a.add_argument("--scope", default="any", help="any, US or Canada (any case)")
    a.add_argument("--sensitive", choices=["yes", "no"])
    a.add_argument("--source")
    a.add_argument("--also", default="", help="aliases separated by ;")
    args = ap.parse_args(argv)
    root = Path(args.root)
    for stream in (sys.stdout, sys.stderr):  # list prints the user's own answers as written
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        return run(args, root)
    except Refused as e:
        print(str(e))
        return 1


def run(args, root):
    if args.cmd == "plan":
        job = Path(args.job) if Path(args.job).is_absolute() else root / args.job
        p = plan(root, job, args.family, write=not args.dry_run)
        c = plan_counts(p)
        print(f"PLAN fields={len(p['fields'])} ok={c['ok']} needs_you={c['needs_you']} "
              f"sensitive_confirm={c['sensitive_confirm']} check={c['check']} ({p['form_source']})")
        for fld in p["fields"]:
            if fld["status"] == "needs_you":
                print(f'NEEDS {fld["qid"]} "{fld["label"]}"')
            elif fld["status"] == "sensitive_confirm":
                print(f'SENSITIVE "{fld["label"]}" = "{fld["answer"]}" ({fld["answer_source"]})')
            elif fld["status"] == "check" and fld["answer"]:
                print(f'CHECK "{fld["label"]}" = "{fld["answer"]}" ({fld["answer_source"]})'
                      + (f' {fld["note"]}' if fld.get("note") else ""))
            if fld.get("missing") == "cover letter":
                print(f'LETTER "{fld["label"]}" is required and no cover letter is built in this job folder: '
                      "write cover_letter.json and build it with --strict (writing_rules.md, Cover letter)")
        if args.dry_run:
            print(json.dumps(p, indent=1))
        else:
            print(f"WROTE {job / 'fill_plan.json'}")
        return 0
    if args.cmd == "years":
        print(years_line(years(root, args.skill)))
        return 0
    if args.cmd == "questions":
        result = questions(root, args.date)
        if args.json:
            print(json.dumps(result, indent=1))
        else:
            print_questions(result)
        return 0
    if args.cmd == "add":
        also = [x.strip() for x in args.also.split(";") if x.strip()]
        sens = None if args.sensitive is None else args.sensitive == "yes"
        verb, new = add(root, args.question, args.answer, args.kind, args.scope, sens, args.source, also)
        print(f"{verb} {new.id}")
        return 0
    if args.cmd == "list":
        for line in listing(root):
            print(line)
        return 0
    if args.cmd == "unpark":
        done = unpark(root, args.date, args.session)
        for it, ev in done:
            print(f"UNPARKED #{it.get('n')} {it.get('job_key', '')} ({', '.join(ev['payload']['question_ids']) or 'parked'})")
        print(f"UNPARK n={len(done)}")
        return 0
    done, skipped = migrate(root)
    for line in done:
        print(line)
    if skipped:
        print(f"SKIPPED {len(skipped)} blank: {'; '.join(skipped)}")
    print(f"MIGRATE n={len(done)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
