// LaunchDiagnostics.c -- see LaunchDiagnostics.h.
#include "LaunchDiagnostics.h"

#include <ctype.h>
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>

#define MD_MAX_PROBLEMS 24
#define MD_TEXT 240

struct md_stage_rec { int reached; double t; char detail[MD_TEXT]; };
struct md_problem {
    md_category cat;
    char stage[32];
    char key[96];        // what makes two lines the same problem (a DLL name, a message head)
    char error[MD_TEXT];
    char hint[MD_TEXT * 2];
    double t;
    unsigned count;
};

static pthread_mutex_t md_lock = PTHREAD_MUTEX_INITIALIZER;
static struct {
    int active;
    double t0;
    char exe[MD_TEXT];
    char dir[1024];
    char started_at[32];
    struct md_stage_rec stages[MD_STAGE_COUNT];
    struct md_problem problems[MD_MAX_PROBLEMS];
    unsigned nproblems, dropped;
    uint64_t present_base, presents;
    int present_base_set;
    int exit_status, exited;
    uint32_t crash_status;
    int dirty;
} md;

static const char *const md_stage_names[MD_STAGE_COUNT] = {
    "launch", "wine-started", "child-process", "graphics-api", "device", "metal-layer",
    "swapchain", "first-present", "gdi-window", "process-exit",
};
static const char *const md_cat_names[MD_CAT_COUNT] = {
    "none", "process-failure", "missing-dll", "graphics-device-failure", "swapchain-failure",
    "shader-translation-failure", "metal-present-failure", "window-visibility", "video-init-failure",
    "unimplemented-function", "dependency-load-failure", "architecture-mismatch", "wine-init-failure",
    "audio-init-failure", "unclassified",
};
static const char *const md_cat_titles[MD_CAT_COUNT] = {
    "No problem", "Process failure", "Missing DLL", "Graphics device failure", "Swapchain failure",
    "Shader translation failure", "Metal present failure", "Window not visible", "Video / media failure",
    "Unimplemented function", "Dependency could not load", "Wrong architecture", "Wine start-up failure",
    "Audio failure", "Unclassified error",
};
static const char *const md_cat_hints[MD_CAT_COUNT] = {
    "",
    "The program's process failed or ended before it showed a frame. A crash code (0xC0000005 access "
    "violation, 0xC000001D illegal instruction) points at Wine or the x86 emulator; a normal exit soon after "
    "start usually means the program refused to run (a missing file, a launcher, copy protection).",
    "Wine could not load a DLL the program imports. A Windows redistributable (msvcp*, vcruntime*, d3dx*, "
    "xinput*) is installed through the game's dependencies; a Wine module this build does not ship for this "
    "architecture has to be built and added to the DLL folder.",
    "The Direct3D device could not be created on Metal.",
    "The swapchain could not be created or could not be bound to the app's Metal layer.",
    "A shader did not translate to Metal or a pipeline did not build. Draws that use it are skipped, which "
    "can leave the frame black or incomplete.",
    "Frames were rendered but Metal did not hand out a drawable or did not show it.",
    "The window exists but is not drawn in a game session. A full-screen window that does not present "
    "through Direct3D (GDI, DirectDraw, OpenGL) has no Metal output here. A GDI or DirectDraw (2D) game: "
    "set MADEIRA_GAME_GDI_FULLSCREEN=1 for it, or use the desktop session; a 32-bit DirectDraw game can also "
    "run on cnc-ddraw over Direct3D 9 (MADEIRA_DDRAW=cnc, docs/DIRECTDRAW.md).",
    "A video or media component is missing or failed. A game that waits for an intro video can stay black "
    "(with or without sound).",
    "A DLL loaded, but a function the program calls is only a stub in this Wine build, or is not exported by "
    "the DLL that was found. This is a gap in the Windows layer itself (named in the error), not a missing "
    "file: replacing the DLL with a game-shipped one, or a newer Wine module, is the fix.",
    "The DLL exists, but loading it failed: one of its own imports is missing, its DllMain refused, or a "
    "side-by-side assembly (Visual C++ 2005/2008 CRT, Common Controls 6) it declares could not be found. "
    "The first missing-dll line, if any, names the real gap.",
    "A DLL of the wrong architecture was found first (STATUS_INVALID_IMAGE_FORMAT, c000007b): a 32-bit DLL "
    "beside a 64-bit program, or the reverse. Usually a game folder carrying both, or a DLL copied into the "
    "wrong place.",
    "Wine did not get as far as running the program: the executable could not be opened or mapped, or a core "
    "module (kernel32, start.exe) did not load. Check the path and that the file is a Windows program.",
    "An audio component failed to initialise. Some games stop, or wait silently, when XAudio2, DirectSound or "
    "the audio endpoint cannot be created; others continue without sound.",
    "An error matched no known shape; Documents/madeira-log.txt has the context.",
};

static double md_now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void md_copy(char *dst, size_t cap, const char *src, size_t n)
{
    size_t i, o = 0;
    if (!cap) return;
    if (!src) { dst[0] = 0; return; }
    for (i = 0; i < n && src[i] && o + 1 < cap; i++) {
        unsigned char c = (unsigned char)src[i];
        if (c == '\n' || c == '\r') break;
        dst[o++] = (c < 32 && c != '\t') ? '?' : (char)c;
    }
    dst[o] = 0;
}

const char *madeira_diag_stage_name(md_stage stage)
{
    return (unsigned)stage < MD_STAGE_COUNT ? md_stage_names[stage] : "?";
}

const char *madeira_diag_category_name(md_category cat)
{
    return (unsigned)cat < MD_CAT_COUNT ? md_cat_names[cat] : "?";
}

static md_category md_category_from_name(const char *s, size_t n)
{
    unsigned i;
    for (i = 0; i < MD_CAT_COUNT; i++)
        if (strlen(md_cat_names[i]) == n && !strncmp(md_cat_names[i], s, n)) return (md_category)i;
    return MD_CAT_UNCLASSIFIED;
}

/* "launch" .. "process-exit", plus the short names PE-side code uses. */
static int md_stage_from_name(const char *s, size_t n, md_stage *out)
{
    static const struct { const char *name; md_stage stage; } alias[] = {
        { "api", MD_STAGE_GRAPHICS_API }, { "layer", MD_STAGE_METAL_LAYER },
        { "present", MD_STAGE_FIRST_PRESENT }, { "exit", MD_STAGE_PROCESS_EXIT },
        { "child", MD_STAGE_CHILD_PROCESS }, { "wine", MD_STAGE_WINE_STARTED },
    };
    unsigned i;
    for (i = 0; i < MD_STAGE_COUNT; i++)
        if (strlen(md_stage_names[i]) == n && !strncmp(md_stage_names[i], s, n)) { *out = (md_stage)i; return 1; }
    for (i = 0; i < sizeof(alias) / sizeof(alias[0]); i++)
        if (strlen(alias[i].name) == n && !strncmp(alias[i].name, s, n)) { *out = alias[i].stage; return 1; }
    return 0;
}

