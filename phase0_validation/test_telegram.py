import requests
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

message = """
🔴 <b>SIGNAL RADAR — TEST</b>

<b>$TEST</b> | Score: <b>85/100</b>

Señales:
• [PTR] Compra política detectada
• [INSIDER] Compra insider detectada
• [CONTRACT] Contrato federal detectado

Si ves este mensaje, Telegram funciona.
"""

r = requests.post(
    url,
    json={
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    },
    timeout=10
)

print("Status:", r.status_code)
print(r.json())