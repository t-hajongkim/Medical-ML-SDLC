from app.blocks.checks import (
    cohort, demographics, label_observability, patient_overlap,
    prediction_timing, preprocessing_lineage, schema,
)


CHECKS = {
    "patient_overlap": patient_overlap.run,
    "demographics": demographics.run,
    "schema": schema.run,
    "cohort": cohort.run,
    "prediction_timing": prediction_timing.run,
    "label_observability": label_observability.run,
    "preprocessing_lineage": preprocessing_lineage.run,
}


def run_checks(identifiers: list[str], context: dict) -> list[dict]:
    unknown = set(identifiers) - CHECKS.keys()
    if unknown:
        raise ValueError("Unknown check blocks: " + ", ".join(sorted(unknown)))
    return [CHECKS[identifier](context) for identifier in identifiers]
