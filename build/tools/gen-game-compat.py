#!/usr/bin/env python3
"""Build app/Madeira/compat.json, Madeira's game compatibility database.

The database is one JSON document with three halves:

* "dependencies" -- the Windows components a game can need (Visual C++
  runtimes, DirectX, Media Foundation, XAudio/XACT, PhysX, OpenAL, .NET,
  MSXML, fonts, ...) and, for each, exactly how it is satisfied on Madeira's
  iOS/Wine stack. Hand-written in compat/dependencies.json, then widened from
  Winetricks' verb metadata and from Bottles' dependency definitions.

* "recipes" -- reusable fixes (a DLL override, a registry value, an
  environment variable, an argument or a component) referenced by name from a
  profile or a dependency, so the same remedy is written once.

* "games" -- per-title profiles. Curated profiles live in compat/games.json;
  the bulk come from the Protonfixes game scripts (see --protonfixes), with
  every fix that Madeira cannot express classified rather than dropped.

Usage
    build/tools/gen-game-compat.py --protonfixes DIR --winetricks DIR --bottles DIR
    build/tools/gen-game-compat.py --check

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

# Winetricks spells the same thing out.
WINETRICKS_ORDERS = {
    'native,builtin': 'n,b',
    'builtin,native': 'b,n',
    'native': 'n',
    'builtin': 'b',
    'disabled': '',
}

# Winetricks Windows-version verbs -> the version name Madeira stores.
WINDOWS_VERSIONS = {
    'win7', 'win10', 'win11', 'winxp', 'winxp64', 'win2k', 'win8', 'win81',
    'vista', 'win2003', 'win2008', 'win2008r2', 'win20', 'win2k3', 'win95',
    'win98', 'winme', 'win31', 'win30', 'nt40', 'nt351',
}

# Winetricks verbs that are settings, not components.
SETTINGS_VERBS = {'sound=alsa', 'sound=pulse', 'hidewineexports=enable'}

# Protonfixes verbs that mean a catalogue entry under another name.
VERB_ALIASES = {
    'cnc_ddraw': 'cnc-ddraw',
    'dmusic': 'directmusic',
    'xact_x64': 'xact',
    'ucrtbase2019': 'ucrtbase2019',
    'fakejapanese_ipamona': 'fakejapanese',
    'ie8': 'wininet',
}

# Protonfixes helpers with no equivalent on Madeira: a Linux kernel feature, an
# NVIDIA-only API, a Vulkan path, a Linux desktop service, an installer or a
# launch-command rewrite Madeira has no wrapper for. They are recorded per title
# so the compatibility report is honest, never silently dropped.
UNAVAILABLE_FIXES = {
    'disable_esync': 'esync (Linux only)',
    'disable_fsync': 'fsync (Linux only)',
    'disable_ntsync': 'ntsync (Linux only)',
    'disable_nvapi': 'NVAPI (NVIDIA only)',
    'patch_libcuda': 'libcuda (NVIDIA only)',
    'set_dxvk_option': 'DXVK (Vulkan; Madeira uses DXMT)',
    'import_saves_folder': 'imports saves from a Steam Proton prefix',
    'set_cpu_topology': 'CPU topology pinning (Linux)',
    'set_cpu_topology_limit': 'CPU topology pinning (Linux)',
    'set_cpu_topology_nosmt': 'CPU topology pinning (Linux)',
    'create_dosbox_conf': 'DOSBox configuration',
    'create_dos_device': 'DOS device mapping',
    'set_game_drive': 'Drive mapping',
    'set_ini_options': 'edits a game INI',
    'set_xml_options': 'edits a game XML file',
    'install_eac_runtime': 'Easy Anti-Cheat runtime (unsupported on iOS)',
    'install_battleye_runtime': 'BattlEye runtime (unsupported on iOS)',
    'install_from_zip': 'runs an installer',
    'install_all_from_tgz': 'runs an installer',
    'copytree': 'copies files into the prefix',
    'rmtree': 'deletes files in the prefix',
    'move': 'moves files in the prefix',
    'copy': 'copies files in the prefix',
    'get_resolution': 'reads the display mode',
    'get_steam_account_id': 'reads the Steam account',
    'get_game_install_path': 'reads the install path',
}

# A launch argument that asks the game for a renderer Madeira does not have.
# Proton translates Direct3D through Vulkan, so an upstream fix often selects a
# Vulkan device; on Madeira that device cannot be created, so the argument is
# dropped and recorded. "-fullscreen -vulkan", for instance, keeps -fullscreen.
VULKAN_ARGUMENTS = {
    '-vulkan': 'Vulkan renderer',
    '--vulkan': 'Vulkan renderer',
    '-vk': 'Vulkan renderer',
    '-force-vulkan': 'Vulkan renderer',
    '+r_renderapi': 'Vulkan renderer (id Tech r_renderAPI)',
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


def slot(entry: dict, key: str, value, after: str | None = None) -> None:
    """Insert `key` into `entry` next to a related field, keeping the file readable."""
    if key in entry or value in (None, [], {}, ''):
        return
    if after and after in entry:
        items = list(entry.items())
        rebuilt = {}
        for k, v in items:
            rebuilt[k] = v
            if k == after:
                rebuilt[key] = value
        entry.clear(); entry.update(rebuilt)
    else:
        entry[key] = value


# ---------------------------------------------------------------------------
# Category inference

def category_for(name: str, dep: dict) -> str:
    """The failure class a component belongs to (see CompatDiagnosis.swift)."""
    n = name.lower()
    # "drm" as a word: "d3drm" is Direct3D Retained Mode, not copy protection.
    if re.search(r'denuvo|securom|safedisc|starforce|\bdrm\b|solidshield|protect', n):
        return 'drm'
    if dep.get('kind') == 'anticheat':
        return 'anticheat'
    if re.search(r'd3d12|dxil|d3d12', n):
        return 'dx12'
    if re.search(r'd3d11|d3dx11|dxgi', n):
        return 'dx11'
    if re.search(r'd3d9|d3d8|d3drm|ddraw|d3dx9|directx9|d3dcompiler|d3dxof|cnc|dgvoodoo|directplay|quartz|devenum|media', n):
        return 'dx9'
    if re.search(r'xinput|dinput|rawinput', n):
        return 'input'
    if dep.get('kind') == 'audio':
        return 'audio'
    if dep.get('kind') == 'media':
        return 'media'
    if re.search(r'wine|windows', n):
        return 'wine_fex'
    return {
        'vcredist': 'dependency', 'runtime': 'dependency', 'framework': 'dependency',
        'dotnet': 'dependency', 'physx': 'dependency', 'font': 'other',
    }.get(dep.get('kind'), 'dependency')


# ---------------------------------------------------------------------------
# Winetricks: verbs, the DLLs they provide, and the overrides they set

WINETRICKS_DLL_EXTENSIONS = ('.dll', '.ax', '.ocx', '.drv', '.vxd', '.acm', '.cpl')


def parse_winetricks(path: Path) -> dict[str, dict]:
    """Verb -> {verb, category, title, media, dlls, overrides, needs_installer}."""
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = text.split('\n')
    verbs: dict[str, dict] = {}
    index = 0
    while index < len(lines):
        match = re.match(r'w_metadata\s+(\S+)\s+(\S+)', lines[index])
        if not match:
            index += 1
            continue
        block = [lines[index]]
        while block[-1].rstrip().endswith('\\') and index + 1 < len(lines):
            index += 1
            block.append(lines[index])
        header = '\n'.join(block)
        verb, group = match.group(1), match.group(2)
        title = re.search(r'title="([^"]*)"', header)
        media = re.search(r'media="([^"]*)"', header)
        installed = [Path(value).name for value in re.findall(r'installed_file\d+="[^"]*?([^"/]+)"', header)]
        # The load_<verb> function carries the DLL overrides.
        body = ''
        loader = re.search(rf'^load_{re.escape(verb)}\(\)\s*$', text, re.MULTILINE)
        if loader:
            rest = text[loader.end():]
            end = re.search(r'^\}', rest, re.MULTILINE)
            body = rest[:end.start()] if end else rest[:4000]
        overrides: dict[str, str] = {}
        for order, names in re.findall(r'w_override_dlls\s+([a-z,]+)\s+([^\n\\]+)', body):
            token = WINETRICKS_ORDERS.get(order.strip().lower())
            if token is None:
                continue
            for name in names.split():
                if name.startswith('-') or name.startswith('$'):
                    continue
                overrides[name.lower()] = token
        verbs[verb] = {
            'verb': verb, 'group': group,
            'title': title.group(1) if title else verb,
            'media': media.group(1) if media else '',
            'installed': [name for name in installed if name],
            'overrides': overrides,
            'needs_installer': bool(re.search(r'w_download|w_try_ms_installer|w_try_cabextract|w_try_unzip', body)),
        }
        index += 1
    return verbs


def import_winetricks(verbs: dict[str, dict], wanted: set[str]) -> dict[str, dict]:
    """Dependency entries for the verbs a game can reference.

    A verb that ships DLLs becomes a `payload` component: Madeira applies its
    overrides once the files are in the payload directory, and reports exactly
    which are missing otherwise. Skimmed from Winetricks so the override order
    and the DLL names are the ones the Wine community already validated.
    """
    generated: dict[str, dict] = {}
    for verb, info in sorted(verbs.items()):
        if info['group'] not in {'dlls', 'fonts'} and verb not in wanted:
            continue
        overrides = {name.lower(): token for name, token in info['overrides'].items()}
        provided = [name for name in overrides if not name.endswith(WINETRICKS_DLL_EXTENSIONS)]
        dll_names = [f'{name}.dll' for name in provided]
        for name in overrides:
            if name.endswith(WINETRICKS_DLL_EXTENSIONS):
                dll_names.append(name)
        payload = sorted(set(dll_names) | set(info['installed']))
        # DLL names from the title, e.g. "Visual C++ ... (concrt140.dll,...)".
        imports = sorted({name.lower() for name in overrides} |
                         {Path(m.group(1)).stem.lower() for m in re.finditer(r'([A-Za-z0-9_.\-]+\.dll)', info['title'])})
        entry: dict = {
            'title': info['title'][:120],
            'kind': 'font' if info['group'] == 'fonts' else 'component',
            'category': category_for(verb, {'kind': 'font' if info['group'] == 'fonts' else 'component'}),
            'support': 'payload' if payload else 'manual',
            'summary': ('Font files' if info['group'] == 'fonts' else 'Windows component') +
                       ' from Winetricks verb "' + verb + '".' +
                       (' Files come from the Winetricks download.' if info['needs_installer'] else ''),
            'imports': [name for name in imports if name],
            'dlls': payload,
            'source': f'winetricks:{verb}',
        }
        if overrides:
            entry['dll_overrides'] = overrides
        if not payload:
            entry.pop('dlls', None)
        generated[verb] = entry
    return generated


# ---------------------------------------------------------------------------
# Bottles: dependency definitions with their override bundles

def parse_bottles(path: Path) -> dict[str, dict]:
    """name -> {name, description, requires, overrides, windows_version, installs}."""
    deps: dict[str, dict] = {}
    current: dict | None = None
    action = ''
    in_dependencies = False
    bundle_dll: str | None = None

    def start(name: str, description: str) -> None:
        nonlocal current
        current = {'name': name, 'description': description, 'requires': [],
                   'overrides': {}, 'windows_version': None, 'installs': False}
        deps[name] = current

    for raw in path.read_text(encoding='utf-8', errors='replace').split('\n'):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not line[:1].isspace():
            key, _, value = stripped.partition(':')
            value = value.strip()
            in_dependencies = False
            bundle_dll = None
            if key == 'Name':
                start(value, '')
                continue
            if current is None:
                continue
            if key == 'Description':
                current['description'] = value
            elif key == 'Dependencies':
                in_dependencies = not value or value == '[]'
                if value and value != '[]':
                    current['requires'] += [item.strip() for item in value.strip('[]').split(',') if item.strip()]
            continue
        if current is None:
            continue
        if stripped.startswith('- '):
            item = stripped[2:].strip()
            if in_dependencies:
                current['requires'].append(item)
                continue
            key, _, value = item.partition(':')
            key, value = key.strip(), value.strip()
            if key == 'action':
                action = value
                bundle_dll = None
                if value in {'install_exe', 'archive_extract', 'install_msi', 'copy_dll', 'download'}:
                    current['installs'] = True
            elif key == 'value':
                if action == 'override_dll':
                    bundle_dll = value.lower()
            elif key == 'data' and bundle_dll:
                current['overrides'][bundle_dll] = WINETRICKS_ORDERS.get(value.replace(' ', ''), 'n,b')
            continue
        key, _, value = stripped.partition(':')
        key, value = key.strip(), value.strip()
        if key == 'action':
            action = value
            bundle_dll = None
            if value in {'install_exe', 'archive_extract', 'install_msi', 'copy_dll', 'download'}:
                current['installs'] = True
        elif action == 'override_dll' and key == 'dll':
            bundle_dll = value.lower()
        elif action == 'override_dll' and key == 'type' and bundle_dll:
            current['overrides'][bundle_dll] = WINETRICKS_ORDERS.get(value.lower(), 'n,b')
        elif action == 'set_windows' and key == 'version':
            current['windows_version'] = value.lower()
    return deps


def import_bottles(deps: dict[str, dict]) -> dict[str, dict]:
    generated: dict[str, dict] = {}
    for name, info in sorted(deps.items()):
        overrides = {dll: token for dll, token in info['overrides'].items() if token in {'n', 'b', 'n,b', 'b,n'}}
        entry: dict = {
            'title': (info['description'] or name)[:120],
            'kind': 'component',
            'category': category_for(name, {'kind': 'component'}),
            'support': 'payload' if (info['installs'] or overrides) else 'manual',
            'summary': (info['description'] or name) + ' (Bottles dependency).',
            'imports': sorted(overrides),
            'source': f'bottles:{name}',
        }
        if overrides:
            entry['dll_overrides'] = overrides
            entry['dlls'] = sorted(f'{dll}.dll' for dll in overrides)[:24]
        if info['requires']:
            entry['requires'] = [r.lower() for r in info['requires']]
        if info['windows_version']:
            entry['windows_version'] = info['windows_version']
        generated[name.lower()] = entry
    return generated


# ---------------------------------------------------------------------------
# Protonfixes import

STEAM_FILE = re.compile(r'^(\d{2,})\.py$')
UMU_FILE = re.compile(r'^umu-(\d{2,})\.py$')
DOCSTRING = re.compile(r'"""(.*?)"""', re.DOTALL)
PROTONTRICKS = re.compile(r'protontricks\(\s*[\'"]([^\'"]+)[\'"]')
WINE_OVERRIDE = re.compile(r'(?:winedll_override|wineexe_override)\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*(?:util\.)?OverrideOrder\.([A-Z_]+)')
SET_ENV = re.compile(r'set_environment\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]*)[\'"]')
DEL_ENV = re.compile(r'del_environment\(\s*[\'"]([^\'"]+)')
APPEND_ARG = re.compile(r'append_argument\(\s*[\'"]([^\'"]+)[\'"]')
REGEDIT_START = re.compile(r'regedit_add\(')
# replace_command(a, b): run b instead of a, or drop the argument a when b is
# empty. Forty-odd scripts switch a broken launcher for the game itself this
# way, which is exactly what a profile's "run" and "remove_arguments" express.
REPLACE_COMMAND = re.compile(r'replace_command\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]*)[\'"]')
# os.rename('a', 'b') and os.replace('a', 'b') with two literals: a file the
# title must not load (an old ddraw.dll beside the executable).
OS_RENAME = re.compile(r'os\.(?:rename|replace)\(\s*[\'"]([^\'"$*]+)[\'"]\s*,\s*[\'"]([^\'"$*]+)[\'"]')


def launch_rewrite(from_text: str, to_text: str) -> tuple[str | None, str | None]:
    """(run, removed argument) for one replace_command call.

    The upstream helper rewrites the command line: the first argument is what
    to look for, the second what to put in its place. A flag (leading "-") that
    is replaced with nothing is an argument the game must not receive; anything
    that names a Windows program is the program the profile should start
    instead of the launcher.
    """
    if to_text:
        # A replacement that names a program (an .exe, or a path) is a launch
        # target; a replacement of some other kind cannot be expressed.
        if to_text.lower().endswith('.exe') or '/' in to_text or '\\' in to_text:
            return to_text, None
        return None, None
    if from_text.startswith('-') or from_text.startswith('--'):
        return None, from_text
    if from_text.lower().endswith('.exe'):
        # "run this launcher" with no replacement: nothing to do.
        return None, None
    return None, from_text


def split_arguments(body: str) -> list[str]:
    """Split a call's arguments on top-level commas, dropping the quotes."""
    arguments: list[str] = []
    current = ''
    quote: str | None = None
    depth = 0
    for character in body:
        if quote:
            if character == quote:
                quote = None
            else:
                current += character
            continue
        if character in '\'"':
            quote = character
        elif character in '([{':
            depth += 1
            current += character
        elif character in ')]}':
            depth -= 1
            current += character
        elif character == ',' and depth == 0:
            arguments.append(current.strip())
            current = ''
        else:
            current += character
    if current.strip():
        arguments.append(current.strip())
    return arguments


