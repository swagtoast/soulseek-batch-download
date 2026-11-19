ObjC.import("AppKit");
ObjC.import("Foundation");

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

function run(argv) {
  var home = ObjC.unwrap($.NSHomeDirectory());
  var defaultDir = home + "/Music/Soulseek Downloads";
  var defaultUser = "";
  var defaultFormat = "flac";
  var defaultDelete = true;

  for (var i = 0; i < argv.length; i++) {
    if (argv[i] === "--default-dir" && i + 1 < argv.length) {
      defaultDir = argv[++i];
    } else if (argv[i] === "--default-user" && i + 1 < argv.length) {
      defaultUser = argv[++i];
    } else if ((argv[i] === "--default-format" || argv[i] === "--default-convert") && i + 1 < argv.length) {
      defaultFormat = normalizeFormat(argv[++i]);
    } else if (argv[i] === "--default-delete" && i + 1 < argv.length) {
      defaultDelete = parseBool(argv[++i]);
    }
  }

  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);

  var state = { confirmed: false };
  var width = 520;
  var height = 398;
  var formatKeys = ["flac", "aiff", "mp3"];

  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    $.NSMakeRect(0, 0, width, height),
    $.NSWindowStyleMaskTitled | $.NSWindowStyleMaskClosable,
    $.NSBackingStoreBuffered,
    false
  );
  win.title = "Soulseek Batch Download — Configurazione iniziale";
  win.releasedWhenClosed = false;
  win.center;

  var content = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, width, height));

  var title = $.NSTextField.labelWithString("Benvenuto! Configura il tuo account Soulseek");
  title.font = $.NSFont.boldSystemFontOfSize(15);
  title.frame = $.NSMakeRect(24, 360, width - 48, 22);
  content.addSubview(title);

  var sub = $.NSTextField.labelWithString(
    "Credenziali e cartella vengono chieste solo al primo avvio. Nelle sessioni successive non dovrai reinserirle."
  );
  sub.font = $.NSFont.systemFontOfSize(11.5);
  sub.textColor = $.NSColor.secondaryLabelColor;
  sub.maximumNumberOfLines = 2;
  sub.frame = $.NSMakeRect(24, 322, width - 48, 34);
  content.addSubview(sub);

  var uLbl = $.NSTextField.labelWithString("Username Soulseek:");
  uLbl.frame = $.NSMakeRect(24, 296, width - 48, 16);
  content.addSubview(uLbl);

  var userField = $.NSTextField.alloc.initWithFrame($.NSMakeRect(24, 268, width - 48, 24));
  userField.stringValue = defaultUser;
  userField.placeholderString = "Il tuo username Soulseek";
  content.addSubview(userField);

  var pLbl = $.NSTextField.labelWithString("Password Soulseek:");
  pLbl.frame = $.NSMakeRect(24, 238, width - 48, 16);
  content.addSubview(pLbl);

  var passField = $.NSSecureTextField.alloc.initWithFrame($.NSMakeRect(24, 210, width - 48, 24));
  passField.placeholderString = "La tua password Soulseek";
  content.addSubview(passField);

  var dLbl = $.NSTextField.labelWithString("Cartella di download:");
  dLbl.frame = $.NSMakeRect(24, 180, width - 48, 16);
  content.addSubview(dLbl);

  var dirField = $.NSTextField.alloc.initWithFrame($.NSMakeRect(24, 152, width - 144, 24));
  dirField.stringValue = defaultDir;
  content.addSubview(dirField);

  var formatLbl = $.NSTextField.labelWithString("formato");
  formatLbl.frame = $.NSMakeRect(24, 122, width - 48, 16);
  content.addSubview(formatLbl);

  var formatPopup = $.NSPopUpButton.alloc.initWithFramePullsDown(
    $.NSMakeRect(24, 92, width - 48, 26),
    false
  );
  formatPopup.addItemWithTitle(".flac (lossless)");
  formatPopup.addItemWithTitle(".aiff (PCM 24-bit lossless)");
  formatPopup.addItemWithTitle(".mp3 (320 kbps)");
  var selIdx = formatKeys.indexOf(defaultFormat);
  formatPopup.selectItemAtIndex(selIdx >= 0 ? selIdx : 0);
  content.addSubview(formatPopup);

  var deleteCheck = $.NSButton.checkboxWithTitleTargetAction("elimina file originale", null, null);
  deleteCheck.state = defaultDelete ? $.NSControlStateValueOn : $.NSControlStateValueOff;
  deleteCheck.frame = $.NSMakeRect(24, 60, width - 48, 22);
  deleteCheck.enabled = formatKeys[selIdx >= 0 ? selIdx : 0] !== "flac";
  content.addSubview(deleteCheck);

  var errLbl = $.NSTextField.labelWithString("");
  errLbl.textColor = $.NSColor.systemRedColor;
  errLbl.frame = $.NSMakeRect(24, 20, width - 260, 18);
  content.addSubview(errLbl);

  ObjC.registerSubclass({
    name: "SlskSetupHandler",
    methods: {
      "formatChanged:": {
        types: ["void", ["id"]],
        implementation: function () {
          var idx = formatPopup.indexOfSelectedItem;
          if (idx < 0 || idx >= formatKeys.length) idx = 0;
          deleteCheck.enabled = formatKeys[idx] !== "flac";
        },
      },
      "browseClicked:": {
        types: ["void", ["id"]],
        implementation: function () {
          var panel = $.NSOpenPanel.openPanel;
          panel.title = "Scegli cartella download";
          panel.message = "Seleziona la cartella in cui salvare i download di Soulseek";
          panel.prompt = "Scegli";
          panel.canChooseFiles = false;
          panel.canChooseDirectories = true;
          panel.allowsMultipleSelection = false;
          panel.canCreateDirectories = true;
          if (panel.runModal === $.NSModalResponseOK) {
            dirField.stringValue = ObjC.unwrap(panel.URL.path);
          }
        },
      },
      "saveClicked:": {
        types: ["void", ["id"]],
        implementation: function () {
          var u = ObjC.unwrap(userField.stringValue).trim();
          var p = ObjC.unwrap(passField.stringValue);
          var d = ObjC.unwrap(dirField.stringValue).trim();
          if (!u) {
            errLbl.stringValue = "Inserisci il tuo username Soulseek.";
            win.makeFirstResponder(userField);
            return;
          }
          if (!p) {
            errLbl.stringValue = "Inserisci la tua password Soulseek.";
            win.makeFirstResponder(passField);
            return;
          }
          if (!d) {
            errLbl.stringValue = "Scegli una cartella di download.";
            win.makeFirstResponder(dirField);
            return;
          }
          state.confirmed = true;
          app.stopModal;
          win.orderOut(null);
        },
      },
      "cancelClicked:": {
        types: ["void", ["id"]],
        implementation: function () {
          state.confirmed = false;
          app.stopModal;
          win.orderOut(null);
        },
      },
      "windowWillClose:": {
        types: ["void", ["id"]],
        implementation: function () {
          if (!state.confirmed) {
            app.stopModal;
          }
        },
      },
    },
  });

  var handler = $.SlskSetupHandler.alloc.init;
  win.delegate = handler;
  formatPopup.target = handler;
  formatPopup.action = "formatChanged:";

  var browseBtn = $.NSButton.buttonWithTitleTargetAction("Sfoglia…", handler, "browseClicked:");
  browseBtn.bezelStyle = $.NSBezelStyleRounded;
  browseBtn.frame = $.NSMakeRect(width - 112, 149, 88, 28);
  content.addSubview(browseBtn);

  var cancelBtn = $.NSButton.buttonWithTitleTargetAction("Annulla", handler, "cancelClicked:");
  cancelBtn.bezelStyle = $.NSBezelStyleRounded;
  cancelBtn.keyEquivalent = "\x1b";
  cancelBtn.frame = $.NSMakeRect(width - 232, 14, 88, 30);
  content.addSubview(cancelBtn);

  var saveBtn = $.NSButton.buttonWithTitleTargetAction("Salva e avvia", handler, "saveClicked:");
  saveBtn.bezelStyle = $.NSBezelStyleRounded;
  saveBtn.keyEquivalent = "\r";
  saveBtn.frame = $.NSMakeRect(width - 136, 14, 112, 30);
  content.addSubview(saveBtn);

  win.contentView = content;
  win.initialFirstResponder = userField;

  app.activateIgnoringOtherApps(true);
  win.makeKeyAndOrderFront(null);
  app.runModalForWindow(win);

  var finalDir = ObjC.unwrap(dirField.stringValue).trim();
  if (finalDir.indexOf("~/") === 0) {
    finalDir = home + finalDir.slice(1);
  }
  var chosenIdx = formatPopup.indexOfSelectedItem;
  if (chosenIdx < 0 || chosenIdx >= formatKeys.length) chosenIdx = 0;

  return JSON.stringify({
    confirmed: state.confirmed,
    username: ObjC.unwrap(userField.stringValue).trim(),
    password: ObjC.unwrap(passField.stringValue),
    output_dir: finalDir,
    convert_format: formatKeys[chosenIdx],
    delete_original: deleteCheck.state === $.NSControlStateValueOn,
  });
}
