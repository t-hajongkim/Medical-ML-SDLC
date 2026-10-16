import hashlib
import json
from pathlib import Path
import re


PINNED_IMAGE = re.compile(
    r"ghcr\.io/[a-z0-9]+(?:-+[a-z0-9]+)*/"
    r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*@sha256:[0-9a-f]{64}"
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


def verify_files(directory: Path, manifest: dict) -> None:
    for name, checksum in manifest["files"].items():
        candidate = directory / name
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(directory.resolve()) or not candidate.is_file() or file_hash(candidate) != checksum:
            raise ValueError(f"Asset file integrity check failed: {name}.")
