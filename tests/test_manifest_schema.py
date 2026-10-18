import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from manifest_schema import ManifestSchemaError, normalize_slice


class ManifestSchemaTests(unittest.TestCase):
    def test_legacy_aliases_are_normalized(self):
        item = normalize_slice({
            "id": "demo-001-example",
            "goal": "A free-form goal",
            "conclusion": ["The behavior is verified"],
        })
        self.assertEqual(item["objective"], "A free-form goal")
        self.assertEqual(item["completion_criteria"], ["The behavior is verified"])
        self.assertEqual(item["goal"], "A free-form goal")
        self.assertEqual(item["conclusion"], ["The behavior is verified"])

    def test_conflicting_aliases_are_rejected(self):
        with self.assertRaises(ManifestSchemaError):
            normalize_slice({"id": "demo-001", "objective": "one", "goal": "two"})

    def test_canonical_fields_are_preserved(self):
        item = normalize_slice({
            "id": "demo-001",
            "objective": "one",
            "completion_criteria": ["done"],
        })
        self.assertEqual(item["objective"], "one")
        self.assertEqual(item["completion_criteria"], ["done"])


if __name__ == "__main__":
    unittest.main()
