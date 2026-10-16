"""Pure PR1 research-intent coordination; no AI, rows, models or block execution.

``build_context`` is the transient input for the real Copilot agent. Its
``request_hash`` hashes exactly the four public form text fields, not the whole
Issue request. ``feedback_hash`` hashes the sorted, validated pending records
(including their bodies); only its digest and comment IDs enter the audit record.
The caller must authenticate comment authors and retrieve authoritative bodies.
``schema_hash`` covers the entire public schema/execution-contract dictionary,
including feature order and fixed model references. Its caller validates the
input-specific schema; this module does not impose input_contracts' closed keys.

``apply_proposal`` accepts agent-produced JSON, never manufactures suggestions.
AI EDA additions stay separate from human ``selection.eda.add``. Record objectives
are the final union, not a replacement of human objectives. Coordination hashes
bind the record, registered catalogs and policy without retaining source prose.
Hashes provide source consistency, not proof of agent or comment authenticity.
"""

from copy import deepcopy
import hashlib
import math
import re

from app import planning
from app.asset_contract import canonical_hash


PROVIDER = "github-copilot-cloud-agent"
_STAGES = ("eda", "evaluation")
_FORM_FIELDS = ("research_goal", "research_question", "cohort_requirements",
                "additional_requests")
_PROTECTED_EDA = ("schema", "patient_overlap")
_NONPUBLIC_SCHEMA_KEYS = {
    "raw_metadata", "raw_rows", "metadata_rows", "patient_rows", "patient_records",
    "row_samples", "sample_rows", "samples", "examples", "feature_rows", "feature_values",
    "environment", "environ", "env", "credentials", "credential", "password",
    "api_key", "access_token", "private_key", "token", "patient_name", "patient_full_name",
    "환자명", "성명",
}
_PROPOSAL_FIELDS = {
    "schema_version", "context_hash", "eda_add", "objectives", "feedback_ids",
    "selection_updates", "reason",
}
_RECORD_FIELDS = {
    "schema_version", "provider", "context_hash", "request_hash", "base_selection_hash",
    "selection_hash", "schema_hash", "feedback_hash", "feedback_ids", "eda_add",
    "objectives", "reason", "coordination_hash",
}
_SENSITIVE = re.compile(
    r"-----BEGIN [A-Z0-9 -]+-----"
    r"|\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b"
    r"|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    r"|(?:환자\s*(?:성명|이름|명)|성명|patient[\s_-]*(?:name|full[\s_-]*name))"
    r"\s*[:=：]\s*\S+"
    r"|(?:api[\s_-]*key|access[\s_-]*token|authorization|password|비밀번호"
    r"|client[\s_-]*secret)\s*[:=：]\s*\S+",
    re.IGNORECASE,
)


def _object(value, label, allowed, required=()):
    return planning._object(value, label, allowed, required)


