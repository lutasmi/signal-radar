import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["requests"])

import requests

from radar.candidate_sources import (
    clean_text,
    ensure_output_dirs,
    join_values,
    nested_get,
    parse_iso_date,
    request_with_retries,
    require_unique,
    utc_run_id,
    write_csv,
    write_json,
)


API_URL = "https://lda.senate.gov/api/v1/filings/"
SOURCE = "lda_gov"
DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 2
FIELDNAMES = [
    "filing_uuid",
    "filing_type",
    "filing_type_display",
    "filing_year",
    "filing_period",
    "posted_date",
    "registrant_id",
    "registrant_name",
    "client_id",
    "client_name",
    "income",
    "expenses",
    "issue_codes",
    "issue_descriptions",
    "government_entities",
    "signal_types",
    "source_url",
]


def normalize_filing(row):
    activities = row.get("lobbying_activities") or []
    issue_codes = []
    issue_descriptions = []
    government_entities = []
    for activity in activities:
        issue_codes.append(activity.get("general_issue_code"))
        issue_descriptions.append(activity.get("general_issue_code_display"))
        for entity in activity.get("government_entities") or []:
            government_entities.append(entity.get("name"))
    signal_types = ["LOBBY_CLIENT_ACTIVITY"]
    if row.get("filing_type") in {"RR", "RA"}:
        signal_types.append("LOBBY_NEW_REGISTRATION")
    if row.get("income") or row.get("expenses"):
        signal_types.append("LOBBY_SPEND_INCREASE")
    if issue_codes:
        signal_types.append("LOBBY_ISSUE_MATCH")
    return {
        "filing_uuid": clean_text(row.get("filing_uuid")),
        "filing_type": clean_text(row.get("filing_type")),
        "filing_type_display": clean_text(row.get("filing_type_display")),
        "filing_year": clean_text(row.get("filing_year")),
        "filing_period": clean_text(row.get("filing_period")),
        "posted_date": parse_iso_date(row.get("dt_posted")),
        "registrant_id": clean_text(nested_get(row, ["registrant", "id"])),
        "registrant_name": clean_text(nested_get(row, ["registrant", "name"])),
        "client_id": clean_text(nested_get(row, ["client", "id"])),
        "client_name": clean_text(nested_get(row, ["client", "name"])),
        "income": clean_text(row.get("income")),
        "expenses": clean_text(row.get("expenses")),
        "issue_codes": join_values(issue_codes),
        "issue_descriptions": join_values(issue_descriptions),
        "government_entities": join_values(government_entities),
        "signal_types": ";".join(signal_types),
        "source_url": clean_text(row.get("url") or row.get("filing_document_url")),
    }


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("LDA.gov returned no filing rows")
    require_unique([row["filing_uuid"] for row in rows], "lda_gov filing_uuid")
    for row in rows:
        if not row["filing_uuid"]:
            raise ValueError("LDA.gov row missing filing_uuid")
        if not row["posted_date"] and not row["filing_year"]:
            raise ValueError(f"LDA.gov row missing usable date: {row['filing_uuid']}")
        if not row["registrant_name"] or not row["client_name"]:
            raise ValueError(f"LDA.gov row missing registrant/client: {row['filing_uuid']}")


def fetch_pages(filing_year, page_size, max_pages, raw_dir, run_id):
    session = requests.Session()
    rows = []
    seen_pages = set()
    for page in range(1, max_pages + 1):
        if page in seen_pages:
            raise ValueError(f"LDA.gov duplicate page: {page}")
        seen_pages.add(page)
        params = {"page_size": page_size, "page": page, "ordering": "-dt_posted"}
        if filing_year:
            params["filing_year"] = filing_year
        response = request_with_retries(session, "GET", API_URL, params=params)
        data = response.json()
        write_json(raw_dir / f"{SOURCE}_{run_id}_page_{page}.json", data)
        page_rows = data.get("results") or []
        rows.extend(normalize_filing(row) for row in page_rows)
        print(f"LDA.gov page {page}: {len(page_rows)} rows")
        if not data.get("next") or not page_rows:
            break
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect LDA.gov lobbying filings.")
    parser.add_argument("--filing-year", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/lda_gov"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/lda_gov"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.page_size <= 0 or args.page_size > 100:
        raise ValueError("--page-size must be between 1 and 100")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    run_id = args.run_id or utc_run_id()
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(args.filing_year, args.page_size, args.max_pages, args.raw_dir, run_id)
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
