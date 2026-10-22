from collections import Counter
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account"])

import gspread

from radar.dates import normalize_date
from radar.records import get_value, stable_id
from radar.sheets import open_sheet, replace_worksheet


OUTPUT_WORKSHEET_NAME = "signals"

RAW_CAPITOL_TRADES = "raw_capitol_trades"
RAW_CONGRESS_PTR = "raw_congress_ptr"
RAW_SEC_FORM4 = "raw_sec_form4"
RAW_USASPENDING = "raw_usaspending"
RAW_SAM_GOV = "raw_sam_gov"
RAW_CONGRESS_GOV = "raw_congress_gov"
RAW_FEDERAL_REGISTER = "raw_federal_register"
RAW_LDA_GOV = "raw_lda_gov"
RAW_SEC_EDGAR_ADDITIONAL = "raw_sec_edgar_additional"
RAW_FEC = "raw_fec"
RAW_GRANTS_GOV = "raw_grants_gov"

RAW_HEADERS = {
    RAW_CONGRESS_PTR: [
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
    ],
    RAW_CAPITOL_TRADES: [
        "politician",
        "asset_name",
        "ticker",
        "published",
        "transaction_date",
        "filing_delay",
        "owner",
        "trade_type",
        "amount",
        "price",
        "source",
        "source_url",
    ],
    RAW_SEC_FORM4: [
        "ticker",
        "issuer_name",
        "insider_name",
        "insider_title",
        "transaction_date",
        "transaction_code",
        "acquired_disposed",
        "shares",
        "price",
        "estimated_value",
        "filing_date",
        "source_url",
    ],
    RAW_USASPENDING: [
        "award_id",
        "recipient_name",
        "awarding_agency_name",
        "award_amount",
        "period_of_performance_start_date",
        "description",
        "source_url",
    ],
    RAW_SAM_GOV: [
        "notice_id",
        "title",
        "solicitation_number",
        "posted_date",
        "response_deadline",
        "archive_date",
        "last_modified_date",
        "type",
        "base_type",
        "signal_type",
        "full_parent_path_name",
        "organization_type",
        "naics_code",
        "classification_code",
        "active",
        "award_number",
        "award_amount",
        "award_date",
        "awardee_name",
        "awardee_uei",
        "ui_link",
        "source_url",
    ],
    RAW_CONGRESS_GOV: [
        "bill_id",
        "congress",
        "bill_type",
        "bill_number",
        "title",
        "origin_chamber",
        "introduced_date",
        "latest_action_date",
        "latest_action_text",
        "sponsor_name",
        "sponsor_bioguide_id",
        "policy_area",
        "signal_types",
        "source_url",
    ],
    RAW_FEDERAL_REGISTER: [
        "document_number",
        "publication_date",
        "type",
        "signal_type",
        "title",
        "agencies",
        "agency_slugs",
        "cfr_references",
        "html_url",
        "pdf_url",
        "abstract",
    ],
    RAW_LDA_GOV: [
        "filing_uuid",
        "filing_type",
        "filing_type_display",
        "filing_year",
        "filing_period",
        "posted_date",
        "registrant_id",
        "registrant_name",
        "client_id",
        "client_name",
        "income",
        "expenses",
        "issue_codes",
        "issue_descriptions",
        "government_entities",
        "signal_types",
        "source_url",
    ],
    RAW_SEC_EDGAR_ADDITIONAL: [
        "accession",
        "form_type",
        "signal_type",
        "company_name",
        "cik",
        "filing_date",
        "updated_at",
        "title",
        "source_url",
    ],
    RAW_FEC: [
        "transaction_id",
        "committee_id",
        "committee_name",
        "contributor_name",
        "contributor_employer",
        "contributor_occupation",
        "contributor_city",
        "contributor_state",
        "contribution_receipt_date",
        "contribution_receipt_amount",
        "two_year_transaction_period",
        "memo_text",
        "signal_types",
        "source_url",
    ],
    RAW_GRANTS_GOV: [
        "opportunity_id",
        "opportunity_number",
        "title",
        "agency_code",
        "agency",
        "open_date",
        "close_date",
        "opportunity_status",
        "document_type",
        "cfda_list",
        "signal_type",
        "source_url",
    ],
}

