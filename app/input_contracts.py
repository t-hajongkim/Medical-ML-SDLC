"""GHCR manifest의 이진분류 입력 메타데이터를 표준 EDA 문맥으로 연결합니다.

공개 API는 load_profile, read_manifest, inspect_inputs, apply_mapping입니다.
AI에는 bundle 전체가
아닌 schema_request만 전달합니다. 추천 bindings는 역할별 원본 컬럼명이며 reasons는
같은 역할별 한국어 설명, evidence는 요청의 column:<순번>:<역할> 근거 ID 목록입니다.
새 데이터는 digest로 고정된 이미지 안에 운영자가 명시한 입력 의미 사전이 필요합니다.
원자료 다운로드, 전처리 fit, 모델 실행 또는 임의 코드 실행은 수행하지 않습니다.
"""

from collections import Counter
from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import re

from app import prepared
from app.asset_contract import canonical_hash, file_hash


REQUIRED_ROLES = ("row_key", "patient_key", "split", "target")
OPTIONAL_ROLES = ("race", "gender", "age_group", "race_original", "race_was_missing")
ROLES = REQUIRED_ROLES + OPTIONAL_ROLES
PROVIDER = "github-copilot-cloud-agent"
PROFILE_FIELDS = {
    "source_kind", "data_id", "bindings", "columns", "split_values",
    "target_values", "target_definition", "source_selection", "limitations",
}
OPTIONAL_PROFILE_FIELDS = {"probability_output_planned"}
MANIFEST_FIELDS = {
    "schema_version", "kind", "data_id", "feature_columns", "split_rows", "files",
    "training_performed", "preprocessing_performed", "input_metadata",
}


def _fail(message):
    raise ValueError(message)


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("JSON 객체에 중복 키가 있습니다.")
        value[key] = item
    return value


def _invalid_constant(_):
    _fail("JSON의 비표준 숫자는 허용하지 않습니다.")


def _json(text):
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        _fail("올바른 표준 JSON이 필요합니다.")


def _read_json(path):
    try:
        value = _json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        _fail("등록 설정 또는 자산 JSON을 읽을 수 없습니다.")
    if not isinstance(value, dict):
        _fail("JSON 최상위 값은 객체여야 합니다.")
    return value


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_bindings(bindings, profile):
    if (not isinstance(bindings, dict) or not set(REQUIRED_ROLES).issubset(bindings)
            or not set(bindings).issubset(ROLES)):
        _fail("필수 역할을 포함하는 등록 역할 매핑이 필요합니다.")
    for role, column in bindings.items():
        if (not isinstance(column, str) or column not in profile["columns"]
                or role not in profile["columns"][column]["semantic_roles"]):
            _fail("매핑 컬럼에 해당 역할의 운영자 등록 의미 근거가 없습니다.")
    if len(set(bindings.values())) != len(bindings):
        _fail("서로 다른 역할을 같은 컬럼으로 대체할 수 없습니다.")