def _is_literal(argument: str) -> bool:
    """A registry argument that can be read statically, not built at runtime."""
    return not any(marker in argument for marker in ('(', 'f"', "f'", '%', '$', '+', '['))


def parse_registry(text: str) -> tuple[list[dict], int]:
    """Every regedit_add a script makes, plus how many were computed at runtime.

    Protonfixes' helper takes a key, then an optional value name, type and
    value, and many calls pass only the key to create it. Values built from
    variables cannot be read statically, so those are counted and reported
    rather than guessed at.
    """
    values: list[dict] = []
    computed = 0
    for match in REGEDIT_START.finditer(text):
        index = match.end()
        depth = 1
        quote: str | None = None
        while index < len(text) and depth:
            character = text[index]
            if quote:
                if character == quote:
                    quote = None
            elif character in '\'"':
                quote = character
            elif character == '(':
                depth += 1
            elif character == ')':
                depth -= 1
            index += 1
        arguments = split_arguments(text[match.end():index - 1])
        if not arguments:
            continue
        folder = arguments[0]
        if any(not _is_literal(argument) for argument in arguments[1:]) \
                or ('\\' not in folder and '/' not in folder):
            computed += 1
            continue
        cleaned = re.sub(r'^(?:HKEY_CURRENT_USER|HKEY_LOCAL_MACHINE|HKCU|HKLM)[\\/]*', '', folder.replace('/', '\\'))
        hive = 'HKLM' if folder.upper().startswith(('HKLM', 'HKEY_LOCAL_MACHINE')) else 'HKCU'
        value: dict = {'hive': hive, 'key': cleaned}
        if len(arguments) > 1 and arguments[1] and not arguments[1].startswith('None'):
            value['name'] = arguments[1]
        if len(arguments) > 2 and arguments[2]:
            value['type'] = arguments[2].upper()
        if len(arguments) > 3 and arguments[3] is not None and arguments[3] != '':
            value['value'] = arguments[3]
        if 'name' not in value and 'value' in value:
            computed += 1
            continue
        values.append(value)
    return values, computed

