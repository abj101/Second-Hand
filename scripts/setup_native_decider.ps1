param(
    [string]$VenvPath = ".venv-decider",
    [switch]$DownloadModel
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venv = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $VenvPath))
$python = Join-Path $venv "Scripts\python.exe"
$lock = Join-Path $repoRoot "requirements-decider-windows.txt"
$smoke = Join-Path $repoRoot "scripts\check_native_decider.py"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/."
}

if ((Test-Path -LiteralPath $venv) -and -not (Test-Path -LiteralPath $python)) {
    throw "$venv already exists but is not a Windows virtual environment; choose another VenvPath."
}

if (Test-Path -LiteralPath $python) {
    $version = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($version -ne "3.12") {
        throw "$venv uses Python $version; choose a new VenvPath for the Python 3.12 profile."
    }
} else {
    uv venv --python 3.12 $venv
}

uv pip install --python $python --editable "${repoRoot}[dev]"
uv pip install --python $python `
    --index https://download.pytorch.org/whl/cu130 `
    --default-index https://pypi.org/simple `
    --index-strategy unsafe-best-match `
    --requirement $lock
uv pip check --python $python

& $python $smoke
if ($DownloadModel) {
    & $python $smoke --model
}

Write-Host "Native Decider environment ready: $python"
