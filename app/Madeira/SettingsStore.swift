import Foundation
import Combine

/// The settings screen's live model.
///
/// `UserDefaults` is the durable copy; the `madeira-*.txt` files in Documents
/// are the *contract* with the engine, rewritten on every change. Both exist on
/// purpose: the files are what the launch sequence already reads (and what the
/// Files app can still edit by hand), and defaults are what survives a
/// reinstall of the app bundle.
final class SettingsStore: ObservableObject {
    static let shared = SettingsStore()

    private static let defaultsKey = "madeira.settings.v1"
    private static let ownedKey = "madeira.settings.ownedFiles"

    @Published var settings: MadeiraSettings = .empty {
        didSet {
            guard settings != oldValue else { return }
            persist()
            syncOverrideFiles()
        }
    }

    private init() {
        guard let data = UserDefaults.standard.data(forKey: Self.defaultsKey),
              let decoded = try? JSONDecoder().decode(MadeiraSettings.self, from: data) else { return }
        settings = decoded
    }

    private func persist() {
        guard let data = try? JSONEncoder().encode(settings) else { return }
        UserDefaults.standard.set(data, forKey: Self.defaultsKey)
    }

    /// Restore every knob to the engine's own default: the files are removed,
    /// not written with a guessed value. The sync is explicit because setting
    /// the value back to `.empty` from `.empty` is not a change.
    func reset() {
        settings = .empty
        syncOverrideFiles()
    }

    /// Write (or delete) the override files this configuration implies.
    ///
    /// Deleting rather than writing a default matters: the engine's defaults
    /// include device-derived values, so a written number would pin the device
    /// to whatever this screen happened to know about.
    ///
    /// Removal is limited to files this store wrote. The Files-app channel is
    /// still supported, and a hand-written `madeira-wx.txt` must not disappear
    /// because someone opened Settings and toggled an unrelated switch.
    ///
    /// Returns the file names that changed, without their contents: the remote
    /// token would otherwise be echoed into the on-screen log.
    @discardableResult
    func syncOverrideFiles() -> [String] {
        guard let dir = FileManager.default.urls(for: .documentDirectory,
                                                 in: .userDomainMask).first else { return [] }
        var owned = Set(UserDefaults.standard.stringArray(forKey: Self.ownedKey) ?? [])
        var touched: [String] = []
        for file in settings.overrideFiles {
            let url = dir.appendingPathComponent(file.name)
            if let body = file.body {
                try? body.write(to: url, atomically: true, encoding: .utf8)
                owned.insert(file.name)
                touched.append(file.name)
            } else if owned.contains(file.name) {
                try? FileManager.default.removeItem(at: url)
                owned.remove(file.name)
                touched.append("\(file.name) (default)")
            }
        }
        UserDefaults.standard.set(Array(owned), forKey: Self.ownedKey)
        return touched
    }

    /// Apply what is not expressible as a file. The frame-rate request lives in
    /// the engine's own state, so it is pushed directly — and only when the user
    /// actually chose one.
    func applyNonFileSettings() {
        guard let mode = settings.frameRate else { return }
        madeira_set_vsync_locked(Int32(mode.rawValue))
        ProMotionIntent.shared.setActive(mode != .sixty)
    }
}

/// What the home screen shows about the current run.
///
/// The launch sequence used to report a failed JIT pool by calling `exit(0)`
/// after a ten-second countdown, which reads to a user as "the app closed
/// itself". It now records the failure here instead, so the app stays up and
/// the same button that started the run can start it again.
final class RunStatus: ObservableObject {
    static let shared = RunStatus()

    enum Phase: Equatable {
        case idle
        case preparing
        case running
        case failed(String)

        var isBusy: Bool { self == .preparing || self == .running }
    }

    @Published var phase: Phase = .idle
    /// The pool that was actually allocated, in MB. Zero until a run starts.
    @Published var poolMB: Int = 0

    private init() {}

    func begin(preparing: Bool) {
        phase = preparing ? .preparing : .running
    }

    func succeed(poolMB: Int) {
        self.poolMB = poolMB
        phase = .running
    }

    func fail(_ message: String) {
        phase = .failed(message)
    }

    func reset() {
        phase = .idle
        poolMB = 0
    }
}
