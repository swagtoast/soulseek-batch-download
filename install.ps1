$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoSlug = if ($env:SLSK_REPO_SLUG) { $env:SLSK_REPO_SLUG } else { "swagtoast/soulseek-batch-download" }
$RepoBranch = if ($env:SLSK_REPO_BRANCH) { $env:SLSK_REPO_BRANCH } else { "main" }
$InstallDir = if ($env:SLSK_INSTALL_DIR) { $env:SLSK_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Soulseek Batch Download" }
$SockseekVersion = "3.0.6"
$FfmpegRelease = "b6.1.1"
$PythonTag = "20261110"
$PythonVer = "3.12.16"

$Arch = $env:PROCESSOR_ARCHITECTURE
$OsVer = [System.Environment]::OSVersion.Version.ToString()
$CpuCores = [Environment]::ProcessorCount
$RamGb = 8
try {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
    $RamGb = [Math]::Max(1, [int][Math]::Round($cs.TotalPhysicalMemory / 1GB))
} catch {}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Soulseek Batch Download — Installer Windows" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "Profilo PC: Windows ($Arch) | v$OsVer | $CpuCores core | $RamGb GB RAM"
Write-Host "Installazione in: $InstallDir"
Write-Host ""

function Download-File([string]$Url, [string]$Dest) {
    $curl = Get-Command "curl.exe" -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -fL --progress-bar "$Url" -o "$Dest"
        if ($LASTEXITCODE -ne 0) { throw "Download fallito: $Url" }
    } else {
        $prev = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
        } finally {
            $ProgressPreference = $prev
        }
    }
}

function Test-ValidBin([string]$Path) {
    return (Test-Path $Path -PathType Leaf) -and ((Get-Item $Path).Length -gt 0)
}