void madeira_diag_reset(const char *exe, const char *out_dir)
{
    char prev[1100], cur[1100];
    time_t wall = time(NULL);
    struct tm tmv;

    pthread_mutex_lock(&md_lock);
    memset(&md, 0, sizeof md);
    md.active = 1;
    md.t0 = md_now();
    md_copy(md.exe, sizeof md.exe, exe ? exe : "(unknown)", (size_t)-1);
    if (out_dir && *out_dir) md_copy(md.dir, sizeof md.dir, out_dir, (size_t)-1);
    else {
        const char *docs = getenv("MADEIRA_DOCS_DIR");
        if (docs && *docs) snprintf(md.dir, sizeof md.dir, "%s/madeira-diagnostics", docs);
    }
    if (localtime_r(&wall, &tmv)) strftime(md.started_at, sizeof md.started_at, "%Y-%m-%d %H:%M:%S", &tmv);
    md.stages[MD_STAGE_LAUNCH].reached = 1;
    md.stages[MD_STAGE_LAUNCH].t = 0;
    md_copy(md.stages[MD_STAGE_LAUNCH].detail, MD_TEXT, md.exe, (size_t)-1);
    md.dirty = 1;
    if (md.dir[0]) {
        mkdir(md.dir, 0755);
        snprintf(cur, sizeof cur, "%s/last-launch.txt", md.dir);
        snprintf(prev, sizeof prev, "%s/previous-launch.txt", md.dir);
        rename(cur, prev);   /* the last launch stays readable after the next one starts */
    }
    pthread_mutex_unlock(&md_lock);
    madeira_diag_flush();
}

static void md_stage_locked(md_stage stage, const char *detail, size_t n)
{
    struct md_stage_rec *r;
    if ((unsigned)stage >= MD_STAGE_COUNT || !md.active) return;
    r = &md.stages[stage];
    if (r->reached) {
        /* the first time is the one that counts; a later detail only fills an empty one */
        if (!r->detail[0] && detail) md_copy(r->detail, sizeof r->detail, detail, n);
        return;
    }
    r->reached = 1;
    r->t = md_now() - md.t0;
    if (detail) md_copy(r->detail, sizeof r->detail, detail, n);
    md.dirty = 2;   /* a stage change is written at once */
}

void madeira_diag_stage(md_stage stage, const char *detail)
{
    int write_now;
    pthread_mutex_lock(&md_lock);
    md_stage_locked(stage, detail, (size_t)-1);
    write_now = md.dirty == 2;
    pthread_mutex_unlock(&md_lock);
    if (write_now) madeira_diag_flush();
}

/* Wine prefixes its debug lines with the thread id ("0124:err:..."); two
 * threads printing the same error are one problem. */
static const char *md_skip_tid(const char *s, size_t *len)
{
    size_t i = 0;
    while (i < *len && i < 8 && isxdigit((unsigned char)s[i])) i++;
    if (i >= 4 && i < *len && s[i] == ':') { *len -= i + 1; return s + i + 1; }
    return s;
}

static void md_problem_locked(md_category cat, const char *stage, const char *key, const char *error,
                              size_t error_len, const char *hint)
{
    unsigned i;
    struct md_problem *p;
    char k[96];
    if (!md.active) return;
    if (error) error = md_skip_tid(error, &error_len);
    if ((unsigned)cat >= MD_CAT_COUNT || cat == MD_CAT_NONE) cat = MD_CAT_UNCLASSIFIED;
    md_copy(k, sizeof k, key && *key ? key : error, key && *key ? (size_t)-1 : (error_len < 80 ? error_len : 80));
    for (i = 0; i < md.nproblems; i++) {
        p = &md.problems[i];
        if (p->cat == cat && !strcmp(p->key, k)) { p->count++; return; }
    }
    if (md.nproblems >= MD_MAX_PROBLEMS) { md.dropped++; return; }
    p = &md.problems[md.nproblems++];
    memset(p, 0, sizeof *p);
    p->cat = cat;
    md_copy(p->stage, sizeof p->stage, stage ? stage : "", (size_t)-1);
    memcpy(p->key, k, sizeof k);
    md_copy(p->error, sizeof p->error, error ? error : "", error_len);
    md_copy(p->hint, sizeof p->hint, hint && *hint ? hint : md_cat_hints[cat], (size_t)-1);
    p->t = md_now() - md.t0;
    p->count = 1;
    if (!md.dirty) md.dirty = 1;
}

void madeira_diag_problem(md_category cat, const char *stage_name, const char *error, const char *hint)
{
    pthread_mutex_lock(&md_lock);
    md_problem_locked(cat, stage_name, NULL, error, error ? strlen(error) : 0, hint);
    pthread_mutex_unlock(&md_lock);
}

/* --- log line recognition ------------------------------------------------ */

static const char *md_find(const char *line, const char *needle)
{
    return strstr(line, needle);
}

static int md_name_in(const char *name, const char *const *list)
{
    for (; *list; list++) {
        size_t n = strlen(*list);
        if ((*list)[n - 1] == '*') { if (!strncmp(name, *list, n - 1)) return 1; }
        else if (!strcmp(name, *list)) return 1;
    }
    return 0;
}

