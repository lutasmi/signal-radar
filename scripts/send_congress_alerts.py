"""Congress trade alerts (Telegram) + conflict_trades / ptr_manual_review sheets.

An alert is raised for a freshly disclosed trade when any of these apply:
* CONFLICTO   - buy by a member whose committee covers the company's sector
* SEGUIDO     - any trade by a politician in config/tracker.json followed list
* TOP         - buy by a top-ranked politician in politician_scorecard
* CLUSTER     - >= N different politicians bought the same ticker recently
* PAPEL       - a followed politician filed a scanned report (review the PDF)

Each message is enriched with price context (move since the politician traded,
move since disclosure), other politicians buying the same ticker, the
politician's track record and whether the replica portfolio buys it.
Alerts are deduplicated per trade in telegram_alert_log.
"""

import argparse
import sys
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account", "requests"])

from radar import tracker
from radar.agent import AgentClient
from radar.conflicts import load_config
from radar.dates import parse_date
from radar.prices import PriceStore, fmt_pct, pct
from radar.records import get_value, stable_id


RAW_WORKSHEET_NAME = "raw_congress_ptr"
SCORECARD_WORKSHEET_NAME = "politician_scorecard"
CONFLICT_WORKSHEET_NAME = "conflict_trades"
MANUAL_REVIEW_WORKSHEET_NAME = "ptr_manual_review"
DEFAULT_MAX_MESSAGES = 20
MAX_DETAILED_TRADES = 5  # per message; the rest are summarized (big filings have 50+ trades)

CONFLICT_HEADER = [
    "conflict_score", "filing_date", "transaction_date", "disclosure_delay_days", "chamber",
    "politician", "party", "state_district", "trade_type", "ticker", "asset_name", "amount",
    "owner", "conflict_sectors", "conflict_committees", "conflict_reason", "source_url", "alert_id",
]
MANUAL_REVIEW_HEADER = [
    "filing_date", "chamber", "politician", "party", "state_district", "followed",
    "member_committees", "source_url",
]

REVIEW_WORKSHEET_NAME = "agent_review"
REVIEW_HEADER = [
    "alert_id", "reviewed_at", "politician", "filing_id", "ticker", "suspicion", "thesis",
    "evidence", "catalysts", "sources", "model",
]

