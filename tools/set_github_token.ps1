# Set (or clear) the optional GitHub token used by the update checker.
# Run from a NORMAL console (no admin needed - config.json is per-user):
#   powershell -ExecutionPolicy Bypass -File tools\set_github_token.ps1
# The prompt is masked; the token never appears on screen or in logs.
# Create a read-only token at:
#   GitHub -> Settings -> Developer settings -> Personal access tokens ->
#   Fine-grained tokens -> generate, select this repo only,
#   "Public repositories" (read-only) is enough for update checks.
# To REMOVE the token later: run this script and enter "clear".

$ErrorActionPreference = 'Stop'
$data = Join-Path $env:APPDATA 'SSDTempMonitor'
$cfg  = Join-Path $data 'config.json'

New-Item -ItemType Directory -Force -Path $data | Out-Null
if (Test-Path $cfg) {
  $json = Get-Content $cfg -Raw | ConvertFrom-Json
} else {
  $json = [pscustomobject]@{}
}

Write-Output 'GitHub token for update checks (optional, read-only PAT).'
Write-Output 'Type "clear" to remove the stored token.'
$token = Read-Host 'Token (input is hidden)' -MaskInput

if ([string]::IsNullOrWhiteSpace($token)) {
  Write-Output 'Nothing entered - config unchanged.'
  exit 0
}

if ($token -eq 'clear') {
  $json | Add-Member -NotePropertyName github_token -NotePropertyValue '' -Force
  $json | ConvertTo-Json -Depth 10 | Set-Content -Path $cfg -Encoding utf8
  Write-Output 'Token cleared.'
  exit 0
}

if ($token.Length -lt 20) {
  Write-Output 'That does not look like a GitHub PAT (too short) - aborted, nothing saved.'
  exit 1
}

$json | Add-Member -NotePropertyName github_token -NotePropertyValue $token -Force
$json | ConvertTo-Json -Depth 10 | Set-Content -Path $cfg -Encoding utf8
Write-Output 'Token saved to config.json (never logged by the app).'
Write-Output 'Restart the app (tray -> Exit, then reopen) to use it.'