/* Hint for a DLL Wine could not load, from what the DLL is. */
static const char *md_dll_hint(const char *dll)
{
    static const char *const media[] = {
        "quartz.dll", "winegstreamer.dll", "mfmediaengine.dll", "mf.dll", "mfplat.dll", "mfreadwrite.dll",
        "evr.dll", "dxva2.dll", "wmvcore.dll", "devenum.dll", "amstream.dll", "msdmo.dll", "wmadmod.dll",
        "qedit.dll", "mfplay.dll", NULL };
    static const char *const no_backend[] = {
        "opengl32.dll", "wined3d.dll", "vulkan-1.dll", "d3drm.dll", NULL };
    static const char *const redist[] = {
        "msvcp*", "vcruntime*", "msvcr*", "concrt*", "vcomp*", "d3dx9_*", "d3dx10*", "d3dx11*", "d3dcompiler_*",
        "xinput*", "xaudio*", "x3daudio*", "xapofx*", "physxloader.dll", "physx*", NULL };
    if (md_name_in(dll, media))
        return "A media component. Both DLL folders ship Wine's DirectShow and Media Foundation modules (the 64-bit "
               "set is listed in build/wine-pe/arm64ec-farm.json); one reported missing here was left out of that "
               "list or of this build. 64-bit decoding additionally needs MADEIRA_WG_64BIT=1 (docs/MEDIA.md).";
    if (md_name_in(dll, (const char *const[]){ "d3d8.dll", NULL }))
        return "Direct3D 8 is in the 32-bit DLL folder only (third_party/d3d8to9 over DXMT's Direct3D 9, "
               "docs/D3D8.md); Windows never had a 64-bit d3d8.dll either. A build without it predates that "
               "or was made with SKIP_DXMT=1.";
    if (md_name_in(dll, (const char *const[]){ "ddraw.dll", NULL }))
        return "Wine's DirectDraw draws through wined3d, which has no backend in Madeira. A 32-bit DirectDraw "
               "game can run on cnc-ddraw over DXMT's Direct3D 9 instead: the cnc-ddraw recipe, or "
               "env.MADEIRA_DDRAW = cnc (docs/DIRECTDRAW.md).";
    if (md_name_in(dll, no_backend))
        return "This graphics API has no rendering backend in Madeira (no wined3d, OpenGL or Vulkan); only "
               "Direct3D 9, 10, 11 and 12 reach Metal.";
    if (md_name_in(dll, (const char *const[]){ "api-ms-win-*", "ext-ms-win-*", NULL }))
        return "An API set contract. Wine maps it through its schema (dlls/apisetschema) to a host module and loads "
               "that; this one is not in the schema, has no host there, or its host is not in this session's DLL "
               "folder (the 64-bit folder lacks windows.storage, twinapi.appcore and wintypes, among others). The "
               "game's compatibility view names which, from compat.json's wine_api_sets.";
    if (md_name_in(dll, (const char *const[]){ "msi.dll", "msiexec.exe", "scrrun.dll", "wshom.ocx", "jscript.dll",
                                               "vbscript.dll", "msxml4.dll", "hnetcfg.dll", NULL }))
        return "An installer or scripting component. Both DLL folders ship Wine's (the 64-bit ones are the "
               "installer_scripting group of build/wine-pe/arm64ec-farm.json); one reported missing here means "
               "this build predates that group or left it out.";
    if (md_name_in(dll, redist))
        return "A Windows redistributable the game expects to be installed. Wine answers most of them as builtins "
               "(the 64-bit versions shipped are listed in build/wine-pe/arm64ec-farm.json); for one that is not "
               "there, add it through the game's dependencies (compatibility engine) or place the DLL beside the game.";
    return NULL;
}

static void md_lower(char *s) { for (; *s; s++) *s = (char)tolower((unsigned char)*s); }

/* "Library foo.dll (which is needed by L"...") not found" -> foo.dll */
static void md_dll_from(const char *after, char *out, size_t cap)
{
    size_t n = 0;
    while (*after == ' ') after++;
    while (after[n] && after[n] != ' ' && after[n] != '(' && n + 1 < cap) n++;
    memcpy(out, after, n);
    out[n] = 0;
    md_lower(out);
}

/* "[madeira-diag] stage=swapchain ok=0 cat=swapchain-failure detail=..." */
static void md_feed_diag_locked(const char *p)
{
    const char *st = strstr(p, "stage="), *ok = strstr(p, "ok="), *cat = strstr(p, "cat="), *det = strstr(p, "detail=");
    size_t sn = 0, cn = 0;
    md_stage stage;
    int good = 1, have_stage;
    if (st) { st += 6; while (st[sn] && st[sn] != ' ') sn++; }
    if (ok) good = ok[3] != '0';
    if (cat) { cat += 4; while (cat[cn] && cat[cn] != ' ') cn++; }
    if (det) det += 7;
    have_stage = st && md_stage_from_name(st, sn, &stage);
    if (good) {
        if (have_stage) md_stage_locked(stage, det, (size_t)-1);
        return;
    }
    {
        md_category c = cat ? md_category_from_name(cat, cn) : MD_CAT_UNCLASSIFIED;
        char sname[32];
        if (!cat && have_stage) {
            switch (stage) {
            case MD_STAGE_DEVICE: case MD_STAGE_GRAPHICS_API: c = MD_CAT_DEVICE; break;
            case MD_STAGE_SWAPCHAIN: case MD_STAGE_METAL_LAYER: c = MD_CAT_SWAPCHAIN; break;
            case MD_STAGE_FIRST_PRESENT: c = MD_CAT_PRESENT; break;
            case MD_STAGE_GDI_WINDOW: c = MD_CAT_WINDOW; break;
            case MD_STAGE_LAUNCH: case MD_STAGE_WINE_STARTED: case MD_STAGE_CHILD_PROCESS:
            case MD_STAGE_PROCESS_EXIT: c = MD_CAT_PROCESS; break;
            default: break;
            }
        }
        md_copy(sname, sizeof sname, st ? st : "", sn);
        md_problem_locked(c, sname, NULL, det ? det : p, strlen(det ? det : p), NULL);
    }
}