def _validate_profile(profile):
    if (not (PROFILE_FIELDS | {"profile_id"}).issubset(profile)
            or set(profile) - PROFILE_FIELDS - OPTIONAL_PROFILE_FIELDS - {"profile_id"}
            or not _text(profile["profile_id"])
            or profile["source_kind"] not in ("legacy_prepared_v2", "registered_prepared_v1")
            or not _hash(profile["data_id"])):
        _fail("등록 profile의 종류, 식별자 또는 필드가 올바르지 않습니다.")
    if ("probability_output_planned" in profile
            and type(profile["probability_output_planned"]) is not bool):
        _fail("모델 확률 출력 계획은 운영자가 명시한 boolean이어야 합니다.")
    columns = profile["columns"]
    if not isinstance(columns, dict) or not columns:
        _fail("운영자가 등록한 컬럼 의미 사전이 필요합니다.")
    for name, entry in columns.items():
        if (not _text(name) or not isinstance(entry, dict)
                or set(entry) != {"semantic_roles", "description"}
                or not _text(entry["description"])):
            _fail("컬럼 이름, 설명 및 의미 역할을 등록해야 합니다.")
        roles = entry["semantic_roles"]
        if (not isinstance(roles, list)
                or any(not isinstance(role, str) or role not in ROLES for role in roles)
                or len(roles) != len(set(roles))
                or {"row_key", "patient_key"}.issubset(roles)):
            _fail("컬럼 의미 역할이 올바르지 않거나 환자 키와 입원 키가 혼동되었습니다.")
    if any(not any(role in entry["semantic_roles"] for entry in columns.values())
           for role in REQUIRED_ROLES):
        _fail("필수 역할의 운영자 등록 의미 근거가 없습니다.")
    splits, targets = profile["split_values"], profile["target_values"]
    if (not isinstance(splits, dict) or not splits
            or any(not _text(key) or not isinstance(value, str) or value not in prepared.SPLITS
                   for key, value in splits.items())
            or set(splits.values()) != set(prepared.SPLITS)):
        _fail("고정 세 분할에 대한 명시적 값 매핑이 필요합니다.")
    if (not isinstance(targets, dict) or not targets
            or any(not _text(key) or type(value) is not int or value not in (0, 1)
                   for key, value in targets.items())
            or set(targets.values()) != {0, 1}):
        _fail("정답에는 명시적인 0/1 이진 매핑이 필요합니다.")
    selection = profile["source_selection"]
    if (not _text(profile["target_definition"])
            or not (isinstance(selection, str) or (
                isinstance(selection, dict)
                and all(isinstance(k, str) and isinstance(v, str) for k, v in selection.items())))
            or not isinstance(profile["limitations"], list)
            or any(not _text(item) for item in profile["limitations"])):
        _fail("정답 정의, 출처 및 한계 설명을 등록해야 합니다.")
    if profile["bindings"] is not None:
        _validate_bindings(profile["bindings"], profile)


def load_profile(profile_id=None, *, manifest=None, registry=None):
    """이미지 manifest에서 입력 계약을 만듭니다. registry는 명시적 오프라인 검증용입니다."""
    if manifest is not None:
        if registry is not None or not isinstance(manifest, dict):
            _fail("이미지 manifest 하나만 입력 계약의 출처로 사용할 수 있습니다.")
        if manifest.get("kind") == "registered-prepared-input":
            _validate_registered_manifest(manifest)
            metadata = manifest["input_metadata"]
            kind = "registered_prepared_v1"
        elif manifest.get("kind") == "prepared-data-only":
            if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 2
                    or manifest.get("training_performed") is not False
                    or manifest.get("preprocessing_performed") is not False
                    or not isinstance(manifest.get("prepared_source"), dict)
                    or not isinstance(manifest.get("content_files"), dict)
                    or not _hash(manifest.get("raw_source_sha256"))
                    or not _hash(manifest.get("data_id"))
                    or prepared.data_identifier(manifest) != manifest["data_id"]):
                _fail("기존 준비 이미지 manifest의 내용 식별자가 올바르지 않습니다.")
            metadata = prepared.input_metadata(manifest["prepared_source"])
            kind = "legacy_prepared_v2"
        else:
            _fail("지원하는 준비 이미지 manifest와 명시적 입력 메타데이터가 필요합니다.")
        if (not isinstance(metadata, dict) or not _text(metadata.get("profile_id"))
                or "data_id" in metadata or metadata.get("source_kind") != kind):
            _fail("이미지 입력 메타데이터의 식별자 또는 종류가 올바르지 않습니다.")
        identifier = metadata["profile_id"]
        entry = {key: deepcopy(value) for key, value in metadata.items() if key != "profile_id"}
        entry["data_id"] = manifest["data_id"]
        registry = {"schema_version": 1, "active_profile": identifier, "profiles": {identifier: entry}}
    if registry is None:
        _fail("저장소 기본 profile 대신 선택한 GHCR 이미지 manifest가 필요합니다.")
    config = registry
    if (not isinstance(config, dict)
            or set(config) != {"schema_version", "active_profile", "profiles"}
            or type(config["schema_version"]) is not int or config["schema_version"] != 1
            or not isinstance(config["profiles"], dict)
            or not isinstance(config["active_profile"], str)
            or config["active_profile"] not in config["profiles"]):
        _fail("입력 profile 등록 설정의 버전 또는 활성 profile이 올바르지 않습니다.")
    identifier = config["active_profile"] if profile_id is None else profile_id
    if not isinstance(identifier, str) or identifier not in config["profiles"]:
        _fail("등록되지 않은 입력 profile입니다.")
    entry = config["profiles"][identifier]
    if (not isinstance(entry, dict) or not PROFILE_FIELDS.issubset(entry)
            or set(entry) - PROFILE_FIELDS - OPTIONAL_PROFILE_FIELDS):
        _fail("입력 profile에 필요한 등록 필드가 없거나 모르는 필드가 있습니다.")
    profile = {**deepcopy(entry), "profile_id": identifier}
    _validate_profile(profile)
    return profile