CHAMBER_PREFIX = {"senate": "Sen.", "house": "Rep."}
TRADE_LABEL = {"buy": "COMPRA", "sell": "VENTA", "sell_partial": "VENTA PARCIAL", "exchange": "CANJE"}
TAG_EMOJI = {"CONFLICTO": "🚨", "SEGUIDO": "👀", "TOP": "🏆", "CLUSTER": "🧲", "PAPEL": "📄"}
TAG_LABEL = {"TOP": "BATE AL MERCADO"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_alert_id(row):
    return stable_id(
        "conflict_alert",  # kept for continuity with alerts already logged
        [
            get_value(row, "filing_id"),
            get_value(row, "owner"),
            get_value(row, "asset_name"),
            get_value(row, "trade_type"),
            get_value(row, "transaction_date"),
            get_value(row, "amount"),
        ],
    )


def score_of(row):
    try:
        return int(get_value(row, "conflict_score") or 0)
    except ValueError:
        return 0


def _d(value):
    return parse_date(value or "")


def to_float(value):
    """Parse numbers read back from Sheets, which may use a Spanish locale ("12,5")."""
    text = (value or "").strip().replace("%", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def amount_min(row):
    try:
        return int(get_value(row, "amount_min") or 0)
    except ValueError:
        return 0


def conflict_rows(raw_rows):
    rows = [row for row in raw_rows if get_value(row, "conflict") == "YES"]
    rows.sort(key=lambda row: (get_value(row, "politician"), get_value(row, "ticker")))
    rows.sort(key=lambda row: get_value(row, "filing_date"), reverse=True)
    rows.sort(key=score_of, reverse=True)
    records = []
    for row in rows:
        record = {column: get_value(row, column) for column in CONFLICT_HEADER}
        record["alert_id"] = make_alert_id(row)
        records.append(record)
    return records


def manual_review_rows(raw_rows, followed=frozenset()):
    rows = [row for row in raw_rows if get_value(row, "trade_type") == "paper_filing"]
    rows.sort(key=lambda row: get_value(row, "filing_date"), reverse=True)
    records = []
    for row in rows:
        record = {c: get_value(row, c) for c in MANUAL_REVIEW_HEADER if c != "followed"}
        record["followed"] = "YES" if get_value(row, "bioguide_id") in followed else ""
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# candidate selection
# ---------------------------------------------------------------------------


def recent_buyers(raw_rows, window_days):
    """ticker -> list of (transaction_date, politician_key, politician) buys."""
    buyers = defaultdict(list)
    for row in raw_rows:
        if get_value(row, "trade_type") != "buy" or not get_value(row, "ticker"):
            continue
        day = _d(get_value(row, "transaction_date")) or _d(get_value(row, "filing_date"))
        if day:
            buyers[get_value(row, "ticker").upper()].append(
                (day, get_value(row, "bioguide_id") or get_value(row, "politician"), get_value(row, "politician"))
            )
    return buyers


def other_buyers(buyers, row, window_days):
    ticker = get_value(row, "ticker").upper()
    me = get_value(row, "bioguide_id") or get_value(row, "politician")
    day = _d(get_value(row, "transaction_date")) or _d(get_value(row, "filing_date"))
    if not ticker or not day:
        return []
    names = OrderedDict()
    for other_day, key, name in sorted(buyers.get(ticker, [])):
        if key != me and abs((other_day - day).days) <= window_days:
            names[key] = name
    return list(names.values())


def classify(row, conflict_config, tracker_config, top_keys, buyers):
    alerts = tracker_config["alerts"]
    followed = tracker.followed_keys(tracker_config)
    key = get_value(row, "bioguide_id") or get_value(row, "politician")
    trade_type = get_value(row, "trade_type")
    tags = []
    if trade_type == "paper_filing":
        return ["PAPEL"] if get_value(row, "bioguide_id") in followed else []
    if trade_type == "buy" and get_value(row, "conflict") == "YES" and score_of(row) >= conflict_config["scoring"]["alert_min_score"]:
        tags.append("CONFLICTO")
    if (
        get_value(row, "bioguide_id") in followed
        and get_value(row, "ticker")  # skip bonds/funds without a ticker
        and amount_min(row) >= alerts["followed_min_amount"]
    ):
        if trade_type == "buy" or (alerts["followed_include_sells"] and trade_type in ("sell", "sell_partial")):
            tags.append("SEGUIDO")
    if trade_type == "buy" and key in top_keys:
        tags.append("TOP")
    if trade_type == "buy" and get_value(row, "ticker"):
        if len(other_buyers(buyers, row, alerts["cluster_window_days"])) + 1 >= alerts["cluster_min_politicians"]:
            tags.append("CLUSTER")
    return tags


def alert_candidates(raw_rows, conflict_config, tracker_config, top_keys=(), today=None):
    today = today or date.today()
    oldest = today - timedelta(days=tracker_config["alerts"]["max_filing_age_days"])
    buyers = recent_buyers(raw_rows, tracker_config["alerts"]["cluster_window_days"])
    candidates = []
    for row in raw_rows:
        filing = _d(get_value(row, "filing_date"))
        if filing is None or filing < oldest:
            continue
        tags = classify(row, conflict_config, tracker_config, set(top_keys), buyers)
        if tags:
            candidates.append((row, tags))
    return candidates, buyers


# ---------------------------------------------------------------------------
# message
# ---------------------------------------------------------------------------


def member_label(row):
    prefix = CHAMBER_PREFIX.get(get_value(row, "chamber"), "")
    party = get_value(row, "party")[:1]
    state = get_value(row, "state_district")
    detail = "-".join(part for part in [party, state] if part)
    label = f"{prefix} {get_value(row, 'politician')}".strip()
    return f"{label} ({detail})" if detail else label


def price_context(row, prices):
    ticker = get_value(row, "ticker")
    if not ticker or prices is None or not prices.has(ticker):
        return ""
    tx_day = _d(get_value(row, "transaction_date"))
    filing = _d(get_value(row, "filing_date"))
    _, tx_price = prices.on_or_after(ticker, tx_day) if tx_day else (None, None)
    _, filing_price = prices.on_or_before(ticker, filing) if filing else (None, None)
    last_day, last_price = prices.latest(ticker)
    parts = []
    if last_price:
        parts.append(f"precio {last_price:,.2f} ({last_day})")
    if tx_price and last_price:
        parts.append(f"desde su operación {fmt_pct(pct(tx_price, last_price))}")
    if filing_price and last_price:
        parts.append(f"desde la declaración {fmt_pct(pct(filing_price, last_price))}")
    return " · ".join(parts)


def scorecard_line(row, scorecard_lookup):
    key = get_value(row, "bioguide_id") or get_value(row, "politician")
    sc = scorecard_lookup.get(key)
    if not sc or not get_value(sc, "evaluated_trades") or get_value(sc, "evaluated_trades") == "0":
        return ""
    rank = get_value(sc, "rank")
    excess = get_value(sc, "avg_excess_90d")
    hit = get_value(sc, "hit_rate")
    try:
        excess_txt = fmt_pct(to_float(excess))
    except ValueError:
        excess_txt = "n/d"
    try:
        hit_txt = f"{to_float(hit):.0f}%"
    except ValueError:
        hit_txt = "n/d"
    rank_txt = f"#{rank} · " if rank else ""
    return (
        f"Historial: {rank_txt}{get_value(sc, 'evaluated_trades')} compras evaluadas, "
        f"exceso medio a 90 días vs S&P 500 {excess_txt}, aciertos {hit_txt}"
    )


def replica_line(row, tracker_config, tags=()):
    if get_value(row, "trade_type") not in ("buy", "sell", "sell_partial") or not get_value(row, "ticker"):
        return ""
    followed = tracker.followed_keys(tracker_config)
    trade = {
        "bioguide_id": get_value(row, "bioguide_id"),
        "conflict": get_value(row, "conflict") == "YES",
        "beater": "TOP" in tags,
    }
    names = [
        s["name"].split(" (")[0]
        for s in tracker_config["portfolios"]
        if "all" not in s["include"] and tracker.trade_included(trade, s["include"], followed)
    ]
    if not names:
        return ""
    verb = "compra" if get_value(row, "trade_type") == "buy" else "vende"
    return f"Cartera réplica: {', '.join(names)} {verb} {get_value(row, 'ticker')} al cierre del próximo día hábil"


def headline_rows(group, limit=MAX_DETAILED_TRADES):
    """Most relevant trades of a filing first: conflicts/clusters, buys, larger amounts."""
    weight = {"CONFLICTO": 4, "CLUSTER": 3, "TOP": 2, "SEGUIDO": 1, "PAPEL": 0}
    ranked = sorted(
        group,
        key=lambda item: (
            -sum(weight.get(t, 0) for t in item[1]),
            not get_value(item[0], "ticker"),
            get_value(item[0], "trade_type") != "buy",
            -amount_min(item[0]),
            get_value(item[0], "ticker"),
        ),
    )
    return ranked[:limit], ranked[limit:]


def review_lines(review):
    if not review:
        return []
    try:
        level = int(review.get("suspicion") or 0)
    except ValueError:
        level = 0
    lines = [f"  🤖 Sospecha {level}/5: {review.get('thesis', '')}"]
    if review.get("catalysts"):
        lines.append(f"  Próximos catalizadores: {review['catalysts']}")
    sources = review.get("sources") or []
    if isinstance(sources, str):
        sources = [u for u in sources.split() if u.startswith("http")]
    if sources:
        lines.append(f"  Fuentes: {' '.join(sources[:2])}")
    return lines


def build_message(group, prices=None, scorecard_lookup=None, buyers=None, tracker_config=None, reviews=None):
    first_row = group[0][0]
    tags = []
    for _, row_tags in group:
        for tag in row_tags:
            if tag not in tags:
                tags.append(tag)
    head_emoji = TAG_EMOJI.get(tags[0], "🔔")
    top_score = max((score_of(r) for r, _ in group), default=0)
    title = " · ".join(TAG_LABEL.get(tag, tag) for tag in tags)
    if "CONFLICTO" in tags:
        title += f" · score {top_score}"
    lines = [f"{head_emoji} {title}", member_label(first_row)]
    committees = get_value(first_row, "conflict_committees") or get_value(first_row, "member_committees")
    if committees:
        lines.append(f"Comités: {committees}")
    sc = scorecard_line(first_row, scorecard_lookup or {})
    if sc:
        lines.append(sc)
    lines.append("")
    window = (tracker_config or {}).get("alerts", {}).get("cluster_window_days", 30)
    shown, rest = headline_rows(group)
    for row, row_tags in shown:
        if get_value(row, "trade_type") == "paper_filing":
            lines.append("• Declaración ESCANEADA (no legible automáticamente): revisar el PDF")
            continue
        ticker = get_value(row, "ticker")
        trade = TRADE_LABEL.get(get_value(row, "trade_type"), get_value(row, "trade_type").upper())
        option = " (OPCIONES)" if get_value(row, "asset_type") in ("OP", "Stock Option") else ""
        delay = get_value(row, "disclosure_delay_days")
        lines.append(f"• {trade}{option} {ticker} {get_value(row, 'asset_name')[:60]}".replace("  ", " "))
        lines.append(f"  Importe: {get_value(row, 'amount')} · titular: {get_value(row, 'owner') or 'n/d'}")
        lines.append(
            f"  Operación: {get_value(row, 'transaction_date') or 'n/d'} · declarada: "
            f"{get_value(row, 'filing_date')}" + (f" ({delay} días después)" if delay else "")
        )
        if get_value(row, "extraction") == "claude_vision":
            lines.append("  (leída por IA de una declaración escaneada: verificar en el PDF)")
        context = price_context(row, prices)
        if context:
            lines.append(f"  Mercado: {context}")
        if "CONFLICTO" in row_tags and get_value(row, "conflict_reason"):
            lines.append(f"  Conflicto: {get_value(row, 'conflict_reason')}")
        if buyers is not None and get_value(row, "trade_type") == "buy":
            others = other_buyers(buyers, row, window)
            if others:
                lines.append(f"  También compran {ticker} ({window} días): {', '.join(others[:6])}")
        if tracker_config:
            replica = replica_line(row, tracker_config, row_tags)
            if replica:
                lines.append(f"  {replica}")
        lines.extend(review_lines((reviews or {}).get(make_alert_id(row))))
    if rest:
        buys = sum(1 for r, _ in rest if get_value(r, "trade_type") == "buy")
        tickers = ", ".join(sorted({get_value(r, "ticker") for r, _ in rest if get_value(r, "ticker")})[:15])
        lines.append(f"• y {len(rest)} operaciones más ({buys} compras): {tickers}")
    lines.append("")
    lines.append(f"Fuente oficial: {get_value(first_row, 'source_url')}")
    lines.append("Información pública; no es una recomendación de inversión.")
    return "\n".join(lines)


def group_candidates(candidates):
    groups = OrderedDict()
    order = {"CONFLICTO": 0, "SEGUIDO": 1, "TOP": 2, "CLUSTER": 3, "PAPEL": 4}
    ranked = sorted(
        candidates,
        key=lambda c: (min(order.get(t, 9) for t in c[1]), -score_of(c[0]), get_value(c[0], "filing_id")),
    )
    for row, tags in ranked:
        key = (get_value(row, "politician"), get_value(row, "filing_id"))
        groups.setdefault(key, []).append((row, tags))
    return list(groups.values())


def pending_groups(raw_rows, sent_ids, conflict_config, tracker_config, top_keys=(), today=None):
    candidates, buyers = alert_candidates(raw_rows, conflict_config, tracker_config, top_keys, today=today)
    fresh = [(row, tags) for row, tags in candidates if make_alert_id(row) not in sent_ids]
    return group_candidates(fresh), buyers


def pending_messages(raw_rows, sent_ids, conflict_config, tracker_config, prices=None,
                     scorecard_lookup=None, top_keys=(), today=None, reviews=None):
    groups, buyers = pending_groups(raw_rows, sent_ids, conflict_config, tracker_config, top_keys, today)
    return [
        (group, build_message(group, prices, scorecard_lookup, buyers, tracker_config, reviews))
        for group in groups
    ]


# ---------------------------------------------------------------------------
# Claude investigation
# ---------------------------------------------------------------------------


def review_context(group, prices, scorecard_lookup, buyers, tracker_config, today=None):
    """Plain-text dossier handed to the investigating agent."""
    first = group[0][0]
    window = tracker_config["alerts"]["cluster_window_days"]
    lines = [
        f"Fecha de hoy: {(today or date.today()).isoformat()}",
        f"Congresista: {member_label(first)} · cámara: {get_value(first, 'chamber')}",
        f"Comités: {get_value(first, 'member_committees') or 'desconocidos'}",
    ]
    sc = scorecard_line(first, scorecard_lookup or {})
    if sc:
        lines.append(sc)
    lines.append(f"Declaración oficial: {get_value(first, 'source_url')}")
    lines.append("")
    lines.append("Operaciones declaradas:")
    for row, tags in group:
        lines.append(
            f"- {TRADE_LABEL.get(get_value(row, 'trade_type'), get_value(row, 'trade_type'))} "
            f"{get_value(row, 'ticker')} ({get_value(row, 'asset_name')}) tipo {get_value(row, 'asset_type')} · "
            f"importe {get_value(row, 'amount')} · titular {get_value(row, 'owner')} · operación "
            f"{get_value(row, 'transaction_date')} · declarada {get_value(row, 'filing_date')} · motivos alerta: {', '.join(tags)}"
        )
        if get_value(row, "conflict_reason"):
            lines.append(f"  Conflicto por reglas: {get_value(row, 'conflict_reason')}")
        if get_value(row, "sic_description"):
            lines.append(f"  Sector SEC: {get_value(row, 'sic_description')}")
        context = price_context(row, prices)
        if context:
            lines.append(f"  Mercado: {context}")
        others = other_buyers(buyers, row, window) if get_value(row, "trade_type") == "buy" else []
        if others:
            lines.append(f"  Otros congresistas que compraron en ±{window} días: {', '.join(others)}")
    return "\n".join(lines)


def review_eligible(row, tags, agent_config):
    """Only the strongest signals are worth paying for an investigation."""
    if not get_value(row, "ticker") or get_value(row, "trade_type") != "buy":
        return False
    if "CONFLICTO" in tags and score_of(row) >= agent_config.get("review_min_conflict_score", 60):
        return True
    if "SEGUIDO" in tags and amount_min(row) >= agent_config.get("review_min_amount", 15001):
        return True
    return "CLUSTER" in tags and agent_config.get("review_clusters", False)


def reviews_today(reviews, today=None):
    day = (today or date.today()).isoformat()
    return len({get_value(r, "filing_id") for r in reviews.values() if get_value(r, "reviewed_at").startswith(day)})


def run_reviews(client, groups, already, prices, scorecard_lookup, buyers, tracker_config, max_reviews):
    """Investigate the highest-priority unreviewed disclosures. Returns review records."""
    from radar.agent import BudgetExceeded

    agent_config = tracker_config.get("agent", {})
    records = []
    done = 0
    for group in groups:
        if done >= max_reviews:
            break
        shown, _ = headline_rows(group, limit=agent_config.get("max_tickers_per_review", 3))
        rows = [
            (r, t) for r, t in shown
            if make_alert_id(r) not in already and review_eligible(r, t, agent_config)
        ]
        if not rows:
            continue
        tickers = sorted({get_value(r, "ticker") for r, _ in rows})
        try:
            assessments = client.review_trades(
                review_context(rows, prices, scorecard_lookup, buyers, tracker_config),
                tickers,
                max_searches=agent_config.get("max_searches_per_review", 2),
            )
        except BudgetExceeded as exc:
            print(f"Agent stopped: {exc}")
            break
        except Exception as exc:
            print(f"WARNING: agent review failed for {get_value(rows[0][0], 'filing_id')}: {exc}")
            continue
        done += 1
        by_ticker = {a.get("ticker", "").upper(): a for a in assessments}
        from scripts import send_telegram_alerts as telegram

        for row, _ in rows:
            a = by_ticker.get(get_value(row, "ticker").upper())
            if not a:
                continue
            records.append(
                {
                    "alert_id": make_alert_id(row),
                    "reviewed_at": telegram.now_iso(),
                    "politician": get_value(row, "politician"),
                    "filing_id": get_value(row, "filing_id"),
                    "ticker": get_value(row, "ticker"),
                    "suspicion": a.get("suspicion", ""),
                    "thesis": a.get("thesis", ""),
                    "evidence": a.get("evidence", ""),
                    "catalysts": a.get("catalysts", ""),
                    "sources": " ".join(a.get("sources") or []),
                    "model": agent_config.get("model", ""),
                }
            )
    return records


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def read_reviews(sheet):
    from radar.sheets import read_records_or_empty

    return {get_value(r, "alert_id"): r for r in read_records_or_empty(sheet, REVIEW_WORKSHEET_NAME)}


def read_scorecard(sheet):
    from radar.sheets import read_records_or_empty

    rows = read_records_or_empty(sheet, SCORECARD_WORKSHEET_NAME)
    lookup = {get_value(r, "politician_key"): r for r in rows}
    return lookup, rows


def top_keys_from_rows(rows, tracker_config):
    """Politicians flagged beats_market=YES in politician_scorecard (best first)."""
    return [get_value(r, "politician_key") for r in rows if get_value(r, "beats_market") == "YES"]


def run(dry_run=False, max_messages=DEFAULT_MAX_MESSAGES, with_prices=True):
    import gspread
    import requests
    from dotenv import load_dotenv

    from radar.sheets import append_records as append_rows
    from radar.sheets import open_sheet, read_records, replace_worksheet
    from scripts import send_telegram_alerts as telegram

    load_dotenv()
    conflict_config = load_config()
    tracker_config = tracker.load_tracker_config()
    sheet = open_sheet()
    try:
        raw_rows = read_records(sheet, RAW_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        print(f"WARNING: {RAW_WORKSHEET_NAME} not found; nothing to do")
        return []

    followed = tracker.followed_keys(tracker_config)
    conflicts = conflict_rows(raw_rows)
    manual = manual_review_rows(raw_rows, followed)
    print(f"Raw PTR rows: {len(raw_rows)} | conflicts: {len(conflicts)} | scanned filings: {len(manual)}")
    if not dry_run:
        replace_worksheet(sheet, CONFLICT_WORKSHEET_NAME, CONFLICT_HEADER, conflicts)
        replace_worksheet(sheet, MANUAL_REVIEW_WORKSHEET_NAME, MANUAL_REVIEW_HEADER, manual)

    scorecard_lookup, scorecard_rows = read_scorecard(sheet)
    top_keys = top_keys_from_rows(scorecard_rows, tracker_config)
    sent_ids = telegram.existing_alert_ids(telegram.alert_log_records(sheet))

    groups, buyers = pending_groups(raw_rows, sent_ids, conflict_config, tracker_config, top_keys)
    prices = None
    if with_prices and groups:
        prices = PriceStore(session=requests.Session())
        prices.prefetch({get_value(r, "ticker") for g in groups for r, _ in g if get_value(r, "ticker")})

    reviews = read_reviews(sheet)
    agent_config = tracker_config.get("agent", {})
    from radar import agent as agent_module

    client = None
    if agent_module.api_key() and agent_config.get("enabled", True) and groups:
        client = AgentClient(
            requests.Session(), agent_config, spent_this_month=agent_module.month_spend(sheet)
        )
    allowance = min(
        agent_config.get("max_reviews_per_run", 1),
        agent_config.get("max_reviews_per_day", 3) - reviews_today(reviews),
    )
    if client is not None and client.enabled and allowance > 0:
        new_reviews = run_reviews(
            client, groups, set(reviews), prices, scorecard_lookup, buyers, tracker_config, allowance
        )
        if new_reviews and not dry_run:
            append_rows(sheet, REVIEW_WORKSHEET_NAME, REVIEW_HEADER, new_reviews)
        reviews.update({r["alert_id"]: r for r in new_reviews})
        print(f"Agent reviews this run: {len(new_reviews)} · {client.usage_line()}")
        if not dry_run:
            agent_module.record_usage(sheet, client, "review")
    elif client is not None:
        print(f"Agent review skipped (allowance {allowance}, budget left ~{client.remaining():.2f} USD)")

    messages = [
        (group, build_message(group, prices, scorecard_lookup, buyers, tracker_config, reviews))
        for group in groups
    ]
    print(f"Alert messages pending: {len(messages)}")
    if dry_run:
        for _, message in messages:
            print("\n--- DRY RUN ALERT ---")
            print(message)
        return messages
    if not messages:
        return messages

    token, chat_id = telegram.telegram_credentials()
    sent_at = telegram.now_iso()
    log_records = []

    def log(group, message, band):
        for row, tags in group:
            log_records.append(
                {
                    "alert_id": make_alert_id(row),
                    "sent_at": sent_at,
                    "item_id": f"{get_value(row, 'filing_id')}:{get_value(row, 'ticker')}",
                    "score": get_value(row, "conflict_score"),
                    "score_band": band + ":" + "+".join(tags),
                    "message": message[:4000],
                }
            )

    to_send, skipped = messages[:max_messages], messages[max_messages:]
    try:
        for group, message in to_send:
            for chunk_start in range(0, len(message), 3900):  # Telegram limit is 4096 chars
                telegram.send_telegram_message(token, chat_id, message[chunk_start:chunk_start + 3900])
            log(group, message, "SENT")
    except Exception:
        telegram.append_alert_log(sheet, log_records)  # never resend what was delivered
        raise
    if skipped:
        extra = sum(len(group) for group, _ in skipped)
        telegram.send_telegram_message(
            token,
            chat_id,
            f"ℹ️ Hay {len(skipped)} declaraciones más ({extra} operaciones) que cumplen criterios de alerta. "
            f"Ver pestañas {CONFLICT_WORKSHEET_NAME} y replica_holdings en Google Sheets.",
        )
        for group, message in skipped:
            log(group, message, "SUMMARIZED")
    telegram.append_alert_log(sheet, log_records)
    print(f"Alerts sent: {len(to_send)} (+{len(skipped)} summarized)")
    return messages


# ---------------------------------------------------------------------------
# offline validation
# ---------------------------------------------------------------------------


def validate_logic():
    conflict_config = load_config()
    tracker_config = tracker.load_tracker_config()
    today = date(2026, 10, 21)
    base = {
        "filing_id": "F1", "chamber": "senate", "politician": "Test Senator", "bioguide_id": "X0001",
        "party": "Republican", "state_district": "MS", "owner": "Self",
        "asset_name": "Lockheed Martin Corp", "ticker": "LMT", "asset_type": "Stock", "trade_type": "buy",
        "transaction_date": "2026-09-01", "filing_date": "2026-10-15", "disclosure_delay_days": "44",
        "amount": "$50,001 - $100,000", "amount_min": "50001", "conflict": "YES", "conflict_score": "75",
        "conflict_sectors": "defense", "conflict_committees": "Senate Committee on Armed Services (Chairman)",
        "conflict_reason": "comité con jurisdicción sobre Defensa / aeroespacial",
        "source_url": "https://efdsearch.senate.gov/search/view/ptr/x/",
    }
    pelosi = {
        **base, "filing_id": "P1", "politician": "Nancy Pelosi", "bioguide_id": "P000197", "chamber": "house",
        "party": "Democrat", "state_district": "CA11", "ticker": "NVDA", "asset_name": "NVIDIA Corp (NVDA)",
        "asset_type": "OP", "conflict": "NO", "conflict_score": "0", "amount": "$1,000,001 - $5,000,000",
        "amount_min": "1000001", "source_url": "https://disclosures-clerk.house.gov/x.pdf",
    }
    rows = [
        base,
        {**base, "asset_name": "Northrop Grumman", "ticker": "NOC", "conflict_score": "60"},
        {**base, "filing_id": "F2", "trade_type": "sell"},
        {**base, "filing_id": "F3", "conflict": "NO", "conflict_score": "0", "ticker": "XYZ"},
        {**base, "filing_id": "F4", "filing_date": "2026-06-01"},
        pelosi,
        {**pelosi, "filing_id": "P2", "trade_type": "paper_filing", "ticker": "", "asset_name": "scan"},
        {**base, "filing_id": "C1", "politician": "A", "bioguide_id": "A1", "ticker": "CRWD", "conflict": "NO", "conflict_score": "0"},
        {**base, "filing_id": "C2", "politician": "B", "bioguide_id": "B1", "ticker": "CRWD", "conflict": "NO", "conflict_score": "0", "transaction_date": "2026-09-10"},
        {**base, "filing_id": "C3", "politician": "C", "bioguide_id": "C1", "ticker": "CRWD", "conflict": "NO", "conflict_score": "0", "transaction_date": "2026-09-20"},
    ]
    prices = PriceStore(
        histories={
            "LMT": [(date(2026, 9, 1), 500.0), (date(2026, 10, 15), 520.0), (date(2026, 10, 20), 550.0)],
            "NVDA": [(date(2026, 9, 1), 100.0), (date(2026, 10, 20), 120.0)],
        },
        today=today,
    )
    messages = pending_messages(rows, set(), conflict_config, tracker_config, prices, {}, (), today=today)
    by_key = {(g[0][0]["politician"], g[0][0]["filing_id"]): m for g, m in messages}
    conflict_msg = by_key.get(("Test Senator", "F1"))
    if not conflict_msg or "COMPRA LMT" not in conflict_msg or "COMPRA NOC" not in conflict_msg:
        raise ValueError(f"Conflict alert missing or incomplete: {by_key.keys()}")
    if "desde su operación +10.0%" not in conflict_msg:
        raise ValueError(f"Conflict alert lacks price/replica context:\n{conflict_msg}")
    pelosi_msg = by_key.get(("Nancy Pelosi", "P1"))
    if not pelosi_msg or "SEGUIDO" not in pelosi_msg or "(OPCIONES)" not in pelosi_msg:
        raise ValueError(f"Followed-politician alert missing:\n{pelosi_msg}")
    if ("Nancy Pelosi", "P2") not in by_key or "ESCANEADA" not in by_key[("Nancy Pelosi", "P2")]:
        raise ValueError("Scanned filing by a followed politician must alert")
    cluster = by_key.get(("C", "C3"))
    if not cluster or "CLUSTER" not in cluster or "También compran CRWD" not in cluster:
        raise ValueError(f"Cluster alert missing:\n{cluster}")
    if ("Test Senator", "F2") in by_key or ("Test Senator", "F4") in by_key:
        raise ValueError("Conflict sells and stale filings must not alert")
    reviewed = build_message(
        [(base, ["CONFLICTO"])], prices, {}, None, tracker_config,
        {make_alert_id(base): {"suspicion": "4", "thesis": "Contrato del Pentágono días después.", "catalysts": "", "sources": "https://a https://b https://c"}},
    )
    if "🤖 Sospecha 4/5: Contrato del Pentágono" not in reviewed or "https://c" in reviewed:
        raise ValueError(f"Agent review not rendered:\n{reviewed}")
    if "Operaciones declaradas" not in review_context([(base, ["CONFLICTO"])], prices, {}, {}, tracker_config, today):
        raise ValueError("Agent dossier incomplete")
    line = scorecard_line(base, {"X0001": {"rank": "2", "evaluated_trades": "40", "avg_excess_90d": "6,25", "hit_rate": "61,5"}})
    if "+6.2%" not in line or "aciertos 62%" not in line:
        raise ValueError(f"Scorecard numbers with Spanish locale not parsed: {line}")
    if "Trumpito ETF" not in replica_line({**base, "conflict": "NO"}, tracker_config, ["TOP"]):
        raise ValueError("Replica must buy trades of market-beating politicians")
    if replica_line({**base, "conflict": "NO", "bioguide_id": "Z9"}, tracker_config, ["CLUSTER"]):
        raise ValueError("Replica must ignore politicians that do not beat the market")
    if top_keys_from_rows([{"politician_key": "A", "beats_market": "YES"}, {"politician_key": "B", "beats_market": ""}], tracker_config) != ["A"]:
        raise ValueError("Top keys must come from beats_market")
    if to_float("1.234,5") != 1234.5 or to_float("7.5") != 7.5:
        raise ValueError("to_float locale handling")
    sent = {make_alert_id(r) for r in rows}
    if pending_messages(rows, sent, conflict_config, tracker_config, prices, {}, (), today=today):
        raise ValueError("Already-sent alerts must not be resent")
    if [r["conflict_score"] for r in conflict_rows(rows)][:3] != ["75", "75", "75"]:
        raise ValueError("conflict_trades ordering")
    if manual_review_rows(rows, {"P000197"})[0]["followed"] != "YES":
        raise ValueError("Manual review must flag followed politicians")
    return True


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES)
    parser.add_argument("--no-prices", action="store_true")
    parser.add_argument("--validate", action="store_true", help="Run offline logic checks only.")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.validate:
        validate_logic()
        print("OK congress alert logic")
        return
    run(dry_run=args.dry_run, max_messages=args.max_messages, with_prices=not args.no_prices)


if __name__ == "__main__":
    main()
