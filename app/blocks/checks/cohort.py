from app.blocks.checks._shared import metadata, result, split_counts


SOURCE_SELECTION = [
    "Inpatient encounters",
    "Diabetes entered as a diagnosis during the encounter",
    "Length of stay between 1 and 14 days",
    "Laboratory tests performed during the encounter",
    "Medications administered during the encounter",
]
SOURCE_URL = "https://archive.ics.uci.edu/dataset/296/diabetes+130-us+hospitals+for+years+1999-2008"


def run(context: dict) -> dict:
    records, error = metadata(context, "cohort", ("row_key", "patient_key", "split", "target"))
    if error:
        return error
    unique = len({row["row_key"] for row in records})
    details = {
        "data_unit": "admission/encounter",
        "research_unit": context.get("research_unit") or "not declared",
        "counts": split_counts(records), "total_admissions": len(records),
        "unique_admission_keys": unique, "duplicate_admission_keys": len(records) - unique,
        "unique_patients": len({row["patient_key"] for row in records}),
        "source_selection_description": {"source": SOURCE_URL, "criteria": SOURCE_SELECTION,
                                         "scope": "Source selection description, not row-level certification."},
        "row_level_selection": context.get("raw_evidence", {}).get("selection", {
            "status": "unverifiable", "reason": "Raw source fields are not supplied."}),
        "upstream_population": {"status": "unverifiable", "population": None, "excluded": None,
                                "reason": "Pre-publication source population and exclusion counts are unavailable."},
        "additional_selection": {"status": "unverifiable",
                                 "declared": bool(context.get("additional_selection")),
                                 "reason": "Free text is neither executed nor echoed; no approved structured selection is implemented."},
        "repeated_admissions": "Retained; not duplicate patients or a first-admission cohort.",
    }
    if context.get("dataset_context", {}).get("legacy") is False:
        registered = context["dataset_context"]
        details["data_unit"] = "registered row; clinical unit requires source documentation"
        details["count_unit"] = "rows"
        details["source_selection_description"] = {
            "registered": registered.get("source_selection"),
            "scope": "Registered source description only; not row-level certification.",
        }
        details["repeated_admissions"] = "Rows retained; no first-event filtering performed."
    if details["research_unit"] not in ("admission", "patient", "not declared"):
        return result("cohort", "failed", {**details, "reason": "Unknown research unit."}, context)
    failed = unique != len(records) or details["row_level_selection"]["status"] == "failed"
    return result("cohort", "failed" if failed else "unverifiable", details, context)
