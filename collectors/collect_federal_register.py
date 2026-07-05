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
    request_with_retries,
    require_unique,
    utc_run_id,
    write_csv,
    write_json,
)


API_URL = "https://www.federalregister.gov/api/v1/documents.json"
SOURCE = "federal_register"
DEFAULT_PER_PAGE = 20
DEFAULT_MAX_PAGES = 2
TYPE_SIGNAL_TYPES = {
    "Rule": "RULE_FINAL",
    "Proposed Rule": "RULE_PROPOSED",
    "Notice": "AGENCY_NOTICE",
    "Presidential Document": "EXECUTIVE_ACTION",
}
FIELDNAMES = [
    "document_number",
    "publication_date",
    "type",
    "signal_type",
    "title",
    "agencies",
    "agency_slugs",
    "cfr_references",
    "html_url",
    "pdf_url",
    "abstract",
]


def normalize_document(row):
    agencies = row.get("agencies") or []
    cfr_refs = row.get("cfr_references") or []
    doc_type = clean_text(row.get("type"))
    signal_type = TYPE_SIGNAL_TYPES.get(doc_type, "REGULATORY_CATALYST")
    return {
        "document_number": clean_text(row.get("document_number")),
        "publication_date": clean_text(row.get("publication_date")),
        "type": doc_type,
        "signal_type": signal_type,
        "title": clean_text(row.get("title")),
        "agencies": join_values(agency.get("name") for agency in agencies),
        "agency_slugs": join_values(agency.get("slug") for agency in agencies),
        "cfr_references": join_values(
            f"{ref.get('title')} CFR {ref.get('part')}" for ref in cfr_refs
        ),
        "html_url": clean_text(row.get("html_url")),
        "pdf_url": clean_text(row.get("pdf_url")),
        "abstract": clean_text(row.get("abstract")),
    }


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("Federal Register returned no document rows")
    require_unique([row["document_number"] for row in rows], "federal_register document_number")
    for row in rows:
        if not row["document_number"]:
            raise ValueError("Federal Register row missing document_number")
        if not row["publication_date"]:
            raise ValueError(f"Federal Register row missing publication_date: {row['document_number']}")
        if row["signal_type"] not in set(TYPE_SIGNAL_TYPES.values()) | {"REGULATORY_CATALYST"}:
            raise ValueError(f"unexpected Federal Register signal_type: {row['signal_type']}")


def fetch_pages(document_type, per_page, max_pages, raw_dir, run_id):
    session = requests.Session()
    rows = []
    seen_pages = set()
    for page in range(1, max_pages + 1):
        if page in seen_pages:
            raise ValueError(f"Federal Register duplicate page: {page}")
        seen_pages.add(page)
        params = {"per_page": per_page, "page": page, "order": "newest"}
        if document_type:
            params["conditions[type][]"] = document_type
        response = request_with_retries(session, "GET", API_URL, params=params)
        data = response.json()
        write_json(raw_dir / f"{SOURCE}_{run_id}_page_{page}.json", data)
        page_rows = data.get("results") or []
        rows.extend(normalize_document(row) for row in page_rows)
        print(f"Federal Register page {page}: {len(page_rows)} rows")
        if not page_rows or page >= int(data.get("total_pages") or page):
            break
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect Federal Register documents.")
    parser.add_argument("--type", default="", help="Rule, Proposed Rule, Notice, or Presidential Document.")
    parser.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/federal_register"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/federal_register"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.per_page <= 0 or args.per_page > 1000:
        raise ValueError("--per-page must be between 1 and 1000")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    if args.type and args.type not in TYPE_SIGNAL_TYPES:
        raise ValueError(f"--type must be one of: {', '.join(TYPE_SIGNAL_TYPES)}")
    run_id = args.run_id or utc_run_id()
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(args.type, args.per_page, args.max_pages, args.raw_dir, run_id)
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
