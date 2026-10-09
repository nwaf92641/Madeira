/*
 * Madeira: <ddraw.h> for cnc-ddraw without Microsoft's SDK copy.
 *
 * Copyright 2026 the Madeira contributors
 * SPDX-License-Identifier: MIT
 *
 * Upstream ships inc/ddraw.h, a copy of the DirectX SDK header (Microsoft,
 * all rights reserved), which Madeira does not import (MADEIRA_IMPORT.md).
 * The toolchain's (llvm-mingw / mingw-w64) ddraw.h has everything cnc-ddraw
 * uses except DDCAPS_DX1, the DirectX 1 caps layout that
 * IDirectDraw::GetCaps accepts by dwSize. Its fields are those of DDCAPS_DX3
 * up to dwReserved3 (DirectX 3 appended dwSVBCaps and the rest).
 * build/host-tests/check-cnc-ddraw.py compares the code built this way with
 * the code built from upstream's headers.
 */
#pragma once
#include_next <ddraw.h>

#ifndef MADEIRA_DDCAPS_DX1_DEFINED
#define MADEIRA_DDCAPS_DX1_DEFINED
typedef struct _DDCAPS_DX1
{
    DWORD dwSize;
    DWORD dwCaps;
    DWORD dwCaps2;
    DWORD dwCKeyCaps;
    DWORD dwFXCaps;
    DWORD dwFXAlphaCaps;
    DWORD dwPalCaps;
    DWORD dwSVCaps;
    DWORD dwAlphaBltConstBitDepths;
    DWORD dwAlphaBltPixelBitDepths;
    DWORD dwAlphaBltSurfaceBitDepths;
    DWORD dwAlphaOverlayConstBitDepths;
    DWORD dwAlphaOverlayPixelBitDepths;
    DWORD dwAlphaOverlaySurfaceBitDepths;
    DWORD dwZBufferBitDepths;
    DWORD dwVidMemTotal;
    DWORD dwVidMemFree;
    DWORD dwMaxVisibleOverlays;
    DWORD dwCurrVisibleOverlays;
    DWORD dwNumFourCCCodes;
    DWORD dwAlignBoundarySrc;
    DWORD dwAlignSizeSrc;
    DWORD dwAlignBoundaryDest;
    DWORD dwAlignSizeDest;
    DWORD dwAlignStrideAlign;
    DWORD dwRops[DD_ROP_SPACE];
    DDSCAPS ddsCaps;
    DWORD dwMinOverlayStretch;
    DWORD dwMaxOverlayStretch;
    DWORD dwMinLiveVideoStretch;
    DWORD dwMaxLiveVideoStretch;
    DWORD dwMinHwCodecStretch;
    DWORD dwMaxHwCodecStretch;
    DWORD dwReserved1;
    DWORD dwReserved2;
    DWORD dwReserved3;
} DDCAPS_DX1, *LPDDCAPS_DX1;
#endif
