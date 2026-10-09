// WineMono.c -- see WineMono.h.
#include "WineMono.h"

#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MONO_DIR_NAME "wine-mono-" MADEIRA_WINE_MONO_VERSION

static int is_file(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

static int is_dir(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static int mkdirs(char *path)
{
    for (char *p = path + 1; *p; p++) {
        if (*p != '/') continue;
        *p = 0;
        int r = mkdir(path, 0755);
        *p = '/';
        if (r && errno != EEXIST) return -1;
    }
    return (mkdir(path, 0755) && errno != EEXIST) ? -1 : 0;
}

// What mscoree's find_mono_dll looks for, for both bitnesses, and the class
// libraries load_mono points MONO_PATH at.
static int complete(const char *dir)
{
    char p[PATH_MAX + 64];
    snprintf(p, sizeof p, "%s/bin/libmono-2.0-x86.dll", dir);
    if (!is_file(p)) return 0;
    snprintf(p, sizeof p, "%s/bin/libmono-2.0-x86_64.dll", dir);
    if (!is_file(p)) return 0;
    snprintf(p, sizeof p, "%s/lib/mono", dir);
    return is_dir(p);
}

// Another wine-mono-* folder in components, for the log line.
static int other_version(const char *components, char *name, size_t size)
{
    DIR *d = components ? opendir(components) : NULL;
    struct dirent *e;
    int found = 0;
    if (!d) return 0;
    while ((e = readdir(d))) {
        if (!strncmp(e->d_name, "wine-mono-", 10) && strcmp(e->d_name, MONO_DIR_NAME)) {
            snprintf(name, size, "%s", e->d_name);
            found = 1;
            break;
        }
    }
    closedir(d);
    return found;
}

madeira_wine_mono_state madeira_wine_mono_link(const char *prefix, const char *components)
{
    char target[PATH_MAX], parent[PATH_MAX], source[PATH_MAX], current[PATH_MAX], other[256];
    struct stat st;
    int have_link;

    if (!prefix || !*prefix) {
        dprintf(2, "[WineProc] wine-mono: no prefix\n");
        return MADEIRA_WINE_MONO_FAILED;
    }
    if (strlen(prefix) > PATH_MAX - 64 || (components && strlen(components) > PATH_MAX - 64)) {
        dprintf(2, "[WineProc] wine-mono: path too long\n");
        return MADEIRA_WINE_MONO_FAILED;
    }
    snprintf(parent, sizeof parent, "%s/drive_c/windows/mono", prefix);
    snprintf(target, sizeof target, "%s/drive_c/windows/mono/mono-2.0", prefix);
    have_link = lstat(target, &st) == 0 && S_ISLNK(st.st_mode);

    if (!have_link && lstat(target, &st) == 0) {
        dprintf(2, "[WineProc] wine-mono: the prefix has its own install (C:\\windows\\mono\\mono-2.0), left alone%s\n",
                complete(target) ? "" : " (it lacks bin/libmono-2.0-x86{,_64}.dll or lib/mono)");
        return MADEIRA_WINE_MONO_IN_PREFIX;
    }

    if (components && *components)
        snprintf(source, sizeof source, "%s/" MONO_DIR_NAME, components);
    else
        source[0] = 0;

    if (!source[0] || !is_dir(source) || !complete(source)) {
        madeira_wine_mono_state state = MADEIRA_WINE_MONO_ABSENT;
        // A link to a component that is gone (or broken) would only make
        // mscoree look in an empty place; remove it so the state is plain.
        if (have_link) unlink(target);
        if (source[0] && is_dir(source)) {
            state = MADEIRA_WINE_MONO_INCOMPLETE;
            dprintf(2, "[WineProc] wine-mono: %s is incomplete (needs bin/libmono-2.0-x86.dll, "
                       "bin/libmono-2.0-x86_64.dll and lib/mono); .NET programs cannot start\n", source);
        } else if (other_version(components, other, sizeof other)) {
            state = MADEIRA_WINE_MONO_WRONG_VERSION;
            dprintf(2, "[WineProc] wine-mono: found %s, but this Wine needs " MONO_DIR_NAME
                       "; .NET programs cannot start\n", other);
        } else {
            dprintf(2, "[WineProc] wine-mono: not installed (.NET programs need Documents/Components/"
                       MONO_DIR_NAME ", docs/WINE_MONO.md)\n");
        }
        return state;
    }

    if (have_link) {
        ssize_t n = readlink(target, current, sizeof current - 1);
        if (n > 0) {
            current[n] = 0;
            if (!strcmp(current, source)) {
                dprintf(2, "[WineProc] wine-mono: " MADEIRA_WINE_MONO_VERSION " linked from %s\n", source);
                return MADEIRA_WINE_MONO_LINKED;
            }
        }
        unlink(target);   // left by an earlier install (the bundle / container path moves)
    }
    if (mkdirs(parent) || symlink(source, target)) {
        dprintf(2, "[WineProc] wine-mono: could not link %s -> %s (errno %d)\n", target, source, errno);
        return MADEIRA_WINE_MONO_FAILED;
    }
    dprintf(2, "[WineProc] wine-mono: " MADEIRA_WINE_MONO_VERSION " linked from %s\n", source);
    return MADEIRA_WINE_MONO_LINKED;
}
