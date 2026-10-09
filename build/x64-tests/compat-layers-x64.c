/*
 * compat-layers-x64.c -- does each Windows layer the 64-bit DLL farm gained
 * (build/wine-pe/arm64ec-farm.json) load AND answer its first call?
 *
 * A DLL being present is not the claim; this program makes the first call a
 * game makes into each layer and checks the answer: the filter graph a
 * DirectShow player creates, the media engine factory, XAudio2Create, the
 * WMI query for the display adapter, D3DCompile, an ACM conversion, ...
 * One line per check:
 *   [compat-layers] PASS <layer>: <what>
 *   [compat-layers] FAIL <layer>: <what> (<why>, hr/status)
 *   [compat-layers] SKIP <layer>: <what> (<why>)
 * and a summary line. Exit code: 0 all passed, otherwise the number of failures.
 *
 * It only uses LoadLibrary/GetProcAddress and CoCreateInstance, so a missing
 * DLL ("not loadable"), a DLL without the entry point ("no export") and an
 * entry point that refuses ("call failed") are told apart, which is the
 * distinction the launch diagnostics make too (docs/LAUNCH_DIAGNOSTICS.md).
 *
 * --d3d10  also creates a Direct3D 10 / 10.1 device (needs a renderer: DXMT on
 *          the iPad; on a desktop Wine without a GPU it is expected to fail).
 * --audio  also creates an XAudio2 mastering voice (needs an audio device).
 *
 * Build: build/x64-tests/build-compat-layers.sh
 */
#define COBJMACROS
#define CINTERFACE
#define INITGUID
#include <windows.h>
#include <objbase.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <mmreg.h>
#include <msacm.h>
#include <vfw.h>
#include <wbemcli.h>
#include <dshow.h>
#include <msxml6.h>
#include <xmllite.h>
#include <dplay8.h>
#include <d2d1.h>
#include <mfapi.h>
#include <mfmediaengine.h>
#include <wmsdkidl.h>
#include <msi.h>
#include <msiquery.h>
#ifndef CSIDL_APPDATA
#define CSIDL_APPDATA 0x001a   /* shlobj.h does not compile as C here */
#endif
#include <activscp.h>
#include <netfw.h>

/* Declared here rather than taken from headers that do not compile as C
 * (dxdiag.h) or do not carry them (qedit.h, evr.h in this mingw-w64). */
