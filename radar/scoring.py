import json
from pathlib import Path

from radar.dates import parse_date
from radar.records import get_value, split_semicolon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCORING_CONFIG = PROJECT_ROOT / "config" / "scoring.json"
SCORE_FIELDS = ["score", "score_band", "score_reason"]


def load_scoring_config(path=DEFAULT_SCORING_CONFIG):
    with Path(path).open("r", encoding="utf-8") as f:
        config = json.load(f)
    validate_scoring_config(config)
    return config


def validate_weight_map(config, name):
    value = config.get(name)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Scoring config section must be a non-empty object: {name}")
    for key, weight in value.items():
        if not key or not isinstance(weight, (int, float)):
            raise ValueError(f"Invalid scoring weight in {name}: {key}={weight}")


def validate_scoring_config(config):
    for section in ["priority_level", "opportunity_type", "signal_type", "status"]:
        validate_weight_map(config, section)

    if not isinstance(config.get("source_diversity_per_extra_source"), (int, float)):
        raise ValueError("Scoring config requires source_diversity_per_extra_source")
    if not isinstance(config.get("review_today"), (int, float)):
        raise ValueError("Scoring config requires review_today")

    recency = config.get("recency")
    if not isinstance(recency, list) or not recency:
        raise ValueError("Scoring config recency must be a non-empty list")
    for bucket in recency:
        if not isinstance(bucket.get("max_days"), int):
            raise ValueError(f"Invalid recency max_days: {bucket}")
        if not isinstance(bucket.get("weight"), (int, float)):
            raise ValueError(f"Invalid recency weight: {bucket}")

    bands = config.get("bands")
    if not isinstance(bands, list) or not bands:
        raise ValueError("Scoring config bands must be a non-empty list")
    for band in bands:
        if not band.get("name") or not isinstance(band.get("min_score"), (int, float)):
            raise ValueError(f"Invalid scoring band: {band}")


def build_signal_lookup(signals):
    return {
        get_value(signal, "signal_id"): signal
        for signal in signals or []
        if get_value(signal, "signal_id")
    }


def score_band(score, config):
    bands = sorted(config["bands"], key=lambda band: band["min_score"], reverse=True)
    for band in bands:
        if score >= band["min_score"]:
            return band["name"]
    return bands[-1]["name"]


def recency_weight(row, run_date, config):
    last_date = parse_date(get_value(row, "last_date"))
    current_date = parse_date(run_date)
    if last_date is None or current_date is None:
        return 0, ""

    age_days = max((current_date - last_date).days, 0)
    for bucket in sorted(config["recency"], key=lambda item: item["max_days"]):
        if age_days <= bucket["max_days"]:
            return bucket["weight"], f"recency <= {bucket['max_days']}d +{bucket['weight']}"
    return 0, ""


def score_review_row(row, signal_lookup, run_date, config):
    score = 0
    reasons = []

    priority_level = get_value(row, "priority_level")
    priority_weight = config["priority_level"].get(priority_level, 0)
    if priority_weight:
        score += priority_weight
        reasons.append(f"priority {priority_level} +{priority_weight}")

    status = get_value(row, "status")
    status_weight = config["status"].get(status, 0)
    if status_weight:
        score += status_weight
        reasons.append(f"status {status} +{status_weight}")

    if get_value(row, "review_today") == "YES":
        weight = config["review_today"]
        score += weight
        reasons.append(f"review_today +{weight}")

    sources = split_semicolon(get_value(row, "sources"))
    if len(sources) > 1:
        weight = (len(sources) - 1) * config["source_diversity_per_extra_source"]
        score += weight
        reasons.append(f"source diversity {len(sources)} sources +{weight}")

    opportunity_type = get_value(row, "opportunity_type")
    opportunity_weight = config["opportunity_type"].get(opportunity_type, 0)
    if opportunity_weight:
        score += opportunity_weight
        reasons.append(f"{opportunity_type} +{opportunity_weight}")

    seen_signal_types = set()
    for signal_id in split_semicolon(get_value(row, "signal_ids")):
        signal_type = get_value(signal_lookup.get(signal_id, {}), "signal_type")
        if not signal_type or signal_type in seen_signal_types:
            continue
        weight = config["signal_type"].get(signal_type, 0)
        if weight:
            score += weight
            reasons.append(f"{signal_type} +{weight}")
        seen_signal_types.add(signal_type)

    weight, reason = recency_weight(row, run_date, config)
    if weight:
        score += weight
        reasons.append(reason)

    if not reasons:
        reasons.append("No configured score weights matched.")

    return {
        "score": str(int(score)),
        "score_band": score_band(score, config),
        "score_reason": "; ".join(reasons),
    }
