import sys
import csv
import glob
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors import (
    collect_congress_gov,
    collect_fec,
    collect_federal_register,
    collect_grants_gov,
    collect_lda_gov,
    collect_sam_gov,
    collect_sec_edgar_additional,
    collect_uspto,
)
from radar.candidate_sources import (
    SourceAuthenticationError,
    SourceRateLimitError,
    request_with_retries,
    sanitize_message,
)


class FakeResponse:
    def __init__(self, status_code, text="{}", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def request(self, *args, **kwargs):
        return self.responses.pop(0)


def assert_equal(first, second, label):
    if first != second:
        raise ValueError(f"{label}: output is not deterministic")


def validate_http_error_classes():
    secret_message = (
        "https://example.test/path?api_key=SECRET&limit=1 "
        "https://example.test/path?token=SECRET2"
    )
    sanitized = sanitize_message(secret_message)
    if "SECRET" in sanitized:
        raise ValueError("sensitive query values were not redacted")

    try:
        request_with_retries(FakeSession([FakeResponse(401, "bad key")]), "GET", "https://example.test")
    except SourceAuthenticationError:
        pass
    else:
        raise ValueError("HTTP 401 was not classified as authentication failure")

    try:
        request_with_retries(
            FakeSession([FakeResponse(429, "rate limit")]),
            "GET",
            "https://example.test",
            attempts=1,
        )
    except SourceRateLimitError:
        pass
    else:
        raise ValueError("HTTP 429 was not classified as rate limit")


def validate_sam_gov():
    raw = {
        "noticeId": "5b345bbb7127b91a3ad577b203fc6f68",
        "title": "Historic Office Renovation",
        "solicitationNumber": "47PF0018R0023",
        "postedDate": "2018-05-04",
        "responseDeadLine": "2018-06-04T17:00:00-04:00",
        "archiveDate": "2018-07-04",
        "modifiedDate": "2018-05-05",
        "type": "Award Notice",
        "baseType": "Combined Synopsis/Solicitation",
        "fullParentPathName": "GENERAL SERVICES ADMINISTRATION.PUBLIC BUILDINGS SERVICE",
        "organizationType": "OFFICE",
        "naicsCode": "236220",
        "classificationCode": "Z",
        "active": "Yes",
        "award": {
            "date": "2018-05-04",
            "number": "47PF0018C0066",
            "amount": "800620",
            "awardee": {"name": "D.G. Beyer, Inc.", "ueiSAM": "025114695AST"},
        },
        "links": [{"href": "https://api.sam.gov/opportunities/v2/search?noticeid=x"}],
    }
    rows = [collect_sam_gov.normalize_opportunity(raw)]
    assert_equal(rows, [collect_sam_gov.normalize_opportunity(raw)], "sam_gov")
    collect_sam_gov.validate_rows(rows)
    collect_sam_gov.validate_rows([], allow_empty=True)
    try:
        collect_sam_gov.validate_rows(rows + rows)
    except ValueError:
        pass
    else:
        raise ValueError("sam_gov duplicate notice_id was not detected")
    return len(rows)


def validate_congress_gov():
    raw = {
        "congress": 119,
        "type": "HR",
        "number": "1",
        "title": "Example Act",
        "originChamber": "House",
        "introducedDate": "2026-01-03",
        "latestAction": {"actionDate": "2026-01-04", "text": "Referred to the Committee."},
        "sponsors": [{"fullName": "Example Member", "bioguideId": "X000001"}],
        "policyArea": {"name": "Finance and Financial Sector"},
        "url": "https://api.congress.gov/v3/bill/119/hr/1",
    }
    rows = [collect_congress_gov.normalize_bill(raw)]
    assert_equal(rows, [collect_congress_gov.normalize_bill(raw)], "congress_gov")
    collect_congress_gov.validate_rows(rows)
    collect_congress_gov.validate_rows([], allow_empty=True)
    if "COMMITTEE_ACTION" not in rows[0]["signal_types"]:
        raise ValueError("congress_gov did not derive COMMITTEE_ACTION")
    return len(rows)


def validate_federal_register():
    raw = {
        "document_number": "2026-13491",
        "publication_date": "2026-07-02",
        "type": "Rule",
        "title": "Rescission of Guidelines",
        "agencies": [{"name": "Equal Employment Opportunity Commission", "slug": "eeoc"}],
        "cfr_references": [{"title": 29, "part": 1608}],
        "html_url": "https://www.federalregister.gov/documents/2026/07/02/2026-13491/x",
        "pdf_url": "https://www.govinfo.gov/content/pkg/FR-2026-07-02/pdf/2026-13491.pdf",
        "abstract": "Example abstract.",
    }
    rows = [collect_federal_register.normalize_document(raw)]
    assert_equal(rows, [collect_federal_register.normalize_document(raw)], "federal_register")
    collect_federal_register.validate_rows(rows)
    collect_federal_register.validate_rows([], allow_empty=True)
    if rows[0]["signal_type"] != "RULE_FINAL":
        raise ValueError("federal_register did not map Rule to RULE_FINAL")
    return len(rows)


def validate_lda_gov():
    raw = {
        "url": "https://lda.senate.gov/api/v1/filings/a934e791/",
        "filing_uuid": "a934e791-d564-4fd3-8b78-041f8cfcf115",
        "filing_type": "RR",
        "filing_type_display": "Registration",
        "filing_year": 2026,
        "filing_period": "first_quarter",
        "dt_posted": "2026-07-01T11:31:00-04:00",
        "registrant": {"id": 1, "name": "EXAMPLE REGISTRANT"},
        "client": {"id": 2, "name": "EXAMPLE CLIENT"},
        "income": "10000.00",
        "expenses": None,
        "lobbying_activities": [
            {
                "general_issue_code": "DEF",
                "general_issue_code_display": "Defense",
                "government_entities": [{"name": "HOUSE OF REPRESENTATIVES"}],
            }
        ],
    }
    rows = [collect_lda_gov.normalize_filing(raw)]
    assert_equal(rows, [collect_lda_gov.normalize_filing(raw)], "lda_gov")
    collect_lda_gov.validate_rows(rows)
    collect_lda_gov.validate_rows([], allow_empty=True)
    if "LOBBY_NEW_REGISTRATION" not in rows[0]["signal_types"]:
        raise ValueError("lda_gov did not derive LOBBY_NEW_REGISTRATION")
    return len(rows)


def validate_sec_edgar_additional():
    feed_xml = """<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<category term="8-K"/>
<id>https://www.sec.gov/Archives/edgar/data/320193/000032019326000001-index.htm?accession-number=0000320193-26-000001</id>
<title>- APPLE INC (0000320193) (Filer)</title>
<updated>2026-07-01T12:00:00-04:00</updated>
<summary>Filed: 2026-07-01 AccNo: 0000320193-26-000001</summary>
<link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000001-index.htm"/>
</entry>
</feed>"""
    rows = collect_sec_edgar_additional.parse_feed(feed_xml, "8-K")
    assert_equal(rows, collect_sec_edgar_additional.parse_feed(feed_xml, "8-K"), "sec_edgar_additional")
    collect_sec_edgar_additional.validate_rows(rows)
    collect_sec_edgar_additional.validate_rows([], allow_empty=True)
    merged, duplicate_count = collect_sec_edgar_additional.merge_unique_rows(rows + rows)
    if len(merged) != len(rows) or duplicate_count != len(rows):
        raise ValueError("sec_edgar_additional duplicate tracking failed")
    if rows[0]["signal_type"] != "MATERIAL_EVENT_8K":
        raise ValueError("sec_edgar_additional did not map 8-K")
    return len(rows)


def validate_fec():
    raw = {
        "transaction_id": "SA11AI.123",
        "committee_id": "C00000000",
        "committee": {"name": "EXAMPLE PAC"},
        "contributor_name": "JANE EXECUTIVE",
        "contributor_employer": "EXAMPLE INC",
        "contributor_occupation": "CEO",
        "contributor_city": "NEW YORK",
        "contributor_state": "NY",
        "contribution_receipt_date": "2026-06-30T00:00:00",
        "contribution_receipt_amount": 1000,
        "two_year_transaction_period": 2026,
    }
    rows = [collect_fec.normalize_contribution(raw)]
    assert_equal(rows, [collect_fec.normalize_contribution(raw)], "fec")
    collect_fec.validate_rows(rows)
    collect_fec.validate_rows([], allow_empty=True)
    if collect_fec.count_unusable_rows(rows) != 0:
        raise ValueError("fec counted valid rows as unusable")
    if "EXECUTIVE_CONTRIBUTION" not in rows[0]["signal_types"]:
        raise ValueError("fec did not derive EXECUTIVE_CONTRIBUTION")
    return len(rows)


def validate_grants_gov():
    raw = {
        "id": "356623",
        "number": "DE-FOA-0003467",
        "title": "SEEDING CRITICAL ADVANCES",
        "agencyCode": "DOE-ARPAE",
        "agency": "Advanced Research Projects Agency Energy",
        "openDate": "10/02/2024",
        "closeDate": "09/29/2029",
        "oppStatus": "posted",
        "docType": "synopsis",
        "cfdaList": ["81.135"],
    }
    rows = [collect_grants_gov.normalize_opportunity(raw)]
    assert_equal(rows, [collect_grants_gov.normalize_opportunity(raw)], "grants_gov")
    collect_grants_gov.validate_rows(rows)
    collect_grants_gov.validate_rows([], allow_empty=True)
    if rows[0]["open_date"] != "2024-10-02":
        raise ValueError("grants_gov did not parse openDate")
    return len(rows)


def validate_uspto():
    raw = {
        "applicationNumberText": "18123456",
        "filingDate": "2026-02-01",
        "publicationDate": "2026-06-01",
        "inventionTitle": "Example Patent Application",
        "applicantName": "EXAMPLE INC",
        "assigneeName": "EXAMPLE INC",
        "inventors": [{"name": "Jane Inventor"}],
    }
    rows = [collect_uspto.normalize_application(raw)]
    assert_equal(rows, [collect_uspto.normalize_application(raw)], "uspto")
    collect_uspto.validate_rows(rows)
    collect_uspto.validate_rows([], allow_empty=True)
    return len(rows)


def validate_candidate_sources():
    validators = [
        ("http_error_classes", validate_http_error_classes),
        ("sam_gov", validate_sam_gov),
        ("congress_gov", validate_congress_gov),
        ("federal_register", validate_federal_register),
        ("lda_gov", validate_lda_gov),
        ("sec_edgar_additional", validate_sec_edgar_additional),
        ("fec", validate_fec),
        ("grants_gov", validate_grants_gov),
        ("uspto", validate_uspto),
    ]
    summary = []
    for name, validator in validators:
        count = validator() or 1
        print(f"OK candidate_source {name}: {count}")
        summary.append((name, count))
    return summary


def latest_file(pattern):
    matches = sorted(glob.glob(str(pattern)))
    if not matches:
        raise FileNotFoundError(f"generated candidate file not found: {pattern}")
    return Path(matches[-1])


def read_csv_rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_run_id(path, source):
    name = path.stem
    prefix = f"{source}_"
    if not name.startswith(prefix):
        raise ValueError(f"cannot parse run id from {path}")
    return name[len(prefix):]


def require_iso_date(value, label):
    if not value:
        return
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{label}: invalid date {value}") from exc


def validate_generated_sam_gov(data_dir=Path("data")):
    processed = latest_file(data_dir / "processed/sam_gov/sam_gov_*.csv")
    run_id = parse_run_id(processed, "sam_gov")
    raw_files = sorted((data_dir / "raw/sam_gov").glob(f"sam_gov_{run_id}_page_*.json"))
    if not raw_files:
        raise FileNotFoundError(f"SAM.gov raw pages not found for {run_id}")
    page_numbers = [path.stem.rsplit("_page_", 1)[1] for path in raw_files]
    if len(page_numbers) != len(set(page_numbers)):
        raise ValueError("SAM.gov duplicate raw page numbers found")
    raw_total = 0
    for raw_file in raw_files:
        text = raw_file.read_text(encoding="utf-8")
        if "api_key=" in text.lower():
            raise ValueError(f"SAM.gov raw file contains api_key query text: {raw_file}")
        data = json.loads(text)
        raw_total += len(data.get("opportunitiesData") or [])
    rows = read_csv_rows(processed)
    if not rows:
        raise ValueError("SAM.gov generated CSV has no rows")
    notice_ids = [row["notice_id"] for row in rows]
    if len(notice_ids) != len(set(notice_ids)):
        raise ValueError("SAM.gov duplicate notice_id values found")
    for row in rows:
        if not row["notice_id"]:
            raise ValueError("SAM.gov row missing notice_id")
        if not row["type"]:
            raise ValueError(f"SAM.gov row missing type: {row['notice_id']}")
        require_iso_date(row["posted_date"], f"SAM.gov posted_date {row['notice_id']}")
        require_iso_date(row["response_deadline"], f"SAM.gov response_deadline {row['notice_id']}")
        require_iso_date(row["archive_date"], f"SAM.gov archive_date {row['notice_id']}")
        require_iso_date(row["last_modified_date"], f"SAM.gov last_modified_date {row['notice_id']}")
    if raw_total != len(rows):
        raise ValueError(f"SAM.gov raw/processed mismatch: raw={raw_total} processed={len(rows)}")
    print(
        "OK generated_candidate sam_gov: "
        f"{len(rows)} rows, {len(raw_files)} raw pages, run_id={run_id}"
    )
    return len(rows)


def validate_generated_sec_edgar_additional(data_dir=Path("data")):
    processed = latest_file(
        data_dir / "processed/sec_edgar_additional/sec_edgar_additional_*.csv"
    )
    run_id = parse_run_id(processed, "sec_edgar_additional")
    raw_files = sorted(
        (data_dir / "raw/sec_edgar_additional").glob(
            f"sec_edgar_additional_{run_id}_*_page_*.xml"
        )
    )
    rows = read_csv_rows(processed)
    raw_total = 0
    for raw_file in raw_files:
        root = ET.parse(raw_file).getroot()
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        raw_total += len(root.findall("atom:entry", namespace))
    keys = {(row["form_type"], row["accession"]) for row in rows}
    if len(keys) != len(rows):
        raise ValueError("SEC EDGAR generated CSV has duplicate form/accession rows")
    duplicates_removed = raw_total - len(rows)
    if duplicates_removed < 0:
        raise ValueError("SEC EDGAR processed rows exceed raw rows")
    print(
        "OK generated_candidate sec_edgar_additional: "
        f"{len(rows)} rows, {raw_total} raw entries, "
        f"{duplicates_removed} duplicates removed"
    )
    return len(rows)


def validate_generated_fec(data_dir=Path("data")):
    processed = latest_file(data_dir / "processed/fec/fec_*.csv")
    run_id = parse_run_id(processed, "fec")
    raw_files = sorted((data_dir / "raw/fec").glob(f"fec_{run_id}_page_*.json"))
    rows = read_csv_rows(processed)
    raw_total = 0
    raw_without_dates = 0
    for raw_file in raw_files:
        data = json.loads(raw_file.read_text(encoding="utf-8"))
        page_rows = data.get("results") or []
        raw_total += len(page_rows)
        raw_without_dates += sum(
            1 for row in page_rows if not row.get("contribution_receipt_date")
        )
    processed_without_dates = sum(
        1 for row in rows if not row.get("contribution_receipt_date")
    )
    if processed_without_dates:
        raise ValueError(f"FEC generated CSV has {processed_without_dates} rows without dates")
    for row in rows:
        require_iso_date(row["contribution_receipt_date"], f"FEC contribution date {row['transaction_id']}")
    print(
        "OK generated_candidate fec: "
        f"{len(rows)} rows, {raw_total} raw rows, "
        f"{raw_without_dates} raw rows without dates"
    )
    return len(rows)


def validate_generated_candidate_sources(data_dir=Path("data")):
    summary = [
        ("generated_sam_gov", validate_generated_sam_gov(data_dir)),
        (
            "generated_sec_edgar_additional",
            validate_generated_sec_edgar_additional(data_dir),
        ),
        ("generated_fec", validate_generated_fec(data_dir)),
    ]
    return summary


def main():
    try:
        validate_candidate_sources()
    except Exception as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
