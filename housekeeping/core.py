"""Core logic for the private Innioasis mirror housekeeping app.

The app is a control plane over the existing ``mirror.yml`` workflows in the
mirror repositories.  It does not download or rebuild gigabyte archives
locally: it reads each published macOS ZIP's central directory with HTTP range
requests, then dispatches the mirror's own workflow when a repair or sync is
needed.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import getpass
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote

import requests

DEFAULT_OWNER = "y1-community"
DEFAULT_WORKFLOW = "mirror.yml"
DEFAULT_MAX_AGE_HOURS = 12
DEFAULT_INTERVAL_HOURS = 6
DEFAULT_APP_NAME = "Innioasis Mirror Housekeeping"
KEYCHAIN_SERVICE = "Innioasis Mirror Housekeeping"
KEYCHAIN_ACCOUNT = "github-token"
USER_AGENT = "innioasis-mirror-housekeeping/1.0"
API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
LAUNCH_AGENT_LABEL = "app.innioasis.mirror-housekeeping"
ZIP_TAIL_BYTES = 1_200_000
EOCD_SIG = b"PK\x05\x06"
ZIP64_EOCD_SIG = b"PK\x06\x06"
ZIP64_LOCATOR_SIG = b"PK\x06\x07"
CD_SIG = b"PK\x01\x02"
ACTIVE_RUN_STATES = {"queued", "in_progress", "waiting", "requested", "pending"}

FetchRange = Callable[[str, int, int], bytes]
Progress = Callable[[int, int, str], None]


class HousekeepingError(RuntimeError):
    """An expected housekeeping failure."""


class GitHubError(HousekeepingError):
    """A GitHub API failure."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class IndexMissing(HousekeepingError):
    """The mirror has no public index yet."""


class ActiveRunError(HousekeepingError):
    """A mirror workflow is already running."""


