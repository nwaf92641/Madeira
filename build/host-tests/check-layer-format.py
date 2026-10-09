#!/usr/bin/env python3
"""Metal layer formats and shared-layer presentation, without a device.

Covers the changes that keep a swapchain from ending the app or presenting the
wrong bytes (patches/dxmt-ios-layer-safety.patch and the D3D12 runtime):
  - madeira_layer_format.h (taken from the patch and compiled): the documented
    CAMetalLayer formats pass unchanged, every other format maps to one of
    them with its sRGB encoding kept, view swizzle flags are ignored, and no
    input in 0..1023 (with or without flags) maps outside the documented set;
  - the D3D12 runtime's blit-or-draw decision (mad_swap_direct_copy, cut from
    madeira_d3d12.c and compiled against stub texture queries): a blit only
    when the drawable has the back buffer's format and size;
  - the patch still applies to the pinned DXMT commit (when research/dxmt is
    checked out), and the source keeps the properties that matter: winemetal
    tries the requested format before falling back; the presenter draws into
    the drawable's real extent and drops a frame whose format it cannot draw;
    D3D11 CreateSwapChain fails instead of abort(); MadeiraCtl op 8 answers 0
    in remote mode; the D3D12 runtime re-applies the shared layer, converts
    through op 8 and falls back to a clipped copy or a dropped frame.
What CAMetalLayer and the GPU do with this on an iPad is not tested here.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
patch_path = root / 'patches/dxmt-ios-layer-safety.patch'
patch = patch_path.read_text()
d3d12 = (root / 'research/madeira-d3d12/src/pe/madeira_d3d12.c').read_text()

failures = []
def check(cond, what):
    if not cond:
        failures.append(what)
        print('FAIL:', what)


def file_from_patch(text, path):
    """The full new content of a file the patch adds, or the '+' side of its hunks."""
    m = re.search(r'^diff --git a/%s b/%s\n(.*?)(?=^diff --git |\Z)' % (re.escape(path), re.escape(path)), text, re.S | re.M)
    if not m:
        return None
    body = m.group(1)
    if 'new file mode' not in body:
        return None
    out = []
    for line in body.split('\n@@', 1)[1].splitlines()[1:]:
        if line.startswith('+'):
            out.append(line[1:])
    return '\n'.join(out) + '\n'


def section(text, path):
    m = re.search(r'^diff --git a/%s b/%s\n(.*?)(?=^diff --git |\Z)' % (re.escape(path), re.escape(path)), text, re.S | re.M)
    return m.group(1) if m else ''


hdr = file_from_patch(patch, 'src/winemetal/madeira_layer_format.h')
check(hdr is not None, 'the patch adds src/winemetal/madeira_layer_format.h')

direct = re.search(r'static int mad_swap_direct_copy\(.*?\n\}\n', d3d12, re.S)
check(direct is not None, 'madeira_d3d12.c has mad_swap_direct_copy')

if hdr and direct:
    prog = r'''
#include <stdint.h>
#include <stdio.h>
#include "madeira_layer_format.h"
typedef uint64_t obj_handle_t; typedef unsigned UINT; typedef uint32_t UINT32;
#define ORIGINAL_FORMAT(f) ((f) & ~0x00F00000u)
struct mad_swapchain { uint32_t pf; };
struct mad_resource { UINT32 width, height; };
static uint32_t t_pf; static uint64_t t_w, t_h;
static uint32_t MTLTexture_pixelFormat(obj_handle_t t) { (void)t; return t_pf; }
static uint64_t MTLTexture_width(obj_handle_t t) { (void)t; return t_w; }
static uint64_t MTLTexture_height(obj_handle_t t) { (void)t; return t_h; }
@DIRECT@
static int fails;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL: %s\n", m); fails++; } } while (0)
int main(void) {
  static const uint32_t documented[] = { 80, 81, 115, 90, 94, 552, 553, 554, 555 };
  unsigned i; uint32_t f;
  for (i = 0; i < sizeof documented / sizeof *documented; i++) {
    CHECK(madeira_layer_format_ok(documented[i]), "documented format accepted");
    CHECK(madeira_layer_format_for(documented[i]) == documented[i], "documented format unchanged");
  }
  CHECK(!madeira_layer_format_ok(70) && !madeira_layer_format_ok(71) && !madeira_layer_format_ok(40), "RGBA8 / 565 not documented");
  CHECK(madeira_layer_format_for(70) == 80, "RGBA8Unorm -> BGRA8Unorm");
  CHECK(madeira_layer_format_for(71) == 81, "RGBA8Unorm_sRGB -> BGRA8Unorm_sRGB (encoding kept)");
  CHECK(madeira_layer_format_for(40) == 80, "B5G6R5 -> BGRA8Unorm");
  CHECK(madeira_layer_format_for(92) == 115 && madeira_layer_format_for(125) == 115 && madeira_layer_format_for(110) == 115,
        "float / 16-bit-per-channel formats -> RGBA16Float");
  CHECK(madeira_layer_format_for(0x00800000u | 80u) == 80, "BGRX8 (alpha-is-one flag) -> BGRA8");
  for (f = 0; f < 1024; f++) {
    uint32_t a = madeira_layer_format_for(f), b = madeira_layer_format_for(f | 0x00400000u);
    if (!madeira_layer_format_ok(a) || !madeira_layer_format_ok(b)) { printf("FAIL: %u maps outside the set\n", f); fails++; break; }
  }
  {
    struct mad_swapchain s = { 70 }; struct mad_resource r = { 1920, 1080 };
    t_pf = 70; t_w = 1920; t_h = 1080;
    CHECK(mad_swap_direct_copy(&s, &r, 1), "same format and size: blit");
    t_pf = 80;
    CHECK(!mad_swap_direct_copy(&s, &r, 1), "layer refused RGBA8 (drawable BGRA8): no blit");
    t_pf = 70; t_w = 124; t_h = 73;
    CHECK(!mad_swap_direct_copy(&s, &r, 1), "drawable left at another swapchain's size: no blit");
    t_pf = 70 | 0x00800000u; t_w = 1920; t_h = 1080;
    CHECK(mad_swap_direct_copy(&s, &r, 1), "swizzle flags do not count as a format change");
  }
  return fails != 0;
}
'''.replace('@DIRECT@', direct.group(0))
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / 'madeira_layer_format.h').write_text(hdr)
        (Path(td) / 't.c').write_text(prog)
        cc = os.environ.get('CC', 'cc')
        exe = Path(td) / 't'
        r = subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-I', td,
                            str(Path(td) / 't.c'), '-o', str(exe)], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            check(False, 'the format header and mad_swap_direct_copy compile')
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            sys.stdout.write(r.stdout)
            check(r.returncode == 0, 'format mapping and blit decision')

# the patch against the pinned DXMT
dxmt = root / 'research/dxmt'
if (dxmt / 'src/winemetal/winemetal.h').is_file() and (dxmt / '.git').exists():
    fwd = subprocess.run(['git', '-C', str(dxmt), 'apply', '--check', str(patch_path)], capture_output=True)
    rev = subprocess.run(['git', '-C', str(dxmt), 'apply', '--reverse', '--check', str(patch_path)], capture_output=True)
    check(fwd.returncode == 0 or rev.returncode == 0, 'the patch applies to (or is already in) research/dxmt')
else:
    print('note: research/dxmt is not checked out; the apply check is skipped (build/dxmt-ios/apply-madeira-patches.sh does it at build time)')

wm = section(patch, 'src/winemetal/unix/winemetal_unix.c')
check(re.search(r'\+    @try \{\n\+      layer\.pixelFormat = want;\n\+    \} @catch', wm) is not None,
      'winemetal: the requested format is tried first, the fallback only on refusal')
check('+    if (wmtr_enabled() || !a->ptr || a->len < sizeof(struct madeira_present_convert)) break;' in wm,
      'winemetal: op 8 answers 0 in remote mode and on a short argument')
check('newLibraryWithSource' in wm and 'madeira_convert_failed' in wm, 'winemetal: runtime shader, failures not retried every frame')
pr = section(patch, 'src/dxmt/dxmt_presenter.cpp')
check('+  double width = (double)target_w;' in pr and '+    if (target_format != want_format)\n+      return {};' in pr,
      'presenter: draws into the real drawable extent, drops a frame it cannot draw')
check('foreign_layer_state_.exchange' in pr and 'layer_.setProps(layer_props_);' in pr,
      'presenter: a reconfigured shared layer is configured again on the Present thread')
sc = section(patch, 'src/d3d11/d3d11_swapchain.cpp')
check(re.search(r'^-\s+abort\(\);', sc, re.M) is not None and not re.search(r'^\+\s*abort\(\);', sc, re.M),
      'd3d11: abort() on a missing Metal view is gone')
check(sc.count('chain->IsValid()') == 2 and 'DXGI_ERROR_UNSUPPORTED' in sc, 'd3d11: CreateSwapChain fails cleanly')
check('DXGI_ERROR_INVALID_CALL' in sc and 'ConvertSwapChainFormat(pDesc->Format) == WMTPixelFormatInvalid' in sc,
      'd3d11: non-swapchain formats refused')
wh = section(patch, 'src/winemetal/winemetal.h')
check(re.search(r'\+struct madeira_present_convert \{\n\+  uint64_t cmdbuf;.*\n\+  uint64_t src;.*\n\+  uint64_t dst;.*\n\+  uint64_t fence;', wh)
      is not None, 'winemetal.h: op 8 argument is four uint64 fields')

check('if (g_layer_cfg_layer == s->layer && g_layer_cfg_owner != s)' in d3d12 and
      'if (g_layer_cfg_owner == s) g_layer_cfg_owner = NULL;' in d3d12, 'd3d12: shared layer re-applied, owner cleared on release')
check('a.op = 8;' in d3d12 and 'pc.cmdbuf = cb; pc.src = src->texture; pc.dst = tex;' in d3d12, 'd3d12: converts through op 8')
check('if (tw < copy_w) copy_w = (UINT)tw;' in d3d12 and 'frame dropped' in d3d12,
      'd3d12: without op 8, a clipped copy for the same format, else a dropped frame')

for script in ('build/dxmt-ios/build.sh', 'build/madeira-d3d12/build-pe.sh'):
    check('apply-madeira-patches.sh' in (root / script).read_text(), '%s applies the DXMT patches' % script)

if failures:
    sys.exit('FAIL: %d check(s)' % len(failures))
print('PASS: layer formats stay displayable, presents follow the real drawable, and swapchain failures no longer abort')
