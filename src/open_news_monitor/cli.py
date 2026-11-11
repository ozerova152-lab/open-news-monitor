from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigurationError, load_config
from .doctor import render_doctor_report
from .initializer import create_monitor_interactively
from .local_env import load_local_env
from .locking import MonitorAlreadyRunningError
from .pipeline import collect_monitor, deliver_monitor, run_monitor
from .scheduling import ScheduleConfigurationError, render_schedule_tick_report, schedule_tick
from .source_check import check_rss_source
from .source_probe import probe_source_report
from .status import read_status, render_status
from .windows_scheduler import (
    TaskSchedulerError,
    WindowsTaskScheduler,
    build_task_spec,
    confirmation_approved,
    render_task_preview,
    render_task_status,
)


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description="Open News Monitor: source-first RSS monitoring.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="Validate a monitor JSON file.")
    validate.add_argument("config", help="Path to a monitor JSON file.")
    source_check = subparsers.add_parser("check-source", help="Inspect a public RSS or Atom source without adding it.")
    source_check.add_argument("url", help="Public RSS or Atom URL.")
    source_check.add_argument("--name", default="Checked source", help="Human-readable source name for the report.")
    source_probe = subparsers.add_parser("probe-source", help="Safely examine a public URL before adding it to a monitor.")
    source_probe.add_argument("url", help="Public page, RSS/Atom, or JSON URL.")
    source_probe.add_argument("--name", help="Optional human-readable source name.")
    init = subparsers.add_parser("init-monitor", help="Create a safe monitor profile through a short interview.")
    init.add_argument("config", help="Where to save the new JSON configuration.")
    run = subparsers.add_parser("run", help="Run one monitoring cycle.")
    run.add_argument("config", help="Path to a monitor JSON file.")
    run.add_argument("--dry-run", action="store_true", help="Fetch and preview without storing or delivering results.")
    run.add_argument(
        "--initialize",
        action="store_true",
        help="Save the current feed items as a first-run baseline without sending them.",
    )
    collect = subparsers.add_parser("collect", help="Collect and queue publications without delivery.")
    collect.add_argument("config", help="Path to a monitor JSON file.")
    collect.add_argument("--dry-run", action="store_true", help="Fetch and preview without storing or queueing.")
    deliver = subparsers.add_parser("deliver", help="Deliver queued publications without fetching sources.")
    deliver.add_argument("config", help="Path to a monitor JSON file.")
    deliver.add_argument(
        "--channels",
        help="Comma-separated channels: telegram,email,google_sheets. Default: all enabled channels.",
    )
    schedule_tick_parser = subparsers.add_parser("schedule-tick", help="Run only the collection and deliveries due under a profile schedule.")
    schedule_tick_parser.add_argument("config", help="Path to a monitor JSON file.")
    schedule = subparsers.add_parser("schedule", help="Safely manage the Windows task for a profile schedule.")
    schedule_actions = schedule.add_subparsers(dest="schedule_action", required=True)
    for action in ("install", "status", "disable", "enable", "remove"):
        action_parser = schedule_actions.add_parser(action)
        action_parser.add_argument("config", help="Path to a monitor JSON file.")
    status = subparsers.add_parser("status", help="Show the health report from the last ordinary run.")
    status.add_argument("config", help="Path to a monitor JSON file.")
    doctor = subparsers.add_parser("doctor", help="Check local readiness without sending anything.")
    doctor.add_argument("config", nargs="?", help="Path to a monitor JSON file.")
    args = parser.parse_args()

    if args.command == "check-source":
        print(check_rss_source(args.url, args.name))
        return

    if args.command == "probe-source":
        print(probe_source_report(args.url, args.name))
        return

    if args.command == "init-monitor":
        try:
            create_monitor_interactively(args.config)
        except ValueError as error:
            parser.error(str(error))
        return

    if args.command == "doctor" and not args.config:
        print("Профиль не указан. Укажите путь, например: open-news-monitor doctor config/local/my-monitor.json")
        print("Если профиля ещё нет, создайте его через: .\\scripts\\create-monitor.ps1 -Name my-monitor")
        return

    try:
        config = load_config(args.config)
    except ConfigurationError as error:
        parser.error(str(error))

    if args.command == "validate":
        print(f"Configuration is valid: {args.config}")
        return

    if args.command == "status":
        database_path = config["monitor"].get("database_path", "runtime/open-news-monitor.sqlite3")
        status_path = config["monitor"].get("status_path") or str(Path(database_path).with_suffix(".status.json"))
        print(render_status(read_status(status_path)))
        return

    if args.command == "doctor":
        print(render_doctor_report(config))
        return

    if args.command == "schedule-tick":
        try:
            print(render_schedule_tick_report(schedule_tick(config)))
        except (MonitorAlreadyRunningError, ScheduleConfigurationError) as error:
            parser.error(str(error))
        return

    if args.command == "schedule":
        try:
            spec = build_task_spec(config, args.config)
            scheduler = WindowsTaskScheduler()
            if args.schedule_action == "status":
                print(render_task_status(spec, scheduler.status(spec)))
                return
            print(render_task_preview(args.schedule_action, spec))
            if not confirmation_approved():
                print("Действие отменено: явное подтверждение не получено.")
                return
            if args.schedule_action == "install":
                scheduler.create(spec)
            elif args.schedule_action == "enable":
                scheduler.set_enabled(spec, True)
            elif args.schedule_action == "disable":
                scheduler.set_enabled(spec, False)
            elif args.schedule_action == "remove":
                scheduler.remove(spec)
            print("Действие с задачей Планировщика выполнено.")
        except (TaskSchedulerError, ScheduleConfigurationError) as error:
            parser.error(str(error))
        return

    if args.command == "collect":
        try:
            findings, errors, markdown = collect_monitor(config, dry_run=args.dry_run)
        except MonitorAlreadyRunningError as error:
            parser.error(str(error))
        print(markdown)
        print(f"Collection completed: {len(findings)} new matching publication(s).")
        if errors:
            print("Completed with source errors:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
        return

    if args.command == "deliver":
        channels = None
        if args.channels:
            channels = {item.strip() for item in args.channels.split(",") if item.strip()}
            allowed = {"telegram", "email", "google_sheets"}
            unknown = channels - allowed
            if unknown:
                parser.error("Unknown delivery channel: " + ", ".join(sorted(unknown)))
            if not channels:
                parser.error("Choose at least one delivery channel.")
        try:
            errors, delivery_status = deliver_monitor(config, channels=channels)
        except MonitorAlreadyRunningError as error:
            parser.error(str(error))
        print("Delivery completed.")
        for channel, result in delivery_status.items():
            print(f"- {channel}: {result['status']}; confirmed: {result['sent']}; retry: {result['failed']}")
        if errors:
            print("Completed with delivery errors:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
        return

    if args.dry_run and args.initialize:
        parser.error("Choose either --dry-run or --initialize, not both.")
    try:
        findings, errors, markdown = run_monitor(config, dry_run=args.dry_run, initialize=args.initialize)
    except MonitorAlreadyRunningError as error:
        parser.error(str(error))
    print(markdown)
    print(f"Run completed: {len(findings)} new matching publication(s).")
    if errors:
        print("Completed with source/output errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
