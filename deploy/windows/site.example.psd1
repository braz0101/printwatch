# Copier en site.local.psd1 (NON versionné) et adapter : valeurs propres au serveur de production.
@{
    HostName    = "printwatch.exemple.sn"      # nom public (enregistrement DNS interne vers ce serveur)
    CertSubject = "CN=*.exemple.sn*"           # filtre -like sur le sujet du certificat dans LocalMachine\My
    SiteName    = "PrintWatch"                 # nom du site IIS
    Port        = 8080                         # port local de PrintWatch
    DeployDir   = "C:\deploy"                  # nssm, installateurs, journaux
}
