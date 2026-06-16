import argparse
import csv
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

import requests


CURRENT_FILINGS_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
OUTPUT_FILE = Path("data/sec_form4_latest.csv")
DEFAULT_FEED_COUNT = 100
DEFAULT_FEED_PAGES = 3
DEFAULT_MAX_FILINGS = 50
DEFAULT_DELAY_SECONDS = 0.2

SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "SignalRadar/0.1 contact@example.com",
)

HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Host": "www.sec.gov",
}

FIELDNAMES = [
    "ticker",
    "issuer_name",
    "insider_name",
    "insider_title",
    "transaction_date",
    "transaction_code",
    "acquired_disposed",
    "shares",
    "price",
    "estimated_value",
    "filing_date",
    "source_url",
]


def clean_text(value):
    return (value or "").strip()


def child_text(element, path):
    found = element.find(path)
    return clean_text("".join(found.itertext()) if found is not None else "")


def namespaced_child_text(element, path, namespace):
    found = element.find(path, namespace)
    return clean_text("".join(found.itertext()) if found is not None else "")


def parse_number(value):
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def format_number(value):
    if value is None:
        return ""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def current_filings_feed_url(feed_count, start):
    return (
        f"{CURRENT_FILINGS_URL}"
        f"?action=getcurrent&type=4&owner=include&count={feed_count}"
        f"&start={start}&output=atom"
    )


def fetch_text(session, url):
    response = session.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.text


def parse_recent_form4_filings(feed_xml):
    root = ET.fromstring(feed_xml)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    filings = []
    seen_accessions = set()

    for entry in root.findall("atom:entry", namespace):
        category = entry.find("atom:category", namespace)
        form_type = category.attrib.get("term", "") if category is not None else ""

        if form_type != "4":
            continue

        accession = ""
        entry_id = namespaced_child_text(entry, "atom:id", namespace)
        if "accession-number=" in entry_id:
            accession = entry_id.split("accession-number=", 1)[1]

        if not accession or accession in seen_accessions:
            continue

        link = entry.find("atom:link", namespace)
        source_url = link.attrib.get("href", "") if link is not None else ""
        summary = namespaced_child_text(entry, "atom:summary", namespace)
        filing_date = ""
        filing_date_match = re.search(r"Filed:\D+(\d{4}-\d{2}-\d{2})", summary)
        if filing_date_match:
            filing_date = filing_date_match.group(1)

        filings.append(
            {
                "accession": accession,
                "filing_date": filing_date,
                "source_url": source_url,
            }
        )
        seen_accessions.add(accession)

    return filings


def archive_index_url(source_url):
    return source_url.rsplit("/", 1)[0] + "/index.json"


def find_ownership_xml_url(session, source_url):
    index_url = archive_index_url(source_url)
    index = session.get(index_url, headers=HEADERS, timeout=20)
    index.raise_for_status()
    items = index.json()["directory"]["item"]

    xml_files = [
        item["name"]
        for item in items
        if item["name"].lower().endswith(".xml")
        and not item["name"].lower().startswith("filingsummary")
    ]

    for filename in xml_files:
        xml_url = urljoin(index_url, filename)
        xml_text = fetch_text(session, xml_url)
        if "<ownershipDocument" in xml_text:
            return xml_url, xml_text

    return "", ""


def parse_ownership_document(xml_text, filing_date, source_url):
    root = ET.fromstring(xml_text)
    issuer_name = child_text(root, "issuer/issuerName")
    ticker = child_text(root, "issuer/issuerTradingSymbol")

    owner_names = []
    owner_titles = []
    for owner in root.findall("reportingOwner"):
        owner_name = child_text(owner, "reportingOwnerId/rptOwnerName")
        owner_title = child_text(owner, "reportingOwnerRelationship/officerTitle")

        if owner_name:
            owner_names.append(owner_name)
        if owner_title:
            owner_titles.append(owner_title)

    transactions = []
    for transaction in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        transaction_code = child_text(transaction, "transactionCoding/transactionCode")
        if transaction_code != "P":
            continue

        shares = child_text(transaction, "transactionAmounts/transactionShares/value")
        price = child_text(transaction, "transactionAmounts/transactionPricePerShare/value")
        shares_number = parse_number(shares)
        price_number = parse_number(price)
        estimated_value = None
        if shares_number is not None and price_number is not None:
            estimated_value = shares_number * price_number

        transactions.append(
            {
                "ticker": ticker,
                "issuer_name": issuer_name,
                "insider_name": "; ".join(owner_names),
                "insider_title": "; ".join(owner_titles),
                "transaction_date": child_text(transaction, "transactionDate/value"),
                "transaction_code": transaction_code,
                "acquired_disposed": child_text(
                    transaction,
                    "transactionAmounts/transactionAcquiredDisposedCode/value",
                ),
                "shares": shares,
                "price": price,
                "estimated_value": format_number(estimated_value),
                "filing_date": filing_date,
                "source_url": source_url,
            }
        )

    return transactions


