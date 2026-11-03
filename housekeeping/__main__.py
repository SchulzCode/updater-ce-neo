"""Command-line entry point for the private mirror housekeeping tool."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Sequence

from housekeeping import __version__, core


def _add_check_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner", default=None, help="GitHub owner or organisation (default: config)")
    parser.add_argument("--repo", action="append", default=[], help="mirror repo; repeat for several")
    parser.add_argument("--deep", action="store_true", help="inspect every release, not just the newest")
    parser.add_argument("--max-age", type=float, default=None, help="stale-after hours")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _client(require_token: bool = False) -> core.GitHubClient:
    token, _ = core.resolve_token()
    if require_token and not token:
        raise core.HousekeepingError(
            "A GitHub token is required. Run `python -m housekeeping token set`, "
            "or sign in with `gh auth login`."
        )
    return core.GitHubClient(token)


def _owner(args: argparse.Namespace) -> str:
    return args.owner or core.load_config().owner


def _max_age(args: argparse.Namespace) -> float:
    return float(args.max_age) if args.max_age is not None else core.load_config().max_age_hours


def _statuses_json(statuses: Sequence[core.MirrorStatus]) -> str:
    return json.dumps([dataclasses.asdict(status) for status in statuses], indent=2)


def _report_json(report: core.CheckReport) -> str:
    return json.dumps(
        {
            "statuses": [dataclasses.asdict(status) for status in report.statuses],
            "actions": [dataclasses.asdict(action) for action in report.actions],
        },
        indent=2,
    )


def _print_actions(actions: Sequence[core.Action]) -> None:
    for action in actions:
        print(f"{'OK' if action.ok else 'FAIL'} {action.kind} {action.repo}: {action.detail}")


def _status_exit(statuses: Sequence[core.MirrorStatus], max_age_hours: float) -> int:
    return 1 if core.has_issues(statuses, max_age_hours) else 0


def cmd_status(args: argparse.Namespace) -> int:
    client = _client()
    max_age = _max_age(args)
    statuses = core.check_all(
        client,
        owner=_owner(args),
        repos=args.repo or None,
        deep=args.deep,
        max_age_hours=max_age,
    )
    if args.json:
        print(_statuses_json(statuses))
    else:
        print(core.format_table(statuses))
    return _status_exit(statuses, max_age)


def cmd_check(args: argparse.Namespace) -> int:
    deep = bool(args.deep or args.repair)
    if args.repair and not args.deep:
        print("Deep check enabled because --repair needs every release.", file=sys.stderr)
    client = _client(require_token=bool(args.update or args.repair))
    max_age = _max_age(args)
    report = core.check_and_act(
        client,
        owner=_owner(args),
        repos=args.repo or None,
        deep=deep,
        auto_update=args.update,
        auto_repair=args.repair,
        max_age_hours=max_age,
    )
    if args.json:
        print(_report_json(report))
    else:
        print(core.format_table(report.statuses))
        if report.actions:
            print()
            _print_actions(report.actions)
    issues = [status for status in report.statuses if status.error or status.needs_repair or status.is_stale(max_age)]
    if args.notify and issues:
        core.notify("Mirror Housekeeping", f"{len(issues)} mirror(s) need attention")
    return 1 if issues or any(not action.ok for action in report.actions) else 0


def cmd_repair(args: argparse.Namespace) -> int:
    client = _client(require_token=True)
    statuses = core.check_all(
        client,
        owner=_owner(args),
        repos=args.repo or None,
        deep=True,
        max_age_hours=_max_age(args),
    )
    targets = [status for status in statuses if status.needs_repair]
    if not targets:
        print("No broken macOS layouts found.")
        return 0
    actions: list[core.Action] = []
    for status in targets:
        tags = args.tags or ",".join(
            sorted(
                {
                    asset.tag
                    for asset in status.macos_assets
                    if asset.status in {"wrong_layout", "error"}
                }
            )
        )
        if args.dry_run:
            print(f"WOULD repair {status.repo} tags={tags or '(all)'}")
            actions.append(core.Action(status.repo, "repair", True, "dry run"))
            continue
        try:
            core.repair_macos(client, status.repo, tags=tags)
        except Exception as exc:  # noqa: BLE001
            actions.append(core.Action(status.repo, "repair", False, str(exc)))
        else:
            actions.append(core.Action(status.repo, "repair", True, "dispatched"))
    if args.json:
        print(json.dumps([dataclasses.asdict(action) for action in actions], indent=2))
    else:
        _print_actions(actions)
    return 0 if all(action.ok for action in actions) else 1


def cmd_update(args: argparse.Namespace) -> int:
    client = _client(require_token=True)
    targets = list(args.repo or [])
    if not targets:
        statuses = core.check_all(
            client,
            owner=_owner(args),
            deep=False,
            max_age_hours=_max_age(args),
        )
        if args.stale_only:
            targets = [status.repo for status in statuses if status.error or status.is_stale(_max_age(args))]
        else:
            targets = [status.repo for status in statuses]
    if not targets:
        print("No mirror repositories to update.")
        return 0
    actions: list[core.Action] = []
    for repo in targets:
        if args.dry_run:
            print(f"WOULD dispatch {repo}")
            actions.append(core.Action(repo, "update", True, "dry run"))
            continue
        try:
            core.dispatch_workflow(
                client,
                repo,
                platforms=args.platforms,
                tags=args.tags,
                force=args.force,
            )
        except Exception as exc:  # noqa: BLE001
            actions.append(core.Action(repo, "update", False, str(exc)))
        else:
            actions.append(core.Action(repo, "update", True, "dispatched"))
    if args.json:
        print(json.dumps([dataclasses.asdict(action) for action in actions], indent=2))
    else:
        _print_actions(actions)
    return 0 if all(action.ok for action in actions) else 1


def cmd_schedule(args: argparse.Namespace) -> int:
    if args.action == "status":
        print(f"Installed: {core.schedule_installed()} ({core.launch_agent_path()})")
        print(f"Log: {core.schedule_log_path()}")
        return 0
    if args.action == "remove":
        existed = core.remove_schedule()
        print("Removed." if existed else "No schedule was installed.")
        return 0
    config = core.load_config()
    config.interval_hours = float(args.interval)
    config.auto_update = not args.check_only
    config.auto_repair = bool(args.auto_repair)
    config.deep_check = bool(args.deep or args.auto_repair)
    core.save_config(config)
    program = core.schedule_command(
        auto_update=config.auto_update,
        auto_repair=config.auto_repair,
        deep=config.deep_check,
    )
    path = core.install_schedule(
        program,
        interval_hours=config.interval_hours,
        working_directory=core.schedule_working_directory(),
        log_path=core.schedule_log_path(),
    )
    print(f"Installed {path}")
    print(f"Runs every {config.interval_hours:g} hours; log: {core.schedule_log_path()}")
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    if args.action == "set":
        token = core.read_token_interactive()
        destination = core.save_token(token)
        print(f"Token saved in {destination}.")
        return 0
    if args.action == "clear":
        core.clear_token()
        print("Saved token cleared.")
        return 0
    token, source = core.resolve_token()
    print(f"Token source: {source}")
    if not token:
        print("Set one with `python -m housekeeping token set` or sign in with `gh auth login`.")
    return 0 if token else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="housekeeping",
        description="Private housekeeping for the y1-community firmware mirrors.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("app", help="launch the macOS GUI")

    status_parser = sub.add_parser("status", help="check mirror indexes and macOS layouts")
    _add_check_arguments(status_parser)

    check_parser = sub.add_parser("check", help="check and optionally dispatch updates/repairs")
    _add_check_arguments(check_parser)
    check_parser.add_argument("--update", action="store_true", help="dispatch a sync for stale/missing mirrors")
    check_parser.add_argument("--repair", action="store_true", help="deep-check and repair broken macOS layouts")
    check_parser.add_argument("--notify", action="store_true", help="post a macOS notification when issues remain")

    repair_parser = sub.add_parser("repair", help="deep-check and rebuild broken macOS layouts")
    repair_parser.add_argument("--owner", default=None)
    repair_parser.add_argument("--repo", action="append", default=[])
    repair_parser.add_argument("--tags", default="", help="override the comma-separated tag list")
    repair_parser.add_argument("--max-age", type=float, default=None)
    repair_parser.add_argument("--dry-run", action="store_true")
    repair_parser.add_argument("--json", action="store_true")

    update_parser = sub.add_parser("update", help="dispatch the mirror workflow")
    update_parser.add_argument("--owner", default=None)
    update_parser.add_argument("--repo", action="append", default=[])
    update_parser.add_argument("--platforms", default="", help="comma-separated platform keys, e.g. macos")
    update_parser.add_argument("--tags", default="", help="comma-separated tag globs")
    update_parser.add_argument("--force", action="store_true", help="rebuild releases that are already current")
    update_parser.add_argument("--stale-only", action="store_true", help="only stale or missing indexes")
    update_parser.add_argument("--max-age", type=float, default=None)
    update_parser.add_argument("--dry-run", action="store_true")
    update_parser.add_argument("--json", action="store_true")

    schedule_parser = sub.add_parser("schedule", help="install or remove the launchd check")
    schedule_sub = schedule_parser.add_subparsers(dest="action", required=True)
    schedule_sub.add_parser("status")
    schedule_sub.add_parser("remove")
    install_parser = schedule_sub.add_parser("install")
    install_parser.add_argument("--interval", type=float, default=core.DEFAULT_INTERVAL_HOURS)
    install_parser.add_argument("--check-only", action="store_true", help="do not dispatch updates from the schedule")
    install_parser.add_argument("--auto-repair", action="store_true", help="also repair broken macOS layouts")
    install_parser.add_argument("--deep", action="store_true", help="inspect every release in scheduled checks")

    token_parser = sub.add_parser("token", help="manage the saved GitHub token")
    token_sub = token_parser.add_subparsers(dest="action", required=True)
    token_sub.add_parser("set")
    token_sub.add_parser("clear")
    token_sub.add_parser("status")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "app":
        from housekeeping.app import main as app_main

        return app_main([])
    try:
        if args.command == "status":
            return cmd_status(args)
        if args.command == "check":
            return cmd_check(args)
        if args.command == "repair":
            return cmd_repair(args)
        if args.command == "update":
            return cmd_update(args)
        if args.command == "schedule":
            return cmd_schedule(args)
        if args.command == "token":
            return cmd_token(args)
    except core.HousekeepingError as exc:
        print(f"housekeeping: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("housekeeping: interrupted", file=sys.stderr)
        return 130
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
