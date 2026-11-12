$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    # Prefer the newest Python 3 from the official launcher; fall back to PATH.
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
        $created = ($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $python)
    }
    if (-not $created) {
        $fallback = Get-Command python -ErrorAction SilentlyContinue
        if (-not $fallback) {
            throw "Python 3.11+ не найден. Установите его с https://www.python.org/downloads/ и включите 'Add python.exe to PATH'."
        }
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}
& $python -c 'import sys; sys.exit("Требуется Python 3.11+ (найден %s)." % ".".join(map(str, sys.version_info[:3]))) if sys.version_info < (3, 11) else None'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m pip install --upgrade pip setuptools
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python check_setup.py
exit $LASTEXITCODE
