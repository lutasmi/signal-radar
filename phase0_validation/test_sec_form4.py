import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "SignalRadarV1 tuemail@example.com"
}

ticker = "PLTR"

url = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    f"?action=getcompany&CIK={ticker}&type=4&dateb=&owner=include&count=10"
)

try:
    r = requests.get(url, headers=HEADERS, timeout=15)

    print(f"Status: {r.status_code}")

    if r.status_code == 200:
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table", {"class": "tableFile2"})

        if not table:
            print("No se encontró tabla de filings")
            print(r.text[:500])
        else:
            rows = table.find_all("tr")[1:]
            print(f"Filings encontrados: {len(rows)}")

            for row in rows[:5]:
                cells = row.find_all("td")
                if len(cells) >= 4:
                    filing_type = cells[0].get_text(strip=True)
                    filing_date = cells[3].get_text(strip=True)
                    link = row.find("a", href=True)
                    href = "https://www.sec.gov" + link["href"] if link else ""
                    print(filing_type, "|", filing_date, "|", href)

    else:
        print(r.text[:500])

except Exception as e:
    print(f"ERROR: {e}")