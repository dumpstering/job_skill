"""Build a resume or cover letter from JSON into .docx, .pdf and .txt.

Usage:
    python build_resume.py resume.json --out <folder> --base "First Last Resume"
        [--sources FILE [FILE ...]] [--terms terms.json]
        [--mode industry|cv|usajobs] [--max-pages N] [--publications publications.md]
        [--posting posting.md] [--strict]

The JSON format is documented in references/writing_rules.md (Block A and
Cover letter) and references/cv_publications.md (citations, modes). The .docx
uses Arial and the .pdf Helvetica, which share metrics, so the PDF's page
count holds for both. Lint warnings mirror the writing rules a script can
check; the agent fixes each or keeps it for a stated reason. After the WARN
lines come the checks in checks.py: PARSE (the PDF read back as text), FACT
(numbers, names and dates against --sources), EVIDENCE (ev tags against the
library), CITE (citations against --publications), LETTER (a letter against
--posting) and KEYWORD (posting terms from --terms). The agent acts on each
line they print: fix the resume, or carry the item into "Verify before
sending".

v1.2 flags (without them the v1.1 behavior is unchanged):
- --mode sets the page cap: industry 2, usajobs 2 (hard), cv none (a warning
  above 8 pages, and the sections are put in the CV order). --max-pages
  lowers the cap (or sets one for cv).
- --publications fills {"citations": ["P-003"]} sections with the exact
  publications.md text, *italics* rendered, the user's name in bold with
  "bold_name": true. Citations skip the bullet lint and FACT.
- --posting runs the letter check: company and role equal posting.md's.
- --strict (mode industry unless given) fails the build on any FACT line,
  PARSE other than ok, an EVIDENCE, CITE or LETTER problem, pages over the
  cap, or a banned word, printing one STRICT-FAIL line each.
- Any v1.2 flag ends the output with VERDICT ok|fail.

Exit codes: 0 built, 1 bad input, 2 a package is missing (pip install
python-docx reportlab), 6 STRICT-FAIL (only with --strict). Without --strict
the checks never change the exit code.
"""
import argparse
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks  # noqa: E402

EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
BULLET = chr(0x2022)
FONT_DOCX = "Arial"
FONT_PDF = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
PAPER_IN = {"letter": (8.5, 11.0), "a4": (8.27, 11.69)}

# v1.2 document modes (spec 4.8): page caps and the CV section order.
MODE_CAPS = {"industry": 2, "usajobs": 2, "cv": None}
CV_WARN_PAGES = 8
CV_ORDER = ["education", "research experience", "professional experience", "publications",
            "presentations", "patents", "grants and fellowships", "teaching and mentoring",
            "techniques", "awards", "service", "memberships", "references"]
CV_ALIASES = {"experience": "professional experience", "selected publications": "publications",
              "skills": "techniques", "honors": "awards", "awards and honors": "awards",
              "honors and awards": "awards", "grants": "grants and fellowships",
              "fellowships": "grants and fellowships", "teaching": "teaching and mentoring",
              "mentoring": "teaching and mentoring", "professional service": "service",
              "professional memberships": "memberships"}
CITATION_ID = re.compile(r"^P-\d{3,}$")
BULLET_KEYS = {"text", "ev", "keep"}

# Mirror of the Writing style list in references/writing_rules.md.
BANNED = [
    "proven", "strong", "rigorous", "highly skilled", "expert", "data-driven",
    "results-driven", "data-fluent", "dynamic", "passionate", "spearheaded",
    "leveraged", "leveraging", "utilized", "utilizing", "orchestrated",
    "robust", "seamless", "seamlessly", "synergy", "cutting-edge",
    "high-velocity", "engine", "track record", "fast-paced",
]
PRONOUNS = re.compile(r"\b(I|me|my|mine|we|our|ours)\b")
SECTION_KEYS = {"heading", "paragraph", "lines", "entries", "bullets", "citations", "bold_name"}
ENTRY_KEYS = {"title", "dates", "org", "location", "bullets"}


class InputError(Exception):
    pass


text_of = checks.bullet_text


