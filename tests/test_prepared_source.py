import ast
import copy
import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app.asset_contract import file_hash, read_json, write_json
from app.dataset import ROOT, SOURCE_URL
from app.prepared import (
    CONFIG, FEATURE_COLUMNS, INFO_COLUMNS, METADATA_COLUMNS, SPLITS,
    compare_database_csv, download_resource, prepare_data, smoke_database,
    model_identifier, prepare_model, validate_data_manifest, validate_model_manifest,
    validate_source_contract, verify_model_image,
)


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def resource(path):
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path)}


class PreparedSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="source-contract-", dir=ROOT.parent)
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.source = self.folder / "supplied"
        self.source.mkdir()
        self.output = self.folder / "imported"
        self.spec = copy.deepcopy(read_json(CONFIG))
        self.spec["features"] = ["synthetic_a", "synthetic_b"]
        self.spec["expected_patient_overlap"] = {
            "train_validation": 1, "train_test": 1, "validation_test": 1,
        }
        self.spec["expected_test_race_missing"] = 1
        self.model_source = self.folder / "model-source"
        self.model_source.mkdir()
        (self.model_source / "logistic_regression.pkl").write_bytes(b"synthetic artifact; never deserialize")
        (self.model_source / "MODEL_SOURCE_README.txt").write_text("Synthetic model source, MIT license", encoding="utf-8")
        self.spec["model"]["checkpoint"] = resource(self.model_source / "logistic_regression.pkl")
        self.spec["model"]["readme"] = {
            **resource(self.model_source / "MODEL_SOURCE_README.txt"), "name": "README.md",
        }
        raw_rows = []
        for index, split in enumerate(SPLITS):
            keys = [f"synthetic-enc-{index}-0", f"synthetic-enc-{index}-1"]
            patients = [f"synthetic-subject-{index}", f"synthetic-subject-{(index + 1) % 3}"]
            race = ["Caucasian" if split == "test" else "synthetic-race", "synthetic-race"]
            write_csv(self.source / f"{split}.csv",
                      ["synthetic_a", "synthetic_b", "target"],
                      [["-0.25", "1.2345678901234567", "1"], ["0.50", "-2.5", "0"]])
            write_csv(self.source / f"{split}_demographics.csv",
                      self.spec["demographics_columns"],
                      [[race[i], "synthetic-gender", "synthetic-age", keys[i]] for i in range(2)])
            raw_rows.extend([
                [keys[i], patients[i], "?" if split == "test" and i == 0 else race[i],
                 "synthetic-gender", "synthetic-age", "<30" if i == 0 else "NO"]
                for i in range(2)
            ])
            self.spec["splits"][split] = {
                "rows": 2, "data": resource(self.source / f"{split}.csv"),
                "demographics": resource(self.source / f"{split}_demographics.csv"),
            }
        self.raw = self.folder / "original.csv"
        raw_columns = ["encounter_id", "patient_nbr", "race", "gender", "age", "readmitted"]
        write_csv(self.raw, raw_columns, raw_rows)
        self.raw_manifest = self.folder / "raw-manifest.json"
        self.raw_spec = {
            **resource(self.raw), "source_url": SOURCE_URL, "columns": raw_columns,
            "rows": 6, "target": "readmitted", "missing_tokens": ["", "?", "NaN"],
            "id_columns": ["encounter_id", "patient_nbr"],
        }
        write_json(self.raw_manifest, self.raw_spec)
        self.config = self.folder / "prepared-source.json"
        write_json(self.config, self.spec)

    def prepare(self):
        return prepare_data(
            self.output, self.source, self.raw, self.config, self.raw_manifest, "synthetic-test",
        )

    def change_csv(self, split, kind, transform):
        path = self.source / self.spec["splits"][split][kind]["name"]
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        transform(rows)
        write_csv(path, rows[0], rows[1:])
        self.spec["splits"][split][kind] = resource(path)
        write_json(self.config, self.spec)

    def test_frozen_manifest_names_hashes_rows_and_113_features(self):
        spec = read_json(CONFIG)
        validate_source_contract(spec)
        self.assertEqual(len(spec["features"]), 113)
        self.assertEqual(sum(entry["rows"] for entry in spec["splits"].values()), 101766)
        self.assertEqual(spec["revision"], "4a61a3591f079628c72f5edbc7494ca7dda8741d")
        self.assertNotIn("expected_patient_overlap", spec)
        self.assertNotIn("expected_test_race_missing", spec)
        self.assertNotIn("input_metadata", spec)
        self.assertEqual(spec["splits"]["train"]["data"]["sha256"],
                         "71895ccb1886d8fd3d95c60542098ce59133e06fea7c372e91d770347bf5bc09")
        self.assertEqual(spec["target_mapping"], {"<30": 1, ">30": 0, "NO": 0})

    def test_publisher_computes_observations_without_golden_eda_values(self):
        self.spec.pop("expected_patient_overlap")
        self.spec.pop("expected_test_race_missing")
        write_json(self.config, self.spec)
        result = self.prepare()
        manifest = read_json(self.output / "data" / "assets" / "data_manifest.json")
        self.assertEqual(result["patient_overlap"],
                         {"train_validation": 1, "train_test": 1, "validation_test": 1})
        self.assertEqual(manifest["race_missing"]["test"], 1)
        self.assertNotIn("expected_patient_overlap", manifest["prepared_source"])
        validate_data_manifest(self.output / "data" / "assets")

    def test_import_preserves_source_bytes_feature_values_and_order(self):
        result = self.prepare()
        assets = self.output / "data" / "assets"
        manifest = validate_data_manifest(assets)
        self.assertEqual(result["rows"], 6)
        self.assertEqual(result["features"], 2)
        self.assertEqual(result["split_rows"], {"train": 2, "validation": 2, "test": 2})
        self.assertEqual(result["data_id"], manifest["data_id"])
        self.assertFalse(result["training_performed"])
        self.assertFalse(result["preprocessing_performed"])
        for split in SPLITS:
            for kind in ("data", "demographics"):
                name = self.spec["splits"][split][kind]["name"]
                self.assertEqual((assets / name).read_bytes(), (self.source / name).read_bytes())
        with (assets / "db_features.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["split"] for row in rows], ["train"] * 2 + ["validation"] * 2 + ["test"] * 2)
        self.assertEqual([row["row_position"] for row in rows], ["1", "2"] * 3)
        self.assertEqual(json.loads(rows[0]["feature_values"]), [-0.25, 1.2345678901234567])
        self.assertEqual(manifest["feature_columns"], self.spec["features"])

    def test_original_patient_keys_overlap_and_missing_race_are_preserved(self):
        result = self.prepare()
        self.assertEqual(result["patient_overlap"], self.spec["expected_patient_overlap"])
        self.assertEqual(result["race_missing"], {"train": 0, "validation": 0, "test": 1})
        with (self.output / "data" / "assets" / "metadata.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        missing = [row for row in rows if row["race_was_missing"] == "true"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["race"], "Caucasian")
        self.assertEqual(missing[0]["race_original"], "")
        self.assertTrue(all(row["patient_key"].startswith("synthetic-subject-") for row in rows))
        self.assertTrue(all(row["race_original"] == row["race"] for row in rows if row not in missing))

    def test_existing_output_and_repository_output_are_rejected_without_overwrite(self):
        self.output.mkdir()
        sentinel = self.output / "sentinel"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "new prepared output"):
            self.prepare()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        with self.assertRaisesRegex(ValueError, "outside"):
            prepare_data(ROOT / "forbidden", self.source, self.raw, self.config, self.raw_manifest)

    def test_bad_size_and_checksum_fail_before_packaging(self):
        path = self.source / "train.csv"
        for payload in (path.read_bytes() + b"x", path.read_bytes().replace(b"0.50", b"0.51")):
            with self.subTest(payload_size=len(payload)):
                original = path.read_bytes()
                path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "integrity"):
                    self.prepare()
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_wrong_feature_order_is_rejected(self):
        self.change_csv("train", "data", lambda rows: rows[0].reverse())
        with self.assertRaisesRegex(ValueError, "feature order"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_nonfinite_and_nonnumeric_features_are_rejected(self):
        original = (self.source / "train.csv").read_bytes()
        for value in ("nan", "inf", "not-a-number"):
            with self.subTest(value=value):
                (self.source / "train.csv").write_bytes(original)
                self.change_csv("train", "data", lambda rows: rows[1].__setitem__(0, value))
                with self.assertRaisesRegex(ValueError, "finite|numeric"):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_nonbinary_and_misaligned_targets_are_rejected(self):
        original = (self.source / "train.csv").read_bytes()
        for value in ("0", "2", "1.0"):
            with self.subTest(value=value):
                (self.source / "train.csv").write_bytes(original)
                self.change_csv("train", "data", lambda rows: rows[1].__setitem__(2, value))
                with self.assertRaisesRegex(ValueError, "Target row alignment"):
                    self.prepare()

    def test_demographic_row_order_and_schema_mismatches_are_rejected(self):
        self.change_csv("train", "demographics", lambda rows: rows.reverse())
        with self.assertRaisesRegex(ValueError, "schema"):
            self.prepare()

    def test_target_checks_detect_reordered_sidecar_rows(self):
        self.change_csv("train", "demographics", lambda rows: rows.__setitem__(slice(1, None), rows[:0:-1]))
        with self.assertRaisesRegex(ValueError, "Target row alignment"):
            self.prepare()

    def test_demographic_values_must_match_original(self):
        self.change_csv("train", "demographics", lambda rows: rows[1].__setitem__(1, "wrong-gender"))
        with self.assertRaisesRegex(ValueError, "Demographic row alignment"):
            self.prepare()

    def test_imputed_race_must_match_known_source_rule(self):
        self.change_csv("test", "demographics", lambda rows: rows[1].__setitem__(0, "invented-race"))
        with self.assertRaisesRegex(ValueError, "Race row alignment"):
            self.prepare()

    def test_duplicate_or_absent_encounter_keys_fail(self):
        original = (self.source / "train_demographics.csv").read_bytes()
        for key in ("synthetic-enc-0-0", "absent-synthetic-encounter"):
            with self.subTest(key=key):
                (self.source / "train_demographics.csv").write_bytes(original)
                self.change_csv("train", "demographics", lambda rows: rows[2].__setitem__(3, key))
                with self.assertRaisesRegex(ValueError, "Encounter key"):
                    self.prepare()

    def test_unequal_sidecar_lengths_and_frozen_row_counts_fail(self):
        self.change_csv("train", "demographics", lambda rows: rows.pop())
        with self.assertRaisesRegex(ValueError, "row count"):
            self.prepare()

    def test_frozen_row_count_is_checked_after_integrity(self):
        self.spec["splits"]["train"]["rows"] = 3
        write_json(self.config, self.spec)
        with self.assertRaisesRegex(ValueError, "split row count"):
            self.prepare()

    def test_known_overlap_cannot_be_silently_repaired_or_hidden(self):
        self.spec["expected_patient_overlap"]["train_test"] = 0
        write_json(self.config, self.spec)
        with self.assertRaisesRegex(ValueError, "Patient overlap"):
            self.prepare()

    def test_original_missing_race_contract_is_enforced(self):
        self.spec["expected_test_race_missing"] = 0
        write_json(self.config, self.spec)
        with self.assertRaisesRegex(ValueError, "missing-race"):
            self.prepare()

    def test_original_keys_are_required_and_raw_hash_is_enforced(self):
        self.raw.write_bytes(self.raw.read_bytes() + b"x")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_missing_patient_key_is_rejected_after_raw_integrity(self):
        with self.raw.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        rows[1][1] = "?"
        write_csv(self.raw, rows[0], rows[1:])
        self.raw_spec.update(resource(self.raw))
        write_json(self.raw_manifest, self.raw_spec)
        with self.assertRaisesRegex(ValueError, "patient keys"):
            self.prepare()

    def test_source_url_revision_and_resource_names_cannot_drift(self):
        for key, value in (("base_url", "https://example.invalid/"),
                           ("revision", "main"), ("features", ["target"])):
            with self.subTest(key=key):
                changed = {**self.spec, key: value}
                with self.assertRaises(ValueError):
                    validate_source_contract(changed)
        changed = copy.deepcopy(self.spec)
        changed["splits"]["train"]["data"]["name"] = "../train.csv"
        with self.assertRaises(ValueError):
            validate_source_contract(changed)

    def test_stream_download_limits_size_and_removes_invalid_files(self):
        original = self.source / "train.csv"
        item = resource(original)
        destination = self.folder / "download.csv"
        with patch("app.prepared.urlopen", return_value=io.BytesIO(original.read_bytes())) as request:
            download_resource(destination, item, self.spec["base_url"])
        request.assert_called_once_with(self.spec["base_url"] + "train.csv", timeout=60)
        self.assertEqual(destination.read_bytes(), original.read_bytes())
        destination.unlink()
        for payload in (original.read_bytes()[:-1], original.read_bytes() + b"x",
                        original.read_bytes().replace(b"0.50", b"0.51")):
            with self.subTest(length=len(payload)):
                with patch("app.prepared.urlopen", return_value=io.BytesIO(payload)):
                    with self.assertRaises(ValueError):
                        download_resource(destination, item, self.spec["base_url"])
                self.assertFalse(destination.exists())

    def test_default_download_flow_fetches_only_frozen_data_and_model_resources(self):
        payloads = {
            self.spec["base_url"] + entry[kind]["name"]:
                (self.source / entry[kind]["name"]).read_bytes()
            for entry in self.spec["splits"].values() for kind in ("data", "demographics")
        }
        payloads.update({
            self.spec["model"]["base_url"] + "logistic_regression.pkl":
                (self.model_source / "logistic_regression.pkl").read_bytes(),
            self.spec["model"]["base_url"] + "README.md":
                (self.model_source / "MODEL_SOURCE_README.txt").read_bytes(),
        })
        def fetch(url, timeout):
            return io.BytesIO(payloads[url])
        def original_source(destination, manifest):
            shutil.copyfile(self.raw, destination)
        with patch("app.prepared.urlopen", side_effect=fetch) as requests, \
                patch("app.prepared.download_source", side_effect=original_source) as raw_request:
            result = prepare_data(
                self.output, config_path=self.config, raw_manifest_path=self.raw_manifest,
            )
            model = prepare_model(self.output)
        self.assertEqual(result["rows"], 6)
        self.assertFalse(model["inference_performed_by_publisher"])
        self.assertEqual({call.args[0] for call in requests.call_args_list}, set(payloads))
        self.assertEqual(requests.call_count, 8)
        self.assertEqual(raw_request.call_count, 1)
        self.assertFalse((self.output / "source").exists())
        self.assertFalse(any("scaler" in call.args[0] for call in requests.call_args_list))

    def test_packaging_excludes_original_rows_models_and_python_runtime(self):
        self.prepare()
        context = self.output / "data"
        self.assertFalse((self.output / "source").exists())
        self.assertFalse((self.output / "model").exists())
        self.assertFalse(list(context.rglob("*.py")))
        self.assertFalse(list(context.rglob("*.pkl")))
        self.assertFalse(list(context.rglob("*.joblib")))
        self.assertFalse(list(context.rglob("*raw*")))
        for source, destination in (
            ("db/Dockerfile", "Dockerfile"), (".dockerignore", ".dockerignore"),
            ("db/init.sql", "db/init.sql"), ("db/healthcheck.sh", "db/healthcheck.sh"),
        ):
            self.assertEqual((ROOT / source).read_bytes(), (context / destination).read_bytes())

    def test_packaged_file_corruption_and_contract_drift_fail(self):
        self.prepare()
        assets = self.output / "data" / "assets"
        path = assets / "metadata.csv"
        original = path.read_bytes()
        path.write_bytes(original + b"x")
        with self.assertRaisesRegex(ValueError, "integrity"):
            validate_data_manifest(assets)
        path.write_bytes(original)
        manifest_path = assets / "data_manifest.json"
        manifest = read_json(manifest_path)
        manifest["prepared_source"]["features"].reverse()
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            validate_data_manifest(assets)

    def test_extra_data_files_and_aggregate_manifest_drift_are_rejected(self):
        self.prepare()
        assets = self.output / "data" / "assets"
        unexpected = assets / "unregistered.csv"
        unexpected.write_text("synthetic unexpected file", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unexpected files"):
            validate_data_manifest(assets)
        unexpected.unlink()
        manifest_path = assets / "data_manifest.json"
        manifest = read_json(manifest_path)
        manifest["race_missing"]["train"] = 1
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "database input contract"):
            validate_data_manifest(assets)

    def test_full_database_comparison_checks_values_counts_and_features(self):
        self.prepare()
        assets = self.output / "data" / "assets"
        for filename, columns, label, expected in (
            ("metadata.csv", METADATA_COLUMNS, "metadata", 6),
            ("db_features.csv", FEATURE_COLUMNS, "features", 6),
            ("asset_info.csv", INFO_COLUMNS, "asset_info", 1),
        ):
            with self.subTest(label=label):
                path = assets / filename
                actual = path.read_text(encoding="utf-8")
                self.assertEqual(compare_database_csv(path, actual, columns, label), expected)
                with self.assertRaisesRegex(ValueError, "row count"):
                    compare_database_csv(path, "\n".join(actual.splitlines()[:-1]), columns, label)
        actual = (assets / "db_features.csv").read_text(encoding="utf-8").replace("-0.25", "-0.26", 1)
        with self.assertRaisesRegex(ValueError, "value mismatch"):
            compare_database_csv(assets / "db_features.csv", actual, FEATURE_COLUMNS, "features")

    def test_runner_smoke_is_data_only_no_ports_and_compares_every_row(self):
        self.prepare()
        assets = self.output / "data" / "assets"
        replies = [
            "container-id", json.dumps({"Running": True, "Health": {"Status": "healthy"}}),
            (assets / "metadata.csv").read_text(encoding="utf-8"),
            (assets / "db_features.csv").read_text(encoding="utf-8"),
            (assets / "asset_info.csv").read_text(encoding="utf-8"), "removed",
        ]
        with patch("app.prepared.docker", side_effect=replies) as calls:
            result = smoke_database("ghcr.io/example/workshop-db:synthetic", self.output)
        self.assertEqual(result["compared_rows"], {"metadata": 6, "features": 6, "asset_info": 1})
        run = calls.call_args_list[0]
        self.assertIn("--network", run.args)
        self.assertIn("none", run.args)
        self.assertNotIn("-p", run.args)
        self.assertNotIn("--publish", run.args)
        self.assertIn("POSTGRES_PASSWORD", run.kwargs["env"])
        self.assertEqual(calls.call_args_list[-1].args[:2], ("rm", "--force"))
        self.assertTrue((self.output / "database-smoke-summary.json").is_file())

    def test_runner_smoke_failure_never_publishes_and_cleans_container(self):
        self.prepare()
        with patch("app.prepared.docker", side_effect=[
            "container", json.dumps({"Running": False}), "removed",
        ]) as calls:
            with self.assertRaisesRegex(RuntimeError, "initialize"):
                smoke_database("ghcr.io/example/workshop-db:synthetic", self.output)
        self.assertEqual(calls.call_args_list[-1].args[:2], ("rm", "--force"))
        self.assertFalse((self.output / "database-smoke-summary.json").exists())

    def test_model_artifact_is_opaque_and_matches_prepared_feature_contract(self):
        data_result = self.prepare()
        result = prepare_model(self.output, self.model_source)
        data = validate_data_manifest(self.output / "data" / "assets")
        assets = self.output / "model" / "assets"
        model = validate_model_manifest(assets, data)
        self.assertEqual(result["data_id"], data_result["data_id"])
        self.assertEqual(model["feature_columns"], self.spec["features"])
        self.assertEqual(model["target_mapping"], {"<30": 1, ">30": 0, "NO": 0})
        self.assertEqual(model["classes"], [0, 1])
        self.assertEqual(model["positive_class"], 1)
        self.assertEqual(model["model_source"]["artifact_scikit_learn_version"], "1.2.2")
        self.assertEqual(model["model_source"]["local_validation_scikit_learn_version"], "1.6.1")
        self.assertFalse(model["training_performed"])
        self.assertFalse(model["inference_performed_by_publisher"])
        self.assertEqual((assets / "logistic_regression.pkl").read_bytes(),
                         (self.model_source / "logistic_regression.pkl").read_bytes())
        dockerfile = (self.output / "model" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM scratch", dockerfile)
        self.assertNotIn("ENTRYPOINT", dockerfile)
        self.assertNotIn("RUN ", dockerfile)
        self.assertFalse(list((self.output / "model").rglob("*.py")))
        self.assertFalse(list((self.output / "model").rglob("*.csv")))
        self.assertFalse(list((self.output / "data").rglob("*.pkl")))
        self.assertNotIn("scaler", " ".join(path.name for path in assets.iterdir()))

    def test_model_source_integrity_failure_cleans_artifact_context(self):
        self.prepare()
        checkpoint = self.model_source / "logistic_regression.pkl"
        checkpoint.write_bytes(checkpoint.read_bytes() + b"x")
        with self.assertRaisesRegex(ValueError, "integrity"):
            prepare_model(self.output, self.model_source)
        self.assertFalse((self.output / "model").exists())
        self.assertTrue((self.output / "data").is_dir())

    def test_model_manifest_rejects_wrong_data_feature_order_or_extra_files(self):
        self.prepare()
        prepare_model(self.output, self.model_source)
        data = validate_data_manifest(self.output / "data" / "assets")
        assets = self.output / "model" / "assets"
        original = read_json(assets / "model_manifest.json")
        for field, value in (("data_id", "a" * 64),
                             ("feature_columns", list(reversed(self.spec["features"]))),
                             ("inference_performed_by_publisher", True)):
            with self.subTest(field=field):
                changed = {**original, field: value}
                changed["model_id"] = model_identifier(changed)
                write_json(assets / "model_manifest.json", changed)
                with self.assertRaisesRegex(ValueError, "inconsistent"):
                    validate_model_manifest(assets, data)
        write_json(assets / "model_manifest.json", original)
        (assets / "scaler.pkl").write_bytes(b"forbidden synthetic transformer")
        with self.assertRaisesRegex(ValueError, "Unexpected files"):
            validate_model_manifest(assets, data)

    def test_built_model_files_are_checked_without_running_container(self):
        self.prepare()
        prepare_model(self.output, self.model_source)
        def fake_docker(*arguments, **kwargs):
            if arguments[0] == "cp":
                shutil.copytree(self.output / "model" / "assets", Path(arguments[2]), dirs_exist_ok=True)
            return "artifact-container"
        with patch("app.prepared.docker", side_effect=fake_docker) as calls:
            result = verify_model_image("ghcr.io/example/workshop-model:synthetic", self.output)
        self.assertFalse(result["inference_performed"])
        self.assertEqual([call.args[0] for call in calls.call_args_list], ["create", "cp", "rm"])
        self.assertNotIn("run", [call.args[0] for call in calls.call_args_list])
        self.assertFalse((self.output / "model-image-inspection").exists())
        self.assertTrue((self.output / "model-image-summary.json").is_file())

    def test_built_model_corruption_is_rejected_and_inspection_is_cleaned(self):
        self.prepare()
        prepare_model(self.output, self.model_source)
        def fake_docker(*arguments, **kwargs):
            if arguments[0] == "cp":
                target = Path(arguments[2])
                shutil.copytree(self.output / "model" / "assets", target, dirs_exist_ok=True)
                (target / "logistic_regression.pkl").write_bytes(b"corrupt synthetic artifact")
            return "artifact-container"
        with patch("app.prepared.docker", side_effect=fake_docker) as calls:
            with self.assertRaisesRegex(ValueError, "integrity"):
                verify_model_image("ghcr.io/example/workshop-model:synthetic", self.output)
        self.assertEqual(calls.call_args_list[-1].args[:2], ("rm", "--force"))
        self.assertFalse((self.output / "model-image-inspection").exists())
        self.assertFalse((self.output / "model-image-summary.json").exists())

    def test_publisher_uses_stdlib_data_import_not_legacy_training_or_model(self):
        tree = ast.parse((ROOT / "app" / "prepared.py").read_text(encoding="utf-8"))
        forbidden = {"fit", "fit_transform", "predict", "predict_proba", "load"}
        self.assertFalse(any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden for node in ast.walk(tree)
        ))
        imports = {
            name.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
            for name in node.names
        }
        imports.update(node.module.split(".")[0] for node in ast.walk(tree)
                       if isinstance(node, ast.ImportFrom) and node.module)
        self.assertFalse(imports & {"numpy", "pandas", "sklearn", "joblib", "pickle"})
        workflow = (ROOT / ".github" / "workflows" / "publish-db-image.yml").read_text(encoding="utf-8")
        self.assertIn("name: Establish Database Image", workflow)
        self.assertIn("app.prepared prepare", workflow)
        self.assertIn("app.prepared smoke", workflow)
        self.assertIn('test_prepared_source.py', workflow)
        self.assertLess(workflow.index("app.prepared smoke"), workflow.index("docker login"))
        self.assertLess(workflow.index("docker login"), workflow.index("docker push"))
        self.assertIn("app.prepared verify-model-image", workflow)
        self.assertIn("--kind model", workflow)
        self.assertIn("model_digest", workflow)
        self.assertLess(workflow.index("app.prepared verify-model-image"), workflow.index("docker login"))
        for forbidden_text in ("app.assets", "app.infer", "pip install", ".fit(", ".predict(",
                               "requirements-ml", "git commit", "git push", "contents: write"):
            self.assertNotIn(forbidden_text, workflow)
        self.assertEqual(workflow.count("docker build"), 2)
        self.assertEqual(workflow.count("docker push"), 2)
        self.assertIn("packages: write", workflow)
        for retired in (
            ".github/workflows/initialize-workshop.yml",
            "app/assets.py", "app/bootstrap.py", "app/infer.py", "config/preparation.json",
            "tests/test_prepared_assets.py", "tests/test_database_image.py",
        ):
            with self.subTest(retired=retired):
                self.assertFalse(ROOT.joinpath(*retired.split("/")).exists())


if __name__ == "__main__":
    unittest.main()
