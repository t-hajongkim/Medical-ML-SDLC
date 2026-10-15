import argparse
import csv
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://archive.ics.uci.edu/static/public/296/data.csv"
MANIFEST = ROOT / "data" / "source_manifest.json"


def validate_dataset(csv_path: Path, manifest_path: Path) -> dict[str, int | str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    content = csv_path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != manifest["sha256"]:
        raise ValueError("The source checksum differs from the frozen dataset.")
    if len(content) != manifest["bytes"]:
        raise ValueError("The source file size differs from the dataset manifest.")

    with csv_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames != manifest["columns"]:
            raise ValueError("The source columns differ from the dataset manifest.")
        rows = list(reader)
    if len(rows) != manifest["rows"]:
        raise ValueError("The source rows differ from the dataset manifest.")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("A source row does not have the expected number of columns.")

    target = manifest["target"]
    missing_tokens = set(manifest["missing_tokens"])
    missing = sum(
        value in missing_tokens
        for row in rows
        for column, value in row.items()
        if column != target
    )
    if "missing_cells" in manifest and missing != manifest["missing_cells"]:
        raise ValueError("The source missing values differ from the dataset manifest.")

    return {
        "rows": len(rows),
        "features": len(manifest["columns"]) - 1 - len(manifest.get("id_columns", [])),
        "missing_cells": missing,
        "sha256": checksum,
    }


def download_source(
    destination: Path, manifest_path: Path = MANIFEST
) -> dict[str, int | str]:
    destination = destination.resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError("The source destination must be outside the Git repository.")
    if destination.exists():
        raise ValueError("The source destination already exists; refusing to overwrite it.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["source_url"] != SOURCE_URL:
        raise ValueError("The source URL is not the registered UCI source URL.")
    expected_size = manifest["bytes"]
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError("The expected source size must be a positive integer.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workshop-source-", dir=destination.parent) as temporary:
        with urlopen(SOURCE_URL, timeout=60) as response:
            content = response.read(expected_size + 1)
        if len(content) != expected_size:
            raise ValueError("The download size differs from the frozen source.")
        source_csv = Path(temporary) / "source.csv"
        source_csv.write_bytes(content)
        result = validate_dataset(source_csv, manifest_path)
        source_csv.rename(destination)
    return {**result, "path": str(destination)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or download the registered source.")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download")
    download.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--csv", type=Path, required=True)
    for command in (download, validate):
        command.add_argument("--manifest", type=Path, default=MANIFEST)
    arguments = parser.parse_args()
    try:
        if arguments.command == "download":
            result = download_source(arguments.output, arguments.manifest)
        else:
            result = validate_dataset(arguments.csv, arguments.manifest)
    except (OSError, ValueError, csv.Error, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
