import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(
    ["dotenv", "requests"]
    + ([] if "--test" in sys.argv else ["gspread"])
)

import requests
from dotenv import load_dotenv

from radar.records import get_value, rows_to_dicts, split_semicolon, stable_id


REVIEW_QUEUE_WORKSHEET_NAME = "review_queue"
SIGNALS_WORKSHEET_NAME = "signals"
ALERT_LOG_WORKSHEET_NAME = "telegram_alert_log"
ALERT_LOG_HEADER = [
    "alert_id",
    "sent_at",
    "item_id",
    "score",
    "score_band",
    "message",
]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_test_message():
    return "\n".join(
        [
            "✅ Signal Radar",
            "",
            "Telegram integration OK",
            "",
            f"UTC: {now_iso()}",
            "",
            "Version: v1",
            "",
            "This is a test message.",
        ]
    )


def telegram_credentials():
    import os

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return token, chat_id


def make_alert_id(row):
    return stable_id("telegram_alert", [get_value(row, "review_id")])


def should_alert(row):
    if get_value(row, "status") == "CLOSED":
        return False
    return (
        get_value(row, "status") == "NEW"
        or get_value(row, "review_today") == "YES"
        or get_value(row, "score_band") == "HIGH"
    )


def build_signal_lookup(signals):
    return {
        get_value(signal, "signal_id"): signal
        for signal in signals or []
        if get_value(signal, "signal_id")
    }


def unique_join(values, fallback="No disponible"):
    seen = []
    for value in values:
        value = (value or "").strip()
        if value and value not in seen:
            seen.append(value)
    return "; ".join(seen) if seen else fallback


def signal_asset(signal):
    ticker = get_value(signal, "ticker")
    entity = get_value(signal, "entity_name")
    if ticker and entity:
        return f"{ticker} - {entity}"
    return ticker or entity


def signal_action(signal):
    signal_type = get_value(signal, "signal_type")
    if signal_type in {"PTR_BUY", "PTR_BUY_CONFLICT", "INSIDER_BUY"}:
        return "compra"
    if signal_type == "PTR_SELL":
        return "vende"
    if signal_type in {
        "CONTRACT",
        "CONTRACT_AWARD_NOTICE",
    }:
        return "adjudica contrato a"
    if signal_type in {
        "CONTRACT_OPPORTUNITY",
        "CONTRACT_PRE_SOLICITATION",
        "CONTRACT_SOLE_SOURCE",
    }:
        return "publica oportunidad para"
    if signal_type in {"BILL_INTRODUCED", "COMMITTEE_ACTION", "LEGISLATIVE_ACTION"}:
        return "mueve iniciativa sobre"
    if signal_type in {
        "RULE_PROPOSED",
        "RULE_FINAL",
        "AGENCY_NOTICE",
        "EXECUTIVE_ACTION",
        "REGULATORY_CATALYST",
    }:
        return "publica accion regulatoria sobre"
    if signal_type.startswith("LOBBY_"):
        return "registra lobby para"
    if signal_type in {
        "MATERIAL_EVENT_8K",
        "MATERIAL_CONTRACT_8K",
        "MAJOR_HOLDER_ENTRY",
        "ACTIVIST_POSITION",
        "FORM144_PLANNED_SALE",
        "INSTITUTIONAL_POSITION_13F",
    }:
        return "presenta evento SEC sobre"
    if signal_type in {
        "POLITICAL_CONTRIBUTION",
        "PAC_CONTRIBUTION",
        "EXECUTIVE_CONTRIBUTION",
    }:
        return "aporta a"
    if signal_type in {"GRANT_FORECAST", "GRANT_OPPORTUNITY"}:
        return "abre financiacion para"
    return "activa"


def summarize_signal(signal):
    signal_date = get_value(signal, "signal_date")
    actor = get_value(signal, "actor_name") or "Actor desconocido"
    actor_type = get_value(signal, "actor_type")
    action = signal_action(signal)
    asset = signal_asset(signal) or "Activo/entidad desconocida"
    amount = get_value(signal, "amount")
    source = get_value(signal, "source")

    actor_label = f"{actor} ({actor_type})" if actor_type else actor
    amount_text = f" | importe: {amount}" if amount else ""
    date_text = f"{signal_date} | " if signal_date else ""
    source_text = f" | fuente: {source}" if source else ""
    return f"{date_text}{actor_label} {action} {asset}{amount_text}{source_text}"


def linked_signals(row, signal_lookup):
    signals = []
    for signal_id in split_semicolon(get_value(row, "signal_ids")):
        signal = signal_lookup.get(signal_id)
        if signal:
            signals.append(signal)
    return signals


def buyer_text(row, signals):
    buyers = [
        get_value(signal, "actor_name")
        for signal in signals
        if get_value(signal, "signal_type") in {"PTR_BUY", "PTR_BUY_CONFLICT", "INSIDER_BUY"}
    ]
    if buyers:
        return unique_join(buyers)

    actors = [get_value(signal, "actor_name") for signal in signals]
    return unique_join(actors)