UTIL_CALL = re.compile(r'util\.([a-z_]+)\(')

# Proton/Steam Deck switches that mean nothing on an iPad.
PROTON_ONLY_ENV = {
    'SteamDeck', 'PULSE_LATENCY_MSEC', 'AMD_DEBUG', 'radeonsi_disable_sam',
    'vk_x11_override_min_image_count', 'ENABLE_GAMESCOPE_WSI', '__GL_13ebad',
    'PROTON_OLD_GL_STRING', 'WINE_DISABLE_VULKAN_OPWR', 'UMU_USE_STEAM',
    'PROTON_USE_D7VK', 'PROTON_NO_XALIA', 'taskset', '__GL_ExtensionStringVersion',
}


def override_only_recipes(recipes: dict) -> dict[tuple[str, str], str]:
    """Recipes that are nothing but one DLL override, keyed by (dll, order).

    A game whose fix is exactly that override references the recipe instead of
    repeating it, which is what makes a fix reusable across titles.
    """
    index: dict[tuple[str, str], str] = {}
    for name, recipe in recipes.items():
        fields = {key for key, value in recipe.items()
                  if key not in {'title', 'category', 'summary', 'source', 'id'} and value not in (None, [], {}, '')}
        if fields != {'dll_overrides'}:
            continue
        for dll, order in (recipe.get('dll_overrides') or {}).items():
            index[(dll.lower(), order)] = name
    return index


def filter_arguments(argument: str, unavailable: list[str]) -> list[str]:
    """Split one append_argument() call, dropping renderer choices Madeira cannot honour.

    Proton renders Direct3D through Vulkan, so an upstream fix that asks for a
    Vulkan device is a fix on Linux and a failure on Madeira. The remaining
    tokens are kept, so "-fullscreen -vulkan" still yields -fullscreen, and the
    dropped token is recorded on the title.
    """
    tokens = argument.split()
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered.startswith('+r_renderapi'):
            value = tokens[index + 1] if index + 1 < len(tokens) else ''
            index += 2 if value else 1
            if value == '1':
                unavailable.append('+r_renderAPI 1 launch argument (Vulkan; Madeira presents through Metal)')
                continue
            kept.append(' '.join([token, value]) if value else token)
            continue
        if lowered in VULKAN_ARGUMENTS:
            unavailable.append(f'{token} launch argument ({VULKAN_ARGUMENTS[lowered]}; Madeira presents through Metal)')
            index += 1
            continue
        kept.append(token)
        index += 1
    return kept


