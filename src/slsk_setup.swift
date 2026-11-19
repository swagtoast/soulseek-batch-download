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
    switch raw.lowercased() {
    case "aiff", "aif", "1", "true", "yes":
        return "aiff"
    case "mp3", "mp3_320", "mp3-320", "320":
        return "mp3"
    default:
        return "none"
    }
}

var defaultDir = (NSHomeDirectory() as NSString).appendingPathComponent("Music/Soulseek Downloads")
var defaultUser = ""
var defaultFormat = "none"

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

final class SetupController: NSObject, NSWindowDelegate {
    let window: NSWindow
    let userField: NSTextField
    let passField: NSSecureTextField
    let dirField: NSTextField
    let formatPopup: NSPopUpButton
    let errorLabel: NSTextField
    let formatKeys = ["none", "aiff", "mp3"]
    var confirmed = false

    init(defaultUser: String, defaultDir: String, defaultFormat: String) {
        let width: CGFloat = 520
        let height: CGFloat = 370

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Soulseek Batch Download — Configurazione iniziale"
        window.isReleasedWhenClosed = false
        window.center()

        userField = NSTextField(frame: NSRect(x: 24, y: 240, width: width - 48, height: 24))
        passField = NSSecureTextField(frame: NSRect(x: 24, y: 182, width: width - 48, height: 24))
        dirField = NSTextField(frame: NSRect(x: 24, y: 124, width: width - 144, height: 24))
        formatPopup = NSPopUpButton(frame: NSRect(x: 24, y: 62, width: width - 48, height: 26), pullsDown: false)
        errorLabel = NSTextField(labelWithString: "")

        super.init()
        window.delegate = self

        let content = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))

        let title = NSTextField(labelWithString: "Benvenuto! Configura il tuo account Soulseek")
        title.font = NSFont.boldSystemFont(ofSize: 15)
        title.frame = NSRect(x: 24, y: 332, width: width - 48, height: 22)
        content.addSubview(title)

        let subtitle = NSTextField(
            labelWithString: "Queste impostazioni vengono chieste solo al primo avvio. Nelle sessioni successive non dovrai scegliere più nulla."
        )
        subtitle.font = NSFont.systemFont(ofSize: 11.5)
        subtitle.textColor = .secondaryLabelColor
        subtitle.lineBreakMode = .byWordWrapping
        subtitle.maximumNumberOfLines = 2
        subtitle.frame = NSRect(x: 24, y: 294, width: width - 48, height: 34)
        content.addSubview(subtitle)

        let userLabel = NSTextField(labelWithString: "Username Soulseek:")
        userLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        userLabel.frame = NSRect(x: 24, y: 268, width: width - 48, height: 16)
        content.addSubview(userLabel)
        userField.stringValue = defaultUser
        userField.placeholderString = "Il tuo username Soulseek"
        content.addSubview(userField)

        let passLabel = NSTextField(labelWithString: "Password Soulseek:")
        passLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        passLabel.frame = NSRect(x: 24, y: 210, width: width - 48, height: 16)
        content.addSubview(passLabel)
        passField.placeholderString = "La tua password Soulseek"
        content.addSubview(passField)

        let dirLabel = NSTextField(labelWithString: "Cartella di download:")
        dirLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        dirLabel.frame = NSRect(x: 24, y: 152, width: width - 48, height: 16)
        content.addSubview(dirLabel)
        dirField.stringValue = defaultDir
        dirField.placeholderString = defaultDir
        content.addSubview(dirField)

        let browseBtn = NSButton(title: "Sfoglia…", target: self, action: #selector(didTapBrowse))
        browseBtn.bezelStyle = .rounded
        browseBtn.frame = NSRect(x: width - 112, y: 121, width: 88, height: 28)
        content.addSubview(browseBtn)

        let convLabel = NSTextField(labelWithString: "Conversione automatica dei file .flac scaricati:")
        convLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        convLabel.frame = NSRect(x: 24, y: 94, width: width - 48, height: 16)
        content.addSubview(convLabel)

        formatPopup.addItems(withTitles: [
            "Nessuna (mantieni FLAC e MP3 originali)",
            "AIFF (PCM 24-bit lossless — elimina .flac, lascia .mp3)",
            "MP3 (320 kbps — elimina .flac, lascia .mp3 invariati)",
        ])
        if let selIdx = formatKeys.firstIndex(of: defaultFormat) {
            formatPopup.selectItem(at: selIdx)
        }
        content.addSubview(formatPopup)

        errorLabel.font = NSFont.systemFont(ofSize: 11.5)
        errorLabel.textColor = .systemRed
        errorLabel.frame = NSRect(x: 24, y: 20, width: width - 260, height: 18)
        content.addSubview(errorLabel)

        let cancelBtn = NSButton(title: "Annulla", target: self, action: #selector(didTapCancel))
        cancelBtn.bezelStyle = .rounded
        cancelBtn.keyEquivalent = "\u{1b}"
        cancelBtn.frame = NSRect(x: width - 232, y: 14, width: 88, height: 30)
        content.addSubview(cancelBtn)

        let saveBtn = NSButton(title: "Salva e avvia", target: self, action: #selector(didTapSave))
        saveBtn.bezelStyle = .rounded
        saveBtn.keyEquivalent = "\r"
        saveBtn.frame = NSRect(x: width - 136, y: 14, width: 112, height: 30)
        content.addSubview(saveBtn)

        window.contentView = content
        window.initialFirstResponder = userField
    }

    @objc func didTapBrowse() {
        let panel = NSOpenPanel()
        panel.title = "Scegli cartella download"
        panel.message = "Seleziona la cartella in cui salvare i brani e gli album scaricati da Soulseek"
        panel.prompt = "Scegli"
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
            errorLabel.stringValue = "Inserisci il tuo username Soulseek."
            window.makeFirstResponder(userField)
            return
        }
        if p.isEmpty {
            errorLabel.stringValue = "Inserisci la tua password Soulseek."
            window.makeFirstResponder(passField)
            return
        }
        if d.isEmpty {
            errorLabel.stringValue = "Scegli una cartella di download."
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
let controller = SetupController(defaultUser: defaultUser, defaultDir: defaultDir, defaultFormat: defaultFormat)
let result = controller.run()
if let data = try? JSONEncoder().encode(result), let json = String(data: data, encoding: .utf8) {
    print(json)
}
exit(result.confirmed ? 0 : 1)
