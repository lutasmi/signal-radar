"""Committee / sector conflict detection for congressional trades.

Pure helpers plus small network loaders. Everything that touches the network
caches to data/reference/ so a run is repeatable and auditable.

Data used:
* unitedstates/congress-legislators (public domain, GitHub): current
  legislators, committees and committee membership.
* SEC EDGAR company_tickers.json + submissions API: SIC code per ticker.
* config/conflict_sectors.json: committee -> sectors and sector definitions.
"""

import json
import os
import re
import time
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config" / "conflict_sectors.json"
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"

LEGISLATORS_BASE_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/"
)
LEGISLATOR_FILES = [
    "legislators-current.yaml",
    "committees-current.yaml",
    "committee-membership-current.yaml",
]
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SIC_CACHE_FILE = "sec_sic_cache.json"

LEADERSHIP_TITLES = ("chair", "ranking", "vice chair")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path=CONFIG_FILE):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------


def normalize_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-zA-Z ]", " ", value).lower()
    return " ".join(value.split())


def name_tokens(value):
    stop = {"hon", "jr", "sr", "ii", "iii", "iv", "mr", "mrs", "ms", "dr"}
    return [token for token in normalize_name(value).split() if token not in stop]


# ---------------------------------------------------------------------------
# Legislators and committees
# ---------------------------------------------------------------------------


def download_reference_files(session, reference_dir=REFERENCE_DIR, max_age_hours=20):
    reference_dir.mkdir(parents=True, exist_ok=True)
    for file_name in LEGISLATOR_FILES:
        target = reference_dir / file_name
        if target.exists() and time.time() - target.stat().st_mtime < max_age_hours * 3600:
            continue
        response = session.get(LEGISLATORS_BASE_URL + file_name, timeout=60)
        response.raise_for_status()
        target.write_bytes(response.content)
    return reference_dir


def load_yaml(path):
    import yaml

    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class CongressDirectory:
    """Lookup of current members with their committee assignments."""

    def __init__(self, legislators, committees, membership):
        self.members = {}
        self.house_by_seat = {}
        self.senators_by_state = {}
        for legislator in legislators:
            bioguide = legislator["id"]["bioguide"]
            term = legislator["terms"][-1]
            name = legislator.get("name", {})
            member = {
                "bioguide": bioguide,
                "first": name.get("first", ""),
                "last": name.get("last", ""),
                "nickname": name.get("nickname", ""),
                "full": name.get("official_full")
                or f"{name.get('first', '')} {name.get('last', '')}",
                "chamber": "senate" if term["type"] == "sen" else "house",
                "state": term.get("state", ""),
                "district": term.get("district"),
                "party": term.get("party", ""),
                "committees": [],
            }
            self.members[bioguide] = member
            if member["chamber"] == "house":
                district = member["district"] if member["district"] is not None else 0
                self.house_by_seat[(member["state"], int(district))] = member
            else:
                self.senators_by_state.setdefault(member["state"], []).append(member)

        self.committee_names = {}
        for committee in committees:
            thomas_id = committee["thomas_id"]
            self.committee_names[thomas_id] = committee["name"]
            for subcommittee in committee.get("subcommittees") or []:
                self.committee_names[thomas_id + subcommittee["thomas_id"]] = (
                    f"{committee['name']} - {subcommittee['name']}"
                )

        for committee_id, entries in (membership or {}).items():
            for entry in entries or []:
                member = self.members.get(entry.get("bioguide", ""))
                if member is None:
                    continue
                member["committees"].append(
                    {
                        "id": committee_id,
                        "name": self.committee_names.get(committee_id, committee_id),
                        "title": entry.get("title", ""),
                    }
                )

    @classmethod
    def from_reference_dir(cls, reference_dir=REFERENCE_DIR):
        return cls(
            load_yaml(reference_dir / "legislators-current.yaml"),
            load_yaml(reference_dir / "committees-current.yaml"),
            load_yaml(reference_dir / "committee-membership-current.yaml"),
        )

    def find_house_member(self, state_district):
        match = re.match(r"^([A-Z]{2})(\d{1,2})$", (state_district or "").strip())
        if not match:
            return None
        return self.house_by_seat.get((match.group(1), int(match.group(2))))

    def find_senator(self, first_name, last_name, state=""):
        candidates = []
        states = [state] if state else list(self.senators_by_state)
        last_tokens = name_tokens(last_name)
        first_tokens = name_tokens(first_name)
        for candidate_state in states:
            for member in self.senators_by_state.get(candidate_state, []):
                member_last = name_tokens(member["last"])
                if not last_tokens or not member_last:
                    continue
                if last_tokens[-1] != member_last[-1]:
                    continue
                candidates.append(member)
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        if first_tokens:
            for member in candidates:
                names = name_tokens(member["first"]) + name_tokens(member["nickname"])
                if first_tokens[0] in names or any(
                    name.startswith(first_tokens[0][:3]) for name in names
                ):
                    return member
        return None