def import_protonfixes(path: Path, dependencies: dict, recipes: dict, absorb: dict) -> list[dict]:
    """One profile per Protonfixes game script.

    Protonfixes keys every fix by the game's Steam App ID; the store-specific
    directories (gamefixes-gog, gamefixes-egs, ...) hold fixes for the same
    App ID that also need store-specific setup, and gamefixes-umu is the
    generic fallback. All of them collapse onto the App ID here, the generic
    one first so a store-specific fix wins a conflicting field.
    """
    games: dict[str, dict] = {}
    directories = sorted(path.glob('gamefixes-*'), key=lambda item: (item.name != 'gamefixes-umu', item.name))
    for category in directories:
        store = category.name.split('-', 1)[1]
        for script in sorted(category.glob('*.py')):
            if script.name.startswith('__') or script.name == 'default.py':
                continue
            steam = STEAM_FILE.match(script.name)
            umu = UMU_FILE.match(script.name)
            if steam:
                appid = int(steam.group(1))
            elif umu:
                appid = int(umu.group(1))
            else:
                # A store id that is not a Steam App ID (an EGS hash, a slug)
                # cannot be matched to a launch, so it is not imported.
                continue
            text = script.read_text(encoding='utf-8', errors='replace')
            title = ''
            match = DOCSTRING.search(text)
            if match:
                title = match.group(1).strip().splitlines()[0].strip()
            key = f'appid:{appid}'
            entry: dict = {'title': title or str(appid), 'appid': appid}
            sources = [f'{store}/{script.stem}']
            if re.search(r'^\s*if\b', text, re.MULTILINE) or text.count('def ') > 1:
                entry['conditional'] = True

            deps: list[str] = []
            env: dict[str, str] = {}
            overrides: dict[str, str] = {}
            registry: list[dict] = []
            arguments: list[str] = []
            removed: list[str] = []
            files: list[dict] = []
            run_target: str | None = None
            fixes: list[str] = []
            unavailable: list[str] = []

            for verb in PROTONTRICKS.findall(text):
                target = VERB_ALIASES.get(verb, verb)
                if target in dependencies:
                    if target not in deps:
                        deps.append(target)
                elif verb in WINDOWS_VERSIONS:
                    entry['windows_version'] = verb
                elif verb in SETTINGS_VERBS:
                    continue
                else:
                    fixes.append(f'winetricks:{verb}')
            for dll, order in WINE_OVERRIDE.findall(text):
                if order in OVERRIDE_ORDERS:
                    overrides[dll.lower()] = OVERRIDE_ORDERS[order]
            for name, value in SET_ENV.findall(text):
                if name.startswith('PROTON_') or name in PROTON_ONLY_ENV:
                    continue
                env[name] = value
            for name in DEL_ENV.findall(text):
                env.setdefault(name, '')
            for argument in APPEND_ARG.findall(text):
                for token in filter_arguments(argument, unavailable):
                    if token not in arguments:
                        arguments.append(token)
            registry, computed_registry = parse_registry(text)
            if computed_registry:
                unavailable.append(f'{computed_registry} registry value(s) computed at runtime')
            for from_text, to_text in REPLACE_COMMAND.findall(text):
                target, dropped = launch_rewrite(from_text, to_text)
                if target and run_target is None:
                    run_target = target
                elif dropped:
                    removed.append(dropped)
                elif target is None and dropped is None:
                    unavailable.append(f'launch command rewrite ({from_text})')
            for source, destination in OS_RENAME.findall(text):
                files.append({'action': 'rename', 'path': source, 'to': destination})
            for call in sorted(set(UTIL_CALL.findall(text))):
                if call in UNAVAILABLE_FIXES:
                    unavailable.append(UNAVAILABLE_FIXES[call])
                elif call in {'main', 'protontricks', 'winedll_override', 'wineexe_override',
                              'set_environment', 'del_environment', 'append_argument', 'regedit_add',
                              'replace_command',
                              'set_app_winver', 'set_winver', 'checkinstalled', 'is_custom_verb',
                              'protonprefix', 'protondir', 'once', 'log', 'get_game_install_path'}:
                    continue
            # A fix that is exactly a reusable override becomes a recipe reference.
            recipe_refs: list[str] = []
            for dll, order in sorted(overrides.items()):
                name = absorb.get((dll, order))
                if name:
                    overrides.pop(dll)
                    recipe_refs.append(name)
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
            if run_target:
                entry['run'] = run_target
            if removed:
                entry['remove_arguments'] = sorted(set(removed))
            if files:
                deduped = {json.dumps(item, sort_keys=True): item for item in files}
                entry['files'] = [deduped[key] for key in sorted(deduped)]
            if recipe_refs:
                entry['recipes'] = sorted(set(recipe_refs))
            if fixes:
                entry['fixes'] = sorted(set(fixes))
            if unavailable:
                entry['unavailable_fixes'] = sorted(set(unavailable))

            previous = games.get(key)
            if previous is None:
                entry['source'] = 'protonfixes:' + ','.join(sources)
                games[key] = entry
            else:
                for field in ('dependencies', 'fixes', 'unavailable_fixes', 'recipes', 'remove_arguments'):
                    if field in entry:
                        previous[field] = sorted(set(previous.get(field, []) + entry[field]))
                for field in ('files',):
                    if field in entry:
                        merged = previous.get(field, []) + entry[field]
                        deduped = {json.dumps(item, sort_keys=True): item for item in merged}
                        previous[field] = [deduped[key] for key in sorted(deduped)]
                for field in ('dll_overrides', 'env'):
                    if field in entry:
                        previous.setdefault(field, {}).update(entry[field])
                for field in ('run', 'launch_arguments', 'windows_version'):
                    if field in entry and field not in previous:
                        previous[field] = entry[field]
                previous.setdefault('registry', []).extend(entry.get('registry', []))
                if 'registry' in previous and not previous['registry']:
                    del previous['registry']
                previous['source'] = previous.get('source', '') + ',' + ','.join(sources)
                if entry.get('title') and previous.get('title') in {'', str(appid)}:
                    previous['title'] = entry['title']
    return list(games.values())


# ---------------------------------------------------------------------------
# Winlator import
#
# Winlator (github.com/brunodev85/winlator) ships its compatibility knowledge
# as data rather than as code: a per-executable configuration file for its
# x86 emulator (box64/default.box64rc), Windows component definitions with the
# DLLs each one owns (wincomponents/wincomponents.json) and the list of Wine
# debug channels the build knows. Most of the configuration is Android
# specific -- box64 tuning, Vulkan drivers (Turnip, Vortek), the X server, the
# touch input overlay, its own patched Wine -- and none of that can be ported.
# What is portable is the part that reaches Windows and means the same thing
# under Wine anywhere: DLL overrides, launch arguments, environment variables,
# the Windows version a title expects, and the mapping from a component (XAudio,
# DirectPlay, the WMA/WMV decoders) to the DLLs that belong to it. Those are
# imported here; everything else is recorded per title so the report is honest.

BOX64RC_SECTION = re.compile(r'^\[([^\]]+)\]\s*$')
# A section named for a variable or a pattern targets the Android loader.
WINLATOR_PATTERN_SECTION = re.compile(r'^[*/]')
# Executable names that belong to no particular title.
WINLATOR_GENERIC_EXES = {'launcher.exe', 'start.exe', 'game.exe', 'setup.exe',
                         'launcher64.exe', 'unins000.exe'}
# Box64/Android settings with no Windows-side meaning.
WINLATOR_UNAVAILABLE = {
    'MESA_EXTENSION_MAX_YEAR': 'limits Mesa (OpenGL) extensions; Madeira renders through Metal',
    'ZINK_CONTEXT_THREADED': 'a Zink (Vulkan/OpenGL) setting',
    'BOX64_SKIPCPU': 'box64 CPU skip (Android)',
    'BOX64_SSE42': 'box64 SSE4.2 emulation (Android; FEX decides feature exposure itself)',
    'BOX64_EXIT': 'box64 exits before the installer runs (Android)',
    'BOX64_DYNAREC_DIRTY': 'box64 write-tracking (Android)',
    'WINEVMEMMAXSIZE': "Winlator's own Wine patch; Madeira's Wine has no such variable",
    'WINE_DO_NOT_OPEN_SC_MANAGER': "Winlator's own Wine patch; Madeira keeps the service control "
                                   "manager and fixes its bootstrap race instead "
                                   "(patches/wine-rpcss-scm-bootstrap.patch)",
    'WINPREEXEC': "Winlator's pre-exec hook; Madeira applies file fixes through a profile's \"files\"",
    'WINEOVERRIDEAFFINITYMASK': "Winlator's own Wine patch",
}
# box64's dynamic recompiler settings, which are FEX's on Madeira.
WINLATOR_BOX64_DYNAREC = re.compile(r'^BOX64_DYNAREC_([A-Z0-9_]+)$')
# A Windows version Winlator sets through its Wine patch; Madeira writes the
# same version into Wine's own AppDefaults key.
WINLATOR_WINDOWS_VERSIONS = {
    'winxp': 'winxp', 'winxp64': 'winxp64', 'win2003': 'win2003',
    'vista': 'vista', 'win7': 'win7', 'win8': 'win8', 'win10': 'win10', 'win11': 'win11',
}
# Which catalogue entries own the DLLs of a Winlator component. A name an
# entry already claims (as its id or in its imports) is left alone; a name
# nothing claims goes to the entry for its family, which is how the list
# closes the gaps a Winlator prefix fills with files.
WINLATOR_DLL_FAMILIES = (
    ('d3dcsx_', 'd3dcsx'),
    ('d3dcompiler_', 'd3dcompiler-legacy'),
    ('d3dx9_', 'd3dx9'),
    ('d3dx11_', 'd3dx11_43'),
    ('d3dx10', 'd3dx10'),
    ('dswave', 'directmusic'),
    ('dm', 'directmusic'),
    ('dplay', 'directplay'),
    ('dp', 'directplay'),
    ('q', 'quartz'),
    ('xactengine', 'xact'),
    ('x3daudio', 'xact'),
    ('xapofx', 'xact'),
    ('xaudio2', 'xaudio2-legacy'),
    ('wmadmod', 'wmv9vcm'),
    ('wmasf', 'wmv9vcm'),
    ('wmv', 'wmv9vcm'),
    ('msvcm', 'vcrun2005'),
    ('vcomp', 'vcrun2005'),
    ('atl', 'vcrun2005'),
)


