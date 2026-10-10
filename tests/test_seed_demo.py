"""Le script de démonstration produit une base cohérente avec ce que l'API et le dashboard attendent."""
import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from printwatch.web import create_app

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "seed_demo.py"


def load_seed():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_demo"] = mod          # requis par @dataclass pour un module chargé dynamiquement
    spec.loader.exec_module(mod)
    return mod


def test_seed_feeds_api_with_forecast_replacement_and_down(tmp_path):
    db = str(tmp_path / "demo.db")
    load_seed().seed(db)          # heure courante : l API filtre sur les 30 derniers jours réels
    client = TestClient(create_app(db))
    data = client.get("/api/status").json()

    assert data["summary"]["printers"] == 4 and data["summary"]["down"] == 1
    assert data["summary"]["crit"] >= 1 and data["summary"]["warn"] >= 1
    assert data["printers"][0]["down"] is True                       # injoignable en tête
    assert all(s["forecast"] is None and s["stale"] for s in data["printers"][0]["supplies"])   # valeurs périmées
    assert not any(s["stale"] for p in data["printers"][1:] for s in p["supplies"])

    by_name = {p["name"]: {s["key"]: s for s in p["supplies"]} for p in data["printers"]}
    cyan = by_name["DEMO-Compta-1"]["1.2"]
    assert cyan["alert"] == "WARN" and cyan["forecast"] and 0 < cyan["forecast"]["days_left"] < 15
    assert by_name["DEMO-Direction-1"]["1.1"]["alert"] == "CRIT"

    hist = client.get("/api/history/demo-2/1.1?days=30").json()
    assert len(hist) > 100 and max(h["pct"] for h in hist) == 100.0   # remplacement visible dans la courbe
    events = client.get("/api/events?limit=50").json()
    assert [e["ts"] for e in events] == sorted((e["ts"] for e in events), reverse=True)   # journal chronologique
    kinds = {e["type"] for e in events}
    assert {"replaced", "down", "warn", "critical"} <= kinds


def test_seed_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    mod = load_seed()
    db = tmp_path / "demo.db"
    db.write_text("x")
    monkeypatch.setattr("sys.argv", ["seed_demo.py", str(db)])
    assert mod.main() == 1 and db.read_text() == "x"
