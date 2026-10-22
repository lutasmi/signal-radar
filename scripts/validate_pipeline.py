import argparse
import csv
import glob
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors import collect_capitol_trades, collect_sec_form4, collect_usaspending
from collectors import collect_congress_ptr
from loaders import (
    load_congress_ptr_csv_to_sheets,
    load_congress_gov_csv_to_sheets,
    load_capitol_trades_csv_to_sheets,
    load_fec_csv_to_sheets,
    load_federal_register_csv_to_sheets,
    load_grants_gov_csv_to_sheets,
    load_lda_gov_csv_to_sheets,
    load_sam_gov_csv_to_sheets,
    load_sec_form4_csv_to_sheets,
    load_sec_edgar_additional_csv_to_sheets,
    load_usaspending_csv_to_sheets,
)
from collectors import (
    collect_congress_gov,
    collect_fec,
    collect_federal_register,
    collect_grants_gov,
    collect_lda_gov,
    collect_sam_gov,
    collect_sec_edgar_additional,
)
from scripts import build_signals_sheet


CSV_SPECS = [
    (
        "congress_ptr",
        "congress_ptr_latest.csv",
        collect_congress_ptr.FIELDNAMES,
        load_congress_ptr_csv_to_sheets,
    ),
    (
        "capitol_trades",
        "capitol_trades_latest.csv",
        collect_capitol_trades.FIELDNAMES,
        load_capitol_trades_csv_to_sheets,
    ),
    (
        "sec_form4",
        "sec_form4_latest.csv",
        collect_sec_form4.FIELDNAMES,
        load_sec_form4_csv_to_sheets,
    ),
    (
        "usaspending",
        "usaspending_latest.csv",
        collect_usaspending.FIELDNAMES,
        load_usaspending_csv_to_sheets,
    ),
    (
        "sam_gov",
        "sam_gov_latest.csv",
        collect_sam_gov.FIELDNAMES,
        load_sam_gov_csv_to_sheets,
    ),
    (
        "congress_gov",
        "congress_gov_latest.csv",
        collect_congress_gov.FIELDNAMES,
        load_congress_gov_csv_to_sheets,
    ),
    (
        "federal_register",
        "federal_register_latest.csv",
        collect_federal_register.FIELDNAMES,
        load_federal_register_csv_to_sheets,
    ),
    (
        "lda_gov",
        "lda_gov_latest.csv",
        collect_lda_gov.FIELDNAMES,
        load_lda_gov_csv_to_sheets,
    ),
    (
        "sec_edgar_additional",
        "sec_edgar_additional_latest.csv",
        collect_sec_edgar_additional.FIELDNAMES,
        load_sec_edgar_additional_csv_to_sheets,
    ),
    (
        "fec",
        "fec_latest.csv",
        collect_fec.FIELDNAMES,
        load_fec_csv_to_sheets,
    ),
    (
        "grants_gov",
        "grants_gov_latest.csv",
        collect_grants_gov.FIELDNAMES,
        load_grants_gov_csv_to_sheets,
    ),
]

DEFAULT_CSV_DIR = Path("data")

WORKSHEET_BY_SOURCE = {
    "congress_ptr": build_signals_sheet.RAW_CONGRESS_PTR,
    "capitol_trades": build_signals_sheet.RAW_CAPITOL_TRADES,
    "sec_form4": build_signals_sheet.RAW_SEC_FORM4,
    "usaspending": build_signals_sheet.RAW_USASPENDING,
    "sam_gov": build_signals_sheet.RAW_SAM_GOV,
    "congress_gov": build_signals_sheet.RAW_CONGRESS_GOV,
    "federal_register": build_signals_sheet.RAW_FEDERAL_REGISTER,
    "lda_gov": build_signals_sheet.RAW_LDA_GOV,
    "sec_edgar_additional": build_signals_sheet.RAW_SEC_EDGAR_ADDITIONAL,
    "fec": build_signals_sheet.RAW_FEC,
    "grants_gov": build_signals_sheet.RAW_GRANTS_GOV,
}


class FakeWorksheet:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return self.values


class FakeSheet:
    def __init__(self, worksheets):
        self.worksheets = worksheets

    def worksheet(self, name):
        return FakeWorksheet(self.worksheets[name])


