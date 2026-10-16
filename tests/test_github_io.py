"""Synthetic Git objects verify shared file access without network calls."""

import base64
import hashlib
import unittest
from unittest.mock import Mock, patch

from app import github_io


REPO = "synthetic/workshop"
COMMIT = "a" * 40
PATH = "research/reports/issue-7/input-mapping.json"


class GitHubFileTests(unittest.TestCase):
    def setUp(self):
        self.content = b'{"status":"validated"}'
        self.sha = hashlib.sha1(
            b"blob " + str(len(self.content)).encode() + b"\0" + self.content).hexdigest()
        self.entry = {"path": PATH, "type": "blob", "mode": "100644",
                      "size": len(self.content), "sha": self.sha}
        self.blob = {"encoding": "base64",
                     "content": base64.b64encode(self.content).decode("ascii")}
        self.api = Mock(side_effect=lambda endpoint: (
            {"truncated": False, "tree": [self.entry]} if "/git/trees/" in endpoint else self.blob))

    def test_tree_and_file_are_pinned_to_the_supplied_commit_and_blob(self):
        tree = github_io.read_tree(REPO, COMMIT, self.api)
        self.assertEqual(github_io.read_file(REPO, tree, PATH, self.api), self.content)
        self.assertEqual(self.api.call_args_list[0].args[0],
                         f"repos/{REPO}/git/trees/{COMMIT}?recursive=1")
        self.assertEqual(self.api.call_args_list[1].args[0],
                         f"repos/{REPO}/git/blobs/{self.sha}")

    def test_optional_missing_file_is_distinct_from_corrupt_or_required_file(self):
        self.assertIsNone(github_io.read_file(REPO, {}, PATH, self.api, optional=True))
        self.api.assert_not_called()
        with self.assertRaises(ValueError):
            github_io.read_file(REPO, {}, PATH, self.api)

    def test_truncated_or_malformed_tree_is_rejected(self):
        for response in ({"truncated": True, "tree": [self.entry]},
                         {"truncated": False, "tree": None}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                github_io.read_tree(REPO, COMMIT, Mock(return_value=response))

    def test_invalid_commit_is_rejected_before_api_access(self):
        for value in (None, "../main", "A" * 40):
            with self.subTest(value=value), self.assertRaises(ValueError):
                github_io.read_tree(REPO, value, self.api)
        self.api.assert_not_called()

    def test_only_regular_bounded_files_are_accepted(self):
        for update in ({"mode": "120000"}, {"type": "tree"}, {"size": 1_000_001}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                github_io.read_file(REPO, {PATH: {**self.entry, **update}}, PATH,
                                    self.api, max_bytes=1_000_000)
        self.api.assert_not_called()

    def test_actual_decoded_size_is_checked_independently(self):
        with self.assertRaises(ValueError):
            github_io.read_file(REPO, {PATH: {**self.entry, "size": 1}}, PATH,
                                self.api, max_bytes=2)

    def test_encoding_base64_and_blob_hash_errors_are_explicit(self):
        for update in ({"encoding": "none"}, {"content": "not-base64"},
                       {"content": base64.b64encode(b"changed").decode()}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                github_io.read_file(REPO, {PATH: self.entry}, PATH,
                                    Mock(return_value={**self.blob, **update}))

    def test_json_objects_are_strict_and_utf8(self):
        self.assertEqual(github_io.read_object(self.content), {"status": "validated"})
        for content in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'\xff', b'{'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                github_io.read_object(content)




if __name__ == "__main__":
    unittest.main()