SIGNALS_HEADER = [
    "signal_id",
    "signal_date",
    "signal_type",
    "ticker",
    "entity_name",
    "actor_name",
    "actor_type",
    "amount",
    "source",
    "source_url",
    "raw_key",
]

DATE_WARNINGS = []


def make_signal_id(source, raw_key):
    return stable_id(source, [source, raw_key])


def normalize_signal_date(value):
    normalized_value = normalize_date(value, DATE_WARNINGS)
    if normalized_value == value.strip() and value.strip() in DATE_WARNINGS:
        print(f"WARNING: could not parse signal_date: {value}")
    return normalized_value


def build_signal(
    signal_date,
    signal_type,
    ticker,
    entity_name,
    actor_name,
    actor_type,
    amount,
    source,
    source_url,
    raw_key,
):
    return {
        "signal_id": make_signal_id(source, raw_key),
        "signal_date": normalize_signal_date(signal_date),
        "signal_type": signal_type,
        "ticker": ticker,
        "entity_name": entity_name,
        "actor_name": actor_name,
        "actor_type": actor_type,
        "amount": amount,
        "source": source,
        "source_url": source_url,
        "raw_key": raw_key,
    }


def normalize_capitol_trade(row):
    trade_type = get_value(row, "trade_type").lower()
    if trade_type == "buy":
        signal_type = "PTR_BUY"
    elif trade_type == "sell":
        signal_type = "PTR_SELL"
    else:
        signal_type = "PTR_OTHER"

    raw_key = "".join(
        [
            get_value(row, "politician"),
            get_value(row, "transaction_date"),
            get_value(row, "asset_name"),
            get_value(row, "trade_type"),
            get_value(row, "amount"),
        ]
    )

    return build_signal(
        signal_date=get_value(row, "transaction_date"),
        signal_type=signal_type,
        ticker=get_value(row, "ticker"),
        entity_name=get_value(row, "asset_name"),
        actor_name=get_value(row, "politician"),
        actor_type="politician",
        amount=get_value(row, "amount"),
        source="capitol_trades",
        source_url=get_value(row, "source_url"),
        raw_key=raw_key,
    )


def normalize_congress_ptr(row):
    """Official House/Senate PTR rows. Committee-conflict buys get their own type."""
    trade_type = get_value(row, "trade_type").lower()
    if trade_type == "buy" and get_value(row, "conflict") == "YES":
        signal_type = "PTR_BUY_CONFLICT"
    elif trade_type == "buy":
        signal_type = "PTR_BUY"
    elif trade_type in ("sell", "sell_partial"):
        signal_type = "PTR_SELL"
    elif trade_type == "paper_filing":
        signal_type = "PTR_PAPER_FILING"
    else:
        signal_type = "PTR_OTHER"

    raw_key = "|".join(
        [
            get_value(row, "filing_id"),
            get_value(row, "owner"),
            get_value(row, "asset_name"),
            get_value(row, "trade_type"),
            get_value(row, "transaction_date"),
            get_value(row, "amount"),
        ]
    )

    return build_signal(
        signal_date=get_value(row, "transaction_date") or get_value(row, "filing_date"),
        signal_type=signal_type,
        ticker=get_value(row, "ticker"),
        entity_name=get_value(row, "asset_name"),
        actor_name=get_value(row, "politician"),
        actor_type="politician",
        amount=get_value(row, "amount"),
        source=get_value(row, "source") or "congress_ptr",
        source_url=get_value(row, "source_url"),
        raw_key=raw_key,
    )


def normalize_sec_form4(row):
    raw_key = "|".join(
        [
            get_value(row, "source_url"),
            get_value(row, "ticker"),
            get_value(row, "insider_name"),
            get_value(row, "transaction_date"),
            get_value(row, "transaction_code"),
            get_value(row, "acquired_disposed"),
            get_value(row, "shares"),
            get_value(row, "price"),
        ]
    )
    return build_signal(
        signal_date=get_value(row, "transaction_date"),
        signal_type="INSIDER_BUY",
        ticker=get_value(row, "ticker"),
        entity_name=get_value(row, "issuer_name"),
        actor_name=get_value(row, "insider_name"),
        actor_type="insider",
        amount=get_value(row, "estimated_value"),
        source="sec_form4",
        source_url=get_value(row, "source_url"),
        raw_key=raw_key,
    )


