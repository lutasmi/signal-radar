"""Build the congress copy-trading tracker in Google Sheets.

Reads raw_congress_ptr, downloads/caches prices and writes:
* politician_scorecard      -> who beats the S&P 500 after their disclosures
* replica_holdings          -> current holdings of each copy portfolio (weights)
* copy_portfolio_summary    -> performance vs SPY per strategy
* copy_portfolio_nav        -> daily index per strategy vs SPY (chartable)
* copy_portfolio_positions  -> every simulated lot (open/closed) with returns
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account", "requests"])

from radar import tracker
from radar.prices import BENCHMARK, PriceStore

RAW_WORKSHEET_NAME = "raw_congress_ptr"

SCORECARD_HEADER = [
    "rank",
    "politician",
    "chamber",
    "party",
    "buys",
    "evaluated_trades",
    "avg_excess_30d",
    "avg_excess_90d",
    "median_excess_90d",
    "avg_excess_180d",
    "hit_rate",
    "avg_pre_disclosure_move",
    "avg_excess_since_entry",
    "conflict_buys",
    "option_buys",
    "tickers",
    "last_filing",
    "followed",
    "beats_market",
    "red_flags",
    "red_flag_reasons",
    "watchlist",
    "conflict_evaluated",
    "conflict_avg_excess",
    "other_avg_excess",
    "late_filing_pct",
    "politician_key",
]
GROUP_HEADER = [
    "group", "buys", "evaluated_90d", "avg_excess_30d", "avg_excess_90d", "median_excess_90d",
    "hit_rate_90d", "avg_excess_180d", "avg_pre_disclosure_move",
]
WATCHLIST_HEADER = [
    "red_flags", "politician", "chamber", "party", "red_flag_reasons", "conflict_evaluated",
    "conflict_avg_excess", "other_avg_excess", "avg_excess_30d", "avg_pre_disclosure_move",
    "late_filing_pct", "buys", "last_filing",
]
HOLDINGS_HEADER = ["strategy", "ticker", "weight_pct", "value_usd", "politicians", "since", "conflict", "options"]
SUMMARY_HEADER = [
    "strategy", "name", "start", "end", "total_return_pct", "benchmark_return_pct", "excess_pct",
    "cagr_pct", "max_drawdown_pct", "return_30d_pct", "benchmark_30d_pct", "return_1y_pct",
    "benchmark_1y_pct", "open_positions", "open_tickers", "closed_trades", "closed_hit_rate_pct",
]
POSITIONS_HEADER = [
    "strategy", "status", "ticker", "politician", "option", "conflict", "transaction_date",
    "filing_date", "entry_day", "entry_price", "exit_day", "exit_price", "exit_reason", "lot_usd",
    "current_value_usd", "weight_pct", "return_pct", "benchmark_pct", "excess_pct", "source_url",
]


def fmt(value, digits=2):
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, digits)
    return value


def scorecard_records(scorecard, config):
    followed = tracker.followed_keys(config)
    beaters = set(tracker.top_politicians(scorecard, config))
    records = []
    for row in scorecard:
        record = {column: fmt(row.get(column)) for column in SCORECARD_HEADER}
        record["followed"] = "YES" if row["politician_key"] in followed else ""
        record["beats_market"] = "YES" if row["politician_key"] in beaters else ""
        records.append(record)
    return records


def merged_nav(nav_by_strategy, prices):
    dates = sorted({row["date"] for rows in nav_by_strategy.values() for row in rows})
    lookup = {sid: {r["date"]: r["index"] for r in rows} for sid, rows in nav_by_strategy.items()}
    header = ["date"] + list(nav_by_strategy) + [BENCHMARK]
    records = []
    base = None
    from datetime import date as _date

    for day in dates:
        record = {"date": day}
        for sid in nav_by_strategy:
            record[sid] = lookup[sid].get(day, "")
        _, spy = prices.on_or_before(BENCHMARK, _date.fromisoformat(day))
        if spy and base is None:
            base = spy
        record[BENCHMARK] = round(spy / base * 100, 4) if spy and base else ""
        records.append(record)
    return header, records


def insider_watchlist(scorecard_rows):
    """Politicians with >= 2 red flags of a possible informational edge."""
    rows = [r for r in scorecard_rows if r.get("watchlist") == "YES"]
    rows.sort(key=lambda r: (-r["red_flags"], -(r.get("conflict_avg_excess") or -1e9)))
    return [{c: fmt(r.get(c)) for c in WATCHLIST_HEADER} for r in rows]


def build(raw_rows, prices, config):
    trades = tracker.tradeable_trades(raw_rows, config)
    scorecard = tracker.build_scorecard(trades, prices, config)
    tracker.mark_point_in_time_beaters(trades, prices, config)
    results = {}
    for strategy in config["portfolios"]:
        results[strategy["id"]] = tracker.simulate_portfolio(trades, prices, strategy, config)
    return trades, scorecard, results


def load_prices(trades, session, refresh=True):
    """Load prices; tickers not traded for >400 days only need their old window."""
    from datetime import timedelta

    from radar.prices import last_weekday

    prices = PriceStore(session=session, refresh=refresh)
    tickers = tracker.needed_tickers(trades)
    last_filing = {}
    for trade in trades:
        last_filing[trade["ticker"]] = max(last_filing.get(trade["ticker"], trade["filing_date"]), trade["filing_date"])
    yesterday = last_weekday(prices.today) - timedelta(days=1)
    need = {t: min(yesterday, last_filing[t] + timedelta(days=400)) for t in last_filing}
    print(f"Tickers needing prices: {len(tickers)}")
    refreshed = prices.prefetch(tickers, need)
    print(f"Tickers refreshed: {refreshed}; downloaded: {prices.downloads}; failed: {len(prices.failed)}")
    return prices


def build_digest(summaries, holdings, scorecard_records_, max_holdings=10):
    lines = ["📊 Resumen semanal · carteras réplica del Congreso", ""]
    for s in summaries:
        lines.append(
            f"{s['name']}: {s['total_return_pct']}% vs S&P 500 {s['benchmark_return_pct']}% "
            f"desde {s['start']} · 30d {s['return_30d_pct']}% (SPY {s['benchmark_30d_pct']}%) · "
            f"posiciones abiertas {s['open_positions']}"
        )
    main_id = summaries[0]["strategy"] if summaries else ""
    top = [h for h in holdings if h["strategy"] == main_id][:max_holdings]
    if top:
        lines += ["", f"Mayores posiciones de {summaries[0]['name']}:"]
        for h in top:
            flags = " ".join(f for f in ["⚠️conflicto" if h["conflict"] else "", "opciones" if h["options"] else ""] if f)
            lines.append(f"• {h['ticker']} {h['weight_pct']}% — {h['politicians'][:60]} {flags}".rstrip())
    ranked = [r for r in scorecard_records_ if r.get("rank")][:5]
    if ranked:
        lines += ["", "Top políticos (exceso medio a 90 días vs S&P 500):"]
        for r in ranked:
            lines.append(f"{r['rank']}. {r['politician']}: {r['avg_excess_90d']}% en {r['evaluated_trades']} compras, aciertos {r['hit_rate']}%")
    lines += ["", "Simulación con datos públicos; no es una recomendación de inversión."]
    return "\n".join(lines)


def build_report(summaries, groups, watchlist, holdings, alert_rows, today):
    """Weekly markdown status report (committed to reports/ for follow-up reviews)."""
    from datetime import date, timedelta

    lines = [f"# Congress tracker · informe semanal {today}", ""]
    lines += ["## Carteras réplica vs S&P 500", "",
              "| Cartera | Desde | Total | S&P 500 | 30 días | S&P 500 30d | Abiertas |", "|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s['name']} | {s['start']} | {s['total_return_pct']}% | {s['benchmark_return_pct']}% | "
            f"{s['return_30d_pct']}% | {s['benchmark_30d_pct']}% | {s['open_positions']} |"
        )
    lines += ["", "## Grupos de compras vs S&P 500 (exceso medio)", "",
              "| Grupo | n (90d) | 30d | 90d | aciertos 90d | 180d | movimiento previo |", "|---|---|---|---|---|---|---|"]
    for g in groups:
        lines.append(
            f"| {g['group']} | {g['evaluated_90d']} | {fmt(g['avg_excess_30d'])} | {fmt(g['avg_excess_90d'])} | "
            f"{fmt(g['hit_rate_90d'])} | {fmt(g['avg_excess_180d'])} | {fmt(g['avg_pre_disclosure_move'])} |"
        )
    lines += ["", f"## Lista de vigilancia ({len(watchlist)})", ""]
    for w in watchlist:
        lines.append(f"- [{w['red_flags']}] {w['politician']} ({w['party']}, {w['chamber']}): {w['red_flag_reasons']}")
    main_id = summaries[0]["strategy"] if summaries else ""
    lines += ["", "## Mayores posiciones de la cartera principal", ""]
    for h in [h for h in holdings if h["strategy"] == main_id][:15]:
        lines.append(f"- {h['ticker']} {h['weight_pct']}% — {h['politicians'][:80]}")
    week_ago = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    recent = [r for r in alert_rows if (r.get("sent_at") or "")[:10] > week_ago]
    lines += ["", f"## Alertas enviadas los últimos 7 días: {len(recent)}", ""]
    for r in recent[-30:]:
        lines.append(f"- {r.get('sent_at', '')[:16]} · {r.get('item_id', '')} · {r.get('score_band', '')}")
    lines += ["", "_Simulación con datos públicos; no es una recomendación de inversión._", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", default="", help="Write the weekly markdown report to this path.")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print, do not write Sheets.")
    parser.add_argument("--digest", action="store_true", help="Also send the weekly Telegram digest.")
    parser.add_argument("--no-refresh", action="store_true", help="Use cached prices only.")
    args = parser.parse_args()

    import requests
    from dotenv import load_dotenv

    from radar.sheets import open_sheet, read_records, replace_worksheet

    load_dotenv()
    config = tracker.load_tracker_config()
    sheet = open_sheet()
    raw_rows = read_records(sheet, RAW_WORKSHEET_NAME)
    print(f"Raw PTR rows: {len(raw_rows)}")
    trades = tracker.tradeable_trades(raw_rows, config)
    prices = load_prices(trades, requests.Session(), refresh=not args.no_refresh)
    trades, scorecard, results = build(raw_rows, prices, config)

    holdings, summaries, positions, navs = [], [], [], {}
    for sid, (strategy_positions, nav_rows, summary) in results.items():
        if summary:
            summaries.append({c: summary.get(c, "") for c in SUMMARY_HEADER})
        positions.extend(strategy_positions)
        navs[sid] = nav_rows
        for row in tracker.holdings_by_ticker(strategy_positions):
            holdings.append({"strategy": sid, **row})

    for s in summaries:
        print(
            f"{s['strategy']}: {s['total_return_pct']}% vs SPY {s['benchmark_return_pct']}% "
            f"({s['start']}..{s['end']}), open {s['open_positions']}"
        )
    print(f"Scorecard politicians: {len(scorecard)}")
    groups = tracker.group_comparison(trades, prices, config)
    print("\nGroup comparison vs S&P 500 (excess return, %):")
    for g in groups:
        print(
            f"- {g['group']}: n={g['evaluated_90d']} | 30d {fmt(g['avg_excess_30d'])} | 90d {fmt(g['avg_excess_90d'])} "
            f"(mediana {fmt(g['median_excess_90d'])}, aciertos {fmt(g['hit_rate_90d'])}%) | 180d {fmt(g['avg_excess_180d'])} "
            f"| pre-disclosure {fmt(g['avg_pre_disclosure_move'])}"
        )
    watchlist = insider_watchlist(scorecard)
    print(f"\nInsider watchlist (>=2 red flags incl. a performance flag): {len(watchlist)}")
    for w in watchlist[:20]:
        print(f"- [{w['red_flags']}] {w['politician']} ({w['party']}, {w['chamber']}): {w['red_flag_reasons']} | conflicto {w['conflict_avg_excess']} vs resto {w['other_avg_excess']} (n={w['conflict_evaluated']})")
    if args.dry_run:
        return

    replace_worksheet(sheet, "politician_scorecard", SCORECARD_HEADER, scorecard_records(scorecard, config))
    replace_worksheet(sheet, "replica_holdings", HOLDINGS_HEADER, holdings)
    replace_worksheet(sheet, "insider_analysis", GROUP_HEADER, [{c: fmt(g.get(c)) for c in GROUP_HEADER} for g in groups])
    replace_worksheet(sheet, "insider_watchlist", WATCHLIST_HEADER, watchlist)
    replace_worksheet(sheet, "copy_portfolio_summary", SUMMARY_HEADER, summaries)
    nav_header, nav_records = merged_nav(navs, prices)
    if nav_records:
        replace_worksheet(sheet, "copy_portfolio_nav", nav_header, nav_records)
    replace_worksheet(
        sheet,
        "copy_portfolio_positions",
        POSITIONS_HEADER,
        [{c: p.get(c, "") for c in POSITIONS_HEADER} for p in positions],
    )
    print("Tracker sheets written")

    if args.report:
        from datetime import date as _date

        from radar.sheets import read_records_or_empty

        report = build_report(
            summaries, groups, watchlist, holdings,
            read_records_or_empty(sheet, "telegram_alert_log"), _date.today().isoformat(),
        )
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"Weekly report written: {args.report}")

    if args.digest:
        from scripts import send_telegram_alerts as telegram

        message = build_digest(summaries, holdings, scorecard_records(scorecard, config))
        agent_config = config.get("agent", {})
        if agent_config.get("weekly_memo"):
            from radar.agent import AgentClient

            from radar import agent as agent_module

            client = AgentClient(
                requests.Session(), agent_config, spent_this_month=agent_module.month_spend(sheet)
            )
            if client.enabled:
                try:
                    memo = client.weekly_memo(message)
                    if memo:
                        message = f"{message}\n\n🤖 Comentario:\n{memo}"
                except Exception as exc:
                    print(f"WARNING: weekly memo failed: {exc}")
                agent_module.record_usage(sheet, client, "weekly_memo")
        token, chat_id = telegram.telegram_credentials()
        telegram.send_telegram_message(token, chat_id, message[:4000])
        print("Weekly digest sent")


if __name__ == "__main__":
    main()
