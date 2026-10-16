"""Execute registered EDA blocks over validated prepared data; never a model."""

import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path

from app.asset_contract import canonical_hash, write_json
from app.blocks.catalog import CHECKS, run_checks
from app.blocks.checks._shared import split_name, target_value
from app.blocks.checks.label_observability import MAPPING
from app.dataset import MANIFEST, ROOT, validate_dataset
from app.prepared import (
    FEATURE_COLUMNS, METADATA_COLUMNS, csv_reader, valid_row, validate_data_manifest,
)


STATUS_MEANINGS = {
    "passed": "All checks within the explicitly stated block scope are supported and satisfied.",
    "failed": "A measured violation or malformed input was found; report generation may still succeed.",
    "unverifiable": "Required input or evidence is missing; measured subchecks may still be available.",
    "completed": "Descriptive aggregation succeeded; not research suitability or leakage certification.",
}


class InputMappingRequired(ValueError):
    def __init__(self, schema_request):
        super().__init__("입력 매핑이 없거나 오래되었습니다. AI 추천과 검증 후에 데이터 점검을 실행합니다.")
        self.schema_request = schema_request


def feature_rows(directory):
    with (directory / "db_features.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv_reader(handle, FEATURE_COLUMNS, "EDA features"):
            valid_row(row, "EDA features")
            yield row


def raw_evidence(raw_csv, records, manifest):
    validation = validate_dataset(raw_csv, MANIFEST)
    if validation["sha256"] != manifest["raw_source_sha256"]:
        raise ValueError("EDA raw source differs from the prepared data manifest.")
    indexed = {row["row_key"]: row for row in records}
    if len(indexed) != len(records):
        raise ValueError("EDA admission keys are duplicated.")
    seen = set()
    counts = Counter()
    predicates = {
        "length_of_stay_1_to_14": {"satisfied": 0, "violated": 0, "unverifiable": 0},
        "laboratory_count_positive": {"satisfied": 0, "violated": 0, "unverifiable": 0},
        "medication_count_positive": {"satisfied": 0, "violated": 0, "unverifiable": 0},
    }
    labels = Counter()
    for_readmission = Counter()
    with raw_csv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, strict=True):
            key = row["encounter_id"]
            if key not in indexed or key in seen:
                raise ValueError("EDA raw encounter coverage or uniqueness differs.")
            seen.add(key)
            reference = indexed[key]
            label = row["readmitted"]
            labels[label] += 1
            for_readmission[split_name(reference["split"])] += 1
            counts["target_mismatches"] += int(
                label not in MAPPING or target_value(reference["target"]) != MAPPING.get(label))
            counts["patient_mismatches"] += int(row["patient_nbr"] != reference["patient_key"])
            for name, column, low, high in (
                ("length_of_stay_1_to_14", "time_in_hospital", 1, 14),
                ("laboratory_count_positive", "num_lab_procedures", 1, None),
                ("medication_count_positive", "num_medications", 1, None),
            ):
                try:
                    value = int(row[column])
                except (ValueError, KeyError):
                    outcome = "unverifiable"
                else:
                    outcome = "satisfied" if value >= low and (high is None or value <= high) else "violated"
                predicates[name][outcome] += 1
            counts["diagnosis_250_proxy"] += int(
                any(row.get(name, "").split(".")[0] == "250" for name in ("diag_1", "diag_2", "diag_3")))
    if seen != set(indexed):
        raise ValueError("EDA prepared encounters are missing from the original source.")
    for entry in predicates.values():
        entry["denominator"] = len(records)
        entry["status"] = "failed" if entry["violated"] else (
            "unverifiable" if entry["unverifiable"] else "passed")
    return {
        "selection": {
            "status": "failed" if any(entry["violated"] for entry in predicates.values()) else "unverifiable",
            "raw_count_checks": predicates,
            "inpatient_encounter": {"status": "unverifiable",
                                   "reason": "Source description; no independent row-level inpatient adjudication."},
            "diabetic_encounter": {"status": "unverifiable",
                                  "diagnosis_250_proxy_count": counts["diagnosis_250_proxy"],
                                  "denominator": len(records),
                                  "reason": "Three diagnosis fields do not independently adjudicate all source diabetes selection."},
            "scope": "Selected UCI encounters only; no upstream exclusion reconstruction.",
        },
        "label_alignment": {
            "status": "failed" if counts["target_mismatches"] or counts["patient_mismatches"] else "passed",
            "matched_admissions": len(seen), "split_rows": dict(for_readmission),
            "raw_readmitted_counts": dict(labels),
            "target_mismatches": counts["target_mismatches"],
            "patient_link_mismatches": counts["patient_mismatches"],
            "scope": "Original label encoding and patient linkage, not 30-day follow-up completeness.",
        },
    }