class ZipReadError(HousekeepingError):
    """A remote ZIP could not be read."""


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def relative_age(value: str | None) -> str:
    parsed = parse_time(value)
    if parsed is None:
        return "-"
    seconds = max(0, int((dt.datetime.now(dt.timezone.utc) - parsed).total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


@dataclasses.dataclass
class LayoutResult:
    ok: bool
    roots: list[str]
    apps: list[str]
    has_app: bool
    has_info: bool
    has_fw_ini: bool
    scatters: list[str]
    detail: str


@dataclasses.dataclass
class MacAsset:
    repo: str
    tag: str
    name: str
    url: str
    size: int
    status: str = "unchecked"  # ok | wrong_layout | error | unchecked
    detail: str = ""
    roots: list[str] = dataclasses.field(default_factory=list)
    checked_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclasses.dataclass
class MirrorStatus:
    repo: str
    generated_at: str = ""
    releases: int = 0
    macos_total: int = 0
    macos_assets: list[MacAsset] = dataclasses.field(default_factory=list)
    run: dict[str, Any] | None = None
    run_error: str = ""
    error: str = ""
    checked_at: str = dataclasses.field(default_factory=utcnow)

    @property
    def macos_checked(self) -> int:
        return sum(1 for asset in self.macos_assets if asset.status in {"ok", "wrong_layout", "error"})

    @property
    def macos_ok(self) -> int:
        return sum(1 for asset in self.macos_assets if asset.status == "ok")

    @property
    def macos_broken(self) -> int:
        return sum(1 for asset in self.macos_assets if asset.status in {"wrong_layout", "error"})

    @property
    def macos_unknown(self) -> int:
        return max(0, self.macos_total - self.macos_checked)

    @property
    def needs_repair(self) -> bool:
        return self.macos_broken > 0

    @property
    def run_active(self) -> bool:
        return run_is_active(self.run)

    @property
    def run_url(self) -> str:
        return str((self.run or {}).get("html_url") or "")

    @property
    def index_age_hours(self) -> float | None:
        parsed = parse_time(self.generated_at)
        if parsed is None:
            return None
        return max(0.0, (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds() / 3600)

    def is_stale(self, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> bool:
        age = self.index_age_hours
        return age is not None and age > max_age_hours


@dataclasses.dataclass
class Action:
    repo: str
    kind: str
    ok: bool
    detail: str = ""


@dataclasses.dataclass
class CheckReport:
    statuses: list[MirrorStatus]
    actions: list[Action] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class AppConfig:
    owner: str = DEFAULT_OWNER
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS
    interval_hours: float = DEFAULT_INTERVAL_HOURS
    auto_update: bool = True
    auto_repair: bool = False
    deep_check: bool = False


class GitHubClient:
    """Small GitHub REST client that never exposes its token."""

    def __init__(self, token: str | None = None, *, api_root: str = API_ROOT, session: requests.Session | None = None):
        self._token = token or None
        self.api_root = api_root.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            }
        )
        if self._token:
            self.session.headers["Authorization"] = f"Bearer {self._token}"

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = path if path.startswith("http") else self.api_root + path
        kwargs.setdefault("timeout", 60)
        try:
            response = self.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise GitHubError(f"{method} {path}: {exc}") from exc
        if not response.ok:
            try:
                payload = response.json()
                message = str(payload.get("message") or "")
            except ValueError:
                message = response.text[:300]
            raise GitHubError(f"{method} {path}: HTTP {response.status_code} {message}".strip(), response.status_code)
        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        response = self._request("GET", path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def post(self, path: str, **kwargs: Any) -> Any:
        response = self._request("POST", path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def paginate(self, path: str) -> Iterable[dict[str, Any]]:
        url = path if path.startswith("http") else self.api_root + path
        while url:
            response = self._request("GET", url)
            payload = response.json()
            if isinstance(payload, list):
                yield from payload
            else:
                yield payload
            url = response.links.get("next", {}).get("url")


def _run_quiet(args: Sequence[str], *, timeout: float = 20) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def app_support_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / DEFAULT_APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "innioasis-mirror-housekeeping"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return app_support_dir() / "config.json"


def local_token_path() -> Path:
    return app_support_dir() / "token"


def load_config() -> AppConfig:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppConfig()
    if not isinstance(data, dict):
        return AppConfig()
    defaults = AppConfig()
    return AppConfig(
        owner=str(data.get("owner") or defaults.owner),
        max_age_hours=float(data.get("max_age_hours") or defaults.max_age_hours),
        interval_hours=float(data.get("interval_hours") or defaults.interval_hours),
        auto_update=bool(data.get("auto_update", defaults.auto_update)),
        auto_repair=bool(data.get("auto_repair", defaults.auto_repair)),
        deep_check=bool(data.get("deep_check", defaults.deep_check)),
    )


def save_config(config: AppConfig) -> None:
    path = config_path()
    payload = dataclasses.asdict(config)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def keychain_token() -> str | None:
    if sys.platform != "darwin":
        return None
    return _run_quiet(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"]
    )


def file_token() -> str | None:
    try:
        value = local_token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def resolve_token(explicit: str | None = None) -> tuple[str | None, str]:
    """Return (token, human-readable source), preferring explicit and Keychain."""
    if explicit:
        return explicit, "settings"
    for env_name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(env_name)
        if value:
            return value, f"${env_name}"
    value = keychain_token()
    if value:
        return value, "macOS Keychain"
    value = _run_quiet(["gh", "auth", "token"])
    if value:
        return value, "gh CLI login"
    value = file_token()
    if value:
        return value, "local token file"
    return None, "not configured"


def save_token(token: str) -> str:
    token = token.strip()
    if not token:
        raise HousekeepingError("The token is empty.")
    if sys.platform == "darwin" and shutil.which("security"):
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
                token,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return "macOS Keychain"
    path = local_token_path()
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return str(path)


def clear_token() -> None:
    if sys.platform == "darwin" and shutil.which("security"):
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
            capture_output=True,
            text=True,
            check=False,
        )
    try:
        local_token_path().unlink()
    except OSError:
        pass


def discover_mirrors(client: GitHubClient, owner: str = DEFAULT_OWNER) -> list[str]:
    """Discover mirror repositories, including ones that have no index yet."""
    try:
        repositories = list(client.paginate(f"/orgs/{owner}/repos?per_page=100&type=all"))
    except GitHubError as exc:
        if exc.status_code != 404:
            raise
        repositories = list(client.paginate(f"/users/{owner}/repos?per_page=100&type=all"))
    names = {
        str(repo.get("full_name") or "")
        for repo in repositories
        if str(repo.get("name") or "").endswith("-mirror") and repo.get("full_name")
    }
    return sorted(names)


def index_url(repo: str) -> str:
    return f"{RAW_ROOT}/{repo}/main/index.json"


def fetch_index(repo: str, *, client: GitHubClient | None = None) -> dict[str, Any]:
    try:
        response = requests.get(index_url(repo), timeout=45, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        raise HousekeepingError(f"{repo}: {exc}") from exc
    if response.status_code == 404:
        if client is not None and client.has_token:
            try:
                payload = client.get(f"/repos/{repo}/contents/index.json")
                content = base64.b64decode(str(payload.get("content") or ""))
                data = json.loads(content.decode("utf-8"))
                if isinstance(data, dict):
                    return data
            except (GitHubError, ValueError, KeyError) as exc:
                raise IndexMissing(f"{repo}: no public index.json ({exc})") from exc
        raise IndexMissing(f"{repo}: no index.json (HTTP 404)")
    if not response.ok:
        raise HousekeepingError(f"{repo}: index.json HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HousekeepingError(f"{repo}: index.json is not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("releases"), list):
        raise HousekeepingError(f"{repo}: index.json has no releases array")
    return data


def latest_workflow_run(
    client: GitHubClient,
    repo: str,
    workflow: str = DEFAULT_WORKFLOW,
) -> dict[str, Any] | None:
    try:
        payload = client.get(f"/repos/{repo}/actions/workflows/{quote(workflow, safe='')}/runs?per_page=1")
    except GitHubError as exc:
        if exc.status_code == 404:
            return None
        raise
    runs = (payload or {}).get("workflow_runs") or []
    return runs[0] if runs else None


def run_is_active(run: dict[str, Any] | None) -> bool:
    return bool(run and str(run.get("status") or "").lower() in ACTIVE_RUN_STATES)


def _fetch_range(url: str, start: int, end: int, *, timeout: float = 60) -> bytes:
    if start < 0 or end < start:
        raise ZipReadError(f"invalid byte range {start}-{end}")
    last_error = "range request failed"
    for attempt in range(4):
        response = None
        try:
            response = requests.get(
                url,
                headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT},
                stream=True,
                allow_redirects=True,
                timeout=timeout,
            )
            if response.status_code == 206:
                data = response.content
                expected = end - start + 1
                if len(data) == expected:
                    return data
                last_error = f"short range response ({len(data)} of {expected} bytes)"
            if response.status_code == 200:
                raise ZipReadError("the server ignored the byte range")
            if response.status_code in {404, 416}:
                raise ZipReadError(f"HTTP {response.status_code} for range {start}-{end}")
            last_error = f"HTTP {response.status_code}"
        except ZipReadError:
            raise
        except requests.RequestException as exc:
            last_error = str(exc)
        finally:
            if response is not None:
                response.close()
        time.sleep(0.8 * (attempt + 1))
    raise ZipReadError(last_error)


def _total_from_content_range(value: str | None) -> int | None:
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


def probe_zip_size(url: str, *, timeout: float = 60) -> int:
    """Return the live size of a release asset without downloading it.

    Index files are generated at the end of a mirror run. During or just after
    a rebuild an index can still carry the previous asset size, so the size in
    the index must not be used to choose a range for the ZIP tail.
    """
    last_error = "size probe failed"
    for attempt in range(4):
        response = None
        try:
            response = requests.get(
                url,
                headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT},
                stream=True,
                allow_redirects=True,
                timeout=timeout,
            )
            if response.status_code == 206:
                total = _total_from_content_range(response.headers.get("Content-Range"))
                if total is not None and total > 0:
                    return total
                last_error = "range response had no total size"
            elif response.status_code == 200:
                length = response.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > 0:
                    return int(length)
                last_error = "response had no content length"
            elif response.status_code in {404, 416}:
                raise ZipReadError(f"HTTP {response.status_code} while probing the ZIP size")
            else:
                last_error = f"HTTP {response.status_code}"
        except ZipReadError:
            raise
        except requests.RequestException as exc:
            last_error = str(exc)
        finally:
            if response is not None:
                response.close()
        time.sleep(0.8 * (attempt + 1))
    raise ZipReadError(last_error)


def _parse_central_directory(data: bytes, total_entries: int) -> list[str]:
    names: list[str] = []
    offset = 0
    for _ in range(total_entries):
        if offset + 46 > len(data) or data[offset : offset + 4] != CD_SIG:
            raise ZipReadError("invalid ZIP central directory")
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, offset + 28)
        end = offset + 46 + name_len + extra_len + comment_len
        if end > len(data):
            raise ZipReadError("truncated ZIP central directory")
        raw_name = data[offset + 46 : offset + 46 + name_len]
        try:
            names.append(raw_name.decode("utf-8"))
        except UnicodeDecodeError:
            names.append(raw_name.decode("cp437"))
        offset = end
    if len(names) != total_entries:
        raise ZipReadError(f"parsed {len(names)} of {total_entries} ZIP entries")
    return names


def _names_from_tail(
    blob: bytes,
    blob_start: int,
    file_size: int,
    fetch: FetchRange,
) -> list[str]:
    position = blob.rfind(EOCD_SIG)
    if position < 0 or position + 22 > len(blob):
        raise ZipReadError("ZIP end-of-central-directory record not found")
    _, _, _, _, total, cd_size, cd_offset, _ = struct.unpack_from("<4s4H2LH", blob, position)

    if total == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        locator = blob.rfind(ZIP64_LOCATOR_SIG, 0, position)
        if locator < 0 or locator + 20 > len(blob):
            raise ZipReadError("ZIP64 locator not found")
        zip64_offset = struct.unpack_from("<Q", blob, locator + 8)[0]
        relative = zip64_offset - blob_start
        if 0 <= relative and relative + 56 <= len(blob):
            zip64 = blob[relative : relative + 56]
        else:
            zip64 = fetch(zip64_offset, zip64_offset + 55)
        if len(zip64) < 56 or zip64[:4] != ZIP64_EOCD_SIG:
            raise ZipReadError("invalid ZIP64 end-of-central-directory record")
        _, _, _, _, _, _, _, total, cd_size, cd_offset = struct.unpack_from("<4sQ2H2L4Q", zip64)
        total = int(total)
        cd_size = int(cd_size)
        cd_offset = int(cd_offset)

    if cd_offset < 0 or cd_size < 0 or cd_offset + cd_size > file_size:
        raise ZipReadError("ZIP central directory is outside the file")
    relative = cd_offset - blob_start
    if 0 <= relative and relative + cd_size <= len(blob):
        central_directory = blob[relative : relative + cd_size]
    else:
        central_directory = fetch(cd_offset, cd_offset + cd_size - 1)
    return _parse_central_directory(central_directory, total)


def list_remote_zip(
    url: str,
    size: int,
    *,
    fetch: FetchRange | None = None,
    timeout: float = 60,
) -> list[str]:
    """Read a remote ZIP's entry names without downloading its payload."""
    if size <= 0:
        raise ZipReadError("empty ZIP")
    range_fetch = fetch or (lambda target, start, end: _fetch_range(target, start, end, timeout=timeout))
    window = min(size, ZIP_TAIL_BYTES)
    tail_start = size - window
    blob = range_fetch(url, tail_start, size - 1)
    return _names_from_tail(blob, tail_start, size, lambda start, end: range_fetch(url, start, end))


def parse_zip_names(blob: bytes) -> list[str]:
    """Parse entry names from a complete local ZIP (used by tests and diagnostics)."""
    def unavailable(_start: int, _end: int) -> bytes:
        raise ZipReadError("central directory is outside the supplied bytes")

    return _names_from_tail(blob, 0, len(blob), unavailable)


def classify_zip_names(names: Sequence[str]) -> LayoutResult:
    normalized = [str(name).replace("\\", "/") for name in names]
    roots = sorted({name.split("/", 1)[0] for name in normalized if name and not name.startswith("/")})
    apps = sorted({name.split("/", 1)[0] for name in normalized if name.split("/", 1)[0].endswith(".app")})
    has_app = "Install Firmware.app" in apps
    has_info = "Install Firmware.app/Contents/Info.plist" in normalized
    has_fw_ini = "Firmware/fw_package.ini" in normalized
    scatters = [
        name
        for name in normalized
        if name.startswith("Firmware/")
        and name.lower().endswith(".txt")
        and "scatter" in name.lower()
        and "preserve" not in name.lower()
    ]
    missing: list[str] = []
    if not has_app:
        missing.append("Install Firmware.app")
    if not has_info:
        missing.append("Install Firmware.app/Contents/Info.plist")
    if not has_fw_ini:
        missing.append("Firmware/fw_package.ini")
    if not scatters:
        missing.append("Firmware/*scatter*.txt")
    ok = not missing
    detail = "Install Firmware.app + Firmware/" if ok else "missing " + ", ".join(missing)
    return LayoutResult(
        ok=ok,
        roots=roots,
        apps=apps,
        has_app=has_app,
        has_info=has_info,
        has_fw_ini=has_fw_ini,
        scatters=scatters,
        detail=detail,
    )


def assets_from_index(repo: str, index: dict[str, Any], *, latest_only: bool = False) -> list[MacAsset]:
    releases = index.get("releases") or []
    if latest_only:
        releases = releases[:1]
    result: list[MacAsset] = []
    for release in releases:
        tag = str(release.get("tag") or "")
        for asset in release.get("assets") or []:
            name = str(asset.get("name") or "")
            if not name.endswith("-macos.zip"):
                continue
            url = str(asset.get("url") or "")
            if not url:
                url = f"https://github.com/{repo}/releases/download/{quote(tag, safe='')}/{quote(name)}"
            result.append(
                MacAsset(
                    repo=repo,
                    tag=tag,
                    name=name,
                    url=url,
                    size=int(asset.get("size") or 0),
                )
            )
    return result


def check_assets(assets: Sequence[MacAsset], *, max_workers: int = 4, progress: Progress | None = None) -> None:
    if not assets:
        return
    lock = threading.Lock()
    completed = 0

    def inspect(asset: MacAsset) -> None:
        try:
            actual_size = probe_zip_size(asset.url)
            asset.size = actual_size
            names = list_remote_zip(asset.url, actual_size)
            result = classify_zip_names(names)
            asset.status = "ok" if result.ok else "wrong_layout"
            asset.detail = result.detail
            asset.roots = result.roots
        except Exception as exc:  # noqa: BLE001 - an asset failure belongs in the row
            asset.status = "error"
            asset.detail = str(exc)
            asset.roots = []
        asset.checked_at = utcnow()

    workers = max(1, min(max_workers, len(assets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(inspect, asset) for asset in assets]
        for future in as_completed(futures):
            future.result()
            with lock:
                completed += 1
                done = completed
            if progress:
                progress(done, len(assets), f"{assets[0].repo}: {done}/{len(assets)} macOS assets")


def check_mirror(
    client: GitHubClient,
    repo: str,
    *,
    deep: bool = False,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    asset_workers: int = 4,
    progress: Progress | None = None,
) -> MirrorStatus:
    status = MirrorStatus(repo=repo)
    try:
        status.run = latest_workflow_run(client, repo)
    except Exception as exc:  # noqa: BLE001 - a run lookup must not hide the index
        status.run_error = str(exc)
    try:
        index = fetch_index(repo, client=client)
    except Exception as exc:  # noqa: BLE001 - shown as a mirror error
        status.error = str(exc)
        status.checked_at = utcnow()
        return status

    status.generated_at = str(index.get("generated_at") or "")
    status.releases = len(index.get("releases") or [])
    all_assets = assets_from_index(repo, index)
    status.macos_total = len(all_assets)
    status.macos_assets = all_assets if deep else assets_from_index(repo, index, latest_only=True)
    check_assets(status.macos_assets, max_workers=asset_workers, progress=progress)
    status.checked_at = utcnow()
    return status


def check_all(
    client: GitHubClient,
    *,
    owner: str = DEFAULT_OWNER,
    repos: Sequence[str] | None = None,
    deep: bool = False,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    max_workers: int = 4,
    progress: Progress | None = None,
) -> list[MirrorStatus]:
    selected = list(repos) if repos else discover_mirrors(client, owner)
    if not selected:
        raise HousekeepingError(f"No mirror repositories found under {owner}.")
    results: list[MirrorStatus] = []
    workers = max(1, min(max_workers, len(selected)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                check_mirror,
                client,
                repo,
                deep=deep,
                max_age_hours=max_age_hours,
                asset_workers=1,
            ): repo
            for repo in selected
        }
        for done, future in enumerate(as_completed(futures), start=1):
            repo = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - keep checking the rest
                results.append(MirrorStatus(repo=repo, error=str(exc)))
            if progress:
                progress(done, len(selected), repo)
    return sorted(results, key=lambda item: item.repo)


def dispatch_inputs(
    *,
    platforms: str = "",
    tags: str = "",
    limit: str = "",
    offset: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for key, value in (("tags", tags), ("platforms", platforms), ("limit", limit), ("offset", offset)):
        if value:
            inputs[key] = str(value)
    if force:
        inputs["force"] = "true"
    if dry_run:
        inputs["dry_run"] = "true"
    return inputs


def dispatch_workflow(
    client: GitHubClient,
    repo: str,
    *,
    platforms: str = "",
    tags: str = "",
    limit: str = "",
    offset: str = "",
    force: bool = False,
    dry_run: bool = False,
    workflow: str = DEFAULT_WORKFLOW,
    ref: str = "main",
    allow_active: bool = False,
) -> None:
    if not client.has_token:
        raise HousekeepingError("A GitHub token is required to dispatch a workflow.")
    active = latest_workflow_run(client, repo, workflow)
    if run_is_active(active) and not allow_active:
        url = str((active or {}).get("html_url") or "")
        raise ActiveRunError(f"{repo}: a mirror run is already in progress. {url}".strip())
    inputs = dispatch_inputs(
        platforms=platforms,
        tags=tags,
        limit=limit,
        offset=offset,
        force=force,
        dry_run=dry_run,
    )
    client.post(
        f"/repos/{repo}/actions/workflows/{quote(workflow, safe='')}/dispatches",
        json={"ref": ref, "inputs": inputs},
    )


def repair_macos(client: GitHubClient, repo: str, *, tags: str = "") -> None:
    dispatch_workflow(client, repo, platforms="macos", tags=tags, force=True)


def update_mirror(client: GitHubClient, repo: str, *, force: bool = False) -> None:
    dispatch_workflow(client, repo, force=force)


def check_and_act(
    client: GitHubClient,
    *,
    owner: str = DEFAULT_OWNER,
    repos: Sequence[str] | None = None,
    deep: bool = False,
    auto_update: bool = False,
    auto_repair: bool = False,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    progress: Progress | None = None,
) -> CheckReport:
    statuses = check_all(
        client,
        owner=owner,
        repos=repos,
        deep=deep,
        max_age_hours=max_age_hours,
        progress=progress,
    )
    report = CheckReport(statuses=statuses)
    for status in statuses:
        if status.run_active:
            report.actions.append(Action(status.repo, "skip", True, "a run is already in progress"))
            continue
        try:
            if auto_repair and status.needs_repair:
                repair_macos(client, status.repo)
                report.actions.append(Action(status.repo, "repair", True, "dispatched macOS rebuild"))
            elif auto_update and (status.error or status.is_stale(max_age_hours)):
                update_mirror(client, status.repo)
                report.actions.append(Action(status.repo, "update", True, "dispatched mirror sync"))
        except Exception as exc:  # noqa: BLE001 - keep acting on the other mirrors
            report.actions.append(Action(status.repo, "error", False, str(exc)))
    return report


def macos_label(status: MirrorStatus) -> str:
    if status.error:
        return "error"
    if status.macos_total == 0:
        return "none"
    if status.macos_broken:
        suffix = f" +{status.macos_unknown} unchecked" if status.macos_unknown else ""
        return f"{status.macos_broken} broken{suffix}"
    if status.macos_checked == 0:
        return f"{status.macos_total} unchecked"
    suffix = f" +{status.macos_unknown} unchecked" if status.macos_unknown else ""
    return f"OK ({status.macos_ok}){suffix}"


def run_label(status: MirrorStatus) -> str:
    if status.run_error:
        return "no workflow"
    run = status.run
    if not run:
        return "none"
    state = str(run.get("status") or "unknown")
    conclusion = str(run.get("conclusion") or "")
    label = state if state != "completed" else (conclusion or "completed")
    when = relative_age(str(run.get("created_at") or ""))
    return f"{label} ({when})"


def format_table(statuses: Sequence[MirrorStatus]) -> str:
    headers = ("Mirror", "Index", "Releases", "macOS", "Latest run")
    rows = [
        (
            status.repo,
            relative_age(status.generated_at) if status.generated_at else "missing",
            str(status.releases),
            macos_label(status),
            run_label(status),
        )
        for status in statuses
    ]
    widths = [max(len(headers[i]), max((len(row[i]) for row in rows), default=0)) for i in range(len(headers))]
    lines = ["  ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in rows)
    return "\n".join(lines)


def has_issues(statuses: Sequence[MirrorStatus], max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> bool:
    return any(status.error or status.needs_repair or status.is_stale(max_age_hours) for status in statuses)


def notify(title: str, message: str) -> None:
    """Post a macOS notification as this process's own app, or not at all.

    A scheduled run started from the packaged app inherits its bundle identity,
    so the notice appears under the app that installed the job. A source
    checkout has no such identity, and the only thing left to hand a banner to
    would be a script host: macOS files those under Script Editor, which is not
    this app and not something the user opened. There the run's log is the
    report.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        if bundle is None or not bundle.bundleIdentifier():
            return
        from AppKit import NSUserNotification, NSUserNotificationCenter

        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            return
        notification = NSUserNotification.alloc().init()
        notification.setTitle_(str(title))
        notification.setInformativeText_(str(message))
        center.deliverNotification_(notification)
    except Exception:
        pass


def launch_agent_path(label: str = LAUNCH_AGENT_LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def schedule_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "InnioasisMirrorHousekeeping.log"


def build_launchd_plist(
    program: Sequence[str],
    *,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    working_directory: str | Path | None = None,
    log_path: str | Path | None = None,
    label: str = LAUNCH_AGENT_LABEL,
) -> bytes:
    log = str(log_path or schedule_log_path())
    payload: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": [str(part) for part in program],
        "StartInterval": max(60, int(float(interval_hours) * 3600)),
        "RunAtLoad": False,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }
    if working_directory:
        payload["WorkingDirectory"] = str(working_directory)
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def install_schedule(
    program: Sequence[str],
    *,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    working_directory: str | Path | None = None,
    log_path: str | Path | None = None,
    label: str = LAUNCH_AGENT_LABEL,
) -> Path:
    if sys.platform != "darwin":
        raise HousekeepingError("The launchd schedule is only available on macOS.")
    path = launch_agent_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        build_launchd_plist(
            program,
            interval_hours=interval_hours,
            working_directory=working_directory,
            log_path=log_path,
            label=label,
        )
    )
    path.chmod(0o644)
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise HousekeepingError(f"launchctl bootstrap failed: {result.stderr.strip() or result.stdout.strip()}")
    subprocess.run(
        ["launchctl", "enable", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return path


def remove_schedule(label: str = LAUNCH_AGENT_LABEL) -> bool:
    if sys.platform != "darwin":
        return False
    path = launch_agent_path(label)
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    existed = path.exists()
    try:
        path.unlink()
    except OSError:
        pass
    return existed


def schedule_installed(label: str = LAUNCH_AGENT_LABEL) -> bool:
    return launch_agent_path(label).exists()


def schedule_command(*, auto_update: bool = True, auto_repair: bool = False, deep: bool = True) -> list[str]:
    base = [sys.executable]
    if not getattr(sys, "frozen", False):
        base += ["-m", "housekeeping"]
    command = base + ["check", "--notify"]
    if auto_update:
        command.append("--update")
    if auto_repair:
        command.append("--repair")
        if deep:
            command.append("--deep")
    return command


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def schedule_working_directory() -> Path | None:
    if getattr(sys, "frozen", False):
        return None
    return project_root()


def read_token_interactive() -> str:
    token = getpass.getpass("GitHub token (input hidden): ").strip()
    if not token:
        raise HousekeepingError("No token entered.")
    return token