static void md_feed_locked(const char *line)
{
    const char *p;
    size_t len = strlen(line);

    if ((p = md_find(line, "[madeira-diag] "))) { md_feed_diag_locked(p + 15); return; }

    /* Wine's loader (dlls/ntdll/loader.c, import_dll). Two messages:
     *   "Library X (which is needed by Y) not found"            -> X is not there
     *   "Loading library X (which is needed by Y) failed (error S)." -> X is there, loading it failed;
     * S says why: c000007b wrong architecture, c0000139 an entry point X lacks
     * (or one of X's own imports lacks), anything else (c0000135 a nested
     * import missing, c0000142 DllMain refused) a dependency problem. */
    if ((p = md_find(line, "Loading library ")) && md_find(line, "(which is needed by")) {
        const char *err = md_find(line, "failed (error ");
        char dll[64];
        md_dll_from(p + 16, dll, sizeof dll);
        if (!dll[0]) return;
        if (err && !strncmp(err + 14, "c000007b", 8))
            md_problem_locked(MD_CAT_ARCH, "dll-load", dll, line, len, NULL);
        else if (err && !strncmp(err + 14, "c0000139", 8))
            md_problem_locked(MD_CAT_UNIMPLEMENTED, "dll-load", dll, line, len,
                              "An entry point is missing: the DLL that was found (or one it imports) does not "
                              "export a function the importer names. A Windows layer this Wine build only has "
                              "in part, or an older copy of the DLL earlier on the search path.");
        else
            md_problem_locked(MD_CAT_DEPENDENCY, "dll-load", dll, line, len, NULL);
        return;
    }
    if (((p = md_find(line, "Library ")) || (p = md_find(line, "library "))) && md_find(line, "(which is needed by")) {
        char dll[64];
        md_dll_from(p + 8, dll, sizeof dll);
        if (dll[0]) md_problem_locked(MD_CAT_MISSING_DLL, "dll-load", dll, line, len, md_dll_hint(dll));
        return;
    }
    /* dlls/ntdll/exception.c: a winebuild stub entry was called */
    if ((p = md_find(line, "to unimplemented function "))) {
        char fn[96];
        md_dll_from(p + 26, fn, sizeof fn);
        if (fn[0] && fn[strlen(fn) - 1] == ',') fn[strlen(fn) - 1] = 0;
        /* TaskDialog / TaskDialogIndirect are exported by comctl32 6.0 only: a
         * stub here means the program's Common Controls 6 dependency was not
         * resolved, so comctl32 5.x was loaded (build/host-tests/check-winsxs.py). */
        md_problem_locked(MD_CAT_UNIMPLEMENTED, "running", fn[0] ? fn : "stub called", line, len,
                          !strncasecmp(fn, "comctl32.dll.TaskDialog", 23)
                          ? "Only Common Controls 6.0 exports this. The program asked for it in its manifest, but "
                            "C:\\windows\\winsxs had no Common Controls assembly for its architecture, so comctl32 5.x "
                            "was loaded. The '[WineProc] winsxs:' line of this launch says what was seeded."
                          : NULL);
        return;
    }
    /* dlls/ntdll/actctx.c: a side-by-side dependency (VC80/VC90 CRT, Common Controls 6) */
    if (md_find(line, "Could not find dependent assembly")) {
        md_problem_locked(MD_CAT_DEPENDENCY, "side-by-side", "dependent assembly not found", line, len,
                          "A side-by-side assembly the program's manifest declares is not in C:\\windows\\winsxs. "
                          "The loader still finds a CRT DLL in system32 when it is in the DLL folder, so this is "
                          "fatal only when that DLL is missing too. For Common-Controls 6.0 the program gets "
                          "comctl32 5.x instead, and a later TaskDialog call aborts.");
        return;
    }
    /* dlls/mscoree/metahost.c: a managed (.NET) program, and no Wine Mono */
    if (md_find(line, "Wine Mono is not installed")) {
        md_problem_locked(MD_CAT_DEPENDENCY, "dotnet", "Wine Mono is not installed", line, len,
                          "A .NET program: Wine runs it through Wine Mono, which the app does not ship (235 MB "
                          "unpacked). Put the unpacked wine-mono-" "11.0.0" "-x86.tar.xz from dl.winehq.org in "
                          "Documents/Components/wine-mono-11.0.0 (the Files app can copy a folder there); the next "
                          "launch links it into the prefix. The '[WineProc] wine-mono:' line says what was found. "
                          "docs/WINE_MONO.md.");
        return;
    }
    /* dlls/ntdll/unix/env.c, unix/loader.c, loader.c: Wine never reached the program */
    if (md_find(line, "wine: failed to start ") || md_find(line, "wine: failed to open ") ||
        md_find(line, "wine: failed to load start.exe") || md_find(line, "wine: could not load kernel32.dll")) {
        md_problem_locked(MD_CAT_WINE_INIT, "wine-start", NULL, line, len, NULL);
        return;
    }
    if ((p = md_find(line, "[Wine child] BOOT FAILED at stage"))) {
        md_problem_locked(MD_CAT_PROCESS, "child-process", "child boot failed", p, strlen(p),
                          "A process the program started could not boot inside Madeira; the stage named in the "
                          "message is where it stopped.");
        return;
    }
    if ((p = md_find(line, "spawn_process: creating child thread for "))) {
        md_stage_locked(MD_STAGE_CHILD_PROCESS, p + 41, (size_t)-1);
        return;
    }
    if (md_find(line, "spawn_process: pthread_create failed")) {
        md_problem_locked(MD_CAT_PROCESS, "child-process", "pthread_create failed", line, len, NULL);
        return;
    }
    if (md_find(line, "Unhandled exception") || md_find(line, "Unhandled page fault") ||
        md_find(line, "Unhandled illegal instruction")) {
        md_problem_locked(MD_CAT_PROCESS, "running", "unhandled exception", line, len, NULL);
        return;
    }

    /* Direct3D 12 runtime (research/madeira-d3d12/src/pe/madeira_d3d12.c) */
    if ((p = md_find(line, "[madeira-d3d12] device created"))) {
        md_stage_locked(MD_STAGE_GRAPHICS_API, "Direct3D 12 (madeira-d3d12)", (size_t)-1);
        md_stage_locked(MD_STAGE_DEVICE, "Direct3D 12", (size_t)-1);
        return;
    }
    if (md_find(line, "[madeira-d3d12] no Metal device or queue available")) {
        md_problem_locked(MD_CAT_DEVICE, "device", "d3d12 no metal device", line, len, NULL);
        return;
    }
    if ((p = md_find(line, "[madeira-d3d12] swapchain: "))) {
        md_stage_locked(MD_STAGE_SWAPCHAIN, p + 27, (size_t)-1);
        return;
    }
    if (md_find(line, "is not presentable")) {
        md_problem_locked(MD_CAT_SWAPCHAIN, "swapchain", "format not presentable", line, len, NULL);
        return;
    }
    if (md_find(line, "conversion failed:") || md_find(line, "pipeline FAILED") ||
        md_find(line, "newRenderPipelineState failed") || md_find(line, "newComputePipelineState failed") ||
        md_find(line, "Failed to compile shader")) {
        const char *head = md_find(line, "conversion failed:") ? "shader conversion failed"
                         : md_find(line, "Failed to compile shader") ? "d3d11 shader compile failed"
                         : "pipeline creation failed";
        md_problem_locked(MD_CAT_SHADER, "shader", head, line, len, NULL);
        return;
    }
    if (md_find(line, "the layer gave no drawable")) {
        md_problem_locked(MD_CAT_PRESENT, "present", "no drawable", line, len, NULL);
        return;
    }

    /* DirectDraw through cnc-ddraw (third_party/cnc-ddraw/madeira/madeira_log.c,
     * MADEIRA_DDRAW=cnc; docs/DIRECTDRAW.md), and the app's own line when the
     * build lacks it. Before DXMT's d3d9 lines, so the API stage names it. */
    if ((p = md_find(line, "[WineProc] cnc-ddraw: requested, but"))) {
        md_problem_locked(MD_CAT_DEPENDENCY, "dll", "cnc-ddraw not in this build", line, len,
                          "MADEIRA_DDRAW=cnc (or the cnc-ddraw recipe) was set, but the app bundle has no "
                          "cnc-ddraw/ddraw.dll, so Wine's ddraw ran. build/wine-i386/build.sh builds it "
                          "(build/ddraw/build.sh).");
        return;
    }
    if ((p = md_find(line, "[cnc-ddraw] "))) {
        p += 12;
        if (!strncmp(p, "renderer ", 9)) {
            const char *r = p + 9;
            if (!strncmp(r, "direct3d9", 9))
                md_stage_locked(MD_STAGE_GRAPHICS_API, "DirectDraw (cnc-ddraw over DXMT's Direct3D 9)", (size_t)-1);
            else if (!strncmp(r, "opengl", 6)) {
                md_stage_locked(MD_STAGE_GRAPHICS_API, "DirectDraw (cnc-ddraw, OpenGL renderer)", (size_t)-1);
                md_problem_locked(MD_CAT_DEVICE, "graphics-api", "cnc-ddraw: OpenGL renderer", line, len,
                                  "cnc-ddraw chose OpenGL, which has no backend here, so the picture stays black. "
                                  "Set renderer=direct3d9 in C:\\ProgramData\\cnc-ddraw\\ddraw.ini ([ddraw] or the "
                                  "game's own section; delete the file to get Madeira's copy back).");
            } else
                md_stage_locked(MD_STAGE_GRAPHICS_API, "DirectDraw (cnc-ddraw, GDI renderer)", (size_t)-1);
        } else if (!strncmp(p, "Direct3D 9 device ", 18)) {
            md_stage_locked(MD_STAGE_DEVICE, "DirectDraw via Direct3D 9 (cnc-ddraw)", (size_t)-1);
        } else if (md_find(p, "d3d9.dll could not be loaded")) {
            md_problem_locked(MD_CAT_DEPENDENCY, "dll", "cnc-ddraw: d3d9.dll did not load", line, len,
                              "cnc-ddraw presents through d3d9.dll (DXMT's, in the 32-bit DLL folder), which did "
                              "not load; it falls back to GDI. Check that the 32-bit DLL folder has d3d9.dll and "
                              "d3d9-emulated.dll (build/wine-i386/build.sh).");
        } else if (md_find(p, "Direct3DCreate9 returned NULL")) {
            md_problem_locked(MD_CAT_DEVICE, "device", "cnc-ddraw: no Direct3D 9", line, len,
                              "DXMT's Direct3DCreate9 returned nothing, so cnc-ddraw falls back to GDI (visible "
                              "in a game session only with MADEIRA_GAME_GDI_FULLSCREEN=1, which the cnc-ddraw "
                              "recipe sets).");
        } else if (md_find(p, "CreateDevice failed") || md_find(p, "could not be created") ||
                   md_find(p, "setting its states failed")) {
            md_problem_locked(MD_CAT_DEVICE, "device", "cnc-ddraw: Direct3D 9 device failed", line, len,
                              "cnc-ddraw's Direct3D 9 device (or its textures / ps_2_0 palette shader) was refused "
                              "by DXMT; cnc-ddraw falls back to GDI. The d3d9 lines next to it have the reason. "
                              "renderer=gdi in C:\\ProgramData\\cnc-ddraw\\ddraw.ini skips the attempt.");
        } else if (md_find(p, "falling back to GDI")) {
            /* replaces the "over Direct3D 9" detail the renderer line set */
            static const char fb[] = "DirectDraw (cnc-ddraw, GDI fallback)";
            md_stage_locked(MD_STAGE_GRAPHICS_API, fb, (size_t)-1);
            md_copy(md.stages[MD_STAGE_GRAPHICS_API].detail, sizeof md.stages[0].detail, fb, (size_t)-1);
            md.dirty = 2;
        }
        return;
    }

    /* Direct3D 8 over Direct3D 9 (third_party/d3d8to9, madeira_log.hpp). Before
     * DXMT's own d3d9 lines, so the API stage names the Direct3D 8 path. */
    if ((p = md_find(line, "[d3d8to9] "))) {
        if (md_find(p, "Direct3DCreate9 failed")) {
            md_problem_locked(MD_CAT_DEVICE, "device", "d3d8: no Direct3D 9 runtime", line, len,
                              "Direct3D 8 is translated to Direct3D 9 (DXMT's d3d9.dll); Direct3DCreate9 returned "
                              "nothing, so the game gets no Direct3D 8 either. Check that the 32-bit DLL folder "
                              "has d3d9.dll and d3d9-emulated.dll (build/wine-i386/build.sh).");
        } else if (md_find(p, "Direct3DCreate8(")) {
            md_stage_locked(MD_STAGE_GRAPHICS_API, "Direct3D 8 (d3d8to9 over DXMT's Direct3D 9)", (size_t)-1);
        } else if (md_find(p, "CreateDevice ")) {
            const char *hr = md_find(p, "-> hr 0x");
            if (hr && hr[8] == '0' && (hr[9] == 0 || hr[9] == ' ' || hr[9] == '\r' || hr[9] == '\n'))
                md_stage_locked(MD_STAGE_DEVICE, "Direct3D 8", (size_t)-1);
            else if (hr)
                md_problem_locked(MD_CAT_DEVICE, "device", "d3d8 CreateDevice failed", line, len,
                                  "IDirect3D8::CreateDevice was passed to Direct3D 9 and refused for these "
                                  "presentation parameters; the d3d9 line next to it has the reason.");
        } else if (md_find(p, "CreateVertexShader:") || md_find(p, "CreatePixelShader:")) {
            md_problem_locked(MD_CAT_SHADER, "shader", "d3d8 shader refused", line, len,
                              "A Direct3D 8 shader was not accepted: either it is not valid shader model 1 "
                              "bytecode (the reason is in the line) or the Direct3D 9 runtime refused the "
                              "translated shader. Draws that use it are skipped.");
        } else if (md_find(p, "d3dx9_43.dll did not load")) {
            md_problem_locked(MD_CAT_DEPENDENCY, "dll", "d3d8: d3dx9_43 missing", line, len,
                              "Only CopyRects between different surface formats needs it; the 32-bit DLL folder "
                              "normally ships Wine's d3dx9_43.");
        }
        return;
    }

    /* DXMT (research/dxmt) */
    if (md_find(line, "Using feature level")) {   /* d3d11.cpp D3D11CoreCreateDevice */
        md_stage_locked(MD_STAGE_GRAPHICS_API, "Direct3D 10/11 (DXMT)", (size_t)-1);
        return;
    }
    if (md_find(line, "[d3d11-fail]") || md_find(line, "Minimum required feature level") ||
        md_find(line, "Not a DXMT adapter")) {
        md_problem_locked(MD_CAT_DEVICE, "device", "d3d11 device failed", line, len, NULL);
        return;
    }
    if ((p = md_find(line, "[d3d9-modes] CreateDevice"))) {   /* d3d9_interface.cpp LogPresentRequest */
        const char *hr = md_find(p, "-> hr 0x");
        md_stage_locked(MD_STAGE_GRAPHICS_API, "Direct3D 9 (DXMT)", (size_t)-1);
        if (hr && hr[8] == '0' && (hr[9] == 0 || hr[9] == ' ' || hr[9] == '\r' || hr[9] == '\n')) {
            md_stage_locked(MD_STAGE_DEVICE, "Direct3D 9", (size_t)-1);
            md_stage_locked(MD_STAGE_SWAPCHAIN, p + 13, (size_t)-1);
        } else if (hr) {
            md_problem_locked(MD_CAT_DEVICE, "device", "d3d9 CreateDevice failed", line, len,
                              "IDirect3D9::CreateDevice returned an error for these presentation parameters "
                              "(back buffer size, format, windowed); the game may retry with others.");
        }
        return;
    }
    /* Madeira's bounded main-thread hops (winemetal execute_on_main, Winios
     * winios_metal_layer_for_hwnd) and the freeze detector's main-thread probe. */
    if (md_find(line, "[madeira-main-hop]")) {
        md_problem_locked(MD_CAT_SWAPCHAIN, "main-thread", "main thread did not answer", line, len,
                          "A guest thread waited for the iOS main thread (a Metal layer update or a window's "
                          "layer) and it did not answer in time. Before the bounded hop this was a permanent "
                          "hang at device / swapchain creation with nothing logged; now the update is applied "
                          "anyway (or the swapchain fails and the game may retry). Repeats mean the main thread "
                          "is busy or blocked: see the [freeze] MAIN THREAD lines and the thread stacks in the log.");
        return;
    }
    if (md_find(line, "[freeze] MAIN THREAD unresponsive")) {
        md_problem_locked(MD_CAT_SWAPCHAIN, "main-thread", "main thread unresponsive", line, len,
                          "The iOS main thread ran nothing for seconds while the app was in the foreground. "
                          "Everything that has to touch UIKit or Core Animation waits behind it, so a game can "
                          "stall at device or swapchain creation; the thread stacks dumped next to this line name "
                          "what the main thread is doing.");
        return;
    }
    /* DXMT's IMPLEMENT_ME / UNIMPLEMENTED: "<file>:<function> is not implemented."
     * (d3d11_private.h). In the prebuilt DLLs the next thing it does is abort(). */
    if ((md_find(line, "d3d11_") || md_find(line, "d3d10_") || md_find(line, "dxgi_")) &&
        md_find(line, ".cpp:") && (md_find(line, " is not implemented.") || md_find(line, ": \"todo\"") ||
                                   md_find(line, "runs into an unreachable path"))) {
        md_problem_locked(MD_CAT_UNIMPLEMENTED, "d3d11", "DXMT call not implemented", line, len,
                          "The game called a Direct3D 11 / DXGI method DXMT does not implement (named in the line). "
                          "DXMT ends the process there (abort, exit code 3); a game with its own crash handler "
                          "(Unity, Unreal) can instead stay alive in that handler with no frame. Methods that "
                          "only query or annotate were given their Windows answers in "
                          "patches/dxmt-ios-no-hang.patch; a d3d11.dll built before it still aborts.");
        return;
    }
    if (md_find(line, "nextDrawable #") && md_find(line, "BLOCKED")) {
        md_problem_locked(MD_CAT_PRESENT, "present", "nextDrawable blocked", line, len,
                          "CAMetalLayer.nextDrawable waited long: queued frames are not being shown, so the "
                          "layer ran out of drawables. When this repeats about once a second the layer is not "
                          "being composited (a detached or hidden view), which looks like a black screen.");
        return;
    }
    if (md_find(line, "Failed to create metal view") || md_find(line, "no exported symbols needed by DXMT") ||
        md_find(line, "[madeira-display] no window data for hwnd")) {
        md_problem_locked(MD_CAT_SWAPCHAIN, "swapchain", "no metal view", line, len,
                          "The swapchain got no Metal layer for its window: the app's game layer was not "
                          "registered yet, or the macdrv_functions export is missing from this build.");
        return;
    }
    if (md_find(line, "CAMetalLayerInvalid") || md_find(line, "invalid pixel format") ||
        md_find(line, "CAMetalLayer refused pixel format")) {
        md_problem_locked(MD_CAT_SWAPCHAIN, "swapchain", "layer pixel format", line, len,
                          "The Metal layer refused the swapchain's pixel format. Madeira gives the layer the "
                          "closest documented format (BGRA8, BGRA8 sRGB, RGBA16Float, RGB10A2, BGR10A2, XR) and "
                          "converts each frame; if the screen stays black, this format is the first suspect.");
        return;
    }

    /* The app (IOSDisplayShim.m, Winios.m) */
    if (md_find(line, "called before layer registered") || md_find(line, "game layer was not registered") ||
        md_find(line, "desktop metal layer creation failed")) {
        md_problem_locked(MD_CAT_SWAPCHAIN, "metal-layer", "layer not registered", line, len,
                          "A swapchain asked for the game layer before the game view had registered it.");
        return;
    }
    if (md_find(line, "[winios] game window") && md_find(line, "not drawn (covers the guest desktop)")) {
        md_problem_locked(MD_CAT_WINDOW, "window", "full-desktop window not drawn", line, len, NULL);
        return;
    }

    /* Media */
    if (md_find(line, "using stub table") && md_find(line, "winegstreamer")) {
        md_problem_locked(MD_CAT_VIDEO, "video", "winegstreamer stub", line, len,
                          "winegstreamer has no media backend for this caller: 64-bit callers get one only with "
                          "MADEIRA_WG_64BIT=1 (the arm64ec winegstreamer.dll itself is shipped; docs/MEDIA.md).");
        return;
    }
    if (md_find(line, "GL-absent stub table")) {
        md_stage_locked(MD_STAGE_GRAPHICS_API, "OpenGL (no backend)", (size_t)-1);
        md_problem_locked(MD_CAT_DEVICE, "graphics-api", "opengl no backend", line, len,
                          "The program loaded opengl32: OpenGL has no rendering backend in Madeira, so an "
                          "OpenGL game cannot show a frame.");
        return;
    }
    if (md_find(line, "err:xaudio2") || md_find(line, "err:xact3") || md_find(line, "err:mmdevapi") ||
        md_find(line, "err:dsound") || md_find(line, "err:winmm") || md_find(line, "err:msacm")) {
        md_problem_locked(MD_CAT_AUDIO, "audio", NULL, line, len, NULL);
        return;
    }
    if (md_find(line, "err:quartz") || md_find(line, "err:mfplat") || md_find(line, "err:mf:") ||
        md_find(line, "err:winegstreamer") || md_find(line, "err:wmvcore") || md_find(line, "err:mfmediaengine") ||
        md_find(line, "err:strmbase") || (md_find(line, "[wg-parser]") && (md_find(line, "fail") || md_find(line, "error")))) {
        md_problem_locked(MD_CAT_VIDEO, "video", NULL, line, len, NULL);
        return;
    }
    if ((p = md_find(line, "class {")) && md_find(line, "not registered")) {
        /* combase: CLSID_FilterGraph (DirectShow) and the MF media engine are the media classes games create */
        if (md_find(line, "e436ebb3-524f-11ce-9f53-0020af0ba770") || md_find(line, "b44392da-499b-446b-a4cb-005fead0e6d5"))
            md_problem_locked(MD_CAT_VIDEO, "video", NULL, line, len, NULL);
        else
            md_problem_locked(MD_CAT_UNCLASSIFIED, "com", NULL, p, strlen(p),
                              "A COM class the program created is not registered in the prefix.");
        return;
    }
}

