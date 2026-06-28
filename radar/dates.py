from datetime import date, datetime


MONTHS = {
    "jan": 1,
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "dic": 12,
}


def normalize_date(value, warnings=None):
    original_value = value
    value = value.strip()
    if not value:
        return value

    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass

    parts = value.replace(",", " ").split()
    if len(parts) == 3:
        day_text, month_text, year_text = parts
        month = MONTHS.get(month_text[:3].lower())
        if month:
            try:
                return date(int(year_text), month, int(day_text)).isoformat()
            except ValueError:
                pass

    if warnings is not None:
        warnings.append(original_value)
    return original_value


def parse_date(value):
    value = value.strip()
    if not value:
        return None

    normalized_value = normalize_date(value)
    try:
        return datetime.strptime(normalized_value, "%Y-%m-%d").date()
    except ValueError:
        return None
