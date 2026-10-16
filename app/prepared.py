"""Import the frozen public splits without fitting, transforming or loading a model."""

import argparse
from copy import deepcopy
import csv
import io
from itertools import zip_longest
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time
from urllib.request import urlopen
import uuid

from app.asset_contract import canonical_hash, file_hash, read_json, verify_files, write_json
from app.dataset import MANIFEST, ROOT, download_source, validate_dataset


CONFIG = ROOT / "config" / "prepared-source.json"
SPLITS = ("train", "validation", "test")
METADATA_COLUMNS = (
    "row_key", "patient_key", "split", "target", "race", "gender", "age_group",
    "race_original", "race_was_missing",
)
FEATURE_COLUMNS = ("row_key", "split", "row_position", "feature_values")
INFO_COLUMNS = (
    "data_id", "expected_rows", "feature_count", "train_rows", "validation_rows",
    "test_rows", "train_validation_overlap", "train_test_overlap",
    "validation_test_overlap", "train_race_missing", "validation_race_missing",
    "test_race_missing",
)


def input_metadata(spec: dict) -> dict:
    """Read publisher-declared semantics; adapt only the documented pre-metadata v2 format."""
    if "input_metadata" in spec:
        return deepcopy(spec["input_metadata"])
    if (spec.get("dataset") != "auphong2707/hospital-readmission-risk-data"
            or spec.get("target_mapping") != {"<30": 1, ">30": 0, "NO": 0}):
        raise ValueError("Prepared input semantics are missing; unknown sources cannot be inferred.")
    descriptions = {
        "row_key": "UCI encounter_id에 대응하는 입원 단위 고유 키입니다.",
        "patient_key": "UCI encounter_id 조인으로 복원한 patient_nbr입니다. 동일 환자의 여러 입원을 연결하며 입원 ID가 아닙니다.",
        "split": "공개 준비 데이터의 고정 train/validation/test 분할입니다.",
        "target": "이미 이진 인코딩된 30일 이내 재입원 정답입니다.",
        "race": "공개 준비 데이터의 인종 값이며 결측치 대체가 포함됩니다.",
        "gender": "준비 데이터의 성별 값입니다.",
        "age_group": "준비 데이터의 연령 구간입니다.",
        "race_original": "원본 인종 값으로, 원본 결측은 빈 값으로 보존됩니다.",
        "race_was_missing": "원본 인종 결측 여부를 나타내는 명시적 true/false 플래그입니다.",
    }
    return {
        "profile_id": "diabetes_prepared", "source_kind": "legacy_prepared_v2",
        "bindings": {name: name for name in METADATA_COLUMNS},
        "columns": {name: {"semantic_roles": [name], "description": descriptions[name]}
                    for name in METADATA_COLUMNS},
        "split_values": {name: name for name in SPLITS}, "target_values": {"0": 0, "1": 1},
        "target_definition": "UCI readmitted의 <30은 1, >30과 NO는 0으로 제공된 정답입니다. 0이 완전한 30일 무사건 추적을 입증하지는 않습니다.",
        "source_selection": "등록된 hospital-readmission-risk-data의 고정 준비 분할과 대응 모델을 사용합니다. UCI 당뇨병 입원 자료에서 선택된 코호트이며 원자료를 새로 다운로드하거나 전처리하지 않습니다.",
        "limitations": [
            "환자 단위 분할 간 중복은 실제 patient_nbr로 측정하며 입원 ID로 대신하지 않습니다.",
            "인종 결측 대체와 원본 결측은 별도 필드로 보존합니다.",
            "등록된 준비 데이터와 대응 모델에 한정되며 임의 원자료나 모든 ML 문제의 자동 지원을 의미하지 않습니다.",
            "새 데이터에는 운영자의 profile, data_id 및 대응 이미지 등록이 필요합니다.",
        ],
    }


