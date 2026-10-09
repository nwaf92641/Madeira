/*
 * One-line diagnostics for Madeira's launch log.
 *
 * Copyright 2026 the Madeira contributors
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Upstream d3d8to9 writes a d3d8.log file in the working directory and shows
 * message boxes. On Madeira the launch record (app/Madeira/LaunchDiagnostics.c)
 * reads the session's stderr, and a modal dialog on an iPad is a session that
 * looks frozen. So the few lines that tell a launch where it stopped go through
 * Wine's debug output (ntdll's __wine_dbg_output, which writes to the unix
 * stderr Madeira captures; the same route DXMT's logger takes), always prefixed
 * "[d3d8to9] " so the launch diagnostics can recognise them. Off Wine,
 * OutputDebugStringA.
 */
#pragma once

#include <windows.h>
#include <cstdarg>
#include <cstdio>

namespace madeira_d3d8 {

inline void log(const char *format, ...)
{
	typedef int (__cdecl *PFN_wine_dbg_output)(const char *);
	static PFN_wine_dbg_output output = nullptr;
	static bool resolved = false;
	if (!resolved) {
		const HMODULE ntdll = GetModuleHandleA("ntdll.dll");
		if (ntdll)
			output = reinterpret_cast<PFN_wine_dbg_output>(GetProcAddress(ntdll, "__wine_dbg_output"));
		resolved = true;
	}
	char line[512];
	int n = snprintf(line, sizeof(line), "[d3d8to9] ");
	va_list args;
	va_start(args, format);
	vsnprintf(line + n, sizeof(line) - n - 2, format, args);
	va_end(args);
	size_t len = strlen(line);
	line[len] = '\n';
	line[len + 1] = 0;
	if (output)
		output(line);
	else
		OutputDebugStringA(line);
}

} // namespace madeira_d3d8
