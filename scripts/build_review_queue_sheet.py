from collections import Counter
from datetime import date

import gspread

from radar.dates import parse_date
from radar.records import get_value, rows_to_dicts, stable_id
from radar.sheets import open_sheet, read_records, replace_worksheet
from scripts import build_priority_signals_sheet


PRIORITIES_WORKSHEET_NAME = "priority_signals"
OUTPUT_WORKSHEET_NAME = "review_queue"

REVIEW_QUEUE_HEADER = [
    "review_id",
    "status",
    "review_today",
    "first_seen",
    "last_seen",
    "closed_date",
    "review_status",
    "review_note",
    "priority_level",
    "ticker",
    "entity_name",
    "last_date",
    "opportunity_type",
    "sources",
    "evidence_count",
    "summary",
    "priority_reason",
    "cluster_ids",
    "signal_ids",
]

PRESERVED_COLUMNS = ["review_status", "review_note"]
DEFAULT_REVIEW_STATUS = "NEW"
STATUS_NEW = "NEW"
STATUS_ACTIVE = "ACTIVE"
STATUS_CLOSED = "CLOSED"
OPEN_STATUSES = {STATUS_NEW, STATUS_ACTIVE}
STATUS_ORDER = {
    STATUS_NEW: 0,
    STATUS_ACTIVE: 1,
    STATUS_CLOSED: 2,
}


def make_review_id(priority):
    raw_key = get_value(priority, "priority_id")
    return stable_id("review", [raw_key])


def build_summary(priority):
    ticker = get_value(priority, "ticker")
    entity_name = get_value(priority, "entity_name")
    opportunity_type = get_value(priority, "opportunity_type")
    priority_level = get_value(priority, "priority_level")

    subject = ticker or entity_name or "Opportunity"
    return f"{priority_level} {opportunity_type}: {subject}"


