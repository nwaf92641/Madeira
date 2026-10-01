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
// build/tools/gen-game-compat.py). A launch starts from the universal
// configuration (compat/baseline.json), then the general rules that follow from
// what the executable is (compat/rules.json: the DLLs it imports, the files
// beside it, its name, its architecture), then the profile that matches it by
// Steam App ID, executable name, GOG slug or install-path fragment; its
// dependencies are resolved against the dependency catalogue; and the result is
// a plan of environment variables, DLL overrides, registry values, Windows
// version and launch arguments that apply to that launch only. When a session
// fails, the diagnosis picks the remedy for what went wrong (compat/rules.json,
// `remedies`) and the next launch is that variation, so a program nobody has
// profiled still converges.
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

/// One file operation a profile performs before the game starts.
///
/// A title sometimes has to be given a different file than it ships: an old
/// `ddraw.dll` beside the executable that must not be loaded (Command &
/// Conquer, Red Alert 2), a `.SkuDef` that makes the patcher pick the wrong
/// branch (Command & Conquer 3), a config the game rewrites into a broken one
/// (Doom). Upstream fixes rename or delete those files; this is the same
/// remedy as data, applied to the install folder or the prefix before launch.
struct CompatFileAction: Codable, Equatable {
    /// rename | delete | mkdir.
    var action: String?
    /// Path below the base, with either separator.
    var path: String?
    /// Destination for `rename`, below the same base.
    var to: String?
    /// "game" (the launched executable's folder, the default) or "prefix"
    /// (the prefix's drive_c).
    var location: String?

    enum CodingKeys: String, CodingKey {
        case action, path, to
        case location = "in"
    }

    var actionName: String { (action ?? "").lowercased() }
    var locationName: String { (location ?? "game").lowercased() }
    /// Description for the log and the compatibility section.
    var summaryText: String {
        switch actionName {
        case "rename": return "rename \(path ?? "?") to \(to ?? "?")"
        case "delete": return "delete \(path ?? "?")"
        case "mkdir": return "create \(path ?? "?")"
        default: return "\(actionName) \(path ?? "?")"
        }
    }
}

/// A reusable fix: a named bundle of environment, overrides, registry,
/// arguments and dependencies that many games or components share. A recipe is
/// applied wherever it is referenced, so the same fix is written once; the
/// fixes in the catalogue come from Protonfixes, Winetricks and Bottles, where
/// the same handful of remedies recur across hundreds of titles.
struct CompatRecipe: Codable, Equatable {
    var title: String?
    /// dependency | dll | registry | dx11 | dx12 | dx9 | media | audio |
    /// wine_fex | drm | anticheat | save_path | input | launch | other.
    var category: String?
    var summary: String?
    var dependencies: [String]?
    /// Other recipes this one needs first.
    var requires: [String]?
    var dllOverrides: [String: String]?
    var registry: [CompatRegistryValue]?
    var env: [String: String]?
    var launchArguments: String?
    var windowsVersion: String?
    /// Arguments to drop from the command line (a launcher's own `--steam`).
    var removeArguments: [String]?
    /// Files to move aside or remove before the game starts.
    var files: [CompatFileAction]?
    var notes: String?
    var source: String?
    var id: String = ""

    enum CodingKeys: String, CodingKey {
        case title, category, summary, dependencies, requires, registry, env, notes, source, files
        case dllOverrides = "dll_overrides"
        case launchArguments = "launch_arguments"
        case removeArguments = "remove_arguments"
        case windowsVersion = "windows_version"
    }
}

/// One fallback: a delta applied on top of a profile when an earlier attempt
/// failed. Madeira tries alternatives in order across launches, so a game with
/// more than one workable configuration is retried automatically.
struct CompatFallback: Codable, Equatable {
    var name: String?
    var note: String?
    var dependencies: [String]?
    var recipes: [String]?
    var dllOverrides: [String: String]?
    var env: [String: String]?
    var launchArguments: String?
    var windowsVersion: String?
    /// Registry values this attempt writes.
    var registry: [CompatRegistryValue]?
    /// Recipes or dependency ids this attempt must not apply.
    var disable: [String]?
    /// Arguments to drop for this attempt.
    var removeArguments: [String]?
    /// Files to move aside or remove for this attempt.
    var files: [CompatFileAction]?

    enum CodingKeys: String, CodingKey {
        case name, note, dependencies, recipes, env, disable, files, registry
        case dllOverrides = "dll_overrides"
        case launchArguments = "launch_arguments"
        case removeArguments = "remove_arguments"
        case windowsVersion = "windows_version"
    }
}

/// What a general rule matches on. Every condition present has to hold; within
/// one list, any name is enough. All of it is read from the executable itself
/// (its imports, the files beside it, its name, its architecture), which is why
/// a rule applies to a program no profile has ever seen.
struct CompatRuleWhen: Codable, Equatable {
    /// DLL names the program imports, normalised (lowercase, no ".dll").
    var imports: [String]?
    /// DLL or program names present in the program's folder, normalised.
    var files: [String]?
    /// Substrings of the executable's file name, lowercased.
    var executableContains: [String]?
    /// Substrings of the executable's path inside the prefix.
    var pathContains: [String]?
    /// 32 or 64.
    var bits: Int?

    enum CodingKeys: String, CodingKey {
        case imports, files, bits
        case executableContains = "executable_contains"
        case pathContains = "path_contains"
    }

    var isEmpty: Bool {
        (imports ?? []).isEmpty && (files ?? []).isEmpty && (executableContains ?? []).isEmpty
            && (pathContains ?? []).isEmpty && bits == nil
    }
}

/// A fix that follows from what a program is rather than from which title it
/// is: an import it links, a file beside it, its name or its architecture. The
/// fields are the same shape as a fallback, so a rule adds components, recipes,
/// overrides, registry values, environment, a Windows version or arguments.
struct CompatRule: Codable, Equatable {
    var id: String = ""
    var title: String?
    var when: CompatRuleWhen?
    var dependencies: [String]?
    var recipes: [String]?
    var dllOverrides: [String: String]?
    var registry: [CompatRegistryValue]?
    var env: [String: String]?
    var launchArguments: String?
    var windowsVersion: String?
    var note: String?
    var source: String?

