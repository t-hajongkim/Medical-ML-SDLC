import csv
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import time
import unittest
import uuid

from app.asset_contract import read_json
from app.bootstrap import verify_asset_directories


DATA_IMAGE = os.environ.get("WORKSHOP_DATA_IMAGE")
MODEL_IMAGE = os.environ.get("WORKSHOP_MODEL_IMAGE")
PREPARED_DIRECTORY = os.environ.get("WORKSHOP_PREPARED_DIR")


def docker(*arguments, env=None):
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True,
        encoding="utf-8", timeout=180, env=env,
    )
    if result.returncode:
        raise RuntimeError(f"Docker {arguments[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@unittest.skipUnless(DATA_IMAGE or MODEL_IMAGE or PREPARED_DIRECTORY, "Container execution is performed by the operator GitHub workflow.")
class PreparedImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all((DATA_IMAGE, MODEL_IMAGE, PREPARED_DIRECTORY)):
            raise ValueError("Both built image references and the prepared directory are required.")
        cls.prepared = Path(PREPARED_DIRECTORY)
        cls.inspection = verify_asset_directories(
            cls.prepared / "data" / "assets",
            cls.prepared / "model" / "assets",
        )
        cls.container = "prepared-db-verify-" + uuid.uuid4().hex[:12]
        environment = {**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(32)}
        docker(
            "run", "--detach", "--rm", "--network", "none",
            "--name", cls.container, "--env", "POSTGRES_PASSWORD", DATA_IMAGE,
            env=environment,
        )
        cls.addClassCleanup(docker, "rm", "--force", cls.container)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", "--format", "{{json .State}}", cls.container))
            if state.get("Health", {}).get("Status") == "healthy":
                return
            if not state["Running"] or state.get("Health", {}).get("Status") == "unhealthy":
                raise RuntimeError("The prepared database did not initialize successfully.")
            time.sleep(1)
        raise TimeoutError("The prepared database did not become healthy.")

    def query(self, sql):
        return json.loads(docker(
            "exec", self.container, "psql", "-X", "-w", "-qAt",
            "-U", "postgres", "-d", "workshop", "-v", "ON_ERROR_STOP=1", "-c", sql,
        ))

    def test_every_metadata_value_is_preserved(self):
        with (self.prepared / "data" / "assets" / "metadata.csv").open(encoding="utf-8", newline="") as source:
            expected = {row["row_key"]: row for row in csv.DictReader(source)}
        actual = self.query("SELECT json_agg(row_to_json(m)) FROM prepared.metadata AS m")
        self.assertEqual(len(actual), len(expected))
        for row in actual:
            reference = expected[row["row_key"]]
            for column, value in reference.items():
                target = int(value) if column == "target" else value or None
                self.assertTrue(row[column] == target, f"Prepared metadata mismatch at column {column}.")

    def test_every_feature_vector_matches_prepared_data(self):
        with (self.prepared / "data" / "assets" / "db_features.csv").open(encoding="utf-8", newline="") as source:
            expected = {row["row_key"]: row for row in csv.DictReader(source)}
        actual = self.query("SELECT json_agg(row_to_json(f)) FROM prepared.features AS f")
        self.assertEqual(len(actual), len(expected))
        for row in actual:
            reference = expected[row["row_key"]]
            values = json.loads(reference["feature_values"])
            self.assertEqual(row["split"], reference["split"])
            self.assertEqual(len(row["feature_values"]), len(values))
            self.assertTrue(
                all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) for a, b in zip(row["feature_values"], values)),
                "Prepared feature vector differs from the supplied asset.",
            )

    def test_database_has_no_cross_split_patient_overlap(self):
        overlap = self.query(
            "SELECT count(*) FROM (SELECT patient_key FROM prepared.metadata "
            "GROUP BY patient_key HAVING count(DISTINCT split) > 1) AS overlaps"
        )
        self.assertEqual(overlap, 0)
        pair = self.query("SELECT row_to_json(i) FROM prepared.asset_info AS i")
        self.assertEqual(pair["pair_id"], self.inspection["pair_id"])
        self.assertEqual(pair["expected_rows"], self.inspection["rows"])

    def test_inference_image_reproduces_operator_checkpoint(self):
        verification = self.prepared / "verification"
        docker(
            "run", "--rm", "--network", "none",
            "--mount", f"type=bind,source={verification.resolve()},target=/run/verification",
            MODEL_IMAGE, "--input", "/run/verification/input.json",
            "--output", "/run/verification/actual.json",
        )
        actual = read_json(verification / "actual.json")
        expected = read_json(verification / "expected.json")
        request = read_json(verification / "input.json")
        self.assertEqual(actual["pair_id"], self.inspection["pair_id"])
        self.assertEqual(actual["row_keys"], request["row_keys"])
        self.assertEqual(len(actual["probabilities"]), len(expected["probabilities"]))
        self.assertTrue(all(
            math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10)
            for a, b in zip(actual["probabilities"], expected["probabilities"])
        ))


if __name__ == "__main__":
    unittest.main()
