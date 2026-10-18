from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class SyncVerificationTests(unittest.TestCase):
    def run_controller(self, controller, *args):
        return subprocess.run(
            [str(controller), *map(str, args)], text=True, capture_output=True
        )

    def test_deployed_controller_verifies_each_supported_target(self):
        for target in ('generic', 'antigravity', 'opencode', 'kilocode', 'cursor', 'claude', 'all'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / 'project with spaces'
                result = self.run_controller(REPO / 'sync.sh', target, project)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = self.run_controller(project / '.ctrust/sync.sh', 'verify')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('ctrust verification passed', result.stdout)

    def test_kilocode_verification_skips_partial_other_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / '.agents/skills').mkdir(parents=True)
            result = self.run_controller(REPO / 'sync.sh', 'kilocode', project)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = self.run_controller(project / '.ctrust/sync.sh', 'verify')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_kilocode_verification_rejects_missing_components(self):
        for component in ('agents', 'skills'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                result = self.run_controller(REPO / 'sync.sh', 'kilocode', project)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                # Rename the component directory to simulate an incomplete install.
                root = project / '.kilo' / component
                root.rename(root.with_name(component + '-missing'))
                result = self.run_controller(project / '.ctrust/sync.sh', 'verify')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'Missing component: {project}/.kilo/{component}/', result.stderr)
    def test_update_refreshes_managed_components_without_touching_migration_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'
            result = self.run_controller(REPO / 'sync.sh', 'generic', project)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            managed = project / '.ctrust/tools/workflow.py'
            migration = project / '.ctrust/migration.yaml'
            managed.write_text('local old controller copy\n')
            migration.write_text('version: 2\nstatus: in_progress\n')
            result = self.run_controller(REPO / 'sync.sh', 'generic', 'update', project)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(managed.read_bytes(), (REPO / 'tools/workflow.py').read_bytes())
            self.assertEqual(migration.read_text(), 'version: 2\nstatus: in_progress\n')
            self.assertIn('updated', result.stdout)

    def test_update_form_works_for_kilocode_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = self.run_controller(REPO / 'sync.sh', 'kilocode', project)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            managed = project / '.ctrust/tools/workflow.py'
            managed.write_text('stale deployed workflow\n')
            result = self.run_controller(REPO / 'sync.sh', 'kilocode', 'update', project)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(managed.read_bytes(), (REPO / 'tools/workflow.py').read_bytes())

    def test_update_unknown_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_controller(REPO / 'sync.sh', 'update', 'unknown', tmp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Unknown update target', result.stderr)


if __name__ == '__main__':
    unittest.main()
