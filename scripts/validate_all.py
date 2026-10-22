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

from collectors import collect_congress_ptr
from radar import conflicts
from scripts import (
    send_congress_alerts,
    build_cluster_signals_sheet,
    build_correlation_signals_sheet,
    build_priority_signals_sheet,
    build_review_queue_sheet,
    build_signals_sheet,
    send_capture_summary,
    send_telegram_alerts,
    validate_candidate_sources,
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


HOUSE_PTR_FIXTURES = {
    # Official House Clerk PTR PDFs (public records) used as parser fixtures.
    "20035551.pdf": {
        "count": 6,
        "expected": [
            ("JT", "DDOG", "buy", "$50,001 - $100,000"),
            ("JT", "EL", "buy", "$50,001 - $100,000"),
            ("JT", "GH", "buy", "$15,001 - $50,000"),
            ("SP", "TYG", "sell", "$1,001 - $15,000"),
            ("JT", "VSAT", "sell", "$50,001 - $100,000"),
            ("JT", "WMT", "sell", "$50,001 - $100,000"),
        ],
    },
    "20035660.pdf": {
        "count": 4,
        "expected": [
            ("Self", "T", "buy", "$1,001 - $15,000"),
            ("Self", "FITB", "buy", "$1,001 - $15,000"),
            ("Self", "PG", "sell", "$1,001 - $15,000"),
            ("Self", "RPM", "buy", "$1,001 - $15,000"),
        ],
    },
    # Multi-page report with an asset name split across a page break.
    "20035408.pdf": {"count": 78, "expected": None},
}


def validate_congress_ptr_parsing():
    fixture_dir = PROJECT_ROOT / FIXTURE_CSV_DIR / "congress_ptr"
    total = 0
    for file_name, spec in HOUSE_PTR_FIXTURES.items():
        parsed = collect_congress_ptr.parse_house_ptr_pdf((fixture_dir / file_name).read_bytes())
        if len(parsed) != spec["count"]:
            raise ValueError(f"House PTR parser: {file_name} expected {spec['count']} rows, got {len(parsed)}")
        actual = [(tx["owner"], tx["ticker"], tx["trade_type"], tx["amount"]) for tx in parsed]
        if spec["expected"] is not None and actual != spec["expected"]:
            raise ValueError(
                f"House PTR parser mismatch for {file_name}:\n"
                f"expected: {spec['expected']}\nactual:   {actual}"
            )
        for tx in parsed:
            problems = []
            if not tx["asset_type"]:
                problems.append("missing asset type")
            if tx["asset_type"] == "ST" and not tx["ticker"]:
                problems.append("stock without ticker")
            if tx["trade_type"] == "other" or tx["transaction_date"] is None:
                problems.append("bad type/date")
            if not tx["amount"].startswith("$") or ":" in tx["asset_name"]:
                problems.append("bad amount or label noise")
            if problems:
                raise ValueError(f"House PTR parser {file_name}: {problems}: {tx}")
        total += len(parsed)
    print(f"OK congress_ptr_parser: {total} transactions from official House PTR PDFs")
    return total


def validate_tracker_logic():
    """Copy portfolio + scorecard on synthetic prices with a known answer."""
    from datetime import date, timedelta

    from radar import tracker
    from radar.prices import PriceStore

    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(0, 60) if (date(2026, 1, 5) + timedelta(days=i)).weekday() < 5]
    spy = [(d, 100.0) for d in days]
    # XYZ: 100 until Jan 9, then +1 per trading day.
    xyz = [(d, 100.0 + max(0, i - 4)) for i, d in enumerate(days)]
    prices = PriceStore(histories={"SPY": spy, "XYZ": xyz}, today=days[-1])
    config = tracker.load_tracker_config()
    raw = [
        {"politician": "A", "bioguide_id": "P000197", "ticker": "XYZ", "asset_type": "ST", "trade_type": "buy",
         "transaction_date": "2026-01-05", "filing_date": "2026-01-08", "amount": "$15,001 - $50,000",
         "conflict": "NO", "conflict_score": "0", "filing_id": "1", "source_url": "u"},
        {"politician": "A", "bioguide_id": "P000197", "ticker": "XYZ", "asset_type": "ST", "trade_type": "sell",
         "transaction_date": "2026-01-20", "filing_date": "2026-01-22", "amount": "$15,001 - $50,000",
         "conflict": "NO", "conflict_score": "0", "filing_id": "2", "source_url": "u"},
    ]
    trades = tracker.tradeable_trades(raw, config)
    strategy = next(s for s in config["portfolios"] if s["id"] == "pelosi")
    positions, nav, summary = tracker.simulate_portfolio(trades, prices, strategy, config)
    if len(positions) != 1 or positions[0]["status"] != "CLOSED":
        raise ValueError(f"tracker: expected one closed lot, got {positions}")
    lot = positions[0]
    # Entry: first trading day after Jan 8 (Jan 9, price 100); exit: Jan 23 (price 110).
    if (lot["entry_day"], lot["entry_price"], lot["exit_day"], lot["exit_price"]) != ("2026-01-09", 100.0, "2026-01-23", 110.0):
        raise ValueError(f"tracker: wrong entry/exit timing {lot}")
    if lot["return_pct"] != 10.0 or lot["excess_pct"] != 10.0:
        raise ValueError(f"tracker: wrong lot return {lot}")
    if abs(summary["total_return_pct"] - 10.0) > 1e-6 or summary["benchmark_return_pct"] != 0.0:
        raise ValueError(f"tracker: wrong NAV summary {summary}")
    scorecard = tracker.build_scorecard(trades, prices, config)
    if scorecard[0]["buys"] != 1 or round(scorecard[0]["avg_excess_30d"], 2) != 20.0:
        raise ValueError(f"tracker: wrong scorecard {scorecard[0]}")
    print("OK tracker_logic: no look-ahead entry/exit, NAV vs SPY, scorecard")
    return 1


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, ""

    def json(self):
        return self.payload


