import argparse
import compileall
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["bs4", "gspread", "requests"])

from scripts import (
    build_cluster_signals_sheet,
    build_correlation_signals_sheet,
    build_priority_signals_sheet,
    build_review_queue_sheet,
    build_signals_sheet,
    send_telegram_alerts,
    validate_pipeline,
)
from radar.scoring import load_scoring_config

FIXTURE_CSV_DIR = Path("tests/fixtures")
GENERATED_CSV_DIR = Path("data")
VALIDATION_RUN_DATE = "2026-01-10"
PYTHON_MODULE_DIRS = ["radar", "collectors", "loaders", "scripts"]


def print_step(name):
    print(f"\n== {name} ==")


def validate_python_modules():
    for module_dir in PYTHON_MODULE_DIRS:
        path = PROJECT_ROOT / module_dir
        if not compileall.compile_dir(str(path), quiet=1, maxlevels=20):
            raise ValueError(f"Python compile failed: {module_dir}")
    print(f"OK python_modules: {', '.join(PYTHON_MODULE_DIRS)}")
    return len(PYTHON_MODULE_DIRS)


def validate_patch_whitespace():
    result = subprocess.run(
        ["git", "diff", "--check"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise ValueError(f"git diff --check failed:\n{output}")
    print("OK patch_whitespace: git diff --check")
    return 1


def load_fixture_worksheets(csv_dir):
    worksheets = {}
    for name, csv_filename, _, _ in validate_pipeline.CSV_SPECS:
        csv_file = csv_dir / csv_filename
        if csv_file.exists():
            worksheets[validate_pipeline.WORKSHEET_BY_SOURCE[name]] = (
                validate_pipeline.read_csv_values(csv_file)
            )
    return worksheets


def validate_missing_source_tolerance(csv_dir):
    worksheets = load_fixture_worksheets(csv_dir)
    if len(worksheets) < 2:
        print("SKIP missing source tolerance: fewer than 2 CSV fixtures available")
        return 0

    partial_worksheets = dict(worksheets)
    skipped_worksheet = build_signals_sheet.RAW_CAPITOL_TRADES
    partial_worksheets.pop(skipped_worksheet, None)

    sheet = validate_pipeline.FakeSheet(partial_worksheets)
    build_signals_sheet.DATE_WARNINGS.clear()
    first = build_signals_sheet.build_signals(sheet)
    build_signals_sheet.DATE_WARNINGS.clear()
    second = build_signals_sheet.build_signals(sheet)

    if first != second:
        raise ValueError("signals: partial-source generation is not deterministic")

    signal_ids = [signal["signal_id"] for signal in first]
    if len(signal_ids) != len(set(signal_ids)):
        raise ValueError("signals: duplicate signal_id values found with partial sources")

    if not first:
        raise ValueError("signals: partial sources produced no rows")

    print(
        "OK missing source tolerance: "
        f"skipped {skipped_worksheet}, generated {len(first)} signals"
    )
    return len(first)


def validate_review_lifecycle(priorities, review_rows, signals, scoring_config):
    if len(priorities) < 2 or not review_rows:
        print("SKIP review_queue lifecycle: insufficient priority rows")
        return 0

    previous_state = {row["review_id"]: dict(row) for row in review_rows}
    preserved_priority = priorities[0]
    preserved_review_id = build_review_queue_sheet.make_review_id(preserved_priority)
    previous_state[preserved_review_id]["review_status"] = "WATCHING"
    previous_state[preserved_review_id]["review_note"] = "manual note preserved"

    active_rows = build_review_queue_sheet.build_review_queue(
        priorities,
        previous_state,
        run_date="2026-01-11",
        signals=signals,
        scoring_config=scoring_config,
    )
    build_review_queue_sheet.validate_review_queue(active_rows, priorities)

    active_preserved_rows = [
        row for row in active_rows if row["review_id"] == preserved_review_id
    ]
    if not active_preserved_rows:
        raise ValueError("review_queue: active row disappeared during rebuild")

    active_row = active_preserved_rows[0]
    if active_row["status"] != build_review_queue_sheet.STATUS_ACTIVE:
        raise ValueError("review_queue: existing priority was not marked ACTIVE")
    if active_row["last_seen"] != "2026-01-11":
        raise ValueError("review_queue: last_seen was not updated for active row")
    if active_row["review_status"] != "WATCHING":
        raise ValueError("review_queue: active review_status was not preserved")
    if active_row["review_note"] != "manual note preserved":
        raise ValueError("review_queue: active review_note was not preserved")

    next_rows = build_review_queue_sheet.build_review_queue(
        priorities[1:],
        previous_state,
        run_date="2026-01-11",
        signals=signals,
        scoring_config=scoring_config,
    )
    build_review_queue_sheet.validate_review_queue(next_rows, priorities[1:])

    preserved_rows = [
        row for row in next_rows if row["review_id"] == preserved_review_id
    ]
    if not preserved_rows:
        raise ValueError("review_queue: disappeared row was not retained")

    preserved_row = preserved_rows[0]
    if preserved_row["status"] != build_review_queue_sheet.STATUS_CLOSED:
        raise ValueError("review_queue: disappeared priority was not marked CLOSED")
    if preserved_row["closed_date"] != "2026-01-11":
        raise ValueError("review_queue: closed_date was not set to rebuild date")
    if preserved_row["review_status"] != "WATCHING":
        raise ValueError("review_queue: manual review_status was not preserved")
    if preserved_row["review_note"] != "manual note preserved":
        raise ValueError("review_queue: manual review_note was not preserved")
    original_row = previous_state[preserved_review_id]
    if preserved_row["first_seen"] != original_row["first_seen"]:
        raise ValueError("review_queue: first_seen history was not preserved")

    print("OK review_queue lifecycle: ACTIVE/CLOSED states and manual fields preserved")
    return len(next_rows)


def validate_all(require_csv=True, csv_dir=FIXTURE_CSV_DIR):
    started_at = time.monotonic()
    summary = []
    csv_dir = validate_pipeline.resolve_csv_dir(csv_dir)

    print_step("repository")
    summary.append(("python_modules", validate_python_modules()))
    summary.append(("patch_whitespace", validate_patch_whitespace()))
    send_telegram_alerts.validate_dry_run_logic()
    print("OK telegram_dry_run_logic: message generation and deduplication")
    summary.append(("telegram_dry_run_logic", 1))

    print_step("pipeline")
    scoring_config = load_scoring_config()
    print("OK scoring_config: config/scoring.json")
    summary.append(("scoring_config", 1))
    print(f"CSV directory: {csv_dir}")
    loaded_sources, signals = validate_pipeline.validate_pipeline(
        require_csv=require_csv,
        csv_dir=csv_dir,
    )
    summary.append(("sources", len(loaded_sources)))
    summary.append(("signals", len(signals)))

    if signals:
        summary.append(("partial_source_signals", validate_missing_source_tolerance(csv_dir)))

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
        signals=signals,
        scoring_config=scoring_config,
    )
    second_review_rows = build_review_queue_sheet.build_review_queue(
        priorities,
        run_date=VALIDATION_RUN_DATE,
        signals=signals,
        scoring_config=scoring_config,
    )
    if review_rows != second_review_rows:
        raise ValueError("review_queue: generation is not deterministic")
    build_review_queue_sheet.validate_review_queue(review_rows, priorities)
    print(f"OK review_queue: {len(review_rows)} deterministic review rows")
    summary.append(("review_queue", len(review_rows)))

    lifecycle_rows = validate_review_lifecycle(
        priorities,
        review_rows,
        signals,
        scoring_config,
    )
    summary.append(("review_queue_lifecycle", lifecycle_rows))

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
