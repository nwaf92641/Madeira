import SwiftUI
import UIKit

/// Live state for the on-screen pad.
///
/// This exists because the pad cannot be a SwiftUI child of the game view: the
/// game surface is a window-level `UIView` inserted ABOVE the whole SwiftUI
/// hierarchy (see `MetalHostView`), so anything drawn underneath it is simply
/// covered. The pad is therefore hosted in its own window — which also means it
/// cannot read `ContentView`'s state, and needs somewhere shared to live.
///
/// Both the overlay (which draws and takes touches) and `VirtualPadWindow`
/// (which decides whether a touch belongs to the pad or to the game) read this
/// same object, so the drawn button and the claimed touch region cannot drift
/// apart.
final class VirtualPadState: ObservableObject {
    static let shared = VirtualPadState()

    /// Is the pad up? Written by the overlay, read by the window's hit test.
    @Published private(set) var visible = false
    /// Set once the overlay window exists, so "always" has somewhere to draw.
    @Published private(set) var attached = false
    /// 0.2...1. Mirrored from settings for the same reason as `visible`.
    @Published var opacity = VirtualPadLayout.defaultOpacity
    /// What the last frame drew: knob positions and held-button highlights.
    @Published private(set) var axes: [PadStick: PadAxis] = [:]
    @Published private(set) var held: Set<GamepadButton> = []

    private var touches = VirtualPadTouchState()

    private init() {}

    func markAttached() { attached = true }

    func setVisible(_ next: Bool) {
        guard next != visible else { return }
        visible = next
        // Hiding is the one case where nothing else will lift a held key: the
        // touch that would have ended is gone with the view.
        if !next { releaseAll() }
        fputs("[pad] on-screen pad \(next ? "shown" : "hidden")\n", stderr)
    }

    /// Does the pad own this window point? Read by `ControlsWindow` so the two
    /// overlays cannot both claim the same tap.
    func claims(_ point: CGPoint, in bounds: CGRect) -> Bool {
        guard visible else { return false }
        let controls = VirtualPadLayout.controls(for: bounds.size,
                                                 topInset: GameChromeState.shared.topInset)
        if VirtualPadLayout.hit(point, in: bounds.size, controls: controls) != nil { return true }
        return Self.hitsHideDisc(point, in: bounds)
    }

    /// The hide disc is not a gamepad button, so it is not in the hit table —
    /// but it does have to take touches, or it is decoration.
    static func hitsHideDisc(_ point: CGPoint, in bounds: CGRect) -> Bool {
        let disc = VirtualPadLayout.hideDisc(for: bounds.size,
                                            topInset: GameChromeState.shared.topInset)
        let dx = Double(point.x - disc.centre.x), dy = Double(point.y - disc.centre.y)
        return (dx * dx + dy * dy).squareRoot() <= disc.radius + VirtualPadLayout.hitSlop
    }

    // MARK: - touch plumbing

    /// `touch` is the control's index in the layout: each control view owns one
    /// gesture, so an index identifies a finger for as long as it is down.
    func begin(_ hit: PadHit, touch: Int) {
        touches.begin(hit, touch: touch)
        push()
    }

    func move(_ stick: PadStick, to axis: PadAxis) {
        touches.move(stick, to: axis)
        push()
    }

    func end(touch: Int) {
        touches.end(touch: touch)
        push()
    }

    func releaseAll() {
        touches.endAll()
        push()
    }

    private func push() {
        GamepadBridge.shared.setVirtual(touches.input)
        // Only on the frames that changed, or the whole overlay re-renders at
        // 60Hz for nothing while a single button is held.
        let buttons = touches.buttons
        if buttons != held { held = buttons }
        var next: [PadStick: PadAxis] = [:]
        let input = touches.input
        if input.leftX != 0 || input.leftY != 0 {
            next[.left] = PadAxis(x: input.leftX, y: input.leftY)
        }
        if input.rightX != 0 || input.rightY != 0 {
            next[.right] = PadAxis(x: input.rightX, y: input.rightY)
        }
        if next != axes { axes = next }
    }
}