    enum CodingKeys: String, CodingKey {
        case id, title, when, dependencies, recipes, registry, env, note, source
        case dllOverrides = "dll_overrides"
        case launchArguments = "launch_arguments"
        case windowsVersion = "windows_version"
    }
}

/// A Windows component and how Madeira satisfies it.
struct CompatDependency: Codable, Equatable {
    var title: String?
    var kind: String?
    /// builtin | override | payload | manual | unsupported | partial.
    var support: String?
    /// Failure class this component belongs to (see CompatDiagnosis).
    var category: String?
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
    /// Reusable fixes applied with this component.
    var recipes: [String]?
    var notes: String?
    var source: String?
    /// Filled from the catalogue key, not from JSON.
    var id: String = ""

    enum CodingKeys: String, CodingKey {
        case title, kind, support, category, summary, imports, dlls, registry, env, requires, recipes, notes, source
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
    /// The program to start instead of the one the entry picked: a launcher
    /// that does not run under Wine (`Launcher.exe` → `Borderlands2.exe`,
    /// `launcher.exe` → `binaries/Darktide.exe`), relative to the picked
    /// program's folder or to drive_c ("C:\...").
    var run: String?
    /// Arguments to drop from the command line before the game sees them.
    var removeArguments: [String]?
    /// Files to move aside or remove before the game starts.
    var files: [CompatFileAction]?
    var fixes: [String]?
    /// Fixes a Protonfixes script applies that have no equivalent on Madeira's
    /// stack (Vulkan, esync/fsync, NVIDIA, Linux paths), kept so the report is
    /// honest about them rather than silently dropping them.
    var unavailableFixes: [String]?
    var issues: [String]?
    var notes: String?
    var conditional: Bool?
    /// Reusable fixes this title needs.
    var recipes: [String]?
    /// Ordered alternatives tried when an earlier attempt fails.
    var fallbacks: [CompatFallback]?
    var source: String?

    enum CodingKeys: String, CodingKey {
        case title, appid, executables, engine, rating, dependencies, registry, env
        case fixes, issues, notes, conditional, recipes, fallbacks, source, run, files
        case unavailableFixes = "unavailable_fixes"
        case gogSlug = "gog_slug"
        case pathContains = "path_contains"
        case dllOverrides = "dll_overrides"
        case launchArguments = "launch_arguments"
        case removeArguments = "remove_arguments"
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

/// A Windows component the runtime does not build, with the reason
/// (compat/wine-modules.json, `not_built`). A program that imports one cannot
/// be served: there is no builtin, no native copy in the prefix and nothing a
/// user could install.
struct CompatMissingModule: Codable, Equatable {
    var name: String?
    var reason: String?
}

/// The whole database.
struct CompatDatabase: Codable {
    var schema: Int?
    var updated: String?
    var dependencies: [String: CompatDependency]?
    var recipes: [String: CompatRecipe]?
    var games: [CompatGame]?
    /// The universal configuration, applied to every launch before any profile.
    var baseline: CompatGame?
    /// General rules: a fix that follows from what a program is.
    var rules: [CompatRule]?
    /// Diagnosis category -> the variation tried on the next launch.
    var remedies: [String: CompatFallback]?
    /// Modules the runtime provides (compat/wine-modules.json), so an import
    /// Wine resolves is not reported as missing. This is the 32-bit set: the
    /// i386 Wine build minus what the iOS build skips.
    var wineModules: [String]?
    /// Modules the app ships for a 64-bit guest (the ARM64EC and native ARM64
    /// farms). Smaller than the 32-bit list — no quartz, wmvcore or
    /// winegstreamer — so the two have to be told apart.
    var wineModules64: [String]?
    /// Modules upstream Wine builds that this runtime does not ship.
    var wineNotShipped: [String]?
    /// Windows components Wine has never built (mfcore, the DirectX SDK
    /// compilers), with the reason. An import of one cannot be served.
    var wineNotBuilt: [CompatMissingModule]?
    /// Prefixes of the API set contract names (`api-ms-win-*`, `ext-ms-win-*`).
    var apiSetPrefixes: [String]?

    enum CodingKeys: String, CodingKey {
        case schema, updated, dependencies, recipes, games, baseline, rules, remedies
        case wineModules = "wine_modules"
        case wineModules64 = "wine_modules_64"
        case wineNotShipped = "wine_not_shipped"
        case wineNotBuilt = "not_built"
        case apiSetPrefixes = "api_set_prefixes"
    }

    var dependencyTable: [String: CompatDependency] {
        var table = dependencies ?? [:]
        for (key, var value) in table { value.id = key; table[key] = value }
        return table
    }
    var recipeTable: [String: CompatRecipe] {
        var table = recipes ?? [:]
        for (key, var value) in table { value.id = key; table[key] = value }
        return table
    }
    var gameList: [CompatGame] { games ?? [] }
    var ruleList: [CompatRule] { rules ?? [] }
    var remedyTable: [String: CompatFallback] { remedies ?? [:] }
    /// Module names as this engine compares them (lowercase, no ".dll").
    ///
    /// A launch asks for the set its process can load: the 32-bit Wine build,
    /// or the farms the app ships for a 64-bit guest. Anything in the other
    /// set is missing for this launch even though the runtime has the file
    /// somewhere — a 64-bit program cannot load a 32-bit module.
    func providedModules(_ bits: Int?) -> Set<String> {
        let names = bits == 64 ? (wineModules64 ?? wineModules ?? []) : (wineModules ?? [])
        return Set(names.map { GameCompatibility.importName($0) })
    }

    /// Names this launch cannot load: what upstream Wine builds but the iOS
    /// build skips, what Wine never built, and — for a 64-bit launch — every
    /// module the 64-bit farms do not carry.
    func absentModules(_ bits: Int?) -> Set<String> {
        var absent = Set((wineNotShipped ?? []).map { GameCompatibility.importName($0) })
        for entry in wineNotBuilt ?? [] { absent.insert(GameCompatibility.importName(entry.name ?? "")) }
        if bits == 64 {
            let all = Set(((wineModules ?? []) + (wineNotShipped ?? [])).map { GameCompatibility.importName($0) })
            absent.formUnion(all.subtracting(providedModules(64)))
        }
        return absent
    }

