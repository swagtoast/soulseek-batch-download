ObjC.import("AppKit");
ObjC.import("Foundation");
ObjC.import("stdlib");

function normalizeFormat(raw) {
  var v = String(raw || "").toLowerCase().replace(/^\.+/, "").trim();
  if (v === "aiff" || v === "aif" || v === "1" || v === "true" || v === "yes") return "aiff";
  if (v === "mp3" || v === "mp3_320" || v === "mp3-320" || v === "320") return "mp3";
  return "flac";
}

function parseBool(raw) {
  var v = String(raw || "").toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

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
  var urlLabel = "";
  var defaultFormat = "flac";
  var defaultDelete = true;
  for (var i = 0; i < argv.length; i++) {
    if (argv[i] === "--url" && i + 1 < argv.length) {
      prefilledURL = argv[++i];
    } else if (argv[i] === "--label" && i + 1 < argv.length) {
      urlLabel = argv[++i];
    } else if ((argv[i] === "--default-format" || argv[i] === "--default-convert") && i + 1 < argv.length) {
      defaultFormat = normalizeFormat(argv[++i]);
    } else if (argv[i] === "--default-delete" && i + 1 < argv.length) {
      defaultDelete = parseBool(argv[++i]);
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
    var heading = urlLabel || "Link rilevato";
    alert.informativeText = heading + ":\n" + prefilledURL;
  }

  alert.addButtonWithTitle("Scarica");
  alert.addButtonWithTitle("Annulla");

  var formatKeys = ["flac", "aiff", "mp3"];
  var fieldWidth = 440;

  var formatLabel = $.NSTextField.labelWithString("formato");
  var formatPopup = $.NSPopUpButton.alloc.initWithFramePullsDown(
    $.NSMakeRect(0, 30, fieldWidth, 26),
    false
  );
  formatPopup.addItemWithTitle(".flac (lossless)");
  formatPopup.addItemWithTitle(".aiff (PCM 24-bit lossless)");
  formatPopup.addItemWithTitle(".mp3 (320 kbps)");
  var selIdx = formatKeys.indexOf(defaultFormat);
  formatPopup.selectItemAtIndex(selIdx >= 0 ? selIdx : 0);

  var deleteCheck = $.NSButton.checkboxWithTitleTargetAction("elimina file originale", null, null);
  deleteCheck.state = defaultDelete ? $.NSControlStateValueOn : $.NSControlStateValueOff;
  deleteCheck.frame = $.NSMakeRect(0, 2, fieldWidth, 22);
  deleteCheck.enabled = formatKeys[selIdx >= 0 ? selIdx : 0] !== "flac";

  ObjC.registerSubclass({
    name: "SlskPromptFormatHandler",
    methods: {
      "formatChanged:": {
        types: ["void", ["id"]],
        implementation: function () {
          var idx = formatPopup.indexOfSelectedItem;
          if (idx < 0 || idx >= formatKeys.length) idx = 0;
          deleteCheck.enabled = formatKeys[idx] !== "flac";
        },
      },
    },
  });
  var handler = $.SlskPromptFormatHandler.alloc.init;
  formatPopup.target = handler;
  formatPopup.action = "formatChanged:";

  var urlField = null;

  if (needsInput) {
    var accessory = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, fieldWidth, 114));
    urlField = $.NSTextField.alloc.initWithFrame($.NSMakeRect(0, 86, fieldWidth, 24));
    urlField.placeholderString =
      "https://open.spotify.com/... oppure https://www.youtube.com/...";
    var clip = clipboardMusicURL();
    if (clip) {
      urlField.stringValue = clip;
    }
    formatLabel.frame = $.NSMakeRect(0, 60, fieldWidth, 16);
    formatPopup.frame = $.NSMakeRect(0, 30, fieldWidth, 26);
    deleteCheck.frame = $.NSMakeRect(0, 2, fieldWidth, 22);
    accessory.addSubview(urlField);
    accessory.addSubview(formatLabel);
    accessory.addSubview(formatPopup);
    accessory.addSubview(deleteCheck);
    alert.accessoryView = accessory;
    alert.window.initialFirstResponder = urlField;
  } else {
    var acc = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, fieldWidth, 80));
    formatLabel.frame = $.NSMakeRect(0, 60, fieldWidth, 16);
    formatPopup.frame = $.NSMakeRect(0, 30, fieldWidth, 26);
    deleteCheck.frame = $.NSMakeRect(0, 2, fieldWidth, 22);
    acc.addSubview(formatLabel);
    acc.addSubview(formatPopup);
    acc.addSubview(deleteCheck);
    alert.accessoryView = acc;
  }

  alert.layout;
  app.activateIgnoringOtherApps(true);

  var response = alert.runModal;
  var confirmed = response === $.NSAlertFirstButtonReturn;
  var finalURL = urlField
    ? ObjC.unwrap(urlField.stringValue).trim()
    : prefilledURL.trim();
  var chosenIdx = formatPopup.indexOfSelectedItem;
  if (chosenIdx < 0 || chosenIdx >= formatKeys.length) chosenIdx = 0;
  var ok = confirmed && finalURL.length > 0;

  return JSON.stringify({
    confirmed: ok,
    url: finalURL,
    convert_format: formatKeys[chosenIdx],
    delete_original: deleteCheck.state === $.NSControlStateValueOn,
  });
}
