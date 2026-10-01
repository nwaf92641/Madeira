// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 the Madeira contributors
//
// Game compatibility engine.
//
// Madeira runs Windows games on a Wine/FEX/DXMT stack. Getting a specific
// title to run has, until now, meant rediscovering the same Wine fixes the
// rest of the ecosystem already knows: which Visual C++ runtime it wants,
// which DLL has to be overridden, which Windows version it expects, which
// environment switch makes its videos play. This file carries that knowledge
// and turns it into a per-launch plan.
//
// The data lives in compat.json (generated from compat/ by
// build/tools/gen-game-compat.py). A title is matched by Steam App ID,
// executable name, GOG slug or install-path fragment; its dependencies are
// resolved against the dependency catalogue; and the result is a plan of
// environment variables, DLL overrides, registry values, Windows version and
// launch arguments that apply to that launch only.
//
// The engine is deliberately Foundation-only and side-effect free apart from
// reading the database and writing registry files: it is compiled and tested
// on the host by build/host-tests/check-game-compat.py. The app applies the
// plan from LibraryEntry.applyEnvironment().

import Foundation

// MARK: - Database models (compat.json, schema 1)

/// One registry value, in the shape Wine's .reg files use.
struct CompatRegistryValue: Codable, Equatable {
    /// "HKCU" or "HKLM".
    var hive: String?
    /// Key path below the hive, e.g. "Software\\Wine\\AppDefaults".
    var key: String?
    /// Value name; nil or "" writes the key's default value.
    var name: String?
    /// REG_SZ, REG_DWORD, REG_MULTI_SZ or REG_BINARY.
    var type: String?
    /// The value as text; a dword is decimal, a multi-string is separated by "\0".
    var value: String?

    var hiveName: String { (hive ?? "HKCU").uppercased() }
    var keyName: String { key ?? "" }
    var typeName: String { (type ?? "REG_SZ").uppercased() }
    var valueText: String { value ?? "" }
}

/// A Windows component and how Madeira satisfies it.
struct CompatDependency: Codable, Equatable {
    var title: String?
    var kind: String?
    /// builtin | override | payload | manual | unsupported | partial.
    var support: String?
    var summary: String?
    /// Imported DLL names (without ".dll", lowercased) that select this
    /// dependency from a game executable's import table.
    var imports: [String]?
    var dlls: [String]?
    /// DLL name (lowercase) -> Wine override order ("n", "b", "n,b", "b,n", "").
    var dllOverrides: [String: String]?
    var registry: [CompatRegistryValue]?
    var env: [String: String]?
    var windowsVersion: String?
    var requires: [String]?
    var notes: String?
    var source: String?
    /// Filled from the catalogue key, not from JSON.
    var id: String = ""

    enum CodingKeys: String, CodingKey {
        case title, kind, support, summary, imports, dlls, registry, env, requires, notes, source
        case dllOverrides = "dll_overrides"
        case windowsVersion = "windows_version"
    }

    var supportName: String { support ?? "unsupported" }
    /// A component that needs files or an installer cannot be satisfied by
    /// overrides alone.
    var needsPayload: Bool { supportName == "payload" }
}

/// One game profile.
struct CompatGame: Codable, Equatable {
    var title: String?
    var appid: Int?
    var gogSlug: String?
    var executables: [String]?
    var pathContains: [String]?
    var engine: String?
    var rating: String?
    var dependencies: [String]?
    var dllOverrides: [String: String]?
    var registry: [CompatRegistryValue]?
    var env: [String: String]?
    var launchArguments: String?
    var windowsVersion: String?
    var fixes: [String]?
    var issues: [String]?
    var notes: String?
    var conditional: Bool?
    var source: String?

    enum CodingKeys: String, CodingKey {
        case title, appid, executables, engine, rating, dependencies, registry, env
        case fixes, issues, notes, conditional, source
        case gogSlug = "gog_slug"
        case pathContains = "path_contains"
        case dllOverrides = "dll_overrides"
        case launchArguments = "launch_arguments"
        case windowsVersion = "windows_version"
    }

