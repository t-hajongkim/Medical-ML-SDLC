from app.blocks.checks._shared import metadata, result, split_name
from app.prepared import SPLITS


def run(context: dict) -> dict:
    records, error = metadata(context, "patient_overlap", ("patient_key", "split"))
    if error:
        return error
    groups = {split: set() for split in SPLITS}
    admissions = dict.fromkeys(SPLITS, 0)
    for row in records:
        split = split_name(row["split"])
        groups[split].add(row["patient_key"])
        admissions[split] += 1
    overlaps = {
        left + "_" + right: len(groups[left] & groups[right])
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
    }
    details = {"overlaps": overlaps, "patients": {key: len(value) for key, value in groups.items()},
               "admissions": admissions, "unit": "unique patients, not duplicate admission rows"}
    if any(row["split"] == "valid" for row in records):
        # Preserve the old consumer lookup only for explicitly aliased input.
        overlaps["train_valid"] = overlaps["train_validation"]
        overlaps["valid_test"] = overlaps["validation_test"]
        details["deprecated_overlap_aliases"] = {
            "train_valid": "train_validation", "valid_test": "validation_test",
        }
    status = "failed" if any(overlaps.values()) else (
        "passed" if all(groups.values()) else "unverifiable")
    if not all(groups.values()):
        details["reason"] = "All three splits are required for a complete overlap assessment."
    return result("patient_overlap", status, details, context)
