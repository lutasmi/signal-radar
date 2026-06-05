import yfinance as yf

ticker = "PLTR"

try:
    t = yf.Ticker(ticker)
    hist = t.history(period="30d")
    info = t.info

    print(f"Ticker: {ticker}")
    print(f"Días de histórico: {len(hist)}")

    if not hist.empty:
        print("Últimos cierres:")
        print(hist[["Close", "Volume"]].tail())

    print("Market cap:", info.get("marketCap"))
    print("Sector:", info.get("sector"))
    print("Industry:", info.get("industry"))

except Exception as e:
    print(f"ERROR: {e}")