from app.blocks.checks._shared import metadata, result, split_counts


MAPPING = {"<30": 1, ">30": 0, "NO": 0}


def run(context: dict) -> dict:
    records, error = metadata(context, "label_observability", ("patient_key", "split", "target"))
    if error:
        return error
    mapping = context.get("target_mapping")
    if context.get("dataset_context", {}).get("legacy") is False:
        expected = context["data_manifest"]["prepared_source"]["target_mapping"]
        definition = "passed" if mapping == expected else "failed"
        return result("label_observability", "failed" if definition == "failed" else "unverifiable", {
            "target_definition": {"status": definition, "mapping": mapping, "expected": expected,
                                  "scope": "Registered coding contract, not clinical adjudication."},
            "encoding": {"status": "passed", "classes": [0, 1], "positive_class": 1,
                         "observed_counts": split_counts(records)},
            "observation_evidence": {"status": "unverifiable",
                                     "reason": "No independent outcome-ascertainment evidence supplied."},
            "interpretation": "Registered encoding does not establish outcome ascertainment or clinical validity.",
        }, context)
    alignment = context.get("raw_evidence", {}).get("label_alignment", {
        "status": "unverifiable", "reason": "Original readmitted values are not supplied."})
    definition_status = "unverifiable" if mapping is None else ("passed" if mapping == MAPPING else "failed")
    status = "failed" if definition_status == "failed" or alignment["status"] == "failed" else "unverifiable"
    return result("label_observability", status, {
        "target_definition": {"status": definition_status, "mapping": mapping, "expected": MAPPING},
        "encoding": {"status": "passed", "classes": [0, 1], "positive_class": 1,
                     "observed_counts": split_counts(records)},
        "raw_label_alignment": alignment,
        "followup_completeness": {"status": "unverifiable",
                                 "reason": "No observation end dates or individual 30-day follow-up dates."},
        "outside_hospital_capture": {"status": "unverifiable",
                                    "reason": "No evidence that readmissions outside participating hospitals are captured."},
        "interpretation": "Encoded target 0 does not establish complete, event-free 30-day follow-up.",
    }, context)
