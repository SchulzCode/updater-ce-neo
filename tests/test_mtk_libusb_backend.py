"""MTKClient's bundled libusb must win over a host that lacks the soname."""

from src import mtk_api


def _linux_appdir(tmp_path, monkeypatch):
    """Point the Linux finder at a temporary AppDir and no dev bundle."""
    appdir = tmp_path / "AppDir"
    appdir.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setenv("APPDIR", str(appdir))
    monkeypatch.setattr(mtk_api.sys, "platform", "linux")
    monkeypatch.setattr(mtk_api.paths, "BUNDLE_DIR", bundle)
    return appdir


def test_linux_finder_uses_appdir_libusb_so_0(tmp_path, monkeypatch):
    appdir = _linux_appdir(tmp_path, monkeypatch)
    libdir = appdir / "usr" / "lib"
    libdir.mkdir(parents=True)
    real = libdir / "libusb-1.0.so.0"
    real.write_bytes(b"\x7fELF")

    assert mtk_api.find_cross_platform_libusb() == str(real)


def test_linux_finder_skips_a_dangling_symlink(tmp_path, monkeypatch):
    appdir = _linux_appdir(tmp_path, monkeypatch)
    libdir = appdir / "usr" / "lib"
    libdir.mkdir(parents=True)
    (libdir / "libusb-1.0.so.0").symlink_to(libdir / "missing-libusb.so.0")

    real = appdir / "usr" / "bin" / "_internal" / "libusb-1.0.so.0"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"\x7fELF")

    assert mtk_api.find_cross_platform_libusb() == str(real)


def test_pyusb_find_uses_the_shared_backend_by_default(monkeypatch):
    import usb.core

    sentinel = object()
    calls = []

    def fake_find(*args, **kwargs):
        calls.append((args, kwargs))
        return "found"

    monkeypatch.setattr(usb.core, "find", fake_find)
    monkeypatch.setattr(mtk_api, "get_cross_platform_libusb_backend", lambda: sentinel)

    assert mtk_api.install_pyusb_default_backend() is sentinel
    assert usb.core.find(idVendor=0x0E8D) == "found"
    assert calls[-1][1]["backend"] is sentinel


def test_pyusb_find_keeps_an_explicit_backend(monkeypatch):
    import usb.core

    sentinel = object()
    explicit = object()
    calls = []

    def fake_find(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(usb.core, "find", fake_find)
    monkeypatch.setattr(mtk_api, "get_cross_platform_libusb_backend", lambda: sentinel)

    mtk_api.install_pyusb_default_backend()
    usb.core.find(backend=explicit)
    assert calls[-1][1]["backend"] is explicit


def test_no_backend_leaves_pyusb_find_alone(monkeypatch):
    import usb.core

    def fake_find(*args, **kwargs):
        return None

    monkeypatch.setattr(usb.core, "find", fake_find)
    monkeypatch.setattr(mtk_api, "get_cross_platform_libusb_backend", lambda: None)

    assert mtk_api.install_pyusb_default_backend() is None
    assert usb.core.find is fake_find
