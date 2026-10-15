import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

from app.asset_contract import read_json, validate_image_reference, validate_manifest, validate_pair, verify_files, write_json
from app.blocks.catalog import run_checks
from app.dataset import ROOT
from app.ghcr import image_name


def docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True,
        encoding="utf-8", timeout=600,
    )
    if result.returncode:
        raise RuntimeError(f"Docker {arguments[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def load_references(config_path: Path) -> tuple[str, str]:
    config = read_json(config_path)
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported source asset configuration.")
    return (
        validate_image_reference(config.get("data_image")),
        validate_image_reference(config.get("model_image")),
    )


def verify_asset_directories(data_directory: Path, model_directory: Path) -> dict:
    data = read_json(data_directory / "data_manifest.json")
    model = read_json(model_directory / "model_manifest.json")
    pair_id = validate_pair(data, model)
    verify_files(data_directory, data)
    verify_files(model_directory, model)
    with (data_directory / "metadata.csv").open(encoding="utf-8", newline="") as source:
        records = list(csv.DictReader(source))
    if len(records) != sum(data["split_rows"].values()):
        raise ValueError("Metadata row count differs from the split contract.")
    keys = [record["row_key"] for record in records]
    if len(keys) != len(set(keys)):
        raise ValueError("Prepared encounter keys are not unique.")
    for split in ("train", "valid", "test"):
        expected = {record["row_key"] for record in records if record["split"] == split}
        with (data_directory / f"{split}.csv").open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != ["row_key", *data["feature_columns"]]:
                raise ValueError("Prepared split feature order differs from the model.")
            rows = list(reader)
        if len(rows) != data["split_rows"][split] or {row["row_key"] for row in rows} != expected:
            raise ValueError("Prepared split membership differs from its metadata.")
    def audit_features():
        positions = {}
        with (data_directory / "db_features.csv").open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != ["row_key", "split", "feature_values"]:
                raise ValueError("Legacy database feature schema mismatch.")
            for row in reader:
                split = row["split"]
                positions[split] = positions.get(split, 0) + 1
                yield {**row, "row_position": positions[split]}

    checks = run_checks(["patient_overlap", "demographics", "schema"], {
        "metadata": records,
        "feature_columns": data["feature_columns"],
        "expected_feature_columns": model["feature_columns"],
        "feature_rows": audit_features,
    })
    # Keep unverifiable clinical/demographic findings visible in the returned checks.
    if checks[0]["status"] != "passed" or checks[2]["details"].get("numeric_contract") != "passed":
        raise ValueError("Initialization checks failed.")
    return {"pair_id": pair_id, "rows": len(records), "feature_count": len(data["feature_columns"]), "split_rows": data["split_rows"], "checks": checks}


def copy_image_assets(reference: str, kind: str, destination: Path) -> None:
    validate_image_reference(reference)
    docker("pull", reference)
    container = "asset-inspect-" + uuid.uuid4().hex[:12]
    docker("create", "--name", container, reference, "unused")
    try:
        destination.mkdir(parents=True)
        manifest_name = "data_manifest.json" if kind == "prepared-data" else "model_manifest.json"
        image_directory = "/opt/workshop/assets" if kind == "prepared-data" else "/opt/workshop/model"
        docker("cp", f"{container}:{image_directory}/{manifest_name}", str(destination / manifest_name))
        manifest = read_json(destination / manifest_name)
        validate_manifest(manifest, kind)
        for name in manifest["files"]:
            docker("cp", f"{container}:{image_directory}/{name}", str(destination / name))
    finally:
        docker("rm", "--force", container)


def verify_image_pair(data_reference: str, model_reference: str, output: Path) -> dict:
    data_reference = validate_image_reference(data_reference)
    model_reference = validate_image_reference(model_reference)
    output = output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new inspection folder outside the Git checkout.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asset-inspection-", dir=output.parent) as temporary:
        directory = Path(temporary)
        copy_image_assets(data_reference, "prepared-data", directory / "data")
        copy_image_assets(model_reference, "fixed-model", directory / "model")
        result = verify_asset_directories(directory / "data", directory / "model")
    output.mkdir()
    result.update({"source_data_image": data_reference, "source_model_image": model_reference})
    write_json(output / "inspection.json", result)
    return result


def participant_context(data_reference: str, repository: str, output: Path) -> None:
    validate_image_reference(data_reference)
    image_name(repository, "data")
    output = output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new participant build folder outside the Git checkout.")
    output.mkdir(parents=True)
    (output / "Dockerfile").write_text(
        f"FROM {data_reference}\n"
        f'LABEL org.opencontainers.image.source="https://github.com/{repository}"\n',
        encoding="utf-8",
    )


def record_initialization(inspection_path: Path, data_reference: str, model_reference: str, output: Path, run_id: str) -> dict:
    inspection = read_json(inspection_path)
    validate_image_reference(data_reference)
    validate_image_reference(model_reference)
    if model_reference != inspection["source_model_image"]:
        raise ValueError("Initialization model differs from the verified source.")
    record = {
        "schema_version": 1,
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "initialization_run_id": run_id,
        "pair_id": inspection["pair_id"],
        "data_image": data_reference,
        "model_image": model_reference,
        "source_data_image": inspection["source_data_image"],
        "split_rows": inspection["split_rows"],
        "feature_count": inspection["feature_count"],
        "participant_training": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize participant assets without fitting a model.")
    commands = parser.add_subparsers(dest="command", required=True)
    refs = commands.add_parser("refs")
    refs.add_argument("--config", type=Path, default=ROOT / "config" / "assets.json")
    inspect = commands.add_parser("verify")
    inspect.add_argument("--data-image", required=True)
    inspect.add_argument("--model-image", required=True)
    inspect.add_argument("--output", type=Path, required=True)
    context = commands.add_parser("context")
    context.add_argument("--data-image", required=True)
    context.add_argument("--repository", required=True)
    context.add_argument("--output", type=Path, required=True)
    record = commands.add_parser("record")
    record.add_argument("--inspection", type=Path, required=True)
    record.add_argument("--data-image", required=True)
    record.add_argument("--model-image", required=True)
    record.add_argument("--output", type=Path, default=ROOT / "config" / "workshop.json")
    record.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "refs":
            data, model = load_references(arguments.config)
            print(f"data_image={data}\nmodel_image={model}")
        elif arguments.command == "verify":
            print(json.dumps(verify_image_pair(arguments.data_image, arguments.model_image, arguments.output)))
        elif arguments.command == "context":
            participant_context(arguments.data_image, arguments.repository, arguments.output)
            print(json.dumps({"context": str(arguments.output)}))
        else:
            result = record_initialization(arguments.inspection, arguments.data_image, arguments.model_image, arguments.output, arguments.run_id)
            print(json.dumps({"pair_id": result["pair_id"], "record": str(arguments.output)}))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
