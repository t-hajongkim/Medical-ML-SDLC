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
    return result("preprocessing_lineage", "unverifiable", {
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
    })
