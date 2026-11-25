import asyncio
import contextlib
import io
import hashlib
import json
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warp_pool import Pool, Observation, atomic_json, validate_config, invalidate_status, boot_id
import warp_profiles as profiles
import warp_ctl
import warp_menu
import warp_install
import warp_system


class Profiles(unittest.TestCase):
    def test_custom_read_root_cannot_uninstall_a_real_instance(self):
        with patch('warp_ctl.run') as command, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                warp_ctl.main(['--root', '/tmp/example', 'uninstall', '--yes'])
        self.assertEqual(error.exception.code, 2)
        command.assert_not_called()

    def test_invalidation_preserves_previous_observation_but_clears_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = profiles.defaults(1)
            old = {'updated': time.time(), 'ready_tunnels': 1, 'nodes': [{'name': 'warp1', 'gemini': 'SERVING'}]}
            atomic_json(root / 'state.json', old)
            invalidate_status(root, config); invalidate_status(root, config)
            self.assertEqual(json.loads((root / 'state.json').read_text())['ready_tunnels'], 0)
            self.assertEqual(json.loads((root / 'previous-state.json').read_text()), old)

    def test_fresh_state_from_previous_process_is_not_serving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); atomic_json(root / 'config.json', profiles.defaults(1))
            atomic_json(root / 'state.json', {'updated': time.time(), 'pid': 123, 'boot_id': boot_id(),
                'ready_tunnels': 1, 'distinct_ready_ips': 1, 'active': 'warp1',
                'nodes': [{'name': 'warp1', 'state': 'READY', 'gemini': 'SERVING'}]})
            with patch('warp_ctl.unit_state', return_value='active'), patch('warp_ctl.unit_pid', return_value='456'):
                data = warp_ctl.status_data(root)
            self.assertTrue(data['stale'])
            self.assertEqual(data['ready_tunnels'], 0)
            self.assertEqual(data['nodes'][0]['gemini'], 'UNKNOWN')

    def test_failed_shared_cli_copy_preserves_existing_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir(); lib = root / 'lib'; lib.mkdir()
            (source / 'one').write_text('new'); (lib / 'one').write_text('old')
            with patch.object(warp_install, 'LIB', lib), patch.object(warp_install, 'SOURCE_FILES', ['one']), patch('warp_install.copy_source', side_effect=OSError('copy failed')):
                with self.assertRaises(OSError):
                    warp_install.install_shared(source)
            self.assertEqual((lib / 'one').read_text(), 'old')

    def test_failed_shared_cli_switch_restores_previous_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir(); lib = root / 'lib'; lib.mkdir()
            (source / 'one').write_text('new'); (lib / 'one').write_text('old')
            original = Path.rename
            def rename(path, target):
                if path.name.startswith('.warp-pool-stage-'):
                    raise OSError('switch failed')
                return original(path, target)
            with patch.object(warp_install, 'LIB', lib), patch.object(warp_install, 'SOURCE_FILES', ['one']), patch.object(Path, 'rename', rename):
                with self.assertRaises(OSError):
                    warp_install.install_shared(source)
            self.assertEqual((lib / 'one').read_text(), 'old')

    def test_99_full_pools_have_no_default_port_collisions(self):
        used = set()
        for number in range(1, 100):
            config = profiles.defaults(number, count=32)
            ports = {p for _, p in profiles.endpoints(config)}
            self.assertFalse(used & ports)
            self.assertEqual(len(ports), 34)
            used.update(ports)

    def test_reservations_include_stopped_saved_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = profiles.directory(1, base); root.mkdir()
            first = profiles.defaults(1); atomic_json(root / 'config.json', first)
            second = profiles.defaults(2); second['listen_port'] = first['listen_port']
            with self.assertRaises(ValueError):
                profiles.reservations(second, base)

    def test_shrinking_keeps_registration_volume_declarations(self):
        original = profiles.defaults(1, count=3)
        smaller = profiles.resize(original, 1)
        generated = profiles.compose(smaller, Path('/tmp/example'), 'test:image')
        self.assertEqual(len(generated['services']), 1)
        self.assertEqual(set(generated['volumes']), {'warp1', 'warp2', 'warp3'})
        self.assertEqual(len(original['nodes']), 3)

    def test_template_marking_does_not_change_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = profiles.directory(1, base); root.mkdir()
            atomic_json(root / 'config.json', profiles.defaults(1))
            before = (root / 'config.json').read_bytes()
            profiles.mark_template([1], base)
            self.assertEqual((root / 'config.json').read_bytes(), before)
            self.assertEqual(profiles.origin(root)['source'], 'template')

    def test_easy_edit_preserves_advanced_policy(self):
        config = profiles.defaults(1)
        config.update(max_rotations_per_hour=7, deny_regions=['RU', 'BY'], min_speed_kib=1234)
        edited, _ = warp_menu.change(config, 'interval_minutes', '1.5')
        self.assertEqual(edited['check_interval'], 90)
        self.assertEqual(edited['max_rotations_per_hour'], 7)
        self.assertEqual(edited['deny_regions'], ['RU', 'BY'])
        self.assertEqual(config['check_interval'], 60)

    def test_cancelled_edit_has_no_saved_changes(self):
        config = profiles.defaults(1)
        before = json.dumps(config, sort_keys=True)
        with patch('builtins.input', side_effect=['3', '40100', '0']), contextlib.redirect_stdout(io.StringIO()):
            result, browser = warp_menu.edit_draft(config)
        self.assertIsNone(result)
        self.assertEqual(json.dumps(config, sort_keys=True), before)

    def test_public_listener_and_mixed_roles_are_rejected(self):
        for address in ('0.0.0.0', '8.8.8.8', '169.254.1.1'):
            with self.assertRaises(ValueError):
                validate_config(dict(profiles.defaults(1), listen_host=address))
        validate_config(dict(profiles.defaults(1), listen_host='172.17.0.1'))
        with self.assertRaises(ValueError):
            validate_config(dict(profiles.defaults(1), youtube_mode=True))

    def test_routing_export_uses_saved_ports_and_blocks_quic(self):
        youtube = profiles.defaults(2, 'youtube', 1)
        gemini = profiles.defaults(1)
        data = profiles.routing(youtube, gemini)
        self.assertEqual(data['outbounds'][0]['settings']['servers'][0]['port'], youtube['listen_port'])
        self.assertEqual(data['routing']['rules'][0]['network'], 'udp')
        self.assertEqual(data['routing']['rules'][0]['outboundTag'], 'warp-quic-block')
        with self.assertRaises(ValueError):
            profiles.routing(gemini, gemini)

    def test_all_flag_cannot_stop_or_delete_instances(self):
        for command in ('stop', 'uninstall', 'rotate'):
            with patch('warp_ctl.run') as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    warp_ctl.main([command, '--all'])
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()

    def test_stale_status_never_claims_current_serving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); atomic_json(root / 'config.json', profiles.defaults(1))
            atomic_json(root / 'state.json', {'updated': 1, 'ready_tunnels': 1, 'distinct_ready_ips': 1,
                'active': 'warp1', 'nodes': [{'name': 'warp1', 'state': 'READY'}]})
            with patch('warp_ctl.unit_state', return_value='inactive'):
                result = warp_ctl.status_data(root)
            self.assertTrue(result['stale'])
            self.assertEqual(result['ready_tunnels'], 0)
            self.assertEqual(result['nodes'][0]['state'], 'UNKNOWN')

    def test_template_partial_failure_keeps_first_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plan = [profiles.defaults(1, 'gemini', 1), profiles.defaults(2, 'youtube', 1)]
            used = set()
            def free_port():
                while True:
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                    if port not in used:
                        used.add(port); return port
            for config in plan:
                config['listen_port'] = free_port(); config['http_port'] = free_port()
                config['nodes'][0]['port'] = free_port()
            count = 0
            def install(*args):
                nonlocal count
                count += 1
                if count == 2:
                    raise subprocess.CalledProcessError(1, args)
                config = json.loads(Path(args[args.index('--config') + 1]).read_text())
                root = profiles.directory(config['instance']); root.mkdir()
                atomic_json(root / 'config.json', config)
            with patch.object(profiles, 'OPT', base), patch('warp_ctl.run', side_effect=install), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    warp_ctl.execute_plan(plan)
            self.assertTrue((profiles.directory(1, base) / 'config.json').exists())
            self.assertFalse(profiles.directory(2, base).exists())
            self.assertTrue(profiles.origin(profiles.directory(1, base)))

    def test_failed_build_does_not_stop_existing_service(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = profiles.directory(1, base); root.mkdir()
            config = profiles.defaults(1)
            atomic_json(root / 'config.json', config)
            before = (root / 'config.json').read_bytes()
            calls = []
            def command(*args, **kwargs):
                calls.append(tuple(map(str, args)))
                if args[:2] == ('docker', 'build'):
                    raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 1, '', '')
            with patch.object(profiles, 'OPT', base), patch('warp_install.ownership'), patch('warp_install.run', side_effect=command):
                with self.assertRaises(subprocess.CalledProcessError):
                    warp_install.deploy(Path(__file__).resolve().parents[1], config, update=True)
            self.assertFalse(any(c[:2] == ('systemctl', 'stop') for c in calls))
            self.assertEqual((root / 'config.json').read_bytes(), before)


