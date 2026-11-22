# Offline one-click verification for Club Autopilot. Windows PowerShell 5.1. UTF-8 BOM.
#   powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Find-Python {
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) {
        return $venv
    }
    foreach ($name in @('py', 'python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $src = [string]$cmd.Source
        if ($src -like '*\WindowsApps\*') { continue }
        return $src
    }
    return $null
}

$Py = Find-Python
if (-not $Py) {
    Write-Host 'Python 3 was not found. Install Python 3.12, then run this script again.'
    Write-Host 'VERIFY FAIL'
    exit 1
}

& $Py (Join-Path $PSScriptRoot 'verify.py')
exit $LASTEXITCODE
