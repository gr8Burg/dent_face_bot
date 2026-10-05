<#
.SYNOPSIS
    Deploys the committed code to the Raspberry Pi: pushes to git, then pulls, syncs and restarts there.

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -PiHost 100.64.0.5 -PiUser egor
    .\deploy.ps1 -Logs          # just follow the service log on the Pi
#>
param(
    [string]$PiHost = "attendance-pi",
    [string]$PiUser = "pi",
    [string]$Dir = "~/attendance",
    [switch]$Logs
)

$ErrorActionPreference = "Stop"
$target = "$PiUser@$PiHost"

if ($Logs) {
    ssh -t $target "journalctl -u attendance -f -n 50"
    exit $LASTEXITCODE
}

$dirty = git status --porcelain --untracked-files=no
if ($dirty) {
    Write-Warning "Uncommitted changes will NOT be deployed:"
    $dirty | ForEach-Object { Write-Host "  $_" }
}

Write-Host "==> git push" -ForegroundColor Cyan
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }

Write-Host "==> Updating $target" -ForegroundColor Cyan
$remote = "set -e; cd $Dir; git pull --ff-only; ~/.local/bin/uv sync --frozen --no-dev; " +
          "sudo systemctl restart attendance; sleep 3; systemctl status attendance --no-pager -n 8"
# -t so sudo can ask for a password if the Pi requires one
ssh -t $target $remote
if ($LASTEXITCODE -ne 0) { throw "remote update failed" }

Write-Host "==> Deployed $(git rev-parse --short HEAD)" -ForegroundColor Green
