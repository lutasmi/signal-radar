import csv


def read_csv(csv_file):
    if not csv_file.exists():
        raise FileNotFoundError(f"CSV not found: {csv_file}")

    with csv_file.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        raise ValueError(f"CSV is empty: {csv_file}")

    return rows[0], rows[1:]


def normalize_row(row, width):
    return row + [""] * (width - len(row))


def build_key(header, row, key_columns, normalize_value=None):
    missing_columns = [column for column in key_columns if column not in header]
    if missing_columns:
        raise ValueError(f"Missing key columns: {missing_columns}")

    normalized_row = normalize_row(row, len(header))
    key_values = []
    for column in key_columns:
        value = normalized_row[header.index(column)].strip()
        if normalize_value is not None:
            value = normalize_value(column, value)
        key_values.append(value)
    return tuple(key_values)


def build_existing_keys(values, csv_header, key_columns, normalize_value=None):
    if not values:
        return set()

    if set(key_columns).issubset(set(values[0])):
        header = values[0]
        data_rows = values[1:]
    else:
        header = csv_header
        data_rows = values

    return {
        build_key(header, row, key_columns, normalize_value=normalize_value)
        for row in data_rows
    }


def filter_new_rows(header, data_rows, existing_keys, key_columns, normalize_value=None):
    new_rows = []
    seen_keys = set(existing_keys)

    for row in data_rows:
        key = build_key(header, row, key_columns, normalize_value=normalize_value)
        if key in seen_keys:
            continue

        seen_keys.add(key)
        new_rows.append(row)

    return new_rows
