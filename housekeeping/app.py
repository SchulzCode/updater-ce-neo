"""PySide6 front end for the private mirror housekeeping tool."""

from __future__ import annotations

import html
import sys
from typing import Callable, Sequence

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from housekeeping import core

TINT_RED = (231, 76, 60, 38)
TINT_GREEN = (39, 174, 96, 38)
TINT_AMBER = (243, 156, 18, 42)
TINT_BLUE = (52, 152, 219, 38)


class Task(QThread):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[Callable[[int, int, str], None]], object]):
        super().__init__()
        self._work = work

    def run(self) -> None:
        try:
            result = self._work(self.progress.emit)
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            message = str(exc).strip() or exc.__class__.__name__
            self.failed.emit(message)
        else:
            self.done.emit(result)


def _tint(item: QTableWidgetItem, rgba: tuple[int, int, int, int]) -> None:
    item.setBackground(QColor(*rgba))


class SettingsDialog(QDialog):
    def __init__(self, config: core.AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Housekeeping Settings")
        self.setMinimumWidth(620)
        self._save_config = config

        self.owner_edit = QLineEdit(config.owner)
        self.age_spin = QDoubleSpinBox()
        self.age_spin.setRange(1, 168)
        self.age_spin.setDecimals(1)
        self.age_spin.setSuffix(" h")
        self.age_spin.setValue(config.max_age_hours)

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(1, 168)
        self.interval_spin.setDecimals(0)
        self.interval_spin.setSuffix(" h")
        self.interval_spin.setValue(config.interval_hours)

        self.auto_update = QCheckBox("Dispatch a sync when a mirror index is stale or missing")
        self.auto_update.setChecked(config.auto_update)
        self.auto_repair = QCheckBox("Rebuild macOS layouts automatically when a check finds a broken one")
        self.auto_repair.setChecked(config.auto_repair)
        self.deep_check = QCheckBox("Inspect every release in the scheduled deep check")
        self.deep_check.setChecked(config.deep_check)

        self.token_label = QLabel()
        self.token_label.setWordWrap(True)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token_label, 1)
        replace_token = QPushButton("Replace token...")
        replace_token.clicked.connect(self._replace_token)
        token_row.addWidget(replace_token)
        clear_token = QPushButton("Clear")
        clear_token.clicked.connect(self._clear_token)
        token_row.addWidget(clear_token)
        token_widget = QWidget()
        token_widget.setLayout(token_row)

        self.schedule_label = QLabel()
        self.schedule_label.setWordWrap(True)
        schedule_row = QHBoxLayout()
        schedule_row.addWidget(self.schedule_label, 1)
        install_schedule = QPushButton("Install schedule")
        install_schedule.clicked.connect(self._install_schedule)
        schedule_row.addWidget(install_schedule)
        remove_schedule = QPushButton("Remove")
        remove_schedule.clicked.connect(self._remove_schedule)
        schedule_row.addWidget(remove_schedule)
        schedule_widget = QWidget()
        schedule_widget.setLayout(schedule_row)

        form = QFormLayout()
        form.addRow("Mirror owner", self.owner_edit)
        form.addRow("Warn when an index is older than", self.age_spin)
        form.addRow("Scheduled check interval", self.interval_spin)
        form.addRow("", self.auto_update)
        form.addRow("", self.auto_repair)
        form.addRow("", self.deep_check)
        form.addRow("GitHub token", token_widget)
        form.addRow("Local schedule", schedule_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        explanation = QLabel(
            "The app uses your existing gh login, a token in the macOS Keychain, or a token "
            "you paste here. Tokens are never written into the repository. The scheduled job "
            "runs in the background and logs to ~/Library/Logs."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        layout.addWidget(buttons)

        self._refresh_token_label()
        self._refresh_schedule_label()

    def _refresh_token_label(self) -> None:
        _, source = core.resolve_token()
        if source == "not configured":
            self.token_label.setText("Not configured. Sign in with gh or paste a token.")
        else:
            self.token_label.setText(f"Using {source}.")

    def _refresh_schedule_label(self) -> None:
        if core.schedule_installed():
            self.schedule_label.setText(f"Installed at {core.launch_agent_path()}")
        else:
            self.schedule_label.setText("Not installed.")

    def _replace_token(self) -> None:
        token, accepted = QInputDialog.getText(
            self,
            "GitHub token",
            "Paste a token with repository Contents and Actions write access:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted or not token.strip():
            return
        try:
            destination = core.save_token(token)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save token", str(exc))
            return
        self._refresh_token_label()
        QMessageBox.information(self, "Token saved", f"The token was stored in {destination}.")

    def _clear_token(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear token",
            "Remove the saved token? The app can still use your gh CLI login.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        core.clear_token()
        self._refresh_token_label()

    def _install_schedule(self) -> None:
        config = self.config()
        core.save_config(config)
        program = core.schedule_command(
            auto_update=config.auto_update,
            auto_repair=config.auto_repair,
            deep=config.deep_check,
        )
        try:
            path = core.install_schedule(
                program,
                interval_hours=config.interval_hours,
                working_directory=core.schedule_working_directory(),
                log_path=core.schedule_log_path(),
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not install schedule", str(exc))
            return
        self._refresh_schedule_label()
        QMessageBox.information(self, "Schedule installed", f"The launchd job was installed at {path}.")

    def _remove_schedule(self) -> None:
        try:
            existed = core.remove_schedule()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not remove schedule", str(exc))
            return
        self._refresh_schedule_label()
        message = "The launchd job was removed." if existed else "No schedule was installed."
        QMessageBox.information(self, "Schedule", message)

    def _save(self) -> None:
        if not self.owner_edit.text().strip():
            QMessageBox.warning(self, "Owner required", "Enter the GitHub owner or organisation.")
            return
        self.accept()

    def config(self) -> core.AppConfig:
        return core.AppConfig(
            owner=self.owner_edit.text().strip() or core.DEFAULT_OWNER,
            max_age_hours=float(self.age_spin.value()),
            interval_hours=float(self.interval_spin.value()),
            auto_update=self.auto_update.isChecked(),
            auto_repair=self.auto_repair.isChecked(),
            deep_check=self.deep_check.isChecked(),
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = core.load_config()
        self.statuses: list[core.MirrorStatus] = []
        self._task: Task | None = None
        self._busy = False
        self._build_ui()
        self._log(
            "Ready. The app checks published ZIP directories; it does not download firmware.",
        )

    def _build_ui(self) -> None:
        self.setWindowTitle("Mirror Housekeeping")
        self.resize(1180, 820)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header = QLabel(
            "<h2>Mirror Housekeeping</h2>"
            "<p>Verifies the macOS layout of the published mirror zips and drives the existing "
            "mirror workflows. Repairs rebuild only the affected macOS assets.</p>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Mirror", "Index", "Releases", "macOS", "Latest run"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_details)
        layout.addWidget(self.table, 1)

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setMinimumHeight(165)
        self.details.setPlaceholderText("Select a mirror to see its macOS assets.")
        layout.addWidget(self.details)

        activity_row = QHBoxLayout()
        self.activity_label = QLabel("Not checked yet.")
        self.activity_label.setWordWrap(True)
        activity_row.addWidget(self.activity_label, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(320)
        self.progress.setVisible(False)
        activity_row.addWidget(self.progress)
        layout.addLayout(activity_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setFixedHeight(120)
        layout.addWidget(self.log)

        self.setCentralWidget(central)

        self.auth_label = QLabel("Auth: not checked")
        self.statusBar().addPermanentWidget(self.auth_label)

        toolbar = QToolBar("Housekeeping")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        self.check_action = QAction("Check", self)
        self.check_action.setShortcut(QKeySequence("Ctrl+R"))
        self.check_action.triggered.connect(lambda: self._start_check(False))
        toolbar.addAction(self.check_action)

        self.deep_action = QAction("Deep Check", self)
        self.deep_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.deep_action.triggered.connect(lambda: self._start_check(True))
        toolbar.addAction(self.deep_action)

        toolbar.addSeparator()
        self.update_action = QAction("Update Stale", self)
        self.update_action.triggered.connect(self._start_update)
        toolbar.addAction(self.update_action)

        self.repair_action = QAction("Repair macOS", self)
        self.repair_action.triggered.connect(self._start_repair)
        toolbar.addAction(self.repair_action)

        toolbar.addSeparator()
        self.open_action = QAction("Open GitHub", self)
        self.open_action.triggered.connect(self._open_github)
        toolbar.addAction(self.open_action)

        self.settings_action = QAction("Settings", self)
        self.settings_action.triggered.connect(self._show_settings)
        toolbar.addAction(self.settings_action)

        self._actions = [
            self.check_action,
            self.deep_action,
            self.update_action,
            self.repair_action,
            self.open_action,
            self.settings_action,
        ]

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.check_action)
        file_menu.addAction(self.deep_action)
        file_menu.addSeparator()
        file_menu.addAction(self.update_action)
        file_menu.addAction(self.repair_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("About Mirror Housekeeping", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _client(self) -> core.GitHubClient:
        token, source = core.resolve_token()
        self.auth_label.setText(f"Auth: {source}")
        return core.GitHubClient(token)

    def _ensure_token_client(self) -> core.GitHubClient | None:
        client = self._client()
        if client.has_token:
            return client
        QMessageBox.information(
            self,
            "GitHub token needed",
            "Dispatching a mirror workflow needs a GitHub token. Use Settings to paste one, "
            "or sign in with `gh auth login`.",
        )
        self._show_settings()
        return None

    def _set_actions_enabled(self, enabled: bool) -> None:
        for action in self._actions:
            action.setEnabled(enabled)
        self.table.setEnabled(enabled)

    def _start_task(
        self,
        label: str,
        work: Callable[[Callable[[int, int, str], None]], object],
        on_done: Callable[[object], None],
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_actions_enabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.activity_label.setText(label)
        self._log(label)
        task = Task(work)
        task.progress.connect(self._on_progress)
        task.done.connect(on_done)
        task.failed.connect(self._on_failed)
        task.finished.connect(self._on_task_finished)
        self._task = task
        task.start()

    def _on_task_finished(self) -> None:
        self._busy = False
        self.progress.setVisible(False)
        self._set_actions_enabled(True)
        if self._task is not None:
            self._task.deleteLater()
            self._task = None

    def _on_progress(self, done: int, total: int, label: str) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.activity_label.setText(label)

    def _on_failed(self, message: str) -> None:
        self._log(f"ERROR: {message}")
        self.activity_label.setText("The task failed. See the log.")
        QMessageBox.warning(self, "Housekeeping", message)

    def _start_check(self, deep: bool) -> None:
        if self._busy:
            return
        client = self._client()
        label = "Deep checking every release..." if deep else "Checking the newest release in each mirror..."

        def work(progress: Callable[[int, int, str], None]) -> list[core.MirrorStatus]:
            return core.check_all(
                client,
                owner=self.config.owner,
                deep=deep,
                max_age_hours=self.config.max_age_hours,
                progress=progress,
            )

        self._start_task(label, work, lambda result: self._on_checked(result, deep))

    def _on_checked(self, result: object, deep: bool) -> None:
        statuses = list(result) if isinstance(result, list) else []
        self.statuses = statuses
        self._populate()
        issues = [
            status
            for status in statuses
            if status.error or status.needs_repair or status.is_stale(self.config.max_age_hours)
        ]
        self._log(
            f"Checked {len(statuses)} mirrors; {len(issues)} need attention."
            + ("" if deep else " Quick check inspected only each mirror's newest release.")
        )
        for status in issues:
            detail = status.error or core.macos_label(status)
            self._log(f"  {status.repo}: {detail}")
        self.activity_label.setText(f"{len(statuses)} mirrors checked - {len(issues)} need attention")
        if statuses:
            first = next(
                (
                    index
                    for index, status in enumerate(statuses)
                    if status.error or status.needs_repair or status.is_stale(self.config.max_age_hours)
                ),
                0,
            )
            self.table.selectRow(first)

    def _populate(self) -> None:
        self.table.setRowCount(len(self.statuses))
        for row, status in enumerate(self.statuses):
            mirror = QTableWidgetItem(status.repo.split("/")[-1])
            mirror.setToolTip(status.repo)
            index_text = core.relative_age(status.generated_at) if status.generated_at else "missing"
            index = QTableWidgetItem(index_text)
            if status.error:
                index.setToolTip(status.error)
            releases = QTableWidgetItem(str(status.releases))
            macos = QTableWidgetItem(core.macos_label(status))
            macos.setToolTip(
                "\n".join(
                    f"{asset.tag} {asset.name}: {asset.status} - {asset.detail}"
                    for asset in status.macos_assets
                )
            )
            run = QTableWidgetItem(core.run_label(status))
            if status.run_url:
                run.setToolTip(status.run_url)
            if status.error:
                _tint(mirror, TINT_RED)
                _tint(index, TINT_RED)
            elif status.is_stale(self.config.max_age_hours):
                _tint(index, TINT_AMBER)
            if status.needs_repair:
                _tint(macos, TINT_RED)
            elif status.macos_ok:
                _tint(macos, TINT_GREEN)
            if status.run_active:
                _tint(run, TINT_BLUE)
            self.table.setItem(row, 0, mirror)
            self.table.setItem(row, 1, index)
            self.table.setItem(row, 2, releases)
            self.table.setItem(row, 3, macos)
            self.table.setItem(row, 4, run)

    def _selected_statuses(self) -> list[core.MirrorStatus]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.statuses[row] for row in rows if 0 <= row < len(self.statuses)]

    def _show_details(self) -> None:
        selected = self._selected_statuses()
        if not selected:
            self.details.clear()
            return
        status = selected[0]
        parts = [f"<h3>{html.escape(status.repo)}</h3>"]
        if status.error:
            parts.append(f"<p><b>Error:</b> {html.escape(status.error)}</p>")
        parts.append(
            "<p>"
            f"Index: {html.escape(core.relative_age(status.generated_at) if status.generated_at else 'missing')}"
            f" &middot; Releases: {status.releases}"
            f" &middot; macOS: {html.escape(core.macos_label(status))}"
            "</p>"
        )
        if status.run:
            url = str(status.run.get("html_url") or "")
            label = html.escape(core.run_label(status))
            parts.append(
                f"<p>Latest run: <a href='{html.escape(url)}'>{label}</a></p>"
                if url
                else f"<p>Latest run: {label}</p>"
            )
        elif status.run_error:
            parts.append(f"<p>Workflow: {html.escape(status.run_error)}</p>")
        if status.macos_assets:
            parts.append("<h4>macOS assets checked</h4>")
            parts.append("<table cellspacing='4' cellpadding='2'>")
            for asset in status.macos_assets:
                color = "#1e8449" if asset.ok else "#b03a2e"
                parts.append(
                    "<tr>"
                    f"<td>{html.escape(asset.tag)}</td>"
                    f"<td><a href='{html.escape(asset.url)}'>{html.escape(asset.name)}</a></td>"
                    f"<td style='color:{color}'>{html.escape(asset.status)}</td>"
                    f"<td>{html.escape(asset.detail)}</td>"
                    "</tr>"
                )
            parts.append("</table>")
        else:
            parts.append("<p>No macOS assets were found in this index.</p>")
        if status.macos_unknown:
            parts.append(
                f"<p><i>{status.macos_unknown} older asset(s) were not checked. "
                "Run Deep Check before repairing every release.</i></p>"
            )
        self.details.setHtml("".join(parts))

    def _start_update(self) -> None:
        if self._busy:
            return
        selected = self._selected_statuses()
        targets = selected or [
            status
            for status in self.statuses
            if status.error or status.is_stale(self.config.max_age_hours)
        ]
        if not targets:
            QMessageBox.information(
                self,
                "Nothing stale",
                "No stale or missing mirror indexes were found in the current check.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Dispatch mirror sync",
            f"Dispatch the existing mirror workflow for {len(targets)} mirror(s)?\n\n"
            "This runs on GitHub Actions; nothing is rebuilt locally.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        client = self._ensure_token_client()
        if client is None:
            return

        def work(progress: Callable[[int, int, str], None]) -> list[core.Action]:
            actions: list[core.Action] = []
            for done, status in enumerate(targets, start=1):
                progress(done, len(targets), status.repo)
                try:
                    core.update_mirror(client, status.repo)
                except Exception as exc:  # noqa: BLE001
                    actions.append(core.Action(status.repo, "update", False, str(exc)))
                else:
                    actions.append(core.Action(status.repo, "update", True, "dispatched"))
            return actions

        self._start_task("Dispatching mirror syncs...", work, self._on_dispatch_actions)

    def _start_repair(self) -> None:
        if self._busy:
            return
        selected = self._selected_statuses()
        if selected:
            targets = [status for status in selected if status.needs_repair]
            if not targets:
                QMessageBox.information(
                    self,
                    "Nothing to repair",
                    "The selected mirror(s) have no broken macOS assets in the current check.",
                )
                return
        else:
            targets = [status for status in self.statuses if status.needs_repair]
            if not targets:
                QMessageBox.information(
                    self,
                    "Nothing to repair",
                    "No broken macOS layouts were found. Run Deep Check to inspect every release.",
                )
                return

        unchecked = sum(status.macos_unknown for status in targets)
        warning = (
            f"\n\n{unchecked} older asset(s) have not been inspected; run Deep Check for a complete repair."
            if unchecked
            else ""
        )
        answer = QMessageBox.question(
            self,
            "Repair macOS layouts",
            f"Rebuild the affected macOS ZIPs for {len(targets)} mirror(s)?\n\n"
            "The mirror workflow re-publishes macOS assets only; Windows and Linux assets are left alone."
            + warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        client = self._ensure_token_client()
        if client is None:
            return

        def work(progress: Callable[[int, int, str], None]) -> list[core.Action]:
            actions: list[core.Action] = []
            for done, status in enumerate(targets, start=1):
                progress(done, len(targets), status.repo)
                tags = ",".join(
                    sorted(
                        {
                            asset.tag
                            for asset in status.macos_assets
                            if asset.status in {"wrong_layout", "error"}
                        }
                    )
                )
                try:
                    core.repair_macos(client, status.repo, tags=tags)
                except Exception as exc:  # noqa: BLE001
                    actions.append(core.Action(status.repo, "repair", False, str(exc)))
                else:
                    actions.append(core.Action(status.repo, "repair", True, "dispatched"))
            return actions

        self._start_task("Dispatching macOS repairs...", work, self._on_dispatch_actions)

    def _on_dispatch_actions(self, result: object) -> None:
        actions = list(result) if isinstance(result, list) else []
        ok = [action for action in actions if action.ok]
        failed = [action for action in actions if not action.ok]
        for action in actions:
            self._log(f"{'OK' if action.ok else 'FAIL'} {action.kind}: {action.repo} - {action.detail}")
        self.activity_label.setText(f"Dispatched {len(ok)} job(s); {len(failed)} failed")
        if failed:
            QMessageBox.warning(
                self,
                "Some jobs were not dispatched",
                "\n".join(f"{action.repo}: {action.detail}" for action in failed[:6]),
            )

    def _open_github(self) -> None:
        selected = self._selected_statuses()
        if selected:
            url = f"https://github.com/{selected[0].repo}"
        else:
            url = f"https://github.com/{self.config.owner}"
        QDesktopServices.openUrl(QUrl(url))

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config = dialog.config()
        core.save_config(self.config)
        _, source = core.resolve_token()
        self.auth_label.setText(f"Auth: {source}")
        self._log("Settings saved.")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Mirror Housekeeping",
            "Private mirror housekeeping for the y1-community firmware mirrors.\n\n"
            "Checks published macOS ZIP layout over HTTP range requests and dispatches the "
            "existing mirror workflows for syncs and repairs.",
        )

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._busy:
            QMessageBox.information(
                self,
                "Housekeeping",
                "A check or dispatch is still running. Wait for it to finish before closing.",
            )
            event.ignore()
            return
        event.accept()


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName(core.DEFAULT_APP_NAME)
    app.setApplicationDisplayName("Mirror Housekeeping")
    app.setOrganizationName("Innioasis")
    window = MainWindow()
    window.show()
    return app.exec()