    var titleText: String { title ?? "" }
    var ratingText: String { rating ?? "unknown" }
    var launchArgumentTokens: [String] {
        guard let args = launchArguments, !args.isEmpty else { return [] }
        return CompatGame.splitArguments(args)
    }

    /// Whitespace-separated tokens that keep double-quoted spans together.
    static func splitArguments(_ text: String) -> [String] {
        var tokens: [String] = []
        var current = ""
        var quoted = false
        for character in text {
            if character == "\"" { quoted.toggle(); continue }
            if !quoted, character == " " || character == "\t" {
                if !current.isEmpty { tokens.append(current); current = "" }
            } else {
                current.append(character)
            }
        }
        if !current.isEmpty { tokens.append(current) }
        return tokens
    }
}

/// The whole database.
struct CompatDatabase: Codable {
    var schema: Int?
    var updated: String?
    var dependencies: [String: CompatDependency]?
    var games: [CompatGame]?

    var dependencyTable: [String: CompatDependency] {
        var table = dependencies ?? [:]
        for (key, var value) in table { value.id = key; table[key] = value }
        return table
    }
    var gameList: [CompatGame] { games ?? [] }
}

// MARK: - Per-launch input and user overrides

/// What the user can force for one library entry, stored in LibraryEntry so it
/// survives across sessions. Every field is optional: an entry with no
/// compatibility override is exactly the automatic behaviour.
struct CompatOverrides: Codable, Equatable {
    /// nil = automatic (a matched profile applies); false = launch untouched.
    var enabled: Bool?
    var extraDependencies: [String]?
    var disabledDependencies: [String]?
    var dllOverrides: [String: String]?
    var env: [String: String]?
    var launchArguments: String?
    var windowsVersion: String?

    enum CodingKeys: String, CodingKey {
        case enabled, env
        case extraDependencies = "extra_dependencies"
        case disabledDependencies = "disabled_dependencies"
        case dllOverrides = "dll_overrides"
        case launchArguments = "launch_arguments"
        case windowsVersion = "windows_version"
    }
}

/// Everything the engine needs to know about a launch.
struct CompatLaunch {
    /// Executable base name, e.g. "THUMPER_win10.exe".
    var executable: String
    /// Path relative to drive_c, e.g. "Games/Thumper/THUMPER_win10.exe".
    var relativePath: String?
    /// Steam App ID when the entry is a Steam game.
    var appid: Int?
    /// GOG slug when the install is known by one.
    var gogSlug: String?
    /// The current value of an environment variable, or nil.
    var environment: [String: String]
    /// Directories searched for native payload files (DLLs, fonts).
    var payloadDirectories: [String]
    /// The DLLs the game's executable imports (PEImports), which select
    /// dependencies even when no profile matches.
    var importedDLLs: [String]
    /// The user's overrides for this entry, if any.
    var overrides: CompatOverrides?

    init(executable: String, relativePath: String? = nil, appid: Int? = nil, gogSlug: String? = nil,
         environment: [String: String] = [:], payloadDirectories: [String] = [],
         importedDLLs: [String] = [], overrides: CompatOverrides? = nil) {
        self.executable = executable
        self.relativePath = relativePath
        self.appid = appid
        self.gogSlug = gogSlug
        self.environment = environment
        self.payloadDirectories = payloadDirectories
        self.importedDLLs = importedDLLs
        self.overrides = overrides
    }
}

/// A resolved dependency the launch cannot satisfy on this stack.
struct CompatUnsatisfied: Equatable {
    var id: String
    var title: String
    var support: String
    var reason: String
}

/// The plan the launch applies.
struct CompatPlan {
    var matched: [CompatGame] = []
    var dependencies: [CompatDependency] = []
    /// dependency id -> the components that were merged or skipped.
    var environment: [String: String] = [:]
    /// Variables to remove (from an env entry with an empty value).
    var unsetEnvironment: [String] = []
    var dllOverrides: [String: String] = [:]
    var launchArguments: [String] = []
    var windowsVersion: String?
    var registry: [CompatRegistryValue] = []
    var unsatisfied: [CompatUnsatisfied] = []
    var notes: [String] = []

