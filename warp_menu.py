"""Russian Easy/Advanced console. SPDX-License-Identifier: GPL-3.0-only."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile

from warp_pool import ROOT, VERSION, read_config, validate_config, atomic_json
import warp_profiles as profiles
from warp_ctl import status, request, convert, apply_config, execute_plan, run, service, unit_state

EASY = [('role', 'Режим', 'gemini/youtube/warp'),
        ('connection_type', 'Формат подключения', 'xray/socks5'),
        ('listen_port', 'SOCKS-порт', '1024–65534'),
        ('target_gbps', 'Канал VPS / цель автопула, Гбит/с', '0 — фиксированный пул; например 0.1, 1, 10'),
        ('publish_http', 'HTTP-прокси', 'bool'),
        ('interval_minutes', 'Watchdog, минуты', 'от 1; например 1.5'),
        ('tunnels', 'Туннелей / лимит автопула вместе с резервом', '1–128')]
ADVANCED = EASY + [
    ('listen_host', 'Адрес привязки', 'loopback или частный IPv4'),
    ('deny_regions', 'Запрещённые страны Google', 'RU,BY; - очищает'),
    ('allow_regions', 'Допустимые страны Google', 'пусто — любые; - очищает'),
    ('gemini_interval', 'Полная проверка Gemini, секунд', 'секунды'),
    ('min_speed_kib', 'Минимальная скорость, KiB/s', '0 отключает'),
    ('probe_mb', 'Объём пробы, MiB', '0–10; 0 — страница YouTube'),
    ('speed_interval', 'Интервал проб скорости, секунд', 'секунды'),
    ('slow_threshold', 'Низких измерений до ротации', '2–20'),
    ('fail_threshold', 'Сбоев транспорта до восстановления', '2–20'),
    ('bad_threshold', 'Отказов сервиса до ротации', '2–20'),
    ('wait_timeout', 'Ожидание запуска туннеля, секунд', '20–300'),
    ('rotation_delay', 'Начальная пауза ротации, секунд', 'секунды'),
    ('max_backoff', 'Максимальная пауза, секунд', 'секунды'),
    ('max_rotations_per_hour', 'Ротаций в час на туннель', '1–120'),
    ('bad_ip_ttl', 'Хранить отклонённый IP, секунд', 'секунды'),
    ('impersonate', 'Браузерный TLS-профиль Gemini', 'bool'),
    ('selection_strategy', 'Выбор выхода', 'failover/least_connections')]
ADVANCED += [
    ('capacity_seconds', 'Длительность замера автопула, секунд', '2–30'),
    ('capacity_interval', 'Повторный замер автопула, секунд', '60–86400'),
    ('capacity_sample_ttl', 'Срок актуальности замера, секунд', 'не меньше двух интервалов'),
    ('capacity_parallel_search', 'Одновременно ищущих выходов', '1–8'),
    ('capacity_url', 'HTTPS-файл для замера', 'HTTPS URL')]


def value(config, key):
    if key == 'role':
        return profiles.role(config).lower()
    if key == 'publish_http':
        return config['http_port'] is not None
    if key == 'tunnels':
        return len(config['nodes'])
    if key == 'interval_minutes':
        return config['check_interval'] / 60
    if key == 'impersonate':
        return bool(config['impersonate_dir'])
    return config[key]


def change(config, key, text):
    result = copy.deepcopy(config)
    if not text:
        return result, False
    old = value(config, key); new = convert(old, text)
    browser = False
    if key == 'role':
        if new not in ('gemini', 'youtube', 'warp'):
            raise ValueError('режим: gemini/youtube/warp')
        result.update(youtube_mode=new == 'youtube', gemini_check=new == 'gemini')
        if new == 'youtube':
            result['listen_host'] = '127.0.0.1'
    elif key == 'publish_http':
        result['http_port'] = result['listen_port'] + 1 if new else None
    elif key == 'tunnels':
        result = profiles.resize(result, new)
    elif key == 'interval_minutes':
        seconds = float(new) * 60
        if not seconds.is_integer() or seconds < 60:
            raise ValueError('минимум 1 минута и целое число секунд')
        result.update(check_interval=int(seconds), health_ttl=max(result['health_ttl'], seconds + 300))
    elif key == 'impersonate':
        if new:
            browser = True
        else:
            result['impersonate_dir'] = ''
    else:
        result[key] = new
        if key == 'target_gbps' and new > 0 and old == 0:
            result = profiles.resize(result, max(64, len(result['nodes'])))
        elif key == 'target_gbps' and new == 0 and old > 0:
            result = profiles.resize(result, 3)
        if key == 'listen_port' and result['http_port'] is not None:
            result['http_port'] = new + 1
    return validate_config(result), browser


def confirm(message):
    return input(message + ' [y/N]: ').strip().lower() in ('y', 'yes', '1', 'да')


def edit_draft(config, mode='easy'):
    draft = copy.deepcopy(config); browser = False
    fields = ADVANCED if mode == 'advanced' else EASY
    if draft['youtube_mode'] and mode == 'easy':
        fields = fields + [('min_speed_kib', 'Минимальная скорость, KiB/s', '0 отключает')]
    while True:
        print(f"\n{mode.title()} · экземпляр {draft['instance']} · {profiles.role(draft)}")
        for i, (key, label, help_text) in enumerate(fields, 1):
            val = 'включён (после применения)' if key == 'impersonate' and browser else value(draft, key)
            print(f'{i}. {label}: {val}')
        print('y. Просмотреть и применить   0. Отменить')
        answer = input('> ').strip().lower()
        if answer == '0':
            return None, False
        if answer == 'y':
            print(json.dumps(draft, ensure_ascii=False, indent=2))
            if browser:
                print('Будет установлен проверенный по SHA-256 curl-impersonate.')
            if confirm('Применить эти настройки?'):
                draft['menu_mode'] = mode
                return draft, browser
            continue
        try:
            index = int(answer) - 1
            if not 0 <= index < len(fields):
                raise ValueError('выберите номер строки')
            key, label, help_text = fields[index]
            if key == 'selection_strategy':
                print('least_connections распределяет новые TCP-соединения; их IP могут различаться. failover сохраняет один рабочий выход.')
            if help_text == 'bool':
                help_text = '1 — включить, 0 — выключить'
            text = input(f'{label} ({help_text}; Enter — оставить): ').strip()
            draft, enable = change(draft, key, text)
            if key == 'impersonate' and text:
                browser = enable
        except (ValueError, TypeError) as exc:
            print('Ошибка:', exc)


def edit(root, mode='easy'):
    config = read_config(root / 'config.json')
    draft, browser = edit_draft(config, mode)
    if draft:
        if profiles.role(draft) != profiles.role(config):
            if confirm('Создать новый независимый экземпляр для нового режима?'):
                free = next(n for n in range(1, 100) if n not in profiles.discover())
                fresh = profiles.fork_config(draft, free)
                with tempfile.TemporaryDirectory() as work:
                    path = Path(work) / 'config.json'; atomic_json(path, fresh)
                    run('bash', ROOT / 'install.sh', '--instance', free, '--config', path, *(['--impersonate'] if browser else []))
                return
            if not confirm('Заменить режим текущего экземпляра?'):
                return
        apply_config(root, draft, browser)


def targets():
    numbers = profiles.discover()
    answer = input('Экземпляр или a — все: ').strip().lower()
    result = numbers if answer == 'a' else [int(answer)]
    if any(n not in numbers for n in result):
        raise ValueError('экземпляр не установлен')
    return result


def templates():
    print('1 Gemini: да / YouTube: да\n2 Gemini: да / YouTube: нет\n3 Gemini: нет / YouTube: да\ng Отметить существующие экземпляры как шаблон\n0 Назад')
    answer = input('> ').strip().lower()
    if answer == '0':
        return
    if answer == 'g':
        numbers = [int(v) for v in input('Номера через запятую: ').split(',')]
        profiles.mark_template(numbers); return
    choice = {'1': 'both', '2': 'gemini', '3': 'youtube'}[answer]
    plan = profiles.template_plan(choice)
    browser_ids = set()
    print('Будут созданы новые независимые экземпляры:')
    for config in plan:
        print(config['instance'], profiles.role(config), 'SOCKS', config['listen_port'], 'туннелей', len(config['nodes']))
    if confirm('Изменить настройки перед установкой?'):
        for index, config in enumerate(plan):
            edited, browser = edit_draft(config, 'advanced')
            if edited is None:
                return
            if browser:
                browser_ids.add(edited['instance'])
            plan[index] = edited
    profiles.validate_plan(plan)
    if confirm('Установить выбранный шаблон?'):
        execute_plan(plan, browser_ids=browser_ids)


def overview():
    numbers = profiles.discover()
    for template in (True, False):
        group = [n for n in numbers if bool(profiles.origin(profiles.directory(n))) == template]
        if group:
            print('\nШаблоны' if template else '\nВаши экземпляры')
        for number in group:
            try:
                status(profiles.directory(number))
            except (ValueError, OSError) as exc:
                print(f'Экземпляр {number}: ошибка чтения: {exc}')


def menu(root):
    selected = read_config(root / 'config.json')['instance'] if (root / 'config.json').exists() else 1
    while True:
        print('\nWarp-Manager', VERSION)
        overview()
        print(f'Выбран для изменения: {selected}')
        print('1 Easy  2 Advanced  3 Операции  4 Выбрать экземпляр  5 Обновить')
        print('t Шаблоны  c Создать экземпляр  r Обновить обзор  0 Выход')
        try:
            answer = input('> ').strip().lower()
            if answer in ('0', 'q', 'exit'):
                return
            if answer == 'r' or (answer == '' and profiles.discover()):
                continue
            if answer == 't' or (answer == '' and not profiles.discover()):
                templates(); continue
            if answer == '4':
                selected = int(input('Номер экземпляра: '))
                read_config(profiles.directory(selected) / 'config.json'); continue
            current = profiles.directory(selected)
            if answer in ('1', '2'):
                edit(current, 'easy' if answer == '1' else 'advanced')
            elif answer == 'c':
                free = next(n for n in range(1, 100) if n not in profiles.discover())
                purpose = input('Роль gemini/youtube/warp [gemini]: ').strip() or 'gemini'
                draft, browser = edit_draft(profiles.defaults(free, purpose), 'advanced')
                if draft:
                    with tempfile.TemporaryDirectory() as work:
                        path = Path(work) / 'config.json'; atomic_json(path, draft)
                        run('bash', ROOT / 'install.sh', '--instance', free, '--config', path, *(['--impersonate'] if browser else []))
                    selected = free
            elif answer == '5':
                source = input('Каталог или ZIP нового релиза: ').strip()
                scope = targets()
                for n in scope:
                    run('python3', ROOT / 'warp_ctl.py', '--instance', n, 'update', source)
            elif answer == '3':
                print('status config logs xray check speed rotate start stop restart autostart browser xray-routing uninstall')
                action = input('Операция: ').strip()
                if action in ('status', 'config', 'logs', 'xray', 'check', 'speed'):
                    scope = targets()
                    for n in scope:
                        run('python3', ROOT / 'warp_ctl.py', '--instance', n, action)
                elif action == 'rotate':
                    request(current, 'rotate', input('Имя туннеля: ').strip())
                elif action in ('start', 'stop', 'restart'):
                    service(current, action)
                elif action in ('autostart', 'browser'):
                    value = input('on/off: ').strip()
                    run('python3', ROOT / 'warp_ctl.py', '--instance', selected, action, value)
                elif action == 'xray-routing':
                    yt = input('Номер YouTube: ').strip(); gm = input('Номер Gemini: ').strip()
                    run('python3', ROOT / 'warp_ctl.py', 'xray-routing', '--youtube-instance', yt, '--gemini-instance', gm)
                elif action == 'uninstall':
                    run('python3', ROOT / 'warp_ctl.py', '--instance', selected, 'uninstall')
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
            print('Ошибка:', exc)
