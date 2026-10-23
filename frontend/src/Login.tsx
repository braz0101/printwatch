import { useState, type FormEvent } from 'react'
import { auth, type Me } from './api'
import { LogoDisc } from './Icons'

/** Page de connexion. Depuis l'écran mural (`#/mur`), « rester connecté » est coché d'office : la session s'y prolonge seule. */
export default function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const wall = window.location.hash.startsWith('#/mur')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(wall)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try { onLogin(await auth.login(email.trim(), password, remember)) } catch (x) {
      const m = (x as Error).message
      setErr(m.charAt(0).toUpperCase() + m.slice(1) + (m.endsWith('.') ? '' : '.'))
      setPassword(''); setBusy(false)
    }
  }

  return (
    <main className="login login-photo">
      <div className="login-card panel">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <LogoDisc size={30} /><span style={{ fontSize: 17, fontWeight: 600 }}>PrintWatch</span>
          <span aria-hidden="true" style={{ width: 1, height: 18, background: 'var(--line)' }} />
          <span className="muted" style={{ fontSize: 13.5 }}>Parc d'impression</span>
        </div>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: '-.01em' }}>Connexion</h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>{wall ? "Ouvrez la session de l'écran mural." : 'Accès réservé aux comptes autorisés.'}</p>
        </div>
        <form onSubmit={submit} className="grid gap-4">
          <label className="field">Adresse e-mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" autoFocus required
              spellCheck={false} autoCapitalize="none" maxLength={254} placeholder="prenom.nom@exemple.sn" />
          </label>
          <label className="field">Mot de passe
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required maxLength={1024} />
          </label>
          <label className="check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            <span>Rester connecté sur cet appareil</span>
          </label>
          {err && <p role="alert" className="note" style={{ margin: 0, color: 'var(--crit-text)', background: 'var(--crit-bg)' }}>{err}</p>}
          <button type="submit" className="btn btn-primary" disabled={busy || !email.trim() || !password} style={{ height: 40 }}>
            {busy ? 'Connexion…' : 'Se connecter'}
          </button>
        </form>
      </div>
    </main>
  )
}