def winlator_dll_owner(name: str, dependencies: dict, claimed: dict[str, str]) -> str | None:
    """The entry that should own one of a component's DLLs, if any.

    A name an entry already owns is never moved: the catalogue's own choice of
    which component answers for a DLL outranks this list.
    """
    if name in claimed:
        return None
    if name in dependencies:
        return name
    for prefix, owner in WINLATOR_DLL_FAMILIES:
        if name.startswith(prefix) and owner in dependencies:
            return owner
    return None


def parse_box64rc(path: Path) -> dict[str, dict[str, list[str]]]:
    """{executable: {setting: [values]}} for Winlator's per-executable config."""
    sections: dict[str, dict[str, list[str]]] = {}
    current: str | None = None
    for raw in path.read_text(encoding='utf-8', errors='replace').split('\n'):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        header = BOX64RC_SECTION.match(line)
        if header:
            name = header.group(1).strip()
            current = None if WINLATOR_PATTERN_SECTION.match(name) else name
            continue
        if current is None or '=' not in line:
            continue
        key, _, value = line.partition('=')
        sections.setdefault(current, {}).setdefault(key.strip(), []).append(value.strip())
    return sections


def parse_winlator_env(setting: str) -> tuple[str, str] | None:
    """(name, value) from "WINEENV=NAME=value" or "NAME=value"."""
    body = setting
    for prefix in ('WINEENV=', 'WINEARGS=', 'WINEDLLOVERRIDES='):
        if body.startswith(prefix):
            body = body[len(prefix):]
            break
    name, separator, value = body.partition('=')
    if not separator:
        return None
    return name.strip(), value.strip()


# Wine's own WINEDLLOVERRIDES syntax: "dll=n", "dll=b", "dll=d" (disabled),
# and "a.dll,b.dll=d" for a group. Madeira spells "disabled" as an empty order.
WINLATOR_OVERRIDE_ORDERS = {
    'd': '', 'disabled': '', '': '',
    'n': 'n', 'native': 'n',
    'b': 'b', 'builtin': 'b',
    'n,b': 'n,b', 'native,builtin': 'n,b',
    'b,n': 'b,n', 'builtin,native': 'b,n',
}


def parse_winlator_overrides(text: str) -> dict[str, str]:
    """{dll: order} from one WINEDLLOVERRIDES value.

    A name that carries no order, and an order Madeira cannot express, are
    dropped rather than guessed at.
    """
    overrides: dict[str, str] = {}
    for group in text.split(';'):
        group = group.strip()
        if not group:
            continue
        names, separator, order = group.rpartition('=')
        if not separator:
            continue
        token = WINLATOR_OVERRIDE_ORDERS.get(order.strip().lower())
        if token is None:
            continue
        for name in names.split(','):
            name = Path(name.strip()).stem.lower()
            if name:
                overrides[name] = token
    return overrides


def import_winlator(assets: Path, dependencies: dict, recipes: dict) -> tuple[list[dict], int]:
    """(profiles, skipped generic sections) from Winlator's data files.

    Only the Windows-side part is taken: DLL overrides, launch arguments,
    environment variables, the Windows version, and the DLLs each Windows
    component owns. Anything box64-, Android- or Winlator-Wine-specific is
    recorded per title as an unavailable fix rather than dropped.
    """
    games: list[dict] = []
    skipped = 0
    box64rc = assets / 'box64/default.box64rc'
    if box64rc.exists():
        for executable, settings in sorted(parse_box64rc(box64rc).items()):
            if executable.lower() in WINLATOR_GENERIC_EXES:
                # "Launcher.exe" belongs to no one title; a profile keyed by it
                # would match every launcher in the library.
                skipped += 1
                continue
            entry: dict = {'title': executable, 'executables': [executable],
                           'source': 'winlator:box64/default.box64rc'}
            overrides: dict[str, str] = {}
            env: dict[str, str] = {}
            arguments: list[str] = []
            recipes_used: list[str] = []
            unavailable: list[str] = []
            for setting, values in settings.items():
                # A setting the loader itself acts on is named by the key, not
                # by a value ("BOX64_SKIPCPU=4").
                if setting in WINLATOR_UNAVAILABLE:
                    unavailable.append(f'{setting} ({WINLATOR_UNAVAILABLE[setting]})')
                for value in values:
                    if value.startswith('WINEARGS='):
                        for token in filter_arguments(value[len('WINEARGS='):], unavailable):
                            if token not in arguments:
                                arguments.append(token)
                        continue
                    if value.startswith('WINEDLLOVERRIDES='):
                        overrides.update(parse_winlator_overrides(value[len('WINEDLLOVERRIDES='):]))
                        continue
                    parsed = parse_winlator_env(value)
                    if parsed is None:
                        continue
                    name, setting_value = parsed
                    if name in WINLATOR_UNAVAILABLE:
                        unavailable.append(f'{name} ({WINLATOR_UNAVAILABLE[name]})')
                    elif name == 'WINVERSION':
                        version = WINLATOR_WINDOWS_VERSIONS.get(setting_value.lower())
                        if version:
                            entry['windows_version'] = version
                        else:
                            unavailable.append(f'Windows version {setting_value}')
                    elif name:
                        env[name] = setting_value
                # A dynamic recompiler setting is FEX's here. The one that
                # matters is the strong memory model: titles that hang or
                # corrupt state under a relaxed one need FEX's TSO emulation.
                if WINLATOR_BOX64_DYNAREC.match(setting):
                    if setting == 'BOX64_DYNAREC_STRONGMEM' and any(int(v or 0) > 0 for v in values):
                        if 'fex-strong-memory' in recipes:
                            recipes_used.append('fex-strong-memory')
                        else:
                            unavailable.append('BOX64_DYNAREC_STRONGMEM (no equivalent recipe)')
                    else:
                        unavailable.append(f'{setting} (box64 tuning; FEX has its own knobs)')
            if overrides:
                entry['dll_overrides'] = overrides
            if env:
                entry['env'] = env
            if arguments:
                entry['launch_arguments'] = ' '.join(arguments)
            if recipes_used:
                entry['recipes'] = sorted(set(recipes_used))
            if unavailable:
                entry['unavailable_fixes'] = sorted(set(unavailable))
            games.append(entry)

    wincomponents = assets / 'wincomponents/wincomponents.json'
    if wincomponents.exists():
        components = load(wincomponents)
        # What every entry already answers for, so a name is never taken from
        # one component and given to another.
        claimed = {dep_id.lower(): dep_id for dep_id in dependencies}
        for dep_id, dependency in dependencies.items():
            for name in dependency.get('imports', []) or []:
                claimed.setdefault(str(name).lower(), dep_id)
        component_sources: dict[str, set] = {}
        for component, definition in sorted(components.items()):
            for raw_name in definition.get('dlnames', []):
                name = Path(str(raw_name)).stem.lower()
                owner = winlator_dll_owner(name, dependencies, claimed)
                if owner is None:
                    continue
                dependency = dependencies[owner]
                imports = list(dependency.get('imports', []))
                if name not in imports:
                    imports.append(name)
                dependency['imports'] = sorted(imports)
                # A component Madeira has to be given files for keeps the file
                # list complete: Winlator ships a whole Windows component and a
                # game may load any DLL of it.
                if dependency.get('support') == 'payload':
                    payload = list(dependency.get('dlls', []))
                    filename = str(raw_name) if '.' in str(raw_name) else str(raw_name) + '.dll'
                    if filename.lower() not in [item.lower() for item in payload]:
                        payload.append(filename)
                    dependency['dlls'] = sorted(payload, key=str.lower)
                claimed[name] = owner
                component_sources.setdefault(owner, set()).add(component)
        for owner, used in component_sources.items():
            dependency = dependencies[owner]
            existing = [part for part in str(dependency.get('source', '')).split(',') if part]
            for component in sorted(used):
                tag = f'winlator:{component}'
                if tag not in existing:
                    existing.append(tag)
            dependency['source'] = ','.join(existing)
    return games, skipped


