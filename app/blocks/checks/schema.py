import csv
import json
import math

from app.blocks.checks._shared import metadata, result, split_name
from app.prepared import SPLITS


def run(context: dict) -> dict:
    actual = context.get("feature_columns")
    expected = context.get("expected_feature_columns")
    if (not isinstance(actual, list) or not isinstance(expected, list) or not actual or not expected
            or any(not isinstance(name, str) or not name for name in actual + expected)):
        return result("schema", "unverifiable", {"reason": "Both ordered feature schemas are required."})
    details = {"actual_count": len(actual), "expected_count": len(expected),
               "feature_columns": actual, "schema_match": actual == expected,
               "duplicate_columns": len(actual) - len(set(actual))}
    if actual != expected or len(actual) != len(set(actual)) or len(expected) != len(set(expected)):
        return result("schema", "failed", details)
    records, error = metadata(context, "schema", ("row_key", "split"))
    if error:
        return error
    supplied = context.get("feature_rows")
    if supplied is None:
        return result("schema", "unverifiable", {**details, "reason": "Feature values are required."})
    rows = supplied() if callable(supplied) else iter(supplied)
    quality = {name: {"missing": 0, "non_numeric": 0, "non_finite": 0,
                      "finite": 0, "min": None, "max": None, "input_types": {}}
               for name in actual}
    positions = dict.fromkeys(SPLITS, 0)
    seen = set()
    issues = {"length": 0, "alignment_or_order": 0}
    count = 0
    try:
        for count, row in enumerate(rows, 1):
            split = split_name(row.get("split"))
            positions[split] += 1
            reference = records[count - 1] if count <= len(records) else {}
            if (not row.get("row_key") or row["row_key"] in seen
                    or row.get("row_key") != reference.get("row_key")
                    or split != split_name(reference.get("split"))
                    or str(row.get("row_position")) != str(positions[split])):
                issues["alignment_or_order"] += 1
            seen.add(row.get("row_key"))
            values = row.get("feature_values")
            if isinstance(values, str):
                values = json.loads(values)
            if not isinstance(values, list) or len(values) != len(actual):
                issues["length"] += 1
                continue
            for name, value in zip(actual, values):
                entry = quality[name]
                kind = type(value).__name__
                entry["input_types"][kind] = entry["input_types"].get(kind, 0) + 1
                if value is None or value == "":
                    entry["missing"] += 1
                    continue
                if type(value) not in (int, float):
                    entry["non_numeric"] += 1
                    continue
                if not math.isfinite(value):
                    entry["non_finite"] += 1
                    continue
                entry["finite"] += 1
                entry["min"] = value if entry["min"] is None else min(entry["min"], value)
                entry["max"] = value if entry["max"] is None else max(entry["max"], value)
    except (ValueError, TypeError, AttributeError, csv.Error):
        return result("schema", "failed", {**details, "reason": "Malformed feature input or split."})
    issues["row_count"] = abs(count - len(records))
    for entry in quality.values():
        entry["constant"] = entry["min"] == entry["max"] if entry["finite"] else None
    defective = any(issues.values()) or any(
        entry["missing"] or entry["non_numeric"] or entry["non_finite"] for entry in quality.values())
    details.update({
        "rows": count, "split_rows": positions, "quality": quality, "row_issues": issues,
        "numeric_contract": "failed" if defective else ("passed" if count else "unverifiable"),
        "clinical_semantics": {"status": "unverifiable",
                               "reason": "No verified clinical variable dictionary or units supplied."},
        "range_interpretation": "Prepared standardized values; no raw clinical range judgment.",
        "constant_interpretation": "Measured quality observation, not automatic variable removal.",
    })
    return result("schema", "failed" if defective else "unverifiable", details, context)
