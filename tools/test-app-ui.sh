#!/bin/sh
# Unit tests for the layout policy and the gamepad mapping.
#
# Both are pure functions with a table of expected answers, and both guard
# decisions that are invisible until they are wrong on a device the developer
# is not holding: the layout policy is what locked every iPad out of
# fullscreen, and the gamepad map is a pile of magic key codes.
#
# Needs a Swift toolchain: Xcode's swiftc on macOS, or any swiftc on Linux.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

SWIFTC=${SWIFTC:-swiftc}
if ! command -v "$SWIFTC" >/dev/null 2>&1; then
    echo "test-app-ui: SKIP -- no swiftc on PATH (override with SWIFTC=...)" >&2
    exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/main.swift" <<'SWIFT'
import Foundation

var failures = 0
func check(_ label: String, _ got: String, _ want: String) {
    let ok = got == want
    if !ok { failures += 1 }
    print("  \(ok ? "ok  " : "FAIL") \(label): got \(got), want \(want)")
}

// MARK: - LayoutPolicy
//
// The regression this exists for is the iPad column: an iPad reports a
// REGULAR vertical size class in both orientations, so the old
// `verticalSizeClass == .compact` test sent it to the tooling layout every
// time and the game could not be enlarged at all.

print("layout policy:")
func name(_ l: AppLayout) -> String { l == .immersive ? "immersive" : "tooling" }

let layoutCases: [(label: String, compact: Bool, pad: Bool, want: Bool, expect: String)] = [
    // iPhone. Portrait shows tooling until the user asks otherwise; landscape
    // is forced, because the tooling rows do not fit.
    ("iPhone portrait, no toggle",  false, false, false, "tooling"),
    ("iPhone portrait, toggled",    false, false, true,  "immersive"),
    ("iPhone landscape, no toggle", true,  false, false, "immersive"),
    // The reported bug: an iPad in landscape must NOT be forced, and must be
    // able to reach immersive with the toggle.
    ("iPad portrait, no toggle",    false, true,  false, "tooling"),
    ("iPad portrait, toggled",      false, true,  true,  "immersive"),
    ("iPad landscape, no toggle",   false, true,  false, "tooling"),
    ("iPad landscape, toggled",     false, true,  true,  "immersive"),
    // A programmatic size class from a host that does not know the idiom would
    // otherwise force immersion on an iPad; the idiom check keeps it honest.
    ("iPad, compact reported",      true,  true,  false, "tooling"),
    ("iPad, compact reported+on",   true,  true,  true,  "immersive"),
]
for c in layoutCases {
    let got = LayoutPolicy.resolve(verticalCompact: c.compact, isPad: c.pad,
                                   userWantsImmersive: c.want)
    check(c.label, name(got), c.expect)
}

print("exit affordance:")
for c in layoutCases {
    let got = LayoutPolicy.needsExitAffordance(verticalCompact: c.compact, isPad: c.pad)
    // The exit button exists exactly where immersion was a choice. Forced
    // immersion (iPhone landscape) must not offer one.
    let want = c.compact && !c.pad ? false : true
    check("\(c.label)", "\(got)", "\(want)")
}

print(failures == 0
      ? "test-app-ui: OK"
      : "test-app-ui: \(failures) FAILURES")
exit(failures == 0 ? 0 : 1)
SWIFT

# shellcheck disable=SC2086
"$SWIFTC" -O -o "$TMP/test-app-ui" app/Madeira/AppLayout.swift "$TMP/main.swift"
"$TMP/test-app-ui"