def _digest(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a canonical SHA256 digest")
    return value


def validate_feedback_state(ledger, applied, repository, number, issue):
    """Validate body-free feedback provenance before queuing, AI use or approval."""
    references = []
    if ledger is not None:
        fields = {"schema_version", "kind", "repository", "pull_request", "issue_number", "comments"}
        _object(ledger, "feedback ledger", fields, fields)
        if (type(ledger["schema_version"]) is not int or ledger["schema_version"] != 1
                or ledger["kind"] != "pr1-natural-feedback"
                or ledger["repository"] != repository
                or type(ledger["pull_request"]) is not int or ledger["pull_request"] != number
                or type(ledger["issue_number"]) is not int or ledger["issue_number"] != issue
                or not isinstance(ledger["comments"], list)):
            raise ValueError("자연어 피드백 전달 기록의 출처가 올바르지 않습니다.")
        references = ledger["comments"]
        identifiers = []
        for reference in references:
            required = {"comment_id", "actor", "body_sha256"}
            _object(reference, "feedback reference", required | {"superseded_hash"}, required)
            if (type(reference["comment_id"]) is not int or reference["comment_id"] <= 0
                    or not isinstance(reference["actor"], str)
                    or re.fullmatch(r"[A-Za-z0-9-]{1,39}", reference["actor"]) is None):
                raise ValueError("피드백 ID 또는 작성자가 올바르지 않습니다.")
            _digest(reference["body_sha256"], "feedback body")
            if "superseded_hash" in reference:
                _digest(reference["superseded_hash"], "superseded feedback body")
            identifiers.append(reference["comment_id"])
        if identifiers != sorted(set(identifiers)):
            raise ValueError("피드백 ID는 중복 없이 정렬되어야 합니다.")
    applied_ids = []
    if applied is not None:
        fields = {"schema_version", "repository", "pull_request", "comment_ids", "coordination_hash"}
        _object(applied, "applied feedback", fields, fields)
        applied_ids = applied["comment_ids"]
        if (type(applied["schema_version"]) is not int or applied["schema_version"] != 1
                or applied["repository"] != repository
                or type(applied["pull_request"]) is not int or applied["pull_request"] != number
                or not isinstance(applied_ids, list)
                or any(type(item) is not int or item <= 0 for item in applied_ids)):
            raise ValueError("이미 반영한 피드백 기록의 출처가 올바르지 않습니다.")
        _digest(applied["coordination_hash"], "applied coordination")
        if (applied_ids != sorted(set(applied_ids))
                or not set(applied_ids).issubset(item["comment_id"] for item in references)):
            raise ValueError("반영한 피드백 ID와 원본 전달 기록이 일치하지 않습니다.")
    return references, applied_ids


def _public_json(value):
    """Reject rather than redact unsafe input; never include rejected text in errors."""
    length = 0

    def visit(item, depth=0):
        nonlocal length
        if depth > 24:
            raise ValueError("Public coordination input is nested too deeply")
        if isinstance(item, str):
            length += len(item)
            if length > 65536 or "\x00" in item or _SENSITIVE.search(item):
                raise ValueError("Public coordination input contains oversized or sensitive text")
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise ValueError("Public coordination input must be valid UTF-8 text") from None
        elif isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("Public coordination objects require string keys")
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is None or type(item) in (bool, int):
            return
        elif type(item) is float and math.isfinite(item):
            return
        else:
            raise ValueError("Public coordination input must contain finite JSON values")

    visit(value)


def _configuration(catalogs, policy):
    if catalogs is None:
        catalogs = {stage: planning.load_catalog(stage) for stage in _STAGES}
    else:
        _object(catalogs, "catalogs", _STAGES, _STAGES)
        catalogs = {stage: planning._catalog(catalogs[stage], stage) for stage in _STAGES}
    policy = planning.load_policy() if policy is None else planning._policy(policy)
    for stage in _STAGES:
        aliases = planning._aliases(catalogs[stage], stage)
        for field in ("required", "defaults"):
            planning._ids(policy[stage][field], aliases, f"{stage} {field}")
    planning._ids(list(_PROTECTED_EDA), planning._aliases(catalogs["eda"], "eda"),
                  "protected EDA")
    _public_json({"catalogs": catalogs, "policy": policy})
    return catalogs, policy


def _selection(selection, catalogs, policy):
    normalized = planning._selection(selection, policy, catalogs)
    evaluation = normalized["evaluation"]
    evaluation["parameters"].pop("threshold", None)
    if set(_PROTECTED_EDA).intersection(normalized["eda"]["exclude"]):
        raise ValueError("Required schema and patient_overlap EDA cannot be excluded")
    primary = evaluation["primary_metric"]
    if primary is not None:
        aliases = planning._aliases(catalogs["evaluation"], "evaluation")
        defaults = planning._ids(
            [*policy["evaluation"]["required"], *policy["evaluation"]["defaults"]],
            aliases, "evaluation defaults")
        if primary in evaluation["exclude"] or primary not in [*defaults, *evaluation["add"]]:
            raise ValueError("primary_metric must belong to the selected evaluation metrics")
    _public_json(normalized)
    return normalized


def _request(request):
    planning._object(request, "research request")
    planning._version(request, "research request")
    if request.get("public_data_confirmation") is not True:
        raise ValueError("Public-data confirmation must be explicitly true")
    result = {}
    for field in _FORM_FIELDS:
        value = request.get(field)
        if not isinstance(value, str):
            raise ValueError("The four public form fields must be text")
        if field in ("research_goal", "research_question") and not value.strip():
            raise ValueError("Public research goal and question must be nonempty")
        result[field] = value
    _public_json(result)
    return result


def _schema(schema_request):
    """Copy a caller-validated public execution contract without reshaping it."""
    planning._object(schema_request, "public schema/execution contract")
    _public_json(schema_request)

    def reject_nonpublic(item):
        if isinstance(item, dict):
            for key, value in item.items():
                normalized = re.sub(r"[\s-]+", "_", key.strip().lower())
                if (normalized in _NONPUBLIC_SCHEMA_KEYS
                        or (normalized in ("rows", "records", "row")
                            and isinstance(value, (list, dict)))):
                    raise ValueError("Public execution contracts cannot contain rows or secrets")
                reject_nonpublic(value)
        elif isinstance(item, list):
            for child in item:
                reject_nonpublic(child)

    reject_nonpublic(schema_request)
    return deepcopy(schema_request)


def _feedback(feedback):
    if not isinstance(feedback, list):
        raise ValueError("Pending feedback must be an array")
    result, seen = [], set()
    fields = {"comment_id", "actor", "body", "body_sha256"}
    for item in feedback:
        _object(item, "pending feedback", fields, fields)
        identifier = item["comment_id"]
        if type(identifier) is not int or identifier <= 0 or identifier in seen:
            raise ValueError("Pending feedback comment IDs must be unique positive integers")
        actor, body = item["actor"], item["body"]
        if (not isinstance(actor, str)
                or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", actor) is None
                or not isinstance(body, str) or not body.strip()):
            raise ValueError("Pending feedback requires a GitHub actor and nonempty body")
        _public_json(item)
        _digest(item["body_sha256"], "feedback body_sha256")
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != item["body_sha256"]:
            raise ValueError("Pending feedback body does not match its SHA256")
        seen.add(identifier)
        result.append(deepcopy(item))
    return sorted(result, key=lambda item: item["comment_id"])


def _feedback_ids(values):
    if (not isinstance(values, list)
            or any(type(value) is not int or value <= 0 for value in values)
            or len(values) != len(set(values))):
        raise ValueError("feedback_ids must contain unique positive comment IDs")
    return sorted(values)


def _objectives(value, policy):
    _object(value, "objectives", _STAGES, _STAGES)
    result = {}
    for stage in _STAGES:
        result[stage] = planning._tokens(value[stage], f"{stage} objectives")
        if set(result[stage]) - set(policy[stage]["allowed_objectives"]):
            raise ValueError(f"Unknown {stage} objective")
    return result


def _reason(value):
    if not isinstance(value, str) or not value.strip() or re.search(r"[가-힣]", value) is None:
        raise ValueError("The agent must provide a nonempty Korean coordination reason")
    _public_json(value)
    return value.strip()


def build_context(request, schema_request, selection, feedback, *, catalogs=None, policy=None):
    """Build transient public agent input, including raw public goal and writer prose.

    No Issue title, repository metadata, arbitrary request keys, row samples,
    environment variables or credentials are copied into the input.
    """
    catalogs, policy = _configuration(catalogs, policy)
    public_request = _request(request)
    schema = _schema(schema_request)
    current = _selection(selection, catalogs, policy)
    pending = _feedback(feedback)
    context = {
        "schema_version": 1, "stage": "pr1_intent", "request": public_request,
        "schema_request": schema, "selection": current, "catalogs": catalogs,
        "policy": policy,
        "allowed_objectives": {stage: list(policy[stage]["allowed_objectives"])
                               for stage in _STAGES},
        "feedback": pending, "request_hash": canonical_hash(public_request),
        "selection_hash": canonical_hash(current), "schema_hash": canonical_hash(schema),
        "feedback_hash": canonical_hash(pending),
    }
    context["context_hash"] = canonical_hash(context)
    _public_json(context)
    return context


def _validate_context(context, catalogs, policy):
    planning._object(context, "intent context")
    public = context.get("request")
    _object(public, "public research request", _FORM_FIELDS, _FORM_FIELDS)
    request = {**public, "schema_version": 1, "public_data_confirmation": True}
    rebuilt = build_context(request, context.get("schema_request"), context.get("selection"),
                            context.get("feedback"), catalogs=catalogs, policy=policy)
    if context != rebuilt:
        raise ValueError("Intent context is stale, modified or has unsupported fields")
    return rebuilt


def _updates(current, updates, catalogs, policy):
    _object(updates, "selection_updates", _STAGES)
    result = deepcopy(current)
    for stage, patch in updates.items():
        allowed = {"add", "exclude"}
        if stage == "evaluation":
            allowed |= {"primary_metric", "threshold", "parameters",
                        "clinical_action", "comparison_model"}
        _object(patch, f"{stage} selection updates", allowed)
        aliases = planning._aliases(catalogs[stage], stage)
        additions = planning._ids(patch.get("add", []), aliases, f"{stage} add")
        exclusions = planning._ids(patch.get("exclude", []), aliases, f"{stage} exclude")
        if set(additions).intersection(exclusions):
            raise ValueError("An update cannot both add and exclude the same block")
        target = result[stage]
        if "primary_metric" in patch:
            primary = planning._ids([patch["primary_metric"]], aliases, "primary_metric")[0]
            target["primary_metric"] = primary
            target["exclude"] = [item for item in target["exclude"] if item != primary]
            if primary not in target["add"]:
                target["add"].append(primary)
        required = planning._ids(policy[stage]["required"], aliases, f"{stage} required")
        if stage == "eda":
            required = [*required, *_PROTECTED_EDA]
        if set(required).intersection(exclusions):
            raise ValueError("Required planning blocks cannot be excluded")
        if stage == "evaluation" and target["primary_metric"] in exclusions:
            raise ValueError("Change primary_metric before removing the current primary")
        for identifiers, include, remove in ((additions, "add", "exclude"),
                                              (exclusions, "exclude", "add")):
            target[remove] = [item for item in target[remove] if item not in identifiers]
            target[include] = list(dict.fromkeys([*target[include], *identifiers]))
        if stage == "evaluation":
            parameters = planning._parameters(patch.get("parameters", {}), patch.get("threshold"))
            if "threshold" in parameters:
                target["threshold"] = parameters.pop("threshold")
            elif "threshold" in patch:
                target["threshold"] = None
            target["parameters"].update(parameters)
            for field in ("clinical_action", "comparison_model"):
                if field in patch:
                    target[field] = deepcopy(patch[field])
    return _selection(result, catalogs, policy)


def _eda_ids(selection, additions, catalogs, policy):
    aliases = planning._aliases(catalogs["eda"], "eda")
    defaults = planning._ids(
        [*policy["eda"]["required"], *_PROTECTED_EDA, *policy["eda"]["defaults"]],
        aliases, "EDA default")
    return [identifier for identifier in dict.fromkeys(
        [*defaults, *additions, *selection["eda"]["add"]])
        if identifier not in selection["eda"]["exclude"]]


def _coordination_hash(record, catalogs, policy):
    return canonical_hash({
        "record": {key: value for key, value in record.items() if key != "coordination_hash"},
        "catalogs_hash": canonical_hash(catalogs), "policy_hash": canonical_hash(policy),
    })


def apply_proposal(selection, proposal, context, *, catalogs=None, policy=None):
    """Validate one real agent proposal and return metadata-only planning choices.

    The exact complete pending ID set is required. Protected choice patches need
    nonempty authenticated feedback; public goal alone may only union objectives
    and propose separate EDA additions. Parameter patches merge with prior values.
    """
    catalogs, policy = _configuration(catalogs, policy)
    context = _validate_context(context, catalogs, policy)
    current = _selection(selection, catalogs, policy)
    if canonical_hash(current) != context["selection_hash"]:
        raise ValueError("The proposal's base selection is stale")
    _object(proposal, "intent proposal", _PROPOSAL_FIELDS, _PROPOSAL_FIELDS)
    planning._version(proposal, "intent proposal")
    _public_json(proposal)
    if _digest(proposal["context_hash"], "proposal context_hash") != context["context_hash"]:
        raise ValueError("The proposal does not match the public intent context")
    identifiers = _feedback_ids(proposal["feedback_ids"])
    pending_ids = [item["comment_id"] for item in context["feedback"]]
    if identifiers != pending_ids:
        raise ValueError("feedback_ids must match every pending comment exactly")
    _object(proposal["selection_updates"], "selection_updates", _STAGES)
    if proposal["selection_updates"] and not pending_ids:
        raise ValueError("Human selection updates require authenticated pending feedback")
    additions = planning._ids(proposal["eda_add"], planning._aliases(catalogs["eda"], "eda"),
                              "AI EDA")
    hints = _objectives(proposal["objectives"], policy)
    reason = _reason(proposal["reason"])
    result = _updates(current, proposal["selection_updates"], catalogs, policy)
    for stage in _STAGES:
        result[stage]["objectives"] = list(dict.fromkeys(
            [*result[stage]["objectives"], *hints[stage]]))
    result = _selection(result, catalogs, policy)
    record = {
        "schema_version": 1, "provider": PROVIDER, "context_hash": context["context_hash"],
        "request_hash": context["request_hash"],
        "base_selection_hash": context["selection_hash"], "selection_hash": canonical_hash(result),
        "schema_hash": context["schema_hash"], "feedback_hash": context["feedback_hash"],
        "feedback_ids": identifiers, "eda_add": additions,
        "objectives": {stage: list(result[stage]["objectives"]) for stage in _STAGES},
        "reason": reason,
    }
    record["coordination_hash"] = _coordination_hash(record, catalogs, policy)
    _public_json(record)
    return {"selection": result,
            "eda_ids": _eda_ids(result, additions, catalogs, policy), "record": record}


def validate_record(record, selection, request, schema_request, *, catalogs=None, policy=None):
    """Validate a frozen record against public source settings, without source bodies.

    The caller separately verifies authenticated feedback/context provenance.
    Their digests cannot be reconstructed from a final selection and no bodies.
    This check binds all retained digests to the registered configuration and
    rechecks public goal, schema, final selection, canonical IDs and objectives.
    """
    catalogs, policy = _configuration(catalogs, policy)
    _object(record, "coordination record", _RECORD_FIELDS, _RECORD_FIELDS)
    planning._version(record, "coordination record")
    _public_json(record)
    if record["provider"] != PROVIDER:
        raise ValueError("Unsupported intent coordination provider")
    for field in ("context_hash", "request_hash", "base_selection_hash", "selection_hash",
                  "schema_hash", "feedback_hash", "coordination_hash"):
        _digest(record[field], field)
    current = _selection(selection, catalogs, policy)
    if selection != current or canonical_hash(current) != record["selection_hash"]:
        raise ValueError("Frozen selection does not match the normalized coordination record")
    if canonical_hash(_request(request)) != record["request_hash"]:
        raise ValueError("Frozen public research text does not match request_hash")
    if canonical_hash(_schema(schema_request)) != record["schema_hash"]:
        raise ValueError("Frozen public input schema does not match schema_hash")
    aliases = planning._aliases(catalogs["eda"], "eda")
    additions = planning._ids(record["eda_add"], aliases, "AI EDA")
    if additions != record["eda_add"]:
        raise ValueError("Recorded AI EDA IDs must be canonical and deduplicated")
    objectives = _objectives(record["objectives"], policy)
    if (objectives != record["objectives"]
            or objectives != {stage: current[stage]["objectives"] for stage in _STAGES}):
        raise ValueError("Recorded objectives do not match final selection objectives")
    if _feedback_ids(record["feedback_ids"]) != record["feedback_ids"]:
        raise ValueError("Recorded feedback IDs must be ordered and deduplicated")
    if not record["feedback_ids"] and record["feedback_hash"] != canonical_hash([]):
        raise ValueError("An empty feedback ID list requires the empty feedback hash")
    if _reason(record["reason"]) != record["reason"]:
        raise ValueError("Recorded reason must be normalized Korean text")
    _eda_ids(current, additions, catalogs, policy)
    if _coordination_hash(record, catalogs, policy) != record["coordination_hash"]:
        raise ValueError("Coordination hash does not match the record and registered configuration")
    return deepcopy(record)
