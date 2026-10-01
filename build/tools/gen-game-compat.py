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
    'replace_command': 'rewrites the launch command',
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
    if re.search(r'denuvo|securom|safedisc|starforce|drm|solidshield|protect', n):
        return 'drm'
    if dep.get('kind') == 'anticheat':
        return 'anticheat'
    if re.search(r'd3d12|dxil|d3d12', n):
        return 'dx12'
    if re.search(r'd3d11|d3dx11|dxgi', n):
        return 'dx11'
    if re.search(r'd3d9|d3d8|ddraw|d3dx9|directx9|d3dcompiler|d3dxof|cnc|dgvoodoo|directplay|quartz|devenum|media', n):
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
            for call in sorted(set(UTIL_CALL.findall(text))):
                if call in UNAVAILABLE_FIXES:
                    unavailable.append(UNAVAILABLE_FIXES[call])
                elif call in {'main', 'protontricks', 'winedll_override', 'wineexe_override',
                              'set_environment', 'del_environment', 'append_argument', 'regedit_add',
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
                for field in ('dependencies', 'fixes', 'unavailable_fixes', 'recipes'):
                    if field in entry:
                        previous[field] = sorted(set(previous.get(field, []) + entry[field]))
                for field in ('dll_overrides', 'env'):
                    if field in entry:
                        previous.setdefault(field, {}).update(entry[field])
                previous.setdefault('registry', []).extend(entry.get('registry', []))
                if 'registry' in previous and not previous['registry']:
                    del previous['registry']
                previous['source'] = previous.get('source', '') + ',' + ','.join(sources)
                if entry.get('title') and previous.get('title') in {'', str(appid)}:
                    previous['title'] = entry['title']
    return list(games.values())


# ---------------------------------------------------------------------------
# Validation and output

VALID_SUPPORT = {'builtin', 'override', 'payload', 'manual', 'unsupported', 'partial'}
VALID_OVERRIDE = {'', 'n', 'b', 'n,b', 'b,n'}
VALID_CATEGORY = {'dependency', 'dll', 'registry', 'dx9', 'dx11', 'dx12', 'media', 'audio',
                  'input', 'wine_fex', 'drm', 'anticheat', 'save_path', 'launch', 'other'}


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
        for fallback in game.get('fallbacks', []):
            for recipe in fallback.get('recipes', []):
                if recipe not in recipes:
                    fail(f'game {game.get("title")}: unknown fallback recipe {recipe!r}')
            for dep_id in fallback.get('dependencies', []):
                if dep_id not in dependencies:
                    fail(f'game {game.get("title")}: unknown fallback dependency {dep_id!r}')
        if game.get('rating') and game['rating'] not in {'perfect', 'playable', 'runnable', 'broken', 'unknown'}:
            fail(f'game {game.get("title")}: bad rating {game["rating"]!r}')


def build(protonfixes: Path | None, winetricks: Path | None, bottles: Path | None) -> dict:
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
        },
        'dependencies': dependencies,
        'recipes': load(COMPAT / 'recipes.json').get('recipes', {}),
        'games': list(load(COMPAT / 'games.json').get('games', [])),
    }
    recipes = database['recipes']
    for dep in database['dependencies'].values():
        slot(dep, 'category', dep.get('category') or category_for('', dep), after='kind')

    if protonfixes:
        absorb = override_only_recipes(recipes)
        imported = import_protonfixes(protonfixes, dependencies, recipes, absorb)
        curated = {str(game.get('appid')) for game in database['games'] if game.get('appid')}
        curated |= {(game.get('executables') or [''])[0].lower() for game in database['games']}
        for game in imported:
            key = str(game.get('appid')) if game.get('appid') else (game.get('gog_slug') or '').lower()
            exe = (game.get('executables') or [''])[0].lower()
            if key in curated or (exe and exe in curated):
                continue
            database['games'].append(game)

    validate(database)
    return database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--protonfixes', type=Path, help='Protonfixes checkout to import game fixes from')
    parser.add_argument('--winetricks', type=Path, help='Winetricks checkout (the directory with src/winetricks)')
    parser.add_argument('--bottles', type=Path, help='Bottles dependencies checkout (the directory of .yml files)')
    parser.add_argument('--check', action='store_true', help='validate and compare without writing')
    args = parser.parse_args()

    winetricks = args.winetricks
    if winetricks and (winetricks / 'src/winetricks').exists():
        winetricks = winetricks / 'src/winetricks'
    database = build(args.protonfixes, winetricks, args.bottles)
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
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
