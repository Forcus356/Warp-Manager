#!/usr/bin/env python3
"""Check public source, documentation links, examples and shell syntax. GPL-3.0-only."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from warp_install import SOURCE_FILES
from warp_pool import validate_config
from warp_profiles import defaults, routing, outbound
from build_release import version, source_files

LANGUAGES = ['README.md','README.ru_RU.md','README.fa_IR.md','README.ar_EG.md',
             'README.zh_CN.md','README.es_ES.md','README.tr_TR.md']


def check(root=ROOT):
    version(root)
    for name in SOURCE_FILES + LANGUAGES + ['SECURITY.md','SECURITY.ru_RU.md','CONTRIBUTING.md',
            'docs/README.md','docs/CONFIGURATION.md','docs/OPERATIONS.md','docs/TESTING.md',
            'docs/xray/README.md','docs/testing/RESULTS.md','install_warp_manager.sh']:
        if not (root/name).is_file():raise ValueError('missing required file: '+name)
    if (root/'README_RU.md').read_bytes() != (root/'README.ru_RU.md').read_bytes():
        raise ValueError('compatibility README_RU.md must match README.ru_RU.md')
    for path in source_files(root):
        if path.suffix == '.py':ast.parse(path.read_text(),filename=str(path))
        elif path.suffix == '.json':json.loads(path.read_text())
        elif path.suffix == '.sh':subprocess.run(['bash','-n',str(path)],check=True)
        elif path.suffix == '.svg':ET.parse(path)
        if path.suffix == '.md':
            for target in re.findall(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)',path.read_text()):
                if target.startswith(('https://','http://','mailto:','#')):continue
                name=target.split('#',1)[0]
                dest=(path.parent/name).resolve()
                if not dest.is_relative_to(root.resolve()) or not dest.exists():
                    raise ValueError('broken local link in '+str(path.relative_to(root))+': '+target)
        if path.suffix in {'.py','.sh','.md','.json','.yml','.txt'}:
            content=path.read_bytes()
            for kind in [b'OPENSSH',b'RSA',b'EC']:
                if b'-----BEGIN '+kind+b' PRIVATE KEY-----' in content:
                    raise ValueError('private key material in '+str(path))
    examples={p.stem:validate_config(json.loads(p.read_text())) for p in (root/'examples').glob('*.json')}
    expected=routing(examples['youtube-fixed'],examples['gemini-fixed'])
    if json.loads((root/'docs/xray/youtube-gemini.fragment.json').read_text())!=expected:
        raise ValueError('Xray routing example differs from the actual exporter')
    if json.loads((root/'docs/xray/outbound.fragment.json').read_text())!=outbound(examples['gemini-fixed']):
        raise ValueError('Xray outbound example differs from the actual exporter')
    source=(root/'install_warp_manager.sh').read_text().split("<<'PYBOOT'\n",1)[1].split('\nPYBOOT\n',1)[0]
    ast.parse(source,filename='embedded bootstrap.py')
    return len(source_files(root))


if __name__=='__main__':
    print('Project valid:',check(),'source files; local links, examples and scripts checked.')
