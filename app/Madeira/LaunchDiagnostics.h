// LaunchDiagnostics.h -- where a launch stopped on its way to the first frame.
//
// A black screen has many causes that look the same to the player: the game
// never started, a DLL was missing, the Direct3D device or swapchain was
// refused, a shader did not translate, the Metal layer gave no drawable, the
// window was never drawn, or a video the game waits on could not play. This
// module keeps one record per launch: which stages were reached (with the time
// since launch), and the problems seen on the way, each in a category, with the
// error text that was actually printed and a hint that follows from it.
//
// It is fed two ways: native code calls the functions below directly, and
// every log line (Wine, DXMT, the D3D12 runtime, FEX) goes through
// madeira_diag_feed_line, which recognises the messages those components
// really print, including "[madeira-diag] stage=..." lines that PE-side code
// (which cannot call into the app) writes for this module.
//
// The report is written to Documents/madeira-diagnostics/last-launch.txt (and
// .json), which the Files app shows: no Mac or computer is needed to read it.
// "Process running" and "first frame presented" are separate stages: a game
// whose process lives but never presents is reported as such, not as success.
//
// Plain C, no Foundation, so build/host-tests/check-launch-diagnostics.py can
// compile and exercise it on any machine.
#ifndef MADEIRA_LAUNCH_DIAGNOSTICS_H
#define MADEIRA_LAUNCH_DIAGNOSTICS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MD_STAGE_LAUNCH = 0,        // the app handed the program to Wine
    MD_STAGE_WINE_STARTED,      // __wine_main is running the program
    MD_STAGE_CHILD_PROCESS,     // the program started another process (launchers)
    MD_STAGE_GRAPHICS_API,      // a Direct3D runtime was loaded (which one is the detail)
    MD_STAGE_DEVICE,            // a Direct3D device was created
    MD_STAGE_METAL_LAYER,       // the app's CAMetalLayer was handed to a swapchain
    MD_STAGE_SWAPCHAIN,         // a DXGI / D3D9 swapchain was created
    MD_STAGE_FIRST_PRESENT,     // the first frame was presented to Metal
    MD_STAGE_GDI_WINDOW,        // a GDI window (launcher, dialog) was drawn by the app
    MD_STAGE_PROCESS_EXIT,      // the program's process ended
    MD_STAGE_COUNT
} md_stage;

typedef enum {
    MD_CAT_NONE = 0,
    MD_CAT_PROCESS,          // the process could not start, crashed or ended early
    MD_CAT_MISSING_DLL,      // a DLL the program imports is not in this build
    MD_CAT_DEVICE,           // the Direct3D / Metal device was refused
    MD_CAT_SWAPCHAIN,        // the swapchain was refused or has an unusable format
    MD_CAT_SHADER,           // a shader did not translate / a pipeline did not build
    MD_CAT_PRESENT,          // Metal gave no drawable, or presenting failed
    MD_CAT_WINDOW,           // the window exists but is not drawn
    MD_CAT_VIDEO,            // a video / media component is missing or failed
    MD_CAT_UNIMPLEMENTED,    // a DLL loaded but a function it needs is a stub or not exported
    MD_CAT_DEPENDENCY,       // a DLL is there but could not be loaded (its own import, init, side-by-side)
    MD_CAT_ARCH,             // a DLL of the wrong architecture was found (c000007b)
    MD_CAT_WINE_INIT,        // Wine itself did not get the program started
    MD_CAT_AUDIO,            // an audio component (XAudio2, DirectSound, mmdevapi) failed
    MD_CAT_ENGINE_STALL,     // the game's main thread sat in an INFINITE wait (Wine/FEX lock), no frame
    MD_CAT_UNCLASSIFIED,     // an error that matched no known shape
    MD_CAT_COUNT
} md_category;

const char *madeira_diag_stage_name(md_stage stage);
const char *madeira_diag_category_name(md_category cat);

// Start a new record. exe is the program as the app named it; out_dir is the
// folder the report goes to (NULL: $MADEIRA_DOCS_DIR/madeira-diagnostics, or no
// file at all when that is unset). The previous report is kept as
// previous-launch.txt.
void madeira_diag_reset(const char *exe, const char *out_dir);

// A stage was reached. Only the first time is kept; detail may be NULL.
void madeira_diag_stage(md_stage stage, const char *detail);

// A problem. stage_name says where it happened (free text, e.g. "swapchain"),
// error is what was printed, hint may be NULL (the category's own hint is used).
void madeira_diag_problem(md_category cat, const char *stage_name, const char *error, const char *hint);

// One log line, as written to madeira-log.txt (no trailing newline needed).
void madeira_diag_feed_line(const char *line);

// The present counter (madeira_get_present_count); the first increase after
// reset marks MD_STAGE_FIRST_PRESENT.
void madeira_diag_note_present_count(uint64_t count);

// The program's process ended. status is its exit code; crash_status is the
// NTSTATUS when it ended with an exception (0 otherwise).
void madeira_diag_process_exit(int status, uint32_t crash_status);

// The verdict category (MD_CAT_NONE when the first frame was presented and no
// problem was seen).
md_category madeira_diag_verdict(void);
int madeira_diag_reached(md_stage stage);

// The report as text / JSON. Returns the length written (truncated to cap-1).
size_t madeira_diag_render_text(char *buf, size_t cap);
size_t madeira_diag_render_json(char *buf, size_t cap);

// Write the report files when something changed since the last write.
void madeira_diag_flush(void);

#ifdef __cplusplus
}
#endif

#endif
