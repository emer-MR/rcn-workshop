"""Testy app/plugin_loader.py -- walidacja kontraktu PLUGIN + odporność na
zepsute pliki (serwer ma żyć, wtyczka ma być widoczna z błędem)."""
from __future__ import annotations

VALID_SRC = '''
PLUGIN = {"id": "ok_plugin", "name": "OK", "version": "1.0",
          "author": "test", "kind": "analysis", "description": "opis"}

def run(rows, ctx):
    return {"text": f"n={len(rows)}"}
'''


def _write(tmp_data_dir, filename, src):
    pdir = tmp_data_dir / "plugins"
    pdir.mkdir(exist_ok=True)
    (pdir / filename).write_text(src, encoding="utf-8")
    return pdir / filename


def test_valid_plugin(client, tmp_data_dir):
    _write(tmp_data_dir, "ok_plugin.py", VALID_SRC)
    from app.plugin_loader import scan_plugins
    regs = scan_plugins()
    rec = regs["ok_plugin"]
    assert rec.error is None
    assert rec.name == "OK" and rec.kind == "analysis"
    assert rec.accepts_ctx is True
    assert rec.run([{"a": 1}], None) == {"text": "n=1"}


def test_run_without_ctx_param(client, tmp_data_dir):
    _write(tmp_data_dir, "noctx.py", '''
PLUGIN = {"id": "noctx", "name": "NoCtx", "kind": "analysis"}
def run(rows):
    return {"text": "ok"}
''')
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["noctx"]
    assert rec.error is None and rec.accepts_ctx is False


def test_missing_plugin_dict(client, tmp_data_dir):
    _write(tmp_data_dir, "nometa.py", "def run(rows, ctx):\n    return {}\n")
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["nometa"]
    assert rec.error == "Brak słownika PLUGIN"


def test_bad_id(client, tmp_data_dir):
    _write(tmp_data_dir, "badid.py", '''
PLUGIN = {"id": "Zle ID!", "name": "X", "kind": "analysis"}
def run(rows, ctx):
    return {}
''')
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["badid"]
    assert "id" in (rec.error or "")


def test_bad_kind(client, tmp_data_dir):
    _write(tmp_data_dir, "badkind.py", '''
PLUGIN = {"id": "badkind", "name": "X", "kind": "magia"}
def run(rows, ctx):
    return {}
''')
    from app.plugin_loader import scan_plugins
    assert "kind" in (scan_plugins()["badkind"].error or "")


def test_run_not_callable(client, tmp_data_dir):
    _write(tmp_data_dir, "norun.py", '''
PLUGIN = {"id": "norun", "name": "X", "kind": "analysis"}
run = 42
''')
    from app.plugin_loader import scan_plugins
    assert "run" in (scan_plugins()["norun"].error or "")


def test_toplevel_raise(client, tmp_data_dir):
    _write(tmp_data_dir, "boom.py", 'raise RuntimeError("boom na starcie")\n')
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["boom"]
    assert "RuntimeError" in (rec.error or "")


def test_syntax_error(client, tmp_data_dir):
    _write(tmp_data_dir, "syntax.py", "def run(rows, ctx:\n")
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["syntax"]
    assert "SyntaxError" in (rec.error or "")


def test_params_spec_valid(client, tmp_data_dir):
    _write(tmp_data_dir, "zparam.py", '''
PLUGIN = {"id": "zparam", "name": "Z parametrami", "kind": "analysis",
          "params": [
              {"name": "alpha", "label": "Poziom α", "type": "number",
               "default": 0.1, "min": 0.01, "max": 0.5, "step": 0.01,
               "help": "opis"},
              {"name": "data_wyceny", "type": "date"},
          ]}
def run(rows, ctx):
    return {}
''')
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["zparam"]
    assert rec.error is None
    assert rec.params[0] == {"name": "alpha", "label": "Poziom α", "type": "number",
                             "default": 0.1, "min": 0.01, "max": 0.5, "step": 0.01,
                             "help": "opis"}
    # Brak label -> label = name; brak default -> None.
    assert rec.params[1] == {"name": "data_wyceny", "label": "data_wyceny",
                             "type": "date", "default": None}
    assert rec.to_api()["params"] == rec.params


def test_params_spec_select(client, tmp_data_dir):
    _write(tmp_data_dir, "zsel.py", '''
PLUGIN = {"id": "zsel", "name": "Z selectem", "kind": "analysis",
          "params": [{"name": "kategoria", "type": "select", "default": "szkola",
                      "options": ["szkola", {"value": "apteka", "label": "Apteka"}]}]}
def run(rows, ctx):
    return {}
''')
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["zsel"]
    assert rec.error is None
    assert rec.params[0]["options"] == [{"value": "szkola", "label": "szkola"},
                                        {"value": "apteka", "label": "Apteka"}]


def test_params_spec_invalid(client, tmp_data_dir):
    base = ('PLUGIN = {{"id": "bad", "name": "X", "kind": "analysis", "params": {spec}}}\n'
            'def run(rows, ctx):\n    return {{}}\n')
    from app.plugin_loader import scan_plugins
    for spec, fragment in [
        ('"nie-lista"', "listą"),
        ('[{"name": "Złe Imię!"}]', "name"),
        ('[{"name": "ok", "type": "magia"}]', "type"),
        ('[{"name": "ok", "default": [1, 2]}]', "default"),
        ('[{"name": "ok", "type": "select"}]', "options"),
        ('[{"name": "ok", "type": "select", "options": [42]}]', "opcja select"),
    ]:
        _write(tmp_data_dir, "bad.py", base.format(spec=spec))
        rec = scan_plugins()["bad"]
        assert rec.error and fragment in rec.error, (spec, rec.error)


def test_duplicate_id(client, tmp_data_dir):
    src = VALID_SRC.replace('"id": "ok_plugin"', '"id": "dup"')
    _write(tmp_data_dir, "a_first.py", src)
    _write(tmp_data_dir, "b_second.py", src)
    from app.plugin_loader import scan_plugins
    regs = scan_plugins()
    assert regs["dup"].path.name == "a_first.py" and regs["dup"].error is None
    dups = [r for r in regs.values() if r.error and "Duplikat" in r.error]
    assert len(dups) == 1 and dups[0].path.name == "b_second.py"


def test_disabled_listed_not_executed(client, tmp_data_dir):
    # `raise` w treści dowodzi, że plik wyłączony NIE jest wykonywany.
    _write(tmp_data_dir, "wylaczona.py.disabled", 'raise RuntimeError("nie wykonuj")\n')
    from app.plugin_loader import scan_plugins
    rec = scan_plugins()["wylaczona"]
    assert rec.enabled is False and rec.error is None and rec.run is None
