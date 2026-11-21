param(
    [string]$DefaultDir = "",
    [string]$DefaultUser = "",
    [string]$DefaultFormat = "flac"
)

[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

if ([string]::IsNullOrWhiteSpace($DefaultDir)) {
    $DefaultDir = Join-Path ([Environment]::GetFolderPath("MyMusic")) "Soulseek Downloads"
}

function Normalize-Format([string]$raw) {
    $v = ($raw -replace '^\.+', '').Trim().ToLowerInvariant()
    if ($v -in @("aiff", "aif", "1", "true", "yes")) { return "aiff" }
    if ($v -in @("mp3", "mp3_320", "mp3-320", "320")) { return "mp3" }
    return "flac"
}

$isIT = if (-not [string]::IsNullOrWhiteSpace($env:SLSK_LANG)) {
    $env:SLSK_LANG.Trim().ToLowerInvariant().StartsWith("it")
} else {
    [System.Globalization.CultureInfo]::CurrentUICulture.TwoLetterISOLanguageName -eq "it"
}

$fmtNorm = Normalize-Format $DefaultFormat
$formatKeys = @("flac", "aiff", "mp3")
$formatLabels = @(
    ".flac (lossless)",
    ".aiff (PCM 24-bit lossless)",
    ".mp3 (320 kbps)"
)

$form = New-Object System.Windows.Forms.Form
$form.Text = if ($isIT) { "Soulseek Batch Download — Configurazione iniziale" } else { "Soulseek Batch Download — Initial Setup" }
$form.Size = New-Object System.Drawing.Size(520, 390)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)

$iconPath = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "assets\app_icon.ico"
if (Test-Path $iconPath) {
    try { $form.Icon = New-Object System.Drawing.Icon($iconPath) } catch {}
}

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = if ($isIT) { "Benvenuto! Configura il tuo account Soulseek" } else { "Welcome! Set up your Soulseek account" }
$lblTitle.Font = New-Object System.Drawing.Font("Segoe UI", 11.5, [System.Drawing.FontStyle]::Bold)
$lblTitle.Location = New-Object System.Drawing.Point(20, 16)
$lblTitle.Size = New-Object System.Drawing.Size(465, 24)
$form.Controls.Add($lblTitle)

$lblSub = New-Object System.Windows.Forms.Label
$lblSub.Text = if ($isIT) { "Credenziali e cartella vengono chieste solo al primo avvio. Nelle sessioni successive non dovrai reinserirle." } else { "Credentials and download folder are only asked on first launch. You won't be asked again." }
$lblSub.ForeColor = [System.Drawing.Color]::DimGray
$lblSub.Location = New-Object System.Drawing.Point(20, 44)
$lblSub.Size = New-Object System.Drawing.Size(465, 34)
$form.Controls.Add($lblSub)

$lblUser = New-Object System.Windows.Forms.Label
$lblUser.Text = if ($isIT) { "Username Soulseek:" } else { "Soulseek Username:" }
$lblUser.Location = New-Object System.Drawing.Point(20, 86)
$lblUser.Size = New-Object System.Drawing.Size(465, 18)
$form.Controls.Add($lblUser)

$txtUser = New-Object System.Windows.Forms.TextBox
$txtUser.Text = $DefaultUser
$txtUser.Location = New-Object System.Drawing.Point(20, 108)
$txtUser.Size = New-Object System.Drawing.Size(465, 25)
$form.Controls.Add($txtUser)

$lblPass = New-Object System.Windows.Forms.Label
$lblPass.Text = if ($isIT) { "Password Soulseek:" } else { "Soulseek Password:" }
$lblPass.Location = New-Object System.Drawing.Point(20, 142)
$lblPass.Size = New-Object System.Drawing.Size(465, 18)
$form.Controls.Add($lblPass)

$txtPass = New-Object System.Windows.Forms.TextBox
$txtPass.UseSystemPasswordChar = $true
$txtPass.Location = New-Object System.Drawing.Point(20, 164)
$txtPass.Size = New-Object System.Drawing.Size(465, 25)
$form.Controls.Add($txtPass)

$lblDir = New-Object System.Windows.Forms.Label
$lblDir.Text = if ($isIT) { "Cartella di download:" } else { "Download folder:" }
$lblDir.Location = New-Object System.Drawing.Point(20, 198)
$lblDir.Size = New-Object System.Drawing.Size(465, 18)
$form.Controls.Add($lblDir)

