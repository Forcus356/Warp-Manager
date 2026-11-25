import contextlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import warp_assets
import warp_ctl


class Assets(unittest.TestCase):
    def test_mismatched_compose_checksum_does_not_install_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'docker-compose'
            def download(args, **kwargs):
                Path(args[args.index('-o') + 1]).write_bytes(b'invalid compose')
            with patch('warp_assets.subprocess.run', side_effect=download) as call:
                with self.assertRaises(ValueError):
                    warp_assets.ensure_compose(target)
            self.assertEqual(call.call_count, 1)
            self.assertFalse(target.exists())

    def test_mismatched_browser_checksum_never_executes_download(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            calls = []
            def download(args, **kwargs):
                calls.append(args)
                Path(args[args.index('-o') + 1]).write_bytes(b'invalid release')
            with patch('warp_assets.subprocess.run', side_effect=download):
                with self.assertRaises(ValueError):
                    warp_assets.ensure(base)
            self.assertEqual(len(calls), 1)
            self.assertFalse(any(p.name.startswith('curl-impersonate-') for p in base.iterdir()))

    def test_archive_cannot_write_outside_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); payload = root / 'payload'; payload.mkdir()
            archive = root / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                item = tarfile.TarInfo('../escaped'); item.size = 1
                tar.addfile(item, io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                warp_assets.unpack(archive, payload)
            self.assertFalse((root / 'escaped').exists())

    def test_remote_update_requires_known_digest(self):
        with self.assertRaises(ValueError):
            with warp_ctl.release('https://example.test/release.zip'):
                self.fail('unverified update accepted')

    def test_wrong_local_update_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release.zip'; path.write_bytes(b'not zip')
            with self.assertRaises(ValueError):
                with warp_ctl.release(path, '0' * 64):
                    self.fail('wrong checksum accepted')


if __name__ == '__main__':
    unittest.main()
