import { useEffect, type RefObject } from 'react'

/** Focus d'un panneau latéral modal : on y entre, Tab y boucle, Échap le ferme, puis le focus revient au déclencheur. */
export function useDrawerFocus(panel: RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    panel.current?.focus()
    document.documentElement.classList.add('no-scroll')
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return }
      if (e.key !== 'Tab' || !panel.current) return
      const f = [...panel.current.querySelectorAll<HTMLElement>('button, [href], select, input, [tabindex]:not([tabindex="-1"])')].filter((x) => !x.hasAttribute('disabled') && x.offsetParent !== null)
      if (!f.length) return
      const first = f[0], last = f[f.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); document.documentElement.classList.remove('no-scroll'); prev?.focus?.() }
  }, [onClose, panel])
}
