# admin-install.ps1
# Déploie PrintWatch sur un serveur Windows + IIS, accessible en https://<HostName> :
#   1) net-snmp (snmpwalk / snmpget) dans C:\usr, sans agent SNMP
#   2) service Windows « PrintWatch » (NSSM) : collecte + dashboard sur 127.0.0.1:<Port>
#   3) site IIS : HTTPS (certificat dont le sujet correspond à <CertSubject>, SNI) + proxy ARR vers 127.0.0.1:<Port>
#
# REQUIERT ADMIN (s'auto-élève). Rejouable : chaque étape remplace ce qui existe déjà.
# Préalables, faits sans droits admin (voir README §15.1) : .venv, config.toml, printers.csv, .env.
#
# LES VALEURS PROPRES À VOTRE SERVEUR NE SONT PAS DANS LE DÉPÔT. Les passer en paramètres, ou les poser une fois
# dans deploy\windows\site.local.psd1 (non versionné ; modèle : site.example.psd1) :
#   .\deploy\windows\admin-install.ps1 -HostName printwatch.exemple.sn -CertSubject "CN=*.exemple.sn*"
#   .\deploy\windows\admin-install.ps1 -Uninstall      (retire service et site ; garde code, base et net-snmp)

param(
    [switch]$Uninstall,
    [string]$HostName,                    # nom public servi par IIS (doit exister dans le DNS interne)
    [string]$CertSubject,                 # filtre -like sur le sujet du certificat, ex. "CN=*.exemple.sn*"
    [string]$SiteName,                    # nom du site IIS (défaut : PrintWatch)
    [int]$Port = 0,                       # port local de PrintWatch (défaut : 8080)
    [string]$DeployDir                    # dossier des outils (nssm, installateurs, journaux) ; défaut : C:\deploy
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Host "Elevation requise..." -ForegroundColor Yellow
    $argList = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Uninstall) { $argList += "-Uninstall" }
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        if ($kv.Key -ne "Uninstall") { $argList += @("-$($kv.Key)", "`"$($kv.Value)`"") }
    }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

Import-Module WebAdministration

# Valeurs locales (non versionnées) : complètent les paramètres absents de la ligne de commande.
$local = "$PSScriptRoot\site.local.psd1"
$conf  = if (Test-Path $local) { Import-PowerShellDataFile $local } else { @{} }
if (-not $HostName)    { $HostName    = $conf.HostName }
if (-not $CertSubject) { $CertSubject = $conf.CertSubject }
if (-not $SiteName)    { $SiteName    = if ($conf.SiteName) { $conf.SiteName } else { "PrintWatch" } }
if (-not $Port)        { $Port        = if ($conf.Port) { [int]$conf.Port } else { 8080 } }
if (-not $DeployDir)   { $DeployDir   = if ($conf.DeployDir) { $conf.DeployDir } else { "C:\deploy" } }

$root      = (Resolve-Path "$PSScriptRoot\..\..").Path          # racine du dépôt
$python    = "$root\.venv\Scripts\python.exe"
$iisDir    = "$PSScriptRoot\iis"
$nssm      = "$DeployDir\nssm\nssm.exe"
$logsDir   = "$DeployDir\logs"
$snmpSetup = "$DeployDir\installers\net-snmp-5.5.0-2.x64.exe"
$snmpwalk  = "C:\usr\bin\snmpwalk.exe"
$svc       = "PrintWatch"
$siteName  = $SiteName
$appPool   = "$SiteName-Pool"
$hostname  = $HostName
$port      = $Port

function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Skip($m) { Write-Host "  [--] $m" -ForegroundColor Gray }
function Fail($m) { Write-Host "[ERR] $m" -ForegroundColor Red; Pause; exit 1 }

if ($Uninstall) {
    Write-Host "=== Désinstallation PrintWatch ===" -ForegroundColor Cyan
    if (Get-Website -Name $siteName -ErrorAction SilentlyContinue) { Remove-Website -Name $siteName; Ok "site $siteName supprimé" }
    if (Test-Path "IIS:\AppPools\$appPool") { Remove-WebAppPool -Name $appPool; Ok "AppPool $appPool supprimé" }
    if (Get-Service $svc -ErrorAction SilentlyContinue) { & $nssm stop $svc | Out-Null; & $nssm remove $svc confirm | Out-Null; Ok "service $svc supprimé" }
    Write-Host "`nConservés : code, .venv, config.toml, .env, printers.csv, data\, net-snmp (C:\usr)." -ForegroundColor Yellow
    exit
}

if (-not $hostname -or -not $CertSubject) {
    Fail "nom public ou certificat non renseignés : -HostName et -CertSubject, ou deploy\windows\site.local.psd1 (voir site.example.psd1)"
}
Write-Host "=== Déploiement PrintWatch -> https://$hostname ===" -ForegroundColor Cyan

# ────────────────────────────────────────────────────────────────────────────
# 0) Préalables
# ────────────────────────────────────────────────────────────────────────────
Write-Host "`n[0] Préalables..." -ForegroundColor Yellow
foreach ($f in $python, "$root\config.toml", "$root\printers.csv", "$root\.env", $nssm, "$iisDir\web.config.template") {
    if (-not (Test-Path $f)) { Fail "absent : $f" }
}
$modules = (& "$env:windir\System32\inetsrv\appcmd.exe" list module | Out-String)
if ($modules -notmatch "RewriteModule" -or $modules -notmatch "ApplicationRequestRouting") { Fail "URL Rewrite / ARR non chargés dans IIS" }
$proxy = Get-WebConfiguration -PSPath "MACHINE/WEBROOT/APPHOST" -Filter "system.webServer/proxy"
if (-not $proxy.enabled -or -not $proxy.preserveHostHeader) { Fail "proxy ARR désactivé ou preserveHostHeader=False (activer le proxy ARR et la conservation de l'en-tête Host)" }
$cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -like $CertSubject -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } |
        Sort-Object NotAfter -Descending | Select-Object -First 1
