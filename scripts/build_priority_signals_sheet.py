from collections import Counter
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

from radar.dates import parse_date
from radar.records import (
    get_value,
    rows_to_dicts,
    split_semicolon,
    stable_id,
)
from radar.sheets import open_sheet, read_records, replace_worksheet


CLUSTERS_WORKSHEET_NAME = "cluster_signals"
CORRELATIONS_WORKSHEET_NAME = "correlation_signals"
OUTPUT_WORKSHEET_NAME = "priority_signals"

PRIORITY_SIGNALS_HEADER = [
    "priority_id",
    "priority_level",
    "opportunity_type",
    "ticker",
    "entity_name",
    "first_date",
    "last_date",
    "sources",
    "evidence_count",
    "cluster_ids",
    "signal_ids",
    "reason",
    "priority_reason",
]

PRIORITY_ORDER = {
    "HIGH": 0,
    "MEDIUM": 1,
    "LOW": 2,
}


def split_ids(value):
    return split_semicolon(value)


def make_priority_id(opportunity_type, cluster_ids, signal_ids):
    return stable_id(
        "priority",
        [opportunity_type] + sorted(cluster_ids) + sorted(signal_ids),
    )


def count_evidence(cluster_ids, signal_ids):
    return len(set(cluster_ids)) + len(set(signal_ids))


def priority_from_correlation(correlation):
    correlation_type = get_value(correlation, "correlation_type")
    if correlation_type == "CONTRACT_MARKET_ENTITY":
        return (
            "HIGH",
            "Contract cluster is connected to a related market signal.",
        )
    return (
        "MEDIUM",
        "Ticker cluster has additional related market activity.",
    )


def priority_from_cluster(cluster):
    cluster_type = get_value(cluster, "cluster_type")
    signal_count = int(get_value(cluster, "signal_count") or "0")
    sources = split_ids(get_value(cluster, "sources"))

    if cluster_type == "CROSS_SOURCE":
        return (
            "HIGH",
            "Multiple source families reference the same ticker.",
        )

    if cluster_type == "REPEATED_CONTRACTS" and signal_count >= 5:
        return (
            "MEDIUM",
            "Repeated contract activity has enough observations to deserve review.",
        )

    if cluster_type == "TICKER_ACTIVITY" and signal_count >= 2:
        return (
            "MEDIUM",
            "Repeated market activity on the same ticker deserves review.",
        )

    if len(sources) > 1:
        return (
            "MEDIUM",
            "Opportunity includes evidence from multiple sources.",
        )

    return (
        "LOW",
        "Single simple cluster retained for completeness.",
    )


def build_priority_from_correlation(correlation):
    cluster_ids = split_ids(get_value(correlation, "cluster_ids"))
    signal_ids = split_ids(get_value(correlation, "signal_ids"))
    priority_level, priority_reason = priority_from_correlation(correlation)
    opportunity_type = get_value(correlation, "correlation_type")

    return {
        "priority_id": make_priority_id(opportunity_type, cluster_ids, signal_ids),
        "priority_level": priority_level,
        "opportunity_type": opportunity_type,
        "ticker": get_value(correlation, "ticker"),
        "entity_name": get_value(correlation, "entity_name"),
        "first_date": get_value(correlation, "first_date"),
        "last_date": get_value(correlation, "last_date"),
        "sources": get_value(correlation, "sources"),
        "evidence_count": str(count_evidence(cluster_ids, signal_ids)),
        "cluster_ids": "; ".join(cluster_ids),
        "signal_ids": "; ".join(signal_ids),
        "reason": get_value(correlation, "reason"),
        "priority_reason": priority_reason,
    }


