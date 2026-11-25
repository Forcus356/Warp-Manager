"""Capacity-driven WARP pool with verified hot spares. GPL-3.0-only.

The budget includes primary, reserve and searching slots. Only primary nodes
carry new user streams. No sequential speed sum is advertised as pool capacity.
"""
import asyncio
import contextlib
import json
import logging
import math
from pathlib import Path
import shutil
import time

LOG = logging.getLogger('warp-pool.capacity')


def spare_count(primary):
    return max(1, primary // 3) if primary else 0


def token(node):
    return node.ip, node.generation


def finished_window(data, transferred, deadline):
    """A final handshake may time out after earlier requests delivered data.

    Keep the earlier real bytes and include this idle period in elapsed time.
    Never accept an empty whole trial, early timeout, HTTP error or HTML.
    """
    return (transferred >= 50000 and time.monotonic() >= deadline
            and int(data.get('exitcode', 0)) == 28
            and int(data['http_code']) in (0, 200)
            and int(data['size_download']) == 0
            and 'text/html' not in (data.get('content_type') or '').lower())


class Capacity:
    def __init__(self, pool):
        self.pool = pool
        self.config = pool.config
        self.target = self.config['target_gbps'] * 1000
        self.samples = {}  # name -> (IP/generation token, Mbps, monotonic time)
        self.primary = []
        self.preferred = []
        self.minimum = 1
        self.aggregate = None
        self.last_trial = None
        self.plateaus = 0
        self.hold_until = 0
        self.next_aggregate = 0
        self.retry_at = 0
        self.cursor = 0
        self.phase = 'STARTING'
        self.reason = ''
        self.root = None
        self.force_nodes = set()
        for index, node in enumerate(pool.nodes):
            node.enabled = index == 0
            if not node.enabled:
                node.state = node.phase = 'DORMANT'

    def persist(self):
        return {'target_gbps': self.config['target_gbps'], 'minimum': self.minimum,
                'enabled': [n.name for n in self.pool.nodes if n.enabled],
                'primary': list(self.primary)}

    def restore(self, value):
        # Restore allocations, never readiness or old benchmark claims.
        if not isinstance(value, dict) or value.get('target_gbps') != self.config['target_gbps']:
            return
        enabled = value.get('enabled', [])
        if not isinstance(enabled, list):
            return
        names = {n.name for n in self.pool.nodes}
        self.preferred = [n for n in value.get('primary', []) if n in names]
        self.minimum = max(1, min(len(names), int(value.get('minimum', 1))))
        for n in self.pool.nodes:
            n.enabled = n.name in enabled or n is self.pool.nodes[0]
            n.state = n.phase = 'STARTING' if n.enabled else 'DORMANT'

    def invalidate(self, node):
        self.samples.pop(node.name, None)

    def measured(self, node):
        value = self.samples.get(node.name)
        return bool(self.pool.eligible(node) and value and value[0] == token(node)
                    and time.monotonic() - value[2] < self.config['capacity_sample_ttl'])

    def reconcile(self):
        good = [n for n in self.pool.nodes if self.measured(n)]
        by_name = {n.name: n for n in good}
        self.primary = [name for name in self.primary if name in by_name]
        others = [n for n in good if n.name not in self.primary]
        others.sort(key=lambda n: (n.name not in self.preferred,
                                  self.preferred.index(n.name) if n.name in self.preferred else 0,
                                  -self.samples[n.name][1]))
        total = sum(self.samples[name][1] for name in self.primary)
        held = time.monotonic() < self.hold_until
        for n in others:
            if self.primary and ((held and len(self.primary) >= self.minimum)
                                 or (not held and total >= self.target and len(self.primary) >= self.minimum)):
                break
            self.primary.append(n.name)
            total += self.samples[n.name][1]
        return [by_name[name] for name in self.primary], [n for n in good if n.name not in self.primary]

    def members(self, nodes):
        return tuple((n.name, token(n)) for n in nodes)

    def current_aggregate(self, primary):
        if (self.aggregate and self.aggregate['members'] == self.members(primary)
                and time.monotonic() - self.aggregate['at'] < self.config['capacity_sample_ttl']):
            return self.aggregate['mbps']
        return None

    def needs_search(self):
        primary, reserve = self.reconcile()
        total = sum(self.samples[n.name][1] for n in primary)
        need_core = (not primary or total < self.target or len(primary) < self.minimum)
        if time.monotonic() < self.hold_until and primary:
            need_core = False
        return need_core or len(reserve) < spare_count(len(primary))

    def select(self, route=False):
        primary, _ = self.reconcile()
        if not primary:
            return None
        # A 1-Gbps node gets twice the new-stream allocation of a 0.5-Gbps
        # node when stream counts are comparable. Existing TCP is never moved.
        start = self.cursor % len(primary)
        ordered = primary[start:] + primary[:start]
        selected = min(ordered, key=lambda n: (len(self.pool.connections[n.name]) // 2 + n.pending)
                       / max(self.samples[n.name][1], 0.001))
        if route:
            self.cursor = (primary.index(selected) + 1) % len(primary)
        return selected

    def role(self, node):
        if not node.enabled:
            return 'DORMANT'
        self.reconcile()
        if node.name in self.primary:
            return 'PRIMARY'
        return 'RESERVE' if self.measured(node) else 'SEARCH'

    def snapshot(self):
        primary, reserve = self.reconcile()
        aggregate = self.current_aggregate(primary)
        reached = aggregate is not None and aggregate >= self.target
        return {'target_gbps': self.config['target_gbps'],
                'phase': self.phase, 'reason': self.reason,
                'primary': [n.name for n in primary], 'reserve': [n.name for n in reserve],
                'reserve_needed': spare_count(len(primary)),
                'enabled': sum(n.enabled for n in self.pool.nodes), 'limit': len(self.pool.nodes),
                'individual_sum_gbps': round(sum(self.samples[n.name][1] for n in primary) / 1000, 4),
                'measured_gbps': round(aggregate / 1000, 4) if aggregate is not None else None,
                'target_reached': reached,
                'complete': reached and len(reserve) >= spare_count(len(primary)),
                'retry_in': max(0, round(self.hold_until - time.monotonic()))}

    async def download(self, node, deadline):
        from warp_pool import command
        downloaded = 0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining < 0.05:
                break
            # A timed-out transfer is useful only when HTTP 200 and real bytes
            # have already arrived. Other curl errors never produce capacity.
            text = await command('curl', '-q', '-4', '-fsS', '--noproxy', '',
                                 '--proxy', f'socks5h://127.0.0.1:{node.port}',
                                 '--connect-timeout', str(min(7, remaining)),
                                 '--max-time', str(remaining), '--http1.1',
                                 '-H', 'Accept-Encoding: identity', '-o', '/dev/null',
                                 '-w', '%{json}', self.config['capacity_url'],
                                 timeout=remaining + 3, accepted_codes=(0, 28))
            value = json.loads(text)
            if finished_window(value, downloaded, deadline):
                break
            if (int(value['http_code']) != 200 or
                    'text/html' in (value.get('content_type') or '').lower() or
                    int(value['size_download']) <= 0):
                raise ValueError('speed endpoint did not return test data')
            downloaded += int(value['size_download'])
        if downloaded < 50000:
            raise ValueError('speed sample too small')
        return downloaded

    async def measure(self, nodes):
        async with self.pool.speed_lock:
            return await self._measure(nodes)

    async def _measure(self, nodes):
        from warp_pool import Observation
        identities = self.members(nodes)
        before = await asyncio.gather(*(self.pool.trace(n) for n in nodes))
        if before != [n.ip for n in nodes] or identities != self.members(nodes):
            for node, address in zip(nodes, before):
                if address != node.ip:
                    self.pool.apply(node, Observation('UNKNOWN', reason='exit changed before speed probe'))
            raise ValueError('exit changed before speed probe')
        started = time.monotonic()
        deadline = started + self.config['capacity_seconds']
        # Wait for every result, including failed transfers, before returning.
        # No cancelled subprocess can outlive the sample or overlap the next.
        results = await asyncio.gather(*(self.download(n, deadline) for n in nodes), return_exceptions=True)
        elapsed = time.monotonic() - started
        if any(isinstance(r, BaseException) for r in results):
            raise ValueError('incomplete simultaneous speed probe')
        after = await asyncio.gather(*(self.pool.trace(n) for n in nodes))
        if (after != before or identities != self.members(nodes)
                or any(not self.pool.eligible(n) for n in nodes)
                or elapsed < self.config['capacity_seconds'] * 0.75):
            for node, address, expected in zip(nodes, after, before):
                if address != expected:
                    self.pool.apply(node, Observation('UNKNOWN', reason='exit changed during speed probe'))
            raise ValueError('exit or health changed during speed probe')
        mbps = sum(results) * 8 / elapsed / 1e6
        if not math.isfinite(mbps) or mbps <= 0:
            raise ValueError('invalid capacity result')
        return mbps

    def record_aggregate(self, primary, mbps):
        now = time.monotonic()
        self.aggregate = {'members': self.members(primary), 'mbps': mbps, 'at': now}
        self.next_aggregate = now + self.config['capacity_interval']
        if mbps < self.target:
            if self.last_trial and len(primary) > self.last_trial[0]:
                self.plateaus = self.plateaus + 1 if mbps < self.last_trial[1] * 1.05 else 0
            self.last_trial = (len(primary), mbps)
            if self.plateaus >= 3:
                self.hold_until = now + self.config['capacity_interval']
                self.reason = 'three expansions added less than 5%; shared link may be saturated'
            else:
                self.minimum = min(len(self.pool.nodes), len(primary) + 1)
        else:
            self.plateaus = 0
            self.last_trial = None

    def resources(self):
        if self.root and shutil.disk_usage(self.root).free < 512 * 1024**2:
            return 'less than 512 MiB free disk'
        try:
            values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
            if int(values['MemAvailable'].split()[0]) < 256 * 1024:
                return 'less than 256 MiB available RAM'
        except (OSError, ValueError, KeyError):
            pass
        return ''

    async def start(self, node):
        from warp_pool import command
        await command('docker', 'compose', '-f', self.root / 'compose.yaml',
                      'up', '-d', '--no-build', node.name, timeout=120)
        node.enabled = True
        node.state = node.phase = 'STARTING'
        node.start_deadline = time.monotonic() + self.config['wait_timeout']
        node.wake.set()
        if getattr(self.pool, 'runtime_path', None):
            self.pool.save_runtime(self.pool.runtime_path)

    async def park(self, node):
        from warp_pool import command
        async with self.pool.repair_lock:
            if self.pool.connections[node.name] or node.pending:
                return
            node.enabled = False
            node.generation += 1
            node.requested_method = ''
            self.invalidate(node)
            node.state = node.phase = 'DORMANT'
            await command('docker', 'compose', '-f', self.root / 'compose.yaml',
                          'stop', node.name, timeout=60)
            if getattr(self.pool, 'runtime_path', None):
                self.pool.save_runtime(self.pool.runtime_path)

    async def tick(self):
        now = time.monotonic()
        if self.hold_until and now >= self.hold_until:
            self.hold_until = 0
            self.plateaus = 0
            self.minimum = min(len(self.pool.nodes), len(self.primary) + 1)
            self.aggregate = None
        primary, reserve = self.reconcile()
        if self.current_aggregate(primary) is not None and self.current_aggregate(primary) >= self.target and len(reserve) >= spare_count(len(primary)):
            kept = {n.name for n in primary + reserve[:spare_count(len(primary))]}
            extra = next((n for n in reversed(self.pool.nodes) if n.enabled and n.name not in kept), None)
            if extra and self.root:
                self.phase = 'PARKING_EXCESS'
                await self.park(extra)
                return
        # Refresh primary/reserve samples before their expiry. One at a time;
        # aggregate tests are the only intentionally competing speed downloads.
        due = [n for n in self.pool.nodes if self.pool.eligible(n)
               and (n.name in self.force_nodes or not self.measured(n)
                    or now - self.samples[n.name][2] >= self.config['capacity_interval'])]
        if due and now >= self.retry_at:
            node = due[0]
            self.phase = 'MEASURING_NODE'
            try:
                mbps = await self.measure([node])
                self.samples[node.name] = (token(node), mbps, time.monotonic())
                self.force_nodes.discard(node.name)
                node.speed_kib_s = mbps * 1e6 / 8192
                node.speed_at = time.monotonic()
                node.slow_samples = node.slow_samples + 1 if node.speed_kib_s < self.config['min_speed_kib'] else 0
                if node.slow_samples >= self.config['slow_threshold']:
                    self.invalidate(node)
                    node.requested_method = self.config['rotation_method']
                    node.wake.set()
                self.reason = ''
            except (OSError, RuntimeError, ValueError, KeyError, asyncio.TimeoutError):
                # A broken test endpoint must never rotate a valid Gemini exit.
                self.reason = 'speed probe inconclusive; retrying without rotating verified exits'
                self.retry_at = time.monotonic() + 60
            return
        primary, reserve = self.reconcile()
        mbps = self.current_aggregate(primary)
        enough = sum(self.samples[n.name][1] for n in primary) >= self.target and len(primary) >= self.minimum
        if primary and enough and (mbps is None or now >= self.next_aggregate) and now >= self.retry_at:
            self.phase = 'MEASURING_POOL'
            try:
                self.record_aggregate(primary, await self.measure(primary))
                self.reason = '' if not self.hold_until else self.reason
            except (OSError, RuntimeError, ValueError, KeyError, asyncio.TimeoutError):
                self.aggregate = None
                self.reason = 'simultaneous probe inconclusive; target is not confirmed'
                self.retry_at = time.monotonic() + 60
            return
        need = self.needs_search()
        waiting = [n for n in self.pool.nodes if n.enabled and not self.measured(n)]
        dormant = next((n for n in self.pool.nodes if not n.enabled), None)
        core_needed = not primary or sum(self.samples[n.name][1] for n in primary) < self.target or len(primary) < self.minimum
        concurrency = self.config['capacity_parallel_search'] if core_needed else min(
            self.config['capacity_parallel_search'], max(0, spare_count(len(primary)) - len(reserve)))
        if need and dormant and len(waiting) < concurrency and now >= self.retry_at:
            shortage = self.resources()
            if shortage:
                self.phase, self.reason = 'RESOURCE_LIMIT', shortage
                self.retry_at = now + 60
                return
            self.phase = 'STARTING_NODE'
            try:
                await self.start(dormant)
            except (OSError, RuntimeError, asyncio.TimeoutError):
                self.phase, self.reason = 'START_FAILED', 'cannot start another WARP slot'
                self.retry_at = time.monotonic() + 60
            return
        state = self.snapshot()
        if self.hold_until:
            self.phase = 'SATURATED'
        elif state['complete']:
            self.phase = 'READY'
        elif state['target_reached']:
            self.phase = 'RESERVE_SEARCH'
        elif need and not dormant and not waiting:
            self.phase, self.reason = 'LIMIT', 'tunnel budget reached; target or reserve is incomplete'
        elif not primary:
            self.phase = 'SEARCHING'
        else:
            self.phase = 'BUILDING'

    async def run(self, root):
        from warp_pool import command
        self.root = root
        enabled = [n.name for n in self.pool.nodes if n.enabled]
        dormant = [n.name for n in self.pool.nodes if not n.enabled]
        # Recover previously allocated services after a reboot. Compose profiles
        # ensure the full configured budget is never started by ExecStartPre.
        if dormant:
            await command('docker', 'compose', '-f', root / 'compose.yaml', 'stop', *dormant, timeout=120)
        await command('docker', 'compose', '-f', root / 'compose.yaml',
                      'up', '-d', '--no-build', *enabled, timeout=120)
        while True:
            try:
                await self.tick()
            except (OSError, RuntimeError, ValueError, KeyError, asyncio.TimeoutError):
                LOG.exception('capacity controller iteration failed')
                self.phase = 'RETRY'
                self.retry_at = time.monotonic() + 60
            await asyncio.sleep(2)