def existing_review_state(sheet):
    try:
        worksheet = sheet.worksheet(OUTPUT_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return {}

    state = {}
    for row in rows_to_dicts(worksheet.get_all_values()):
        review_id = get_value(row, "review_id")
        if not review_id:
            continue
        state[review_id] = dict(row)
    return state


def today_iso():
    return date.today().isoformat()


def lifecycle_status(saved_state, run_date):
    previous_status = get_value(saved_state, "status")
    first_seen = get_value(saved_state, "first_seen")
    last_seen = get_value(saved_state, "last_seen")

    if not saved_state:
        return STATUS_NEW

    if previous_status == STATUS_CLOSED:
        return STATUS_ACTIVE

    if previous_status == STATUS_NEW and (first_seen == run_date or last_seen == run_date):
        return STATUS_NEW

    if first_seen and first_seen == run_date:
        return STATUS_NEW

    return STATUS_ACTIVE


def should_review_today(row):
    status = get_value(row, "status")
    priority_level = get_value(row, "priority_level")
    if status == STATUS_CLOSED:
        return "NO"
    if status == STATUS_NEW or priority_level == "HIGH":
        return "YES"
    return "NO"


def build_open_review_row(priority, saved_state, run_date):
    review_id = make_review_id(priority)
    status = lifecycle_status(saved_state, run_date)
    first_seen = get_value(saved_state, "first_seen") or run_date

    row = {
        "review_id": review_id,
        "status": status,
        "review_today": "",
        "first_seen": first_seen,
        "last_seen": run_date,
        "closed_date": "",
        "review_status": get_value(saved_state, "review_status") or DEFAULT_REVIEW_STATUS,
        "review_note": get_value(saved_state, "review_note"),
        "priority_level": get_value(priority, "priority_level"),
        "ticker": get_value(priority, "ticker"),
        "entity_name": get_value(priority, "entity_name"),
        "last_date": get_value(priority, "last_date"),
        "opportunity_type": get_value(priority, "opportunity_type"),
        "sources": get_value(priority, "sources"),
        "evidence_count": get_value(priority, "evidence_count"),
        "summary": build_summary(priority),
        "priority_reason": get_value(priority, "priority_reason"),
        "cluster_ids": get_value(priority, "cluster_ids"),
        "signal_ids": get_value(priority, "signal_ids"),
    }
    row["review_today"] = should_review_today(row)
    return row


def build_closed_review_row(saved_state, run_date):
    row = {column: get_value(saved_state, column) for column in REVIEW_QUEUE_HEADER}
    row["status"] = STATUS_CLOSED
    row["review_today"] = "NO"
    row["first_seen"] = get_value(saved_state, "first_seen") or run_date
    row["last_seen"] = get_value(saved_state, "last_seen") or row["first_seen"]
    row["closed_date"] = get_value(saved_state, "closed_date") or run_date
    row["review_status"] = get_value(saved_state, "review_status") or DEFAULT_REVIEW_STATUS
    return row


def build_review_queue(priorities, existing_state=None, run_date=None):
    existing_state = existing_state or {}
    run_date = run_date or today_iso()
    rows = []
    current_review_ids = set()

    for priority in priorities:
        review_id = make_review_id(priority)
        saved_state = existing_state.get(review_id, {})
        current_review_ids.add(review_id)
        rows.append(build_open_review_row(priority, saved_state, run_date))

    for review_id, saved_state in sorted(existing_state.items()):
        if review_id in current_review_ids:
            continue
        if get_value(saved_state, "status") == STATUS_CLOSED:
            rows.append(build_closed_review_row(saved_state, run_date))
            continue
        rows.append(build_closed_review_row(saved_state, run_date))

    return sorted(
        rows,
        key=lambda row: (
            STATUS_ORDER.get(row["status"], 99),
            row["review_today"] != "YES",
            build_priority_signals_sheet.PRIORITY_ORDER.get(row["priority_level"], 99),
            row["last_date"],
            row["ticker"],
            row["entity_name"],
            row["review_id"],
        ),
    )


def write_review_queue(sheet, rows):
    replace_worksheet(sheet, OUTPUT_WORKSHEET_NAME, REVIEW_QUEUE_HEADER, rows)


def validate_review_queue(rows, priorities):
    priority_review_ids = {
        make_review_id(priority)
        for priority in priorities
    }
    review_ids = [row["review_id"] for row in rows]
    if len(review_ids) != len(set(review_ids)):
        raise ValueError("Duplicate review_id values found")

    for row in rows:
        status = get_value(row, "status")
        if status not in STATUS_ORDER:
            raise ValueError(f"Review row has invalid status: {row}")

        if status in OPEN_STATUSES and row["review_id"] not in priority_review_ids:
            raise ValueError(f"Review row does not map to a priority: {row}")

        if status == STATUS_CLOSED and row["review_id"] in priority_review_ids:
            raise ValueError(f"Closed review row still maps to an active priority: {row}")

        if get_value(row, "review_today") not in {"YES", "NO"}:
            raise ValueError(f"Review row has invalid review_today value: {row}")

        for column in ["first_seen", "last_seen"]:
            if parse_date(get_value(row, column)) is None:
                raise ValueError(f"Review row has invalid {column}: {row}")

        if status == STATUS_CLOSED and parse_date(get_value(row, "closed_date")) is None:
            raise ValueError(f"Closed review row has invalid closed_date: {row}")

        if not get_value(row, "review_status"):
            raise ValueError(f"Review row has no review_status: {row}")

        if not get_value(row, "summary") or not get_value(row, "priority_reason"):
            raise ValueError(f"Review row has missing explanation: {row}")

        last_date = parse_date(get_value(row, "last_date"))
        if last_date is None:
            raise ValueError(f"Review row has invalid last_date: {row}")

    return True


def print_summary(rows):
    status_counts = Counter(row["status"] for row in rows)
    review_status_counts = Counter(row["review_status"] for row in rows)
    review_today_count = sum(1 for row in rows if row["review_today"] == "YES")

    print(f"Worksheet rebuilt: {OUTPUT_WORKSHEET_NAME}")
    print(f"Review rows generated: {len(rows)}")
    print(f"Rows for review today: {review_today_count}")
    print("Count by status:")
    for status, count in sorted(status_counts.items()):
        print(f"- {status}: {count}")
    print("Count by review_status:")
    for status, count in sorted(review_status_counts.items()):
        print(f"- {status}: {count}")

    print("Example 5 review rows:")
    for row in rows[:5]:
        print({column: row[column] for column in REVIEW_QUEUE_HEADER})


def main():
    sheet = open_sheet()
    priorities = read_records(sheet, PRIORITIES_WORKSHEET_NAME)
    rows = build_review_queue(priorities, existing_review_state(sheet))
    validate_review_queue(rows, priorities)
    write_review_queue(sheet, rows)
    print_summary(rows)


if __name__ == "__main__":
    main()
