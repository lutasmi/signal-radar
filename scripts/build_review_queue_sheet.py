from collections import Counter

import gspread

from radar.dates import parse_date
from radar.records import get_value, rows_to_dicts, stable_id
from radar.sheets import open_sheet, read_records, replace_worksheet
from scripts import build_priority_signals_sheet


PRIORITIES_WORKSHEET_NAME = "priority_signals"
OUTPUT_WORKSHEET_NAME = "review_queue"

REVIEW_QUEUE_HEADER = [
    "review_id",
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
        state[review_id] = {
            column: get_value(row, column)
            for column in PRESERVED_COLUMNS
        }
    return state


def build_review_queue(priorities, existing_state=None):
    existing_state = existing_state or {}
    rows = []

    for priority in priorities:
        review_id = make_review_id(priority)
        saved_state = existing_state.get(review_id, {})
        review_status = saved_state.get("review_status") or DEFAULT_REVIEW_STATUS
        review_note = saved_state.get("review_note", "")

        rows.append(
            {
                "review_id": review_id,
                "review_status": review_status,
                "review_note": review_note,
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
        )

    return sorted(
        rows,
        key=lambda row: (
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
        if row["review_id"] not in priority_review_ids:
            raise ValueError(f"Review row does not map to a priority: {row}")

        if not get_value(row, "review_status"):
            raise ValueError(f"Review row has no status: {row}")

        if not get_value(row, "summary") or not get_value(row, "priority_reason"):
            raise ValueError(f"Review row has missing explanation: {row}")

        last_date = parse_date(get_value(row, "last_date"))
        if last_date is None:
            raise ValueError(f"Review row has invalid last_date: {row}")

    return True


def print_summary(rows):
    counts = Counter(row["review_status"] for row in rows)

    print(f"Worksheet rebuilt: {OUTPUT_WORKSHEET_NAME}")
    print(f"Review rows generated: {len(rows)}")
    print("Count by review_status:")
    for status, count in sorted(counts.items()):
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
