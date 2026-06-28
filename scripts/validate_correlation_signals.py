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
    validate_pipeline,
)


def validate_local_correlations(require_csv):
    _, signals = validate_pipeline.validate_pipeline(require_csv=require_csv)
    if not signals:
        print("SKIP correlation_signals: no local signals available")
        return []

    clusters = build_cluster_signals_sheet.build_clusters(signals)
    build_cluster_signals_sheet.validate_clusters(clusters, signals)
    first = build_correlation_signals_sheet.build_correlations(signals, clusters)
    second = build_correlation_signals_sheet.build_correlations(signals, clusters)

    if first != second:
        raise ValueError("correlation_signals: generation is not deterministic")

    build_correlation_signals_sheet.validate_correlations(first, signals, clusters)
    print(f"OK correlation_signals: {len(first)} deterministic correlations")
    return first


def validate_google_sheets_correlations():
    sheet = build_correlation_signals_sheet.open_sheet()
    signals = build_correlation_signals_sheet.read_records(
        sheet,
        build_correlation_signals_sheet.SIGNALS_WORKSHEET_NAME,
    )
    clusters = build_correlation_signals_sheet.read_records(
        sheet,
        build_correlation_signals_sheet.CLUSTERS_WORKSHEET_NAME,
    )
    expected = build_correlation_signals_sheet.build_correlations(signals, clusters)
    build_correlation_signals_sheet.validate_correlations(expected, signals, clusters)

    worksheet = sheet.worksheet(build_correlation_signals_sheet.OUTPUT_WORKSHEET_NAME)
    actual = rows_to_dicts(worksheet.get_all_values())

    if actual != expected:
        raise ValueError("correlation_signals worksheet does not match rebuilt correlations")

    print(f"OK Google Sheets correlation_signals: {len(actual)} rows match rebuild")
    return actual


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate correlation_signals generation locally or against Google Sheets."
    )
    parser.add_argument(
        "--require-csv",
        action="store_true",
        help="Fail if any expected local CSV artifact is missing.",
    )
    parser.add_argument(
        "--google-sheets",
        action="store_true",
        help="Validate the Google Sheets correlation_signals worksheet against a fresh rebuild.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.google_sheets:
        correlations = validate_google_sheets_correlations()
    else:
        correlations = validate_local_correlations(require_csv=args.require_csv)

    print(f"Correlations validated: {len(correlations)}")
    for correlation in correlations[:5]:
        print(correlation)


if __name__ == "__main__":
    main()
