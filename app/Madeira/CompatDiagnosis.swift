import Foundation

// Why a session ended the way it did, in the categories a user can act on. The
// patterns are the strings Wine, its DLL loader and the game itself really
// print, so a failure is classified from the log rather than guessed. The
// result is stored on the library entry and drives the automatic fallback: a
// failure advances the title to its next alternative.
enum CompatDiagnosis: String, CaseIterable, Codable {
    case dependency
    case dll
    case registry
    case dx9
    case dx11
    case dx12
    case media
    case audio
    case input
    case wineFex = "wine_fex"
    case drm
    case anticheat
    case savePath = "save_path"
    case launch
    case other

    var title: String {
        switch self {
        case .dependency: return "Missing Windows component"
        case .dll: return "Plugin or DLL failed to load"
        case .registry: return "Registry"
        case .dx9: return "Direct3D 9"
        case .dx11: return "Direct3D 11"
        case .dx12: return "Direct3D 12"
        case .media: return "Video playback"
        case .audio: return "Audio"
        case .input: return "Controller or mouse input"
        case .wineFex: return "Wine or the CPU emulator"
        case .drm: return "Copy protection"
        case .anticheat: return "Anti-cheat"
        case .savePath: return "Writing saves or configuration"
        case .launch: return "The game did not start"
        case .other: return "Something else"
        }
    }

    var explanation: String {
        switch self {
        case .dependency: return "The game asks for a Windows component that is not in the prefix yet."
        case .dll: return "Wine could not load a DLL the game needs, or the wrong DLL was used."
        case .registry: return "A registry value the game reads is missing or wrong."
        case .dx9: return "The Direct3D 9 path failed."
        case .dx11: return "The Direct3D 11 path failed."
        case .dx12: return "The Direct3D 12 path failed."
        case .media: return "The game could not play a video or use a media component."
        case .audio: return "The audio path failed."
        case .input: return "A controller or input device could not be opened."
        case .wineFex: return "Wine or the x86 emulator hit an unimplemented call or a crash."
        case .drm: return "This copy protection cannot run on Madeira."
        case .anticheat: return "This anti-cheat cannot run on Madeira."
        case .savePath: return "The game could not write its saves or configuration."
        case .launch: return "The process started but the game never reached its first frame."
        case .other: return "No known pattern matched; the diagnostic log has the details."
        }
    }

    /// Nothing Madeira can configure away.
    var isFatal: Bool { self == .drm || self == .anticheat }
}

/// What a finished session did, kept on the library entry so a title is not
/// re-diagnosed from scratch every time. `attempt` records which alternative
/// was running, so the next launch can move on when it failed.
struct CompatResult: Codable, Equatable {
    var outcome: String
    var categories: [String]
    var attempt: Int
    var alternative: String?
    var detail: String?
    var updated: String?

    var outcomeTitle: String {
        switch outcome {
        case "works": return "Works"
        case "issues": return "Works with problems"
        case "fails": return "Did not work"
        default: return "Not tried yet"
        }
    }

    var isFailure: Bool { outcome == "fails" }

    var categoriesTitle: String {
        categories.compactMap { CompatDiagnosis(rawValue: $0)?.title }.joined(separator: ", ")
    }

    /// A failure more configuration cannot fix (anti-cheat, DRM).
    var isFatal: Bool {
        isFailure && categories.contains { CompatDiagnosis(rawValue: $0)?.isFatal == true }
    }
}