$txtDir = New-Object System.Windows.Forms.TextBox
$txtDir.Text = $DefaultDir
$txtDir.Location = New-Object System.Drawing.Point(20, 220)
$txtDir.Size = New-Object System.Drawing.Size(370, 25)
$form.Controls.Add($txtDir)

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = if ($isIT) { "Sfoglia..." } else { "Browse..." }
$btnBrowse.Location = New-Object System.Drawing.Point(398, 219)
$btnBrowse.Size = New-Object System.Drawing.Size(87, 27)
$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = if ($isIT) { "Seleziona la cartella in cui salvare i download di Soulseek" } else { "Select the folder where Soulseek downloads will be saved" }
    $dlg.ShowNewFolderButton = $true
    if (Test-Path $txtDir.Text) { $dlg.SelectedPath = $txtDir.Text }
    if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
        $txtDir.Text = $dlg.SelectedPath
    }
})
$form.Controls.Add($btnBrowse)

$lblFmt = New-Object System.Windows.Forms.Label
$lblFmt.Text = if ($isIT) { "formato" } else { "format" }
$lblFmt.Location = New-Object System.Drawing.Point(20, 254)
$lblFmt.Size = New-Object System.Drawing.Size(465, 18)
$form.Controls.Add($lblFmt)

$cmbFmt = New-Object System.Windows.Forms.ComboBox
$cmbFmt.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
$cmbFmt.Location = New-Object System.Drawing.Point(20, 276)
$cmbFmt.Size = New-Object System.Drawing.Size(465, 25)
foreach ($item in $formatLabels) { [void]$cmbFmt.Items.Add($item) }
$selIdx = [Array]::IndexOf($formatKeys, $fmtNorm)
$cmbFmt.SelectedIndex = if ($selIdx -ge 0) { $selIdx } else { 0 }
$form.Controls.Add($cmbFmt)

$lblErr = New-Object System.Windows.Forms.Label
$lblErr.Text = ""
$lblErr.ForeColor = [System.Drawing.Color]::Firebrick
$lblErr.Location = New-Object System.Drawing.Point(20, 314)
$lblErr.Size = New-Object System.Drawing.Size(260, 22)
$form.Controls.Add($lblErr)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = if ($isIT) { "Annulla" } else { "Cancel" }
$btnCancel.Location = New-Object System.Drawing.Point(290, 310)
$btnCancel.Size = New-Object System.Drawing.Size(88, 30)
$btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.CancelButton = $btnCancel
$form.Controls.Add($btnCancel)

$script:Confirmed = $false
$btnSave = New-Object System.Windows.Forms.Button
$btnSave.Text = if ($isIT) { "Salva e avvia" } else { "Save & start" }
$btnSave.Location = New-Object System.Drawing.Point(385, 310)
$btnSave.Size = New-Object System.Drawing.Size(100, 30)
$btnSave.Add_Click({
    if ([string]::IsNullOrWhiteSpace($txtUser.Text)) {
        $lblErr.Text = if ($isIT) { "Inserisci il tuo username Soulseek." } else { "Please enter your Soulseek username." }
        $txtUser.Focus()
        return
    }
    if ([string]::IsNullOrEmpty($txtPass.Text)) {
        $lblErr.Text = if ($isIT) { "Inserisci la tua password Soulseek." } else { "Please enter your Soulseek password." }
        $txtPass.Focus()
        return
    }
    if ([string]::IsNullOrWhiteSpace($txtDir.Text)) {
        $lblErr.Text = if ($isIT) { "Scegli una cartella di download." } else { "Please choose a download folder." }
        $txtDir.Focus()
        return
    }
    $script:Confirmed = $true
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})
$form.AcceptButton = $btnSave
$form.Controls.Add($btnSave)

$form.Add_Shown({
    $form.Activate()
    $txtUser.Focus()
})

[void]$form.ShowDialog()

$idx = [Math]::Max(0, [Math]::Min($cmbFmt.SelectedIndex, $formatKeys.Length - 1))
$result = [ordered]@{
    confirmed      = [bool]$script:Confirmed
    username       = $txtUser.Text.Trim()
    password       = $txtPass.Text
    output_dir     = $txtDir.Text.Trim()
    convert_format = $formatKeys[$idx]
}
Write-Output ($result | ConvertTo-Json -Compress)
if ($script:Confirmed) { exit 0 } else { exit 1 }