# ---------------------------------------------------------------------------
# Validation and output

VALID_SUPPORT = {'builtin', 'override', 'payload', 'manual', 'unsupported', 'partial'}
VALID_OVERRIDE = {'', 'n', 'b', 'n,b', 'b,n'}
VALID_CATEGORY = {'dependency', 'dll', 'registry', 'dx9', 'dx11', 'dx12', 'media', 'audio',
                  'input', 'wine_fex', 'drm', 'anticheat', 'save_path', 'launch', 'other'}


VALID_FILE_ACTIONS = {'rename', 'delete', 'mkdir'}
VALID_FILE_LOCATIONS = {'game', 'prefix'}


def check_file_actions(actions, owner: str) -> None:
    """A file action must be one Madeira can perform and complete."""
    for action in actions or []:
        kind = str(action.get('action', '')).lower()
        if kind not in VALID_FILE_ACTIONS:
            fail(f'{owner}: bad file action {action.get("action")!r}')
            continue
        if not action.get('path'):
            fail(f'{owner}: {kind} without a path')
        if kind == 'rename' and not action.get('to'):
            fail(f'{owner}: rename of {action.get("path")!r} without a destination')
        location = str(action.get('in', 'game')).lower()
        if location not in VALID_FILE_LOCATIONS:
            fail(f'{owner}: bad file location {action.get("in")!r}')


RULE_CONDITIONS = {'imports', 'files', 'executable_contains', 'path_contains', 'bits'}


def check_registry(values, owner: str) -> None:
    for value in values or []:
        if value.get('type') not in {'REG_SZ', 'REG_DWORD', 'REG_MULTI_SZ', 'REG_BINARY'}:
            fail(f'{owner}: bad registry type {value.get("type")!r}')


def check_fix_body(entry: dict, owner: str, dependencies: dict, recipes: dict) -> None:
    """The fields a recipe, a fallback, a rule, a remedy or the baseline share."""
    for dep_id in entry.get('dependencies', []):
        if dep_id not in dependencies:
            fail(f'{owner}: unknown dependency {dep_id!r}')
    for recipe in entry.get('recipes', []):
        if recipe not in recipes:
            fail(f'{owner}: unknown recipe {recipe!r}')
    for token in (entry.get('dll_overrides') or {}).values():
        if token not in VALID_OVERRIDE:
            fail(f'{owner}: bad override token {token!r}')
    version = entry.get('windows_version')
    if version and version not in WINDOWS_VERSIONS:
        fail(f'{owner}: bad windows_version {version!r}')
    check_registry(entry.get('registry'), owner)
    check_file_actions(entry.get('files'), owner)
    for token in entry.get('remove_arguments') or []:
        if not isinstance(token, str) or not token:
            fail(f'{owner}: bad remove_arguments entry {token!r}')
    for key, value in (entry.get('env') or {}).items():
        if not key or not isinstance(value, str):
            fail(f'{owner}: bad env entry {key!r}')


def validate_universal(database: dict) -> None:
    """The baseline, the general rules, the remedies and the module list.

    These are what make an unprofiled program work, so a mistake in them is a
    mistake in every launch: an unknown dependency id, a rule that would match
    everything, a remedy for a category nothing produces.
    """
    dependencies = database.get('dependencies', {})
    recipes = database.get('recipes', {})

    baseline = database.get('baseline') or {}
    if not baseline.get('title'):
        fail('baseline: no title')
    check_fix_body(baseline, 'baseline', dependencies, recipes)

    seen_rules: set[str] = set()
    for rule in database.get('rules', []):
        rule_id = rule.get('id')
        if not rule_id:
            fail('rule without an id'); continue
        if rule_id in seen_rules:
            fail(f'duplicate rule id {rule_id!r}')
        seen_rules.add(rule_id)
        when = rule.get('when') or {}
        if not when:
            fail(f'rule {rule_id}: no conditions, so it would match every program')
        for key in when:
            if key not in RULE_CONDITIONS:
                fail(f'rule {rule_id}: unknown condition {key!r}')
        if when.get('bits') not in (None, 32, 64):
            fail(f'rule {rule_id}: bits must be 32 or 64, not {when.get("bits")!r}')
        for name in (when.get('imports') or []) + (when.get('files') or []):
            if name != name.lower() or name.endswith('.dll'):
                fail(f'rule {rule_id}: {name!r} is not a normalised DLL name')
        for name in (when.get('executable_contains') or []) + (when.get('path_contains') or []):
            if name != name.lower():
                fail(f'rule {rule_id}: condition {name!r} must be lowercase')
        if not any(rule.get(field) for field in
                   ('dependencies', 'recipes', 'dll_overrides', 'registry', 'env',
                    'windows_version', 'launch_arguments', 'note')):
            fail(f'rule {rule_id}: nothing to apply and nothing to say')
        check_fix_body(rule, f'rule {rule_id}', dependencies, recipes)

    for category, remedy in (database.get('remedies') or {}).items():
        if category not in VALID_CATEGORY:
            fail(f'remedy {category!r}: not a diagnosis category')
        if not remedy.get('name'):
            fail(f'remedy {category}: no name')
        if not any(remedy.get(field) for field in
                   ('dependencies', 'recipes', 'dll_overrides', 'registry', 'env',
                    'windows_version', 'launch_arguments', 'remove_arguments', 'files')):
            fail(f'remedy {category}: nothing to try')
        check_fix_body(remedy, f'remedy {category}', dependencies, recipes)

    modules = database.get('wine_modules') or []
    if len(modules) < 400:
        fail(f'wine_modules: {len(modules)} names is not a Wine module list')
    for name in modules:
        if not name or name != name.lower():
            fail(f'wine_modules: {name!r} must be a lowercase module name')
    not_shipped = database.get('wine_not_shipped') or []
    for name in not_shipped:
        if name in modules:
            fail(f'wine_not_shipped: {name!r} is also listed as provided')
    api_sets = database.get('api_set_prefixes') or []
    if not api_sets:
        fail('api_set_prefixes: a Wine module list without the API set prefixes '
             'would report every api-ms-win-* import as unaccounted')
    for prefix in api_sets:
        if not prefix.endswith('-') or prefix != prefix.lower():
            fail(f'api_set_prefixes: {prefix!r} must be a lowercase prefix ending in "-"')

    # The engine stops the retry ladder when an anti-cheat or DRM component is
    # unsatisfied; that only works while those components are marked unsupported.
    for dep_id, dep in dependencies.items():
        if dep.get('category') in {'anticheat', 'drm'} and dep.get('support') != 'unsupported':
            fail(f'dependency {dep_id}: category {dep["category"]} must be unsupported '
                 '(the ladder stops on it)')

    # What the runtime serves decides which claims the catalogue may make. A
    # component that says "the runtime has this" has to name a module one of the
    # farms carries; one that says "partly" must not claim a name Wine never
    # built without naming the file it installs itself. Otherwise the entry
    # silences the report while nothing answers the import, which is how a title
    # fails with no explanation.
    provided, never_built, prefixes = runtime_names(database)
    for dep_id, dep in dependencies.items():
        support = dep.get('support')
        if support not in ('builtin', 'partial'):
            continue
        installed = {normalise_name(name) for name in dep.get('dlls') or []}
        for name in dep.get('imports') or []:
            if serves(provided, prefixes, name) or normalise_name(name) in installed:
                continue
            if support == 'builtin':
                fail(f'dependency {dep_id}: imports {name!r}, which no farm provides')
            if normalise_name(name) in never_built:
                fail(f'dependency {dep_id}: claims {name!r}, which Wine never built, without '
                     'naming a file it installs (add it to dlls, or drop it from imports: '
                     'the engine reports such a name as unavailable)')

    # An override that pins the builtin alone cannot resolve when the runtime has
    # no builtin of that name, and it stops the title's own copy from loading.
    # The import sanitiser drops what it finds in imported data; this keeps an
    # authored one from creeping back in.
    def check_overrides(owner: str, overrides: dict) -> None:
        for name, order in (overrides or {}).items():
            if name != name.lower().lstrip('*'):
                fail(f'{owner}: override name {name!r} is not a normalised module name')
            tokens = [token.strip() for token in str(order).split(',') if token.strip()]
            if tokens == ['b'] and not serves(provided, prefixes, name):
                fail(f'{owner}: {name}=b names a module the runtime does not ship')

    for dep_id, dep in dependencies.items():
        check_overrides(f'dependency {dep_id}', dep.get('dll_overrides'))
    for recipe_id, recipe in recipes.items():
        check_overrides(f'recipe {recipe_id}', recipe.get('dll_overrides'))
    for rule in database.get('rules', []):
        check_overrides(f'rule {rule.get("id")}', rule.get('dll_overrides'))
    for category, remedy in (database.get('remedies') or {}).items():
        check_overrides(f'remedy {category}', remedy.get('dll_overrides'))
    if database.get('baseline'):
        check_overrides('baseline', database['baseline'].get('dll_overrides'))
    for game in database.get('games', []):
        check_overrides(f'game {game.get("title")}', game.get('dll_overrides'))


