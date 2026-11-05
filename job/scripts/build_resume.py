"""Build a resume or cover letter from JSON into .docx, .pdf and .txt.

Usage:
    python build_resume.py resume.json --out <folder> --base "First Last Resume"

The JSON format is documented in references/writing_rules.md (Block A and
Cover letter). The .docx uses Arial and the .pdf Helvetica, which share
metrics, so the PDF's page count holds for both. Lint warnings mirror the
writing rules a script can check; the agent fixes each or keeps it for a
stated reason.

Exit codes: 0 built, 1 bad input, 2 a package is missing (pip install
python-docx reportlab).
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
BULLET = chr(0x2022)
FONT_DOCX = "Arial"
FONT_PDF = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
PAPER_IN = {"letter": (8.5, 11.0), "a4": (8.27, 11.69)}

# Mirror of the Writing style list in references/writing_rules.md.
BANNED = [
    "proven", "strong", "rigorous", "highly skilled", "expert", "data-driven",
    "results-driven", "data-fluent", "dynamic", "passionate", "spearheaded",
    "leveraged", "leveraging", "utilized", "utilizing", "orchestrated",
    "robust", "seamless", "seamlessly", "synergy", "cutting-edge",
    "high-velocity", "engine", "track record", "fast-paced",
]
PRONOUNS = re.compile(r"\b(I|me|my|mine|we|our|ours)\b")
SECTION_KEYS = {"heading", "paragraph", "lines", "entries", "bullets"}
ENTRY_KEYS = {"title", "dates", "org", "location", "bullets"}


class InputError(Exception):
    pass


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
            for line in s.get("lines", []):
                if "label" not in line or "text" not in line:
                    raise InputError(f'lines in "{s["heading"]}" need "label" and "text"')
    return data


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
        if "paragraph" in s:
            check_text(h, s["paragraph"], False)
            if h.lower() == "summary":
                n = len(s["paragraph"].split())
                if n > 60:
                    out.append(f"Summary is {n} words (60 at most)")
        for e in s.get("entries", []):
            bullets = e.get("bullets", [])
            for i, b in enumerate(bullets, 1):
                check_text(f'{h} / {e["title"]} bullet {i}', b, True)
            check_run(f'{h} / {e["title"]}', bullets)
        for i, b in enumerate(s.get("bullets", []), 1):
            check_text(f"{h} bullet {i}", b, True)
    return out


def _cp1252(c):
    try:
        c.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


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
        if "paragraph" in s:
            lines.append(s["paragraph"])
        for ln in s.get("lines", []):
            lines.append(f'{ln["label"]}: {ln["text"]}')
        for e in s.get("entries", []):
            head = " | ".join(x for x in (e["title"], e.get("org"), e.get("location"), e.get("dates")) if x)
            lines.append(head)
            lines += [f"- {b}" for b in e.get("bullets", [])]
        lines += [f"- {b}" for b in s.get("bullets", [])]
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
                    doc.add_paragraph(b, style="List Bullet")
            for b in s.get("bullets", []):
                doc.add_paragraph(b, style="List Bullet")

    now = datetime.now()
    cp = doc.core_properties
    cp.author = cp.last_modified_by = data["name"]
    cp.title = Path(path).stem
    cp.comments = cp.subject = cp.keywords = cp.category = ""
    cp.created = cp.modified = now
    cp.revision = 1
    doc.save(path)


# ---------- pdf ----------

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
            if "paragraph" in s:
                blocks.append([Paragraph(escape(s["paragraph"]), body)])
            for ln in s.get("lines", []):
                blocks.append([Paragraph(f'<font name="{bold}">{escape(ln["label"])}:</font> {escape(ln["text"])}', ParagraphStyle("line", parent=body, spaceAfter=1))])
            for k, e in enumerate(s.get("entries", [])):
                block = [Spacer(1, 0 if k == 0 else 5), TwoSided(e["title"], show_dates(e.get("dates")), bold, regular)]
                if e.get("org") or e.get("location"):
                    block.append(TwoSided(e.get("org", ""), e.get("location", ""), italic, italic))
                block += [Paragraph(escape(b), bullet_st, bulletText=BULLET) for b in e.get("bullets", [])]
                blocks.append(block)
            if s.get("bullets"):
                blocks.append([Paragraph(escape(b), bullet_st, bulletText=BULLET) for b in s["bullets"]])
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
    doc = SimpleDocTemplate(str(path), pagesize=(w, h), leftMargin=margin * inch, rightMargin=margin * inch,
                            topMargin=margin * inch, bottomMargin=margin * inch,
                            title=Path(path).stem, author=data["name"], creator=data["name"], subject="")
    doc.build(story)
    return doc.page


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("json_path")
    ap.add_argument("--out", required=True, help="folder to write into")
    ap.add_argument("--base", required=True, help='file name without extension, e.g. "First Last Resume"')
    args = ap.parse_args()

    warnings = []
    try:
        data = load(args.json_path)
        paper, size, margin = page_settings(data, warnings)
    except InputError as e:
        print(f"ERROR {e}")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    code = 0

    def target(ext):
        # Not with_suffix: a base like "John Q. Public Resume" would lose everything after "Q".
        return out / (args.base + ext)

    target(".txt").write_text(build_txt(data), encoding="utf-8")
    print(f"wrote: {target('.txt')}")

    try:
        build_docx(data, target(".docx"), paper, size, margin)
        print(f"wrote: {target('.docx')}")
    except ImportError:
        print("MISSING python-docx: run  pip install python-docx  and build again")
        code = 2

    try:
        pages = build_pdf(data, target(".pdf"), paper, size, margin)
        print(f"wrote: {target('.pdf')} ({pages} page{'s' if pages != 1 else ''})")
    except ImportError:
        print("MISSING reportlab: run  pip install reportlab  and build again")
        code = 2

    for w in warnings + lint(data):
        print(f"WARN {w}")
    return code


if __name__ == "__main__":
    sys.exit(main())