def profile_for_data(manifest):
    """시작 이미지의 내용에 묶인 계약만 선택하며 저장소의 데이터 목록은 조회하지 않습니다."""
    return load_profile(manifest=manifest)


def _profile(assets, value):
    registered = profile_for_data(read_manifest(assets))
    if value is None:
        return registered
    if isinstance(value, str) and value == registered["profile_id"]:
        return registered
    if not isinstance(value, dict) or value != registered:
        _fail("입력 계약은 선택한 GHCR 이미지의 메타데이터와 같아야 합니다.")
    return registered


def _safe_directory(directory):
    directory = Path(directory).absolute()
    if any(path.is_symlink() or getattr(path, "is_junction", lambda: False)()
           for path in (directory, *directory.parents)) or not directory.is_dir():
        _fail("자산 경로는 심볼릭 링크가 아닌 등록 준비 디렉터리여야 합니다.")
    return directory


def _safe_file(path):
    if (path.is_symlink() or not path.is_file() or path.stat().st_size <= 0
            or getattr(path, "is_junction", lambda: False)()):
        _fail("준비 자산 파일이 없거나 비어 있거나 안전하지 않은 경로입니다.")


def read_manifest(assets):
    """Read the selected image manifest with path and strict JSON checks."""
    directory = _safe_directory(assets)
    path = directory / "data_manifest.json"
    _safe_file(path)
    return _read_json(path)


def _validate_feature_roles(manifest, profile):
    features = manifest.get("feature_columns")
    if not isinstance(features, list) or any(not isinstance(name, str) for name in features):
        _fail("검증할 모델 입력 특성 목록이 올바르지 않습니다.")
    protected = {"row_key", "patient_key", "encounter_id", "patient_nbr", "split", "target"}
    protected.update(name for name, column in profile["columns"].items()
                     if set(column["semantic_roles"]).intersection(REQUIRED_ROLES))
    if protected.intersection(features):
        _fail("환자·행 식별자, 분할 또는 정답 역할 컬럼을 모델 입력 특성에 포함할 수 없습니다.")


def _manifest(directory, profile):
    for name in ("data_manifest.json", "metadata.csv", "db_features.csv"):
        _safe_file(directory / name)
    manifest = _read_json(directory / "data_manifest.json")
    if profile["source_kind"] == "legacy_prepared_v2":
        # 기존 공개 준비 자산의 모든 무결성 및 원본 계약 검증을 그대로 유지합니다.
        try:
            manifest = prepared.validate_data_manifest(directory)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, UnicodeError, csv.Error):
            _fail("기존 준비 자산의 manifest 또는 무결성 검증에 실패했습니다.")
    else:
        _validate_registered_manifest(manifest)
        files = manifest["files"]
        if {path.name for path in directory.iterdir()} != {*files, "data_manifest.json"}:
            _fail("등록 준비 자산에 허용되지 않은 파일이 있습니다.")
        for name, checksum in files.items():
            _safe_file(directory / name)
            if file_hash(directory / name) != checksum:
                _fail("준비 자산의 파일 해시가 등록 manifest와 다릅니다.")
    if manifest.get("data_id") != profile["data_id"]:
        _fail("자산 data_id가 운영자 등록 profile과 다릅니다.")
    _validate_feature_roles(manifest, profile)
    return manifest


