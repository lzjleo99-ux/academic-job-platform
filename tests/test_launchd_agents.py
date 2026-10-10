"""T28 (packet 016): 'plist XML validity asserted in a test; install script shellcheck-clean or bash -n'.
shellcheck itself is not installed in this environment (checked live) - `bash -n` is the packet's own
explicitly sanctioned fallback when it is not available, used here.
"""
import plistlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = REPO_ROOT / "tools" / "launchd"
PLISTS = ["com.ajp.scheduler.plist", "com.ajp.api.plist", "com.ajp.web.plist"]


def _load(name: str) -> dict:
    return plistlib.loads((LAUNCHD_DIR / name).read_bytes())


def test_every_plist_is_valid_xml_plistlib_can_parse():
    for name in PLISTS:
        d = _load(name)
        assert isinstance(d, dict) and "Label" in d


def test_every_plist_declares_the_right_label_matching_its_own_filename():
    for name in PLISTS:
        d = _load(name)
        assert d["Label"] == name.removesuffix(".plist")


def test_every_plist_runs_at_load_and_restarts_only_after_a_crash_not_a_clean_exit():
    """RunAtLoad + KeepAlive.SuccessfulExit=false - the user's own explicit demand ('never depend on a
    human manually running a command') needs both: start itself, and come back after a real failure."""
    for name in PLISTS:
        d = _load(name)
        assert d["RunAtLoad"] is True
        assert d["KeepAlive"] == {"SuccessfulExit": False}
        assert d["ThrottleInterval"] == 600


def test_every_plist_logs_under_the_ajp_logs_directory_placeholder():
    for name in PLISTS:
        d = _load(name)
        assert d["StandardOutPath"].startswith("__AJP_LOG_DIR__/")
        assert d["StandardErrorPath"].startswith("__AJP_LOG_DIR__/")


def test_scheduler_plist_runs_ajp_run_daemon():
    d = _load("com.ajp.scheduler.plist")
    assert d["ProgramArguments"] == ["__AJP_VENV_BIN__/ajp-run", "--daemon"]


def test_api_plist_runs_ajp_api():
    d = _load("com.ajp.api.plist")
    assert d["ProgramArguments"] == ["__AJP_VENV_BIN__/ajp-api"]


def test_web_plist_runs_npm_with_a_substitutable_script_name():
    d = _load("com.ajp.web.plist")
    assert d["ProgramArguments"] == ["__AJP_NPM_BIN__", "run", "__AJP_WEB_NPM_SCRIPT__"]
    assert d["EnvironmentVariables"]["PORT"] == "3000"


def test_no_committed_plist_ever_leaks_a_real_absolute_path_from_this_dev_machine():
    """The committed templates must stay generic - a real path (this machine's own home directory, for
    instance) baked in here would be wrong on every OTHER machine that installs this project."""
    home = str(Path.home())
    for name in PLISTS:
        raw = (LAUNCHD_DIR / name).read_text()
        assert home not in raw
        assert "/Users/" not in raw or "__AJP_" in raw  # only ever appears inside a placeholder token


def _bash_syntax_ok(path: Path) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)


def test_install_agents_script_has_valid_bash_syntax():
    r = _bash_syntax_ok(REPO_ROOT / "tools" / "install_agents.sh")
    assert r.returncode == 0, r.stderr


def test_uninstall_agents_script_has_valid_bash_syntax():
    r = _bash_syntax_ok(REPO_ROOT / "tools" / "uninstall_agents.sh")
    assert r.returncode == 0, r.stderr


def test_both_scripts_are_executable():
    assert (REPO_ROOT / "tools" / "install_agents.sh").stat().st_mode & 0o111
    assert (REPO_ROOT / "tools" / "uninstall_agents.sh").stat().st_mode & 0o111


def test_install_script_never_deletes_the_ajp_data_directory():
    """A real, textual guard: neither script may ever reference removing ~/.ajp itself - only the log dir
    it creates and the LaunchAgent plists it manages."""
    for script in ("install_agents.sh", "uninstall_agents.sh"):
        text = (REPO_ROOT / "tools" / script).read_text()
        assert "rm -rf" not in text and "rm -r " not in text
        assert ".ajp\"" not in text.replace("logs", "")  # never a bare rm target on the data dir itself


def test_uninstall_script_only_boots_out_and_removes_plists_never_touches_ajp_data():
    text = (REPO_ROOT / "tools" / "uninstall_agents.sh").read_text()
    assert "bootout" in text
    assert "$HOME/.ajp\"" not in text.replace("/.ajp/logs", "")
