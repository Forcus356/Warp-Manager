"""128 loopback echo sessions through the real relay; synthetic, not WARP."""
import argparse
import asyncio
import contextlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from warp_pool import Pool,Observation,VERSION
from warp_capacity import token
import warp_profiles


async def main(args):
    c=warp_profiles.defaults(99,'warp',4)
    c['target_gbps']=0.001
    p=Pool(c);p.capacity.minimum=4
    servers=[];writers=set();handlers=set()
    async def echo(reader,writer):
        task=asyncio.current_task();handlers.add(task);writers.add(writer)
        try:
            while data:=await reader.read(65536):
                writer.write(data);await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(OSError):await writer.wait_closed()
            writers.discard(writer);handlers.discard(task)
    for i,n in enumerate(p.nodes):
        s=await asyncio.start_server(echo,'127.0.0.1',0);servers.append(s)
        n.port=s.sockets[0].getsockname()[1];n.enabled=True
        p.apply(n,Observation('GOOD',ip=f'203.0.113.{i+1}',gemini='DISABLED',reason='SYNTHETIC loopback control'))
        p.capacity.samples[n.name]=(token(n),1,time.monotonic())
    front=await asyncio.start_server(p.relay,'127.0.0.1',0);servers.append(front)
    port=front.sockets[0].getsockname()[1]
    data=b'loopback-control'*4096
    async def client():
        r,w=await asyncio.open_connection('127.0.0.1',port)
        try:
            w.write(data);await w.drain()
            assert await r.readexactly(len(data))==data
        finally:
            w.close();await w.wait_closed()
    start=time.monotonic()
    try:
        await asyncio.wait_for(asyncio.gather(*(client() for _ in range(args.clients))),10)
        result={'kind':'synthetic_loopback_not_WARP_not_Gemini','version':VERSION,
                'clients':args.clients,'successful_clients':args.clients,'elapsed_seconds':time.monotonic()-start}
        print(json.dumps(result),flush=True)
        args.output.write_text(json.dumps(result,indent=2))
    finally:
        for s in servers:s.close();await s.wait_closed()
        for w in tuple(writers):w.close()
        for group in p.connections.values():
            for w in tuple(group):w.close()
        if handlers:
            with contextlib.suppress(asyncio.TimeoutError):await asyncio.wait_for(asyncio.gather(*tuple(handlers)),2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clients',type=int,default=128)
    parser.add_argument('--output',type=Path,default=Path('relay-control.json'))
    args=parser.parse_args()
    if not 1<=args.clients<=512:parser.error('--clients: 1–512')
    asyncio.run(main(args))