    var isEmpty: Bool {
        matched.isEmpty && environment.isEmpty && unsetEnvironment.isEmpty && dllOverrides.isEmpty
            && launchArguments.isEmpty && windowsVersion == nil && registry.isEmpty
    }
    var titles: [String] { matched.compactMap { $0.title } }
    var dependencyTitles: [String] { dependencies.compactMap { $0.title } }

    /// "dll1=n,b;dll2=b" for WINEDLLOVERRIDES.
    var dllOverrideString: String {
        dllOverrides.filter { !$0.key.isEmpty }
            .sorted { $0.key < $1.key }
            .map { "\($0.key)=\($0.value)" }
            .joined(separator: ";")
    }
}

// MARK: - The engine

enum GameCompatibility {
    /// Decode one database document. Unknown keys are ignored; a missing id is
    /// filled from the catalogue key.
    static func decode(_ data: Data) -> CompatDatabase? {
        (try? JSONDecoder().decode(CompatDatabase.self, from: data))
    }

    /// Merge an overlay (an updated database) over a base. Games are keyed by
    /// App ID, GOG slug or first executable; dependencies by id. Fields present
    /// in the overlay win; absent fields keep the base's value.
    static func merge(base: CompatDatabase, overlay: CompatDatabase) -> CompatDatabase {
        var dependencies = base.dependencies ?? [:]
        for (key, value) in overlay.dependencies ?? [:] { dependencies[key] = value }
        var games: [CompatGame] = []
        var index: [String: Int] = [:]
        for game in base.games ?? [] { index[gameKey(game)] = games.count; games.append(game) }
        for game in overlay.games ?? [] {
            let key = gameKey(game)
            if let position = index[key] { games[position] = game } else { index[key] = games.count; games.append(game) }
        }
        var merged = CompatDatabase()
        merged.schema = overlay.schema ?? base.schema
        merged.updated = overlay.updated ?? base.updated
        merged.dependencies = dependencies
        merged.games = games
        return merged
    }

    static func gameKey(_ game: CompatGame) -> String {
        if let appid = game.appid { return "appid:\(appid)" }
        if let slug = game.gogSlug { return "gog:\(slug)" }
        return "exe:\((game.executables?.first ?? "").lowercased())"
    }

    /// Load the database: the bundled seed, then Documents/madeira-compat as an
    /// overlay so a data update needs no rebuild. `bundled` is the parsed
    /// bundle resource; `overlayURLs` are files that may or may not exist.
    static func load(bundled: Data?, overlays: [URL] = []) -> CompatDatabase {
        var database = bundled.flatMap(decode) ?? emptyDatabase()
        for url in overlays {
            guard let data = try? Data(contentsOf: url), let overlay = decode(data) else { continue }
            database = merge(base: database, overlay: overlay)
        }
        return database
    }

    static func emptyDatabase() -> CompatDatabase {
        var database = CompatDatabase(); database.games = []; database.dependencies = [:]; return database
    }

    // MARK: Matching

    /// Specificity of each match kind, lower is better.
    enum MatchKind: Int { case appid = 0, executable = 1, gog = 2, path = 3 }

    static func matches(_ game: CompatGame, _ launch: CompatLaunch) -> MatchKind? {
        if let appid = launch.appid, let gameAppID = game.appid, appid == gameAppID { return .appid }
        let exe = launch.executable.lowercased()
        if !exe.isEmpty, (game.executables ?? []).contains(where: { $0.lowercased() == exe }) { return .executable }
        if let slug = launch.gogSlug?.lowercased(), let gameSlug = game.gogSlug?.lowercased(), slug == gameSlug { return .gog }
        if let path = launch.relativePath?.lowercased() {
            let fragments = (game.pathContains ?? []).map { $0.lowercased() }
            if !fragments.isEmpty, fragments.allSatisfy({ path.contains($0) }) { return .path }
        }
        return nil
    }