def fetch_sec_form4_transactions(
    feed_count=DEFAULT_FEED_COUNT,
    feed_pages=DEFAULT_FEED_PAGES,
    max_filings=DEFAULT_MAX_FILINGS,
    delay_seconds=DEFAULT_DELAY_SECONDS,
):
    session = requests.Session()
    filings = []
    seen_accessions = set()

    for page in range(feed_pages):
        start = page * feed_count
        feed_xml = fetch_text(session, current_filings_feed_url(feed_count, start))
        page_filings = parse_recent_form4_filings(feed_xml)

        for filing in page_filings:
            if filing["accession"] in seen_accessions:
                continue
            filings.append(filing)
            seen_accessions.add(filing["accession"])
            if len(filings) >= max_filings:
                break

        print(
            f"SEC feed page {page + 1}: "
            f"{len(page_filings)} Form 4 filings, {len(filings)} unique kept"
        )

        if len(filings) >= max_filings:
            break

        if delay_seconds > 0 and page < feed_pages - 1:
            time.sleep(delay_seconds)

    transactions = []

    print(f"Recent Form 4 filings found in SEC feed: {len(filings)}")

    for index, filing in enumerate(filings, start=1):
        try:
            xml_url, xml_text = find_ownership_xml_url(session, filing["source_url"])
            if not xml_text:
                print(f"{index}. {filing['accession']}: no ownership XML found")
                continue

            filing_transactions = parse_ownership_document(
                xml_text=xml_text,
                filing_date=filing["filing_date"],
                source_url=filing["source_url"],
            )
            transactions.extend(filing_transactions)
            print(
                f"{index}. {filing['accession']}: "
                f"{len(filing_transactions)} open-market purchase transactions"
            )
        except Exception as exc:
            print(f"{index}. {filing['accession']}: ERROR {exc}")

        if delay_seconds > 0 and index < len(filings):
            time.sleep(delay_seconds)

    return transactions


def write_csv(transactions, output_file=OUTPUT_FILE):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(transactions)


def print_sample_rows(transactions, sample_size=5):
    print(f"\n--- EXAMPLE {min(sample_size, len(transactions))} ROWS ---")
    for row in transactions[:sample_size]:
        print(
            row["filing_date"],
            "|",
            row["transaction_date"],
            "|",
            row["ticker"] or "N/A",
            "|",
            row["issuer_name"],
            "|",
            row["insider_name"],
            "|",
            row["shares"],
            "|",
            row["price"],
            "|",
            row["estimated_value"],
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract recent SEC Form 4 open-market purchases to CSV."
    )
    parser.add_argument("--feed-count", type=int, default=DEFAULT_FEED_COUNT)
    parser.add_argument("--feed-pages", type=int, default=DEFAULT_FEED_PAGES)
    parser.add_argument("--max-filings", type=int, default=DEFAULT_MAX_FILINGS)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.feed_count <= 0:
        raise ValueError("--feed-count must be greater than 0")
    if args.feed_pages <= 0:
        raise ValueError("--feed-pages must be greater than 0")
    if args.max_filings <= 0:
        raise ValueError("--max-filings must be greater than 0")
    if args.delay < 0:
        raise ValueError("--delay cannot be negative")

    transactions = fetch_sec_form4_transactions(
        feed_count=args.feed_count,
        feed_pages=args.feed_pages,
        max_filings=args.max_filings,
        delay_seconds=args.delay,
    )
    write_csv(transactions, args.output)

    print(f"\nTransactions extracted: {len(transactions)}")
    print(f"CSV generated: {args.output}")
    print_sample_rows(transactions)