class _FakeAnthropic:
    """Scripted Messages API: pause_turn -> answer without tool -> record_review."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.requests = []

    def post(self, url, headers=None, json=None, timeout=None):
        import copy

        self.requests.append(copy.deepcopy(json))
        return _FakeResponse(self.scripted.pop(0))


def validate_agent_logic():
    import tempfile

    from radar.agent import AgentClient

    usage = {"input_tokens": 10, "output_tokens": 5, "server_tool_use": {"web_search_requests": 1}}
    review = {"ticker": "LMT", "suspicion": 4, "thesis": "t", "evidence": "e", "catalysts": "", "sources": ["https://x"]}
    fake = _FakeAnthropic(
        [
            {"stop_reason": "pause_turn", "content": [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}}], "usage": usage},
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": "notes"}], "usage": usage},
            {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "record_review", "input": {"assessments": [review]}}], "usage": usage},
        ]
    )
    client = AgentClient(fake, {"model": "m"}, key="test")
    result = client.review_trades("ctx", ["LMT"])
    if result != [review] or len(fake.requests) != 3:
        raise ValueError(f"agent: review loop failed {result}")
    if fake.requests[1]["messages"][-1]["role"] != "assistant":
        raise ValueError("agent: pause_turn content must be sent back unchanged")
    if client.usage["web_search_requests"] != 3:
        raise ValueError("agent: usage accounting")

    extraction = {"is_periodic_transaction_report": True, "filer_name": "X", "notes": "", "transactions": [
        {"owner": "SP", "asset_name": "Lockheed Martin (LMT)", "ticker": "LMT", "asset_type": "ST", "trade_type": "buy",
         "transaction_date": "2026-09-01", "notification_date": "", "amount": "$1,001-$15,000"}]}
    import json as _json
    fake = _FakeAnthropic([{"stop_reason": "end_turn", "content": [{"type": "text", "text": _json.dumps(extraction)}], "usage": usage}])
    client = AgentClient(fake, {}, key="test")
    with tempfile.TemporaryDirectory() as tmp:
        first = client.extract_scanned_ptr(b"%PDF-1.4", "X", "9000001", cache_dir=tmp)
        second = client.extract_scanned_ptr(b"%PDF-1.4", "X", "9000001", cache_dir=tmp)
    if first != second or len(fake.requests) != 1:
        raise ValueError("agent: vision extraction must be cached per DocID")
    if fake.requests[0]["output_config"]["format"]["type"] != "json_schema":
        raise ValueError("agent: extraction must use structured outputs")
    txs = collect_congress_ptr.vision_transactions(first)
    if txs[0]["trade_type"] != "buy" or txs[0]["amount"] != "$1,001 - $15,000" or str(txs[0]["transaction_date"]) != "2026-09-01":
        raise ValueError(f"agent: vision rows not normalized {txs}")
    if AgentClient(None, {}, key="").enabled:
        raise ValueError("agent: must be disabled without API key")
    print("OK agent_logic: review loop (pause_turn/tool), cached vision extraction, graceful disable")
    return 1


def validate_conflict_logic():
    config = conflicts.load_config()
    resolver = conflicts.SectorResolver(config, session=None)
    if "defense" not in resolver.resolve("LMT")["sectors"]:
        raise ValueError("LMT must resolve to defense")
    if resolver.sectors_for_sic("3812") != ["defense"]:
        raise ValueError("SIC 3812 must map to defense")
    armed_services_chair = [
        {"id": "SSAS", "name": "Senate Committee on Armed Services", "title": "Chairman"},
        {"id": "SSAS13", "name": "Senate Committee on Armed Services - Seapower", "title": "Ex Officio"},
    ]
    agriculture = [{"id": "HSAG", "name": "House Committee on Agriculture", "title": ""}]
    flagged = conflicts.evaluate_conflict(
        config, armed_services_chair, ["defense"], "buy", "$250,001 - $500,000", 60
    )
    if flagged["conflict"] != "YES" or flagged["conflict_score"] != 95:
        raise ValueError(f"Armed Services chair buying defense must score 95: {flagged}")
    clean = conflicts.evaluate_conflict(config, agriculture, ["defense"], "buy", "$1,001 - $15,000", 3)
    if clean["conflict"] != "NO":
        raise ValueError("Agriculture member buying defense must not be flagged")
    directory = conflicts.CongressDirectory(
        legislators=[
            {
                "id": {"bioguide": "X1"},
                "name": {"first": "Roger", "last": "Wicker", "official_full": "Roger F. Wicker"},
                "terms": [{"type": "sen", "state": "MS", "party": "Republican"}],
            },
            {
                "id": {"bioguide": "X2"},
                "name": {"first": "Jonathan", "last": "Jackson"},
                "terms": [{"type": "rep", "state": "IL", "district": 1, "party": "Democrat"}],
            },
        ],
        committees=[{"thomas_id": "SSAS", "name": "Senate Committee on Armed Services", "subcommittees": []}],
        membership={"SSAS": [{"bioguide": "X1", "title": "Chairman"}]},
    )
    if directory.find_senator("Roger F", "Wicker")["bioguide"] != "X1":
        raise ValueError("Senator lookup by name failed")
    if directory.find_house_member("IL01")["bioguide"] != "X2":
        raise ValueError("House lookup by state/district failed")
    send_congress_alerts.validate_logic()
    print("OK conflict_logic: committee/sector matching, scoring and alert grouping")
    return 1


def load_fixture_worksheets(csv_dir):
    worksheets = {}
    for name, csv_filename, _, _ in validate_pipeline.CSV_SPECS:
        csv_file = validate_pipeline.source_csv_file(csv_dir, name, csv_filename)
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
    send_capture_summary.validate_capture_summary_logic()
    print("OK capture_summary_logic: message generation and failure reporting")
    summary.append(("capture_summary_logic", 1))
    summary.append(("congress_ptr_parser", validate_congress_ptr_parsing()))
    summary.append(("conflict_logic", validate_conflict_logic()))
    summary.append(("tracker_logic", validate_tracker_logic()))
    summary.append(("agent_logic", validate_agent_logic()))
    candidate_summary = validate_candidate_sources.validate_candidate_sources()
    summary.append(("candidate_sources", len(candidate_summary)))
    if csv_dir == validate_pipeline.resolve_csv_dir(GENERATED_CSV_DIR):
        generated_candidate_summary = (
            validate_candidate_sources.validate_generated_candidate_sources(csv_dir)
        )
        summary.append(("generated_candidate_sources", len(generated_candidate_summary)))

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
