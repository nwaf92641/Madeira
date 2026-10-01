#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
r"""Game compatibility: matching, dependency resolution and the launch plan.

Compiles the production engine (app/Madeira/GameCompat.swift) with a small
harness and checks, on synthetic databases:

  * decoding, catalogue-key-to-id, and an overlay that updates games and
    dependencies;
  * matching by Steam App ID, executable (case-insensitively), GOG slug and
    install-path fragment, and the specificity order between them;
  * dependency resolution: `requires` expands before the dependent, the user's
    exclusions and extras apply, an unsupported or manual dependency is
    reported without merging anything, and a payload dependency is satisfied
    case-insensitively from a payload directory or reported with its missing
    file names;
  * the plan: DLL overrides from dependencies, profile and user merge with the
    user winning, WINEDLLOVERRIDES preserves whatever the launch already had
    (ours winning), a Windows version and the overrides land in the per-app
    HKCU\Software\Wine\AppDefaults\<exe> keys, launch arguments split, and an
    explicitly disabled override produces an empty plan;
  * automatic detection: a dependency's `imports` names select it from the
    DLLs the executable imports (normalised case- and extension-insensitively);
  * reusable recipes: a recipe referenced by a profile or a dependency applies
    its overrides, arguments and components exactly once, and a recipe that is
    nothing but one override is reused rather than repeated per title;
  * fallbacks: the alternatives a title offers, the one selected, an
    out-of-range request clamped to the last, and the summary line naming the
    attempt;
  * session diagnosis (CompatDiagnosis.swift): a log is classified into the
    categories a user can act on, Madeira's own bracketed diagnostic lines
    never classify a session, a crash without a log line is a launch failure, a
    clean exit with errors is "works with problems", and anti-cheat or DRM
    stops the retry ladder;
  * the registry text merge: a REG_SZ value creates and updates its section
    idempotently with escaped backslashes, REG_DWORD and REG_MULTI_SZ encode
    correctly, and a new section carries a timestamp.

It then loads the real app/Madeira/compat.json and checks it decodes, is
large enough to matter, maps the DLLs games import, references only known
dependencies and resolves the reference title (Thumper, 356400). It also loads
the generator and checks that a Proton fix which selects the Vulkan renderer is
not passed through to a game. Finally it checks the source wiring: the
LibraryEntry field, the launch hook, the game-details section, the library
badge and the Xcode project entries.

Run from anywhere; needs `swiftc` on PATH (or SWIFTC set).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc')
DB_PATH = APP / 'compat.json'
failures = 0


def require(condition: bool, label: str) -> None:
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


HARNESS = r'''
import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

var failed = 0
func check(_ condition: Bool, _ what: String) {
    print((condition ? "PASS: " : "FAIL: ") + what)
    if !condition { failed += 1 }
}
func db(_ json: String) -> CompatDatabase {
    (try? JSONDecoder().decode(CompatDatabase.self, from: Data(json.utf8))) ?? CompatDatabase()
}

 let fixture = db(#"""
{"schema":1,
 "dependencies":{
   "vcrun2015":{"title":"VC++ 2015","support":"builtin","dll_overrides":{"vcruntime140":"n,b"}},
   "vcrun2019":{"title":"VC++ 2019","support":"builtin","dll_overrides":{"msvcp140":"n,b"},"requires":["vcrun2015"]},
   "d3dx9":{"title":"D3DX9","support":"payload","dlls":["d3dx9_43.dll"],"imports":["d3dx9_43"],"dll_overrides":{"d3dx9_43":"n,b"}},
   "xinput":{"title":"XInput","support":"builtin","imports":["xinput1_3","xinput1_4"],"dll_overrides":{"xinput1_3":"b"}},
   "ddraw9x":{"title":"DirectDraw","support":"builtin","recipes":["native-ddraw"]},
   "dotnet":{"title":".NET","support":"partial","notes":"wine-mono covers most titles"},
   "bad":{"title":"Bad Anti-Cheat","support":"unsupported"},
   "man":{"title":"Manual Installer","support":"manual"}},
 "recipes":{
   "native-ddraw":{"title":"Native ddraw","category":"dx9","dll_overrides":{"ddraw":"n,b"}},
   "engine-dx9":{"title":"Direct3D 9 renderer","category":"dx9","launch_arguments":"-dx9"},
   "builtin-dinput8":{"title":"Builtin dinput8","category":"input","dll_overrides":{"dinput8":"b"}}},
 "games":[
   {"title":"Foo","appid":42,"executables":["foo.exe"],"dependencies":["vcrun2019"],
    "recipes":["engine-dx9"],
    "dll_overrides":{"dinput8":"b"},"windows_version":"win7","launch_arguments":"-windowed -dx11",
    "fallbacks":[{"name":"Builtin DirectInput","recipes":["builtin-dinput8"],
                  "env":{"MADEIRA_FALLBACK":"1"},"note":"when the game's own proxy fails"}]},
   {"title":"Generic","path_contains":["/Foo/"],"env":{"MADEIRA_SAMPLE":"1"}}]}
