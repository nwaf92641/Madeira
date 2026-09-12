#!/bin/bash
# Complete DXMT build, shared by the standalone and full IPA workflows.
set -euo pipefail
BUILD_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$BUILD_DIR/../.." && pwd)"
APP_PE="$REPO_ROOT/app/Madeira/aarch64-windows"

bash "$REPO_ROOT/scripts/prepare-wine-ios.sh"
bash "$BUILD_DIR/build-llvm.sh"
bash "$BUILD_DIR/build.sh"

# The four PE DLLs are committed and shipped with the app, so rebuilding them
# only replaces known-good binaries that were built against the same Wine
# revision with a second opinion -- a needless way to break D3D. Build them
# only when they are absent (a fresh fork, or a deliberate DXMT update).
rebuild_pe=0
for dll in d3d11 dxgi winemetal d3d10core; do
    [[ -s "$APP_PE/$dll.dll" ]] || rebuild_pe=1
done
if [[ "$rebuild_pe" == 1 ]]; then
    echo "DXMT PE DLLs missing from app/Madeira/aarch64-windows/ -- building them."
    bash "$REPO_ROOT/scripts/prepare-wine-ios.sh" --dxmt-pe
    bash "$BUILD_DIR/build-pe.sh"
else
    echo "DXMT PE DLLs already shipped in app/Madeira/aarch64-windows/ -- leaving them alone."
fi
