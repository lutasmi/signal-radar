import argparse
import os
import sys
from datetime import date, timedelta
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


API_URL = "https://api.sam.gov/opportunities/v2/search"
SOURCE = "sam_gov"
DEFAULT_LIMIT = 100
DEFAULT_MAX_PAGES = 2
MAX_LIMIT = 1000
PTYPE_SIGNAL_TYPES = {
    "p": "CONTRACT_PRE_SOLICITATION",
    "a": "CONTRACT_AWARD_NOTICE",
    "u": "CONTRACT_SOLE_SOURCE",
    "o": "CONTRACT_OPPORTUNITY",
    "k": "CONTRACT_OPPORTUNITY",
}
FIELDNAMES = [
    "notice_id",
    "title",
    "solicitation_number",
    "posted_date",
    "response_deadline",
    "archive_date",
    "last_modified_date",
    "type",
    "base_type",
    "signal_type",
    "full_parent_path_name",
    "organization_type",
    "naics_code",
    "classification_code",
    "active",
    "award_number",
    "award_amount",
    "award_date",
    "awardee_name",
    "awardee_uei",
    "ui_link",
    "source_url",
]


def mmddyyyy(value):
    return value.strftime("%m/%d/%Y")


def default_date_window(days):
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    return mmddyyyy(start_date), mmddyyyy(end_date)


def signal_type_for(row):
    notice_type = clean_text(row.get("type")).lower()
    base_type = clean_text(row.get("baseType")).lower()
    text = f"{notice_type} {base_type}"
    if "award" in text:
        return "CONTRACT_AWARD_NOTICE"
    if "pre" in text and "solicitation" in text:
        return "CONTRACT_PRE_SOLICITATION"
    if "sole source" in text or "justification" in text:
        return "CONTRACT_SOLE_SOURCE"
    return "CONTRACT_OPPORTUNITY"


def normalize_opportunity(row):
    return {
        "notice_id": clean_text(row.get("noticeId")),
        "title": clean_text(row.get("title")),
        "solicitation_number": clean_text(row.get("solicitationNumber")),
        "posted_date": clean_text(row.get("postedDate"))[:10],
        "response_deadline": clean_text(row.get("responseDeadLine"))[:10],
        "archive_date": clean_text(row.get("archiveDate"))[:10],
        "last_modified_date": clean_text(
            row.get("modifiedDate")
            or row.get("lastModifiedDate")
            or row.get("updatedDate")
        )[:10],
        "type": clean_text(row.get("type")),
        "base_type": clean_text(row.get("baseType")),
        "signal_type": signal_type_for(row),
        "full_parent_path_name": clean_text(row.get("fullParentPathName")),
        "organization_type": clean_text(row.get("organizationType")),
        "naics_code": clean_text(row.get("naicsCode")),
        "classification_code": clean_text(row.get("classificationCode")),
        "active": clean_text(row.get("active")),
        "award_number": clean_text(nested_get(row, ["award", "number"])),
        "award_amount": clean_text(nested_get(row, ["award", "amount"])),
        "award_date": clean_text(nested_get(row, ["award", "date"]))[:10],
        "awardee_name": clean_text(nested_get(row, ["award", "awardee", "name"])),
        "awardee_uei": clean_text(nested_get(row, ["award", "awardee", "ueiSAM"])),
        "ui_link": clean_text(row.get("uiLink")),
        "source_url": clean_text(nested_get(row, ["links", 0, "href"], API_URL)),
    }


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("SAM.gov returned no opportunity rows")
    require_unique([row["notice_id"] for row in rows], "sam_gov notice_id")
    for row in rows:
        if not row["notice_id"]:
            raise ValueError("SAM.gov row missing notice_id")
        if not row["posted_date"]:
            raise ValueError(f"SAM.gov row missing posted_date: {row['notice_id']}")
        if not row["type"]:
            raise ValueError(f"SAM.gov row missing opportunity type: {row['notice_id']}")
        if row["signal_type"] not in set(PTYPE_SIGNAL_TYPES.values()):
            raise ValueError(f"unexpected SAM.gov signal_type: {row['signal_type']}")


def fetch_pages(api_key, posted_from, posted_to, ptype, limit, max_pages, raw_dir, run_id):
    session = requests.Session()
    rows = []
    seen_offsets = set()
    for page in range(max_pages):
        offset = page * limit
        if offset in seen_offsets:
            raise ValueError(f"SAM.gov duplicate offset: {offset}")
        seen_offsets.add(offset)
        params = {
            "api_key": api_key,
            "postedFrom": posted_from,
            "postedTo": posted_to,
            "limit": limit,
            "offset": offset,
        }
        if ptype:
            params["ptype"] = ptype
        response = request_with_retries(session, "GET", API_URL, params=params)
        data = response.json()
        write_json(raw_dir / f"{SOURCE}_{run_id}_page_{page + 1}.json", data)
        page_rows = data.get("opportunitiesData") or []
        rows.extend(normalize_opportunity(row) for row in page_rows)
        print(f"SAM.gov page {page + 1}: {len(page_rows)} rows")
        total = int(data.get("totalRecords") or 0)
        if not page_rows or offset + limit >= total:
            break
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect SAM.gov Contract Opportunities.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="SAM_GOV_API_KEY")
    parser.add_argument("--posted-from", default="")
    parser.add_argument("--posted-to", default="")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--ptype", default="", help="SAM.gov ptype such as p, a, u, o, k.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/sam_gov"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/sam_gov"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    if args.days <= 0:
        raise ValueError("--days must be greater than 0")
    if args.limit <= 0 or args.limit > MAX_LIMIT:
        raise ValueError(f"--limit must be between 1 and {MAX_LIMIT}")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    if args.ptype and args.ptype not in PTYPE_SIGNAL_TYPES:
        raise ValueError(f"--ptype must be one of: {', '.join(sorted(PTYPE_SIGNAL_TYPES))}")
    api_key = args.api_key or os.getenv(args.api_key_env, "")
    if not api_key:
        raise SourceAuthenticationError(f"{args.api_key_env} is required for SAM.gov")
    posted_from = args.posted_from
    posted_to = args.posted_to
    if not posted_from or not posted_to:
        posted_from, posted_to = default_date_window(args.days)
    run_id = args.run_id or utc_run_id()
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(
        api_key,
        posted_from,
        posted_to,
        args.ptype,
        args.limit,
        args.max_pages,
        args.raw_dir,
        run_id,
    )
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
