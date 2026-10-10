# Script 1 : détection réseau et validation SNMP (sockets UDP natifs, aucune dépendance).
# Usage : powershell -ExecutionPolicy Bypass -File .\01-Test-Parc.ps1
# Liste des adresses IP du parc
$ips = @(
    "198.51.100.17", "198.51.100.19", "198.51.100.20", "198.51.100.10",
    "198.51.100.21", "198.51.100.13", "198.51.100.16", "198.51.100.15",
    "198.51.100.14", "198.51.100.18", "198.51.100.22", "198.51.100.11",
    "198.51.100.23", "198.51.100.24", "198.51.100.25", "198.51.100.26",
    "198.51.100.27", "198.51.100.28"
)

# Trame SNMP v1 GetRequest (sysDescr) avec communauté 'public'
$snmpPacket = [byte[]](
    0x30, 0x29, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70, 0x75, 0x62, 0x6c, 0x69, 0x63,
    0xa0, 0x1c, 0x02, 0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00, 0x02, 0x01,
    0x00, 0x30, 0x0e, 0x30, 0x0c, 0x06, 0x08, 0x2b, 0x06, 0x01, 0x02, 0x01, 0x01,
    0x01, 0x00, 0x05, 0x00
)

$results = foreach ($ip in $ips) {
    $ping = Test-Connection -ComputerName $ip -Count 1 -Quiet
    $snmpOK = $false

    if ($ping) {
        $client = New-Object System.Net.Sockets.UdpClient
        $client.Client.ReceiveTimeout = 1200
        try {
            $client.Connect($ip, 161)
            [void]$client.Send($snmpPacket, $snmpPacket.Length)
            $remoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
            $resp = $client.Receive([ref]$remoteEP)
            if ($resp.Length -gt 0) { $snmpOK = $true }
        } catch {
            $snmpOK = $false
        } finally {
            $client.Close()
        }
    }

    [PSCustomObject]@{
        IP_Adresse  = $ip
        PING        = if ($ping) { "OK" } else { "ÉCHEC" }
        SNMP_Status = if ($snmpOK) { "OK (Opérationnel)" } else { "Inaccessible / Éteinte" }
    }
}

$results | Format-Table -AutoSize
