# Équivalent PowerShell de tools/pwsync : installation en mode éditable puis tests unitaires.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python -m pip install --quiet -e ".[dev]"
python -m pytest -q @args
