#!/usr/bin/env python3
"""Render real console functions with explicitly synthetic state. GPL-3.0-only."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import warp_menu
import warp_profiles as profiles
import warp_ctl
from warp_pool import VERSION


def captures():
    configs={1:dict(profiles.defaults(1,'gemini',4),target_gbps=1),
             2:dict(profiles.defaults(2,'youtube',4),target_gbps=0.1)}
    def sample(root):
        c=json.loads((root/'config.json').read_text());n=c['instance']
        rows=[dict(name=row['name'],state='STARTING' if i<2 else 'DORMANT',
                   role='SEARCH' if i<2 else 'DORMANT',enabled=i<2,ip='',google='',gemini='',
                   reason='Проверка выхода' if i<2 else '',checked=0,rotations=0,active=False,
                   connections=0,speed_kib_s=None,retry_in=0,phase='GOOGLE' if i<2 else 'DORMANT')
              for i,row in enumerate(c['nodes'])]
        cap=dict(target_gbps=c['target_gbps'],phase='SEARCHING',reason='',primary=[],reserve=[],
                 reserve_needed=0,enabled=2,limit=4,individual_sum_gbps=0,measured_gbps=None)
        return dict(instance=n,role=profiles.role(c),version=VERSION,stale=False,host='127.0.0.1',
                    socks=c['listen_port'],http=c['http_port'],service='active',autostart='enabled',
                    ready_tunnels=0,configured_tunnels=4,distinct_ready_ips=0,
                    strategy='weighted_capacity',active=None,nodes=rows,youtube='SEARCHING',capacity=cap)
    with tempfile.TemporaryDirectory() as folder:
        with patch.object(profiles,'OPT',Path(folder)):
            for number,c in configs.items():
                root=profiles.directory(number);root.mkdir();(root/'config.json').write_text(json.dumps(c))
                (root/'origin.json').write_text(json.dumps({'source':'template','group':'DEMO','role':profiles.role(c)}))
            text=io.StringIO()
            with patch('warp_ctl.status_data',side_effect=sample),patch('builtins.input',return_value='0'),contextlib.redirect_stdout(text):
                warp_menu.menu(profiles.directory(1))
            menu=text.getvalue()
    text=io.StringIO()
    c=dict(profiles.defaults(1,'gemini',12),target_gbps=1)
    with patch('builtins.input',return_value='0'),contextlib.redirect_stdout(text):
        warp_menu.edit_draft(c,'easy')
    easy=text.getvalue()
    text=io.StringIO()
    plan=[profiles.defaults(1,'gemini',3),profiles.defaults(2,'youtube',1)]
    with patch('builtins.input',side_effect=['1','n','n']),patch.object(profiles,'template_plan',return_value=plan),\
         patch.object(profiles,'validate_plan'),contextlib.redirect_stdout(text):
        warp_menu.templates()
    return {'menu':menu,'easy-mode':easy,'templates':text.getvalue()}


def main():
    try:
        from PIL import Image,ImageDraw,ImageFont
    except ImportError:
        raise SystemExit('Preview rendering requires optional Pillow; it is not a runtime/test dependency.')
    fonts=[Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'),Path('/usr/share/fonts/TTF/DejaVuSansMono.ttf')]
    font_path=next((p for p in fonts if p.is_file()),None)
    if font_path is None:raise SystemExit('Install DejaVu Sans Mono for reproducible console previews.')
    font=ImageFont.truetype(str(font_path),17)
    assets=ROOT/'docs/assets';assets.mkdir(parents=True,exist_ok=True)
    for name,body in captures().items():
        text='DEMO: synthetic state rendered by the actual console; no live exit is certified.\n'+body
        (assets/(name+'-demo.txt')).write_text(text)
        lines=text.splitlines();width=max(1100,int(max(font.getlength(line) for line in lines))+56)
        image=Image.new('RGB',(width,72+len(lines)*25),'#0c1422');draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((0,0,width,40),radius=12,fill='#172337')
        for x,color in [(22,'#f87171'),(43,'#fbbf24'),(64,'#34d399')]:draw.ellipse((x,14,x+10,24),fill=color)
        draw.text((90,9),'Warp-Manager · illustrative console',font=font,fill='#cbd5e1')
        for i,line in enumerate(lines):draw.text((26,56+i*25),line,font=font,fill='#7dd3fc' if i==0 else '#e2e8f0')
        image.save(assets/(name+'-preview.png'),optimize=True)
        print(name,image.size)


if __name__=='__main__':main()
