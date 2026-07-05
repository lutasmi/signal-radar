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
    parse_us_date,
    request_with_retries,
    require_unique,
    utc_run_id,
    write_csv,
    write_json,
)


API_URL = "https://api.grants.gov/v1/api/search2"
SOURCE = "grants_gov"
DEFAULT_ROWS = 20
DEFAULT_MAX_PAGES = 2
FIELDNAMES = [
    "opportunity_id",
    "opportunity_number",
    "title",
    "agency_code",
    "agency",
    "open_date",
    "close_date",
    "opportunity_status",
    "document_type",
    "cfda_list",
    "signal_type",
    "source_url",
]


def normalize_opportunity(row):
    status = clean_text(row.get("oppStatus")).lower()
    signal_type = "GRANT_FORECAST" if status == "forecasted" else "GRANT_OPPORTUNITY"
    return {
        "opportunity_id": clean_text(row.get("id")),
        "opportunity_number": clean_text(row.get("number")),
        "title": clean_text(row.get("title")),
        "agency_code": clean_text(row.get("agencyCode")),
        "agency": clean_text(row.get("agency")),
        "open_date": parse_us_date(row.get("openDate")) if row.get("openDate") else "",
        "close_date": parse_us_date(row.get("closeDate")) if row.get("closeDate") else "",
        "opportunity_status": status,
        "document_type": clean_text(row.get("docType")),
        "cfda_list": join_values(row.get("cfdaList") or []),
        "signal_type": signal_type,
        "source_url": f"https://www.grants.gov/search-results-detail/{clean_text(row.get('id'))}",
    }


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("Grants.gov returned no opportunity rows")
    require_unique([row["opportunity_id"] for row in rows], "grants_gov opportunity_id")
    for row in rows:
        if not row["opportunity_id"] or not row["opportunity_number"]:
            raise ValueError(f"Grants.gov row missing opportunity identity: {row}")
        if not row["open_date"]:
            raise ValueError(f"Grants.gov row missing open_date: {row['opportunity_id']}")


def fetch_pages(keyword, statuses, rows_per_page, max_pages, raw_dir, run_id):
    session = requests.Session()
    rows = []
    seen_starts = set()
    for page in range(max_pages):
        start = page * rows_per_page
        if start in seen_starts:
            raise ValueError(f"Grants.gov duplicate startRecordNum: {start}")
        seen_starts.add(start)
        payload = {
            "rows": rows_per_page,
            "startRecordNum": start,
            "keyword": keyword,
            "oppStatuses": statuses,
            "resultType": "json",
        }
        response = request_with_retries(session, "POST", API_URL, json_body=payload)
        data = response.json()
        if "token" in data:
            data["token"] = "[REDACTED]"
        write_json(raw_dir / f"{SOURCE}_{run_id}_page_{page + 1}.json", data)
        if data.get("errorcode") not in (0, "0", None):
            raise ValueError(f"Grants.gov API error: {data.get('msg')}")
        api_data = data.get("data") or {}
        page_rows = api_data.get("oppHits") or []
        rows.extend(normalize_opportunity(row) for row in page_rows)
        print(f"Grants.gov page {page + 1}: {len(page_rows)} rows")
        hit_count = int(api_data.get("hitCount") or len(page_rows))
        if not page_rows or start + rows_per_page >= hit_count:
            break
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect Grants.gov opportunities.")
    parser.add_argument("--keyword", default="energy")
    parser.add_argument("--statuses", default="forecasted|posted")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/grants_gov"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/grants_gov"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.rows <= 0 or args.rows > 100:
        raise ValueError("--rows must be between 1 and 100")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    run_id = args.run_id or utc_run_id()
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(args.keyword, args.statuses, args.rows, args.max_pages, args.raw_dir, run_id)
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
