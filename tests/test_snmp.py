import os
import stat
import subprocess

import pytest

from printwatch.config import SnmpConfig
from printwatch.snmp import NetSnmpClient, SnmpError, parse_supplies

# Sortie type de `snmpwalk -On -Oq` sur une table Printer MIB (valeurs de l'Epson WF-C879R vue en test, simplifiées).
WALK = """\
.1.3.6.1.2.1.43.11.1.1.4.1.1 3
.1.3.6.1.2.1.43.11.1.1.4.1.2 3
.1.3.6.1.2.1.43.11.1.1.4.1.10 4
.1.3.6.1.2.1.43.11.1.1.5.1.1 5
.1.3.6.1.2.1.43.11.1.1.6.1.1 "Black Ink Cartridge"
.1.3.6.1.2.1.43.11.1.1.6.1.2 "Cyan Ink Cartridge"
.1.3.6.1.2.1.43.11.1.1.6.1.10 "Maintenance Box"
.1.3.6.1.2.1.43.11.1.1.7.1.1 19
.1.3.6.1.2.1.43.11.1.1.8.1.1 100
.1.3.6.1.2.1.43.11.1.1.8.1.2 100
.1.3.6.1.2.1.43.11.1.1.8.1.10 100
.1.3.6.1.2.1.43.11.1.1.9.1.1 80
.1.3.6.1.2.1.43.11.1.1.9.1.2 -3
.1.3.6.1.2.1.43.11.1.1.9.1.10 55
.1.3.6.1.2.1.1.1.0 "ignoré : hors table"
"""


def test_parse_groups_columns_and_sorts_naturally():
    rows = parse_supplies(WALK)
    assert [r.index for r in rows] == ["1.1", "1.2", "1.10"]
    black, cyan, box = rows
    assert (black.klass, black.description, black.max_capacity, black.level) == (3, "Black Ink Cartridge", 100, 80)
    assert cyan.level == -3
    assert box.klass == 4 and box.description == "Maintenance Box"


def test_parse_ignores_garbage():
    assert parse_supplies("Timeout: No Response\nfoo bar\n") == []


class Runner:
    def __init__(self, stdout="", returncode=0, stderr="", exc=None):
        self.calls, self.stdout, self.returncode, self.stderr, self.exc = [], stdout, returncode, stderr, exc

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


V3 = SnmpConfig(version="3", user="monitor")
SECRETS = {"auth": "authsecret-123", "priv": "privsecret-456"}


def test_secrets_never_on_command_line_but_in_private_conf():
    runner = Runner(stdout=WALK)
    client = NetSnmpClient(V3, runner=runner, secrets=SECRETS)
    try:
        assert len(client.supplies("10.0.0.5")) == 3
        cmd, kw = runner.calls[0]
        assert not any("secret" in part for part in cmd)
        assert cmd[0] == "snmpwalk" and "10.0.0.5" in cmd and cmd[-1] == "1.3.6.1.2.1.43.11.1.1"
        assert ["-v", "3"] == cmd[cmd.index("-v"):cmd.index("-v") + 2] and "monitor" in cmd
        conf_dir = kw["env"]["SNMPCONFPATH"]
        conf = os.path.join(conf_dir, "snmp.conf")
        if os.name == "posix":   # les bits de permission n'ont pas de sens sous Windows
            assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600
            assert stat.S_IMODE(os.stat(conf_dir).st_mode) == 0o700
        text = open(conf).read()
        assert 'defAuthPassphrase "authsecret-123"' in text and 'defPrivPassphrase "privsecret-456"' in text
    finally:
        client.close()
    assert not os.path.exists(conf_dir)


def test_v3_context_name_passed_only_when_set():
    for ctx, expected in [("", False), ("printer-epson", True)]:
        runner = Runner(stdout=WALK)
        client = NetSnmpClient(SnmpConfig(version="3", user="monitor", context=ctx), runner=runner, secrets=SECRETS)
        try:
            client.supplies("10.0.0.5")
            cmd, _ = runner.calls[0]
            assert ("-n" in cmd) is expected
            if expected:
                assert cmd[cmd.index("-n") + 1] == "printer-epson"
        finally:
            client.close()


def test_v2c_uses_conf_for_community():
    runner = Runner(stdout=WALK)
    client = NetSnmpClient(SnmpConfig(version="2c"), runner=runner, secrets={"community": "c0mm-secret"})
    try:
        client.supplies("10.0.0.5")
        cmd, kw = runner.calls[0]
        assert not any("c0mm-secret" in p for p in cmd)
        assert "defCommunity" in open(os.path.join(kw["env"]["SNMPCONFPATH"], "snmp.conf")).read()
    finally:
        client.close()


def test_errors_are_wrapped():
    for runner, text in [
        (Runner(returncode=1, stderr="Timeout: No Response from 10.0.0.5\n"), "Timeout"),
        (Runner(exc=FileNotFoundError()), "introuvable"),
        (Runner(exc=subprocess.TimeoutExpired("snmpwalk", 30)), "délai"),
    ]:
        client = NetSnmpClient(V3, runner=runner, secrets=SECRETS)
        try:
            with pytest.raises(SnmpError, match=text):
                client.supplies("10.0.0.5")
        finally:
            client.close()


