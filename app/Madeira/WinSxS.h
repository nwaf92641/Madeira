// WinSxS.h -- C:\windows\winsxs for the prefix: the side-by-side assemblies
// Wine ships (Common Controls 6, the VC80/VC90 CRT and ATL, GDI+, MSXML).
//
// A normal Wine prefix gets these from wineboot's fake-DLL install
// (dlls/setupapi/fakedll.c). Madeira never runs that install: the prefix comes
// from prefix-template.tar.gz, whose build strips winsxs
// (scripts/build-prefix-snapshot.sh), and the DLLs are linked from the app
// bundle's farms at launch. Without the store, a program whose manifest asks
// for Microsoft.Windows.Common-Controls 6.0 gets comctl32 5.x, and every
// import that only 6.0 exports (TaskDialog, TaskDialogIndirect) is bound to a
// stub: the program starts, then aborts with "unimplemented function
// COMCTL32.dll.TaskDialogIndirect" the moment it calls one. A VC80/VC90 CRT
// dependency still finds msvcr80/msvcr90 in system32, so that part is not
// fatal under Wine (no c0150002 here: Wine's loader does not refuse a process
// whose activation context cannot be built). build/host-tests/check-winsxs.py
// shows both, under a real Wine.
//
// For each assembly this writes what fakedll.c writes:
//   windows\winsxs\manifests\<DIR>.manifest
//   windows\winsxs\<DIR>\<file>          (a link to the farm's DLL)
//   <DIR> = <arch>_<lower-case name>_<publicKeyToken>_<version>_none_deadbeef
// with <arch> also in processorArchitecture. ntdll's lookup (actctx.c,
// lookup_winsxs) pins major.minor and accepts any build/revision >= the one
// requested, so one assembly per major.minor serves every service pack.
//
// Architectures: "x86" for 32-bit programs (from the i386 farm), "arm64" for
// 64-bit ones. An ARM64EC ntdll is built with current_archW "arm64", so it
// resolves processorArchitecture="*" to arm64_ directories, and it rewrites an
// "amd64_" lookup to "a??64_", so an x64 program's explicit "amd64" request
// finds them too. The arm64_ set mirrors the farm of the session being started
// (arm64ec-windows or aarch64-windows), the same way system32 does: a plain
// ARM64 process cannot load an ARM64EC DLL, so an assembly whose DLL is not
// in the session's farm is removed rather than left pointing at the other one.
// "amd64" is accepted for the host test (an x86_64 Wine).
//
// Plain C, no Foundation, so the host test compiles and runs it.
#ifndef MADEIRA_WINSXS_H
#define MADEIRA_WINSXS_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int seeded;    // assemblies whose manifest and links were written
    int absent;    // assemblies whose DLL is not in the farm (left out, stale copies removed)
    int failed;    // assemblies that could not be written
    int total;     // assemblies known
} madeira_winsxs_stats;

// prefix: the Wine prefix (the directory holding drive_c).
// farm:   the directory the DLLs are linked from (the bundle's i386-windows,
//         arm64ec-windows or aarch64-windows).
// arch:   "x86", "arm64" or "amd64".
// Returns 0, or -1 if the arguments are wrong or winsxs\manifests cannot be
// created. Writes one "[WineProc] winsxs: ..." summary line to stderr.
int madeira_winsxs_seed(const char *prefix, const char *farm, const char *arch,
                        madeira_winsxs_stats *stats);

#ifdef __cplusplus
}
#endif

#endif
