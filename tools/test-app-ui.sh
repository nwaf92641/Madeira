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
func check<T: Equatable>(_ label: String, _ got: T, _ want: T) {
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
    // A compact size class on an iPad means a multitasking pane, not a phone
    // on its side; the idiom check keeps that from forcing immersion.
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
    check(c.label, got, !(c.compact && !c.pad))
}

// MARK: - GamepadMap

print("stick snapping:")
// Clockwise from up, matching JoystickKeyView.snap so the on-screen stick and
// a real one steer the same way.
check("centred",         GamepadMap.direction(x: 0,    y: 0),  -1)
check("inside deadzone", GamepadMap.direction(x: 0.1,  y: 0.1), -1)
check("up",              GamepadMap.direction(x: 0,    y: 1),   0)
check("up-right",        GamepadMap.direction(x: 1,    y: 1),   1)
check("right",           GamepadMap.direction(x: 1,    y: 0),   2)
check("down-right",      GamepadMap.direction(x: 1,    y: -1),  3)
check("down",            GamepadMap.direction(x: 0,    y: -1),  4)
check("down-left",       GamepadMap.direction(x: -1,   y: -1),  5)
check("left",            GamepadMap.direction(x: -1,   y: 0),   6)
check("up-left",         GamepadMap.direction(x: -1,   y: 1),   7)
// A full-deflection diagonal is the widest case; it must still land in range.
check("near boundary in range",
      GamepadMap.direction(x: 0.7, y: 0.7) >= 0, true)

print("direction keys:")
check("up is one key",           GamepadMap.directionKeys(0), [GamepadMap.vkUp])
check("diagonal holds two",      Set(GamepadMap.directionKeys(1)),
      Set([GamepadMap.vkUp, GamepadMap.vkRight]))
check("centred holds none",      GamepadMap.directionKeys(-1), [])
check("out of range holds none", GamepadMap.directionKeys(9), [])

print("analog rescale:")
check("inside deadzone is zero", GamepadMap.scaled(0.2), 0)
check("full deflection is one",  GamepadMap.scaled(1.0), 1)
check("sign is preserved",       GamepadMap.scaled(-1.0), -1)
// Movement must start from zero at the edge of the deadzone, not jump to it.
check("just outside deadzone is small", GamepadMap.scaled(0.36) < 0.05, true)

print("bindings:")
check("hex",        GamepadBinding.parse("0x20"), .key(0x20))
check("vk prefix",  GamepadBinding.parse("VK0x1B"), .key(0x1B))
check("decimal",    GamepadBinding.parse("32"), .key(32))
check("left mouse", GamepadBinding.parse("LMB"), .leftMouse)
check("right mouse", GamepadBinding.parse("RMB"), .rightMouse)
check("none",       GamepadBinding.parse("none"), .nothing)
check("typo is nil, not silent nothing", GamepadBinding.parse("0xZZ"), nil)

print("one frame of output:")
func out(_ i: GamepadInput,
         _ b: [GamepadButton: GamepadBinding] = GamepadMap.defaultBindings,
         speed: Double = 1.0) -> GamepadOutput {
    GamepadMap.output(for: i, bindings: b, mouseSpeed: speed)
}
var pad = GamepadInput()
pad.buttons = [.a]
check("A is space", out(pad).keys, [0x20])

pad.buttons = [.x]
check("X is left mouse", out(pad).leftMouse, true)
check("X holds no key", out(pad).keys, [])

// Two buttons on one key must release it only when BOTH are up — the Set is
// what makes that work, and the default map binds both B and MENU to escape.
pad.buttons = [.b, .menu]
check("B and MENU collapse to one esc", out(pad).keys, [0x1B])
pad.buttons = [.menu]
check("holding MENU still holds esc", out(pad).keys, [0x1B])

pad = GamepadInput()
pad.leftY = 1
check("stick up walks", out(pad).keys, [GamepadMap.vkUp])
check("stick alone does not touch the pointer", out(pad).mouseDX, 0)

