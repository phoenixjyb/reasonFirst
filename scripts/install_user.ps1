$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it using the supported method for your platform, then rerun."
}

Write-Host "[ReasonFirst] installing editable source tool from: $Root"
uv tool install --editable $Root --force
if ($LASTEXITCODE -ne 0) {
    throw "uv tool install failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Source/developer installation complete."
Write-Host "The editable tool follows this checkout; keep the checkout on a reviewed ref."
Write-Host ""
Write-Host "Verify from any directory:"
Write-Host "  reasonfirst --version"
Write-Host "  reasonfirst setup --status"
Write-Host ""
Write-Host "Start/resume the same guided setup used by packaged installs:"
Write-Host "  reasonfirst setup"
Write-Host ""
Write-Host "Compatibility/expert commands remain available:"
Write-Host "  actual-coder --help"
Write-Host "  codingagent --help"
Write-Host "  gitlab-agent --help"
