#requires -Version 5.1
<#
.SYNOPSIS
Build the lean perception environment (.venv-perception, Python 3.12) for the
Qwen reaction policy + GPA GUI proposals + RapidOCR Small stack.

.DESCRIPTION
Pinned profile. Installs only the packages in
requirements-perception-windows.txt plus the project's base/[dev] dependencies,
then checks `pip check`, compares installed versions against every pin and
verifies the cached model assets. It never downloads models, never contacts
Hugging Face or a cloud service, and never sends input. The legacy Decider
profile (requirements-decider-windows.txt, scripts/setup_native_decider.ps1)
is untouched and remains usable.

Running the GPU smoke check (scripts/check_perception.py) is a separate,
coordinator-run step.
#>
[CmdletBinding()]
param(
    [string]$VenvPath = ".venv-perception",
    [string]$BootstrapPython = ".venv-decider\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venv = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $VenvPath))
$python = Join-Path $venv "Scripts\python.exe"
$lock = Join-Path $repoRoot "requirements-perception-windows.txt"

# Frozen asset locations and hashes (see the manifest at the end of the lock file).
$qwenRevision = "2fc06364715b967f1860aea9cf38778875588b17"
$qwenSnapshot = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--Qwen--Qwen3.5-0.8B\snapshots\$qwenRevision"
$ocrModels = Join-Path $venv "Lib\site-packages\rapidocr\models"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Asset {
    param(
        [string]$Label,
        [string]$Path,
        [string]$Sha256
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "MISSING ASSET: $Label`n  expected at: $Path`n  This profile does not download weights; stage the file or point at an existing cache, then re-run."
    }
    $actual = Get-Sha256 -Path $Path
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        throw "HASH MISMATCH: $Label`n  path:     $Path`n  expected: $Sha256`n  actual:   $actual`n  Re-stage the exact pinned revision; do not substitute another build."
    }
    Write-Host "  asset verified: $Label"
}

function Normalize-PackageName([string]$Name) {
    return ($Name.ToLowerInvariant() -replace '[-_.]+', '-')
}

if (-not (Test-Path -LiteralPath $lock -PathType Leaf)) {
    throw "Missing lock file: $lock"
}
$bootstrap = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $BootstrapPython))
if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
    throw "Python 3.12 bootstrap interpreter missing: $bootstrap"
}

if ((Test-Path -LiteralPath $venv) -and -not (Test-Path -LiteralPath $python)) {
    throw "$venv already exists but is not a Windows virtual environment; choose another VenvPath."
}

if (Test-Path -LiteralPath $python) {
    $version = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($version -ne "3.12") {
        throw "$venv uses Python $version; this profile requires a fresh 3.12 environment. Choose another VenvPath."
    }
} else {
    Write-Host "Creating $venv with Python 3.12 ..."
    & $bootstrap -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed" }
}

Write-Host "Installing pinned perception stack (CUDA PyTorch index) ..."
& $python -m pip install --no-input `
    --extra-index-url https://download.pytorch.org/whl/cu130 `
    --requirement $lock
if ($LASTEXITCODE -ne 0) { throw "Pinned perception dependency install failed" }

Write-Host "Installing project editable without changing pinned dependencies ..."
& $python -m pip install --no-input --no-deps --editable "${repoRoot}[dev]"
if ($LASTEXITCODE -ne 0) { throw "Project editable install failed" }

Write-Host "Dependency consistency check (pip check) ..."
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency consistency check failed" }

Write-Host "Verifying installed versions against every pin in $([System.IO.Path]::GetFileName($lock)) ..."
$required = @{}
foreach ($line in Get-Content -LiteralPath $lock) {
    $text = $line.Trim()
    if ($text -eq "" -or $text.StartsWith("#")) { continue }
    if ($text -notmatch '^(?<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?<version>[^;\s#]+)$') {
        throw "Unsupported requirement line in $lock : $text"
    }
    $required[(Normalize-PackageName $Matches['name'])] = $Matches['version']
}

$dumpCode = "import importlib.metadata as m`nfor d in m.distributions():`n    n = d.metadata['Name']`n    if n: print(n + '==' + d.version)"
$installed = @{}
foreach ($line in (& $python -c $dumpCode)) {
    $split = $line.IndexOf("==")
    if ($split -lt 1) { continue }
    $installed[(Normalize-PackageName $line.Substring(0, $split))] = $line.Substring($split + 2)
}

$missing = @()
$mismatched = @()
foreach ($name in ($required.Keys | Sort-Object)) {
    if (-not $installed.ContainsKey($name)) {
        $missing += "$name==$($required[$name])"
    } elseif ($installed[$name] -ne $required[$name]) {
        $mismatched += "$name expected $($required[$name]) got $($installed[$name])"
    }
}
if ($missing.Count -gt 0) {
    throw "Pinned packages missing after install:`n  $($missing -join "`n  ")"
}
if ($mismatched.Count -gt 0) {
    throw "Pinned versions differ from the lock file:`n  $($mismatched -join "`n  ")"
}
Write-Host "  all $($required.Count) pins match the installed environment."

Write-Host "Verifying cached model assets (read-only, no downloads) ..."
Assert-Asset -Label "Qwen3.5-0.8B safetensors" -Path (Join-Path $qwenSnapshot "model.safetensors-00001-of-00001.safetensors") -Sha256 "04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696"
Assert-Asset -Label "Qwen3.5-0.8B config.json" -Path (Join-Path $qwenSnapshot "config.json") -Sha256 "b90b86f35c8e6925ef74ee04d0e758f0a845c83a42089ad82bbaa948de9b4204"
Assert-Asset -Label "Qwen3.5-0.8B preprocessor_config.json" -Path (Join-Path $qwenSnapshot "preprocessor_config.json") -Sha256 "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"
Assert-Asset -Label "Qwen3.5-0.8B tokenizer.json" -Path (Join-Path $qwenSnapshot "tokenizer.json") -Sha256 "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"
Assert-Asset -Label "GPA GUI detector weights" -Path (Join-Path $repoRoot "data\loop\weights\gpa-gui\model.pt") -Sha256 "dd404f25b7f329998c7ed97a67827a174dd626dc2683f6844ab33a5219c05f71"
Assert-Asset -Label "RapidOCR PP-OCRv6 det small" -Path (Join-Path $ocrModels "PP-OCRv6_det_small.onnx") -Sha256 "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"
Assert-Asset -Label "RapidOCR PP-OCRv6 rec small" -Path (Join-Path $ocrModels "PP-OCRv6_rec_small.onnx") -Sha256 "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"
Assert-Asset -Label "RapidOCR mobile cls" -Path (Join-Path $ocrModels "ch_ppocr_mobile_v2.0_cls_mobile.onnx") -Sha256 "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"

Write-Host ""
Write-Host "Lean perception environment ready: $python"
Write-Host "Cached Qwen revision: $qwenRevision"
Write-Host "RapidOCR: 3.9.2, PP-OCRv6 Small det/rec, ONNX Runtime CPU provider (use_cuda false)."
Write-Host "Next (coordinator only): run the GPU smoke check with HF_HUB_OFFLINE=1 to prove no network use:"
Write-Host "  `$env:HF_HUB_OFFLINE = '1'; & '$python' scripts\check_perception.py"
