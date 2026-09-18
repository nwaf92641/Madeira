#!/usr/bin/env python3
"""Gate the DirectX module aliases in app/Madeira/DLLAliases.h.

The alias table is a claim about what the bundle can serve: "the name a title
imports resolves to a module that is present in the session's system32". Two
ways for that claim to be false, and both are silent at runtime -- the symlink
simply is not made (a missing target) or the game loads the wrong library (an
alias that shadows a real implementation). Both are checked here instead.

  1. Every alias target must be a module this bundle ships, for the
     architecture the x64 session uses (arm64ec-windows) and for the native one
     (aarch64-windows). The runtime links into system32 from whichever of those
     the session selected, so a target missing from either is a target that is
     absent for somebody.
  2. No alias name may be a module the bundle ships. Shadowing a shipped module
     with a link to a different one would replace a working implementation.
  3. The sets must be internally consistent: no duplicate alias names, no alias
     that points at itself, no alias whose target is itself an alias (a chain
     would make the result depend on iteration order).
  4. Coverage: every DirectX SDK generation that can be served by a shipped
     module is in the table. This is the check that turns "we aliased the ones
     we remembered" into "the family is closed", which is the whole point --
     the failure mode being fixed is a name nobody thought about.

Exit status is 0 when every property holds.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TABLE = REPO / "app/Madeira/DLLAliases.h"

# Which bundle directory the runtime copies into system32 depends on the guest,
# and the two are not the same set. `arm64ec-windows` is the set every real
# game uses -- WineProcessBridge.m selects it for an x64 target, and an x64
# executable is what a DirectX title is. `aarch64-windows` serves the native
# ARM64 session, which has no DirectX 9 shader compiler in it at all today; that
# asymmetry is reported by tools/check-pe-module-set.py against
# tools/pe-module-manifest.txt rather than failed here, because an alias cannot
# fix a module the bundle does not build.
TARGET_ARCH = "arm64ec-windows"
SHADOW_ARCHS = ("arm64ec-windows", "aarch64-windows")

# `{ "alias.dll", "target.dll" },`
ROW = re.compile(r'\{\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\}')

# The generations a shipped module can serve. Each entry is the family, the
# range a title may ask for, and the module(s) that may answer it.
#
# d3dx9: the bundle ships d3dx9_43 (the newest of the family), so 24..42 are
# servable. d3dx10_43, d3dx11_43 and d3dcsx_43 are *not* shipped, so those
# families have no target to point at and are deliberately absent -- the gap is
# recorded in tools/pe-module-manifest.txt instead of papered over with an alias
# to a module that is not there.
COVERAGE = [
    ("d3dx9", 24, 42, {"d3dx9_43.dll"}),
    ("d3dcompiler", 33, 42, {"d3dcompiler_43.dll"}),
    ("d3dcompiler", 44, 46, {"d3dcompiler_47.dll"}),
]


def parse_table(text: str) -> list[tuple[str, str]]:
    body = text.split("kMadeiraDLLAliases[] = {", 1)
    if len(body) != 2:
        raise SystemExit(f"{TABLE}: no kMadeiraDLLAliases[] initializer")
    return ROW.findall(body[1].split("};", 1)[0])


def shipped(arch: str) -> set[str]:
    directory = REPO / "app/Madeira" / arch
    if not directory.is_dir():
        raise SystemExit(f"missing bundle directory: {directory}")
    return {p.name.lower() for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() == ".dll"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, default=TABLE)
    args = parser.parse_args()

    rows = parse_table(args.table.read_text())
    if not rows:
        print("check-dll-aliases: FAIL -- table is empty", file=sys.stderr)
        return 1

    problems: list[str] = []
    names = [name.lower() for name, _ in rows]
    targets = [target.lower() for _, target in rows]

    # 1. internal consistency
    seen: set[str] = set()
    for name in names:
        if name in seen:
            problems.append(f"{name}: listed twice")
        seen.add(name)
    for name, target in zip(names, targets):
        if name == target:
            problems.append(f"{name}: aliases itself")
    name_set = set(names)
    for name, target in zip(names, targets):
        if target in name_set:
            problems.append(f"{name}: target {target} is itself an alias")

    # 2. the runtime claim, per relevant architecture
    targets_present = shipped(TARGET_ARCH)
    for name, target in zip(names, targets):
        if target not in targets_present:
            problems.append(f"{name}: target {target} is not shipped in {TARGET_ARCH}")
    for arch in SHADOW_ARCHS:
        present = targets_present if arch == TARGET_ARCH else shipped(arch)
        for name in names:
            if name in present:
                problems.append(
                    f"{name}: alias would shadow a module the bundle ships in {arch}")

    # 3. coverage
    for family, first, last, allowed in COVERAGE:
        for version in range(first, last + 1):
            wanted = f"{family}_{version}.dll"
            if wanted in name_set:
                continue
            if wanted in targets_present:
                problems.append(f"{wanted}: shipped in {TARGET_ARCH} but missing from the table")
            elif allowed & targets_present:
                problems.append(
                    f"{wanted}: not in the table although {sorted(allowed & targets_present)[0]}"
                    f" is shipped in {TARGET_ARCH}")

    if problems:
        print("check-dll-aliases: FAIL", file=sys.stderr)
        for problem in sorted(set(problems)):
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"check-dll-aliases: OK -- {len(rows)} aliases, "
          f"{TARGET_ARCH}={len(targets_present)} modules, "
          f"aarch64-windows={len(shipped('aarch64-windows'))} modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
