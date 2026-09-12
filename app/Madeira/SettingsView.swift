import SwiftUI

/// Everything that used to require writing a `madeira-*.txt` file by hand.
///
/// The screen is a front-end for the engine's existing override channel rather
/// than a second configuration system: each control writes the same file the
/// launch sequence already reads, so the Files app stays a valid escape hatch
/// and nothing here duplicates parsing rules that already have tests.
struct SettingsView: View {
    @ObservedObject private var store = SettingsStore.shared
    @Environment(\.dismiss) private var dismiss

    @State private var customWidth = ""
    @State private var customHeight = ""

    var body: some View {
        NavigationStack {
            Form {
                displaySection
                graphicsSection
                controlsSection
                performanceSection
                remoteSection
                advancedSections
                overrideSection
                resetSection
                aboutSection
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.fontWeight(.semibold)
                }
            }
            .onAppear(perform: loadCustomFields)
        }
    }

    // MARK: - Display

    private var displaySection: some View {
        Section {
            Picker("Preset", selection: presetBinding) {
                ForEach(presetOptions) { preset in
                    Text(preset.label).tag(preset.id)
                }
            }

            HStack {
                Text("Width")
                Spacer()
                TextField("auto", text: $customWidth)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 96)
                    .onChange(of: customWidth) { _, _ in commitCustomSize() }
            }
            HStack {
                Text("Height")
                Spacer()
                TextField("auto", text: $customHeight)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 96)
                    .onChange(of: customHeight) { _, _ in commitCustomSize() }
            }

            Picker("Frame rate", selection: $store.settings.frameRate) {
                Text("Engine default").tag(FrameRateMode?.none)
                ForEach(FrameRateMode.allCases) { mode in
                    Text(mode.label).tag(FrameRateMode?.some(mode))
                }
            }
        } header: {
            Text("Display")
        } footer: {
            Text("\(frameRateDetail). Clear both size fields to leave the "
                + "desktop at the size the engine chooses for this device "
                + "(\(ResolutionPolicy.minDimension)–\(ResolutionPolicy.maxDimension) per side).")
        }
    }

    private var frameRateDetail: String {
        guard let mode = store.settings.frameRate else {
            return "The engine chooses its own pacing unless you pick one here"
        }
        return mode.detail
    }

    /// The preset list, plus the current size when it is not one of them, so the
    /// picker always has a selected row instead of showing nothing.
    private var presetOptions: [ResolutionPreset] {
        guard isCustomResolution else { return ResolutionPolicy.presets }
        return ResolutionPolicy.presets
            + [ResolutionPreset(width: store.settings.width, height: store.settings.height)]
    }

    private var isCustomResolution: Bool {
        store.settings.width > 0
            && !ResolutionPolicy.presets.contains {
                $0.width == store.settings.width && $0.height == store.settings.height
            }
    }

    private var presetBinding: Binding<String> {
        Binding(
            get: {
                if let match = ResolutionPolicy.presets.first(where: {
                    $0.width == store.settings.width && $0.height == store.settings.height
                }) {
                    return match.id
                }
                return "\(store.settings.width)x\(store.settings.height)"
            },
            set: { id in
                guard let preset = ResolutionPolicy.presets.first(where: { $0.id == id }) else { return }
                store.settings.width = preset.width
                store.settings.height = preset.height
                loadCustomFields()
            }
        )
    }

    private func loadCustomFields() {
        customWidth = store.settings.width > 0 ? String(store.settings.width) : ""
        customHeight = store.settings.height > 0 ? String(store.settings.height) : ""
    }

    /// Commit on every keystroke, but only once both sides are a size the guest
    /// will accept. A half-typed number therefore leaves the previous value
    /// alone instead of writing something out of range to disk.
    private func commitCustomSize() {
        let width = Int(customWidth.trimmingCharacters(in: .whitespaces)) ?? 0
        let height = Int(customHeight.trimmingCharacters(in: .whitespaces)) ?? 0
        if width == 0 && height == 0 {
            store.settings.width = 0
            store.settings.height = 0
            return
        }
        guard let clamped = ResolutionPolicy.clamped(width: width, height: height) else { return }
        store.settings.width = clamped.width
        store.settings.height = clamped.height
    }

    // MARK: - Graphics

    private var graphicsSection: some View {
        Section {
            Toggle(isOn: $store.settings.clampCompressedMips) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Clamp compressed mip levels")
                    Text("Sets d3d11.mipClampBC. This GPU cannot sample block-compressed "
                        + "textures directly, so DXMT expands them at 2–8× their shipped "
                        + "size; clamping bounds how much of the mip chain is expanded.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } header: {
            Text("Graphics")
        } footer: {
            Text("Leave off unless a title is running out of memory. It trades texture "
                + "sharpness at distance for headroom.")
        }
    }

    // MARK: - Controls

    /// The on-screen controller. Deliberately its own section rather than part
    /// of Graphics: this is the thing that replaces the system keyboard, so it
    /// has to be findable by someone who is trying to play, not to tune.
    private var controlsSection: some View {
        Section {
            Picker("On-screen controller", selection: $store.settings.virtualPad) {
                ForEach(VirtualPadMode.allCases, id: \.self) { mode in
                    Text(mode.label).tag(mode)
                }
            }
            if store.settings.virtualPad != .off {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Visibility")
                        Spacer()
                        Text("\(Int(VirtualPadLayout.opacityClamped(store.settings.virtualPadOpacity) * 100))%")
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                    Slider(value: $store.settings.virtualPadOpacity,
                           in: VirtualPadLayout.minOpacity...1)
                }
            }
        } header: {
            Text("Controls")
        } footer: {
            Text("A PlayStation-style touch pad — d-pad, \u{25B3}\u{25CB}\u{2715}\u{25A1}, "
                + "shoulders and two sticks — mapped onto the same keyboard and mouse "
                + "input a paired controller uses, so mouse-look works on the right "
                + "stick. Rebind it in madeira-gamepad.txt. \"In game\" shows it while "
                + "a game is on screen; the small \u{2715} in its middle turns it off.")
        }
    }

    // MARK: - Performance

    /// The profile picker reads back as a *comparison* over the four fields it
    /// owns rather than a stored choice, so it can never disagree with them:
    /// change the desktop size by hand and the row falls to Custom on its own.
    private var profileBinding: Binding<PerformanceProfile?> {
        Binding(
            get: { store.settings.matchingProfile },
            set: { if let picked = $0 { store.settings.apply(picked) } }
        )
    }

    private var profileDetail: String {
        store.settings.matchingProfile?.detail
            ?? "A combination no preset produces. Choosing one replaces the desktop "
            + "size, compressed-mip clamping and Wine logging together; every other "
            + "setting here is left alone."
    }

    private var performanceSection: some View {
        Section {
            Picker("Profile", selection: profileBinding) {
                ForEach(PerformanceProfile.allCases) { profile in
                    Text(profile.label).tag(PerformanceProfile?.some(profile))
                }
                if store.settings.matchingProfile == nil {
                    Text("Custom").tag(PerformanceProfile?.none)
                }
            }

            Toggle(isOn: $store.settings.x87FastMath) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("x87 fast math")
                    Text("Translates x87 as 64-bit doubles instead of 80-bit "
                        + "extended precision. Often a large win in titles from the "
                        + "2000s, which do their own math in x87. FEX's own "
                        + "description: \"reduces emulation accuracy and may result "
                        + "in rendering bugs\" — so it is off in every profile and "
                        + "only on if you ask.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Toggle(isOn: $store.settings.disableWineLogging) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Silence Wine's error log")
                    Text("Writes WINEDEBUG=-all. Every enabled channel formats and "
                        + "writes a line, and this stack takes page-protection "
                        + "failures and SEH on hot paths, so the error channel is "
                        + "not free. The app's own log is unaffected.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Picker("JIT pool", selection: $store.settings.poolMB) {
                Text("Automatic").tag(0)
                ForEach(MadeiraSettings.poolChoices.filter { $0 > 0 }, id: \.self) { mb in
                    Text("\(mb) MB").tag(mb)
                }
            }
        } header: {
            Text("Performance")
        } footer: {
            Text(profileDetail + "\n\nThe JIT pool is separate: automatic sizes the "
                + "translation cache from this device's memory budget "
                + "(\(DeviceCapabilities.recommendedPoolMB()) MB here). A larger pool "
                + "recompiles less; it also occupies more of the memory limit. Madeira "
                + "shrinks the pool to fit the address space if the full size will not "
                + "place, so the run always starts.")
        }
    }

    // MARK: - Remote Metal

    private var remoteSection: some View {
        Section {
            TextField("Host (address:port)", text: $store.settings.remoteHost)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            TextField("Token", text: $store.settings.remoteToken)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
        } header: {
            Text("Remote Metal")
        } footer: {
            Text("Renders through a Metal daemon on another machine instead of this "
                + "device. The mode is decided once per launch. Both fields are needed; "
                + "the token is stored in plain text alongside the rest of the settings.")
        }
    }

    // MARK: - Advanced

    @ViewBuilder
    private var advancedSections: some View {
        ForEach(EngineSwitch.Group.allCases) { group in
            let items = EngineSwitches.all.filter { $0.group == group }
            if !items.isEmpty {
                Section {
                    ForEach(items) { item in
                        switchRow(item)
                    }
                } header: {
                    Text(group.rawValue)
                } footer: {
                    if items.contains(where: { $0.experimental }) {
                        Text("Switches marked EXPERIMENTAL are not proven. Leave them off "
                            + "unless you are reproducing a specific result.")
                    }
                }
            }
        }
    }

    private func switchRow(_ item: EngineSwitch) -> some View {
        Toggle(isOn: switchBinding(item)) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(item.title)
                    if item.experimental {
                        Text("EXPERIMENTAL")
                            .font(.system(size: 9, weight: .bold))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(AppTheme.warn.opacity(0.2)))
                            .foregroundStyle(AppTheme.warn)
                    }
                }
                Text(item.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func switchBinding(_ item: EngineSwitch) -> Binding<Bool> {
        Binding(
            get: { store.settings.switches.contains(item.id) },
            set: { on in
                if on {
                    store.settings.switches.insert(item.id)
                } else {
                    store.settings.switches.remove(item.id)
                }
            }
        )
    }

    // MARK: - Overrides in effect

    private var overrideSection: some View {
        let written = store.settings.overrideFiles.compactMap { $0.body == nil ? nil : $0.name }
        return Section {
            if written.isEmpty {
                Text("None — the engine's own defaults are in effect.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(written, id: \.self) { name in
                    Text(name)
                        .font(.system(.caption, design: .monospaced))
                }
            }
        } header: {
            Text("Override files")
        } footer: {
            Text("These are the files in the app's Documents folder that the engine reads "
                + "at launch. They can still be edited by hand from the Files app.")
        }
    }

    private var resetSection: some View {
        Section {
            Button(role: .destructive) {
                store.reset()
                loadCustomFields()
            } label: {
                Text("Reset all settings")
            }
        }
    }

    // MARK: - About

    private var aboutSection: some View {
        Section {
            LabeledContent("Device", value: DeviceCapabilities.machine)
            LabeledContent("Model", value: DeviceCapabilities.model)
            LabeledContent("Memory budget",
                           value: DeviceCapabilities.availableMemoryBytes > 0
                               ? "\(DeviceCapabilities.availableMemoryBytes / (1024 * 1024)) MB"
                               : "unlimited")
            LabeledContent("Recommended pool",
                           value: "\(DeviceCapabilities.recommendedPoolMB()) MB")
        } header: {
            Text("About")
        } footer: {
            Text("Pool placement is chosen by the kernel at launch. If a run cannot find "
                + "room below the guest address window it says so on the home screen "
                + "instead of closing the app.")
        }
    }
}