def _validate_registered_manifest(manifest):
    if (not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["kind"] != "registered-prepared-input"
            or manifest["training_performed"] is not False
            or manifest["preprocessing_performed"] is not False
            or manifest["data_id"] != canonical_hash({
                key: value for key, value in manifest.items() if key != "data_id"})):
        _fail("등록 준비 자산 manifest의 버전 또는 내용 식별자가 올바르지 않습니다.")
    features, counts, files = manifest["feature_columns"], manifest["split_rows"], manifest["files"]
    if (not isinstance(features, list) or not features
            or any(not _text(name) for name in features)
            or len(features) != len(set(features))
            or not isinstance(counts, dict) or set(counts) != set(prepared.SPLITS)
            or any(type(value) is not int or value <= 0 for value in counts.values())
            or not isinstance(files, dict) or set(files) != {"metadata.csv", "db_features.csv"}
            or any(not _hash(value) for value in files.values())):
        _fail("등록 준비 자산의 특징, 고정 분할 또는 파일 명세가 올바르지 않습니다.")


def _data_type(kinds):
    if not kinds:
        return "unknown"
    if kinds == {"boolean"}:
        return "boolean"
    if kinds <= {"integer", "number"}:
        return "number" if "number" in kinds else "integer"
    return "string"


def _value_type(value):
    if value in ("true", "false"):
        return "boolean"
    if re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        return "integer"
    try:
        if math.isfinite(float(value)):
            return "number"
    except (ValueError, OverflowError):
        pass
    return "string"


def _metadata(directory, profile):
    records = []
    columns = profile["columns"]
    missing = Counter()
    kinds = {name: set() for name in columns}
    try:
        with (directory / "metadata.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            headers = reader.fieldnames
            if (headers is None or len(headers) != len(set(headers))
                    or set(headers) != set(columns)):
                _fail("metadata 헤더는 중복 없이 등록된 컬럼의 정확한 집합이어야 합니다.")
            for row in reader:
                prepared.valid_row(row, "입력 metadata")
                records.append(row)
                for name, value in row.items():
                    if not value.strip():
                        missing[name] += 1
                    else:
                        kinds[name].add(_value_type(value))
    except (OSError, UnicodeError, csv.Error, ValueError):
        _fail("metadata CSV를 안전하게 읽을 수 없습니다.")
    if not records:
        _fail("metadata에 준비된 행이 없습니다.")
    summaries = []
    for name in headers:
        entry = columns[name]
        identifier = bool({"row_key", "patient_key"}.intersection(entry["semantic_roles"]))
        summaries.append({
            "name": name, "description": entry["description"],
            "semantic_roles": list(entry["semantic_roles"]),
            "data_type": "string" if identifier else _data_type(kinds[name]),
            "missing_count": missing[name],
        })
    return records, summaries


def _feature_rows(directory, manifest):
    path = directory / "db_features.csv"
    _safe_file(path)
    if file_hash(path) != manifest["files"]["db_features.csv"]:
        _fail("특징 파일의 무결성이 변경되었습니다.")
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in prepared.csv_reader(handle, prepared.FEATURE_COLUMNS, "입력 특징"):
                prepared.valid_row(row, "입력 특징")
                values = _json(row["feature_values"])
                if (not isinstance(values, list) or len(values) != len(manifest["feature_columns"])
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in values)
                        or not row["row_key"].strip() or row["split"] not in prepared.SPLITS
                        or not re.fullmatch(r"[1-9][0-9]*", row["row_position"])):
                    _fail("표준 특징 행은 고정 분할과 유한 숫자 JSON 배열을 사용해야 합니다.")
                yield row
    except (OSError, UnicodeError, csv.Error, ValueError, OverflowError):
        _fail("특징 CSV 또는 숫자 JSON 배열이 올바르지 않습니다.")


