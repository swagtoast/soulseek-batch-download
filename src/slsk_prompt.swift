import AppKit
import Foundation

struct PromptResult: Codable {
    let confirmed: Bool
    let url: String
    let convert_format: String
    let delete_original: Bool
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

func parseBool(_ raw: String) -> Bool {
    let v = raw.lowercased()
    return v == "1" || v == "true" || v == "yes"
}

var prefilledURL: String?
var urlLabel: String?
var defaultFormat = "flac"
var defaultDelete = true
var idx = 1
let args = CommandLine.arguments
while idx < args.count {
    let arg = args[idx]
    if arg == "--url", idx + 1 < args.count {
        prefilledURL = args[idx + 1]
        idx += 2
    } else if arg == "--label", idx + 1 < args.count {
        urlLabel = args[idx + 1]
        idx += 2
    } else if arg == "--default-format", idx + 1 < args.count {
        defaultFormat = normalizeFormat(args[idx + 1])
        idx += 2
    } else if arg == "--default-convert", idx + 1 < args.count {
        defaultFormat = normalizeFormat(args[idx + 1])
        idx += 2
    } else if arg == "--default-delete", idx + 1 < args.count {
        defaultDelete = parseBool(args[idx + 1])
        idx += 2
    } else {
        idx += 1
    }
}

func clipboardMusicURL() -> String? {
    guard let clip = NSPasteboard.general.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines),
          !clip.isEmpty else {
        return nil
    }
    let lower = clip.lowercased()
    if lower.contains("spotify.com/") || lower.contains("spotify.link/") || lower.contains("spoti.fi/") ||
       lower.contains("spotify:") || lower.contains("youtube.com/") || lower.contains("youtu.be/") {
        return clip
    }
    return nil
}

final class FormatHandler: NSObject {
    let formatPopup: NSPopUpButton
    let deleteCheck: NSButton
    let formatKeys = ["flac", "aiff", "mp3"]

    init(formatPopup: NSPopUpButton, deleteCheck: NSButton) {
        self.formatPopup = formatPopup
        self.deleteCheck = deleteCheck
        super.init()
        updateState()
    }

    @objc func formatChanged(_ sender: Any?) {
        updateState()
    }

    func updateState() {
        let sel = max(0, min(formatPopup.indexOfSelectedItem, formatKeys.count - 1))
        deleteCheck.isEnabled = (formatKeys[sel] != "flac")
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let alert = NSAlert()
alert.alertStyle = .informational
alert.messageText = "Soulseek Batch Download"

let needsInput = (prefilledURL == nil || prefilledURL?.isEmpty == true)
if let url = prefilledURL, !url.isEmpty {
    let heading = (urlLabel?.isEmpty == false) ? urlLabel! : "Link rilevato"
    alert.informativeText = "\(heading):\n\(url)"
} else {
    alert.informativeText = "Incolla un URL Spotify o YouTube (playlist, album o video full album):"
}

alert.addButton(withTitle: "Scarica")
alert.addButton(withTitle: "Annulla")

let formatKeys = ["flac", "aiff", "mp3"]
let fieldWidth: CGFloat = 440

let formatLabel = NSTextField(labelWithString: "formato")
formatLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)

let formatPopup = NSPopUpButton(frame: NSRect(x: 0, y: 30, width: fieldWidth, height: 26), pullsDown: false)
formatPopup.addItems(withTitles: [
    ".flac (lossless)",
    ".aiff (PCM 24-bit lossless)",
    ".mp3 (320 kbps)",
])
if let selIdx = formatKeys.firstIndex(of: defaultFormat) {
    formatPopup.selectItem(at: selIdx)
}

let deleteCheck = NSButton(checkboxWithTitle: "elimina file originale", target: nil, action: nil)
deleteCheck.state = defaultDelete ? .on : .off
deleteCheck.font = NSFont.systemFont(ofSize: 12)
deleteCheck.frame = NSRect(x: 0, y: 2, width: fieldWidth, height: 22)

let formatHandler = FormatHandler(formatPopup: formatPopup, deleteCheck: deleteCheck)
formatPopup.target = formatHandler
formatPopup.action = #selector(FormatHandler.formatChanged(_:))

var urlField: NSTextField?

if needsInput {
    let accessory = NSView(frame: NSRect(x: 0, y: 0, width: fieldWidth, height: 114))
    let field = NSTextField(frame: NSRect(x: 0, y: 86, width: fieldWidth, height: 24))
    field.placeholderString = "https://open.spotify.com/... oppure https://www.youtube.com/..."
    if let clip = clipboardMusicURL() {
        field.stringValue = clip
    }
    formatLabel.frame = NSRect(x: 0, y: 60, width: fieldWidth, height: 16)
    formatPopup.frame = NSRect(x: 0, y: 30, width: fieldWidth, height: 26)
    deleteCheck.frame = NSRect(x: 0, y: 2, width: fieldWidth, height: 22)
    accessory.addSubview(field)
    accessory.addSubview(formatLabel)
    accessory.addSubview(formatPopup)
    accessory.addSubview(deleteCheck)
    alert.accessoryView = accessory
    urlField = field
    alert.window.initialFirstResponder = field
} else {
    let accessory = NSView(frame: NSRect(x: 0, y: 0, width: fieldWidth, height: 80))
    formatLabel.frame = NSRect(x: 0, y: 60, width: fieldWidth, height: 16)
    formatPopup.frame = NSRect(x: 0, y: 30, width: fieldWidth, height: 26)
    deleteCheck.frame = NSRect(x: 0, y: 2, width: fieldWidth, height: 22)
    accessory.addSubview(formatLabel)
    accessory.addSubview(formatPopup)
    accessory.addSubview(deleteCheck)
    alert.accessoryView = accessory
}

alert.layout()
NSApp.activate(ignoringOtherApps: true)
if let field = urlField {
    DispatchQueue.main.async {
        alert.window.makeFirstResponder(field)
    }
}

let response = alert.runModal()
let confirmed = (response == .alertFirstButtonReturn)
let finalURL = (urlField?.stringValue ?? prefilledURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
let sel = max(0, min(formatPopup.indexOfSelectedItem, formatKeys.count - 1))

let output = PromptResult(
    confirmed: confirmed && !finalURL.isEmpty,
    url: finalURL,
    convert_format: formatKeys[sel],
    delete_original: deleteCheck.state == .on
)
if let data = try? JSONEncoder().encode(output), let json = String(data: data, encoding: .utf8) {
    print(json)
}

exit(output.confirmed ? 0 : 1)
