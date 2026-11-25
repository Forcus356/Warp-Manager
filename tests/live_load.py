#!/usr/bin/env python3
"""Explicit isolated WARP transport stress test, not a Gemini certificate.

Creates only containers in the dedicated --project namespace in its own Compose project, then removes
those containers/volumes in finally. Never changes Xray, Psiphon or WARP pools.
Real per-node measurements weight the production Capacity selector. A forced
minimum selects all N tunnels for a scaling test; no READY status is published.
"""
import argparse
import asyncio
import collections
import contextlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace


def resources():
    mem = {k: int(v.split()[0]) for k, v in (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())}
    cpu = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    vm = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
    return {'available_mib': mem['MemAvailable'] / 1024,
            'swap_used_mib': (mem['SwapTotal'] - mem['SwapFree']) / 1024,
            'cpu': cpu, 'swap_out_pages': int(vm['pswpout'])}


def cpu_delta(before, after):
    values = [b - a for a, b in zip(before['cpu'], after['cpu'])]
    total = sum(values)
    return {'cpu_busy_percent': 100 * (total - values[3] - values[4]) / total if total else 0,
            'cpu_steal_percent': 100 * values[7] / total if total else 0}


async def main(args):
    sys.path.insert(0, str(args.source))
    from warp_pool import Pool, Observation, command, validate_config, atomic_json, VERSION
    from warp_capacity import token, finished_window
    import warp_profiles as profiles
    args.work.mkdir(parents=True, exist_ok=True, mode=0o700)
    info=args.work.stat()
    if args.work.is_symlink() or info.st_uid!=os.geteuid() or info.st_mode & 0o022:
        raise ValueError('test work directory must be owned and not writable by other users')
    config = profiles.defaults(99, 'warp', args.maximum)
    config.update(deny_regions=[], allow_regions=[], target_gbps=1,
                  capacity_seconds=2, capacity_url=args.url,
                  listen_port=args.base_port+90, http_port=None,
                  nodes=[{'name': f'warp{i}', 'container': f'{args.project}-{i}', 'port': args.base_port+i}
                         for i in range(1, args.maximum + 1)])
    config = validate_config(config)
    for node in config['nodes']:
        existing = await command('docker', 'ps', '-aq', '--filter', 'name=^/' + node['container'] + '$')
        if existing.strip():
            raise ValueError('test container already exists: ' + node['container'])
    with profiles.reserve_ports(config):
        pass
    compose = profiles.compose(config, args.work, 'warp-google-pool-worker:' + VERSION)
    compose['name'] = args.project
    for service in compose['services'].values():
        service.update(profiles=['loadtest'], mem_limit='512m', pids_limit=512)
    compose_path = args.work / 'compose.json'
    atomic_json(compose_path, compose)
    atomic_json(args.work / 'compose.yaml',compose)
    pool = Pool(config)
    cap = pool.capacity
    cap.root=args.work
    selected = collections.Counter()
    original_choose = pool.choose
    def choose(route=False):
        node = original_choose(route)
        if node is not None and route:
            selected[node.name] += 1
        return node
    pool.choose = choose
    result = {'kind': 'transport_only_no_gemini_or_youtube_certification',
              'started': time.time(), 'source_version': VERSION,
              'seconds_per_trial': args.seconds, 'url': args.url,
              'ramp_seconds': args.ramp_seconds,
              'baseline': [], 'trials': [], 'native_nodes': [], 'identity_changes': [],
              'completed': False, 'success': False,
              'constraints': {'memory_floor_mib': 384, 'per_container_memory_limit_mib': 512,
                              'all_traffic_generated_on_same_vps': True}}
    def save():
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    save()
    cgroups = {}

    def container_limits():
        values={}
        for name,path in cgroups.items():
            try:
                mem=dict(line.split() for line in (path/'memory.events').read_text().splitlines())
                pids=dict(line.split() for line in (path/'pids.events').read_text().splitlines())
                values[name]={'oom_kill':int(mem.get('oom_kill',0)), 'pids_limit_events':int(pids.get('max',0)),
                              'memory_peak_mib':int((path/'memory.peak').read_text())/1024**2}
            except (OSError,ValueError):
                values[name]={'unavailable':True}
        return values

    async def certify_transport(node):
        last=None
        for attempt in range(4):
            try:
                address=await pool.trace(node)
                changed=address!=node.ip
                if changed and node.ip:
                    result['identity_changes'].append({'name':node.name,'before':node.ip,'after':address,'at':time.time()})
                pool.apply(node,Observation('GOOD',ip=address,gemini='DISABLED',reason='isolated transport test; service not certified'))
                if changed or not cap.measured(node):
                    mbps=await cap.measure([node])
                    cap.samples[node.name]=(token(node),mbps,time.monotonic())
                return address,cap.samples[node.name][1]
            except (OSError,RuntimeError,ValueError,asyncio.TimeoutError) as error:
                last=error
                pool.apply(node,Observation('NETWORK',reason='transport unavailable during test certification'))
                if attempt==1:
                    await pool.repair(node,'reconnect')
                await asyncio.sleep(2)
        raise RuntimeError('test transport did not stabilize: '+node.name) from last

    async def direct_download(deadline):
        count = 0
        while (remaining := deadline - time.monotonic()) > 0.05:
            text = await command('curl', '-q', '-4', '-fsS', '--noproxy', '*',
                                 '--connect-timeout', str(min(7, remaining)),
                                 '--max-time', str(remaining), '--http1.1',
                                 '-H', 'Accept-Encoding: identity', '-o', '/dev/null',
                                 '-w', '%{json}', args.url, timeout=remaining+3, accepted_codes=(0,28))
            data = json.loads(text)
            if finished_window(data,count,deadline):
                break
            if int(data['http_code']) != 200 or 'text/html' in (data.get('content_type') or '') or data['size_download'] <= 0:
                raise ValueError('direct endpoint returned no test data')
            count += int(data['size_download'])
        if count < 50000:
            raise ValueError('too few direct bytes')
        return count

    async def trial(clients, front_port=None):
        selected.clear()
        samples = []
        maxima = collections.Counter()
        before = resources()
        running = True
        peak_total = 0
        async def monitor():
            nonlocal peak_total
            while running:
                sample = resources(); samples.append(sample)
                peak_total = max(peak_total,sum(len(pool.connections[n.name]) // 2 + n.pending for n in pool.nodes))
                for n in pool.nodes:
                    maxima[n.name] = max(maxima[n.name], len(pool.connections[n.name]) // 2 + n.pending)
                if sample['available_mib'] < 256:
                    result['aborted_trial'] = {'clients':clients, 'native_tunnels':cap.minimum,
                                               'available_mib':sample['available_mib']}
                    save()
                    raise RuntimeError('memory pressure; aborting load before host exhaustion')
                await asyncio.sleep(0.1)
        monitor_task = asyncio.create_task(monitor())
        started = time.monotonic()
        deadline = started + args.seconds
        proxy = SimpleNamespace(port=front_port)
        async def transfer(index):
            if args.ramp_seconds:
                await asyncio.sleep(index * args.ramp_seconds / clients)
            return await (cap.download(proxy, deadline) if front_port else direct_download(deadline))
        transfers = [asyncio.create_task(transfer(index)) for index in range(clients)]
        async def all_transfers():
            return await asyncio.gather(*transfers, return_exceptions=True)
        aggregate = asyncio.create_task(all_transfers())
        try:
            done, _ = await asyncio.wait([aggregate, monitor_task], return_when=asyncio.FIRST_COMPLETED)
            if monitor_task in done:
                monitor_task.result()
            outputs = await aggregate
        finally:
            running = False
            for task in transfers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*transfers, return_exceptions=True)
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task
        elapsed = time.monotonic() - started
        after = resources()
        good = [n for n in outputs if isinstance(n, int)]
        failures = [type(n).__name__ + ': ' + str(n) for n in outputs if isinstance(n, BaseException)]
        return {'clients': clients, 'bytes': sum(good), 'elapsed_seconds': elapsed,
                'mbps': sum(good) * 8 / elapsed / 1e6,
                'successful_clients': len(good), 'failed_clients': len(failures),
                'errors': dict(collections.Counter(failures)),
                'selected_connections': dict(selected), 'peak_connections_by_node': dict(maxima),
                'peak_tcp_connections': peak_total,
                'container_limits':container_limits(),
                'minimum_available_mib': min(s['available_mib'] for s in samples),
                'swap_out_pages': after['swap_out_pages'] - before['swap_out_pages'],
                **cpu_delta(before, after)}

    front = None
    allocated = 0
    try:
        for clients in args.baseline_clients:
            item = await trial(clients)
            result['baseline'].append(item); save()
            print(json.dumps({'baseline_clients': clients, **{k:item[k] for k in ('mbps','failed_clients','cpu_busy_percent')}}), flush=True)
        front = await asyncio.start_server(pool.relay, '127.0.0.1', 0)
        front_port = front.sockets[0].getsockname()[1]
        for size in args.sizes:
            for index in range(allocated, size):
                if resources()['available_mib'] < 384 or shutil.disk_usage(args.work).free < 512 * 1024**2:
                    result['stop_reason'] = 'resource floor before allocating another native WARP'
                    save()
                    return
                node = pool.nodes[index]
                await command('docker', 'compose', '-f', compose_path, 'up', '-d', '--no-build', node.name, timeout=120)
                allocated = index + 1
                node.enabled = True
                process=int((await command('docker','inspect',node.container,'--format','{{.State.Pid}}')).strip())
                with contextlib.suppress(OSError,ValueError):
                    relative=next(line.split(':',2)[2] for line in Path(f'/proc/{process}/cgroup').read_text().splitlines() if line.startswith('0::'))
                    cgroups[node.name]=Path('/sys/fs/cgroup')/relative.lstrip('/')
                deadline = time.monotonic() + 100
                while True:
                    try:
                        address = await pool.trace(node)
                        break
                    except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('native WARP did not connect: ' + node.name)
                        await asyncio.sleep(2)
                address,mbps=await certify_transport(node)
                result['native_nodes'].append({'name': node.name, 'ip': address, 'individual_mbps': mbps})
                save()
            cap.minimum = size
            for node in pool.nodes[:size]:
                await certify_transport(node)
            save()
            for clients in args.clients:
                item = await trial(clients, front_port)
                item['native_tunnels'] = size
                result['trials'].append(item); save()
                print(json.dumps({'native_tunnels':size, **{k:item[k] for k in ('clients','mbps','failed_clients','cpu_busy_percent','minimum_available_mib')}}), flush=True)
                if item['minimum_available_mib'] < 384:
                    result['stop_reason'] = 'resource floor during load'
                    save()
                    return
            await asyncio.sleep(1)
        result['completed'] = True
        result['success'] = all(r['failed_clients'] == 0 for r in result['baseline'] + result['trials'])
    finally:
        if front:
            front.close(); await front.wait_closed()
        for writers in pool.connections.values():
            for writer in tuple(writers):
                writer.close()
        cleanup = await command('docker', 'compose', '-f', compose_path, '--profile', '*',
                                'down', '-v', timeout=180, accepted_codes=(0,1))
        result['cleanup'] = cleanup
        result['finished'] = time.time(); save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--work', type=Path)
    parser.add_argument('--output', type=Path, default=Path('load-results.json'))
    parser.add_argument('--maximum', type=int, choices=(1,2,4,8,12,16), default=4)
    parser.add_argument('--seconds', type=float, default=8)
    parser.add_argument('--url', default='https://speed.cloudflare.com/__down?bytes=8000000')
    parser.add_argument('--sizes', default='1,2,4,8,12')
    parser.add_argument('--clients', default='1,8,32,64,128')
    parser.add_argument('--baseline-clients', default='1,8,32')
    parser.add_argument('--base-port', type=int, default=23000)
    parser.add_argument('--ramp-seconds', type=float, default=0)
    parser.add_argument('--project', default='warp-manager-loadtest')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}',args.project):
        parser.error('invalid dedicated Compose project name')
    if not 2 <= args.seconds <= 30:
        parser.error('--seconds: 2–30')
    if not 1024 <= args.base_port <= 65400:
        parser.error('--base-port: 1024–65400')
    if not 0 <= args.ramp_seconds < args.seconds-2:
        parser.error('--ramp-seconds must leave at least two seconds of measurement')
    for key in ('sizes','clients','baseline_clients'):
        try:
            raw=getattr(args,key)
            values = [] if key=='baseline_clients' and raw=='none' else [int(n) for n in raw.split(',')]
        except ValueError:
            parser.error(key + ': expected comma-separated integers')
        if (not values and key!='baseline_clients') or any(n < 1 or n > (16 if key == 'sizes' else 256) for n in values):
            parser.error('invalid '+key)
        if key == 'sizes':
            values = sorted(set(n for n in values if n <= args.maximum))
            if not values:
                parser.error('no stage is within --maximum')
        setattr(args,key,values)
    with tempfile.TemporaryDirectory(prefix='warp-manager-load-') as folder:
        args.work=args.work or Path(folder)
        asyncio.run(main(args))
