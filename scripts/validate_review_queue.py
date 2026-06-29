import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.records import rows_to_dicts
from scripts import (
    build_cluster_signals_sheet,
    build_correlation_signals_sheet,
    build_priority_signals_sheet,
    build_review_queue_sheet,
    validate_pipeline,
)


def validate_local_review_queue(require_csv):
    _, signals = validate_pipeline.validate_pipeline(require_csv=require_csv)
    if not signals:
        print("SKIP review_queue: no local signals available")
        return []

    clusters = build_cluster_signals_sheet.build_clusters(signals)
    correlations = build_correlation_signals_sheet.build_correlations(signals, clusters)
    priorities = build_priority_signals_sheet.build_priorities(clusters, correlations)
    first = build_review_queue_sheet.build_review_queue(
        priorities,
        run_date="2026-01-10",
    )
    second = build_review_queue_sheet.build_review_queue(
        priorities,
        run_date="2026-01-10",
    )

    if first != second:
        raise ValueError("review_queue: generation is not deterministic")

    build_review_queue_sheet.validate_review_queue(first, priorities)

    previous_state = {
        row["review_id"]: dict(row)
        for row in first
    }
    previous_review_id = first[0]["review_id"] if first else ""
    if previous_review_id:
        previous_state[previous_review_id]["review_status"] = "WATCHING"
        previous_state[previous_review_id]["review_note"] = "manual note preserved"

    changed_rows = build_review_queue_sheet.build_review_queue(
        priorities[1:],
        previous_state,
        run_date="2026-01-11",
    )
    build_review_queue_sheet.validate_review_queue(changed_rows, priorities[1:])

    closed_rows = [
        row
        for row in changed_rows
        if row["status"] == build_review_queue_sheet.STATUS_CLOSED
    ]
    if previous_review_id and not closed_rows:
        raise ValueError("review_queue: disappeared priorities are not marked CLOSED")

    preserved_closed_rows = [
        row
        for row in closed_rows
        if row["review_id"] == previous_review_id
    ]
    if preserved_closed_rows:
        closed_row = preserved_closed_rows[0]
        if closed_row["review_status"] != "WATCHING":
            raise ValueError("review_queue: manual review_status was not preserved")
        if closed_row["review_note"] != "manual note preserved":
            raise ValueError("review_queue: manual review_note was not preserved")

    print(f"OK review_queue: {len(first)} deterministic review rows")
    return first


def validate_google_sheets_review_queue():
    sheet = build_review_queue_sheet.open_sheet()
    priorities = build_review_queue_sheet.read_records(
        sheet,
        build_review_queue_sheet.PRIORITIES_WORKSHEET_NAME,
    )
    actual = rows_to_dicts(
        sheet.worksheet(build_review_queue_sheet.OUTPUT_WORKSHEET_NAME).get_all_values()
    )
    expected = build_review_queue_sheet.build_review_queue(
        priorities,
        {row["review_id"]: dict(row) for row in actual},
    )

    build_review_queue_sheet.validate_review_queue(expected, priorities)
    if actual != expected:
        raise ValueError("review_queue worksheet does not match rebuilt review queue")

    print(f"OK Google Sheets review_queue: {len(actual)} rows match rebuild")
    return actual


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate review_queue generation locally or against Google Sheets."
    )
    parser.add_argument(
        "--require-csv",
        action="store_true",
        help="Fail if any expected local CSV artifact is missing.",
    )
    parser.add_argument(
        "--google-sheets",
        action="store_true",
        help="Validate the Google Sheets review_queue worksheet against a fresh rebuild.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.google_sheets:
        rows = validate_google_sheets_review_queue()
    else:
        rows = validate_local_review_queue(require_csv=args.require_csv)

    print(f"Review rows validated: {len(rows)}")
    for row in rows[:5]:
        print(row)


if __name__ == "__main__":
    main()
