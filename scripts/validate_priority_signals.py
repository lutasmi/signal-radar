import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from radar.records import rows_to_dicts
from scripts import (
    build_cluster_signals_sheet,
    build_correlation_signals_sheet,
    build_priority_signals_sheet,
    validate_pipeline,
)


def validate_local_priorities(require_csv):
    _, signals = validate_pipeline.validate_pipeline(require_csv=require_csv)
    if not signals:
        print("SKIP priority_signals: no local signals available")
        return []

    clusters = build_cluster_signals_sheet.build_clusters(signals)
    correlations = build_correlation_signals_sheet.build_correlations(signals, clusters)
    first = build_priority_signals_sheet.build_priorities(clusters, correlations)
    second = build_priority_signals_sheet.build_priorities(clusters, correlations)

    if first != second:
        raise ValueError("priority_signals: generation is not deterministic")

    build_priority_signals_sheet.validate_priorities(first, clusters, correlations)
    print(f"OK priority_signals: {len(first)} deterministic priorities")
    return first


def validate_google_sheets_priorities():
    sheet = build_priority_signals_sheet.open_sheet()
    clusters = build_priority_signals_sheet.read_records(
        sheet,
        build_priority_signals_sheet.CLUSTERS_WORKSHEET_NAME,
    )
    correlations = build_priority_signals_sheet.read_records(
        sheet,
        build_priority_signals_sheet.CORRELATIONS_WORKSHEET_NAME,
    )
    expected = build_priority_signals_sheet.build_priorities(clusters, correlations)
    build_priority_signals_sheet.validate_priorities(expected, clusters, correlations)

    worksheet = sheet.worksheet(build_priority_signals_sheet.OUTPUT_WORKSHEET_NAME)
    actual = rows_to_dicts(worksheet.get_all_values())

    if actual != expected:
        raise ValueError("priority_signals worksheet does not match rebuilt priorities")

    print(f"OK Google Sheets priority_signals: {len(actual)} rows match rebuild")
    return actual


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate priority_signals generation locally or against Google Sheets."
    )
    parser.add_argument(
        "--require-csv",
        action="store_true",
        help="Fail if any expected local CSV artifact is missing.",
    )
    parser.add_argument(
        "--google-sheets",
        action="store_true",
        help="Validate the Google Sheets priority_signals worksheet against a fresh rebuild.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.google_sheets:
        priorities = validate_google_sheets_priorities()
    else:
        priorities = validate_local_priorities(require_csv=args.require_csv)

    print(f"Priorities validated: {len(priorities)}")
    for priority in priorities[:5]:
        print(priority)


if __name__ == "__main__":
    main()