    /// Why the runtime cannot serve a name, when it is a component Wine never
    /// built. The catalogue and the details screen quote this.
    func notBuiltReason(_ name: String) -> String? {
        let wanted = GameCompatibility.importName(name)
        for entry in wineNotBuilt ?? [] where GameCompatibility.importName(entry.name ?? "") == wanted {
            return entry.reason
        }
        return nil
    }

    /// Wine resolves an API set name (`api-ms-win-*`, `ext-ms-win-*`) to the
    /// module that implements the contract — ucrtbase for the C runtime sets,
    /// kernelbase for the core ones — so there is no file to look for and
    /// nothing a user could install. A modern program imports a dozen of them;
    /// reporting those would bury the names that mean something.
    func isAPISet(_ name: String) -> Bool {
        let lower = name.lowercased()
        return (apiSetPrefixes ?? []).contains { lower.hasPrefix($0) }
    }

    /// Whether this launch's runtime answers an import, API sets included.
    /// A claim that is true for the 32-bit build can be false for a 64-bit
    /// launch, because the two farms are not the same set.
    func serves(_ name: String, bits: Int?) -> Bool {
        let clean = GameCompatibility.importName(name)
        return providedModules(bits).contains(clean) || isAPISet(clean)
    }

    /// Whether the database carries a module list at all. A partial or older
    /// database does not, and then nothing can be said about what the runtime
    /// serves: the engine keeps its previous behaviour rather than reading
    /// silence as "nothing", which would drop every pin and report every
    /// import as unanswered.
    var knowsItsModules: Bool { !(wineModules ?? []).isEmpty }
}

/// Which attempt a launch is on, and what it consists of. The first plan is
/// alternative 0; each fallback adds one, tried in order.
struct CompatAlternative: Equatable {
    var index: Int
    var name: String
    var note: String?
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
    /// The DLL and program names present in the executable's own folder, which
    /// select general rules a game that loads a module at runtime (anti-cheat,
    /// a store client, a crash handler) would not show in its import table.
    var files: [String]
    /// 32 or 64, from the PE header.
    var bits: Int?
    /// The categories the last attempt failed with (CompatDiagnosis), so the
    /// next attempt can be the remedy for what actually went wrong.
    var previousFailure: [String]
    /// The user's overrides for this entry, if any.
    var overrides: CompatOverrides?

    init(executable: String, relativePath: String? = nil, appid: Int? = nil, gogSlug: String? = nil,
         environment: [String: String] = [:], payloadDirectories: [String] = [],
         importedDLLs: [String] = [], files: [String] = [], bits: Int? = nil,
         previousFailure: [String] = [], overrides: CompatOverrides? = nil) {
        self.executable = executable
        self.relativePath = relativePath
        self.appid = appid
        self.gogSlug = gogSlug
        self.environment = environment
        self.payloadDirectories = payloadDirectories
        self.importedDLLs = importedDLLs
        self.files = files
        self.bits = bits
        self.previousFailure = previousFailure
        self.overrides = overrides
    }
}

/// A resolved dependency the launch cannot satisfy on this stack.
struct CompatUnsatisfied: Equatable {
    var id: String
    var title: String
    var support: String
    var reason: String
    /// The component's failure class, when it has one (see CompatDiagnosis).
    /// An unsatisfied anti-cheat or DRM component is the reason no
    /// configuration will help, so the retry ladder stops there.
    var category: String? = nil
}

/// The plan the launch applies.
struct CompatPlan {
    var matched: [CompatGame] = []
    var dependencies: [CompatDependency] = []
    /// General rules that applied to this program, by id and title. They follow
    /// from what the executable is, not from which title it is.
    var rules: [String] = []
    var ruleTitles: [String] = []
    /// The universal configuration every launch starts from (compat/baseline).
    var baselineTitle: String?
    /// Imports nothing accounts for: not a catalogue component, not a general
    /// rule, not a module the runtime provides. The program must ship them.
    var unaccountedImports: [String] = []
    /// Of those, the ones the runtime deliberately does not ship (see
    /// compat/wine-modules.json). Reported apart because the reason differs:
    /// the module is a real Wine module that the iOS build leaves out.
    var unavailableModules: [String] = []
    /// The remedy this attempt is, when it is one (a diagnosis category).
    var remedy: String?
    var environment: [String: String] = [:]
    /// Variables to remove (from an env entry with an empty value).
    var unsetEnvironment: [String] = []
    var dllOverrides: [String: String] = [:]
    var launchArguments: [String] = []
    /// Tokens the profile wants gone from the command line.
    var removeArguments: [String] = []
    /// The program to start instead of the entry's own, as a Windows path.
    var launchExecutable: String?
    /// File operations to perform before the session starts.
    var files: [CompatFileAction] = []
    var windowsVersion: String?
    var registry: [CompatRegistryValue] = []
    var unsatisfied: [CompatUnsatisfied] = []
    /// Recipes applied to this launch, by id, and their titles for display.
    var recipes: [String] = []
    var recipeTitles: [String] = []
    /// Every attempt this title offers; `alternativeIndex` is the one in use.
    var alternatives: [CompatAlternative] = []
    var alternativeIndex: Int = 0
    var notes: [String] = []