void madeira_diag_feed_line(const char *line)
{
    int write_now;
    if (!line || !md.active) return;
    /* Cheap gate: every recognised line carries one of these. Most of a
     * session's log is frame and trace output that has none of them. */
    if (!strstr(line, "err:") && !strstr(line, "[madeira-") && !strstr(line, "[iOS DXMT]") &&
        !strstr(line, "[winios]") && !strstr(line, "[Wine child]") && !strstr(line, "spawn_process") &&
        !strstr(line, "Unhandled") && !strstr(line, "stub table") && !strstr(line, "Library ") &&
        !strstr(line, "shader") && !strstr(line, "CAMetalLayer") && !strstr(line, "pixel format") &&
        !strstr(line, "[wg-parser]") && !strstr(line, "metal view") && !strstr(line, "feature level") &&
        !strstr(line, "[d3d9-modes] CreateDevice") && !strstr(line, "DXMT adapter") &&
        !strstr(line, "wine: ") && !strstr(line, "dependent assembly") && !strstr(line, "[d3d8to9] ") &&
        !strstr(line, "cnc-ddraw") && !strstr(line, "[freeze] MAIN THREAD"))
        return;
    pthread_mutex_lock(&md_lock);
    md_feed_locked(line);
    write_now = md.dirty == 2;
    pthread_mutex_unlock(&md_lock);
    if (write_now) madeira_diag_flush();
}

