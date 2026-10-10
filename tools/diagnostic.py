import csv, io, sqlite3, time, os
import sys
"""Diagnostic en LECTURE SEULE de l'installation : python3 tools/diagnostic.py [base.db] [printers.csv]  (à lancer depuis ~/printwatch)."""
DB = sys.argv[1] if len(sys.argv) > 1 else 'data/printwatch.db'
CSVF = sys.argv[2] if len(sys.argv) > 2 else 'printers.csv'
now = time.time()
c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True); c.row_factory = sqlite3.Row
age = lambda ts: '—' if not ts else (f'{(now-ts)/60:.0f} min' if now-ts < 7200 else f'{(now-ts)/3600:.1f} h')
print(f"BASE : {os.path.getsize(DB)/1e6:.1f} Mo | journal={c.execute('PRAGMA journal_mode').fetchone()[0]}")
cy = c.execute('SELECT ts, polled, failed, duration FROM cycles ORDER BY id DESC LIMIT 1').fetchone()
n = c.execute('SELECT COUNT(*) FROM cycles').fetchone()[0]
print(f"COLLECTE : {n} cycles enregistrés | dernier il y a {age(cy['ts']) if cy else 'jamais'}" + (f" ({cy['polled']} interrogées, {cy['failed']} en échec, {cy['duration']:.0f} s)" if cy else ''))
gaps = [r[0] for r in c.execute('SELECT ts FROM cycles ORDER BY id DESC LIMIT 40')]
big = [round((a-b)/60) for a, b in zip(gaps, gaps[1:]) if a-b > 1500]
print(f"  interruptions (>25 min entre 2 cycles, 40 derniers cycles) : {big or 'aucune'}")
print("\nIMPRIMANTES (activées)")
rows = c.execute('''SELECT p.id, p.name, p.host, p.down, p.failures, p.last_seen, p.user,
   (SELECT COUNT(*) FROM supplies s WHERE s.printer_id=p.id) n,
   (SELECT COUNT(*) FROM supplies s WHERE s.printer_id=p.id AND s.pct IS NULL) unk,
   (SELECT MIN(pct) FROM supplies s WHERE s.printer_id=p.id) low FROM printers p WHERE p.enabled=1 ORDER BY p.host''').fetchall()
for r in rows:
    st = 'INJOIGNABLE' if r['down'] else ('sans relevé' if not r['n'] else 'ok')
    print(f"  {r['id']:<14}{r['host']:<16}{st:<12} échecs={r['failures']:<2} vu={age(r['last_seen']):<9} niveaux={r['n']-r['unk']}/{r['n']} min={r['low'] if r['low'] is not None else '—':<5} user={'oui' if r['user'] else 'NON'}")
print(f"\nDÉSACTIVÉES en base : {[r[0] for r in c.execute('SELECT id FROM printers WHERE enabled=0')] or 'aucune'}")
txt = open(CSVF, 'rb').read().decode('utf-8-sig', errors='replace')
d = ';' if txt.splitlines()[0].count(';') > txt.splitlines()[0].count(',') else ','
ids = [r['id'] for r in csv.DictReader(io.StringIO(txt), delimiter=d)]
hosts = [r['host'] for r in csv.DictReader(io.StringIO(txt), delimiter=d)]
dbids = {r['id'] for r in c.execute('SELECT id FROM printers WHERE enabled=1')}
print(f"CSV : {len(ids)} imprimantes | dans la base mais pas dans le CSV : {sorted(dbids-set(ids)) or 'aucune'} | dans le CSV mais pas encore en base : {sorted(set(ids)-{r[0] for r in c.execute('SELECT id FROM printers')}) or 'aucune'}")
print(f"  IP en double dans le CSV : {sorted({h for h in hosts if hosts.count(h) > 1}) or 'aucune'}")
print(f"\nHISTORIQUE : {c.execute('SELECT COUNT(*) FROM samples').fetchone()[0]} relevés | plus ancien il y a {age(c.execute('SELECT MIN(ts) FROM samples').fetchone()[0])}")
ev = c.execute('SELECT COUNT(*) FROM events WHERE notified=0').fetchone()[0]
print(f"ÉVÉNEMENTS non notifiés : {ev} (doit rester à 0 : sans SMTP, le journal du terminal tient lieu de notification)")
