from hashlib import sha256


def get_value(row, column):
    value = row.get(column, "")
    if value is None:
        return ""
    return str(value).strip()


def rows_to_dicts(values):
    if not values:
        return []

    header = values[0]
    records = []
    for row in values[1:]:
        record = {}
        for index, column in enumerate(header):
            if not column or column in record:
                continue
            record[column] = row[index] if index < len(row) else ""
        records.append(record)
    return records


def stable_id(prefix, parts):
    raw_key = "|".join(str(part) for part in parts)
    digest = sha256(raw_key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def unique_sorted(values):
    return sorted({value for value in values if value})


def first_non_empty(values):
    for value in values:
        if value:
            return value
    return ""


def split_semicolon(value):
    return [item.strip() for item in value.split(";") if item.strip()]
