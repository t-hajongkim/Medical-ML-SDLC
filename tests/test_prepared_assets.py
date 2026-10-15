import ast
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

from app.asset_contract import read_json, validate_image_reference, validate_pair, verify_files
from app.assets import CONFIG, patient_splits, prepare_pair
from app.blocks.catalog import run_checks
from app.bootstrap import load_references, participant_context, record_initialization, verify_asset_directories
from app.dataset import ROOT, SOURCE_URL
from app.infer import predict


def synthetic_source(folder: Path):
    config = read_json(CONFIG)
    # Synthetic grouping fixture; these are not actual patient records.
    records = []
    for patient in range(60):
        for encounter in range(2):
            row = {column: str(2 + (patient + encounter) % 3) for column in config["numeric_features"]}
            row.update({
                "encounter_id": f"enc-{patient}-{encounter}",
                "patient_nbr": f"subject-{patient}",
                "race": "group-a" if patient % 2 else "group-b",
                "gender": "category-a" if patient % 3 else "category-b",
                "age": "band-a" if patient % 2 else "band-b",
                "readmitted": "<30" if patient % 2 else "NO",
            })
            records.append(row)
    frame = pd.DataFrame(records)
    assignments = patient_splits(frame["patient_nbr"], config["split"])
    frame.loc[frame.index[assignments == "train"][0], "num_medications"] = "NaN"
    frame.loc[assignments != "train", "num_medications"] = "10000"
    source = folder / "source.csv"
    frame.to_csv(source, index=False)
    manifest = {
        "source_url": SOURCE_URL,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "bytes": source.stat().st_size,
        "rows": len(frame),
        "columns": frame.columns.tolist(),
        "target": "readmitted",
        "missing_tokens": ["", "?", "NaN"],
    }
    manifest_path = folder / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return frame, source, manifest_path, config


class PreparedAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.frame, cls.source, cls.manifest, cls.config = synthetic_source(cls.folder)
        cls.output = cls.folder / "prepared"
        cls.result = prepare_pair(cls.source, cls.output, CONFIG, cls.manifest, "synthetic-test")
        cls.data_dir = cls.output / "data" / "assets"
        cls.model_dir = cls.output / "model" / "assets"

    def test_data_model_contract_and_file_integrity(self):
        data = read_json(self.data_dir / "data_manifest.json")
        model = read_json(self.model_dir / "model_manifest.json")
        self.assertEqual(validate_pair(data, model), self.result["pair_id"])
        verify_files(self.data_dir, data)
        verify_files(self.model_dir, model)
        inspection = verify_asset_directories(self.data_dir, self.model_dir)
        self.assertEqual(inspection["rows"], 120)
        self.assertEqual(inspection["checks"][0]["status"], "passed")

    def test_patient_disjoint_splits_and_demographic_metadata(self):
        metadata = pd.read_csv(self.data_dir / "metadata.csv", dtype=str, keep_default_na=False)
        self.assertEqual(metadata.groupby("patient_key")["split"].nunique().max(), 1)
        self.assertEqual(set(metadata["split"]), {"train", "valid", "test"})
        self.assertTrue({"race", "gender", "age_group"}.issubset(metadata.columns))
        for split in ("train", "valid", "test"):
            features = pd.read_csv(self.data_dir / f"{split}.csv")
            self.assertFalse(features.isna().any().any())
            self.assertTrue(np.isfinite(features.drop(columns="row_key").to_numpy()).all())

    def test_preprocessor_is_fitted_only_on_train(self):
        preprocessor = joblib.load(self.model_dir / "preprocessor.joblib")
        numeric = preprocessor.named_transformers_["numeric"]
        index = self.config["numeric_features"].index("num_medications")
        self.assertLess(numeric.named_steps["imputer"].statistics_[index], 10)
        self.assertLess(numeric.named_steps["scaler"].mean_[index], 10)

    def test_saved_checkpoint_reproduces_operator_prediction(self):
        payload = read_json(self.output / "verification" / "input.json")
        result = predict(self.model_dir, payload)
        expected = read_json(self.output / "verification" / "expected.json")
        np.testing.assert_allclose(result["probabilities"], expected["probabilities"], atol=1e-12)
        self.assertEqual(result["row_keys"], payload["row_keys"])
        self.assertTrue(all(0 <= value <= 1 for value in result["probabilities"]))

    def test_wrong_feature_order_and_nonfinite_inputs_are_rejected(self):
        original = read_json(self.output / "verification" / "input.json")
        changed = {**original, "feature_columns": original["feature_columns"][::-1]}
        with self.assertRaisesRegex(ValueError, "order"):
            predict(self.model_dir, changed)
        changed = json.loads(json.dumps(original))
        changed["rows"][0][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            predict(self.model_dir, changed)

    def test_corrupted_checkpoint_is_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "model"
            shutil.copytree(self.model_dir, model_dir)
            checkpoint = model_dir / "checkpoint.joblib"
            checkpoint.write_bytes(checkpoint.read_bytes() + b"x")
            with patch("app.infer.joblib.load") as loader:
                with self.assertRaisesRegex(ValueError, "integrity"):
                    predict(model_dir, read_json(self.output / "verification" / "input.json"))
                loader.assert_not_called()

    def test_mismatched_asset_pair_is_rejected(self):
        data = read_json(self.data_dir / "data_manifest.json")
        model = read_json(self.model_dir / "model_manifest.json")
        model["feature_columns"] = model["feature_columns"][::-1]
        with self.assertRaisesRegex(ValueError, "pair ID"):
            validate_pair(data, model)

    def test_missing_patient_ids_do_not_pass_audit(self):
        result = run_checks(["patient_overlap"], {"metadata": [{"split": "train"}]})
        self.assertEqual(result[0]["status"], "unverifiable")
        with self.assertRaises(ValueError):
            run_checks(["not-implemented"], {})

    def test_overlap_block_detects_real_group_overlap(self):
        records = [
            {"patient_key": "a", "split": "train"},
            {"patient_key": "a", "split": "valid"},
            {"patient_key": "b", "split": "test"},
        ]
        result = run_checks(["patient_overlap"], {"metadata": records})[0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["overlaps"]["train_valid"], 1)

    def test_inference_module_never_calls_fit(self):
        tree = ast.parse((ROOT / "app" / "infer.py").read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == "fit" for item in ast.walk(tree)))

    def test_prepared_data_and_model_contexts_are_separate(self):
        self.assertFalse((self.data_dir / "checkpoint.joblib").exists())
        self.assertFalse((self.model_dir / "source.csv").exists())
        self.assertFalse((self.output / "source.csv").exists())
        self.assertTrue((self.output / "model" / "app" / "infer.py").exists())
        self.assertFalse((self.output / "model" / "app" / "assets.py").exists())

    def test_relocated_build_files_preserve_generated_contexts(self):
        for source, destination in (
            ("db/Dockerfile", "data/Dockerfile"),
            ("app/Dockerfile", "model/Dockerfile"),
            (".dockerignore", "data/.dockerignore"),
            ("db/init.sql", "data/db/init.sql"),
            ("db/healthcheck.sh", "data/db/healthcheck.sh"),
            ("requirements-ml.txt", "model/requirements-ml.txt"),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    (ROOT / source).read_bytes(),
                    (self.output / destination).read_bytes(),
                )
        for context in ("data", "model"):
            self.assertTrue((self.output / context / ".dockerignore").is_file())

    def test_unpublished_or_mutable_image_references_are_rejected(self):
        for reference in (None, "ghcr.io/example/data:latest", "ghcr.io/example/data\nx=y"):
            with self.assertRaises(ValueError):
                validate_image_reference(reference)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "assets.json"
            config.write_text(json.dumps({
                "schema_version": 1, "data_image": None, "model_image": None,
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_references(config)
            references = ("ghcr.io/example/data@sha256:" + "a" * 64,
                          "ghcr.io/example/model@sha256:" + "b" * 64)
            config.write_text(json.dumps({
                "schema_version": 1, "data_image": references[0], "model_image": references[1],
            }), encoding="utf-8")
            self.assertEqual(load_references(config), references)

    def test_participant_context_and_record_use_verified_pair(self):
        data_ref = "ghcr.io/example/data@sha256:" + "a" * 64
        model_ref = "ghcr.io/example/model@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            participant_context(data_ref, "Example/Workshop", directory / "context")
            dockerfile = (directory / "context" / "Dockerfile").read_text()
            self.assertIn("FROM " + data_ref, dockerfile)
            self.assertIn("https://github.com/Example/Workshop", dockerfile)
            inspection = directory / "inspection.json"
            inspection.write_text(json.dumps({
                "pair_id": self.result["pair_id"], "source_data_image": data_ref,
                "source_model_image": model_ref, "split_rows": self.result["split_rows"],
                "feature_count": self.result["features"],
            }), encoding="utf-8")
            record = record_initialization(inspection, data_ref, model_ref, directory / "workshop.json", "test")
            self.assertFalse(record["participant_training"])
            self.assertEqual(record["model_image"], model_ref)


if __name__ == "__main__":
    unittest.main()
