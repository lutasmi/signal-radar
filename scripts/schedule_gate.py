"""Decide whether this Congress watch run must also do the daily/weekly tasks.

GitHub Actions delays or silently drops scheduled runs, so matching the exact
cron string (``github.event.schedule``) missed both the Monday report and the
daily heartbeat. Instead, any scheduled run decides from state:

* heartbeat: from 06:00 UTC, if no heartbeat was sent today
  (data/reference/last_heartbeat.txt, kept in the actions cache).
* weekly: if reports/weekly_status.md (committed to git) is older than this
  week's Monday 06:00 UTC.

Manual runs only do what their inputs ask for. Writes ``heartbeat=`` and
``weekly=`` to $GITHUB_OUTPUT (or stdout).
"""

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_FILE = PROJECT_ROOT / "reports" / "weekly_status.md"
HEARTBEAT_FILE = PROJECT_ROOT / "data" / "reference" / "last_heartbeat.txt"
DAILY_HOUR_UTC = 6


def report_date(text):
    match = re.search(r"informe semanal (\d{4}-\d{2}-\d{2})", text or "")
    return date.fromisoformat(match.group(1)) if match else None


def decide(now, event, last_heartbeat, last_report, want_heartbeat=False, want_weekly=False):
    """Return (heartbeat, weekly) booleans."""
    if event != "schedule":
        return want_heartbeat, want_weekly
    today = now.date()
    heartbeat = now.hour >= DAILY_HOUR_UTC and last_heartbeat != today
    monday = today - timedelta(days=today.weekday())
    week_started = today > monday or now.hour >= DAILY_HOUR_UTC
    if not week_started:  # Monday before 06:00: the previous week's report is the current one
        monday -= timedelta(days=7)
    weekly = last_report is None or last_report < monday
    return heartbeat, weekly


def read_text(path):
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def mark_heartbeat_sent(today=None):
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.write_text((today or datetime.now(timezone.utc).date()).isoformat() + "\n", encoding="utf-8")


def main():
    try:
        last_heartbeat = date.fromisoformat(read_text(HEARTBEAT_FILE))
    except ValueError:
        last_heartbeat = None
    heartbeat, weekly = decide(
        datetime.now(timezone.utc),
        os.getenv("GITHUB_EVENT_NAME", ""),
        last_heartbeat,
        report_date(read_text(REPORT_FILE)),
        os.getenv("INPUT_HEARTBEAT") == "true",
        os.getenv("INPUT_WEEKLY") == "true",
    )
    lines = f"heartbeat={str(heartbeat).lower()}\nweekly={str(weekly).lower()}\n"
    print(lines, end="")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(lines)


def validate_logic():
    utc = timezone.utc
    mon_0729 = datetime(2026, 10, 26, 7, 29, tzinfo=utc)
    cases = [
        # (now, event, last_heartbeat, last_report, want_hb, want_weekly) -> expected
        ((mon_0729, "schedule", date(2026, 10, 25), date(2026, 10, 22)), (True, True)),
        ((mon_0729, "schedule", date(2026, 10, 26), date(2026, 10, 26)), (False, False)),
        ((datetime(2026, 10, 26, 3, 0, tzinfo=utc), "schedule", date(2026, 10, 25), date(2026, 10, 19)), (False, False)),
        ((datetime(2026, 10, 26, 3, 0, tzinfo=utc), "schedule", None, date(2026, 10, 12)), (False, True)),
        ((datetime(2026, 10, 28, 13, 0, tzinfo=utc), "schedule", None, None), (True, True)),
        ((datetime(2026, 10, 28, 13, 0, tzinfo=utc), "schedule", date(2026, 10, 28), date(2026, 10, 27)), (False, False)),
        ((mon_0729, "workflow_dispatch", None, None), (False, False)),
    ]
    for args, expected in cases:
        got = decide(*args)
        if got != expected:
            raise ValueError(f"schedule_gate {args}: expected {expected}, got {got}")
    if decide(mon_0729, "workflow_dispatch", None, None, True, True) != (True, True):
        raise ValueError("schedule_gate: manual inputs must be honoured")
    if report_date("# Congress tracker · informe semanal 2026-10-22\n") != date(2026, 10, 22):
        raise ValueError("schedule_gate: report date not parsed")
    if report_date("") is not None:
        raise ValueError("schedule_gate: empty report must have no date")
    return True


if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate_logic()
        print("OK schedule gate logic")
    else:
        main()
