import asyncio
import contextlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from http_front import relay_http, parse_request
from warp_pool import Pool, Observation
import warp_profiles


class HTTP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = Pool(warp_profiles.defaults(1, count=3))
        self.writers = []; self.servers = []; self.handlers = set(); self.destinations = []; self.headers = []
        self.plain = False
        async def socks(reader, writer):
            task = asyncio.current_task(); self.handlers.add(task); self.writers.append(writer)
            try:
                self.assertEqual(await reader.readexactly(3), b'\x05\x01\x00')
                writer.write(b'\x05\x00'); await writer.drain()
                head = await reader.readexactly(4)
                self.assertEqual(head[:3], b'\x05\x01\x00')
                size = (await reader.readexactly(1))[0] if head[3] == 3 else (4 if head[3] == 1 else 16)
                host = await reader.readexactly(size)
                port = int.from_bytes(await reader.readexactly(2), 'big')
                self.destinations.append((host, port))
                writer.write(b'\x05\x00\x00\x01' + b'\x00' * 6); await writer.drain()
                if self.plain:
                    self.headers.append(await reader.readuntil(b'\r\n\r\n'))
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK'); await writer.drain()
                else:
                    while data := await reader.read(65536):
                        writer.write(data); await writer.drain()
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
                self.handlers.discard(task)
        for node in self.pool.nodes:
            backend = await asyncio.start_server(socks, '127.0.0.1', 0)
            self.servers.append(backend); node.port = backend.sockets[0].getsockname()[1]
        front = await asyncio.start_server(lambda r, w: relay_http(self.pool, r, w), '127.0.0.1', 0)
        self.servers.append(front); self.port = front.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
        for server in self.servers:
            server.close(); await server.wait_closed()
        for writer in self.writers:
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        if self.handlers:
            await asyncio.wait_for(asyncio.gather(*list(self.handlers), return_exceptions=True), 2)

    def ready(self, node):
        self.pool.apply(node, Observation('GOOD', ip='1.1.1.1', google='US', gemini='SERVING'))

    async def client(self, header):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        self.writers.append(writer)
        writer.write(header); await writer.drain()
        return reader, writer

    async def test_connect_uses_remote_dns_and_closes_on_rejection(self):
        node = self.pool.nodes[0]; self.ready(node)
        reader, writer = await self.client(b'CONNECT gemini.test:443 HTTP/1.1\r\nHost: gemini.test:443\r\n\r\n')
        self.assertTrue((await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)).startswith(b'HTTP/1.1 200'))
        writer.write(b'payload'); await writer.drain()
        self.assertEqual(await reader.readexactly(7), b'payload')
        self.assertEqual(self.destinations, [(b'gemini.test', 443)])
        self.pool.apply(node, Observation('BAD', ip='1.1.1.1', google='RU', reason='rejected'))
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')

    async def test_no_verified_exit_returns_503_without_opening_backend(self):
        reader, writer = await self.client(b'CONNECT example.test:443 HTTP/1.1\r\n\r\n')
        self.assertTrue((await asyncio.wait_for(reader.read(), 1)).startswith(b'HTTP/1.1 503'))
        self.assertFalse(self.destinations)

    async def test_plain_http_strips_proxy_credentials_and_rewrites_absolute_url(self):
        self.plain = True; self.ready(self.pool.nodes[0])
        reader, writer = await self.client(b'GET http://example.test/path?q=1 HTTP/1.1\r\nHost: other.test\r\nProxy-Authorization: secret\r\n\r\n')
        response = await asyncio.wait_for(reader.read(), 1)
        self.assertIn(b'200 OK', response)
        self.assertTrue(response.endswith(b'OK'))
        self.assertTrue(self.headers[0].startswith(b'GET /path?q=1 HTTP/1.1'))
        self.assertIn(b'Host: example.test', self.headers[0])
        self.assertNotIn(b'secret', self.headers[0])

    async def test_malformed_request_is_400(self):
        reader, writer = await self.client(b'GET /relative HTTP/1.1\r\n\r\n')
        self.assertTrue((await asyncio.wait_for(reader.read(), 1)).startswith(b'HTTP/1.1 400'))

    async def test_eight_connections_are_distributed_when_requested(self):
        self.pool.config['selection_strategy'] = 'least_connections'
        for node in self.pool.nodes:
            self.ready(node)
        async def connect():
            reader, writer = await self.client(b'CONNECT example.test:443 HTTP/1.1\r\n\r\n')
            self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 200'))
        await asyncio.wait_for(asyncio.gather(*(connect() for _ in range(8))), 2)
        counts = [len(v) // 2 for v in self.pool.connections.values()]
        self.assertEqual(sum(counts), 8)
        self.assertEqual(max(counts) - min(counts), 1)


if __name__ == '__main__':
    unittest.main()
