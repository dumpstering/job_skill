# Plan and usage: the measurement and the decision rule

Usage limits are shared across the app's surfaces and not published, and
browser work costs more by an amount nobody publishes. So the plan is chosen
from the user's own numbers, measured on the first two days at about 4 extra
actions. Facts as of setup: Pro is $20 a month; Max is $100 for 5 times Pro;
Ultra is $200 for 10 times Pro weekly, with no 5-hour limit for verified
accounts. Start on Max for two weeks, or Pro with usage credits on, then
decide.

## The readings (days 1 and 2 only; never on a short day)

1. Before #1, after #3 and after #5, ask in one line: "What does Settings >
   Usage show now? Session %, weekly %." The user replies with two numbers.
2. Record each reading, with the minutes the session has run so far:
   `report.py usage --root "<folder>" --add --after-n <n> --session <s>
   --weekly <w> --minutes <m> [--plan "Max"]` (`--after-n 0` before #1;
   `--plan` once, the plan the user is on). It prints `ADDED <date> after
   #<n>: session <s>% weekly <w>%`, then the projection below.
3. Nothing else changes: the readings never slow the fill loop.

## /job usage

`report.py usage --root "<folder>"` prints, once a day has two readings:
- `PER_APP session <s>% weekly <w>%`: the cost of one application.
- `MORNING weekly <m>% a day (job-morning and job-inbox)`: the scheduled
  tasks' cost, from the gap between one day's last reading and the next
  day's first.
- `DAY <n> applications: session <s>% (two sittings <s/2>% each), weekly <w>%
  a day, <7w>% a week`, for the day's target plus the usual skips (22 for
  20).
- `PLAN <suggestion>`, by the rule below.
With fewer readings it prints `NONE need two readings on one day ...`; say
when the next reading comes.

Show the PLAN line and the DAY line in plain words. Never call a number
over or under anything.

## The decision rule

- The day fits under 70% of one 5-hour window across two sittings, and 7
  days of it fit the weekly bar: stay on the plan, or step down to Pro with
  credits if a day fits in one fifth.
- Only the 5-hour window binds: two sittings about 5 hours apart.
- The weekly bar binds: Ultra, or a lower daily number (`[daily] target`,
  settings.md).
- Measure again after any change of browser or model.

Apply tasks run at the lowest effort level the app offers, with connectors
other than Gmail off, and a new task every 10 applications; say so once if
the readings look high.