def validate_source_contract(spec: dict) -> None:
    base = (
        "https://huggingface.co/datasets/auphong2707/hospital-readmission-risk-data/"
        f"resolve/{spec.get('revision')}/splits/"
    )
    if (spec.get("schema_version") != 1
            or spec.get("dataset") != "auphong2707/hospital-readmission-risk-data"
            or not re.fullmatch(r"[0-9a-f]{40}", spec.get("revision", ""))
            or spec.get("base_url") != base):
        raise ValueError("Prepared source must use an immutable registered splits URL.")
    features = spec.get("features")
    if (not isinstance(features, list) or not features
            or any(not isinstance(name, str) or not name for name in features)
            or len(features) != len(set(features))
            or {"target", "encounter_id", "patient_nbr", "row_key"}.intersection(features)):
        raise ValueError("Prepared feature schema is invalid.")
    if (spec.get("target") != "target"
            or spec.get("target_mapping") != {"<30": 1, ">30": 0, "NO": 0}
            or spec.get("demographics_columns") != ["race", "gender", "age", "encounter_id"]
            or set(spec.get("splits", {})) != set(SPLITS)):
        raise ValueError("Prepared target, demographics or split contract is invalid.")
    for split in SPLITS:
        entry = spec["splits"][split]
        if type(entry.get("rows")) is not int or entry["rows"] <= 0:
            raise ValueError("Each split must have a positive frozen row count.")
        for kind, suffix in (("data", ".csv"), ("demographics", "_demographics.csv")):
            resource = entry[kind]
            if (resource.get("name") != split + suffix
                    or type(resource.get("bytes")) is not int or resource["bytes"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{64}", resource.get("sha256", ""))):
                raise ValueError("Prepared resource name, bytes or SHA256 is invalid.")
    overlap = spec.get("expected_patient_overlap", {})
    if (("expected_patient_overlap" in spec or "expected_test_race_missing" in spec)
            and (set(overlap) != {"train_validation", "train_test", "validation_test"}
            or any(type(value) is not int or value < 0 for value in overlap.values())
            or type(spec.get("expected_test_race_missing")) is not int
            or spec["expected_test_race_missing"] < 0)):
        raise ValueError("Frozen overlap or missing-race expectations are invalid.")


def verify_resource(path: Path, resource: dict) -> None:
    if (path.is_symlink() or not path.is_file() or path.stat().st_size != resource["bytes"]
            or file_hash(path) != resource["sha256"]):
        raise ValueError(f"Prepared resource integrity mismatch: {resource['name']}.")


def download_resource(destination: Path, resource: dict, base_url: str) -> None:
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing prepared download.")
    try:
        with urlopen(base_url + resource["name"], timeout=60) as response, destination.open("xb") as target:
            remaining = resource["bytes"]
            while remaining:
                chunk = response.read(min(1048576, remaining))
                if not chunk:
                    raise ValueError("Prepared download is shorter than its frozen size.")
                target.write(chunk)
                remaining -= len(chunk)
            if response.read(1):
                raise ValueError("Prepared download exceeds its frozen size.")
        verify_resource(destination, resource)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def csv_reader(handle, columns, label):
    reader = csv.DictReader(handle, strict=True)
    if reader.fieldnames != list(columns):
        raise ValueError(f"{label} schema or feature order mismatch.")
    return reader


def valid_row(row, label):
    if row is None or None in row or any(value is None for value in row.values()):
        raise ValueError(f"{label} row count or column count mismatch.")


def data_identifier(manifest: dict) -> str:
    return canonical_hash({
        "source": manifest["prepared_source"],
        "raw_sha256": manifest["raw_source_sha256"],
        "files": manifest["content_files"],
    })


def validate_model_source(spec: dict) -> None:
    model = spec.get("model", {})
    base = (
        "https://huggingface.co/auphong2707/hospital-readmission-phase2-lr/"
        f"resolve/{model.get('revision')}/"
    )
    if (model.get("repository") != "auphong2707/hospital-readmission-phase2-lr"
            or not re.fullmatch(r"[0-9a-f]{40}", model.get("revision", ""))
            or model.get("base_url") != base
            or model.get("model_class") != "sklearn.linear_model.LogisticRegression"
            or model.get("classes") != [0, 1] or model.get("positive_class") != 1
            or model.get("artifact_scikit_learn_version") != "1.2.2"
            or model.get("local_validation_scikit_learn_version") != "1.6.1"
            or model.get("license") != "MIT"):
        raise ValueError("The conditional model source contract is invalid.")
    for kind, name in (("checkpoint", "logistic_regression.pkl"), ("readme", "README.md")):
        entry = model.get(kind, {})
        if (entry.get("name") != name or type(entry.get("bytes")) is not int or entry["bytes"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", ""))):
            raise ValueError("The model artifact resource contract is invalid.")


def model_identifier(manifest: dict) -> str:
    return canonical_hash({key: value for key, value in manifest.items() if key != "model_id"})


def validate_model_manifest(directory: Path, data_manifest: dict) -> dict:
    manifest = read_json(directory / "model_manifest.json")
    model = data_manifest["prepared_source"]["model"]
    validate_model_source(data_manifest["prepared_source"])
    if (manifest.get("schema_version") != 2 or manifest.get("kind") != "model-artifact"
            or manifest.get("model_id") != model_identifier(manifest)
            or manifest.get("data_id") != data_manifest["data_id"]
            or manifest.get("model_source") != model
            or manifest.get("feature_columns") != data_manifest["feature_columns"]
            or manifest.get("raw_source_sha256") != data_manifest["raw_source_sha256"]
            or manifest.get("input_contract") != "already-standardized ordered prepared features; no additional scaler"
            or manifest.get("target_mapping") != data_manifest["prepared_source"]["target_mapping"]
            or manifest.get("classes") != [0, 1] or manifest.get("positive_class") != 1
            or manifest.get("training_performed") is not False
            or manifest.get("inference_performed_by_publisher") is not False):
        raise ValueError("Model artifact manifest is inconsistent with the prepared data.")
    files = manifest.get("files", {})
    if (set(files) != {"logistic_regression.pkl", "MODEL_SOURCE_README.txt", "NOTICE.txt"}
            or files["logistic_regression.pkl"] != model["checkpoint"]["sha256"]
            or files["MODEL_SOURCE_README.txt"] != model["readme"]["sha256"]
            or any(not re.fullmatch(r"[0-9a-f]{64}", value or "") for value in files.values())):
        raise ValueError("Model artifact file allowlist or checkpoint hash is inconsistent.")
    verify_files(directory, manifest)
    if {path.name for path in directory.iterdir()} != {*files, "model_manifest.json"}:
        raise ValueError("Unexpected files in the model artifact.")
    return manifest


def prepare_model(
    prepared_directory: Path, model_source_directory: Path | None = None,
) -> dict:
    data = validate_data_manifest(prepared_directory / "data" / "assets")
    spec = data["prepared_source"]
    validate_model_source(spec)
    context = prepared_directory / "model"
    if context.exists():
        raise ValueError("The model artifact context already exists.")
    assets = context / "assets"
    assets.mkdir(parents=True)
    try:
        for kind, packaged_name, cached_name in (
            ("checkpoint", "logistic_regression.pkl", "logistic_regression.pkl"),
            ("readme", "MODEL_SOURCE_README.txt", "MODEL_SOURCE_README.txt"),
        ):
            entry = spec["model"][kind]
            destination = assets / packaged_name
            if model_source_directory is None:
                download_resource(destination, entry, spec["model"]["base_url"])
            else:
                candidate = model_source_directory / cached_name
                verify_resource(candidate, entry)
                shutil.copyfile(candidate, destination)
                verify_resource(destination, entry)
        shutil.copyfile(ROOT / "data" / "NOTICE.txt", assets / "NOTICE.txt")
        manifest = {
            "schema_version": 2, "kind": "model-artifact", "data_id": data["data_id"],
            "model_source": spec["model"], "raw_source_sha256": data["raw_source_sha256"],
            "feature_columns": data["feature_columns"],
            "input_contract": "already-standardized ordered prepared features; no additional scaler",
            "target_mapping": spec["target_mapping"], "classes": [0, 1], "positive_class": 1,
            "training_performed": False, "inference_performed_by_publisher": False,
            "files": {path.name: file_hash(path) for path in assets.iterdir()},
        }
        manifest["model_id"] = model_identifier(manifest)
        write_json(assets / "model_manifest.json", manifest)
        validate_model_manifest(assets, data)
        shutil.copyfile(ROOT / "app" / "Dockerfile", context / "Dockerfile")
        (context / ".dockerignore").write_text(
            "**\n!Dockerfile\n!assets/\n!assets/logistic_regression.pkl\n"
            "!assets/model_manifest.json\n!assets/MODEL_SOURCE_README.txt\n!assets/NOTICE.txt\n",
            encoding="utf-8",
        )
        return {"model_id": manifest["model_id"], "data_id": data["data_id"],
                "checkpoint_sha256": manifest["files"]["logistic_regression.pkl"],
                "registration": spec["model"]["registration"], "inference_performed_by_publisher": False}
    except Exception:
        shutil.rmtree(context)
        raise


def validate_data_manifest(directory: Path) -> dict:
    manifest = read_json(directory / "data_manifest.json")
    if (manifest.get("schema_version") != 2 or manifest.get("kind") != "prepared-data-only"
            or manifest.get("training_performed") is not False
            or manifest.get("preprocessing_performed") is not False
            or manifest.get("data_id") != data_identifier(manifest)):
        raise ValueError("Unsupported or inconsistent data-only manifest.")
    validate_source_contract(manifest["prepared_source"])
    spec = manifest["prepared_source"]
    if (manifest.get("feature_columns") != spec["features"]
            or manifest.get("split_rows") != {
                split: spec["splits"][split]["rows"] for split in SPLITS
            }):
        raise ValueError("Recorded features or split counts differ from the source contract.")
    expected_names = {
        entry[kind]["name"] for entry in spec["splits"].values()
        for kind in ("data", "demographics")
    } | {"metadata.csv", "db_features.csv"}
    files = manifest.get("files", {})
    if (not isinstance(files, dict) or set(files) != expected_names | {"asset_info.csv", "NOTICE.txt"}
            or set(manifest["content_files"]) != expected_names
            or files != {
                **manifest["content_files"],
                "asset_info.csv": files.get("asset_info.csv"),
                "NOTICE.txt": files.get("NOTICE.txt"),
            }):
        raise ValueError("Data-only file allowlist is inconsistent.")
    for name, checksum in manifest["files"].items():
        if not re.fullmatch(r"[0-9a-f]{64}", checksum or ""):
            raise ValueError("Data-only file checksum is invalid.")
    for split in SPLITS:
        for kind in ("data", "demographics"):
            source = spec["splits"][split][kind]
            if manifest["files"][source["name"]] != source["sha256"]:
                raise ValueError("Packaged source differs from the frozen public asset.")
    overlap = manifest.get("patient_overlap")
    pairs = (("train", "validation"), ("train", "test"), ("validation", "test"))
    if (not isinstance(overlap, dict) or set(overlap) != {a + "_" + b for a, b in pairs}
            or any(type(overlap[a + "_" + b]) is not int
                   or not 0 <= overlap[a + "_" + b] <= min(manifest["split_rows"][a], manifest["split_rows"][b])
                   for a, b in pairs)):
        raise ValueError("Recorded patient overlap counts are invalid.")
    if "expected_patient_overlap" in spec and overlap != spec["expected_patient_overlap"]:
        raise ValueError("Recorded overlap differs from the source contract.")
    if ("expected_test_race_missing" in spec
            and manifest.get("race_missing", {}).get("test") != spec["expected_test_race_missing"]):
        raise ValueError("Recorded original missing race differs from the source contract.")
    if (not re.fullmatch(r"[0-9a-f]{64}", manifest.get("raw_source_sha256", ""))
            or set(manifest.get("race_missing", {})) != set(SPLITS)
            or any(type(manifest["race_missing"][split]) is not int
                   or not 0 <= manifest["race_missing"][split] <= spec["splits"][split]["rows"]
                   for split in SPLITS)):
        raise ValueError("Original source hash or missing-race counts are invalid.")
    verify_files(directory, manifest)
    from app.input_contracts import load_profile
    load_profile(manifest=manifest)
    if {path.name for path in directory.iterdir()} != {*files, "data_manifest.json"}:
        raise ValueError("Unexpected files in the data-only asset directory.")
    with (directory / "asset_info.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv_reader(handle, INFO_COLUMNS, "Asset info"))
    expected_info = [
        manifest["data_id"], sum(manifest["split_rows"].values()), len(spec["features"]),
        *(manifest["split_rows"][split] for split in SPLITS),
        *(manifest["patient_overlap"][name] for name in ("train_validation", "train_test", "validation_test")),
        *(manifest["race_missing"][split] for split in SPLITS),
    ]
    if rows != [dict(zip(INFO_COLUMNS, map(str, expected_info)))]:
        raise ValueError("Data-only manifest and database input contract differ.")
    return manifest


def prepare_data(
    output: Path,
    source_directory: Path | None = None,
    raw_csv: Path | None = None,
    config_path: Path = CONFIG,
    raw_manifest_path: Path = MANIFEST,
    source_commit: str = "uncommitted",
) -> dict:
    output = output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new prepared output directory outside the Git checkout.")
    spec = read_json(config_path)
    validate_source_contract(spec)
    raw_manifest = read_json(raw_manifest_path)
    output.mkdir(parents=True)
    try:
        source = output / "source"
        source.mkdir()
        assets = output / "data" / "assets"
        assets.mkdir(parents=True)
        for split in SPLITS:
            for kind in ("data", "demographics"):
                resource = spec["splits"][split][kind]
                destination = assets / resource["name"]
                if source_directory is None:
                    download_resource(destination, resource, spec["base_url"])
                else:
                    candidate = source_directory / resource["name"]
                    verify_resource(candidate, resource)
                    shutil.copyfile(candidate, destination)
                    verify_resource(destination, resource)
        if raw_csv is None:
            raw_csv = source / "raw.csv"
            download_source(raw_csv, raw_manifest_path)
        else:
            validate_dataset(raw_csv, raw_manifest_path)
        raw = {}
        with raw_csv.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, strict=True):
                key = row["encounter_id"]
                patient = row["patient_nbr"]
                if (not key or not patient or key in raw_manifest["missing_tokens"]
                        or patient in raw_manifest["missing_tokens"] or key in raw):
                    raise ValueError("Original encounter or patient keys are missing or duplicated.")
                if row["readmitted"] not in spec["target_mapping"]:
                    raise ValueError("Original target is not covered by the frozen mapping.")
                raw[key] = {
                    name: row[name] for name in ("patient_nbr", "readmitted", "race", "gender", "age")
                }
        seen = set()
        patients = {split: set() for split in SPLITS}
        missing_race = {split: 0 for split in SPLITS}
        split_rows = {}
        with (assets / "metadata.csv").open("w", encoding="utf-8", newline="") as meta_handle, \
                (assets / "db_features.csv").open("w", encoding="utf-8", newline="") as feature_handle:
            meta_writer = csv.writer(meta_handle)
            feature_writer = csv.writer(feature_handle)
            meta_writer.writerow(METADATA_COLUMNS)
            feature_writer.writerow(FEATURE_COLUMNS)
            for split in SPLITS:
                entry = spec["splits"][split]
                with (assets / entry["data"]["name"]).open(encoding="utf-8", newline="") as x_handle, \
                        (assets / entry["demographics"]["name"]).open(encoding="utf-8", newline="") as d_handle:
                    x_reader = csv_reader(x_handle, [*spec["features"], spec["target"]], split)
                    d_reader = csv_reader(d_handle, spec["demographics_columns"], "Demographics")
                    count = 0
                    for count, (features, demo) in enumerate(zip_longest(x_reader, d_reader), 1):
                        valid_row(features, split)
                        valid_row(demo, "Demographics")
                        key = demo["encounter_id"]
                        if key in seen or key not in raw:
                            raise ValueError("Encounter key is duplicated or absent from the frozen original.")
                        original = raw[key]
                        target = features[spec["target"]]
                        if target not in ("0", "1") or int(target) != spec["target_mapping"][original["readmitted"]]:
                            raise ValueError("Target row alignment mismatch.")
                        if demo["gender"] != original["gender"] or demo["age"] != original["age"]:
                            raise ValueError("Demographic row alignment mismatch.")
                        race_missing = original["race"] in raw_manifest["missing_tokens"]
                        expected_race = "Caucasian" if race_missing else original["race"]
                        if demo["race"] != expected_race:
                            raise ValueError("Race row alignment or known imputation mismatch.")
                        try:
                            values = [float(features[name]) for name in spec["features"]]
                        except ValueError:
                            raise ValueError("Prepared features must be numeric.") from None
                        if not all(math.isfinite(value) for value in values):
                            raise ValueError("Prepared features must be finite.")
                        seen.add(key)
                        patients[split].add(original["patient_nbr"])
                        missing_race[split] += int(race_missing)
                        meta_writer.writerow([
                            key, original["patient_nbr"], split, target, demo["race"],
                            demo["gender"], demo["age"], "" if race_missing else original["race"],
                            str(race_missing).lower(),
                        ])
                        feature_writer.writerow([
                            key, split, count, json.dumps(values, allow_nan=False, separators=(",", ":")),
                        ])
                    if count != entry["rows"]:
                        raise ValueError("Prepared split row count mismatch.")
                    split_rows[split] = count
        if seen != set(raw):
            raise ValueError("Prepared encounter coverage differs from the frozen original.")
        overlap = {
            left + "_" + right: len(patients[left] & patients[right])
            for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
        }
        if "expected_patient_overlap" in spec and overlap != spec["expected_patient_overlap"]:
            raise ValueError("Patient overlap differs from the known source limitation.")
        if "expected_test_race_missing" in spec and missing_race["test"] != spec["expected_test_race_missing"]:
            raise ValueError("Original test missing-race count differs from the known source limitation.")
        names = [path.name for path in assets.iterdir()]
        content_files = {name: file_hash(assets / name) for name in sorted(names)}
        manifest = {
            "schema_version": 2, "kind": "prepared-data-only",
            "prepared_source": spec, "raw_source_sha256": raw_manifest["sha256"],
            "feature_columns": spec["features"], "split_rows": split_rows,
            "patient_overlap": overlap, "race_missing": missing_race,
            "training_performed": False, "preprocessing_performed": False,
            "source_commit": source_commit, "content_files": content_files,
        }
        manifest["data_id"] = data_identifier(manifest)
        with (assets / "asset_info.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(INFO_COLUMNS)
            writer.writerow([
                manifest["data_id"], len(seen), len(spec["features"]),
                *(split_rows[split] for split in SPLITS),
                *(overlap[name] for name in ("train_validation", "train_test", "validation_test")),
                *(missing_race[split] for split in SPLITS),
            ])
        shutil.copyfile(ROOT / "data" / "NOTICE.txt", assets / "NOTICE.txt")
        manifest["files"] = {
            **content_files,
            **{name: file_hash(assets / name) for name in ("asset_info.csv", "NOTICE.txt")},
        }
        write_json(assets / "data_manifest.json", manifest)
        validate_data_manifest(assets)
        for name, relative in (
            ("db/Dockerfile", "Dockerfile"), (".dockerignore", ".dockerignore"),
            ("db/init.sql", "db/init.sql"), ("db/healthcheck.sh", "db/healthcheck.sh"),
        ):
            destination = output / "data" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        shutil.rmtree(source)
        result = {
            "data_id": manifest["data_id"], "rows": len(seen),
            "features": len(spec["features"]), "split_rows": split_rows,
            "patient_overlap": overlap, "race_missing": missing_race,
            "training_performed": False, "preprocessing_performed": False,
            "output": str(output),
        }
        write_json(output / "preparation-summary.json", result)
        return result
    except Exception:
        shutil.rmtree(output)
        raise


def docker(*arguments, env=None) -> str:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, encoding="utf-8",
        timeout=180, env=env,
    )
    if result.returncode:
        # Database diagnostics can contain input records: never echo stderr or SQL.
        raise RuntimeError(f"Docker {arguments[0]} failed; inspect the restricted runner context.")
    return result.stdout.strip()


def compare_database_csv(expected_path: Path, actual: str, columns, label: str) -> int:
    with expected_path.open(encoding="utf-8", newline="") as handle:
        expected = csv_reader(handle, columns, label)
        actual_rows = csv_reader(io.StringIO(actual), columns, "Database " + label)
        count = 0
        for count, (reference, row) in enumerate(zip_longest(expected, actual_rows), 1):
            valid_row(reference, label)
            valid_row(row, "Database " + label)
            if label == "features":
                left = json.loads(reference["feature_values"])
                right = json.loads(row["feature_values"])
                equal = left == right and all(row[key] == reference[key] for key in columns[:-1])
            else:
                if label == "metadata":
                    row["race_was_missing"] = {"t": "true", "f": "false"}.get(row["race_was_missing"], row["race_was_missing"])
                equal = row == reference
            if not equal:
                raise ValueError(f"Database {label} value mismatch; no individual records are logged.")
        return count


def smoke_database(image: str, prepared_directory: Path) -> dict:
    assets = prepared_directory / "data" / "assets"
    manifest = validate_data_manifest(assets)
    container = "workshop-data-verify-" + uuid.uuid4().hex[:12]
    environment = {**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(32)}
    started = False
    try:
        docker(
            "run", "--detach", "--rm", "--network", "none", "--name", container,
            "--env", "POSTGRES_PASSWORD", image, env=environment,
        )
        started = True
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
            health = state.get("Health", {}).get("Status")
            if health == "healthy":
                break
            if not state.get("Running") or health == "unhealthy":
                raise RuntimeError("The data-only database did not initialize successfully.")
            time.sleep(1)
        else:
            raise TimeoutError("The data-only database did not become healthy.")
        counts = {}
        for table, columns, order, label, filename in (
            ("metadata", METADATA_COLUMNS, "split, f.row_position", "metadata", "metadata.csv"),
            ("features", FEATURE_COLUMNS, "split, row_position", "features", "db_features.csv"),
            ("asset_info", INFO_COLUMNS, "data_id", "asset_info", "asset_info.csv"),
        ):
            if table == "metadata":
                select = ", ".join("m." + column for column in columns)
                query = f"SELECT {select} FROM prepared.metadata m JOIN prepared.features f USING (row_key) ORDER BY m.{order}"
            else:
                query = f"SELECT {', '.join(columns)} FROM prepared.{table} ORDER BY {order}"
            # CSV source order is train, validation, test, not lexical split order.
            if table != "asset_info":
                query = query.replace("ORDER BY " + ("m.split" if table == "metadata" else "split"),
                                      "ORDER BY CASE " + ("m.split" if table == "metadata" else "split")
                                      + " WHEN 'train' THEN 1 WHEN 'validation' THEN 2 ELSE 3 END")
            actual = docker(
                "exec", container, "psql", "-X", "-w", "-q", "-U", "postgres",
                "-d", "workshop", "-v", "ON_ERROR_STOP=1",
                "-c", f"COPY ({query}) TO STDOUT WITH (FORMAT csv, HEADER true)",
            )
            counts[label] = compare_database_csv(assets / filename, actual, columns, label)
        if counts != {"metadata": sum(manifest["split_rows"].values()),
                      "features": sum(manifest["split_rows"].values()), "asset_info": 1}:
            raise ValueError("Database smoke row counts differ from the data-only contract.")
        result = {"status": "passed", "data_id": manifest["data_id"], "compared_rows": counts}
        write_json(prepared_directory / "database-smoke-summary.json", result)
        return result
    finally:
        if started:
            docker("rm", "--force", container)


def verify_model_image(image: str, prepared_directory: Path) -> dict:
    data = validate_data_manifest(prepared_directory / "data" / "assets")
    expected = validate_model_manifest(prepared_directory / "model" / "assets", data)
    inspection = prepared_directory / "model-image-inspection"
    if inspection.exists():
        raise ValueError("The model image inspection destination already exists.")
    inspection.mkdir()
    container = "workshop-model-inspect-" + uuid.uuid4().hex[:12]
    created = False
    try:
        # Create a filesystem handle only. The scratch artifact has no executable runtime.
        docker("create", "--network", "none", "--name", container, image, "artifact-inspection-only")
        created = True
        docker("cp", container + ":/opt/workshop/assets/.", str(inspection))
        actual = validate_model_manifest(inspection, data)
        if actual != expected:
            raise ValueError("The built model artifact differs from the verified source files.")
        result = {
            "status": "passed", "model_id": actual["model_id"],
            "checkpoint_sha256": actual["files"]["logistic_regression.pkl"],
            "inference_performed": False,
        }
        write_json(prepared_directory / "model-image-summary.json", result)
        return result
    finally:
        try:
            if created:
                docker("rm", "--force", container)
        finally:
            shutil.rmtree(inspection)


def main() -> None:
    parser = argparse.ArgumentParser(description="Frozen data and conditional model artifact publication.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--source-directory", type=Path)
    prepare.add_argument("--raw-csv", type=Path)
    prepare.add_argument("--model-source-directory", type=Path)
    prepare.add_argument("--source-commit", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--image", required=True)
    smoke.add_argument("--prepared-directory", required=True, type=Path)
    model_check = commands.add_parser("verify-model-image")
    model_check.add_argument("--image", required=True)
    model_check.add_argument("--prepared-directory", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "prepare":
            result = prepare_data(
                arguments.output, arguments.source_directory, arguments.raw_csv,
                source_commit=arguments.source_commit,
            )
            try:
                result["model"] = prepare_model(arguments.output, arguments.model_source_directory)
                write_json(arguments.output / "preparation-summary.json", result)
            except Exception:
                shutil.rmtree(arguments.output)
                raise
        elif arguments.command == "smoke":
            result = smoke_database(arguments.image, arguments.prepared_directory)
        else:
            result = verify_model_image(arguments.image, arguments.prepared_directory)
    except (OSError, ValueError, csv.Error, KeyError, RuntimeError, subprocess.TimeoutExpired):
        parser.error("Frozen asset validation failed; no individual input records are logged.")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