def normalise_name(name: str) -> str:
    """A module or import name as the engine compares them."""
    name = str(name).lower()
    return name[:-4] if name.endswith('.dll') else name


def runtime_names(database: dict) -> tuple[set[str], set[str], list[str]]:
    """(names the runtime serves, names Wine never built, API set prefixes).

    Both architectures count: a component is a claim about what the runtime
    serves, and the per-launch report decides which half applies. The second set
    is what Wine never built (mfcore, the DirectX SDK compilers), whose reasons
    the engine quotes. An API set name is not a file yet is served all the same:
    the loader resolves it to the module implementing the contract.
    """
    provided = {normalise_name(name) for name in (database.get('wine_modules') or [])}
    provided |= {normalise_name(name) for name in (database.get('wine_modules_64') or [])}
    missing = {normalise_name((entry or {}).get('name') or '') for entry in (database.get('not_built') or [])}
    prefixes = [str(prefix).lower() for prefix in database.get('api_set_prefixes') or []]
    return provided, missing, prefixes


def serves(provided: set[str], prefixes: list[str], name: str) -> bool:
    """Whether the runtime answers an import, API sets included."""
    name = normalise_name(name)
    return name in provided or any(name.startswith(prefix) for prefix in prefixes)


def sanitise_overrides(database: dict) -> list[str]:
    """Correct the DLL overrides taken from other launchers, in place.

    Two spellings mean something in another runtime and nothing here:

    * a leading `*` (Protonfixes writes `winedll_override('*dsound', BUILTIN)`),
      which Proton understands as a wildcard and Wine reads as a module name no
      DLL has. Madeira's overrides are written for the one launch, so the
      literal name is the fix the file meant;
    * `b` alone for a module this runtime does not ship, which cannot resolve
      and — worse — stops the title's own copy from loading. Winlator's data has
      `vulkan-1=b` for titles it runs on a Wine with Vulkan; here it would break
      the file the title ships. The override is dropped and the title records
      why, which is what `unavailable_fixes` is for.

    Returns the lines the caller logs.
    """
    provided, missing, prefixes = runtime_names(database)
    notes: list[str] = []

    def fix(owner: str, overrides: dict) -> tuple[dict, list[str]]:
        result: dict[str, str] = {}
        dropped: list[str] = []
        for name, order in overrides.items():
            clean = name.lower().lstrip('*')
            tokens = [token.strip() for token in str(order).split(',') if token.strip()]
            if clean != name.lower():
                notes.append(f'{owner}: {name} -> {clean} (a `*` wildcard is not Wine syntax)')
            if tokens == ['b'] and not serves(provided, prefixes, clean):
                why = 'Wine never built it' if normalise_name(clean) in missing else 'this runtime does not ship it'
                notes.append(f'{owner}: dropped {clean}={order} ({why}; it would hide the copy the title ships)')
                dropped.append(clean)
                continue
            result[clean] = order
        return result, dropped

    def before(block: dict, owner: str) -> None:
        if 'dll_overrides' not in block:
            return
        original = dict(block.get('dll_overrides') or {})
        fixed, dropped = fix(owner, original)
        if fixed == original:
            return
        block['dll_overrides'] = fixed
        if not dropped:
            return   # a renamed wildcard needs no apology; the data says it
        if 'title' in block or 'executables' in block or 'unavailable_fixes' in block:
            # A title records the loss where the details screen shows it.
            fixes = list(block.get('unavailable_fixes') or [])
            fixes.append('DLL override dropped: ' + ', '.join(dropped)
                         + ' (this runtime does not ship it, and pinning it would hide the file the title brings)')
            block['unavailable_fixes'] = fixes
        else:
            lines = [line for line in notes if line.startswith(owner + ':')]
            existing = block.get('notes')
            block['notes'] = (existing + ' ' if existing else '') + '; '.join(lines)

    for dep_id, dep in database.get('dependencies', {}).items():
        before(dep, f'dependency {dep_id}')
    for recipe_id, recipe in database.get('recipes', {}).items():
        before(recipe, f'recipe {recipe_id}')
    for rule in database.get('rules', []):
        before(rule, f'rule {rule.get("id")}')
    for category, remedy in (database.get('remedies') or {}).items():
        before(remedy, f'remedy {category}')
    if database.get('baseline'):
        before(database['baseline'], 'baseline')
    for game in database.get('games', []):
        before(game, f'game {game.get("title") or game.get("appid") or game.get("gog_slug")}')
    return notes


def validate(database: dict) -> None:
    dependencies = database.get('dependencies', {})
    recipes = database.get('recipes', {})
    for dep_id, dep in dependencies.items():
        if dep.get('support') not in VALID_SUPPORT:
            fail(f'dependency {dep_id}: bad support {dep.get("support")!r}')
        if dep_id != 'wine-version' and not dep.get('category'):
            fail(f'dependency {dep_id}: no category')
        if dep.get('category') not in VALID_CATEGORY:
            fail(f'dependency {dep_id}: bad category {dep.get("category")!r}')
        for token in (dep.get('dll_overrides') or {}).values():
            if token not in VALID_OVERRIDE:
                fail(f'dependency {dep_id}: bad override token {token!r}')
        for name in dep.get('imports', []):
            if name != name.lower() or name.endswith('.dll'):
                fail(f'dependency {dep_id}: import {name!r} is not a normalised DLL name')
        for requires in dep.get('requires', []):
            if requires not in dependencies:
                fail(f'dependency {dep_id}: requires unknown {requires}')
        for recipe in dep.get('recipes', []):
            if recipe not in recipes:
                fail(f'dependency {dep_id}: unknown recipe {recipe}')
        for value in dep.get('registry', []):
            if value.get('type') not in {'REG_SZ', 'REG_DWORD', 'REG_MULTI_SZ', 'REG_BINARY'}:
                fail(f'dependency {dep_id}: bad registry type {value.get("type")!r}')

    for recipe_id, recipe in recipes.items():
        if recipe.get('category') and recipe['category'] not in VALID_CATEGORY:
            fail(f'recipe {recipe_id}: bad category {recipe["category"]!r}')
        for requires in recipe.get('requires', []):
            if requires not in recipes:
                fail(f'recipe {recipe_id}: requires unknown recipe {requires}')
        for dep_id in recipe.get('dependencies', []):
            if dep_id not in dependencies:
                fail(f'recipe {recipe_id}: unknown dependency {dep_id}')
        for token in (recipe.get('dll_overrides') or {}).values():
            if token not in VALID_OVERRIDE:
                fail(f'recipe {recipe_id}: bad override token {token!r}')
        check_file_actions(recipe.get('files'), f'recipe {recipe_id}')
        for token in recipe.get('remove_arguments') or []:
            if not isinstance(token, str) or not token:
                fail(f'recipe {recipe_id}: bad remove_arguments entry {token!r}')

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
        for recipe in game.get('recipes', []):
            if recipe not in recipes:
                fail(f'game {game.get("title")}: unknown recipe {recipe!r}')
        for token in (game.get('dll_overrides') or {}).values():
            if token not in VALID_OVERRIDE:
                fail(f'game {game.get("title")}: bad override token {token!r}')
        if 'run' in game:
            target = game['run']
            if not isinstance(target, str) or not target:
                fail(f'game {game.get("title")}: bad run target {target!r}')
            elif not (target.lower().endswith('.exe') or '\\' in target or '/' in target
                      or (len(target) > 1 and target[1] == ':')):
                fail(f'game {game.get("title")}: run target {target!r} is not a program')
        check_file_actions(game.get('files'), f'game {game.get("title")}')
        for fallback in game.get('fallbacks', []):
            check_file_actions(fallback.get('files'), f'game {game.get("title")} fallback')
            for recipe in fallback.get('recipes', []):
                if recipe not in recipes:
                    fail(f'game {game.get("title")}: unknown fallback recipe {recipe!r}')
            for dep_id in fallback.get('dependencies', []):
                if dep_id not in dependencies:
                    fail(f'game {game.get("title")}: unknown fallback dependency {dep_id!r}')
        if game.get('rating') and game['rating'] not in {'perfect', 'playable', 'runnable', 'broken', 'unknown'}:
            fail(f'game {game.get("title")}: bad rating {game["rating"]!r}')

    validate_universal(database)


