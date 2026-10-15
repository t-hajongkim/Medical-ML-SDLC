from app.blocks.checks._shared import result


def run(context: dict) -> dict:
    features = context.get("feature_columns") or []
    declaration = context.get("prediction_time") or {}
    evidence = context.get("feature_availability") or {}
    risks = [name for name in features if name in (
        "time_in_hospital", "discharge_disposition_id", "num_lab_procedures",
        "num_procedures", "num_medications", "number_diagnoses",
        "los_medication_interaction", "medication_complexity_score",
    )]
    declared = declaration.get("approved") is True and declaration.get("point") in ("admission", "discharge")
    known = {}
    unknown = []
    conflicts = []
    for name in features:
        entry = evidence.get(name, {})
        if not entry.get("source") or entry.get("available_at") not in ("admission", "discharge"):
            unknown.append(name)
            continue
        known[name] = entry
        if declared and declaration["point"] == "admission" and entry["available_at"] == "discharge":
            conflicts.append(name)
    status = "failed" if conflicts else (
        "passed" if declared and features and not unknown else "unverifiable")
    return result("prediction_timing", status, {
        "prediction_time": declaration if declared else "No approved prediction-time declaration.",
        "documented_availability": known, "undocumented_features": unknown,
        "timing_conflicts": conflicts,
        "completion_information_candidates": risks,
        "candidate_scope": "Name-based screening only, not proof of leakage or safe availability.",
        "evidence_gap": "No actual encounter timestamps or verified feature-availability dictionary in published assets.",
        "interpretation": "Admission-time use of completed-stay or discharge information is not assumed.",
    })
