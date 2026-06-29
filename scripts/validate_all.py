import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import (
    build_cluster_signals_sheet,
    build_correlation_signals_sheet,
    build_priority_signals_sheet,
    build_review_queue_sheet,
    validate_pipeline,
)

FIXTURE_CSV_DIR = Path("tests/fixtures")
GENERATED_CSV_DIR = Path("data")
VALIDATION_RUN_DATE = "2026-01-10"


def print_step(name):
    print(f"\n== {name} ==")


def validate_all(require_csv=True, csv_dir=FIXTURE_CSV_DIR):
    started_at = time.monotonic()
    summary = []
    csv_dir = validate_pipeline.resolve_csv_dir(csv_dir)

    print_step("pipeline")
    print(f"CSV directory: {csv_dir}")
    loaded_sources, signals = validate_pipeline.validate_pipeline(
        require_csv=require_csv,
        csv_dir=csv_dir,
    )
    summary.append(("sources", len(loaded_sources)))
    summary.append(("signals", len(signals)))

    if not signals:
        print("\nValidation skipped derived layers because no local signals were available.")
        print("\n== summary ==")
        for name, count in summary:
            print(f"OK {name}: {count}")
        return summary

    print_step("cluster_signals")
    clusters = build_cluster_signals_sheet.build_clusters(signals)
    second_clusters = build_cluster_signals_sheet.build_clusters(signals)
    if clusters != second_clusters:
        raise ValueError("cluster_signals: generation is not deterministic")
    build_cluster_signals_sheet.validate_clusters(clusters, signals)
    print(f"OK cluster_signals: {len(clusters)} deterministic clusters")
    summary.append(("cluster_signals", len(clusters)))

    print_step("correlation_signals")
    correlations = build_correlation_signals_sheet.build_correlations(signals, clusters)
    second_correlations = build_correlation_signals_sheet.build_correlations(
        signals,
        clusters,
    )
    if correlations != second_correlations:
        raise ValueError("correlation_signals: generation is not deterministic")
    build_correlation_signals_sheet.validate_correlations(
        correlations,
        signals,
        clusters,
    )
    print(f"OK correlation_signals: {len(correlations)} deterministic correlations")
    summary.append(("correlation_signals", len(correlations)))

    print_step("priority_signals")
    priorities = build_priority_signals_sheet.build_priorities(clusters, correlations)
    second_priorities = build_priority_signals_sheet.build_priorities(
        clusters,
        correlations,
    )
    if priorities != second_priorities:
        raise ValueError("priority_signals: generation is not deterministic")
    build_priority_signals_sheet.validate_priorities(
        priorities,
        clusters,
        correlations,
    )
    print(f"OK priority_signals: {len(priorities)} deterministic priorities")
    summary.append(("priority_signals", len(priorities)))

    print_step("review_queue")
    review_rows = build_review_queue_sheet.build_review_queue(
        priorities,
        run_date=VALIDATION_RUN_DATE,
    )
    second_review_rows = build_review_queue_sheet.build_review_queue(
        priorities,
        run_date=VALIDATION_RUN_DATE,
    )
    if review_rows != second_review_rows:
        raise ValueError("review_queue: generation is not deterministic")
    build_review_queue_sheet.validate_review_queue(review_rows, priorities)
    print(f"OK review_queue: {len(review_rows)} deterministic review rows")
    summary.append(("review_queue", len(review_rows)))

    elapsed = time.monotonic() - started_at
    print("\n== summary ==")
    for name, count in summary:
        print(f"OK {name}: {count}")
    print(f"Validation completed in {elapsed:.2f}s")
    return summary


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run all safe local Signal Radar validations without touching Google Sheets."
        )
    )
    parser.add_argument(
        "--allow-missing-csv",
        action="store_true",
        help="Skip missing local CSV artifacts instead of failing.",
    )
    parser.add_argument(
        "--csv-dir",
        default=str(FIXTURE_CSV_DIR),
        help="Directory containing validation CSV files. Defaults to tracked fixtures.",
    )
    parser.add_argument(
        "--generated-csv",
        action="store_true",
        help="Validate ignored generated CSV artifacts from data/ instead of fixtures.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    csv_dir = GENERATED_CSV_DIR if args.generated_csv else Path(args.csv_dir)
    try:
        validate_all(
            require_csv=not args.allow_missing_csv,
            csv_dir=csv_dir,
        )
    except Exception as exc:
        print(f"\nVALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
