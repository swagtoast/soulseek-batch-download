import AppKit
import Foundation

struct PromptResult: Codable {
    let confirmed: Bool
    let url: String
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

var prefilledURL: String?
var defaultFormat = "none"
var idx = 1
let args = CommandLine.arguments
while idx < args.count {
    let arg = args[idx]
    if arg == "--url", idx + 1 < args.count {
        prefilledURL = args[idx + 1]
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

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let alert = NSAlert()
alert.alertStyle = .informational
alert.messageText = "Soulseek Batch Download"

let needsInput = (prefilledURL == nil || prefilledURL?.isEmpty == true)
if let url = prefilledURL, !url.isEmpty {
    alert.informativeText = "Link rilevato:\n\(url)"
} else {
    alert.informativeText = "Incolla un URL Spotify o YouTube (playlist, album o video full album):"
}

alert.addButton(withTitle: "Scarica")
alert.addButton(withTitle: "Annulla")

let formatKeys = ["none", "aiff", "mp3"]
let fieldWidth: CGFloat = 460

let convLabel = NSTextField(labelWithString: "Conversione file .flac scaricati:")
convLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)

let formatPopup = NSPopUpButton(frame: NSRect(x: 0, y: 2, width: fieldWidth, height: 26), pullsDown: false)
formatPopup.addItems(withTitles: [
    "Nessuna (mantieni FLAC e MP3 originali)",
    "AIFF (PCM 24-bit lossless — elimina .flac, lascia .mp3)",
    "MP3 (320 kbps — elimina .flac, lascia .mp3 invariati)",
])
if let selIdx = formatKeys.firstIndex(of: defaultFormat) {
    formatPopup.selectItem(at: selIdx)
}

var urlField: NSTextField?

if needsInput {
    let accessory = NSView(frame: NSRect(x: 0, y: 0, width: fieldWidth, height: 88))
    let field = NSTextField(frame: NSRect(x: 0, y: 60, width: fieldWidth, height: 24))
    field.placeholderString = "https://open.spotify.com/... oppure https://www.youtube.com/..."
    if let clip = clipboardMusicURL() {
        field.stringValue = clip
    }
    convLabel.frame = NSRect(x: 0, y: 34, width: fieldWidth, height: 16)
    formatPopup.frame = NSRect(x: 0, y: 2, width: fieldWidth, height: 26)
    accessory.addSubview(field)
    accessory.addSubview(convLabel)
    accessory.addSubview(formatPopup)
    alert.accessoryView = accessory
    urlField = field
    alert.window.initialFirstResponder = field
} else {
    let accessory = NSView(frame: NSRect(x: 0, y: 0, width: fieldWidth, height: 54))
    convLabel.frame = NSRect(x: 0, y: 32, width: fieldWidth, height: 16)
    formatPopup.frame = NSRect(x: 0, y: 2, width: fieldWidth, height: 26)
    accessory.addSubview(convLabel)
    accessory.addSubview(formatPopup)
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

let output = PromptResult(confirmed: confirmed && !finalURL.isEmpty, url: finalURL, convert_format: formatKeys[sel])
if let data = try? JSONEncoder().encode(output), let json = String(data: data, encoding: .utf8) {
    print(json)
}

exit(output.confirmed ? 0 : 1)
