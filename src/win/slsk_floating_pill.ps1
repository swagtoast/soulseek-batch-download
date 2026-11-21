param(
    [string]$InstallDir = ""
)

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
}

# Single-instance mutex so only one lightweight tray/pill helper runs per Windows user.
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, "Global\SoulseekBatchDownloadFloatingPill", [ref]$createdNew)
if (-not $createdNew) {
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$csharp = @"
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Windows.Automation;
using System.Windows.Forms;

public class SlskFloatingPillApp : NativeWindow, IDisposable {
    private const int WM_CLIPBOARDUPDATE = 0x031D;
    private const int WM_HOTKEY = 0x0312;
    private const int HOTKEY_ID = 0x534C;
    private const uint MOD_CONTROL = 0x0002;
    private const uint MOD_ALT = 0x0001;
    private const int WH_MOUSE_LL = 14;
    private const int WM_LBUTTONDOWN = 0x0201;
    private const int WM_LBUTTONUP = 0x0202;

    private delegate IntPtr LowLevelMouseProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int x; public int y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MSLLHOOKSTRUCT {
        public POINT pt;
        public uint mouseData;
        public uint flags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool AddClipboardFormatListener(IntPtr hwnd);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RemoveClipboardFormatListener(IntPtr hwnd);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelMouseProc lpfn, IntPtr hMod, uint dwThreadId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(IntPtr hhk);

    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

    [DllImport("kernel32.dll", CharSet = CharSet.Auto)]
    private static extern IntPtr GetModuleHandle(string lpModuleName);

    private static readonly Regex MusicUrlRegex = new Regex(
        @"(https?://(?:www\.|open\.|music\.|m\.)?(?:spotify\.com|spotify\.link|spoti\.fi|youtube\.com|youtu\.be)/[^\s<>""'\)\]]+|spotify:(?:playlist|album|track):[A-Za-z0-9]+)",
        RegexOptions.IgnoreCase | RegexOptions.Compiled
    );

    private readonly string _installDir;
    private readonly string _batPath;
    private readonly NotifyIcon _tray;
    private readonly PillForm _pill;
    private readonly Timer _hideTimer;
    private readonly Timer _uiaCheckTimer;
    private readonly LowLevelMouseProc _mouseProc;
    private IntPtr _mouseHook = IntPtr.Zero;
    private POINT _dragStart;
    private bool _dragging = false;
    private string _currentUrl = "";
    private string _lastShownUrl = "";
    private DateTime _lastShownAt = DateTime.MinValue;

    private class PillForm : Form {
        protected override bool ShowWithoutActivation { get { return true; } }
        protected override CreateParams CreateParams {
            get {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= 0x08000000 | 0x00000008 | 0x00000080; // WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
                return cp;
            }
        }
        public PillForm(Icon appIcon, Action onClick) {
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            BackColor = Color.FromArgb(24, 24, 28);
            Size = new Size(212, 38);
            Padding = new Padding(1);

            var btn = new Button();
            btn.Dock = DockStyle.Fill;
            btn.FlatStyle = FlatStyle.Flat;
            btn.FlatAppearance.BorderColor = Color.FromArgb(65, 115, 255);
            btn.FlatAppearance.BorderSize = 1;
            btn.FlatAppearance.MouseOverBackColor = Color.FromArgb(40, 44, 58);
            btn.BackColor = Color.FromArgb(26, 28, 36);
            btn.ForeColor = Color.White;
            btn.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            bool isIT = !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SLSK_LANG"))
                ? Environment.GetEnvironmentVariable("SLSK_LANG").Trim().ToLowerInvariant().StartsWith("it")
                : System.Globalization.CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "it";
            btn.Text = isIT ? "  Scarica con Soulseek" : "  Download with Soulseek";
            btn.TextAlign = ContentAlignment.MiddleCenter;
            btn.Cursor = Cursors.Hand;
            if (appIcon != null) {
                btn.Image = new Bitmap(appIcon.ToBitmap(), new Size(20, 20));
                btn.ImageAlign = ContentAlignment.MiddleLeft;
                btn.TextImageRelation = TextImageRelation.ImageBeforeText;
                btn.Padding = new Padding(8, 0, 6, 0);
            }
            btn.Click += (s, e) => { Hide(); onClick(); };
            Controls.Add(btn);
        }
    }

    public SlskFloatingPillApp(string installDir) {
        _installDir = installDir;
        _batPath = Path.Combine(installDir, "Soulseek Batch Pipeline.bat");

        Icon appIcon = null;
        string icoFile = Path.Combine(installDir, "assets", "app_icon.ico");
        if (File.Exists(icoFile)) {
            try { appIcon = new Icon(icoFile); } catch {}
        }

        CreateHandle(new CreateParams());
        AddClipboardFormatListener(Handle);
        RegisterHotKey(Handle, HOTKEY_ID, MOD_CONTROL | MOD_ALT, (uint)Keys.S);

        _pill = new PillForm(appIcon, () => LaunchDownload(_currentUrl));
        IntPtr ensurePillHandle = _pill.Handle;
        if (ensurePillHandle == IntPtr.Zero) { }

        _hideTimer = new Timer { Interval = 4800 };
        _hideTimer.Tick += (s, e) => { _hideTimer.Stop(); _pill.Hide(); };

        _uiaCheckTimer = new Timer { Interval = 120 };
        _uiaCheckTimer.Tick += (s, e) => {
            _uiaCheckTimer.Stop();
            CheckUiAutomationSelection();
        };

        bool isIT = !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SLSK_LANG"))
            ? Environment.GetEnvironmentVariable("SLSK_LANG").Trim().ToLowerInvariant().StartsWith("it")
            : System.Globalization.CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "it";
        var menu = new ContextMenuStrip();
        menu.Items.Add(isIT ? "Apri Soulseek Batch Download" : "Open Soulseek Batch Download", null, (s, e) => LaunchDownload(""));
        menu.Items.Add(isIT ? "Configurazione iniziale (--setup)" : "Initial setup (--setup)", null, (s, e) => LaunchSetup());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(isIT ? "Esci (disattiva popup rapido)" : "Quit (disable quick popup)", null, (s, e) => Application.ExitThread());

        _tray = new NotifyIcon {
            Icon = appIcon ?? SystemIcons.Application,
            Text = isIT
                ? "Soulseek Batch Download (evidenzia o copia un link Spotify/YouTube)"
                : "Soulseek Batch Download (highlight or copy a Spotify/YouTube link)",
            ContextMenuStrip = menu,
            Visible = true
        };
        _tray.DoubleClick += (s, e) => LaunchDownload("");

        _mouseProc = MouseHookCallback;
        using (var curProc = Process.GetCurrentProcess())
        using (var curMod = curProc.MainModule) {
            _mouseHook = SetWindowsHookEx(WH_MOUSE_LL, _mouseProc, GetModuleHandle(curMod.ModuleName), 0);
        }
    }

    private IntPtr MouseHookCallback(int nCode, IntPtr wParam, IntPtr lParam) {
        if (nCode >= 0) {
            int msg = wParam.ToInt32();
            var info = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
            if (msg == WM_LBUTTONDOWN) {
                if (_pill.Visible && !_pill.Bounds.Contains(info.pt.x, info.pt.y)) {
                    _pill.BeginInvoke((Action)(() => { _hideTimer.Stop(); _pill.Hide(); }));
                }
                _dragStart = info.pt;
                _dragging = true;
            } else if (msg == WM_LBUTTONUP && _dragging) {
                _dragging = false;
                int dx = Math.Abs(info.pt.x - _dragStart.x);
                int dy = Math.Abs(info.pt.y - _dragStart.y);
                if (dx >= 14 || dy >= 14) {
                    try {
                        BeginCheckUia();
                    } catch {}
                }
            }
        }
        return CallNextHookEx(_mouseHook, nCode, wParam, lParam);
    }

    private void BeginCheckUia() {
        if (!_pill.IsHandleCreated) return;
        _pill.BeginInvoke((Action)(() => {
            _uiaCheckTimer.Stop();
            _uiaCheckTimer.Start();
        }));
    }

    private void CheckUiAutomationSelection() {
        try {
            var focused = AutomationElement.FocusedElement;
            if (focused == null) return;
            object patObj;
            if (!focused.TryGetCurrentPattern(TextPattern.Pattern, out patObj)) return;
            var tp = (TextPattern)patObj;
            var ranges = tp.GetSelection();
            if (ranges == null || ranges.Length == 0) return;
            string selected = ranges[0].GetText(512);
            string url = ExtractMusicUrl(selected);
            if (!string.IsNullOrEmpty(url)) {
                ShowPillForUrl(url, force: false);
            }
        } catch {}
    }

    private static string ExtractMusicUrl(string text) {
        if (string.IsNullOrWhiteSpace(text)) return "";
        var m = MusicUrlRegex.Match(text);
        return m.Success ? m.Value.TrimEnd('.', ',', ';', ')', ']') : "";
    }

    private void ShowPillForUrl(string url, bool force) {
        if (string.IsNullOrEmpty(url)) return;
        if (!force && url == _lastShownUrl && (DateTime.UtcNow - _lastShownAt).TotalSeconds < 6) {
            return;
        }
        _currentUrl = url;
        _lastShownUrl = url;
        _lastShownAt = DateTime.UtcNow;

        Point cursor = Cursor.Position;
        Rectangle screen = Screen.FromPoint(cursor).WorkingArea;
        int px = Math.Min(Math.Max(screen.Left + 8, cursor.X - _pill.Width / 2), screen.Right - _pill.Width - 8);
        int py = cursor.Y - _pill.Height - 16;
        if (py < screen.Top + 8) py = cursor.Y + 20;

        _pill.Location = new Point(px, py);
        if (!_pill.Visible) _pill.Show();
        _hideTimer.Stop();
        _hideTimer.Start();
    }

    private void LaunchDownload(string url) {
        try {
            if (!File.Exists(_batPath)) return;
            string args = string.IsNullOrEmpty(url)
                ? "/c \"\"" + _batPath + "\"\""
                : "/c \"\"" + _batPath + "\" --pause \"" + url.Replace("\"", "") + "\"\"";
            Process.Start(new ProcessStartInfo("cmd.exe", args) {
                WorkingDirectory = _installDir,
                UseShellExecute = true
            });
        } catch {}
    }

    private void LaunchSetup() {
        try {
            if (!File.Exists(_batPath)) return;
            Process.Start(new ProcessStartInfo("cmd.exe", "/c \"\"" + _batPath + "\" --setup\"") {
                WorkingDirectory = _installDir,
                UseShellExecute = true
            });
        } catch {}
    }

    protected override void WndProc(ref Message m) {
        if (m.Msg == WM_CLIPBOARDUPDATE) {
            try {
                if (Clipboard.ContainsText()) {
                    string url = ExtractMusicUrl(Clipboard.GetText());
                    if (!string.IsNullOrEmpty(url)) {
                        ShowPillForUrl(url, force: false);
                    }
                }
            } catch {}
        } else if (m.Msg == WM_HOTKEY && m.WParam.ToInt32() == HOTKEY_ID) {
            CheckUiAutomationSelection();
            if (!_pill.Visible) {
                try {
                    if (Clipboard.ContainsText()) {
                        string url = ExtractMusicUrl(Clipboard.GetText());
                        if (!string.IsNullOrEmpty(url)) {
                            LaunchDownload(url);
                            return;
                        }
                    }
                } catch {}
                LaunchDownload("");
            }
        }
        base.WndProc(ref m);
    }

    public void Dispose() {
        if (_mouseHook != IntPtr.Zero) { UnhookWindowsHookEx(_mouseHook); _mouseHook = IntPtr.Zero; }
        UnregisterHotKey(Handle, HOTKEY_ID);
        RemoveClipboardFormatListener(Handle);
        _hideTimer.Dispose();
        _uiaCheckTimer.Dispose();
        _pill.Dispose();
        _tray.Visible = false;
        _tray.Dispose();
        DestroyHandle();
    }
}
"@

Add-Type -TypeDefinition $csharp -ReferencedAssemblies @(
    "System.Windows.Forms",
    "System.Drawing",
    "UIAutomationClient",
    "UIAutomationTypes"
)

[System.Windows.Forms.Application]::EnableVisualStyles()
$app = New-Object SlskFloatingPillApp($InstallDir)
try {
    [System.Windows.Forms.Application]::Run()
} finally {
    $app.Dispose()
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
