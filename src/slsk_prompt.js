ObjC.import("AppKit");
ObjC.import("Foundation");
ObjC.import("stdlib");

function clipboardMusicURL() {
  try {
    var pb = $.NSPasteboard.generalPasteboard;
    var raw = pb.stringForType($.NSPasteboardTypeString);
    if (!raw) return "";
    var clip = ObjC.unwrap(raw).trim();
    var lower = clip.toLowerCase();
    if (
      lower.indexOf("spotify.com/") !== -1 ||
      lower.indexOf("spotify.link/") !== -1 ||
      lower.indexOf("spoti.fi/") !== -1 ||
      lower.indexOf("spotify:") !== -1 ||
      lower.indexOf("youtube.com/") !== -1 ||
      lower.indexOf("youtu.be/") !== -1
    ) {
      return clip;
    }
  } catch (e) {}
  return "";
}

function run(argv) {
  var prefilledURL = "";
  var defaultConvert = false;
  for (var i = 0; i < argv.length; i++) {
    if (argv[i] === "--url" && i + 1 < argv.length) {
      prefilledURL = argv[++i];
    } else if (argv[i] === "--default-convert" && i + 1 < argv.length) {
      var v = argv[++i].toLowerCase();
      defaultConvert = v === "1" || v === "true" || v === "yes";
    }
  }

  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);

  var alert = $.NSAlert.alloc.init;
  alert.alertStyle = $.NSAlertStyleInformational;
  alert.messageText = "Soulseek Batch Download";

  var needsInput = !prefilledURL;
  if (needsInput) {
    alert.informativeText =
      "Incolla un URL Spotify o YouTube (playlist, album o video full album):";
  } else {
    alert.informativeText = "Link rilevato:\n" + prefilledURL;
  }

  alert.addButtonWithTitle("Scarica");
  alert.addButtonWithTitle("Annulla");

  var checkbox = $.NSButton.checkboxWithTitleTargetAction(
    "Converti FLAC → AIFF (elimina i .flac originali; lascia gli .mp3 in .mp3)",
    null,
    null
  );
  checkbox.state = defaultConvert ? $.NSControlStateValueOn : $.NSControlStateValueOff;
  checkbox.sizeToFit;

  var cbFrame = checkbox.frame;
  var fieldWidth = Math.max(cbFrame.size.width, 430);
  var urlField = null;

  if (needsInput) {
    var accessory = $.NSView.alloc.initWithFrame(
      $.NSMakeRect(0, 0, fieldWidth, 58)
    );
    urlField = $.NSTextField.alloc.initWithFrame(
      $.NSMakeRect(0, 30, fieldWidth, 24)
    );
    urlField.placeholderString =
      "https://open.spotify.com/... oppure https://www.youtube.com/...";
    var clip = clipboardMusicURL();
    if (clip) {
      urlField.stringValue = clip;
    }
    checkbox.setFrameOrigin($.NSMakePoint(0, 2));
    accessory.addSubview(urlField);
    accessory.addSubview(checkbox);
    alert.accessoryView = accessory;
    alert.window.initialFirstResponder = urlField;
  } else {
    var acc = $.NSView.alloc.initWithFrame(
      $.NSMakeRect(0, 0, fieldWidth, cbFrame.size.height + 6)
    );
    checkbox.setFrameOrigin($.NSMakePoint(0, 3));
    acc.addSubview(checkbox);
    alert.accessoryView = acc;
  }

  alert.layout;
  app.activateIgnoringOtherApps(true);

  var response = alert.runModal;
  var confirmed = response === $.NSAlertFirstButtonReturn;
  var finalURL = urlField
    ? ObjC.unwrap(urlField.stringValue).trim()
    : prefilledURL.trim();
  var convert = checkbox.state === $.NSControlStateValueOn;
  var ok = confirmed && finalURL.length > 0;

  var payload = JSON.stringify({
    confirmed: ok,
    url: finalURL,
    convert_flac_to_aiff: convert,
  });
  return payload;
}