    /// Every matching profile, most specific first.
    static func matchingGames(_ database: CompatDatabase, _ launch: CompatLaunch) -> [CompatGame] {
        database.gameList.enumerated().compactMap { offset, game -> (Int, Int, CompatGame)? in
            guard let kind = matches(game, launch) else { return nil }
            return (kind.rawValue, offset, game)
        }
        .sorted { $0.0 != $1.0 ? $0.0 < $1.0 : $0.1 < $1.1 }
        .map { $0.2 }
    }

    // MARK: Plan

    /// Resolve a launch into a plan. Never mutates anything.
    static func plan(_ launch: CompatLaunch, database: CompatDatabase) -> CompatPlan {
        var plan = CompatPlan()
        let overrides = launch.overrides
        guard overrides?.enabled != false else { return plan }   // explicitly disabled

        plan.matched = matchingGames(database, launch)
        let catalogue = database.dependencyTable

        // Dependencies: matched profiles (most specific last, so a less
        // specific profile cannot silently drop a more specific one) plus the
        // user's extras, minus the user's exclusions.
        var requested: [String] = []
        func request(_ id: String) { if !requested.contains(id) { requested.append(id) } }
        for game in plan.matched { (game.dependencies ?? []).forEach(request) }
        // Automatic detection: the DLLs the executable imports select
        // dependencies even when no profile matches, so a title nobody has
        // written a profile for still gets its runtimes and overrides.
        let imported = Set(launch.importedDLLs.map { importName($0) })
        if !imported.isEmpty {
            for key in catalogue.keys.sorted() {
                guard let names = catalogue[key]?.imports, !names.isEmpty else { continue }
                if names.contains(where: { imported.contains(importName($0)) }) { request(key) }
            }
        }
        (overrides?.extraDependencies ?? []).forEach(request)
        let disabled = Set(overrides?.disabledDependencies ?? [])
        requested.removeAll { disabled.contains($0) }

        // Expand `requires` depth-first, keeping dependencies before the things
        // that need them.
        var ordered: [String] = []
        var visiting: Set<String> = []
        func resolve(_ id: String) {
            guard !ordered.contains(id), !visiting.contains(id) else { return }
            visiting.insert(id)
            if let dependency = catalogue[id] { (dependency.requires ?? []).forEach(resolve) }
            visiting.remove(id)
            if !ordered.contains(id) { ordered.append(id) }
        }
        requested.forEach(resolve)
        plan.dependencies = ordered.compactMap { catalogue[$0] }
        for id in ordered where catalogue[id] == nil {
            plan.unsatisfied.append(CompatUnsatisfied(id: id, title: id, support: "unknown",
                reason: "Madeira has no entry for this component"))
        }

        // Merge each dependency: overrides, registry, environment, version.
        for dependency in plan.dependencies {
            switch dependency.supportName {
            case "unsupported":
                plan.unsatisfied.append(CompatUnsatisfied(id: dependency.id,
                    title: dependency.title ?? dependency.id, support: "unsupported",
                    reason: "not supported on iOS"))
            case "manual":
                plan.unsatisfied.append(CompatUnsatisfied(id: dependency.id,
                    title: dependency.title ?? dependency.id, support: "manual",
                    reason: "needs a separate installer"))
            case "payload":
                let missing = missingPayload(dependency, directories: launch.payloadDirectories)
                if missing.isEmpty {
                    merge(dependency, into: &plan)
                } else {
                    plan.unsatisfied.append(CompatUnsatisfied(id: dependency.id,
                        title: dependency.title ?? dependency.id, support: "payload",
                        reason: "needs " + missing.joined(separator: ", ")))
                }
            default:   // builtin, override, partial
                merge(dependency, into: &plan)
                if dependency.supportName == "partial" {
                    plan.notes.append("\(dependency.title ?? dependency.id): partially supported on Madeira")
                }
            }
        }

        // Matched-profile fields, least specific first so the most specific
        // profile wins a scalar field and caps the merge.
        for game in plan.matched.reversed() {
            for (key, value) in game.env ?? [:] { plan.environment[key] = value }
            for (dll, order) in game.dllOverrides ?? [:] { plan.dllOverrides[dll.lowercased()] = order }
            plan.registry.append(contentsOf: game.registry ?? [])
            if let version = game.windowsVersion { plan.windowsVersion = version }
            plan.launchArguments.append(contentsOf: game.launchArgumentTokens)
            for fix in game.fixes ?? [] { plan.notes.append("\(game.titleText): \(fix)") }
            for issue in game.issues ?? [] { plan.notes.append("\(game.titleText): \(issue)") }
        }

        // The user's overrides win over everything.
        if let overrides {
            for (key, value) in overrides.env ?? [:] { plan.environment[key] = value }
            for (dll, order) in overrides.dllOverrides ?? [:] { plan.dllOverrides[dll.lowercased()] = order }
            if let version = overrides.windowsVersion { plan.windowsVersion = version }
            plan.launchArguments.append(contentsOf: CompatGame.splitArguments(overrides.launchArguments ?? ""))
        }

        // Fold the environment into the launch: empty means unset.
        let environment = plan.environment
        plan.environment = [:]
        for (key, value) in environment {
            if value.isEmpty { plan.unsetEnvironment.append(key) } else { plan.environment[key] = value }
        }

        // WINEDLLOVERRIDES: merge with whatever the launch already carries, so
        // a madeira.cfg env. entry is not clobbered; our entries win.
        if !plan.dllOverrides.isEmpty {
            var merged = launch.environment["WINEDLLOVERRIDES"] ?? ""
            for (dll, order) in plan.dllOverrides.sorted(by: { $0.key < $1.key }) {
                merged = mergeOverrideOrder(merged, dll: dll, order: order)
            }
            plan.environment["WINEDLLOVERRIDES"] = merged
        }

        // Per-application registry: version and DLL overrides keyed by the
        // executable, so two games in the one shared prefix cannot collide.
        let app = appDefaultsKey(launch.executable)
        if let version = plan.windowsVersion {
            plan.registry.append(CompatRegistryValue(hive: "HKCU", key: "Software\\Wine\\AppDefaults\\\(app)",
                                                     name: "Version", type: "REG_SZ", value: version))
        }
        if !plan.dllOverrides.isEmpty {
            for (dll, order) in plan.dllOverrides.sorted(by: { $0.key < $1.key }) where !order.isEmpty {
                plan.registry.append(CompatRegistryValue(
                    hive: "HKCU", key: "Software\\Wine\\AppDefaults\\\(app)\\DllOverrides",
                    name: dll, type: "REG_SZ", value: registryOrder(order)))
            }
        }
        return plan
    }

