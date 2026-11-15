# Relaunch Club Autopilot after setup.ps1. Windows PowerShell 5.1. UTF-8 BOM.
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\club-autopilot\start.ps1"
# $PSScriptRoot is empty when this file is piped into iex. The home checkout is the fallback.
$ClubStartScriptRoot = ''
if ($PSScriptRoot) {
    $ClubStartScriptRoot = $PSScriptRoot
} elseif ($MyInvocation.MyCommand.Path) {
    $ClubStartScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $OnboardingUrl = 'http://127.0.0.1:5050/onboarding'

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

    function Test-RepoDir([string]$Dir) {
        if ([string]::IsNullOrWhiteSpace($Dir)) { return $false }
        if (-not (Test-Path -LiteralPath $Dir)) { return $false }
        if (Test-UnsafeDir $Dir) { return $false }
        $req = Join-Path $Dir 'requirements.txt'
        $pkg = Join-Path $Dir 'autopilot\__main__.py'
        return (Test-Path -LiteralPath $req) -and (Test-Path -LiteralPath $pkg)
    }

    $Branch = 'launchpad'

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
        if ($cmd -and $cmd.Source -and $cmd.Source -notlike '*\WindowsApps\*') { return $cmd.Source }
        return $null
    }

    function Update-Launchpad([string]$Repo) {
        $gitDir = Join-Path $Repo '.git'
        if (-not (Test-Path -LiteralPath $gitDir)) { return }
        $git = Find-RealGit
        if (-not $git) {
            Say "這台電腦還沒有 Git，先用現有的程式。 / Git is not installed, so the existing copy will be used."
            return
        }
        Say "正在更新 launchpad … / Updating launchpad…"
        Push-Location -LiteralPath $Repo
        try {
            & $git fetch origin $Branch
            if ($LASTEXITCODE -ne 0) {
                Say "連不到 GitHub，沿用這台電腦上的 launchpad。 / Could not reach GitHub. Using the launchpad checkout already on this computer."
                return
            }
            & $git checkout $Branch
            if ($LASTEXITCODE -ne 0) {
                Say "無法切換到 launchpad，沿用目前的版本。 / Could not check out launchpad. Using the current version."
                return
            }
            & $git pull --ff-only origin $Branch
            if ($LASTEXITCODE -ne 0) {
                Say "無法快轉更新 launchpad，沿用目前的版本。 / Could not fast-forward launchpad. Using the current version."
            }
        } finally {
            Pop-Location
        }
    }

    function Find-Repo {
        if (Test-RepoDir $ClubStartScriptRoot) {
            return [System.IO.Path]::GetFullPath($ClubStartScriptRoot)
        }
        $start = (Get-Location).Path
        if (-not (Test-UnsafeDir $start)) {
            $dir = $start
            $i = 0
            while ($i -lt 6 -and $dir) {
                if (Test-RepoDir $dir) { return [System.IO.Path]::GetFullPath($dir) }
                $parent = Split-Path -Parent $dir
                if (-not $parent -or $parent -eq $dir) { break }
                $dir = $parent
                $i = $i + 1
            }
        }
        $homeDir = $HOME
        if (-not $homeDir) { $homeDir = $env:USERPROFILE }
        $dest = Join-Path $homeDir 'club-autopilot'
        if (Test-RepoDir $dest) { return [System.IO.Path]::GetFullPath($dest) }
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

    Say "正在打開 Club Autopilot … / Opening Club Autopilot…"
    $repo = Find-Repo
    if ($repo) { Update-Launchpad $repo }
    if (Test-OnboardingUp) {
        Open-Onboarding
        Say "網站已經在跑，已打開初次設定。若剛剛更新了 launchpad，請關掉伺服器視窗後再執行一次 start.ps1。 / The site is already running. Opened onboarding. If launchpad was just updated, close the server window and run start.ps1 again."
        return
    }

    if (-not $repo) {
        Fail "找不到專案。請先貼上 setup 那一行。 / The project folder was not found. Paste the setup one-liner first."
    }
    $venvPy = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Fail "找不到 .venv。請先貼上 setup 那一行完成安裝。 / No .venv yet. Paste the setup one-liner first."
    }

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
            Say "請讓伺服器視窗保持開啟。 / Leave the server window open."
            return
        }
        Start-Sleep -Seconds 1
    }
    Fail "90 秒內連不到 $OnboardingUrl 。請看彈出的視窗。埠號 5050 可能被占用。 / Nothing answered $OnboardingUrl within 90 seconds. Read the window that opened. Port 5050 may be in use."
}
