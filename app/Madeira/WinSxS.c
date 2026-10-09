// WinSxS.c -- see WinSxS.h.
#include "WinSxS.h"

#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

struct sxs_file { const char *in_assembly, *in_farm; };
struct sxs_assembly {
    const char *source;               // the Wine manifest these values come from
    const char *name, *lname, *key, *version;
    int window_classes;               // the comctl32 6.0 <windowClass> list
    struct sxs_file files[4];
};

// dlls/comctl32_v6/comctl32.manifest, in its order.
static const char *const comctl32_classes[] = {
    "Button", "ButtonListBox", "ComboBoxEx32", "ComboLBox", "ComboBox", "Edit", "ListBox",
    "NativeFontCtl", "ReBarWindow32", "ScrollBar", "Static", "SysAnimate32", "SysDateTimePick32",
    "SysHeader32", "SysIPAddress32", "SysLink", "SysListView32", "SysMonthCal32", "SysPager",
    "SysTabControl32", "SysTreeView32", "ToolbarWindow32", "msctls_hotkey32", "msctls_progress32",
    "msctls_statusbar32", "msctls_trackbar32", "msctls_updown32", "tooltips_class32",
};

// One entry per WINE_MANIFEST resource in the Wine tree; check-winsxs.py
// compares this table with those files.
static const struct sxs_assembly sxs_assemblies[] = {
    { "dlls/comctl32_v6/comctl32.manifest", "Microsoft.Windows.Common-Controls",
      "microsoft.windows.common-controls", "6595b64144ccf1df", "6.0.2600.2982", 1,
      { { "comctl32.dll", "comctl32_v6.dll" } } },
    { "dlls/msvcr80/msvcr80.manifest", "Microsoft.VC80.CRT", "microsoft.vc80.crt",
      "1fc8b3b9a1e18e3b", "8.0.50727.9672", 0,
      { { "msvcr80.dll", "msvcr80.dll" }, { "msvcp80.dll", "msvcp80.dll" },
        { "msvcm80.dll", "msvcm80.dll" } } },
    { "dlls/msvcr90/msvcr90.manifest", "Microsoft.VC90.CRT", "microsoft.vc90.crt",
      "1fc8b3b9a1e18e3b", "9.0.30729.6161", 0,
      { { "msvcr90.dll", "msvcr90.dll" }, { "msvcp90.dll", "msvcp90.dll" },
        { "msvcm90.dll", "msvcm90.dll" } } },
    { "dlls/atl80/atl80.manifest", "Microsoft.VC80.ATL", "microsoft.vc80.atl",
      "1fc8b3b9a1e18e3b", "8.0.50727.4053", 0, { { "atl80.dll", "atl80.dll" } } },
    { "dlls/atl90/atl90.manifest", "Microsoft.VC90.ATL", "microsoft.vc90.atl",
      "1fc8b3b9a1e18e3b", "9.0.30729.6161", 0, { { "atl90.dll", "atl90.dll" } } },
    { "dlls/gdiplus/gdiplus.manifest", "Microsoft.Windows.GdiPlus", "microsoft.windows.gdiplus",
      "6595b64144ccf1df", "1.0.6000.16386", 0, { { "gdiplus.dll", "gdiplus.dll" } } },
    { "dlls/gdiplus/gdiplus11.manifest", "Microsoft.Windows.GdiPlus", "microsoft.windows.gdiplus",
      "6595b64144ccf1df", "1.1.7601.23038", 0, { { "gdiplus.dll", "gdiplus.dll" } } },
    { "dlls/msxml3/msxml3.manifest", "Microsoft-Windows-MSXML30", "microsoft-windows-msxml30",
      "31bf3856ad364e35", "6.0.6000.16386", 0, { { "msxml3.dll", "msxml3.dll" } } },
    { "dlls/msxml4/msxml4.manifest", "Microsoft.MSXML2", "microsoft.msxml2",
      "6bd6b9abf345378f", "4.1.0.0", 0, { { "msxml4.dll", "msxml4.dll" } } },
    { "dlls/msxml6/msxml6.manifest", "Microsoft-Windows-MSXML60", "microsoft-windows-msxml60",
      "31bf3856ad364e35", "6.0.6000.16386", 0, { { "msxml6.dll", "msxml6.dll" } } },
};

#define SXS_COUNT (sizeof(sxs_assemblies) / sizeof(sxs_assemblies[0]))
#define SXS_FILES 4

// snprintf that reports truncation: a cut path must not be written to.
static int sxs_path(char *buf, size_t size, const char *fmt, ...)
{
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vsnprintf(buf, size, fmt, ap);
    va_end(ap);
    return n < 0 || (size_t)n >= size ? -1 : 0;
}

static int sxs_mkdir_p(const char *path)
{
    char tmp[PATH_MAX];
    size_t len = strlen(path);
    if (len == 0 || len >= sizeof tmp) return -1;
    memcpy(tmp, path, len + 1);
    for (char *p = tmp + 1; *p; p++) {
        if (*p != '/') continue;
        *p = 0;
        if (mkdir(tmp, 0755) && errno != EEXIST) return -1;
        *p = '/';
    }
    if (mkdir(tmp, 0755) && errno != EEXIST) return -1;
    return 0;
}

