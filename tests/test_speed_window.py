import asyncio
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warp_pool import Pool
import warp_profiles


class SpeedWindow(unittest.IsolatedAsyncioTestCase):
    async def sample(self, http_code=0, previous=True, expired=True, content_type=None):
        c=warp_profiles.defaults()
        c['target_gbps']=1
        p=Pool(c)
        calls=0
        deadline=time.monotonic()+0.09
        async def command(*args, **kwargs):
            nonlocal calls
            calls+=1
            if calls==1 and previous:
                return json.dumps({'http_code':200,'size_download':1000000,'exitcode':0,'content_type':'application/octet-stream'})
            if expired:
                await asyncio.sleep(max(0,deadline-time.monotonic())+0.002)
            return json.dumps({'http_code':http_code,'size_download':0,'exitcode':28,'content_type':content_type})
        with patch('warp_pool.command',command):
            return await p.capacity.download(p.nodes[0],deadline)

    async def test_final_empty_handshake_preserves_previously_downloaded_bytes(self):
        self.assertEqual(await self.sample(),1000000)

    async def test_final_200_headers_without_body_preserve_previous_data(self):
        self.assertEqual(await self.sample(http_code=200),1000000)

    async def test_wholly_empty_trial_cannot_be_accepted(self):
        with self.assertRaises(ValueError):
            await self.sample(previous=False)

    async def test_early_empty_timeout_cannot_be_accepted(self):
        with self.assertRaises(ValueError):
            await self.sample(expired=False)

    async def test_final_http_error_or_html_cannot_be_accepted(self):
        for code,content in [(403,None),(200,'text/html; charset=utf-8')]:
            with self.subTest(code=code,content=content), self.assertRaises(ValueError):
                await self.sample(http_code=code,content_type=content)


if __name__=='__main__':
    unittest.main()
