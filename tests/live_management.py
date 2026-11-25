#!/usr/bin/env python3
"""Manual staging integration test; explicitly adopts a disposable YouTube instance.

Run only on a staging server. --adopt-test-instance N authorizes reconfiguration
and removal of that disposable instance. Instance 1 is updated but never removed.
SPDX-License-Identifier: GPL-3.0-only
"""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, '/usr/local/lib/warp-google-pool')
import warp_ctl
import warp_profiles as profiles
from warp_pool import read_config, VERSION


def run(*args, accepted=(0,)):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=240)
    if result.returncode not in accepted:
        raise RuntimeError(f'{args[0]} failed: {result.returncode}: {result.stderr[-1000:]}')
    return result.stdout.strip()


def ctl(*args):
    return run(sys.executable, '/usr/local/lib/warp-google-pool/warp_ctl.py', *args)


def wait_ready(number, timeout=150):
    root = profiles.directory(number); deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = root / 'state.json'
        if path.is_file():
            data = json.loads(path.read_text())
            pid = run('systemctl', 'show', profiles.unit(number), '-p', 'MainPID', '--value')
            if time.time() - data['updated'] < 10 and data['ready_tunnels'] and str(data.get('pid')) == pid:
                return data
        time.sleep(1)
    raise RuntimeError(f'instance {number}: no verified exit before timeout')


def trace(host, port, scheme='http'):
    text = run('curl', '-q', '-4', '-fsS', '--noproxy', '', '--proxy', f'{scheme}://{host}:{port}',
               '--max-time', '25', 'https://www.cloudflare.com/cdn-cgi/trace')
    values = dict(row.split('=', 1) for row in text.splitlines() if '=' in row)
    assert values.get('warp') in ('on', 'plus'), values
    return {'warp': values['warp'], 'ip': values['ip']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adopt-test-instance', type=int, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    number = args.adopt_test_instance
    if number == 1:
        raise ValueError('instance 1 cannot be adopted as disposable')
    root = profiles.directory(number)
    original = read_config(root / 'config.json')
    if not original['youtube_mode'] or len(original['nodes']) != 1:
        raise ValueError('expected a disposable single-tunnel YouTube instance')
    results = {'version': VERSION, 'test_instance': number}
    try:
        data = wait_ready(number)
        assert data['youtube'] == 'RU VERIFIED'
        results['youtube'] = data['youtube']
        results['http_connect'] = trace(original['listen_host'], original['http_port'])
        results['socks'] = trace(original['listen_host'], original['listen_port'], 'socks5h')
        configs = json.loads(ctl('config', '--all'))['instances']
        assert {c['instance'] for c in configs} >= {1, number}
        results['all_views'] = True
        plan = json.loads(ctl('templates', 'both'))
        assert len(plan) == 2 and all(not profiles.directory(c['instance']).exists() for c in plan)
        results['template_preview'] = [{'instance': c['instance'], 'role': profiles.role(c)} for c in plan]
        ctl('--instance', number, 'autostart', 'off')
        ctl('update', args.source, '--all')
        assert run('systemctl', 'is-enabled', profiles.unit(number), accepted=(0, 1)).strip() == 'disabled'
        assert run('systemctl', 'is-active', profiles.unit(number)) == 'active'
        results['update_all_preserves_disabled_autostart'] = True
        wait_ready(number)
        primary_pid = run('systemctl', 'show', profiles.unit(1), '-p', 'MainPID', '--value')
        config = read_config(root / 'config.json')
        warp_ctl.apply_config(root, profiles.resize(config, 2))
        assert len(read_config(root / 'config.json')['nodes']) == 2
        deadline = time.monotonic() + 45
        added = read_config(root / 'config.json')['nodes'][1]
        while time.monotonic() < deadline:
            text = run('docker', 'exec', added['container'], 'warp-cli', '--accept-tos', 'status', accepted=(0, 1))
            if 'Connected' in text:
                break
            time.sleep(1)
        else:
            raise RuntimeError('added tunnel did not connect')
        warp_ctl.apply_config(root, profiles.resize(read_config(root / 'config.json'), 1))
        assert run('docker', 'volume', 'inspect', profiles.directory(number).name + '_warp2')
        assert primary_pid == run('systemctl', 'show', profiles.unit(1), '-p', 'MainPID', '--value')
        results['resize_preserves_volume_and_other_manager'] = True
        port = 40102 + 2 * number
        ctl('--instance', number, 'config', 'listen_port', port)
        wait_ready(number)
        changed = read_config(root / 'config.json')
        assert changed['listen_port'] == port and changed['http_port'] == port + 1
        results['changed_http_port'] = trace('127.0.0.1', port + 1)
        routing = json.loads(ctl('xray-routing', '--youtube-instance', number, '--gemini-instance', 1))
        assert routing['outbounds'][0]['settings']['servers'][0]['port'] == port
        results['routing_uses_saved_ports'] = True
        addresses = json.loads(run('ip', '-j', '-4', 'addr'))
        private = next(info['local'] for row in addresses for info in row.get('addr_info', [])
                       if any(ipaddress.ip_address(info['local']) in ipaddress.ip_network(net)
                              for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
        private_config = dict(changed, listen_host=private, youtube_mode=False, gemini_check=False,
                              deny_regions=[], allow_regions=[], min_speed_kib=0)
        warp_ctl.apply_config(root, private_config); wait_ready(number)
        results['private_http_bind'] = trace(private, port + 1)
        warp_ctl.apply_config(root, changed); wait_ready(number)
        assert primary_pid == run('systemctl', 'show', profiles.unit(1), '-p', 'MainPID', '--value')
        results['primary_pid_unchanged_by_secondary_changes'] = True
    finally:
        if (root / 'config.json').exists():
            results['cleanup'] = ctl('--instance', number, 'uninstall', '--yes', '--purge-profiles', '--purge-deps')
        results['remaining_instances'] = profiles.discover()
        results['docker'] = run('docker', '--version')
        results['primary_service'] = run('systemctl', 'is-active', profiles.unit(1))
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
