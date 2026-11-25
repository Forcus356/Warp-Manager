#!/usr/bin/env python3
"""Instance installation and transactions. SPDX-License-Identifier: GPL-3.0-only."""
import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from warp_pool import ROOT, VERSION, atomic_json, read_config, validate_config, invalidate_status
import warp_profiles as profiles
import warp_system

IMAGE = 'warp-google-pool-worker:' + VERSION
LIB = Path('/usr/local/lib/warp-google-pool')
SOURCE_FILES = ['warp_pool.py', 'warp_ctl.py', 'warp_profiles.py', 'warp_install.py',
                'warp_capacity.py',
                'warp_system.py', 'warp_assets.py', 'warp_menu.py', 'http_front.py', 'gemini_probe.py',
                'tcp_bridge.py', 'worker.sh', 'install.sh', 'uninstall.sh', 'Dockerfile',
                'LICENSE', 'THIRD_PARTY_NOTICES.md', 'UPSTREAM_NOTICES.md', 'README_RU.md']


def run(*args, check=True, capture=False):
    return subprocess.run([str(a) for a in args], check=check,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, text=True)


def installed_package(name):
    return run('apt-cache', 'show', name, check=False, capture=True).returncode == 0


def dependencies():
    system = platform.freedesktop_os_release()
    if system.get('ID') not in ('ubuntu', 'debian') or platform.machine() != 'x86_64':
        raise ValueError('поддерживаются Ubuntu/Debian x86_64')
    missing = [package for binary, package in [('curl', 'curl'), ('systemctl', 'systemd'), ('ss', 'iproute2')]
               if not shutil.which(binary)]
    if missing:
        run('apt-get', 'update')
        if warp_system.install('tools', ['ca-certificates', *missing], lock_held=True):
            raise RuntimeError('не удалось установить зависимости')
    if not shutil.which('docker'):
        run('apt-get', 'update')
        packages = ['docker-ce', 'docker-ce-cli', 'containerd.io'] if installed_package('docker-ce') else ['docker.io']
        if warp_system.install('docker', packages, lock_held=True):
            raise RuntimeError('не удалось установить Docker')
    run('systemctl', 'start', 'docker')
    run('docker', 'info', capture=True)
    if run('docker', 'compose', 'version', check=False, capture=True).returncode:
        run('apt-get', 'update')
        for name in ('docker-compose-plugin', 'docker-compose-v2', 'docker-compose'):
            if not installed_package(name):
                continue
            # Refuse an APT plan that replaces any preexisting Docker packages.
            plan = run('apt-get', '-s', 'install', name, capture=True).stdout
            if any(line.startswith(('Remv ', 'Purg ')) for line in plan.splitlines()):
                continue
            if warp_system.install('docker', [name], lock_held=True) == 0 and run('docker', 'compose', 'version', check=False, capture=True).returncode == 0:
                break
        else:
            import warp_assets
            warp_assets.ensure_compose()
            run('docker', 'compose', 'version', capture=True)


def ownership(config):
    project = profiles.directory(config['instance']).name
    for node in config['nodes']:
        result = run('docker', 'inspect', node['container'], check=False, capture=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)[0]
            if data.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') != project:
                raise ValueError('контейнер принадлежит другому приложению: ' + node['container'])


