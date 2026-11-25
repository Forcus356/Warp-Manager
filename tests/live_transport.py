#!/usr/bin/env python3
"""Manual transport benchmark through a temporary loopback front, without Gemini certification.

Run on the test VPS with the manager stopped (containers running), then restart it.
This never edits the installed configuration or status and never publishes READY.
SPDX-License-Identifier: GPL-3.0-only
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warp_pool import Pool, command, read_config
import warp_profiles


async def main(args):
    path=args.config or warp_profiles.directory(args.instance)/'config.json'
    config=dict(read_config(path),target_gbps=0,gemini_check=False,youtube_mode=False,deny_regions=[],allow_regions=[])
    pool = Pool(config)
    node = pool.nodes[0]
    ip = await pool.trace(node)
    node.ip, node.state, node.good_at = ip, 'READY', time.monotonic()
    node.gemini='DISABLED'
    # Only this temporary diagnostic front accepts transport without a Gemini verdict.
    server = await asyncio.start_server(pool.relay, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]

    async def download(size):
        text = await command('curl', '-q', '-4', '-fsS', '--noproxy', '',
                             '--proxy', f'socks5h://127.0.0.1:{port}',
                             '--connect-timeout', '10', '--max-time', '90',
                             '--max-filesize', str(size), '-o', '/dev/null', '-w', '%{json}',
                             f'https://speed.cloudflare.com/__down?bytes={size}', timeout=95)
        data = json.loads(text)
        if int(data['size_download']) != size:
            raise ValueError('incomplete download')
        return {'bytes': int(data['size_download']), 'seconds': data['time_total'],
                'mbps_including_connect': size * 8 / data['time_total'] / 1e6}

    async with server:
        single = await download(8 * 1024 * 1024)
        start = time.monotonic()
        clients = await asyncio.gather(*(download(4 * 1024 * 1024) for _ in range(8)))
        elapsed = time.monotonic() - start
    print(json.dumps({'kind': 'transport_only_not_gemini', 'ip': ip, 'single': single,
                      'concurrent_clients': 8, 'clients': clients,
                      'aggregate_mbps': sum(c['bytes'] for c in clients) * 8 / elapsed / 1e6}, indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',type=int,default=1)
    parser.add_argument('--config',type=Path)
    args=parser.parse_args()
    asyncio.run(main(args))
