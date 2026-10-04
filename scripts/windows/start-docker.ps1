# Start Docker Desktop on Windows after clearing stale AF_UNIX socket files.
#
# After a crash or an unclean shutdown, Docker Desktop leaves socket files behind (Docker\run\dockerInference,
# docker-secrets-engine\engine.sock, ...). Windows can't remove them ("The file cannot be accessed by the
# system", error 1920), so the next start crashes with "An unexpected error occurred ... listening on unix://...".
# This script moves those folders aside (nothing is deleted; Docker recreates them) and then starts Docker.
#
# Run it by hand:   powershell -ExecutionPolicy Bypass -File scripts\windows\start-docker.ps1
# Or use it at sign-in instead of Docker's own entry: see scripts/DOCKER.md, "Docker Desktop won't start".
$ErrorActionPreference = "Stop"
$exe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

if (Get-Process -Name "com.docker.backend" -ErrorAction SilentlyContinue) {
    Write-Output "Docker Desktop is already running."
    exit 0
}

$stamp = Get-Date -Format "yyyyMMdd-HHmm"
foreach ($dir in "$env:LOCALAPPDATA\Docker\run", "$env:LOCALAPPDATA\docker-secrets-engine") {
    if (-not (Test-Path $dir)) { continue }
    $sockets = Get-ChildItem -Path $dir -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue
    if ($sockets) {
        Rename-Item -Path $dir -NewName ("{0}.stale-{1}" -f (Split-Path $dir -Leaf), $stamp)
        Write-Output ("Moved aside {0} ({1} stale socket(s))." -f $dir, @($sockets).Count)
    }
}

Start-Process $exe
Write-Output "Docker Desktop started."
