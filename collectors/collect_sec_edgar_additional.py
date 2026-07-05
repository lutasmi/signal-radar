import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
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
    request_with_retries,
    require_unique,
    utc_run_id,
    write_csv,
    write_text,
)


API_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SOURCE = "sec_edgar_additional"
DEFAULT_FORMS = ["8-K", "SC 13D", "SC 13G", "144", "13F-HR"]
DEFAULT_COUNT = 40
DEFAULT_MAX_PAGES = 1
DEFAULT_DELAY_SECONDS = 0.2
FORM_SIGNAL_TYPES = {
    "8-K": "MATERIAL_EVENT_8K",
    "SC 13D": "MAJOR_HOLDER_ENTRY",
    "SC 13G": "MAJOR_HOLDER_ENTRY",
    "144": "FORM144_PLANNED_SALE",
    "13F-HR": "INSTITUTIONAL_POSITION_13F",
}
FIELDNAMES = [
    "accession",
    "form_type",
    "signal_type",
    "company_name",
    "cik",
    "filing_date",
    "updated_at",
    "title",
    "source_url",
]


def feed_url(form_type, count, start):
    return (
        f"{API_URL}?action=getcurrent&type={form_type}&owner=include"
        f"&count={count}&start={start}&output=atom"
    )


def parse_company_and_cik(title):
    title = clean_text(title)
    match = re.search(r"^-?\s*(.+?)\s+\((\d{10})\)", title)
    if match:
        return clean_text(match.group(1)), match.group(2)
    cik_match = re.search(r"\((\d{10})\)", title)
    return title, cik_match.group(1) if cik_match else ""


def parse_feed(feed_xml, expected_form_type):
    root = ET.fromstring(feed_xml)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    rows = []
    for entry in root.findall("atom:entry", namespace):
        category = entry.find("atom:category", namespace)
        form_type = clean_text(category.attrib.get("term", "")) if category is not None else ""
        if form_type != expected_form_type:
            continue
        entry_id = "".join(entry.findtext("atom:id", default="", namespaces=namespace))
        accession = entry_id.split("accession-number=", 1)[-1] if "accession-number=" in entry_id else ""
        title = "".join(entry.findtext("atom:title", default="", namespaces=namespace))
        company_name, cik = parse_company_and_cik(title)
        link = entry.find("atom:link", namespace)
        updated_at = "".join(entry.findtext("atom:updated", default="", namespaces=namespace))
        summary = "".join(entry.findtext("atom:summary", default="", namespaces=namespace))
        filing_date = ""
        match = re.search(r"Filed:\D+(\d{4}-\d{2}-\d{2})", summary)
        if match:
            filing_date = match.group(1)
        rows.append(
            {
                "accession": accession,
                "form_type": form_type,
                "signal_type": FORM_SIGNAL_TYPES.get(form_type, "ACTIVIST_POSITION"),
                "company_name": company_name,
                "cik": cik,
                "filing_date": filing_date,
                "updated_at": updated_at[:10],
                "title": clean_text(title),
                "source_url": clean_text(link.attrib.get("href", "") if link is not None else ""),
            }
        )
    return rows


def validate_rows(rows, allow_empty=False):
    if not rows and not allow_empty:
        raise ValueError("SEC EDGAR returned no filing rows")
    require_unique(
        [f"{row['form_type']}:{row['accession']}" for row in rows],
        "sec_edgar_additional form/accession",
    )
    for row in rows:
        if not row["accession"]:
            raise ValueError("SEC EDGAR row missing accession")
        if not row["form_type"] or not row["signal_type"]:
            raise ValueError(f"SEC EDGAR row missing form/signal: {row['accession']}")
        if not row["filing_date"] and not row["updated_at"]:
            raise ValueError(f"SEC EDGAR row missing date: {row['accession']}")


def merge_unique_rows(rows):
    unique_rows = []
    seen_keys = set()
    duplicate_count = 0
    for row in rows:
        key = (row.get("form_type"), row.get("accession"))
        if key[1] and key in seen_keys:
            duplicate_count += 1
            continue
        if key[1]:
            seen_keys.add(key)
        unique_rows.append(row)
    return unique_rows, duplicate_count


def fetch_pages(forms, count, max_pages, delay, raw_dir, run_id):
    session = requests.Session()
    headers = {"User-Agent": "SignalRadar/1.0 contact@example.com"}
    rows = []
    for form_type in forms:
        for page in range(max_pages):
            start = page * count
            response = request_with_retries(
                session,
                "GET",
                feed_url(form_type, count, start),
                headers=headers,
            )
            xml_text = response.text
            safe_form = re.sub(r"[^A-Za-z0-9]+", "_", form_type).strip("_").lower()
            write_text(raw_dir / f"{SOURCE}_{run_id}_{safe_form}_page_{page + 1}.xml", xml_text)
            page_rows = parse_feed(xml_text, form_type)
            rows.extend(page_rows)
            print(f"SEC EDGAR {form_type} page {page + 1}: {len(page_rows)} rows")
            if len(page_rows) < count:
                break
            if delay > 0:
                time.sleep(delay)
    unique_rows, duplicate_count = merge_unique_rows(rows)
    print(f"SEC EDGAR duplicate filings removed: {duplicate_count}")
    return unique_rows


def parse_args():
    parser = argparse.ArgumentParser(description="Collect additional SEC EDGAR filing feeds.")
    parser.add_argument("--forms", nargs="+", default=DEFAULT_FORMS)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/sec_edgar_additional"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed/sec_edgar_additional"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    if args.count <= 0 or args.count > 100:
        raise ValueError("--count must be between 1 and 100")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    unknown_forms = [form for form in args.forms if form not in FORM_SIGNAL_TYPES]
    if unknown_forms:
        raise ValueError(f"unsupported SEC forms: {', '.join(unknown_forms)}")
    run_id = args.run_id or utc_run_id()
    ensure_output_dirs(args.raw_dir, args.processed_dir)
    rows = fetch_pages(args.forms, args.count, args.max_pages, args.delay, args.raw_dir, run_id)
    validate_rows(rows, allow_empty=args.allow_empty)
    output = args.processed_dir / f"{SOURCE}_{run_id}.csv"
    write_csv(output, rows, FIELDNAMES)
    print(f"Endpoint: {API_URL}")
    print(f"Rows extracted: {len(rows)}")
    print(f"CSV generated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
