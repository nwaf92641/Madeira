#!/bin/sh
# Pre-release gate: run every consistency check that guards a shipped artifact.
#
# Each check here exists because the thing it guards has already shipped broken
# once:
#   - jit-script-sync: the JIT debugger script lives base64-embedded in
#     StikJITHelper.swift and madeira-jit.js is NOT in the Xcode target, so
#     editing the file changes nothing until the literal is regenerated.
#   - check-prefix-template: the prefix template shipped absolute host symlinks
#     that dangle on every device (ml719).
#
# Run from the repo root before tagging/packaging a release. Exits non-zero on
# the first failure.
set -e
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

echo "== JIT script sync =="
tools/jit-script-sync.py

echo "== prefix template =="
tools/check-prefix-template.sh