def build_priority_from_cluster(cluster):
    cluster_ids = [get_value(cluster, "cluster_id")]
    signal_ids = split_ids(get_value(cluster, "source_signal_ids"))
    priority_level, priority_reason = priority_from_cluster(cluster)
    opportunity_type = get_value(cluster, "cluster_type")

    return {
        "priority_id": make_priority_id(opportunity_type, cluster_ids, signal_ids),
        "priority_level": priority_level,
        "opportunity_type": opportunity_type,
        "ticker": get_value(cluster, "ticker"),
        "entity_name": get_value(cluster, "entity_name"),
        "first_date": get_value(cluster, "first_signal_date"),
        "last_date": get_value(cluster, "last_signal_date"),
        "sources": get_value(cluster, "sources"),
        "evidence_count": str(count_evidence(cluster_ids, signal_ids)),
        "cluster_ids": "; ".join(cluster_ids),
        "signal_ids": "; ".join(signal_ids),
        "reason": get_value(cluster, "reason"),
        "priority_reason": priority_reason,
    }


def build_priorities(clusters, correlations):
    priorities = []
    covered_cluster_ids = set()

    for correlation in correlations:
        priority = build_priority_from_correlation(correlation)
        priorities.append(priority)
        covered_cluster_ids.update(split_ids(priority["cluster_ids"]))

    for cluster in clusters:
        cluster_id = get_value(cluster, "cluster_id")
        if cluster_id in covered_cluster_ids:
            continue
        priorities.append(build_priority_from_cluster(cluster))

    unique_priorities = {}
    for priority in priorities:
        unique_priorities[priority["priority_id"]] = priority

    return sorted(
        unique_priorities.values(),
        key=lambda priority: (
            PRIORITY_ORDER.get(priority["priority_level"], 99),
            priority["last_date"],
            priority["opportunity_type"],
            priority["ticker"],
            priority["entity_name"],
            priority["priority_id"],
        ),
    )


def write_priorities(sheet, priorities):
    replace_worksheet(sheet, OUTPUT_WORKSHEET_NAME, PRIORITY_SIGNALS_HEADER, priorities)


def validate_priorities(priorities, clusters, correlations):
    cluster_ids = {get_value(cluster, "cluster_id") for cluster in clusters}
    priority_ids = [priority["priority_id"] for priority in priorities]
    if len(priority_ids) != len(set(priority_ids)):
        raise ValueError("Duplicate priority_id values found")

    for priority in priorities:
        if priority["priority_level"] not in PRIORITY_ORDER:
            raise ValueError(f"Invalid priority level: {priority}")

        if not get_value(priority, "reason") or not get_value(priority, "priority_reason"):
            raise ValueError(f"Priority has missing explanation: {priority}")

        referenced_clusters = split_ids(get_value(priority, "cluster_ids"))
        missing_clusters = [
            cluster_id for cluster_id in referenced_clusters if cluster_id not in cluster_ids
        ]
        if missing_clusters:
            raise ValueError(
                f"Priority references missing clusters: {priority['priority_id']}"
            )

        if int(priority["evidence_count"]) < 2:
            raise ValueError(f"Priority has insufficient evidence: {priority}")

        first_date = parse_date(get_value(priority, "first_date"))
        last_date = parse_date(get_value(priority, "last_date"))
        if first_date is None or last_date is None or first_date > last_date:
            raise ValueError(f"Priority has invalid dates: {priority}")

    return True


def print_summary(priorities):
    counts = Counter(priority["priority_level"] for priority in priorities)

    print(f"Worksheet rebuilt: {OUTPUT_WORKSHEET_NAME}")
    print(f"Priorities generated: {len(priorities)}")
    print("Count by priority_level:")
    for priority_level, count in sorted(counts.items()):
        print(f"- {priority_level}: {count}")

    print("Example 5 priorities:")
    for priority in priorities[:5]:
        print({column: priority[column] for column in PRIORITY_SIGNALS_HEADER})


def main():
    sheet = open_sheet()
    clusters = read_records(sheet, CLUSTERS_WORKSHEET_NAME)
    correlations = read_records(sheet, CORRELATIONS_WORKSHEET_NAME)
    priorities = build_priorities(clusters, correlations)
    validate_priorities(priorities, clusters, correlations)
    write_priorities(sheet, priorities)
    print_summary(priorities)


if __name__ == "__main__":
    main()
