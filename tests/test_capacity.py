import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warp_pool import Pool, Observation, validate_config
from warp_capacity import spare_count, token
import warp_profiles as profiles
from warp_menu import change


def pool(count=16, gbps=10, role='gemini'):
    config = profiles.defaults(1, role, count)
    config['target_gbps'] = gbps
    return Pool(validate_config(config))


def ready(p, index, mbps=1000):
    node = p.nodes[index]
    node.enabled = True
    p.apply(node, Observation('GOOD', ip=f'10.0.{index // 250}.{index % 250 + 1}',
                              google='RU' if p.config['youtube_mode'] else 'DE',
                              gemini='DISABLED' if p.config['youtube_mode'] else 'SERVING'))
    p.capacity.samples[node.name] = (token(node), mbps, time.monotonic())
    return node


class Planning(unittest.TestCase):
    def test_ten_primary_get_three_hot_reserves_and_thirty_get_ten(self):
        for count, reserves in [(10, 3), (30, 10), (1, 1), (2, 1)]:
            p = pool(count + reserves, count)
            for i in range(count + reserves):
                ready(p, i)
            primary, standby = p.capacity.reconcile()
            self.assertEqual(len(primary), count)
            self.assertEqual(len(standby), reserves)
            self.assertEqual(spare_count(count), reserves)
            self.assertFalse(p.capacity.snapshot()['target_reached'])
            p.capacity.record_aggregate(primary, count * 1000)
            self.assertTrue(p.capacity.snapshot()['complete'])

    def test_speed_sum_is_not_claimed_as_simultaneous_capacity(self):
        p = pool(6, 2)
        for i in range(6):
            ready(p, i)
        primary, _ = p.capacity.reconcile()
        p.capacity.record_aggregate(primary, 1100)
        state = p.capacity.snapshot()
        self.assertFalse(state['target_reached'])
        self.assertGreaterEqual(len(state['primary']), 3)
        self.assertIsNone(state['measured_gbps'])  # membership already expanded

    def test_bad_primary_promotes_checked_reserve_synchronously(self):
        p = pool()
        for i in range(13):
            ready(p, i)
        primary, reserve = p.capacity.reconcile()
        failed, promoted = primary[0], reserve[0]
        p.capacity.record_aggregate(primary, 10000)
        p.apply(failed, Observation('BAD', ip=failed.ip, reason='Gemini REFUSED'))
        state = p.capacity.snapshot()
        self.assertNotIn(failed.name, state['primary'])
        self.assertIn(promoted.name, state['primary'])
        self.assertEqual(len(state['primary']), 10)
        self.assertEqual(len(state['reserve']), 2)
        self.assertTrue(p.capacity.needs_search())
        self.assertFalse(state['target_reached'])  # verify new composition

    def test_hot_reserves_never_take_new_user_traffic(self):
        p = pool(13)
        for i in range(13):
            ready(p, i)
        primary, reserve = p.capacity.reconcile()
        chosen = {p.choose(route=True).name for _ in range(100)}
        self.assertEqual(chosen, {n.name for n in primary})
        self.assertFalse(chosen & {n.name for n in reserve})

    def test_expired_health_and_changed_identity_remove_capacity(self):
        p = pool(4, 1)
        n = ready(p, 0)
        ready(p, 1)
        primary, _ = p.capacity.reconcile()
        p.capacity.record_aggregate(primary, 1000)
        n.generation += 1
        self.assertFalse(p.capacity.measured(n))
        self.assertFalse(p.capacity.snapshot()['target_reached'])
        p.nodes[1].good_at -= p.config['health_ttl'] + 1
        self.assertIsNone(p.choose())

    def test_three_unproductive_expansions_pause_growth(self):
        p = pool(10, 1)
        for i in range(10):
            ready(p, i)
        for _ in range(4):
            primary, _ = p.capacity.reconcile()
            p.capacity.record_aggregate(primary, 700)
        self.assertGreater(p.capacity.hold_until, time.monotonic())
        before = len(p.capacity.reconcile()[0])
        self.assertEqual(len(p.capacity.reconcile()[0]), before)
        self.assertFalse(p.capacity.snapshot()['target_reached'])

    def test_weighted_connections_and_pending_attempts(self):
        p = pool(3, 1.5)
        a = ready(p, 0, 1000); b = ready(p, 1, 500)
        p.capacity.reconcile()
        counts = {a.name: 0, b.name: 0}
        for _ in range(30):
            n = p.choose(route=True)
            n.pending += 1; counts[n.name] += 1
        self.assertEqual(counts, {a.name: 20, b.name: 10})

    def test_reboot_restores_slots_but_not_ready_or_benchmark(self):
        p = pool()
        for i in range(13):
            ready(p, i)
        p.capacity.reconcile()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            p.save_runtime(path)
            restored = pool(); restored.load_runtime(path)
        self.assertEqual(sum(n.enabled for n in restored.nodes), 13)
        self.assertEqual(restored.capacity.snapshot()['primary'], [])
        self.assertFalse(restored.capacity.snapshot()['complete'])
        self.assertIsNone(restored.choose())

    def test_youtube_and_gemini_have_independent_roles_and_health(self):
        youtube, gemini = pool(3, 1, 'youtube'), pool(3, 1, 'gemini')
        ready(youtube, 0)
        self.assertIsNotNone(youtube.choose())
        self.assertIsNone(gemini.choose())
        self.assertTrue(youtube.config['youtube_mode'])
        self.assertFalse(youtube.config['gemini_check'])
        self.assertTrue(gemini.config['gemini_check'])

    def test_128_slots_per_instance_have_no_default_port_collisions(self):
        seen = set()
        for number in range(1, 100):
            c = profiles.defaults(number, count=128)
            ports = {p for _, p in profiles.endpoints(c)}
            self.assertFalse(ports & seen)
            seen.update(ports)
        self.assertLess(max(seen), 65535)

    def test_compose_only_starts_first_capacity_slot_by_default(self):
        p = pool(40)
        c = profiles.compose(p.config, Path('/tmp/profile'), 'test')
        self.assertNotIn('profiles', c['services']['warp1'])
        self.assertTrue(all(s.get('profiles') == ['capacity'] for name, s in c['services'].items() if name != 'warp1'))
        fixed = profiles.compose(profiles.defaults(), Path('/tmp/profile'), 'test')
        self.assertFalse(any(s.get('profiles') for s in fixed['services'].values()))

    def test_invalid_capacity_settings_rejected_for_both_modes(self):
        for role in ('gemini', 'youtube'):
            for key, value in [('target_gbps', float('nan')), ('target_gbps', -1),
                               ('target_gbps', True), ('capacity_seconds', 0),
                               ('capacity_url', 'http://example.test/file'),
                               ('capacity_url', 'https://user:secret@example.test/file'),
                               ('capacity_sample_ttl', 100), ('capacity_parallel_search', 1.5)]:
                c = profiles.defaults(1, role)
                c[key] = value
                with self.subTest(role=role, key=key, value=value), self.assertRaises(ValueError):
                    validate_config(c)

    def test_menu_capacity_keeps_mode_and_other_settings(self):
        for role in ('youtube', 'gemini'):
            c = profiles.defaults(1, role)
            changed, _ = change(c, 'target_gbps', '10')
            changed, _ = change(changed, 'tunnels', '40')
            self.assertEqual(profiles.role(changed).lower(), role)
            self.assertEqual(changed['target_gbps'], 10)
            self.assertEqual(len(changed['nodes']), 40)
            self.assertEqual(changed['impersonate_dir'], c['impersonate_dir'])

    def test_new_instance_for_edited_mode_keeps_capacity_and_gets_fresh_identities(self):
        c = profiles.defaults(1, 'gemini')
        c, _ = change(c, 'target_gbps', '10')
        c, _ = change(c, 'role', 'youtube')
        c['impersonate_dir'] = '/verified/browser'
        fresh = profiles.fork_config(c, 2)
        self.assertEqual(fresh['target_gbps'], 10)
        self.assertEqual(len(fresh['nodes']), 64)
        self.assertTrue(fresh['youtube_mode'])
        self.assertFalse(fresh['gemini_check'])
        self.assertEqual(fresh['impersonate_dir'], '/verified/browser')
        self.assertNotEqual(fresh['listen_port'], c['listen_port'])
        self.assertFalse({n['container'] for n in c['nodes']} & {n['container'] for n in fresh['nodes']})
        self.assertFalse({n['port'] for n in c['nodes']} & {n['port'] for n in fresh['nodes']})

    def test_cli_capacity_preserves_youtube_mode(self):
        import warp_ctl
        with tempfile.TemporaryDirectory() as directory, patch.object(profiles, 'OPT', Path(directory)):
            root = profiles.directory(2); root.mkdir()
            (root / 'config.json').write_text(json.dumps(profiles.defaults(2, 'youtube')))
            with patch('warp_ctl.apply_config') as apply:
                warp_ctl.main(['--instance', '2', 'capacity', '--gbps', '10', '--max-tunnels', '40'])
            config = apply.call_args.args[1]
            self.assertEqual(config['target_gbps'], 10)
            self.assertEqual(len(config['nodes']), 40)
            self.assertTrue(config['youtube_mode'])
            self.assertFalse(config['gemini_check'])

    def test_inflight_health_cannot_reenable_parked_node(self):
        p = pool(); n = p.nodes[1]
        p.apply(n, Observation('GOOD', ip='1.1.1.1', gemini='SERVING'))
        self.assertEqual(n.state, 'DORMANT')
        self.assertFalse(p.eligible(n))

    def test_saturation_pause_still_allows_hot_replacement(self):
        p = pool(8, 2)
        for i in range(8):
            ready(p, i)
        for _ in range(4):
            primary, _ = p.capacity.reconcile()
            p.capacity.record_aggregate(primary, 700)
        primary, reserve = p.capacity.reconcile()
        count = len(primary)
        p.apply(primary[0], Observation('BAD', ip=primary[0].ip, reason='transport lost'))
        self.assertEqual(len(p.capacity.reconcile()[0]), count)


