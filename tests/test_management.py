"""Gestion des imprimantes depuis le dashboard : ajout, édition, retrait, synchronisation, import/export."""
import argparse

import pytest
from fastapi.testclient import TestClient

from printwatch.cli import _printers_for_cycle, cmd_export, cmd_import, import_printers
from printwatch.config import AppConfig, ConfigError, load_printers
from printwatch.model import Printer
from printwatch.storage import Storage
from printwatch.sync import SyncController
from printwatch.web import create_app, new_printer_id

HOST = {"host": "testserver"}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "pw.db")
    st = Storage(path)
    st.upsert_printer(Printer(id="imp-100-15", name="Pool Contrôle", host="198.51.100.15", brand="EPSON", model="WF", location="BAT A", user="NOM4"))
    st.close()
    return path


def client(db, **kw):
    return TestClient(create_app(db, **kw))


# --- stockage ---------------------------------------------------------------------------------------------------
def test_storage_edit_archive_restore_and_host_change_resets_down(db):
    st = Storage(db)
    st.record_failure("imp-100-15", "timeout")
    st.conn.execute("UPDATE printers SET down=1 WHERE id='imp-100-15'"); st.conn.commit()
    assert st.update_printer("imp-100-15", location="Bâtiment Social", user="NDIAYE", evil="x") is True     # champ inconnu ignoré
    assert st.is_down("imp-100-15")                                       # même adresse : l'état injoignable est conservé
    st.update_printer("imp-100-15", host="198.51.100.29")
    assert not st.is_down("imp-100-15")                                   # nouvelle adresse : on repart de zéro
    assert st.set_enabled("imp-100-15", False) and st.list_printers()[0].enabled is False
    assert st.printer_by_host("198.51.100.29").id == "imp-100-15"
    assert st.update_printer("absente", name="x") is False


def test_ensure_printer_never_overwrites(db):
    st = Storage(db)
    st.ensure_printer(Printer(id="imp-100-15", name="ÉCRASÉ", host="1.1.1.1"))
    assert st.get_printer("imp-100-15").name == "Pool Contrôle"


# --- API : droits ---------------------------------------------------------------------------------------------------
def test_management_is_off_by_default_and_status_hides_hosts(db):
    c = client(db)
    assert c.post("/api/printers", json={"name": "X", "host": "10.0.0.1"}).status_code == 403
    st = c.get("/api/status").json()
    assert st["capabilities"] == {"sync": False, "manage": "off"} and "host" not in st["printers"][0]


def test_token_mode(db):
    c = client(db, manage="token", admin_token="s3cret")
    body = {"name": "X", "host": "10.0.0.1"}
    assert c.post("/api/printers", json=body).status_code == 401
    assert c.post("/api/printers", json=body, headers={"X-Admin-Token": "faux"}).status_code == 401
    assert c.post("/api/printers", json=body, headers={"X-Admin-Token": "s3cret"}).status_code == 201


