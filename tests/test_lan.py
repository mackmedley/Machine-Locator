"""The bit that decides what address an iPad should open, and proves it works.

Nothing here touches the app itself -- lan.py exists so a launcher can check
its own work without any of the app's behaviour changing.
"""

import socket

import pytest

from machine_locator import lan


# ------------------------------------------------------------- the address

def test_the_address_is_never_loopback():
    """An iPad typing 127.0.0.1 would be asking itself, not this computer."""
    address = lan.local_ip()
    assert address is None or not address.startswith("127.")


def test_the_banner_shows_the_address_to_type(monkeypatch):
    monkeypatch.setattr(lan, "local_ip", lambda: "192.168.1.44")
    banner = lan.banner(5000)
    assert "http://192.168.1.44:5000" in banner
    assert "same Wi-Fi" in banner


def test_the_banner_says_so_rather_than_inventing_an_address(monkeypatch):
    """A made-up address is worse than none -- it sends someone off typing
    something that cannot work."""
    monkeypatch.setattr(lan, "local_ip", lambda: None)
    banner = lan.banner(5000)
    assert "192.168" not in banner
    assert "Wi-Fi" in banner


# ---------------------------------------------------------------- the port

def test_it_steps_over_a_port_already_in_use():
    taken = socket.socket()
    taken.bind(("0.0.0.0", 0))
    port = taken.getsockname()[1]
    taken.listen(1)
    try:
        assert lan.free_port([port, port + 1]) == port + 1
    finally:
        taken.close()


def test_a_free_port_is_taken_as_is():
    spare = socket.socket()
    spare.bind(("0.0.0.0", 0))
    port = spare.getsockname()[1]
    spare.close()
    assert lan.free_port([port]) == port


# ------------------------------------------------------------ reachability

def test_reachable_is_checked_over_the_network_not_localhost(monkeypatch):
    """Binding correctly and being reachable are different things: a firewall
    sits between them, and only the second one matters to the iPad."""
    asked = []

    def fake_connect(address, timeout=None):
        asked.append(address)
        raise OSError("refused")

    monkeypatch.setattr(lan, "local_ip", lambda: "192.168.1.44")
    monkeypatch.setattr(lan.socket, "create_connection", fake_connect)
    assert lan.reachable(5000) is False
    assert asked == [("192.168.1.44", 5000)]


class _Corpse:
    """A server process that has already exited."""

    def poll(self):
        return 1


def test_waiting_gives_up_at_once_when_the_server_died(monkeypatch):
    """Twenty seconds of waiting followed by firewall advice would be the
    wrong answer to a crash."""
    monkeypatch.setattr(lan, "reachable", lambda *a, **k: pytest.fail(
        "should not probe a process that is gone"))
    assert lan.wait_until_reachable(5000, _Corpse()) is False


def test_waiting_stops_as_soon_as_it_answers(monkeypatch):
    tries = []

    def answers_on_the_third_go(port, timeout=0.6):
        tries.append(port)
        return len(tries) == 3

    monkeypatch.setattr(lan, "reachable", answers_on_the_third_go)
    monkeypatch.setattr(lan.time, "sleep", lambda _s: None)
    assert lan.wait_until_reachable(5000, None) is True
    assert len(tries) == 3


def test_waiting_gives_up_eventually(monkeypatch):
    monkeypatch.setattr(lan, "reachable", lambda *a, **k: False)
    monkeypatch.setattr(lan.time, "sleep", lambda _s: None)
    assert lan.wait_until_reachable(5000, None, attempts=3) is False


# --------------------------------------------------------------- the fixes

def test_trouble_names_the_three_usual_causes():
    text = lan.trouble(5000)
    assert "firewall" in text.lower()
    assert "same one" in text
    assert "guest" in text
    # The app has not stopped, and saying otherwise would send someone
    # restarting things that are already working.
    assert "still running" in text


def test_windows_gets_the_command_that_undoes_a_refused_firewall_box(monkeypatch):
    """Windows asks once. Answer No and it never asks again -- which is
    exactly the silent blank page this text exists to explain."""
    monkeypatch.setattr(lan.sys, "platform", "win32")
    text = lan.trouble(5050)
    assert "New-NetFirewallRule" in text
    assert "-LocalPort 5050" in text
    assert "Run as administrator" in text


def test_other_platforms_are_not_told_to_run_powershell(monkeypatch):
    monkeypatch.setattr(lan.sys, "platform", "darwin")
    assert "PowerShell" not in lan.trouble(5000)


# ----------------------------------------------------------- the whole run

class _FakeServer:
    def __init__(self, alive=True):
        self.alive = alive
        self.stopped = False

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.stopped = True
        self.alive = False

    def kill(self):
        self.stopped = True
        self.alive = False


def _run_serve(monkeypatch, reachable, capsys, alive=True):
    server = _FakeServer(alive=alive)
    monkeypatch.setattr(lan.subprocess, "Popen", lambda *a, **k: server)
    monkeypatch.setattr(lan, "free_port", lambda *a, **k: 5000)
    monkeypatch.setattr(lan, "local_ip", lambda: "192.168.1.44")
    monkeypatch.setattr(lan, "wait_until_reachable", lambda *a, **k: reachable)
    monkeypatch.setattr(lan, "_clear", lambda: None)
    code = lan.serve()
    return code, capsys.readouterr().out, server


def test_a_reachable_run_prints_the_address_to_type(monkeypatch, capsys):
    code, out, _ = _run_serve(monkeypatch, True, capsys)
    assert code == 0
    assert "http://192.168.1.44:5000" in out
    assert "Checked: this computer is answering" in out


def test_an_unreachable_run_explains_itself(monkeypatch, capsys):
    """The whole point: a blank page on the iPad becomes a stated reason in
    the window the user is already looking at."""
    code, out, _ = _run_serve(monkeypatch, False, capsys)
    assert code == 0
    assert "Could not reach this computer" in out
    assert "firewall" in out.lower()
    # It must not claim success as well.
    assert "Checked: this computer is answering" not in out


def test_a_server_that_died_is_reported_as_that(monkeypatch, capsys):
    code, out, _ = _run_serve(monkeypatch, False, capsys, alive=False)
    assert code == 1
    assert "stopped while starting up" in out
    assert "firewall" not in out.lower()


def test_the_server_is_stopped_when_the_window_closes(monkeypatch, capsys):
    """Orphaning it would leave the app serving with nothing on screen
    saying so."""
    server = _FakeServer()

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(lan.subprocess, "Popen", lambda *a, **k: server)
    monkeypatch.setattr(lan, "free_port", lambda *a, **k: 5000)
    monkeypatch.setattr(lan, "wait_until_reachable", interrupted)
    monkeypatch.setattr(lan, "_clear", lambda: None)

    assert lan.serve() == 0
    assert server.stopped
