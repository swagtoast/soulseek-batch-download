import AppKit
import Foundation

struct SetupResult: Codable {
    let confirmed: Bool
    let username: String
    let password: String
    let output_dir: String
    let convert_format: String
}

func normalizeFormat(_ raw: String) -> String {
    switch raw.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ". ")) {
    case "aiff", "aif", "1", "true", "yes":
        return "aiff"
    case "mp3", "mp3_320", "mp3-320", "320":
        return "mp3"
    default:
        return "flac"
    }
}

var defaultDir = (NSHomeDirectory() as NSString).appendingPathComponent("Music/Soulseek Downloads")
var defaultUser = ""
var defaultFormat = "flac"

let args = CommandLine.arguments
var idx = 1
while idx < args.count {
    let arg = args[idx]
    if arg == "--default-dir", idx + 1 < args.count {
        defaultDir = args[idx + 1]
        idx += 2
    } else if arg == "--default-user", idx + 1 < args.count {
        defaultUser = args[idx + 1]
        idx += 2
    } else if arg == "--default-format", idx + 1 < args.count {
        defaultFormat = normalizeFormat(args[idx + 1])
        idx += 2
    } else if arg == "--default-convert", idx + 1 < args.count {
        defaultFormat = normalizeFormat(args[idx + 1])
        idx += 2
    } else {
        idx += 1
    }
}

func isItalianLocale() -> Bool {
    if let env = ProcessInfo.processInfo.environment["SLSK_LANG"]?.trimmingCharacters(in: .whitespacesAndNewlines), !env.isEmpty {
        return env.lowercased().hasPrefix("it")
    }
    if let preferred = Locale.preferredLanguages.first?.lowercased() {
        return preferred.hasPrefix("it")
    }
    return false
}

let isIT = isItalianLocale()

final class SetupController: NSObject, NSWindowDelegate {
    let window: NSWindow
    let userField: NSTextField
    let passField: NSSecureTextField
    let dirField: NSTextField
    let formatPopup: NSPopUpButton
    let errorLabel: NSTextField
    let formatKeys = ["flac", "aiff", "mp3"]
    var confirmed = false