/// Classifying a session from its log and exit status.
enum CompatLogDiagnosis {
    /// Distinctive strings. Kept as data so a new Wine message is one line, not
    /// a new branch.
    private static let rules: [(CompatDiagnosis, String)] = [
        // The DLL loader names the file it could not find or load.
        (.dependency, "err:module:import_dll"),
        (.dependency, "which is needed by"),
        (.dependency, "err:module:load_builtin_dll"),
        (.dependency, "library not found"),
        (.dependency, "the program can't start because"),
        (.dependency, "is missing from your computer"),
        (.dependency, "err:module:find_forwarded_export"),
        (.dependency, "\\bmscoree\\b"),
        (.dependency, "\\bdotnet\\b"),
        (.dependency, "\\.net framework"),
        (.dll, "failed to load dll"),
        (.dll, "dll not found"),
        (.dll, "invalid dll"),
        (.dll, "bad image"),
        (.dll, "err:module:delay_load"),
        (.registry, "regopenkey"),
        (.registry, "err:reg"),
        (.registry, "err:ole:coinitialize"),
        (.dx11, "\\bd3d11\\b"),
        (.dx11, "dxgi"),
        (.dx11, "create_dxgifactory"),
        (.dx12, "\\bd3d12\\b"),
        (.dx12, "d3d12createdevice"),
        (.dx9, "\\bd3d9\\b"),
        (.dx9, "direct3d9"),
        (.dx9, "d3dx9"),
        (.dx9, "err:d3d"),
        (.dx9, "ddraw"),
        (.dx9, "wined3d"),
        (.media, "mfplat"),
        (.media, "media foundation"),
        (.media, "mfreadwrite"),
        (.media, "err:quartz"),
        (.media, "\\bwmv"),
        (.media, "msmpeg2"),
        (.media, "devenum"),
        (.media, "err:mf"),
        (.audio, "xaudio2"),
        (.audio, "xact"),
        (.audio, "\\bdsound\\b"),
        (.audio, "mmdevapi"),
        (.audio, "audioclient"),
        (.audio, "err:alsa"),
        (.input, "err:dinput"),
        (.input, "err:xinput"),
        (.input, "rawinput"),
        (.input, "err:wintab32"),
        (.wineFex, "unimplemented function"),
        (.wineFex, "err:virtual"),
        (.wineFex, "err:seh"),
        (.wineFex, "unhandled exception"),
        (.wineFex, "0x80000003"),
        (.wineFex, "\\bfex\\b"),
        (.drm, "denuvo"),
        (.drm, "securom"),
        (.drm, "safedisc"),
        (.drm, "starforce"),
        (.drm, "steam drm"),
        (.drm, "steamstub"),
        (.drm, "themida"),
        (.drm, "vmprotect"),
        (.anticheat, "easy anti-cheat"),
        (.anticheat, "easyanticheat"),
        (.anticheat, "\\beac\\b"),
        (.anticheat, "battleye"),
        (.anticheat, "\\bbe service\\b"),
        (.anticheat, "vanguard"),
        (.anticheat, "\\bvkguard\\b"),
        (.anticheat, "equ8"),
        (.savePath, "permission denied"),
        (.savePath, "access is denied"),
        (.savePath, "read-only file system"),
        (.savePath, "err:file"),
        (.savePath, "err:dosmem"),
    ]

    private static let compiled: [(CompatDiagnosis, NSRegularExpression)] = rules.compactMap { category, pattern in
        (try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive])).map { (category, $0) }
    }

    /// Madeira logs its own diagnostics as "[tag] ..." into the same file as
    /// Wine, and those lines mention xinput, D3D11 and the JIT constantly, so
    /// they are dropped before anything is matched. Wine prints "err:"/"fixme:"
    /// and games print plain text, none of which is bracketed.
    static func scrub(_ text: String) -> String {
        guard text.contains("\n[") || text.hasPrefix("[") else { return text }
        return text.split(separator: "\n", omittingEmptySubsequences: true)
            .filter { !$0.hasPrefix("[") }
            .joined(separator: "\n")
    }

    /// Every category the log shows, anti-cheat and DRM first.
    static func categories(in text: String, limit: Int = 4) -> [CompatDiagnosis] {
        guard !text.isEmpty else { return [] }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        var found: [CompatDiagnosis] = []
        for (category, pattern) in compiled where !found.contains(category) {
            if pattern.firstMatch(in: text, options: [], range: range) != nil { found.append(category) }
        }
        // Anti-cheat and DRM are the reason a title cannot work, so they lead.
        let order = CompatDiagnosis.allCases
        found.sort { lhs, rhs in
            rank(lhs, in: order) < rank(rhs, in: order)
        }
        return Array(found.prefix(limit))
    }

    private static func rank(_ category: CompatDiagnosis, in order: [CompatDiagnosis]) -> Int {
        if category == .anticheat || category == .drm { return 0 }
        return 1 + (order.firstIndex(of: category) ?? order.count)
    }

    /// Turn a finished session into a result. `exitStatus` is the Windows error
    /// the process died with, nil when it exited cleanly; `presented` is
    /// whether a frame ever reached the screen. A session that never presented
    /// one did not start the game, whatever the log says.
    static func classify(log: String, exitStatus: UInt32?, presented: Bool,
                         duration: TimeInterval, attempt: Int, alternative: String?,
                         now: Date = Date()) -> CompatResult {
        let text = scrub(log)
        let found = categories(in: text)
        let errorLine = text.contains("err:") || text.contains("unhandled exception")
        let failed = exitStatus != nil || !presented

        let outcome: String
        var categories: [CompatDiagnosis]
        if failed {
            outcome = "fails"
            categories = found.isEmpty ? [.launch] : found
        } else if errorLine, !found.isEmpty {
            outcome = "issues"
            categories = found
        } else {
            outcome = "works"
            categories = []
        }

        var detail: String?
        if let exitStatus {
            detail = "Windows error 0x" + String(exitStatus, radix: 16, uppercase: true)
        } else if !presented {
            detail = duration < 20 ? "No frame was presented."
                                   : "No frame was presented in a \(Int(duration))s session."
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withFullDate]
        return CompatResult(outcome: outcome, categories: categories.map { $0.rawValue },
                            attempt: attempt, alternative: alternative, detail: detail,
                            updated: formatter.string(from: now))
    }

    /// One line for the launch log.
    static func report(_ result: CompatResult) -> String {
        var line = "[compat-result] outcome=\(result.outcome) attempt=\(result.attempt + 1)"
        if !result.categories.isEmpty { line += " category=" + result.categories.joined(separator: ",") }
        if let detail = result.detail { line += " detail=\(detail)" }
        return line
    }
}