    /// An import or catalogue name normalised for matching: lowercased and
    /// without the ".dll" extension.
    static func importName(_ name: String) -> String {
        let lowered = name.lowercased()
        return lowered.hasSuffix(".dll") ? String(lowered.dropLast(4)) : lowered
    }

    /// Wine's AppDefaults key is the image name as Windows sees it.
    static func appDefaultsKey(_ executable: String) -> String {
        let base = (executable as NSString).lastPathComponent
        return base.isEmpty ? "madeira.exe" : base
    }

    /// "n,b" -> "native,builtin".
    static func registryOrder(_ order: String) -> String {
        order.split(separator: ",").map { token -> String in
            switch token.trimmingCharacters(in: .whitespaces).lowercased() {
            case "n": return "native"
            case "b": return "builtin"
            default: return token.trimmingCharacters(in: .whitespaces)
            }
        }.joined(separator: ",")
    }

    /// Replace one DLL's entry in a WINEDLLOVERRIDES string.
    static func mergeOverrideOrder(_ existing: String, dll: String, order: String) -> String {
        var entries = existing.split(separator: ";").map(String.init).filter { !$0.isEmpty }
        entries.removeAll { $0.split(separator: "=", maxSplits: 1).first.map { $0.lowercased() == dll.lowercased() } ?? false }
        entries.append("\(dll)=\(order)")
        return entries.joined(separator: ";")
    }

    private static func merge(_ dependency: CompatDependency, into plan: inout CompatPlan) {
        for (key, value) in dependency.env ?? [:] { plan.environment[key] = value }
        for (dll, order) in dependency.dllOverrides ?? [:] { plan.dllOverrides[dll.lowercased()] = order }
        plan.registry.append(contentsOf: dependency.registry ?? [])
        if let version = dependency.windowsVersion, plan.windowsVersion == nil { plan.windowsVersion = version }
    }