def build(protonfixes: Path | None, winetricks: Path | None, bottles: Path | None,
          winlator: Path | None = None) -> dict:
    hand = load(COMPAT / 'dependencies.json')
    dependencies = dict(hand.get('dependencies', {}))
    if not dependencies:
        raise SystemExit('compat/dependencies.json is empty')
    hand_written = set(dependencies)

    wanted = set()
    if protonfixes:
        for category in sorted(protonfixes.glob('gamefixes-*')):
            for script in category.glob('*.py'):
                wanted.update(PROTONTRICKS.findall(script.read_text(encoding='utf-8', errors='replace')))

    if winetricks:
        verbs = parse_winetricks(winetricks)
        for name, entry in import_winetricks(verbs, wanted).items():
            if name not in dependencies:
                dependencies[name] = entry

    if bottles:
        definitions: dict[str, dict] = {}
        for path in sorted(bottles.rglob('*.yml')):
            definitions.update(parse_bottles(path))
        for name, entry in import_bottles(definitions).items():
            if name not in dependencies:
                dependencies[name] = entry

    database = {
        'schema': 1,
        'updated': load(COMPAT / 'games.json').get('updated', '2026-10-01'),
        'generated_by': 'build/tools/gen-game-compat.py',
        'sources': {
            'winetricks': winetricks.name if winetricks else None,
            'bottles': bottles.name if bottles else None,
            'protonfixes': protonfixes.name if protonfixes else None,
            'winlator': winlator.name if winlator else None,
        },
        'dependencies': dependencies,
        'recipes': load(COMPAT / 'recipes.json').get('recipes', {}),
        'games': list(load(COMPAT / 'games.json').get('games', [])),
        'baseline': load(COMPAT / 'baseline.json').get('baseline', {}),
        'rules': load(COMPAT / 'rules.json').get('rules', []),
        'remedies': load(COMPAT / 'rules.json').get('remedies', {}),
        'wine_modules': load(COMPAT / 'wine-modules.json').get('modules', []),
        'wine_modules_64': load(COMPAT / 'wine-modules.json').get('modules_64', []),
        'wine_not_shipped': load(COMPAT / 'wine-modules.json').get('not_shipped', []),
        'not_built': load(COMPAT / 'wine-modules.json').get('not_built', []),
        'api_set_prefixes': load(COMPAT / 'wine-modules.json').get('api_set_prefixes', []),
    }
    recipes = database['recipes']
    for dep in database['dependencies'].values():
        slot(dep, 'category', dep.get('category') or category_for('', dep), after='kind')

    if protonfixes:
        absorb = override_only_recipes(recipes)
        imported = import_protonfixes(protonfixes, dependencies, recipes, absorb)
        curated = {str(game.get('appid')) for game in database['games'] if game.get('appid')}
        curated |= {exe.lower() for game in database['games'] for exe in (game.get('executables') or [])}
        for game in imported:
            key = str(game.get('appid')) if game.get('appid') else (game.get('gog_slug') or '').lower()
            executables = [exe.lower() for exe in game.get('executables') or []]
            if key in curated or any(exe in curated for exe in executables):
                continue
            database['games'].append(game)

    if winlator:
        imported, skipped = import_winlator(winlator, dependencies, recipes)
        # A curated profile, or one from another source with the same title,
        # keeps its place; Winlator fills in what nothing else knows.
        taken = {exe.lower() for game in database['games'] for exe in (game.get('executables') or [])}
        taken |= {(game.get('title') or '').lower() for game in database['games']}
        for game in imported:
            executables = [exe.lower() for exe in game.get('executables') or []]
            title = (game.get('title') or '').lower()
            if title in taken or any(exe in taken for exe in executables):
                continue
            taken.add(title)
            taken.update(executables)
            database['games'].append(game)
        database['winlator_skipped_generic_sections'] = skipped

    sanitised = sanitise_overrides(database)
    for line in sanitised:
        print('override:' , line)
    validate(database)
    return database


def winlator_assets(path: Path) -> Path | None:
    """The Winlator assets directory in a path that may be either repository.

    The two repositories do not carry the same assets: the box64 per-executable
    profiles and the Windows component definitions live in the app repository.
    A path with neither file is not a source, and saying so beats importing the
    smaller set without saying anything.
    """
    for candidate in (path / 'app/src/main/assets', path / 'assets', path):
        if (candidate / 'box64/default.box64rc').exists() or (candidate / 'wincomponents/wincomponents.json').exists():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--protonfixes', type=Path, help='Protonfixes checkout to import game fixes from')
    parser.add_argument('--winetricks', type=Path, help='Winetricks checkout (the directory with src/winetricks)')
    parser.add_argument('--bottles', type=Path, help='Bottles dependencies checkout (the directory of .yml files)')
    parser.add_argument('--winlator', type=Path,
                        help='Winlator assets directory (winlator-app/app/src/main/assets) or a checkout of either repository')
    parser.add_argument('--check', action='store_true', help='validate and compare without writing')
    args = parser.parse_args()

    winetricks = args.winetricks
    if winetricks and (winetricks / 'src/winetricks').exists():
        winetricks = winetricks / 'src/winetricks'
    winlator = args.winlator
    if winlator:
        winlator = winlator_assets(args.winlator)
        if winlator is None:
            raise SystemExit(f'{args.winlator} does not look like the Winlator assets directory '
                             '(no box64/default.box64rc and no wincomponents/wincomponents.json)')
        print(f'winlator: reading {winlator}')
    database = build(args.protonfixes, winetricks, args.bottles, winlator)
    text = json.dumps(database, indent=1, sort_keys=False, ensure_ascii=False) + '\n'
    if args.check:
        if OUT.exists() and OUT.read_text(encoding='utf-8') == text:
            print(f'PASS: compat.json is up to date ({len(database["dependencies"])} dependencies, '
                  f'{len(database["recipes"])} recipes, {len(database["games"])} games)')
            return 1 if failures else 0
        print('FAIL: app/Madeira/compat.json is stale; run gen-game-compat.py')
        return 1
    OUT.write_text(text, encoding='utf-8')
    for failure in failures[:40]:
        print('FAIL: ' + failure, file=sys.stderr)
    print(f'wrote {OUT.relative_to(ROOT)}: {len(database["dependencies"])} dependencies, '
          f'{len(database["recipes"])} recipes, {len(database["games"])} games, {len(text)} bytes')
    if winlator:
        print(f'winlator: {database.get("winlator_skipped_generic_sections", 0)} generic section(s) skipped')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
