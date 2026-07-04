import argparse
import csv
import time
from datetime import date, timedelta
from pathlib import Path

import requests


API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
OUTPUT_FILE = Path("data/usaspending_latest.csv")
DEFAULT_DAYS = 90
DEFAULT_LIMIT = 100
DEFAULT_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 5

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


def build_payload(start_date, end_date, limit, page):
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
        "sort": "Start Date",
        "order": "desc",
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


def fetch_usaspending_contracts(days=DEFAULT_DAYS, limit=DEFAULT_LIMIT):
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    payload = build_payload(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        limit=limit,
        page=1,
    )

    response = post_with_retries(API_URL, payload)
    results = response.json().get("results", [])
    return [normalize_award(row) for row in results]


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
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.days <= 0:
        raise ValueError("--days must be greater than 0")
    if args.limit <= 0:
        raise ValueError("--limit must be greater than 0")

    contracts = fetch_usaspending_contracts(days=args.days, limit=args.limit)
    write_csv(contracts, args.output)

    print(f"Endpoint: {API_URL}")
    print(f"Recent window: last {args.days} days")
    print(f"Contracts extracted: {len(contracts)}")
    print(f"CSV generated: {args.output}")
    print_sample_rows(contracts)