if (-not $cert) { Fail "aucun certificat valide avec clé privée ne correspond à « $CertSubject » dans LocalMachine\My (importer le .pfx d'abord)" }
Ok "certificat $($cert.Subject) $($cert.Thumbprint), expire le $($cert.NotAfter.ToString('dd/MM/yyyy'))"
if (($cert.NotAfter - (Get-Date)).TotalDays -lt 60) { Write-Host "  [!!] le certificat expire dans moins de 60 jours" -ForegroundColor Yellow }

# ────────────────────────────────────────────────────────────────────────────
# 1) net-snmp (client seulement)
# ────────────────────────────────────────────────────────────────────────────
Write-Host "`n[1] net-snmp..." -ForegroundColor Yellow
if (Test-Path $snmpwalk) {
    Skip "déjà installé ($snmpwalk)"
} else {
    if (-not (Test-Path $snmpSetup)) { Fail "installateur absent : $snmpSetup" }
    Start-Process -FilePath $snmpSetup -ArgumentList "/S" -Wait
    if (-not (Test-Path $snmpwalk)) { Fail "net-snmp ne s'est pas installé dans C:\usr" }
    Ok "net-snmp installé dans C:\usr"
}
# PrintWatch n'utilise que les clients. Aucun agent ni récepteur de traps ne doit tourner sur ce serveur.
Get-CimInstance Win32_Service | Where-Object { $_.PathName -match '\\usr\\bin\\snmp(trap)?d\.exe' } | ForEach-Object {
    Stop-Service -Name $_.Name -Force -ErrorAction SilentlyContinue
    & sc.exe delete $_.Name | Out-Null
    Ok "service net-snmp « $($_.Name) » retiré"
}

# ────────────────────────────────────────────────────────────────────────────
# 2) Service Windows PrintWatch (NSSM)
# ────────────────────────────────────────────────────────────────────────────
Write-Host "`n[2] Service $svc..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $logsDir, "$root\data" | Out-Null
# data\ contient la base et users.json (empreintes ET clé de signature des sessions) : SYSTEM et administrateurs seulement.
& icacls "$root\data" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /Q | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "icacls sur $root\data" }
Ok "droits de data\ restreints à SYSTEM et Administrateurs"
if (Test-Path "$root\.secrets.enc") {
    & icacls "$root\.secrets.enc" /inheritance:r /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F" /Q | Out-Null
    Ok "droits du coffre .secrets.enc restreints"
}

# Variables d'environnement : celles de .env (secrets) + encodage. Stockées par NSSM dans le registre (lisible par les admins seuls).
$envVars = @("PYTHONIOENCODING=utf-8", "PYTHONUNBUFFERED=1")
foreach ($line in Get-Content "$root\.env" -Encoding UTF8) {
    $line = $line.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) { $envVars += $line }
}

if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
    & $nssm stop $svc 2>&1 | Out-Null
    & $nssm remove $svc confirm 2>&1 | Out-Null
    Skip "ancien service retiré"
}
& $nssm install $svc $python
if ($LASTEXITCODE -ne 0) { Fail "nssm install $svc" }
& $nssm set $svc AppParameters "-m printwatch -c config.toml serve --collect -p printers.csv --host 127.0.0.1 --port $port" | Out-Null
& $nssm set $svc AppDirectory $root | Out-Null
& $nssm set $svc AppEnvironmentExtra $envVars | Out-Null
& $nssm set $svc AppStdout "$logsDir\printwatch-stdout.log" | Out-Null
& $nssm set $svc AppStderr "$logsDir\printwatch-stderr.log" | Out-Null
& $nssm set $svc AppRotateFiles 1 | Out-Null
& $nssm set $svc AppRotateOnline 1 | Out-Null
& $nssm set $svc AppRotateBytes 10485760 | Out-Null
& $nssm set $svc Start SERVICE_AUTO_START | Out-Null
& $nssm set $svc DisplayName "PrintWatch (supervision imprimantes)" | Out-Null
& $nssm set $svc Description "Collecte SNMP des consommables + dashboard sur 127.0.0.1:$port, servi par IIS en https://$hostname" | Out-Null
& $nssm set $svc AppExit Default Restart | Out-Null
& $nssm set $svc AppRestartDelay 5000 | Out-Null
& $nssm set $svc AppThrottle 10000 | Out-Null
& $nssm start $svc | Out-Null

