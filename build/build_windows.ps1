Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

function Remove-ProjectPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $projectFullPath = [System.IO.Path]::GetFullPath($projectRoot)
    if (-not $fullPath.StartsWith($projectFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside project root: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

if (-not (Test-Path $python)) {
    throw "Python venv not found: $python"
}

Push-Location $projectRoot
try {
    Invoke-Checked -FilePath $python -ArgumentList @("-m", "pip", "install", "-e", ".[build]")

    Remove-ProjectPath -Path (Join-Path $projectRoot "dist\JobScraper")
    Remove-ProjectPath -Path (Join-Path $projectRoot "dist\installer")

    Invoke-Checked -FilePath $python -ArgumentList @("-m", "PyInstaller", "$PSScriptRoot\JobScraper.spec", "--noconfirm", "--clean")

    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if ($iscc) {
        $distInstaller = Join-Path $projectRoot "dist\installer"
        New-Item -ItemType Directory -Force -Path $distInstaller | Out-Null
        Invoke-Checked -FilePath $iscc.Source -ArgumentList @("$projectRoot\installer\JobScraper.iss")
    }
    else {
        Write-Host "Inno Setup not found; skipping installer build."
    }
}
finally {
    Pop-Location
}