/// The pad's window, above everything the app draws.
///
/// One level above `TouchControlsHost` (+101), which is itself above the
/// joystick pad (+100). A window level cannot be undone by anything inside the
/// app window, which is what makes this reliable where a subview ordering is
/// not — see `JoystickPadHost`.
///
/// Unlike the joystick pad's window this one has to take input, so it is not
/// click-through: it claims a point only when a control is actually there.
/// Everything else — the whole screen between the buttons — falls through to
/// the game, so mouse-look and the game's own gestures keep working.
final class VirtualPadWindow: UIWindow {
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        guard VirtualPadState.shared.claims(point, in: bounds) else { return nil }
        return super.hitTest(point, with: event)
    }
}

enum VirtualPadHost {
    private static var window: VirtualPadWindow?

    static func attach() {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive })
                        ?? scenes.first else { return }
        if window == nil {
            // Same reason as TouchControlsHost: the orientation notification is
            // not posted unless generation has been switched on, and without it
            // this window keeps a portrait frame after the first rotation.
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
            let w = VirtualPadWindow(windowScene: scene)
            w.windowLevel = .normal + 102
            w.backgroundColor = .clear
            w.isHidden = false              // deliberately never made key
            let host = UIHostingController(rootView: VirtualPadOverlay())
            host.view.backgroundColor = .clear
            w.rootViewController = host
            window = w
            VirtualPadState.shared.markAttached()
        }
        window?.frame = scene.coordinateSpace.bounds
    }
}

/// The pad itself: a translucent DualShock laid over the bottom of the screen.
struct VirtualPadOverlay: View {
    @ObservedObject private var pad = VirtualPadState.shared
    @ObservedObject private var chrome = GameChromeState.shared
    @ObservedObject private var store = SettingsStore.shared

    /// `automatic` means "while a game is on screen", which is what
    /// `GameChromeState.immersive` reports — the overlay is in another window
    /// and cannot see the layout itself.
    private var shouldShow: Bool {
        store.settings.virtualPad.shows(gameOnScreen: chrome.immersive)
    }

    var body: some View {
        GeometryReader { geo in
            let controls = VirtualPadLayout.controls(for: geo.size, topInset: chrome.topInset)
            ZStack(alignment: .topLeading) {
                // Behind the buttons: the cross plates and the soft deck. Drawn
                // here rather than by each control because a cross is one shape
                // spanning four hit regions, and four separate plates read as a
                // flower rather than a d-pad.
                decoration(controls: controls, in: geo.size)
                // Indexed rather than enumerated: a Swift 6 closure cannot
                // destructure the `(offset:element:)` tuple.
                ForEach(controls.indices, id: \.self) { index in
                    let control = controls[index]
                    PadControlView(control: control, index: index)
                        .position(VirtualPadLayout.centre(control, in: geo.size))
                }
                hideDisc(in: geo.size)
            }
            .opacity(pad.opacity)
            .allowsHitTesting(pad.visible)
        }
        // MUST ignore the safe area: the hit test runs in window coordinates,
        // and an inset host would draw the pad somewhere other than where it
        // claims touches.
        .ignoresSafeArea()
        .onAppear { sync() }
        .onChange(of: shouldShow) { _, _ in sync() }
        .onChange(of: store.settings.virtualPadOpacity) { _, _ in sync() }
    }

    private func sync() {
        pad.opacity = VirtualPadLayout.opacityClamped(store.settings.virtualPadOpacity)
        pad.setVisible(shouldShow && pad.attached)
    }

