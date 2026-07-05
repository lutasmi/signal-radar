import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from scripts import (
    build_cluster_signals_sheet,
    build_correlation_signals_sheet,
    build_priority_signals_sheet,
    build_review_queue_sheet,
    build_signals_sheet,
)


def rebuild_radar():
    sheet = build_signals_sheet.open_sheet()

    build_signals_sheet.DATE_WARNINGS.clear()
    signals = build_signals_sheet.build_signals(sheet)
    build_signals_sheet.write_signals(sheet, signals)
    build_signals_sheet.print_summary(signals)

    clusters = build_cluster_signals_sheet.build_clusters(signals)
    build_cluster_signals_sheet.validate_clusters(clusters, signals)
    build_cluster_signals_sheet.write_clusters(sheet, clusters)
    build_cluster_signals_sheet.print_summary(clusters)

    correlations = build_correlation_signals_sheet.build_correlations(signals, clusters)
    build_correlation_signals_sheet.validate_correlations(
        correlations,
        signals,
        clusters,
    )
    build_correlation_signals_sheet.write_correlations(sheet, correlations)
    build_correlation_signals_sheet.print_summary(correlations)

    priorities = build_priority_signals_sheet.build_priorities(clusters, correlations)
    build_priority_signals_sheet.validate_priorities(
        priorities,
        clusters,
        correlations,
    )
    build_priority_signals_sheet.write_priorities(sheet, priorities)
    build_priority_signals_sheet.print_summary(priorities)

    review_rows = build_review_queue_sheet.build_review_queue(
        priorities,
        build_review_queue_sheet.existing_review_state(sheet),
        signals=signals,
    )
    build_review_queue_sheet.validate_review_queue(review_rows, priorities)
    build_review_queue_sheet.write_review_queue(sheet, review_rows)
    build_review_queue_sheet.print_summary(review_rows)

    return signals, clusters, correlations, priorities, review_rows


def main():
    signals, clusters, correlations, priorities, review_rows = rebuild_radar()
    print("Radar rebuild complete")
    print(f"Signals: {len(signals)}")
    print(f"Clusters: {len(clusters)}")
    print(f"Correlations: {len(correlations)}")
    print(f"Priorities: {len(priorities)}")
    print(f"Review rows: {len(review_rows)}")


if __name__ == "__main__":
    main()