def load_context(assets: Path, raw_csv: Path | None = None, *,
                 input_profile=None, input_mapping=None) -> dict:
    if input_profile is not None:
        from app.input_contracts import inspect_inputs, apply_mapping
        bundle = inspect_inputs(assets, profile=input_profile)
        if bundle["status"] == "needs_mapping":
            if (input_mapping is None or input_mapping.get("context_hash")
                    != bundle["schema_request"]["context_hash"]):
                raise InputMappingRequired(bundle["schema_request"])
            recommendation = input_mapping.get("recommendation")
            if not isinstance(recommendation, dict):
                raise ValueError("검증된 입력 매핑의 원래 추천 기록이 없습니다.")
            bundle = apply_mapping(assets, recommendation, profile=input_profile)
        context = bundle["context"]
        context["_input_schema_request"] = bundle["schema_request"]
        if context.get("dataset_context", {}).get("legacy") is True:
            context["target_mapping"] = context["data_manifest"]["prepared_source"]["target_mapping"]
        if raw_csv is not None:
            if context.get("dataset_context", {}).get("legacy") is not True:
                raise ValueError("등록 입력의 raw 검사는 별도 출처 어댑터가 필요합니다.")
            context["raw_evidence"] = raw_evidence(raw_csv, context["metadata"], context["data_manifest"])
        return context
    manifest = validate_data_manifest(assets)
    with (assets / "metadata.csv").open(encoding="utf-8", newline="") as handle:
        records = []
        for row in csv_reader(handle, METADATA_COLUMNS, "EDA metadata"):
            valid_row(row, "EDA metadata")
            records.append(row)
    counts = Counter(split_name(row["split"]) for row in records)
    if dict(counts) != manifest["split_rows"]:
        raise ValueError("EDA metadata split counts differ from the validated manifest.")
    context = {
        "metadata": records, "feature_columns": manifest["feature_columns"],
        "expected_feature_columns": manifest["prepared_source"]["features"],
        "feature_rows": lambda: feature_rows(assets),
        "target_mapping": manifest["prepared_source"]["target_mapping"],
        "data_manifest": manifest, "asset_integrity_verified": True,
    }
    if raw_csv is not None:
        context["raw_evidence"] = raw_evidence(raw_csv, records, manifest)
    return context


def report(context: dict, identifiers: list[str] | None = None) -> dict:
    results = run_checks(list(CHECKS) if identifiers is None else identifiers, context)
    return aggregate_results(context, results)


def aggregate_results(context: dict, results: list[dict]) -> dict:
    """Assemble measured block outputs without performing another calculation."""
    statuses = [item["status"] for item in results]
    status = "failed" if "failed" in statuses else (
        "unverifiable" if "unverifiable" in statuses else "completed")
    manifest = context.get("data_manifest") or {}
    output = {
        "schema_version": 1, "kind": "aggregate-eda", "report_generated": True,
        "assessment_status": status, "status_meanings": STATUS_MEANINGS,
        "interpretation": "Report success is not a research suitability or leakage-free certification.",
        "data_id": manifest.get("data_id"),
        "prepared_revision": manifest.get("prepared_source", {}).get("revision"),
        "raw_source_sha256": manifest.get("raw_source_sha256"),
        "model_execution_performed": False, "training_performed": False,
        "preprocessing_performed": False, "results": results,
    }
    binding = context.get("input_contract")
    if binding is not None:
        output["input_profile"] = binding["profile_id"]
        output["input_mapping_hash"] = canonical_hash({
            name: binding[name] for name in
            ("profile_id", "data_id", "bindings", "split_values", "target_values", "profile_snapshot")
        })
        output["dataset_context"] = context["dataset_context"]
    if context.get("_pr1_coordination") is not None:
        coordination = context["_pr1_coordination"]
        output["coordination_hash"] = coordination["coordination_hash"]
        output["research_request_hash"] = coordination["record"]["request_hash"]
        output["coordination_evidence"] = coordination["eda_evidence"]
        if "execution_plan" in coordination:
            output["initial_execution_plan_hash"] = coordination["execution_plan"]["plan_hash"]
    return output


def _validate_result(result, identifier):
    if (not isinstance(result, dict) or set(result) != {"block", "status", "details"}
            or result["block"] != identifier or result["status"] not in STATUS_MEANINGS
            or not isinstance(result["details"], dict)):
        raise ValueError("A registered EDA block returned an invalid output contract")
    forbidden = {"row_key", "patient_key", "row_keys", "patient_keys", "metadata",
                 "feature_values", "predictions", "y_true", "y_probability",
                 "reference_probabilities", "sample_ids", "reference_sample_ids"}
    def visit(value):
        if isinstance(value, dict):
            if forbidden.intersection(value):
                raise ValueError("EDA outputs must contain aggregates, not individual input rows")
            if any(not isinstance(key, str) for key in value):
                raise ValueError("EDA output keys must be strings")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif value is None or type(value) in (str, bool, int):
            return
        elif type(value) is float and math.isfinite(value):
            return
        else:
            raise ValueError("EDA outputs must contain finite JSON values")
    visit(result["details"])