void madeira_diag_note_present_count(uint64_t count)
{
    int write_now = 0;
    pthread_mutex_lock(&md_lock);
    if (md.active) {
        if (!md.present_base_set) { md.present_base = count; md.present_base_set = 1; }
        if (count > md.present_base) {
            md.presents = count - md.present_base;
            if (!md.stages[MD_STAGE_FIRST_PRESENT].reached) {
                md_stage_locked(MD_STAGE_FIRST_PRESENT, "a frame was presented to the Metal layer", (size_t)-1);
                write_now = 1;
            }
        }
    }
    pthread_mutex_unlock(&md_lock);
    if (write_now) madeira_diag_flush();
}

void madeira_diag_process_exit(int status, uint32_t crash_status)
{
    char detail[96];
    pthread_mutex_lock(&md_lock);
    if (md.active && !md.exited) {
        md.exited = 1;
        md.exit_status = status;
        md.crash_status = crash_status;
        if (crash_status) snprintf(detail, sizeof detail, "ended with Windows error 0x%08X", crash_status);
        else snprintf(detail, sizeof detail, "exit code %d", status);
        md_stage_locked(MD_STAGE_PROCESS_EXIT, detail, (size_t)-1);
        if (crash_status)
            md_problem_locked(MD_CAT_PROCESS, "running", "crash", detail, strlen(detail), NULL);
        else if (!md.stages[MD_STAGE_FIRST_PRESENT].reached && !md.stages[MD_STAGE_CHILD_PROCESS].reached)
            md_problem_locked(MD_CAT_PROCESS, "running", "exit before first frame", detail, strlen(detail),
                              "The program ended on its own before it presented a frame and started no other "
                              "process. Look for a missing DLL or file above, or a message the program showed.");
    }
    pthread_mutex_unlock(&md_lock);
    madeira_diag_flush();
}