DEFINE_GUID(CLSID_SampleGrabber_,   0xc1f400a0, 0x3f08, 0x11d3, 0x9f, 0x0b, 0x00, 0x60, 0x08, 0x03, 0x9e, 0x37);
DEFINE_GUID(IID_IDirect3DDevice9_,  0xd0223b96, 0xbf7a, 0x43fd, 0x92, 0xbd, 0xa4, 0x3b, 0x0d, 0x82, 0xb9, 0xeb);
DEFINE_GUID(IID_IMFVideoPresenter_, 0x29aff080, 0x182a, 0x4a5d, 0xaf, 0x3b, 0x44, 0x8f, 0x3a, 0x63, 0x46, 0xcb);
DEFINE_GUID(CLSID_DxDiagProvider_,  0xa65b8071, 0x3bfe, 0x4213, 0x9a, 0x5b, 0x49, 0x1d, 0xa4, 0x46, 0x1c, 0xa7);
DEFINE_GUID(IID_IDxDiagProvider_,   0x9c6b4cb0, 0x23f8, 0x49cc, 0xa3, 0xed, 0x45, 0xa5, 0x50, 0x00, 0xa6, 0xd2);
DEFINE_GUID(CLSID_DOMDocument40_,   0x88d969c0, 0xf192, 0x11d4, 0xa6, 0x5f, 0x00, 0x40, 0x96, 0x32, 0x51, 0xe5);
DEFINE_GUID(CLSID_FileSystemObject_, 0x0d43fe01, 0xf093, 0x11cf, 0x89, 0x40, 0x00, 0xa0, 0xc9, 0x05, 0x42, 0x28);
DEFINE_GUID(CLSID_WshShell_,        0x72c24dd5, 0xd70a, 0x438b, 0x8a, 0x42, 0x98, 0x42, 0x4b, 0x88, 0xaf, 0xb8);
DEFINE_GUID(CLSID_JScript_,         0xf414c260, 0x6ac0, 0x11cf, 0xb6, 0xd1, 0x00, 0xaa, 0x00, 0xbb, 0xbb, 0x58);
DEFINE_GUID(CLSID_VBScript_,        0xb54f3741, 0x5b07, 0x11cf, 0xa4, 0xb0, 0x00, 0xaa, 0x00, 0x4a, 0x55, 0xe8);
DEFINE_GUID(CLSID_SWbemLocator_,    0x76a64158, 0xcb41, 0x11d1, 0x8b, 0x02, 0x00, 0x60, 0x08, 0x06, 0xd9, 0xb6);
DEFINE_GUID(CLSID_NetFwMgr_,        0x304ce942, 0x6e39, 0x40d8, 0x94, 0x3a, 0xb9, 0x13, 0xc4, 0x0c, 0x9c, 0xd4);
DEFINE_GUID(IID_INetFwMgr_,         0xf7898af5, 0xcac4, 0x4632, 0xa2, 0xec, 0xda, 0x06, 0xe5, 0x11, 0x1a, 0xf2);
DEFINE_GUID(GUID_NULL_,          0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
DEFINE_GUID(IID_IDispatch_,         0x00020400, 0x0000, 0x0000, 0xc0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46);
DEFINE_GUID(IID_IActiveScript_,     0xbb1a2ae1, 0xa4f9, 0x11cf, 0x8f, 0x20, 0x00, 0x80, 0x5f, 0x2c, 0xd0, 0x64);
DEFINE_GUID(CLSID_DOMDocument30_,   0xf5078f32, 0xc551, 0x11d3, 0x89, 0xb9, 0x00, 0x00, 0xf8, 0x1f, 0xe2, 0x21);

static int passed, failed, skipped;
static int want_d3d10, want_audio, want_display;

static void pass(const char *layer, const char *what)
{ printf("[compat-layers] PASS %s: %s\n", layer, what); passed++; }
static void fail(const char *layer, const char *what, const char *why, long code)
{ printf("[compat-layers] FAIL %s: %s (%s, 0x%08lx)\n", layer, what, why, (unsigned long)code); failed++; }
static void skip(const char *layer, const char *what, const char *why)
{ printf("[compat-layers] SKIP %s: %s (%s)\n", layer, what, why); skipped++; }

static FARPROC entry(const char *layer, const char *dll, const char *name)
{
    HMODULE h = LoadLibraryA(dll);
    FARPROC p;
    if (!h) { fail(layer, dll, "not loadable", GetLastError()); return NULL; }
    if (!(p = GetProcAddress(h, name))) { fail(layer, name, "no export", GetLastError()); return NULL; }
    return p;
}

static void load_only(const char *layer, const char *dll)
{
    char what[96];
    HMODULE h = LoadLibraryA(dll);
    snprintf(what, sizeof what, "LoadLibrary %s", dll);
    if (h) pass(layer, what); else fail(layer, what, "not loadable", GetLastError());
}

static IUnknown *create(const char *layer, const char *what, REFCLSID clsid, REFIID iid)
{
    IUnknown *unk = NULL;
    HRESULT hr = CoCreateInstance(clsid, NULL, CLSCTX_INPROC_SERVER, iid, (void **)&unk);
    if (FAILED(hr) || !unk) { fail(layer, what, hr == REGDB_E_CLASSNOTREG ? "class not registered" : "CoCreateInstance failed", hr); return NULL; }
    pass(layer, what);
    return unk;
}

/* --- Visual C++ runtimes ------------------------------------------------- */
static void test_vcrun(void)
{
    static const char *const crt[] = { "msvcr80.dll", "msvcr90.dll", "msvcr100.dll", "msvcr110.dll" };
    static const char *const cpp[] = { "msvcp80.dll", "msvcp90.dll", "msvcp100.dll", "msvcp110.dll", "msvcp120.dll",
                                       "atl80.dll", "atl90.dll", "atl100.dll", "atl110.dll", "vccorlib140.dll" };
    unsigned i;
    for (i = 0; i < sizeof crt / sizeof crt[0]; i++) {
        int (__cdecl *snp)(char *, size_t, const char *, ...) = (void *)entry("vcrun", crt[i], "_snprintf");
        char buf[32] = "", what[64];
        snprintf(what, sizeof what, "%s _snprintf", crt[i]);
        if (!snp) continue;
        snp(buf, sizeof buf, "%d-%s", 42, "ok");
        if (!strcmp(buf, "42-ok")) pass("vcrun", what); else fail("vcrun", what, "wrong result", 0);
    }
    for (i = 0; i < sizeof cpp / sizeof cpp[0]; i++) load_only("vcrun", cpp[i]);
    {
        int (__cdecl *maxt)(void) = (void *)entry("vcrun", "vcomp100.dll", "omp_get_max_threads");
        if (maxt) { if (maxt() >= 1) pass("vcrun", "vcomp100 omp_get_max_threads"); else fail("vcrun", "vcomp100 omp_get_max_threads", "< 1", 0); }
    }
}

/* --- XAudio2 / XACT ------------------------------------------------------ */
DEFINE_GUID(CLSID_XAudio27_, 0x5a508685, 0xa254, 0x4fba, 0x9b, 0x82, 0x9a, 0x24, 0xb0, 0x03, 0x06, 0xaf);
DEFINE_GUID(IID_IXAudio27_,  0x8bcf1f58, 0x9fe7, 0x4583, 0x8a, 0xc6, 0xe2, 0xad, 0xc4, 0x65, 0xc8, 0xbb);
static void test_xaudio(void)
{
    /* XAudio2Create(IXAudio2 **, UINT32 flags, XAUDIO2_PROCESSOR) -- 2.8 and 2.9 */
    static const char *const flat[] = { "xaudio2_8.dll", "xaudio2_9.dll" };
    unsigned i;
    for (i = 0; i < 2; i++) {
        HRESULT (WINAPI *create2)(IUnknown **, UINT32, UINT32) = (void *)entry("xaudio", flat[i], "XAudio2Create");
        IUnknown *x = NULL; HRESULT hr; char what[64];
        snprintf(what, sizeof what, "%s XAudio2Create", flat[i]);
        if (!create2) continue;
        hr = create2(&x, 0, 0x00000001 /* XAUDIO2_DEFAULT_PROCESSOR (2.8+) */);
        if (SUCCEEDED(hr) && x) {
            pass("xaudio", what);
            if (want_audio) {
                /* IXAudio2 (2.8/2.9) vtable: QI, AddRef, Release, RegisterForCallbacks, UnregisterForCallbacks,
                 * CreateSourceVoice, CreateSubmixVoice, CreateMasteringVoice */
                typedef HRESULT (WINAPI *mv_t)(IUnknown *, void **, UINT32, UINT32, UINT32, LPCWSTR, const void *, UINT32);
                void *mv = NULL;
                hr = ((mv_t)(*(void ***)x)[7])(x, &mv, 0, 0, 0, NULL, NULL, 6 /* AudioCategory_GameEffects */);
                if (SUCCEEDED(hr)) pass("xaudio", "mastering voice"); else fail("xaudio", "mastering voice", "CreateMasteringVoice failed", hr);
            }
            IUnknown_Release(x);
        } else fail("xaudio", what, "call failed", hr);
    }
    {   /* xaudio2_7 is a COM server; Initialize is the first call after creation */
        IUnknown *x = create("xaudio", "xaudio2_7 CLSID_XAudio2 (2.7)", &CLSID_XAudio27_, &IID_IXAudio27_);
        if (x) {
            typedef HRESULT (WINAPI *init_t)(IUnknown *, UINT32, UINT32);
            /* IXAudio2 2.7: QI, AddRef, Release, GetDeviceCount, GetDeviceDetails, Initialize */
            HRESULT hr = ((init_t)(*(void ***)x)[5])(x, 0, 0xffffffff /* XAUDIO2_DEFAULT_PROCESSOR (2.7) */);
            if (SUCCEEDED(hr)) pass("xaudio", "xaudio2_7 IXAudio2::Initialize"); else fail("xaudio", "xaudio2_7 IXAudio2::Initialize", "call failed", hr);
            IUnknown_Release(x);
        }
    }
    load_only("xaudio", "x3daudio1_3.dll");
    load_only("xaudio", "xapofx1_3.dll");
    load_only("xaudio", "xactengine3_7.dll");
}

/* --- DirectShow ---------------------------------------------------------- */
static void test_dshow(void)
{
    IUnknown *u;
    if ((u = create("directshow", "quartz CLSID_FilterGraph -> IGraphBuilder", &CLSID_FilterGraph, &IID_IGraphBuilder))) {
        IMediaControl *mc = NULL;
        HRESULT hr = IUnknown_QueryInterface(u, &IID_IMediaControl, (void **)&mc);
        if (SUCCEEDED(hr)) { pass("directshow", "filter graph IMediaControl"); IMediaControl_Release(mc); }
        else fail("directshow", "filter graph IMediaControl", "QueryInterface failed", hr);
        IUnknown_Release(u);
    }
    if ((u = create("directshow", "devenum CLSID_SystemDeviceEnum", &CLSID_SystemDeviceEnum, &IID_ICreateDevEnum))) {
        IEnumMoniker *e = NULL;
        HRESULT hr = ICreateDevEnum_CreateClassEnumerator((ICreateDevEnum *)u, &CLSID_LegacyAmFilterCategory, &e, 0);
        if (hr == S_OK || hr == S_FALSE) pass("directshow", "devenum CreateClassEnumerator(LegacyAmFilterCategory)");
        else fail("directshow", "devenum CreateClassEnumerator", "call failed", hr);
        if (e) IEnumMoniker_Release(e);
        IUnknown_Release(u);
    }
    if ((u = create("directshow", "qedit CLSID_SampleGrabber", &CLSID_SampleGrabber_, &IID_IBaseFilter))) IUnknown_Release(u);
    /* The renderers need a device: VMR-9 and the video renderer a Direct3D 9
     * device / window driver, DSoundRender an audio endpoint (it fails with
     * VFW_E_NO_AUDIO_HARDWARE without one). Headless runs skip them. */
    if (want_display) {
        if ((u = create("directshow", "quartz CLSID_VideoRenderer", &CLSID_VideoRenderer, &IID_IBaseFilter))) IUnknown_Release(u);
        if ((u = create("directshow", "quartz CLSID_VideoMixingRenderer9", &CLSID_VideoMixingRenderer9, &IID_IBaseFilter))) IUnknown_Release(u);
    } else skip("directshow", "video renderers (VR, VMR-9)", "need a display; run with --display");
    if (want_audio) {
        if ((u = create("directshow", "quartz CLSID_DSoundRender", &CLSID_DSoundRender, &IID_IBaseFilter))) IUnknown_Release(u);
    } else skip("directshow", "DSoundRender", "needs an audio endpoint; run with --audio");
}

/* --- Media Foundation ---------------------------------------------------- */
static void test_mf(void)
{
    HRESULT (WINAPI *startup)(ULONG, DWORD) = (void *)entry("media-foundation", "mfplat.dll", "MFStartup");
    HRESULT hr;
    IUnknown *u;
    if (!startup) return;
    hr = startup(MF_VERSION, MFSTARTUP_FULL);
    if (FAILED(hr)) { fail("media-foundation", "MFStartup", "call failed", hr); return; }
    pass("media-foundation", "MFStartup");
    if ((u = create("media-foundation", "mfmediaengine CLSID_MFMediaEngineClassFactory",
                    &CLSID_MFMediaEngineClassFactory, &IID_IMFMediaEngineClassFactory))) IUnknown_Release(u);
    {
        HRESULT (WINAPI *mkpres)(IUnknown *, REFIID, REFIID, void **) = (void *)entry("media-foundation", "evr.dll", "MFCreateVideoPresenter");
        IUnknown *p = NULL;
        if (mkpres && !want_display) {
            skip("media-foundation", "evr MFCreateVideoPresenter", "creates a Direct3D 9 device; run with --display");
        } else if (mkpres) {
            hr = mkpres(NULL, &IID_IDirect3DDevice9_, &IID_IMFVideoPresenter_, (void **)&p);
            if (SUCCEEDED(hr) && p) { pass("media-foundation", "evr MFCreateVideoPresenter"); IUnknown_Release(p); }
            else fail("media-foundation", "evr MFCreateVideoPresenter", "call failed", hr);
        }
    }
    load_only("media-foundation", "winegstreamer.dll");
    load_only("media-foundation", "mfplay.dll");
    load_only("media-foundation", "msmpeg2vdec.dll");
    load_only("media-foundation", "colorcnv.dll");
    load_only("media-foundation", "resampledmo.dll");
    {
        HRESULT (WINAPI *shutdown)(void) = (void *)entry("media-foundation", "mfplat.dll", "MFShutdown");
        if (shutdown) shutdown();
    }
}

/* --- Windows Media Format ------------------------------------------------ */
static void test_wmf(void)
{
    HRESULT (WINAPI *mk)(IUnknown *, DWORD, IWMSyncReader **);
    /* wmvcore delay-imports winegstreamer for its readers; without it the
     * call dies on an unimplemented-function abort instead of failing. */
    if (!GetModuleHandleA("winegstreamer.dll") && !LoadLibraryA("winegstreamer.dll")) {
        fail("wmdecoder", "wmvcore WMCreateSyncReader", "winegstreamer.dll (delay import) not loadable", GetLastError());
        return;
    }
    mk = (void *)entry("wmdecoder", "wmvcore.dll", "WMCreateSyncReader");
    IWMSyncReader *r = NULL;
    HRESULT hr;
    if (!mk) return;
    hr = mk(NULL, 0, &r);
    if (SUCCEEDED(hr) && r) { pass("wmdecoder", "wmvcore WMCreateSyncReader"); IWMSyncReader_Release(r); }
    else fail("wmdecoder", "wmvcore WMCreateSyncReader", "call failed", hr);
    load_only("wmdecoder", "wmadmod.dll");
}

/* --- VfW / ACM ----------------------------------------------------------- */
static void test_vfw_acm(void)
{
    void (WINAPI *init)(void) = (void *)entry("vfw-acm", "avifil32.dll", "AVIFileInit");
    void (WINAPI *exitf)(void) = (void *)entry("vfw-acm", "avifil32.dll", "AVIFileExit");
    HIC (WINAPI *icopen)(DWORD, DWORD, UINT) = (void *)entry("vfw-acm", "msvfw32.dll", "ICOpen");
    MMRESULT (WINAPI *suggest)(HACMDRIVER, LPWAVEFORMATEX, LPWAVEFORMATEX, DWORD, DWORD) =
        (void *)entry("vfw-acm", "msacm32.dll", "acmFormatSuggest");
    if (init && exitf) { init(); exitf(); pass("vfw-acm", "AVIFileInit/AVIFileExit"); }
    if (icopen) {
        HIC h = icopen(mmioFOURCC('v','i','d','c'), mmioFOURCC('c','v','i','d'), ICMODE_DECOMPRESS);
        LRESULT (WINAPI *icclose)(HIC) = (void *)entry("vfw-acm", "msvfw32.dll", "ICClose");
        if (h) { pass("vfw-acm", "ICOpen Cinepak decompressor (iccvid)"); if (icclose) icclose(h); }
        else fail("vfw-acm", "ICOpen Cinepak decompressor (iccvid)", "no driver answered", 0);
    }
    if (suggest) {
        /* IMA ADPCM -> PCM: what a game playing a compressed WAV asks the ACM for */
        struct { IMAADPCMWAVEFORMAT f; } src = {{{ WAVE_FORMAT_IMA_ADPCM, 1, 22050, 11100, 512, 4, 2 }, 1017 }};
        WAVEFORMATEX dst = { 0 };
        MMRESULT mr;
        dst.wFormatTag = WAVE_FORMAT_PCM;
        mr = suggest(NULL, (LPWAVEFORMATEX)&src.f, &dst, sizeof dst, ACM_FORMATSUGGESTF_WFORMATTAG);
        if (mr == MMSYSERR_NOERROR && dst.wBitsPerSample == 16) pass("vfw-acm", "acmFormatSuggest IMA ADPCM -> PCM (imaadp32)");
        else fail("vfw-acm", "acmFormatSuggest IMA ADPCM -> PCM (imaadp32)", "no codec", mr);
    }
}

/* --- Direct3D helpers ---------------------------------------------------- */
static DWORD WINAPI dxdiag_sta(void *arg);

static void test_d3d_helpers(void)
{
    typedef struct { float m[4][4]; } M4;
    M4 *(WINAPI *mul)(M4 *, const M4 *, const M4 *) = (void *)entry("direct3d", "d3dx9_36.dll", "D3DXMatrixMultiply");
    HRESULT (WINAPI *compile)(const void *, SIZE_T, const char *, const void *, void *, const char *, const char *,
                              UINT, UINT, IUnknown **, IUnknown **) = (void *)entry("direct3d", "d3dcompiler_43.dll", "D3DCompile");
    HRESULT (WINAPI *chk10)(UINT, UINT) = (void *)entry("direct3d", "d3dx10_43.dll", "D3DX10CheckVersion");
    if (mul) {
        M4 a = {{{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}}}, b = {{{2,0,0,0},{0,3,0,0},{0,0,4,0},{5,6,7,1}}}, out;
        mul(&out, &a, &b);
        if (!memcmp(&out, &b, sizeof b)) pass("direct3d", "d3dx9_36 D3DXMatrixMultiply"); else fail("direct3d", "d3dx9_36 D3DXMatrixMultiply", "wrong result", 0);
    }
    if (compile) {
        static const char src[] = "float4 main(float4 p : SV_Position) : SV_Target { return p * 0.5; }";
        IUnknown *blob = NULL, *err = NULL;
        HRESULT hr = compile(src, sizeof src - 1, "t.hlsl", NULL, NULL, "main", "ps_4_0", 0, 0, &blob, &err);
        if (SUCCEEDED(hr) && blob) pass("direct3d", "d3dcompiler_43 D3DCompile ps_4_0"); else fail("direct3d", "d3dcompiler_43 D3DCompile ps_4_0", "call failed", hr);
        if (blob) IUnknown_Release(blob);
        if (err) IUnknown_Release(err);
    }
    if (chk10) {
        HRESULT hr = chk10(29 /* D3D10_SDK_VERSION */, 43 /* D3DX10_SDK_VERSION */);
        if (SUCCEEDED(hr)) pass("direct3d", "d3dx10_43 D3DX10CheckVersion"); else fail("direct3d", "d3dx10_43 D3DX10CheckVersion", "call failed", hr);
    }
    load_only("direct3d", "d3dx9_31.dll");
    load_only("direct3d", "d3dx11_43.dll");
    load_only("direct3d", "d3dcompiler_33.dll");
    {
        IUnknown *f = NULL;
        HRESULT (WINAPI *d2d)(D2D1_FACTORY_TYPE, REFIID, const D2D1_FACTORY_OPTIONS *, void **) =
            (void *)entry("direct3d", "d2d1.dll", "D2D1CreateFactory");
        if (d2d) {
            HRESULT hr = d2d(D2D1_FACTORY_TYPE_SINGLE_THREADED, &IID_ID2D1Factory, NULL, (void **)&f);
            if (SUCCEEDED(hr) && f) { pass("direct3d", "d2d1 D2D1CreateFactory"); IUnknown_Release(f); }
            else fail("direct3d", "d2d1 D2D1CreateFactory", "call failed", hr);
        }
    }
    {
        FARPROC c10 = entry("direct3d", "d3d10.dll", "D3D10CreateDeviceAndSwapChain");
        FARPROC c101 = entry("direct3d", "d3d10_1.dll", "D3D10CreateDevice1");
        if (c10) pass("direct3d", "d3d10 D3D10CreateDeviceAndSwapChain export");
        if (c101) pass("direct3d", "d3d10_1 D3D10CreateDevice1 export");
        if (want_d3d10 && c101) {
            HRESULT (WINAPI *mk)(IUnknown *, UINT, HMODULE, UINT, UINT, UINT, IUnknown **) = (void *)c101;
            IUnknown *dev = NULL;
            HRESULT hr = mk(NULL, 0 /* D3D10_DRIVER_TYPE_HARDWARE */, NULL, 0, 0xa000 /* 10.0 */, 0x20 /* D3D10_1_SDK_VERSION */, &dev);
            if (SUCCEEDED(hr) && dev) { pass("direct3d", "d3d10_1 D3D10CreateDevice1 (hardware)"); IUnknown_Release(dev); }
            else fail("direct3d", "d3d10_1 D3D10CreateDevice1 (hardware)", "call failed", hr);
        } else if (!want_d3d10) skip("direct3d", "D3D10 device creation", "needs a renderer; run with --d3d10");
    }
    /* dxdiagn is apartment-threaded and IDxDiagProvider has no proxy, so from
     * this MTA thread COM would answer E_NOINTERFACE; games query it from an
     * STA thread, so the test does too. */
    {
        HANDLE t = CreateThread(NULL, 0, dxdiag_sta, NULL, 0, NULL);
        if (t) { WaitForSingleObject(t, INFINITE); CloseHandle(t); }
    }
}

static DWORD WINAPI dxdiag_sta(void *arg)
{
    IUnknown *u;
    (void)arg;
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if ((u = create("direct3d", "dxdiagn CLSID_DxDiagProvider (STA)", &CLSID_DxDiagProvider_, &IID_IDxDiagProvider_)))
        IUnknown_Release(u);
    CoUninitialize();
    return 0;
}

/* --- system libraries ---------------------------------------------------- */
static void test_system(void)
{
    IUnknown *u;
    if ((u = create("system", "wbemprox CLSID_WbemLocator", &CLSID_WbemLocator, &IID_IWbemLocator))) {
        IWbemServices *svc = NULL;
        BSTR ns = SysAllocString(L"ROOT\\CIMV2");
        HRESULT hr = IWbemLocator_ConnectServer((IWbemLocator *)u, ns, NULL, NULL, NULL, 0, NULL, NULL, &svc);
        if (SUCCEEDED(hr) && svc) {
            IEnumWbemClassObject *e = NULL;
            BSTR lang = SysAllocString(L"WQL"), q = SysAllocString(L"SELECT * FROM Win32_VideoController");
            pass("system", "WMI ConnectServer ROOT\\CIMV2");
            hr = IWbemServices_ExecQuery(svc, lang, q, WBEM_FLAG_FORWARD_ONLY, NULL, &e);
            if (SUCCEEDED(hr)) pass("system", "WMI ExecQuery Win32_VideoController"); else fail("system", "WMI ExecQuery Win32_VideoController", "call failed", hr);
            if (e) IEnumWbemClassObject_Release(e);
            SysFreeString(lang); SysFreeString(q);
            IWbemServices_Release(svc);
        } else fail("system", "WMI ConnectServer ROOT\\CIMV2", "call failed", hr);
        SysFreeString(ns);
        IUnknown_Release(u);
    }
    if ((u = create("system", "msxml6 CLSID_DOMDocument60", &CLSID_DOMDocument60, &IID_IXMLDOMDocument))) {
        VARIANT_BOOL ok = VARIANT_FALSE;
        BSTR x = SysAllocString(L"<settings><v a=\"1\"/></settings>");
        HRESULT hr = IXMLDOMDocument_loadXML((IXMLDOMDocument *)u, x, &ok);
        if (hr == S_OK && ok == VARIANT_TRUE) pass("system", "msxml6 loadXML"); else fail("system", "msxml6 loadXML", "call failed", hr);
        SysFreeString(x);
        IUnknown_Release(u);
    }
    if ((u = create("system", "msxml3 CLSID_DOMDocument30", &CLSID_DOMDocument30_, &IID_IXMLDOMDocument))) IUnknown_Release(u);
    {
        HRESULT (WINAPI *mk)(REFIID, void **, IMalloc *) = (void *)entry("system", "xmllite.dll", "CreateXmlReader");
        IUnknown *r = NULL;
        if (mk) { HRESULT hr = mk(&IID_IXmlReader, (void **)&r, NULL);
                  if (SUCCEEDED(hr) && r) { pass("system", "xmllite CreateXmlReader"); IUnknown_Release(r); }
                  else fail("system", "xmllite CreateXmlReader", "call failed", hr); }
    }
    {
        struct { UINT32 GdiplusVersion; void *cb; BOOL a, b; } in = { 1, NULL, FALSE, FALSE };
        ULONG_PTR token = 0;
        int (WINAPI *start)(ULONG_PTR *, const void *, void *) = (void *)entry("system", "gdiplus.dll", "GdiplusStartup");
        void (WINAPI *stop)(ULONG_PTR) = (void *)entry("system", "gdiplus.dll", "GdiplusShutdown");
        if (start) { int st = start(&token, &in, NULL);
                     if (st == 0) { pass("system", "gdiplus GdiplusStartup"); if (stop) stop(token); }
                     else fail("system", "gdiplus GdiplusStartup", "status", st); }
    }
    {
        const void *props = NULL; int n = 0;
        HRESULT (WINAPI *gp)(const void ***, int *) = (void *)entry("system", "usp10.dll", "ScriptGetProperties");
        if (gp) { HRESULT hr = gp((const void ***)&props, &n);
                  if (SUCCEEDED(hr) && n > 0) pass("system", "usp10 ScriptGetProperties"); else fail("system", "usp10 ScriptGetProperties", "call failed", hr); }
    }
    {
        WNDCLASSW wc;
        if (LoadLibraryA("riched20.dll") && GetClassInfoW(NULL, L"RichEdit20W", &wc)) pass("system", "riched20 registers RichEdit20W");
        else fail("system", "riched20 registers RichEdit20W", "class missing", GetLastError());
    }
    if ((u = create("system", "dpnet CLSID_DirectPlay8Peer", &CLSID_DirectPlay8Peer, &IID_IDirectPlay8Peer))) IUnknown_Release(u);
    load_only("system", "gameux.dll");
    load_only("system", "wer.dll");
    load_only("system", "cabinet.dll");
}

/* --- Windows Installer, script hosts, firewall API, shell folders ---------
 * The installer_scripting group of build/wine-pe/arm64ec-farm.json: a 64-bit
 * redistributable / game installer, an x64 MSI custom action, a launcher's
 * script objects and a multiplayer game's firewall exception. */
static void test_installer_scripting(void)
{
    IUnknown *u;
    char tmp[MAX_PATH], db[MAX_PATH];
    {
        UINT (WINAPI *open)(LPCSTR, LPCSTR, MSIHANDLE *) = (void *)entry("installer", "msi.dll", "MsiOpenDatabaseA");
        UINT (WINAPI *view)(MSIHANDLE, LPCSTR, MSIHANDLE *) = (void *)entry("installer", "msi.dll", "MsiDatabaseOpenViewA");
        UINT (WINAPI *exec)(MSIHANDLE, MSIHANDLE) = (void *)entry("installer", "msi.dll", "MsiViewExecute");
        UINT (WINAPI *commit)(MSIHANDLE) = (void *)entry("installer", "msi.dll", "MsiDatabaseCommit");
        UINT (WINAPI *close)(MSIHANDLE) = (void *)entry("installer", "msi.dll", "MsiCloseHandle");
        if (open && view && exec && commit && close) {
            MSIHANDLE h = 0, v = 0;
            UINT r;
            GetTempPathA(sizeof tmp, tmp);
            snprintf(db, sizeof db, "%smadeira-compat-layers.msi", tmp);
            DeleteFileA(db);
            r = open(db, MSIDBOPEN_CREATE, &h);
            if (r == ERROR_SUCCESS) {
                pass("installer", "msi MsiOpenDatabase (create)");
                r = view(h, "CREATE TABLE `Madeira` (`Key` CHAR(32) NOT NULL PRIMARY KEY `Key`)", &v);
                if (r == ERROR_SUCCESS) r = exec(v, 0);
                if (v) close(v);
                if (r == ERROR_SUCCESS) r = commit(h);
                if (r == ERROR_SUCCESS) pass("installer", "msi SQL CREATE TABLE + commit");
                else fail("installer", "msi SQL CREATE TABLE + commit", "call failed", r);
                close(h);
            } else fail("installer", "msi MsiOpenDatabase (create)", "call failed", r);
            DeleteFileA(db);
        }
    }
    {
        /* msiexec.exe from system32, as an x64 custom action server or a
         * double-clicked .msi would start it: removing an unknown product
         * answers 1605 (ERROR_UNKNOWN_PRODUCT) once msiexec actually ran. */
        char cmd[MAX_PATH + 96], sys[MAX_PATH];
        STARTUPINFOA si = { sizeof si };
        PROCESS_INFORMATION pi;
        GetSystemDirectoryA(sys, sizeof sys);
        snprintf(cmd, sizeof cmd, "\"%s\\msiexec.exe\" /x {00000000-0000-0000-0000-00000000DEAD} /qn", sys);
        if (CreateProcessA(NULL, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
            DWORD code = 0;
            if (WaitForSingleObject(pi.hProcess, 60000) == WAIT_OBJECT_0 && GetExitCodeProcess(pi.hProcess, &code)
                && code == ERROR_UNKNOWN_PRODUCT)
                pass("installer", "msiexec.exe /x unknown product -> 1605");
            else fail("installer", "msiexec.exe /x unknown product", "unexpected exit code", code);
            CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        } else fail("installer", "msiexec.exe", "not startable", GetLastError());
    }
    if ((u = create("scripting", "msxml4 CLSID_DOMDocument40", &CLSID_DOMDocument40_, &IID_IXMLDOMDocument))) {
        VARIANT_BOOL ok = VARIANT_FALSE;
        BSTR x = SysAllocString(L"<config><v a=\"1\"/></config>");
        HRESULT hr = IXMLDOMDocument_loadXML((IXMLDOMDocument *)u, x, &ok);
        if (hr == S_OK && ok == VARIANT_TRUE) pass("scripting", "msxml4 loadXML"); else fail("scripting", "msxml4 loadXML", "call failed", hr);
        SysFreeString(x);
        IUnknown_Release(u);
    }
    load_only("scripting", "msxml.dll");
    load_only("scripting", "msxml2.dll");
    if ((u = create("scripting", "scrrun Scripting.FileSystemObject", &CLSID_FileSystemObject_, &IID_IDispatch_))) {
        /* GetSpecialFolder / FolderExists are what setup scripts call first;
         * IDispatch::GetIDsOfNames shows the type information loads. */
        LPOLESTR name = (LPOLESTR)L"FolderExists";
        DISPID id = 0;
        HRESULT hr = IDispatch_GetIDsOfNames((IDispatch *)u, &GUID_NULL_, &name, 1, 0, &id);
        if (SUCCEEDED(hr)) pass("scripting", "scrrun IDispatch FolderExists"); else fail("scripting", "scrrun IDispatch FolderExists", "call failed", hr);
        IUnknown_Release(u);
    }
    if ((u = create("scripting", "wshom WScript.Shell", &CLSID_WshShell_, &IID_IDispatch_))) {
        LPOLESTR name = (LPOLESTR)L"ExpandEnvironmentStrings";
        DISPID id = 0;
        HRESULT hr = IDispatch_GetIDsOfNames((IDispatch *)u, &GUID_NULL_, &name, 1, 0, &id);
        if (SUCCEEDED(hr)) pass("scripting", "wshom IDispatch ExpandEnvironmentStrings"); else fail("scripting", "wshom IDispatch ExpandEnvironmentStrings", "call failed", hr);
        IUnknown_Release(u);
    }
    if ((u = create("scripting", "jscript CLSID_JScript", &CLSID_JScript_, &IID_IActiveScript_))) IUnknown_Release(u);
    if ((u = create("scripting", "vbscript CLSID_VBScript", &CLSID_VBScript_, &IID_IActiveScript_))) IUnknown_Release(u);
    if ((u = create("scripting", "wbemdisp WbemScripting.SWbemLocator", &CLSID_SWbemLocator_, &IID_IDispatch_))) IUnknown_Release(u);
    if ((u = create("network", "hnetcfg HNetCfg.FwMgr", &CLSID_NetFwMgr_, &IID_INetFwMgr_))) {
        INetFwPolicy *pol = NULL;
        HRESULT hr = INetFwMgr_get_LocalPolicy((INetFwMgr *)u, &pol);
        if (SUCCEEDED(hr) && pol) { pass("network", "hnetcfg INetFwMgr::get_LocalPolicy"); INetFwPolicy_Release(pol); }
        else fail("network", "hnetcfg INetFwMgr::get_LocalPolicy", "call failed", hr);
        IUnknown_Release(u);
    }
    {
        BOOL (WINAPI *alive)(DWORD *) = (void *)entry("network", "sensapi.dll", "IsNetworkAlive");
        DWORD flags = 0;
        if (alive) { alive(&flags); pass("network", "sensapi IsNetworkAlive returns"); }
    }
    {
        HRESULT (WINAPI *gfp)(HWND, int, HANDLE, DWORD, LPSTR) = (void *)entry("system", "shfolder.dll", "SHGetFolderPathA");
        char path[MAX_PATH] = "";
        if (gfp) { HRESULT hr = gfp(NULL, CSIDL_APPDATA, NULL, 0, path);
                   if (SUCCEEDED(hr) && path[0]) pass("system", "shfolder SHGetFolderPathA(CSIDL_APPDATA)");
                   else fail("system", "shfolder SHGetFolderPathA(CSIDL_APPDATA)", "call failed", hr); }
    }
}

int main(int argc, char **argv)
{
    int i;
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--d3d10")) want_d3d10 = 1;
        else if (!strcmp(argv[i], "--audio")) want_audio = 1;
        else if (!strcmp(argv[i], "--display")) want_display = 1;
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    if (FAILED(CoInitializeEx(NULL, COINIT_MULTITHREADED))) { fail("com", "CoInitializeEx", "call failed", 0); return 1; }
    test_vcrun();
    test_xaudio();
    test_dshow();
    test_mf();
    test_wmf();
    test_vfw_acm();
    test_d3d_helpers();
    test_system();
    test_installer_scripting();
    CoUninitialize();
    printf("[compat-layers] summary: %d passed, %d failed, %d skipped\n", passed, failed, skipped);
    return failed;
}
