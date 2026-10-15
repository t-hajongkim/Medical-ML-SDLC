import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from app.dataset import download_source, validate_dataset
from app.ghcr import image_name, pushed_digest, validate_tag


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "source_manifest.json"
SOURCE_URL = "https://archive.ics.uci.edu/static/public/296/data.csv"
# Synthetic parser fixture, not copied clinical records.
SAMPLE = b"feature,category,class\r\n1,NaN,positive\t\r\n2,group-a,negative\r\n"


class ImageNameTests(unittest.TestCase):
    def test_lowercase_and_package_kind(self):
        self.assertEqual(
            image_name("Example-Org/Workshop_2026", "db"),
            "ghcr.io/example-org/workshop_2026-db",
        )
        self.assertEqual(
            image_name("Example-Org/Workshop_2026", "data"),
            "ghcr.io/example-org/workshop_2026-data",
        )
        self.assertEqual(
            image_name("Example-Org/Workshop_2026", "model"),
            "ghcr.io/example-org/workshop_2026-model",
        )

    def test_invalid_repository_is_rejected(self):
        for repository in ("owner", "owner/repo/extra", "../repo", "owner/..", "owner/repo\nx=y", "owner/repo:tag", "owner/repo;echo"):
            with self.subTest(repository=repository):
                with self.assertRaises(ValueError):
                    image_name(repository, "db")

    def test_invalid_package_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            image_name("owner/repository", "unknown")

    def test_valid_tags(self):
        for tag in ("ckd-v1", "run-123", "v1.2.3", "a" * 128):
            self.assertEqual(validate_tag(tag), tag)

    def test_invalid_tags(self):
        for tag in ("", ".hidden", "-bad", "a" * 129, "v1/x", "v1\nx=y", "$(id)"):
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError):
                    validate_tag(tag)

    def test_push_digest_is_extracted(self):
        digest = "sha256:" + "a" * 64
        self.assertEqual(
            pushed_digest("layer: Pushed\nckd-v1: digest: " + digest + " size: 1234\n"),
            digest,
        )

    def test_missing_or_ambiguous_push_digest_is_rejected(self):
        for log in ("unauthorized", "digest: sha256:bad", "digest: sha256:" + "a" * 64 + "\ndigest: sha256:" + "b" * 64):
            with self.subTest(log=log):
                with self.assertRaises(ValueError):
                    pushed_digest(log)

    def test_name_cli_outputs_validated_github_outputs(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.ghcr", "name", "--repository", "Example/Workshop", "--kind", "db", "--tag", "ckd-v1"],
            cwd=ROOT, text=True, capture_output=True, check=True,
        )
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "image=ghcr.io/example/workshop-db",
                "tag=ckd-v1",
                "reference=ghcr.io/example/workshop-db:ckd-v1",
            ],
        )


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.csv = self.folder / "sample.csv"
        self.csv.write_bytes(SAMPLE)
        self.manifest = self.folder / "manifest.json"
        self.specification = {
            "source_url": SOURCE_URL,
            "sha256": hashlib.sha256(SAMPLE).hexdigest(),
            "bytes": len(SAMPLE),
            "columns": ["feature", "category", "class"],
            "target": "class",
            "rows": 2,
            "missing_tokens": ["", "?", "NaN"],
            "missing_cells": 1,
        }
        self.manifest.write_text(json.dumps(self.specification), encoding="utf-8")

    def test_frozen_source_is_valid_without_modification(self):
        result = validate_dataset(self.csv, self.manifest)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["features"], 2)
        self.assertEqual(result["missing_cells"], 1)
        self.assertEqual(self.csv.read_bytes(), SAMPLE)

    def test_modified_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "changed.csv"
            altered.write_bytes(SAMPLE + b"\n")
            with self.assertRaisesRegex(ValueError, "checksum"):
                validate_dataset(altered, self.manifest)

    def test_schema_and_counts_are_checked_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            changed_manifest = Path(directory) / "manifest.json"
            for key, value, message in (
                ("columns", ["wrong"], "columns"),
                ("rows", 399, "rows"),
                ("missing_cells", 0, "missing"),
            ):
                candidate = {**self.specification, key: value}
                changed_manifest.write_text(json.dumps(candidate), encoding="utf-8")
                with self.subTest(field=key):
                    with self.assertRaisesRegex(ValueError, message):
                        validate_dataset(self.csv, changed_manifest)

    def test_source_special_values_are_not_cleaned(self):
        with self.csv.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]["class"], "positive\t")
        self.assertEqual(
            sum(value == "NaN" for row in rows for key, value in row.items() if key != "class"),
            1,
        )

    def test_registered_source_manifest_is_pinned(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_url"], SOURCE_URL)
        self.assertEqual(manifest["sha256"], "f792c388d9b470aac4fbf21c39b176534d55a116e7626881c30bbe7ab0341422")
        self.assertEqual(manifest["rows"], 101766)
        self.assertEqual(len(manifest["columns"]), 50)
        self.assertEqual(manifest["doi"], "10.24432/C5230J")

    def test_downloads_and_validates_outside_repository(self):
        destination = self.folder / "downloaded.csv"
        with patch("app.dataset.urlopen", return_value=io.BytesIO(SAMPLE)) as request:
            result = download_source(destination, self.manifest)
        request.assert_called_once_with(SOURCE_URL, timeout=60)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(destination.read_bytes(), SAMPLE)

    def test_bad_download_is_not_kept(self):
        for content, message in (
            (b"x" + SAMPLE[1:], "checksum"),
            (SAMPLE[:-1], "size"),
            (SAMPLE + b"x", "size"),
        ):
            context = self.folder / "bad-context"
            with self.subTest(message=message):
                with patch("app.dataset.urlopen", return_value=io.BytesIO(content)):
                    with self.assertRaisesRegex(ValueError, message):
                        download_source(context, self.manifest)
                self.assertFalse(context.exists())
                self.assertEqual(list(self.folder.glob("workshop-source-*")), [])

    def test_download_failure_is_explicit(self):
        context = self.folder / "failed-context"
        with patch("app.dataset.urlopen", side_effect=URLError("unavailable")):
            with self.assertRaises(URLError):
                download_source(context, self.manifest)
        self.assertFalse(context.exists())

    def test_existing_context_is_never_overwritten(self):
        context = self.folder / "existing-context"
        context.mkdir()
        sentinel = context / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with patch("app.dataset.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "exists"):
                download_source(context, self.manifest)
        request.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_repository_cannot_be_used_as_download_destination(self):
        with patch("app.dataset.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "outside"):
                download_source(ROOT / "data" / "forbidden", self.manifest)
        request.assert_not_called()

    def test_unregistered_download_url_is_rejected(self):
        specification = {**self.specification, "source_url": "https://example.invalid/data.csv"}
        self.manifest.write_text(json.dumps(specification), encoding="utf-8")
        with patch("app.dataset.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "source URL"):
                download_source(self.folder / "untrusted", self.manifest)
        request.assert_not_called()

    def test_build_context_is_an_explicit_data_only_allowlist(self):
        lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        entries = [line for line in lines if line and not line.startswith("#")]
        self.assertEqual(entries[0], "**")
        self.assertEqual(
            set(entries[1:]),
            {
                "!Dockerfile", "!assets/", "!assets/*.csv",
                "!assets/data_manifest.json", "!assets/NOTICE.txt", "!db/", "!db/init.sql",
                "!db/healthcheck.sh",
            },
        )


if __name__ == "__main__":
    unittest.main()
