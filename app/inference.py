"""Inference for the registered frozen model; never fit or preprocess inputs."""

from collections import Counter
import csv
import json
import math
from pathlib import Path
import re
import warnings

from app.asset_contract import read_json
from app.prepared import (
    CONFIG, FEATURE_COLUMNS, SPLITS, csv_reader, valid_row,
    validate_data_manifest, validate_model_manifest,
)


def read_split(assets: Path, manifest: dict, split="validation"):
    if split not in ("validation", "test"):
        raise ValueError("Inference supports only validation or an explicitly selected test split.")
    import numpy as np
    labels = {}
    counts = Counter()
    with (assets / "metadata.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if not reader.fieldnames or not {"row_key", "split", "target"}.issubset(reader.fieldnames):
            raise ValueError("Inference metadata is missing alignment fields.")
        for row in reader:
            valid_row(row, "Inference metadata")
            key, partition, target = row["row_key"], row["split"], row["target"]
            if (not key.strip() or key in labels or partition not in SPLITS
                    or target not in ("0", "1")):
                raise ValueError("Inference metadata keys, split or target are invalid.")
            labels[key] = (partition, int(target))
            counts[partition] += 1
    if dict(counts) != manifest["split_rows"]:
        raise ValueError("Inference metadata counts differ from the frozen splits.")
    positions = {name: set() for name in SPLITS}
    seen, selected = set(), {}
    width = len(manifest["feature_columns"])
    with (assets / "db_features.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv_reader(handle, FEATURE_COLUMNS, "Inference features"):
            valid_row(row, "Inference features")
            key, partition = row["row_key"], row["split"]
            if (key not in labels or key in seen or partition != labels[key][0]
                    or not re.fullmatch(r"[1-9][0-9]*", row["row_position"])):
                raise ValueError("Inference feature keys, split or row position do not align.")
            position = int(row["row_position"])
            if position > manifest["split_rows"][partition] or position in positions[partition]:
                raise ValueError("Inference row positions are duplicated or out of range.")
            values = json.loads(row["feature_values"])
            if (not isinstance(values, list) or len(values) != width
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)):
                raise ValueError("Inference features must have the frozen width and finite numeric values.")
            seen.add(key)
            positions[partition].add(position)
            if partition == split:
                selected[position] = (values, labels[key][1])
    if seen != set(labels):
        raise ValueError("Inference features do not cover the exact metadata rows.")
    ordered = [selected[index] for index in range(1, manifest["split_rows"][split] + 1)]
    return np.asarray([item[0] for item in ordered], dtype=float), np.asarray(
        [item[1] for item in ordered], dtype=int)


def validate_registered_model(data, model):
    import sklearn
    registered = read_json(CONFIG)
    if (data["feature_columns"] != registered["features"]
            or len(data["feature_columns"]) != 113
            or model["model_source"] != registered["model"]
            or data["prepared_source"]["model"] != registered["model"]):
        raise ValueError("Inference requires the registered fixed model and its frozen feature contract.")
    if sklearn.__version__ != registered["model"]["artifact_scikit_learn_version"]:
        raise ValueError("Use requirements-inference.txt with Python 3.11 for the model's saved runtime.")


def predict(data_assets: Path, model_assets: Path, split="validation", *, expected_manifest=None):
    data = validate_data_manifest(data_assets)
    if expected_manifest is not None and data != expected_manifest:
        raise ValueError("Actual inference data differs from the merged plan's input manifest.")
    model_manifest = validate_model_manifest(model_assets, data)
    validate_registered_model(data, model_manifest)
    matrix, labels = read_split(data_assets, data, split)
    import joblib
    import numpy as np
    import pandas as pd
    import sklearn
    from sklearn.linear_model import LogisticRegression
    # Only the hash-verified, operator-registered checkpoint may be deserialized.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        model = joblib.load(model_assets / "logistic_regression.pkl")
    if (type(model) is not LogisticRegression or model.n_features_in_ != matrix.shape[1]
            or not np.array_equal(model.classes_, [0, 1])):
        raise ValueError("Loaded model type, feature count or binary class order is incompatible.")
    features = matrix
    if hasattr(model, "feature_names_in_"):
        if not np.array_equal(model.feature_names_in_, data["feature_columns"]):
            raise ValueError("Loaded model feature names differ from the frozen feature order.")
        features = pd.DataFrame(matrix, columns=data["feature_columns"])
    probabilities = np.asarray(model.predict_proba(features))
    if (probabilities.shape != (len(labels), 2) or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()
            or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=0, atol=1e-10)):
        raise ValueError("Model probabilities violate the binary inference output contract.")
    return {
        "context": {"y_true": labels, "y_probability": probabilities[:, 1]},
        "data_manifest": data, "model_manifest": model_manifest,
        "summary": {
            "split": split, "rows": len(labels), "feature_count": matrix.shape[1],
            "positive_class": 1, "model_class": "sklearn.linear_model.LogisticRegression",
            "runtime_scikit_learn": sklearn.__version__,
            "training_performed": False, "preprocessing_performed": False,
            "inference_performed": True,
        },
    }
