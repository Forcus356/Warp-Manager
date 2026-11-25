"""Instance layout, templates and connection exports. SPDX-License-Identifier: GPL-3.0-only."""
import contextlib
import ipaddress
import json
from pathlib import Path
import socket
import uuid
import re

from warp_pool import validate_config, read_config, atomic_json

OPT = Path('/opt')


def directory(number, base=None):
    if type(number) is not int or not 1 <= number <= 99:
        raise ValueError('номер экземпляра: 1–99')
    return (base or OPT) / ('warp-google-pool' + ('' if number == 1 else f'-{number}'))


def unit(number):
    return directory(number).name + '.service'


def discover(base=None):
    return [n for n in range(1, 100) if (directory(n, base) / 'config.json').is_file()]


def role(config):
    return 'YouTube' if config['youtube_mode'] else 'Gemini' if config['gemini_check'] else 'WARP'


def defaults(number=1, purpose='gemini', count=3):
    if purpose not in ('gemini', 'youtube', 'warp'):
        raise ValueError('роль: gemini, youtube, warp')
    directory(number)
    if type(count) is not int or not 1 <= count <= 128:
        raise ValueError('число туннелей: 1–128')
    prefix = 'wgp' if number == 1 else f'wgp{number}'
    port = 40010 + 2 * (number - 1)
    return validate_config({'instance': number, 'listen_port': port, 'http_port': port + 1,
                           'youtube_mode': purpose == 'youtube', 'gemini_check': purpose == 'gemini',
                           'min_speed_kib': 1500 if purpose == 'youtube' else 0,
                           'nodes': [{'name': f'warp{i}', 'container': f'{prefix}-warp{i}',
                                      'port': (41011 + 32 * (number - 1) + i - 1 if i <= 32
                                               else 45000 + 96 * (number - 1) + i - 33)}
                                     for i in range(1, count + 1)]})


def resize(config, count):
    generated = defaults(config['instance'], count=count)['nodes']
    old = config['nodes']
    config = dict(config, nodes=old[:count] + generated[len(old):count])
    retained = set(config.get('retained_volumes', [])) | {n['name'] for n in old[count:]}
    config['retained_volumes'] = sorted(retained)
    return validate_config(config)


def fork_config(config, number):
    """Keep the edited policy but allocate a fresh instance's ports/identities."""
    fresh = defaults(number, role(config).lower(), len(config['nodes']))
    structural = {'instance', 'nodes', 'retained_volumes', 'listen_port', 'http_port'}
    fresh.update({k: v for k, v in config.items() if k not in structural})
    fresh['http_port'] = fresh['listen_port'] + 1 if config['http_port'] is not None else None
    return validate_config(fresh)


def endpoints(config):
    rows = [(config['listen_host'], config['listen_port'])]
    if config['http_port'] is not None:
        rows.append((config['listen_host'], config['http_port']))
    return rows + [('127.0.0.1', n['port']) for n in config['nodes']]


def reservations(config, base=None):
    wanted = {p for _, p in endpoints(config)}
    for number in discover(base):
        if number == config['instance']:
            continue
        saved = read_config(directory(number, base) / 'config.json')
        overlap = wanted & {p for _, p in endpoints(saved)}
        if overlap:
            raise ValueError(f'порты заняты экземпляром {number}: {sorted(overlap)}')


@contextlib.contextmanager
def reserve_ports(config, previous=None):
    current = set(endpoints(previous)) if previous else set()
    sockets = []
    try:
        for host, port in endpoints(config):
            if (host, port) in current:
                continue
            sock = socket.socket(); sockets.append(sock)
            try:
                sock.bind((host, port))
            except OSError as exc:
                raise ValueError(f'нельзя привязать {host}:{port}: {exc}') from exc
        yield
    finally:
        for sock in sockets:
            sock.close()


