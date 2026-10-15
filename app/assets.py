import argparse
import csv
import importlib.metadata
import json
from pathlib import Path
import shutil
import tempfile
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.asset_contract import canonical_hash, file_hash, pair_identifier, read_json, validate_pair, write_json
from app.blocks.catalog import run_checks
from app.dataset import MANIFEST, ROOT, download_source, validate_dataset


CONFIG = ROOT / "config" / "preparation.json"
SPLITS = ("train", "valid", "test")


def patient_splits(patient_keys: pd.Series, settings: dict) -> pd.Series:
    fractions = [settings[split] for split in SPLITS]
    if any(not isinstance(value, (int, float)) or value <= 0 for value in fractions) or not np.isclose(sum(fractions), 1.0):
        raise ValueError("Positive train/valid/test fractions must sum to one.")
    patients = np.array(sorted(set(patient_keys)), dtype=object)
    np.random.default_rng(settings["seed"]).shuffle(patients)
    train_end = int(len(patients) * fractions[0])
    valid_end = train_end + int(len(patients) * fractions[1])
    groups = (patients[:train_end], patients[train_end:valid_end], patients[valid_end:])
    if any(not len(group) for group in groups):
        raise ValueError("There are too few patients for three nonempty splits.")
    assignments = {patient: split for split, group in zip(SPLITS, groups) for patient in group}
    return patient_keys.map(assignments)