    var isEmpty: Bool {
        matched.isEmpty && environment.isEmpty && unsetEnvironment.isEmpty && dllOverrides.isEmpty
            && launchArguments.isEmpty && windowsVersion == nil && registry.isEmpty
            && launchExecutable == nil && removeArguments.isEmpty && files.isEmpty
            && baselineTitle == nil && rules.isEmpty
    }
    /// No configuration can help: an anti-cheat or DRM component is unsatisfied.
    /// The retry ladder stops rather than spending another session on it.
    var isFatal: Bool {
        unsatisfied.contains {
            $0.support == "unsupported" && ($0.category == "anticheat" || $0.category == "drm")
        }
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
    /// App ID, GOG slug or first executable; dependencies by id, rules by id,
    /// remedies by diagnosis category. Fields present in the overlay win;
    /// absent fields keep the base's value.
    static func merge(base: CompatDatabase, overlay: CompatDatabase) -> CompatDatabase {
        var dependencies = base.dependencies ?? [:]
        for (key, value) in overlay.dependencies ?? [:] { dependencies[key] = value }
        var recipes = base.recipes ?? [:]
        for (key, value) in overlay.recipes ?? [:] { recipes[key] = value }
        var games: [CompatGame] = []
        var index: [String: Int] = [:]
        for game in base.games ?? [] { index[gameKey(game)] = games.count; games.append(game) }
        for game in overlay.games ?? [] {
            let key = gameKey(game)
            if let position = index[key] { games[position] = game } else { index[key] = games.count; games.append(game) }
        }
        var rules: [CompatRule] = []
        var ruleIndex: [String: Int] = [:]
        for rule in base.rules ?? [] { ruleIndex[rule.id] = rules.count; rules.append(rule) }
        for rule in overlay.rules ?? [] {
            if let position = ruleIndex[rule.id] { rules[position] = rule } else { ruleIndex[rule.id] = rules.count; rules.append(rule) }
        }
        var remedies = base.remedies ?? [:]
        for (category, remedy) in overlay.remedies ?? [:] { remedies[category] = remedy }
        var merged = CompatDatabase()
        merged.schema = overlay.schema ?? base.schema
        merged.updated = overlay.updated ?? base.updated
        merged.dependencies = dependencies
        merged.recipes = recipes
        merged.games = games
        merged.baseline = overlay.baseline ?? base.baseline
        merged.rules = rules
        merged.remedies = remedies
        merged.wineModules = overlay.wineModules ?? base.wineModules
        merged.wineNotShipped = overlay.wineNotShipped ?? base.wineNotShipped
        merged.apiSetPrefixes = overlay.apiSetPrefixes ?? base.apiSetPrefixes
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

    /// The API set prefixes a database uses when it carries none of its own.
    /// They are a property of Windows and Wine, not of a build of Madeira, so
    /// the fallback is the same list compat/wine-modules.json records.
    static let defaultAPISetPrefixes = ["api-ms-win-", "ext-ms-win-"]

    static func emptyDatabase() -> CompatDatabase {
        var database = CompatDatabase()
        database.games = []; database.dependencies = [:]; database.recipes = [:]
        database.rules = []; database.remedies = [:]
        database.wineModules = []; database.wineNotShipped = []
        database.apiSetPrefixes = defaultAPISetPrefixes
        return database
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

    /// Resolve a launch into a plan. `alternative` picks a fallback: 0 is the
    /// profile as written, 1..n are its fallbacks in order. Never mutates.
    static func plan(_ launch: CompatLaunch, database: CompatDatabase, alternative: Int = 0) -> CompatPlan {
        var plan = CompatPlan()
        let overrides = launch.overrides
        guard overrides?.enabled != false else { return plan }   // explicitly disabled

        plan.matched = matchingGames(database, launch)
        let catalogue = database.dependencyTable
        let recipes = database.recipeTable
        let baseline = database.baseline
        let applicable = rules(database, launch)

        // The selected fallback, if any. Its `disable` list removes components
        // and recipes before anything merges.
        var fallback: CompatFallback?
        if alternative > 0 {
            for game in plan.matched where (game.fallbacks?.count ?? 0) >= alternative {
                fallback = game.fallbacks?[alternative - 1]; break
            }
        }

        // Attempts after a profile's own fallbacks are the remedy for what the
        // last session failed with. A program with no profile has only these,
        // which is what lets an unknown executable retry with the fix for what
        // actually went wrong rather than repeat itself.
        let profileFallbacks = plan.matched.compactMap { $0.fallbacks?.count }.max() ?? 0
        let remedies = remedyAttempts(database, launch)
        var remedy: CompatFallback?
        var remedyCategory: String?
        let remedyIndex = alternative - profileFallbacks - 1
        if alternative > profileFallbacks, remedies.indices.contains(remedyIndex) {
            remedy = remedies[remedyIndex].remedy
            remedyCategory = remedies[remedyIndex].category
        }

        let disabled = Set((overrides?.disabledDependencies ?? [])
                           + (fallback?.disable ?? []) + (remedy?.disable ?? []))

        // What is wanted: matched profiles (most specific last, so a less
        // specific profile cannot silently drop a more specific one), the
        // selected fallback, the user's extras, the executable's imports, and
        // everything those pull in through `requires` and recipes. One closure
        // resolves the whole graph; a component's recipe can add components and
        // vice versa.
        var requested: [String] = []
        var requestedRecipes: [String] = []
        var seenDependencies = Set<String>()
        var seenRecipes = Set<String>()
        var pendingDependencies: [String] = []
        var pendingRecipes: [String] = []
        func addDependency(_ id: String) {
            guard !disabled.contains(id), seenDependencies.insert(id).inserted else { return }
            requested.append(id); pendingDependencies.append(id)
        }
        func addRecipe(_ id: String) {
            guard !disabled.contains(id), recipes[id] != nil, seenRecipes.insert(id).inserted else { return }
            requestedRecipes.append(id); pendingRecipes.append(id)
        }
        for game in plan.matched { (game.dependencies ?? []).forEach(addDependency); (game.recipes ?? []).forEach(addRecipe) }
        (baseline?.dependencies ?? []).forEach(addDependency)
        (baseline?.recipes ?? []).forEach(addRecipe)
        for rule in applicable {
            (rule.dependencies ?? []).forEach(addDependency)
            (rule.recipes ?? []).forEach(addRecipe)
        }
        (remedy?.dependencies ?? []).forEach(addDependency)
        (remedy?.recipes ?? []).forEach(addRecipe)
        (fallback?.dependencies ?? []).forEach(addDependency)
        (fallback?.recipes ?? []).forEach(addRecipe)
        (overrides?.extraDependencies ?? []).forEach(addDependency)
        let imported = Set(launch.importedDLLs.map { importName($0) })
        if !imported.isEmpty {
            for key in catalogue.keys.sorted() {
                guard let names = catalogue[key]?.imports, !names.isEmpty else { continue }
                if names.contains(where: { imported.contains(importName($0)) }) { addDependency(key) }
            }
        }
        while !pendingDependencies.isEmpty || !pendingRecipes.isEmpty {
            while !pendingDependencies.isEmpty {
                let id = pendingDependencies.removeFirst()
                guard let dependency = catalogue[id] else { continue }
                (dependency.requires ?? []).forEach(addDependency)
                (dependency.recipes ?? []).forEach(addRecipe)
            }
            while !pendingRecipes.isEmpty {
                let id = pendingRecipes.removeFirst()
                guard let recipe = recipes[id] else { continue }
                (recipe.requires ?? []).forEach(addRecipe)
                (recipe.dependencies ?? []).forEach(addDependency)
            }
        }

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
                    reason: "not supported on iOS", category: dependency.category))
            case "manual":
                plan.unsatisfied.append(CompatUnsatisfied(id: dependency.id,
                    title: dependency.title ?? dependency.id, support: "manual",
                    reason: "needs a separate installer", category: dependency.category))
            case "payload":
                let missing = missingPayload(dependency, directories: launch.payloadDirectories,
                                             beside: launch.files)
                if missing.isEmpty {
                    merge(dependency, into: &plan)
                } else {
                    plan.unsatisfied.append(CompatUnsatisfied(id: dependency.id,
                        title: dependency.title ?? dependency.id, support: "payload",
                        reason: "needs " + missing.joined(separator: ", "), category: dependency.category))
                }
            default:   // builtin, override, partial
                merge(dependency, into: &plan)
                if dependency.supportName == "partial" {
                    // The component's own note explains the specific caveat;
                    // the generic sentence is the fallback.
                    plan.notes.append(dependency.notes.map { "\(dependency.title ?? dependency.id): \($0)" }
                                      ?? "\(dependency.title ?? dependency.id): partially supported on Madeira")
                }
            }
        }