def test_cross_origin_writes_are_refused(db):
    c = client(db, manage="open")
    r = c.post("/api/printers", json={"name": "X", "host": "10.0.0.1"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/printers", json={"name": "X", "host": "10.0.0.1"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 201


# --- API : ajout / édition / retrait ------------------------------------------------------------------------------
def test_add_edit_remove_roundtrip(db):
    c = client(db, manage="open")
    r = c.post("/api/printers", json={"name": "  Pool RH  ", "host": "198.51.100.10", "brand": "EPSON", "user": "NOM3"})
    assert r.status_code == 201 and r.json()["id"] == "imp-100-10" and r.json()["name"] == "Pool RH" and not r.json()["restored"]
    assert c.post("/api/printers", json={"name": "Doublon", "host": "198.51.100.10"}).status_code == 409
    assert c.patch("/api/printers/imp-100-10", json={"user": "Awa EXEMPLE", "location": "Bâtiment Administration"}).json()["user"] == "Awa EXEMPLE"
    assert c.patch("/api/printers/imp-100-10", json={"host": "198.51.100.15"}).status_code == 409      # adresse d'une autre imprimante
    assert c.patch("/api/printers/inconnue", json={"user": "x"}).status_code == 404
    st = c.get("/api/status").json()
    assert {p["id"] for p in st["printers"]} == {"imp-100-15", "imp-100-10"} and st["printers"][0]["host"]
    assert c.delete("/api/printers/imp-100-10").json() == {"archived": True, "id": "imp-100-10"}
    assert {p["id"] for p in c.get("/api/status").json()["printers"]} == {"imp-100-15"}
    assert c.delete("/api/printers/imp-100-10").status_code == 404
    again = c.post("/api/printers", json={"name": "Pool RH bis", "host": "198.51.100.10"}).json()      # même adresse : remise en service
    assert again["restored"] and again["id"] == "imp-100-10" and again["name"] == "Pool RH bis"


@pytest.mark.parametrize("body", [
    {"name": "", "host": "10.0.0.1"}, {"name": "X", "host": "-v3"}, {"name": "X", "host": "10.0.0.1 -c a"},
    {"name": "X\nY", "host": "10.0.0.1"}, {"name": "X", "host": "10.0.0.1", "role": "admin"}, {"name": "x" * 81, "host": "10.0.0.1"},
])
def test_invalid_input_is_rejected(db, body):
    assert client(db, manage="open").post("/api/printers", json=body).status_code == 422


def test_ids_are_unique_and_derived_from_the_address():
    assert new_printer_id("198.51.100.11", "x", set()) == "imp-100-11"
    assert new_printer_id("198.51.100.11", "x", {"imp-100-11"}) == "imp-100-11-2"
    assert new_printer_id("imprimante.example.org", "Pool Achats é", set()) == "imp-pool-achats-e"


# --- synchronisation ---------------------------------------------------------------------------------------------
def test_sync_controller_rules():
    t = [100.0]
    ctl = SyncController(min_gap_s=15, clock=lambda: t[0])
    assert ctl.request() == "started"
    with ctl.cycle():
        assert ctl.running and ctl.request() == "running"
    assert not ctl.running and ctl.request() == "too_soon"               # cycle tout juste terminé : pas de martelage
    t[0] += 16
    assert ctl.request() == "started"


def test_sync_endpoint(db):
    assert client(db).post("/api/sync").status_code == 501                # collecte non intégrée (service web seul)
    ctl = SyncController(clock=lambda: 1e6)
    c = client(db, sync=ctl)
    assert c.post("/api/sync").json() == {"state": "started"}
    assert c.get("/api/status").json()["capabilities"]["sync"] is True
    assert c.post("/api/sync", headers={"Origin": "http://evil.example"}).status_code == 403


def test_adding_a_printer_requests_an_immediate_sync(db):
    ctl = SyncController(clock=lambda: 1e6)
    client(db, manage="open", sync=ctl).post("/api/printers", json={"name": "X", "host": "10.0.0.9"})
    assert ctl._wake.is_set()


# --- import / export / amorçage ---------------------------------------------------------------------------------
def test_csv_only_seeds_an_empty_database_then_database_wins(tmp_path):
    csv = tmp_path / "p.csv"
    csv.write_text("id,name,host\na,Alpha,10.0.0.1\nb,Beta,10.0.0.2\n", encoding="utf-8")
    st = Storage(":memory:")
    assert [p.id for p in _printers_for_cycle(st, str(csv))] == ["a", "b"]           # base vide : amorçage
    st.update_printer("a", name="Alpha renommée")
    csv.write_text("id,name,host\na,Alpha CSV,10.0.0.1\nc,Gamma,10.0.0.3\n", encoding="utf-8")
    assert [p.name for p in _printers_for_cycle(st, str(csv))] == ["Alpha renommée", "Beta"]   # le CSV n'écrase plus rien
    with pytest.raises(ConfigError):
        _printers_for_cycle(Storage(":memory:"), None)


def test_import_overwrite_prune_and_export_roundtrip(tmp_path):
    cfg = AppConfig()
    cfg.collector.db_path = str(tmp_path / "x.db")
    csv = tmp_path / "p.csv"
    csv.write_text("id,name,host,user\na,Alpha,10.0.0.1,Awa\nb,Beta,10.0.0.2,\n", encoding="utf-8")
    ns = lambda **k: argparse.Namespace(printers=str(csv), overwrite=False, prune=False, **k)  # noqa: E731
    assert cmd_import(ns(), cfg) == 0
    st = Storage(cfg.collector.db_path)
    st.update_printer("a", user="Modifié"); st.close()
    csv.write_text("id,name,host,user\na,Alpha2,10.0.0.1,Awa\n", encoding="utf-8")
    cmd_import(argparse.Namespace(printers=str(csv), overwrite=False, prune=True), cfg)           # b retirée, a intacte
    st = Storage(cfg.collector.db_path)
    assert {p.id: (p.enabled, p.name, p.user) for p in st.list_printers()} == {"a": (True, "Alpha", "Modifié"), "b": (False, "Beta", "")}
    assert import_printers(st, load_printers(csv), overwrite=True) == (0, 1)                       # --overwrite : le CSV reprend la main
    st.close()
    out = tmp_path / "export.csv"
    cmd_export(argparse.Namespace(out=str(out), excel=True), cfg)
    assert out.read_bytes().startswith(b"\xef\xbb\xbfid;name;host") and len(load_printers(out)) == 2    # relu par le chargeur (Excel FR)
