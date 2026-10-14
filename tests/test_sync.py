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


if __name__ == '__main__':
    unittest.main()