static int sxs_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;   // follows links: a dangling link is "absent"
}

// Removes what an earlier seed wrote for one assembly (its manifest, its links
// and the directory if that leaves it empty). Returns 1 if anything was there.
static int sxs_remove(const char *manifest, const char *dir, const struct sxs_assembly *a)
{
    char path[PATH_MAX];
    int any = unlink(manifest) == 0;
    for (int f = 0; f < SXS_FILES && a->files[f].in_assembly; f++) {
        if (sxs_path(path, sizeof path, "%s/%s", dir, a->files[f].in_assembly)) continue;
        if (unlink(path) == 0) any = 1;
    }
    if (rmdir(dir) == 0) any = 1;
    return any;
}

int madeira_winsxs_seed(const char *prefix, const char *farm, const char *arch,
                        madeira_winsxs_stats *stats)
{
    madeira_winsxs_stats st = { 0, 0, 0, (int)SXS_COUNT };
    char winsxs[PATH_MAX], manifests[PATH_MAX];

    if (stats) *stats = st;
    if (!prefix || !farm || !arch ||
        (strcmp(arch, "x86") && strcmp(arch, "arm64") && strcmp(arch, "amd64")))
        return -1;
    if (sxs_path(winsxs, sizeof winsxs, "%s/drive_c/windows/winsxs", prefix) ||
        sxs_path(manifests, sizeof manifests, "%s/manifests", winsxs) ||
        sxs_mkdir_p(manifests)) {
        fprintf(stderr, "[WineProc] winsxs: %s: cannot create %s (%s)\n", arch, manifests, strerror(errno));
        return -1;
    }

    for (size_t i = 0; i < SXS_COUNT; i++) {
        const struct sxs_assembly *a = &sxs_assemblies[i];
        char dirname[256], dir[PATH_MAX], manifest[PATH_MAX], tmp[PATH_MAX], src[PATH_MAX], link[PATH_MAX];
        FILE *out;
        int ok = 1;

        if (sxs_path(dirname, sizeof dirname, "%s_%s_%s_%s_none_deadbeef", arch, a->lname, a->key, a->version) ||
            sxs_path(dir, sizeof dir, "%s/%s", winsxs, dirname) ||
            sxs_path(manifest, sizeof manifest, "%s/%s.manifest", manifests, dirname) ||
            sxs_path(tmp, sizeof tmp, "%s.tmp", manifest) ||
            sxs_path(src, sizeof src, "%s/%s", farm, a->files[0].in_farm)) {
            st.failed++;
            continue;
        }

        // The assembly's first file is its reason to exist: without it in the
        // farm, a manifest would redirect that DLL's loads to nothing.
        if (!sxs_exists(src)) {
            sxs_remove(manifest, dir, a);
            st.absent++;
            continue;
        }
        if (sxs_mkdir_p(dir)) { st.failed++; continue; }

        // Manifest and directory are built together so the <file> list and
        // the directory cannot disagree. UTF-8, LF, no BOM, as fakedll.c
        // writes them (with processorArchitecture filled in).
        if (!(out = fopen(tmp, "w"))) { st.failed++; continue; }
        fprintf(out, "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                     "<assembly xmlns=\"urn:schemas-microsoft-com:asm.v1\" manifestVersion=\"1.0\">\n"
                     "  <assemblyIdentity type=\"win32\" name=\"%s\" version=\"%s\" "
                     "processorArchitecture=\"%s\" publicKeyToken=\"%s\"/>\n",
                a->name, a->version, arch, a->key);
        for (int f = 0; f < SXS_FILES && a->files[f].in_assembly; f++) {
            if (sxs_path(src, sizeof src, "%s/%s", farm, a->files[f].in_farm) ||
                sxs_path(link, sizeof link, "%s/%s", dir, a->files[f].in_assembly)) { ok = 0; break; }
            unlink(link);   // the bundle path changes on reinstall; the farm may have changed
            if (!sxs_exists(src)) continue;
            if (symlink(src, link)) { ok = 0; break; }
            if (a->window_classes) {
                fprintf(out, "  <file name=\"%s\">\n", a->files[f].in_assembly);
                for (size_t c = 0; c < sizeof(comctl32_classes) / sizeof(comctl32_classes[0]); c++)
                    fprintf(out, "    <windowClass>%s</windowClass>\n", comctl32_classes[c]);
                fprintf(out, "  </file>\n");
            } else {
                fprintf(out, "  <file name=\"%s\"/>\n", a->files[f].in_assembly);
            }
        }
        fprintf(out, "</assembly>\n");
        if (fclose(out) || !ok || rename(tmp, manifest)) {
            unlink(tmp);
            sxs_remove(manifest, dir, a);
            st.failed++;
            continue;
        }
        st.seeded++;
    }

    fprintf(stderr, "[WineProc] winsxs: %s: %d/%d assemblies seeded from %s, %d not in that farm, %d failed\n",
            arch, st.seeded, st.total, farm, st.absent, st.failed);
    if (stats) *stats = st;
    return 0;
}
