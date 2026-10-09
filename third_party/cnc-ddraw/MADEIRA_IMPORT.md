# cnc-ddraw import

Source: https://github.com/FunkyFr3sh/cnc-ddraw at commit
279a057ee7e1e4d584b56141c014ab01f3c6ee6d (2026-08-30, "Merge pull request
#497 from auntyellow/dump_ddsd_all"). License: MIT (`LICENSE`).

Imported verbatim (`git archive` of that commit), except these paths, which
are left out of this repository:

| Path | Why |
|---|---|
| `inc/ddraw.h`, `inc/d3dcaps.h` | Copies of Microsoft's DirectX SDK headers ("Copyright (C) Microsoft Corporation. All Rights Reserved."), not under cnc-ddraw's MIT license. The build uses llvm-mingw's own `ddraw.h` / `d3dcaps.h` instead (see `MADEIRA_CHANGES.md`). |
| `config/` | The Windows settings program (C++Builder project). Not part of the DLL. |
| `src/detours/` | Microsoft Detours (MIT). Compiled only by the MSVC project (`#ifdef _MSC_VER` in `src/directinput.c`); the mingw build that Madeira uses never includes it. |
| `.github/` | CI configuration. |

Madeira does not let cnc-ddraw write its own ddraw.ini at run time. It ships
one generated at build time from the text of `cfg_create_ini` in
`src/config.c` (`build/ddraw/make-ini.py`), with `renderer=direct3d9` and the
other changes listed in `MADEIRA_CHANGES.md` and `docs/DIRECTDRAW.md`.
