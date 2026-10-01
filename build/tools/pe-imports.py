#!/usr/bin/env python3
"""Read a PE module's imports, and check a farm against itself.

A 64-bit program that imports a DLL the farm does not carry never reaches its
first instruction.  `build/wine-i386/build.sh` answers that by installing every
module the configured tree has a rule for, and the 64-bit farm is built the same
way by `build/wine-arm64ec/build.sh` -- but a hand-copied farm, or a policy skip
that names one module and not its import, can still leave a module behind that
cannot load.  Reading the import tables is how that is caught without running
every program.

Imports come in two flavours and only one is fatal:

* the import directory is resolved when the module is loaded; a name that is
  not there fails the load, which is what `--fail-on-load-gap` refuses;
* the delay-import directory is resolved on the first call.  A gap there is a
  code path that will fail later (`shell32` reaching for `gdiplus`), so it is
  reported but does not stop a build.

API set contract names (`api-ms-win-*`, `ext-ms-win-*`) are not files: the
loader maps them onto the module that implements the contract, so they are not
gaps.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

MODULE_SUFFIXES = ('.dll', '.exe', '.drv', '.cpl', '.ax', '.acm', '.ocx')
API_SET_PREFIXES = ('api-ms-win-', 'ext-ms-win-')
DIR_IMPORT = 1
DIR_DELAY_IMPORT = 13
IMAGE_FILE_MACHINE_ARM64 = 0xaa64
IMAGE_FILE_MACHINE_ARM64EC = 0xa641
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_FILE_MACHINE_I386 = 0x14c

MACHINES = {
    IMAGE_FILE_MACHINE_ARM64: 'arm64',
    IMAGE_FILE_MACHINE_ARM64EC: 'arm64ec',
    IMAGE_FILE_MACHINE_AMD64: 'x64',
    IMAGE_FILE_MACHINE_I386: 'i386',
}


@dataclass
class Imports:
    machine: int | None
    load: list[str] = field(default_factory=list)
    delay: list[str] = field(default_factory=list)

    @property
    def arch(self) -> str:
        return MACHINES.get(self.machine, hex(self.machine) if self.machine else 'unreadable')


def _rva_to_offset(sections, rva):
    for va, vsize, raw, rawsize in sections:
        if va <= rva < va + max(vsize, rawsize):
            return raw + (rva - va)
    return None


def imports_of(path: Path) -> Imports:
    """The load-time and delay-load import names of one PE file, lowercase."""
    data = path.read_bytes()
    if data[:2] != b'MZ':
        return Imports(None)
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        return Imports(None)
    machine = struct.unpack_from('<H', data, pe + 4)[0]
    nsections = struct.unpack_from('<H', data, pe + 6)[0]
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    if magic == 0x20b:      # PE32+
        directories = optional + 112
    elif magic == 0x10b:    # PE32
        directories = optional + 96
    else:
        return Imports(machine)
    sections = []
    for index in range(nsections):
        header = optional + optional_size + index * 40
        va, vsize = struct.unpack_from('<II', data, header + 12)
        raw, rawsize = struct.unpack_from('<II', data, header + 20)
        sections.append((va, vsize, raw, rawsize))

    def cstring(rva):
        offset = _rva_to_offset(sections, rva)
        if offset is None:
            return None
        end = data.find(b'\0', offset)
        if end < 0:
            return None
        return data[offset:end].decode('ascii', 'replace').lower()

    def names_at(rva, entry_size, name_field):
        if not rva:
            return []
        base = _rva_to_offset(sections, rva)
        if base is None:
            return []
        out = []
        for index in range(4096):       # a module does not import more than this
            entry = base + index * entry_size
            if entry + entry_size > len(data):
                break
            fields = struct.unpack_from('<IIIII', data, entry)
            if not any(fields[:4]):
                break
            name = cstring(fields[name_field])
            if name:
                out.append(name)
        return out

    load_rva = struct.unpack_from('<I', data, directories + 8 * DIR_IMPORT)[0]
    delay_rva = struct.unpack_from('<I', data, directories + 8 * DIR_DELAY_IMPORT)[0]
    return Imports(machine,
                   names_at(load_rva, 20, 3),
                   names_at(delay_rva, 32, 1))


def modules_in(directory: Path) -> dict[str, Path]:
    """{lowercase file name: path} for every Windows module in a farm."""
    found: dict[str, Path] = {}
    if not directory.is_dir():
        return found
    for entry in sorted(directory.iterdir()):
        name = entry.name.lower()
        if name.endswith(MODULE_SUFFIXES):
            found[name] = entry
    return found


def is_gap(name: str, provided: set[str]) -> bool:
    if name.startswith(API_SET_PREFIXES):
        return False
    return name not in provided


def check_farms(farms: list[Path], allowed: dict[str, str] | None = None):
    """(load gaps, delay gaps) as {import name: [module, ...]} for a farm set.

    The farms are one namespace: a name the native ARM64 farm provides is still
    a name a WoW64 session's 64-bit half can load.
    """
    allowed = allowed or {}
    modules: dict[str, Path] = {}
    for farm in farms:
        modules.update(modules_in(farm))
    provided = set(modules)
    load_gaps: dict[str, list[str]] = {}
    delay_gaps: dict[str, list[str]] = {}
    for name, path in modules.items():
        table = imports_of(path)
        for import_name in table.load:
            if is_gap(import_name, provided) and import_name not in allowed:
                load_gaps.setdefault(import_name, []).append(name)
        for import_name in table.delay:
            if is_gap(import_name, provided) and import_name not in allowed:
                delay_gaps.setdefault(import_name, []).append(name)
    return load_gaps, delay_gaps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--farm', type=Path, action='append', required=True,
                        help='a farm directory (app/Madeira/arm64ec-windows)')
    parser.add_argument('--allow', action='append', default=[],
                        help='NAME=reason: an import the farm is knowingly without')
    parser.add_argument('--fail-on-load-gap', action='store_true',
                        help='exit non-zero when a module would not load')
    parser.add_argument('--quiet', action='store_true')
    arguments = parser.parse_args()

    allowed = {}
    for entry in arguments.allow:
        name, _, reason = entry.partition('=')
        allowed[name.lower()] = reason

    modules: dict[str, Path] = {}
    for farm in arguments.farm:
        modules.update(modules_in(farm))
    load_gaps, delay_gaps = check_farms(arguments.farm, allowed)

    if not arguments.quiet:
        print(f'== {len(modules)} modules in {", ".join(str(f) for f in arguments.farm)}')
        for name, reason in sorted(allowed.items()):
            print(f'known load-time gap, allowed: {name} ({reason})')
        for import_name, users in sorted(load_gaps.items()):
            print(f'LOAD  {import_name} missing, imported by {", ".join(users)}')
        for import_name, users in sorted(delay_gaps.items()):
            print(f'delay {import_name} missing, imported by {", ".join(users)}')
        print(f'== load-time gaps: {len(load_gaps)}; delay-load gaps: {len(delay_gaps)}'
              f' ({len(modules)} modules)')

    if load_gaps and arguments.fail_on_load_gap:
        print(f'FAIL {len(load_gaps)} import(s) would stop a module from loading',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
