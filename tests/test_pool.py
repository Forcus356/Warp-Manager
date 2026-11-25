import asyncio
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warp_pool import Pool, Observation, command, parse_trace, parse_google_region, read_config


def pool():
    return Pool(read_config(Path(__file__).resolve().parents[1] / 'config.json'))


def ready(node, ip):
    node.state, node.ip, node.good_at = 'READY', ip, time.monotonic()
    node.gemini = 'SERVING'
    node.model_ip, node.model_at = ip, time.monotonic()


class Parsing(unittest.TestCase):
    def test_direct_and_invalid_trace_are_rejected(self):
        for body in ('ip=1.1.1.1\nwarp=off', 'warp=on', 'ip=not-an-ip\nwarp=on'):
            with self.assertRaises(ValueError):
                parse_trace(body)
        self.assertEqual(parse_trace('ip=1.1.1.1\nwarp=plus\nloc=DE'), '1.1.1.1')

    def test_google_region_is_not_cloudflare_loc(self):
        self.assertIsNone(parse_google_region('loc=DE'))
        self.assertEqual(parse_google_region('{"GL":"RU"}'), 'RU')
        self.assertEqual(parse_google_region('{\\"GL\\":\\"DE\\"}'), 'DE')
        self.assertIsNone(parse_google_region('{"GL":"DE"}{"GL":"RU"}'))

    def test_active_is_kept_and_distinct_backup_is_preferred(self):
        p = pool()
        a, b, c = p.nodes
        ready(a, '1.1.1.1'); ready(b, '1.1.1.1'); ready(c, '8.8.8.8')
        self.assertIs(p.choose(), a)
        self.assertEqual(p.snapshot()['distinct_ready_ips'], 2)
        a.state = 'BAD'
        self.assertIs(p.choose(), c)
        ready(a, '9.9.9.9')
        self.assertIs(p.choose(), c)  # no unnecessary failback

    def test_old_health_and_uncertain_nodes_are_not_selected(self):
        p = pool()
        ready(p.nodes[0], '1.1.1.1')
        p.nodes[0].good_at -= p.config['health_ttl'] + 1
        p.nodes[1].state = 'UNKNOWN'
        self.assertIsNone(p.choose())

    def test_cached_bad_ip_does_not_extend_its_own_ttl(self):
        p = pool(); n = p.nodes[0]
        obs = Observation('BAD', ip='1.1.1.1', google='RU')
        p.apply(n, obs)
        self.assertNotIn('1.1.1.1', n.bad_ips)
        p.apply(n, obs)
        expiry = n.bad_ips['1.1.1.1']
        p.apply(n, obs)
        self.assertEqual(expiry, n.bad_ips['1.1.1.1'])


