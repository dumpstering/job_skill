# tools

Maintainer scripts for this repository. Nothing here is packaged: `build_zip.py`
packages `job/`, the plugin agents and `runner/` only, so `tools/` never reaches
the skill zip or the runner zip.

## seed_boards.py

Grows a preset's `watchlist.csv` from the public **StoreSignal ATS job boards
directory** (github.com/storesignalhq/ats-job-boards, `boards.csv`: about
15,600 company boards on Greenhouse, Ashby, Workable, Recruitee, SmartRecruiters
and Lever, with `open_jobs` as a size hint).

Credit: "StoreSignal ATS job boards directory", CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/), link
https://github.com/storesignalhq/ats-job-boards. The file is downloaded to a
cache folder outside this repository and never copied into it; the rows it
adds carry `ref` = `storesignal-2026-11`.

```
python tools\seed_boards.py --preset lifesci_bayarea            # dry run: reads boards, prints the summary
python tools\seed_boards.py --preset lifesci_bayarea --append   # also adds the kept rows to the watch list
python tools\seed_boards.py --preset remote_tech --append --max-minutes 90
```

| Option | What |
|---|---|
| `--preset NAME` | A folder under `job/templates/presets/` with rules in `tools/seed/NAME.toml` |
| `--append` | Add the kept rows to `watchlist.csv` (created with the header when missing) |
| `--cache DIR` | Cache folder (default `%LOCALAPPDATA%\JobSkillTools`): `boards.csv`, `probes.jsonl`, `new_rows_<preset>.csv` |
| `--refresh` | Download `boards.csv` again |
| `--max-minutes N` | Stop starting list calls after N minutes (default 90); a rerun continues |
| `--max-age-days N` | Reuse a cached list answer younger than N days (default 7); a board that errored is read again |
| `--limit N` | Read at most N boards per ATS (a trial run) |
| `--ats a,b` | Read only these ATSs. Reading is CPU-bound (one core per process), so for a long run start one process per ATS (`--ats greenhouse`, `--ats ashby`, ...), then judge with `--no-probe --append`. Never two processes on one ATS: that host would see more than one request a second |
| `--no-probe` | Judge the cached answers only, no network (after changing a rules file) |
| `--keep-name-dupes` | Do not skip an employer name already on the watch list |

### What a run does

1. Downloads `boards.csv` once, through `netfetch` (robots.txt obeyed).
2. Filters it by the rules file, then drops every `(ats, slug)` already in the
   preset's `watchlist.csv`, and every employer name already there.
3. Reads each candidate's job list through `boards.read_board` with no detail
   calls: the same call as `python job\scripts\boards.py fetch --board <ats>:<slug> --list-only`.
   Everything goes through `netfetch` (allowlist, robots.txt, one request a
   second per host); one thread per ATS, so the hosts run side by side, biggest
   boards first. Each answer (status, and title, location and remote flag of each
   posting) is appended to `probes.jsonl`, so Ctrl+C or the time limit loses
   nothing and a rerun reads only what is missing.
4. Keeps a board when its list answered and at least `min_relevant` postings
   are relevant, then prints candidates, answered and kept per ATS.
5. Writes the new rows to `new_rows_<preset>.csv` in the cache folder, and with
   `--append` to the preset's `watchlist.csv`, in its exact column format:
   `ats` and `board` from the directory, `careers_url` the board's own page,
   `city` the Bay Area place most of the relevant postings name (blank when none
   is specific, and always blank for `remote_tech`), `priority` 3, `status`
   `active`, `added_by` `research` (the preset test requires it), `ref`
   `storesignal-2026-11`, and a note with the day and the counts.

Only Greenhouse, Lever, Ashby and Workable are seeded. Recruitee boards are not:
the poll skips them until the runner selftest passes. SmartRecruiters is lead-only
and has no list call. A Greenhouse board whose list is over 20 MB answers
"response is not JSON" in the reader too and is not kept.

### Rules files (`tools/seed/<preset>.toml`)

| Key | Meaning |
|---|---|
| `ats` | Allowed ATSs |
| `min_open_jobs` | Skip boards the directory lists with fewer jobs |
| `name_include`, `name_exclude` | Regexes (case-insensitive) on the company name; an empty include list takes every name |
| `min_relevant` | Relevant postings a board needs |
| `cap` | Most rows the source may have in the list (the boards with the most relevant postings win); 0 is no cap |
| `priority`, `segment`, `[[segment_rule]]` | Column values; a segment rule is a name regex and a segment |
| `[relevant] kind = "places"` | Relevant = located in a `[home] places` entry of the preset's `families.toml`, judged by `gates.home_place_verdict` (so "Dublin, Ireland" and "Newark, NJ" are out), and titled like one of its families (`gates.family_score`, after the `[gates]` title excludes; paused families count). `strong_name` regexes: a board whose name has one of those words (bio, pharma, genomics, ...) is also kept on any posting in a home place, since such an employer will post family titles later (the row's note says so) |
| `[relevant] kind = "remote_us"` | Relevant = remote in the US (the location says Remote with a US word or state or no country, or the board flags it remote and names the US) and the title has a word from `title_any` (case-insensitive) or `title_caps` (capitals only: `IT`, `AI`, `TPM`) |

A name regex only decides which boards are worth a list call; the location rule
decides what is kept.