def read_csv_values(csv_file):
    with csv_file.open("r", newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def validate_csv_shape(name, values, expected_header):
    if not values:
        raise ValueError(f"{name}: CSV is empty")

    header = values[0]
    if header != expected_header:
        raise ValueError(
            f"{name}: unexpected header\n"
            f"expected: {expected_header}\n"
            f"actual:   {header}"
        )

    width = len(header)
    invalid_rows = [
        index
        for index, row in enumerate(values[1:], start=2)
        if len(row) > width
    ]
    if invalid_rows:
        raise ValueError(f"{name}: rows wider than header: {invalid_rows[:10]}")


def validate_loader_idempotency(name, values, loader_module):
    header = values[0]
    data_rows = values[1:]

    first_pass = loader_module.filter_new_rows(header, data_rows, existing_keys=set())
    first_keys = {
        loader_module.build_key(header, row)
        for row in first_pass
    }
    second_pass = loader_module.filter_new_rows(header, data_rows, first_keys)

    if second_pass:
        raise ValueError(f"{name}: loader is not idempotent")

    if len(first_pass) != len(first_keys):
        raise ValueError(f"{name}: duplicate keys found inside CSV")

    if not data_rows:
        return

    first_row = data_rows[0]
    header_to_value = {
        column: first_row[index] if index < len(first_row) else ""
        for index, column in enumerate(header)
    }

    reordered_header = list(reversed(header))
    reordered_row = [header_to_value[column] for column in reordered_header]
    reordered_existing_keys = loader_module.build_existing_keys(
        [reordered_header, reordered_row],
        header,
    )
    if loader_module.filter_new_rows(header, [first_row], reordered_existing_keys):
        raise ValueError(f"{name}: loader does not respect existing sheet header order")

    headerless_existing_keys = loader_module.build_existing_keys([first_row], header)
    if loader_module.filter_new_rows(header, [first_row], headerless_existing_keys):
        raise ValueError(f"{name}: loader ignores first existing row without header")


def validate_signals_are_deterministic(worksheets):
    sheet = FakeSheet(worksheets)
    build_signals_sheet.DATE_WARNINGS.clear()
    first = build_signals_sheet.build_signals(sheet)
    build_signals_sheet.DATE_WARNINGS.clear()
    second = build_signals_sheet.build_signals(sheet)

    if first != second:
        raise ValueError("signals: generation is not deterministic")

    signal_ids = [signal["signal_id"] for signal in first]
    if len(signal_ids) != len(set(signal_ids)):
        raise ValueError("signals: duplicate signal_id values found")

    return first


def resolve_csv_dir(csv_dir):
    csv_dir = Path(csv_dir)
    if not csv_dir.is_absolute():
        csv_dir = PROJECT_ROOT / csv_dir
    return csv_dir


def source_csv_file(csv_dir, name, csv_filename):
    csv_file = csv_dir / csv_filename
    if csv_file.exists():
        return csv_file

    processed_pattern = csv_dir / "processed" / name / f"{name}_*.csv"
    matches = sorted(glob.glob(str(processed_pattern)))
    if matches:
        return Path(matches[-1])

    return csv_file


def validate_pipeline(require_csv, csv_dir=DEFAULT_CSV_DIR):
    worksheets = {}
    loaded_sources = []
    csv_dir = resolve_csv_dir(csv_dir)

    for name, csv_filename, expected_header, loader_module in CSV_SPECS:
        csv_file = source_csv_file(csv_dir, name, csv_filename)
        if not csv_file.exists():
            if require_csv:
                raise FileNotFoundError(f"{name}: CSV not found: {csv_file}")
            print(f"SKIP {name}: CSV not found: {csv_file}")
            continue

        values = read_csv_values(csv_file)
        validate_csv_shape(name, values, expected_header)
        validate_loader_idempotency(name, values, loader_module)
        worksheets[WORKSHEET_BY_SOURCE[name]] = values
        loaded_sources.append(name)
        print(f"OK {name}: {len(values) - 1} CSV rows")

    missing_for_signals = [
        worksheet_name
        for worksheet_name in build_signals_sheet.RAW_HEADERS
        if worksheet_name not in worksheets
    ]
    if missing_for_signals:
        print(
            "WARN signals: missing CSV data for "
            + ", ".join(missing_for_signals)
        )

    signals = validate_signals_are_deterministic(worksheets)
    print(f"OK signals: {len(signals)} deterministic rows")
    return loaded_sources, signals


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate local CSV artifacts, loader idempotency, and deterministic signals generation without touching Google Sheets."
    )
    parser.add_argument(
        "--require-csv",
        action="store_true",
        help="Fail if any expected local CSV artifact is missing.",
    )
    parser.add_argument(
        "--csv-dir",
        default=str(DEFAULT_CSV_DIR),
        help="Directory containing capitol_trades_latest.csv, sec_form4_latest.csv, and usaspending_latest.csv.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    loaded_sources, signals = validate_pipeline(
        require_csv=args.require_csv,
        csv_dir=args.csv_dir,
    )
    print(f"Sources validated: {len(loaded_sources)}")
    print(f"Signals validated: {len(signals)}")


if __name__ == "__main__":
    main()
