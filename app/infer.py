import argparse
import importlib.metadata
import json
from pathlib import Path

import joblib
import numpy as np

from app.asset_contract import read_json, validate_manifest, verify_files, write_json


def predict(model_directory: Path, payload: dict) -> dict:
    manifest = read_json(model_directory / "model_manifest.json")
    validate_manifest(manifest, "fixed-model")
    verify_files(model_directory, manifest)
    for name, version in manifest["runtime_versions"].items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f"Inference runtime version mismatch: {name}.")
    if payload.get("feature_columns") != manifest["feature_columns"]:
        raise ValueError("Feature names and order differ from the trained checkpoint.")
    rows = payload.get("rows")
    keys = payload.get("row_keys")
    if not isinstance(rows, list) or not rows or not isinstance(keys, list) or len(keys) != len(rows):
        raise ValueError("Nonempty rows and matching row keys are required.")
    if any(not isinstance(key, str) or not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("Row keys must be unique, nonempty strings.")
    if any(not isinstance(row, list) or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in row) for row in rows):
        raise ValueError("Prepared model inputs must be numeric arrays.")
    values = np.asarray(rows, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != len(manifest["feature_columns"]) or not np.isfinite(values).all():
        raise ValueError("Prepared inputs have invalid dimensions or nonfinite values.")
    model = joblib.load(model_directory / "checkpoint.joblib")
    if list(model.classes_) != [0, 1]:
        raise ValueError("Checkpoint classes do not match the binary label contract.")
    probabilities = model.predict_proba(values)[:, 1]
    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise ValueError("Checkpoint returned invalid probabilities.")
    return {
        "pair_id": manifest["pair_id"],
        "checkpoint_sha256": manifest["checkpoint_sha256"],
        "positive_class": 1,
        "row_keys": keys,
        "predictions": model.predict(values).astype(int).tolist(),
        "probabilities": probabilities.tolist(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inference only; no participant model fitting.")
    parser.add_argument("--model-dir", type=Path, default=Path("/opt/workshop/model"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = predict(arguments.model_dir, read_json(arguments.input))
        write_json(arguments.output, result)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps({"rows": len(result["row_keys"]), "pair_id": result["pair_id"]}))


if __name__ == "__main__":
    main()
