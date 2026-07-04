import re
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
    split_semicolon,
    stable_id,
    unique_sorted,
)
from radar.sheets import open_sheet, read_records, replace_worksheet


SIGNALS_WORKSHEET_NAME = "signals"
CLUSTERS_WORKSHEET_NAME = "cluster_signals"
OUTPUT_WORKSHEET_NAME = "correlation_signals"
CORRELATION_WINDOW_DAYS = 180

CORRELATION_SIGNALS_HEADER = [
    "correlation_id",
    "correlation_type",
    "ticker",
    "entity_name",
    "first_date",
    "last_date",
    "sources",
    "cluster_ids",
    "signal_ids",
    "reason",
]

MARKET_SIGNAL_TYPES = {"PTR_BUY", "PTR_SELL", "INSIDER_BUY"}
STOPWORDS = {
    "a",
    "and",
    "co",
    "company",
    "corp",
    "corporation",
    "federal",
    "holdings",
    "inc",
    "incorporated",
    "joint",
    "llc",
    "ltd",
    "mission",
    "plc",
    "programs",
    "systems",
    "the",
    "venture",
}


def signal_date(signal):
    return parse_date(get_value(signal, "signal_date"))


def cluster_dates(cluster):
    first_date = parse_date(get_value(cluster, "first_signal_date"))
    last_date = parse_date(get_value(cluster, "last_signal_date"))
    return first_date, last_date


def normalize_text(value):
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def entity_tokens(value):
    normalized = normalize_text(value)
    compact = normalized.replace(" ", "")
    tokens = {
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in STOPWORDS
    }
    if compact:
        tokens.add(compact)
    return tokens


def entities_look_related(left, right):
    left_tokens = entity_tokens(left)
    right_tokens = entity_tokens(right)
    if not left_tokens or not right_tokens:
        return False

    overlap = left_tokens & right_tokens
    if len(overlap) >= 2:
        return True

    left_compact = normalize_text(left).replace(" ", "")
    right_compact = normalize_text(right).replace(" ", "")
    if left_compact and right_compact and left_compact == right_compact:
        return True

    return False


def source_signal_ids(cluster):
    return split_semicolon(get_value(cluster, "source_signal_ids"))


def within_window(first_date, last_date):
    if first_date is None or last_date is None:
        return False
    return abs((last_date - first_date).days) <= CORRELATION_WINDOW_DAYS


def relation_dates(cluster, signal):
    cluster_first, cluster_last = cluster_dates(cluster)
    current_signal_date = signal_date(signal)
    dates = [date for date in [cluster_first, cluster_last, current_signal_date] if date]
    if not dates:
        return None, None
    return min(dates), max(dates)


def make_correlation_id(correlation_type, cluster_ids, signal_ids):
    return stable_id(
        correlation_type.lower(),
        [correlation_type] + sorted(cluster_ids) + sorted(signal_ids),
    )


def build_correlation(correlation_type, cluster, signal):
    cluster_id = get_value(cluster, "cluster_id")
    signal_id = get_value(signal, "signal_id")
    first_date, last_date = relation_dates(cluster, signal)
    sources = unique_sorted(
        get_value(signal, "source")
        for signal in [signal]
    )
    for source in get_value(cluster, "sources").split(";"):
        if source.strip():
            sources.append(source.strip())
    sources = unique_sorted(sources)

    ticker = get_value(signal, "ticker") or get_value(cluster, "ticker")
    entity_name = get_value(signal, "entity_name") or get_value(cluster, "entity_name")

    if correlation_type == "CONTRACT_MARKET_ENTITY":
        reason = (
            f"Contract cluster for {get_value(cluster, 'entity_name')} aligns with "
            f"{get_value(signal, 'signal_type')} on related entity {get_value(signal, 'entity_name')} "
            f"within {CORRELATION_WINDOW_DAYS} days."
        )
    else:
        reason = (
            f"Cluster for ticker {ticker} has an additional "
            f"{get_value(signal, 'signal_type')} signal within {CORRELATION_WINDOW_DAYS} days."
        )

    return {
        "correlation_id": make_correlation_id(correlation_type, [cluster_id], [signal_id]),
        "correlation_type": correlation_type,
        "ticker": ticker,
        "entity_name": entity_name,
        "first_date": first_date.isoformat() if first_date else "",
        "last_date": last_date.isoformat() if last_date else "",
        "sources": "; ".join(sources),
        "cluster_ids": cluster_id,
        "signal_ids": signal_id,
        "reason": reason,
    }


