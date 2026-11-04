"""A portable run sharing files with an installed Updater CE.

The run and an installed copy write the same cache and read the same settings
store, so a run with an install present keeps those files, while a run with
nothing installed still cleans up after itself. Its background/notification
switches point at the install screen instead of storing a promise the run
cannot keep.
"""


def _session(tmp_path, monkeypatch, **kwargs):
    from src import portable_run

    folders = {name: tmp_path / name for name in ("downloads", "cache", "temp")}
    for folder in folders.values():
        folder.mkdir()
    monkeypatch.setattr(portable_run, "_downloads_dir", lambda: folders["downloads"])
    monkeypatch.setattr(portable_run, "_cache_dir", lambda: folders["cache"])
    monkeypatch.setattr(portable_run, "_temp_dir", lambda: folders["temp"])
    return portable_run.PortableSession(**kwargs), folders


def test_installed_copy_keeps_the_cache_it_shares(tmp_path, monkeypatch):
    session, folders = _session(tmp_path, monkeypatch, app_installed=True)
    staged = folders["downloads"] / "firmware.bin"
    staged.write_text("staged")
    refreshed = folders["cache"] / "manifest.json"
    refreshed.write_text("{}")
    # The cache is the installed app's, so cleanup never plans to remove it.
    assert refreshed not in session.targets()

    session.cleanup()

    assert not staged.exists(), "this run's staged firmware still goes"
    assert refreshed.exists(), "the installed app's cache survives the run"


def test_no_install_still_cleans_its_own_cache(tmp_path, monkeypatch):
    session, folders = _session(tmp_path, monkeypatch, app_installed=False)
    refreshed = folders["cache"] / "manifest.json"
    refreshed.write_text("{}")

    removed = session.cleanup()

    assert not refreshed.exists()
    assert str(refreshed) in removed


def test_install_presence_is_asked_when_not_supplied(tmp_path, monkeypatch):
    from src import paths

    session, _folders = _session(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "installed_app_present", lambda: True)

    assert session.app_data_kept() is True


def test_progress_card_names_the_software_instead_of_the_ready_heading():
    from PySide6.QtWidgets import QApplication
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    page = FlashPage()
    try:
        page.set_model("Y1")
        page.set_package_name("Get your Y1 ready to install Original Software 3.1.2")
        assert page._flash_pkg_label.text() == "Original Software 3.1.2 for Y1"
    finally:
        page.close()
        app.processEvents()


def test_generic_heading_still_uses_the_device_title():
    from PySide6.QtWidgets import QApplication
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    page = FlashPage()
    try:
        page.set_model("Y2")
        page.set_package_name("Get your Y2 ready to install software")
        assert page._flash_pkg_label.text() == page._generic_install_title()
    finally:
        page.close()
        app.processEvents()


def test_keep_alive_switch_without_an_install_opens_the_install_screen(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src import paths
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    monkeypatch.setattr(paths, "installed_app_present", lambda: False)
    window = MainWindow()
    try:
        opened = []
        window._install_portable_app = lambda: opened.append(True)
        window._settings_page.set_portable_run(True)

        window._settings_page._on_run_in_background_toggled(True)
        app.processEvents()

        assert opened == [True]
        assert window._settings_page._cb_run_in_background.isChecked() is False
    finally:
        window.close()
        app.processEvents()


def test_keep_alive_switch_works_with_an_install(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src import paths
    from src.tray import RUN_IN_BACKGROUND_KEY
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    monkeypatch.setattr(paths, "installed_app_present", lambda: True)
    monkeypatch.setattr("src.login_item.set_login_item", lambda enabled: None)
    window = MainWindow()
    previous = window.settings.value(RUN_IN_BACKGROUND_KEY, None)
    try:
        opened = []
        window._install_portable_app = lambda: opened.append(True)
        window._settings_page.set_portable_run(True)

        window._settings_page._on_run_in_background_toggled(True)
        app.processEvents()

        assert opened == []
        assert window.settings.value(RUN_IN_BACKGROUND_KEY, False) in (True, "true")
    finally:
        if previous is None:
            window.settings.remove(RUN_IN_BACKGROUND_KEY)
        else:
            window.settings.setValue(RUN_IN_BACKGROUND_KEY, previous)
        window.close()
        app.processEvents()
