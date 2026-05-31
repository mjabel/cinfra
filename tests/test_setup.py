import pytest

from cinfra.crawler import setup as setup_mod
from cinfra.crawler.setup import Obscura


class FakePopen:
    last: "FakePopen | None" = None

    def __init__(self, cmd: list[str], env: dict[str, str] | None = None) -> None:
        self.cmd = cmd
        self.env = env
        FakePopen.last = self


def test_serve_reuses_running_server(monkeypatch: pytest.MonkeyPatch) -> None:
    obscura = Obscura()
    monkeypatch.setattr(obscura, "_is_running", lambda port, host="127.0.0.1": True)
    assert obscura.serve(port=9222) is None


def test_serve_command_includes_workers_and_stealth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obscura = Obscura()
    monkeypatch.setattr(obscura, "_is_running", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(obscura, "is_installed", lambda: True)
    monkeypatch.setattr(setup_mod.subprocess, "Popen", FakePopen)

    obscura.serve(port=9333, workers=6, stealth=True, rust_log="off")

    cmd = FakePopen.last.cmd
    assert cmd[cmd.index("--port") + 1] == "9333"
    assert cmd[cmd.index("--workers") + 1] == "6"
    assert "--stealth" in cmd
    assert FakePopen.last.env["RUST_LOG"] == "off"


def test_serve_omits_stealth_and_rust_log_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obscura = Obscura()
    monkeypatch.setattr(obscura, "_is_running", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(obscura, "is_installed", lambda: True)
    monkeypatch.setattr(setup_mod.subprocess, "Popen", FakePopen)

    obscura.serve(workers=1, stealth=False, rust_log=None)

    assert "--stealth" not in FakePopen.last.cmd
    assert FakePopen.last.env is None
