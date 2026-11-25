#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Standalone GitHub bootstrap; a full local checkout uses its local install.sh.
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<'HELP'
Warp-Manager installer
Local archive: bash install.sh [installer options]
GitHub bootstrap: bash install_warp_manager.sh [--source-repo OWNER/REPO]
    [--source-ref main|TAG|COMMIT] [--source-sha256 CODELOAD_ZIP_SHA256]
    [--instance N --role gemini|youtube|warp --target-gbps N --tunnels N ...]
Use --update with an existing instance. Source defaults: Forcus356/Warp-Manager, main.
The standalone bootstrap requires the repository to have been published first.
HELP
    exit 0
fi
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
base=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
remote=0
for option in "$@"; do
    [[ $option != --source-* ]] || remote=1
done
if [[ $remote == 0 && -f "$base/warp_install.py" && -f "$base/install.sh" ]]; then
    exec bash "$base/install.sh" "$@"
fi
if ! command -v python3 >/dev/null; then
    apt-get update
    apt-get install -y python3 ca-certificates
fi
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cat > "$work/bootstrap.py" <<'PYBOOT'
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

MAX_DOWNLOAD = 50 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024


def repository(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value):
        raise ValueError('source repository must be OWNER/REPO on github.com')
    return value


def resolve(repo, ref):
    repository(repo)
    if re.fullmatch(r'[a-fA-F0-9]{40}', ref):
        return ref.lower()
    request = Request('https://api.github.com/repos/' + repo + '/commits/' + quote(ref, safe=''),
                      headers={'User-Agent': 'Warp-Manager-bootstrap', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=30) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError('commit response is too large')
    commit = json.loads(payload).get('sha', '')
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('GitHub did not return an immutable commit')
    return commit


def download(repo, commit, destination, expected=None):
    repository(repo)
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('download requires a resolved commit')
    if expected and not re.fullmatch(r'[a-fA-F0-9]{64}', expected):
        raise ValueError('source SHA-256 must contain 64 hex characters')
    request = Request('https://codeload.github.com/' + repo + '/zip/' + commit,
                      headers={'User-Agent': 'Warp-Manager-bootstrap'})
    size = 0; digest = hashlib.sha256(); deadline = time.monotonic() + 300
    with urlopen(request, timeout=30) as response, destination.open('wb') as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DOWNLOAD or time.monotonic() > deadline:
                raise ValueError('source download exceeded its size or time limit')
            output.write(chunk); digest.update(chunk)
    actual = digest.hexdigest()
    if expected and actual.lower() != expected.lower():
        raise ValueError('source archive SHA-256 mismatch')
    return actual


def unpack(archive_path, destination):
    with zipfile.ZipFile(archive_path) as archive:
        items = archive.infolist()
        if len(items) > 4096 or sum(i.file_size for i in items) > MAX_UNPACKED:
            raise ValueError('source archive is too large')
        seen = set()
        for item in items:
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or '..' in name.parts or '\\' in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.filename in seen):
                raise ValueError('unsafe or duplicate source archive member')
            seen.add(item.filename)
            if not (destination / item.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError('source path escapes extraction directory')
        if archive.testzip() is not None:
            raise ValueError('source archive CRC check failed')
        archive.extractall(destination)
    candidates = [p.parent for p in destination.rglob('warp_install.py')
                  if (p.parent / 'install.sh').is_file()]
    if len(candidates) != 1:
        raise ValueError('expected exactly one Warp-Manager source tree')
    source = candidates[0]
    for name in ['warp_pool.py', 'warp_capacity.py', 'warp_ctl.py', 'warp_profiles.py', 'worker.sh', 'Dockerfile', 'LICENSE']:
        if not (source / name).is_file():
            raise ValueError('incomplete source tree: ' + name)
    return source


def main(argv=None):
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10+ is required; use a supported modern Ubuntu/Debian release.')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--source-repo', default='Forcus356/Warp-Manager')
    parser.add_argument('--source-ref', default='main')
    parser.add_argument('--source-sha256')
    args, installer_args = parser.parse_known_args(argv)
    if os.geteuid() != 0:
        raise SystemExit('Run as root.')
    commit = resolve(args.source_repo, args.source_ref)
    with tempfile.TemporaryDirectory(prefix='warp-manager-source-') as folder:
        work = Path(folder); archive = work / 'source.zip'
        digest = download(args.source_repo, commit, archive, args.source_sha256)
        source = unpack(archive, work / 'source')
        subprocess.run(['bash', '-n', str(source / 'install.sh')], check=True)
        subprocess.run([sys.executable, '-m', 'py_compile', *map(str, source.glob('*.py'))], check=True)
        print('Warp-Manager source:', args.source_repo, '\nCommit:', commit, '\nCodeload ZIP SHA-256:', digest, flush=True)
        return subprocess.run(['bash', str(source / 'install.sh'), *installer_args]).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        raise SystemExit('Bootstrap failed before or during installation: ' + str(error))
PYBOOT
python3 "$work/bootstrap.py" "$@"
