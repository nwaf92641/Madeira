#!/usr/bin/env python3
"""WoW64 launch routing in WineProcessBridge.m; no Wine, no app build.

Part A reads the launch function and checks that a target which is not a 32-bit PE takes
the path it took before: the name heuristic that picks the session core is untouched and
is overridden only for an i386 target, every WoW64 step (syswow64 farm, wbem, winsxs, the
host-probe export) is behind "the bundle carries the i386 set" or "the target is i386",
and ios_main_image_i386 is published before __wine_main. Part B compiles the production
madeira_pe_machine and madeira_target_machine against synthetic PE files and a fake bundle.
"""
from pathlib import Path
import re, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "app/Madeira/WineProcessBridge.m").read_text().replace("\r\n", "\n")

def function(source, start):
    a = source.index(start); b = source.index("{", a); depth = 1; c = b + 1
    while depth:
        depth += (source[c] == "{") - (source[c] == "}"); c += 1
    return source[a:c]

# ---- Part A: the launch function
thread = function(src, "static void *wine_process_thread(void *arg) {")
upstream_ec = """BOOL use_arm64ec = (force_ec && *force_ec == '1') ||
                           (strstr(madeira_exe, "x64") != NULL) ||
                           (strchr(madeira_exe, '\\\\') != NULL);"""
assert upstream_ec in thread, "the session-core heuristic is upstream's, verbatim"
assigns = re.findall(r"use_arm64ec\s*=[^=][^;]*;", thread)
assert assigns[1:] == ["use_arm64ec = NO;"], assigns
assert "if (is_i386_target) use_arm64ec = NO;" in thread, "only an i386 target changes the core"
assert "const BOOL is_i386_target = has_i386_set && target_machine == MADEIRA_IMAGE_FILE_MACHINE_I386;" in thread
farm = thread[thread.index("if (has_i386_set) {"):]
farm = farm[:farm.index("/* ml719: REPAIR THE SHELL FOLDERS.")]
assert "madeira_link_syswow64(fm, prefix, bundlePath);" in farm
inner = farm[farm.index("if (is_i386_target) {"):]
wow_wbem = 'madeira_link_wbem(fm, prefix, bundlePath, @"syswow64", @"i386-windows");'
wow_sxs = 'madeira_seed_winsxs(prefix, bundlePath, @"i386-windows", "x86");'
assert wow_wbem in inner and wow_sxs in inner, "syswow64 wbem / x86 winsxs for i386 targets only"
# the arm64 side-by-side store follows the session's 64-bit farm for every session (WinSxS.h)
arm_sxs = 'madeira_seed_winsxs(prefix, bundlePath, [NSString stringWithUTF8String:bundle_subdir], "arm64");'
assert thread.count(arm_sxs) == 1 and thread.index(arm_sxs) > thread.index("if (has_i386_set) {")
# cnc-ddraw (MADEIRA_DDRAW=cnc) right after the i386 farm relink (docs/DIRECTDRAW.md)
assert "madeira_link_syswow64(fm, prefix, bundlePath);\n                madeira_apply_cnc_ddraw(fm, prefix, bundlePath);" in farm
# system32\wbem comes from the session's own 64-bit farm, for every session,
# outside the WoW64 gate (the arm64ec farm ships wbemprox/wmiutils).
sys_wbem = thread.index('madeira_link_wbem(fm, prefix, bundlePath, @"system32",')
assert sys_wbem < thread.index("if (has_i386_set) {"), "system32 wbem is not gated on WoW64"
assert "[NSString stringWithUTF8String:bundle_subdir]" in thread[sys_wbem:sys_wbem + 200]
assert thread.count("madeira_link_wbem(") == 2
for call in ["madeira_link_syswow64(", wow_wbem, wow_sxs, "madeira_apply_cnc_ddraw(",
             "madeira_publish_host_probe()"]:
    assert thread.count(call) == 1, call
assert thread.index("ios_main_image_i386 = is_i386_target ? 1 : 0;") < thread.index("__wine_main(argc, argv);")
assert "if (has_i386_set) madeira_publish_host_probe();" in thread
exe = thread[thread.index("char exe_path[512];"):]
exe = exe[:exe.index("// Optional MADEIRA_ARGS")]
i386_branch = exe[exe.index("} else if (is_i386_target) {"):]
assert i386_branch.index('"C:\\\\windows\\\\syswow64\\\\%s"') < i386_branch.index("} else {"), exe
assert 'snprintf(exe_path, sizeof(exe_path), "C:\\\\windows\\\\system32\\\\%s", madeira_exe);' in exe
# The table and the per-architecture layout moved to WinSxS.c (plain C,
# exercised under Wine by check-winsxs.py); the bridge only calls it.
sxs = function(src, "static void madeira_seed_winsxs(")
assert "madeira_winsxs_seed(" in sxs
csrc = (root / "app/Madeira/WinSxS.c").read_text()
assert "%s_%s_%s_%s_none_deadbeef" in csrc or "_none_deadbeef" in csrc
print("PASS: non-i386 targets keep the upstream core choice, farms and exe path; WoW64 steps are gated")

