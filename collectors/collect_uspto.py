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
    SourceAuthenticationError,
    clean_text,
    ensure_output_dirs,
    nested_get,
    request_with_retries,
    require_unique,
    utc_run_id,
    write_csv,
    write_json,
)


API_URL = "https://api.uspto.gov/api/v1/patent/applications/search"
SOURCE = "uspto"
DEFAULT_ROWS = 20
DEFAULT_MAX_PAGES = 1
FIELDNAMES = [
    "application_number",
    "patent_number",
    "filing_date",
    "publication_date",
    "title",
    "applicant_name",
    "assignee_name",
    "inventor_names",
    "signal_type",
    "source_url",
]


def list_names(values):
    if not isinstance(values, list):
        return ""
    names = []
    for value in values:
        if isinstance(value, dict):
            names.append(value.get("name") or value.get("fullName"))
        else:
            names.append(value)
    return "; ".join(clean_text(name) for name in names if clean_text(name))


def normalize_application(row):
    application_number = clean_text(
        row.get("applicationNumberText")
        or row.get("applicationNumber")
        or row.get("applicationMetaData", {}).get("applicationNumberText")
    )
    patent_number = clean_text(row.get("patentNumber") or nested_get(row, ["patentTermAdjustmentData", "patentNumber"]))
    return {
        "application_number": application_number,
        "patent_number": patent_number,
        "filing_date": clean_text(row.get("filingDate") or nested_get(row, ["applicationMetaData", "filingDate"]))[:10],
        "publication_date": clean_text(row.get("publicationDate") or nested_get(row, ["applicationMetaData", "publicationDate"]))[:10],
        "title": clean_text(row.get("inventionTitle") or nested_get(row, ["applicationMetaData", "inventionTitle"])),
        "applicant_name": clean_text(row.get("applicantName") or nested_get(row, ["applicationMetaData", "applicantName"])),
        "assignee_name": clean_text(row.get("assigneeName") or nested_get(row, ["assignmentData", "assigneeName"])),
        "inventor_names": list_names(row.get("inventorBag") or row.get("inventors")),
        "signal_type": "PATENT_ACTIVITY_CLUSTER",
        "source_url": f"https://patentcenter.uspto.gov/applications/{application_number}" if application_number else "",
    }


def extract_results(data):
    for path in (
        ["patentFileWrapperDataBag"],
        ["results"],
        ["data", "results"],
        ["data", "patentFileWrapperDataBag"],
    ):
        value = nested_get(data, path, None)
        if isinstance(value, list):
            return value
    return []


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("USPTO returned no application rows")
    require_unique([row["application_number"] for row in rows], "uspto application_number")
    for row in rows:
        if not row["application_number"]:
            raise ValueError("USPTO row missing application_number")
        if not row["filing_date"] and not row["publication_date"]:
            raise ValueError(f"USPTO row missing useful dates: {row['application_number']}")


def fetch_pages(api_key, query, rows_per_page, max_pages, raw_dir, run_id):
    session = requests.Session()
    rows = []
    headers = {"X-API-KEY": api_key} if api_key else {}
    for page in range(max_pages):
        payload = {"q": query, "pagination": {"offset": page * rows_per_page, "limit": rows_per_page}}
        response = request_with_retries(session, "POST", API_URL, json_body=payload, headers=headers)
        data = response.json()
        write_json(raw_dir / f"{SOURCE}_{run_id}_page_{page + 1}.json", data)
        page_rows = extract_results(data)
        rows.extend(normalize_application(row) for row in page_rows)
        print(f"USPTO page {page + 1}: {len(page_rows)} rows")
        if len(page_rows) < rows_per_page:
            break
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect USPTO patent application search data.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="USPTO_API_KEY")
    parser.add_argument("--query", default="Tesla")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/uspto"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/uspto"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    if args.rows <= 0 or args.rows > 100:
        raise ValueError("--rows must be between 1 and 100")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    api_key = args.api_key or os.getenv(args.api_key_env, "")
    if not api_key:
        raise SourceAuthenticationError(
            f"{args.api_key_env} is required for the official USPTO API endpoint"
        )
    run_id = args.run_id or utc_run_id()
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(api_key, args.query, args.rows, args.max_pages, args.raw_dir, run_id)
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
