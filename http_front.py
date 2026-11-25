"""HTTP/1 proxy via an eligible WARP SOCKS backend. SPDX-License-Identifier: GPL-3.0-only."""
import asyncio
import contextlib
import ipaddress
from urllib.parse import urlsplit


async def socks_connect(reader, writer, host, port):
    writer.write(b'\x05\x01\x00'); await writer.drain()
    if await reader.readexactly(2) != b'\x05\x00':
        raise OSError('SOCKS authentication rejected')
    try:
        ip = ipaddress.ip_address(host)
        address = bytes([1 if ip.version == 4 else 4]) + ip.packed
    except ValueError:
        name = host.encode('idna')
        if not 1 <= len(name) <= 255:
            raise ValueError('invalid destination')
        address = b'\x03' + bytes([len(name)]) + name
    writer.write(b'\x05\x01\x00' + address + port.to_bytes(2, 'big')); await writer.drain()
    version, reply, reserved, kind = await reader.readexactly(4)
    if version != 5 or reply != 0 or reserved != 0:
        raise OSError('SOCKS destination refused')
    if kind == 1:
        await reader.readexactly(4)
    elif kind == 4:
        await reader.readexactly(16)
    elif kind == 3:
        await reader.readexactly((await reader.readexactly(1))[0])
    else:
        raise OSError('invalid SOCKS address')
    await reader.readexactly(2)


def parse_request(header):
    lines = header.decode('latin-1').split('\r\n')
    method, target, version = lines[0].split(' ')
    if version not in ('HTTP/1.0', 'HTTP/1.1') or not method.isalpha():
        raise ValueError('invalid request line')
    connect = method.upper() == 'CONNECT'
    url = urlsplit('//' + target if connect else target)
    if not connect and url.scheme != 'http':
        raise ValueError('absolute HTTP URL required')
    if not url.hostname or url.username is not None or url.password is not None:
        raise ValueError('invalid host')
    port = url.port or (443 if connect else 80)
    if not 1 <= port <= 65535 or (connect and url.path):
        raise ValueError('invalid destination port')
    if connect:
        return url.hostname, port, None
    path = url.path or '/'
    if url.query:
        path += '?' + url.query
    outgoing = [f'{method} {path} {version}']
    for line in lines[1:]:
        if not line:
            continue
        name, value = line.split(':', 1)
        if name.lower() not in ('proxy-connection', 'proxy-authorization', 'host', 'connection'):
            outgoing.append(line)
    outgoing += ['Host: ' + url.netloc, 'Connection: close', '', '']
    return url.hostname, port, '\r\n'.join(outgoing).encode('latin-1')


async def relay_http(pool, reader, writer):
    from warp_pool import bridge
    backend = None
    node = None
    tracked = False
    pending = False
    response_started = False
    try:
        header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
        host, port, request = parse_request(header)
        node = pool.choose(route=True)
        if node is None:
            writer.write(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
            return
        node.pending += 1; pending = True
        upstream, backend = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', node.port), 5)
        if not pool.eligible(node):
            raise OSError('exit quarantined')
        pool.connections[node.name].update((writer, backend)); tracked = True
        node.pending -= 1; pending = False
        await asyncio.wait_for(socks_connect(upstream, backend, host, port), 20)
        if not pool.eligible(node):
            raise OSError('exit quarantined')
        if request is None:
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n'); await writer.drain()
        else:
            backend.write(request); await backend.drain()
        response_started = True
        await bridge(reader, writer, upstream, backend)
    except (ValueError, UnicodeError, asyncio.LimitOverrunError):
        if not response_started:
            writer.write(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        if not response_started:
            with contextlib.suppress(OSError):
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
    finally:
        if pending:
            node.pending -= 1
        if tracked:
            pool.connections[node.name].difference_update((writer, backend))
        for item in (writer, backend):
            if item:
                item.close()
                with contextlib.suppress(OSError):
                    await item.wait_closed()
