param(
    [string]$Url = "",
    [string]$Label = "",
    [string]$DefaultFormat = "flac"
)

[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

function Normalize-Format([string]$raw) {
    $v = ($raw -replace '^\.+', '').Trim().ToLowerInvariant()
    if ($v -in @("aiff", "aif", "1", "true", "yes")) { return "aiff" }
    if ($v -in @("mp3", "mp3_320", "mp3-320", "320")) { return "mp3" }
    return "flac"
}

function Get-ClipboardMusicUrl {
    try {
        if ([System.Windows.Forms.Clipboard]::ContainsText()) {
            $clip = [System.Windows.Forms.Clipboard]::GetText().Trim()
            $low = $clip.ToLowerInvariant()
            if ($low.Contains("spotify.com/") -or $low.Contains("spotify.link/") -or $low.Contains("spoti.fi/") -or
                $low.Contains("spotify:") -or $low.Contains("youtube.com/") -or $low.Contains("youtu.be/")) {
                return $clip
            }
        }
    } catch {}
    return ""
}

$fmtNorm = Normalize-Format $DefaultFormat
$formatKeys = @("flac", "aiff", "mp3")
$formatLabels = @(
    ".flac (lossless)",
    ".aiff (PCM 24-bit lossless)",
    ".mp3 (320 kbps)"
)

$needsInput = [string]::IsNullOrWhiteSpace($Url)
$formHeight = if ($needsInput) { 245 } else { 225 }

$form = New-Object System.Windows.Forms.Form
$form.Text = "Soulseek Batch Download"
$form.Size = New-Object System.Drawing.Size(500, $formHeight)
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

$txtUrl = $null
$y = 16

if ($needsInput) {
    $lblInfo = New-Object System.Windows.Forms.Label
    $lblInfo.Text = "Incolla un URL Spotify o YouTube (playlist, album o video full album):"
    $lblInfo.Location = New-Object System.Drawing.Point(20, $y)
    $lblInfo.Size = New-Object System.Drawing.Size(445, 22)
    $form.Controls.Add($lblInfo)
    $y += 26

    $txtUrl = New-Object System.Windows.Forms.TextBox
    $txtUrl.Location = New-Object System.Drawing.Point(20, $y)
    $txtUrl.Size = New-Object System.Drawing.Size(445, 25)
    $clipUrl = Get-ClipboardMusicUrl
    if (-not [string]::IsNullOrWhiteSpace($clipUrl)) {
        $txtUrl.Text = $clipUrl
    }
    $form.Controls.Add($txtUrl)
    $y += 38
} else {
    $heading = if (-not [string]::IsNullOrWhiteSpace($Label)) { $Label } else { "Link rilevato" }
    $lblHead = New-Object System.Windows.Forms.Label
    $lblHead.Text = "${heading}:"
    $lblHead.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
    $lblHead.Location = New-Object System.Drawing.Point(20, $y)
    $lblHead.Size = New-Object System.Drawing.Size(445, 20)
    $form.Controls.Add($lblHead)
    $y += 22

    $lblUrl = New-Object System.Windows.Forms.Label
    $lblUrl.Text = $Url
    $lblUrl.ForeColor = [System.Drawing.Color]::DimGray
    $lblUrl.AutoEllipsis = $true
    $lblUrl.Location = New-Object System.Drawing.Point(20, $y)
    $lblUrl.Size = New-Object System.Drawing.Size(445, 24)
    $form.Controls.Add($lblUrl)
    $y += 32
}

$lblFmt = New-Object System.Windows.Forms.Label
$lblFmt.Text = "formato"
$lblFmt.Location = New-Object System.Drawing.Point(20, $y)
$lblFmt.Size = New-Object System.Drawing.Size(445, 18)
$form.Controls.Add($lblFmt)
$y += 22

$cmbFmt = New-Object System.Windows.Forms.ComboBox
$cmbFmt.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
$cmbFmt.Location = New-Object System.Drawing.Point(20, $y)
$cmbFmt.Size = New-Object System.Drawing.Size(445, 25)
foreach ($item in $formatLabels) { [void]$cmbFmt.Items.Add($item) }
$selIdx = [Array]::IndexOf($formatKeys, $fmtNorm)
$cmbFmt.SelectedIndex = if ($selIdx -ge 0) { $selIdx } else { 0 }
$form.Controls.Add($cmbFmt)
$y += 42

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Annulla"
$btnCancel.Location = New-Object System.Drawing.Point(280, $y)
$btnCancel.Size = New-Object System.Drawing.Size(88, 30)
$btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.CancelButton = $btnCancel
$form.Controls.Add($btnCancel)

$script:Confirmed = $false
$btnGo = New-Object System.Windows.Forms.Button
$btnGo.Text = "Scarica"
$btnGo.Location = New-Object System.Drawing.Point(377, $y)
$btnGo.Size = New-Object System.Drawing.Size(88, 30)
$btnGo.Add_Click({
    $script:Confirmed = $true
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})
$form.AcceptButton = $btnGo
$form.Controls.Add($btnGo)

$form.Add_Shown({
    $form.Activate()
    if ($null -ne $txtUrl) { $txtUrl.Focus(); $txtUrl.SelectAll() } else { $btnGo.Focus() }
})

[void]$form.ShowDialog()

$finalUrl = if ($null -ne $txtUrl) { $txtUrl.Text.Trim() } else { $Url.Trim() }
$idx = [Math]::Max(0, [Math]::Min($cmbFmt.SelectedIndex, $formatKeys.Length - 1))
$ok = $script:Confirmed -and (-not [string]::IsNullOrWhiteSpace($finalUrl))

$result = [ordered]@{
    confirmed      = [bool]$ok
    url            = $finalUrl
    convert_format = $formatKeys[$idx]
}
Write-Output ($result | ConvertTo-Json -Compress)
if ($ok) { exit 0 } else { exit 1 }
