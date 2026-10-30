from types import SimpleNamespace

from camoufox.fingerprints import Screen

import epic_free.browser as browser


def test_camoufox_uses_its_own_screen_type_and_pinned_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browser,
        "settings",
        SimpleNamespace(
            user_data_dir_for=lambda backend: tmp_path / backend,
            CAMOUFOX_EXECUTABLE_PATH="/opt/camoufox/camoufox-bin",
        ),
    )

    options = browser._camoufox_launch_options(True)

    assert isinstance(options["screen"], Screen)
    assert options["screen"].min_width == options["screen"].max_width == 1920
    assert options["executable_path"] == "/opt/camoufox/camoufox-bin"
    assert options["headless"] is True


def test_unpinned_local_browser_keeps_library_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browser,
        "settings",
        SimpleNamespace(
            user_data_dir_for=lambda backend: tmp_path / backend,
            CAMOUFOX_EXECUTABLE_PATH="",
        ),
    )

    assert "executable_path" not in browser._camoufox_launch_options(False)
