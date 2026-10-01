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

// the universal configuration, the general rules and the remedies
let universal = db(#"""
{"schema":1,
 "baseline":{"title":"Any Windows program","windows_version":"win10"},
 "dependencies":{
   "xact":{"title":"XACT","support":"builtin","imports":["xactengine3_7"]},
   "eac":{"title":"Easy Anti-Cheat","kind":"anticheat","category":"anticheat","support":"unsupported",
          "imports":["easyanticheat_x64"]},
   "font":{"title":"Fonts","support":"payload","dlls":["arial.ttf"]},
   "ole-served":{"title":"OLE, on the builtin","support":"builtin","imports":["ole32"],
                 "dll_overrides":{"ole32":"b"}},
   "xaudio-absent":{"title":"XAudio2 2.7","support":"builtin","imports":["xaudio2_7"],
                    "dll_overrides":{"xaudio2_7":"b"}}},
 "recipes":{
   "legacy-d3d":{"title":"Legacy Direct3D configuration","category":"dx9",
                 "registry":[{"hive":"HKCU","key":"Software\\Wine\\AppDefaults\\{app}\\Direct3D",
                              "name":"VideoMemorySize","type":"REG_SZ","value":"1024"}]},
   "no-3d":{"title":"No 3D for this program","category":"launch",
            "registry":[{"hive":"HKCU","key":"Software\\Wine\\AppDefaults\\{app}\\Direct3D",
                         "name":"renderer","type":"REG_SZ","value":"no3d"}]},
   "wave":{"title":"DirectShow wave renderer","category":"audio"}},
 "games":[{"title":"Known","executables":["known.exe"],"windows_version":"win7"}],
 "rules":[
   {"id":"legacy-directdraw","title":"DirectDraw-era Direct3D","when":{"imports":["ddraw","d3d8"]},
    "recipes":["legacy-d3d"]},
   {"id":"helper-no-3d","title":"Helper process, no Direct3D","when":{"executable_contains":["crashhandler"]},
    "recipes":["no-3d"]},
   {"id":"anticheat-eac-files","title":"Easy Anti-Cheat files","when":{"files":["easyanticheat_x64"]},
    "dependencies":["eac"]},
   {"id":"media-foundation-64bit","title":"64-bit Media Foundation","when":{"imports":["mfplat"],"bits":64},
    "env":{"MADEIRA_WG_64BIT":"1"},
    "note":"the media unix side is 32-bit only until the 64-bit module ships"},
   {"id":"never","title":"Never matches","when":{"imports":["nosuchdll"]},"env":{"MADEIRA_UNUSED":"1"}}],
 "remedies":{
   "dx9":{"name":"Legacy Direct3D settings","recipes":["legacy-d3d"]},
   "audio":{"name":"DirectShow wave path","recipes":["wave"]}},
 "wine_modules":["ole32","d3d9","mfplat","user32","dmusic"],
 "wine_modules_64":["ole32","d3d9","user32"],
 "wine_not_shipped":["ir50_32","vulkan-1"],
 "not_built":[{"name":"mfcore","reason":"Windows 8 split the Media Foundation platform in two"}],
 "api_set_prefixes":["api-ms-win-","ext-ms-win-"]}
"""#)

let unknown = GameCompatibility.plan(CompatLaunch(executable: "somegame.exe"), database: universal)
check(unknown.matched.isEmpty && unknown.baselineTitle == "Any Windows program",
      "a program with no profile starts from the universal configuration")
check(unknown.windowsVersion == "win10" && !unknown.isEmpty,
      "the baseline sets the Windows version and makes the plan real")
check(GameCompatibility.summary(unknown).contains { $0.contains("no profile matches") },
      "the summary says no profile matched")
let known = GameCompatibility.plan(CompatLaunch(executable: "known.exe"), database: universal)
check(known.windowsVersion == "win7" && known.matched.first?.title == "Known",
      "a profile overrides the baseline")

let ddraw = GameCompatibility.plan(CompatLaunch(executable: "oldgame.exe", importedDLLs: ["DDRAW.DLL"]), database: universal)
check(ddraw.rules == ["legacy-directdraw"], "a rule applies from the DLL the program imports")
check(ddraw.recipes.contains("legacy-d3d"), "the rule's fix is applied")
check(ddraw.registry.contains { $0.name == "VideoMemorySize"
        && ($0.key?.contains("AppDefaults\\oldgame.exe") ?? false) },
      "a rule's per-application registry value is written for this program")
check(GameCompatibility.summary(ddraw).contains { $0.hasPrefix("rules: ") }, "the summary names the rules")
let modern = GameCompatibility.plan(CompatLaunch(executable: "modern.exe", importedDLLs: ["d3d11.dll"]), database: universal)
check(modern.rules.isEmpty && modern.environment["MADEIRA_UNUSED"] == nil,
      "a rule whose conditions do not hold does not apply")

let files = GameCompatibility.plan(CompatLaunch(executable: "game.exe", files: ["easyanticheat_x64.dll", "assets"]),
                                   database: universal)
check(files.rules.contains("anticheat-eac-files") && files.unsatisfied.contains { $0.id == "eac" },
      "a file beside the program selects the rule that names the component")
check(files.isFatal && files.alternatives.count == 1,
      "an anti-cheat title keeps one attempt: no configuration helps")
let helper = GameCompatibility.plan(CompatLaunch(executable: "UnityCrashHandler64.exe"), database: universal)
check(helper.rules.contains("helper-no-3d"), "a rule can match the program's name alone")

let mf64 = GameCompatibility.plan(CompatLaunch(executable: "player.exe", importedDLLs: ["mfplat.dll"], bits: 64),
                                  database: universal)
check(mf64.environment["MADEIRA_WG_64BIT"] == "1", "a 64-bit Media Foundation title gets the runtime's media side")
let mf32 = GameCompatibility.plan(CompatLaunch(executable: "player.exe", importedDLLs: ["mfplat.dll"], bits: 32),
                                  database: universal)
check(mf32.environment["MADEIRA_WG_64BIT"] == nil, "the same import on 32-bit needs nothing: the default applies")

let dx9Failure = GameCompatibility.plan(CompatLaunch(executable: "unknown.exe", importedDLLs: ["ddraw.dll"],
                                                 previousFailure: ["dx9"]), database: universal)
check(dx9Failure.alternatives.count == 2 && dx9Failure.alternatives[1].name == "Legacy Direct3D settings",
      "a failed category adds the attempt that answers it")
let retry = GameCompatibility.plan(CompatLaunch(executable: "unknown.exe", importedDLLs: ["ddraw.dll"],
                                                previousFailure: ["dx9"]), database: universal, alternative: 1)
check(retry.remedy == "dx9" && retry.recipes.contains("legacy-d3d"), "the remedy is applied on the second attempt")
check(retry.notes.contains { $0.contains("retry") }, "the retry says why it happened")
let noRemedy = GameCompatibility.plan(CompatLaunch(executable: "unknown.exe", previousFailure: ["save_path"]),
                                      database: universal)
check(noRemedy.alternatives.count == 1, "a category with no remedy adds no attempt")
let fatal = GameCompatibility.plan(CompatLaunch(executable: "online.exe", files: ["easyanticheat_x64"],
                                                previousFailure: ["dx9"]), database: universal)
check(fatal.isFatal && fatal.alternatives.count == 1, "a fatal reason collapses the ladder even when a remedy exists")
let outOfRange = GameCompatibility.plan(CompatLaunch(executable: "unknown.exe", previousFailure: ["dx9"]),
                                     database: universal, alternative: 9)
check(outOfRange.alternativeIndex == outOfRange.alternatives.count - 1, "an out-of-range attempt clamps")

let loose = GameCompatibility.plan(CompatLaunch(executable: "odd.exe",
                                                importedDLLs: ["ole32.dll", "ir50_32.dll", "thirdparty.dll", "ddraw.dll"]),
                                   database: universal)
check(loose.unavailableModules == ["ir50_32"], "a module the runtime does not ship is named as such")
check(loose.unaccountedImports == ["thirdparty"], "an import nothing accounts for is named")
check(loose.rules.contains("legacy-directdraw"), "an import a rule answers for applies the rule")
check(loose.notes.contains { $0.contains("ir50_32") }, "the report says which module is missing")
check(loose.notes.allSatisfy { !$0.contains("ddraw") }, "an import that is answered is not reported as unaccounted")

// A 64-bit program cannot load a module the 32-bit build has: the two farms are
// not the same set, and the report has to say which half of the runtime is short.
let half64 = GameCompatibility.plan(CompatLaunch(executable: "x64.exe", importedDLLs: ["dmusic.dll"],
                                                 bits: 64), database: universal)
check(half64.unavailableModules == ["dmusic"], "a module only the 32-bit side carries is missing for a 64-bit launch")
check(half64.notes.contains { $0.contains("the 64-bit runtime does not ship") && $0.contains("dmusic") },
      "the note names the 64-bit runtime rather than the runtime")
let same32 = GameCompatibility.plan(CompatLaunch(executable: "x86.exe", importedDLLs: ["dmusic.dll"],
                                                 bits: 32), database: universal)
check(same32.unavailableModules.isEmpty, "the same import is served on the 32-bit side")
let mediaRule64 = GameCompatibility.plan(CompatLaunch(executable: "player.exe", importedDLLs: ["mfplat.dll"],
                                                      bits: 64), database: universal)
check(mediaRule64.rules.contains("media-foundation-64bit")
      && mediaRule64.notes.contains { $0.contains("32-bit only") },
      "the rule that answers a 64-bit media import carries its own note, and that note is the report")
let never = GameCompatibility.plan(CompatLaunch(executable: "any.exe", importedDLLs: ["mfcore.dll"]),
                                   database: universal)
check(never.unavailableModules == ["mfcore"], "a component Wine never built is reported as unavailable")
check(never.notes.contains { $0.contains("Windows 8 split the Media Foundation platform in two") },
      "the reason a component can never be served is carried into the report")
let apiSets = GameCompatibility.plan(CompatLaunch(executable: "modern.exe",
    importedDLLs: ["api-ms-win-core-synch-l1-1-0.dll", "api-ms-win-crt-runtime-l1-1-0.dll",
                   "ext-ms-win-ntuser-window-l1-1-0.dll"]), database: universal)
check(apiSets.unaccountedImports.isEmpty && apiSets.unavailableModules.isEmpty,
      "an API set name is resolved by the loader, so it is neither missing nor unaccounted")
let blank = GameCompatibility.emptyDatabase()
check(blank.isAPISet("api-ms-win-core-heap-l1-1-0.dll") && blank.providedModules(nil).isEmpty,
      "with no database at all, a loader-resolved name is still not reported")

// A payload the title ships itself is not something the user has to supply.
let payload = db(#"{"schema":1,"dependencies":{"dx9":{"title":"DirectX 9","support":"payload","dlls":["d3dx9_43.dll"]}},"games":[{"title":"P","executables":["p.exe"],"dependencies":["dx9"]}]}"#)
check(GameCompatibility.plan(CompatLaunch(executable: "p.exe"), database: payload).unsatisfied.map(\.id) == ["dx9"],
      "a payload nobody supplied is reported with its file names")
check(GameCompatibility.plan(CompatLaunch(executable: "p.exe", files: ["D3DX9_43.DLL"]), database: payload).unsatisfied.isEmpty,
      "a payload the title ships beside itself is satisfied")

// A builtin pin is a claim about this runtime, so it is only written when the
// runtime can back it; otherwise it would hide the title's own copy.
let servedPin = GameCompatibility.plan(CompatLaunch(executable: "game.exe", importedDLLs: ["ole32.dll"]),
                                       database: universal)
check(servedPin.dllOverrides["ole32"] == "b", "a pin the runtime can honour is kept")
let deadPin = GameCompatibility.plan(CompatLaunch(executable: "game.exe", importedDLLs: ["xaudio2_7.dll"]),
                                     database: universal)
check(deadPin.dllOverrides["xaudio2_7"] == nil, "a pin for a module the runtime does not ship is dropped")
check(deadPin.notes.contains { $0.contains("no builtin pin for") },
      "and the plan says why, instead of writing an override that cannot resolve")
check(deadPin.unaccountedImports == ["xaudio2_7"],
      "the import is left to the title rather than answered by a component the architecture cannot honour")

// An unsupported component is itself the answer, so its import is not repeated
// as a module the runtime is short of.
let anticheat = GameCompatibility.plan(CompatLaunch(executable: "game.exe",
                                                    importedDLLs: ["EasyAntiCheat_x64.dll"]),
                                       database: universal)
check(anticheat.unsatisfied.map(\.id) == ["eac"], "a component that cannot work here is reported")
check(anticheat.unaccountedImports.isEmpty && anticheat.unavailableModules.isEmpty,
      "and its import is not reported a second time as a module nothing answers")

// the architecture a rule matches on, read from a PE header
func peImage(machine: Int) -> Data {
    var bytes = [UInt8](repeating: 0, count: 0x80)
    bytes[0] = 0x4d; bytes[1] = 0x5a                       // "MZ"
    bytes[0x3c] = 0x40                                     // e_lfanew
    bytes[0x40] = 0x50; bytes[0x41] = 0x45                 // "PE"
    bytes[0x44] = UInt8(machine & 0xff); bytes[0x45] = UInt8((machine >> 8) & 0xff)
    return Data(bytes)
}
check(GameCompatibility.machineBits(peImage(machine: 0x8664)) == 64,
      "a 64-bit PE image is read as 64-bit")
check(GameCompatibility.machineBits(peImage(machine: 0x014c)) == 32,
      "a 32-bit PE image is read as 32-bit")
check(GameCompatibility.machineBits(peImage(machine: 0x01c4)) == 32,
      "an ARM PE image is read as 32-bit")
check(GameCompatibility.machineBits(peImage(machine: 0x1234)) == nil
      && GameCompatibility.machineBits(Data([0x4d, 0x5a])) == nil
      && GameCompatibility.machineBits(Data("not a program".utf8)) == nil,
      "an unknown machine, a truncated header and a non-PE file are all no answer")

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
    check(gpu.dllOverrides["d3dcompiler_46"] == "b", "an older shader compiler is pinned to Wine's builtin")
    check(gpu.dependencies.map(\.id).contains("d3d10-d3d11"),
          "Direct3D 11 is recognised as Madeira's own DXMT path")
    check(gpu.unsatisfied.map(\.id) == ["vendor-gpu-libraries"],
          "the vendor GPU libraries are reported as unsupported, and nothing else is missing")
    check(gpu.dllOverrides["nvapi64"] == nil && gpu.dllOverrides["nvngx_dlss"] == nil,
          "no builtin pin is written for a vendor library the runtime does not have: it would hide the title's own file")
    let d3dcompile = GameCompatibility.plan(CompatLaunch(executable: "sample.exe",
        importedDLLs: ["d3dcompiler_44.dll", "dxil.dll"]), database: real)
    check(d3dcompile.unavailableModules.contains("d3dcompiler_44") && d3dcompile.unavailableModules.contains("dxil"),
          "a shader compiler Wine never shipped is reported as unavailable, not claimed as a builtin")
    let media64 = GameCompatibility.plan(CompatLaunch(executable: "sample.exe",
        importedDLLs: ["mfplat.dll", "quartz.dll", "xaudio2_7.dll"], bits: 64), database: real)
    check(media64.rules.contains("media-unix-side-64bit") && media64.environment["MADEIRA_WG_64BIT"] == "1",
          "a 64-bit title that decodes media gets the runtime's media switch")
    check(media64.notes.contains { $0.contains("winegstreamer") },
          "and is told what the 64-bit farm is missing rather than failing with no explanation")
    let vulkan = GameCompatibility.plan(CompatLaunch(executable: "sample.exe", importedDLLs: ["vulkan-1.dll"]), database: real)
    check(vulkan.dependencies.map(\.id).contains("vulkan") && vulkan.unsatisfied.isEmpty,
          "a Vulkan import is classified without being called a missing file")
    check(real.baseline?.windowsVersion == "win10", "the bundled database carries the universal baseline")
    let unprofiled = GameCompatibility.plan(CompatLaunch(executable: "no-such-title.exe"), database: real)
    check(unprofiled.matched.isEmpty && unprofiled.windowsVersion == "win10",
          "an unprofiled executable still gets the universal configuration")
    check(real.ruleList.count >= 8 && real.remedyTable.count >= 6,
          "the bundled database carries the general rules and the remedies")
    check(real.providedModules(nil).contains("ole32") && real.providedModules(nil).contains("d3d11"),
          "the module list is what this runtime provides (Wine's, plus the names DXMT answers for)")
    check(real.absentModules(nil).contains("ir50_32") && !real.absentModules(nil).contains("d3d11"),
          "the modules the iOS build leaves out are listed apart, and not the DXMT-owned ones")
    let farm64 = real.providedModules(64)
    check(farm64.contains("d3d11") && !farm64.contains("quartz") && !farm64.contains("winegstreamer")
          && real.providedModules(nil).contains("quartz"),
          "the 64-bit farm is its own set: the media and DirectShow modules are 32-bit only here")
    check(real.absentModules(64).contains("quartz") && real.absentModules(64).contains("dinput8") == false,
          "a module the 64-bit farm does not carry is absent for a 64-bit launch")
    check(real.wineNotBuilt?.contains { $0.name == "mfcore" } == true
          && real.notBuiltReason("MFCore.dll")?.isEmpty == false,
          "the components Wine never built are carried with the reason the report quotes")
    let odd = GameCompatibility.plan(CompatLaunch(executable: "sample.exe",
        importedDLLs: ["ole32.dll", "madeup_thing.dll"]), database: real)
    check(odd.unaccountedImports == ["madeup_thing"], "a runtime module is not reported; an unknown one is")
    check(real.isAPISet("api-ms-win-crt-stdio-l1-1-0.dll") && !real.isAPISet("kernel32.dll"),
          "the bundled database carries the API set prefixes, and a real module name is not one")
    let modern = GameCompatibility.plan(CompatLaunch(executable: "modern.exe",
        importedDLLs: ["api-ms-win-core-synch-l1-1-0.dll", "api-ms-win-core-file-l1-2-0.dll"]), database: real)
    check(modern.unaccountedImports.isEmpty && modern.unavailableModules.isEmpty,
          "a modern program whose extra imports are all API sets is reported clean")
    let eacFiles = GameCompatibility.plan(CompatLaunch(executable: "game.exe", files: ["EasyAntiCheat_x64.dll"]),
                                          database: real)
    check(eacFiles.rules.contains("anticheat-eac-files") && eacFiles.isFatal,
          "the anti-cheat files beside a program stop the retry ladder")
    let retryable = GameCompatibility.plan(CompatLaunch(executable: "sample.exe", importedDLLs: ["ddraw.dll"],
                                                        previousFailure: ["dx9"]), database: real)
    check(retryable.alternatives.count >= 2 && retryable.rules.contains("legacy-directdraw"),
          "a DirectDraw title has its fix applied and a remedy to try next")
    let steamStub = GameCompatibility.plan(CompatLaunch(executable: "sample.exe", files: ["steam_api64.dll"]), database: real)
    check(steamStub.rules.contains("steam-drm-stub") && steamStub.dependencies.map(\.id).contains("steamworks"),
          "a Steam-wrapped program is pointed at the Steam client rather than at DRM support")
    let gfwl = GameCompatibility.plan(CompatLaunch(executable: "sample.exe", importedDLLs: ["xlive.dll"]), database: real)
    check(gfwl.dependencies.map(\.id).contains("gfwl") && gfwl.unsatisfied.contains { $0.id == "gfwl" },
          "a Games for Windows LIVE title is told what is missing rather than left to fail silently")
    check(!gfwl.isFatal, "an uninstallable client is reported without stopping the retry ladder")
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
    # A "builtin" claim has to name a module the runtime ships, or an API set
    # the loader resolves. Otherwise the component looks answered while nothing
    # answers it, which is the failure this list exists to catch.
    modules = set(data.get('wine_modules') or [])
    prefixes = tuple(data.get('api_set_prefixes') or [])
    unbacked = []
    for dep_id, dep in dependencies.items():
        if dep.get('support') != 'builtin':
            continue
        names = [name.lower() for name in dep.get('imports') or []]
        names = [name[:-4] if name.endswith('.dll') else name for name in names]
        if not names:
            continue
        if not any(name in modules or name.startswith(prefixes) for name in names):
            unbacked.append(f'{dep_id}: {names}')
    require(not unbacked, 'every builtin claim names a module the runtime ships')
    for problem in unbacked[:10]:
        print('  ' + problem)
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

    # Stage 3: what makes a program nobody has profiled work. The baseline, the
    # general rules, the remedies for a failed session, and the list of modules
    # this runtime actually provides.
    baseline = data.get('baseline') or {}
    rules = data.get('rules') or []
    remedies = data.get('remedies') or {}
    modules = data.get('wine_modules') or []
    modules_64 = data.get('wine_modules_64') or []
    not_shipped = data.get('wine_not_shipped') or []
    not_built = data.get('not_built') or []
    valid_categories = {'dependency', 'dll', 'registry', 'dx9', 'dx11', 'dx12', 'media', 'audio',
                        'input', 'wine_fex', 'drm', 'anticheat', 'save_path', 'launch', 'other'}
    require(bool(baseline.get('title')) and baseline.get('windows_version'),
            'the database carries the universal baseline')
    require(len(rules) >= 8, f'the general rules cover what a program is, not only which title it is ({len(rules)})')
    require(len(remedies) >= 6, f'a failed session has a remedy to try next ({len(remedies)})')
    require(len(modules) >= 400, f'the runtime module list is present ({len(modules)})')
    require('ir50_32' in not_shipped and 'ir50_32' not in modules,
            'the modules the iOS build leaves out are listed apart from the ones it provides')
    require(len(modules_64) >= 80 and len(set(modules) - set(modules_64)) >= 300,
            f'the 64-bit farm is its own, smaller set ({len(modules_64)} names to the 32-bit build\'s {len(modules)})')
    require({'quartz', 'winegstreamer', 'wmvcore', 'dinput8'} & set(modules_64) == {'dinput8'},
            'the media and DirectShow modules are 32-bit only in this build, and the report says so per launch')
    built_never = {(entry or {}).get('name', '').lower() for entry in not_built}
    require({'mfcore', 'd3dcompiler_44', 'd3dcompiler_45'} <= built_never,
            'the names Wine has never built in any architecture are listed, with their reasons')
    require(all((entry or {}).get('reason') for entry in not_built)
            and not (built_never & (set(modules) | set(modules_64))),
            'each never-built name carries the reason the report quotes, and none is also claimed as provided')
    # An override that pins the builtin alone cannot resolve when the runtime has
    # no builtin of that name, and it stops the title's own copy from loading.
    # The import sanitiser drops what it finds; this keeps the database clean. An
    # API set name is the exception: the loader resolves it to the module
    # implementing the contract, so pinning it is how the UCRT recipe keeps the
    # contracts inside the prefix.
    api_set_prefixes = [str(prefix).lower() for prefix in data.get('api_set_prefixes') or []]
    def serves(name: str) -> bool:
        return name in modules or name in modules_64 \
            or any(name.startswith(prefix) for prefix in api_set_prefixes)
    named = list(dependencies.items()) + list(recipes.items()) \
        + [(game.get('title') or str(index), game) for index, game in enumerate(games)]
    for owner, block in named:
        for name, order in (block.get('dll_overrides') or {}).items():
            require(name == name.lower().lstrip('*'), f'{owner}: override {name!r} is not a module name')
            if set(t for t in order.split(',') if t) == {'b'}:
                require(serves(name), f'{owner}: {name}=b pins a module the runtime does not ship')
    general: list[str] = []
    for rule in rules:
        if not rule.get('id') or not rule.get('when'):
            general.append(f'rule {rule.get("id")!r}: no id or no conditions')
        for name in (rule.get('when', {}).get('imports') or []) + (rule.get('when', {}).get('files') or []):
            if name != name.lower() or name.endswith('.dll'):
                general.append(f'rule {rule.get("id")}: {name!r} is not normalised')
        for dep_id in rule.get('dependencies', []):
            if dep_id not in dependencies:
                general.append(f'rule {rule.get("id")}: unknown dependency {dep_id}')
        for recipe in rule.get('recipes', []):
            if recipe not in recipes:
                general.append(f'rule {rule.get("id")}: unknown recipe {recipe}')
        for token in (rule.get('dll_overrides') or {}).values():
            if token not in valid_tokens:
                general.append(f'rule {rule.get("id")}: token {token!r}')
    for category, remedy in remedies.items():
        if category not in valid_categories:
            general.append(f'remedy {category!r}: not a diagnosis category')
        for recipe in remedy.get('recipes', []):
            if recipe not in recipes:
                general.append(f'remedy {category}: unknown recipe {recipe}')
        for dep_id in remedy.get('dependencies', []):
            if dep_id not in dependencies:
                general.append(f'remedy {category}: unknown dependency {dep_id}')
    require(not general, 'the rules and remedies reference only known components and recipes')
    for problem in general[:10]:
        print('  ' + problem)

    # Every DLL named in the Stage 3 audit is answered: by the catalogue, by a
    # general rule, or by the runtime itself. A name Wine has never built in any
    # architecture (mfcore, the DirectX SDK compilers) is answered too, because
    # the report carries it with the reason and the title is told to bring its
    # own copy. Silence here means a game that imports it gets no component, no
    # fix and no note.
    answered = imports | {name for rule in rules
                          for name in (rule.get('when', {}).get('imports') or [])
                          + (rule.get('when', {}).get('files') or [])}
    answered |= {name.lower() for name in modules}
    answered |= {name.lower() for name in modules_64}
    answered |= {(entry or {}).get('name', '').lower() for entry in (data.get('not_built') or [])}
    for name in ['easyanticheat', 'easyanticheat_x64', 'beclient', 'beclient_x64', 'vmprotect', 'themida',
                 'dstorage', 'dstoragecore', 'opengl32', 'glu32', 'mfcore', 'mfreadwrite', 'eossdk',
                 'eossdk-win64-shipping', 'upc_r2_loader64', 'uplay_r1_loader', 'discord_game_sdk',
                 'physxcore', 'physxloader', 'xactengine3_7', 'd3d8', 'ddraw', 'd3drm', 'quartz',
                 'devenum', 'dinput8', 'xinput1_3', 'gfwlivesetup', 'xlive', 'binkw32']:
        require(name in answered, f'{name} is answered by a component, a rule or the runtime')


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

    # Stage 3 wiring: the universal configuration, the rules, the remedies and
    # the module list reach the engine, and the launcher hands them what they
    # match on.
    generator = (ROOT / 'build/tools/gen-game-compat.py').read_text(encoding='utf-8')
    require("'baseline.json'" in generator and "'rules.json'" in generator and "'wine-modules.json'" in generator,
            'the generator loads the baseline, the rules and the module list')
    require('def validate_universal' in generator and "'wine_modules'" in generator,
            'the generator validates what applies to every launch and carries it into the database')
    require('struct CompatRule' in engine and 'struct CompatRuleWhen' in engine and 'var wineModules: [String]?' in engine,
            'the engine models the rules, their conditions and the module list')
    require('func remedyAttempts' in engine and 'var unaccountedImports: [String]' in engine,
            'the engine retries with a remedy and reports what nothing accounts for')
    require('func mergeBaseline' in engine and 'rules(database, launch)' in engine,
            'the engine merges the baseline first and the matching rules over it')
    require('files: LibraryModel.folderNames(for:' in library and 'bits: LibraryModel.programBits(for:' in library,
            'the launch hands the rules the files beside the program and its architecture')
    require('previousFailure: compatResult?.categories' in library,
            'the launch remembers the categories the last session failed with')
    require('static func folderNames' in library and 'static func programBits' in library,
            'the program folder and the architecture of its programs are read, not guessed')
    require('GameCompatibility.machineBits(header)' in library and 'static func machineBits(_ image: Data)' in engine,
            'the launcher reads the PE header and the engine parses it, so both halves are testable')

    module_tool = ROOT / 'build/tools/gen-wine-modules.py'
    require(module_tool.exists(), 'the module list has its own generator')
    if module_tool.exists():
        tool = load_generator(module_tool, 'gen_wine_modules')
        require('ir50_32' in tool.not_shipped_from_build(tool.BUILD_SCRIPT),
                'the iOS build script is the source of the modules that are not shipped')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'build.sh'
            script.write_text('SKIP_REASON=(\n'
                              '  "ir50_32.dll|ir50_32=Indeo codec"\n'
                              '  "vulkan-1.dll=no Vulkan"\n'
                              '  "d3d9=d3d9 is DXMT\'s"\n'
                              '  "not a module name!"\n'
                              ')\n', encoding='utf-8')
            left_out = set(tool.not_shipped_from_build(script))
            require(left_out == {'ir50_32', 'vulkan-1'},
                    'the skip list is read as module names, and a DXMT-owned one is a provision, not a gap')
            configure = Path(tmp) / 'configure'
            configure.write_text('# Generated by GNU Autoconf 2.72 for Wine 11.18\n'
                                 'ac_dlls="dlls/ole32/Makefile.in dlls/d3d9/Makefile.in '
                                 'dlls/mfplat/Makefile.in dlls/ddraw/Makefile.in dlls/win16.dll16/Makefile.in"\n',
                                 encoding='utf-8')
            for name in ('ole32', 'd3d9', 'mfplat', 'ddraw'):
                (Path(tmp) / 'dlls' / name).mkdir(parents=True)
                (Path(tmp) / 'dlls' / name / 'Makefile.in').write_text('', encoding='utf-8')
            modules, version = tool.modules_from_configure(configure)
            require(version == '11.18' and set(modules) == {'ole32', 'd3d9', 'mfplat', 'ddraw'},
                    'the module list and the Wine version come from a configure')
            provided = [name for name in modules if name not in left_out]
            require(provided == modules, 'a module is provided unless the build leaves it out')


def load_generator(path: Path | None = None, name: str = 'gen_game_compat'):
    """A build tool, as a module.

    Importing it must not leave a __pycache__ behind in the repository.
    """
    path = path or ROOT / 'build/tools/gen-game-compat.py'
    spec = importlib.util.spec_from_file_location(name, path)
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