    init(defaultUser: String, defaultDir: String, defaultFormat: String) {
        let width: CGFloat = 520
        let height: CGFloat = 368

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = isIT
            ? "Soulseek Batch Download — Configurazione iniziale"
            : "Soulseek Batch Download — Initial Setup"
        window.isReleasedWhenClosed = false
        window.center()

        userField = NSTextField(frame: NSRect(x: 24, y: 238, width: width - 48, height: 24))
        passField = NSSecureTextField(frame: NSRect(x: 24, y: 180, width: width - 48, height: 24))
        dirField = NSTextField(frame: NSRect(x: 24, y: 122, width: width - 144, height: 24))
        formatPopup = NSPopUpButton(frame: NSRect(x: 24, y: 60, width: width - 48, height: 26), pullsDown: false)
        errorLabel = NSTextField(labelWithString: "")

        super.init()
        window.delegate = self

        let content = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))

        let title = NSTextField(
            labelWithString: isIT
                ? "Benvenuto! Configura il tuo account Soulseek"
                : "Welcome! Set up your Soulseek account"
        )
        title.font = NSFont.boldSystemFont(ofSize: 15)
        title.frame = NSRect(x: 24, y: 330, width: width - 48, height: 22)
        content.addSubview(title)

        let subtitle = NSTextField(
            labelWithString: isIT
                ? "Credenziali e cartella vengono chieste solo al primo avvio. Nelle sessioni successive non dovrai reinserirle."
                : "Credentials and download folder are only asked on first launch. You won't be asked again."
        )
        subtitle.font = NSFont.systemFont(ofSize: 11.5)
        subtitle.textColor = .secondaryLabelColor
        subtitle.lineBreakMode = .byWordWrapping
        subtitle.maximumNumberOfLines = 2
        subtitle.frame = NSRect(x: 24, y: 292, width: width - 48, height: 34)
        content.addSubview(subtitle)

        let userLabel = NSTextField(labelWithString: isIT ? "Username Soulseek:" : "Soulseek Username:")
        userLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        userLabel.frame = NSRect(x: 24, y: 266, width: width - 48, height: 16)
        content.addSubview(userLabel)
        userField.stringValue = defaultUser
        userField.placeholderString = isIT ? "Il tuo username Soulseek" : "Your Soulseek username"
        content.addSubview(userField)

        let passLabel = NSTextField(labelWithString: isIT ? "Password Soulseek:" : "Soulseek Password:")
        passLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        passLabel.frame = NSRect(x: 24, y: 208, width: width - 48, height: 16)
        content.addSubview(passLabel)
        passField.placeholderString = isIT ? "La tua password Soulseek" : "Your Soulseek password"
        content.addSubview(passField)

        let dirLabel = NSTextField(labelWithString: isIT ? "Cartella di download:" : "Download folder:")
        dirLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        dirLabel.frame = NSRect(x: 24, y: 150, width: width - 48, height: 16)
        content.addSubview(dirLabel)
        dirField.stringValue = defaultDir
        dirField.placeholderString = defaultDir
        content.addSubview(dirField)

        let browseBtn = NSButton(title: isIT ? "Sfoglia…" : "Browse…", target: self, action: #selector(didTapBrowse))
        browseBtn.bezelStyle = .rounded
        browseBtn.frame = NSRect(x: width - 112, y: 119, width: 88, height: 28)
        content.addSubview(browseBtn)

        let formatLabel = NSTextField(labelWithString: isIT ? "formato" : "format")
        formatLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        formatLabel.frame = NSRect(x: 24, y: 90, width: width - 48, height: 16)
        content.addSubview(formatLabel)

        formatPopup.addItems(withTitles: [
            ".flac (lossless)",
            ".aiff (PCM 24-bit lossless)",
            ".mp3 (320 kbps)",
        ])
        if let selIdx = formatKeys.firstIndex(of: defaultFormat) {
            formatPopup.selectItem(at: selIdx)
        }
        content.addSubview(formatPopup)

        errorLabel.font = NSFont.systemFont(ofSize: 11.5)
        errorLabel.textColor = .systemRed
        errorLabel.frame = NSRect(x: 24, y: 20, width: width - 260, height: 18)
        content.addSubview(errorLabel)

        let cancelBtn = NSButton(title: isIT ? "Annulla" : "Cancel", target: self, action: #selector(didTapCancel))
        cancelBtn.bezelStyle = .rounded
        cancelBtn.keyEquivalent = "\u{1b}"
        cancelBtn.frame = NSRect(x: width - 232, y: 14, width: 88, height: 30)
        content.addSubview(cancelBtn)

        let saveBtn = NSButton(title: isIT ? "Salva e avvia" : "Save & start", target: self, action: #selector(didTapSave))
        saveBtn.bezelStyle = .rounded
        saveBtn.keyEquivalent = "\r"
        saveBtn.frame = NSRect(x: width - 136, y: 14, width: 112, height: 30)
        content.addSubview(saveBtn)

        window.contentView = content
        window.initialFirstResponder = userField
    }

    @objc func didTapBrowse() {
        let panel = NSOpenPanel()
        panel.title = isIT ? "Scegli cartella download" : "Choose download folder"
        panel.message = isIT
            ? "Seleziona la cartella in cui salvare i brani e gli album scaricati da Soulseek"
            : "Select the folder where downloaded tracks and albums will be saved"
        panel.prompt = isIT ? "Scegli" : "Choose"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true

        let currentPath = (dirField.stringValue as NSString).expandingTildeInPath
        if FileManager.default.fileExists(atPath: currentPath) {
            panel.directoryURL = URL(fileURLWithPath: currentPath, isDirectory: true)
        } else {
            let musicURL = URL(fileURLWithPath: (NSHomeDirectory() as NSString).appendingPathComponent("Music"), isDirectory: true)
            panel.directoryURL = musicURL
        }

        if panel.runModal() == .OK, let selected = panel.url {
            dirField.stringValue = selected.path
        }
    }

    @objc func didTapSave() {
        let u = userField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let p = passField.stringValue
        let d = dirField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if u.isEmpty {
            errorLabel.stringValue = isIT ? "Inserisci il tuo username Soulseek." : "Please enter your Soulseek username."
            window.makeFirstResponder(userField)
            return
        }
        if p.isEmpty {
            errorLabel.stringValue = isIT ? "Inserisci la tua password Soulseek." : "Please enter your Soulseek password."
            window.makeFirstResponder(passField)
            return
        }
        if d.isEmpty {
            errorLabel.stringValue = isIT ? "Scegli una cartella di download." : "Please choose a download folder."
            window.makeFirstResponder(dirField)
            return
        }
        confirmed = true
        NSApp.stopModal()
        window.orderOut(nil)
    }

    @objc func didTapCancel() {
        confirmed = false
        NSApp.stopModal()
        window.orderOut(nil)
    }

    func windowWillClose(_ notification: Notification) {
        if !confirmed {
            NSApp.stopModal()
        }
    }

    func run() -> SetupResult {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        DispatchQueue.main.async {
            self.window.makeFirstResponder(self.userField)
        }
        NSApp.runModal(for: window)
        let expandedDir = (dirField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) as NSString).expandingTildeInPath
        let sel = max(0, min(formatPopup.indexOfSelectedItem, formatKeys.count - 1))
        return SetupResult(
            confirmed: confirmed,
            username: userField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
            password: passField.stringValue,
            output_dir: expandedDir,
            convert_format: formatKeys[sel]
        )
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = SetupController(
    defaultUser: defaultUser,
    defaultDir: defaultDir,
    defaultFormat: defaultFormat
)
let result = controller.run()
if let data = try? JSONEncoder().encode(result), let json = String(data: data, encoding: .utf8) {
    print(json)
}
exit(result.confirmed ? 0 : 1)
