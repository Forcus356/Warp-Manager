#!/usr/bin/env python3
"""Build a deterministic source release with a manifest. GPL-3.0-only."""
import argparse
import ast
import hashlib
from pathlib import Path
import re
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'venv', 'dist', 'build', '.pytest_cache', 'requests', 'backups'}


def source_files(root):
    files = []
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if any(p in EXCLUDE_DIRS for p in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError('source symlinks are not included: ' + relative.as_posix())
        if not path.is_file() or path.name == 'MANIFEST.sha256' or path.suffix in {'.pyc','.zip','.gz','.log','.key','.pem'}:
            continue
        if path.name in {'state.json','runtime.json','previous-state.json','config.previous.json','config.local.json','reg.json','.env'}:
            raise ValueError('runtime or private file in source: ' + relative.as_posix())
        files.append(path)
    return sorted(files)


def version(root):
    value = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('invalid VERSION')
    parsed = ast.parse((root / 'warp_pool.py').read_text())
    code = next(ast.literal_eval(n.value) for n in parsed.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in n.targets))
    if code != value:
        raise ValueError('VERSION and warp_pool.VERSION differ')
    return value


def build(root, output):
    release = version(root); output.mkdir(parents=True, exist_ok=True)
    files = source_files(root)
    payload = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
    payload['MANIFEST.sha256'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                        for name,data in payload.items()).encode()
    target = output / f'Warp-Manager-v{release}.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name,data in sorted(payload.items()):
            info = zipfile.ZipInfo('Warp-Manager/' + name, (2026,1,1,0,0,0))
            info.create_system = 3
            executable = name.endswith('.sh') or name.startswith('scripts/') and name.endswith('.py')
            info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(digest + '  ' + target.name + '\n')
    return target, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--expected-tag', help='release tag, for example v0.4.1')
    args = parser.parse_args()
    if args.expected_tag and args.expected_tag != 'v' + version(ROOT):
        parser.error('release tag must match VERSION')
    target,digest = build(ROOT, args.output.resolve())
    print(target); print(digest)


if __name__ == '__main__':
    main()
