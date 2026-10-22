"""Congress copy-trading logic: trades, politician scorecard, simulated portfolios.

Everything here is pure and deterministic given raw PTR rows and a PriceStore,
so it can be validated offline with synthetic prices.

Timing convention (no look-ahead): a trade becomes actionable the first
trading day AFTER its filing date, at that day's close.
"""

import bisect
import json
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from radar.prices import BENCHMARK, pct

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACKER_CONFIG = PROJECT_ROOT / "config" / "tracker.json"

BUY_TYPES = {"buy"}
SELL_TYPES = {"sell", "sell_partial"}


def load_tracker_config(path=TRACKER_CONFIG):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def _d(value):
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def amount_midpoint(amount):
    numbers = [int(n.replace(",", "")) for n in re.findall(r"\$\s*([\d,]+)", amount or "")]
    if not numbers:
        return 0
    if len(numbers) == 1:
        return numbers[0]
    return (numbers[0] + numbers[1]) // 2


def politician_key(row):
    return row.get("bioguide_id") or row.get("politician", "")


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


def tradeable_trades(raw_rows, config):
    """Normalize raw_congress_ptr rows into stock trades usable for tracking."""
    asset_types = set(config.get("tradeable_asset_types", []))
    trades = []
    for row in raw_rows:
        ticker = (row.get("ticker") or "").strip().upper()
        trade_type = (row.get("trade_type") or "").strip()
        if not ticker or trade_type not in BUY_TYPES | SELL_TYPES:
            continue
        if asset_types and row.get("asset_type") and row.get("asset_type") not in asset_types:
            continue
        filing = _d(row.get("filing_date"))
        transaction = _d(row.get("transaction_date"))
        if filing is None:
            continue
        trades.append(
            {
                "key": politician_key(row),
                "politician": row.get("politician", ""),
                "bioguide_id": row.get("bioguide_id", ""),
                "chamber": row.get("chamber", ""),
                "party": row.get("party", ""),
                "ticker": ticker,
                "asset_type": row.get("asset_type", ""),
                "is_option": row.get("asset_type") in ("OP", "Stock Option"),
                "trade_type": trade_type,
                "transaction_date": transaction,
                "filing_date": filing,
                "amount": row.get("amount", ""),
                "amount_mid": amount_midpoint(row.get("amount", "")),
                "conflict": row.get("conflict", "") == "YES",
                "conflict_score": int(row.get("conflict_score") or 0),
                "leadership": "cargo de liderazgo" in (row.get("conflict_reason") or ""),
                "delay": int(row.get("disclosure_delay_days") or 0) if str(row.get("disclosure_delay_days") or "").lstrip("-").isdigit() else None,
                "filing_id": row.get("filing_id", ""),
                "source_url": row.get("source_url", ""),
            }
        )
    trades.sort(key=lambda t: (t["filing_date"], t["transaction_date"] or t["filing_date"], t["key"], t["ticker"]))
    # Amended reports repeat transactions under a new filing id: keep the first.
    unique, seen = [], set()
    for trade in trades:
        signature = (trade["key"], trade["ticker"], trade["trade_type"], trade["transaction_date"], trade["amount"], trade["asset_type"])
        if trade["transaction_date"] and signature in seen:
            continue
        seen.add(signature)
        unique.append(trade)
    return unique


def needed_tickers(trades):
    return sorted({t["ticker"] for t in trades} | {BENCHMARK})


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