def check_bullets(bullets, where):
    """A bullet is a string (v1.1) or {"text", "ev": [L- IDs], "keep": bool}."""
    if not isinstance(bullets, list):
        raise InputError(f'"bullets" in {where} must be a list')
    for b in bullets:
        if isinstance(b, str):
            continue
        if not isinstance(b, dict) or not isinstance(b.get("text"), str):
            raise InputError(f'a bullet in {where} must be a string or {{"text": ..., "ev": [...]}}')
        extra = set(b) - BULLET_KEYS
        if extra:
            raise InputError(f'a bullet in {where} has unknown keys {sorted(extra)}')
        ev = b.get("ev", [])
        if not isinstance(ev, list) or not all(isinstance(i, str) for i in ev):
            raise InputError(f'"ev" of a bullet in {where} must be a list of library IDs')


# ---------- input ----------

def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise InputError(f"cannot read {path}: {e}")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise InputError('"name" is required')
    contact = data.setdefault("contact", [])
    if not isinstance(contact, list) or not all(isinstance(c, str) for c in contact):
        raise InputError('"contact" must be a list of strings')
    if "letter" in data:
        letter = data["letter"]
        if not isinstance(letter.get("paragraphs"), list) or not letter["paragraphs"]:
            raise InputError('"letter.paragraphs" must be a non-empty list')
    else:
        sections = data.get("sections")
        if not isinstance(sections, list) or not sections:
            raise InputError('"sections" must be a non-empty list (or give "letter")')
        for i, s in enumerate(sections, 1):
            if not isinstance(s.get("heading"), str):
                raise InputError(f"section {i} needs a \"heading\"")
            extra = set(s) - SECTION_KEYS
            if extra:
                raise InputError(f'section "{s["heading"]}" has unknown keys {sorted(extra)}')
            for j, e in enumerate(s.get("entries", []), 1):
                if not isinstance(e.get("title"), str):
                    raise InputError(f'entry {j} in "{s["heading"]}" needs a "title"')
                extra = set(e) - ENTRY_KEYS
                if extra:
                    raise InputError(f'entry "{e["title"]}" has unknown keys {sorted(extra)}')
                check_bullets(e.get("bullets", []), f'"{e["title"]}"')
            check_bullets(s.get("bullets", []), f'"{s["heading"]}"')
            for line in s.get("lines", []):
                if "label" not in line or "text" not in line:
                    raise InputError(f'lines in "{s["heading"]}" need "label" and "text"')
            if "citations" in s:
                cites = s["citations"]
                if not isinstance(cites, list) or not all(isinstance(c, str) and CITATION_ID.match(c) for c in cites):
                    raise InputError(f'"citations" in "{s["heading"]}" must be a list of publication IDs like "P-003"')
                if set(s) & {"paragraph", "lines", "entries", "bullets"}:
                    raise InputError(f'section "{s["heading"]}" has "citations"; it cannot also hold other content')
    return data


# ---------- v1.2: citations and modes ----------

def prepare(data, pubs=None, mode=None, warnings=None):
    """Resolve citations into s["_cites"] (the exact text to print) and, in cv
    mode, put the sections in the CV order. Raises InputError for a citation
    section without --publications or an ID publications.md lacks."""
    if "letter" in data:
        return data
    for s in data["sections"]:
        if "citations" in s:
            if pubs is None:
                raise InputError(f'section "{s["heading"]}" has citations: give --publications publications.md')
            missing = [c for c in s["citations"] if c not in pubs]
            if missing:
                raise InputError(f'citation {", ".join(missing)} is not in publications.md')
            s["_cites"] = [checks.printable(pubs[c]) for c in s["citations"]]
        elif (pubs is not None and s.get("bullets") and not s.get("entries")
              and checks.CITE_HEADING.search(s["heading"])):
            # Citations typed out as bullets: printed as written, checked by CITE.
            s["_cite"] = True
            s["_cites"] = [text_of(b) for b in s["bullets"]]
    if mode == "cv":
        cv_order(data, warnings if warnings is not None else [])
    return data


def cv_order(data, warnings):
    """Stable sort into CV_ORDER; a heading not in it stays after the one before it."""
    ranks, last = [], -1.0
    for s in data["sections"]:
        key = s["heading"].strip().lower()
        key = CV_ALIASES.get(key, key)
        if key in CV_ORDER:
            last = float(CV_ORDER.index(key))
        ranks.append(last)
    order = sorted(range(len(ranks)), key=lambda i: ranks[i])
    if order != list(range(len(ranks))):
        data["sections"] = [data["sections"][i] for i in order]
        warnings.append("cv mode: sections reordered to the CV order (" +
                        ", ".join(s["heading"] for s in data["sections"]) + ")")


