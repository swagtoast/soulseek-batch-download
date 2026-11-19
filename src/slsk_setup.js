ObjC.import("AppKit");
ObjC.import("Foundation");

function run(argv) {
  var home = ObjC.unwrap($.NSHomeDirectory());
  var defaultDir = home + "/Music/Soulseek Downloads";
  var defaultUser = "";
  var defaultConvert = false;

  for (var i = 0; i < argv.length; i++) {
    if (argv[i] === "--default-dir" && i + 1 < argv.length) {
      defaultDir = argv[++i];
    } else if (argv[i] === "--default-user" && i + 1 < argv.length) {
      defaultUser = argv[++i];
    } else if (argv[i] === "--default-convert" && i + 1 < argv.length) {
      var v = argv[++i].toLowerCase();
      defaultConvert = v === "1" || v === "true" || v === "yes";
    }
  }

  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);

  var state = { confirmed: false };
  var width = 520;
  var height = 340;

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
  title.frame = $.NSMakeRect(24, 302, width - 48, 22);
  content.addSubview(title);

  var sub = $.NSTextField.labelWithString(
    "Queste impostazioni vengono chieste solo al primo avvio. Nelle sessioni successive non dovrai scegliere più nulla."
  );
  sub.font = $.NSFont.systemFontOfSize(11.5);
  sub.textColor = $.NSColor.secondaryLabelColor;
  sub.maximumNumberOfLines = 2;
  sub.frame = $.NSMakeRect(24, 264, width - 48, 34);
  content.addSubview(sub);

  var uLbl = $.NSTextField.labelWithString("Username Soulseek:");
  uLbl.frame = $.NSMakeRect(24, 238, width - 48, 16);
  content.addSubview(uLbl);

  var userField = $.NSTextField.alloc.initWithFrame($.NSMakeRect(24, 210, width - 48, 24));
  userField.stringValue = defaultUser;
  userField.placeholderString = "Il tuo username Soulseek";
  content.addSubview(userField);

  var pLbl = $.NSTextField.labelWithString("Password Soulseek:");
  pLbl.frame = $.NSMakeRect(24, 180, width - 48, 16);
  content.addSubview(pLbl);

  var passField = $.NSSecureTextField.alloc.initWithFrame($.NSMakeRect(24, 152, width - 48, 24));
  passField.placeholderString = "La tua password Soulseek";
  content.addSubview(passField);

  var dLbl = $.NSTextField.labelWithString("Cartella di download:");
  dLbl.frame = $.NSMakeRect(24, 122, width - 48, 16);
  content.addSubview(dLbl);

  var dirField = $.NSTextField.alloc.initWithFrame($.NSMakeRect(24, 94, width - 144, 24));
  dirField.stringValue = defaultDir;
  content.addSubview(dirField);

  var convertCheck = $.NSButton.checkboxWithTitleTargetAction(
    "Converti FLAC → AIFF automaticamente (elimina i .flac; lascia gli .mp3 in .mp3)",
    null,
    null
  );
  convertCheck.state = defaultConvert ? $.NSControlStateValueOn : $.NSControlStateValueOff;
  convertCheck.frame = $.NSMakeRect(24, 56, width - 48, 22);
  content.addSubview(convertCheck);

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
  browseBtn.frame = $.NSMakeRect(width - 112, 91, 88, 28);
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
  return JSON.stringify({
    confirmed: state.confirmed,
    username: ObjC.unwrap(userField.stringValue).trim(),
    password: ObjC.unwrap(passField.stringValue),
    output_dir: finalDir,
    convert_flac_to_aiff: convertCheck.state === $.NSControlStateValueOn,
  });
}