def purchase_text(row, signals):
    buy_assets = [
        signal_asset(signal)
        for signal in signals
        if get_value(signal, "signal_type") in {"PTR_BUY", "PTR_BUY_CONFLICT", "INSIDER_BUY"}
    ]
    if buy_assets:
        return unique_join(buy_assets)

    subject = get_value(row, "ticker") or get_value(row, "entity_name")
    if subject:
        return subject

    return unique_join(signal_asset(signal) for signal in signals)


def scoring_lines(row):
    score_reason = get_value(row, "score_reason")
    if not score_reason:
        return ["No hay explicacion de scoring disponible."]
    return [
        humanize_score_reason(part.strip())
        for part in score_reason.split(";")
        if part.strip()
    ]


def humanize_score_reason(reason):
    if reason.startswith("priority "):
        return reason.replace("priority ", "Prioridad ", 1).replace(" +", ": +")
    if reason.startswith("status "):
        return reason.replace("status ", "Estado ", 1).replace(" +", ": +")
    if reason.startswith("source diversity "):
        return reason.replace("source diversity ", "Diversidad de fuentes: ", 1)
    if reason.startswith("review_today "):
        return reason.replace("review_today ", "Revisar hoy: ", 1)
    if reason.startswith("recency <= "):
        return reason.replace("recency <= ", "Recencia <= ", 1)
    if " +" in reason:
        label, points = reason.rsplit(" +", 1)
        return f"Tipo/senal {label}: +{points}"
    return reason


def evidence_lines(signals, max_lines=3):
    lines = [summarize_signal(signal) for signal in signals[:max_lines]]
    if len(signals) > max_lines:
        lines.append(f"... y {len(signals) - max_lines} senales vinculadas mas")
    return lines


def build_message(row, signal_lookup=None):
    signal_lookup = signal_lookup or {}
    signals = linked_signals(row, signal_lookup)
    subject = get_value(row, "ticker") or get_value(row, "entity_name") or "Oportunidad"
    lines = [
        f"Signal Radar - {get_value(row, 'score_band') or 'UNSCORED'}",
        "",
        f"Asunto: {subject}",
        f"Quien compra / actua: {buyer_text(row, signals)}",
        f"Que compra / recibe / queda afectado: {purchase_text(row, signals)}",
        "",
        "Vinculacion / relevancia:",
        f"- {get_value(row, 'priority_reason') or get_value(row, 'summary')}",
        f"- Tipo de vinculo: {get_value(row, 'opportunity_type')}",
        f"- Evidencia: {get_value(row, 'evidence_count')} elementos de {get_value(row, 'sources')}",
        "",
        "Detalle:",
    ]
    evidence = evidence_lines(signals)
    if evidence:
        lines.extend(f"- {line}" for line in evidence)
    else:
        lines.append("- No hay detalle de senales vinculadas en la fila local.")

    lines.extend(
        [
            "",
            "Scoring:",
            f"- Total: {get_value(row, 'score')} ({get_value(row, 'score_band')})",
            "- Metodo: suma configurable de prioridad, estado, diversidad de fuentes, tipo de oportunidad, tipos de senal y recencia.",
        ]
    )
    lines.extend(f"- {line}" for line in scoring_lines(row))
    lines.extend(
        [
            "",
            f"Estado: {get_value(row, 'status')}",
            "Revision: abrir Google Sheets",
        ]
    )
    return "\n".join(lines)


