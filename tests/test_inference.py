"""Fixed-model inference contracts; fixtures contain synthetic rows only."""

from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import inference


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fixed-inference-")
        self.addCleanup(self.temporary.cleanup)
        self.assets = Path(self.temporary.name)
        self.manifest = {"feature_columns": ["f1", "f2"], "split_rows": {
            "train": 1, "validation": 2, "test": 1}}
        self.metadata = [
            ["synthetic-v2", "validation", "0"], ["synthetic-t", "test", "1"],
            ["synthetic-v1", "validation", "1"], ["synthetic-tr", "train", "0"]]
        self.features = [
            ["synthetic-v2", "validation", "2", "[0.5,2]"],
            ["synthetic-tr", "train", "1", "[0,0]"],
            ["synthetic-t", "test", "1", "[3,4]"],
            ["synthetic-v1", "validation", "1", "[1,-1]"]]
        self.write()

    def write(self):
        for name, header, rows in (
                ("metadata.csv", ["row_key", "split", "target"], self.metadata),
                ("db_features.csv", ["row_key", "split", "row_position", "feature_values"], self.features)):
            with (self.assets / name).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)

    def test_aligns_by_split_key_and_position_not_metadata_file_order(self):
        matrix, labels = inference.read_split(self.assets, self.manifest, "validation")
        self.assertEqual(matrix.tolist(), [[1, -1], [0.5, 2]])
        self.assertEqual(labels.tolist(), [1, 0])

    def test_default_split_is_validation_and_train_is_rejected(self):
        self.assertEqual(inference.read_split(self.assets, self.manifest)[1].tolist(), [1, 0])
        self.assertEqual(inference.read_split(self.assets, self.manifest, "test")[1].tolist(), [1])
        for split in ("train", "all", "", None):
            with self.subTest(split=split), self.assertRaises(ValueError):
                inference.read_split(self.assets, self.manifest, split)

    def test_damaged_rows_keys_positions_targets_and_vectors_are_rejected(self):
        original = deepcopy(self.features)
        for column, value in ((0, "missing"), (1, "test"), (2, "1"),
                              (2, "0"), (3, "[1]"), (3, "[NaN,2]"), (3, '[true,2]')):
            with self.subTest(column=column, value=value):
                self.features = deepcopy(original)
                self.features[0][column] = value
                self.write()
                with self.assertRaises(ValueError) as raised:
                    inference.read_split(self.assets, self.manifest)
                self.assertNotIn("synthetic-v2", str(raised.exception))
        self.features = original
        self.metadata[0][2] = "2"
        self.write()
        with self.assertRaises(ValueError):
            inference.read_split(self.assets, self.manifest)

    def test_integrity_failure_precedes_deserialization(self):
        with patch.object(inference, "validate_data_manifest", side_effect=ValueError("integrity")), \
                patch("joblib.load") as loader:
            with self.assertRaises(ValueError):
                inference.predict(self.assets, self.assets)
        loader.assert_not_called()

    def test_scratch_image_is_created_with_an_inert_command_but_never_started(self):
        from app import research
        with patch.object(research, "docker", return_value="synthetic") as docker:
            research.image_assets("ghcr.io/synthetic/model@sha256:" + "a" * 64,
                                  self.assets / "new-assets")
        calls = [call.args for call in docker.call_args_list]
        self.assertEqual(calls[1][-1], "/bin/true")
        self.assertEqual([call[0] for call in calls], ["pull", "create", "cp", "rm"])

    def test_model_manifest_failure_precedes_deserialization(self):
        with patch.object(inference, "validate_data_manifest", return_value=self.manifest), \
                patch.object(inference, "validate_model_manifest", side_effect=ValueError("model")), \
                patch("joblib.load") as loader:
            with self.assertRaises(ValueError):
                inference.predict(self.assets, self.assets)
        loader.assert_not_called()

    def test_prediction_shape_probability_and_class_contracts(self):
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        model = LogisticRegression()
        model.n_features_in_ = 2
        model.classes_ = np.array([0, 1])
        model.coef_ = np.array([[0.1, 0.2]])
        model.intercept_ = np.array([0.0])
        from app.prepared import CONFIG
        from app.asset_contract import read_json
        spec = read_json(CONFIG)
        data = {**self.manifest, "prepared_source": spec}
        model_manifest = {"model_source": spec["model"], "model_id": "f" * 64}
        with patch.object(inference, "validate_data_manifest", return_value=data), \
                patch.object(inference, "validate_model_manifest", return_value=model_manifest), \
                patch.object(inference, "validate_registered_model"), \
                patch("joblib.load", return_value=model) as loader:
            result = inference.predict(self.assets, self.assets)
            self.assertEqual(result["context"]["y_true"].tolist(), [1, 0])
            self.assertEqual(result["summary"]["rows"], 2)
            self.assertFalse(result["summary"]["training_performed"])
            self.assertNotIn("y_probability", json.dumps(result["summary"]))
            loader.assert_called_once()
            for output in (np.array([[0.2, 0.8]]),
                           np.array([[0.2, 0.8], [float("nan"), 0.5]]),
                           np.array([[0.2, 0.9], [0.5, 0.5]])):
                with self.subTest(output=str(output.shape)), \
                        patch.object(model, "predict_proba", return_value=output):
                    with self.assertRaises(ValueError):
                        inference.predict(self.assets, self.assets)
            model.classes_ = np.array([1, 0])
            with self.assertRaises(ValueError):
                inference.predict(self.assets, self.assets)
            model.classes_ = np.array([0, 1])
            model.feature_names_in_ = np.array(["f2", "f1"])
            with self.assertRaisesRegex(ValueError, "feature names"):
                inference.predict(self.assets, self.assets)

    def test_unregistered_checkpoint_is_never_loaded(self):
        from app.prepared import CONFIG
        from app.asset_contract import read_json
        spec = read_json(CONFIG)
        data = {"prepared_source": spec, "feature_columns": spec["features"]}
        model = {"model_source": deepcopy(spec["model"])}
        model["model_source"]["checkpoint"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            inference.validate_registered_model(data, model)


if __name__ == "__main__":
    unittest.main()
