import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["requests"])

import requests


API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
OUTPUT_FILE = Path("data/usaspending_latest.csv")
DEFAULT_DAYS = 90
DEFAULT_LIMIT = 100
DEFAULT_MAX_PAGES = 5
DEFAULT_TOP_AMOUNT_PAGES = 2
DEFAULT_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 5
MAX_API_LIMIT = 100

FIELDNAMES = [
    "award_id",
    "recipient_name",
    "awarding_agency_name",
    "award_amount",
    "period_of_performance_start_date",
    "description",
    "source_url",
]


def award_source_url(row):
    generated_id = row.get("generated_internal_id") or row.get("internal_id")
    if generated_id:
        return f"https://www.usaspending.gov/award/{generated_id}"
    return API_URL


def build_payload(start_date, end_date, limit, page, sort="Start Date", order="desc"):
    return {
        "filters": {
            "time_period": [
                {
                    "start_date": start_date,
                    "end_date": end_date,
                }
            ],
            "award_type_codes": ["A", "B", "C", "D"],
            "agencies": [
                {
                    "type": "awarding",
                    "tier": "toptier",
                    "name": "Department of Defense",
                }
            ],
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Awarding Agency",
            "Award Amount",
            "Start Date",
            "Description",
        ],
        "sort": sort,
        "order": order,
        "limit": limit,
        "page": page,
    }


def normalize_award(row):
    return {
        "award_id": row.get("Award ID", ""),
        "recipient_name": row.get("Recipient Name", ""),
        "awarding_agency_name": row.get("Awarding Agency", ""),
        "award_amount": row.get("Award Amount", ""),
        "period_of_performance_start_date": row.get("Start Date", ""),
        "description": row.get("Description", ""),
        "source_url": award_source_url(row),
    }


def post_with_retries(
    url,
    payload,
    attempts=DEFAULT_ATTEMPTS,
    retry_delay=DEFAULT_RETRY_DELAY_SECONDS,
):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.ok:
                return response
            last_error = RuntimeError(
                f"USASpending API error {response.status_code}: {response.text[:1000]}"
            )
        except requests.RequestException as exc:
            last_error = exc

        if attempt < attempts:
            print(
                f"USASpending request failed on attempt {attempt}/{attempts}: "
                f"{last_error}. Retrying in {retry_delay}s."
            )
            time.sleep(retry_delay)

    raise RuntimeError(
        f"USASpending API request failed after {attempts} attempts: {last_error}"
    )


def merge_unique_contracts(contract_groups):
    contracts = []
    seen_award_ids = set()

    for group in contract_groups:
        for contract in group:
            award_id = contract["award_id"]
            if award_id and award_id in seen_award_ids:
                continue
            if award_id:
                seen_award_ids.add(award_id)
            contracts.append(contract)

    return contracts


def fetch_contract_pages(
    start_date,
    end_date,
    limit=DEFAULT_LIMIT,
    max_pages=DEFAULT_MAX_PAGES,
    sort="Start Date",
    order="desc",
    label="USASpending",
):
    contracts = []
    seen_award_ids = set()

    for page in range(1, max_pages + 1):
        payload = build_payload(
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            page=page,
            sort=sort,
            order=order,
        )

        response = post_with_retries(API_URL, payload)
        data = response.json()
        results = data.get("results", [])
        if not results:
            break

        new_rows = 0
        for row in results:
            contract = normalize_award(row)
            award_id = contract["award_id"]
            if award_id and award_id in seen_award_ids:
                continue
            if award_id:
                seen_award_ids.add(award_id)
            contracts.append(contract)
            new_rows += 1

        print(
            f"{label} page {page}: "
            f"{len(results)} rows, {new_rows} new awards"
        )

        page_metadata = data.get("page_metadata") or {}
        if page_metadata.get("hasNext") is False:
            break
        if len(results) < limit:
            break

    return contracts


def fetch_usaspending_contracts(
    days=DEFAULT_DAYS,
    limit=DEFAULT_LIMIT,
    max_pages=DEFAULT_MAX_PAGES,
    top_amount_pages=DEFAULT_TOP_AMOUNT_PAGES,
):
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    start_text = start_date.isoformat()
    end_text = end_date.isoformat()

    recent_contracts = fetch_contract_pages(
        start_date=start_text,
        end_date=end_text,
        limit=limit,
        max_pages=max_pages,
        sort="Start Date",
        order="desc",
        label="USASpending recent",
    )

    amount_contracts = []
    if top_amount_pages:
        amount_contracts = fetch_contract_pages(
            start_date=start_text,
            end_date=end_text,
            limit=limit,
            max_pages=top_amount_pages,
            sort="Award Amount",
            order="desc",
            label="USASpending top amount",
        )

    return merge_unique_contracts([recent_contracts, amount_contracts])


def write_csv(contracts, output_file=OUTPUT_FILE):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(contracts)


def print_sample_rows(contracts, sample_size=5):
    print(f"\n--- EXAMPLE {min(sample_size, len(contracts))} ROWS ---")
    for row in contracts[:sample_size]:
        print(
            row["period_of_performance_start_date"],
            "|",
            row["recipient_name"],
            "|",
            row["awarding_agency_name"],
            "|",
            row["award_amount"],
            "|",
            row["award_id"],
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract recent Department of Defense contract awards from USASpending to CSV."
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--top-amount-pages", type=int, default=DEFAULT_TOP_AMOUNT_PAGES)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.days <= 0:
        raise ValueError("--days must be greater than 0")
    if args.limit <= 0:
        raise ValueError("--limit must be greater than 0")
    if args.limit > MAX_API_LIMIT:
        raise ValueError(f"--limit cannot be greater than {MAX_API_LIMIT}")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    if args.top_amount_pages < 0:
        raise ValueError("--top-amount-pages cannot be negative")

    contracts = fetch_usaspending_contracts(
        days=args.days,
        limit=args.limit,
        max_pages=args.max_pages,
        top_amount_pages=args.top_amount_pages,
    )
    write_csv(contracts, args.output)

    print(f"Endpoint: {API_URL}")
    print(f"Recent window: last {args.days} days")
    print(f"Recent pages: {args.max_pages}")
    print(f"Top amount pages: {args.top_amount_pages}")
    print(f"Contracts extracted: {len(contracts)}")
    print(f"CSV generated: {args.output}")
    print_sample_rows(contracts)
