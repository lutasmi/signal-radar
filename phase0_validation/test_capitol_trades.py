import requests
from bs4 import BeautifulSoup
import re

url = "https://www.capitoltrades.com/trades"

headers = {
    "User-Agent": "Mozilla/5.0"
}


def extract_ticker(asset_text: str):
    match = re.search(r"\b([A-Z]{1,5}(?:/[A-Z])?):US\b", asset_text)
    return match.group(1) if match else None


def clean_asset_name(asset_text: str):
    return re.sub(r"\s+[A-Z]{1,5}(?:/[A-Z])?:US$", "", asset_text).strip()


try:
    r = requests.get(url, headers=headers, timeout=20)
    print(f"Status: {r.status_code}")

    soup = BeautifulSoup(r.text, "lxml")
    rows = soup.find_all("tr")

    clean_rows = []

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

        clean_rows.append({
            "politician": politician,
            "asset_name": asset_name,
            "ticker": ticker,
            "published": published,
            "transaction_date": transaction_date,
            "filing_delay": filing_delay,
            "owner": owner,
            "trade_type": trade_type,
            "amount": amount,
            "price": price
        })

    print(f"Filas limpias extraídas: {len(clean_rows)}")

    for row in clean_rows[:10]:
        print(row)

    print("\n--- COMPRAS ÚTILES ---")

    useful_buys = [
        row for row in clean_rows
        if row["trade_type"] == "buy"
        and row["ticker"] is not None
        and "N/A" not in row["asset_name"]
    ]

    print(f"Compras útiles: {len(useful_buys)}")

    for row in useful_buys:
        print(
            row["transaction_date"],
            "|",
            row["politician"],
            "|",
            row["ticker"],
            "|",
            row["asset_name"],
            "|",
            row["amount"],
            "|",
            row["filing_delay"]
        )

except Exception as e:
    print(f"ERROR: {e}")