"""Pages prerequisite checks must not mistake missing setup for deployment success."""

import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from app.dataset import ROOT


class DashboardWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "deploy-dashboard.yml").read_text(encoding="utf-8"))
        run = self.workflow["jobs"]["check-pages"]["steps"][0]["run"]
        self.script = run.split("python -B - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

    def check(self, metadata):
        with tempfile.TemporaryDirectory(prefix="pages-preflight-") as temporary:
            directory = Path(temporary)
            output, summary = directory / "output", directory / "summary"
            with patch.dict(os.environ, {
                    "GITHUB_REPOSITORY": "fixture-owner/workshop",
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_STEP_SUMMARY": str(summary)}), \
                    patch.object(subprocess, "check_output", return_value=json.dumps(metadata).encode()), \
                    patch("sys.stdout", new_callable=io.StringIO):
                exec(compile(self.script, "pages-prerequisite", "exec"), {})
            return output.read_text(encoding="utf-8"), (
                summary.read_text(encoding="utf-8") if summary.exists() else "")

    def test_missing_pages_is_explicitly_not_deployed_and_skipped(self):
        output, summary = self.check({"has_pages": False})
        self.assertEqual(output, "enabled=false\n")
        self.assertIn("아직 배포하지 않았습니다", summary)
        self.assertIn("https://github.com/fixture-owner/workshop/settings/pages", summary)
        deploy = self.workflow["jobs"]["deploy"]
        self.assertEqual(deploy["needs"], "check-pages")
        self.assertEqual(deploy["if"], "needs.check-pages.outputs.enabled == 'true'")

    def test_enabled_pages_proceeds_without_setup_warning(self):
        output, summary = self.check({"has_pages": True})
        self.assertEqual(output, "enabled=true\n")
        self.assertEqual(summary, "")

    def test_missing_metadata_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(ValueError):
            self.check({})

    def test_settings_check_has_no_pages_write_or_oidc_permission(self):
        self.assertEqual(
            self.workflow["jobs"]["check-pages"]["permissions"], {"contents": "read"})


if __name__ == "__main__":
    unittest.main()