    /// The deck and the two d-pad crosses. No hit testing: the controls are what
    /// take the touches.
    private func decoration(controls: [PadControl], in size: CGSize) -> some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 46, style: .continuous)
                .fill(Color.black)
                .frame(height: size.height * 0.52)
                .blur(radius: 28)
                .opacity(0.55)
                .offset(y: size.height * 0.48)
            ForEach([true, false], id: \.self) { isLeft in
                if let centre = crossCentre(controls: controls, left: isLeft, in: size) {
                    PadCross()
                        .frame(width: CGFloat(VirtualPadLayout.armPoints * 2.7),
                               height: CGFloat(VirtualPadLayout.armPoints * 2.7))
                        .position(centre)
                }
            }
        }
        .allowsHitTesting(false)
    }

    /// The pad's own hide control, drawn and hit-tested separately from the
    /// gamepad buttons: pressing it turns the setting off, which is the shortest
    /// way out of a pad that is in the way.
    private func hideDisc(in size: CGSize) -> some View {
        let disc = VirtualPadLayout.hideDisc(for: size, topInset: chrome.topInset)
        return ZStack {
            Circle().fill(Color.black.opacity(0.45))
            Circle().stroke(Color.white.opacity(0.25), lineWidth: 1)
            Image(systemName: "xmark")
                .font(.system(size: CGFloat(disc.radius) * 0.8, weight: .semibold))
                .foregroundStyle(.white.opacity(0.8))
        }
        .frame(width: CGFloat(disc.radius) * 2, height: CGFloat(disc.radius) * 2)
        .position(disc.centre)
        .onTapGesture {
            store.settings.virtualPad = .off
            sync()
        }
        .accessibilityLabel("Hide the on-screen controller")
    }

    /// The centre of a d-pad cluster, from the four controls that make it up.
    private func crossCentre(controls: [PadControl], left: Bool,
                             in size: CGSize) -> CGPoint? {
        let dpad = controls.filter { c in
            guard case .button(let b) = c.hit else { return false }
            switch b {
            case .up, .down, .left, .right:
                return left ? c.nx < 0.5 : c.nx >= 0.5
            default:
                return false
            }
        }
        guard !dpad.isEmpty else { return nil }
        let points = dpad.map { VirtualPadLayout.centre($0, in: size) }
        let x = points.reduce(0) { $0 + $1.x } / CGFloat(points.count)
        let y = points.reduce(0) { $0 + $1.y } / CGFloat(points.count)
        return CGPoint(x: x, y: y)
    }
}

/// A PlayStation d-pad: two rounded bars crossing.
struct PadCross: View {
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            ZStack {
                RoundedRectangle(cornerRadius: w * 0.16, style: .continuous)
                    .frame(width: w, height: h * 0.34)
                RoundedRectangle(cornerRadius: w * 0.16, style: .continuous)
                    .frame(width: w * 0.34, height: h)
            }
            .foregroundStyle(Color.white.opacity(0.13))
            .overlay(
                ZStack {
                    RoundedRectangle(cornerRadius: w * 0.16, style: .continuous)
                        .stroke(Color.white.opacity(0.18), lineWidth: 1)
                        .frame(width: w, height: h * 0.34)
                    RoundedRectangle(cornerRadius: w * 0.16, style: .continuous)
                        .stroke(Color.white.opacity(0.18), lineWidth: 1)
                        .frame(width: w * 0.34, height: h)
                }
            )
        }
    }
}

/// One control: a face button, a d-pad arrow, a shoulder, or a stick.
///
/// Every control owns its own `DragGesture`, which is what makes multi-touch
/// work at all: two thumbs on two buttons are two gesture recognisers on two
/// views, and neither one can swallow the other's touch.
struct PadControlView: View {
    let control: PadControl
    let index: Int

    @ObservedObject private var pad = VirtualPadState.shared
    @State private var down = false

    private var radius: CGFloat { CGFloat(VirtualPadLayout.radius(control)) }

    var body: some View {
        content
            .frame(width: radius * 2, height: radius * 2)
            .contentShape(Circle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { g in
                        if !down {
                            down = true
                            pad.begin(control.hit, touch: index)
                        }
                        if case .stick(let s) = control.hit {
                            pad.move(s, to: axis(local: g.location))
                        }
                    }
                    .onEnded { _ in
                        down = false
                        pad.end(touch: index)
                    }
            )
    }