    /// Which of a dependency's required files are absent from every payload
    /// directory. Matching is case-insensitive.
    static func missingPayload(_ dependency: CompatDependency, directories: [String]) -> [String] {
        let required = dependency.dlls ?? []
        guard !required.isEmpty, !directories.isEmpty else { return required }
        var found = Set<String>()
        let fm = FileManager.default
        for directory in directories {
            guard let names = try? fm.contentsOfDirectory(atPath: directory) else { continue }
            for name in names { found.insert(name.lowercased()) }
        }
        return required.filter { !found.contains($0.lowercased()) }
    }

    /// Human-readable lines describing what a plan will do, for the launch log
    /// and the game details screen.
    static func summary(_ plan: CompatPlan) -> [String] {
        guard !plan.isEmpty else { return [] }
        var lines: [String] = []
        if !plan.titles.isEmpty { lines.append("profile: " + plan.titles.joined(separator: ", ")) }
        if !plan.dependencies.isEmpty { lines.append("dependencies: " + plan.dependencyTitles.joined(separator: ", ")) }
        if !plan.dllOverrides.isEmpty { lines.append("dll overrides: " + plan.dllOverrideString) }
        if let version = plan.windowsVersion { lines.append("windows version: \(version)") }
        if !plan.environment.isEmpty { lines.append("environment: " + plan.environment.keys.sorted().joined(separator: ", ")) }
        if !plan.unsetEnvironment.isEmpty { lines.append("unset: " + plan.unsetEnvironment.sorted().joined(separator: ", ")) }
        if !plan.launchArguments.isEmpty { lines.append("arguments: " + plan.launchArguments.joined(separator: " ")) }
        for item in plan.unsatisfied {
            lines.append("unsatisfied: \(item.title) — \(item.reason)")
        }
        return lines
    }

    // MARK: Applying

    /// Apply the environment half of a plan. A value of "" removes the
    /// variable. `set`/`unset` are injected so the engine stays testable.
    static func applyEnvironment(_ plan: CompatPlan, set: (String, String) -> Void, unset: (String) -> Void) {
        for name in plan.unsetEnvironment { unset(name) }
        for (name, value) in plan.environment { set(name, value) }
    }

    /// Write a plan's registry values into the prefix's user.reg, returning how
    /// many values changed. Only call while no session is running: wineserver
    /// holds the registry in memory and writes it back when it stops. The file
    /// is left untouched when the prefix has not been seeded yet.
    @discardableResult
    static func writeRegistry(_ plan: CompatPlan, prefix: URL, now: Int = Int(Date().timeIntervalSince1970)) -> (written: Int, error: String?) {
        let values = plan.registry
        guard !values.isEmpty else { return (0, nil) }
        let fm = FileManager.default
        var total = 0
        for (hive, file) in [("HKCU", "user.reg"), ("HKLM", "system.reg")] {
            let selected = values.filter { $0.hiveName == hive }
            guard !selected.isEmpty else { continue }
            let registry = prefix.appendingPathComponent(file)
            guard let contents = try? Data(contentsOf: registry), !contents.isEmpty else { continue }
            let (updated, changed) = CompatRegistryText.merge(selected, into: String(decoding: contents, as: UTF8.self), now: now)
            guard changed > 0 else { continue }
            let backup = registry.appendingPathExtension("madeira-bak")
            if !fm.fileExists(atPath: backup.path) { try? fm.copyItem(at: registry, to: backup) }
            do { try Data(updated.utf8).write(to: registry, options: .atomic) }
            catch { return (total, "\(file): \(error.localizedDescription)") }
            total += changed
        }
        return (total, nil)
    }
}