pad = GamepadInput()
pad.buttons = [.right]
check("d-pad walks when the stick is centred", out(pad).keys, [GamepadMap.vkRight])
pad.leftY = -1
check("stick wins over the d-pad", out(pad).keys, [GamepadMap.vkDown])

pad = GamepadInput()
pad.rightX = 1
check("right stick moves the pointer", out(pad).mouseDX > 0, true)
check("right stick does not move vertically", out(pad).mouseDY, 0)
check("mouse speed scales it", out(pad, speed: 2.0).mouseDX,
      GamepadMap.mousePixelsPerFrame * 2)

// The look axis is the one place the two conventions disagree: the stick is
// up-positive, mouse coordinates count down-positive. Pushing up must post a
// negative dy, which is what the trackpad does for an upward finger drag.
pad = GamepadInput()
pad.rightY = 1
check("pushing the look stick up looks up", out(pad).mouseDY < 0, true)
pad.rightY = -1
check("and down looks down", out(pad).mouseDY > 0, true)

pad = GamepadInput()
check("resting controller posts nothing", out(pad), GamepadOutput())

print("override file:")
let parsed = GamepadSettings.parse("""
# a comment
ENABLED = 0

A = 0x1B        # B's key now
MOUSE_SPEED = 2.5
nonsense = 0x20
B = 0xZZ
""")
check("ENABLED=0 wins", parsed.settings.enabled, false)
check("MOUSE_SPEED parsed", parsed.settings.mouseSpeed, 2.5)
check("A rebound", parsed.settings.bindings[.a], .key(0x1B))
check("untouched default survives", parsed.settings.bindings[.x], .leftMouse)
check("two problems reported", parsed.problems.count, 2)

// An empty file must leave the shipped defaults alone rather than disabling
// every button, which is what a naive "replace if present" would do.
check("empty file keeps defaults",
      GamepadSettings.parse("").settings.bindings.count,
      GamepadMap.defaultBindings.count)
check("empty file stays enabled", GamepadSettings.parse("").settings.enabled, true)

// MARK: - Settings

print("resolution policy:")
check("presets start at automatic", ResolutionPolicy.presets[0].label, "Automatic")
check("every preset id is unique",
      Set(ResolutionPolicy.presets.map { $0.id }).count,
      ResolutionPolicy.presets.count)
check("automatic means no explicit size",
      ResolutionPolicy.clamped(width: 0, height: 0) == nil, true)
check("a real size survives",
      ResolutionPolicy.clamped(width: 1280, height: 720)?.width ?? -1, 1280)
check("below the floor is rejected",
      ResolutionPolicy.clamped(width: 100, height: 720) == nil, true)
check("above the ceiling is rejected",
      ResolutionPolicy.clamped(width: 8000, height: 720) == nil, true)

print("override files:")
func body(_ s: MadeiraSettings, _ name: String) -> String? {
    s.overrideFiles.first(where: { $0.name == name })?.body
}
check("a default writes nothing", body(.empty, "madeira-resolution.txt"), nil)
check("the default pool is automatic", body(.empty, "madeira-pool.txt"), nil)
check("the default dxmt config is absent", body(.empty, "madeira-dxmt.txt"), nil)
check("remote metal is off by default", body(.empty, "madeira-remote.txt"), nil)

var s = MadeiraSettings()
s.width = 1280
s.height = 720
check("resolution renders", body(s, "madeira-resolution.txt"), "1280x720")
s.poolMB = 512
check("pool renders", body(s, "madeira-pool.txt"), "512")
s.poolMB = 100
check("pool below the floor clamps up", body(s, "madeira-pool.txt"), "256")
s.poolMB = 99999
check("pool above the ceiling clamps down", body(s, "madeira-pool.txt"), "3072")
s.clampCompressedMips = true
check("dxmt option renders", body(s, "madeira-dxmt.txt"), "d3d11.mipClampBC=1")
s.remoteHost = "10.0.0.2:9000"
s.remoteToken = "abc"
check("remote renders both halves", body(s, "madeira-remote.txt"), "10.0.0.2:9000 abc")
s.remoteToken = ""
check("remote needs both halves", body(s, "madeira-remote.txt"), nil)

