"""Aggregate-only EDA over validated prepared data assets; no model execution."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from app.asset_contract import write_json
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


def load_context(assets: Path, raw_csv: Path | None = None) -> dict:
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
    statuses = [item["status"] for item in results]
    status = "failed" if "failed" in statuses else (
        "unverifiable" if "unverifiable" in statuses else "completed")
    manifest = context.get("data_manifest") or {}
    return {
        "schema_version": 1, "kind": "aggregate-eda", "report_generated": True,
        "assessment_status": status, "status_meanings": STATUS_MEANINGS,
        "interpretation": "Report success is not a research suitability or leakage-free certification.",
        "data_id": manifest.get("data_id"),
        "prepared_revision": manifest.get("prepared_source", {}).get("revision"),
        "raw_source_sha256": manifest.get("raw_source_sha256"),
        "model_execution_performed": False, "training_performed": False,
        "preprocessing_performed": False, "results": results,
    }


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