/// Remembers the environment variables a plan set, so the next launch in the
/// same app run removes the ones it no longer wants. Madeira normally runs one
/// session per app run, but the desktop entry and a game can both launch, and
/// MADEIRA_ONE_SESSION_PER_RUN=0 exists, so a compat variable must not outlive
/// the game that needed it.
final class CompatEnvironmentRegistry: @unchecked Sendable {
    static let shared = CompatEnvironmentRegistry()
    private let lock = NSLock()
    private var names: Set<String> = []

    /// Variables currently set by a plan.
    var current: Set<String> {
        lock.lock(); defer { lock.unlock() }
        return names
    }

    /// Forget and unset any variable the previous plan set that the next plan
    /// does not, then record the new set. Call before applying the plan.
    func reconcile(keeping next: Set<String>, unset: (String) -> Void) {
        lock.lock()
        let stale = names.subtracting(next)
        names = next
        lock.unlock()
        for name in stale { unset(name) }
    }
}

// MARK: - Registry text

/// Merging registry values into a Wine .reg file's text. Wine's format is
/// section headers "[Key] 1699999999", then one value per line; strings are
/// quoted, dwords are "dword:NNNNNNNN", multi-strings and binary are hex.
enum CompatRegistryText {
    static func escape(_ text: String) -> String {
        text.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
    }

    /// The line that sets one value.
    static func line(_ value: CompatRegistryValue) -> String {
        let name = (value.name?.isEmpty ?? true) ? "@" : "\"" + escape(value.name!) + "\""
        switch value.typeName {
        case "REG_DWORD":
            return "\(name)=dword:\(String(format: "%08x", dword(value.valueText)))"
        case "REG_MULTI_SZ":
            return "\(name)=hex(7):\(hex(value.valueText.components(separatedBy: "\0")))"
        case "REG_BINARY":
            let bytes = value.valueText.split(separator: " ").compactMap { UInt8($0, radix: 16) }
            return "\(name)=hex:\(bytes.map { String(format: "%02x", $0) }.joined(separator: ","))"
        default:
            return "\(name)=\"\(escape(value.valueText))\""
        }
    }

    /// A dword written as decimal, or as 0x-prefixed or bare hex.
    static func dword(_ text: String) -> UInt32 {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.lowercased().hasPrefix("0x") { return UInt32(trimmed.dropFirst(2), radix: 16) ?? 0 }
        return UInt32(trimmed) ?? UInt32(trimmed, radix: 16) ?? 0
    }

    private static func hex(_ strings: [String]) -> String {
        // Each string is UTF-16LE and null-terminated; the list ends with an
        // extra null (the terminating empty string).
        var bytes: [UInt8] = []
        for string in strings {
            for unit in string.utf16 { bytes.append(UInt8(unit & 0xff)); bytes.append(UInt8(unit >> 8)) }
            bytes.append(0); bytes.append(0)
        }
        bytes.append(0); bytes.append(0)
        return bytes.map { String(format: "%02x", $0) }.joined(separator: ",")
    }

    /// Merge values into the .reg text; returns the new text and how many
    /// values were added or changed.
    static func merge(_ values: [CompatRegistryValue], into text: String, now: Int) -> (text: String, changed: Int) {
        var lines = text.components(separatedBy: "\n")
        var changed = 0
        for value in values {
            let header = "[" + escape(value.keyName) + "]"
            let namePrefix = (value.name?.isEmpty ?? true) ? "@=" : "\"" + escape(value.name!) + "\"="
            let newLine = line(value)
            if let start = lines.firstIndex(where: { $0.lowercased().hasPrefix(header.lowercased()) }) {
                var end = start + 1
                while end < lines.count, !lines[end].hasPrefix("[") { end += 1 }
                if let index = (start + 1..<end).first(where: { lines[$0].lowercased().hasPrefix(namePrefix.lowercased()) }) {
                    if lines[index] == newLine { continue }
                    lines[index] = newLine
                } else {
                    var at = end
                    while at > start + 1, lines[at - 1].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { at -= 1 }
                    lines.insert(newLine, at: at)
                }
            } else {
                while let last = lines.last, last.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { lines.removeLast() }
                lines += ["", header + " \(now)", newLine, ""]
            }
            changed += 1
        }
        return (lines.joined(separator: "\n"), changed)
    }
}