def test_sys_descr_is_best_effort():
    client = NetSnmpClient(V3, runner=Runner(returncode=1, stderr="boom"), secrets=SECRETS)
    try:
        assert client.sys_descr("10.0.0.5") is None
    finally:
        client.close()
    client = NetSnmpClient(V3, runner=Runner(stdout='"EPSON Linux WF-C879R"\n'), secrets=SECRETS)
    try:
        assert client.sys_descr("10.0.0.5") == "EPSON Linux WF-C879R"
    finally:
        client.close()


def test_secrets_resolution_from_env(monkeypatch):
    from printwatch.config import ConfigError

    monkeypatch.delenv("PRINTWATCH_SNMP_AUTH_PASS", raising=False)
    with pytest.raises(ConfigError, match="PRINTWATCH_SNMP_AUTH_PASS"):
        V3.secrets()
    monkeypatch.setenv("PRINTWATCH_SNMP_AUTH_PASS", "a" * 12)
    monkeypatch.setenv("PRINTWATCH_SNMP_PRIV_PASS", 'bad"quote')
    with pytest.raises(ConfigError, match="interdit"):
        V3.secrets()


def test_error_message_skips_net_snmp_informational_lines():
    stderr = "Created directory: /tmp/printwatch-snmp-x/cert_indexes\nTimeout: No Response from 127.0.0.1:1161.\n"
    client = NetSnmpClient(V3, runner=Runner(returncode=1, stderr=stderr), secrets=SECRETS)
    try:
        with pytest.raises(SnmpError) as exc:
            client.supplies("127.0.0.1:1161")
    finally:
        client.close()
    assert str(exc.value) == "Timeout: No Response from 127.0.0.1:1161."


def test_parse_end_of_mib_marker_does_not_overwrite_last_value():
    # snmpwalk affiche, en fin de MIB, le dernier OID avec un message ; il ne doit pas remplacer le niveau réel.
    out = WALK + ".1.3.6.1.2.1.43.11.1.1.9.1.10 No more variables left in this MIB View (It is past the end of the MIB tree)\n"
    box = parse_supplies(out)[-1]
    assert box.level == 55


def test_parse_hex_description_multiline():
    text = "Cartouche noire \u00e9co".encode("utf-8")           # contient un octet non ASCII
    hexed = " ".join(f"{b:02X}" for b in text)
    first, rest = hexed[:47], hexed[48:]                          # 16 octets sur la première ligne
    out = f".1.3.6.1.2.1.43.11.1.1.6.1.1 {first}\n{rest}\n.1.3.6.1.2.1.43.11.1.1.9.1.1 42\n"
    row = parse_supplies(out)[0]
    assert row.description == "Cartouche noire \u00e9co"
    assert row.level == 42


def test_sys_descr_decodes_hex_string():
    raw = "EPSON Linux WF-C879R Series (simul\u00e9)".encode("utf-8")
    hexed = [" ".join(f"{b:02X}" for b in raw[i:i + 16]) for i in range(0, len(raw), 16)]
    client = NetSnmpClient(V3, runner=Runner(stdout="\n".join(hexed) + "\n"), secrets=SECRETS)
    assert client.sys_descr("10.0.0.1") == "EPSON Linux WF-C879R Series (simul\u00e9)"


def _netsnmp_hex(raw: bytes) -> list[str]:
    """Format observé avec `snmpget/snmpwalk -Oq` : 16 octets par ligne, guillemets autour de l'ensemble."""
    lines = [" ".join(f"{b:02X}" for b in raw[i:i + 16]) for i in range(0, len(raw), 16)]
    lines[0] = '"' + lines[0]
    lines[-1] = lines[-1] + '"'
    return lines


def test_sys_descr_decodes_quoted_multiline_hex():
    raw = "EPSON Linux WF-C879R Series (simul\u00e9)".encode("utf-8")
    client = NetSnmpClient(V3, runner=Runner(stdout="\n".join(_netsnmp_hex(raw)) + "\n"), secrets=SECRETS)
    assert client.sys_descr("10.0.0.1") == "EPSON Linux WF-C879R Series (simul\u00e9)"


def test_parse_quoted_multiline_hex_description():
    raw = "Cartouche noire \u00e9conomique".encode("utf-8")
    hexed = _netsnmp_hex(raw)
    out = (f".1.3.6.1.2.1.43.11.1.1.6.1.1 {hexed[0]}\n" + "\n".join(hexed[1:]) + "\n"
           ".1.3.6.1.2.1.43.11.1.1.9.1.1 42\n")
    row = parse_supplies(out)[0]
    assert row.description == "Cartouche noire \u00e9conomique"
    assert row.level == 42


def test_v1_flag_and_community_conf():
    runner = Runner(stdout=WALK)
    client = NetSnmpClient(SnmpConfig(version="1"), runner=runner, secrets={"community": "c0mm-secret"})
    try:
        client.supplies("10.0.0.5")
        cmd, kw = runner.calls[0]
        assert cmd[cmd.index("-v") + 1] == "1"
        assert not any("c0mm-secret" in p for p in cmd)
        assert "defCommunity" in open(os.path.join(kw["env"]["SNMPCONFPATH"], "snmp.conf")).read()
    finally:
        client.close()


def test_v1_config_validates_and_reads_community(monkeypatch):
    monkeypatch.setenv("PRINTWATCH_SNMP_COMMUNITY", "c0mm-secret")
    assert SnmpConfig(version="1").secrets() == {"community": "c0mm-secret"}
