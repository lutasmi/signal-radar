from collections import Counter, defaultdict

from radar.dates import parse_date
from radar.records import (
    first_non_empty,
    get_value,
    stable_id,
    unique_sorted,
)
from radar.sheets import open_sheet, read_records, replace_worksheet


INPUT_WORKSHEET_NAME = "signals"
OUTPUT_WORKSHEET_NAME = "cluster_signals"
WINDOW_DAYS = 90

CLUSTER_SIGNALS_HEADER = [
    "cluster_id",
    "cluster_type",
    "ticker",
    "entity_name",
    "first_signal_date",
    "last_signal_date",
    "sources",
    "signal_count",
    "actors",
    "reason",
    "source_signal_ids",
]

TICKER_SIGNAL_TYPES = {"PTR_BUY", "INSIDER_BUY"}
CONTRACT_SIGNAL_TYPES = {"CONTRACT"}


def parse_signal_date(signal):
    return parse_date(get_value(signal, "signal_date"))


def normalize_entity_key(value):
    return " ".join(value.lower().replace("&", "and").split())


def read_signals(sheet):
    return read_records(sheet, INPUT_WORKSHEET_NAME)


def make_cluster_id(cluster_type, signal_ids):
    return stable_id(cluster_type.lower(), [cluster_type] + sorted(signal_ids))


def signals_with_dates(signals):
    dated_signals = []
    for signal in signals:
        signal_date = parse_signal_date(signal)
        if signal_date is None:
            continue
        dated_signals.append((signal_date, signal))
    return sorted(
        dated_signals,
        key=lambda item: (
            item[0].isoformat(),
            get_value(item[1], "signal_id"),
        ),
    )


def group_within_window(dated_signals):
    if not dated_signals:
        return []

    first_date = dated_signals[0][0]
    last_date = dated_signals[-1][0]
    if (last_date - first_date).days <= WINDOW_DAYS:
        return [signal for _, signal in dated_signals]

    latest_date = last_date
    return [
        signal
        for signal_date, signal in dated_signals
        if (latest_date - signal_date).days <= WINDOW_DAYS
    ]


def summarize_signal_types(signals):
    counts = Counter(get_value(signal, "signal_type") for signal in signals)
    return ", ".join(
        f"{count} {signal_type}"
        for signal_type, count in sorted(counts.items())
        if signal_type
    )


def build_reason(cluster_type, signals):
    sources = unique_sorted(get_value(signal, "source") for signal in signals)
    actors = unique_sorted(get_value(signal, "actor_name") for signal in signals)
    signal_type_summary = summarize_signal_types(signals)

    if cluster_type == "TICKER_ACTIVITY":
        return (
            f"Repeated ticker activity within {WINDOW_DAYS} days: "
            f"{signal_type_summary} from {len(actors)} actor(s)."
        )

    if cluster_type == "CROSS_SOURCE":
        return (
            f"Cross-source coincidence within {WINDOW_DAYS} days: "
            f"{' + '.join(sources)} reference the same ticker."
        )

    if cluster_type == "REPEATED_CONTRACTS":
        return (
            f"Repeated federal contract activity within {WINDOW_DAYS} days: "
            f"{len(signals)} contracts for the same recipient."
        )

    return f"Cluster detected from {len(signals)} related signals."


def build_cluster(cluster_type, signals, ticker="", entity_name=""):
    dated = signals_with_dates(signals)
    if not dated:
        raise ValueError("Cannot build cluster without dated signals")

    ordered_signals = [signal for _, signal in dated]
    signal_ids = [get_value(signal, "signal_id") for signal in ordered_signals]
    sources = unique_sorted(get_value(signal, "source") for signal in ordered_signals)
    actors = unique_sorted(get_value(signal, "actor_name") for signal in ordered_signals)

    return {
        "cluster_id": make_cluster_id(cluster_type, signal_ids),
        "cluster_type": cluster_type,
        "ticker": ticker,
        "entity_name": entity_name or first_non_empty(
            get_value(signal, "entity_name") for signal in ordered_signals
        ),
        "first_signal_date": dated[0][0].isoformat(),
        "last_signal_date": dated[-1][0].isoformat(),
        "sources": "; ".join(sources),
        "signal_count": str(len(ordered_signals)),
        "actors": "; ".join(actors),
        "reason": build_reason(cluster_type, ordered_signals),
        "source_signal_ids": "; ".join(signal_ids),
    }