"""#)

// decoding
let table = fixture.dependencyTable
check(table["vcrun2019"]?.id == "vcrun2019", "catalogue key becomes the dependency id")
check(fixture.gameList.count == 2, "games decode")

// matching and specificity
let byAppID = CompatLaunch(executable: "foo.exe", relativePath: "Games/Foo/foo.exe", appid: 42)
check(GameCompatibility.matchingGames(fixture, byAppID).first?.title == "Foo", "Steam App ID match")
let byExe = CompatLaunch(executable: "FOO.EXE", relativePath: "Elsewhere/foo.exe")
check(GameCompatibility.matchingGames(fixture, byExe).first?.title == "Foo", "executable match is case-insensitive")
let byPath = CompatLaunch(executable: "bar.exe", relativePath: "Games/Foo/bar.exe")
check(GameCompatibility.matchingGames(fixture, byPath).first?.title == "Generic", "install-path fragment match")
let both = CompatLaunch(executable: "foo.exe", relativePath: "Games/Foo/foo.exe", appid: 42)
check(GameCompatibility.matchingGames(fixture, both).map(\.title) == ["Foo", "Generic"], "app id outranks a path fragment; both merge")

// resolution
let plan = GameCompatibility.plan(byAppID, database: fixture)
check(plan.dependencies.map(\.id) == ["vcrun2015", "vcrun2019"], "requires expands before the dependent")
check(plan.windowsVersion == "win7", "Windows version comes from the profile")
check(plan.dllOverrides["dinput8"] == "b" && plan.dllOverrides["msvcp140"] == "n,b", "profile and dependency overrides merge")
check(plan.launchArguments.contains("-windowed") && plan.launchArguments.contains("-dx11"),
      "launch arguments split on spaces")
check(plan.environment["MADEIRA_SAMPLE"] == "1", "the path profile's environment merges too")

// exclusion and extras
let excluded = GameCompatibility.plan(CompatLaunch(executable: "foo.exe", appid: 42,
    overrides: CompatOverrides(disabledDependencies: ["vcrun2019"])), database: fixture)
check(excluded.dependencies.isEmpty, "a disabled dependency is dropped")
let extra = GameCompatibility.plan(CompatLaunch(executable: "n.exe",
    overrides: CompatOverrides(extraDependencies: ["bad", "man"])), database: fixture)
check(extra.unsatisfied.contains { $0.id == "bad" && $0.support == "unsupported" }, "unsupported dependency is reported")
check(extra.unsatisfied.contains { $0.id == "man" && $0.support == "manual" }, "manual dependency is reported")
check(extra.dllOverrides.isEmpty && extra.windowsVersion == nil, "an unsatisfied dependency merges nothing")
let partial = GameCompatibility.plan(CompatLaunch(executable: "p.exe",
    overrides: CompatOverrides(extraDependencies: ["dotnet"])), database: fixture)
check(partial.notes.contains { $0.contains("wine-mono") },
      "a partially supported component reports its own caveat, not a generic one")

// payload availability
let missing = GameCompatibility.plan(CompatLaunch(executable: "f.exe",
    overrides: CompatOverrides(extraDependencies: ["d3dx9"])), database: fixture)
check(missing.unsatisfied.contains { $0.id == "d3dx9" && $0.reason.contains("d3dx9_43.dll") },
      "a missing payload names its files")
let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
try? Data().write(to: dir.appendingPathComponent("D3DX9_43.DLL"))
let satisfied = GameCompatibility.plan(CompatLaunch(executable: "f.exe", payloadDirectories: [dir.path],
    overrides: CompatOverrides(extraDependencies: ["d3dx9"])), database: fixture)
check(satisfied.unsatisfied.isEmpty, "a payload found case-insensitively is satisfied")
check(satisfied.dllOverrides["d3dx9_43"] == "n,b", "a satisfied payload applies its override")
try? FileManager.default.removeItem(at: dir)

// automatic detection from the import table
let detected = GameCompatibility.plan(CompatLaunch(executable: "g.exe", importedDLLs: ["XInput1_4.dll"]), database: fixture)
check(detected.dllOverrides["xinput1_3"] == "b", "an imported DLL selects its dependency")
let detectedPayload = GameCompatibility.plan(CompatLaunch(executable: "g.exe", importedDLLs: ["d3dx9_43.dll"]), database: fixture)
check(detectedPayload.unsatisfied.contains { $0.id == "d3dx9" }, "a detected payload still needs its files")
check(GameCompatibility.plan(CompatLaunch(executable: "g.exe", importedDLLs: ["xinput1_3.dll"],
    overrides: CompatOverrides(disabledDependencies: ["xinput"])), database: fixture).dllOverrides.isEmpty,
    "a detected dependency can be disabled")


// reusable fixes
check(plan.recipes.contains("engine-dx9") && plan.launchArguments.contains("-dx9"),
      "a profile's recipe applies its launch arguments")
check(plan.recipeTitles.contains("Direct3D 9 renderer"), "the recipe's title is reported, not its id")
let viaDependency = GameCompatibility.plan(CompatLaunch(executable: "d.exe",
    overrides: CompatOverrides(extraDependencies: ["ddraw9x"])), database: fixture)
check(viaDependency.dllOverrides["ddraw"] == "n,b", "a dependency's recipe applies its override")
check(viaDependency.recipes == ["native-ddraw"], "a recipe is applied exactly once")

// fallbacks: the profile, then its own attempts
check(plan.alternatives.count == 2 && plan.alternatives[0].name == "Default",
      "a profile with a fallback offers two attempts, the first unnamed")
check(plan.alternatives[1].name == "Builtin DirectInput" && plan.alternatives[1].note != nil,
      "the fallback's name and note are carried")
check(plan.alternativeIndex == 0, "the first attempt is the default")
let second = GameCompatibility.plan(byAppID, database: fixture, alternative: 1)
check(second.alternativeIndex == 1 && second.environment["MADEIRA_FALLBACK"] == "1",
      "the selected fallback's own settings apply")
check(second.recipes.contains("builtin-dinput8"), "the fallback's recipe applies")
check(second.launchArguments.contains("-windowed"), "the profile's settings still apply under a fallback")
let clamped = GameCompatibility.plan(byAppID, database: fixture, alternative: 9)
check(clamped.alternativeIndex == 1, "an out-of-range attempt clamps to the last")
check(GameCompatibility.summary(clamped).contains { $0.contains("attempt: 2 of 2") },
      "the summary names the attempt in use")

// session diagnosis (CompatDiagnosis.swift)
let missingDLL = CompatLogDiagnosis.classify(
    log: #"err:module:import_dll Library msvcp140.dll (which is needed by L"C:\\game\\g.exe") not found"#,
    exitStatus: nil, presented: false, duration: 6, attempt: 0, alternative: nil)
check(missingDLL.outcome == "fails" && missingDLL.categories.contains("dependency"),
      "a missing DLL fails the session and is classified as a missing component")
let clean = CompatLogDiagnosis.classify(log: "[xinput] slot=0 connected\n[jit] block translated\n[render] d3d11 ok",
                                        exitStatus: nil, presented: true, duration: 900, attempt: 0, alternative: nil)
check(clean.outcome == "works" && clean.categories.isEmpty,
      "Madeira's own bracketed diagnostic lines never classify a session")
let crash = CompatLogDiagnosis.classify(log: "wine: Unhandled exception 0xc0000005",
                                        exitStatus: 0xC0000005, presented: false, duration: 4,
                                        attempt: 0, alternative: nil)
check(crash.outcome == "fails" && crash.detail?.contains("C0000005") == true,
      "an exit status is carried into the result")
let mute = CompatLogDiagnosis.classify(log: "", exitStatus: nil, presented: false, duration: 3,
                                       attempt: 0, alternative: nil)
check(mute.outcome == "fails" && mute.categories == ["launch"],
      "a session that never presented and logged nothing is a launch failure")
let stalled = CompatLogDiagnosis.classify(log: "", exitStatus: nil, presented: false, duration: 300,
                                         attempt: 0, alternative: nil)
check(stalled.outcome == "fails" && stalled.detail?.contains("300s") == true,
      "a session that never presented is a failure however long it lasted")
let noisy = CompatLogDiagnosis.classify(log: "err:dsound: could not open the device",
                                        exitStatus: nil, presented: true, duration: 300,
                                        attempt: 0, alternative: nil)
check(noisy.outcome == "issues" && noisy.categories == ["audio"],
      "a clean session with an error line is 'works with problems'")
let protected = CompatLogDiagnosis.classify(log: "Denuvo anti-tamper initialised",
                                            exitStatus: 1, presented: false, duration: 2,
                                            attempt: 0, alternative: nil)
check(protected.isFailure && protected.isFatal, "copy protection is a fatal classification")
check(protected.outcomeTitle == "Did not work" && protected.categoriesTitle == "Copy protection",
      "the result renders for the user")
check(CompatLogDiagnosis.report(missingDLL).hasPrefix("[compat-result] outcome=fails attempt=1"),
      "the result is logged as one line")

// disabled override
let disabled = GameCompatibility.plan(CompatLaunch(executable: "foo.exe", appid: 42,
    overrides: CompatOverrides(enabled: false)), database: fixture)
check(disabled.isEmpty, "an explicitly disabled override yields an empty plan")

// environment reconciliation between launches
var environment: [String: String] = ["A": "1", "B": "2"]
let registry = CompatEnvironmentRegistry()
registry.reconcile(keeping: ["A", "B"], unset: { environment[$0] = nil })   // first launch
registry.reconcile(keeping: ["A", "C"], unset: { environment[$0] = nil })   // next launch drops B
check(environment["B"] == nil && environment["A"] == "1", "a variable the next plan drops is unset")
registry.reconcile(keeping: [], unset: { environment[$0] = nil })
check(environment["A"] == nil, "every compatibility variable is dropped when nothing needs one")

// WINEDLLOVERRIDES merge: keep what the launch had, ours win
let merged = GameCompatibility.plan(CompatLaunch(executable: "foo.exe", appid: 42,
    environment: ["WINEDLLOVERRIDES": "dinput8=n;other=b"]), database: fixture)
let overrideString = merged.environment["WINEDLLOVERRIDES"] ?? ""
check(overrideString.contains("other=b"), "an unrelated existing override survives")
check(overrideString.contains("dinput8=b") && !overrideString.contains("dinput8=n"), "our override replaces the existing one")

// per-app registry
check(plan.registry.contains { $0.key == #"Software\Wine\AppDefaults\foo.exe"# && $0.name == "Version" && $0.value == "win7" },
      "the Windows version lands in the per-app key")
check(plan.registry.contains { $0.key == #"Software\Wine\AppDefaults\foo.exe\DllOverrides"# && $0.name == "dinput8" && $0.value == "builtin" },
      "an override lands in the per-app DllOverrides key")
check(plan.registry.allSatisfy { $0.hiveName == "HKCU" }, "per-app values default to HKCU")

// registry text
let registryHeader = "[Software\\\\Wine\\\\AppDefaults\\\\foo.exe] 1"
let versionValue = CompatRegistryValue(hive: "HKCU", key: #"Software\Wine\AppDefaults\foo.exe"#, name: "Version", type: "REG_SZ", value: "win7")
var (text, changed) = CompatRegistryText.merge([versionValue], into: "WINE REGISTRY Version 2\n", now: 1)
check(changed == 1 && text.contains(registryHeader), "a new REG_SZ value creates an escaped, timestamped section")
check(text.contains(#""Version"="win7""#), "a REG_SZ value is quoted")
let dword = CompatRegistryValue(hive: "HKCU", key: #"Software\Wine\Test"#, name: "N", type: "REG_DWORD", value: "1")
var (dwordText, _) = CompatRegistryText.merge([dword], into: "WINE REGISTRY Version 2\n", now: 1)
check(dwordText.contains(#""N"=dword:00000001"#), "a REG_DWORD value is little-endian hex")
let multi = CompatRegistryValue(hive: "HKCU", key: #"Software\Wine\Test"#, name: "M", type: "REG_MULTI_SZ", value: "a\0b")
var (multiText, _) = CompatRegistryText.merge([multi], into: "WINE REGISTRY Version 2\n", now: 1)
check(multiText.contains(#""M"=hex(7):61,00,00,00,62,00,00,00,00,00"#), "a REG_MULTI_SZ value is UTF-16 hex")
let (again, unchanged) = CompatRegistryText.merge([versionValue], into: text, now: 2)
check(unchanged == 0 && again == text, "merging the same value again changes nothing")
let (updated, delta) = CompatRegistryText.merge(
    [CompatRegistryValue(hive: "HKCU", key: #"Software\Wine\AppDefaults\foo.exe"#, name: "Version", type: "REG_SZ", value: "win10")],
    into: text, now: 2)
check(delta == 1 && updated.contains(#""Version"="win10""#) && !updated.contains(#""Version"="win7""#), "an existing value is replaced")

// overlay merge
let base = db(#"{"schema":1,"games":[{"title":"A","appid":1}],"dependencies":{"x":{"title":"X","support":"builtin"}}}"#)
let overlay = db(#"{"schema":1,"games":[{"title":"A2","appid":1},{"title":"B","appid":2}],"dependencies":{"y":{"title":"Y","support":"builtin"}}}"#)
let combined = GameCompatibility.merge(base: base, overlay: overlay)
check(combined.gameList.count == 2, "an overlay replaces a shared game and adds a new one")
check(combined.gameList.first { $0.appid == 1 }?.title == "A2", "the overlay wins for a shared game")
check(combined.dependencyTable["x"] != nil && combined.dependencyTable["y"] != nil, "an overlay keeps and adds dependencies")

// the real database
if CommandLine.arguments.count > 1, let data = try? Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])),
   let real = GameCompatibility.decode(data) {
    check(real.gameList.count >= 350, "the bundled database carries a large number of games")
    check(real.dependencyTable.count >= 300, "the bundled database carries a wide dependency catalogue")
    check(real.recipeTable.count >= 30, "the bundled database carries a reusable fix library")
    let thumper = GameCompatibility.plan(CompatLaunch(executable: "THUMPER_win10.exe", appid: 356400), database: real)
    check(thumper.titles.contains("Thumper"), "Thumper matches by App ID in the bundled database")
    check(thumper.windowsVersion == "win10", "Thumper gets its Windows version")
    check(thumper.dependencies.map(\.id).contains("vcrun2015"), "Thumper requires the VC++ runtime")
    check(thumper.unsatisfied.contains { $0.id == "vcrun2015" }, "with no payload files the runtime is reported, not silently skipped")
    check(thumper.alternatives.count >= 2, "Thumper offers a fallback when the overlay crashes it")
    let thumperFallback = GameCompatibility.plan(CompatLaunch(executable: "THUMPER_win10.exe", appid: 356400),
                                                 database: real, alternative: 1)
    check(thumperFallback.recipes.contains("steam-no-overlay"), "the fallback is a reusable fix, not a repeated override")
    let imported = GameCompatibility.plan(CompatLaunch(executable: "sample.exe", importedDLLs: ["xactengine3_7.dll", "mss32.dll"]), database: real)
    check(!imported.dependencies.isEmpty, "a game with no profile is still resolved from its import table")
    let gpu = GameCompatibility.plan(CompatLaunch(executable: "sample.exe",
        importedDLLs: ["nvapi64.dll", "oo2core_9_win64.dll", "d3dcompiler_46.dll", "xaudio2_7.dll",
                       "api-ms-win-crt-stdio-l1-1-0.dll", "d3d11.dll"]), database: real)
    check(gpu.dllOverrides["nvapi64"] == "b", "a vendor GPU library is pinned to the Wine stub")
    check(gpu.dllOverrides["d3dcompiler_46"] == "b", "an older shader compiler is pinned to Wine's builtin")
    check(gpu.dependencies.map(\.id).contains("d3d10-d3d11"),
          "Direct3D 11 is recognised as Madeira's own DXMT path")
    check(gpu.unsatisfied.isEmpty, "none of those components is reported as missing")
    let vulkan = GameCompatibility.plan(CompatLaunch(executable: "sample.exe", importedDLLs: ["vulkan-1.dll"]), database: real)
    check(vulkan.dependencies.map(\.id).contains("vulkan") && vulkan.unsatisfied.isEmpty,
          "a Vulkan import is classified without being called a missing file")
} else {
    check(false, "the bundled database loads")
}

if failed > 0 { FileHandle.standardError.write(Data("FAILURES: \(failed)\n".utf8)) }
exit(failed == 0 ? 0 : 1)
'''


def run_swift() -> None:
    if not SWIFTC:
        require(False, 'swiftc is available (set SWIFTC to a Swift compiler)')
        return
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / 'main.swift'
        harness.write_text(HARNESS, encoding='utf-8')
        binary = Path(tmp) / 'game-compat-test'
        build = subprocess.run([SWIFTC, str(APP / 'GameCompat.swift'), str(APP / 'CompatDiagnosis.swift'),
                                str(harness), '-o', str(binary)],
                               capture_output=True, text=True)
        if build.returncode != 0:
            print(build.stderr)
            require(False, 'the engine compiles')
            return
        run = subprocess.run([str(binary), str(DB_PATH)], capture_output=True, text=True)
        print(run.stdout, end='')
        if run.stderr.strip():
            print(run.stderr, file=sys.stderr, end='')
        require(run.returncode == 0, 'all engine checks pass')


def check_database() -> None:
    try:
        data = json.loads(DB_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        require(False, f'the bundled database parses ({error})')
        return
    dependencies = data.get('dependencies', {})
    recipes = data.get('recipes', {})
    games = data.get('games', [])
    require(data.get('schema') == 1, 'the database declares schema 1')
    require(len(dependencies) >= 300, f'the dependency catalogue is wide ({len(dependencies)})')
    require(len(recipes) >= 30, f'the fix library is populated ({len(recipes)})')
    require(len(games) >= 350, f'the game database is populated ({len(games)})')
    valid_support = {'builtin', 'override', 'payload', 'manual', 'unsupported', 'partial'}
    valid_tokens = {'', 'n', 'b', 'n,b', 'b,n'}
    problems: list[str] = []
    for dep_id, dep in dependencies.items():
        if dep.get('support') not in valid_support:
            problems.append(f'{dep_id}: support {dep.get("support")!r}')
        for token in (dep.get('dll_overrides') or {}).values():
            if token not in valid_tokens:
                problems.append(f'{dep_id}: token {token!r}')
        for required in dep.get('requires', []):
            if required not in dependencies:
                problems.append(f'{dep_id}: requires {required}')
        for recipe in dep.get('recipes', []):
            if recipe not in recipes:
                problems.append(f'{dep_id}: recipe {recipe}')
    for recipe_id, recipe in recipes.items():
        for required in recipe.get('requires', []):
            if required not in recipes:
                problems.append(f'recipe {recipe_id}: requires {required}')
        for dep_id in recipe.get('dependencies', []):
            if dep_id not in dependencies:
                problems.append(f'recipe {recipe_id}: dependency {dep_id}')
        for token in (recipe.get('dll_overrides') or {}).values():
            if token not in valid_tokens:
                problems.append(f'recipe {recipe_id}: token {token!r}')
    keys: set[str] = set()
    for game in games:
        key = str(game.get('appid') or game.get('gog_slug') or (game.get('executables') or ['?'])[0])
        if key in keys:
            problems.append(f'duplicate game key {key}')
        keys.add(key)
        for dep_id in game.get('dependencies', []):
            if dep_id not in dependencies:
                problems.append(f'{game.get("title")}: unknown dependency {dep_id}')
        for recipe in game.get('recipes', []):
            if recipe not in recipes:
                problems.append(f'{game.get("title")}: unknown recipe {recipe}')
        for token in (game.get('dll_overrides') or {}).values():
            if token not in valid_tokens:
                problems.append(f'{game.get("title")}: token {token!r}')
        for fallback in game.get('fallbacks', []):
            for recipe in fallback.get('recipes', []):
                if recipe not in recipes:
                    problems.append(f'{game.get("title")}: unknown fallback recipe {recipe}')
            for dep_id in fallback.get('dependencies', []):
                if dep_id not in dependencies:
                    problems.append(f'{game.get("title")}: unknown fallback dependency {dep_id}')
    require(not problems, 'the database is internally consistent (no dangling dependencies, unique keys)')
    for problem in problems[:10]:
        print('  ' + problem)
    mapped = sum(1 for dep in dependencies.values() if dep.get('imports'))
    require(mapped >= 150, f'the catalogue maps imported DLLs to dependencies ({mapped})')
    imports = {name.lower() for dep in dependencies.values() for name in dep.get('imports', [])}
    require(len(imports) >= 300, f'the catalogue recognises the DLLs games actually import ({len(imports)})')
    require(all(name == name.lower() and not name.endswith('.dll') for name in imports),
            'import names are stored normalised (lowercase, no extension)')
    recipes_used = {recipe for game in games for recipe in game.get('recipes', [])}
    require(len(recipes_used) >= 5, f'titles reuse the fix library ({len(recipes_used)} recipes referenced)')
    classified = sum(1 for game in games if game.get('unavailable_fixes'))
    require(classified >= 50, f'fixes Madeira cannot express are recorded, not dropped ({classified})')

    # Proton renders through Vulkan; Madeira does not. An imported fix that
    # selects a Vulkan device is a fix on Linux and a failure here, so it must
    # never reach a launch.
    selecting = [game.get('title') for game in games
                 if re.search(r'(?i)(^|\s)-vulkan(\s|$)', game.get('launch_arguments') or '')]
    require(not selecting, f'no profile starts a game with the Vulkan renderer ({selecting[:3]})')
    generator = load_generator()
    dropped: list[str] = []
    require(generator.filter_arguments('-fullscreen -vulkan', dropped) == ['-fullscreen'],
            'a Vulkan renderer argument is dropped and the rest of the command kept')
    require(bool(dropped) and 'Vulkan' in dropped[0],
            'the dropped renderer argument is recorded on the title, not silently removed')
    require(generator.filter_arguments('+r_renderAPI 1', []) == [],
            'an id Tech Vulkan renderer choice is dropped')
    require(generator.filter_arguments('+r_renderAPI 0', []) == ['+r_renderAPI 0'],
            "a game's own non-Vulkan renderer choice is left alone")
    guarded = sum(1 for game in games if game.get('conditional'))
    require(guarded >= 20, f'game fixes that depend on a runtime condition are flagged ({guarded})')

    # Editing compat/games.json without regenerating compat.json is the easy
    # mistake, so the committed database must carry every curated profile.
    stale: list[str] = []
    curated = json.loads((ROOT / 'compat/games.json').read_text(encoding='utf-8')).get('games', [])
    for entry in curated:
        match = next((g for g in games
                      if (entry.get('appid') and g.get('appid') == entry['appid'])
                      or g.get('title') == entry.get('title')), None)
        if match is None:
            stale.append(f'{entry.get("title")}: missing')
        elif not set(entry.get('dependencies', [])) <= set(match.get('dependencies', [])):
            stale.append(f'{entry.get("title")}: dependencies differ')
        elif not set(entry.get('recipes', [])) <= set(match.get('recipes', [])):
            stale.append(f'{entry.get("title")}: recipes differ')
        elif len(match.get('fallbacks', [])) != len(entry.get('fallbacks', [])):
            stale.append(f'{entry.get("title")}: fallbacks differ')
    require(not stale, f'the database is regenerated from compat/games.json ({len(curated)} curated profiles)')
    for problem in stale:
        print('  ' + problem)


def check_wiring() -> None:
    engine = (APP / 'GameCompat.swift').read_text(encoding='utf-8')
    view = (APP / 'GameCompatView.swift').read_text(encoding='utf-8')
    library = (APP / 'Library.swift').read_text(encoding='utf-8')
    diagnosis = (APP / 'CompatDiagnosis.swift').read_text(encoding='utf-8')
    log = (APP / 'LogStore.swift').read_text(encoding='utf-8')
    project = (ROOT / 'app/Madeira.xcodeproj/project.pbxproj').read_text(encoding='utf-8')

    require('enum GameCompatibility' in engine and 'struct CompatPlan' in engine, 'the engine file declares the plan and engine')
    require('struct CompatOverrides' in engine, 'the engine carries the per-entry override model')
    require('struct CompatRecipe' in engine and 'var recipes: [String: CompatRecipe]?' in engine,
            'the engine carries the reusable fix library')
    require('struct CompatFallback' in engine and 'struct CompatAlternative' in engine,
            'the engine carries the per-title fallbacks and their alternatives')
    require('enum CompatDiagnosis' in diagnosis and 'enum CompatLogDiagnosis' in diagnosis,
            'the diagnosis file classifies a session')
    require('func diagnosticSnapshot' in log, 'the log store hands the diagnosis a bounded snapshot')
    require('var compat: CompatOverrides?' in library, 'LibraryEntry stores a compatibility override')
    require('var compatAttempt: Int?' in library and 'var compatResult: CompatResult?' in library,
            'LibraryEntry stores which alternative to try and how the last session ended')
    require('mutating func recordCompatibilityOutcome' in library and 'recordCompatibility()' in library,
            'the launch records what the session did')
    require('wine_crash_exit_status' in library.split('func recordCompatibilityOutcome')[1].split('func nextCompatibilityAlternative')[0],
            'the result uses the session exit status')
    require('func applyCompatibility()' in library and 'applyCompatibility()' in library.split('func applyEnvironment()')[1].split('func configureLaunch()')[0],
            'applyEnvironment applies the compatibility plan')
    require('GameCompatibility.plan(' in library and 'GameCompatibility.writeRegistry(' in library,
            'the launch hook resolves and writes the plan')
    require('importedDLLs: LibraryModel.importedDLLs(for:' in library, 'the launch detects dependencies from the game executable imports')
    require('names.formUnion(importNames(file))' in library, 'detection reuses the existing PE import reader')
    require('CompatEnvironmentRegistry.shared.reconcile' in library, 'each launch drops the previous compatibility variables')
    require('GameCompatSection(entry: $entry)' in library, 'game details shows the compatibility section')
    require('struct GameCompatSection' in view, 'the compatibility section exists')
    require('plan.alternatives' in view and 'entry.compatAttempt' in view,
            'the section shows and selects the attempts')
    require('entry.compatResult' in view, 'the section shows the last result')
    require('result.isFailure' in library and 'badge(result.categoriesTitle' in library,
            'the library row shows a badge after a session failed')

    for symbol in ['GameCompat.swift', 'GameCompatView.swift', 'CompatDiagnosis.swift']:
        require(f'{symbol} in Sources' in project, f'{symbol} is in the Xcode Sources phase')
        require(f'/* {symbol} */' in project, f'{symbol} has an Xcode file reference')
    require('compat.json in Resources' in project, 'compat.json is bundled as a resource')
    require('compat.json */ = {isa = PBXFileReference' in project, 'compat.json has an Xcode file reference')
    require("'recipes.json'" in (ROOT / 'build/tools/gen-game-compat.py').read_text(encoding='utf-8'),
            'the generator loads the fix library')


def load_generator():
    """The compatibility database generator, as a module.

    Importing it must not leave a __pycache__ behind in the repository.
    """
    path = ROOT / 'build/tools/gen-game-compat.py'
    spec = importlib.util.spec_from_file_location('gen_game_compat', path)
    module = importlib.util.module_from_spec(spec)
    writing = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writing
    return module


def main() -> int:
    run_swift()
    check_database()
    check_wiring()
    print(f'\n{"FAIL" if failures else "PASS"}: game compatibility ({failures} failure(s))')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