def compose(config, root, image):
    retained = config.get('retained_volumes', [])
    if not isinstance(retained, list) or any(not isinstance(v, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', v) for v in retained):
        raise ValueError('invalid retained volume names')
    services = {}
    for index, node in enumerate(config['nodes']):
        services[node['name']] = {
            'image': image, 'container_name': node['container'], 'restart': 'unless-stopped',
            'cap_add': ['NET_ADMIN', 'MKNOD', 'AUDIT_WRITE'],
            'device_cgroup_rules': ['c 10:200 rwm'], 'environment': {'WARP_PROTOCOL': 'MASQUE'},
            'ports': [f"127.0.0.1:{node['port']}:1080"],
            'volumes': [f"{node['name']}:/var/lib/cloudflare-warp"],
            'logging': {'driver': 'json-file', 'options': {'max-size': '5m', 'max-file': '2'}}}
        if config['target_gbps'] and index:
            services[node['name']]['profiles'] = ['capacity']
    return {'name': directory(config['instance']).name, 'services': services,
            'volumes': {name: {} for name in sorted(set(retained) | set(services))}}


def outbound(config, tag=None):
    tag = tag or ('warp-google-pool' if config['instance'] == 1 else f"warp-google-pool-{config['instance']}")
    return {'tag': tag, 'protocol': 'socks', 'settings': {'servers': [
        {'address': config['listen_host'], 'port': config['listen_port']}]}}


def routing(youtube, gemini):
    if youtube['instance'] == gemini['instance'] or not youtube['youtube_mode'] or youtube['gemini_check'] or not gemini['gemini_check'] or gemini['youtube_mode']:
        raise ValueError('нужны разные экземпляры YouTube и Gemini с соответствующими режимами')
    if {p for _, p in endpoints(youtube)} & {p for _, p in endpoints(gemini)}:
        raise ValueError('порты экземпляров пересекаются')
    yt = ['domain:youtube.com', 'domain:youtu.be', 'domain:googlevideo.com', 'domain:ytimg.com', 'full:youtubei.googleapis.com']
    gm = ['domain:gemini.google.com', 'domain:aistudio.google.com', 'full:generativelanguage.googleapis.com']
    return {'outbounds': [outbound(youtube, 'warp-youtube'), outbound(gemini, 'warp-gemini'),
                           {'tag': 'warp-quic-block', 'protocol': 'blackhole', 'settings': {}}],
            'routing': {'domainStrategy': 'AsIs', 'rules': [
                {'type': 'field', 'network': 'udp', 'port': '443', 'domain': yt + gm, 'outboundTag': 'warp-quic-block'},
                {'type': 'field', 'network': 'tcp', 'domain': yt, 'outboundTag': 'warp-youtube'},
                {'type': 'field', 'network': 'tcp', 'domain': gm, 'outboundTag': 'warp-gemini'}]}}


def template_plan(choice, base=None):
    choices = {'both': ('gemini', 'youtube'), 'gemini': ('gemini',), 'youtube': ('youtube',)}
    if choice not in choices:
        raise ValueError('шаблон: both, gemini, youtube')
    used = set(discover(base)); reserved = set()
    for n in used:
        reserved.update(p for _, p in endpoints(read_config(directory(n, base) / 'config.json')))
    result = []
    for purpose in choices[choice]:
        number = next((n for n in range(1, 100) if n not in used), None)
        if number is None:
            raise ValueError('нет свободного номера экземпляра')
        config = defaults(number, purpose, 1 if purpose == 'youtube' else 3)
        for port in range(40010, 41000, 2):
            if {port, port + 1} & reserved:
                continue
            trial = dict(config, listen_port=port, http_port=port + 1)
            try:
                reservations(trial, base)
                with reserve_ports(trial):
                    config = trial
                break
            except ValueError:
                continue
        else:
            raise ValueError('нет свободной пары SOCKS/HTTP портов')
        result.append(config); used.add(number)
        reserved.update(p for _, p in endpoints(config))
    return result


def mark_template(numbers, base=None, group=None):
    group = group or uuid.uuid4().hex
    for number in numbers:
        read_config(directory(number, base) / 'config.json')
    for number in numbers:
        path = directory(number, base)
        config = read_config(path / 'config.json')
        atomic_json(path / 'origin.json', {'source': 'template', 'group': group, 'role': role(config)})


def validate_plan(plan, base=None):
    from contextlib import ExitStack
    used = set(discover(base)); numbers = set(); ports = set()
    with ExitStack() as stack:
        for config in plan:
            validate_config(config)
            if config['instance'] in used | numbers:
                raise ValueError('шаблон создаёт только новые экземпляры')
            wanted = {p for _, p in endpoints(config)}
            if ports & wanted:
                raise ValueError('порты шаблона пересекаются')
            reservations(config, base)
            stack.enter_context(reserve_ports(config))
            numbers.add(config['instance']); ports.update(wanted)


def origin(root):
    try:
        data = json.loads((root / 'origin.json').read_text())
        return data if data.get('source') == 'template' and isinstance(data.get('group'), str) else {}
    except (ValueError, OSError, AttributeError):
        return {}
