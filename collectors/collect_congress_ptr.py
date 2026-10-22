"""Collect congressional Periodic Transaction Reports (PTR) from official sources.

House: Office of the Clerk financial disclosure index (yearly ZIP with XML)
       + electronic PTR PDFs.
Senate: eFD search (efdsearch.senate.gov) electronic PTR pages.

Each transaction is enriched with the member's committee assignments and the
traded company's sector, and flagged when the member sits on a committee whose
jurisdiction covers that sector (e.g. Armed Services member buying LMT).

Output: data/congress_ptr_latest.csv (generated artifact) and raw responses
under data/raw/congress_ptr/ for auditability.
"""

import argparse
import csv
import json
import io
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["bs4", "requests", "pdfplumber", "yaml"])

import requests
from bs4 import BeautifulSoup

from radar import conflicts


OUTPUT_FILE = PROJECT_ROOT / "data" / "congress_ptr_latest.csv"
PARSE_CACHE_DIR = PROJECT_ROOT / "data" / "reference" / "ptr_cache"

HOUSE_BASE = "https://disclosures-clerk.house.gov"
HOUSE_INDEX_URL = HOUSE_BASE + "/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR_URL = HOUSE_BASE + "/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

SENATE_BASE = "https://efdsearch.senate.gov"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

FIELDNAMES = [
    "filing_id",
    "chamber",
    "politician",
    "bioguide_id",
    "party",
    "state_district",
    "owner",
    "asset_name",
    "ticker",
    "asset_type",
    "trade_type",
    "transaction_date",
    "notification_date",
    "filing_date",
    "disclosure_delay_days",
    "amount",
    "amount_min",
    "ticker_sectors",
    "sic",
    "sic_description",
    "member_committees",
    "conflict",
    "conflict_score",
    "conflict_sectors",
    "conflict_committees",
    "conflict_reason",
    "source",
    "source_url",
    "extraction",
]

HOUSE_TYPE_MAP = {
    "P": "buy",
    "S": "sell",
    "S (partial)": "sell_partial",
    "E": "exchange",
}
SENATE_TYPE_MAP = {
    "purchase": "buy",
    "sale (full)": "sell",
    "sale (partial)": "sell_partial",
    "sale": "sell",
    "exchange": "exchange",
}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def parse_us_date(value):
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def iso(value):
    return value.isoformat() if value else ""


def extract_ticker(asset_name):
    candidates = re.findall(r"\(([A-Z][A-Z0-9.\-]{0,6})\)", asset_name or "")
    return candidates[-1] if candidates else ""


def get_with_retry(session, url, attempts=3, **kwargs):
    kwargs.setdefault("timeout", 45)
    last_exc = None
    for attempt in range(attempts):
        try:
            response = session.get(url, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code} for {url}")
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    raise last_exc


# ---------------------------------------------------------------------------
# House
# ---------------------------------------------------------------------------