class Policies(unittest.IsolatedAsyncioTestCase):
    async def test_startup_grace_does_not_rotate_a_slow_starting_daemon(self):
        pool = Pool(profiles.defaults(1, count=1)); node = pool.nodes[0]
        pool.config['confirmation_delay'] = 0.01
        seen = asyncio.Event(); count = 0
        async def observe(n):
            nonlocal count
            count += 1
            if count == 4:
                seen.set()
            return Observation('NETWORK', reason='starting')
        pool.observe = observe
        pool.repair = AsyncMock(return_value=False)
        task = asyncio.create_task(pool.worker(node))
        try:
            await asyncio.wait_for(seen.wait(), 1)
            pool.repair.assert_not_awaited()
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_youtube_requires_ru_and_does_not_call_gemini(self):
        pool = Pool(profiles.defaults(2, 'youtube', 1)); pool.config['min_speed_kib'] = 0
        node = pool.nodes[0]; pool.trace = AsyncMock(return_value='1.1.1.1')
        pool.fetch = AsyncMock(return_value='{"GL":"RU"}')
        with patch('warp_pool.command', new_callable=AsyncMock) as command:
            result = await pool.observe(node)
        self.assertEqual(result.result, 'GOOD')
        pool.apply(node, result)
        self.assertEqual(pool.snapshot()['youtube'], 'RU VERIFIED')
        command.assert_not_awaited()
        pool.fetch.return_value = '{"GL":"US"}'
        self.assertEqual((await pool.observe(node)).result, 'BAD')

    async def test_changed_policy_clears_old_rejections_but_keeps_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            original = Pool(profiles.defaults(1)); n = original.nodes[0]
            n.attempts = [time.monotonic()]; n.bad_ips = {'1.1.1.1': time.monotonic() + 100}
            original.save_runtime(path)
            changed = dict(profiles.defaults(1), youtube_mode=True, gemini_check=False)
            restored = Pool(validate_config(changed)); restored.load_runtime(path)
            self.assertFalse(restored.nodes[0].bad_ips)
            self.assertEqual(len(restored.nodes[0].attempts), 1)