def add_cluster(clusters, seen_signal_sets, cluster_type, signals, ticker="", entity_name=""):
    signal_ids = frozenset(get_value(signal, "signal_id") for signal in signals)
    seen_key = (cluster_type, signal_ids)
    if len(signal_ids) < 2 or seen_key in seen_signal_sets:
        return

    seen_signal_sets.add(seen_key)
    clusters.append(
        build_cluster(
            cluster_type=cluster_type,
            signals=signals,
            ticker=ticker,
            entity_name=entity_name,
        )
    )


def build_clusters(signals):
    clusters = []
    seen_signal_sets = set()

    signals_by_ticker = defaultdict(list)
    contracts_by_entity = defaultdict(list)

    for signal in signals:
        signal_type = get_value(signal, "signal_type")
        ticker = get_value(signal, "ticker").upper()
        entity_name = get_value(signal, "entity_name")

        if ticker and signal_type in TICKER_SIGNAL_TYPES:
            signals_by_ticker[ticker].append(signal)

        if signal_type in CONTRACT_SIGNAL_TYPES and entity_name:
            contracts_by_entity[normalize_entity_key(entity_name)].append(signal)

    for ticker, ticker_signals in sorted(signals_by_ticker.items()):
        window_signals = group_within_window(signals_with_dates(ticker_signals))
        if len(window_signals) < 2:
            continue

        sources = unique_sorted(get_value(signal, "source") for signal in window_signals)
        entity_name = first_non_empty(get_value(signal, "entity_name") for signal in window_signals)

        add_cluster(
            clusters,
            seen_signal_sets,
            "TICKER_ACTIVITY",
            window_signals,
            ticker=ticker,
            entity_name=entity_name,
        )

        if len(sources) > 1:
            add_cluster(
                clusters,
                seen_signal_sets,
                "CROSS_SOURCE",
                window_signals,
                ticker=ticker,
                entity_name=entity_name,
            )

    for entity_key, contract_signals in sorted(contracts_by_entity.items()):
        window_signals = group_within_window(signals_with_dates(contract_signals))
        if len(window_signals) < 2:
            continue

        add_cluster(
            clusters,
            seen_signal_sets,
            "REPEATED_CONTRACTS",
            window_signals,
            entity_name=first_non_empty(
                get_value(signal, "entity_name") for signal in window_signals
            ),
        )

    return sorted(
        clusters,
        key=lambda cluster: (
            cluster["last_signal_date"],
            cluster["cluster_type"],
            cluster["ticker"],
            cluster["entity_name"],
            cluster["cluster_id"],
        ),
    )


def write_clusters(sheet, clusters):
    replace_worksheet(sheet, OUTPUT_WORKSHEET_NAME, CLUSTER_SIGNALS_HEADER, clusters)


def validate_clusters(clusters, signals):
    signal_ids = {get_value(signal, "signal_id") for signal in signals}
    cluster_ids = [cluster["cluster_id"] for cluster in clusters]
    if len(cluster_ids) != len(set(cluster_ids)):
        raise ValueError("Duplicate cluster_id values found")

    for cluster in clusters:
        source_signal_ids = [
            signal_id.strip()
            for signal_id in cluster["source_signal_ids"].split(";")
            if signal_id.strip()
        ]
        if len(source_signal_ids) < 2:
            raise ValueError(f"Cluster has fewer than 2 source signals: {cluster}")

        missing = [signal_id for signal_id in source_signal_ids if signal_id not in signal_ids]
        if missing:
            raise ValueError(
                f"Cluster references missing signals: {cluster['cluster_id']} {missing}"
            )

    return True


def print_summary(clusters):
    counts = Counter(cluster["cluster_type"] for cluster in clusters)

    print(f"Worksheet rebuilt: {OUTPUT_WORKSHEET_NAME}")
    print(f"Clusters generated: {len(clusters)}")
    print("Count by cluster_type:")
    for cluster_type, count in sorted(counts.items()):
        print(f"- {cluster_type}: {count}")

    print("Example 5 clusters:")
    for cluster in clusters[:5]:
        print({column: cluster[column] for column in CLUSTER_SIGNALS_HEADER})


def main():
    sheet = open_sheet()
    signals = read_signals(sheet)
    clusters = build_clusters(signals)
    validate_clusters(clusters, signals)
    write_clusters(sheet, clusters)
    print_summary(clusters)


if __name__ == "__main__":
    main()