def model_inputs(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    numeric = config["numeric_features"]
    categorical = config["categorical_features"]
    features = numeric + categorical
    forbidden = {config["row_key"], config["patient_key"], config["target"]}
    if not features or len(features) != len(set(features)) or forbidden.intersection(features):
        raise ValueError("Features must be unique and exclude identifiers and the target.")
    missing = set(features) - set(frame.columns)
    if missing:
        raise ValueError("Missing model feature columns: " + ", ".join(sorted(missing)))
    result = frame[features].copy()
    tokens = config["missing_tokens"]
    for column in features:
        result[column] = result[column].replace(tokens, np.nan)
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
        if np.isinf(result[column].to_numpy()).any():
            raise ValueError(f"Feature {column} contains infinite values.")
    return result


def prepare_pair(
    source_csv: Path,
    output: Path,
    config_path: Path = CONFIG,
    manifest_path: Path = MANIFEST,
    source_commit: str = "uncommitted",
) -> dict:
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Prepared data and model files must remain outside the Git checkout.")
    if output.exists():
        raise ValueError("The prepared asset destination already exists.")
    source_info = validate_dataset(source_csv, manifest_path)
    config = read_json(config_path)
    if config["participant_training"] is not False or config["model"]["algorithm"] != "logistic_regression":
        raise ValueError("This operator recipe prepares one inference-only logistic-regression baseline.")
    frame = pd.read_csv(source_csv, dtype=str, keep_default_na=False)
    required = {config["row_key"], config["patient_key"], config["target"], *config["demographics"].values()}
    if not required.issubset(frame.columns):
        raise ValueError("Required identifier, target or demographic columns are absent.")
    if frame[config["row_key"]].duplicated().any():
        raise ValueError("Encounter row keys must be unique.")
    if any(frame[column].isin(config["missing_tokens"]).any() for column in (config["row_key"], config["patient_key"])):
        raise ValueError("Real row and patient keys are required; row numbers are not substitutes.")
    if not set(frame[config["target"]]).issubset(config["target_mapping"]):
        raise ValueError("The source contains unmapped target values.")
    labels = frame[config["target"]].map(config["target_mapping"]).astype(int)
    if set(config["target_mapping"].values()) != {0, 1}:
        raise ValueError("The operator recipe requires a binary target mapping.")
    splits = patient_splits(frame[config["patient_key"]], config["split"])
    inputs = model_inputs(frame, config)
    train = splits == "train"
    if inputs.loc[train].isna().all().any():
        raise ValueError("A training feature is entirely missing; do not invent an imputation value.")
    for split in SPLITS:
        if set(labels.loc[splits == split]) != {0, 1}:
            raise ValueError(f"Split {split} must contain both target classes; no automatic resplitting is performed.")

    transforms = []
    if config["numeric_features"]:
        transforms.append(("numeric", Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]), config["numeric_features"]))
    if config["categorical_features"]:
        transforms.append(("categorical", Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), config["categorical_features"]))
    preprocessor = ColumnTransformer(transforms, remainder="drop")
    preprocessor.fit(inputs.loc[train])
    transformed = np.asarray(preprocessor.transform(inputs), dtype=np.float64)
    if not np.isfinite(transformed).all():
        raise ValueError("Prepared feature values must be finite and nonmissing.")
    model = LogisticRegression(
        C=config["model"]["C"], max_iter=config["model"]["max_iter"],
        solver="lbfgs", random_state=config["split"]["seed"],
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        model.fit(transformed[train.to_numpy()], labels.loc[train].to_numpy())

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workshop-pair-", dir=output.parent) as temporary:
        staged = Path(temporary) / "pair"
        data_dir = staged / "data" / "assets"
        model_dir = staged / "model" / "assets"
        verification = staged / "verification"
        for directory in (data_dir, model_dir, verification):
            directory.mkdir(parents=True)
        feature_columns = [f"f{index:03d}" for index in range(transformed.shape[1])]
        prepared = pd.DataFrame(transformed, columns=feature_columns)
        prepared.insert(0, "row_key", frame[config["row_key"]].to_numpy())
        for split in SPLITS:
            prepared.loc[(splits == split).to_numpy()].to_csv(
                data_dir / f"{split}.csv", index=False, float_format="%.17g",
            )
        metadata = pd.DataFrame({
            "row_key": frame[config["row_key"]],
            "patient_key": frame[config["patient_key"]],
            "split": splits,
            "target": labels,
        })
        for column, source in config["demographics"].items():
            metadata[column] = frame[source].replace(config["missing_tokens"], "")
        metadata.to_csv(data_dir / "metadata.csv", index=False)
        with (data_dir / "db_features.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.writer(target)
            writer.writerow(["row_key", "split", "feature_values"])
            for key, split, values in zip(prepared["row_key"], splits, transformed):
                writer.writerow([key, split, json.dumps(values.tolist(), separators=(",", ":"))])
        joblib.dump(model, model_dir / "checkpoint.joblib")
        joblib.dump(preprocessor, model_dir / "preprocessor.joblib")
        prepared_names = ("train.csv", "valid.csv", "test.csv", "metadata.csv", "db_features.csv")
        data_hashes = {name: file_hash(data_dir / name) for name in prepared_names}
        records = metadata.to_dict("records")
        split_rows = {split: int((splits == split).sum()) for split in SPLITS}
        contract = {
            "schema_version": 1,
            "source_sha256": source_info["sha256"],
            "configuration_sha256": canonical_hash(config),
            "split_sha256": canonical_hash(metadata[["row_key", "patient_key", "split"]].to_dict("records")),
            "prepared_sha256": canonical_hash(data_hashes),
            "feature_columns": feature_columns,
            "checkpoint_sha256": file_hash(model_dir / "checkpoint.joblib"),
            "preprocessor_sha256": file_hash(model_dir / "preprocessor.joblib"),
            "feature_names": preprocessor.get_feature_names_out().tolist(),
            "participant_training": False,
            "preprocessing_fit_split": "train",
            "model_fit_split": "train",
            "split_patient_overlap": 0,
            "positive_class": 1,
            "target_mapping": config["target_mapping"],
            "split_rows": split_rows,
            "source_commit": source_commit,
            "operator_configuration": config,
            "runtime_versions": {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "scipy", "joblib")},
            "temporal_leakage": "unverifiable: this source supplies no per-feature event timestamps",
        }
        contract["pair_id"] = pair_identifier(contract)
        with (data_dir / "asset_info.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.writer(target)
            writer.writerow(["pair_id", "expected_rows", "feature_count"])
            writer.writerow([contract["pair_id"], len(metadata), len(feature_columns)])
        shutil.copyfile(ROOT / "data" / "NOTICE.txt", data_dir / "NOTICE.txt")
        shutil.copyfile(ROOT / "data" / "NOTICE.txt", model_dir / "NOTICE.txt")
        data_manifest = {
            **contract, "kind": "prepared-data",
            "files": {**data_hashes, "asset_info.csv": file_hash(data_dir / "asset_info.csv"), "NOTICE.txt": file_hash(data_dir / "NOTICE.txt")},
        }
        model_manifest = {
            **contract, "kind": "fixed-model",
            "files": {name: file_hash(model_dir / name) for name in ("checkpoint.joblib", "preprocessor.joblib", "NOTICE.txt")},
        }
        validate_pair(data_manifest, model_manifest)
        write_json(data_dir / "data_manifest.json", data_manifest)
        write_json(model_dir / "model_manifest.json", model_manifest)
        sample = prepared.loc[(splits == "valid").to_numpy()].head(8)
        write_json(verification / "input.json", {
            "feature_columns": feature_columns,
            "row_keys": sample["row_key"].tolist(),
            "rows": sample[feature_columns].to_numpy().tolist(),
        })
        write_json(verification / "expected.json", {
            "probabilities": model.predict_proba(sample[feature_columns].to_numpy())[:, list(model.classes_).index(1)].tolist(),
        })
        def audit_features():
            positions = {}
            for record, values in zip(records, transformed):
                split = record["split"]
                positions[split] = positions.get(split, 0) + 1
                yield {"row_key": record["row_key"], "split": split,
                       "row_position": positions[split], "feature_values": values.tolist()}

        checks = run_checks(["patient_overlap", "demographics", "schema"], {
            "metadata": records, "feature_columns": feature_columns,
            "expected_feature_columns": model_manifest["feature_columns"],
            "feature_rows": audit_features,
        })
        # Legacy initialization gates measurable structure, not clinical certification.
        if checks[0]["status"] != "passed" or checks[2]["details"].get("numeric_contract") != "passed":
            raise ValueError("Prepared pair failed its initialization checks.")
        write_json(staged / "pair_manifest.json", {
            "pair_id": contract["pair_id"], "rows": len(metadata),
            "feature_count": len(feature_columns), "split_rows": split_rows, "checks": checks,
        })
        for source, relative in (
            ("db/Dockerfile", "data/Dockerfile"),
            (".dockerignore", "data/.dockerignore"),
            ("db/init.sql", "data/db/init.sql"),
            ("db/healthcheck.sh", "data/db/healthcheck.sh"),
            ("app/Dockerfile", "model/Dockerfile"),
            ("requirements-ml.txt", "model/requirements-ml.txt"),
            ("app/infer.py", "model/app/infer.py"),
            ("app/asset_contract.py", "model/app/asset_contract.py"),
        ):
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, destination)
        (staged / "model" / ".dockerignore").write_text(
            "**\n!Dockerfile\n!requirements-ml.txt\n!app/\n!app/*.py\n!assets/\n!assets/*.joblib\n!assets/model_manifest.json\n!assets/NOTICE.txt\n",
            encoding="utf-8",
        )
        staged.rename(output)
    return {"pair_id": contract["pair_id"], "rows": len(metadata), "features": len(feature_columns), "split_rows": split_rows, "output": str(output)}


def prepare_operator_assets(output: Path, config_path: Path = CONFIG, manifest_path: Path = MANIFEST, source_commit: str = "uncommitted") -> dict:
    output = output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new operator output folder outside the Git checkout.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="operator-source-", dir=output.parent) as temporary:
        source = Path(temporary) / "source.csv"
        download_source(source, manifest_path)
        return prepare_pair(source, output, config_path, manifest_path, source_commit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator-only prepared dataset/checkpoint creation.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--source-commit", required=True)
    arguments = parser.parse_args()
    try:
        result = prepare_operator_assets(arguments.output, arguments.config, arguments.manifest, arguments.source_commit)
    except (OSError, ValueError, KeyError, ConvergenceWarning) as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