$up = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest "http://127.0.0.1:$port/api/health" -UseBasicParsing -TimeoutSec 3 | Out-Null; $up = $true; break } catch {}
}
if (-not $up) { Fail "PrintWatch ne répond pas sur 127.0.0.1:$port (voir $logsDir\printwatch-stderr.log)" }
Ok "service $svc démarré, répond sur 127.0.0.1:$port"

# ────────────────────────────────────────────────────────────────────────────
# 3) Site IIS (HTTP 80 -> HTTPS 443, SNI)
# ────────────────────────────────────────────────────────────────────────────
Write-Host "`n[3] Site IIS $siteName..." -ForegroundColor Yellow
if (-not (Test-Path "IIS:\AppPools\$appPool")) {
    New-WebAppPool -Name $appPool | Out-Null
    Set-ItemProperty "IIS:\AppPools\$appPool" -Name "managedRuntimeVersion" -Value ""
    Ok "AppPool $appPool créé"
} else { Skip "AppPool $appPool existe" }

# web.config tiré du modèle versionné : nom public et port propres à ce serveur.
$hostRe = [regex]::Escape($hostname)
(Get-Content "$iisDir\web.config.template" -Raw -Encoding UTF8).Replace("__HOSTNAME_RE__", $hostRe).Replace("__HOSTNAME__", $hostname).Replace("__PORT__", "$port") |
    Set-Content "$iisDir\web.config" -Encoding UTF8
Ok "web.config généré pour $hostname"
& icacls $iisDir /grant "IIS_IUSRS:(OI)(CI)RX" "IIS AppPool\$($appPool):(OI)(CI)RX" /T /Q | Out-Null

if (-not (Get-Website -Name $siteName -ErrorAction SilentlyContinue)) {
    New-Website -Name $siteName -PhysicalPath $iisDir -ApplicationPool $appPool -Port 80 -HostHeader $hostname | Out-Null
    Ok "site $siteName créé (http:80:$hostname)"
} else {
    Set-ItemProperty "IIS:\Sites\$siteName" -Name "physicalPath" -Value $iisDir
    Set-ItemProperty "IIS:\Sites\$siteName" -Name "applicationPool" -Value $appPool
    Skip "site $siteName existe (mis à jour)"
}

if (-not (Get-WebBinding -Name $siteName -Protocol https -Port 443 -HostHeader $hostname)) {
    New-WebBinding -Name $siteName -Protocol https -Port 443 -HostHeader $hostname -SslFlags 1
}
(Get-WebBinding -Name $siteName -Protocol https -Port 443 -HostHeader $hostname).AddSslCertificate($cert.Thumbprint, "My")
Ok "https:443:$hostname lié au certificat (SNI)"

Start-Website -Name $siteName -ErrorAction SilentlyContinue
Ok "site démarré"

# ────────────────────────────────────────────────────────────────────────────
# 4) Vérification (sans dépendre du DNS : on force la résolution vers ce serveur)
# ────────────────────────────────────────────────────────────────────────────
Write-Host "`n[4] Vérification..." -ForegroundColor Yellow
$code = & curl.exe -s -o NUL -w "%{http_code}" --resolve "${hostname}:443:127.0.0.1" "https://$hostname/api/health"
if ($code -eq "200") { Ok "https://$hostname/api/health -> 200" } else { Write-Host "  [!!] https://$hostname/api/health -> $code" -ForegroundColor Yellow }
# Connexion obligatoire : sans session, les données doivent être refusées.
$code = & curl.exe -s -o NUL -w "%{http_code}" --resolve "${hostname}:443:127.0.0.1" "https://$hostname/api/status"
if ($code -eq "401") { Ok "https://$hostname/api/status -> 401 sans connexion" } else { Write-Host "  [!!] https://$hostname/api/status -> $code (401 attendu)" -ForegroundColor Yellow }
$users = & $python -m printwatch -c "$root\config.toml" users list 2>&1 | Out-String
if ($users -match "@") { Ok "comptes du tableau de bord présents" } else { Write-Host "  [!!] aucun compte : personne ne peut se connecter (printwatch users add <adresse>)" -ForegroundColor Yellow }
$code = & curl.exe -s -o NUL -w "%{http_code}" --resolve "${hostname}:80:127.0.0.1" "http://$hostname/"
if ($code -eq "301") { Ok "http://$hostname/ -> 301 vers HTTPS" } else { Write-Host "  [!!] http://$hostname/ -> $code" -ForegroundColor Yellow }

try { Resolve-DnsName $hostname -ErrorAction Stop | Out-Null; Ok "DNS : $hostname résolu" }
catch { Write-Host "  [!!] DNS : $hostname n'existe pas encore. Demander un enregistrement A (ou CNAME) vers ce serveur sur les contrôleurs de domaine." -ForegroundColor Yellow }

Write-Host "`nTerminé." -ForegroundColor Cyan
