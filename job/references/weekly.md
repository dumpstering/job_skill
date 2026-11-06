# Weekly summary

For `/job week`, and for job-morning on the `[schedule] weekly_day` (Monday).
Write it once a week: after it is written, create the marker
`claude\day_<date>\weekly.done`.

## Steps

1. Run `report.py weekly --root "<folder>"`. It writes
   `reports\week_<date>.md` for the 7 days before today and prints the card's
   lines:
   ```
   WROTE reports/week_2026-11-16.md
   CARD Last week: 21 applications sent, 3 people contacted.
   CARD 1 conversation started.
   CARD The weekly summary is in reports/week_2026-11-16.md.
   ```
   Show the `CARD` lines word for word (at most 3). Point to the file; do not
   paste it.
2. The file holds, with only nonzero counts:
   - applications sent, by tier and family, and people contacted;
   - conversations started (Screen, Interview, Assessment, Offer, with dates);
   - employers that said no, as one plain list with no commentary. This is
     the only place rejections are listed; never repeat them in the card or
     in chat;
   - what is working, as "x of n" per family, variant, lane, account cost and
     freshness at apply, shrunk toward about 3%; under 30 applications it
     says it is too early to tell;
   - discards by filter, board changes (listed once), widen steps in use,
     family weight moves;
   - at most one question.
3. If the file has a `Question:` line, ask it in the card's question batch,
   word for word, phrased as an experiment. A yes changes one thing (an
   adjacent family, a variant revision, a wider ring, a looser filter, a lane
   off), backup first.
4. Other offers in the file, answered only when he raises them:
   - "mark no response": rows applied 30 or more days ago with no reply get
     `tracker.py update <ID> status="No response"`;
   - "archive", offered when the tracker passes 5000 rows:
     `tracker.py archive --before <date>` (backup first; nothing deleted).
5. In a California certification week, also offer the work-search export
   (worksearch.md).

## Wording

- No comparisons with earlier weeks, no totals of days, never a count of
  days away.
- `report.py check-wording` rejects: streak, missed, behind, overdue,
  target, goal, only, short of, failed to, should have, catch up, falling,
  lagging. Run it on anything you write for the card or a closing line:
  `report.py check-wording --text "<line>"` prints `WORDING ok` or a
  `BANNED` line to rewrite.

## Family weights (the runner)

`report.py adapt --apply` runs on the runner each week. It moves each
family's weight toward its rank by talk rate, by at most 2.5 a week, within
-10 to +10, and only once the family has 30 applications. `min_share`
floors stay. You never edit `state\weights.json`; `report.py adapt` without
`--apply` shows what it would do.

## Funnel and usage

- `report.py funnel --root "<folder>" --days 14` totals `reports\funnel.csv`
  (seen, queued, started, submitted, confirmed, lane resolution, median fill
  minutes) for the week-1 calibration.
- `report.py usage --root "<folder>" --add --after-n <n> --session <pct>
  --weekly <pct> [--minutes <m>] [--plan <plan>]` records a Settings > Usage
  reading; `report.py usage --root "<folder>"` prints `PER_APP`, the day's
  projection and one `PLAN` suggestion (usage.md).
