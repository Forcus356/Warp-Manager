"""Verified optional browser TLS profile. SPDX-License-Identifier: GPL-3.0-only."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import sys

# Same pinned upstream release and digest used by Forcus Auto-Pin cfe8c3b.
URL = 'https://github.com/lexiforest/curl-impersonate/releases/download/v2.3.2/curl-impersonate-v2.3.2.x86_64-linux-gnu.tar.gz'
SHA256 = 'bc9bd2522fe1850dd54c0fff189c8dd606df3f222ab2a905f2dd255123c3f495'
COMPOSE_URL = 'https://github.com/docker/compose/releases/download/v2.39.4/docker-compose-linux-x86_64'
COMPOSE_SHA256 = '7af95166a730b87e172d4fc9aefea8725d3c6c7327d59149267b452114ddb7d4'


def ensure_compose(target=Path('/usr/local/lib/docker/cli-plugins/docker-compose')):
    import warp_system
    if target.exists() or target.is_symlink():
        raise ValueError('существующий Compose plugin не заменяется автоматически: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.compose-', dir=target.parent, delete=False) as f:
        temporary = Path(f.name)
    try:
        subprocess.run(['curl', '-q', '-fLsS', '--retry', '3', '--max-time', '300', COMPOSE_URL, '-o', str(temporary)], check=True)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != COMPOSE_SHA256:
            raise ValueError('Compose checksum mismatch')
        temporary.chmod(0o755)
        subprocess.run([str(temporary), 'version', '--short'], check=True, stdout=subprocess.DEVNULL)
        temporary.replace(target)
        ledger = warp_system.load_ledger()
        ledger.setdefault('binaries', {})['compose'] = {'owned': True, 'path': str(target), 'sha256': COMPOSE_SHA256}
        warp_system.save_ledger(ledger)
    finally:
        temporary.unlink(missing_ok=True)


def unpack(archive, destination):
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            path = (destination / member.name).resolve()
            if not path.is_relative_to(destination.resolve()) or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError('unsafe archive member')
            if member.issym() or member.islnk():
                target = path.parent / member.linkname if member.issym() else destination / member.linkname
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('unsafe archive link')
        if sys.version_info >= (3, 12):
            tar.extractall(destination, filter='data')
        else:
            tar.extractall(destination)


def ensure(base=Path('/var/lib/warp-google-pool-system')):
    target = base / ('curl-impersonate-' + SHA256[:12])
    if (target / '.verified-release').is_file() and (target / '.verified-release').read_text().strip() == SHA256:
        subprocess.run([str(target / 'curl-impersonate'), '--version'], check=True, stdout=subprocess.DEVNULL)
        return target
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='browser-', dir=base) as temporary:
        work = Path(temporary); archive = work / 'release.tar.gz'; payload = work / 'payload'; payload.mkdir()
        subprocess.run(['curl', '-q', '-fLsS', '--retry', '3', '--max-time', '300', URL, '-o', str(archive)], check=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise ValueError('curl-impersonate checksum mismatch')
        unpack(archive, payload)
        subprocess.run([str(payload / 'curl-impersonate'), '--version'], check=True, stdout=subprocess.DEVNULL)
        (payload / '.verified-release').write_text(SHA256 + '\n')
        if target.exists():
            shutil.rmtree(target)
        payload.rename(target)
    return target