def normalize_usaspending(row):
    raw_key = get_value(row, "award_id")
    return build_signal(
        signal_date=get_value(row, "period_of_performance_start_date"),
        signal_type="CONTRACT",
        ticker="",
        entity_name=get_value(row, "recipient_name"),
        actor_name=get_value(row, "awarding_agency_name"),
        actor_type="agency",
        amount=get_value(row, "award_amount"),
        source="usaspending",
        source_url=get_value(row, "source_url"),
        raw_key=raw_key,
    )


def normalize_sam_gov(row):
    signal_date = get_value(row, "award_date") or get_value(row, "posted_date")
    return build_signal(
        signal_date=signal_date,
        signal_type=get_value(row, "signal_type") or "CONTRACT_OPPORTUNITY",
        ticker="",
        entity_name=get_value(row, "awardee_name") or get_value(row, "title"),
        actor_name=get_value(row, "full_parent_path_name"),
        actor_type="agency",
        amount=get_value(row, "award_amount"),
        source="sam_gov",
        source_url=get_value(row, "ui_link") or get_value(row, "source_url"),
        raw_key=get_value(row, "notice_id"),
    )


def primary_signal_type(value, preferred_order):
    signal_types = [item.strip() for item in value.split(";") if item.strip()]
    for candidate in preferred_order:
        if candidate in signal_types:
            return candidate
    return signal_types[0] if signal_types else ""


def normalize_congress_gov(row):
    signal_type = primary_signal_type(
        get_value(row, "signal_types"),
        ["COMMITTEE_ACTION", "LEGISLATIVE_ACTION", "BILL_INTRODUCED"],
    )
    return build_signal(
        signal_date=get_value(row, "latest_action_date") or get_value(row, "introduced_date"),
        signal_type=signal_type or "LEGISLATIVE_ACTION",
        ticker="",
        entity_name=get_value(row, "title"),
        actor_name=get_value(row, "sponsor_name") or get_value(row, "origin_chamber"),
        actor_type="legislator",
        amount="",
        source="congress_gov",
        source_url=get_value(row, "source_url"),
        raw_key=get_value(row, "bill_id"),
    )


def normalize_federal_register(row):
    return build_signal(
        signal_date=get_value(row, "publication_date"),
        signal_type=get_value(row, "signal_type") or "REGULATORY_CATALYST",
        ticker="",
        entity_name=get_value(row, "title") or get_value(row, "agencies"),
        actor_name=get_value(row, "agencies"),
        actor_type="agency",
        amount="",
        source="federal_register",
        source_url=get_value(row, "html_url") or get_value(row, "pdf_url"),
        raw_key=get_value(row, "document_number"),
    )


def normalize_lda_gov(row):
    signal_type = primary_signal_type(
        get_value(row, "signal_types"),
        [
            "LOBBY_NEW_REGISTRATION",
            "LOBBY_SPEND_INCREASE",
            "LOBBY_ISSUE_MATCH",
            "LOBBY_CLIENT_ACTIVITY",
        ],
    )
    amount = get_value(row, "income") or get_value(row, "expenses")
    return build_signal(
        signal_date=get_value(row, "posted_date"),
        signal_type=signal_type or "LOBBY_CLIENT_ACTIVITY",
        ticker="",
        entity_name=get_value(row, "client_name"),
        actor_name=get_value(row, "registrant_name"),
        actor_type="lobby_registrant",
        amount=amount,
        source="lda_gov",
        source_url=get_value(row, "source_url"),
        raw_key=get_value(row, "filing_uuid"),
    )


def normalize_sec_edgar_additional(row):
    return build_signal(
        signal_date=get_value(row, "filing_date") or get_value(row, "updated_at"),
        signal_type=get_value(row, "signal_type"),
        ticker="",
        entity_name=get_value(row, "company_name"),
        actor_name=get_value(row, "company_name"),
        actor_type="sec_filer",
        amount="",
        source="sec_edgar_additional",
        source_url=get_value(row, "source_url"),
        raw_key=f"{get_value(row, 'form_type')}|{get_value(row, 'accession')}",
    )


