/*
 * Madeira: one-line diagnostics for the launch log.
 *
 * Copyright 2026 the Madeira contributors
 * SPDX-License-Identifier: MIT
 *
 * cnc-ddraw's own log is a debug-build file. On Madeira the launch record
 * (app/Madeira/LaunchDiagnostics.c) reads the session's stderr, so the lines
 * that say which renderer was chosen and why a Direct3D 9 one failed go
 * through Wine's debug output (ntdll's __wine_dbg_output, as DXMT and
 * d3d8to9 do), prefixed "[cnc-ddraw] ". Off Wine, OutputDebugStringA.
 */
#pragma once
#include <windows.h>

void madeira_log(const char *format, ...);
const char *madeira_renderer_name(void *renderer);