class Dependencies(unittest.TestCase):
    def test_modified_owned_compose_binary_is_retained(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(warp_system, 'ROOT', Path(directory)):
            target = Path(directory) / 'usr/local/lib/docker/cli-plugins/docker-compose'
            target.parent.mkdir(parents=True); target.write_bytes(b'changed by another installer')
            warp_system.save_ledger({'version': 1, 'components': {}, 'binaries': {'compose': {
                'owned': True, 'path': '/usr/local/lib/docker/cli-plugins/docker-compose',
                'sha256': hashlib.sha256(b'original').hexdigest()}}})
            with patch('warp_system.remaining_instances', return_value=[]), patch('warp_system.inventory', return_value=set()), patch('warp_system.docker_unused', return_value=(True, '')):
                result = warp_system.cleanup_dependencies(purge=True)
            self.assertTrue(target.exists())
            self.assertIn('retained', result['compose-plugin'])

    def test_other_docker_consumers_prevent_dependency_removal(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(warp_system, 'ROOT', Path(directory)):
            warp_system.save_ledger({'version': 1, 'components': {'docker': {'owned': True, 'packages': ['docker.io']}}})
            with patch('warp_system.remaining_instances', return_value=[]), patch('warp_system.inventory', return_value={'docker.io'}), patch('warp_system.docker_unused', return_value=(False, 'Other containers')), patch('warp_system.purge_plan') as plan:
                result = warp_system.cleanup_dependencies(purge=True)
            plan.assert_not_called()
            self.assertIn('retained', result['docker'])

    def test_apt_plan_cannot_remove_unowned_packages(self):
        result = subprocess.CompletedProcess([], 0, 'Purg docker.io\nRemv nginx\n', '')
        with patch('warp_system.subprocess.run', return_value=result):
            allowed, reason = warp_system.purge_plan(['docker.io'], {'docker.io'})
        self.assertFalse(allowed)


if __name__ == '__main__':
    unittest.main()
