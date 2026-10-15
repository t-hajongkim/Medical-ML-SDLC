import hashlib
import json
from pathlib import Path
import re


PINNED_IMAGE = re.compile(
    r"ghcr\.io/[a-z0-9]+(?:-+[a-z0-9]+)*/"
    r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*@sha256:[0-9a-f]{64}"
)
PAIR_FIELDS = (
    "source_sha256", "configuration_sha256", "split_sha256",
    "feature_columns", "prepared_sha256", "checkpoint_sha256", "preprocessor_sha256",
)


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_image_reference(reference: str) -> str:
    if not isinstance(reference, str) or not PINNED_IMAGE.fullmatch(reference):
        raise ValueError("Asset image must be a lowercase GHCR reference pinned by @sha256.")
    return reference


def pair_identifier(manifest: dict) -> str:
    return canonical_hash({field: manifest[field] for field in PAIR_FIELDS})


def validate_manifest(manifest: dict, expected_kind: str) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("kind") != expected_kind:
        raise ValueError("Unsupported asset manifest schema or kind.")
    if manifest.get("pair_id") != pair_identifier(manifest):
        raise ValueError("Asset pair ID does not match its content contract.")
    features = manifest["feature_columns"]
    if not isinstance(features, list) or not features or len(features) != len(set(features)):
        raise ValueError("Prepared feature columns must be nonempty and unique.")
    if manifest.get("participant_training") is not False:
        raise ValueError("Participant assets must be inference-only.")
    if manifest.get("preprocessing_fit_split") != "train" or manifest.get("model_fit_split") != "train":
        raise ValueError("Both preprocessing and model fitting must use train only.")
    if manifest.get("split_patient_overlap") != 0:
        raise ValueError("Prepared assets must have patient-disjoint splits.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Asset file checksums are required.")
    for name, checksum in files.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("Asset file names must not contain paths.")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Asset file checksum is invalid.")
    required = {"train.csv", "valid.csv", "test.csv", "metadata.csv", "db_features.csv", "asset_info.csv"} if expected_kind == "prepared-data" else {"checkpoint.joblib", "preprocessor.joblib"}
    if not required.issubset(files):
        raise ValueError("Required asset files are missing from the manifest.")
    if expected_kind == "prepared-data":
        prepared_files = ("train.csv", "valid.csv", "test.csv", "metadata.csv", "db_features.csv")
        if canonical_hash({name: files[name] for name in prepared_files}) != manifest["prepared_sha256"]:
            raise ValueError("Prepared data checksums do not match the pair contract.")


def verify_files(directory: Path, manifest: dict) -> None:
    for name, checksum in manifest["files"].items():
        candidate = directory / name
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(directory.resolve()) or not candidate.is_file() or file_hash(candidate) != checksum:
            raise ValueError(f"Asset file integrity check failed: {name}.")


def validate_pair(data: dict, model: dict) -> str:
    validate_manifest(data, "prepared-data")
    validate_manifest(model, "fixed-model")
    if data["pair_id"] != model["pair_id"]:
        raise ValueError("Data and model belong to different prepared pairs.")
    for field in PAIR_FIELDS:
        if data[field] != model[field]:
            raise ValueError(f"Data/model contract mismatch: {field}.")
    if model["files"]["checkpoint.joblib"] != model["checkpoint_sha256"]:
        raise ValueError("Checkpoint checksum does not match the model contract.")
    if model["files"]["preprocessor.joblib"] != model["preprocessor_sha256"]:
        raise ValueError("Preprocessor checksum does not match the model contract.")
    return data["pair_id"]