# ---------------------------------------------------------------------------
# Ticker sector lookup
# ---------------------------------------------------------------------------


class SectorResolver:
    """Ticker -> sector, using curated lists first and SEC SIC codes second."""

    def __init__(self, config, session=None, reference_dir=REFERENCE_DIR, user_agent=None):
        self.config = config
        self.session = session
        self.reference_dir = reference_dir
        self.user_agent = user_agent if user_agent is not None else os.getenv("SEC_USER_AGENT", "")
        self.curated = {}
        for sector, spec in config["sectors"].items():
            for ticker in spec.get("tickers", []):
                self.curated.setdefault(ticker.upper(), []).append(sector)
        self._cik_by_ticker = None
        self._sic_cache = self._load_sic_cache()
        self.sec_errors = 0

    # cache ----------------------------------------------------------------
    def _cache_path(self):
        return self.reference_dir / SIC_CACHE_FILE

    def _load_sic_cache(self):
        path = self._cache_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                return {}
        return {}

    def save_cache(self):
        if not self._sic_cache:
            return
        self.reference_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path().write_text(
            json.dumps(self._sic_cache, indent=1, sort_keys=True), encoding="utf-8"
        )

    # SEC ------------------------------------------------------------------
    def _sec_headers(self):
        return {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}

    def _load_cik_map(self):
        if self._cik_by_ticker is not None:
            return self._cik_by_ticker
        self._cik_by_ticker = {}
        if self.session is None or not self.user_agent:
            return self._cik_by_ticker
        try:
            response = self.session.get(SEC_TICKERS_URL, headers=self._sec_headers(), timeout=30)
            response.raise_for_status()
            for item in response.json().values():
                self._cik_by_ticker[str(item["ticker"]).upper()] = int(item["cik_str"])
        except Exception as exc:  # network failures fall back to curated lists
            self.sec_errors += 1
            print(f"WARNING: SEC ticker map unavailable: {exc}")
        return self._cik_by_ticker

    def sic_for_ticker(self, ticker):
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return {"sic": "", "sic_description": ""}
        if ticker in self._sic_cache:
            return self._sic_cache[ticker]
        cik_map = self._load_cik_map()
        cik = cik_map.get(ticker) or cik_map.get(ticker.replace(".", "-"))
        if not cik:
            return {"sic": "", "sic_description": ""}
        try:
            response = self.session.get(
                SEC_SUBMISSIONS_URL.format(cik=cik), headers=self._sec_headers(), timeout=30
            )
            response.raise_for_status()
            data = response.json()
            result = {
                "sic": str(data.get("sic") or ""),
                "sic_description": data.get("sicDescription") or "",
            }
            time.sleep(0.12)  # stay well below SEC's 10 requests/second limit
        except Exception as exc:
            self.sec_errors += 1
            print(f"WARNING: SEC SIC lookup failed for {ticker}: {exc}")
            return {"sic": "", "sic_description": ""}
        self._sic_cache[ticker] = result
        return result

    # public ---------------------------------------------------------------
    def sectors_for_sic(self, sic):
        try:
            code = int(sic)
        except (TypeError, ValueError):
            return []
        sectors = []
        for sector, spec in self.config["sectors"].items():
            for low, high in spec.get("sic_ranges", []):
                if low <= code <= high:
                    sectors.append(sector)
                    break
        return sectors

    def resolve(self, ticker):
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return {"sectors": [], "sic": "", "sic_description": "", "basis": ""}
        sectors = list(self.curated.get(ticker, []))
        basis = "curated" if sectors else ""
        sic_info = self.sic_for_ticker(ticker) if self.session is not None else {
            "sic": "",
            "sic_description": "",
        }
        for sector in self.sectors_for_sic(sic_info["sic"]):
            if sector not in sectors:
                sectors.append(sector)
                basis = f"{basis}+sic" if basis else "sic"
        return {
            "sectors": sectors,
            "sic": sic_info["sic"],
            "sic_description": sic_info["sic_description"],
            "basis": basis,
        }