def build_correlations(signals, clusters):
    correlations = []
    seen = set()

    for cluster in clusters:
        cluster_type = get_value(cluster, "cluster_type")
        cluster_ticker = get_value(cluster, "ticker").upper()
        cluster_entity = get_value(cluster, "entity_name")
        cluster_signal_ids = set(source_signal_ids(cluster))
        cluster_first, cluster_last = cluster_dates(cluster)

        for signal in signals:
            signal_id = get_value(signal, "signal_id")
            if not signal_id or signal_id in cluster_signal_ids:
                continue

            signal_type = get_value(signal, "signal_type")
            if signal_type not in MARKET_SIGNAL_TYPES:
                continue

            current_signal_date = signal_date(signal)
            if not within_window(cluster_last, current_signal_date):
                continue

            signal_ticker = get_value(signal, "ticker").upper()
            signal_entity = get_value(signal, "entity_name")

            correlation_type = ""
            if cluster_type == "REPEATED_CONTRACTS" and entities_look_related(
                cluster_entity,
                signal_entity,
            ):
                correlation_type = "CONTRACT_MARKET_ENTITY"
            elif cluster_ticker and signal_ticker == cluster_ticker:
                correlation_type = "TICKER_CLUSTER_RELATED_SIGNAL"

            if not correlation_type:
                continue

            correlation = build_correlation(correlation_type, cluster, signal)
            seen_key = correlation["correlation_id"]
            if seen_key in seen:
                continue

            if not within_window(
                parse_date(correlation["first_date"]),
                parse_date(correlation["last_date"]),
            ):
                continue

            seen.add(seen_key)
            correlations.append(correlation)

    return sorted(
        correlations,
        key=lambda correlation: (
            correlation["last_date"],
            correlation["correlation_type"],
            correlation["ticker"],
            correlation["entity_name"],
            correlation["correlation_id"],
        ),
    )


def write_correlations(sheet, correlations):
    replace_worksheet(
        sheet,
        OUTPUT_WORKSHEET_NAME,
        CORRELATION_SIGNALS_HEADER,
        correlations,
    )


def validate_correlations(correlations, signals, clusters):
    signal_ids = {get_value(signal, "signal_id") for signal in signals}
    cluster_ids = {get_value(cluster, "cluster_id") for cluster in clusters}
    correlation_ids = [correlation["correlation_id"] for correlation in correlations]
    if len(correlation_ids) != len(set(correlation_ids)):
        raise ValueError("Duplicate correlation_id values found")

    for correlation in correlations:
        referenced_clusters = [
            cluster_id
            for cluster_id in split_semicolon(correlation["cluster_ids"])
        ]
        referenced_signals = [
            signal_id
            for signal_id in split_semicolon(correlation["signal_ids"])
        ]

        missing_clusters = [
            cluster_id for cluster_id in referenced_clusters if cluster_id not in cluster_ids
        ]
        missing_signals = [
            signal_id for signal_id in referenced_signals if signal_id not in signal_ids
        ]
        if missing_clusters or missing_signals:
            raise ValueError(
                f"Correlation references missing records: {correlation['correlation_id']}"
            )

        first_date = parse_date(correlation["first_date"])
        last_date = parse_date(correlation["last_date"])
        if not within_window(first_date, last_date):
            raise ValueError(
                f"Correlation exceeds window: {correlation['correlation_id']}"
            )

        if not get_value(correlation, "reason"):
            raise ValueError(f"Correlation has no reason: {correlation}")

    return True


def print_summary(correlations):
    counts = Counter(correlation["correlation_type"] for correlation in correlations)

    print(f"Worksheet rebuilt: {OUTPUT_WORKSHEET_NAME}")
    print(f"Correlations generated: {len(correlations)}")
    print("Count by correlation_type:")
    for correlation_type, count in sorted(counts.items()):
        print(f"- {correlation_type}: {count}")

    print("Example 5 correlations:")
    for correlation in correlations[:5]:
        print({column: correlation[column] for column in CORRELATION_SIGNALS_HEADER})


def main():
    sheet = open_sheet()
    signals = read_records(sheet, SIGNALS_WORKSHEET_NAME)
    clusters = read_records(sheet, CLUSTERS_WORKSHEET_NAME)
    correlations = build_correlations(signals, clusters)
    validate_correlations(correlations, signals, clusters)
    write_correlations(sheet, correlations)
    print_summary(correlations)


if __name__ == "__main__":
    main()
