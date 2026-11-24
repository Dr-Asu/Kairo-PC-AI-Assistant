"""Tests for app discovery and matching — the v0.3.0 app index.

The matcher and cache logic run everywhere; the real Windows scan
(Start Menu + App Paths registry) only runs on Windows.
"""

import json
import sys
import time

import pytest

from kairo.apps import AppInfo, find_app, load_apps, match_app, normalize, resolve_exe
from kairo.pc.controller import SystemController

SAMPLE = [
    AppInfo(name="Google Chrome", target=r"C:\Program Files\Google\Chrome\Application\chrome.exe", exe="chrome.exe", source="app-paths"),
    AppInfo(name="Microsoft Excel", target=r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE", exe="EXCEL.EXE", source="app-paths"),
    AppInfo(name="Notepad", target="notepad.exe", exe="notepad.exe", source="alias"),
    AppInfo(name="VLC media player", target=r"C:\Program Files\VideoLAN\VLC\vlc.exe", exe="vlc.exe", source="start-menu"),
    AppInfo(name="WhatsApp", target=r"C:\Users\boss\AppData\Local\WhatsApp\WhatsApp.exe", exe="WhatsApp.exe", source="start-menu"),
]


def test_normalize_strips_case_spaces_and_punctuation():
    assert normalize("MS Excel 2016") == "msexcel2016"
    assert normalize("Google-Chrome!") == "googlechrome"
    assert normalize("  VLC media player ") == "vlcmediaplayer"


def test_match_exact_and_case_insensitive():
    assert match_app("Google Chrome", SAMPLE).name == "Google Chrome"
    assert match_app("google chrome", SAMPLE).name == "Google Chrome"


def test_match_by_exe_name():
    assert match_app("excel", SAMPLE).name == "Microsoft Excel"
    assert match_app("vlc", SAMPLE).name == "VLC media player"


def test_match_prefix_and_contains():
    assert match_app("micro", SAMPLE).name == "Microsoft Excel"
    assert match_app("media player", SAMPLE).name == "VLC media player"


def test_match_forgives_typos():
    assert match_app("excell", SAMPLE).name == "Microsoft Excel"
    assert match_app("chrom", SAMPLE).name == "Google Chrome"
    assert match_app("whatsap", SAMPLE).name == "WhatsApp"


def test_match_returns_none_for_unknown():
    assert match_app("definitely-not-a-real-app-xyz", SAMPLE) is None
    assert match_app("", SAMPLE) is None


def test_resolve_exe_for_exe_paths_and_names():
    assert resolve_exe(SAMPLE[1]) == "EXCEL.EXE"
    assert resolve_exe(AppInfo(name="x", target=r"C:\Tools\app.exe")) == "app.exe"
    assert resolve_exe(AppInfo(name="x", target="ms-settings:")) is None


def test_cache_round_trip_and_stale_rebuild(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text(
        json.dumps({"updated_at": time.time(), "apps": [SAMPLE[0].__dict__]}),
        encoding="utf-8",
    )
    loaded = load_apps(tmp_path)
    assert [a.name for a in loaded] == ["Google Chrome"]
    # Stale cache (older than 24 h) is rebuilt from the discover function.
    path.write_text(
        json.dumps({"updated_at": time.time() - 25 * 3600, "apps": []}),
        encoding="utf-8",
    )
    rebuilt = load_apps(tmp_path, _discover=lambda: SAMPLE)
    assert len(rebuilt) == len(SAMPLE)


def test_cache_corrupt_file_rebuilds(tmp_path):
    (tmp_path / "apps.json").write_text("not json at all", encoding="utf-8")
    assert len(load_apps(tmp_path, _discover=lambda: SAMPLE)) == len(SAMPLE)


def test_find_app_rescans_once_on_a_miss(tmp_path, monkeypatch):
    calls = {"n": 0}

    def discover():
        calls["n"] += 1
        # First scan is empty (new app not seen yet), second finds it.
        return [] if calls["n"] == 1 else SAMPLE

    monkeypatch.setattr("kairo.apps.discover_apps", discover)
    hit = find_app("vlc", tmp_path)
    assert hit is not None and hit.name == "VLC media player"
    assert calls["n"] == 2  # first scan missed, one forced rescan found it


def test_find_app_uses_cache_when_present(tmp_path):
    (tmp_path / "apps.json").write_text(
        json.dumps(
            {"updated_at": time.time(), "apps": [a.__dict__ for a in SAMPLE]},
        ),
        encoding="utf-8",
    )
    assert find_app("whatsapp", tmp_path).name == "WhatsApp"


class TestControllerAppIndex:
    def test_list_apps_returns_aliases_on_linux(self, tmp_path):
        ctrl = SystemController(dry_run=False, data_dir=tmp_path)
        if ctrl.is_windows:
            pytest.skip("Linux-only behaviour")
        names = ctrl.list_apps()
        assert "notepad" in [n.lower() for n in names]
        assert "calculator" in [n.lower() for n in names]

    def test_rescan_apps_returns_a_count(self, tmp_path):
        ctrl = SystemController(dry_run=False, data_dir=tmp_path)
        assert isinstance(ctrl.rescan_apps(), int)

    def test_open_unknown_app_raises_with_a_helpful_message(self, tmp_path):
        ctrl = SystemController(dry_run=False, data_dir=tmp_path)
        if ctrl.is_windows:
            pytest.skip("Linux-only behaviour")
        with pytest.raises(Exception) as excinfo:
            ctrl.open_app("definitely-not-a-real-app-xyz")
        assert "definitely-not-a-real-app-xyz" in str(excinfo.value)

    @pytest.mark.windows_only
    @pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows")
    def test_open_notepad_verified_on_windows(self, tmp_path):
        ctrl = SystemController(dry_run=False, data_dir=tmp_path)
        assert "Notepad" in ctrl.open_app("notepad")
        assert "already open" in ctrl.open_app("notepad")
        assert "Closed" in ctrl.close_app("notepad")
