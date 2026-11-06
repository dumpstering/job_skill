"""Run every tests/test_*.py, one after another, each in its own process.

Usage: python tests/run_all.py [name ...]

Prints one `PASS <script>` line per script that exits 0 and prints PASS,
or `FAIL <script> (exit <n>)` with the last lines of its output, then
`ALL PASS` (exit 0) or `FAILED <n> of <m>` (exit 1). Names (for example
`test_tracker` or `tracker`) limit the run to those scripts. Every script
runs with this interpreter, from the repository root, with JOB_SKILL_LIVE
unset unless it was set for this run.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
TIMEOUT = 1200  # seconds per script; test_tracker and test_runner take about a minute or two


def scripts(names):
    found = sorted(TESTS.glob("test_*.py"))
    if not names:
        return found
    want = {n if n.startswith("test_") else "test_" + n for n in (Path(x).stem for x in names)}
    return [p for p in found if p.stem in want]


def passed(code, out):
    return code == 0 and any(line.strip().startswith("PASS") for line in out.splitlines())


def run(path):
    start = time.time()
    try:
        r = subprocess.run([sys.executable, str(path)], cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT, env=dict(os.environ))
        code, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        code, out = -1, f"timed out after {TIMEOUT} s\n{e.stdout or ''}"
    return code, out, time.time() - start


def main(argv):
    todo = scripts(argv)
    if not todo:
        print("ERROR no test scripts matched")
        return 1
    failed = 0
    for path in todo:
        code, out, secs = run(path)
        name = path.relative_to(ROOT).as_posix()
        if passed(code, out):
            print(f"PASS {name} ({secs:.0f} s)", flush=True)
            continue
        failed += 1
        print(f"FAIL {name} (exit {code})", flush=True)
        for line in out.strip().splitlines()[-15:]:
            print(f"    {line}", flush=True)
    if failed:
        print(f"FAILED {failed} of {len(todo)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
