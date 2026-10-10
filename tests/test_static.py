"""Le bundle du dashboard est livré avec le paquet, ne référence aucune ressource externe et part avec une CSP stricte."""
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.storage import Storage  # noqa: E402
from printwatch.web import STATIC, create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    Storage(str(tmp_path / "t.db")).close()
    return TestClient(create_app(str(tmp_path / "t.db")))


def test_index_assets_exist_and_are_local():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    local = [r for r in refs if not r.startswith("data:")]
    assert local, "index.html ne référence aucun bundle : lancer `npm run build` dans frontend/"
    for ref in local:
        assert ref.startswith("/assets/"), f"ressource non locale ou hors /assets : {ref}"
        assert (STATIC / ref.lstrip("/")).is_file(), f"fichier manquant : {ref}"


def test_bundle_has_selfhosted_fonts_and_no_cdn():
    assets = Path(STATIC / "assets")
    assert list(assets.glob("*.woff2")), "polices auto-hébergées absentes du build"
    for f in list(assets.glob("*.css")) + list(assets.glob("*.js")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"https?://(?:fonts\.(?:googleapis|gstatic)|cdn\.|unpkg|cdnjs|ajax\.googleapis)", text), f.name


def test_security_headers_on_page_and_api(client):
    for path in ("/", "/api/events"):
        h = client.get(path).headers
        assert "default-src 'none'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in client.get("/").headers["content-security-policy"]
