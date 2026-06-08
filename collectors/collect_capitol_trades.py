import csv
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup


URL = "https://www.capitoltrades.com/trades"
OUTPUT_FILE = Path("data/capitol_trades_latest.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def extract_ticker(asset_text: str):
    match = re.search(r"\b([A-Z]{1,5}(?:/[A-Z])?):US\b", asset_text)
    return match.group(1) if match else None


def clean_asset_name(asset_text: str):
    return re.sub(r"\s+[A-Z]{1,5}(?:/[A-Z])?:US$", "", asset_text).strip()


def fetch_capitol_trades():
    response = requests.get(URL, headers=HEADERS, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
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
            "source_url": URL
        })

    return trades


def write_csv(trades):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
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
        "source_url"
    ]

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)


if __name__ == "__main__":
    trades = fetch_capitol_trades()
    write_csv(trades)

    useful_buys = [
        trade for trade in trades
        if trade["trade_type"] == "buy"
        and trade["ticker"]
        and "N/A" not in trade["asset_name"]
    ]

    print(f"Registros totales extraídos: {len(trades)}")
    print(f"Compras útiles: {len(useful_buys)}")
    print(f"CSV generado: {OUTPUT_FILE}")

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
            trade["filing_delay"]
        )