def author_pattern(name):
    """The user's name as an author: "Avery JQ", "Avery, J. Q.", "J. Q. Avery", "Jordan Q. Avery"."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", name or "")
    if len(words) < 2:
        return None
    last, first = re.escape(words[-1]), words[0]
    i = re.escape(first[0])
    pats = [last + r",?\s+" + i + r"\.?(?:\s?[A-Z]\.?){0,2}",
            i + r"\.(?:\s?[A-Z]\.){0,2}\s+" + last,
            re.escape(first) + r"(?:\s+[A-Z][A-Za-z]*\.?)?\s+" + last]
    return re.compile(r"(?<![A-Za-z])(?:" + "|".join(pats) + r")(?![A-Za-z])")


def cite_runs(text, name=None):
    """[(text, bold, italic)] for one citation: *...* is italic, and with a
    name its author forms are bold. An unpaired * stays as written."""
    parts = text.split("*")
    if len(parts) % 2 == 0:
        parts = [text]
    pat = author_pattern(name) if name else None
    runs = []
    for k, part in enumerate(parts):
        italic = k % 2 == 1
        pos = 0
        for m in (pat.finditer(part) if pat else []):
            if m.start() > pos:
                runs.append((part[pos:m.start()], False, italic))
            runs.append((m.group(0), True, italic))
            pos = m.end()
        if pos < len(part):
            runs.append((part[pos:], False, italic))
    return [r for r in runs if r[0]]


def plain_cite(text):
    return "".join(t for t, _, _ in cite_runs(text))


def section_name(data, s):
    return data["name"] if s.get("bold_name") else None


def page_settings(data, warnings):
    page = data.get("page", {})
    paper = str(page.get("paper", "letter")).lower()
    if paper not in PAPER_IN:
        raise InputError('"page.paper" must be "letter" or "a4"')
    size = float(page.get("font_size", 10.5))
    if not 10 <= size <= 11:
        warnings.append(f"font_size {size} is outside 10-11; using {min(max(size, 10), 11)}")
        size = min(max(size, 10), 11)
    margin = float(page.get("margin_in", 0.6))
    if margin < 0.5:
        warnings.append(f"margin_in {margin} is below 0.5; using 0.5")
        margin = 0.5
    return paper, size, margin


def show_dates(dates):
    return (dates or "").replace(" - ", f" {EN_DASH} ")


# ---------- lint ----------

def lint(data):
    out = []

    def check_text(where, text, is_bullet, first_person_ok=False):
        low = text.lower()
        for word in BANNED:
            if re.search(r"\b" + re.escape(word) + r"\b", low):
                out.append(f'{where}: "{word}" is on the banned-word list')
        if is_bullet and EM_DASH in text:
            out.append(f"{where}: em dash in a bullet")
        m = None if first_person_ok else PRONOUNS.search(text)
        if m:
            out.append(f'{where}: first-person "{m.group(0)}"')
        if is_bullet and len(text) > 200:
            out.append(f"{where}: {len(text)} characters (about 200 at most)")
        check_chars(where, text)

    def check_chars(where, text):
        bad = sorted({c for c in text if not _cp1252(c)})
        if bad:
            out.append(f"{where}: characters the PDF font cannot show: {bad}")

    check_chars("name", data["name"])
    for c in data["contact"]:
        check_chars("contact", c)
    for s in data.get("sections", []):
        check_chars("heading", s["heading"])
        if checks.is_citation(s):
            continue  # CITE checks citations, including the font
        for ln in s.get("lines", []):
            check_chars(f'{s["heading"]} line', f'{ln["label"]} {ln["text"]}')
        for e in s.get("entries", []):
            check_chars(f'{s["heading"]} entry', " ".join(e.get(k, "") for k in ("title", "dates", "org", "location")))
    if "letter" in data:
        L = data["letter"]
        check_chars("letter", " ".join([L.get("date", ""), L.get("greeting", ""), L.get("closing", "")] + L.get("recipient", [])))

    def check_run(where, bullets):
        firsts = [b.split()[0].lower() if b.split() else "" for b in bullets]
        for k in range(len(firsts) - 2):
            if firsts[k] and firsts[k] == firsts[k + 1] == firsts[k + 2]:
                out.append(f'{where}: three bullets in a row start with "{firsts[k]}"')
                break

    if "letter" in data:
        for i, p in enumerate(data["letter"]["paragraphs"], 1):
            check_text(f"letter paragraph {i}", p, False, first_person_ok=True)
        words = sum(len(p.split()) for p in data["letter"]["paragraphs"])
        if words > 350:
            out.append(f"letter is {words} words (about 300 at most)")
        return out

    for s in data["sections"]:
        h = s["heading"]
        if checks.is_citation(s):
            continue
        if "paragraph" in s:
            check_text(h, s["paragraph"], False)
            if h.lower() == "summary":
                n = len(s["paragraph"].split())
                if n > 60:
                    out.append(f"Summary is {n} words (60 at most)")
        for e in s.get("entries", []):
            bullets = [text_of(b) for b in e.get("bullets", [])]
            for i, b in enumerate(bullets, 1):
                check_text(f'{h} / {e["title"]} bullet {i}', b, True)
            check_run(f'{h} / {e["title"]}', bullets)
        for i, b in enumerate(s.get("bullets", []), 1):
            check_text(f"{h} bullet {i}", text_of(b), True)
    return out


_cp1252 = checks._cp1252


# ---------- text ----------

def build_txt(data):
    lines = [data["name"].upper(), " | ".join(data["contact"]), ""]
    if "letter" in data:
        L = data["letter"]
        lines += [L.get("date", ""), ""] + L.get("recipient", []) + [""]
        if L.get("greeting"):
            lines += [L["greeting"], ""]
        for p in L["paragraphs"]:
            lines += [p, ""]
        lines += [L.get("closing", "Sincerely,"), data["name"]]
        return "\n".join(lines).strip() + "\n"
    for s in data["sections"]:
        lines.append(s["heading"].upper())
        if "_cites" in s:
            lines += [f"- {plain_cite(c)}" for c in s["_cites"]]
            lines.append("")
            continue
        if "paragraph" in s:
            lines.append(s["paragraph"])
        for ln in s.get("lines", []):
            lines.append(f'{ln["label"]}: {ln["text"]}')
        for e in s.get("entries", []):
            head = " | ".join(x for x in (e["title"], e.get("org"), e.get("location"), e.get("dates")) if x)
            lines.append(head)
            lines += [f"- {text_of(b)}" for b in e.get("bullets", [])]
        lines += [f"- {text_of(b)}" for b in s.get("bullets", [])]
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# ---------- docx ----------

def build_docx(data, path, paper, size, margin):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    def set_font(style_or_run, bold=None, italic=None, pt=None):
        f = style_or_run.font
        f.name = FONT_DOCX
        f.color.rgb = RGBColor(0, 0, 0)
        if pt:
            f.size = Pt(pt)
        if bold is not None:
            f.bold = bold
        if italic is not None:
            f.italic = italic
        rpr = style_or_run.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            fonts.attrib.pop(qn(attr), None)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            fonts.set(qn(attr), FONT_DOCX)

    def tight(p, before=0, after=0):
        pf = p.paragraph_format
        pf.space_before = Pt(before)
        pf.space_after = Pt(after)
        pf.line_spacing = 1.0
        return pf

    def link(paragraph, text, url):
        rel = paragraph.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
        h = OxmlElement("w:hyperlink")
        h.set(qn("r:id"), rel)
        r = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.text = text
        t.set(qn("xml:space"), "preserve")
        r.append(t)
        h.append(r)
        paragraph._p.append(h)

    doc = Document()
    sec = doc.sections[0]
    w, h = PAPER_IN[paper]
    sec.page_width, sec.page_height = Inches(w), Inches(h)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Inches(margin))
    text_width = Inches(w - 2 * margin)

    normal = doc.styles["Normal"]
    set_font(normal, pt=size)
    tight(normal)
    head = doc.styles["Heading 1"]
    set_font(head, bold=True, italic=False, pt=size + 1.5)
    hpf = head.paragraph_format
    hpf.space_before, hpf.space_after, hpf.keep_with_next = Pt(8), Pt(3), True
    ppr = head.element.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "000000")):
        bottom.set(qn(k), v)
    bdr.append(bottom)
    # Schema order matters to Word: pBdr goes before shd, tabs, spacing, ind, jc...
    ppr.insert_element_before(bdr, "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku",
                              "w:wordWrap", "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE",
                              "w:autoSpaceDN", "w:bidi", "w:adjustRightInd", "w:snapToGrid",
                              "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
                              "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                              "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle",
                              "w:rPr", "w:sectPr", "w:pPrChange")
    bullet_style = doc.styles["List Bullet"]
    set_font(bullet_style, pt=size)
    tight(bullet_style)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tight(p, after=2)
    set_font(p.add_run(data["name"]), bold=True, pt=size + 8)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tight(p, after=4)
    for i, item in enumerate(data["contact"]):
        if i:
            p.add_run(" | ")
        if "@" in item and " " not in item:
            link(p, item, "mailto:" + item)
        elif re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", item, re.I):
            link(p, item, item if item.startswith("http") else "https://" + item)
        else:
            p.add_run(item)

    def two_sided(left, right, bold=False, italic=False, before=0):
        para = doc.add_paragraph()
        pf = tight(para, before=before)
        pf.keep_with_next = True
        pf.tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT)
        r = para.add_run(left)
        r.bold, r.italic = bold, italic
        if right:
            r = para.add_run("\t" + right)
            r.italic = italic

    if "letter" in data:
        L = data["letter"]
        tight(doc.add_paragraph(L.get("date", "")), before=12, after=10)
        for line in L.get("recipient", []):
            tight(doc.add_paragraph(line))
        if L.get("greeting"):
            tight(doc.add_paragraph(L["greeting"]), before=10, after=8)
        for para in L["paragraphs"]:
            tight(doc.add_paragraph(para), after=8)
        tight(doc.add_paragraph(L.get("closing", "Sincerely,")), before=4, after=18)
        tight(doc.add_paragraph(data["name"]))
    else:
        for s in data["sections"]:
            doc.add_paragraph(s["heading"], style="Heading 1")
            if "_cites" in s:
                for c in s["_cites"]:
                    para = doc.add_paragraph(style="List Bullet")
                    for text, bold, italic in cite_runs(c, section_name(data, s)):
                        run = para.add_run(text)
                        run.bold, run.italic = bold or None, italic or None
                continue
            if "paragraph" in s:
                tight(doc.add_paragraph(s["paragraph"]))
            for ln in s.get("lines", []):
                para = doc.add_paragraph()
                tight(para, after=1)
                para.add_run(ln["label"] + ": ").bold = True
                para.add_run(ln["text"])
            for k, e in enumerate(s.get("entries", [])):
                two_sided(e["title"], show_dates(e.get("dates")), bold=True, before=0 if k == 0 else 5)
                if e.get("org") or e.get("location"):
                    two_sided(e.get("org", ""), e.get("location", ""), italic=True)
                for b in e.get("bullets", []):
                    doc.add_paragraph(text_of(b), style="List Bullet")
            for b in s.get("bullets", []):
                doc.add_paragraph(text_of(b), style="List Bullet")

    now = datetime.now()
    cp = doc.core_properties
    cp.author = cp.last_modified_by = data["name"]
    cp.title = Path(path).stem
    cp.comments = cp.subject = cp.keywords = cp.category = ""
    cp.created = cp.modified = now
    cp.revision = 1
    doc.save(path)


# ---------- pdf ----------

def cite_markup(text, name=None):
    """Paragraph markup for one citation: <i> for *italics*, <b> for the name."""
    out = []
    for t, bold, italic in cite_runs(text, name):
        t = escape(t)
        if italic:
            t = f"<i>{t}</i>"
        if bold:
            t = f"<b>{t}</b>"
        out.append(t)
    return "".join(out)


def build_pdf(data, path, paper, size, margin):
    from reportlab.lib.colors import black
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus.flowables import HRFlowable

    regular, bold, italic = FONT_PDF
    lead = size * 1.2

    class TwoSided(Flowable):
        """Left text and right-aligned text on one baseline, in reading order."""

        def __init__(self, left, right, font, rfont):
            super().__init__()
            self.left, self.right, self.font, self.rfont = left, right, font, rfont

        def wrap(self, avail_w, avail_h):
            self.width = avail_w
            need = stringWidth(self.left, self.font, size) + stringWidth(self.right, self.rfont, size) + 12
            self.split_lines = need > avail_w
            self.height = lead * (2 if self.split_lines else 1)
            return self.width, self.height

        def draw(self):
            c = self.canv
            y = self.height - size
            c.setFont(self.font, size)
            c.drawString(0, y, self.left)
            if self.right:
                if self.split_lines:
                    y -= lead
                c.setFont(self.rfont, size)
                c.drawRightString(self.width, y, self.right)

    body = ParagraphStyle("body", fontName=regular, fontSize=size, leading=lead)
    name_st = ParagraphStyle("name", parent=body, fontName=bold, fontSize=size + 8, leading=(size + 8) * 1.2, alignment=TA_CENTER, spaceAfter=2)
    contact_st = ParagraphStyle("contact", parent=body, alignment=TA_CENTER, spaceAfter=4)
    head_st = ParagraphStyle("head", parent=body, fontName=bold, fontSize=size + 1.5, leading=(size + 1.5) * 1.2, spaceBefore=8, spaceAfter=1)
    bullet_st = ParagraphStyle("bullet", parent=body, leftIndent=14, bulletIndent=3, bulletFontName=regular)
    para_st = ParagraphStyle("para", parent=body, spaceAfter=8)

    def contact_markup(items):
        parts = []
        for item in items:
            text = escape(item)
            if "@" in item and " " not in item:
                parts.append(f'<a href="mailto:{text}" color="black">{text}</a>')
            elif re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", item, re.I):
                url = item if item.startswith("http") else "https://" + item
                parts.append(f'<a href="{escape(url)}" color="black">{text}</a>')
            else:
                parts.append(text)
        return " | ".join(parts)

    story = [Paragraph(escape(data["name"]), name_st), Paragraph(contact_markup(data["contact"]), contact_st)]

    if "letter" in data:
        L = data["letter"]
        story += [Spacer(1, 12), Paragraph(escape(L.get("date", "")), para_st)]
        story += [Paragraph(escape(x), body) for x in L.get("recipient", [])]
        if L.get("greeting"):
            story += [Spacer(1, 10), Paragraph(escape(L["greeting"]), para_st)]
        story += [Paragraph(escape(x), para_st) for x in L["paragraphs"]]
        story += [Spacer(1, 4), Paragraph(escape(L.get("closing", "Sincerely,")), body), Spacer(1, 18), Paragraph(escape(data["name"]), body)]
    else:
        for s in data["sections"]:
            blocks = []
            if "_cites" in s:
                name = section_name(data, s)
                blocks += [[Paragraph(cite_markup(c, name), bullet_st, bulletText=BULLET)] for c in s["_cites"]]
            if "paragraph" in s:
                blocks.append([Paragraph(escape(s["paragraph"]), body)])
            for ln in s.get("lines", []):
                blocks.append([Paragraph(f'<font name="{bold}">{escape(ln["label"])}:</font> {escape(ln["text"])}', ParagraphStyle("line", parent=body, spaceAfter=1))])
            for k, e in enumerate(s.get("entries", [])):
                block = [Spacer(1, 0 if k == 0 else 5), TwoSided(e["title"], show_dates(e.get("dates")), bold, regular)]
                if e.get("org") or e.get("location"):
                    block.append(TwoSided(e.get("org", ""), e.get("location", ""), italic, italic))
                block += [Paragraph(escape(text_of(b)), bullet_st, bulletText=BULLET) for b in e.get("bullets", [])]
                blocks.append(block)
            if s.get("bullets") and "_cites" not in s:
                blocks.append([Paragraph(escape(text_of(b)), bullet_st, bulletText=BULLET) for b in s["bullets"]])
            heading = [Paragraph(escape(s["heading"]), head_st), HRFlowable(width="100%", thickness=0.6, color=black, spaceBefore=1, spaceAfter=3)]
            if not blocks:
                story += heading
                continue
            first = blocks[0]
            # Keep the heading with its first entry header and first bullet.
            story.append(KeepTogether(heading + first[:4]))
            story += first[4:]
            for block in blocks[1:]:
                story.append(KeepTogether(block[:4]))
                story += block[4:]

    w, h = (letter if paper == "letter" else A4)
    target = path if isinstance(path, io.BytesIO) else str(path)
    title = "resume" if isinstance(path, io.BytesIO) else Path(path).stem
    doc = SimpleDocTemplate(target, pagesize=(w, h), leftMargin=margin * inch, rightMargin=margin * inch,
                            topMargin=margin * inch, bottomMargin=margin * inch,
                            title=title, author=data["name"], creator=data["name"], subject="")
    doc.build(story)
    return doc.page


def count_pages(data):
    """PDF page count of prepared data, built in memory (variants.py tune)."""
    warnings = []
    paper, size, margin = page_settings(data, warnings)
    return build_pdf(data, io.BytesIO(), paper, size, margin)


# ---------- main ----------

def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("json_path")
    ap.add_argument("--out", required=True, help="folder to write into")
    ap.add_argument("--base", required=True, help='file name without extension, e.g. "First Last Resume"')
    ap.add_argument("--sources", nargs="+", metavar="FILE", help="master_resume.md, achievement_library.md, notes.md, profile.md: enables the FACT check")
    ap.add_argument("--terms", metavar="terms.json", help="posting terms (strings or {term, aliases}): enables the KEYWORD check")
    ap.add_argument("--mode", choices=sorted(MODE_CAPS), help="v1.2 document mode: page cap and section order")
    ap.add_argument("--max-pages", type=int, metavar="N", help="v1.2: lower the page cap (or set one in cv mode)")
    ap.add_argument("--publications", metavar="publications.md", help="v1.2: fills citation sections and enables CITE")
    ap.add_argument("--posting", metavar="posting.md", help="v1.2: letter check against the posting's company and role")
    ap.add_argument("--strict", action="store_true", help="v1.2: exit 6 with STRICT-FAIL lines on any failed check")
    return ap.parse_args(argv)


def page_cap(mode, max_pages):
    """The hard page cap for a mode: industry and usajobs 2, cv none (--max-pages lowers it)."""
    cap = MODE_CAPS.get(mode)
    if max_pages:
        cap = min(cap, max_pages) if cap else max_pages
    return cap


def main(argv=None, emit=print):
    """Build and check; returns the exit code. emit receives every output line
    (variants.py collects them in-process)."""
    args = parse_args(argv)
    v12 = bool(args.strict or args.mode or args.max_pages or args.publications or args.posting)
    mode = args.mode or ("industry" if args.strict else None)
    warnings = []
    try:
        data = load(args.json_path)
        paper, size, margin = page_settings(data, warnings)
        pubs = checks.load_publications(args.publications) if args.publications else None
        posting = Path(args.posting).read_text(encoding="utf-8-sig") if args.posting else None
        prepare(data, pubs, mode, warnings)
    except (InputError, ValueError, OSError) as e:
        emit(f"ERROR {e}")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    code = 0

    def target(ext):
        # Not with_suffix: a base like "John Q. Public Resume" would lose everything after "Q".
        return out / (args.base + ext)

    target(".txt").write_text(build_txt(data), encoding="utf-8")
    emit(f"wrote: {target('.txt')}")

    try:
        build_docx(data, target(".docx"), paper, size, margin)
        emit(f"wrote: {target('.docx')}")
    except ImportError:
        emit("MISSING python-docx: run  pip install python-docx  and build again")
        code = 2

    pdf_built, pages = False, None
    try:
        pages = build_pdf(data, target(".pdf"), paper, size, margin)
        pdf_built = True
        emit(f"wrote: {target('.pdf')} ({pages} page{'s' if pages != 1 else ''})")
    except ImportError:
        emit("MISSING reportlab: run  pip install reportlab  and build again")
        code = 2

    cap = page_cap(mode, args.max_pages) if "letter" not in data else None
    over = pages is not None and cap is not None and pages > cap
    if over:
        warnings.append(f"pages {pages} > {cap} ({mode or 'page'} cap {cap})")
    elif mode == "cv" and pages and pages > CV_WARN_PAGES and cap is None:
        warnings.append(f"pages {pages}: a CV over {CV_WARN_PAGES} pages (no cap; trim older or minor items)")
    lint_lines = lint(data)
    for w in warnings + lint_lines:
        emit(f"WARN {w}")
    evidence = (args.strict and "letter" not in data) or "auto"
    lines = checks.run_checks(data, target(".pdf") if pdf_built else None, target(".txt"), args.sources, args.terms,
                              pubs=pubs, evidence=evidence, posting=posting, letter=v12)
    for line in lines:
        emit(line)
    if not v12:
        return code

    failures = checks.strict_failures(lines)
    if not pdf_built:
        failures.append("PARSE not run (no PDF built)")
    if not args.sources:
        failures.append("FACT not run (no --sources)")
    if over:
        failures.append(f"pages {pages} > {cap}")
    failures += [f"lint {w}" for w in lint_lines if "is on the banned-word list" in w]
    for line in checks.verdict(failures, args.strict):
        emit(line)
    if args.strict and failures and code == 0:
        code = 6
    return code


if __name__ == "__main__":
    sys.exit(main())