class Controller(unittest.IsolatedAsyncioTestCase):
    async def test_changed_exit_is_quarantined_before_slow_service_probe(self):
        for mode,region in [('gemini','DE'),('youtube','RU')]:
            p=pool(3,1,mode);a=ready(p,0);b=ready(p,1)
            previous_generation=a.generation
            p.trace=AsyncMock(return_value='8.8.8.8')
            async def fetch(*args):
                self.assertFalse(p.eligible(a))
                self.assertIs(p.choose(),b)
                return json.dumps({'GL':region})
            p.fetch=fetch
            with patch('warp_pool.command',new=AsyncMock(return_value='SERVING: verified')):
                obs=await p.observe(a)
            self.assertEqual(obs.result,'GOOD')
            self.assertEqual(a.generation,previous_generation)
            p.apply(a,obs)
            self.assertEqual(a.state,'READY')
            self.assertEqual(a.ip,'8.8.8.8')

    async def test_bad_service_is_excluded_before_final_trace(self):
        for mode,region in [('gemini','RU'),('youtube','DE')]:
            p=pool(3,1,mode);a=ready(p,0);b=ready(p,1)
            old=a.ip;calls=0
            async def trace(*args):
                nonlocal calls
                calls+=1
                if calls==2:
                    self.assertFalse(p.eligible(a))
                    self.assertIs(p.choose(),b)
                return old
            p.trace=trace;p.fetch=AsyncMock(return_value=json.dumps({'GL':region}))
            obs=await p.observe(a)
            self.assertEqual(obs.result,'BAD')

    async def test_each_mode_keeps_its_own_real_observation_policy(self):
        for mode, geo, verdict in [('youtube', 'RU', 'GOOD'), ('youtube', 'DE', 'BAD'),
                                    ('gemini', 'RU', 'BAD'), ('gemini', 'DE', 'GOOD')]:
            p = pool(3, 1, mode); n = p.nodes[0]
            p.trace = AsyncMock(return_value='1.1.1.1')
            p.fetch = AsyncMock(return_value=json.dumps({'GL': geo}))
            with patch('warp_pool.command', new=AsyncMock(return_value='SERVING: verified')) as command:
                obs = await p.observe(n)
            self.assertEqual(obs.result, verdict)
            if mode == 'youtube' or geo == 'RU':
                command.assert_not_awaited()
            else:
                command.assert_awaited_once()

    async def test_inconclusive_gemini_is_never_added_to_capacity_pool(self):
        p = pool(3, 1, 'gemini'); n = p.nodes[0]
        p.trace = AsyncMock(return_value='1.1.1.1')
        p.fetch = AsyncMock(return_value='{"GL":"DE"}')
        with patch('warp_pool.command', new=AsyncMock(return_value='INCONCLUSIVE: no response')):
            obs = await p.observe(n)
        p.apply(n, obs)
        self.assertIsNone(p.choose())
        self.assertFalse(p.capacity.measured(n))

    async def test_capacity_repair_starts_stopped_service_before_rotation(self):
        p = pool(); n = p.nodes[0]; p.capacity.root = Path('/test/profile')
        with patch('warp_pool.command', new_callable=AsyncMock) as command:
            await p.repair(n, 'reconnect')
        calls = [c.args for c in command.await_args_list]
        self.assertEqual(calls[0][:4], ('docker', 'compose', '-f', Path('/test/profile/compose.yaml')))
        self.assertEqual(calls[1][:3], ('docker', 'exec', n.container))

    async def test_reserve_search_does_not_start_more_than_missing_count(self):
        p = pool(16)
        for i in range(12):
            ready(p, i)
        # 10 primary + 2 reserves, one allocated SEARCH node already running.
        p.nodes[12].enabled = True
        p.capacity.start = AsyncMock(); p.capacity.resources = lambda: ''
        primary, _ = p.capacity.reconcile()
        p.capacity.record_aggregate(primary, 10000)
        await p.capacity.tick()
        p.capacity.start.assert_not_awaited()

    async def test_excess_search_slot_is_parked_after_reserve_fills(self):
        p = pool(16)
        for i in range(13):
            ready(p, i)
        p.nodes[13].enabled = True
        primary, _ = p.capacity.reconcile()
        p.capacity.record_aggregate(primary, 10000)
        p.capacity.root = Path('/test/profile')
        with patch('warp_pool.command', new_callable=AsyncMock) as command:
            await p.capacity.tick()
        self.assertFalse(p.nodes[13].enabled)
        self.assertEqual(p.nodes[13].state, 'DORMANT')
        self.assertEqual(command.await_args.args[-2:], ('stop', p.nodes[13].name))

    async def test_growth_is_incremental_and_honors_search_budget(self):
        p = pool(40)
        c = p.capacity
        c.start = AsyncMock(side_effect=lambda n: setattr(n, 'enabled', True))
        c.resources = lambda: ''
        await c.tick()
        self.assertEqual(c.start.await_count, 1)
        await c.tick()
        self.assertEqual(c.start.await_count, 1)
        self.assertEqual(sum(n.enabled for n in p.nodes), 2)

    async def test_resource_shortage_prevents_container_growth(self):
        p = pool()
        c = p.capacity; c.resources = lambda: 'low disk'
        c.start = AsyncMock()
        await c.tick()
        c.start.assert_not_awaited()
        self.assertEqual(c.phase, 'RESOURCE_LIMIT')

    async def test_failed_speed_endpoint_does_not_rotate_good_exit(self):
        p = pool(3, 1)
        n = ready(p, 0); p.capacity.samples.clear()
        p.capacity.measure = AsyncMock(side_effect=ValueError('403'))
        p.repair = AsyncMock()
        await p.capacity.tick()
        self.assertEqual(n.state, 'READY')
        self.assertFalse(n.requested_method)
        p.repair.assert_not_awaited()
        self.assertIsNone(p.choose())  # not measured, never claimed ready for capacity

    async def test_verified_target_stops_growth_after_reserve_is_full(self):
        p = pool(20)
        for i in range(13):
            ready(p, i)
        primary, _ = p.capacity.reconcile()
        p.capacity.record_aggregate(primary, 10000)
        p.capacity.start = AsyncMock()
        await p.capacity.tick()
        p.capacity.start.assert_not_awaited()
        self.assertEqual(p.capacity.phase, 'READY')

    async def test_budget_exhaustion_is_not_success(self):
        p = pool(2, 10)
        ready(p, 0); ready(p, 1)
        await p.capacity.tick()
        self.assertEqual(p.capacity.phase, 'LIMIT')
        self.assertFalse(p.capacity.snapshot()['complete'])

    async def test_capacity_aggregate_counts_bytes_over_common_wall_time(self):
        p = pool(2, 1)
        a = ready(p, 0); b = ready(p, 1)
        p.config['capacity_seconds'] = 0.04
        p.trace = AsyncMock(side_effect=lambda n: n.ip)
        async def download(n, deadline):
            await asyncio.sleep(max(0, deadline - time.monotonic()))
            return 1000000
        p.capacity.download = download
        result = await p.capacity.measure([a, b])
        self.assertGreater(result, 250)
        self.assertLess(result, 410)

    async def test_changed_ip_invalidates_aggregate(self):
        p = pool(2, 1); n = ready(p, 0)
        p.config['capacity_seconds'] = 0.02
        p.trace = AsyncMock(side_effect=[n.ip, '8.8.8.8'])
        async def download(n, deadline):
            await asyncio.sleep(max(0, deadline - time.monotonic()))
            return 1000000
        p.capacity.download = download
        with self.assertRaises(ValueError):
            await p.capacity.measure([n])
        self.assertFalse(p.eligible(n))
        self.assertIsNone(p.choose())

    async def test_partial_aggregate_cannot_confirm_capacity(self):
        p = pool(2, 1); a = ready(p, 0); b = ready(p, 1)
        p.trace = AsyncMock(side_effect=lambda n: n.ip)
        p.capacity.download = AsyncMock(side_effect=[1000000, ValueError('bad')])
        with self.assertRaises(ValueError):
            await p.capacity.measure([a, b])
        self.assertFalse(p.capacity.snapshot()['complete'])


if __name__ == '__main__':
    unittest.main()
