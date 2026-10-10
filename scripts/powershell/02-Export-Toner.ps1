# Script 2 : extraction des niveaux de toner (prtMarkerSuppliesLevel 1.3.6.1.2.1.43.11.1.1.9.1.1) et export CSV sur le Bureau.
# Valeur 254 = octet de -2 en entier signé = « inconnu » (RFC 3805) : l'imprimante ne communique pas son niveau.
# Ce n'est PAS une confirmation que la cartouche est installée et fonctionnelle (la valeur -3 signifie « il en reste »).
$ips = @(
    "198.51.100.17", "198.51.100.19", "198.51.100.20", "198.51.100.21",
    "198.51.100.13", "198.51.100.16", "198.51.100.15", "198.51.100.14",
    "198.51.100.18", "198.51.100.22", "198.51.100.11"
)

# Trame OID 1.3.6.1.2.1.43.11.1.1.9.1.1 (prTonerLevel)
$snmpLevelReq = [byte[]](
    0x30, 0x2d, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70, 0x75, 0x62, 0x6c, 0x69, 0x63,
    0xa0, 0x20, 0x02, 0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00, 0x02, 0x01,
    0x00, 0x30, 0x12, 0x30, 0x10, 0x06, 0x0c, 0x2b, 0x06, 0x01, 0x02, 0x01, 0x2b,
    0x0b, 0x01, 0x01, 0x09, 0x01, 0x01, 0x05, 0x00
)

$tonerReport = foreach ($ip in $ips) {
    $client = New-Object System.Net.Sockets.UdpClient
    $client.Client.ReceiveTimeout = 1500
    $tonerVal = "N/A"

    try {
        $client.Connect($ip, 161)
        [void]$client.Send($snmpLevelReq, $snmpLevelReq.Length)
        $remoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
        $resp = $client.Receive([ref]$remoteEP)
        if ($resp.Length -gt 5) {
            $lastByte = $resp[$resp.Length - 1]
            if ($lastByte -eq 254) {
                $tonerVal = "Niveau non remonté (254 = -2, inconnu)"
            } elseif ($lastByte -le 100 -and $lastByte -ge 0) {
                $tonerVal = "$lastByte %"
            } else {
                $tonerVal = "Code brut: $lastByte"
            }
        }
    } catch {
        $tonerVal = "Erreur lecture"
    } finally {
        $client.Close()
    }

    [PSCustomObject]@{
        IP_Adresse   = $ip
        Statut_SNMP  = "Actif"
        Niveau_Encre = $tonerVal
    }
}

$csvPath = "$env:USERPROFILE\Desktop\Rapport_Final_Encre.csv"
$tonerReport | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8 -Delimiter ";"
Write-Host "Rapport exporté avec succès sur le bureau : $csvPath" -ForegroundColor Green
