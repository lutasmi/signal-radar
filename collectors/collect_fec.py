import argparse
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["dotenv", "requests"])

import requests
from dotenv import load_dotenv

from radar.candidate_sources import (
    clean_text,
    ensure_output_dirs,
    parse_iso_date,
    request_with_retries,
    require_unique,
    utc_run_id,
    write_csv,
    write_json,
)


API_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
SOURCE = "fec"
DEFAULT_PER_PAGE = 20
DEFAULT_MAX_PAGES = 2
FIELDNAMES = [
    "transaction_id",
    "committee_id",
    "committee_name",
    "contributor_name",
    "contributor_employer",
    "contributor_occupation",
    "contributor_city",
    "contributor_state",
    "contribution_receipt_date",
    "contribution_receipt_amount",
    "two_year_transaction_period",
    "memo_text",
    "signal_types",
    "source_url",
]


def normalize_contribution(row):
    signal_types = ["POLITICAL_CONTRIBUTION"]
    if row.get("committee_id"):
        signal_types.append("PAC_CONTRIBUTION")
    employer = clean_text(row.get("contributor_employer"))
    occupation = clean_text(row.get("contributor_occupation"))
    if "executive" in occupation.lower() or "ceo" in occupation.lower():
        signal_types.append("EXECUTIVE_CONTRIBUTION")
    return {
        "transaction_id": clean_text(row.get("transaction_id") or row.get("sub_id")),
        "committee_id": clean_text(row.get("committee_id")),
        "committee_name": clean_text(row.get("committee", {}).get("name") or row.get("committee_name")),
        "contributor_name": clean_text(row.get("contributor_name")),
        "contributor_employer": employer,
        "contributor_occupation": occupation,
        "contributor_city": clean_text(row.get("contributor_city")),
        "contributor_state": clean_text(row.get("contributor_state")),
        "contribution_receipt_date": parse_iso_date(row.get("contribution_receipt_date")),
        "contribution_receipt_amount": clean_text(row.get("contribution_receipt_amount")),
        "two_year_transaction_period": clean_text(row.get("two_year_transaction_period")),
        "memo_text": clean_text(row.get("memo_text")),
        "signal_types": ";".join(signal_types),
        "source_url": f"https://www.fec.gov/data/receipts/individual-contributions/?data_type=processed&two_year_transaction_period={clean_text(row.get('two_year_transaction_period'))}",
    }


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("FEC returned no contribution rows")
    require_unique([row["transaction_id"] for row in rows], "fec transaction_id")
    for row in rows:
        if not row["transaction_id"]:
            raise ValueError("FEC row missing transaction_id")
        if not row["contributor_name"]:
            raise ValueError(f"FEC row missing contributor_name: {row['transaction_id']}")
        if not row["contribution_receipt_date"]:
            raise ValueError(f"FEC row missing contribution date: {row['transaction_id']}")


def count_unusable_rows(rows):
    return sum(1 for row in rows if not row.get("contribution_receipt_date"))


def default_min_date(period):
    return f"{period - 1}-01-01"


def default_max_date(period):
    current_year = date.today().year
    if period >= current_year:
        return f"{period}-12-31"
    return f"{period}-12-31"


def fetch_pages(
    api_key,
    period,
    min_date,
    max_date,
    per_page,
    max_pages,
    contributor_name,
    raw_dir,
    run_id,
):
    session = requests.Session()
    rows = []
    last_indexes = None
    for page in range(1, max_pages + 1):
        params = {
            "api_key": api_key,
            "per_page": per_page,
            "sort": "-contribution_receipt_date",
            "two_year_transaction_period": period,
        }
        if min_date:
            params["min_date"] = min_date
        if max_date:
            params["max_date"] = max_date
        if contributor_name:
            params["contributor_name"] = contributor_name
        if last_indexes:
            params.update(last_indexes)
        response = request_with_retries(session, "GET", API_URL, params=params)
        data = response.json()
        write_json(raw_dir / f"{SOURCE}_{run_id}_page_{page}.json", data)
        page_rows = data.get("results") or []
        rows.extend(normalize_contribution(row) for row in page_rows)
        print(f"FEC page {page}: {len(page_rows)} rows")
        pagination = data.get("pagination") or {}
        last_indexes = pagination.get("last_indexes")
        if not page_rows or not last_indexes:
            break
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect FEC individual contribution records.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="FEC_API_KEY")
    parser.add_argument("--use-demo-key", action="store_true")
    parser.add_argument("--period", type=int, default=2026)
    parser.add_argument("--min-date", default="")
    parser.add_argument("--max-date", default="")
    parser.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--contributor-name", default="")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/fec"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/fec"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    if args.per_page <= 0 or args.per_page > 100:
        raise ValueError("--per-page must be between 1 and 100")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    api_key = args.api_key or os.getenv(args.api_key_env, "")
    if not api_key and args.use_demo_key:
        api_key = "DEMO_KEY"
    if not api_key:
        raise ValueError(f"{args.api_key_env} is required unless --use-demo-key is set")
    run_id = args.run_id or utc_run_id()
    min_date = args.min_date or default_min_date(args.period)
    max_date = args.max_date or default_max_date(args.period)
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(
        api_key,
        args.period,
        min_date,
        max_date,
        args.per_page,
        args.max_pages,
        args.contributor_name,
        args.raw_dir,
        run_id,
    )
    print(f"FEC rows without usable contribution date: {count_unusable_rows(rows)}")
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
