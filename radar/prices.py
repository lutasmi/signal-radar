"""Daily price history with a local CSV cache (data/reference/prices/).

Source: Yahoo Finance via the yfinance library (batched downloads; it handles the
cookies/TLS fingerprint Yahoo requires from cloud IPs), with the raw chart API as
fallback. Adjusted closes are used so returns include splits/dividends. The cache is persisted between
GitHub Actions runs with actions/cache, so after the first backfill only a
short recent window is downloaded per ticker.
"""

import bisect
import csv
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRICE_DIR = PROJECT_ROOT / "data" / "reference" / "prices"
BENCHMARK = "SPY"

YAHOO_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def yahoo_symbol(ticker):
    return ticker.strip().upper().replace(".", "-").replace("/", "-")


def last_weekday(today=None):
    day = today or datetime.now(timezone.utc).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


class PriceStore:
    def __init__(self, session=None, cache_dir=PRICE_DIR, refresh=True, histories=None, today=None):
        self.session = session
        self.cache_dir = Path(cache_dir)
        self.refresh = refresh and session is not None
        self.today = today or datetime.now(timezone.utc).date()
        self._series = {}
        self.failed = set()
        self.downloads = 0
        if histories:
            for ticker, points in histories.items():
                self._set(ticker, points)

    # storage -------------------------------------------------------------
    def _set(self, ticker, points):
        points = sorted({d: p for d, p in points if p and p > 0}.items())
        self._series[ticker.upper()] = ([d for d, _ in points], [p for _, p in points])

    def _path(self, ticker):
        return self.cache_dir / f"{yahoo_symbol(ticker)}.csv"

    def _read_cache(self, ticker):
        path = self._path(ticker)
        if not path.exists():
            return []
        points = []
        with path.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    points.append((date.fromisoformat(row["date"]), float(row["adjclose"])))
                except (KeyError, ValueError):
                    continue
        return points

    def _write_cache(self, ticker):
        dates, prices = self._series[ticker]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self._path(ticker).open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["date", "adjclose"])
            for d, p in zip(dates, prices):
                writer.writerow([d.isoformat(), f"{p:.4f}"])

    # download ------------------------------------------------------------
    def _download(self, ticker, range_="5y"):
        params = {"range": range_, "interval": "1d", "includeAdjustedClose": "true"}
        last_error = None
        for attempt in range(3):
            for url in YAHOO_URLS:
                try:
                    response = self.session.get(
                        url.format(symbol=yahoo_symbol(ticker)),
                        params=params,
                        headers=HEADERS,
                        timeout=30,
                    )
                    if response.status_code == 404:
                        return []
                    if response.status_code == 429:
                        raise RuntimeError("HTTP 429")
                    response.raise_for_status()
                    result = (response.json().get("chart", {}).get("result") or [None])[0]
                    if not result or not result.get("timestamp"):
                        return []
                    stamps = result["timestamp"]
                    indicators = result.get("indicators", {})
                    adj = (indicators.get("adjclose") or [{}])[0].get("adjclose")
                    close = (indicators.get("quote") or [{}])[0].get("close")
                    values = adj or close or []
                    offset = result.get("meta", {}).get("gmtoffset", 0) or 0
                    points = []
                    for stamp, value in zip(stamps, values):
                        if value is None:
                            continue
                        day = datetime.fromtimestamp(stamp + offset, tz=timezone.utc).date()
                        points.append((day, float(value)))
                    self.downloads += 1
                    return points
                except Exception as exc:  # try next host / retry
                    last_error = exc
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"price download failed for {ticker}: {last_error}")

    def _cache_loaded(self, ticker):
        if ticker not in self._series:
            cached = self._read_cache(ticker)
            if cached:
                self._set(ticker, cached)
        return self._series.get(ticker, ([], []))[0]

    def _yf_download(self, tickers, start):
        """Batch download with yfinance. Returns {ticker: [(date, adjclose), ...]}."""
        import pandas as pd
        import yfinance as yf

        symbols = {yahoo_symbol(t): t for t in tickers}
        frame = yf.download(
            tickers=list(symbols),
            start=start.isoformat(),
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
            multi_level_index=True,
        )
        result = {}
        if frame is None or frame.empty:
            return result
        for symbol, ticker in symbols.items():
            try:
                if isinstance(frame.columns, pd.MultiIndex):
                    series = frame[symbol]["Close"]
                else:
                    series = frame["Close"]
            except KeyError:
                continue
            points = [(idx.date(), float(value)) for idx, value in series.dropna().items()]
            if points:
                result[ticker] = points
        return result

    def prefetch(self, tickers, need_until=None, batch_size=80):
        """Refresh every stale ticker, downloading in batches. need_until: date or {ticker: date}."""
        default_need = last_weekday(self.today) - timedelta(days=1)
        stale_new, stale_update = [], []
        for ticker in sorted({t.upper().strip() for t in tickers if t and t.strip()}):
            if ticker in self.failed:
                continue
            need = need_until.get(ticker, default_need) if isinstance(need_until, dict) else (need_until or default_need)
            dates = self._cache_loaded(ticker)
            if dates and dates[-1] >= need:
                continue
            path = self._path(ticker)
            if dates and path.exists() and time.time() - path.stat().st_mtime < 6 * 3600:
                continue  # tried recently (e.g. delisted or holiday): do not hammer the API
            (stale_update if dates else stale_new).append(ticker)
        if not self.refresh or not (stale_new or stale_update):
            return 0
        groups = [
            (stale_new, self.today - timedelta(days=5 * 365)),
            (stale_update, self.today - timedelta(days=90)),
        ]
        for group, start in groups:
            for i in range(0, len(group), batch_size):
                self._fetch_batch(group[i:i + batch_size], start)
        return len(stale_new) + len(stale_update)

    def _fetch_batch(self, batch, start):
        fetched = {}
        try:
            fetched = self._yf_download(batch, start)
        except Exception as exc:  # library missing or Yahoo error: try the raw API
            print(f"WARNING: yfinance batch failed ({exc}); falling back to chart API")
            for ticker in batch:
                try:
                    points = self._download(ticker, "5y")
                    if points:
                        fetched[ticker] = points
                except Exception as inner:
                    print(f"WARNING: {inner}")
                time.sleep(0.3)
        self.downloads += len(fetched)
        for ticker in batch:
            points = fetched.get(ticker)
            if points:
                merged = dict(zip(*self._series.get(ticker, ([], []))))
                merged.update(dict(points))
                self._set(ticker, merged.items())
                self._write_cache(ticker)
            elif self._series.get(ticker, ([], []))[0]:
                self._path(ticker).touch()
            else:
                self.failed.add(ticker)

    def load(self, ticker, need_until=None):
        """Load one ticker (refreshing it if stale). Prefer prefetch() for many tickers."""
        ticker = ticker.upper().strip()
        if not ticker:
            return False
        self.prefetch([ticker], need_until)
        return self.has(ticker)

    # queries -------------------------------------------------------------
    def has(self, ticker):
        return ticker.upper() in self._series and bool(self._series[ticker.upper()][0])

    def dates(self, ticker):
        return self._series.get(ticker.upper(), ([], []))[0]

    def on_or_after(self, ticker, day):
        dates, prices = self._series.get(ticker.upper(), ([], []))
        i = bisect.bisect_left(dates, day)
        if i >= len(dates):
            return None, None
        return dates[i], prices[i]

    def after(self, ticker, day):
        """First trading day strictly after `day` (the day you could act on news)."""
        return self.on_or_after(ticker, day + timedelta(days=1))

    def on_or_before(self, ticker, day):
        dates, prices = self._series.get(ticker.upper(), ([], []))
        i = bisect.bisect_right(dates, day) - 1
        if i < 0:
            return None, None
        return dates[i], prices[i]

    def latest(self, ticker):
        dates, prices = self._series.get(ticker.upper(), ([], []))
        if not dates:
            return None, None
        return dates[-1], prices[-1]


def pct(a, b):
    if not a or not b:
        return None
    return (b / a - 1.0) * 100.0


def fmt_pct(value, digits=1):
    if value is None:
        return "n/d"
    return f"{value:+.{digits}f}%"