class Logic(unittest.IsolatedAsyncioTestCase):
    async def test_google_ru_is_bad_even_if_transport_is_healthy(self):
        p = pool(); n = p.nodes[0]
        p.trace = AsyncMock(return_value='1.1.1.1')
        p.fetch = AsyncMock(return_value='{"GL":"RU"}')
        with patch('warp_pool.command', new_callable=AsyncMock) as cmd:
            observation = await p.observe(n)
        self.assertEqual(observation.result, 'BAD')
        cmd.assert_not_awaited()

    async def test_gemini_refusal_is_preserved_and_inconclusive_is_not_good(self):
        for verdict, expected in [('REFUSED', 'BAD'), ('CAPTCHA', 'BAD'),
                                  ('INCONCLUSIVE', 'UNKNOWN'), ('SERVING', 'GOOD')]:
            p = pool(); n = p.nodes[0]
            p.trace = AsyncMock(return_value='1.1.1.1')
            p.fetch = AsyncMock(return_value='{"GL":"DE"}')
            with patch('warp_pool.command', new=AsyncMock(return_value=verdict + ': detail')):
                self.assertEqual((await p.observe(n)).result, expected)

    async def test_exit_change_cannot_be_accepted(self):
        p = pool(); n = p.nodes[0]
        p.trace = AsyncMock(side_effect=['1.1.1.1', '8.8.8.8'])
        p.fetch = AsyncMock(return_value='{"GL":"DE"}')
        with patch('warp_pool.command', new=AsyncMock(return_value='SERVING: yes')):
            self.assertEqual((await p.observe(n)).result, 'UNKNOWN')
        self.assertEqual(n.model_at, 0)

    async def test_only_bad_container_is_rotated_after_failover(self):
        p = pool(); a, b, c = p.nodes
        ready(a, '1.1.1.1'); ready(b, '8.8.8.8'); ready(c, '9.9.9.9')
        p.choose()
        p.apply(a, Observation('BAD', ip=a.ip, google='RU'))
        self.assertIs(p.active, b)
        with patch('warp_pool.command', new_callable=AsyncMock) as cmd:
            await p.repair(a, 'registration')
        self.assertEqual(cmd.await_args.args[:4], ('docker', 'exec', a.container, '/app/worker.sh'))
        self.assertEqual(b.state, 'READY')
        self.assertEqual(c.state, 'READY')
        self.assertIs(p.active, b)

    async def test_limit_stops_re_registration(self):
        p = pool(); n = p.nodes[0]
        n.attempts = [time.monotonic()] * p.config['max_rotations_per_hour']
        with patch('warp_pool.command', new_callable=AsyncMock) as cmd:
            self.assertFalse(await p.repair(n, 'registration'))
        cmd.assert_not_awaited()
        self.assertEqual(n.state, 'COOLDOWN')

    async def test_command_preserves_nonzero_probe_verdict(self):
        text = await command(sys.executable, '-c',
                             'print("CAPTCHA: blocked"); raise SystemExit(3)',
                             accepted_codes=(0, 1, 2, 3))
        self.assertTrue(text.startswith('CAPTCHA:'))

    async def test_tcp_front_switches_new_streams_without_migrating_existing_streams(self):
        servers = []
        writers = []
        async def make_echo(label):
            async def handle(reader, writer):
                writers.append(writer)
                try:
                    while data := await reader.read(65536):
                        writer.write(label + data)
                        await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            s = await asyncio.start_server(handle, '127.0.0.1', 0)
            servers.append(s)
            return s.sockets[0].getsockname()[1]
        p = pool()
        p.nodes[0].port = await make_echo(b'A')
        p.nodes[1].port = await make_echo(b'B')
        ready(p.nodes[0], '1.1.1.1'); ready(p.nodes[1], '8.8.8.8')
        front = await asyncio.start_server(p.relay, '127.0.0.1', 0)
        servers.append(front)
        clients = []
        try:
            async def connect():
                r, w = await asyncio.open_connection('127.0.0.1', front.sockets[0].getsockname()[1])
                clients.append(w)
                return r, w
            r1, w1 = await connect()
            w1.write(b'\x05\x01\x00'); await w1.drain()
            self.assertEqual(await r1.readexactly(4), b'A\x05\x01\x00')
            # UNKNOWN switches new streams while allowing the old stream to finish.
            p.apply(p.nodes[0], Observation('UNKNOWN', reason='temporary uncertain response'))
            r2, w2 = await connect()
            w2.write(b'x'); await w2.drain()
            self.assertEqual(await r2.readexactly(2), b'Bx')
            w1.write(b'y'); await w1.drain()
            self.assertEqual(await r1.readexactly(2), b'Ay')
            # Explicit RU also shuts the rejected node's old stream.
            p.apply(p.nodes[0], Observation('BAD', ip='1.1.1.1', google='RU'))
            self.assertEqual(await asyncio.wait_for(r1.read(), 1), b'')
        finally:
            for w in clients + writers:
                w.close()
            for s in servers:
                s.close()
                await s.wait_closed()
            for w in clients + writers:
                await w.wait_closed()

    async def test_no_verified_exit_closes_front_without_direct_fallback(self):
        p = pool()
        s = await asyncio.start_server(p.relay, '127.0.0.1', 0)
        try:
            r, w = await asyncio.open_connection('127.0.0.1', s.sockets[0].getsockname()[1])
            self.assertEqual(await asyncio.wait_for(r.read(), 1), b'')
            w.close(); await w.wait_closed()
        finally:
            s.close(); await s.wait_closed()


if __name__ == '__main__':
    unittest.main()