/* --- verdict and report --------------------------------------------------- */

static md_category md_verdict_locked(void)
{
    static const md_category order[] = {
        MD_CAT_WINE_INIT, MD_CAT_MISSING_DLL, MD_CAT_ARCH, MD_CAT_UNIMPLEMENTED, MD_CAT_DEPENDENCY,
        MD_CAT_PROCESS, MD_CAT_DEVICE, MD_CAT_SWAPCHAIN, MD_CAT_PRESENT, MD_CAT_WINDOW, MD_CAT_SHADER,
        MD_CAT_VIDEO, MD_CAT_AUDIO, MD_CAT_UNCLASSIFIED };
    unsigned i, j;
    if (md.stages[MD_STAGE_FIRST_PRESENT].reached) return MD_CAT_NONE;
    for (i = 0; i < sizeof(order) / sizeof(order[0]); i++)
        for (j = 0; j < md.nproblems; j++)
            if (md.problems[j].cat == order[i]) return order[i];
    return md.exited ? MD_CAT_PROCESS : MD_CAT_UNCLASSIFIED;
}

md_category madeira_diag_verdict(void)
{
    md_category c;
    pthread_mutex_lock(&md_lock);
    c = md_verdict_locked();
    pthread_mutex_unlock(&md_lock);
    return c;
}

int madeira_diag_reached(md_stage stage)
{
    int r;
    if ((unsigned)stage >= MD_STAGE_COUNT) return 0;
    pthread_mutex_lock(&md_lock);
    r = md.stages[stage].reached;
    pthread_mutex_unlock(&md_lock);
    return r;
}

struct md_out { char *buf; size_t cap, len; };

