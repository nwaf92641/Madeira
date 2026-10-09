/*
 * Madeira: see madeira_log.h.
 *
 * Copyright 2026 the Madeira contributors
 * SPDX-License-Identifier: MIT
 */
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "madeira_log.h"
#include "render_d3d9.h"
#include "render_gdi.h"
#include "render_ogl.h"

void madeira_log(const char *format, ...)
{
    typedef int (__cdecl *wine_dbg_output_fn)(const char *);
    static wine_dbg_output_fn output;
    static BOOL resolved;
    char line[512];
    va_list args;
    int n;
    size_t len;

    if (!resolved)
    {
        HMODULE ntdll = GetModuleHandleA("ntdll.dll");
        if (ntdll)
            output = (wine_dbg_output_fn)(void *)GetProcAddress(ntdll, "__wine_dbg_output");
        resolved = TRUE;
    }
    n = _snprintf(line, sizeof(line) - 2, "[cnc-ddraw] ");
    va_start(args, format);
    _vsnprintf(line + n, sizeof(line) - n - 2, format, args);
    va_end(args);
    line[sizeof(line) - 2] = 0;
    len = strlen(line);
    line[len] = '\n';
    line[len + 1] = 0;
    if (output)
        output(line);
    else
        OutputDebugStringA(line);
}

const char *madeira_renderer_name(void *renderer)
{
    if (renderer == (void *)d3d9_render_main) return "direct3d9";
    if (renderer == (void *)gdi_render_main) return "gdi";
    if (renderer == (void *)ogl_render_main) return "opengl";
    return "none";
}
