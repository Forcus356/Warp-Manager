#!/usr/bin/env python3
"""Explicit staging-only test; stops one primary container, then observes repair.

Run as root: python3 tests/live_capacity.py --instance 2 --stop-primary
Does not install, remove or change a profile. Uses real mode checks and curl.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time


def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True, timeout=180)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=int, required=True)
    parser.add_argument('--stop-primary', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'instance': args.instance, 'started': time.time(), 'events': []}

    def save(event, state=None):
        item = {'event': event, 'at': time.time()}
        if state:
            item.update(role=state['role'], capacity=state['capacity'],
                        nodes=[{k: n.get(k) for k in ('name', 'role', 'state', 'ip', 'google', 'gemini', 'rotations')} for n in state['nodes']])
        result['events'].append(item)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(event, flush=True)

    def status():
        return json.loads(run('warp-pool', '--instance', args.instance, 'status', '--json'))

    def wait(predicate, timeout=240):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = status()
            if not state['stale'] and predicate(state):
                return state
            time.sleep(2)
        raise RuntimeError('live pool did not reach requested test state')

    def trace(state, http=False):
        address = f"{'http' if http else 'socks5h'}://127.0.0.1:{state['http'] if http else state['socks']}"
        text = run('curl', '-q', '-4', '-fsS', '--noproxy', '', '--proxy', address,
                   '--max-time', '20', 'https://www.cloudflare.com/cdn-cgi/trace')
        values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        assert values['warp'] in ('on', 'plus'), 'front bypassed WARP'

    state = wait(lambda d: d.get('capacity', {}).get('complete'))
    assert state['capacity']['enabled'] < state['configured_tunnels'], 'test needs dormant slots'
    active = set(state['capacity']['primary'] + state['capacity']['reserve'])
    for node in state['nodes']:
        if node['name'] in active:
            assert node['google'] == 'RU' if state['role'] == 'YouTube' else node['gemini'] == 'SERVING'
    save('target_and_hot_reserve_verified', state)
    trace(state); trace(state, http=True)
    save('socks_and_http_fronts_warp_on')
    failed = state['capacity']['primary'][0]
    before_reserve = set(state['capacity']['reserve'])
    root = Path('/opt') / ('warp-google-pool' + ('' if args.instance == 1 else f'-{args.instance}'))
    config = json.loads((root / 'config.json').read_text())
    node = next(n for n in config['nodes'] if n['name'] == failed)
    run('docker', 'stop', node['container'])
    save('primary_container_stopped')
    run('warp-pool', '--instance', args.instance, 'check', failed)
    state = wait(lambda d: failed not in d['capacity']['primary']
                 and bool(set(d['capacity']['primary']) & before_reserve), timeout=50)
    save('watchdog_promoted_hot_reserve', state)
    trace(state)
    save('traffic_continued_through_promoted_reserve')
    # Request immediate observations, but never manually start the container.
    # Native watchdog thresholds/backoff/startup grace still apply.
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        state = status()
        running = run('docker', 'inspect', node['container'], '--format', '{{.State.Running}}').strip()
        if running == 'true' and state['capacity']['complete']:
            break
        run('warp-pool', '--instance', args.instance, 'check', failed)
        time.sleep(5)
    else:
        raise RuntimeError('watchdog did not restore stopped container / reserve')
    save('container_restarted_and_hot_reserve_replenished', state)
    trace(state); trace(state, http=True)
    result['success'] = True; result['finished'] = time.time()
    save('success', state)


if __name__ == '__main__':
    main()