def copy_source(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        if (source / name).resolve() != (destination / name).resolve():
            shutil.copy2(source / name, destination / name)
    for name in ['CHANGELOG.md', 'FEATURE_PARITY_RU.md']:
        if (source / name).is_file() and source.resolve() != destination.resolve():
            shutil.copy2(source / name, destination / name)


def install_shared(source):
    names = SOURCE_FILES + [name for name in ('CHANGELOG.md', 'FEATURE_PARITY_RU.md') if (source / name).is_file()]
    if LIB.is_dir() and all((LIB / name).is_file() and (LIB / name).read_bytes() == (source / name).read_bytes() for name in names):
        return None
    LIB.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.warp-pool-stage-', dir=LIB.parent))
    previous = None
    try:
        copy_source(source, stage)
        if LIB.exists():
            previous = Path(tempfile.mkdtemp(prefix='.warp-pool-previous-', dir=LIB.parent))
            previous.rmdir(); LIB.rename(previous)
        try:
            stage.rename(LIB)
        except BaseException:
            if previous:
                previous.rename(LIB)
            raise
        return previous
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def atomic_text(path, text, mode=0o644):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(mode); temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def service_text(root, number):
    return f'''[Unit]
Description=WARP Google Pool instance {number}
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory={root}
ExecStartPre=/usr/bin/docker compose up -d --no-build --remove-orphans
ExecStart=/usr/bin/python3 {root}/warp_pool.py run
Restart=on-failure
RestartSec=10
UMask=0077
TimeoutStopSec=20
[Install]
WantedBy=multi-user.target
'''


def deploy(source, config, update=False, rebuild=False):
    config = validate_config(config); number = config['instance']; root = profiles.directory(number)
    for name in SOURCE_FILES:
        if not (source / name).is_file():
            raise ValueError('в релизе отсутствует: ' + name)
    previous = read_config(root / 'config.json') if (root / 'config.json').exists() else None
    if bool(previous) != bool(update):
        raise ValueError('для существующего экземпляра требуется --update; для нового — создание')
    profiles.reservations(config)
    ownership(config)
    with profiles.reserve_ports(config, previous):
        pass
    image_exists = run('docker', 'image', 'inspect', IMAGE, check=False, capture=True).returncode == 0
    if rebuild or not image_exists:
        run('docker', 'build', '-t', IMAGE, source)
    generated = profiles.compose(config, root, IMAGE)
    if previous:
        old_names = {n['name'] for n in previous['nodes']} - {n['name'] for n in config['nodes']}
        config['retained_volumes'] = sorted(set(config.get('retained_volumes', [])) | set(previous.get('retained_volumes', [])) | old_names)
        generated = profiles.compose(config, root, IMAGE)
    service = profiles.unit(number); unit_path = Path('/etc/systemd/system') / service
    active = run('systemctl', 'is-active', service, check=False, capture=True).returncode == 0
    running = bool(run('docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + root.name, capture=True).stdout.strip())
    backup = None
    if previous:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = Path('/opt/warp-google-pool-backups') / f'{root.name}-{stamp}'
        backup.mkdir(parents=True, mode=0o700)
        for name in SOURCE_FILES + ['config.json', 'compose.yaml', 'connection.json', 'origin.json']:
            if (root / name).is_file():
                shutil.copy2(root / name, backup / name)
        if unit_path.exists():
            shutil.copy2(unit_path, backup / '.previous.service')
    shared_previous = None
    try:
        if previous:
            run('systemctl', 'stop', service)
            invalidate_status(root, previous)
        copy_source(source, root)
        atomic_json(root / 'config.json', config)
        atomic_json(root / 'compose.yaml', generated)  # JSON is valid Compose YAML.
        atomic_json(root / 'connection.json', profiles.outbound(config))
        atomic_text(unit_path, service_text(root, number))
        run('systemctl', 'daemon-reload')
        if active or running or not previous:
            run('docker', 'compose', '-f', root / 'compose.yaml', 'up', '-d', '--no-build', '--remove-orphans')
        if not previous:
            run('systemctl', 'enable', '--now', service)
        elif active:
            run('systemctl', 'start', service)
        shared_previous = install_shared(source)
        launcher = Path('/usr/local/bin/warp-pool')
        atomic_text(launcher, '#!/bin/sh\nexec /usr/bin/python3 /usr/local/lib/warp-google-pool/warp_ctl.py "$@"\n', 0o755)
        alias = launcher.with_name(f'warp-pool-{number}')
        atomic_text(alias, f'#!/bin/sh\nexec /usr/local/bin/warp-pool --instance {number} "$@"\n', 0o755)
    except BaseException:
        if shared_previous:
            shutil.rmtree(LIB)
            shared_previous.rename(LIB)
        if backup:
            for p in backup.iterdir():
                if not p.name.startswith('.'):
                    shutil.copy2(p, root / p.name)
            if (backup / '.previous.service').exists():
                shutil.copy2(backup / '.previous.service', unit_path)
            run('systemctl', 'daemon-reload', check=False)
            if active or running:
                run('docker', 'compose', '-f', root / 'compose.yaml', 'up', '-d', '--no-build', '--remove-orphans', check=False)
            if active:
                run('systemctl', 'start', service, check=False)
        raise
    if shared_previous:
        shutil.rmtree(shared_previous, ignore_errors=True)
    print(f'Экземпляр {number}: установлен. Проверка выхода выполняется монитором. Меню: warp-pool')
    if backup:
        print('Резервная копия:', backup)


def remove(number, purge_profiles=False, purge_dependencies=False):
    root = profiles.directory(number); config = read_config(root / 'config.json')
    ownership(config)
    run('systemctl', 'disable', '--now', profiles.unit(number), check=False)
    run('docker', 'compose', '-f', root / 'compose.yaml', '--profile', '*', 'down', *(['-v'] if purge_profiles else []))
    backup = Path('/opt/warp-google-pool-backups') / (root.name + '-removed-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.parent.mkdir(exist_ok=True)
    shutil.copytree(root, backup, ignore=shutil.ignore_patterns('requests', '__pycache__', '.manager.lock'))
    backup.chmod(0o700)
    shutil.rmtree(root)
    (Path('/etc/systemd/system') / profiles.unit(number)).unlink(missing_ok=True)
    Path(f'/usr/local/bin/warp-pool-{number}').unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    result = warp_system.cleanup_dependencies(purge=purge_dependencies, lock_held=True)
    if not profiles.discover():
        Path('/usr/local/bin/warp-pool').unlink(missing_ok=True)
        shutil.rmtree(LIB, ignore_errors=True)
    print(json.dumps({'removed_instance': number, 'backup': str(backup), 'dependencies': result}, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=int, default=1)
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--role', choices=['gemini', 'youtube', 'warp'])
    parser.add_argument('--tunnels', type=int)
    parser.add_argument('--target-gbps', type=float, help='цель/скорость канала VPS в Гбит/с; 0 — фиксированный пул')
    parser.add_argument('--port', type=int)
    parser.add_argument('--http', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--bind')
    parser.add_argument('--interval', type=float, help='минуты')
    parser.add_argument('--min-speed', type=int)
    parser.add_argument('--probe-mb', type=int)
    parser.add_argument('--wait', type=int)
    parser.add_argument('--impersonate', action='store_true')
    parser.add_argument('--remove', action='store_true')
    parser.add_argument('--purge-profiles', action='store_true')
    parser.add_argument('--purge-deps', action='store_true')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error('запустите от root')
    profiles.directory(args.instance)
    with warp_system.locked():
        if args.remove:
            remove(args.instance, args.purge_profiles, args.purge_deps); return
        config_path = profiles.directory(args.instance) / 'config.json'
        config = read_config(args.config) if args.config else (read_config(config_path) if config_path.exists() else profiles.defaults(args.instance, args.role or 'gemini', args.tunnels or 3))
        config['instance'] = args.instance
        if args.role:
            config.update(youtube_mode=args.role == 'youtube', gemini_check=args.role == 'gemini')
        if args.tunnels is not None:
            config = profiles.resize(config, args.tunnels)
        if args.target_gbps is not None:
            if args.target_gbps == 0 and config['target_gbps'] and args.tunnels is None:
                raise ValueError('при отключении автопула задайте --tunnels, например 3')
            if args.target_gbps > 0 and not config['target_gbps'] and args.tunnels is None:
                config = profiles.resize(config, max(64, len(config['nodes'])))
            config['target_gbps'] = args.target_gbps
        if args.port is not None:
            config['listen_port'] = args.port
            if config['http_port'] is not None:
                config['http_port'] = args.port + 1
        if args.http is not None:
            config['http_port'] = config['listen_port'] + 1 if args.http else None
        for key, value in [('listen_host', args.bind), ('min_speed_kib', args.min_speed), ('probe_mb', args.probe_mb), ('wait_timeout', args.wait)]:
            if value is not None:
                config[key] = value
        if args.interval is not None:
            seconds = args.interval * 60
            if not seconds.is_integer() or seconds < 60:
                raise ValueError('интервал: от 1 минуты с целым числом секунд')
            config.update(check_interval=int(seconds), health_ttl=max(config['health_ttl'], seconds + 300))
        validate_config(config)
        dependencies()
        if args.impersonate:
            import warp_assets
            config['impersonate_dir'] = str(warp_assets.ensure(warp_system.system_dir()))
        deploy(ROOT, config, args.update, args.rebuild)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Ошибка: {exc}')
