# CVs, publications, techniques, licenses and federal resumes

How the resume's document mode, citations and techniques work (spec 4.8).
Every rule in writing_rules.md still applies.

## Document modes

Pass the mode to every build: `build_resume.py ... --mode industry|cv|usajobs`.

| Mode | When | Rules |
|---|---|---|
| industry (default) | companies | 2 pages at most, hard (`--strict` exits 6 above it; `--max-pages 1` for a one-page family). Sections: Summary, Experience, Techniques, Selected Publications (optional, at most `publications_max`, 3 by default), Education, Certifications. Education goes right after Summary only when writing_rules.md says so. |
| cv | the family's `mode = "cv"`; a posting that asks for a "CV" or "curriculum vitae"; an employer that is a university, institute or government lab; a postdoc title | No page cap (a warning above 8 pages). The build puts the sections in this order, where present: Education, Research Experience, Professional Experience, Publications (all), Presentations, Patents, Grants and Fellowships, Teaching and Mentoring, Techniques, Awards, Service, Memberships, References (only if the user supplies them). Other headings stay after the section before them. |
| usajobs | USAJOBS announcements; always Tier A, at most 1 a day | 2 pages, hard: a longer resume makes the applicant ineligible. Give each role month and year dates. A CV goes in only when the announcement asks for one as an "other document". Federal fields beyond the page cap (hours per week, grade, supervisor) are UNVERIFIED; where an announcement asks for hours, put them in the role's location, e.g. "Springfield, IL (40 hours per week)". |

A CV is saved as a CV: build it with `--base "<Name> CV"`, so the committee
receives `<Name> CV.pdf` (final.json names it for the upload). Industry and
usajobs builds keep `--base "<Name> Resume"`.

Headings the CV order knows by other names: Experience (Professional
Experience), Selected Publications (Publications), Skills (Techniques),
Honors (Awards), Grants or Fellowships, Teaching or Mentoring, Professional
Service, Professional Memberships.

## publications.md

One line per item, exactly as the user cites it:

`- P-001 | journal|preprint|conference|patent|thesis | <citation; *italics* allowed> [| ascii: <approved rendering>] [| doi:<doi>]`

- At setup, write each publication, presentation, patent and the thesis as
  the user gives it; they confirm the file. Never correct or reformat a citation
  yourself; ask.
- Characters the PDF font cannot show (Greek letters and most symbols
  outside Western European text) need an `ascii:` rendering. List them at
  setup, propose a rendering (beta-lactamase for the Greek beta), and add it
  only after the user approves it. The build prints the rendering for those
  citations and the exact citation for all others.
- `*...*` marks italics: species names, gene names where the user italicizes them,
  journal titles.

## Citing in resume.json

```json
{"heading": "Selected Publications", "citations": ["P-003", "P-007"], "bold_name": true}
```

- A citation section holds only `citations` (IDs, in the order to print).
  Use one section per heading: Publications, Presentations, Patents.
- Build with `--publications "<folder>/publications.md"`. The build inserts
  each citation's exact text, renders `*italics*` as italics in the .docx and
  .pdf (the .txt drops the asterisks), and with `"bold_name": true` bolds the
  user's name where it appears as an author ("Avery JQ", "Avery, J. Q.",
  "J. Q. Avery", "Jordan Q. Avery").
- Industry resumes: at most `publications_max` citations, first-author or
  closest to the posting. CVs: all of them.
- Citations skip the bullet lint (verb start, length, banned words,
  pronouns) and FACT. Instead CITE checks that every printed citation equals
  its publications.md line or its approved `ascii:` rendering, ignoring only
  `*` markup, spacing and dash differences, and that it reads back from the
  PDF. A Publications, Presentations or Patents section typed out as bullets
  is checked the same way, and any difference fails (`CITE mismatch P-003`);
  so never type one out, cite by ID. A section of typed citations holds
  nothing else: a paragraph or a line beside them stops the build with
  `ERROR` (put it in its own section, where FACT checks it).
- An ID missing from publications.md stops the build with `ERROR`.

## techniques.md

`- <technique> | aliases: <a>; <b> | level: hands-on|familiar | years_ok: yes|no | evidence: L-0012, L-0031`

- Review it with the user at setup, built from their resume and library.
- It drives the Techniques section (hands-on only; a level qualifier where
  the user partly meets a requirement), the fit check, KEYWORD terms, alias
  swaps and a Tier B letter's `{focus}`. Aliases must name the same thing.
  The TECHNIQUE check fails a Techniques item that is `familiar` here unless
  it says so, "(familiar)".
- Life-science variants title the Skills section "Techniques".
- `years_ok: yes` lets `answers.py years --skill <X>` answer a form's "how
  many years of X" from the dated roles of the evidence entries (the
  writing_rules counting rule, rounded down). Mark `no` where the evidence
  does not show the whole span; the form question then goes to the user.
  A question that adds anything to X ("in a GMP-regulated lab", "and flow
  cytometry") also goes to the user: the span says nothing about the rest.

## Licenses

- profile.md `Licenses held:` lists each license or certificate with its
  year. Postings that require a license the user does not hold (the California CLS
  license, the Public Health Microbiologist certificate) never reach the
  queue; "preferred" ones are flagged.
- A held license prints under Certifications with its year, under the
  writing_rules lapsed-certification rule, named exactly as master_resume.md
  gives it (FACT checks the name). One the library lists as "Not held" fails
  the build wherever it appears in Certifications or Techniques.