def _schema_request(profile, summaries):
    candidates = {role: [entry["name"] for entry in summaries if role in entry["semantic_roles"]]
                  for role in ROLES}
    evidence = [f"column:{index}:{role}" for index, entry in enumerate(summaries)
                for role in entry["semantic_roles"]]
    request = {
        "schema_version": 1, "stage": "input_mapping", "profile_id": profile["profile_id"],
        "data_id": profile["data_id"], "columns": summaries,
        "required_roles": list(REQUIRED_ROLES), "candidate_bindings": candidates,
        "target_definition": profile["target_definition"],
        "split_values": deepcopy(profile["split_values"]),
        "target_values": deepcopy(profile["target_values"]),
        "allowed_evidence_ids": evidence, "known_mapping": profile["bindings"] is not None,
        "profile_hash": canonical_hash(profile),
    }
    request["context_hash"] = canonical_hash(request)
    return request


def _inspect(assets, profile):
    directory = _safe_directory(assets)
    manifest = _manifest(directory, profile)
    records, summaries = _metadata(directory, profile)
    counts = Counter(row["split"] for row in _feature_rows(directory, manifest))
    if dict(counts) != manifest["split_rows"]:
        _fail("특징 파일의 분할 행 수가 등록 manifest와 다릅니다.")
    request = _schema_request(profile, summaries)
    bundle = {"status": "needs_mapping", "schema_request": request,
              "manifest": deepcopy(manifest), "profile": deepcopy(profile)}
    return directory, records, bundle


def _ready(directory, records, bundle, bindings, provider, recommendation=None):
    profile, manifest, request = bundle["profile"], bundle["manifest"], bundle["schema_request"]
    _validate_bindings(bindings, profile)
    metadata = []
    seen = set()
    for row in records:
        item = {role: row[column] for role, column in bindings.items()}
        if any(not _text(item[role]) for role in ("row_key", "patient_key")):
            _fail("환자 키와 입원 키는 결측이 아닌 문자열이어야 합니다. 대체 ID는 만들지 않습니다.")
        if item["row_key"] in seen:
            _fail("입원 단위 row_key가 중복되었습니다.")
        seen.add(item["row_key"])
        if (item["split"] not in profile["split_values"]
                or item["target"] not in profile["target_values"]):
            _fail("분할 또는 정답 값에 명시적으로 등록된 변환이 없습니다.")
        item["split"] = profile["split_values"][item["split"]]
        item["target"] = profile["target_values"][item["target"]]
        if "race_was_missing" in item:
            if item["race_was_missing"] not in ("true", "false"):
                _fail("원본 인종 결측 플래그는 명시적인 true/false여야 합니다.")
            item["race_was_missing"] = item["race_was_missing"] == "true"
        for role in ("race", "gender", "age_group", "race_original"):
            if role in item and not item[role].strip():
                item[role] = None
        metadata.append(item)
    if dict(Counter(row["split"] for row in metadata)) != manifest["split_rows"]:
        _fail("metadata의 정규화 분할 행 수가 등록 manifest와 다릅니다.")
    binding = {
        "schema_version": 1, "provider": provider, "profile_id": profile["profile_id"],
        "data_id": profile["data_id"], "context_hash": request["context_hash"],
        "status": "validated", "bindings": deepcopy(bindings),
        "split_values": deepcopy(profile["split_values"]),
        "target_values": deepcopy(profile["target_values"]),
        "profile_snapshot": deepcopy(profile), "source_manifest": deepcopy(manifest),
        "profile_hash": canonical_hash(profile), "schema_request": deepcopy(request),
        "recommendation": deepcopy(recommendation),
    }
    context = planning_context(manifest, profile, binding, request)
    context.update(metadata=metadata, feature_rows=lambda: _feature_rows(directory, manifest),
                   asset_integrity_verified=True)
    return {**bundle, "status": "ready", "binding": binding, "context": context}


