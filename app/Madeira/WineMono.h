// WineMono.h -- Wine Mono (.NET) for the prefix, as an optional component.
//
// Wine runs a managed (.NET) program through mscoree, which needs Wine Mono:
// the runtime, its class libraries and the x86 / x86_64 libmono DLLs. A
// desktop Wine installs it into the prefix from share/wine/mono at prefix
// creation; Madeira does not ship it (wine-mono-11.0.0-x86.tar.xz is 41 MiB,
// 235 MiB unpacked) and does not run that install, so every .NET program
// stopped at "err:mscoree:CLRRuntimeInfo_GetRuntimeHost Wine Mono is not
// installed".
//
// The component is the unpacked tarball, placed by the user (or a later
// download step) at Documents/Components/wine-mono-<MADEIRA_WINE_MONO_VERSION>.
// At launch this links C:\windows\mono\mono-2.0 to it, which is the first
// place mscoree looks (dlls/mscoree/metahost.c, get_mono_path_local: it
// wants <dir>\bin\libmono-2.0-x86.dll in 32-bit code and
// <dir>\bin\libmono-2.0-x86_64.dll in 64-bit and ARM64EC code, which clang
// builds with __x86_64__ defined). Nothing is copied: the link costs nothing
// and the component can be removed from the Files app at any time, after
// which the next launch removes the dangling link.
//
// A real install in the prefix (a directory, not a link) is left alone.
// The version must be the one this Wine asks for (WINE_MONO_VERSION in
// dlls/mscoree/mscoree_private.h); build/host-tests/check-wine-mono.py checks.
//
// Plain C, no Foundation, so the host test compiles and runs it.
#ifndef MADEIRA_WINE_MONO_H
#define MADEIRA_WINE_MONO_H

#ifdef __cplusplus
extern "C" {
#endif

#define MADEIRA_WINE_MONO_VERSION "11.0.0"

typedef enum {
    MADEIRA_WINE_MONO_LINKED = 0,     // the component is linked (or already was)
    MADEIRA_WINE_MONO_IN_PREFIX = 1,  // the prefix has its own install; left alone
    MADEIRA_WINE_MONO_ABSENT = 2,     // no component; managed programs cannot start
    MADEIRA_WINE_MONO_WRONG_VERSION = 3, // a wine-mono-* folder, not the version this Wine needs
    MADEIRA_WINE_MONO_INCOMPLETE = 4, // the folder lacks bin/libmono-2.0-x86{,_64}.dll or lib/mono
    MADEIRA_WINE_MONO_FAILED = -1,    // bad arguments, or the link could not be made
} madeira_wine_mono_state;

// prefix:     the Wine prefix (the directory holding drive_c).
// components: the directory optional components are placed in
//             (Documents/Components); may not exist.
// Writes one "[WineProc] wine-mono: ..." line to stderr and returns the state.
madeira_wine_mono_state madeira_wine_mono_link(const char *prefix, const char *components);

#ifdef __cplusplus
}
#endif
#endif