def parse_house_index(zip_bytes):
    """Return PTR filings from the yearly House disclosure ZIP."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        xml_name = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
        root = ElementTree.fromstring(archive.read(xml_name))

    filings = []
    for member in root.iter("Member"):
        def text(tag):
            node = member.find(tag)
            return (node.text or "").strip() if node is not None and node.text else ""

        if text("FilingType") != "P":
            continue
        filings.append(
            {
                "doc_id": text("DocID"),
                "first": text("First"),
                "last": text("Last"),
                "suffix": text("Suffix"),
                "state_district": text("StateDst"),
                "year": text("Year"),
                "filing_date": parse_us_date(text("FilingDate")),
            }
        )
    return filings


HOUSE_TYPE_RE = re.compile(r"^(S \(partial\)|P|S|E)$")
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
OWNER_CODES = ("SP", "JT", "DC")
HEADER_WORDS = ("Owner", "Asset", "Transaction", "Notification", "Amount", "Cap.")


def _clean(text):
    return (text or "").replace("\x00", "").strip()


def _group_lines(words, tolerance=2.0):
    """Group words into visual lines by their top coordinate."""
    lines = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1]["top"] - word["top"]) <= tolerance:
            lines[-1]["words"].append(word)
        else:
            lines.append({"top": word["top"], "words": [word]})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
    return lines


def _header_columns(lines):
    """Column x-boundaries from the 'ID Owner Asset Transaction ...' header row."""
    for index, line in enumerate(lines):
        texts = {_clean(w["text"]): w["x0"] for w in line["words"]}
        if all(word in texts for word in HEADER_WORDS):
            x = {word: texts[word] for word in HEADER_WORDS}
            # The second "Date" on the header row belongs to the date column.
            date_x = min(
                (w["x0"] for w in line["words"]
                 if _clean(w["text"]) == "Date" and x["Transaction"] < w["x0"] < x["Notification"]),
                default=(x["Transaction"] + x["Notification"]) / 2,
            )
            columns = {
                "owner": (x["Owner"] - 25, x["Asset"] - 2),
                "asset": (x["Asset"] - 2, x["Transaction"] - 2),
                "type": (x["Transaction"] - 2, date_x - 2),
                "date": (date_x - 2, x["Notification"] - 2),
                "notification": (x["Notification"] - 2, x["Amount"] - 2),
                "amount": (x["Amount"] - 2, x["Cap."] - 2),
            }
            # Header spans up to three lines ("Type", "Date", "$200?").
            body_start = index + 1
            while body_start < len(lines) and lines[body_start]["top"] - line["top"] < 30:
                body_start += 1
            return columns, body_start, x["Owner"]
    return None, 0, 0


def _column_text(words, column):
    low, high = column
    return " ".join(_clean(w["text"]) for w in words if low <= w["x0"] < high and _clean(w["text"]))


def _is_label_line(line, asset_column):
    """'F S: New', 'S O: ...', 'D: ...', 'L: ...', 'C: ...' detail lines."""
    words = [w for w in line["words"] if asset_column[0] <= w["x0"] < asset_column[1]]
    if not words:
        return False
    first = _clean(words[0]["text"])
    joined = " ".join(_clean(w["text"]) for w in words[:3])
    return len(first) <= 2 and first[:1].isupper() and ":" in joined


def _extend_asset(transaction, extra_text):
    asset = f"{transaction['asset_name']} {extra_text}".strip()
    atype = re.search(r"\[([A-Z]{2,3})\]", asset)
    transaction["asset_name"] = re.sub(r"\s*\[[A-Z]{2,3}\]\s*", " ", asset).strip()
    transaction["asset_type"] = atype.group(1) if atype else ""
    transaction["ticker"] = extract_ticker(transaction["asset_name"])


def parse_house_ptr_pdf(pdf_bytes):
    """Parse transactions from an electronic House PTR PDF using word positions."""
    import pdfplumber

    transactions = []
    columns = None
    owner_x = 0
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            lines = _group_lines(page.extract_words(keep_blank_chars=False))
            page_columns, body_start, page_owner_x = _header_columns(lines)
            if page_columns:
                columns, owner_x = page_columns, page_owner_x
            elif columns is None:
                continue
            else:
                body_start = 0

            body = []
            for line in lines[body_start:]:
                first_x = line["words"][0]["x0"]
                if first_x < owner_x - 30 and _clean(line["words"][0]["text"]).startswith(("*", "I", "Filing")):
                    if _clean(line["words"][0]["text"]).startswith("Filing"):
                        continue  # page header/footer "Filing ID #..."
                    break  # footnote / next section: end of the transaction table
                body.append(line)

            starts = [
                i for i, line in enumerate(body)
                if HOUSE_TYPE_RE.match(_column_text(line["words"], columns["type"]))
                and DATE_RE.search(_column_text(line["words"], columns["date"]))
            ]
            # Asset text cut by a page break continues at the top of this page.
            if starts and transactions and not transactions[-1]["asset_type"]:
                carry = []
                for line in body[: starts[0]]:
                    if _is_label_line(line, columns["asset"]):
                        break
                    carry.append(_column_text(line["words"], columns["asset"]))
                if carry and re.search(r"\[[A-Z]{2,3}\]", " ".join(carry)):
                    _extend_asset(transactions[-1], " ".join(c for c in carry if c))
                    body = body[: starts[0] - len(carry)] + body[starts[0]:]
                    starts = [i - len(carry) for i in starts]
            for n, start in enumerate(starts):
                end = starts[n + 1] if n + 1 < len(starts) else len(body)
                block = body[start:end]
                # Asset text can begin a line or two above the type/date row.
                lead = []
                k = start - 1
                prev_end = starts[n - 1] if n > 0 else -1
                while k > prev_end and not _is_label_line(body[k], columns["asset"]):
                    lead.insert(0, body[k])
                    k -= 1
                asset_parts, owner_parts, amount_parts = [], [], []
                for line in lead + block:
                    if _is_label_line(line, columns["asset"]):
                        if line in block:
                            break
                        continue
                    asset_parts.append(_column_text(line["words"], columns["asset"]))
                    owner_parts.append(_column_text(line["words"], columns["owner"]))
                for line in block:
                    amount_parts.append(_column_text(line["words"], columns["amount"]))
                    if _is_label_line(line, columns["asset"]):
                        break
                first_line = block[0]["words"]
                asset = " ".join(part for part in asset_parts if part).strip()
                owner = next((p for p in owner_parts if p in OWNER_CODES), "")
                amount = " ".join(part for part in amount_parts if part)
                amount = re.sub(r"\s*-\s*", " - ", amount).strip()
                type_text = _column_text(first_line, columns["type"])
                atype = re.search(r"\[([A-Z]{2,3})\]", asset)
                asset_name = re.sub(r"\s*\[[A-Z]{2,3}\]\s*", " ", asset).strip()
                transactions.append(
                    {
                        "owner": owner or "Self",
                        "asset_name": asset_name,
                        "ticker": extract_ticker(asset_name),
                        "asset_type": atype.group(1) if atype else "",
                        "trade_type": HOUSE_TYPE_MAP.get(type_text, "other"),
                        "transaction_date": parse_us_date(
                            DATE_RE.search(_column_text(first_line, columns["date"])).group(0)
                        ),
                        "notification_date": parse_us_date(
                            (DATE_RE.search(_column_text(first_line, columns["notification"])) or [""])[0]
                            if DATE_RE.search(_column_text(first_line, columns["notification"])) else ""
                        ),
                        "amount": amount,
                    }
                )
    return transactions


PAPER_ASSET_NAME = "Declaracion escaneada (PDF en papel): revisar manualmente"


def paper_filing_row(filing_id, chamber, politician, state_district, filing_date,
                     source, source_url, senate_first="", senate_last=""):
    """Scanned filings cannot be parsed; keep a reviewable pointer to them."""
    return {
        "filing_id": filing_id,
        "chamber": chamber,
        "politician": politician,
        "senate_first": senate_first,
        "senate_last": senate_last,
        "state_district": state_district,
        "filing_date": filing_date,
        "source": source,
        "source_url": source_url,
        "owner": "",
        "asset_name": PAPER_ASSET_NAME,
        "ticker": "",
        "asset_type": "",
        "trade_type": "paper_filing",
        "extraction": "pending_manual",
        "transaction_date": None,
        "notification_date": None,
        "amount": "",
    }


def vision_transactions(result):
    """Convert a Claude vision extraction into parser-shaped transactions."""
    transactions = []
    for tx in result.get("transactions", []):
        asset = (tx.get("asset_name") or "").strip()
        ticker = (tx.get("ticker") or "").strip().upper() or extract_ticker(asset)
        transactions.append(
            {
                "owner": tx.get("owner") or "Self",
                "asset_name": asset,
                "ticker": ticker if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", ticker or "") else "",
                "asset_type": (tx.get("asset_type") or "").strip().upper()[:3],
                "trade_type": tx.get("trade_type") if tx.get("trade_type") in HOUSE_TYPE_MAP.values() else "other",
                "transaction_date": parse_us_date(tx.get("transaction_date")),
                "notification_date": parse_us_date(tx.get("notification_date")),
                "amount": re.sub(r"\s*-\s*", " - ", (tx.get("amount") or "").strip()),
            }
        )
    return transactions


def cached_transactions(chamber, filing_id, fetch_and_parse, cache_dir=PARSE_CACHE_DIR):
    """Electronic filings never change: parse each one once and reuse it every run."""
    path = Path(cache_dir) / chamber / f"{filing_id}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for tx in data:
            tx["transaction_date"] = parse_us_date(tx["transaction_date"])
            tx["notification_date"] = parse_us_date(tx["notification_date"])
        return data, True
    transactions = fetch_and_parse()
    if transactions:
        path.parent.mkdir(parents=True, exist_ok=True)
        serializable = [
            {**tx, "transaction_date": iso(tx["transaction_date"]), "notification_date": iso(tx["notification_date"])}
            for tx in transactions
        ]
        path.write_text(json.dumps(serializable, ensure_ascii=False), encoding="utf-8")
    return transactions, False


def collect_house(session, since, until, max_filings, delay, vision=None,
                  max_vision=0, priority_names=()):
    years = list(range(since.year, until.year + 1))
    filings = []
    for year in years:
        response = get_with_retry(session, HOUSE_INDEX_URL.format(year=year))
        filings.extend(parse_house_index(response.content))

    filings = [
        f for f in filings
        if f["filing_date"] and since <= f["filing_date"] <= until
    ]
    filings.sort(key=lambda f: (f["filing_date"], f["doc_id"]), reverse=True)

    electronic = [f for f in filings if f["doc_id"].startswith("2")]
    paper = [f for f in filings if not f["doc_id"].startswith("2")]
    print(
        f"House PTR filings {since}..{until}: {len(filings)} "
        f"({len(electronic)} electronic, {len(paper)} scanned listed for manual review)"
    )

    rows = []
    errors = 0
    # Scanned filings: read with Claude vision when enabled (cached per DocID),
    # followed politicians first; the rest stay listed for manual review.
    paper.sort(key=lambda f: f["last"].lower() not in priority_names)
    vision_used = 0
    for filing in paper:
        name = " ".join(p for p in [filing["first"], filing["last"]] if p)
        url = HOUSE_PTR_URL.format(year=filing["year"] or filing["filing_date"].year, doc_id=filing["doc_id"])
        transactions = None
        cached = vision is not None and vision.is_cached(filing["doc_id"])
        eligible = vision is not None and (
            not vision.only_followed or filing["last"].lower() in priority_names
        )
        if cached or (eligible and vision_used < max_vision):
            vision_used += 0 if cached else 1  # count attempts, not successes (cost cap)
            try:
                content = b"" if cached else get_with_retry(session, url).content
                result = vision.extract(content, name, filing["doc_id"])
                if result.get("is_periodic_transaction_report"):
                    transactions = vision_transactions(result)
            except Exception as exc:
                errors += 1
                print(f"WARNING: vision extraction failed for {filing['doc_id']}: {exc}")
        if not transactions:
            rows.append(paper_filing_row(
                filing_id=filing["doc_id"],
                chamber="house",
                politician=name,
                state_district=filing["state_district"],
                filing_date=filing["filing_date"],
                source="house_clerk",
                source_url=url,
            ))
            continue
        for tx in transactions:
            rows.append({
                "filing_id": filing["doc_id"],
                "chamber": "house",
                "politician": name,
                "state_district": filing["state_district"],
                "filing_date": filing["filing_date"],
                "source": "house_clerk",
                "source_url": url,
                "extraction": "claude_vision",
                **tx,
            })
    if vision is not None:
        print(f"Scanned filings read with Claude this run: {vision_used} (cap {max_vision})")
    downloads = 0
    for filing in electronic[:max_filings]:
        url = HOUSE_PTR_URL.format(year=filing["year"] or filing["filing_date"].year, doc_id=filing["doc_id"])

        def fetch_and_parse(url=url):
            content = get_with_retry(session, url).content
            if content[:4] != b"%PDF":
                raise ValueError("response is not a PDF")
            return parse_house_ptr_pdf(content)

        try:
            transactions, from_cache = cached_transactions("house", filing["doc_id"], fetch_and_parse)
        except Exception as exc:
            errors += 1
            print(f"WARNING: House PTR {filing['doc_id']} failed: {exc}")
            continue
        downloads += 0 if from_cache else 1

        if not transactions:
            print(f"WARNING: House PTR {filing['doc_id']} parsed 0 transactions")
        name = " ".join(p for p in [filing["first"], filing["last"], filing["suffix"]] if p)
        for tx in transactions:
            rows.append(
                {
                    "filing_id": filing["doc_id"],
                    "chamber": "house",
                    "politician": name,
                    "state_district": filing["state_district"],
                    "filing_date": filing["filing_date"],
                    "source": "house_clerk",
                    "source_url": url,
                    **tx,
                }
            )
        if delay and not from_cache:
            time.sleep(delay)

    print(f"House transactions: {len(rows)} (new PDFs parsed: {downloads}, errors: {errors})")
    return rows, errors


# ---------------------------------------------------------------------------
# Senate
# ---------------------------------------------------------------------------


def senate_session(session):
    home = get_with_retry(session, SENATE_BASE + "/search/home/")
    soup = BeautifulSoup(home.text, "lxml")
    token_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    if token_input is None:
        raise RuntimeError("Senate eFD: agreement form not found (blocked or layout changed)")
    response = session.post(
        SENATE_BASE + "/search/home/",
        data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token_input["value"]},
        headers={"Referer": SENATE_BASE + "/search/home/"},
        timeout=45,
    )
    response.raise_for_status()
    csrf = session.cookies.get("csrftoken") or token_input["value"]
    return csrf


def senate_report_list(session, csrf, since, until, page_size=100, max_reports=5000):
    reports = []
    start = 0
    while len(reports) < max_reports:
        payload = {
            "start": str(start),
            "length": str(page_size),
            "report_types": "[11]",
            "filer_types": "[]",
            "submitted_start_date": since.strftime("%m/%d/%Y 00:00:00"),
            "submitted_end_date": until.strftime("%m/%d/%Y 23:59:59"),
            "candidate_state": "",
            "senator_state": "",
            "office_id": "",
            "first_name": "",
            "last_name": "",
            "csrfmiddlewaretoken": csrf,
        }
        response = session.post(
            SENATE_BASE + "/search/report/data/",
            data=payload,
            headers={"Referer": SENATE_BASE + "/search/", "X-CSRFToken": csrf},
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("data", [])
        for row in rows:
            first, last, office, link_html, received = (list(row) + [""] * 5)[:5]
            link = BeautifulSoup(link_html, "lxml").find("a")
            href = link["href"] if link else ""
            reports.append(
                {
                    "first": BeautifulSoup(first, "lxml").get_text(" ", strip=True),
                    "last": BeautifulSoup(last, "lxml").get_text(" ", strip=True),
                    "office": BeautifulSoup(office, "lxml").get_text(" ", strip=True),
                    "href": href,
                    "title": link.get_text(" ", strip=True) if link else "",
                    "filing_date": parse_us_date(received),
                }
            )
        total = int(data.get("recordsFiltered") or data.get("recordsTotal") or 0)
        start += page_size
        if not rows or start >= total:
            break
    return reports


def parse_senate_ptr_html(html):
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    transactions = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 8:
            continue
        _, tdate, owner, ticker, asset_name, asset_type, tx_type, amount = cells[:8]
        ticker = "" if ticker in ("--", "") else ticker.split()[0].upper()
        transactions.append(
            {
                "owner": owner or "Self",
                "asset_name": asset_name,
                "ticker": ticker or extract_ticker(asset_name),
                "asset_type": asset_type,
                "trade_type": SENATE_TYPE_MAP.get(tx_type.lower().strip(), "other"),
                "transaction_date": parse_us_date(tdate),
                "notification_date": None,
                "amount": re.sub(r"\s*-\s*", " - ", amount),
            }
        )
    return transactions


def collect_senate(session, since, until, max_filings, delay):
    csrf = senate_session(session)
    reports = senate_report_list(session, csrf, since, until)
    electronic = [r for r in reports if "/search/view/ptr/" in r["href"]]
    print(
        f"Senate PTR reports {since}..{until}: {len(reports)} "
        f"({len(electronic)} electronic, {len(reports) - len(electronic)} paper listed for manual review)"
    )
    rows = []
    errors = 0
    for report in reports:
        if report in electronic or not report["href"]:
            continue
        rows.append(paper_filing_row(
            filing_id=report["href"].rstrip("/").split("/")[-1],
            chamber="senate",
            politician=f"{report['first']} {report['last']}".strip(),
            state_district="",
            filing_date=report["filing_date"],
            source="senate_efd",
            source_url=SENATE_BASE + report["href"],
            senate_first=report["first"],
            senate_last=report["last"],
        ))
    for report in electronic[:max_filings]:
        url = SENATE_BASE + report["href"]
        filing_id = report["href"].rstrip("/").split("/")[-1]

        def fetch_and_parse(url=url):
            response = get_with_retry(session, url, headers={"Referer": SENATE_BASE + "/search/"})
            return parse_senate_ptr_html(response.text)

        try:
            transactions, from_cache = cached_transactions("senate", filing_id, fetch_and_parse)
        except Exception as exc:
            errors += 1
            print(f"WARNING: Senate PTR {url} failed: {exc}")
            continue
        if not transactions:
            print(f"WARNING: Senate PTR {filing_id} parsed 0 transactions")
        for tx in transactions:
            rows.append(
                {
                    "filing_id": filing_id,
                    "chamber": "senate",
                    "politician": f"{report['first']} {report['last']}".strip(),
                    "senate_first": report["first"],
                    "senate_last": report["last"],
                    "state_district": "",
                    "filing_date": report["filing_date"],
                    "source": "senate_efd",
                    "source_url": url,
                    **tx,
                }
            )
        if delay and not from_cache:
            time.sleep(delay)
    print(f"Senate transactions parsed: {len(rows)} (errors: {errors})")
    return rows, errors


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def enrich_rows(rows, directory, resolver, config):
    enriched = []
    unmatched = set()
    for row in rows:
        if row["chamber"] == "house":
            member = directory.find_house_member(row["state_district"]) if directory else None
        else:
            member = (
                directory.find_senator(row.get("senate_first", ""), row.get("senate_last", ""))
                if directory
                else None
            )
        if member is None:
            unmatched.add(row["politician"])
        elif row["chamber"] == "senate":
            row["state_district"] = member["state"]

        tdate = row.get("transaction_date")
        reference_date = row.get("notification_date") or row.get("filing_date")
        delay_days = (reference_date - tdate).days if tdate and reference_date else None
        filing_delay = (row["filing_date"] - tdate).days if tdate and row.get("filing_date") else None

        sector_info = resolver.resolve(row["ticker"]) if row["ticker"] else {
            "sectors": [], "sic": "", "sic_description": "",
        }
        conflict = conflicts.evaluate_conflict(
            config,
            member["committees"] if member else [],
            sector_info["sectors"],
            row["trade_type"],
            row["amount"],
            filing_delay if filing_delay is not None else delay_days,
        )
        enriched.append(
            {
                "filing_id": row["filing_id"],
                "chamber": row["chamber"],
                "politician": member["full"] if member else row["politician"],
                "bioguide_id": member["bioguide"] if member else "",
                "party": member["party"] if member else "",
                "state_district": row["state_district"],
                "owner": row["owner"],
                "asset_name": row["asset_name"],
                "ticker": row["ticker"],
                "asset_type": row["asset_type"],
                "trade_type": row["trade_type"],
                "transaction_date": iso(tdate),
                "notification_date": iso(row.get("notification_date")),
                "filing_date": iso(row.get("filing_date")),
                "disclosure_delay_days": "" if filing_delay is None else str(filing_delay),
                "amount": row["amount"],
                "amount_min": str(conflicts.parse_amount_min(row["amount"])),
                "ticker_sectors": "; ".join(sector_info["sectors"]),
                "sic": sector_info["sic"],
                "sic_description": sector_info["sic_description"],
                "member_committees": conflicts.member_committee_summary(member),
                "conflict": conflict["conflict"],
                "conflict_score": str(conflict["conflict_score"]),
                "conflict_sectors": conflict["conflict_sectors"],
                "conflict_committees": conflict["conflict_committees"],
                "conflict_reason": conflict["conflict_reason"],
                "source": row["source"],
                "source_url": row["source_url"],
                "extraction": row.get("extraction") or ("html" if row["chamber"] == "senate" else "pdf_text"),
            }
        )
    if unmatched:
        print(f"WARNING: members not matched to committee data: {sorted(unmatched)}")
    return enriched


def dedupe_rows(rows):
    seen = set()
    unique = []
    for row in rows:
        key = (
            row["filing_id"], row["owner"], row["asset_name"], row["trade_type"],
            row["transaction_date"], row["amount"],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def write_csv(rows, output_file=OUTPUT_FILE):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows):
    buys = [r for r in rows if r["trade_type"] == "buy"]
    flagged = sorted(
        (r for r in rows if r["conflict"] == "YES"),
        key=lambda r: -int(r["conflict_score"]),
    )
    print(f"\nTransactions: {len(rows)} | buys: {len(buys)} | conflict-flagged: {len(flagged)}")
    print("\n--- TOP POSSIBLE CONFLICTS ---")
    for row in flagged[:25]:
        print(
            f"[{row['conflict_score']}] {row['transaction_date']} {row['politician']} "
            f"({row['chamber']}) {row['trade_type'].upper()} {row['ticker'] or row['asset_name'][:40]} "
            f"{row['amount']} | {row['conflict_committees']}"
        )


class VisionReader:
    """Thin adapter so collect_house does not depend on the agent module directly.

    Cost guards: only followed politicians (configurable), and long scans (e.g.
    full brokerage statements) above `vision_max_pages` are left for manual review.
    """

    def __init__(self, client, only_followed=True, max_pages=15):
        self.client = client
        self.only_followed = only_followed
        self.max_pages = max_pages

    def is_cached(self, doc_id):
        from radar.agent import CACHE_DIR

        return (CACHE_DIR / "vision" / f"{doc_id}.json").exists()

    def extract(self, pdf_bytes, filer_name, doc_id):
        if pdf_bytes:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                pages = len(pdf.pages)
            if pages > self.max_pages:
                raise ValueError(f"{pages} pages > vision_max_pages {self.max_pages}; left for manual review")
        return self.client.extract_scanned_ptr(pdf_bytes, filer_name, doc_id)


def build_vision_reader(sheet=None):
    from radar import agent, tracker

    agent_config = tracker.load_tracker_config().get("agent", {})
    spent = 0.0
    if sheet is not None and agent.api_key():
        try:
            spent = agent.month_spend(sheet)
        except Exception as exc:
            print(f"WARNING: could not read agent_usage ledger: {exc}")
    client = agent.AgentClient(requests.Session(), agent_config, spent_this_month=spent)
    if not agent_config.get("vision", True) or not client.enabled:
        reason = "budget reached" if client.key and client.remaining() <= 0 else "disabled or no ANTHROPIC_API_KEY"
        print(f"Claude vision off ({reason}): scanned filings listed for manual review")
        return None, agent_config, None
    reader = VisionReader(
        client,
        only_followed=agent_config.get("vision_only_followed", True),
        max_pages=agent_config.get("vision_max_pages", 15),
    )
    return reader, agent_config, client


def open_ledger_sheet():
    """Google Sheet used for the agent budget ledger (None when not configured)."""
    try:
        from radar.sheets import open_sheet

        return open_sheet()
    except Exception as exc:
        print(f"WARNING: Google Sheets unavailable for agent ledger: {exc}")
        return None


def parse_args():
    parser = argparse.ArgumentParser(description="Collect official congressional PTR trades.")
    parser.add_argument("--days", type=int, default=30, help="Filing-date lookback window.")
    parser.add_argument("--since", default="", help="Backfill from this filing date (YYYY-MM-DD); overrides --days.")
    parser.add_argument("--chambers", default="house,senate")
    parser.add_argument("--max-filings", type=int, default=200, help="Max filings per chamber.")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--no-sec", action="store_true", help="Skip SEC SIC lookups.")
    parser.add_argument("--max-vision", type=int, default=None, help="Max new scanned filings read with Claude (default: config).")
    return parser.parse_args()


def main():
    args = parse_args()
    until = date.today()
    since = date.fromisoformat(args.since) if args.since else until - timedelta(days=args.days)
    chambers = {c.strip() for c in args.chambers.split(",") if c.strip()}

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    raw_rows = []
    failures = []
    from radar import agent as agent_module

    ledger = open_ledger_sheet() if agent_module.api_key() else None
    vision, agent_config, agent_client = build_vision_reader(ledger)
    max_vision = args.max_vision if args.max_vision is not None else agent_config.get("max_vision_filings_per_run", 2)
    from radar import tracker

    priority = {  # last names of followed politicians: their scans are read first
        p["name"].replace(", Jr.", "").split()[-1].lower()
        for p in tracker.load_tracker_config().get("followed_politicians", [])
    }
    if "house" in chambers:
        try:
            rows, _ = collect_house(
                session, since, until, args.max_filings, args.delay,
                vision=vision, max_vision=max_vision, priority_names=priority,
            )
            raw_rows.extend(rows)
        except Exception as exc:
            failures.append(f"house: {exc}")
            print(f"ERROR: House collection failed: {exc}")
    if "senate" in chambers:
        try:
            senate = requests.Session()
            senate.headers.update(BROWSER_HEADERS)
            rows, _ = collect_senate(senate, since, until, args.max_filings, args.delay)
            raw_rows.extend(rows)
        except Exception as exc:
            failures.append(f"senate: {exc}")
            print(f"ERROR: Senate collection failed: {exc}")

    config = conflicts.load_config()
    try:
        conflicts.download_reference_files(session)
        directory = conflicts.CongressDirectory.from_reference_dir()
    except Exception as exc:
        print(f"WARNING: committee data unavailable, conflicts cannot be evaluated: {exc}")
        directory = None
    resolver = conflicts.SectorResolver(config, session=None if args.no_sec else requests.Session())
    rows = dedupe_rows(enrich_rows(raw_rows, directory, resolver, config))
    resolver.save_cache()

    rows.sort(key=lambda r: (r["filing_date"], r["filing_id"], r["asset_name"]), reverse=True)
    write_csv(rows, args.output)
    print(f"CSV generated: {args.output}")
    print_summary(rows)

    if agent_client is not None:
        print(agent_client.usage_line())
        if ledger is not None:
            agent_module.record_usage(ledger, agent_client, "vision")
    if failures:
        print("\nPartial failures: " + " | ".join(failures))
    if not rows and failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