# ---- Part B: the machine probe
pe_machine = function(src, "static uint16_t madeira_pe_machine(")
target = function(src, "static uint16_t madeira_target_machine(")
target = target.replace("NSString *bundle", "const char *bundle").replace("bundle.fileSystemRepresentation", "bundle")
code = r"""
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>
#include <assert.h>
""" + pe_machine + "\n" + target + r"""
int main(int argc, char **argv)
{
    const char *d = argv[1];
    char prefix[PATH_MAX], bundle[PATH_MAX], p[PATH_MAX];
    snprintf(prefix, sizeof(prefix), "%s/prefix", d);
    snprintf(bundle, sizeof(bundle), "%s/bundle", d);
    snprintf(p, sizeof(p), "%s/pe32.exe", d);     assert(madeira_pe_machine(p) == 0x14c);
    snprintf(p, sizeof(p), "%s/pe64.exe", d);     assert(madeira_pe_machine(p) == 0x8664);
    snprintf(p, sizeof(p), "%s/text.exe", d);     assert(madeira_pe_machine(p) == 0);
    snprintf(p, sizeof(p), "%s/short.exe", d);    assert(madeira_pe_machine(p) == 0);
    snprintf(p, sizeof(p), "%s/badlfa.exe", d);   assert(madeira_pe_machine(p) == 0);
    snprintf(p, sizeof(p), "%s/missing.exe", d);  assert(madeira_pe_machine(p) == 0);
    assert(madeira_pe_machine(NULL) == 0 && madeira_pe_machine("") == 0);
    /* full paths under drive_c, backslashes and spaces */
    assert(madeira_target_machine("C:\\Program Files\\App Dir\\app32.exe", prefix, bundle) == 0x14c);
    assert(madeira_target_machine("c:\\Program Files\\App Dir\\app64.exe", prefix, bundle) == 0x8664);
    assert(madeira_target_machine("C:\\Program Files\\App Dir\\none.exe", prefix, bundle) == 0);
    /* other forms are not probed */
    assert(madeira_target_machine("D:\\app32.exe", prefix, bundle) == 0);
    assert(madeira_target_machine("\\Program Files\\App Dir\\app32.exe", prefix, bundle) == 0);
    /* bare names: a name in a 64-bit farm is 64-bit even if i386-windows has it too */
    assert(madeira_target_machine("both.exe", prefix, bundle) == 0);
    assert(madeira_target_machine("only32.exe", prefix, bundle) == 0x14c);
    assert(madeira_target_machine("nowhere.exe", prefix, bundle) == 0);
    puts("probe ok");
    return 0;
}
"""

def pe(machine, lfanew=0x80):
    b = bytearray(lfanew + 24)
    b[0:2] = b"MZ"; b[0x3c:0x40] = lfanew.to_bytes(4, "little")
    b[lfanew:lfanew + 4] = b"PE\0\0"; b[lfanew + 4:lfanew + 6] = machine.to_bytes(2, "little")
    return bytes(b)

with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "pe32.exe").write_bytes(pe(0x14c)); (t / "pe64.exe").write_bytes(pe(0x8664))
    (t / "text.exe").write_bytes(b"not a PE file at all, just some text" * 4)
    (t / "short.exe").write_bytes(b"MZ\0\0")
    bad = bytearray(pe(0x14c)); bad[0x3c:0x40] = (32 << 20).to_bytes(4, "little"); (t / "badlfa.exe").write_bytes(bad)
    app = t / "prefix/drive_c/Program Files/App Dir"; app.mkdir(parents=True)
    (app / "app32.exe").write_bytes(pe(0x14c)); (app / "app64.exe").write_bytes(pe(0x8664))
    for farm in ["aarch64-windows", "arm64ec-windows", "i386-windows"]:
        (t / "bundle" / farm).mkdir(parents=True)
    (t / "bundle/aarch64-windows/both.exe").write_bytes(pe(0xaa64))
    (t / "bundle/i386-windows/both.exe").write_bytes(pe(0x14c))
    (t / "bundle/i386-windows/only32.exe").write_bytes(pe(0x14c))
    c = t / "check.c"; exe = t / "check"; c.write_text(code)
    subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-Werror=implicit-function-declaration", str(c), "-o", str(exe)], check=True)
    out = subprocess.run([str(exe), str(t)], check=True, capture_output=True, text=True)
    assert "probe ok" in out.stdout, out.stdout + out.stderr
print("PASS: PE machine probe: PE32/PE32+/non-PE/truncated/huge e_lfanew; drive_c paths; bare names prefer the 64-bit farms")
