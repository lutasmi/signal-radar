import requests
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("QUIVER_API_KEY", "")

base = "https://api.quiverquant.com/beta"

endpoints = [
    "/live/congresstrading",
    "/historical/congresstrading/AAPL",
    "/live/insiders"
]

headers = {}
if api_key:
    headers["Authorization"] = f"Token {api_key}"

print("API key configurada:", bool(api_key))

for ep in endpoints:
    url = base + ep

    try:
        r = requests.get(url, headers=headers, timeout=15)

        print("\nEndpoint:", ep)
        print("Status:", r.status_code)

        if r.status_code == 200:
            data = r.json()
            print("OK")
            print("Tipo:", type(data))
            if isinstance(data, list):
                print("Registros:", len(data))
                if data:
                    print("Primer registro:")
                    print(data[0])
            else:
                print(data)

        else:
            print(r.text[:500])

    except Exception as e:
        print("ERROR:", e)