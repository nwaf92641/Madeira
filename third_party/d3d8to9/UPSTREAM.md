# d3d8to9 (vendored)

- Upstream: https://github.com/crosire/d3d8to9
- Commit: `255338f698c8270b537f0a91a13f795f4f988250` (2026-09-24)
- License: BSD-2-Clause, Copyright (C) 2015 Patrick Mours (`LICENSE.md`).

This directory is imported verbatim from that commit (`source/`, `res/`,
`LICENSE.md`, `README.md`, `CMakeLists.txt`); the Visual Studio project files
and the git history are left out. Madeira's changes are made in later commits
and listed in `MADEIRA_CHANGES.md`, so `git log -- third_party/d3d8to9` shows
the upstream state and each change separately.

Why this and not Winlator's choice: Winlator answers `d3d8.dll` with D8VK
(DXVK's `src/d3d8`, zlib), which is a Direct3D 8 frontend over DXVK's own
Direct3D 9 implementation and talks to it through DXVK-private bridge
interfaces (`IDxvkD3D8Bridge`), i.e. it needs DXVK's d3d9 and therefore Vulkan.
Madeira's Direct3D 9 is DXMT's (Metal). d3d8to9 needs only the public
`IDirect3D9` / `IDirect3DDevice9` interfaces, so it sits on DXMT's `d3d9.dll`
unchanged. See `docs/D3D8.md`.
