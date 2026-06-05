import requests
from datetime import date, timedelta

url = "https://efts.senate.gov/LATEST/search-index"

from_date = (date.today() - timedelta(days=90)).isoformat()
to_date = date.today().isoformat()

params = {
    "q": "purchase",
    "dateRange": "custom",
    "fromDate": from_date,
    "toDate": to_date,
    "category": "All"
}

try:
    r = requests.get(url, params=params, timeout=20)

    print(f"Status: {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        print(f"Registros encontrados: {len(hits)}")

        for hit in hits[:5]:
            s = hit.get("_source", {})
            print(s)

    else:
        print(r.text[:1000])

except Exception as e:
    print(f"ERROR: {e}")