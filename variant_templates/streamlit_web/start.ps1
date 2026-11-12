param([Parameter(ValueFromRemainingArguments = $true)][string[]]$StreamlitArgs)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Сначала выполните: .\setup.ps1"
}
$address = if ($env:STREAMLIT_ADDRESS) { $env:STREAMLIT_ADDRESS } else { "127.0.0.1" }
Write-Host "Streamlit: http://${address}:8501  (остановить: Ctrl+C)"
& $python -m streamlit run streamlit_app.py --server.address=$address @StreamlitArgs
exit $LASTEXITCODE
