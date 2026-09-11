#!/bin/sh
# Unit tests for the DeviceCapabilities policy helpers.
#
# These produce the numbers a release ships with -- the JIT pool size derived
# from the device's jetsam budget, and the parsing of the madeira-*.txt override
# files -- so they are asserted, not eyeballed. Everything under test is a pure
# function: the harness injects the memory budget instead of measuring it, and
# calls the parsers directly instead of touching Documents.
#
# The pool table's first four rows are the point of the whole exercise: every
# device at or below the 4096MB budget of the device this emulator was developed
# against must still get exactly 896MB, or the change would silently move the VA
# floor on the only hardware it has ever been validated on.
#
# Needs a Swift toolchain: Xcode's swiftc on macOS, or any swiftc on Linux.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

SWIFTC=${SWIFTC:-swiftc}
if ! command -v "$SWIFTC" >/dev/null 2>&1; then
    echo "test-device-capabilities: SKIP -- no swiftc on PATH (override with SWIFTC=...)" >&2
    exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# The production file imports Apple-only modules. Redirect the memory probe to
# an injectable function, and off Darwin stand in for sysctl and os/proc.h.
sed -e 's/os_proc_available_memory()/testAvailableMemory()/g' \
    app/Madeira/DeviceCapabilities.swift > "$TMP/DeviceCapabilitiesTest.swift"

SOURCES="$TMP/DeviceCapabilitiesTest.swift"
if [ "$(uname -s)" != "Darwin" ]; then
    sed -i.bak -e 's/^import Darwin$/import Glibc/' -e '/^import os$/d' \
        "$TMP/DeviceCapabilitiesTest.swift"
    rm -f "$TMP/DeviceCapabilitiesTest.swift.bak"
    cat > "$TMP/Shims.swift" <<'SWIFT'
import Foundation
import Glibc

@discardableResult
func sysctlbyname(_ name: UnsafePointer<CChar>!,
                  _ oldp: UnsafeMutableRawPointer!,
                  _ oldlenp: UnsafeMutablePointer<Int>!,
                  _ newp: UnsafeMutableRawPointer!,
                  _ newlen: Int) -> Int32 { return -1 }
SWIFT
    SOURCES="$SOURCES $TMP/Shims.swift"
fi

cat > "$TMP/main.swift" <<'SWIFT'
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif
import Foundation

var testBudgetBytes = 0
func testAvailableMemory() -> Int { return testBudgetBytes }

var failures = 0
func check(_ label: String, _ got: String, _ want: String) {
    let ok = got == want
    if !ok { failures += 1 }
    print("  \(ok ? "ok  " : "FAIL") \(label): got \(got), want \(want)")
}

// MARK: - JIT pool size

print("JIT pool size vs jetsam budget:")
let poolCases: [(budgetMB: Int, poolMB: Int)] = [
    // At or below the development device: must not move.
    (0, 896), (1024, 896), (2048, 896), (4096, 896),
    // Above it: scales proportionally, rounded down to a 32MB multiple.
    (5120, 1120), (6144, 1344), (7168, 1568),
    // And is capped, because the resident set does not grow with the device.
    (8192, 1792), (12288, 1792), (16384, 1792),
]
for c in poolCases {
    testBudgetBytes = c.budgetMB * 1024 * 1024
    check("budget \(c.budgetMB)MB", "\(DeviceCapabilities.recommendedPoolMB())", "\(c.poolMB)")
}

// MARK: - madeira-fex.txt

print("madeira-fex.txt parsing:")
let fexCases: [(text: String, applied: String, rejected: Int)] = [
    ("TSOENABLED=0", "(\"FEX_TSOENABLED\", \"0\")", 0),
    ("tsoenabled = 0", "(\"FEX_TSOENABLED\", \"0\")", 0),
    ("  TSOENABLED=0  ", "(\"FEX_TSOENABLED\", \"0\")", 0),
    ("A=a=b", "(\"FEX_A\", \"a=b\")", 0),
    ("MULTIBLOCK=1, X87REDUCEDPRECISION=1",
     "(\"FEX_MULTIBLOCK\", \"1\"), (\"FEX_X87REDUCEDPRECISION\", \"1\")", 0),
    ("MULTIBLOCK=1\nX87REDUCEDPRECISION=1",
     "(\"FEX_MULTIBLOCK\", \"1\"), (\"FEX_X87REDUCEDPRECISION\", \"1\")", 0),
    // A line with no '=' is a comment; it is not a malformed entry.
    ("# why: CEF crashes without this\ngarbage\n", "", 0),
    // These name a key but carry no usable value. Applying either would put a
    // wrong value into the environment, which fails quietly at run time.
    ("TSOENABLED=", "", 1),
    ("TSOENABLED=0 MULTIBLOCK=1", "", 1),
    ("=1", "", 1),
    ("", "", 0),
]
for c in fexCases {
    let got = DeviceCapabilities.fexConfigEntries(from: c.text)
    let rendered = got.applied.map { "(\"\($0.key)\", \"\($0.value)\")" }.joined(separator: ", ")
    check("\(String(reflecting: c.text))",
          "[\(rendered)] rejected=\(got.rejected.count)",
          "[\(c.applied)] rejected=\(c.rejected)")
}

// MARK: - madeira-resolution.txt

print("madeira-resolution.txt parsing:")
let resCases: [(text: String, want: String)] = [
    ("1280x720", "(w: 1280, h: 720)"),
    ("1920X1080", "(w: 1920, h: 1080)"),
    ("  640x480  ", "(w: 640, h: 480)"),
    // Out of the accepted range, or not a size at all.
    ("100x100", "nil"), ("5000x500", "nil"), ("abc", "nil"),
    ("1024", "nil"), ("", "nil"), ("1024x", "nil"), ("x768", "nil"),
]
for c in resCases {
    let got = DeviceCapabilities.parseDesktopResolution(c.text)
    check("\(String(reflecting: c.text))", got.map { "\($0)" } ?? "nil", c.want)
}

print(failures == 0
      ? "test-device-capabilities: OK"
      : "test-device-capabilities: \(failures) FAILURES")
exit(failures == 0 ? 0 : 1)
SWIFT

# shellcheck disable=SC2086
"$SWIFTC" -O -o "$TMP/test-device-capabilities" $SOURCES "$TMP/main.swift"
"$TMP/test-device-capabilities"