# ---------------------------------------------------------------------------
# Conflict evaluation
# ---------------------------------------------------------------------------


def parse_amount_min(amount):
    numbers = re.findall(r"\$\s*([\d,]+)", amount or "")
    if not numbers:
        return 0
    return int(numbers[0].replace(",", ""))


def is_leadership(title):
    lowered = (title or "").lower()
    if "ex officio" in lowered:
        return False
    return any(word in lowered for word in LEADERSHIP_TITLES)


def committee_sectors(config, committee_id):
    """Sectors for a committee id, falling back to its parent committee."""
    mapping = config["committees"]
    if committee_id in mapping:
        return mapping[committee_id]
    return mapping.get(committee_id[:4], [])


def evaluate_conflict(config, member_committees, ticker_sectors, trade_type, amount, delay_days):
    """Return conflict fields for one trade. Deterministic and explainable."""
    scoring = config["scoring"]
    matches = []
    for committee in member_committees:
        overlap = [
            sector for sector in committee_sectors(config, committee["id"])
            if sector in ticker_sectors
        ]
        if overlap:
            matches.append((committee, overlap))

    result = {
        "conflict": "NO",
        "conflict_score": 0,
        "conflict_sectors": "",
        "conflict_committees": "",
        "conflict_reason": "",
    }
    if not matches:
        return result

    sectors = sorted({sector for _, overlap in matches for sector in overlap})
    leadership = [c for c, _ in matches if is_leadership(c["title"])]
    has_subcommittee = any(
        len(c["id"]) > 4 and "ex officio" not in (c["title"] or "").lower()
        for c, _ in matches
    )

    score = scoring["base_conflict"]
    reasons = [
        "comité con jurisdicción sobre "
        + ", ".join(config["sectors"][s]["label"] for s in sectors)
    ]
    if leadership:
        score += scoring["leadership_bonus"]
        reasons.append(
            "cargo de liderazgo: "
            + "; ".join(f"{c['title']} {c['name']}" for c in leadership)
        )
    if has_subcommittee:
        score += scoring["subcommittee_bonus"]
        reasons.append("miembro de subcomité específico")

    amount_min = parse_amount_min(amount)
    for tier in scoring["amount_bonus"]:
        if amount_min >= tier["min_amount"]:
            score += tier["weight"]
            reasons.append(f"importe desde ${amount_min:,}")
            break

    if delay_days is not None and delay_days > scoring["late_filing_days"]:
        score += scoring["late_filing_bonus"]
        reasons.append(f"declarado tarde ({delay_days} días; límite STOCK Act 45)")

    if trade_type != "buy":
        # Sales by committee members are also relevant but scored lower.
        score = score // 2
        reasons.append(f"operación de tipo {trade_type}")

    committee_names = []
    for committee, _ in matches:
        if len(committee["id"]) > 4 and not is_leadership(committee["title"]):
            continue  # keep the summary short: parent committees + led subcommittees
        label = committee["name"]
        if committee["title"]:
            label += f" ({committee['title']})"
        if label not in committee_names:
            committee_names.append(label)

    result.update(
        {
            "conflict": "YES",
            "conflict_score": score,
            "conflict_sectors": "; ".join(sectors),
            "conflict_committees": "; ".join(committee_names),
            "conflict_reason": "; ".join(reasons),
        }
    )
    return result


def member_committee_summary(member, max_items=6):
    if not member:
        return ""
    names = []
    for committee in member["committees"]:
        if len(committee["id"]) > 4:
            continue  # parent committee is enough for the summary
        label = committee["name"]
        if committee["title"]:
            label += f" ({committee['title']})"
        names.append(label)
    return "; ".join(names[:max_items])
