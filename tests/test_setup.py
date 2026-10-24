"""vibe2knob setup-live and vibe2knob doctor."""

from pathlib import Path

import pytest

from live_bridge import LiveError
from live_bridge.remote_script import (ABLETONOSC_COMMIT, DIFFERENT, MARKER, MISSING, OLDER,
                                       SAME, VENDORED, compare, install, install_dir)
from vibe2knob import doctor, server
from vibe2knob.cli import main
from vibe2knob.store import MemoryKeys, Store


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = tmp_path / "User Library"
    lib.mkdir()
    monkeypatch.setenv("VIBE2KNOB_HOME", str(tmp_path / "data"))
    return lib


def test_vendored_abletonosc_is_complete():
    assert len(ABLETONOSC_COMMIT) == 40
    for rel in ["__init__.py", "manager.py", "abletonosc/track.py", "pythonosc/dispatcher.py",
                "LICENSE.md"]:
        assert (VENDORED / rel).is_file(), rel


def test_install_copies_then_leaves_the_same_copy(library, tmp_path):
    target = install_dir(library)
    assert compare(target) == MISSING
    assert install(target, tmp_path / "backups").action == "installed"
    assert (target / "manager.py").read_bytes() == (VENDORED / "manager.py").read_bytes()
    assert compare(target) == SAME
    # Live's logs and caches, or a clone's extra files, don't make it a different copy.
    (target / "logs").mkdir()
    (target / "logs" / "abletonosc.log").write_text("started")
    (target / "tests").mkdir()
    assert install(target, tmp_path / "backups").action == "unchanged"


def test_a_different_copy_needs_force_and_is_backed_up(library, tmp_path):
    target = install_dir(library)
    target.mkdir(parents=True)
    (target / "manager.py").write_text("# someone's own version")
    assert compare(target) == DIFFERENT
    assert install(target, tmp_path / "backups").action == "refused"
    assert (target / "manager.py").read_text() == "# someone's own version"

    result = install(target, tmp_path / "backups", force=True)
    assert result.action == "replaced"
    assert (result.backup / "manager.py").read_text() == "# someone's own version"
    assert result.backup.parent == tmp_path / "backups"  # not in Remote Scripts
    assert compare(target) == SAME


def test_a_copy_we_installed_is_updated_without_force(library, tmp_path):
    target = install_dir(library)
    install(target, tmp_path / "backups")
    assert ABLETONOSC_COMMIT in (target / MARKER).read_text()
    (target / "manager.py").write_text("# the copy an older vibe2knob pinned")
    assert compare(target) == OLDER
    result = install(target, tmp_path / "backups")
    assert (result.action, result.backup) == ("updated", None)
    assert compare(target) == SAME


def test_setup_live_command(library, monkeypatch, capsys):
    live = doctor.Check(doctor.FAIL, "Live")
    monkeypatch.setattr(doctor, "check_live", lambda: live)
    assert main(["setup-live", "--user-library", str(library)]) == 0
    out = capsys.readouterr().out
    assert "Installed AbletonOSC (0ca6821)" in out and "Control Surface" in out
    assert main(["setup-live", "--user-library", str(library)]) == 0
    out = capsys.readouterr().out
    assert "already installed" in out and "Control Surface" in out
    # Already installed and Live answers: nothing is left to do in Live.
    live = doctor.Check(doctor.OK, "Live")
    assert main(["setup-live", "--user-library", str(library)]) == 0
    out = capsys.readouterr().out
    assert "nothing left to do" in out and "Control Surface" not in out

    (install_dir(library) / MARKER).unlink()  # someone's own copy from here on
    (install_dir(library) / "manager.py").write_text("changed")
    assert main(["setup-live", "--user-library", str(library)]) == 1
    assert "--force" in capsys.readouterr().out
    assert main(["setup-live", "--user-library", str(library), "--force"]) == 0
    assert "now in" in capsys.readouterr().out


def test_setup_live_needs_the_user_library(tmp_path, capsys):
    assert main(["setup-live", "--user-library", str(tmp_path / "nowhere")]) == 2
    assert "User Library isn't at" in capsys.readouterr().err


class _Bridge:
    def __init__(self, answers=True, version="11.3"):
        self.answers, self.version, self.closed = answers, version, False

    def ping(self):
        if not self.answers:
            raise LiveError("no answer")

    def live_version(self):
        return self.version

    def close(self):
        self.closed = True


def test_doctor_live_check():
    bridge = _Bridge()
    check = doctor.check_live(bridge_factory=lambda timeout: bridge, hub_running=lambda p: False)
    assert (check.status, check.detail) == (doctor.OK, "Live 11.3 answers")
    assert bridge.closed

    check = doctor.check_live(bridge_factory=lambda timeout: _Bridge(answers=False),
                              hub_running=lambda p: False)
    assert check.status == doctor.FAIL and "Control Surface" in check.detail

    def busy(timeout):
        raise ConnectionError("Port 11001 is busy.")
    check = doctor.check_live(bridge_factory=busy, hub_running=lambda p: False)
    assert check.status == doctor.FAIL and "busy" in check.detail

    # A running panel owns the OSC port, so doctor doesn't try it.
    check = doctor.check_live(bridge_factory=busy, hub_running=lambda p: True)
    assert check.status == doctor.INFO


def test_doctor_never_shows_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = Store(tmp_path, keys=MemoryKeys())
    assert doctor.check_key(store).status == doctor.WARN
    store.set_api_key("sk-ant-secret")
    check = doctor.check_key(store)
    assert check.status == doctor.OK and "Keychain" in check.detail
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-secret")
    check = doctor.check_key(store)
    assert "ANTHROPIC_API_KEY" in check.detail
    assert "secret" not in doctor.report([check])


def test_doctor_report_and_exit_code(library, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_live", lambda port: doctor.Check(doctor.OK, "Live"))
    monkeypatch.setattr(server, "find_ui", lambda: Path("web"))  # CI's hub job doesn't build it
    assert main(["doctor", "--user-library", str(library)]) == 1  # AbletonOSC is missing
    assert "vibe2knob setup-live" in capsys.readouterr().out
    install(install_dir(library), Path(library) / "b")
    assert main(["doctor", "--user-library", str(library)]) == 0
    out = capsys.readouterr().out
    assert "pinned 0ca6821" in out and "All set." in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith("vibe2knob 0.")
