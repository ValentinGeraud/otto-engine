# Otto Clip: runs the clipping engine on this PC for the production site (https://otto-clip.vercel.app).
#
# One-time setup: create engine/clipper/secrets/otto_prod.env (git-ignored) with the line
#   ENGINE_TOKEN=<same value as ENGINE_TOKEN in Vercel > otto-clip > Settings > Environment Variables>
#
# Usage:
#   .\run_prod.ps1             # one pass every 30 minutes, until you close the window
#   .\run_prod.ps1 -Once       # a single pass
#   .\run_prod.ps1 -DryRun     # shows the active agents and their config, creates nothing
#   .\run_prod.ps1 -Minutes 60 # another interval
#
# Free stack, all on this PC: yt-dlp, faster-whisper, Ollama (qwen3:8b), ffmpeg.
# Nothing is published unless OTTO_PUBLISH_ENABLED=true is set here AND on Vercel.
param([switch]$Once, [switch]$DryRun, [int]$Minutes = 30, [string]$Agent)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$secrets = Join-Path $PSScriptRoot 'secrets\otto_prod.env'
if (-not (Test-Path $secrets)) {
	New-Item -ItemType Directory -Force (Split-Path $secrets) | Out-Null
	Set-Content -Path $secrets -Encoding utf8 -Value 'ENGINE_TOKEN='
	Write-Host "Fichier créé : $secrets"
	Write-Host 'Collez-y la valeur de ENGINE_TOKEN (Vercel > otto-clip > Settings > Environment Variables), puis relancez.'
	exit 1
}
foreach ($line in Get-Content $secrets) {
	if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2].Trim() }
}
if (-not $env:ENGINE_TOKEN -or $env:ENGINE_TOKEN.Length -lt 32) { Write-Error "ENGINE_TOKEN manquant ou trop court dans $secrets"; exit 1 }
if (-not $env:OTTO_API_URL) { $env:OTTO_API_URL = 'https://otto-clip.vercel.app' }

# Ollama must answer before the engine asks it to pick the moments.
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5 | Out-Null }
catch { Write-Host 'Ollama ne répond pas : démarrage…'; Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden; Start-Sleep 5 }

$extra = @()
if ($DryRun) { $extra += '--dry-run' }
if ($Agent) { $extra += @('--agent', $Agent) }

while ($true) {
	Write-Host "`n===== $(Get-Date -Format 'yyyy-MM-dd HH:mm') · moteur Otto Clip → $env:OTTO_API_URL ====="
	python saas_worker.py @extra
	if ($Once -or $DryRun) { break }
	Write-Host "Prochain passage dans $Minutes min (Ctrl+C pour arrêter)."
	Start-Sleep -Seconds ($Minutes * 60)
}
