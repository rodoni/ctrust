import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from artifact_sync.transaction import TransactionError, WorkspaceLock
from inventory_transaction import publish_inventory
from workflow import INVENTORY_FIELDS, load


def generate_bundle(stage, generation):
    for name, fields in INVENTORY_FIELDS.items():
        data = {key: kind() for key, kind in fields.items()}
        data.update(slice="demo-001", version=1)
        if name == "manifest.yaml":
            data["generation"] = generation
        (stage / name).write_text(yaml.safe_dump(data))
    path = stage / 'manifest.yaml'
    manifest = load(path)
    manifest['artifact_digests'] = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in INVENTORY_FIELDS if name != 'manifest.yaml'}
    path.write_text(yaml.safe_dump(manifest))


class InventoryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "slices/demo-001"

    def snapshot(self):
        return {str(path.relative_to(self.output)): path.read_bytes() for path in self.output.rglob("*") if path.is_file()}

    def test_initial_and_replacement_publications_archive_the_previous_generation(self):
        first = publish_inventory(self.output, "demo-001", generate_bundle)
        (self.output / "manual-notes.md").write_text("Keep this analysis")
        before = self.snapshot()
        second = publish_inventory(self.output, "demo-001", generate_bundle)
        self.assertNotEqual(first, second)
        self.assertEqual(load(self.output / "manifest.yaml")["generation"], second)
        archive = self.output.parent / ".inventory-transactions" / second / "snapshot"
        self.assertEqual({str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()}, before)
        self.assertEqual((self.output / "manual-notes.md").read_text(), "Keep this analysis")

    def test_generation_failure_preserves_live_inventory(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        before = self.snapshot()
        def fail(stage, generation):
            (stage / "files.yaml").write_text("partial")
            raise RuntimeError("interrupted during generation")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            publish_inventory(self.output, "demo-001", fail)
        self.assertEqual(before, self.snapshot())

    def test_invalid_staged_bundle_never_becomes_live(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        before = self.snapshot()
        def invalid(stage, generation):
            generate_bundle(stage, generation)
            (stage / "behavior.yaml").unlink()
        with self.assertRaisesRegex(TransactionError, "behavior.yaml"):
            publish_inventory(self.output, "demo-001", invalid)
        self.assertEqual(before, self.snapshot())

    def test_failed_atomic_exchange_preserves_live_inventory(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        before = self.snapshot()
        with patch("inventory_transaction.exchange_directories", side_effect=TransactionError("unsupported")):
            with self.assertRaisesRegex(TransactionError, "unsupported"):
                publish_inventory(self.output, "demo-001", generate_bundle)
        self.assertEqual(before, self.snapshot())

    def test_confirmed_inventory_requires_explicit_replacement_and_is_archived(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        path = self.output / "data-structures.yaml"
        data = load(path)
        data["status"] = "confirmed"
        path.write_text(yaml.safe_dump(data))
        before = self.snapshot()
        with self.assertRaisesRegex(TransactionError, "Reviewed inventory preserved"):
            publish_inventory(self.output, "demo-001", generate_bundle)
        self.assertEqual(before, self.snapshot())
        generation = publish_inventory(self.output, "demo-001", generate_bundle, replace_reviewed=True)
        archive = self.output.parent / ".inventory-transactions" / generation / "snapshot"
        self.assertEqual(load(archive / "data-structures.yaml")["status"], "confirmed")

    def test_killed_exporter_leaves_previous_inventory_and_releases_lock(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        before = self.snapshot()
        source = """
import os, signal, sys
from pathlib import Path
from inventory_transaction import publish_inventory
def interrupted(stage, generation):
    (stage / 'files.yaml').write_text('partial')
    os.kill(os.getpid(), signal.SIGKILL)
publish_inventory(Path(sys.argv[1]), 'demo-001', interrupted)
"""
        result = subprocess.run([sys.executable, "-c", source, str(self.output)], env={**os.environ, "PYTHONPATH": str(REPO / "tools")}, capture_output=True)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertEqual(before, self.snapshot())
        publish_inventory(self.output, "demo-001", generate_bundle)
        statuses = [json.loads(path.read_text())["status"] for path in self.output.parent.glob('.inventory-transactions/*/transaction.json')]
        self.assertIn("abandoned", statuses)

    def test_kill_immediately_after_exchange_retains_both_complete_generations(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        before = self.snapshot()
        source = """
import os, signal, sys, yaml, hashlib
from pathlib import Path
import inventory_transaction as transaction
from workflow import INVENTORY_FIELDS
original = transaction.exchange_directories
def interrupted(left, right):
    original(left, right)
    os.kill(os.getpid(), signal.SIGKILL)
transaction.exchange_directories = interrupted
def generate(stage, generation):
    for name, fields in INVENTORY_FIELDS.items():
        data = {key: kind() for key, kind in fields.items()}
        data.update(slice='demo-001', version=1)
        if name == 'manifest.yaml':
            data['generation'] = generation
        (stage / name).write_text(yaml.safe_dump(data))
    path = stage / 'manifest.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest['artifact_digests'] = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in INVENTORY_FIELDS if name != 'manifest.yaml'}
    path.write_text(yaml.safe_dump(manifest))
transaction.publish_inventory(Path(sys.argv[1]), 'demo-001', generate)
"""
        result = subprocess.run([sys.executable, "-c", source, str(self.output)], env={**os.environ, "PYTHONPATH": str(REPO / "tools")}, capture_output=True)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        generation = load(self.output / "manifest.yaml")["generation"]
        archive = self.output.parent / ".inventory-transactions" / generation / "snapshot"
        self.assertEqual({str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()}, before)
        self.assertEqual(set(self.snapshot()), set(before))
        publish_inventory(self.output, "demo-001", generate_bundle)
        journal = json.loads((archive.parent / 'transaction.json').read_text())
        self.assertEqual(journal['status'], 'published')

    def test_real_exporter_publishes_digest_manifest_and_preserves_reviewed_drafts(self):
        source = Path(self.temp.name) / "legacy"
        source.mkdir()
        (source / "api.h").write_text("unsigned int digits(unsigned int value);\n")
        (source / "api.c").write_text('#include "api.h"\nunsigned int digits(unsigned int value) { return value + 1; }\n')
        args = [sys.executable, str(REPO / "tools/header_dependency_analyzer/export_inventory.py"), "--project", str(source), "--header", str(source / "api.h"), "--function", "digits", "--slice", "demo-001", "--output", str(self.output)]
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load(self.output / "manifest.yaml")
        self.assertEqual(len(manifest["artifact_digests"]), 8)
        path = self.output / "behavior.yaml"
        data = load(path)
        data["review_notes"] = "Preserve manual analysis even while draft"
        path.write_text(yaml.safe_dump(data))
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Reviewed inventory preserved", result.stderr)
        self.assertEqual(load(path)["review_notes"], data["review_notes"])

    def test_workspace_lock_excludes_concurrent_writers_and_can_be_reacquired(self):
        root = Path(self.temp.name)
        with WorkspaceLock(root):
            with self.assertRaisesRegex(TransactionError, "lock occupied"):
                with WorkspaceLock(root):
                    self.fail("Concurrent writer acquired the lock")
        with WorkspaceLock(root):
            pass

    def test_explicit_replacement_can_archive_a_corrupted_live_inventory(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        (self.output / 'manifest.yaml').write_text('invalid: [')
        generation = publish_inventory(self.output, "demo-001", generate_bundle, replace_reviewed=True)
        archive = self.output.parent / '.inventory-transactions' / generation / 'snapshot'
        self.assertEqual((archive / 'manifest.yaml').read_text(), 'invalid: [')
        self.assertEqual(load(self.output / 'manifest.yaml')['generation'], generation)

    def test_enriched_dependency_artifact_is_not_silently_replaced(self):
        publish_inventory(self.output, "demo-001", generate_bundle)
        path = self.output / 'dependencies.yaml'
        data = load(path)
        data['manual_classification'] = 'external libc call is adapted'
        path.write_text(yaml.safe_dump(data))
        with self.assertRaisesRegex(TransactionError, 'dependencies.yaml'):
            publish_inventory(self.output, "demo-001", generate_bundle)
        self.assertEqual(load(path)['manual_classification'], data['manual_classification'])

    def test_killed_workflow_releases_workspace_lock(self):
        root = Path(self.temp.name)
        source = """
import os, signal, sys
from pathlib import Path
from artifact_sync.transaction import WorkspaceLock
with WorkspaceLock(Path(sys.argv[1])):
    os.kill(os.getpid(), signal.SIGKILL)
"""
        result = subprocess.run([sys.executable, "-c", source, str(root)], env={**os.environ, "PYTHONPATH": str(REPO / "tools")}, capture_output=True)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        with WorkspaceLock(root):
            pass


if __name__ == "__main__":
    unittest.main()