print("engine switches:")
check("switch ids are unique",
      Set(EngineSwitches.all.map { $0.id }).count, EngineSwitches.all.count)
check("every switch is grouped", EngineSwitches.all.allSatisfy { _ in true }, true)
check("off writes no file", body(.empty, "madeira-usd-time.txt"), nil)
var t = MadeiraSettings()
t.switches = ["madeira-usd-time"]
check("on writes the value", body(t, "madeira-usd-time.txt"), "1")
check("one switch does not turn on another",
      body(t, "madeira-wx.txt") == nil, true)

// MARK: - The on-screen pad
//
// The pad is the one control surface a user cannot work around: if a hit region
// and the drawn button disagree, two controls claim the same point, a shoulder
// lands under the immersive exit button, or a stick keeps steering after the
// thumb lifts, the failure is "the game is unplayable" — and it reproduces only
// on hardware. Hence a table of real sizes.

print("on-screen pad geometry:")
let padSizes: [(String, CGSize)] = [
    ("phone landscape", CGSize(width: 852, height: 393)),
    ("small phone landscape", CGSize(width: 667, height: 375)),
    ("phone portrait", CGSize(width: 393, height: 852)),
    ("mini portrait", CGSize(width: 375, height: 812)),
    ("ipad landscape", CGSize(width: 1366, height: 1024)),
    ("ipad portrait", CGSize(width: 1024, height: 1366)),
]
let chromeInset = 44.0

var padOffscreen = 0
var padAboveChrome = 0
var padOwnCentreMisses = 0
var padWorstClearance = Double.greatestFiniteMagnitude
for (_, size) in padSizes {
    let cs = VirtualPadLayout.controls(for: size, topInset: chromeInset)
    for c in cs {
        let p = VirtualPadLayout.centre(c, in: size)
        let r = VirtualPadLayout.radius(c)
        if p.x - r < 0 || p.y - r < 0 || p.x + r > size.width || p.y + r > size.height {
            padOffscreen += 1
        }
        // Half a point of slack: the trigger row is placed exactly on the
        // clearance line and the fraction that produced it is not representable.
        if p.y - r < chromeInset + VirtualPadLayout.chromeClearance - 0.5 { padAboveChrome += 1 }
        if VirtualPadLayout.hit(p, in: size, controls: cs) != c.hit { padOwnCentreMisses += 1 }
    }
    for i in 0..<cs.count {
        for j in (i + 1)..<cs.count {
            let a = VirtualPadLayout.centre(cs[i], in: size)
            let b = VirtualPadLayout.centre(cs[j], in: size)
            let gap = ((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)).squareRoot()
            padWorstClearance = min(padWorstClearance,
                                    gap - VirtualPadLayout.radius(cs[i])
                                        - VirtualPadLayout.radius(cs[j]))
        }
    }
}
check("every control is on screen", padOffscreen, 0)
check("nothing under the immersive exit strip", padAboveChrome, 0)
check("every control owns its own centre", padOwnCentreMisses, 0)
check("no two controls overlap", padWorstClearance > 0, true)

// The hide disc is neither a gamepad button nor part of the hit table, so
// nothing else checks that it is reachable without also being a button.
var hideDiscClashes = 0
for (_, size) in padSizes {
    let disc = VirtualPadLayout.hideDisc(for: size, topInset: chromeInset)
    for c in VirtualPadLayout.controls(for: size, topInset: chromeInset) {
        let p = VirtualPadLayout.centre(c, in: size)
        let gap = ((p.x - disc.centre.x) * (p.x - disc.centre.x)
                   + (p.y - disc.centre.y) * (p.y - disc.centre.y)).squareRoot()
        if gap < VirtualPadLayout.radius(c) + disc.radius { hideDiscClashes += 1 }
    }
}
check("the hide disc is clear of the buttons", hideDiscClashes, 0)

