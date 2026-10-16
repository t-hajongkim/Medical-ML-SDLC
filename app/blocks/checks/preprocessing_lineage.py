from app.blocks.checks._shared import result


def run(context: dict) -> dict:
    manifest = context.get("data_manifest") or {}
    source = manifest.get("prepared_source") or {}
    limitations = source.get("limitations", [])
    stages = {}
    for name in ("imputation", "scaling", "target_encoding"):
        stages[name] = {
            "status": "unverifiable", "fit_scope": None,
            "reason": "Exact historical fit rows and transform lineage are not supplied.",
        }
    stages["imputation"]["evidence"] = [item for item in limitations if "race" in item and "imputed" in item]
    stages["imputation"]["documented_rule"] = (
        "Original missing race is Caucasian in public metadata; fit population unknown."
        if stages["imputation"]["evidence"] else None)
    stages["scaling"]["evidence"] = [item for item in limitations if "standardized" in item]
    stages["target_encoding"]["evidence"] = [item for item in limitations if "target encoding" in item]
    details = {
        "asset_integrity": {"status": "passed" if context.get("asset_integrity_verified") is True else "unverifiable",
                            "scope": "File hashes and immutable source manifest only; not leakage certification."},
        "current_import": {"training_performed": manifest.get("training_performed"),
                           "preprocessing_performed": manifest.get("preprocessing_performed")},
        "historical_transformations": stages,
        "validation_use": {"evidence": [item for item in limitations if "validation" in item],
                           "interpretation": "Development reuse is not an independent validation holdout."},
        "historical_113_pipeline": {"status": "unverifiable",
                                    "reason": "Exact original 113-feature pipeline has not been independently reproduced."},
        "known_limitations": limitations,
        "interpretation": "Do not infer train-only fit, remove suspected features, refit or modify a model.",
    }
    if context.get("dataset_context", {}).get("legacy") is False:
        stages["imputation"]["documented_rule"] = None
        details.pop("historical_113_pipeline")
        details["historical_input_pipeline"] = {
            "status": "unverifiable", "feature_count": len(manifest.get("feature_columns", [])),
            "reason": "Historical fit rows and transformation lineage are not independently supplied.",
        }
        details["validation_use"]["interpretation"] = (
            "Registered limitations only; development reuse or independence is not inferred.")
    return result("preprocessing_lineage", "unverifiable", details)
