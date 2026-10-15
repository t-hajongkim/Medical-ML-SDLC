from app.blocks.checks import demographics, patient_overlap, schema


CHECKS = {
    "patient_overlap": patient_overlap.run,
    "demographics": demographics.run,
    "schema": schema.run,
}


def run_checks(identifiers: list[str], context: dict) -> list[dict]:
    unknown = set(identifiers) - CHECKS.keys()
    if unknown:
        raise ValueError("Unknown check blocks: " + ", ".join(sorted(unknown)))
    return [CHECKS[identifier](context) for identifier in identifiers]
