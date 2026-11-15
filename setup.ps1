# Club Autopilot Windows setup for the launchpad branch.
# Windows PowerShell 5.1. UTF-8 BOM so Chinese messages parse.
# Paste as ONE line (ExecutionPolicy is set by the caller):
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/cmy-hulala/club-autopilot/launchpad/setup.ps1 | iex"
# When that pipes into iex, $PSScriptRoot is empty. Do not rely on it.
# Captured here, before the script block, so -File still sees the script folder.
$ClubSetupScriptRoot = ''
if ($PSScriptRoot) {
    $ClubSetupScriptRoot = $PSScriptRoot
} elseif ($MyInvocation.MyCommand.Path) {
    $ClubSetupScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $ConfirmPreference = 'None'
    $OnboardingUrl = 'http://127.0.0.1:5050/onboarding'
    $RepoUrl = 'https://github.com/cmy-hulala/club-autopilot.git'
    $Branch = 'launchpad'

    function Say([string]$Message) {
        Write-Host ''
        Write-Host $Message -ForegroundColor Cyan
    }

    function Fail([string]$Message) {
        Write-Host ''
        Write-Host "失敗 / Failed: $Message" -ForegroundColor Red
        exit 1
    }

    trap {
        Fail $_.Exception.Message
    }

    function Test-UnsafeDir([string]$Dir) {
        if ([string]::IsNullOrWhiteSpace($Dir)) { return $true }
        $full = [System.IO.Path]::GetFullPath($Dir)
        $windir = [System.IO.Path]::GetFullPath($env:WINDIR)
        return $full.StartsWith($windir, [System.StringComparison]::OrdinalIgnoreCase)
    }

    function Test-StoreAlias([string]$Path) {
        if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
        if ($Path -like '*\WindowsApps\*') { return $true }
        $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
        if ($item -and $item.Length -eq 0) { return $true }
        return $false
    }

    function Test-RepoDir([string]$Dir) {
        if ([string]::IsNullOrWhiteSpace($Dir)) { return $false }
        if (-not (Test-Path -LiteralPath $Dir)) { return $false }
        if (Test-UnsafeDir $Dir) { return $false }
        $req = Join-Path $Dir 'requirements.txt'
        $pkg = Join-Path $Dir 'autopilot\__main__.py'
        $git = Join-Path $Dir '.git'
        return (Test-Path -LiteralPath $req) -and (Test-Path -LiteralPath $pkg) -and (Test-Path -LiteralPath $git)
    }

    function Update-SessionPath {
        $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $user = [Environment]::GetEnvironmentVariable('Path', 'User')
        $parts = @()
        if ($user) { $parts += $user }
        if ($machine) { $parts += $machine }
        if ($env:Path) { $parts += $env:Path }
        $env:Path = ($parts -join ';')
    }

    function Find-Winget {
        Update-SessionPath
        $known = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
        if (Test-Path -LiteralPath $known) { return $known }
        $cmd = Get-Command winget -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { return $cmd.Source }
        return $null
    }

    function Install-WithWinget([string]$Id) {
        $winget = Find-Winget
        if (-not $winget) {
            Fail "找不到 winget。請到 Microsoft Store 安裝 App Installer，然後再貼一次同一行。 / winget was not found. Install App Installer from the Microsoft Store, then paste the same line again."
        }
        Say "正在用 winget 安裝 $Id … / Installing $Id with winget…"
        & $winget install --exact --id $Id --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
        $code = $LASTEXITCODE
        $already = ($code -eq 0 -or $code -eq -1978335189)
        if (-not $already) {
            Say "使用者層級安裝沒有完成，改試系統層級… / The user-scope install did not finish. Trying a machine-wide install…"
            & $winget install --exact --id $Id --accept-package-agreements --accept-source-agreements --disable-interactivity
            $code = $LASTEXITCODE
        }
        Update-SessionPath
        return $code
    }

    function Get-PythonVersion([string]$Exe) {
        if (Test-StoreAlias $Exe) { return $null }
        if (-not (Test-Path -LiteralPath $Exe)) { return $null }
        $out = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $text = ($out | Select-Object -Last 1)
        if (-not $text) { return $null }
        return $text.ToString().Trim()
    }

    function Test-Py312OrNewer([string]$Ver) {
        if ($Ver -match '^(\d+)\.(\d+)$') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3) { return $true }
            if ($major -eq 3 -and $minor -ge 12) { return $true }
        }
        return $false
    }

    function Add-PythonCandidate([System.Collections.Generic.List[string]]$List, [string]$Exe) {
        if ([string]::IsNullOrWhiteSpace($Exe)) { return }
        if (Test-StoreAlias $Exe) { return }
        if (-not (Test-Path -LiteralPath $Exe)) { return }
        $full = [System.IO.Path]::GetFullPath($Exe)
        if ($List.Contains($full)) { return }
        $List.Add($full)
    }

    function Find-RealPython {
        $list = New-Object 'System.Collections.Generic.List[string]'
        $regKeys = @(
            'HKCU:\Software\Python\PythonCore\3.12\InstallPath',
            'HKLM:\Software\Python\PythonCore\3.12\InstallPath',
            'HKLM:\Software\WOW6432Node\Python\PythonCore\3.12\InstallPath'
        )
        foreach ($key in $regKeys) {
            if (-not (Test-Path -LiteralPath $key)) { continue }
            $item = Get-Item -LiteralPath $key
            Add-PythonCandidate $list $item.GetValue('ExecutablePath')
            $dir = $item.GetValue('')
            if ($dir) { Add-PythonCandidate $list (Join-Path $dir 'python.exe') }
        }
        $known = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
            (Join-Path $env:ProgramFiles 'Python312\python.exe')
        )
        $pf86 = ${env:ProgramFiles(x86)}
        if ($pf86) { $known += (Join-Path $pf86 'Python312\python.exe') }
        foreach ($exe in $known) { Add-PythonCandidate $list $exe }
        $launcher = Join-Path $env:WINDIR 'py.exe'
        if (Test-Path -LiteralPath $launcher) {
            $exe = & $launcher -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) {
                Add-PythonCandidate $list (($exe | Select-Object -Last 1).ToString().Trim())
            }
        }
        foreach ($dir in ($env:Path -split ';')) {
            if ([string]::IsNullOrWhiteSpace($dir)) { continue }
            if ($dir -like '*WindowsApps*') { continue }
            Add-PythonCandidate $list (Join-Path $dir.Trim() 'python.exe')
        }
        $exact = $null
        $newer = $null
        foreach ($exe in $list) {
            $ver = Get-PythonVersion $exe
            if (-not (Test-Py312OrNewer $ver)) { continue }
            if ($ver -eq '3.12' -and -not $exact) { $exact = $exe }
            if (-not $newer) { $newer = $exe }
        }
        if ($exact) { return $exact }
        return $newer
    }

    function Find-RealGit {
        $known = @(
            (Join-Path $env:ProgramFiles 'Git\cmd\git.exe')
        )
        $pf86 = ${env:ProgramFiles(x86)}
        if ($pf86) { $known += (Join-Path $pf86 'Git\cmd\git.exe') }
        foreach ($exe in $known) {
            if (Test-Path -LiteralPath $exe) { return $exe }
        }
        $cmd = Get-Command git -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and -not (Test-StoreAlias $cmd.Source)) { return $cmd.Source }
        return $null
    }

    $script:DidUpdateRepo = $false

    function Update-Repo([string]$Repo, [string]$Git) {
        Say "正在更新 launchpad 分支… / Updating the launchpad branch…"
        Push-Location -LiteralPath $Repo
        try {
            & $Git fetch origin $Branch
            if ($LASTEXITCODE -ne 0) {
                $current = (& $Git rev-parse --abbrev-ref HEAD).ToString().Trim()
                if ($current -eq $Branch) {
                    Say "連不到 GitHub，沿用這台電腦上的 launchpad。 / Could not reach GitHub. Using the launchpad checkout already on this computer."
                    return
                }
                Fail "git fetch 失敗。請檢查網路後再貼一次。 / git fetch failed. Check the network and paste the line again."
            }
            & $Git checkout $Branch
            if ($LASTEXITCODE -ne 0) {
                Fail "無法切換到 launchpad。若資料夾裡有未提交的修改，請先處理再重跑。 / Could not check out launchpad. If this folder has uncommitted changes, keep them somewhere else and run again."
            }
            & $Git pull --ff-only origin $Branch
            if ($LASTEXITCODE -ne 0) {
                Fail "無法快轉更新 launchpad（本地提交和遠端不一致）。 / Could not fast-forward launchpad. Local commits differ from the remote."
            }
            $script:DidUpdateRepo = $true
        } finally {
            Pop-Location
        }
    }

    function Find-ExistingRepo {
        if (Test-RepoDir $ClubSetupScriptRoot) {
            return [System.IO.Path]::GetFullPath($ClubSetupScriptRoot)
        }
        $start = (Get-Location).Path
        if (Test-UnsafeDir $start) { return $null }
        $dir = $start
        $i = 0
        while ($i -lt 6 -and $dir) {
            if (Test-RepoDir $dir) { return [System.IO.Path]::GetFullPath($dir) }
            $parent = Split-Path -Parent $dir
            if (-not $parent -or $parent -eq $dir) { break }
            $dir = $parent
            $i = $i + 1
        }
        return $null
    }

    function Test-OnboardingUp {
        try {
            $resp = Invoke-WebRequest -Uri $OnboardingUrl -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) { return $true }
        } catch {
            return $false
        }
        return $false
    }

    function Open-Onboarding {
        try {
            Start-Process $OnboardingUrl
        } catch {
            Say "請手動打開這個網址 / Open this address yourself: $OnboardingUrl"
        }
    }

    function Get-PrivateDir {
        if ($env:AUTOPILOT_PRIVATE_DIR) { return $env:AUTOPILOT_PRIVATE_DIR }
        $homeDir = $HOME
        if (-not $homeDir) { $homeDir = $env:USERPROFILE }
        $legacy = Join-Path $homeDir '創創社-私密'
        if (Test-Path -LiteralPath $legacy -PathType Container) { return $legacy }
        return (Join-Path $homeDir 'ClubAutopilot-private')
    }

    function Install-LaunchShortcuts([string]$Repo) {
        # Overwrites the same .lnk names, so running setup again does not pile up copies.
        if ([string]::IsNullOrWhiteSpace($Repo)) { return }
        $start = Join-Path $Repo 'start.ps1'
        if (-not (Test-Path -LiteralPath $start)) {
            Say "找不到 start.ps1，這次沒有建立捷徑。 / start.ps1 was not found, so no shortcut was created."
            return
        }
        $icon = Join-Path $Repo 'assets\favicon.ico'
        $psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $desktop = [Environment]::GetFolderPath('Desktop')
        if ([string]::IsNullOrWhiteSpace($desktop)) {
            $homeDir = $HOME
            if (-not $homeDir) { $homeDir = $env:USERPROFILE }
            $desktop = Join-Path $homeDir 'Desktop'
        }
        $programs = [Environment]::GetFolderPath('Programs')
        if ([string]::IsNullOrWhiteSpace($programs)) {
            $programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
        }
        $targets = @(
            (Join-Path $desktop 'Club Autopilot.lnk'),
            (Join-Path $programs 'Club Autopilot.lnk')
        )
        try {
            $shell = New-Object -ComObject WScript.Shell
            foreach ($lnkPath in $targets) {
                $folder = Split-Path -Parent $lnkPath
                if (-not (Test-Path -LiteralPath $folder)) {
                    New-Item -ItemType Directory -Path $folder | Out-Null
                }
                $lnk = $shell.CreateShortcut($lnkPath)
                $lnk.TargetPath = $psExe
                $lnk.Arguments = '-ExecutionPolicy Bypass -File "' + $start + '"'
                $lnk.WorkingDirectory = $Repo
                $lnk.WindowStyle = 1
                $lnk.Description = 'Club Autopilot'
                if (Test-Path -LiteralPath $icon) {
                    $lnk.IconLocation = $icon + ',0'
                }
                $lnk.Save()
            }
            Say "已建立桌面與開始功能表的「Club Autopilot」。下次雙擊桌面圖示即可。 / Created Club Autopilot on the Desktop and in the Start Menu. Double-click the desktop icon next time."
        } catch {
            Say "捷徑沒有建立。 / The shortcut was not created: $($_.Exception.Message)"
        }
    }

    Say "Club Autopilot 安裝程式 / Club Autopilot setup (launchpad)"
    $alias = Get-Command python -ErrorAction SilentlyContinue
    if ($alias -and (Test-StoreAlias $alias.Source)) {
        Say "偵測到 Microsoft Store 的 python 假指令，會略過它。 / The Microsoft Store python alias was found and will be skipped."
    }
    if (Test-UnsafeDir (Get-Location).Path) {
        Set-Location -LiteralPath $HOME
        Say "這個視窗開在 Windows 系統資料夾，已改到使用者家目錄，避免把專案載到 System32。 / This window was opened in a Windows system folder. Moved to your home directory so the project is not cloned into System32."
    }

    $privateDir = Get-PrivateDir
    Say "本機私密設定會放在 $privateDir 。新電腦用 ClubAutopilot-private；如果已經有「創創社-私密」資料夾就繼續用它。環境變數 AUTOPILOT_PRIVATE_DIR 優先。 / Private settings go in $privateDir . A new computer uses ClubAutopilot-private. An existing legacy folder is kept. AUTOPILOT_PRIVATE_DIR wins."

    Update-SessionPath
    $existing = Find-ExistingRepo
    if (-not $existing) {
        $homeDir = $HOME
        if (-not $homeDir) { $homeDir = $env:USERPROFILE }
        $dest = Join-Path $homeDir 'club-autopilot'
        if (Test-RepoDir $dest) { $existing = [System.IO.Path]::GetFullPath($dest) }
    }
    if ($existing) {
        $gitEarly = Find-RealGit
        if ($gitEarly) { Update-Repo $existing $gitEarly }
    }

    if (Test-OnboardingUp) {
        if ($existing) { Install-LaunchShortcuts $existing }
        Say "網站已經在跑，正在打開初次設定。 / The site is already running. Opening the onboarding page."
        Open-Onboarding
        Say "重新貼上這一行會把已有的專案更新到最新的 launchpad。若伺服器還開著，請先關掉那個視窗再貼一次，網站才會用新程式。 / Pasting this line again updates an existing clone to the latest launchpad. If the server window is still open, close it and paste the line again so the site restarts."
        return
    }

    Update-SessionPath
    $git = Find-RealGit
    if (-not $git) {
        $winget = Find-Winget
        if ($winget) {
            Say "正在更新 winget 來源… / Updating winget sources…"
            & $winget source update
            Update-SessionPath
        }
        Install-WithWinget 'Git.Git' | Out-Null
        $git = Find-RealGit
        if (-not $git) {
            Fail "安裝後仍找不到 Git。請關掉這個視窗，開一個新的 PowerShell，再貼一次同一行。 / Git is still missing after install. Close this window, open a new PowerShell, and paste the same line again."
        }
    }
    Say "使用 Git：$git / Using Git: $git"

    $homeDir = $HOME
    if (-not $homeDir) { $homeDir = $env:USERPROFILE }
    $repo = Find-ExistingRepo
    if (-not $repo) {
        $dest = Join-Path $homeDir 'club-autopilot'
        if (Test-Path -LiteralPath $dest) {
            if (-not (Test-RepoDir $dest)) {
                Fail "資料夾已存在，但不是 Club Autopilot 的 git 專案：$dest 。請改名後再貼一次。 / That folder already exists and is not this git repo. Rename it and paste the line again."
            }
            $repo = [System.IO.Path]::GetFullPath($dest)
            if (-not $script:DidUpdateRepo) { Update-Repo $repo $git }
        } else {
            Say "正在把專案下載到 $dest … / Cloning the project into $dest …"
            & $git clone --branch $Branch --single-branch $RepoUrl $dest
            if ($LASTEXITCODE -ne 0 -or -not (Test-RepoDir $dest)) {
                Fail "git clone 失敗。請檢查網路後再貼一次。 / git clone failed. Check the network and paste the line again."
            }
            $repo = [System.IO.Path]::GetFullPath($dest)
        }
    } else {
        if (-not $script:DidUpdateRepo) { Update-Repo $repo $git }
    }
    Set-Location -LiteralPath $repo
    Say "專案位置：$repo / Project folder: $repo"
    Install-LaunchShortcuts $repo

    $python = Find-RealPython
    if (-not $python) {
        $winget = Find-Winget
        if ($winget) {
            Say "正在更新 winget 來源… / Updating winget sources…"
            & $winget source update
            Update-SessionPath
        }
        Install-WithWinget 'Python.Python.3.12' | Out-Null
        Update-SessionPath
        $python = Find-RealPython
        if (-not $python) {
            Fail "安裝後仍找不到 Python 3.12（Store 的 python 假指令不算）。請關掉這個視窗，開新的 PowerShell，再貼一次。 / Python 3.12 is still missing (the Store alias does not count). Close this window, open a new PowerShell, and paste the line again."
        }
    }
    Say "使用 Python：$python / Using Python: $python"

    $venvDir = Join-Path $repo '.venv'
    $venvPy = Join-Path $venvDir 'Scripts\python.exe'
    $venvOk = $false
    if (Test-Path -LiteralPath $venvPy) {
        & $venvPy -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $venvOk = $true }
    }
    if (-not $venvOk) {
        if (Test-Path -LiteralPath $venvDir) {
            Say "舊的虛擬環境不能用，正在重建… / The old virtual environment is unusable. Recreating it…"
            Remove-Item -LiteralPath $venvDir -Recurse -Force
        }
        Say "正在建立虛擬環境 .venv … / Creating the .venv virtual environment…"
        & $python -m venv $venvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPy)) {
            Fail "python -m venv 失敗。 / python -m venv failed."
        }
    }

    Say "正在安裝套件（使用 python -m pip，不使用 pip 指令）… / Installing packages with python -m pip (not the pip command)…"
    & $venvPy -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fail "python -m pip 升級失敗。 / python -m pip could not upgrade pip." }
    & $venvPy -m pip install -r (Join-Path $repo 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail "無法安裝 requirements.txt。請檢查網路後再貼一次。 / Could not install requirements.txt. Check the network and paste the line again." }

    $adb = $null
    $adbCmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($adbCmd -and $adbCmd.Source -and -not (Test-StoreAlias $adbCmd.Source)) { $adb = $adbCmd.Source }
    $adbHome = Join-Path $env:LOCALAPPDATA 'club-autopilot-tools\platform-tools\adb.exe'
    if (-not $adb -and (Test-Path -LiteralPath $adbHome)) { $adb = $adbHome }
    if (-not $adb) {
        Say "正在下載 Android platform-tools（adb），給 Instagram 的 Android 手機用… / Downloading Android platform-tools (adb) for the Instagram phone step…"
        $tools = Join-Path $env:LOCALAPPDATA 'club-autopilot-tools'
        $zip = Join-Path $env:TEMP 'platform-tools-latest-windows.zip'
        try {
            Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile $zip -UseBasicParsing
            if (-not (Test-Path -LiteralPath $tools)) { New-Item -ItemType Directory -Path $tools | Out-Null }
            Expand-Archive -LiteralPath $zip -DestinationPath $tools -Force
            if (Test-Path -LiteralPath $adbHome) {
                $env:Path = "$(Split-Path -Parent $adbHome);$env:Path"
                Say "adb 已放在 $adbHome / adb is at $adbHome"
            } else {
                Say "adb 沒有下載完成。Instagram 步驟可以先略過。 / adb did not finish downloading. You can skip the Instagram step."
            }
        } catch {
            Say "adb 沒有下載完成。Instagram 步驟可以先略過。 / adb did not finish downloading. You can skip the Instagram step."
        }
    }

    Say "正在安裝 Playwright 的 Chromium… / Installing Playwright Chromium…"
    & $venvPy -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Fail "Playwright Chromium 安裝失敗。請檢查網路後再貼一次。 / Playwright Chromium install failed. Check the network and paste the line again." }

    Say "正在啟動網站，埠號 5050 … / Starting the site on port 5050…"
    $repoLit = $repo.Replace("'", "''")
    $pyLit = $venvPy.Replace("'", "''")
    $inner = "Set-Location -LiteralPath '$repoLit'; & '$pyLit' -m autopilot web --port 5050"
    # One argument string: Windows PowerShell 5.1 splits an argument array on spaces.
    $arg = '-NoExit -ExecutionPolicy Bypass -Command "' + $inner + '"'
    $psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Start-Process -FilePath $psExe -ArgumentList $arg | Out-Null

    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if (Test-OnboardingUp) {
            Open-Onboarding
            Say "已打開初次設定 / Opened $OnboardingUrl"
            Say "請讓伺服器視窗保持開啟。關掉它，網站就停了。 / Leave the server window open. Closing it stops the site."
            Say "下次雙擊桌面上的 Club Autopilot 圖示。 / Next time, double-click the Club Autopilot icon on your desktop."
            return
        }
        Start-Sleep -Seconds 1
    }
    Fail "90 秒內連不到 $OnboardingUrl 。請看彈出的 PowerShell 視窗。埠號 5050 可能被其他程式占用。 / Nothing answered $OnboardingUrl within 90 seconds. Read the PowerShell window that opened. Port 5050 may already be in use."
}
