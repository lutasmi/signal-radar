import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


DEFAULT_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 30
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
AUTH_STATUS_CODES = {401, 403}
SENSITIVE_QUERY_KEYS = ("api_key", "apikey", "key", "token", "access_token")


class SourceRequestError(RuntimeError):
    pass


class SourceAuthenticationError(SourceRequestError):
    pass


class SourceRateLimitError(SourceRequestError):
    pass


def sanitize_message(value):
    text = str(value)
    for key in SENSITIVE_QUERY_KEYS:
        text = re.sub(
            rf"([?&]{re.escape(key)}=)[^&\s)]+",
            rf"\1[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )
    return text


def utc_run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_output_dirs(raw_dir, processed_dir):
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)


def request_with_retries(
    session,
    method,
    url,
    *,
    params=None,
    json_body=None,
    headers=None,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    attempts=DEFAULT_ATTEMPTS,
    retry_delay=DEFAULT_RETRY_DELAY_SECONDS,
):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = exc
        else:
            if response.status_code in AUTH_STATUS_CODES:
                raise SourceAuthenticationError(
                    f"authentication failed with HTTP {response.status_code}: "
                    f"{sanitize_message(response.text[:500])}"
                )
            if response.status_code == 429:
                last_error = SourceRateLimitError(
                    f"rate limited with HTTP 429: {sanitize_message(response.text[:500])}"
                )
            elif response.status_code in RETRYABLE_STATUS_CODES:
                last_error = SourceRequestError(
                    f"retryable HTTP {response.status_code}: "
                    f"{sanitize_message(response.text[:500])}"
                )
            elif response.ok:
                return response
            else:
                raise SourceRequestError(
                    f"HTTP {response.status_code}: {sanitize_message(response.text[:1000])}"
                )

        if attempt < attempts:
            print(
                f"Request failed on attempt {attempt}/{attempts}: "
                f"{sanitize_message(last_error)}. Retrying in {retry_delay}s."
            )
            time.sleep(retry_delay)

    if isinstance(last_error, SourceRequestError):
        raise type(last_error)(sanitize_message(last_error))
    raise SourceRequestError(
        f"request failed after {attempts} attempts: {sanitize_message(last_error)}"
    )


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(text)


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def clean_text(value):
    if value is None:
        return ""
    return " ".join(str(value).split())


def nested_get(data, path, default=""):
    current = data
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int):
            if key >= len(current):
                return default
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def join_values(values):
    return "; ".join(clean_text(value) for value in values if clean_text(value))


def require_unique(values, label):
    seen = set()
    duplicates = []
    for value in values:
        if not value:
            continue
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"{label}: duplicate identifiers found: {duplicates[:5]}")


def parse_iso_date(value):
    value = clean_text(value)
    if not value:
        return ""
    return value[:10]


def parse_us_date(value):
    value = clean_text(value)
    if not value:
        return ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:10], fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unsupported date: {value}")
