import argparse
import csv
import glob
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["dotenv", "requests"])

import requests
from dotenv import load_dotenv


SOURCES = [
    {
        "name": "Congress PTR (House + Senate)",
        "env": "CONGRESS_PTR_OUTCOME",
        "csv": Path("data/congress_ptr_latest.csv"),
    },
    {
        "name": "SEC Form 4",
        "env": "SEC_FORM4_OUTCOME",
        "csv": Path("data/sec_form4_latest.csv"),
    },
    {
        "name": "USASpending",
        "env": "USASPENDING_OUTCOME",
        "csv": Path("data/usaspending_latest.csv"),
    },
    {
        "name": "SAM.gov",
        "env": "SAM_GOV_OUTCOME",
        "pattern": "data/processed/sam_gov/sam_gov_*.csv",
    },
    {
        "name": "Congress.gov",
        "env": "CONGRESS_GOV_OUTCOME",
        "pattern": "data/processed/congress_gov/congress_gov_*.csv",
    },
    {
        "name": "Federal Register",
        "env": "FEDERAL_REGISTER_OUTCOME",
        "pattern": "data/processed/federal_register/federal_register_*.csv",
    },
    {
        "name": "LDA.gov",
        "env": "LDA_GOV_OUTCOME",
        "pattern": "data/processed/lda_gov/lda_gov_*.csv",
    },
    {
        "name": "SEC EDGAR additional",
        "env": "SEC_EDGAR_ADDITIONAL_OUTCOME",
        "pattern": "data/processed/sec_edgar_additional/sec_edgar_additional_*.csv",
    },
    {
        "name": "FEC",
        "env": "FEC_OUTCOME",
        "pattern": "data/processed/fec/fec_*.csv",
    },
    {
        "name": "Grants.gov",
        "env": "GRANTS_GOV_OUTCOME",
        "pattern": "data/processed/grants_gov/grants_gov_*.csv",
    },
]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def count_csv_records(csv_file):
    if not csv_file.exists():
        return None
    with csv_file.open("r", newline="", encoding="utf-8") as f:
        return max(sum(1 for _ in csv.reader(f)) - 1, 0)


def source_csv(source):
    if "csv" in source:
        return source["csv"]
    matches = sorted(glob.glob(source["pattern"]))
    return Path(matches[-1]) if matches else None


def source_status(source):
    outcome = os.getenv(source["env"], "unknown").strip() or "unknown"
    csv_file = source_csv(source)
    records = count_csv_records(csv_file) if csv_file else None
    problems = []

    if outcome != "success":
        problems.append(f"collector outcome: {outcome}")
    if outcome == "success" and records is None:
        problems.append("CSV not found")

    return {
        "name": source["name"],
        "outcome": outcome,
        "records": records,
        "problems": problems,
    }


def build_capture_summary(statuses=None):
    statuses = statuses or [
        source_status(source) for source in SOURCES if os.getenv(source["env"]) is not None
    ]
    lines = [
        "Signal Radar capture",
        "",
        f"UTC: {now_iso()}",
        "",
        "Sources consulted:",
    ]

    for status in statuses:
        records = "unknown" if status["records"] is None else str(status["records"])
        lines.append(
            f"- {status['name']}: {status['outcome']} | records extracted: {records}"
        )

    problems = [
        f"- {status['name']}: {problem}"
        for status in statuses
        for problem in status["problems"]
    ]
    lines.append("")
    lines.append("Problems:")
    lines.extend(problems or ["- none"])

    return "\n".join(lines)


def telegram_credentials():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return token, chat_id


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


def send_capture_summary(dry_run=False):
    load_dotenv()
    message = build_capture_summary()
    if dry_run:
        print(message)
        print("Dry-run complete: no Telegram message sent.")
        return message

    token, chat_id = telegram_credentials()
    send_telegram_message(token, chat_id, message)
    print("Capture summary sent")
    return message


def validate_capture_summary_logic():
    statuses = [
        {
            "name": "Capitol Trades",
            "outcome": "success",
            "records": 12,
            "problems": [],
        },
        {
            "name": "SEC Form 4",
            "outcome": "failure",
            "records": None,
            "problems": ["collector outcome: failure"],
        },
    ]
    message = build_capture_summary(statuses)
    for expected in [
        "Signal Radar capture",
        "Capitol Trades: success | records extracted: 12",
        "SEC Form 4: failure | records extracted: unknown",
        "SEC Form 4: collector outcome: failure",
    ]:
        if expected not in message:
            raise ValueError(f"Capture summary missing expected text: {expected}")
    return True


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send a Telegram summary for the source collection step."
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    send_capture_summary(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
