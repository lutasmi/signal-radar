import requests
from datetime import date, timedelta

url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

payload = {
    "filters": {
        "time_period": [
            {
                "start_date": (date.today() - timedelta(days=30)).isoformat(),
                "end_date": date.today().isoformat()
            }
        ],
        "award_type_codes": ["A", "B", "C", "D"],
        "agencies": [
            {
                "type": "awarding",
                "tier": "toptier",
                "name": "Department of Defense"
            }
        ],
        "award_amounts": [
            {"lower_bound": 10000000}
        ]
    },
    "fields": [
        "Recipient Name",
        "Award Amount",
        "Award ID",
        "Start Date",
        "Awarding Agency",
        "Description"
    ],
    "limit": 10,
    "page": 1
}

try:
    r = requests.post(url, json=payload, timeout=30)

    print(f"Status: {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        results = data.get("results", [])

        print(f"OK - contratos encontrados: {len(results)}")

        for c in results[:5]:
            print(
                c.get("Start Date"),
                "|",
                c.get("Recipient Name"),
                "|",
                c.get("Award Amount"),
                "|",
                c.get("Description")
            )
    else:
        print(r.text[:1000])

except Exception as e:
    print(f"ERROR: {e}")