import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from radar.records import rows_to_dicts
from scripts import build_cluster_signals_sheet, validate_pipeline


def validate_cluster_generation(require_csv):
    _, signals = validate_pipeline.validate_pipeline(require_csv=require_csv)
    if not signals:
        print("SKIP cluster_signals: no local signals available")
        return []

    first = build_cluster_signals_sheet.build_clusters(signals)
    second = build_cluster_signals_sheet.build_clusters(signals)
    if first != second:
        raise ValueError("cluster_signals: generation is not deterministic")

    build_cluster_signals_sheet.validate_clusters(first, signals)
    print(f"OK cluster_signals: {len(first)} deterministic clusters")
    return first


def validate_google_sheets_clusters():
    sheet = build_cluster_signals_sheet.open_sheet()
    signals = build_cluster_signals_sheet.read_signals(sheet)
    expected_clusters = build_cluster_signals_sheet.build_clusters(signals)
    build_cluster_signals_sheet.validate_clusters(expected_clusters, signals)

    worksheet = sheet.worksheet(build_cluster_signals_sheet.OUTPUT_WORKSHEET_NAME)
    actual_clusters = rows_to_dicts(worksheet.get_all_values())

    if actual_clusters != expected_clusters:
        raise ValueError("cluster_signals worksheet does not match rebuilt clusters")

    print(f"OK Google Sheets cluster_signals: {len(actual_clusters)} rows match rebuild")
    return actual_clusters


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate cluster_signals generation locally or against Google Sheets."
    )
    parser.add_argument(
        "--require-csv",
        action="store_true",
        help="Fail if any expected local CSV artifact is missing.",
    )
    parser.add_argument(
        "--google-sheets",
        action="store_true",
        help="Validate the Google Sheets cluster_signals worksheet against a fresh rebuild from signals.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.google_sheets:
        clusters = validate_google_sheets_clusters()
        print(f"Clusters validated: {len(clusters)}")
        for cluster in clusters[:5]:
            print(cluster)
        return

    clusters = validate_cluster_generation(require_csv=args.require_csv)
    print(f"Clusters validated: {len(clusters)}")
    for cluster in clusters[:5]:
        print(cluster)


if __name__ == "__main__":
    main()
