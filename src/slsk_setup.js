ObjC.import("AppKit");
ObjC.import("Foundation");

function normalizeFormat(raw) {
  var v = String(raw || "").toLowerCase();
  if (v === "aiff" || v === "aif" || v === "1" || v === "true" || v === "yes") return "aiff";
  if (v === "mp3" || v === "mp3_320" || v === "mp3-320" || v === "320") return "mp3";
  return "none";
}

function run(argv) {
  var home = ObjC.unwrap($.NSHomeDirectory());
  var defaultDir = home + "/Music/Soulseek Downloads";
  var defaultUser = "";
  var defaultFormat = "none";

  for (var i = 0; i < argv.length; i++) {
    if (argv[i] === "--default-dir" && i + 1 < argv.length) {
      defaultDir = argv[++i];
    } else if (argv[i] === "--default-user" && i + 1 < argv.length) {
      defaultUser = argv[++i];
    } else if ((argv[i] === "--default-format" || argv[i] === "--default-convert") && i + 1 < argv.length) {
      defaultFormat = normalizeFormat(argv[++i]);
    }
  }

  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);

  var state = { confirmed: false };
  var width = 520;
  var height = 370;
  var formatKeys = ["none", "aiff", "mp3"];

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
  title.frame = $.NSMakeRect(24, 332, width - 48, 22);
  content.addSubview(title);

  var sub = $.NSTextField.labelWithString(
    "Queste impostazioni vengono chieste solo al primo avvio. Nelle sessioni successive non dovrai scegliere più nulla."
  );
  sub.font = $.NSFont.systemFontOfSize(11.5);
  sub.textColor = $.NSColor.secondaryLabelColor;
  sub.maximumNumberOfLines = 2;
  sub.frame = $.NSMakeRect(24, 294, width - 48, 34);
  content.addSubview(sub);

  var uLbl = $.NSTextField.labelWithString("Username Soulseek:");
  uLbl.frame = $.NSMakeRect(24, 268, width - 48, 16);
  content.addSubview(uLbl);

  var userField = $.NSTextField.alloc.initWithFrame($.NSMakeRect(24, 240, width - 48, 24));
  userField.stringValue = defaultUser;
  userField.placeholderString = "Il tuo username Soulseek";
  content.addSubview(userField);

  var pLbl = $.NSTextField.labelWithString("Password Soulseek:");
  pLbl.frame = $.NSMakeRect(24, 210, width - 48, 16);
  content.addSubview(pLbl);

  var passField = $.NSSecureTextField.alloc.initWithFrame($.NSMakeRect(24, 182, width - 48, 24));
  passField.placeholderString = "La tua password Soulseek";
  content.addSubview(passField);

  var dLbl = $.NSTextField.labelWithString("Cartella di download:");
  dLbl.frame = $.NSMakeRect(24, 152, width - 48, 16);
  content.addSubview(dLbl);

  var dirField = $.NSTextField.alloc.initWithFrame($.NSMakeRect(24, 124, width - 144, 24));
  dirField.stringValue = defaultDir;
  content.addSubview(dirField);

  var convLbl = $.NSTextField.labelWithString("Conversione automatica dei file .flac scaricati:");
  convLbl.frame = $.NSMakeRect(24, 94, width - 48, 16);
  content.addSubview(convLbl);

  var formatPopup = $.NSPopUpButton.alloc.initWithFramePullsDown(
    $.NSMakeRect(24, 62, width - 48, 26),
    false
  );
  formatPopup.addItemWithTitle("Nessuna (mantieni FLAC e MP3 originali)");
  formatPopup.addItemWithTitle("AIFF (PCM 24-bit lossless — elimina .flac, lascia .mp3)");
  formatPopup.addItemWithTitle("MP3 (320 kbps — elimina .flac, lascia .mp3 invariati)");
  var selIdx = formatKeys.indexOf(defaultFormat);
  formatPopup.selectItemAtIndex(selIdx >= 0 ? selIdx : 0);
  content.addSubview(formatPopup);

  var errLbl = $.NSTextField.labelWithString("");
  errLbl.textColor = $.NSColor.systemRedColor;
  errLbl.frame = $.NSMakeRect(24, 20, width - 260, 18);
  content.addSubview(errLbl);

  ObjC.registerSubclass({
    name: "SlskSetupHandler",
    methods: {
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

  var browseBtn = $.NSButton.buttonWithTitleTargetAction("Sfoglia…", handler, "browseClicked:");
  browseBtn.bezelStyle = $.NSBezelStyleRounded;
  browseBtn.frame = $.NSMakeRect(width - 112, 121, 88, 28);
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
  });
}
