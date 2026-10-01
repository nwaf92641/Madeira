#!/usr/bin/env python3
"""Build app/Madeira/compat.json, Madeira's game compatibility database.

The database is a single JSON document with two halves:

* "dependencies" -- the Windows components a game can need (Visual C++
  runtimes, DirectX, Media Foundation, XAudio/XACT, PhysX, OpenAL, .NET,
  fonts, ...) and, for each, exactly how it is satisfied on Madeira's
  iOS/Wine stack. This half is hand-written in compat/dependencies.json.

* "games" -- per-title profiles: which dependencies a title needs, its DLL
  overrides, registry keys, environment variables, Windows version and launch
  arguments, plus known fixes and issues. Curated profiles live in
  compat/games.json; the bulk come from the Protonfixes game scripts, which
  are imported here (see --protonfixes).

Usage
    build/tools/gen-game-compat.py                       # rebuild from compat/
    build/tools/gen-game-compat.py --protonfixes DIR     # import Protonfixes too
    build/tools/gen-game-compat.py --check               # verify without writing

The output is consumed by app/Madeira/GameCompat.swift. Run from anywhere;
only the standard library is used.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPAT = ROOT / 'compat'
OUT = ROOT / 'app/Madeira/compat.json'

# Wine DLL override orders (Protonfixes OverrideOrder -> Wine's one-letter form).
OVERRIDE_ORDERS = {
    'DISABLED': '',
    'NATIVE': 'n',
    'BUILTIN': 'b',
    'NATIVE_BUILTIN': 'n,b',
    'BUILTIN_NATIVE': 'b,n',
}

# Winetricks Windows-version verbs -> the version name Madeira stores.
WINDOWS_VERSIONS = {
    'win7', 'win10', 'win11', 'winxp', 'winxp64', 'win2k', 'win8', 'win81',
    'vista', 'win2003', 'win2008', 'win2008r2', 'win20', 'win2k3', 'win95',
    'win98', 'winme', 'win31', 'win30', 'nt40', 'nt351',
}

# Winetricks verbs that are settings, not components.
SETTINGS_VERBS = {'sound=alsa', 'sound=pulse', 'hidewineexports=enable'}

# A game fix name recorded as a note rather than applied: these are logic the
# compatibility database cannot express as data (they inspect files, patch
# games or replace the command line), so Madeira surfaces them as known fixes.
LOGIC_FIXES = {
    'disable_esync': 'disable-esync',
    'disable_fsync': 'disable-fsync',
    'disable_ntsync': 'disable-ntsync',
    'disable_nvapi': 'disable-nvapi',
    'patch_libcuda': 'patch-libcuda',
    'import_saves_folder': 'import-saves',
    'install_eac_runtime': 'eac',
    'install_battleye_runtime': 'battleye',
    'set_cpu_topology_limit': 'cpu-limit',
    'set_cpu_topology': 'cpu-topology',
    'set_cpu_topology_nosmt': 'cpu-nosmt',
    'create_dos_device': 'dos-device',
    'create_dosbox_conf': 'dosbox-conf',
    'set_ini_options': 'ini-options',
    'set_xml_options': 'xml-options',
    'set_game_drive': 'game-drive',
    'set_dxvk_option': 'dxvk-option',
    'once': 'once',
}

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise SystemExit(f'{path}: {error}')


# ---------------------------------------------------------------------------
# Protonfixes import

STEAM_FILE = re.compile(r'^(\d{2,})\.py$')
DOCSTRING = re.compile(r'"""(.*?)"""', re.DOTALL)
PROTONTRICKS = re.compile(r'protontricks\(\s*[\'"]([^\'"]+)[\'"]')
WINE_OVERRIDE = re.compile(r'(?:winedll_override|wineexe_override)\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*(?:util\.)?OverrideOrder\.([A-Z_]+)')
SET_ENV = re.compile(r'set_environment\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]*)[\'"]')
DEL_ENV = re.compile(r'del_environment\(\s*[\'"]([^\'"]+)')
APPEND_ARG = re.compile(r'append_argument\(\s*[\'"]([^\'"]+)[\'"]')
REGEDIT = re.compile(r'regedit_add\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]*)[\'"]')
UTIL_CALL = re.compile(r'util\.([a-z_]+)\(')


def import_protonfixes(path: Path, dependencies: dict) -> list[dict]:
    """One profile per Protonfixes game script, Steam App ID and GOG slug."""
    games: dict[str, dict] = {}
    for category in sorted(path.glob('gamefixes-*')):
        store = category.name.split('-', 1)[1]
        for script in sorted(category.glob('*.py')):
            if script.name.startswith('__'):
                continue
            stem = script.stem
            key = f'appid:{stem}' if STEAM_FILE.match(script.name) else f'{store}:{stem}'
            if not STEAM_FILE.match(script.name) and store != 'gog':
                # Only Steam App IDs and GOG slugs key a launch that Madeira sees.
                continue
            text = script.read_text(encoding='utf-8', errors='replace')
            title = ''
            match = DOCSTRING.search(text)
            if match:
                title = match.group(1).strip().splitlines()[0].strip()
            entry: dict = {'title': title or stem, 'source': f'protonfixes:{store}/{stem}'}
            if re.search(r'^\s*if\b', text, re.MULTILINE) or text.count('def ') > 1:
                entry['conditional'] = True
            if STEAM_FILE.match(script.name):
                entry['appid'] = int(stem)
            elif store == 'gog':
                entry['gog_slug'] = stem

            deps: list[str] = []
            env: dict[str, str] = {}
            overrides: dict[str, str] = {}
            registry: list[dict] = []
            arguments: list[str] = []
            fixes: list[str] = []

            for verb in PROTONTRICKS.findall(text):
                if verb in dependencies:
                    if verb not in deps:
                        deps.append(verb)
                elif verb in WINDOWS_VERSIONS:
                    entry['windows_version'] = verb
                elif verb in SETTINGS_VERBS:
                    pass
                else:
                    fixes.append(f'winetricks:{verb}')
            for dll, order in WINE_OVERRIDE.findall(text):
                if order in OVERRIDE_ORDERS:
                    overrides[dll.lower()] = OVERRIDE_ORDERS[order]
            for name, value in SET_ENV.findall(text):
                # Proton/SteamDeck-only switches are meaningless here.
                if name.startswith('PROTON_') or name in {'SteamDeck', 'PULSE_LATENCY_MSEC',
                                                          'AMD_DEBUG', 'radeonsi_disable_sam',
                                                          'vk_x11_override_min_image_count',
                                                          'ENABLE_GAMESCOPE_WSI', '__GL_13ebad'}:
                    continue
                env[name] = value
            for name in DEL_ENV.findall(text):
                env.setdefault(name, '')
            for argument in APPEND_ARG.findall(text):
                if argument and argument not in arguments:
                    arguments.append(argument)
            for folder, name, typ, value in REGEDIT.findall(text):
                registry.append({'hive': 'HKCU' if folder.upper().startswith('HKCU') else ('HKLM' if folder.upper().startswith('HKLM') else 'HKCU'),
                                 'key': re.sub(r'^(?:HKEY_CURRENT_USER|HKEY_LOCAL_MACHINE|HKCU|HKLM)\\\\?', '', folder.replace('/', '\\')),
                                 'name': name, 'type': typ, 'value': value})
            for call in set(UTIL_CALL.findall(text)):
                if call in LOGIC_FIXES:
                    fixes.append(LOGIC_FIXES[call])
                elif call in {'winedll_override', 'wineexe_override', 'set_environment',
                              'del_environment', 'append_argument', 'regedit_add', 'protontricks',
                              'replace_command', 'set_app_winver', 'set_winver', 'get_resolution',
                              'checkinstalled', 'is_custom_verb', 'wineexe_override'}:
                    continue
                elif call not in {'main'}:
                    fixes.append(f'call:{call}')
            if deps:
                entry['dependencies'] = deps
            if overrides:
                entry['dll_overrides'] = overrides
            if registry:
                entry['registry'] = registry
            if env:
                entry['env'] = env
            if arguments:
                entry['launch_arguments'] = ' '.join(arguments)
            if fixes:
                entry['fixes'] = sorted(set(fixes))
            previous = games.get(key)
            if previous is None:
                games[key] = entry
            else:
                # Merge duplicates (should not happen, but be deterministic).
                for field in ('dependencies', 'fixes'):
                    if field in entry:
                        previous[field] = sorted(set(previous.get(field, []) + entry[field]))
                for field in ('dll_overrides', 'env'):
                    if field in entry:
                        previous.setdefault(field, {}).update(entry[field])
                previous.setdefault('registry', []).extend(entry.get('registry', []))
                if 'registry' in previous and not previous['registry']:
                    del previous['registry']
    return list(games.values())


# ---------------------------------------------------------------------------
# Validation and output

def validate(database: dict) -> None:
    dependencies = database.get('dependencies', {})
    for dep_id, dep in dependencies.items():
        support = dep.get('support')
        if support not in {'builtin', 'override', 'payload', 'manual', 'unsupported', 'partial'}:
            fail(f'dependency {dep_id}: bad support {support!r}')
        for token in (dep.get('dll_overrides') or {}).values():
            if token not in {'', 'n', 'b', 'n,b', 'b,n'}:
                fail(f'dependency {dep_id}: bad override token {token!r}')
        for requires in dep.get('requires', []):
            if requires not in dependencies:
                fail(f'dependency {dep_id}: requires unknown {requires}')
        for value in dep.get('registry', []):
            if value.get('type') not in {'REG_SZ', 'REG_DWORD', 'REG_MULTI_SZ', 'REG_BINARY'}:
                fail(f'dependency {dep_id}: bad registry type {value.get("type")!r}')

    seen: set[str] = set()
    for game in database.get('games', []):
        if not game.get('title'):
            fail('game without a title')
        key = game.get('appid') or game.get('gog_slug') or (game.get('executables') or ['?'])[0]
        if str(key) in seen:
            fail(f'duplicate game key {key!r}')
        seen.add(str(key))
        for dep_id in game.get('dependencies', []):
            if dep_id not in dependencies:
                fail(f'game {game.get("title")}: unknown dependency {dep_id!r}')
        for token in (game.get('dll_overrides') or {}).values():
            if token not in {'', 'n', 'b', 'n,b', 'b,n'}:
                fail(f'game {game.get("title")}: bad override token {token!r}')
        if game.get('rating') and game['rating'] not in {'perfect', 'playable', 'runnable', 'broken', 'unknown'}:
            fail(f'game {game.get("title")}: bad rating {game["rating"]!r}')


def build(protonfixes: Path | None) -> dict:
    dependencies = load(COMPAT / 'dependencies.json').get('dependencies', {})
    if not dependencies:
        raise SystemExit('compat/dependencies.json is empty')
    curated = load(COMPAT / 'games.json')
    games: list[dict] = list(curated.get('games', []))
    if protonfixes:
        imported = import_protonfixes(protonfixes, dependencies)
        curated_keys = {str(g.get('appid')) for g in games if g.get('appid')}
        curated_keys |= {(g.get('executables') or [''])[0].lower() for g in games}
        for game in imported:
            key = str(game.get('appid')) if game.get('appid') else (game.get('gog_slug') or '').lower()
            exe = (game.get('executables') or [''])[0].lower()
            if key in curated_keys or (exe and exe in curated_keys):
                continue
            games.append(game)
    database = {
        'schema': 1,
        'updated': curated.get('updated', '2026-10-01'),
        'generated_by': 'build/tools/gen-game-compat.py',
        'dependencies': dependencies,
        'games': games,
    }
    validate(database)
    return database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--protonfixes', type=Path, help='Protonfixes checkout to import game fixes from')
    parser.add_argument('--check', action='store_true', help='validate and compare without writing')
    args = parser.parse_args()

    database = build(args.protonfixes)
    text = json.dumps(database, indent=1, sort_keys=False, ensure_ascii=False) + '\n'
    if args.check:
        if OUT.exists() and OUT.read_text(encoding='utf-8') == text:
            print(f'PASS: compat.json is up to date ({len(database["dependencies"])} dependencies, '
                  f'{len(database["games"])} games)')
            return 1 if failures else 0
        print('FAIL: app/Madeira/compat.json is stale; run gen-game-compat.py')
        return 1
    OUT.write_text(text, encoding='utf-8')
    for failure in failures:
        print('FAIL: ' + failure, file=sys.stderr)
    print(f'wrote {OUT.relative_to(ROOT)}: {len(database["dependencies"])} dependencies, '
          f'{len(database["games"])} games, {len(text)} bytes')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
