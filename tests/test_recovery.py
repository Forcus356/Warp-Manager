import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warp_pool import Pool, Observation, read_config
from warp_ctl import request, set_config

ROOT = Path(__file__).resolve().parents[1]


def pool():
    return Pool(read_config(ROOT / 'config.json'))


class Recovery(unittest.TestCase):
    def test_single_rejection_quarantines_but_does_not_poison_cache(self):
        p = pool(); n = p.nodes[0]
        observation = Observation('BAD', ip='1.1.1.1', reason='Gemini REFUSED')
        p.apply(n, observation)
        self.assertFalse(p.eligible(n))
        self.assertEqual(n.failures, 1)
        self.assertFalse(n.bad_ips)
        p.apply(n, observation)
        self.assertEqual(n.failures, 2)
        self.assertIn('1.1.1.1', n.bad_ips)

    def test_changed_ip_needs_new_confirmation(self):
        p = pool(); n = p.nodes[0]
        p.apply(n, Observation('BAD', ip='1.1.1.1', reason='Gemini REFUSED'))
        p.apply(n, Observation('BAD', ip='8.8.8.8', reason='Gemini REFUSED'))
        self.assertEqual(n.failures, 1)
        self.assertFalse(n.bad_ips)

    def test_runtime_preserves_rate_limit_and_never_restores_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            p = pool(); n = p.nodes[0]
            now = time.monotonic()
            n.state, n.rotations, n.repair_streak = 'READY', 8, 4
            n.attempts = [now - 100, now - 4000]
            n.bad_ips = {'1.1.1.1': now + 600, '8.8.8.8': now - 1}
            n.next_repair_at = now + 120
            p.save_runtime(path)
            restored = pool(); restored.load_runtime(path)
            node = restored.nodes[0]
            self.assertEqual(node.state, 'STARTING')
            self.assertEqual(node.rotations, 8)
            self.assertEqual(len(node.attempts), 1)
            self.assertEqual(set(node.bad_ips), {'1.1.1.1'})
            self.assertGreater(node.next_repair_at - time.monotonic(), 118)
            self.assertFalse(restored.eligible(node))

    def test_invalid_config_edit_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'config.json'; path.write_text((ROOT / 'config.json').read_text())
            original = path.read_text()
            with self.assertRaises(ValueError):
                set_config(root, 'bad_threshold', '0')
            self.assertEqual(path.read_text(), original)
            set_config(root, 'deny_regions', 'ru,by')
            self.assertEqual(read_config(path)['deny_regions'], ['RU', 'BY'])
            self.assertTrue((root / 'config.previous.json').exists())

    def test_cli_refuses_rotation_of_all_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.json').write_text((ROOT / 'config.json').read_text())
            with self.assertRaises(ValueError):
                request(root, 'rotate', 'all')
            self.assertFalse((root / 'requests').exists())

    def test_invalid_counter_and_node_names_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config = read_config(ROOT / 'config.json')
            for key, value in [('bad_threshold', True), ('max_rotations_per_hour', 0.5),
                               ('health_ttl', 1), ('deny_regions', 'RU')]:
                bad = {**config, key: value}; path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError, msg=key):
                    read_config(path)


class AsyncRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_new_exit_is_checked_without_waiting_for_next_rotation_window(self):
        p = pool(); n = p.nodes[0]
        p.config.update(rotation_delay=900, max_backoff=900, confirmation_delay=0.01)
        checked_new_exit = asyncio.Event()
        calls = 0
        async def observe(node):
            nonlocal calls
            calls += 1
            if calls < 3:
                return Observation('BAD', ip='1.1.1.1', reason='Google region RU rejected')
            checked_new_exit.set()
            return Observation('GOOD', ip='8.8.8.8', google='DE', gemini='SERVING')
        p.observe = observe
        with patch('warp_pool.command', new=AsyncMock(return_value='')):
            task = asyncio.create_task(p.worker(n))
            try:
                await asyncio.wait_for(checked_new_exit.wait(), 1)
                self.assertEqual(n.rotations, 1)
                self.assertTrue(p.eligible(n))
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_missing_curl_is_network_error(self):
        p = pool()
        with patch('warp_pool.command', new=AsyncMock(side_effect=FileNotFoundError)):
            self.assertEqual((await p.observe(p.nodes[0])).result, 'NETWORK')

    async def test_backoff_survives_manual_repair_request(self):
        p = pool(); n = p.nodes[0]
        n.next_repair_at = time.monotonic() + 100
        with patch('warp_pool.command', new_callable=AsyncMock) as cmd:
            self.assertFalse(await p.repair(n, 'registration'))
        cmd.assert_not_awaited()
        self.assertEqual(n.state, 'COOLDOWN')

    async def test_two_speed_samples_required_and_failed_speed_probe_keeps_verified_gemini(self):
        p = pool(); n = p.nodes[0]
        p.config['min_speed_kib'] = 1000
        p.trace = AsyncMock(return_value='1.1.1.1')
        p.fetch = AsyncMock(return_value='{"GL":"DE"}')
        p.speed = AsyncMock(return_value=100)
        with patch('warp_pool.command', new=AsyncMock(return_value='SERVING: reply')):
            first = await p.observe(n)
            self.assertEqual(first.result, 'GOOD')
            p.apply(n, first)
            n.speed_at -= p.config['speed_interval'] + 1
            second = await p.observe(n)
            self.assertEqual(second.result, 'BAD')
            self.assertEqual(second.reason, 'consecutive slow speed samples')
            n.speed_at = 0
            p.speed.side_effect = RuntimeError('incomplete')
            last = await p.observe(n)
            self.assertEqual(last.result, 'GOOD')
            self.assertIsNone(n.speed_kib_s)
            self.assertEqual(n.slow_samples, 0)

    async def test_cached_rejection_does_not_query_gemini(self):
        p = pool(); n = p.nodes[0]
        n.bad_ips['1.1.1.1'] = time.monotonic() + 100
        p.trace = AsyncMock(return_value='1.1.1.1')
        p.fetch = AsyncMock()
        observation = await p.observe(n)
        p.apply(n, observation)
        self.assertEqual(n.failures, p.config['bad_threshold'])
        p.fetch.assert_not_awaited()

    async def test_disabling_gemini_is_explicit_in_observation(self):
        p = pool(); n = p.nodes[0]
        p.config['gemini_check'] = False
        p.trace = AsyncMock(return_value='1.1.1.1')
        p.fetch = AsyncMock(return_value='{"GL":"DE"}')
        with patch('warp_pool.command', new_callable=AsyncMock) as cmd:
            result = await p.observe(n)
        cmd.assert_not_awaited()
        self.assertEqual((result.result, result.gemini), ('GOOD', 'DISABLED'))


if __name__ == '__main__':
    unittest.main()
