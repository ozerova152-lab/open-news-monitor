from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigurationError, load_config
from .initializer import create_monitor_interactively
from .local_env import load_local_env
from .pipeline import run_monitor
from .source_check import check_rss_source
from .source_probe import probe_source_report
from .status import read_status, render_status


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
    status = subparsers.add_parser("status", help="Show the health report from the last ordinary run.")
    status.add_argument("config", help="Path to a monitor JSON file.")
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

    if args.dry_run and args.initialize:
        parser.error("Choose either --dry-run or --initialize, not both.")
    findings, errors, markdown = run_monitor(config, dry_run=args.dry_run, initialize=args.initialize)
    print(markdown)
    print(f"Run completed: {len(findings)} new matching publication(s).")
    if errors:
        print("Completed with source/output errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
