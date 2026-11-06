"""Linux Qt display/input environment selection before QApplication."""

from src import linux_desktop


def _plugins(
    tmp_path,
    platforms=("xcb", "wayland"),
    themes=("gtk3",),
    input_contexts=("ibus", "compose"),
):
    root = tmp_path / "plugins"
    specs = (
        ("platforms", platforms, "libq{}.so"),
        ("platformthemes", themes, "libq{}.so"),
        ("platforminputcontexts", input_contexts, "lib{}platforminputcontextplugin.so"),
    )
    for subdir, names, pattern in specs:
        directory = root / subdir
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / pattern.format(name)).touch()
    return root


def _prepare(plugin_dir, env):
    return linux_desktop.prepare_qt_environment(
        plugin_dir, environ=env, platform_name="linux"
    )


def test_wayland_session_gets_an_x11_fallback(tmp_path):
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    _prepare(_plugins(tmp_path), env)
    assert env["QT_QPA_PLATFORM"] == "wayland;xcb"


def test_x11_session_selects_xcb(tmp_path):
    env = {"DISPLAY": ":0"}
    _prepare(_plugins(tmp_path), env)
    assert env["QT_QPA_PLATFORM"] == "xcb"


def test_explicit_and_offscreen_platforms_are_kept(tmp_path):
    env = {"DISPLAY": ":0", "QT_QPA_PLATFORM": "xcb"}
    _prepare(_plugins(tmp_path), env)
    assert env["QT_QPA_PLATFORM"] == "xcb"

    env = {"QT_QPA_PLATFORM": "offscreen"}
    _prepare(_plugins(tmp_path, platforms=("offscreen",)), env)
    assert env["QT_QPA_PLATFORM"] == "offscreen"


def test_a_platform_this_build_does_not_ship_falls_back(tmp_path):
    env = {"DISPLAY": ":0", "QT_QPA_PLATFORM": "wayland"}
    _prepare(_plugins(tmp_path, platforms=("xcb",)), env)
    assert env["QT_QPA_PLATFORM"] == "xcb"


def test_a_missing_platform_is_cleared_when_nothing_can_replace_it(tmp_path):
    env = {"QT_QPA_PLATFORM": "wayland"}
    _prepare(_plugins(tmp_path, platforms=("xcb",)), env)
    assert "QT_QPA_PLATFORM" not in env


def test_host_theme_names_are_dropped(tmp_path):
    env = {"DISPLAY": ":0", "QT_QPA_PLATFORMTHEME": "appmenu-qt5"}
    _prepare(_plugins(tmp_path), env)
    assert "QT_QPA_PLATFORMTHEME" not in env


def test_bundled_theme_is_kept(tmp_path):
    env = {"DISPLAY": ":0", "QT_QPA_PLATFORMTHEME": "gtk3"}
    _prepare(_plugins(tmp_path), env)
    assert env["QT_QPA_PLATFORMTHEME"] == "gtk3"


def test_safe_mode_avoids_x11_ibus_and_software_paints(tmp_path):
    env = {
        "WAYLAND_DISPLAY": "wayland-0",
        "DISPLAY": ":0",
        "QT_QPA_PLATFORM": "wayland;xcb",
        "QT_IM_MODULE": "ibus",
        "XMODIFIERS": "@im=ibus",
        "QT_QPA_PLATFORMTHEME": "gtk3",
        "INNIOASIS_SAFE_MODE": "1",
    }
    _prepare(_plugins(tmp_path), env)
    assert env["QT_QPA_PLATFORM"] == "xcb"
    assert env["QT_IM_MODULE"] == "compose"
    assert "XMODIFIERS" not in env
    assert "QT_QPA_PLATFORMTHEME" not in env
    assert env["QT_OPENGL"] == "software"
    assert env["LIBGL_ALWAYS_SOFTWARE"] == "1"


def test_safe_mode_on_a_pure_wayland_desktop_stays_on_wayland(tmp_path):
    env = {"WAYLAND_DISPLAY": "wayland-0", "INNIOASIS_SAFE_MODE": "1"}
    _prepare(_plugins(tmp_path), env)
    assert env["QT_QPA_PLATFORM"] == "wayland"


def test_opt_out_and_other_platforms_change_nothing(tmp_path):
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "INNIOASIS_NO_QT_ENV_FIX": "1"}
    _prepare(_plugins(tmp_path), env)
    assert "QT_QPA_PLATFORM" not in env

    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    changes = linux_desktop.prepare_qt_environment(
        _plugins(tmp_path), environ=env, platform_name="darwin"
    )
    assert changes == []
    assert "QT_QPA_PLATFORM" not in env