static void md_put(struct md_out *o, const char *fmt, ...)
{
    va_list ap;
    int n;
    if (!o->cap || o->len + 1 >= o->cap) return;
    va_start(ap, fmt);
    n = vsnprintf(o->buf + o->len, o->cap - o->len, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    o->len += (size_t)n;
    if (o->len >= o->cap) o->len = o->cap - 1;
}

/* What the furthest stage says when no problem names a cause. */
static const char *md_stall_hint_locked(void)
{
    if (md.exited) return "The process ended before it presented a frame.";
    if (md.stages[MD_STAGE_SWAPCHAIN].reached)
        return "A swapchain exists but nothing was presented yet: the game is still loading, waiting for "
               "something (a video, a launcher window, input), or rendering is stuck before Present.";
    if (md.stages[MD_STAGE_METAL_LAYER].reached)
        return "A swapchain asked for the Metal layer and got it, but its creation was never reported as done "
               "and nothing was presented: swapchain creation or the first Present did not finish (a [madeira-main-hop] "
               "line means it waited for the main thread), or the game is still loading.";
    if (md.stages[MD_STAGE_DEVICE].reached)
        return "A Direct3D device exists but no swapchain was created yet: the game is loading, or it waits "
               "for something before it creates its window's swapchain.";
    if (md.stages[MD_STAGE_GRAPHICS_API].reached)
        return "A Direct3D runtime started creating a device, and neither the end of that nor a swapchain was "
               "reported. A d3d11.dll without the [madeira-diag] lines (the prebuilt DLLs) never reports the "
               "device, so this is ALSO what a game looks like that created its device and then waits or loads "
               "before its swapchain: it does not prove device creation hung. The [madeira-main-hop], [freeze] and "
               "\"is not implemented\" lines and the thread stacks in the log tell the two apart.";
    if (md.stages[MD_STAGE_GDI_WINDOW].reached)
        return "Only GDI windows (a launcher or a dialog) were shown: the game may be waiting for a click there.";
    if (md.stages[MD_STAGE_CHILD_PROCESS].reached)
        return "The program started another process (a launcher starting the game); no Direct3D device yet.";
    if (md.stages[MD_STAGE_WINE_STARTED].reached)
        return "The program runs but created no Direct3D device yet: it is still starting, waiting, or it "
               "uses an API with no backend here (DirectDraw's 3D path, OpenGL, Vulkan).";
    return "Wine has not started the program yet.";
}

size_t madeira_diag_render_text(char *buf, size_t cap)
{
    struct md_out o = { buf, cap, 0 };
    md_category v;
    unsigned i;
    if (cap) buf[0] = 0;
    pthread_mutex_lock(&md_lock);
    v = md_verdict_locked();
    md_put(&o, "Madeira launch diagnostics\n");
    md_put(&o, "Program:  %s\n", md.exe[0] ? md.exe : "(none)");
    md_put(&o, "Started:  %s (device time)\n", md.started_at);
    md_put(&o, "Process:  %s\n", !md.active ? "not launched"
                                : md.exited ? "ended" : "running (this alone does not mean the game is visible)");
    if (md.stages[MD_STAGE_FIRST_PRESENT].reached)
        md_put(&o, "Display:  FIRST FRAME PRESENTED after %.1f s (%llu frames since)\n",
               md.stages[MD_STAGE_FIRST_PRESENT].t, (unsigned long long)md.presents);
    else
        md_put(&o, "Display:  NO FRAME PRESENTED%s\n", md.stages[MD_STAGE_GDI_WINDOW].reached ? " (GDI windows only)" : "");
    if (v == MD_CAT_NONE)
        md_put(&o, "Verdict:  reached the first frame%s\n", md.nproblems ? " (problems below may still explain a "
                                                                          "black or broken picture)" : "");
    else
        md_put(&o, "Verdict:  %s [%s]\n", md_cat_titles[v], md_cat_names[v]);
    md_put(&o, "\nStages (time since launch):\n");
    for (i = 0; i < MD_STAGE_COUNT; i++) {
        const struct md_stage_rec *r = &md.stages[i];
        if (r->reached) md_put(&o, "  [x] %-14s t+%7.2fs  %s\n", md_stage_names[i], r->t, r->detail);
        else md_put(&o, "  [ ] %s\n", md_stage_names[i]);
    }
    if (!md.stages[MD_STAGE_FIRST_PRESENT].reached)
        md_put(&o, "\nWhere it stopped: %s\n", md_stall_hint_locked());
    md_put(&o, "\nProblems (%u%s):\n", md.nproblems, md.dropped ? ", more were dropped" : "");
    if (!md.nproblems) md_put(&o, "  none recognised\n");
    for (i = 0; i < md.nproblems; i++) {
        const struct md_problem *p = &md.problems[i];
        md_put(&o, "- %s [%s] at %s, t+%.2fs\n", md_cat_titles[p->cat], md_cat_names[p->cat],
               p->stage[0] ? p->stage : "?", p->t);
        if (p->count > 1) md_put(&o, "  seen %u times\n", p->count);
        md_put(&o, "  error: %s\n", p->error);
        md_put(&o, "  hint:  %s\n", p->hint);
    }
    md_put(&o, "\nThe full log is Documents/madeira-log.txt. This report is rebuilt from what the log and the "
               "runtime reported; it names where a launch stopped, not always why.\n");
    pthread_mutex_unlock(&md_lock);
    return o.len;
}

static void md_put_json_string(struct md_out *o, const char *s)
{
    md_put(o, "\"");
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') md_put(o, "\\%c", c);
        else if (c < 32) md_put(o, "\\u%04x", c);
        else md_put(o, "%c", c);
    }
    md_put(o, "\"");
}

size_t madeira_diag_render_json(char *buf, size_t cap)
{
    struct md_out o = { buf, cap, 0 };
    md_category v;
    unsigned i, first = 1;
    if (cap) buf[0] = 0;
    pthread_mutex_lock(&md_lock);
    v = md_verdict_locked();
    md_put(&o, "{\"exe\":");
    md_put_json_string(&o, md.exe);
    md_put(&o, ",\"started\":");
    md_put_json_string(&o, md.started_at);
    md_put(&o, ",\"process_running\":%s,\"process_exited\":%s,\"exit_status\":%d,\"crash_status\":%u",
           md.active && !md.exited ? "true" : "false", md.exited ? "true" : "false", md.exit_status, md.crash_status);
    md_put(&o, ",\"first_frame_presented\":%s,\"presents\":%llu",
           md.stages[MD_STAGE_FIRST_PRESENT].reached ? "true" : "false", (unsigned long long)md.presents);
    if (md.stages[MD_STAGE_FIRST_PRESENT].reached)
        md_put(&o, ",\"time_to_first_frame\":%.3f", md.stages[MD_STAGE_FIRST_PRESENT].t);
    md_put(&o, ",\"verdict\":\"%s\",\"stages\":[", md_cat_names[v]);
    for (i = 0; i < MD_STAGE_COUNT; i++) {
        if (!md.stages[i].reached) continue;
        md_put(&o, "%s{\"name\":\"%s\",\"t\":%.3f,\"detail\":", first ? "" : ",", md_stage_names[i], md.stages[i].t);
        md_put_json_string(&o, md.stages[i].detail);
        md_put(&o, "}");
        first = 0;
    }
    md_put(&o, "],\"problems\":[");
    for (i = 0; i < md.nproblems; i++) {
        const struct md_problem *p = &md.problems[i];
        md_put(&o, "%s{\"category\":\"%s\",\"stage\":", i ? "," : "", md_cat_names[p->cat]);
        md_put_json_string(&o, p->stage);
        md_put(&o, ",\"t\":%.3f,\"count\":%u,\"error\":", p->t, p->count);
        md_put_json_string(&o, p->error);
        md_put(&o, ",\"hint\":");
        md_put_json_string(&o, p->hint);
        md_put(&o, "}");
    }
    md_put(&o, "]}\n");
    pthread_mutex_unlock(&md_lock);
    return o.len;
}

static void md_write_file(const char *dir, const char *name, const char *data, size_t len)
{
    char path[1100], tmp[1110];
    FILE *f;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    f = fopen(tmp, "w");
    if (!f) return;
    fwrite(data, 1, len, f);
    if (fclose(f) == 0) rename(tmp, path);
    else remove(tmp);
}

void madeira_diag_flush(void)
{
    static pthread_mutex_t write_lock = PTHREAD_MUTEX_INITIALIZER;
    char dir[1024];
    char *text;
    size_t n;
    pthread_mutex_lock(&md_lock);
    if (!md.active || !md.dirty || !md.dir[0]) { pthread_mutex_unlock(&md_lock); return; }
    md.dirty = 0;
    memcpy(dir, md.dir, sizeof dir);
    pthread_mutex_unlock(&md_lock);

    text = malloc(64 * 1024);
    if (!text) return;
    pthread_mutex_lock(&write_lock);
    n = madeira_diag_render_text(text, 64 * 1024);
    md_write_file(dir, "last-launch.txt", text, n);
    n = madeira_diag_render_json(text, 64 * 1024);
    md_write_file(dir, "last-launch.json", text, n);
    pthread_mutex_unlock(&write_lock);
    free(text);
}
