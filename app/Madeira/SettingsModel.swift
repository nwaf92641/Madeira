import Foundation

/// One selectable desktop resolution.
///
/// `width == 0` is the sentinel for "leave the engine's own default alone".
/// Every desktop the app hardcodes (1024x768 for the Steam shell, 960x540 for
/// services) predates every non-A15 device and is load-bearing for window
/// fitting, so the shipped default must remain reachable from a settings screen
/// that otherwise only offers explicit sizes.
struct ResolutionPreset: Identifiable, Equatable {
    let width: Int
    let height: Int

    static let automatic = ResolutionPreset(width: 0, height: 0)

    var id: String { "\(width)x\(height)" }

    var label: String {
        width == 0 ? "Automatic" : "\(width) × \(height)"
    }

    /// The short edge ratio, for a subtitle. Only used for display.
    var aspect: String? {
        guard width > 0, height > 0 else { return nil }
        let pairs: [(Double, String)] = [
            (16.0 / 9.0, "16:9"), (16.0 / 10.0, "16:10"), (4.0 / 3.0, "4:3"),
        ]
        let ratio = Double(width) / Double(height)
        return pairs.first(where: { abs($0.0 - ratio) < 0.02 })?.1
    }
}

/// Bounds on what a user may ask for. Both the presets and the custom fields
/// go through `clamped`, so a stray value cannot request a frame buffer the
/// guest will reject or a pool that would move the VA floor.
enum ResolutionPolicy {
    static let minDimension = 320
    static let maxDimension = 4096

    static let presets: [ResolutionPreset] = [
        .automatic,
        ResolutionPreset(width: 960, height: 540),
        ResolutionPreset(width: 1024, height: 768),
        ResolutionPreset(width: 1280, height: 720),
        ResolutionPreset(width: 1280, height: 800),
        ResolutionPreset(width: 1600, height: 900),
        ResolutionPreset(width: 1920, height: 1080),
    ]

    /// Both dimensions in range, or nil. A zero pair (`automatic`) is nil too:
    /// the result means "an explicit size to write", and the absence of one is
    /// what `overrideFiles` turns into a removed file.
    static func clamped(width: Int, height: Int) -> (width: Int, height: Int)? {
        guard width >= minDimension, width <= maxDimension,
              height >= minDimension, height <= maxDimension else { return nil }
        return (width, height)
    }
}

/// The render pacing the engine is asked to hold.
///
/// These values are the ones `madeira_set_vsync_locked()` already takes; the
/// FPS overlay cycles through the same three.
enum FrameRateMode: Int, CaseIterable, Identifiable, Codable, Hashable {
    /// Free-run to the display refresh. 120 on a ProMotion panel.
    case maximum = 0
    /// Paced to exactly 60Hz.
    case sixty = 1
    /// Unthrottled frame-skip mailbox: the readout is raw throughput.
    case unthrottled = 2

    var id: Int { rawValue }

    var label: String {
        switch self {
        case .maximum: return "Maximum"
        case .sixty: return "60 fps"
        case .unthrottled: return "Unlimited"
        }
    }

    var detail: String {
        switch self {
        case .maximum: return "Follows the display; 120Hz on ProMotion"
        case .sixty: return "Locked to 60Hz for a steady frame time"
        case .unthrottled: return "No pacing — highest throughput, most heat"
        }
    }
}

/// A switch the engine already exposes through a `madeira-*.txt` file.
///
/// The settings screen writes the same files the launch sequence reads, so the
/// engine keeps one source of truth and every switch stays A/B-able from the
/// Files app as well. `on` is the body the file carries when enabled; a switch
/// that is off has its file removed, which is how the engine's own default is
/// restored.
struct EngineSwitch: Identifiable, Equatable {
    enum Group: String, CaseIterable, Identifiable {
        case graphics = "Graphics"
        case performance = "Performance"
        case compatibility = "Compatibility"
        case diagnostics = "Diagnostics"
        var id: String { rawValue }
    }

    let id: String          // the file stem, e.g. "madeira-usd-time"
    let title: String
    let detail: String
    let group: Group
    let on: String
    /// True when the engine's own comments say the switch is not proven, so the
    /// UI can say so instead of implying it is a supported setting.
    let experimental: Bool

    var fileName: String { id + ".txt" }
}

