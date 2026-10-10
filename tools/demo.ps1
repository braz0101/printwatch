# Lance PrintWatch en MODE DÉMONSTRATION sous Windows : parc fictif, relevés simulés, aucune imprimante ni net-snmp requis.
#   powershell -ExecutionPolicy Bypass -File .\tools\demo.ps1 [-Port 8080]
param([int]$Port = 8080)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Find-Python {
    foreach ($c in @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('python'), @('python3'))) {
        try {
            $rest = if ($c.Length -gt 1) { $c[1..($c.Length - 1)] } else { @() }
            $ok = & $c[0] @rest -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
            if ($ok -eq 'True') { return , $c }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.11 ou plus récent est introuvable." -ForegroundColor Red
    Write-Host "Installation : winget install -e --id Python.Python.3.12   (puis fermez et rouvrez PowerShell)"
    exit 1
}
$rest = if ($py.Length -gt 1) { $py[1..($py.Length - 1)] } else { @() }

if (-not (Test-Path '.venv-demo')) {
    Write-Host "Création de l'environnement Python (une seule fois)…"
    & $py[0] @rest -m venv .venv-demo
}
$venvPy = Join-Path (Get-Location) '.venv-demo\Scripts\python.exe'
Write-Host "Installation des dépendances (fastapi, uvicorn)…"
& $venvPy -m pip install --quiet --disable-pip-version-check -e ".[web]"

Start-Job { param($u) Start-Sleep 4; Start-Process $u } -ArgumentList "http://127.0.0.1:$Port" | Out-Null
Write-Host "Démonstration sur http://127.0.0.1:$Port (Ctrl+C pour arrêter)" -ForegroundColor Green
& $venvPy -m printwatch serve --demo --port $Port