    /// The touch's deflection, measured from the centre of this control's own
    /// frame — the same origin the stick is drawn around, so the knob follows
    /// the thumb exactly.
    private func axis(local: CGPoint) -> PadAxis {
        VirtualPadLayout.stickVector(
            centre: CGPoint(x: radius, y: radius),
            travel: VirtualPadLayout.travel(control),
            at: local)
    }

    @ViewBuilder
    private var content: some View {
        switch control.hit {
        case .stick(let stick):
            ZStack {
                Circle().fill(Color.white.opacity(0.11))
                Circle().stroke(Color.white.opacity(0.22), lineWidth: 1)
                // Knob offset by the live axis. `y` is negated to get back to
                // screen coordinates, which grow downward.
                let a = pad.axes[stick] ?? PadAxis()
                let travel = CGFloat(VirtualPadLayout.travel(control))
                Circle()
                    .fill(Color.white.opacity(down ? 0.75 : 0.55))
                    .frame(width: radius * 0.86, height: radius * 0.86)
                    .offset(x: CGFloat(a.x) * travel, y: -CGFloat(a.y) * travel)
            }

        case .button(let button) where Self.isDPad(button):
            // Just the arrow: the cross plate behind it is drawn once per
            // cluster, in the decoration layer.
            Image(systemName: Self.arrow(for: button))
                .font(.system(size: radius * 0.72, weight: .semibold))
                .foregroundStyle(pad.held.contains(button) ? Color.white : Color.white.opacity(0.75))

        case .button(let button):
            ZStack {
                Circle().fill(Color.black.opacity(0.35))
                Circle().stroke(Color.white.opacity(0.20), lineWidth: 1)
                glyph(for: button, size: radius)
            }
            .overlay(
                Circle().fill(Color.white.opacity(pad.held.contains(button) ? 0.22 : 0))
            )
        }
    }

    /// PlayStation glyphs and their colours, so the four face buttons are
    /// recognisable without reading anything.
    @ViewBuilder
    private func glyph(for button: GamepadButton, size: CGFloat) -> some View {
        switch button {
        case .y:
            glyphText("△", color: Color(red: 0.36, green: 0.78, blue: 0.55), size: size)
        case .b:
            glyphText("○", color: Color(red: 0.91, green: 0.35, blue: 0.42), size: size)
        case .a:
            glyphText("✕", color: Color(red: 0.42, green: 0.62, blue: 0.95), size: size)
        case .x:
            glyphText("□", color: Color(red: 0.92, green: 0.56, blue: 0.80), size: size)
        case .lb:
            glyphText("L1", color: .white.opacity(0.85), size: size * 0.5)
        case .rb:
            glyphText("R1", color: .white.opacity(0.85), size: size * 0.5)
        case .lt:
            glyphText("L2", color: .white.opacity(0.85), size: size * 0.5)
        case .rt:
            glyphText("R2", color: .white.opacity(0.85), size: size * 0.5)
        case .menu:
            Image(systemName: "line.3.horizontal")
                .foregroundStyle(.white.opacity(0.8))
        case .view:
            Image(systemName: "square.on.square")
                .foregroundStyle(.white.opacity(0.8))
        default:
            EmptyView()
        }
    }

    private func glyphText(_ text: String, color: Color, size: CGFloat) -> some View {
        Text(text)
            .font(.system(size: size, weight: .semibold, design: .rounded))
            .foregroundStyle(color)
    }

    private static func isDPad(_ b: GamepadButton) -> Bool {
        switch b {
        case .up, .down, .left, .right: return true
        default: return false
        }
    }

    private static func arrow(for b: GamepadButton) -> String {
        switch b {
        case .up:    return "arrowtriangle.up.fill"
        case .down:  return "arrowtriangle.down.fill"
        case .left:  return "arrowtriangle.left.fill"
        case .right: return "arrowtriangle.right.fill"
        default:     return "circle"
        }
    }
}