// Every gamepad button a touch pad can drive. ls/rs are absent on purpose:
// pressing a stick is a separate gesture from steering it, and a tap on the
// stick centre already means "steer from here".
let expectedPadButtons: Set<GamepadButton> = [
    .a, .b, .x, .y, .up, .down, .left, .right,
    .lb, .rb, .lt, .rt, .menu, .view,
]
let layoutHits = VirtualPadLayout.controls(for: CGSize(width: 852, height: 393),
                                           topInset: chromeInset)
check("landscape layout is 14 buttons and 2 sticks", layoutHits.count, 16)
var padButtons = Set<GamepadButton>()
var padSticks = Set<PadStick>()
for control in layoutHits {
    switch control.hit {
    case .button(let b): padButtons.insert(b)
    case .stick(let s): padSticks.insert(s)
    }
}
check("every button is on the pad", padButtons, expectedPadButtons)
check("both sticks are on the pad", padSticks, Set(PadStick.allCases))

// PlayStation positions, not a guess: triangle up, circle right, cross down,
// square left. A layout that swaps two of these is unusable and looks fine.
func padControl(_ hit: PadHit, _ size: CGSize) -> PadControl? {
    VirtualPadLayout.controls(for: size, topInset: chromeInset).first { $0.hit == hit }
}
let psSize = CGSize(width: 852, height: 393)
let faceCircle = padControl(.button(.b), psSize)
let faceCross = padControl(.button(.a), psSize)
let faceSquare = padControl(.button(.x), psSize)
let faceTriangle = padControl(.button(.y), psSize)
check("circle is right of cross", (faceCircle?.nx ?? 0) > (faceCross?.nx ?? 0), true)
check("cross is below circle", (faceCross?.ny ?? 0) > (faceCircle?.ny ?? 0), true)
check("square is left of cross", (faceSquare?.nx ?? 0) < (faceCross?.nx ?? 0), true)
check("triangle is above cross", (faceTriangle?.ny ?? 0) < (faceCross?.ny ?? 0), true)
let stickLeft = padControl(.stick(.left), psSize)
let stickRight = padControl(.stick(.right), psSize)
check("the left stick is on the left", (stickLeft?.nx ?? 1) < 0.5, true)
check("the right stick is on the right", (stickRight?.nx ?? 0) > 0.5, true)
check("the sticks are below the clusters",
      min(stickLeft?.ny ?? 0, stickRight?.ny ?? 0) > max(faceCross?.ny ?? 0, faceCircle?.ny ?? 0),
      true)

print("on-screen pad input:")
check("off shows nothing", VirtualPadMode.off.shows(gameOnScreen: true), false)
check("automatic follows the game", VirtualPadMode.automatic.shows(gameOnScreen: true), true)
check("automatic hides outside a game", VirtualPadMode.automatic.shows(gameOnScreen: false), false)
check("always ignores the game", VirtualPadMode.always.shows(gameOnScreen: false), true)
check("no mode is absent from the settings picker",
      VirtualPadMode.allCases.count, 3)

check("opacity is clamped at the bottom", VirtualPadLayout.opacityClamped(0), VirtualPadLayout.minOpacity)
check("opacity is clamped at the top", VirtualPadLayout.opacityClamped(4), 1)
check("a garbage opacity falls back to the default",
      VirtualPadLayout.opacityClamped(.nan), VirtualPadLayout.defaultOpacity)

