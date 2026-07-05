import argparse
import os
import sys
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
    nested_get,
    request_with_retries,
    require_unique,
    utc_run_id,
    write_csv,
    write_json,
)


API_URL = "https://api.congress.gov/v3/bill"
SOURCE = "congress_gov"
DEFAULT_LIMIT = 20
DEFAULT_MAX_PAGES = 2
FIELDNAMES = [
    "bill_id",
    "congress",
    "bill_type",
    "bill_number",
    "title",
    "origin_chamber",
    "introduced_date",
    "latest_action_date",
    "latest_action_text",
    "sponsor_name",
    "sponsor_bioguide_id",
    "policy_area",
    "signal_types",
    "source_url",
]


def normalize_bill(row):
    congress = clean_text(row.get("congress"))
    bill_type = clean_text(row.get("type"))
    bill_number = clean_text(row.get("number"))
    latest_action = row.get("latestAction") or {}
    sponsor = row.get("sponsors", [{}])[0] if row.get("sponsors") else {}
    policy_area = row.get("policyArea") or {}
    introduced_date = clean_text(row.get("introducedDate"))
    latest_action_date = clean_text(latest_action.get("actionDate"))
    signal_types = ["BILL_INTRODUCED"]
    if latest_action_date or latest_action.get("text"):
        signal_types.append("LEGISLATIVE_ACTION")
    action_text = clean_text(latest_action.get("text"))
    if "committee" in action_text.lower():
        signal_types.append("COMMITTEE_ACTION")
    return {
        "bill_id": f"{congress}-{bill_type}-{bill_number}".lower(),
        "congress": congress,
        "bill_type": bill_type,
        "bill_number": bill_number,
        "title": clean_text(row.get("title")),
        "origin_chamber": clean_text(row.get("originChamber")),
        "introduced_date": introduced_date,
        "latest_action_date": latest_action_date,
        "latest_action_text": action_text,
        "sponsor_name": clean_text(sponsor.get("fullName")),
        "sponsor_bioguide_id": clean_text(sponsor.get("bioguideId")),
        "policy_area": clean_text(policy_area.get("name")),
        "signal_types": ";".join(signal_types),
        "source_url": clean_text(row.get("url") or nested_get(row, ["latestAction", "url"], "")),
    }


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("Congress.gov returned no bill rows")
    require_unique([row["bill_id"] for row in rows], "congress_gov bill_id")
    for row in rows:
        if not row["bill_id"] or not row["congress"] or not row["bill_number"]:
            raise ValueError(f"Congress.gov row missing bill identity: {row}")
        if not row["introduced_date"] and not row["latest_action_date"]:
            raise ValueError(f"Congress.gov row missing useful dates: {row['bill_id']}")


def fetch_pages(api_key, limit, max_pages, raw_dir, run_id):
    session = requests.Session()
    rows = []
    seen_offsets = set()
    for page in range(max_pages):
        offset = page * limit
        if offset in seen_offsets:
            raise ValueError(f"Congress.gov duplicate offset: {offset}")
        seen_offsets.add(offset)
        params = {"api_key": api_key, "format": "json", "limit": limit, "offset": offset}
        response = request_with_retries(session, "GET", API_URL, params=params)
        data = response.json()
        write_json(raw_dir / f"{SOURCE}_{run_id}_page_{page + 1}.json", data)
        page_rows = data.get("bills") or []
        rows.extend(normalize_bill(row) for row in page_rows)
        print(f"Congress.gov page {page + 1}: {len(page_rows)} rows")
        pagination = data.get("pagination") or {}
        count = int(pagination.get("count") or len(page_rows))
        if not page_rows or offset + limit >= count:
            break
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect Congress.gov bill metadata.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="CONGRESS_GOV_API_KEY")
    parser.add_argument("--use-demo-key", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/congress_gov"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/congress_gov"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    if args.limit <= 0 or args.limit > 250:
        raise ValueError("--limit must be between 1 and 250")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    api_key = args.api_key or os.getenv(args.api_key_env, "")
    if not api_key and args.use_demo_key:
        api_key = "DEMO_KEY"
    if not api_key:
        raise ValueError(f"{args.api_key_env} is required unless --use-demo-key is set")
    run_id = args.run_id or utc_run_id()
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(api_key, args.limit, args.max_pages, args.raw_dir, run_id)
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
