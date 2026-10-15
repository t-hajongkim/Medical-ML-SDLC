"""Small input-contract helpers; never include record values in diagnostics."""

from app.prepared import SPLITS


def split_name(value):
    if value == "valid":
        return "validation"
    if value not in SPLITS:
        raise ValueError("Unknown split name.")
    return value


def target_value(value):
    if type(value) is int and value in (0, 1):
        return value
    if type(value) is str and value in ("0", "1"):
        return int(value)
    raise ValueError("Target must be an explicit 0 or 1.")


def boolean_value(value):
    if type(value) is bool:
        return value
    if type(value) is str and value in ("true", "false"):
        return value == "true"
    raise ValueError("Missingness must be an explicit boolean or true/false string.")


def result(block, status, details, context=None):
    if context and any(row.get("split") == "valid" for row in context.get("metadata", [])):
        details = {**details, "split_aliases": {"valid": "validation"}}
    return {"block": block, "status": status, "details": details}


def metadata(context, block, required):
    records = context.get("metadata")
    if not isinstance(records, list) or not records:
        return [], result(block, "unverifiable", {"reason": "Nonempty metadata is required."})
    if any(not isinstance(row, dict) for row in records):
        return [], result(block, "failed", {"reason": "Metadata rows must be mappings."})
    try:
        for row in records:
            for name in ("row_key", "patient_key"):
                if name in row and row[name] not in (None, ""):
                    if not isinstance(row[name], str) or not row[name].strip():
                        raise ValueError("Admission and patient keys must be nonempty strings.")
            for name in ("race", "gender", "age_group", "race_original"):
                if name in row and row[name] is not None and not isinstance(row[name], str):
                    raise ValueError("Demographic values must be strings or missing.")
            if row.get("split") not in (None, ""):
                split_name(row["split"])
            if "target" in row:
                target_value(row["target"])
            if "race_was_missing" in row:
                boolean_value(row["race_was_missing"])
    except ValueError as error:
        return [], result(block, "failed", {"reason": str(error)})
    if any(any(name not in row or row[name] in (None, "") for name in required) for row in records):
        return [], result(block, "unverifiable", {"reason": "Required metadata fields are missing.",
                                                "required": list(required)})
    return records, None


def split_counts(records):
    counts = {}
    for split in SPLITS:
        rows = [row for row in records if split_name(row["split"]) == split]
        events = sum(target_value(row["target"]) for row in rows)
        counts[split] = {
            "admissions": len(rows), "patients": len({row["patient_key"] for row in rows}),
            "target_0": len(rows) - events, "target_1": events,
            "event_rate": events / len(rows) if rows else None,
            "event_rate_denominator": "admissions",
        }
    return counts
