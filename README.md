# Madeira

Run Windows PC games on a non-jailbroken iPhone.

Madeira combines [Wine](https://www.winehq.org/) (ARM64EC),
[FEX-Emu](https://github.com/FEX-Emu/FEX) for x86-64 → ARM64 translation, and
[DXMT](https://github.com/3Shain/DXMT) for D3D11 → Metal, running as a single
Mach process on iOS with wineserver as a thread rather than a separate process.

## Status

Thumper and ULTRAKILL are playable. Marvel Cosmic Invasion has reached
gameplay, though a run has also ended in an unexplained termination and its
controls are not yet reliable. Others reach gameplay at low frame rates. This
is a research project, not a product: expect rough edges, per-title quirks and
breaking changes.

## Requirements

- A non-jailbroken iPhone. Development has been on an A15 (iPhone 13 Pro).
- JIT, which on iOS requires a debugger to attach —
  [StikDebug](https://github.com/0-Blu/StikJIT) is what this project uses.
- An Apple ID for signing. A free account works; its provisioning profiles
  expire after 7 days, so the app must be rebuilt and reinstalled weekly. The
  app's container survives reinstall, so prefixes and saves are preserved.

Because JIT requires debugger attach, this app cannot be distributed through the
App Store. It is installed by sideloading.

## Building

The build is split across several chains — the unix-side Wine libraries, the
ARM64EC PE modules, FEX, DXMT and the iOS app itself. `build/*/build.sh` covers
the native pieces; the app is built with `xcodebuild`.

```sh
git clone --recurse-submodules <this repo>
```

Note that `FEX`, `wine` and `research/dxmt` are submodules pointing at forks
containing the iOS work; upstream clones will not build here.

Before shipping a build, run the release gates:

```sh
tools/check-all.sh
```

They fail if the JIT script embedded in `StikJITHelper.swift` has drifted from
`app/Madeira/madeira-jit.js` (that file is not in the Xcode target, so the
embedded copy is what actually runs — an unregenerated edit silently ships
nothing), if `prefix-template.tar.gz` contains absolute host symlinks, if a
source file exists without being registered in the Xcode project, or if the
device-capability policy tables regress.

## Per-device tuning

The emulator was developed on an A15, but the guest translator is not pinned to
it: `xtajit64.dll` reads the real chip's features at runtime. What *was* fixed
to the development device is the allocation the app makes before Wine starts.

`DeviceCapabilities` derives the JIT translation-cache size from the device's
jetsam budget. It stays at exactly 896 MB at or below the A15's 4096 MB budget —
the only configuration that has been validated on hardware — and scales up to
1792 MB on devices with more memory, so the cache can hold more translated code
before it has to evict. That is a plausible win rather than a measured one: it
has not yet been benchmarked on an A17/A18 or an M-series device. Devices at or
below 4096 MB behave exactly as before.

Three files in the app's Documents directory override behaviour without a
rebuild, which matters because installing requires a cable and a debugger:

| File | Effect |
|---|---|
| `madeira-pool.txt` | JIT pool size in MB (256–3072) |
| `madeira-resolution.txt` | Desktop size, `WIDTHxHEIGHT` (e.g. `1280x720`) |
| `madeira-fex.txt` | `KEY=VALUE` lines, exported as `FEX_<KEY>` for the translator |

Deleting a file restores the default. Malformed entries in `madeira-fex.txt` are
reported in the log rather than applied, because a wrong translator setting does
not fail loudly — it produces a bad run.

## License

**GPL-3.0-or-later** — see [`LICENSE`](LICENSE). Derivatives that are
distributed must remain open source.

### Upstream licenses vs. this project's forks

Those are the licenses of the **upstream projects**: Wine and GnuTLS
LGPL-2.1-or-later, GMP and Nettle LGPL-3.0-or-later, FEX-Emu and DXMT MIT,
rpmalloc 0BSD. Their texts are in [`LICENSES/`](LICENSES), and upstream code
remains available under them **from upstream**.

**The forks used here are not licensed identically to their upstreams.** Each
carries its own `LICENSE-MADEIRA.md` saying exactly what applies:

| Fork | Terms |
|---|---|
| [`wine`](https://github.com/willfaust/wine) | relicensed to **GPL-3.0-or-later** under LGPL-2.1 §3 |
| [`FEX`](https://github.com/willfaust/FEX), [`dxmt`](https://github.com/willfaust/dxmt) | upstream MIT preserved; modifications **GPL-3.0-or-later** |
| [`rpmalloc`](https://github.com/willfaust/rpmalloc) | upstream 0BSD preserved; Will Faust's modifications **GPL-3.0-or-later** |

This is not retroactive: those forks were public beforehand, so anything
already obtained under a permissive license stays available under it.

[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) has the per-component
breakdown. Note in particular that the Microsoft Visual C++ runtime DLLs are
not distributed here and must be supplied yourself — see
[`tools/fetch-vcruntime.md`](tools/fetch-vcruntime.md).

## A note on upstream contributions

The forks here contain substantial AI-assisted work. FEX-Emu's contribution
policy states that AI must not be used to generate code for contributions to
that project, so **do not submit AI-generated changes from this fork upstream**.
The MIT license permits the fork itself; the policy governs contributions back.
Check each upstream's contribution policy before proposing changes to it.