$TmpWork = Join-Path ([System.IO.Path]::GetTempPath()) ("slsk_win_install_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $TmpWork -Force | Out-Null

try {
    foreach ($sub in @("bin", "config", "lists")) {
        New-Item -ItemType Directory -Path (Join-Path $InstallDir $sub) -Force | Out-Null
    }

    Write-Host "[1/6] Scarico l'app da GitHub ($RepoSlug)..."
    $repoZip = Join-Path $TmpWork "repo.zip"
    Download-File "https://github.com/$RepoSlug/archive/refs/heads/$RepoBranch.zip" $repoZip
    $repoExtract = Join-Path $TmpWork "repo"
    Expand-Archive -Path $repoZip -DestinationPath $repoExtract -Force
    $srcRoot = (Get-ChildItem -Path $repoExtract -Directory | Select-Object -First 1).FullName

    foreach ($dirName in @("src", "tests", "assets", "browser-extension")) {
        $dstSub = Join-Path $InstallDir $dirName
        if (Test-Path $dstSub) { Remove-Item $dstSub -Recurse -Force }
        $srcSub = Join-Path $srcRoot $dirName
        if (Test-Path $srcSub) { Copy-Item $srcSub $dstSub -Recurse -Force }
    }
    Copy-Item (Join-Path $srcRoot "Soulseek Batch Pipeline.bat") (Join-Path $InstallDir "Soulseek Batch Pipeline.bat") -Force
    Copy-Item (Join-Path $srcRoot "README.md") (Join-Path $InstallDir "README.md") -Force
    foreach ($ex in @("sockseek.conf.example", "pipeline.json.example")) {
        $exSrc = Join-Path (Join-Path $srcRoot "config") $ex
        if (Test-Path $exSrc) { Copy-Item $exSrc (Join-Path (Join-Path $InstallDir "config") $ex) -Force }
    }

    # Seed auto-updater commit state
    try {
        $hdr = @{ "Accept" = "application/vnd.github.sha"; "User-Agent" = "soulseek-batch-download-installer/1.0" }
        $sha = (Invoke-RestMethod -Uri "https://api.github.com/repos/$RepoSlug/commits/$RepoBranch" -Headers $hdr -TimeoutSec 4).ToString().Trim()
        if ($sha.Length -eq 40) {
            $epoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            $stateJson = "{`"commit`": `"$sha`", `"last_check`": $epoch}`n"
            [System.IO.File]::WriteAllText((Join-Path $InstallDir "config\.update_state.json"), $stateJson, (New-Object System.Text.UTF8Encoding($false)))
        }
    } catch {}

    Write-Host "[2/6] Verifico Python 3..."
    $PythonBin = Join-Path $InstallDir "runtime\python\python.exe"
    if (-not (Test-ValidBin $PythonBin)) {
        $foundSysPy = $null
        foreach ($cmdName in @("python", "python3")) {
            $c = Get-Command $cmdName -ErrorAction SilentlyContinue
            if ($c -and $c.Source -notlike "*WindowsApps*") {
                & $c.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    $foundSysPy = $c.Source
                    break
                }
            }
        }
        if ($foundSysPy) {
            $PythonBin = $foundSysPy
        } else {
            Write-Host "      Scarico Python $PythonVer standalone..."
            $pyTar = Join-Path $TmpWork "python.tar.gz"
            $pyUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$PythonTag/cpython-$PythonVer%2B$PythonTag-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
            Download-File $pyUrl $pyTar
            $runtimeDir = Join-Path $InstallDir "runtime"
            New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
            if (Test-Path (Join-Path $runtimeDir "python")) { Remove-Item (Join-Path $runtimeDir "python") -Recurse -Force }
            & tar.exe -xzf "$pyTar" -C "$runtimeDir"
            if ($LASTEXITCODE -ne 0) { throw "Estrazione Python fallita." }
            $PythonBin = Join-Path $runtimeDir "python\python.exe"
        }
    }
    Write-Host "      Python pronto: $PythonBin"

    Write-Host "[3/6] Preparo Sockseek v$SockseekVersion (win-x64)..."
    $sockBin = Join-Path $InstallDir "bin\sockseek.exe"
    if (Test-ValidBin $sockBin) {
        Write-Host "      sockseek.exe gia presente."
    } else {
        $sockZip = Join-Path $TmpWork "sockseek.zip"
        Download-File "https://github.com/fiso64/sockseek/releases/download/v$SockseekVersion/sockseek_${SockseekVersion}_win-x64.zip" $sockZip
        Expand-Archive -Path $sockZip -DestinationPath (Join-Path $InstallDir "bin") -Force
    }

    Write-Host "[4/6] Preparo yt-dlp e deno (per YouTube)..."
    $ytBin = Join-Path $InstallDir "bin\yt-dlp.exe"
    if (Test-ValidBin $ytBin) {
        Write-Host "      yt-dlp.exe gia presente."
    } else {
        Download-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" $ytBin
    }

    $denoBin = Join-Path $InstallDir "bin\deno.exe"
    if (Test-ValidBin $denoBin) {
        Write-Host "      deno.exe gia presente."
    } else {
        $denoZip = Join-Path $TmpWork "deno.zip"
        Download-File "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" $denoZip
        Expand-Archive -Path $denoZip -DestinationPath (Join-Path $InstallDir "bin") -Force
    }

    Write-Host "[5/6] Preparo ffmpeg e ffprobe (conversione AIFF / MP3 320)..."
    $ffBin = Join-Path $InstallDir "bin\ffmpeg.exe"
    if (Test-ValidBin $ffBin) {
        Write-Host "      ffmpeg.exe gia presente."
    } else {
        Download-File "https://github.com/eugeneware/ffmpeg-static/releases/download/$FfmpegRelease/ffmpeg-win32-x64" $ffBin
    }

    $fpBin = Join-Path $InstallDir "bin\ffprobe.exe"
    if (Test-ValidBin $fpBin) {
        Write-Host "      ffprobe.exe gia presente."
    } else {
        Download-File "https://github.com/eugeneware/ffmpeg-static/releases/download/$FfmpegRelease/ffprobe-win32-x64" $fpBin
    }

    Write-Host "[6/6] Configuro icona Desktop, protocollo soulseekdl:// e popup rapido..."
    $batPath = Join-Path $InstallDir "Soulseek Batch Pipeline.bat"
    $icoPath = Join-Path $InstallDir "assets\app_icon.ico"
    $vbsPill = Join-Path $InstallDir "src\win\launch_pill.vbs"

    # Desktop + Start Menu shortcuts with custom Swagtoast + Soulseek icon
    $wshell = New-Object -ComObject WScript.Shell
    $shortcutTargets = @(
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Soulseek Batch Download.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Programs")) "Soulseek Batch Download.lnk")
    )
    foreach ($lnkPath in $shortcutTargets) {
        try {
            $sc = $wshell.CreateShortcut($lnkPath)
            $sc.TargetPath = $batPath
            $sc.WorkingDirectory = $InstallDir
            $sc.Description = "Scarica playlist e album Spotify/YouTube da Soulseek"
            if (Test-Path $icoPath) { $sc.IconLocation = "$icoPath,0" }
            $sc.Save()
        } catch {}
    }

    # Register soulseekdl:// URI handler in HKCU (no admin required)
    $protoKey = "HKCU:\Software\Classes\soulseekdl"
    New-Item -Path "$protoKey\shell\open\command" -Force | Out-Null
    Set-ItemProperty -Path $protoKey -Name "(Default)" -Value "URL:Soulseek Batch Download"
    Set-ItemProperty -Path $protoKey -Name "URL Protocol" -Value ""
    if (Test-Path $icoPath) {
        New-Item -Path "$protoKey\DefaultIcon" -Force | Out-Null
        Set-ItemProperty -Path "$protoKey\DefaultIcon" -Name "(Default)" -Value "`"$icoPath`",0"
    }
    Set-ItemProperty -Path "$protoKey\shell\open\command" -Name "(Default)" -Value "cmd.exe /c `"`"`"$batPath`" --pause `"%1`"`"`""

    # Register & start the lightweight Floating Pill helper (auto-appears when you highlight or copy a Spotify/YouTube link)
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    Set-ItemProperty -Path $runKey -Name "SoulseekBatchDownloadPill" -Value "wscript.exe `"$vbsPill`""
    try {
        Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*slsk_floating_pill.ps1*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch {}
    if (-not $env:SLSK_SKIP_PILL) {
        Start-Process "wscript.exe" -ArgumentList "`"$vbsPill`"" -WindowStyle Hidden
    }

    # First-Run Setup window (credentials + Explorer 'Sfoglia...' folder picker + format)
    $sockConf = Join-Path $InstallDir "config\sockseek.conf"
    if ((-not $env:SLSK_SKIP_SETUP) -and (-not (Test-Path $sockConf))) {
        Write-Host ""
        Write-Host "Apro la finestra di configurazione iniziale (credenziali Soulseek + cartella download)..."
        & $PythonBin (Join-Path $InstallDir "src\cli.py") --setup
    }

    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "  Installazione Windows completata!" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "Cartella app: $InstallDir"
    Write-Host ""
    Write-Host "Come usarlo:"
    Write-Host "  1. Evidenzia (o copia con Ctrl+C) un link Spotify o YouTube ovunque:"
    Write-Host "     compare subito il pulsante 'Scarica con Soulseek' vicino al cursore!"
    Write-Host "  2. Oppure premi Ctrl+Alt+S, o fai doppio click sull'icona sul Desktop."
    Write-Host "  3. (Opzionale) Per il tasto destro puro su Chrome/Edge, carica la cartella:"
    Write-Host "     $InstallDir\browser-extension"
    Write-Host ""
} finally {
    if (Test-Path $TmpWork) { Remove-Item $TmpWork -Recurse -Force -ErrorAction SilentlyContinue }
}