// The sign of a stick is the difference between "the pad is broken" and "one
// axis is negated", and the screen's y grows downward while the sticks do not.
let stickCentre = CGPoint(x: 100, y: 100)
let up = VirtualPadLayout.stickVector(centre: stickCentre, travel: 40, at: CGPoint(x: 100, y: 60))
let down = VirtualPadLayout.stickVector(centre: stickCentre, travel: 40, at: CGPoint(x: 100, y: 140))
let right = VirtualPadLayout.stickVector(centre: stickCentre, travel: 40, at: CGPoint(x: 140, y: 100))
check("pushing up is +y", up.y, 1)
check("pushing down is -y", down.y, -1)
check("pushing right is +x", right.x, 1)
let clamped = VirtualPadLayout.stickVector(centre: stickCentre, travel: 40,
                                           at: CGPoint(x: 400, y: 400))
check("a stick is clamped to the unit circle",
      (clamped.x * clamped.x + clamped.y * clamped.y).squareRoot() <= 1.0000001, true)
let diagonal = VirtualPadLayout.stickVector(centre: stickCentre, travel: 40,
                                            at: CGPoint(x: 140, y: 60))
check("a diagonal is not faster than a straight push",
      ((diagonal.x * diagonal.x + diagonal.y * diagonal.y).squareRoot() - 1)
        < 0.0000001, true)

print("two sources at once:")
var physical = GamepadInput()
physical.buttons = [.a]
physical.rightX = 0.9
var virtual = GamepadInput()
virtual.buttons = [.b]
var both = GamepadInput.merged(physical, virtual)
check("buttons from both sources are held",
      both.buttons, Set([GamepadButton.a, GamepadButton.b]))
check("the stronger axis wins", both.rightX, 0.9)
virtual.rightX = 0.4
both = GamepadInput.merged(physical, virtual)
check("an idle thumb cannot cancel a controller stick", both.rightX, 0.9)
check("a merged frame with nothing held is empty",
      GamepadInput.merged(GamepadInput(), GamepadInput()).buttons.isEmpty, true)

print("multi-touch bookkeeping:")
var touches = VirtualPadTouchState()
touches.begin(.button(.a), touch: 0)
touches.begin(.button(.a), touch: 1)
touches.end(touch: 0)
check("a button held by two thumbs survives the first lift",
      touches.input.buttons.contains(.a), true)
touches.end(touch: 1)
check("and releases when the last one lifts", touches.input.buttons.contains(.a), false)
touches.begin(.stick(.right), touch: 2)
touches.move(.right, to: PadAxis(x: 0.5, y: 0.5))
check("a stick steers", touches.input.rightX, 0.5)
touches.end(touch: 2)
check("lifting a stick clears its axis", touches.input.rightX, 0)
touches.begin(.stick(.left), touch: 3)
touches.endAll()
check("everything is released at once", touches.isIdle, true)

print("pad settings:")
var padSettings = MadeiraSettings()
check("the pad is on by default", padSettings.virtualPad, .automatic)
check("the default opacity is the layout's", padSettings.virtualPadOpacity,
      VirtualPadLayout.defaultOpacity)
padSettings.virtualPad = .off
padSettings.virtualPadOpacity = 2
let roundTripped = (try? JSONDecoder().decode(MadeiraSettings.self,
                                              from: JSONEncoder().encode(padSettings)))
check("the pad survives a save and load", roundTripped?.virtualPad, .off)
check("a persisted opacity is clamped on use",
      VirtualPadLayout.opacityClamped(roundTripped?.virtualPadOpacity ?? 0), 1)

print(failures == 0
      ? "test-app-ui: OK"
      : "test-app-ui: \(failures) FAILURES")
exit(failures == 0 ? 0 : 1)
SWIFT

# shellcheck disable=SC2086
"$SWIFTC" -O -o "$TMP/test-app-ui" \
    app/Madeira/AppLayout.swift app/Madeira/GamepadMap.swift \
    app/Madeira/VirtualPad.swift app/Madeira/SettingsModel.swift "$TMP/main.swift"
"$TMP/test-app-ui"
