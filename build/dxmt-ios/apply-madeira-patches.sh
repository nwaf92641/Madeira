#!/bin/bash
# Apply this repository's DXMT patches (patches/dxmt-*.patch) to the
# research/dxmt submodule before it is built.
#
# DXMT is a submodule pinned to willfaust/dxmt; changes this repository needs
# before they land there are kept as patches here. Each patch is applied once:
# one that is already in the tree (it reverse-applies) is skipped, and one
# that neither applies nor reverse-applies stops the build, because building
# without it would ship a runtime the app code does not expect.
#
# Called by build/dxmt-ios/build.sh (unix side) and
# build/madeira-d3d12/build-pe.sh; run it by hand before the PE meson build
# (build/dxmt-ios/README.md).
set -eu
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DXMT_ROOT="$REPO_ROOT/research/dxmt"
shopt -s nullglob
for p in "$REPO_ROOT"/patches/dxmt-*.patch; do
    name=$(basename "$p")
    if git -C "$DXMT_ROOT" apply --reverse --check "$p" 2>/dev/null; then
        echo "  $name: already applied"
    elif git -C "$DXMT_ROOT" apply --check "$p" 2>/dev/null; then
        git -C "$DXMT_ROOT" apply "$p"
        echo "  $name: applied"
    else
        echo "ERROR: $name neither applies to nor is already in research/dxmt" >&2
        echo "       ($(git -C "$DXMT_ROOT" rev-parse --short HEAD 2>/dev/null || echo '?'); the patch is made against a5e0cd3)" >&2
        exit 1
    fi
done
