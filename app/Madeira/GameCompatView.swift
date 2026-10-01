// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 the Madeira contributors
//
// Game details › Compatibility. Shows what Madeira will do for this title
// (GameCompat.swift) and lets the user override the automatic behaviour for
// this game only. The plan is computed off the main thread's critical path:
// the section is one more form row, and the database is small.

import SwiftUI

struct GameCompatSection: View {
    @Binding var entry: LibraryEntry

    @State private var plan = CompatPlan()
    @State private var extraDependencies = ""
    @State private var loaded = false

    private var overrides: CompatOverrides { entry.compat ?? CompatOverrides() }

    private func launch() -> CompatLaunch {
        CompatLaunch(
            executable: (entry.launchRelativePath as NSString).lastPathComponent,
            relativePath: entry.launchRelativePath,
            appid: entry.steamAppID,
            payloadDirectories: LibraryModel.compatPayloadDirectories(),
            overrides: entry.compat)
    }

    private func refresh() {
        plan = GameCompatibility.plan(launch(), database: LibraryModel.compatDatabase())
        extraDependencies = (entry.compat?.extraDependencies ?? []).joined(separator: ", ")
        loaded = true
    }

    private var automatic: Binding<Bool> {
        Binding(get: { entry.compat?.enabled ?? true }, set: { value in
            var updated = entry.compat ?? CompatOverrides()
            updated.enabled = value
            entry.compat = updated
            refresh()
        })
    }

    private var windowsVersion: Binding<String> {
        Binding(get: { entry.compat?.windowsVersion ?? "" }, set: { value in
            var updated = entry.compat ?? CompatOverrides()
            updated.windowsVersion = value.isEmpty ? nil : value
            entry.compat = updated
            refresh()
        })
    }

    private func commitExtras() {
        var updated = entry.compat ?? CompatOverrides()
        let ids = extraDependencies.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            .filter { !$0.isEmpty }
        updated.extraDependencies = ids.isEmpty ? nil : ids
        entry.compat = updated
        refresh()
    }

    var body: some View {
        Section {
            Toggle("Automatic compatibility", isOn: automatic)
            if entry.compat?.enabled == false {
                Text("This game launches exactly as it is, with no compatibility profile or dependencies.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if let profile = plan.matched.first {
                LabeledContent("Profile", value: profile.titleText)
                if let engine = profile.engine, !engine.isEmpty {
                    LabeledContent("Engine", value: engine)
                }
                LabeledContent("Rating", value: plan.matched.map(\.ratingText).joined(separator: ", "))
                if !plan.dependencies.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(plan.dependencies, id: \.id) { dependency in
                            Text("• \(dependency.title ?? dependency.id)")
                                .font(.caption)
                        }
                    }
                }
            } else if loaded {
                Text("No compatibility profile matches this game yet; it launches unchanged. Extra dependencies and a Windows version can still be forced below.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !plan.dllOverrides.isEmpty {
                LabeledContent("DLL overrides", value: plan.dllOverrideString)
                    .font(.caption)
            }
            Picker("Windows version", selection: windowsVersion) {
                Text("Automatic").tag("")
                ForEach(["win10", "win7", "winxp", "winxp64", "win2000", "win8", "vista"], id: \.self) { Text($0).tag($0) }
            }
            TextField("Extra dependencies (comma separated)", text: $extraDependencies)
                .autocorrectionDisabled().textInputAutocapitalization(.never)
                .font(.body.monospaced())
                .onSubmit { commitExtras() }
        } header: {
            Text("Compatibility")
        } footer: {
            VStack(alignment: .leading, spacing: 6) {
                if plan.unsatisfied.isEmpty {
                    Text("Madeira matches the game, prepares its runtimes and configures Wine automatically before it starts.")
                } else {
                    ForEach(plan.unsatisfied, id: \.id) { item in
                        Text("\(item.title): \(item.reason).")
                            .foregroundStyle(.orange)
                    }
                    Text("Native runtime files (Visual C++ DLLs, fonts) can be dropped in Documents/madeira-compat/dlls/ (and dlls/i386/ for 32-bit games) to satisfy them.")
                }
                if !plan.notes.isEmpty {
                    ForEach(plan.notes.prefix(4), id: \.self) { Text($0) }
                }
            }.font(.caption)
        }
        .task { refresh() }
        .onChange(of: entry.compat) { _, _ in refresh() }
    }
}