def execution_result_hash(eda: dict) -> str:
    return canonical_hash({
        key: eda.get(key) for key in
        ("data_id", "input_mapping_hash", "execution_plan_hash", "initial_execution_plan_hash", "results")
    })


def execute_plan(plan: dict, context: dict, *, catalog=None, policy=None,
                 reuse=None, reuse_plan=None) -> dict:
    """Execute one immutable, linear startup-data plan, using only registered tools.

    Patient-overlap failures and unavailable evidence remain diagnostic results.
    Invalid input/output contracts fail explicitly. No model or replanning loop
    is reachable from this executor.
    """
    from app import planning_eda, planning_eval
    catalog = planning_eval.load_catalog("eda") if catalog is None else planning_eval._catalog(catalog, "eda")
    policy = planning_eval.load_policy() if policy is None else planning_eval._policy(policy)
    planning_eda.validate_execution_plan(plan, catalog=catalog, policy=policy)
    if planning_eda.input_signature(context) != plan["input_signature"]:
        raise ValueError("Prepared input does not match the startup execution plan")
    entries = {entry["id"]: entry for entry in catalog["checks"]}
    cached = {}
    previous_calls = {}
    if reuse is not None:
        if reuse_plan is None:
            raise ValueError("Reusing EDA requires the original validated execution plan")
        planning_eda.validate_execution_plan(reuse_plan, catalog=catalog, policy=policy)
        if (any(plan[key] != reuse_plan[key] for key in (
                "data_id", "data_image", "model_image", "code_commit", "schema_hash", "catalog_hash", "policy_hash"))
                or reuse.get("execution_plan_hash") != reuse_plan["plan_hash"]
                or reuse.get("data_id") != reuse_plan["data_id"]
                or reuse.get("input_mapping_hash") != reuse_plan["input_signature"]["input_mapping_hash"]
                or reuse.get("execution_result_hash") != execution_result_hash(reuse)):
            raise ValueError("Cached EDA belongs to a different or modified execution")
        previous_calls = {call["tool_id"]: call for call in reuse_plan["calls"]}
        results = reuse.get("results")
        if (not isinstance(results, list)
                or [item.get("block") for item in results] != list(previous_calls)):
            raise ValueError("Cached EDA outputs do not match their original tool calls")
        for item in results:
            _validate_result(item, item["block"])
            cached[item["block"]] = item
    required_roles = set()
    for call in plan["calls"]:
        if call["tool_id"] in cached and previous_calls[call["tool_id"]] == call:
            continue
        entry = entries[call["tool_id"]]
        for name in entry["requires"]:
            if name not in context or context[name] is None:
                raise ValueError("Missing required EDA input: " + name)
        required_roles.update(entry.get("input_contract", {}).get("metadata_roles", []))
    if required_roles:
        records = context.get("metadata")
        if not isinstance(records, (list, tuple)):
            raise ValueError("Prepared metadata must be a collection of normalized rows")
        if any(not isinstance(row, dict) or not required_roles.issubset(row) for row in records):
            raise ValueError("Prepared metadata is missing a required normalized role")
    import copy
    measured, executed, reused = [], [], []
    for call in plan["calls"]:
        identifier = call["tool_id"]
        if identifier in cached and previous_calls[identifier] == call:
            result = copy.deepcopy(cached[identifier])
            reused.append(identifier)
        else:
            entry = entries[identifier]
            names = set(entry["requires"]) | set(
                entry.get("input_contract", {}).get("optional_context", []))
            # The compatibility catalog predates explicit optional-context metadata.
            if "input_contract" not in entry:
                names.update(context)
            block_context = {name: context[name] for name in names if name in context}
            result = CHECKS[identifier](block_context)
            _validate_result(result, identifier)
            executed.append(identifier)
        measured.append(result)
    output = aggregate_results(context, measured)
    output.update(execution_plan_hash=plan["plan_hash"],
                  execution_mode="registered_execution_plan",
                  executed_ids=executed, reused_ids=reused)
    output.setdefault("initial_execution_plan_hash",
                      reuse.get("initial_execution_plan_hash", reuse_plan["plan_hash"])
                      if reuse is not None else plan["plan_hash"])
    output["execution_result_hash"] = execution_result_hash(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--raw-csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("Keep aggregate result JSON outside the checkout.")
    try:
        result = report(load_context(args.assets, args.raw_csv))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    except (OSError, ValueError, KeyError, csv.Error):
        parser.error("EDA input validation failed; no individual records are logged.")
    print(json.dumps({"report_generated": True, "assessment_status": result["assessment_status"],
                      "blocks": {item["block"]: item["status"] for item in result["results"]},
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