def trade_returns(trade, prices, horizons):
    """Return metrics for one buy: pre-disclosure move and excess returns."""
    ticker = trade["ticker"]
    entry_day, entry_price = prices.after(ticker, trade["filing_date"])
    result = {"entry_day": entry_day, "entry_price": entry_price, "pre_move": None}
    if trade["transaction_date"]:
        _, tx_price = prices.on_or_after(ticker, trade["transaction_date"])
        if entry_price and tx_price:
            result["pre_move"] = pct(tx_price, entry_price)
    if not entry_price:
        return result
    last_day, last_price = prices.latest(ticker)
    _, spy_entry = prices.on_or_before(BENCHMARK, entry_day)
    for horizon in horizons:
        target = entry_day + timedelta(days=horizon)
        if last_day is None or target > last_day:
            result[f"excess_{horizon}"] = None
            continue
        _, exit_price = prices.on_or_before(ticker, target)
        _, spy_exit = prices.on_or_before(BENCHMARK, target)
        stock = pct(entry_price, exit_price)
        bench = pct(spy_entry, spy_exit)
        result[f"excess_{horizon}"] = None if stock is None or bench is None else stock - bench
    result["since_entry"] = pct(entry_price, last_price)
    _, spy_last = prices.latest(BENCHMARK)
    bench_since = pct(spy_entry, spy_last)
    result["excess_since_entry"] = (
        None if result["since_entry"] is None or bench_since is None else result["since_entry"] - bench_since
    )
    return result