def alert_log_records(sheet):
    import gspread

    try:
        worksheet = sheet.worksheet(ALERT_LOG_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return []
    values = worksheet.get_all_values()
    if not values:
        return []
    if values[0] == ALERT_LOG_HEADER:
        return rows_to_dicts(values)
    records = []
    for row in values:
        records.append(
            {
                column: row[index] if index < len(row) else ""
                for index, column in enumerate(ALERT_LOG_HEADER)
            }
        )
    return records


def existing_alert_ids(alert_rows):
    return {
        get_value(row, "alert_id")
        for row in alert_rows
        if get_value(row, "alert_id")
    }


def pending_alerts(review_rows, sent_alert_ids, signal_lookup=None):
    alerts = []
    for row in review_rows:
        if not should_alert(row):
            continue
        alert_id = make_alert_id(row)
        if alert_id in sent_alert_ids:
            continue
        alerts.append((alert_id, row, build_message(row, signal_lookup=signal_lookup)))
    return alerts


def send_telegram_message(token, chat_id, message):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(
            f"Telegram API error {response.status_code}: {response.text[:500]}"
        )


def append_alert_log(sheet, records):
    from radar.sheets import append_records

    append_records(sheet, ALERT_LOG_WORKSHEET_NAME, ALERT_LOG_HEADER, records)


def alert_log_record(alert_id, row, message, sent_at):
    return {
        "alert_id": alert_id,
        "sent_at": sent_at,
        "item_id": get_value(row, "review_id"),
        "score": get_value(row, "score"),
        "score_band": get_value(row, "score_band"),
        "message": message,
    }


def send_alerts(dry_run=False):
    load_dotenv()
    from radar.sheets import open_sheet

    sheet = open_sheet()
    review_rows = rows_to_dicts(
        sheet.worksheet(REVIEW_QUEUE_WORKSHEET_NAME).get_all_values()
    )
    import gspread

    try:
        signals = rows_to_dicts(sheet.worksheet(SIGNALS_WORKSHEET_NAME).get_all_values())
    except gspread.exceptions.WorksheetNotFound:
        signals = []
    signal_lookup = build_signal_lookup(signals)
    sent_ids = existing_alert_ids(alert_log_records(sheet))
    alerts = pending_alerts(review_rows, sent_ids, signal_lookup=signal_lookup)

    print(f"Alert candidates: {len(alerts)}")
    if dry_run:
        for _, _, message in alerts:
            print("\n--- DRY RUN TELEGRAM MESSAGE ---")
            print(message)
        print("Dry-run complete: no Telegram messages sent and no alert log written.")
        return alerts

    token, chat_id = telegram_credentials()

    sent_at = now_iso()
    log_records = []
    for alert_id, row, message in alerts:
        send_telegram_message(token, chat_id, message)
        log_records.append(alert_log_record(alert_id, row, message, sent_at))

    append_alert_log(sheet, log_records)
    print(f"Telegram alerts sent: {len(log_records)}")
    return alerts


def send_test_message():
    load_dotenv()
    token, chat_id = telegram_credentials()
    message = build_test_message()
    send_telegram_message(token, chat_id, message)
    print("Telegram test message sent")
    return message


def validate_dry_run_logic():
    rows = [
        {
            "review_id": "review_1",
            "status": "NEW",
            "review_today": "NO",
            "score": "72",
            "score_band": "MEDIUM",
            "ticker": "ABC",
            "entity_name": "ABC Corp",
            "score_reason": "priority MEDIUM +30",
            "priority_reason": "Test priority",
            "opportunity_type": "TICKER_ACTIVITY",
            "evidence_count": "2",
            "sources": "capitol_trades",
            "signal_ids": "signal_1; signal_2",
        },
        {
            "review_id": "review_2",
            "status": "ACTIVE",
            "review_today": "NO",
            "score": "20",
            "score_band": "LOW",
            "ticker": "XYZ",
            "entity_name": "XYZ Corp",
            "score_reason": "priority LOW +10",
            "priority_reason": "Test priority",
            "opportunity_type": "TICKER_ACTIVITY",
            "evidence_count": "1",
            "sources": "sec_form4",
            "signal_ids": "signal_3",
        },
        {
            "review_id": "review_3",
            "status": "ACTIVE",
            "review_today": "NO",
            "score": "91",
            "score_band": "HIGH",
            "ticker": "HIG",
            "entity_name": "High Corp",
            "score_reason": "priority HIGH +50",
            "priority_reason": "Test priority",
            "opportunity_type": "CROSS_SOURCE",
            "evidence_count": "3",
            "sources": "capitol_trades;sec_form4",
            "signal_ids": "signal_4",
        },
        {
            "review_id": "review_4",
            "status": "CLOSED",
            "review_today": "NO",
            "score": "99",
            "score_band": "HIGH",
            "ticker": "OLD",
            "entity_name": "Closed Corp",
            "score_reason": "priority HIGH +50",
            "priority_reason": "Closed priority",
            "opportunity_type": "CROSS_SOURCE",
            "evidence_count": "3",
            "sources": "capitol_trades;sec_form4",
            "signal_ids": "signal_4",
        },
    ]
    signals = [
        {
            "signal_id": "signal_1",
            "signal_date": "2026-01-10",
            "signal_type": "PTR_BUY",
            "ticker": "ABC",
            "entity_name": "ABC Corp",
            "actor_name": "Rep. Example",
            "actor_type": "politician",
            "amount": "15K-50K",
            "source": "capitol_trades",
        },
        {
            "signal_id": "signal_2",
            "signal_date": "2026-01-11",
            "signal_type": "INSIDER_BUY",
            "ticker": "ABC",
            "entity_name": "ABC Corp",
            "actor_name": "Jane Insider",
            "actor_type": "insider",
            "amount": "120000",
            "source": "sec_form4",
        },
    ]
    signal_lookup = build_signal_lookup(signals)
    sent_ids = {make_alert_id(rows[0])}
    alerts = pending_alerts(rows, sent_ids, signal_lookup=signal_lookup)
    if len(alerts) != 1 or alerts[0][1]["review_id"] != "review_3":
        raise ValueError("Telegram deduplication failed")
    message = build_message(rows[0], signal_lookup=signal_lookup)
    for expected in [
        "Signal Radar",
        "Quien compra / actua: Rep. Example; Jane Insider",
        "Que compra / recibe / queda afectado: ABC - ABC Corp",
        "Vinculacion / relevancia:",
        "Scoring:",
        "Prioridad MEDIUM: +30",
        "abrir Google Sheets",
    ]:
        if expected not in message:
            raise ValueError(f"Telegram message missing expected text: {expected}")
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="Send Telegram alerts for review_queue.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a Telegram integration test message without reading Google Sheets.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.test:
        send_test_message()
        return
    send_alerts(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