def planning_context(manifest, profile, binding, request):
    """Assemble row-free metadata from a previously validated input binding."""
    legacy = profile["source_kind"] == "legacy_prepared_v2"
    context_manifest = deepcopy(manifest)
    if not legacy:
        context_manifest["prepared_source"] = {
            "kind": "registered-prepared-input", "source_profile": profile["profile_id"],
            "profile_id": profile["profile_id"],
            "features": list(manifest["feature_columns"]),
            "target": "target",
            "splits": {split: {"rows": manifest["split_rows"][split]} for split in prepared.SPLITS},
            "target_mapping": deepcopy(profile["target_values"]),
            "limitations": list(profile["limitations"]),
            "probability_output_planned": profile.get("probability_output_planned") is True,
        }
    context = {
        "feature_columns": list(manifest["feature_columns"]),
        "expected_feature_columns": list(context_manifest["prepared_source"]["features"]),
        "target_mapping": deepcopy(context_manifest["prepared_source"]["target_mapping"]),
        "data_manifest": context_manifest,
        "input_contract": deepcopy(binding),
        "_input_schema_request": deepcopy(request),
        "dataset_context": {
            "id": profile["profile_id"], "profile_id": profile["profile_id"],
            "data_id": profile["data_id"], "target_definition": profile["target_definition"],
            "source_selection": deepcopy(profile["source_selection"]),
            "limitations": list(profile["limitations"]), "legacy": legacy,
        },
    }
    return context


def inspect_inputs(assets: Path, *, profile=None):
    """미확정 입력은 행 없는 schema_request만, 확정 입력은 표준 문맥도 반환합니다."""
    registered = _profile(assets, profile)
    directory, records, bundle = _inspect(assets, registered)
    if registered["bindings"] is None:
        return bundle
    return _ready(directory, records, bundle, registered["bindings"], "registered-config")


def apply_mapping(assets: Path, recommendation: dict, *, profile=None):
    """현재 요청 해시 및 역할별 등록 근거에 묶인 AI 추천만 검증 후 적용합니다."""
    registered = _profile(assets, profile)
    if registered["bindings"] is not None:
        _fail("설정에서 확정된 매핑은 AI로 덮어쓸 수 없습니다.")
    directory, records, bundle = _inspect(assets, registered)
    _validate_recommendation(recommendation, registered, bundle["schema_request"])
    return _ready(directory, records, bundle, recommendation["bindings"], PROVIDER, recommendation)


def _validate_recommendation(recommendation, profile, request):
    fields = {"schema_version", "provider", "context_hash", "status", "bindings", "reasons", "evidence"}
    if (not isinstance(recommendation, dict) or set(recommendation) != fields
            or type(recommendation["schema_version"]) is not int
            or recommendation["schema_version"] != 1
            or recommendation["provider"] != PROVIDER or recommendation["status"] != "provided"):
        _fail("입력 매핑 추천의 필드, 버전, 제공자 또는 상태가 올바르지 않습니다.")
    if recommendation["context_hash"] != request["context_hash"]:
        _fail("입력 매핑 추천이 현재 공개 스키마 요청 해시와 다릅니다.")
    bindings, reasons, evidence = (
        recommendation["bindings"], recommendation["reasons"], recommendation["evidence"])
    _validate_bindings(bindings, profile)
    if (not isinstance(reasons, dict) or set(reasons) != set(bindings)
            or any(not _text(reason) or re.search(r"[가-힣]", reason) is None
                   for reason in reasons.values())):
        _fail("모든 매핑 역할에 한국어 근거가 필요합니다.")
    if (not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence)
            or len(evidence) != len(set(evidence))
            or not set(evidence).issubset(request["allowed_evidence_ids"])):
        _fail("추천은 현재 요청에 허용된 근거 ID만 인용해야 합니다.")
    indexes = {entry["name"]: index for index, entry in enumerate(request["columns"])}
    if any(f"column:{indexes[column]}:{role}" not in evidence for role, column in bindings.items()):
        _fail("선택한 각 컬럼과 역할을 직접 뒷받침하는 등록 근거가 필요합니다.")
