# Role-family variants and Tier B

Tier B jobs (every queued job except the day's best 3-5) get no writing at
all. The runner tunes an approved family variant for each posting with
`variants.py`, in code, and builds it with every check failing closed. This
file is how you build, approve and maintain those variants.

Run every command as `python "<skill>/scripts/variants.py" <command> --root "<folder>" ...`.

## The files

`variants/<family>/current.txt` names the approved version (`v1`).
`variants/<family>/v<N>/` holds:

| File | Holds |
|---|---|
| `resume.json` | The family resume (writing_rules.md format). Every bullet has `ev` library IDs; `"keep": true` on bullets tuning must never drop. |
| `pool.json` | What tuning may swap in (below). |
| `letter.json` | The family cover letter, with `{company}`, `{role}`, `{focus}` and `{date}` slots. |
| `review.md` | The Verify before sending list, the pool on one page, the audit verdict. |
| `approved.json` | Written by `approve`: the sha256 of each file above, and of every library entry they cite. |
| `questions.json` | Open variant-gap questions (fit_and_questions.md). |
| built files | The base build the user reads before approving. |

`pool.json`:

```json
{
  "summaries": ["Second summary option.", "Third summary option."],
  "bullets": {"Scientist II, QC Microbiology | Northwind Biologics": [
    {"text": "Performed bioburden and sterility testing under GMP for 2 commercial products.", "ev": ["L-0008"]}]},
  "skills": [{"label": "Molecular", "items": ["16S rRNA sequencing"]}],
  "aliases": [{"variant": "real-time PCR", "posting": ["qPCR", "quantitative PCR"]}],
  "publications": ["P-007", "P-011"]
}
```

- `bullets` are keyed `"<title> | <org>"` exactly as in resume.json. When two
  roles share a title and employer (two stints, two postdocs at one
  university), key each `"<title> | <org> | <dates>"`; `approve` refuses the
  shorter key there, and a bullet never moves to the other stint.
- `aliases` name the same thing in other words, never a related skill
  (writing_rules.md: the posting's terminology only where it names the same
  thing I did). An alias never lists its own term.
- `publications` are extra citation IDs tuning may choose in industry mode.
- `letter.json` uses the cover letter format with slots. `{focus}` becomes
  the hands-on technique from techniques.md (name or alias) that the posting
  uses most, copied from posting.md; never a knockout name or a family
  term. Write the letter so any of the user's hands-on techniques reads
  correctly there. A posting that names none gets no letter: the job parks.

## Building a variant (setup sitting 2, about 15-20 minutes of the user's time per family)

1. `variants.py new --family <id>` scaffolds `v1` from master_resume.md
   (with `--copy`, a new version starts from the newest one). Family ids are
   lowercase ASCII from search_profile.toml.
2. Read 3-5 representative postings for the family from the first runner
   pull. Run fit_and_questions.md against the union of their must-haves (at
   most 6 questions, "skip is fine").
3. Write resume.json under writing_rules.md, with `ev` on every bullet; then
   pool.json, letter.json and review.md. Remove every TODO.
4. Audit with job-auditor in a fresh context (send / fix / rework); fix, and
   build the base with `--strict` and the family's `--mode`.
5. The user reads one PDF and one page of pool per family and replies
   `approve <family>`. Run `variants.py approve --family <id>`
   (`--max-pages 1` for a one-page family, `--publications-max N`).

`approve` refuses a variant with TODO text, a bad pool or letter, an
EVIDENCE, FACT or TECHNIQUE problem anywhere in resume.json or pool.json, a
banned word, or a letter that, filled with a sample posting, carries a
number no source holds. It writes approved.json and current.txt:

```json
{"family": "qc_micro", "version": 1, "approved_on": "2026-11-13", "library_rev": "<sha256 of the library>",
 "sha256": {"resume.json": "...", "pool.json": "...", "letter.json": "..."},
 "entries": {"L-0004": "<sha256 of its heading and text>", "...": "..."},
 "mode": "industry", "max_pages": 2, "publications_max": 3}
```

Until a family is approved (or while its approval no longer holds) at most
`tier_a_per_day` of its jobs are queued as Tier A; the rest are never cut
silently: they fill the list's free places last as `prep_failed`, and
`queue.json` `counts.variant_wait` holds how many jobs each such family has
waiting, for the card's re-approval line (`more` says the same). The daily
log prints `WAIT_VARIANT <family> <n> jobs`.

## Approval holds only for the exact bytes and the cited entries

- `variants.py check` prints `APPROVED <family> v<N>`, or
  `UNAPPROVED <family> v<N>: <file> changed since approval` when any approved
  file changed by even one character (line endings included), or
  `UNAPPROVED <family> v<N>: L-0006 changed since approval` when a library
  entry the variant cites changed its text or role heading (a corrected
  "Replaces:" entry keeps its ID, so the old wording would otherwise ship).
- An unapproved current version falls back to the newest older approved one
  (`FALLBACK <family> v<N>`). With none, tuning refuses and the family's
  items become `prep_failed`; the card names the family and how many jobs
  wait ("The <label> base resume changed, so 12 jobs wait for your ok
  again (type /job variants).") and `next` never hands them over. Families
  with no variant at all share one card line ("180 more jobs need a base
  resume you don't have yet (most: Clinical research 66, Research associate
  50). Say /job variants to make one.").
- The same day too: a job whose Tier B file was built from a version
  (`tune_log.json` names it) that is no longer approved is held at
  handover. `next` prints `HOLD #4: its base resume cites something you
  corrected today. Say skip, or make the new version with /job variants
  first.` in place of `OPEN` and `UPLOAD`, held jobs come after every other
  job, and the card counts them in one line. On the user's ok to the same
  files (`APPROVED <family> v<N> again`) they are ready at once; a new
  version builds them at the next daily run. A Tier A build whose
  `final.json` cites the corrected entry no longer counts either: that job
  goes back to its Tier B file (held as above when that one is affected
  too) until its Tier A resume is built again.
- `approve` never re-approves a changed file: put the change in a new
  version (`new --copy`), and approve that. Earlier versions stay.
- After a cited entry changed, `check` prints each `CHANGED` entry with its
  new text and every variant bullet citing it, then a `NEXT` line. Read each
  bullet against the new text. If one now says more than its entry (the
  library says "supported", the bullet "Led"), make `v<N+1>` with `new
  --copy`, fix the bullet, audit, and ask for approval. If every bullet still
  holds, ask the user for the one-line re-approval; on their `approve
  <family>`, `approve` re-approves the same files (`APPROVED <family> v<N>
  again`). Either way, tell the user in one line which family waits.
- After merging a "Replaces:" entry (writing_rules.md, Block C), run
  `variants.py check` at once, so a family that cites it is caught before
  the next daily run and its jobs built today are held, not handed over.
- An approval written before per-entry hashes holds while
  `achievement_library.md` is byte-identical to `library_rev`; any library
  edit then needs the re-approval above.

## What tuning may do (`tune`; spec 4.3), and nothing else

1. **Summary:** the option (the variant's own, then the pool's) with the most
   posting-term overlap.
2. **Bullets:** stable-sorted within each role by overlap; a pool bullet
   replaces a lower-scoring variant bullet, so each role keeps the variant's
   bullet count; non-`keep` bullets of the oldest roles are dropped to fit
   the page cap.
3. **Skills:** items and lines reordered so posting-matched ones come first;
   a pool item is added only when the posting names it.
4. **Terms:** a variant term becomes the posting's form only through the
   alias table, and only when the posting uses its form and not ours.
   Citations are never changed.
5. **Publications** (industry mode): up to `publications_max` citations, by
   overlap, from the variant's and the pool's IDs.

Never changed: names, titles, employers, dates, education, contact, numbers
(digits or words), or any sentence other than an alias swap. Terms come
from `terms.json`: family terms, techniques.md names and aliases, and
knockout names that posting.md uses (for ranking only; never `{focus}`).

`tune` writes `resume_B.json` and `tune_log.json` (the choices, the swaps,
and a one-line note for today.md such as "summary 2; bullets 4 up; qPCR
(alias of real-time PCR)").

## The Tier B pipeline (`tier-b --item <job folder>`, run by the runner)

1. `terms.json` from posting.md.
2. `tune`.
3. `diff`: `VARIANT ok (<n> units)`, or one `VARIANT-FAIL <unit>` line per
   unit that is not an approved unit or an approved unit after the logged
   alias swaps (exit 6). Identity fields, `ev` and `keep` must match too.
4. `build_resume.py --strict` with the approved mode and page cap (FACT,
   EVIDENCE, TECHNIQUE, CITE, PARSE, the page cap and the banned words; a
   techniques.md level change after approval is caught here).
5. On any failure: `FALLBACK base`, the untouched approved resume.json built
   the same way. If that fails too: `PREP_FAILED`, nothing is staged, and the
   item parks (exit 6).
6. When the form requires a cover letter, `cover_B` from letter.json with
   the letter check; a failure parks the job.

The runner writes only its own files in the job folder (`.runner_files`
lists them), never in a folder holding `.claimed`.

## Rework

Propose a new version at most weekly, when the library gains 5 or more
entries relevant to the family, or after 100 applications in 14 days with no
screen. Build it as `v<N+1>` with `new --copy`, audit, and ask for approval;
`v<N>` stays in use until then.
