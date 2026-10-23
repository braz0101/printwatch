import { StrictMode, useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { auth, SESSION_LOST, type Me } from './api'
import { OwnPassword } from './Accounts'
import App from './App'
import { IconLogout, IconWarn, Logo } from './Icons'
import Login from './Login'

/** Mot de passe provisoire : le serveur n'ouvre rien d'autre tant qu'il n'est pas remplacé ; on n'affiche donc que ce formulaire. */
function FirstPassword({ me, onMe, onLogout }: { me: Me; onMe: (m: Me) => void; onLogout: () => void }) {
  return (
    <main className="login">
      <div style={{ width: '100%', maxWidth: 520, display: 'grid', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Logo size={26} /><span style={{ fontSize: 16, fontWeight: 600 }}>PrintWatch</span>
          <button type="button" className="btn btn-icon" style={{ marginLeft: 'auto' }} onClick={onLogout} title="Se déconnecter" aria-label="Se déconnecter"><IconLogout width={16} height={16} /></button>
        </div>
        <div role="alert" className="banner" data-k="warn"><IconWarn width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />
          <span><b>Votre mot de passe est provisoire.</b> Choisissez un mot de passe personnel pour accéder au tableau de bord.</span></div>
        <OwnPassword me={me} onMe={onMe} />
      </div>
    </main>
  )
}

/** Rien n'est chargé ni affiché avant une session valide ; une session perdue en cours de route ramène à la connexion. */
function Root() {
  const [me, setMe] = useState<Me | null | undefined>(undefined)          // undefined : vérification en cours
  const [down, setDown] = useState(false)
  useEffect(() => {
    let timer = 0
    const check = () => auth.me().then((m) => { setMe(m); setDown(false) }).catch(() => { setDown(true); timer = window.setTimeout(check, 10_000) })
    void check()
    const lost = () => setMe(null)
    window.addEventListener(SESSION_LOST, lost)
    return () => { clearTimeout(timer); window.removeEventListener(SESSION_LOST, lost) }
  }, [])
  const logout = useCallback(async () => {
    try { await auth.logout() } finally { sessionStorage.clear(); setMe(null) }
  }, [])

  if (me === undefined) return down ? <main className="login"><p role="alert" className="muted">Serveur PrintWatch injoignable. Nouvelle tentative dans quelques secondes…</p></main> : null
  if (me === null) return <Login onLogin={setMe} />
  if (me.auth && me.must_change) return <FirstPassword me={me} onMe={setMe} onLogout={logout} />
  return <App me={me} onMe={setMe} onLogout={logout} />
}

createRoot(document.getElementById('root')!).render(<StrictMode><Root /></StrictMode>)
