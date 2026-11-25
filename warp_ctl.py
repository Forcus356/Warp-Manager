#!/usr/bin/env python3
"""WARP Google Pool management. SPDX-License-Identifier: GPL-3.0-only."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit
import zipfile

from warp_pool import ROOT, VERSION, atomic_json, read_config, validate_config, invalidate_status, boot_id
import warp_profiles as profiles


def run(*args):
    return subprocess.run([str(a) for a in args], check=True)


def unit_state(root, action):
    try:
        unit = profiles.unit(read_config(root / 'config.json')['instance'])
        result = subprocess.run(['systemctl', action, unit], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or 'unknown'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 'unknown'


def status_data(root):
    config = read_config(root / 'config.json')
    path = root / 'state.json'
    data = json.loads(path.read_text()) if path.exists() else {'nodes': [], 'updated': 0, 'ready_tunnels': 0, 'distinct_ready_ips': 0, 'active': None}
    data.update(instance=config['instance'], role=profiles.role(config),
                stale=time.time() - data['updated'] > 10, service=unit_state(root, 'is-active'),
                autostart=unit_state(root, 'is-enabled'), host=config['listen_host'],
                socks=config['listen_port'], http=config['http_port'], configured_tunnels=len(config['nodes']))
    if not data['stale']:
        data['stale'] = (not data.get('pid') or str(data['pid']) != unit_pid(root)
                         or data.get('boot_id') != boot_id())
    if data['stale']:
        data.update(previous_ready_tunnels=data['ready_tunnels'], ready_tunnels=0, active=None, youtube='UNKNOWN')
        if data.get('capacity'):
            data['capacity'].update(phase='UNKNOWN', target_reached=False, complete=False, measured_gbps=None,
                                    primary=[], reserve=[])
        for node in data['nodes']:
            node['last_state'], node['state'] = node['state'], 'UNKNOWN'
            node['last_gemini'], node['gemini'] = node.get('gemini'), 'UNKNOWN'
    return data


def unit_pid(root):
    try:
        number = read_config(root / 'config.json')['instance']
        return subprocess.run(['systemctl', 'show', profiles.unit(number), '-p', 'MainPID', '--value'],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return '0'


def status(root, raw=False):
    data = status_data(root)
    if raw:
        print(json.dumps(data, ensure_ascii=False, indent=2)); return
    warning = ' — СТАТУС УСТАРЕЛ' if data['stale'] else ''
    print(f"Экземпляр {data['instance']} · {data['role']} · v{data.get('version', 'prototype')}{warning}")
    print(f"SOCKS {data['host']}:{data['socks']} | HTTP {data['http'] or 'выключен'} | служба {data['service']} | автозапуск {data['autostart']}")
    print(f"Проверено {data['ready_tunnels']}/{data['configured_tunnels']}; разных IP: {data['distinct_ready_ips']}; выбор: {data.get('strategy', 'failover')}; выход: {data.get('active') or 'нет'}")
    cap = data.get('capacity')
    if cap:
        measured = 'ещё не подтверждена' if cap['measured_gbps'] is None else f"{cap['measured_gbps']:.3f} Гбит/с"
        print(f"Автопул: цель {cap['target_gbps']} Гбит/с; общий замер {measured}; {cap['phase']}")
        print(f"Рабочих {len(cap['primary'])}; резерв {len(cap['reserve'])}/{cap['reserve_needed']}; запущено {cap['enabled']}/{cap['limit']}")
        if cap['reason']:
            print('  ' + cap['reason'])
    if data['role'] == 'YouTube':
        print('YouTube:', 'UNKNOWN (устарело)' if data['stale'] else data.get('youtube', 'UNKNOWN'))
    for row in data['nodes']:
        speed = row.get('speed_kib_s')
        speed = '—' if speed is None else f'{speed * 8192 / 1000000:.1f} Мбит/с'
        print(f"  {'*' if row.get('active') else ' '}{row['name']}: {row.get('role', '')} {row['state']} / {row.get('phase', '—')}; Google={row.get('google') or '—'}; Gemini={row.get('gemini') or '—'}; {speed}; TCP={row.get('connections', 0)}")
        print(f"    {row.get('ip') or '—'}; {row.get('reason', '')}; ротаций={row['rotations']}; пауза={row.get('retry_in', 0)} с")
    if not data['nodes'] or any(not n.get('checked') for n in data['nodes']):
        old = root / 'previous-state.json'
        if old.exists():
            previous = json.loads(old.read_text())
            print('  Предыдущие результаты (требуется новая проверка): ' + ', '.join(f"{n['name']}={n.get('gemini') or n.get('state')}" for n in previous.get('nodes', [])))


def request(root, action, node, method=None, full=False):
    config = read_config(root / 'config.json')
    names = [n['name'] for n in config['nodes']]
    if node not in names + ['all']:
        raise ValueError('туннель: ' + ', '.join(names) + ' или all')
    if action == 'rotate' and node == 'all':
        raise ValueError('ротация выполняется по одному туннелю')
    state = root / 'state.json'
    if not state.exists() or time.time() - json.loads(state.read_text())['updated'] > 10:
        raise ValueError('менеджер не запущен или статус устарел; выполните warp-pool start')
    target = root / 'requests'; target.mkdir(mode=0o700, exist_ok=True)
    value = {'action': action, 'node': node, 'full': full}
    if method:
        value['method'] = method
    atomic_json(target / (uuid.uuid4().hex + '.json'), value)
    print(f"Экземпляр {config['instance']}: запрос принят. Результат — status / logs.")


def convert(old, value):
    if type(old) is bool:
        yes, no = ('1', 'true', 'yes', 'y', 'да', 'д'), ('0', 'false', 'no', 'n', 'нет', 'н')
        if value.lower() not in yes + no:
            raise ValueError('1 — включить, 0 — выключить')
        return value.lower() in yes
    if isinstance(old, list):
        return [] if value == '-' else [v.upper() for v in value.replace(',', ' ').split()]
    if isinstance(old, (int, float)):
        number = float(value)
        return int(number) if number.is_integer() else number
    return value


def apply_config(root, config, impersonate=False):
    config = validate_config(config)
    with tempfile.TemporaryDirectory(prefix='warp-settings-') as work:
        path = Path(work) / 'config.json'; atomic_json(path, config)
        args = ['bash', str(ROOT / 'install.sh'), '--instance', str(config['instance']), '--update', '--config', str(path)]
        if impersonate:
            args.append('--impersonate')
        run(*args)


def set_config(root, key, text):
    path = root / 'config.json'; config = read_config(path)
    forbidden = {'instance', 'nodes', 'retained_volumes'}
    if key in forbidden or key not in config:
        raise ValueError('параметр меняется через Easy/Advanced или create --tunnels')
    if key == 'http_port':
        value = None if text.lower() in ('none', 'off', '-') else int(text)
    else:
        value = convert(config[key], text)
    if key == 'target_gbps' and value == 0 and config['target_gbps']:
        raise ValueError('выключите автопул с явным размером: capacity --gbps 0 --max-tunnels 3')
    if key == 'target_gbps' and value > 0 and not config['target_gbps']:
        config = profiles.resize(config, max(64, len(config['nodes'])))
    config[key] = value
    if key == 'check_interval':
        config['health_ttl'] = max(config['health_ttl'], value + 300)
    if key == 'listen_port' and config['http_port'] is not None:
        config['http_port'] = value + 1
    config = validate_config(config)
    if key in ('listen_host', 'listen_port', 'http_port', 'target_gbps'):
        apply_config(root, config); return
    atomic_json(path.with_name('config.previous.json'), read_config(path))
    atomic_json(path, config)
    print(f'Сохранено: {key} = {value}. Примените: warp-pool restart')


def service(root, action):
    config = read_config(root / 'config.json'); unit = profiles.unit(config['instance'])
    if action == 'restart':
        run('systemctl', 'stop', unit)
        invalidate_status(root, config)
        run('systemctl', 'start', unit)
    else:
        if action == 'start' and unit_state(root, 'is-active') != 'active':
            invalidate_status(root, config)
        run('systemctl', action, unit)
    if action == 'stop':
        run('docker', 'compose', '-f', root / 'compose.yaml', '--profile', '*', 'stop')
        invalidate_status(root, config)


def execute_plan(plan, source=ROOT, browser_ids=()):
    profiles.validate_plan(plan)
    completed = []; group = uuid.uuid4().hex
    try:
        for config in plan:
            with tempfile.TemporaryDirectory(prefix='warp-template-') as temporary:
                path = Path(temporary) / 'config.json'; atomic_json(path, config)
                run('bash', source / 'install.sh', '--instance', config['instance'], '--config', path,
                    *(['--impersonate'] if config['instance'] in browser_ids else []))
            completed.append(config['instance'])
            profiles.mark_template([config['instance']], group=group)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print('Шаблон установлен частично. Сохранены экземпляры:', completed)
        raise
    print('Установлены экземпляры:', completed, 'Проверка выходов продолжается монитором.')


@contextlib.contextmanager
def release(source, digest=None):
    parsed = urlsplit(str(source))
    if parsed.scheme and (parsed.scheme != 'https' or not digest or len(digest) != 64):
        raise ValueError('для HTTPS-обновления нужен --sha256 с ожидаемой контрольной суммой')
    path = Path(str(source))
    if not parsed.scheme and path.is_dir():
        yield path.resolve(); return
    with tempfile.TemporaryDirectory(prefix='warp-release-') as temporary:
        root = Path(temporary)
        if parsed.scheme:
            path = root / 'release.zip'
            run('curl', '-q', '-fLsS', '--max-time', '300', '--max-filesize', '50000000', str(source), '-o', path)
        if digest and hashlib.sha256(path.read_bytes()).hexdigest().lower() != digest.lower():
            raise ValueError('контрольная сумма архива не совпала')
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError('слишком большой архив')
            for item in archive.infolist():
                if not (root / item.filename).resolve().is_relative_to(root.resolve()):
                    raise ValueError('небезопасный путь в архиве')
            archive.extractall(root)
        candidates = [p.parent for p in root.rglob('warp_install.py') if (p.parent / 'install.sh').is_file()]
        if len(candidates) != 1:
            raise ValueError('архив должен содержать один релиз WARP Pool')
        yield candidates[0]


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    scope = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    scope.add_argument('--root', type=Path)
    scope.add_argument('--instance', type=int)
    scope.add_argument('--all', action='store_true')
    selected, rest = scope.parse_known_args(arguments)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--version', action='version', version=VERSION)
    subs = parser.add_subparsers(dest='action')
    for action in ('menu', 'easy', 'advanced', 'start', 'stop', 'restart', 'xray'):
        subs.add_parser(action)
    p = subs.add_parser('status'); p.add_argument('--json', action='store_true')
    p = subs.add_parser('config'); p.add_argument('key', nargs='?'); p.add_argument('value', nargs='?')
    for action in ('check', 'search', 'speed', 'rotate'):
        p = subs.add_parser(action); p.add_argument('node', nargs='?', default=None if action == 'rotate' else 'all')
        p.add_argument('--full', action='store_true')
        if action == 'rotate':
            p.add_argument('--method', choices=['registration', 'keys', 'reconnect'])
    p = subs.add_parser('logs'); p.add_argument('--follow', '-f', action='store_true'); p.add_argument('--lines', '-n', type=int, default=60)
    p = subs.add_parser('autostart'); p.add_argument('state', choices=['on', 'off'])
    p = subs.add_parser('create'); p.add_argument('--role', choices=['gemini', 'youtube', 'warp'], default='gemini'); p.add_argument('--tunnels', type=int); p.add_argument('--port', type=int); p.add_argument('--http', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--target-gbps', type=float, default=0)
    p = subs.add_parser('capacity'); p.add_argument('--gbps', type=float, required=True); p.add_argument('--max-tunnels', type=int)
    p = subs.add_parser('templates'); p.add_argument('choice', choices=['both', 'gemini', 'youtube']); p.add_argument('--yes', action='store_true')
    p = subs.add_parser('mark-template'); p.add_argument('numbers')
    p = subs.add_parser('browser'); p.add_argument('state', choices=['on', 'off'])
    p = subs.add_parser('xray-routing'); p.add_argument('--youtube-instance', type=int, required=True); p.add_argument('--gemini-instance', type=int, required=True)
    p = subs.add_parser('update'); p.add_argument('source'); p.add_argument('--sha256'); p.add_argument('--rebuild', action='store_true')
    p = subs.add_parser('uninstall'); p.add_argument('--purge-profiles', action='store_true'); p.add_argument('--purge-deps', action='store_true'); p.add_argument('--yes', action='store_true')
    args = parser.parse_args(rest); action = args.action or 'menu'
    if selected.instance is not None and not 1 <= selected.instance <= 99:
        parser.error('номер экземпляра: 1–99')
    if selected.root and selected.all:
        parser.error('--root и --all нельзя сочетать')
    if selected.root and (action not in ('status', 'config', 'xray', 'logs') or
                          (action == 'config' and args.value is not None)):
        parser.error('--root предназначен только для чтения; изменения выбираются через --instance')
    if selected.all and action not in ('status', 'logs', 'config', 'xray', 'update'):
        parser.error('--all разрешён только для status/logs/config/xray/update')
    if selected.all and action == 'config' and args.key:
        parser.error('--all config выводит настройки и не изменяет их')
    numbers = profiles.discover()
    number = selected.instance or (numbers[0] if numbers else 1)
    root = selected.root.resolve() if selected.root else profiles.directory(number)
    try:
        if action in ('menu', 'easy', 'advanced'):
            import warp_menu
            if action == 'menu':
                warp_menu.menu(root)
            else:
                warp_menu.edit(root, action)
            return
        targets = numbers if selected.all else [number]
        if not selected.all and selected.instance is None and selected.root is None and len(numbers) > 1 and sys.stdin.isatty() and action in ('status', 'logs', 'config', 'xray', 'check', 'speed'):
            answer = input('Экземпляр или a — все: ').strip().lower()
            targets = numbers if answer == 'a' else [int(answer)]
        roots = [root] if selected.root else [profiles.directory(n) for n in targets]
        if action == 'status':
            if args.json:
                rows = [status_data(p) for p in roots]
                print(json.dumps(rows[0] if len(rows) == 1 else {'instances': rows}, ensure_ascii=False, indent=2))
            else:
                for p in roots:
                    status(p)
        elif action == 'config':
            if args.key and args.value is not None:
                set_config(root, args.key, args.value)
            else:
                rows = [read_config(p / 'config.json') for p in roots]
                data = rows[0].get(args.key) if args.key else (rows[0] if len(rows) == 1 else {'instances': rows})
                print(json.dumps(data, ensure_ascii=False, indent=2))
        elif action in ('check', 'search', 'speed', 'rotate'):
            for p in roots:
                request(p, 'check' if action == 'search' else action, args.node, getattr(args, 'method', None), args.full or action == 'search')
        elif action in ('start', 'stop', 'restart'):
            service(root, action)
        elif action == 'autostart':
            run('systemctl', 'enable' if args.state == 'on' else 'disable', profiles.unit(number))
        elif action == 'xray':
            rows = [profiles.outbound(read_config(p / 'config.json')) for p in roots]
            print(json.dumps(rows[0] if len(rows) == 1 else {'outbounds': rows}, indent=2))
        elif action == 'xray-routing':
            print(json.dumps(profiles.routing(read_config(profiles.directory(args.youtube_instance) / 'config.json'), read_config(profiles.directory(args.gemini_instance) / 'config.json')), indent=2))
        elif action == 'logs':
            units = [profiles.unit(n) for n in targets]
            run('journalctl', *(v for unit in units for v in ('-u', unit)), '-n', args.lines, *(['-f'] if args.follow else ['--no-pager']))
        elif action == 'browser':
            config = read_config(root / 'config.json')
            if args.state == 'off':
                config['impersonate_dir'] = ''
            apply_config(root, config, args.state == 'on')
        elif action == 'capacity':
            config = read_config(root / 'config.json')
            count = args.max_tunnels if args.max_tunnels is not None else (len(config['nodes']) if config['target_gbps'] else 64)
            if args.gbps == 0 and args.max_tunnels is None:
                raise ValueError('для отключения автопула задайте число фиксированных туннелей: --max-tunnels 3')
            config = profiles.resize(config, count)
            config['target_gbps'] = args.gbps
            apply_config(root, validate_config(config))
        elif action == 'create':
            if selected.instance is None:
                number = next((n for n in range(1, 100) if n not in numbers), None)
            config = profiles.defaults(number, args.role, args.tunnels if args.tunnels is not None else (64 if args.target_gbps else 3))
            config['target_gbps'] = args.target_gbps
            if args.port is not None:
                config['listen_port'] = args.port
            config['http_port'] = config['listen_port'] + 1 if args.http else None
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'config.json'; atomic_json(path, validate_config(config))
                run('bash', ROOT / 'install.sh', '--instance', number, '--config', path)
        elif action == 'templates':
            plan = profiles.template_plan(args.choice)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            if args.yes:
                execute_plan(plan)
        elif action == 'mark-template':
            profiles.mark_template([int(n) for n in args.numbers.split(',')])
        elif action == 'update':
            result = []
            with release(args.source, args.sha256) as source:
                for target in targets:
                    try:
                        run('bash', source / 'install.sh', '--instance', target, '--update', *(['--rebuild'] if args.rebuild else []))
                        result.append({'instance': target, 'success': True})
                    except subprocess.CalledProcessError as exc:
                        result.append({'instance': target, 'success': False, 'exit_code': exc.returncode})
            print(json.dumps(result, indent=2))
            if any(not r['success'] for r in result):
                raise ValueError('обновление выполнено частично; см. результаты выше')
        elif action == 'uninstall':
            if not args.yes and input(f'Удалить экземпляр {number}? Введите DELETE: ') != 'DELETE':
                return
            run('bash', ROOT / 'uninstall.sh', '--instance', number,
                *(['--purge-profiles'] if args.purge_profiles else []), *(['--purge-deps'] if args.purge_deps else []))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Ошибка: {exc}\n')
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == '__main__':
    main()
