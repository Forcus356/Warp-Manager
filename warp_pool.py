#!/usr/bin/env python3
"""Three independently registered WARP exits behind one local TCP proxy port.

SPDX-License-Identifier: GPL-3.0-only
"""
import argparse
import asyncio
import contextlib
import dataclasses
import fcntl
import hashlib
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import sys
import time

LOG = logging.getLogger("warp-pool")
ROOT = Path(__file__).resolve().parent
VERSION = "0.4.1"


async def command(*args, timeout=40, accepted_codes=(0,)):
    process = await asyncio.create_subprocess_exec(
        *map(str, args), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.communicate()
        raise
    if process.returncode not in accepted_codes:
        # Registration output and potentially sensitive daemon output stay private.
        raise RuntimeError(f"{Path(str(args[0])).name}: exit {process.returncode}")
    return out.decode(errors="replace")


def parse_trace(body):
    fields = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
    ip = str(ipaddress.ip_address(fields.get("ip", "")))
    if fields.get("warp") not in ("on", "plus"):
        raise ValueError("request bypassed WARP")
    return ip


def parse_google_region(body):
    # YouTube's GL is an unauthenticated regional-content signal, not an official
    # GeoIP API and not a guarantee about the Gemini account/API.
    matches = set(re.findall(r'"GL"\s*:\s*"([A-Z]{2})"', body.replace('\\"', '"')))
    return next(iter(matches)) if len(matches) == 1 else None


@dataclasses.dataclass
class Observation:
    result: str  # GOOD, BAD, UNKNOWN, NETWORK
    ip: str = ""
    google: str = ""
    gemini: str = ""
    reason: str = ""


@dataclasses.dataclass
class Node:
    name: str
    container: str
    port: int
    state: str = "STARTING"
    ip: str = ""
    google: str = ""
    gemini: str = ""
    reason: str = ""
    checked: float = 0
    good_at: float = 0
    model_at: float = 0
    model_ip: str = ""
    failures: int = 0
    network_failures: int = 0
    rotations: int = 0
    attempts: list = dataclasses.field(default_factory=list)
    bad_ips: dict = dataclasses.field(default_factory=dict)
    rejection_key: tuple = ()
    next_repair_at: float = 0
    repair_streak: int = 0
    speed_kib_s: float | None = None
    speed_at: float = 0
    slow_samples: int = 0
    requested_method: str = ""
    wake: asyncio.Event = dataclasses.field(default_factory=asyncio.Event, repr=False)
    start_deadline: float = 0
    phase: str = "STARTING"
    pending: int = 0
    enabled: bool = True
    generation: int = 0


class Pool:
    def __init__(self, config):
        self.config = config
        self.nodes = [Node(**n) for n in config["nodes"]]
        self.active = None
        self.repair_lock = asyncio.Lock()
        self.speed_lock = asyncio.Lock()
        self.connections = {n.name: set() for n in self.nodes}
        self.started = time.time()
        for node in self.nodes:
            node.start_deadline = time.monotonic() + config["wait_timeout"]
        self.capacity = None
        if config['target_gbps']:
            from warp_capacity import Capacity
            self.capacity = Capacity(self)

    def save_runtime(self, path):
        wall, mono = time.time(), time.monotonic()
        nodes = {}
        for n in self.nodes:
            nodes[n.name] = {
                "container": n.container, "rotations": n.rotations,
                "attempts": [wall + t - mono for t in n.attempts if mono - t < 3600],
                "bad_ips": {ip: wall + end - mono for ip, end in n.bad_ips.items() if end > mono},
                "next_repair_at": wall + n.next_repair_at - mono,
                "repair_streak": n.repair_streak,
            }
        atomic_json(path, {"version": 1, "policy": self.policy_fingerprint(), "nodes": nodes,
                           "capacity": self.capacity.persist() if self.capacity else None})

    def policy_fingerprint(self):
        policy = {k: self.config[k] for k in ('deny_regions', 'allow_regions', 'gemini_check', 'youtube_mode', 'impersonate_dir', 'min_speed_kib')}
        return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()

    def load_runtime(self, path):
        if not path.exists():
            return
        data = json.loads(path.read_text())
        if data.get("version") != 1:
            raise ValueError("unsupported runtime state version")
        wall, mono = time.time(), time.monotonic()
        if self.capacity:
            self.capacity.restore(data.get('capacity'))
        for n in self.nodes:
            row = data.get("nodes", {}).get(n.name, {})
            if row.get("container") != n.container:
                continue
            n.attempts = [mono + t - wall for t in row.get("attempts", []) if wall - 3600 < t <= wall]
            if data.get('policy', self.policy_fingerprint()) == self.policy_fingerprint():
                n.bad_ips = {ip: mono + min(end - wall, self.config["bad_ip_ttl"])
                             for ip, end in row.get("bad_ips", {}).items() if end > wall}
            n.next_repair_at = mono + max(0, min(row.get("next_repair_at", 0) - wall, 3600))
            n.rotations = max(0, int(row.get("rotations", 0)))
            n.repair_streak = max(0, min(int(row.get("repair_streak", 0)), 20))

    async def speed(self, node):
        async with self.speed_lock:
            return await self._speed(node)

    async def _speed(self, node):
        size = self.config["probe_mb"] * 1024 * 1024
        url = f"https://speed.cloudflare.com/__down?bytes={size}" if size else "https://www.youtube.com/"
        text = await command(
            "curl", "-q", "-4", "-fsS", "--noproxy", "", "--proxy",
            f"socks5h://127.0.0.1:{node.port}", "--connect-timeout", "7",
            "--max-time", "30", "--max-filesize", str(size or 8000000), "-o", "/dev/null",
            "-w", "%{json}", url, timeout=35)
        data = json.loads(text)
        elapsed = float(data["time_total"]) - float(data["time_starttransfer"])
        downloaded = int(data["size_download"])
        if (size and downloaded != size) or downloaded < 50000 or elapsed <= 0:
            raise ValueError("incomplete speed sample")
        return downloaded / elapsed / 1024

    def eligible(self, node):
        return (node.enabled and node.state == "READY" and
                time.monotonic() - node.good_at < self.config["health_ttl"])

    def quarantine(self, node, reason, close=False):
        # The observation is still running: preserve its generation so the
        # worker can commit its final result after verification finishes.
        if not node.enabled:
            return
        node.state, node.reason, node.gemini = 'UNKNOWN', reason, 'UNKNOWN'
        node.model_at = 0
        if self.capacity:
            self.capacity.invalidate(node)
        self.choose()
        if close:
            for writer in tuple(self.connections[node.name]):
                writer.close()

    def choose(self, route=False):
        if self.capacity:
            self.active = self.capacity.select(route)
            return self.active
        # Keep the working exit; there is no automatic failback or per-request
        # round robin between Google identities.
        if self.config['selection_strategy'] == 'failover' and self.active and self.eligible(self.active):
            return self.active
        previous = self.active
        ready = [n for n in self.nodes if self.eligible(n)]
        # A different exit is a better reserve than a second registration on the
        # same public IP. Independence of IPs is still not guaranteed.
        ready.sort(key=lambda n: (bool(previous and n.ip == previous.ip),
                                  self.nodes.index(n)))
        if self.config['selection_strategy'] == 'least_connections':
            ready.sort(key=lambda n: len(self.connections[n.name]) // 2 + n.pending)
        self.active = ready[0] if ready else None
        if previous is not self.active and self.config["selection_strategy"] == "failover":
            LOG.warning("active: %s -> %s", previous.name if previous else "none",
                        self.active.name if self.active else "none")
        return self.active

    async def fetch(self, node, url):
        return await command("curl", "-q", "-4", "-fsS", "--noproxy", "",
                             "--proxy", f"socks5h://127.0.0.1:{node.port}",
                             "--connect-timeout", "7", "--max-time", "25",
                             "--max-filesize", "8000000", "-H",
                             "Accept-Language: en-US,en;q=0.9", url, timeout=30)

    async def trace(self, node):
        return parse_trace(await self.fetch(node, "https://www.cloudflare.com/cdn-cgi/trace"))

    async def observe(self, node):
        node.phase = "WARP"
        try:
            ip = await self.trace(node)
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
            return Observation("NETWORK", reason="WARP transport not verified")
        now = time.monotonic()
        node.bad_ips = {k: v for k, v in node.bad_ips.items() if v > now}
        if ip in node.bad_ips:
            return Observation("BAD", ip=ip, reason="recently rejected exit returned")
        if node.ip and node.ip != ip:
            self.quarantine(node, 'exit changed; rechecking Google/Gemini')
            node.model_at, node.model_ip = 0, ''
            node.speed_at, node.speed_kib_s, node.slow_samples = 0, None, 0
        try:
            node.phase = "GOOGLE"
            geo = parse_google_region(await self.fetch(node, "https://www.youtube.com/"))
        except (OSError, RuntimeError, asyncio.TimeoutError):
            return Observation("UNKNOWN", ip=ip, reason="Google region check unavailable")
        result = Observation("UNKNOWN", ip=ip, google=geo or "UNKNOWN")
        model_checked = False
        if not geo:
            result.reason = "Google GL missing or ambiguous"
        elif self.config["youtube_mode"] and geo != "RU":
            result.result, result.reason = "BAD", f"YouTube requires Google RU; got {geo}"
        elif not self.config["youtube_mode"] and (geo in self.config["deny_regions"] or
                (self.config["allow_regions"] and geo not in self.config["allow_regions"])):
            result.result, result.reason = "BAD", f"Google region {geo} rejected"
        else:
            needs_model = self.config["gemini_check"] and (node.model_ip != ip or not node.model_at or
                           now - node.model_at >= self.config["gemini_interval"])
            if needs_model:
                try:
                    node.phase = "GEMINI"
                    args = [sys.executable, ROOT / "gemini_probe.py",
                            "--host", "127.0.0.1", "--port", node.port]
                    if self.config["impersonate_dir"]:
                        args += ["--impersonate-dir", self.config["impersonate_dir"]]
                    text = await command(*args,
                                         timeout=160, accepted_codes=(0, 1, 2, 3))
                    verdict = text.split(":", 1)[0].strip()
                    result.gemini = verdict if verdict in ("SERVING", "REFUSED", "CAPTCHA") else "INCONCLUSIVE"
                except (OSError, RuntimeError):
                    result.gemini = "INCONCLUSIVE"
                except asyncio.TimeoutError:
                    result.gemini = "INCONCLUSIVE"
                model_checked = True
            else:
                result.gemini = node.gemini if self.config["gemini_check"] else "DISABLED"
            if result.gemini in ("SERVING", "DISABLED"):
                result.result, result.reason = "GOOD", "Google and anonymous Gemini verified"
                if result.gemini == "DISABLED":
                    result.reason = "Google verified; Gemini check disabled by configuration"
            elif result.gemini in ("REFUSED", "CAPTCHA"):
                result.result, result.reason = "BAD", f"Gemini {result.gemini}"
            else:
                result.reason = "Gemini verdict inconclusive"
        if result.result == "GOOD" and self.config["min_speed_kib"] > 0 and not self.capacity:
            if not node.speed_at or time.monotonic() - node.speed_at >= self.config["speed_interval"]:
                try:
                    node.phase = "SPEED"
                    node.speed_kib_s = await self.speed(node)
                    node.speed_at = time.monotonic()
                    node.slow_samples = node.slow_samples + 1 if node.speed_kib_s < self.config["min_speed_kib"] else 0
                except (OSError, RuntimeError, ValueError, KeyError, asyncio.TimeoutError):
                    node.speed_kib_s = None
                    node.speed_at = time.monotonic()
                    node.slow_samples = 0
                    result.reason += "; speed probe inconclusive"
            if node.slow_samples >= self.config["slow_threshold"]:
                result.result, result.reason = "BAD", "consecutive slow speed samples"
        if result.result != 'GOOD':
            self.quarantine(node, result.reason, close=result.result == 'BAD')
        try:
            node.phase = "VERIFY"
            if await self.trace(node) != ip:
                return Observation("UNKNOWN", reason="exit changed during verification")
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
            return Observation("NETWORK", reason="WARP lost during verification")
        if model_checked and result.gemini == "SERVING":
            node.model_at, node.model_ip = time.monotonic(), ip
        return result

    def apply(self, node, observation):
        if not node.enabled:
            return
        old_state, old_ip = node.state, node.ip
        if observation.result != 'GOOD' or old_ip != observation.ip:
            node.generation += 1
            if self.capacity:
                self.capacity.invalidate(node)
        node.ip, node.google = observation.ip, observation.google
        node.gemini, node.reason = observation.gemini, observation.reason
        node.checked = time.time()
        if observation.result == "GOOD":
            node.state, node.good_at = "READY", time.monotonic()
            node.failures = node.network_failures = 0
            node.rejection_key = ()
            node.repair_streak = 0
            node.next_repair_at = 0
        else:
            node.state = observation.result
            if observation.result == "BAD":
                key = (observation.ip, observation.reason)
                node.failures = node.failures + 1 if key == node.rejection_key else 1
                node.rejection_key = key
                if observation.reason == "consecutive slow speed samples":
                    node.failures = self.config["bad_threshold"]
                if observation.reason == "recently rejected exit returned":
                    node.failures = self.config["bad_threshold"]
                if observation.ip and node.failures >= self.config["bad_threshold"]:
                    node.bad_ips.setdefault(observation.ip, time.monotonic() + self.config["bad_ip_ttl"])
            else:
                node.failures = 0
            node.network_failures = node.network_failures + 1 if observation.result == "NETWORK" else 0
            # A later GOOD must establish a fresh model result after uncertainty.
            node.model_at = 0
            # Stop traffic on an explicitly rejected exit; transport uncertainty
            # also makes the old stream unusable. Healthy failover never resets
            # unrelated node connections.
            if observation.result in ("BAD", "NETWORK"):
                for writer in tuple(self.connections[node.name]):
                    writer.close()
        self.choose()
        if old_state != node.state or old_ip != node.ip:
            LOG.info("%s %s ip=%s Google=%s Gemini=%s: %s", node.name,
                     node.state, node.ip, node.google, node.gemini, node.reason)

    async def repair(self, node, method):
        async with self.repair_lock:
            if not node.enabled:
                return False
            # No await between quarantine and selection: new traffic cannot race
            # into the registration that is about to change.
            node.state = "ROLLING"
            node.generation += 1
            if self.capacity:
                self.capacity.invalidate(node)
            node.phase = "ROTATE"
            self.choose()
            if self.active is node:
                raise RuntimeError("refusing to rotate active tunnel")
            now = time.monotonic()
            if now < node.next_repair_at:
                node.state, node.reason = "COOLDOWN", "repair backoff"
                return False
            node.attempts = [t for t in node.attempts if now - t < 3600]
            if len(node.attempts) >= self.config["max_rotations_per_hour"]:
                node.state, node.reason = "COOLDOWN", "hourly repair limit"
                node.next_repair_at = min(node.attempts) + 3600
                return False
            node.attempts.append(now)
            node.rotations += 1
            node.repair_streak += 1
            delay = min(self.config["rotation_delay"] * 2 ** min(node.repair_streak - 1, 20), self.config["max_backoff"])
            node.next_repair_at = now + delay
            node.model_at, node.model_ip = 0, ""
            node.speed_at, node.speed_kib_s, node.slow_samples = 0, None, 0
            if getattr(self, "runtime_path", None):
                self.save_runtime(self.runtime_path)
            for writer in tuple(self.connections[node.name]):
                writer.close()
            try:
                if self.capacity and self.capacity.root:
                    await command('docker', 'compose', '-f', self.capacity.root / 'compose.yaml',
                                  'up', '-d', '--no-build', '--no-recreate', node.name, timeout=120)
                await command("docker", "exec", node.container,
                              "/app/worker.sh", "rotate", method, timeout=120)
                node.state = "STARTING"
                node.start_deadline = time.monotonic() + self.config["wait_timeout"]
                return True
            except (OSError, RuntimeError, asyncio.TimeoutError):
                node.state, node.reason = "NETWORK", "rotation command failed"
                LOG.warning("%s rotation failed; backing off", node.name)
                return False

    async def worker(self, node):
        while True:
            node.wake.clear()
            if not node.enabled:
                await node.wake.wait()
                continue
            repaired = False
            if node.requested_method:
                method, node.requested_method = node.requested_method, ""
                repaired = await self.repair(node, method)
            generation = node.generation
            observation = await self.observe(node)
            if not node.enabled or generation != node.generation:
                continue
            self.apply(node, observation)
            search_needed = not self.capacity or self.capacity.needs_search()
            if observation.result == "BAD" and node.failures >= self.config["bad_threshold"] and search_needed:
                # Confirmed RU/CAPTCHA/region refusal triggers repair of this
                # node only. Cached rejects do not issue new model requests.
                repaired = await self.repair(node, self.config["rotation_method"])
            elif (observation.result == "NETWORK" and node.network_failures >= self.config["fail_threshold"]
                  and time.monotonic() >= node.start_deadline):
                repaired = await self.repair(node, "reconnect")
            delay = self.config["check_interval"]
            if repaired:
                # Backoff limits the next mutation, not verification of a new exit.
                delay = self.config["confirmation_delay"]
            elif observation.result in ("BAD", "NETWORK"):
                remaining = node.next_repair_at - time.monotonic()
                delay = min(delay, max(self.config["confirmation_delay"], remaining))
            try:
                node.phase = "WAIT"
                await asyncio.wait_for(node.wake.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def relay(self, reader, writer):
        backend = None
        tracked = []
        node = self.choose(route=True)
        if not node:
            writer.close()
            await writer.wait_closed()
            return
        node.pending += 1
        pending = True
        try:
            upstream, backend = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", node.port), timeout=5)
            # The backend could become quarantined while connect() was pending.
            if not self.eligible(node):
                return
            tracked = [writer, backend]
            self.connections[node.name].update(tracked)
            node.pending -= 1; pending = False
            await bridge(reader, writer, upstream, backend)
        except (OSError, asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            if pending:
                node.pending -= 1
            for item in tracked:
                self.connections[node.name].discard(item)
            for item in (writer, backend):
                if item:
                    item.close()
                    with contextlib.suppress(OSError, ConnectionError):
                        await item.wait_closed()

    def snapshot(self):
        self.choose()
        rows = []
        for node in self.nodes:
            row = {k: getattr(node, k) for k in ("name", "state", "ip", "google",
                   "gemini", "reason", "checked", "rotations")}
            row["active"] = node is self.active
            row["connections"] = len(self.connections[node.name]) // 2
            row["speed_kib_s"] = node.speed_kib_s
            row["slow_samples"] = node.slow_samples
            row["retry_in"] = max(0, round(node.next_repair_at - time.monotonic()))
            row["phase"] = node.phase
            row['role'] = self.capacity.role(node) if self.capacity else ('ACTIVE' if node is self.active else 'RESERVE')
            row['enabled'] = node.enabled
            rows.append(row)
        ips = [n.ip for n in self.nodes if self.eligible(n)]
        youtube = "RU VERIFIED" if ips else ("SEARCHING" if any(n.state in ("BAD", "ROLLING", "COOLDOWN") for n in self.nodes) else "UNKNOWN")
        return {"version": VERSION, "instance": self.config["instance"],
                "youtube": youtube if self.config["youtube_mode"] else "DISABLED",
                "strategy": 'weighted_capacity' if self.capacity else self.config['selection_strategy'],
                "pid": os.getpid(), "boot_id": boot_id(),
                "uptime": round(time.time() - self.started),
                "active": self.active.name if self.active else None,
                "ready_tunnels": len(ips), "distinct_ready_ips": len(set(ips)),
                "capacity": self.capacity.snapshot() if self.capacity else None,
                "nodes": rows, "updated": time.time()}

    async def publish(self, path):
        while True:
            atomic_json(path, self.snapshot())
            if getattr(self, "runtime_path", None):
                self.save_runtime(self.runtime_path)
            await asyncio.sleep(2)

    async def controls(self, directory):
        directory.mkdir(mode=0o700, exist_ok=True)
        while True:
            for path in sorted(directory.glob("*.json")):
                try:
                    request = json.loads(path.read_text())
                    nodes = [n for n in self.nodes if n.name == request.get("node") or request.get("node") == "all"]
                    action = request.get("action")
                    if action not in ("check", "speed", "rotate") or not nodes:
                        raise ValueError("invalid control request")
                    if action == "rotate" and len(nodes) != 1:
                        raise ValueError("rotate one node at a time")
                    for node in nodes:
                        if not node.enabled:
                            continue
                        if request.get('full'):
                            node.model_at = 0
                        if action == "rotate":
                            method = request.get("method", self.config["rotation_method"])
                            if method not in ("registration", "keys", "reconnect"):
                                raise ValueError("invalid rotation method")
                            node.requested_method = method
                        if action == "speed":
                            if self.capacity:
                                self.capacity.force_nodes.add(node.name)
                                self.capacity.retry_at = 0
                                node.wake.set()
                                continue
                            try:
                                node.speed_kib_s = await self.speed(node)
                                node.speed_at = time.monotonic()
                            except (OSError, RuntimeError, ValueError, KeyError, asyncio.TimeoutError):
                                node.speed_kib_s = None
                        node.wake.set()
                    LOG.info("control %s accepted for %s", action, request.get("node"))
                except (ValueError, OSError):
                    LOG.warning("invalid control request %s", path.name)
                finally:
                    path.unlink(missing_ok=True)
            await asyncio.sleep(1)


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as f:
        os.chmod(temporary, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    temporary.replace(path)


def boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return ''


def invalidate_status(root, config):
    path = root / 'state.json'
    if path.is_file():
        with contextlib.suppress(ValueError, OSError):
            previous = json.loads(path.read_text())
            if previous.get('nodes'):
                atomic_json(root / 'previous-state.json', previous)
    atomic_json(path, {'version': VERSION, 'instance': config['instance'],
                      'updated': 0, 'pid': 0, 'boot_id': boot_id(), 'nodes': [],
                      'active': None, 'ready_tunnels': 0, 'distinct_ready_ips': 0})


async def bridge(reader, writer, upstream, backend):
    async def pump(source, destination):
        while True:
            data = await source.read(65536)
            if not data:
                if destination.can_write_eof():
                    destination.write_eof()
                    await destination.drain()
                return
            destination.write(data)
            await destination.drain()
    tasks = [asyncio.create_task(pump(reader, backend)),
             asyncio.create_task(pump(upstream, writer))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def read_config(path):
    return validate_config(json.loads(path.read_text()))


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    defaults = dict(check_interval=60, gemini_interval=600, health_ttl=360,
                    rotation_delay=20, max_backoff=900, bad_ip_ttl=3600,
                    max_rotations_per_hour=12, rotation_method="registration",
                    deny_regions=["RU"], listen_port=40010,
                    bad_threshold=2, fail_threshold=3, confirmation_delay=5,
                    min_speed_kib=0, probe_mb=4, slow_threshold=2,
                    speed_interval=900, impersonate_dir="", gemini_check=True,
                    instance=1, listen_host="127.0.0.1", http_port=None,
                    youtube_mode=False, allow_regions=[], wait_timeout=75,
                    menu_mode="easy", connection_type="xray", selection_strategy="failover",
                    target_gbps=0, capacity_seconds=6, capacity_interval=900,
                    capacity_sample_ttl=2400, capacity_parallel_search=2,
                    capacity_url="https://speed.cloudflare.com/__down?bytes=8000000")
    config = {**defaults, **config}
    if not isinstance(config.get("nodes"), list) or not 1 <= len(config["nodes"]) <= 128:
        raise ValueError("require 1–128 nodes")
    for n in config["nodes"]:
        if not isinstance(n, dict) or set(n) != {"name", "container", "port"}:
            raise ValueError("node requires only name, container, port")
        for key in ("name", "container"):
            if not isinstance(n[key], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", n[key]):
                raise ValueError(f"invalid node {key}")
    for key in ("name", "container", "port"):
        if len({n[key] for n in config["nodes"]}) != len(config["nodes"]):
            raise ValueError(f"node {key} must be unique")
    if config["rotation_method"] not in ("keys", "registration", "reconnect"):
        raise ValueError("invalid rotation method")
    for key in ("check_interval", "gemini_interval", "health_ttl", "rotation_delay",
                "max_backoff", "bad_ip_ttl", "speed_interval", "confirmation_delay"):
        if isinstance(config[key], bool) or not isinstance(config[key], (float, int)) or not math.isfinite(config[key]) or not 0 < config[key] <= 86400:
            raise ValueError(f"invalid {key}")
    for key, low, high in (("bad_threshold", 2, 20), ("fail_threshold", 2, 20),
                           ("slow_threshold", 2, 20), ("max_rotations_per_hour", 1, 120),
                           ("probe_mb", 0, 10), ("min_speed_kib", 0, 2147483647),
                           ("instance", 1, 99), ("wait_timeout", 20, 300)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"invalid {key}")
    for key, low, high in [('target_gbps', 0, 100), ('capacity_seconds', 2, 30),
                           ('capacity_interval', 60, 86400), ('capacity_sample_ttl', 120, 172800),
                           ('capacity_parallel_search', 1, 8)]:
        v = config[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f'invalid {key}')
    if type(config['capacity_parallel_search']) is not int:
        raise ValueError('capacity_parallel_search must be integer')
    if config['capacity_sample_ttl'] < config['capacity_interval'] * 2:
        raise ValueError('capacity_sample_ttl must be at least twice capacity_interval')
    from urllib.parse import urlsplit
    url = urlsplit(config['capacity_url'])
    if url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None or url.fragment:
        raise ValueError('capacity_url must be an HTTPS file URL without credentials')
    for key in ("deny_regions", "allow_regions"):
        if not isinstance(config[key], list) or any(not isinstance(c, str) or not re.fullmatch("[A-Z]{2}", c) for c in config[key]):
            raise ValueError(f"{key} must be two-letter country codes")
    if type(config["youtube_mode"]) is not bool or (config["youtube_mode"] and config["gemini_check"]):
        raise ValueError("YouTube and Gemini require independent instances")
    address = ipaddress.IPv4Address(config["listen_host"])
    private = any(address in ipaddress.IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    if not (address.is_loopback or private):
        raise ValueError("listen_host must be loopback or a private IPv4")
    if config["youtube_mode"] and not address.is_loopback:
        raise ValueError("YouTube instance requires loopback")
    if config["menu_mode"] not in ("easy", "advanced") or config["connection_type"] not in ("xray", "socks5"):
        raise ValueError("invalid menu or connection mode")
    if config['selection_strategy'] not in ('failover', 'least_connections'):
        raise ValueError('invalid selection strategy')
    if not isinstance(config["impersonate_dir"], str):
        raise ValueError("invalid impersonate_dir")
    if type(config["gemini_check"]) is not bool:
        raise ValueError("gemini_check must be boolean")
    if config["max_backoff"] < config["rotation_delay"]:
        raise ValueError("max_backoff must be at least rotation_delay")
    if config['check_interval'] < 60 or config['rotation_delay'] < 2:
        raise ValueError('check_interval >= 60 seconds; rotation_delay >= 2 seconds')
    if config["health_ttl"] < config["check_interval"] + 300:
        raise ValueError("health_ttl must exceed check_interval by at least 300 seconds")
    ports = [n["port"] for n in config["nodes"]] + [config["listen_port"]]
    if config["http_port"] is not None:
        ports.append(config["http_port"])
    if any(type(p) is not int or not 1024 <= p <= 65534 for p in ports):
        raise ValueError("invalid port")
    if len(set(ports)) != len(ports):
        raise ValueError("front port overlaps a backend")
    return config


async def serve(config, state):
    pool = Pool(config)
    if state.is_file():
        with contextlib.suppress(ValueError, OSError):
            previous = json.loads(state.read_text())
            if previous.get('nodes'):
                atomic_json(state.with_name('previous-state.json'), previous)
    pool.runtime_path = state.with_name("runtime.json")
    pool.load_runtime(pool.runtime_path)
    server = await asyncio.start_server(pool.relay, config["listen_host"], config["listen_port"])
    http_server = None
    if config["http_port"] is not None:
        from http_front import relay_http
        try:
            http_server = await asyncio.start_server(lambda r, w: relay_http(pool, r, w), config["listen_host"], config["http_port"])
        except BaseException:
            server.close(); await server.wait_closed()
            raise
    LOG.info("SOCKS TCP front: %s:%s; waiting for verified exits", config["listen_host"], config["listen_port"])
    tasks = [asyncio.create_task(pool.worker(n)) for n in pool.nodes]
    tasks.append(asyncio.create_task(pool.publish(state)))
    tasks.append(asyncio.create_task(pool.controls(state.with_name("requests"))))
    if pool.capacity:
        tasks.append(asyncio.create_task(pool.capacity.run(state.parent)))
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, main_task.cancel)
    try:
        async with server:
            await asyncio.gather(server.serve_forever(), *tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if http_server:
            http_server.close(); await http_server.wait_closed()
        pool.save_runtime(pool.runtime_path)
        for writers in pool.connections.values():
            for writer in tuple(writers):
                writer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "status"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--state", type=Path, default=ROOT / "state.json")
    args = parser.parse_args()
    if args.command == "status":
        data = json.loads(args.state.read_text())
        data["stale"] = time.time() - data["updated"] > 10
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = read_config(args.config)
    with (ROOT / ".manager.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            asyncio.run(serve(config, args.state))
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass


if __name__ == "__main__":
    main()