        // Reusable fixes, after the components they belong to.
        for id in requestedRecipes {
            guard let recipe = recipes[id] else { continue }
            merge(recipe, into: &plan)
            plan.recipes.append(id)
            plan.recipeTitles.append(recipe.title ?? id)
        }

        // The universal configuration and then the rules that matched: both
        // sit below a profile, so a title with one keeps its own answers.
        if let baseline {
            mergeBaseline(baseline, into: &plan)
        }
        for rule in applicable {
            merge(rule, into: &plan)
            plan.rules.append(rule.id)
            plan.ruleTitles.append(rule.title ?? rule.id)
        }

        // Matched-profile fields, least specific first so the most specific
        // profile wins a scalar field and caps the merge.
        for game in plan.matched.reversed() {
            for (key, value) in game.env ?? [:] { plan.environment[key] = value }
            for (dll, order) in game.dllOverrides ?? [:] { plan.dllOverrides[dll.lowercased()] = order }
            plan.registry.append(contentsOf: game.registry ?? [])
            if let version = game.windowsVersion { plan.windowsVersion = version }
            if let run = game.run, !run.isEmpty, let resolved = windowsPath(for: run, launch: launch) {
                plan.launchExecutable = resolved
            }
            plan.removeArguments.append(contentsOf: game.removeArguments ?? [])
            plan.files.append(contentsOf: game.files ?? [])
            plan.launchArguments.append(contentsOf: game.launchArgumentTokens)
            for fix in game.fixes ?? [] { plan.notes.append("\(game.titleText): \(fix)") }
            for fix in game.unavailableFixes ?? [] { plan.notes.append("\(game.titleText): \(fix) is not available on Madeira") }
            for issue in game.issues ?? [] { plan.notes.append("\(game.titleText): \(issue)") }
        }

        // The selected fallback's own settings, over the profile.
        if let fallback {
            merge(fallback, into: &plan)
            plan.notes.append("fallback: \(fallback.name ?? "alternative \(alternative)")")
        }

