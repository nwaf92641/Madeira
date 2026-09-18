#!/usr/bin/env python3
"""Report the Windows PE modules the bundle ships against the compat manifest.

Only ~120 DLLs ship in app/Madeira/arm64ec-windows, and that set is the imports
of Wine's own test executables rather than a compatibility decision, so the
common case of "this game does not start" is a LoadLibrary of a module that was
never built. tools/pe-module-manifest.txt is the list of modules a title
actually imports and the bundle does not ship; this gate says how much of it is
still missing, and validates the architecture of everything that is shipped.

Two kinds in the manifest:

  wine    Wine builds it, so scripts/build-wine-pe-modules.sh can produce it.
          --strict fails on a missing one, which is what the PE-module workflow
          runs after it builds them.
  native  Wine has no implementation (Microsoft's MFC, riched30, d3dcsx, the
          DirectPlay servers). Always reported, never failed: closing those
          needs a redistributable component like app/Madeira/x86_64-vcruntime,
          and a build cannot conjure one.

Without --strict the exit status only covers the invariants that hold on a
source-only checkout: every shipped module's machine word matches its directory.
That is the half this gate can insist on today; the missing-module half is
printed on every run so the gap stays visible in CI instead of being rediscovered
title by title.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "tools/pe-module-manifest.txt"
VALIDATOR = REPO / "tools/validate-ios-bundle.py"
BUNDLE = REPO / "app/Madeira"

# The machine word each directory must carry. arm64ec-windows is 0x8664 because
# ARM64EC images are marked AMD64 -- the hybrid's whole point is that unmodified
# x64 tooling and the loader accept them -- so this is the same check
# tools/validate-ios-bundle.py applies to the modules it knows by name, applied
# here to every file in the directory.
ARCHS = (("arm64ec-windows", 0x8664), ("aarch64-windows", 0xAA64))


def parse_manifest(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0] not in ("wine", "native"):
            raise SystemExit(f"{path}:{number}: expected `<wine|native> <module>`, got {line!r}")
        entries.append((parts[0], parts[1].lower()))
    if not entries:
        raise SystemExit(f"{path}: no entries")
    return entries


def shipped(directory: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in directory.iterdir():
        if path.is_file() and path.suffix.lower() == ".dll":
            modules[path.stem.lower()] = path
    return modules


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--strict", action="store_true",
                        help="fail when a `wine` module is missing from the bundle")
    args = parser.parse_args()

    entries = parse_manifest(args.manifest)
    validator = runpy.run_path(str(VALIDATOR))

    problems: list[str] = []
    present: dict[str, dict[str, Path]] = {}
    for arch, machine in ARCHS:
        directory = BUNDLE / arch
        if not directory.is_dir():
            raise SystemExit(f"missing bundle directory: {directory}")
        modules = shipped(directory)
        present[arch] = modules
        for stem, path in sorted(modules.items()):
            try:
                validator["pe"](path.read_bytes(), machine)
            except Exception as error:  # noqa: BLE001 - the validator raises ValueError
                problems.append(f"{arch}/{path.name}: {error}")

    reference = "arm64ec-windows"
    others = [arch for arch, _ in ARCHS if arch != reference]
    missing_wine = [stem for kind, stem in entries
                    if kind == "wine" and stem not in present[reference]]
    missing_native = [stem for kind, stem in entries
                      if kind == "native" and stem not in present[reference]]
    # Present for one guest and absent for the other is its own defect: which
    # session a title breaks in then depends on the architecture of its
    # executable, and the failing set is invisible from either directory alone.
    asymmetric = [(stem, [arch for arch in others if stem in present[arch]])
                  for kind, stem in entries
                  if kind == "wine" and stem in present[reference] and stem not in present[others[0]]]

    if problems:
        print("check-pe-module-set: FAIL", file=sys.stderr)
        for problem in sorted(set(problems)):
            print(f"  {problem}", file=sys.stderr)
        return 1

    def wrap(label: str, items: list[str]) -> None:
        line = f"  {label}"
        for item in items:
            if len(line) + len(item) + 1 > 100:
                print(line)
                line = "    " + item
            else:
                line += " " + item
        print(line)

    wines = sum(1 for kind, _ in entries if kind == "wine")
    natives = sum(1 for kind, _ in entries if kind == "native")
    print(f"check-pe-module-set: shipped {len(present[reference])} modules in {reference}, "
          f"{len(present['aarch64-windows'])} in aarch64-windows; machine words verified")
    print(f"  gap manifest: {wines} wine modules, "
          f"{wines - len(missing_wine)} present / {len(missing_wine)} still missing; "
          f"{natives} native modules, {len(missing_native)} missing (needs a component, not a build)")
    if missing_wine:
        wrap("wine modules not shipped:", sorted(missing_wine))
    if asymmetric:
        wrap("shipped for one architecture only:",
             sorted(f"{arch}/{stem}.dll" for stem, archs in asymmetric for arch in archs))
    if missing_native:
        wrap("native modules not shipped:", sorted(missing_native))

    if args.strict and missing_wine:
        print(f"check-pe-module-set: FAIL -- {len(missing_wine)} wine modules missing "
              f"from {reference}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
