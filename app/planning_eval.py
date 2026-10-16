"""Evaluation planning: policy defaults + Copilot suggestions + human choices.

Catalogs describe executable blocks but this module never imports or runs them.
The GitHub Agentic Workflows Copilot engine reads the request and automatically
writes a recommendation file. This planning engine validates that file; it neither
calls an LLM API nor authenticates authorship.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
_STAGES = {"eda": ("checks", "checks", "eda-catalog.json"),
           "evaluation": ("metrics", "metrics", "metric-catalog.json")}
_PROVIDER = "github-copilot-cloud-agent"
_PARAMETERS = {"threshold", "thresholds", "risk_thresholds", "top_fraction", "n_bins"}
_INPUTS = {"y_true", "y_probability", "reference_probabilities",
           "sample_ids", "reference_sample_ids"}
_CONTEXT = {"clinical_action", "comparison_model"}
_STATUSES = {"passed", "failed", "unverifiable", "completed"}


def _object(value, label, allowed=None, required=()):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    if allowed is not None and set(value) - set(allowed):
        raise ValueError(f"{label} contains unsupported fields")
    if set(required) - set(value):
        raise ValueError(f"{label} is missing required fields")
    return value


def _version(value, label):
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError(f"{label} requires schema_version 1")


def _token(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty identifier")
    token = re.sub(r"[\s-]+", "_", value.strip().lower())
    if not re.fullmatch(r"[a-z][a-z0-9_]*", token):
        raise ValueError(f"{label} is not a valid identifier")
    return token


def _tokens(values, label):
    if not isinstance(values, list):
        raise ValueError(f"{label} must be an array")
    return list(dict.fromkeys(_token(value, label) for value in values))


def _read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON contains duplicate object keys")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot load registered planning configuration: {path.name}") from error


def _catalog(catalog, stage):
    if stage not in _STAGES:
        raise ValueError("Unknown planning stage")
    collection, directory, _ = _STAGES[stage]
    _object(catalog, f"{stage} catalog", {"schema_version", "stage", collection},
            {"schema_version", "stage", collection})
    _version(catalog, f"{stage} catalog")
    if catalog["stage"] != stage or not isinstance(catalog[collection], list):
        raise ValueError(f"{stage} catalog has an invalid stage or block collection")
    result = deepcopy(catalog)
    ids, aliases = set(), {}
    fields = {"id", "name", "description", "module", "aliases", "requires",
              "requires_parameters", "requires_context"}
    for entry in result[collection]:
        extra = {"input_contract", "output_contract"} if stage == "eda" else set()
        _object(entry, f"{stage} catalog entry", fields | extra, fields)
        identifier = _token(entry["id"], "catalog id")
        if identifier != entry["id"] or identifier in ids:
            raise ValueError("Catalog ids must be unique canonical identifiers")
        ids.add(identifier)
        for field in ("name", "description"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"Catalog {field} must be nonempty text")
        expected = f"app.blocks.{directory}.{identifier}"
        path = ROOT / "app" / "blocks" / directory / f"{identifier}.py"
        if entry["module"] != expected or not path.is_file():
            raise ValueError(f"Unregistered or missing implementation for {stage} block {identifier}")
        entry["aliases"] = [alias for alias in _tokens(entry["aliases"], "aliases")
                            if alias != identifier]
        for field in ("requires", "requires_parameters", "requires_context"):
            entry[field] = _tokens(entry[field], field)
        if stage == "eda" and extra.intersection(entry):
            if not extra.issubset(entry):
                raise ValueError("EDA input and output contracts must be declared together")
            contract = _object(entry["input_contract"], "EDA input contract",
                               {"metadata_roles", "optional_context"},
                               {"metadata_roles", "optional_context"})
            for field in ("metadata_roles", "optional_context"):
                contract[field] = _tokens(contract[field], field)
            if set(contract["metadata_roles"]) - {
                    "row_key", "patient_key", "split", "target", "race", "gender",
                    "age_group", "race_original", "race_was_missing"}:
                raise ValueError("EDA contract contains an unknown metadata role")
            if set(contract["optional_context"]).intersection(entry["requires"]):
                raise ValueError("An EDA input cannot be both required and optional")
            output = _object(entry["output_contract"], "EDA output contract",
                             {"fields", "statuses"}, {"fields", "statuses"})
            if (output["fields"] != ["block", "status", "details"]
                    or output["statuses"] != ["passed", "failed", "unverifiable", "completed"]):
                raise ValueError("EDA output must preserve the aggregate diagnostic contract")
        if set(entry["requires_parameters"]) - _PARAMETERS:
            raise ValueError("Catalog contains unsupported parameter requirements")
        if set(entry["requires_context"]) - _CONTEXT:
            raise ValueError("Catalog contains unsupported context requirements")
        if stage == "evaluation" and set(entry["requires"]) - _INPUTS:
            raise ValueError("Evaluation catalog contains unsupported input requirements")
        for alias in [identifier, *entry["aliases"]]:
            if alias in aliases and aliases[alias] != identifier:
                raise ValueError("Catalog has an ambiguous alias")
            aliases[alias] = identifier
    if not ids:
        raise ValueError("Catalog must register at least one block")
    return result


def load_catalog(stage):
    """Read a stage catalog and verify fixed, existing implementation paths."""
    if stage not in _STAGES:
        raise ValueError("Unknown planning stage")
    return _catalog(_read(ROOT / "config" / _STAGES[stage][2]), stage)


def _policy(policy):
    _object(policy, "planning policy", {"schema_version", *_STAGES},
            {"schema_version", *_STAGES})
    _version(policy, "planning policy")
    result = deepcopy(policy)
    for stage in _STAGES:
        entry = _object(result[stage], f"{stage} policy",
                        {"required", "defaults", "objectives", "allowed_objectives"},
                        {"required", "defaults", "objectives", "allowed_objectives"})
        for field in entry:
            entry[field] = _tokens(entry[field], f"{stage} policy {field}")
        if set(entry["objectives"]) - set(entry["allowed_objectives"]):
            raise ValueError("Default objectives must belong to allowed_objectives")
    return result


def load_policy():
    """Read selection policy independently of model and block imports."""
    return _policy(_read(ROOT / "config" / "planning-policy.json"))


def _default_selection(policy):
    return {
        "schema_version": 1,
        "eda": {"objectives": list(policy["eda"]["objectives"]), "add": [], "exclude": []},
        "evaluation": {
            "objectives": list(policy["evaluation"]["objectives"]), "add": [], "exclude": [],
            "primary_metric": None, "threshold": None, "parameters": {},
            "clinical_action": None, "comparison_model": None,
        },
    }


def default_selection():
    """Return a fresh, editable selection; defaults remain a separate policy."""
    return _default_selection(load_policy())


def approval_readiness(plans):
    """Distinguish assembled recommendations from a human-approvable demo plan."""
    evaluation = plans["evaluation"]
    reasons = []
    if any(plans[stage]["ai_status"] != "provided"
           or plans[stage]["planning_complete"] is not True for stage in _STAGES):
        reasons.append("AI pending/stale 또는 planning_complete 미완료입니다.")
    primary = evaluation["primary_metric"]
    if not primary or primary not in evaluation["selected_ids"]:
        reasons.append("선택 목록에 포함된 명시적인 primary_metric이 필요합니다.")
    blocked = [item["id"] for item in evaluation["items"] if item["state"] == "blocked"]
    if blocked:
        reasons.append("blocked 평가 항목: " + ", ".join(blocked))
    return {"ready": not reasons, "reasons": reasons,
            "approval_scope": "educational_demo_only", "clinical_use_approved": False}


def _aliases(catalog, stage):
    result = {}
    for item in catalog[_STAGES[stage][0]]:
        for name in [item["id"], *item["aliases"]]:
            result[name] = item["id"]
    return result


def _ids(values, aliases, label):
    result = []
    for name in _tokens(values, label):
        if name not in aliases:
            raise ValueError(f"Unknown {label} id: {name}")
        if aliases[name] not in result:
            result.append(aliases[name])
    return result


def _probability(value, label, *, interior=False):
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not finite:
        raise ValueError(f"{label} must be a finite number")
    if not (0 < value < 1 if interior else 0 <= value <= 1):
        raise ValueError(f"{label} is outside its probability range")
    return value


def _parameters(parameters, threshold):
    _object(parameters, "evaluation parameters")
    result = {}
    for key, value in parameters.items():
        name = _token(key, "parameter")
        if name not in _PARAMETERS or name in result:
            raise ValueError("Unknown or duplicate evaluation parameter")
        if name in ("thresholds", "risk_thresholds"):
            if not isinstance(value, list) or not value:
                raise ValueError(f"{name} must be a nonempty array")
            values = [_probability(item, name, interior=True) for item in value]
            if any(left >= right for left, right in zip(values, values[1:])):
                raise ValueError(f"{name} must be strictly increasing without duplicates")
            result[name] = values
        elif name == "n_bins":
            if type(value) is not int or value < 1:
                raise ValueError("n_bins must be a positive integer")
            result[name] = value
        else:
            result[name] = _probability(value, name)
            if name == "top_fraction" and value == 0:
                raise ValueError("top_fraction must be greater than zero")
    if threshold is not None:
        if "threshold" in result:
            raise ValueError("Duplicate threshold: use either threshold or parameters.threshold")
        result["threshold"] = _probability(threshold, "threshold")
    return result


def _selection(selection, policy, catalogs):
    _object(selection, "selection", {"schema_version", *_STAGES})
    if "schema_version" in selection:
        _version(selection, "selection")
    result = _default_selection(policy)
    for stage in _STAGES:
        allowed = set(result[stage])
        supplied = _object(selection.get(stage, {}), f"{stage} selection", allowed)
        result[stage].update(deepcopy(supplied))
        current = result[stage]
        current["objectives"] = _tokens(current["objectives"], f"{stage} objectives")
        if set(current["objectives"]) - set(policy[stage]["allowed_objectives"]):
            raise ValueError(f"Unknown {stage} objective")
        aliases = _aliases(catalogs[stage], stage)
        for field in ("add", "exclude"):
            current[field] = _ids(current[field], aliases, f"{stage} {field}")
        required = _ids(policy[stage]["required"], aliases, f"{stage} required")
        _ids(policy[stage]["defaults"], aliases, f"{stage} default")
        if set(required) & set(current["exclude"]):
            raise ValueError(f"Cannot exclude required {stage} blocks")
    current = result["evaluation"]
    for field in ("clinical_action", "comparison_model"):
        value = current[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{field} must be nonempty text or null")
    current["parameters"] = _parameters(current["parameters"], current["threshold"])
    current["threshold"] = current["parameters"].get("threshold")
    if current["primary_metric"] is not None:
        current["primary_metric"] = _ids([current["primary_metric"]],
                                       _aliases(catalogs["evaluation"], "evaluation"),
                                       "evaluation primary metric")[0]
    return result


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _status(value):
    return value if isinstance(value, str) and value in _STATUSES else "unverifiable"


def _planned_inputs(manifest, selection):
    source = manifest.get("prepared_source", {})
    source = source if isinstance(source, dict) else {}
    mapping = source.get("target_mapping")
    labels = (isinstance(source.get("target"), str) and bool(source["target"])
              and isinstance(mapping, dict) and bool(mapping)
              and all(type(value) is int and value in (0, 1) for value in mapping.values())
              and set(mapping.values()) == {0, 1})
    features = manifest.get("feature_columns")
    model = source.get("model", {})
    model = model if isinstance(model, dict) else {}
    checkpoint = model.get("checkpoint", {})
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    probability = (
        isinstance(features, list) and bool(features) and features == source.get("features")
        and model.get("classes") == [0, 1] and model.get("positive_class") == 1
        and isinstance(model.get("local_validation_scikit_learn_version"), str)
        and bool(model["local_validation_scikit_learn_version"])
        and isinstance(checkpoint.get("sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", checkpoint["sha256"]) is not None
    )
    if "source_profile" in source:
        probability = (isinstance(features, list) and bool(features)
                       and features == source.get("features")
                       and source.get("probability_output_planned") is True)
    files = manifest.get("files", {})
    aligned_ids = isinstance(files, dict) and "metadata.csv" in files
    comparison = selection["evaluation"]["comparison_model"] is not None
    return {"y_true": labels, "y_probability": bool(probability),
            "sample_ids": aligned_ids, "reference_sample_ids": aligned_ids and comparison,
            "reference_probabilities": bool(probability) and comparison}


def _request(eda, manifest, selection, catalogs):
    # Only these two diagnostics are stable prerequisites. Optional execution
    # results and user additions/exclusions must not invalidate recommendations.
    facts = {}
    data_id = manifest.get("data_id")
    if data_id is not None and (
            not isinstance(data_id, str) or re.fullmatch(r"[0-9a-f]{64}", data_id) is None):
        raise ValueError("Public data_id must be a SHA256 content identifier")
    if eda.get("data_id") is not None and eda["data_id"] != data_id:
        raise ValueError("Required EDA facts and data manifest refer to different data_id values")
    facts["data_id"] = data_id
    if "input_mapping_hash" in eda:
        mapping_hash = eda["input_mapping_hash"]
        if not isinstance(mapping_hash, str) or re.fullmatch(r"[0-9a-f]{64}", mapping_hash) is None:
            raise ValueError("Input mapping must have a SHA256 identifier.")
        facts["input_mapping_hash"] = mapping_hash
    for name in ("coordination_hash", "research_request_hash", "initial_execution_plan_hash"):
        if name in eda:
            if not isinstance(eda[name], str) or re.fullmatch(r"[0-9a-f]{64}", eda[name]) is None:
                raise ValueError("Research coordination must have a SHA256 identifier.")
            facts[name] = eda[name]
    evidence = eda.get("coordination_evidence")
    if evidence is not None:
        if not isinstance(evidence, dict) or set(evidence) - {
                item["id"] for item in catalogs["eda"]["checks"]}:
            raise ValueError("Unregistered coordinated EDA evidence.")
        for identifier, details in evidence.items():
            if identifier not in ("schema", "patient_overlap"):
                facts[f"coordinated.{identifier}"] = deepcopy(details)
    source = manifest.get("prepared_source", {})
    source = source if isinstance(source, dict) else {}
    target = source.get("target")
    facts["target"] = target if isinstance(target, str) and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,63}", target) else None
    splits = manifest.get("split_rows", {})
    splits = splits if isinstance(splits, dict) else {}
    facts["split_counts"] = {key: _count(splits.get(key))
                             for key in ("train", "validation", "test")}
    results = eda.get("results", [])
    if not isinstance(results, list):
        raise ValueError("EDA results must be an array")
    required_results = {}
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("EDA result entries must be objects")
        identifier = item.get("block")
        if identifier in ("schema", "patient_overlap"):
            if identifier in required_results:
                raise ValueError("Duplicate required EDA results")
            required_results[identifier] = item
    for identifier in ("schema", "patient_overlap"):
        item = required_results.get(identifier, {})
        facts[f"required.{identifier}.status"] = _status(item.get("status"))
        details = item.get("details", {})
        details = details if isinstance(details, dict) else {}
        if identifier == "schema":
            facts["required.schema.numeric_contract"] = _status(details.get("numeric_contract"))
            for key in ("actual_count", "expected_count", "rows", "duplicate_columns"):
                facts[f"required.schema.{key}"] = _count(details.get(key))
            facts["required.schema.schema_match"] = (
                details.get("schema_match") if type(details.get("schema_match")) is bool else None)
        else:
            overlaps = details.get("overlaps", {})
            overlaps = overlaps if isinstance(overlaps, dict) else {}
            facts["required.patient_overlap.overlaps"] = {
                key: _count(overlaps.get(key))
                for key in ("train_validation", "train_test", "validation_test")}
    for stage in _STAGES:
        facts[f"objectives.{stage}"] = selection[stage]["objectives"]
    for key in ("clinical_action", "comparison_model"):
        facts[f"{key}_defined"] = selection["evaluation"][key] is not None
    for key in sorted(_PARAMETERS):
        facts[f"parameters.{key}_defined"] = key in selection["evaluation"]["parameters"]
    planned = _planned_inputs(manifest, selection)
    facts["labels_planned"] = planned["y_true"]
    facts["probability_output_planned"] = planned["y_probability"]
    context = {
        "facts": facts, "catalogs": deepcopy(catalogs),
        "catalog_hashes": {stage: _hash(catalogs[stage]) for stage in _STAGES},
    }
    return {
        "schema_version": 1, "provider": _PROVIDER, "context_hash": _hash(context),
        "context": context, "allowed_evidence_ids": list(facts),
        "instructions": (
            "GitHub Agentic Workflows의 Copilot cloud agent로서 이 공개 집계와 정형 목적만 "
            "읽고, 단계별 카탈로그에 이미 구현된 블록 ID만 추천하세요. 추천 이유는 한국어로 "
            "작성하고 워크플로가 지정한 JSON 파일에 저장하세요. schema_version=1, "
            "provider=github-copilot-cloud-agent, 현재 context_hash, status=provided 및 "
            "eda와 evaluation 배열을 모두 포함하세요. 각 항목에는 id, 비어 있지 않은 reason, "
            "allowed_evidence_ids를 참조하는 evidence만 넣으세요. 한 단계의 빈 배열은 "
            "가능하지만 전체에서 최소 한 개의 근거 있는 추천이 필요합니다. 빈 placeholder는 "
            "AI 작업 완료가 아닙니다. 코드·주지표 확정·임계값·매개변수·환자 자료·임상 사용 "
            "승인을 생성하지 마세요. EDA의 근거 부족은 확인 불가를 보고할 사유이지 해당 "
            "검사를 생략할 사유가 아닙니다. 확률 출력은 이후 생성할 계획 입력이며 현재 "
            "계산한 결과가 아닙니다. 계산 가능성과 독립 임상 검증을 구분하세요. 클라우드 "
            "에이전트가 추천 파일을 자동 작성하므로 사용자에게 채팅 결과를 붙여넣으라고 "
            "요청하지 마세요. 계획 엔진은 파일을 검증·조합하며 LLM API를 호출하거나 "
            "실제 작성자의 신원을 보증하지 않습니다."
        ),
        "validation_scope": ["registered_stage_ids", "context_hash", "public_evidence_ids",
                             "recommendation_schema"],
        "authorship_verified": False,
    }


def _recommendations(ai, request, catalogs):
    if ai is None:
        return "pending", {stage: {} for stage in _STAGES}
    fields = {"schema_version", "provider", "context_hash", "status", *_STAGES}
    _object(ai, "AI recommendation", fields, fields)
    _version(ai, "AI recommendation")
    if ai["provider"] != _PROVIDER or ai["status"] != "provided":
        raise ValueError("Only provided github-copilot-cloud-agent recommendations are supported")
    if (not isinstance(ai["context_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", ai["context_hash"]) is None):
        raise ValueError("AI context_hash must be a SHA256 digest")
    result = {}
    allowed = set(request["allowed_evidence_ids"])
    for stage in _STAGES:
        items = ai[stage]
        if not isinstance(items, list):
            raise ValueError(f"AI {stage} recommendations must be an array")
        aliases = _aliases(catalogs[stage], stage)
        result[stage] = {}
        for item in items:
            _object(item, f"AI {stage} recommendation", {"id", "reason", "evidence"},
                    {"id", "reason", "evidence"})
            identifier = _ids([item["id"]], aliases, f"AI {stage}")[0]
            if not isinstance(item["reason"], str) or not item["reason"].strip():
                raise ValueError("AI recommendation requires a nonempty reason")
            evidence = item["evidence"]
            if (not isinstance(evidence, list) or not evidence
                    or any(not isinstance(key, str) or key not in allowed for key in evidence)):
                raise ValueError("AI recommendation evidence must reference allowed public fact IDs")
            entry = result[stage].setdefault(identifier, {"reasons": [], "evidence": []})
            reason = item["reason"].strip()
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
            entry["evidence"] = list(dict.fromkeys([*entry["evidence"], *evidence]))
    if not any(result.values()):
        raise ValueError("An empty AI recommendation placeholder is not completed AI work")
    if ai["context_hash"] != request["context_hash"]:
        return "stale", {stage: {} for stage in _STAGES}
    return "provided", result


def _plan(stage, selection, catalog, policy, recommended, ai_status, planned):
    aliases = _aliases(catalog, stage)
    required = _ids(policy["required"], aliases, f"{stage} required")
    defaults = _ids([*required, *policy["defaults"]], aliases, f"{stage} default")
    excluded = selection["exclude"]
    sources = {}
    for source, identifiers in (("default", defaults), ("ai", recommended),
                                ("user", selection["add"])):
        for identifier in identifiers:
            sources.setdefault(identifier, []).append(source)
    selected = [identifier for identifier in sources if identifier not in excluded]
    indexed = {item["id"]: item for item in catalog[_STAGES[stage][0]]}
    items = []
    for identifier in selected:
        entry = indexed[identifier]
        reasons = []
        if "default" in sources[identifier]:
            reasons.append("설정된 기본 정책에 포함된 블록입니다.")
        if identifier in recommended:
            reasons.extend(recommended[identifier]["reasons"])
        if "user" in sources[identifier]:
            reasons.append("사용자가 추가로 선택한 블록입니다.")
        missing = []
        if stage == "evaluation":
            missing.extend(f"계획된 입력이 부족합니다: {name}." for name in entry["requires"]
                           if not planned.get(name, False))
            missing.extend(f"확정된 매개변수가 없습니다: {name}."
                           for name in entry["requires_parameters"]
                           if name not in selection["parameters"])
            missing.extend(f"정의된 적용 조건이 없습니다: {name}."
                           for name in entry["requires_context"]
                           if selection.get(name) is None)
            state = "blocked" if missing else "eligible_planned_inputs"
            if not missing:
                reasons.append("입력은 이후 생성할 계획이며 아직 계산하지 않았습니다. "
                               "실제 지표 실행 시 행 정합과 수학적 요건을 다시 검증합니다.")
        else:
            state = "selected_for_diagnosis"
            reasons.append("자료나 근거가 부족하면 진단 블록이 확인 불가로 보고합니다.")
        items.append({
            "id": identifier, "sources": sources[identifier], "state": state,
            "reason": " ".join([*reasons, *missing]), "blocked_reasons": missing,
            "requires": list(entry["requires"]),
            "requires_parameters": list(entry["requires_parameters"]),
            "requires_context": list(entry["requires_context"]),
            "required": identifier in required,
            "evidence": recommended.get(identifier, {}).get("evidence", []),
        })
    plan = {
        "schema_version": 1, "stage": stage, "selected_ids": selected, "items": items,
        "defaults": defaults, "required_ids": required,
        "ai_recommended_ids": list(recommended), "user_added_ids": selection["add"],
        "user_excluded_ids": excluded, "objectives": selection["objectives"],
        "ai_status": ai_status, "approval_required": True,
        "planning_complete": ai_status == "provided",
        "draft": ai_status != "provided",
        "clinical_use_approved": False,
    }
    if stage == "evaluation":
        primary = selection["primary_metric"]
        if primary is not None and primary not in selected:
            raise ValueError("primary_metric must belong to the selected evaluation metrics")
        plan.update({
            "primary_metric": primary, "threshold": selection["threshold"],
            "parameters": selection["parameters"], "clinical_action": selection["clinical_action"],
            "comparison_model": selection["comparison_model"], "planned_inputs": planned,
            "interpretation": "계산 계획은 임상 사용 승인이나 독립 성능 검증이 아닙니다. "
                              "환자 중복과 validation 재사용의 설계 한계를 별도로 유지합니다.",
        })
    return plan


def build_plans(eda, data_manifest, selection, ai, *, catalogs=None, policy=None):
    """Build both stage drafts without running EDA, inference or metric code.

    ``catalogs`` and ``policy`` may be trusted default-branch snapshots. Parameters
    are a flat, data-only evaluation dictionary. A top-level threshold and a
    parameters.threshold must not both be supplied. AI has no parameter authority.
    """
    _object(eda, "EDA report")
    _object(data_manifest, "data manifest")
    if catalogs is None:
        catalogs = {stage: load_catalog(stage) for stage in _STAGES}
    else:
        _object(catalogs, "catalogs", set(_STAGES), set(_STAGES))
        catalogs = {stage: _catalog(catalogs[stage], stage) for stage in _STAGES}
    policy = load_policy() if policy is None else _policy(policy)
    selection = _selection(selection, policy, catalogs)
    request = _request(eda, data_manifest, selection, catalogs)
    ai_status, recommendations = _recommendations(ai, request, catalogs)
    planned = _planned_inputs(data_manifest, selection)
    result = {stage: _plan(stage, selection[stage], catalogs[stage], policy[stage],
                           recommendations[stage], ai_status, planned)
              for stage in _STAGES}
    source = data_manifest.get("prepared_source", {})
    if source.get("dataset") != "auphong2707/hospital-readmission-risk-data":
        result["evaluation"]["interpretation"] = (
            "계산 계획은 임상 사용 승인이나 독립 성능 검증이 아닙니다. "
            "이 데이터셋에서 확인한 관측·누수·전처리 이력 제한만 적용합니다.")
    result["recommendation_request"] = request
    return result
