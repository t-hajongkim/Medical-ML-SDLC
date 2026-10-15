def run(context: dict) -> dict:
    records = context.get("metadata", [])
    if not records or any(not row.get("patient_key") or not row.get("split") for row in records):
        return {"block": "patient_overlap", "status": "unverifiable", "details": {"reason": "Patient keys and split metadata are required."}}
    groups = {split: set() for split in ("train", "valid", "test")}
    for row in records:
        if row["split"] not in groups:
            return {"block": "patient_overlap", "status": "failed", "details": {"reason": "Unknown split name."}}
        groups[row["split"]].add(row["patient_key"])
    if any(not group for group in groups.values()):
        return {"block": "patient_overlap", "status": "unverifiable", "details": {"reason": "All three splits are required."}}
    overlaps = {
        "train_valid": len(groups["train"] & groups["valid"]),
        "train_test": len(groups["train"] & groups["test"]),
        "valid_test": len(groups["valid"] & groups["test"]),
    }
    return {
        "block": "patient_overlap",
        "status": "passed" if not any(overlaps.values()) else "failed",
        "details": {"overlaps": overlaps, "patients": {key: len(value) for key, value in groups.items()}},
    }