        // The selected remedy, over the profile: this attempt exists because
        // the previous one failed with that category.
        if let remedy {
            merge(remedy, into: &plan)
            plan.remedy = remedyCategory
            let label = remedyCategory.map { "after \($0)" } ?? "after the previous attempt"
            plan.notes.append("retry (\(label)): \(remedy.name ?? "remedy")")
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

        // A pin to the builtin is a claim about this runtime, and the claim can
        // be false for the architecture being launched: d3dcompiler_33 and
        // xaudio2_7 are in the 32-bit build and not in the 64-bit farms. Keeping
        // such a pin would ignore the copy the title ships and load nothing in
        // its place, so it is dropped for this launch and said out loud.
        if !plan.dllOverrides.isEmpty, database.knowsItsModules {
            var droppedPins: [String] = []
            for (dll, order) in plan.dllOverrides
            where isBuiltinOnly(order) && !database.serves(dll, bits: launch.bits) {
                plan.dllOverrides.removeValue(forKey: dll)
                droppedPins.append(dll)
            }
            if !droppedPins.isEmpty {
                plan.notes.append("no builtin pin for " + summarise(droppedPins.sorted())
                                  + ": \(launch.bits == 64 ? "the 64-bit runtime" : "the runtime") does not ship it,"
                                  + " so this launch uses whatever the title brought")
            }
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

        // DXMT reads its options from DXMT_CONFIG, which madeira.cfg also uses.
        // A per-game option therefore appends to the launch's own list instead
        // of replacing the player's settings.
        if let ours = plan.environment["DXMT_CONFIG"], let existing = launch.environment["DXMT_CONFIG"] {
            let parts = existing.split(separator: ";").map(String.init) + ours.split(separator: ";").map(String.init)
            var seen = Set<String>()
            let merged = parts.filter { !$0.isEmpty && seen.insert($0).inserted }
            plan.environment["DXMT_CONFIG"] = merged.joined(separator: ";")
        }

        // The same token or operation named by a component and by the profile
        // is applied once.
        plan.removeArguments = plan.removeArguments.reduce(into: [String]()) { result, token in
            if !token.isEmpty, !result.contains(token) { result.append(token) }
        }
        var seenActions = Set<String>()
        plan.files = plan.files.filter { action in
            seenActions.insert("\(action.actionName)|\(action.locationName)|\(action.path ?? "")|\(action.to ?? "")").inserted
        }

        // Per-application registry: version and DLL overrides keyed by the
        // executable, so two games in the one shared prefix cannot collide.
        let app = appDefaultsKey(launch.executable)
        // A component cannot know which program will use it, so "{app}" in a
        // registry key or value name stands for this launch's AppDefaults key.
        // That is how a recipe carries per-application settings, such as Wine's
        // own Direct3D configuration.
        for index in plan.registry.indices {
            guard (plan.registry[index].key?.contains("{app}") ?? false)
                    || (plan.registry[index].name?.contains("{app}") ?? false) else { continue }
            plan.registry[index].key = plan.registry[index].key?
                .replacingOccurrences(of: "{app}", with: app)
            plan.registry[index].name = plan.registry[index].name?
                .replacingOccurrences(of: "{app}", with: app)
        }
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

        // What the executable imports that nothing accounts for. A catalogue
        // component answers for its own DLLs, a rule for what it matches, the
        // runtime for the modules Wine provides; anything left the program has
        // to ship itself. It is reported, never acted on: guessing a file for an
        // unknown import is how a prefix gets broken.
        //
        // A component's claim counts only as far as this launch's runtime can
        // honour it. A `builtin` entry names modules Wine builds, and the 64-bit
        // farms carry fewer of them than the 32-bit build, so a claim that is
        // true for one architecture and false for the other does not silence
        // the report for the launch that cannot load the module. An entry that
        // brings its own file (payload, partial with `dlls`) answers whatever
        // the architecture, and so does one that cannot work here at all: an
        // unsupported component is an answer, and repeating it as a missing
        // module would be the same fact twice.
        let importedDLLs = Set(launch.importedDLLs.map { importName($0) })
        if !importedDLLs.isEmpty {
            var accounted = Set(catalogue.keys.map { importName($0) })
            for dependency in catalogue.values {
                let answers = !(dependency.dlls ?? []).isEmpty || dependency.supportName == "unsupported"
                for name in dependency.imports ?? [] {
                    let clean = importName(name)
                    if answers || !database.knowsItsModules || database.serves(clean, bits: launch.bits) {
                        accounted.insert(clean)
                    }
                }
            }
            for rule in applicable {
                for name in (rule.when?.imports ?? []) + (rule.when?.files ?? []) { accounted.insert(importName(name)) }
            }
            let unaccounted = importedDLLs.subtracting(accounted).subtracting(database.providedModules(launch.bits))
                .filter { !database.isAPISet($0) }
            let absent = database.absentModules(launch.bits)
            plan.unavailableModules = unaccounted.intersection(absent).sorted()
            plan.unaccountedImports = unaccounted.subtracting(absent).sorted()
            if !plan.unavailableModules.isEmpty {
                // The 64-bit farms carry fewer modules than the 32-bit build,
                // so the sentence says which half of the runtime is short.
                let scope = launch.bits == 64 ? "the 64-bit runtime does not ship " : "the runtime does not ship "
                plan.notes.append(scope + summarise(plan.unavailableModules)
                                  + "; a title that needs it must bring its own copy")
                for name in plan.unavailableModules {
                    if let reason = database.notBuiltReason(name) {
                        plan.notes.append("\(name): \(reason)")
                    }
                }
            }
            if !plan.unaccountedImports.isEmpty {
                plan.notes.append("no component or rule accounts for " + summarise(plan.unaccountedImports)
                                  + "; the program ships it beside itself")
            }
        }

        // The attempts available for this launch: the profile, its fallbacks in
        // order, then the remedy for the category the last session failed with.
        // The launcher advances through them when one fails, so a title with
        // more than one workable configuration is retried without the user
        // changing anything. When the reason is one no configuration can fix
        // (anti-cheat, DRM), the ladder collapses to the one attempt.
        let fallbackCount = plan.matched.compactMap { $0.fallbacks?.count }.max() ?? 0
        var alternatives = [CompatAlternative(index: 0, name: alternativeName(plan.matched, at: 0), note: nil)]
        if fallbackCount > 0 {
            for index in 1...fallbackCount {
                let entry = plan.matched.compactMap({ $0.fallbacks }).first { $0.count >= index }?[index - 1]
                alternatives.append(CompatAlternative(index: index, name: entry?.name ?? "Alternative \(index)", note: entry?.note))
            }
        }
        for (offset, entry) in remedies.enumerated() {
            alternatives.append(CompatAlternative(index: fallbackCount + 1 + offset,
                                                  name: entry.remedy.name ?? "Fix for \(entry.category)",
                                                  note: entry.remedy.note))
        }
        if plan.isFatal { alternatives = [alternatives[0]] }
        plan.alternatives = alternatives
        plan.alternativeIndex = min(max(alternative, 0), alternatives.count - 1)
        return plan
    }

    /// The label of one attempt: the profile's own name, or a fallback's.
    private static func alternativeName(_ games: [CompatGame], at index: Int) -> String {
        if index == 0 { return games.contains { !($0.fallbacks ?? []).isEmpty } ? "Default" : "Automatic" }
        for game in games {
            guard let fallbacks = game.fallbacks, index <= fallbacks.count else { continue }
            return fallbacks[index - 1].name ?? "Alternative \(index)"
        }
        return "Alternative \(index)"
    }

    /// An import or catalogue name normalised for matching: lowercased and
    /// without the ".dll" extension.
    static func importName(_ name: String) -> String {
        let lowered = name.lowercased()
        return lowered.hasSuffix(".dll") ? String(lowered.dropLast(4)) : lowered
    }

    /// The architecture of a PE image: 32 for i386 and ARM, 64 for AMD64 and
    /// ARM64, nil for anything else (a 16-bit image, an installer's stub, a
    /// file that is not a PE at all). The DOS stub's `e_lfanew` points at the
    /// PE signature and the machine field follows it, so a class-sized rule
    /// (`bits`) rests on four bytes here rather than on a file name.
    /// The launcher reads the header; this parses it, which is what lets the
    /// rules be tested without a Windows program on disk.
    static func machineBits(_ image: Data) -> Int? {
        guard image.count > 0x40 else { return nil }
        let start = image.startIndex
        func byte(_ index: Int) -> UInt8 { image[start + index] }
        guard byte(0) == 0x4d, byte(1) == 0x5a else { return nil }   // "MZ"
        let base = Int(byte(0x3c)) | Int(byte(0x3d)) << 8 | Int(byte(0x3e)) << 16 | Int(byte(0x3f)) << 24
        guard base >= 0, base + 6 <= image.count else { return nil }
        guard byte(base) == 0x50, byte(base + 1) == 0x45,
              byte(base + 2) == 0, byte(base + 3) == 0 else { return nil }   // "PE\0\0"
        switch Int(byte(base + 4)) | Int(byte(base + 5)) << 8 {
        case 0x014c, 0x01c0, 0x01c4: return 32   // i386, ARM, ARMv7
        case 0x8664, 0xaa64: return 64           // AMD64, ARM64
        default: return nil
        }
    }

    /// Wine's AppDefaults key is the image name as Windows sees it.
    static func appDefaultsKey(_ executable: String) -> String {
        let base = (executable as NSString).lastPathComponent
        return base.isEmpty ? "madeira.exe" : base
    }

    /// Turn a profile's `run` target into the Windows path the session starts.
    ///
    /// "Launcher.exe" and "../bin/game.exe" resolve against the folder of the
    /// program the entry picked, which is how upstream fixes name them; an
    /// absolute Windows path ("C:\Games\Foo\foo.exe") is taken as written.
    /// Returns nil when the target escapes drive_c.
    static func windowsPath(for run: String, launch: CompatLaunch) -> String? {
        let trimmed = run.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        if trimmed.count > 1, trimmed.dropFirst().first == ":", trimmed.first?.isLetter == true {
            return trimmed
        }
        guard let relative = launch.relativePath, !relative.isEmpty else { return nil }
        let folder = (relative as NSString).deletingLastPathComponent
        var parts = folder.isEmpty ? [] : folder.components(separatedBy: "/").filter { !$0.isEmpty }
        for component in trimmed.replacingOccurrences(of: "\\", with: "/").components(separatedBy: "/") {
            switch component {
            case "", ".": continue
            case "..":
                // Above drive_c is not addressable as a Windows path here.
                guard !parts.isEmpty else { return nil }
                parts.removeLast()
            default: parts.append(component)
            }
        }
        guard !parts.isEmpty else { return nil }
        return "C:\\" + parts.joined(separator: "\\")
    }

    /// Apply a plan's file actions: run before the session starts, against the
    /// folder of the program being started (`gameDirectory`, a Unix path) or
    /// the prefix's drive_c. Idempotent: a source that is already gone, or a
    /// rename whose destination already exists, is a no-op, so a title that
    /// starts twice does not accumulate copies.
    static func applyFiles(_ plan: CompatPlan, gameDirectory: String, prefix: String,
                           fileManager: FileManager = .default) -> (applied: Int, failures: [String]) {
        var applied = 0
        var failures: [String] = []
        for action in plan.files {
            let base = action.locationName == "prefix"
                ? prefix + "/drive_c" : gameDirectory
            guard let path = action.path, !path.isEmpty else { continue }
            let source = base + "/" + path.replacingOccurrences(of: "\\", with: "/")
            switch action.actionName {
            case "rename":
                guard let to = action.to, !to.isEmpty else {
                    failures.append("rename \(path): no destination"); continue
                }
                let destination = base + "/" + to.replacingOccurrences(of: "\\", with: "/")
                if fileManager.fileExists(atPath: destination) { continue }
                guard fileManager.fileExists(atPath: source) else { continue }
                do {
                    try fileManager.moveItem(atPath: source, toPath: destination)
                    applied += 1
                } catch {
                    failures.append("rename \(path): \(error.localizedDescription)")
                }
            case "delete":
                guard fileManager.fileExists(atPath: source) else { continue }
                do {
                    try fileManager.removeItem(atPath: source)
                    applied += 1
                } catch {
                    failures.append("delete \(path): \(error.localizedDescription)")
                }
            case "mkdir":
                var isDirectory: ObjCBool = false
                if fileManager.fileExists(atPath: source, isDirectory: &isDirectory), isDirectory.boolValue { continue }
                do {
                    try fileManager.createDirectory(atPath: source, withIntermediateDirectories: true)
                    applied += 1
                } catch {
                    failures.append("create \(path): \(error.localizedDescription)")
                }
            default:
                failures.append("\(action.summaryText): unsupported action")
            }
        }
        return (applied, failures)
    }

    /// Whether an override order pins the builtin and nothing else. Wine loads
    /// a native file only when the order allows it, so `b` alone means "never
    /// the title's own copy".
    static func isBuiltinOnly(_ order: String) -> Bool {
        let tokens = order.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            .filter { !$0.isEmpty }
        return tokens == ["b"]
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

    private static func merge(_ recipe: CompatRecipe, into plan: inout CompatPlan) {
        for (key, value) in recipe.env ?? [:] { plan.environment[key] = value }
        for (dll, order) in recipe.dllOverrides ?? [:] { plan.dllOverrides[dll.lowercased()] = order }
        plan.registry.append(contentsOf: recipe.registry ?? [])
        if let version = recipe.windowsVersion { plan.windowsVersion = version }
        plan.launchArguments.append(contentsOf: CompatGame.splitArguments(recipe.launchArguments ?? ""))
        plan.removeArguments.append(contentsOf: recipe.removeArguments ?? [])
        plan.files.append(contentsOf: recipe.files ?? [])
        if let note = recipe.notes, !note.isEmpty {
            plan.notes.append("\(recipe.title ?? recipe.id): \(note)")
        }
    }

    private static func merge(_ fallback: CompatFallback, into plan: inout CompatPlan) {
        for (key, value) in fallback.env ?? [:] { plan.environment[key] = value }
        for (dll, order) in fallback.dllOverrides ?? [:] { plan.dllOverrides[dll.lowercased()] = order }
        plan.registry.append(contentsOf: fallback.registry ?? [])
        if let version = fallback.windowsVersion { plan.windowsVersion = version }
        plan.launchArguments.append(contentsOf: CompatGame.splitArguments(fallback.launchArguments ?? ""))
        plan.removeArguments.append(contentsOf: fallback.removeArguments ?? [])
        plan.files.append(contentsOf: fallback.files ?? [])
    }

    /// The universal configuration, lowest priority of all: it fills in what
    /// nothing more specific said. Its registry goes to the front for the same
    /// reason — a later value for the same key is the one that wins.
    private static func mergeBaseline(_ game: CompatGame, into plan: inout CompatPlan) {
        for (key, value) in game.env ?? [:] where plan.environment[key] == nil {
            plan.environment[key] = value
        }
        for (dll, order) in game.dllOverrides ?? [:] where plan.dllOverrides[dll.lowercased()] == nil {
            plan.dllOverrides[dll.lowercased()] = order
        }
        if !(game.registry ?? []).isEmpty { plan.registry.insert(contentsOf: game.registry ?? [], at: 0) }
        if let version = game.windowsVersion, plan.windowsVersion == nil { plan.windowsVersion = version }
        if plan.baselineTitle == nil { plan.baselineTitle = game.title }
    }

    /// A general rule's fields, over the baseline and below any profile.
    private static func merge(_ rule: CompatRule, into plan: inout CompatPlan) {
        for (key, value) in rule.env ?? [:] { plan.environment[key] = value }
        for (dll, order) in rule.dllOverrides ?? [:] { plan.dllOverrides[dll.lowercased()] = order }
        plan.registry.append(contentsOf: rule.registry ?? [])
        if let version = rule.windowsVersion { plan.windowsVersion = version }
        plan.launchArguments.append(contentsOf: CompatGame.splitArguments(rule.launchArguments ?? ""))
        if let note = rule.note, !note.isEmpty {
            plan.notes.append("\(rule.title ?? rule.id): \(note)")
        }
    }

    /// The general rules that apply to this program, in database order.
    static func rules(_ database: CompatDatabase, _ launch: CompatLaunch) -> [CompatRule] {
        database.ruleList.filter { matches($0, launch) }
    }

    /// Whether a rule's conditions hold. Every list present has to match; a
    /// rule with no conditions would apply to everything, so it never matches.
    static func matches(_ rule: CompatRule, _ launch: CompatLaunch) -> Bool {
        guard let when = rule.when, !when.isEmpty else { return false }
        if let names = when.imports, !names.isEmpty {
            let imports = Set(launch.importedDLLs.map { importName($0) })
            guard names.contains(where: { imports.contains(importName($0)) }) else { return false }
        }
        if let names = when.files, !names.isEmpty {
            let files = Set(launch.files.map { importName($0) })
            guard names.contains(where: { files.contains(importName($0)) }) else { return false }
        }
        if let names = when.executableContains, !names.isEmpty {
            let executable = launch.executable.lowercased()
            guard names.contains(where: { executable.contains($0.lowercased()) }) else { return false }
        }
        if let names = when.pathContains, !names.isEmpty {
            guard let path = launch.relativePath?.lowercased(),
                  names.allSatisfy({ path.contains($0.lowercased()) }) else { return false }
        }
        if let bits = when.bits, launch.bits != bits { return false }
        return true
    }

    /// The attempts that follow a profile's own fallbacks: the remedy for each
    /// category the last session failed with, in diagnosis order, at most two.
    /// A category with no remedy, or a remedy that does nothing, adds nothing.
    static func remedyAttempts(_ database: CompatDatabase, _ launch: CompatLaunch)
        -> [(category: String, remedy: CompatFallback)] {
        let table = database.remedyTable
        guard !table.isEmpty else { return [] }
        var attempts: [(category: String, remedy: CompatFallback)] = []
        for category in launch.previousFailure {
            guard attempts.count < 2, let remedy = table[category], hasEffects(remedy) else { continue }
            guard !attempts.contains(where: { $0.category == category }) else { continue }
            attempts.append((category, remedy))
        }
        return attempts
    }

    /// Whether a remedy would change anything.
    static func hasEffects(_ attempt: CompatFallback) -> Bool {
        !(attempt.dependencies ?? []).isEmpty || !(attempt.recipes ?? []).isEmpty
            || !(attempt.dllOverrides ?? [:]).isEmpty || !(attempt.env ?? [:]).isEmpty
            || !(attempt.registry ?? []).isEmpty || attempt.windowsVersion != nil
            || !(attempt.launchArguments ?? "").isEmpty || !(attempt.removeArguments ?? []).isEmpty
            || !(attempt.files ?? []).isEmpty
    }

    /// "a, b and 3 more" — a note is a sentence, not an inventory.
    static func summarise(_ names: [String], limit: Int = 6) -> String {
        guard names.count > limit else { return names.joined(separator: ", ") }
        return names.prefix(limit).joined(separator: ", ") + " and \(names.count - limit) more"
    }

    /// Which of a dependency's required files are absent from every payload
    /// directory and from the program's own folder. Matching is
    /// case-insensitive.
    ///
    /// `beside` is the executable's folder. A payload is a file the user would
    /// otherwise have to supply, and a title that ships the runtime itself
    /// (d3dx9_43.dll, oo2core, a store client) has already answered the
    /// requirement — reporting it would be a false alarm on half the library.
    static func missingPayload(_ dependency: CompatDependency, directories: [String],
                              beside: [String] = []) -> [String] {
        let required = dependency.dlls ?? []
        guard !required.isEmpty else { return [] }
        var found = Set(beside.map { $0.lowercased() })
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
        else if plan.baselineTitle != nil {
            lines.append("no profile matches; the universal configuration applies")
        }
        if !plan.ruleTitles.isEmpty { lines.append("rules: " + plan.ruleTitles.joined(separator: ", ")) }
        if plan.alternatives.count > 1, plan.alternativeIndex < plan.alternatives.count {
            let attempt = plan.alternatives[plan.alternativeIndex]
            lines.append("attempt: \(attempt.index + 1) of \(plan.alternatives.count) — \(attempt.name)")
        }
        if !plan.dependencies.isEmpty { lines.append("dependencies: " + plan.dependencyTitles.joined(separator: ", ")) }
        if !plan.recipeTitles.isEmpty { lines.append("fixes: " + plan.recipeTitles.joined(separator: ", ")) }
        if !plan.dllOverrides.isEmpty { lines.append("dll overrides: " + plan.dllOverrideString) }
        if let version = plan.windowsVersion { lines.append("windows version: \(version)") }
        if !plan.environment.isEmpty { lines.append("environment: " + plan.environment.keys.sorted().joined(separator: ", ")) }
        if !plan.unsetEnvironment.isEmpty { lines.append("unset: " + plan.unsetEnvironment.sorted().joined(separator: ", ")) }
        if !plan.launchArguments.isEmpty { lines.append("arguments: " + plan.launchArguments.joined(separator: " ")) }
        if !plan.removeArguments.isEmpty { lines.append("dropped arguments: " + plan.removeArguments.joined(separator: " ")) }
        if let run = plan.launchExecutable { lines.append("runs instead: \(run)") }
        for action in plan.files { lines.append("file: \(action.summaryText)") }
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