enum EngineSwitches {
    /// The switches that are safe and useful to offer. Deliberately not every
    /// `madeira-*.txt` the engine reads: the probes (wxprobe, shadow,
    /// apicensus) exist to answer one question each and a user toggling them
    /// only burns a run.
    static let all: [EngineSwitch] = [
        EngineSwitch(
            id: "madeira-usd-time",
            title: "Advance the guest clock",
            detail: "Keeps Windows time moving. Managed games pace every transition "
                + "off this clock, and with it frozen they wait forever while the "
                + "renderer keeps drawing.",
            group: .compatibility, on: "1", experimental: false),
        EngineSwitch(
            id: "madeira-wx",
            title: "W^X page demotion",
            detail: "Drops write permission from pages that have finished being "
                + "patched. Set to 0 to A/B a title that faults while patching.",
            group: .compatibility, on: "1", experimental: false),
        EngineSwitch(
            id: "madeira-ctx-frame",
            title: "Syscall-frame thread context",
            detail: "Reports a thread parked in a syscall using its saved Wine frame "
                + "instead of the registers it happens to be running.",
            group: .compatibility, on: "1", experimental: false),
        EngineSwitch(
            id: "madeira-mono-bridge",
            title: "Mono code-patching bridge",
            detail: "Arms FEX's XCHG patch optimisation for wine-mono.",
            group: .compatibility, on: "1", experimental: true),
        EngineSwitch(
            id: "madeira-real-suspend",
            title: "Real thread suspension",
            detail: "A Wine suspend actually stops the Mach thread. Can deadlock: the "
                + "suspended thread shares this process's allocator.",
            group: .performance, on: "1", experimental: true),
        EngineSwitch(
            id: "madeira-fex-arena",
            title: "Reserve the FEX host arena",
            detail: "Makes Wine reserve FEX's arena before any PE loads. Starves FEX "
                + "on hardware that picks its own band.",
            group: .performance, on: "1", experimental: true),
        EngineSwitch(
            id: "madeira-tf-trace",
            title: "Theorafile call tracer",
            detail: "Routes libtheorafile's exports through wrappers that report "
                + "return values. Costs frame time.",
            group: .diagnostics, on: "1", experimental: false),
        EngineSwitch(
            id: "madeira-census",
            title: "Render command census",
            detail: "Counts which wmtcmd_* types a workload emits and how large "
                + "their sidecar data gets.",
            group: .diagnostics, on: "1", experimental: false),
    ]
}

/// Everything the settings screen can change.
///
/// Pure data: no UserDefaults, no UIKit. `overrideFiles` is the whole contract
/// with the engine — the launch sequence reads these same files, which is why
/// nothing here has to reach into C.
struct MadeiraSettings: Equatable, Codable {
    var width: Int = 0
    var height: Int = 0
    /// nil means "leave the engine's own pacing alone".
    ///
    /// Deliberately optional rather than defaulting to one of the three modes:
    /// the engine picks a pacing itself at startup, and a settings screen that
    /// pushed a value on every launch would silently change the frame behaviour
    /// of a user who never opened it.
    var frameRate: FrameRateMode? = nil
    /// 0 means "derive from the device's jetsam budget".
    var poolMB: Int = 0
    /// Clamp the expanded mip chain for block-compressed textures.
    var clampCompressedMips: Bool = false
    /// The on-screen controller. Defaults to on, because it exists to replace
    /// the system keyboard and a user who has to find the switch first has not
    /// been helped.
    var virtualPad: VirtualPadMode = .automatic
    var virtualPadOpacity: Double = VirtualPadLayout.defaultOpacity
    var remoteHost: String = ""
    var remoteToken: String = ""
    var switches: Set<String> = []

    static let empty = MadeiraSettings()

    /// The pool sizes a user may pick. 0 is automatic; the rest are the values
    /// the engine's comments bracket (256 is the allocation floor, 3072 the
    /// documented clamp).
    static let poolChoices: [Int] = [0, 256, 384, 512, 768, 896, 1024, 1536, 2048, 3072]

    static func poolClamped(_ mb: Int) -> Int {
        guard mb > 0 else { return 0 }
        return min(max(mb, 256), 3072)
    }

    /// The resolution to hand the engine, or nil for "its own default".
    var resolution: (width: Int, height: Int)? {
        ResolutionPolicy.clamped(width: width, height: height)
    }

    /// Remote Metal is only meaningful with both halves of the credential.
    var remoteMetalConfigured: Bool {
        !remoteHost.trimmingCharacters(in: .whitespaces).isEmpty
            && !remoteToken.trimmingCharacters(in: .whitespaces).isEmpty
    }

    /// The `madeira-*.txt` files this settings value implies. A nil body means
    /// "delete the file", which restores the engine's default for that knob.
    ///
    /// Returned as an ordered array rather than a dictionary because the order
    /// is what makes the behaviour readable in a log line.
    var overrideFiles: [(name: String, body: String?)] {
        var files: [(name: String, body: String?)] = []

        if let r = resolution {
            files.append(("madeira-resolution.txt", "\(r.width)x\(r.height)"))
        } else {
            files.append(("madeira-resolution.txt", nil))
        }

        if poolMB > 0 {
            files.append(("madeira-pool.txt", String(MadeiraSettings.poolClamped(poolMB))))
        } else {
            files.append(("madeira-pool.txt", nil))
        }

        // DXMT reads DXMT_CONFIG as inline "key=value" lines. mipClampBC is the one
        // option worth surfacing: this GPU cannot sample block-compressed textures,
        // so they are expanded at 2-8x their shipped size, and clamping the mip
        // chain is what bounds that cost. The file only exists while it is on.
        var dxmt: [String] = []
        if clampCompressedMips {
            dxmt.append("d3d11.mipClampBC=1")
        }
        files.append(("madeira-dxmt.txt", dxmt.isEmpty ? nil : dxmt.joined(separator: "\n")))

        if remoteMetalConfigured {
            let host = remoteHost.trimmingCharacters(in: .whitespaces)
            let token = remoteToken.trimmingCharacters(in: .whitespaces)
            files.append(("madeira-remote.txt", "\(host) \(token)"))
        } else {
            files.append(("madeira-remote.txt", nil))
        }

        for sw in EngineSwitches.all {
            files.append((sw.fileName, switches.contains(sw.id) ? sw.on : nil))
        }

        return files
    }
}
