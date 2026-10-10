# Script 3 : tâche planifiée Windows qui relance 02-Export-Toner.ps1 chaque jour à 8 h.
# Contrairement à une commande inline, le script d'export reste un fichier lisible et versionné : on le modifie sans recréer la tâche.
# Usage (PowerShell administrateur non requis pour une tâche utilisateur) :
#   powershell -ExecutionPolicy Bypass -File .\03-Register-DailyTask.ps1 [-At 08:00]
param([string]$At = '08:00')

$script = Join-Path $PSScriptRoot '02-Export-Toner.ps1'
if (-not (Test-Path $script)) { throw "Introuvable : $script" }

$action  = New-ScheduledTaskAction -Execute 'PowerShell.exe' `
           -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
Register-ScheduledTask -Action $action -Trigger $trigger -TaskName 'Rapport_Encre_Quotidien' `
    -Description "Mise à jour quotidienne automatique du rapport d'encre (PrintWatch)" -Force | Out-Null
Write-Host "Tâche « Rapport_Encre_Quotidien » enregistrée : tous les jours à $At" -ForegroundColor Green
