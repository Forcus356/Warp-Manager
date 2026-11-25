import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

WORKER = Path(__file__).resolve().parents[1] / 'worker.sh'


class WorkerRotation(unittest.TestCase):
    def environment(self, root):
        cli = root / 'warp-cli'
        cli.write_text('#!/bin/bash\nif [[ "$2" == disconnect ]]; then touch "$WGP_TEST_STARTED"; sleep 1; fi\nexit 0\n')
        cli.chmod(0o755)
        return {**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'],
                'WARP_POOL_ROTATION_LOCK': str(root / 'rotation.lock'),
                'WGP_TEST_STARTED': str(root / 'started')}

    def test_concurrent_rotations_do_not_overlap_inside_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); env = self.environment(root)
            first = subprocess.Popen(['bash', str(WORKER), 'rotate', 'reconnect'], env=env)
            try:
                deadline = time.monotonic() + 5
                while not (root / 'started').exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root / 'started').exists())
                second = subprocess.run(['bash', str(WORKER), 'rotate', 'reconnect'], env=env, timeout=5)
                self.assertEqual(second.returncode, 75)
                self.assertEqual(first.wait(timeout=5), 0)
            finally:
                if first.poll() is None:
                    first.kill(); first.wait()

    def test_invalid_method_does_not_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); env = self.environment(root)
            result = subprocess.run(['bash', str(WORKER), 'rotate', 'unknown'], env=env, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((root / 'started').exists())


if __name__ == '__main__':
    unittest.main()
