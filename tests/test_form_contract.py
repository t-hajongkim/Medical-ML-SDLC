from pathlib import Path
import tempfile
import unittest

from app import research
from app.dataset import ROOT


FORM = ROOT / ".github" / "ISSUE_TEMPLATE" / "research-request.yml"


class FormContractTests(unittest.TestCase):
    def test_labels_and_consent_are_loaded_from_actual_form(self):
        contract = research.form_contract()
        self.assertEqual(len(contract["fields"]), 5)
        self.assertEqual(contract["fields"]["연구 목표"], "research_goal")
        self.assertIn("개인정보", contract["consent"])
        self.assertNotIn("demographic_focus", contract["fields"].values())

    def test_label_rename_changes_parser_mapping_without_a_code_constant(self):
        source = FORM.read_text(encoding="utf-8").replace("label: 연구 목표", "label: 연구 목적")
        with tempfile.TemporaryDirectory(prefix="form-contract-fixture-") as folder:
            path = Path(folder) / "form.yml"
            path.write_text(source, encoding="utf-8")
            contract = research.form_contract(path)
            values = {
                "research_goal": "Synthetic goal", "research_question": "Synthetic question",
                "cohort_requirements": "_No response_", "additional_requests": "_No response_",
                "public_data_confirmation": "- [X] " + contract["consent"],
            }
            body = "\n\n".join(
                f"### {label}\n\n{values[identifier]}"
                for label, identifier in contract["fields"].items())
            request = research.parse_issue(
                {"number": 1, "title": "Synthetic title", "body": body},
                "fixture-owner/fixture-workshop", form_path=path,
            )
            self.assertEqual(request["research_goal"], "Synthetic goal")
            self.assertNotIn("연구 목표", contract["fields"])

    def test_duplicate_id_and_label_are_rejected(self):
        source = FORM.read_text(encoding="utf-8")
        for damaged in (
            source.replace("id: research_question", "id: research_goal"),
            source.replace("label: 연구 질문과 상세 설명", "label: 연구 목표"),
        ):
            with self.subTest(damaged=damaged[:20]):
                with tempfile.TemporaryDirectory(prefix="form-contract-fixture-") as folder:
                    path = Path(folder) / "form.yml"
                    path.write_text(damaged, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        research.form_contract(path)

    def test_invalid_required_confirmation_is_rejected(self):
        source = FORM.read_text(encoding="utf-8")
        old = "          required: true"
        self.assertIn(old, source)
        with tempfile.TemporaryDirectory(prefix="form-contract-fixture-") as folder:
            path = Path(folder) / "form.yml"
            path.write_text(source.replace(old, "          required: false"), encoding="utf-8")
            with self.assertRaises(ValueError):
                research.form_contract(path)


if __name__ == "__main__":
    unittest.main()