def _avg(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


DEFAULT_INSIDER_FLAGS = {
    "min_conflict_trades": 5,
    "conflict_edge_pts": 2.0,
    "min_pre_move_trades": 10,
    "pre_move_pct": 1.5,
    "late_filing_days": 45,
    "late_filing_pct": 20.0,
    "min_short_term_trades": 10,
    "short_term_excess_pct": 1.5,
}


def insider_indicators(buys, metrics, horizon, config):
    """Red flags that hint at an informational edge (not proof of anything).

    1. Conflict edge: buys in sectors their committees oversee beat the S&P 500 and
       beat their own other buys by a margin.
    2. Pre-disclosure move: stocks tend to rise between the trade and its disclosure.
    3. Late filer: frequent disclosures after the STOCK Act 45-day limit.
    4. Short-term edge: strong 30-day excess return (information tends to be short-lived).
    """
    rules = {**DEFAULT_INSIDER_FLAGS, **config.get("insider_flags", {})}
    key = f"excess_{horizon}"
    conflict = [m[key] for t, m in zip(buys, metrics) if t["conflict"] and m.get(key) is not None]
    other = [m[key] for t, m in zip(buys, metrics) if not t["conflict"] and m.get(key) is not None]
    pre = [m["pre_move"] for m in metrics if m.get("pre_move") is not None]
    short = [m["excess_30"] for m in metrics if m.get("excess_30") is not None]
    delays = [t["delay"] for t in buys if t.get("delay") is not None]
    late_pct = sum(1 for d in delays if d > rules["late_filing_days"]) / len(delays) * 100 if delays else None
    conflict_avg, other_avg = _avg(conflict), _avg(other)
    flags = []
    if (
        len(conflict) >= rules["min_conflict_trades"] and conflict_avg is not None and conflict_avg > 0
        and conflict_avg - (other_avg or 0) > rules["conflict_edge_pts"]
    ):
        flags.append("compras en sectores de su comité baten al mercado")
    if len(pre) >= rules["min_pre_move_trades"] and _avg(pre) > rules["pre_move_pct"]:
        flags.append("la acción sube antes de hacerse pública la compra")
    if late_pct is not None and len(delays) >= 5 and late_pct >= rules["late_filing_pct"]:
        flags.append("declara tarde con frecuencia")
    if len(short) >= rules["min_short_term_trades"] and _avg(short) > rules["short_term_excess_pct"]:
        flags.append("ventaja fuerte a 30 días")
    return {
        "conflict_evaluated": len(conflict),
        "conflict_avg_excess": conflict_avg,
        "other_avg_excess": other_avg,
        "late_filing_pct": late_pct,
        "red_flags": len(flags),
        "red_flag_reasons": "; ".join(flags),
    }


def group_comparison(trades, prices, config):
    """Compare buy groups vs the S&P 500: are 'informed-looking' buys any better?"""
    horizons = config["scorecard"]["horizons_days"]
    buys = [t for t in trades if t["trade_type"] in BUY_TYPES and prices.has(t["ticker"])]
    metrics = {id(t): trade_returns(t, prices, horizons) for t in buys}
    groups = [
        ("Todas las compras del Congreso", lambda t: True),
        ("Compras SIN relación con su comité", lambda t: not t["conflict"]),
        ("Compras en sectores de su comité (conflicto)", lambda t: t["conflict"]),
        ("Conflicto + preside/ranking del comité", lambda t: t["conflict"] and t["leadership"]),
        ("Conflicto + importe >= 50.001 $", lambda t: t["conflict"] and t["amount_mid"] >= 50001),
        ("Compras declaradas tarde (> 45 días)", lambda t: (t.get("delay") or 0) > 45),
        ("Compras de opciones", lambda t: t["is_option"]),
        ("Compras >= 250.001 $", lambda t: t["amount_mid"] >= 250001),
    ]
    rows = []
    for name, predicate in groups:
        selected = [metrics[id(t)] for t in buys if predicate(t)]
        row = {"group": name, "buys": len(selected)}
        for horizon in horizons:
            values = [m.get(f"excess_{horizon}") for m in selected]
            values = [v for v in values if v is not None]
            row[f"evaluated_{horizon}d"] = len(values)
            row[f"avg_excess_{horizon}d"] = _avg(values)
            row[f"median_excess_{horizon}d"] = _median(values)
            row[f"hit_rate_{horizon}d"] = sum(1 for v in values if v > 0) / len(values) * 100 if values else None
        row["avg_pre_disclosure_move"] = _avg(m.get("pre_move") for m in selected)
        rows.append(row)
    return rows


def build_scorecard(trades, prices, config):
    horizons = config["scorecard"]["horizons_days"]
    min_eval = config["scorecard"]["min_evaluated_trades"]
    by_politician = defaultdict(list)
    for trade in trades:
        if trade["trade_type"] in BUY_TYPES:
            by_politician[trade["key"]].append(trade)

    rows = []
    for key, buys in by_politician.items():
        metrics = [trade_returns(t, prices, horizons) for t in buys]
        main = horizons[1] if len(horizons) > 1 else horizons[0]
        evaluated = [m[f"excess_{main}"] for m in metrics if m.get(f"excess_{main}") is not None]
        row = {
            "politician_key": key,
            "politician": buys[-1]["politician"],
            "chamber": buys[-1]["chamber"],
            "party": buys[-1]["party"],
            "buys": len(buys),
            "conflict_buys": sum(1 for t in buys if t["conflict"]),
            "option_buys": sum(1 for t in buys if t["is_option"]),
            "tickers": len({t["ticker"] for t in buys}),
            "last_filing": max(t["filing_date"] for t in buys).isoformat(),
            "avg_pre_disclosure_move": _avg(m["pre_move"] for m in metrics),
            "evaluated_trades": len(evaluated),
            "hit_rate": (sum(1 for v in evaluated if v > 0) / len(evaluated) * 100) if evaluated else None,
            "avg_excess_since_entry": _avg(m.get("excess_since_entry") for m in metrics),
        }
        for horizon in horizons:
            row[f"avg_excess_{horizon}d"] = _avg(m.get(f"excess_{horizon}") for m in metrics)
            row[f"median_excess_{horizon}d"] = _median(m.get(f"excess_{horizon}") for m in metrics)
        row.update(insider_indicators(buys, metrics, main, config))
        row["ranked"] = "YES" if len(evaluated) >= min_eval else "NO"
        rows.append(row)

    main = horizons[1] if len(horizons) > 1 else horizons[0]
    rows.sort(
        key=lambda r: (
            r["ranked"] != "YES",
            -(r[f"avg_excess_{main}d"] if r[f"avg_excess_{main}d"] is not None else -1e9),
            r["politician"],
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank if row["ranked"] == "YES" else ""
    return rows


DEFAULT_RANKING_RULE = {"horizon_days": 90, "min_evaluated_trades": 10, "min_avg_excess": 0.0, "min_hit_rate": 50.0}


def ranking_rule(config):
    return {**DEFAULT_RANKING_RULE, **config.get("ranking_rule", {})}


def beats_market(evaluated, avg_excess, hit_rate, rule):
    """The single definition of 'this politician beats the market' (used everywhere)."""
    return (
        evaluated >= rule["min_evaluated_trades"]
        and avg_excess is not None and avg_excess > rule["min_avg_excess"]
        and hit_rate is not None and hit_rate >= rule["min_hit_rate"]
    )


def top_politicians(scorecard, config):
    """Politicians that beat the market today, best first."""
    rule = ranking_rule(config)
    horizon = rule["horizon_days"]
    return [
        row["politician_key"]
        for row in scorecard
        if beats_market(row["evaluated_trades"], row.get(f"avg_excess_{horizon}d"), row.get("hit_rate"), rule)
    ]


def mark_point_in_time_beaters(trades, prices, config):
    """Flag each trade with trade["beater"]: did its politician beat the market
    using ONLY outcomes already known on the filing date? (no look-ahead bias)

    An earlier buy's outcome is known `horizon_days` after its entry day.
    """
    rule = ranking_rule(config)
    horizon = rule["horizon_days"]
    history = defaultdict(list)  # politician -> [(known_date, excess)]
    for trade in trades:
        if trade["trade_type"] not in BUY_TYPES or not prices.has(trade["ticker"]):
            continue
        result = trade_returns(trade, prices, [horizon])
        excess = result.get(f"excess_{horizon}")
        if excess is not None and result["entry_day"]:
            history[trade["key"]].append((result["entry_day"] + timedelta(days=horizon), excess))
    known = {}
    for key, items in history.items():
        items.sort()
        known[key] = ([d for d, _ in items], [e for _, e in items])
    for trade in trades:
        dates, excesses = known.get(trade["key"], ([], []))
        sample = excesses[: bisect.bisect_left(dates, trade["filing_date"])]
        avg = statistics.fmean(sample) if sample else None
        hit = sum(1 for e in sample if e > 0) / len(sample) * 100 if sample else None
        trade["beater"] = beats_market(len(sample), avg, hit, rule)
    return trades


# ---------------------------------------------------------------------------
# Portfolio simulation
# ---------------------------------------------------------------------------


def followed_keys(config):
    return {p["bioguide"] for p in config.get("followed_politicians", [])}


def trade_included(trade, rules, followed):
    for rule in rules:
        if rule == "all":
            return True
        if rule == "followed" and trade["bioguide_id"] in followed:
            return True
        if rule == "conflict" and trade["conflict"]:
            return True
        if rule == "beaters" and trade.get("beater"):
            return True
        if rule == "leadership_conflict" and trade["conflict"] and trade.get("leadership"):
            return True
        if rule.startswith("bioguide:") and trade["bioguide_id"] == rule.split(":", 1)[1]:
            return True
    return False


def simulate_portfolio(trades, prices, strategy, config, start=None):
    """Simulate a portfolio that copies disclosed buys and exits on disclosed sells.

    Returns (positions, nav_rows, summary). NAV is a time-weighted index (100 at
    start) so capital added by new disclosures does not distort returns.
    """
    followed = followed_keys(config)
    max_lot = strategy.get("max_lot_usd") or 10 ** 12
    max_hold = timedelta(days=strategy.get("max_hold_days", 365))
    calendar = prices.dates(BENCHMARK)
    if not calendar:
        return [], [], {}

    # Build entry and exit events keyed by the trading day they execute.
    lots = []
    entries = defaultdict(list)
    for trade in trades:
        if trade["trade_type"] not in BUY_TYPES or not trade_included(trade, strategy["include"], followed):
            continue
        if start and trade["filing_date"] < start:
            continue
        if not prices.has(trade["ticker"]):
            continue
        day, price = prices.after(trade["ticker"], trade["filing_date"])
        if not price:
            continue
        lot_usd = max(1000, min(trade["amount_mid"], max_lot)) if strategy.get("weighting") == "amount" else 10000
        lot = {
            "strategy": strategy["id"],
            "politician": trade["politician"],
            "key": trade["key"],
            "ticker": trade["ticker"],
            "is_option": trade["is_option"],
            "conflict": trade["conflict"],
            "transaction_date": trade["transaction_date"],
            "filing_date": trade["filing_date"],
            "entry_day": day,
            "entry_price": price,
            "lot_usd": lot_usd,
            "units": 0.0,
            "exit_day": None,
            "exit_price": None,
            "exit_reason": "",
            "source_url": trade["source_url"],
        }
        lots.append(lot)
        entries[day].append(lot)

    sells = defaultdict(list)
    for trade in trades:
        if trade["trade_type"] in SELL_TYPES:
            day, _ = prices.after(trade["ticker"], trade["filing_date"]) if prices.has(trade["ticker"]) else (None, None)
            if day:
                sells[day].append(trade)

    nav_rows = []
    index = 100.0
    spy_start = None
    open_lots = []
    prev_value = None
    first_day = min(entries) if entries else None
    if first_day is None:
        return [], [], {}

    def price_at(ticker, day):
        return prices.on_or_before(ticker, day)[1]

    for day in calendar:
        if day < first_day:
            continue
        # 1) mark-to-market return of lots held since yesterday
        if open_lots and prev_value:
            value = sum(l["units"] * (price_at(l["ticker"], day) or 0) for l in open_lots)
            index *= value / prev_value if prev_value else 1.0
        # 2) exits at today's close
        still_open = []
        sells_today = sells.get(day, [])
        for lot in open_lots:
            reason = ""
            for sale in sells_today:
                if sale["key"] == lot["key"] and sale["ticker"] == lot["ticker"] and sale["filing_date"] >= lot["filing_date"]:
                    reason = "politician_sold" if sale["trade_type"] == "sell" else "politician_sold_partial"
                    break
            if not reason and day - lot["entry_day"] >= max_hold:
                reason = "max_hold"
            if reason:
                lot["exit_day"] = day
                lot["exit_price"] = price_at(lot["ticker"], day)
                lot["exit_reason"] = reason
            else:
                still_open.append(lot)
        open_lots = still_open
        # 3) entries at today's close
        for lot in entries.get(day, []):
            lot["units"] = lot["lot_usd"] / lot["entry_price"]
            open_lots.append(lot)
        prev_value = sum(l["units"] * (price_at(l["ticker"], day) or 0) for l in open_lots)
        spy = price_at(BENCHMARK, day)
        if spy_start is None:
            spy_start = spy
        nav_rows.append(
            {
                "date": day.isoformat(),
                "index": round(index, 4),
                "benchmark_index": round(100.0 * spy / spy_start, 4) if spy and spy_start else "",
                "open_positions": len(open_lots),
                "invested_usd": round(prev_value, 2),
            }
        )

    last_day = calendar[-1]
    positions = []
    total_open_value = sum(l["units"] * (price_at(l["ticker"], last_day) or 0) for l in open_lots)
    for lot in lots:
        current = lot["exit_price"] if lot["exit_day"] else price_at(lot["ticker"], last_day)
        ret = pct(lot["entry_price"], current)
        end_day = lot["exit_day"] or last_day
        spy_a = price_at(BENCHMARK, lot["entry_day"])
        spy_b = price_at(BENCHMARK, end_day)
        bench = pct(spy_a, spy_b)
        value = lot["units"] * (current or 0)
        positions.append(
            {
                "strategy": lot["strategy"],
                "status": "CLOSED" if lot["exit_day"] else "OPEN",
                "ticker": lot["ticker"],
                "politician": lot["politician"],
                "option": "YES" if lot["is_option"] else "",
                "conflict": "YES" if lot["conflict"] else "",
                "transaction_date": lot["transaction_date"].isoformat() if lot["transaction_date"] else "",
                "filing_date": lot["filing_date"].isoformat(),
                "entry_day": lot["entry_day"].isoformat(),
                "entry_price": round(lot["entry_price"], 4),
                "exit_day": lot["exit_day"].isoformat() if lot["exit_day"] else "",
                "exit_price": round(lot["exit_price"], 4) if lot["exit_price"] else "",
                "exit_reason": lot["exit_reason"],
                "lot_usd": lot["lot_usd"],
                "current_value_usd": round(value, 2),
                "weight_pct": round(value / total_open_value * 100, 2) if not lot["exit_day"] and total_open_value else "",
                "return_pct": round(ret, 2) if ret is not None else "",
                "benchmark_pct": round(bench, 2) if bench is not None else "",
                "excess_pct": round(ret - bench, 2) if ret is not None and bench is not None else "",
                "source_url": lot["source_url"],
            }
        )
    positions.sort(key=lambda p: (p["status"] != "OPEN", -(p["current_value_usd"] or 0) if p["status"] == "OPEN" else 0, p["entry_day"]), reverse=False)

    summary = summarize_nav(nav_rows, strategy, positions)
    return positions, nav_rows, summary


def summarize_nav(nav_rows, strategy, positions):
    if not nav_rows:
        return {}
    first, last = nav_rows[0], nav_rows[-1]
    peak = 0.0
    max_dd = 0.0
    for row in nav_rows:
        peak = max(peak, row["index"])
        max_dd = min(max_dd, (row["index"] / peak - 1) * 100 if peak else 0)
    days = (date.fromisoformat(last["date"]) - date.fromisoformat(first["date"])).days or 1
    total = last["index"] / first["index"] * 100 - 100
    bench_total = (last["benchmark_index"] / first["benchmark_index"] * 100 - 100) if first["benchmark_index"] else None
    years = days / 365.25
    cagr = ((last["index"] / first["index"]) ** (1 / years) - 1) * 100 if years >= 0.25 else None

    def window(days_back):
        target = date.fromisoformat(last["date"]) - timedelta(days=days_back)
        base = None
        for row in nav_rows:
            if date.fromisoformat(row["date"]) <= target:
                base = row
        if base is None:
            return None, None
        r = last["index"] / base["index"] * 100 - 100
        b = last["benchmark_index"] / base["benchmark_index"] * 100 - 100 if base["benchmark_index"] else None
        return r, b

    r30, b30 = window(30)
    r365, b365 = window(365)
    open_positions = [p for p in positions if p["status"] == "OPEN"]
    closed = [p for p in positions if p["status"] == "CLOSED" and p["excess_pct"] != ""]
    return {
        "strategy": strategy["id"],
        "name": strategy["name"],
        "start": first["date"],
        "end": last["date"],
        "total_return_pct": round(total, 2),
        "benchmark_return_pct": round(bench_total, 2) if bench_total is not None else "",
        "excess_pct": round(total - bench_total, 2) if bench_total is not None else "",
        "cagr_pct": round(cagr, 2) if cagr is not None else "",
        "max_drawdown_pct": round(max_dd, 2),
        "return_30d_pct": round(r30, 2) if r30 is not None else "",
        "benchmark_30d_pct": round(b30, 2) if b30 is not None else "",
        "return_1y_pct": round(r365, 2) if r365 is not None else "",
        "benchmark_1y_pct": round(b365, 2) if b365 is not None else "",
        "open_positions": len(open_positions),
        "open_tickers": len({p["ticker"] for p in open_positions}),
        "closed_trades": len(closed),
        "closed_hit_rate_pct": round(sum(1 for p in closed if p["excess_pct"] > 0) / len(closed) * 100, 1) if closed else "",
    }


def holdings_by_ticker(positions):
    """Aggregate open lots into the current replica holdings (what to mirror)."""
    totals = defaultdict(lambda: {"value": 0.0, "politicians": set(), "first_entry": None, "conflict": False, "option": False})
    for p in positions:
        if p["status"] != "OPEN":
            continue
        t = totals[p["ticker"]]
        t["value"] += p["current_value_usd"] or 0
        t["politicians"].add(p["politician"])
        t["conflict"] |= p["conflict"] == "YES"
        t["option"] |= p["option"] == "YES"
        t["first_entry"] = min(filter(None, [t["first_entry"], p["entry_day"]]))
    grand = sum(t["value"] for t in totals.values()) or 1
    rows = [
        {
            "ticker": ticker,
            "weight_pct": round(t["value"] / grand * 100, 2),
            "value_usd": round(t["value"], 2),
            "politicians": "; ".join(sorted(t["politicians"])),
            "since": t["first_entry"],
            "conflict": "YES" if t["conflict"] else "",
            "options": "YES" if t["option"] else "",
        }
        for ticker, t in totals.items()
    ]
    rows.sort(key=lambda r: -r["weight_pct"])
    return rows
