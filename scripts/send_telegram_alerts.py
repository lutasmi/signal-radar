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

from radar.records import get_value, rows_to_dicts, stable_id


REVIEW_QUEUE_WORKSHEET_NAME = "review_queue"
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
    return (
        get_value(row, "status") == "NEW"
        or get_value(row, "review_today") == "YES"
        or get_value(row, "score_band") == "HIGH"
    )


def build_message(row):
    subject = get_value(row, "ticker") or get_value(row, "entity_name") or "Opportunity"
    lines = [
        f"Signal Radar: {get_value(row, 'score_band') or 'UNSCORED'}",
        "",
        f"Ticker / Entity: {subject}",
        f"Status: {get_value(row, 'status')}",
        f"Score: {get_value(row, 'score')}",
        f"Reason: {get_value(row, 'score_reason') or get_value(row, 'priority_reason')}",
        f"Sources: {get_value(row, 'sources')}",
        "",
        "Review: Open Google Sheets",
    ]
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


def pending_alerts(review_rows, sent_alert_ids):
    alerts = []
    for row in review_rows:
        if not should_alert(row):
            continue
        alert_id = make_alert_id(row)
        if alert_id in sent_alert_ids:
            continue
        alerts.append((alert_id, row, build_message(row)))
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
    from radar.sheets import open_or_create_worksheet

    if not records:
        return
    worksheet = open_or_create_worksheet(
        sheet,
        ALERT_LOG_WORKSHEET_NAME,
        row_count=len(records),
        column_count=len(ALERT_LOG_HEADER),
    )
    existing_values = worksheet.get_all_values()
    if not existing_values:
        worksheet.append_row(ALERT_LOG_HEADER, value_input_option="RAW")
    elif existing_values[0] != ALERT_LOG_HEADER:
        worksheet.insert_row(ALERT_LOG_HEADER, 1, value_input_option="RAW")
    worksheet.append_rows(
        [[record[column] for column in ALERT_LOG_HEADER] for record in records],
        value_input_option="RAW",
    )


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
    sent_ids = existing_alert_ids(alert_log_records(sheet))
    alerts = pending_alerts(review_rows, sent_ids)

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
            "sources": "capitol_trades",
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
            "sources": "sec_form4",
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
            "sources": "capitol_trades;sec_form4",
        },
    ]
    sent_ids = {make_alert_id(rows[0])}
    alerts = pending_alerts(rows, sent_ids)
    if len(alerts) != 1 or alerts[0][1]["review_id"] != "review_3":
        raise ValueError("Telegram deduplication failed")
    message = build_message(rows[0])
    for expected in ["Signal Radar", "ABC", "Score: 72", "Open Google Sheets"]:
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
