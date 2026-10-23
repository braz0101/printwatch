"""
Coffre de secrets chiffré par DPAPI, en portée machine.

POURQUOI UN COFFRE PLUTÔT QUE LE `.env`. PrintWatch y garde déjà deux secrets en clair
(`PRINTWATCH_SNMP_COMMUNITY`, `PRINTWATCH_ADMIN_TOKEN`), et c'était tenable tant qu'ils ne
valaient que pour lui : une communauté SNMP en lecture seule sur un parc d'imprimantes, un
jeton d'installation. Les identifiants GLPI ne sont pas de cette nature — ils ouvrent le
système de tickets de toute l'entreprise, et ils existent déjà ailleurs. Un mot de passe en deux
exemplaires dont l'un est en clair, c'est celui-là qu'on oublie le jour d'une rotation.

POURQUOI `ctypes` ET NON `pywin32`. PrintWatch annonce un cœur **sans dépendance**, et ce
n'est pas une coquetterie : c'est ce qui permet de l'installer sur un serveur sans rien
compiler. DPAPI s'appelle très bien depuis `ctypes`, qui est dans la bibliothèque
standard. Soixante lignes de structures valent mieux qu'une dépendance qui ferait mentir
la première phrase du README.

PORTÉE MACHINE, ET IL LE FAUT. Le service tourne en **LocalSystem** ; un coffre chiffré en
portée utilisateur par un administrateur serait illisible par le service, et le défaut ne
se verrait qu'au premier appel GLPI, en production. `CRYPTPROTECT_LOCAL_MACHINE` fait que
tout compte de cette machine peut déchiffrer — c'est le compromis assumé : le fichier
protège contre une copie emportée, pas contre quelqu'un qui est déjà sur le serveur.

CE COFFRE EST CELUI DE PRINTWATCH, distinct de `C:\\deploy\\.secrets.enc` qui est celui de
l'intranet. Les deux applications restent indépendantes : aucune
ne casse parce que l'autre déménage. La contrepartie est connue — le mot de passe GLPI
existe en deux exemplaires, et une rotation doit passer par les deux.
"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

__all__ = ["VaultError", "vault_path", "get_secret", "set_secret", "delete_secret",
           "list_secret_names"]

DEFAULT_PATH = Path(__file__).resolve().parent.parent / ".secrets.enc"
ENV_PATH = "PRINTWATCH_VAULT_PATH"

_DESCRIPTION = "printwatch-secrets"
_CRYPTPROTECT_LOCAL_MACHINE = 0x4
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class VaultError(RuntimeError):
    """Le coffre n'a pas pu être lu ou écrit. Jamais levée pour un secret absent."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _read_and_free(blob: _Blob) -> bytes:
    """Recopie le tampon rendu par DPAPI, PUIS le libère.

    `LocalFree` n'est pas une politesse : sans lui, chaque lecture laisse derrière elle le
    secret en clair dans un tas que personne ne nettoie, dans un service qui tourne des
    mois d'affilée.
    """
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _protect(clear: bytes) -> bytes:
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_blob(clear)),
        _DESCRIPTION,
        None, None, None,
        _CRYPTPROTECT_LOCAL_MACHINE | _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise VaultError(f"CryptProtectData a échoué (code {ctypes.GetLastError()})")
    return _read_and_free(out)


def _unprotect(blob: bytes) -> bytes:
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_blob(blob)),
        None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise VaultError(f"CryptUnprotectData a échoué (code {ctypes.GetLastError()})")
    return _read_and_free(out)


def vault_path() -> Path:
    """Où vit le coffre. L'environnement l'emporte, pour les tests et les reprises."""
    depuis_env = os.environ.get(ENV_PATH, "")
    return Path(depuis_env) if depuis_env else DEFAULT_PATH


def _load() -> dict:
    p = vault_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(_unprotect(p.read_bytes()).decode("utf-8"))
    except VaultError:
        raise
    except Exception as exc:                                   # JSON abîmé, encodage
        raise VaultError(f"coffre illisible ({p}) : {exc}") from exc


def _save(data: dict) -> None:
    p = vault_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    chiffre = _protect(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    # ÉCRITURE EN DEUX TEMPS : un `write_bytes` interrompu laisserait un coffre tronqué,
    # donc illisible, donc tous les secrets perdus. Le remplacement est atomique.
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(chiffre)
    os.replace(tmp, p)


def get_secret(name: str, default: str | None = None) -> str | None:
    """La valeur, ou `default` si elle est ABSENTE.

    UN COFFRE ILLISIBLE LÈVE, il ne rend pas le défaut. C'est la leçon du coffre de
    l'intranet, dont le `get_secret` avalait l'erreur de déchiffrement : « absent » et
    « illisible » s'y lisaient pareil, et on a cherché un secret manquant pendant qu'en
    réalité le fichier était refusé en lecture.
    """
    return _load().get(name, default)


def set_secret(name: str, value: str) -> None:
    data = _load()
    data[name] = value
    _save(data)


def delete_secret(name: str) -> bool:
    data = _load()
    if name not in data:
        return False
    del data[name]
    _save(data)
    return True


def list_secret_names() -> list[str]:
    """Les NOMS, jamais les valeurs — de quoi répondre « le secret est-il posé ? »."""
    return sorted(_load())
