import argparse
import csv
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["bs4", "requests"])

import requests
from bs4 import BeautifulSoup


URL = "https://www.capitoltrades.com/trades"
OUTPUT_FILE = Path("data/capitol_trades_latest.csv")
DEFAULT_MAX_ROWS = 120
DEFAULT_DELAY_SECONDS = 0.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

FIELDNAMES = [
    "politician",
    "asset_name",
    "ticker",
    "published",
    "transaction_date",
    "filing_delay",
    "owner",
    "trade_type",
    "amount",
    "price",
    "source",
    "source_url",
]


def extract_ticker(asset_text: str):
    match = re.search(r"\b([A-Z]{1,5}(?:/[A-Z])?):US\b", asset_text)
    return match.group(1) if match else None


def clean_asset_name(asset_text: str):
    return re.sub(r"\s+[A-Z]{1,5}(?:/[A-Z])?:US$", "", asset_text).strip()


def page_url(page: int) -> str:
    if page <= 1:
        return URL
    return f"{URL}?page={page}"


def fetch_page_html(page: int) -> str:
    """Fetch the normal HTML page, not the Vercel/Next.js _rsc endpoint."""
    response = requests.get(page_url(page), headers=HEADERS, timeout=20)
    response.raise_for_status()

    if "Vercel Security Checkpoint" in response.text or "Security Checkpoint" in response.text:
        raise RuntimeError(
            f"Capitol Trades returned a security checkpoint for page {page}. "
            "Use the normal HTML URL with ?page=N and do not call _rsc endpoints."
        )

    return response.text


def parse_trades(html: str, source_url: str):
    soup = BeautifulSoup(html, "lxml")
    rows = soup.find_all("tr")

    trades = []

    for row in rows:
        cells = row.find_all("td")

        if len(cells) < 8:
            continue

        politician = cells[0].get_text(" ", strip=True)
        asset_text = cells[1].get_text(" ", strip=True)
        published = cells[2].get_text(" ", strip=True)
        transaction_date = cells[3].get_text(" ", strip=True)
        filing_delay = cells[4].get_text(" ", strip=True)
        owner = cells[5].get_text(" ", strip=True)
        trade_type = cells[6].get_text(" ", strip=True).lower()
        amount = cells[7].get_text(" ", strip=True)
        price = cells[8].get_text(" ", strip=True) if len(cells) > 8 else ""

        ticker = extract_ticker(asset_text)
        asset_name = clean_asset_name(asset_text)

        trades.append({
            "politician": politician,
            "asset_name": asset_name,
            "ticker": ticker or "",
            "published": published,
            "transaction_date": transaction_date,
            "filing_delay": filing_delay,
            "owner": owner,
            "trade_type": trade_type,
            "amount": amount,
            "price": price,
            "source": "capitol_trades",
            "source_url": source_url,
        })

    return trades


def fetch_capitol_trades(max_rows: int = DEFAULT_MAX_ROWS, max_pages: int | None = None, delay_seconds: float = DEFAULT_DELAY_SECONDS):
    trades = []
    seen = set()
    page = 1

    while len(trades) < max_rows:
        if max_pages is not None and page > max_pages:
            break

        current_url = page_url(page)
        html = fetch_page_html(page)
        page_trades = parse_trades(html, current_url)

        if not page_trades:
            print(f"Página {page}: 0 registros. Fin de la extracción.")
            break

        new_rows = 0
        for trade in page_trades:
            row_key = tuple(trade[field] for field in FIELDNAMES)
            if row_key in seen:
                continue
            seen.add(row_key)
            trades.append(trade)
            new_rows += 1

            if len(trades) >= max_rows:
                break

        print(f"Página {page}: {len(page_trades)} registros parseados, {new_rows} nuevos.")

        page += 1
        if len(trades) < max_rows and delay_seconds > 0:
            time.sleep(delay_seconds)

    return trades[:max_rows]


def write_csv(trades, output_file: Path = OUTPUT_FILE):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(trades)


def print_sample_rows(trades, sample_size: int = 5):
    print(f"\n--- EJEMPLO {min(sample_size, len(trades))} FILAS ---")
    for trade in trades[:sample_size]:
        print(
            trade["published"],
            "|",
            trade["transaction_date"],
            "|",
            trade["politician"],
            "|",
            trade["ticker"] or "N/A",
            "|",
            trade["asset_name"],
            "|",
            trade["trade_type"],
            "|",
            trade["amount"],
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Extract recent Capitol Trades PTR rows to CSV.")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.max_rows <= 0:
        raise ValueError("--max-rows must be greater than 0")
    if args.max_pages is not None and args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than 0")
    if args.delay < 0:
        raise ValueError("--delay cannot be negative")

    trades = fetch_capitol_trades(
        max_rows=args.max_rows,
        max_pages=args.max_pages,
        delay_seconds=args.delay,
    )
    write_csv(trades, args.output)

    useful_buys = [
        trade for trade in trades
        if trade["trade_type"] == "buy"
        and trade["ticker"]
        and "N/A" not in trade["asset_name"]
    ]

    print(f"\nRegistros totales extraídos: {len(trades)}")
    print(f"Compras útiles: {len(useful_buys)}")
    print(f"CSV generado: {args.output}")
    print_sample_rows(trades)

    print("\n--- COMPRAS ÚTILES ---")
    for trade in useful_buys:
        print(
            trade["transaction_date"],
            "|",
            trade["politician"],
            "|",
            trade["ticker"],
            "|",
            trade["asset_name"],
            "|",
            trade["amount"],
            "|",
            trade["filing_delay"],
        )
