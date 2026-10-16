from app.blocks.checks._shared import boolean_value, metadata, result, split_name, target_value
from app.prepared import SPLITS


def _profiled(records, context):
    columns = [name for name in ("race", "gender", "age_group")
               if any(row.get(name) not in (None, "") for row in records)]
    if not columns:
        return result("demographics", "unverifiable", {"reason": "No registered demographic fields supplied."})
    tables = {split: {column: {} for column in columns} for split in SPLITS}
    for row in records:
        for column in columns:
            value = row.get(column) or "MISSING"
            entry = tables[split_name(row["split"])][column].setdefault(
                value, {"admissions": 0, "target_0": 0, "target_1": 0, "_patients": set()})
            entry["admissions"] += 1
            entry[f"target_{target_value(row['target'])}"] += 1
            entry["_patients"].add(row["patient_key"])
    for split in tables.values():
        for groups in split.values():
            for entry in groups.values():
                entry["patients"] = len(entry.pop("_patients"))
                entry["event_rate"] = entry["target_1"] / entry["admissions"]
    complete = len(columns) == 3 and all(
        row.get(column) not in (None, "") for row in records for column in columns)
    return result("demographics", "completed" if complete else "unverifiable", {
        "distributions": tables, "count_unit": "rows", "event_rate_denominator": "rows",
        "original_missingness": {"status": "unverifiable",
                                 "reason": "Original/imputed distinction requires profile-specific evidence."},
        "interpretation": "Observed registered fields only; no undocumented imputation assumption.",
    }, context)


def run(context: dict) -> dict:
    records, error = metadata(
        context, "demographics", ("row_key", "patient_key", "split", "target"))
    if error:
        return error
    if context.get("dataset_context", {}).get("legacy") is False:
        return _profiled(records, context)
    columns = ("race", "gender", "age_group")
    required = (*columns, "race_original", "race_was_missing")
    if any(any(name not in row for name in required) for row in records):
        return result("demographics", "unverifiable", {
            "reason": "Demographics and original race missingness evidence are required."})
    if len({row["row_key"] for row in records}) != len(records):
        return result("demographics", "failed", {"reason": "Admission keys are duplicated."})
    tables = {split: {column: {} for column in (*columns, "race_original")} for split in SPLITS}
    missing = dict.fromkeys(SPLITS, 0)
    imputed = dict.fromkeys(SPLITS, 0)
    for row in records:
        split = split_name(row["split"])
        was_missing = boolean_value(row["race_was_missing"])
        original = row["race_original"]
        if ((was_missing and (original not in (None, "") or row["race"] != "Caucasian"))
                or (not was_missing and (original in (None, "") or original != row["race"]))):
            return result("demographics", "failed", {"reason": "Original race and imputation evidence disagree."})
        missing[split] += int(was_missing)
        imputed[split] += int(was_missing and row["race"] == "Caucasian")
        for column in tables[split]:
            value = row[column]
            group = "MISSING_ORIGINAL" if column == "race_original" and was_missing else (value or "MISSING")
            entry = tables[split][column].setdefault(group, {
                "admissions": 0, "target_0": 0, "target_1": 0,
                "_patients": set(), "_positive_patients": set(), "_negative_patients": set(),
            })
            target = target_value(row["target"])
            entry["admissions"] += 1
            entry[f"target_{target}"] += 1
            entry["_patients"].add(row["patient_key"])
            entry["_positive_patients" if target else "_negative_patients"].add(row["patient_key"])
    for split in tables.values():
        for groups in split.values():
            for entry in groups.values():
                entry["patients"] = len(entry.pop("_patients"))
                entry["patients_with_target_1"] = len(entry.pop("_positive_patients"))
                entry["patients_with_target_0"] = len(entry.pop("_negative_patients"))
                entry["event_rate"] = entry["target_1"] / entry["admissions"]
                entry["target_0_rate"] = entry["target_0"] / entry["admissions"]
    status = "completed" if all(tables[split]["race"] for split in SPLITS) else "unverifiable"
    if any(row["gender"] in (None, "") or row["age_group"] in (None, "") for row in records):
        status = "unverifiable"
    return result("demographics", status, {
        "distributions": tables, "original_race_missing": missing, "caucasian_imputed": imputed,
        "race_views": {"race": "public metadata, includes Caucasian imputation",
                       "race_original": "observed original race; missing remains missing"},
        "event_rate_denominator": "admissions",
        "patient_count_note": "Patients can contribute multiple admissions and both target classes; subgroup patient counts are not additive.",
        "interpretation": "Descriptive counts only; no minimum-size threshold or significance claim.",
        "absent_groups": "Not observed, not invented zero-count populations.",
    }, context)