def normalize_fec(row):
    return build_signal(
        signal_date=get_value(row, "contribution_receipt_date"),
        signal_type=primary_signal_type(
            get_value(row, "signal_types"),
            ["EXECUTIVE_CONTRIBUTION", "PAC_CONTRIBUTION", "POLITICAL_CONTRIBUTION"],
        ) or "POLITICAL_CONTRIBUTION",
        ticker="",
        entity_name=get_value(row, "committee_name"),
        actor_name=get_value(row, "contributor_name"),
        actor_type="political_contributor",
        amount=get_value(row, "contribution_receipt_amount"),
        source="fec",
        source_url=get_value(row, "source_url"),
        raw_key=get_value(row, "transaction_id"),
    )


def normalize_grants_gov(row):
    return build_signal(
        signal_date=get_value(row, "open_date"),
        signal_type=get_value(row, "signal_type") or "GRANT_OPPORTUNITY",
        ticker="",
        entity_name=get_value(row, "title") or get_value(row, "agency"),
        actor_name=get_value(row, "agency"),
        actor_type="agency",
        amount="",
        source="grants_gov",
        source_url=get_value(row, "source_url"),
        raw_key=get_value(row, "opportunity_id"),
    )


def read_records(sheet, worksheet_name):
    try:
        worksheet = sheet.worksheet(worksheet_name)
    except (gspread.exceptions.WorksheetNotFound, KeyError):
        print(f"WARNING: missing raw worksheet skipped: {worksheet_name}")
        return []

    values = worksheet.get_all_values()
    if not values:
        return []

    expected_header = RAW_HEADERS[worksheet_name]
    if set(expected_header).issubset(set(values[0])):
        header = values[0]
        data_rows = values[1:]
    else:
        header = expected_header
        data_rows = values

    records = []
    for data_row in data_rows:
        record = {}
        for index, column in enumerate(header):
            if not column or column in record:
                continue

            record[column] = data_row[index] if index < len(data_row) else ""

        records.append(record)

    return records


def build_signals(sheet):
    source_specs = [
        (RAW_CONGRESS_PTR, normalize_congress_ptr),
        (RAW_CAPITOL_TRADES, normalize_capitol_trade),
        (RAW_SEC_FORM4, normalize_sec_form4),
        (RAW_USASPENDING, normalize_usaspending),
        (RAW_SAM_GOV, normalize_sam_gov),
        (RAW_CONGRESS_GOV, normalize_congress_gov),
        (RAW_FEDERAL_REGISTER, normalize_federal_register),
        (RAW_LDA_GOV, normalize_lda_gov),
        (RAW_SEC_EDGAR_ADDITIONAL, normalize_sec_edgar_additional),
        (RAW_FEC, normalize_fec),
        (RAW_GRANTS_GOV, normalize_grants_gov),
    ]

    signals = []
    seen_raw_keys = set()

    for worksheet_name, normalize in source_specs:
        for row in read_records(sheet, worksheet_name):
            signal = normalize(row)
            source_raw_key = (signal["source"], signal["raw_key"])
            if source_raw_key in seen_raw_keys:
                continue

            seen_raw_keys.add(source_raw_key)
            signals.append(signal)

    return signals


def write_signals(sheet, signals):
    replace_worksheet(sheet, OUTPUT_WORKSHEET_NAME, SIGNALS_HEADER, signals)


def print_summary(signals):
    counts = Counter(signal["signal_type"] for signal in signals)

    print(f"Worksheet rebuilt: {OUTPUT_WORKSHEET_NAME}")
    print(f"Signals generated: {len(signals)}")
    print(f"Date warnings: {len(DATE_WARNINGS)}")
    print("Count by signal_type:")
    for signal_type, count in sorted(counts.items()):
        print(f"- {signal_type}: {count}")

    print("Example 5 rows:")
    for signal in signals[:5]:
        print({column: signal[column] for column in SIGNALS_HEADER})


def main():
    sheet = open_sheet()
    signals = build_signals(sheet)
    write_signals(sheet, signals)
    print_summary(signals)


if __name__ == "__main__":
    main()
