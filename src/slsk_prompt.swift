import AppKit
import Foundation

struct PromptResult: Codable {
    let confirmed: Bool
    let url: String
    let convert_flac_to_aiff: Bool
}

var prefilledURL: String?
var defaultConvert = false
var idx = 1
let args = CommandLine.arguments
while idx < args.count {
    let arg = args[idx]
    if arg == "--url", idx + 1 < args.count {
        prefilledURL = args[idx + 1]
        idx += 2
    } else if arg == "--default-convert", idx + 1 < args.count {
        let v = args[idx + 1].lowercased()
        defaultConvert = (v == "1" || v == "true" || v == "yes")
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

let checkbox = NSButton(
    checkboxWithTitle: "Converti FLAC → AIFF (elimina i .flac originali; lascia gli .mp3 in .mp3)",
    target: nil,
    action: nil
)
checkbox.state = defaultConvert ? .on : .off
checkbox.sizeToFit()

let fieldWidth = max(checkbox.frame.width, 430)
var urlField: NSTextField?

if needsInput {
    let accessory = NSView(frame: NSRect(x: 0, y: 0, width: fieldWidth, height: 58))
    let field = NSTextField(frame: NSRect(x: 0, y: 30, width: fieldWidth, height: 24))
    field.placeholderString = "https://open.spotify.com/... oppure https://www.youtube.com/..."
    if let clip = clipboardMusicURL() {
        field.stringValue = clip
    }
    checkbox.frame.origin = NSPoint(x: 0, y: 2)
    accessory.addSubview(field)
    accessory.addSubview(checkbox)
    alert.accessoryView = accessory
    urlField = field
    alert.window.initialFirstResponder = field
} else {
    let accessory = NSView(frame: NSRect(x: 0, y: 0, width: fieldWidth, height: checkbox.frame.height + 6))
    checkbox.frame.origin = NSPoint(x: 0, y: 3)
    accessory.addSubview(checkbox)
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
let convert = (checkbox.state == .on)

let output = PromptResult(confirmed: confirmed && !finalURL.isEmpty, url: finalURL, convert_flac_to_aiff: convert)
if let data = try? JSONEncoder().encode(output), let json = String(data: data, encoding: .utf8) {
    print(json)
}

exit(output.confirmed ? 0 : 1)
