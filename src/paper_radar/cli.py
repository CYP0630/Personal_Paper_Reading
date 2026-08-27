from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_radar.config import ResearchConfig
from paper_radar.delivery import DeliveryError, publish_with_hermes
from paper_radar.pipeline import SOURCE_FACTORIES, discover
from paper_radar.reading import (
    DeepReadError,
    DeepReader,
    local_pdf_canonical_id,
    paper_from_url,
    publish_deep_read_item,
    publish_deep_read_run,
)
from paper_radar.render import render_discord, write_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover", help="Build a daily paper digest")
    discover_parser.add_argument("--config", default="config/topics.yaml")
    discover_parser.add_argument("--date", help="Digest date in YYYY-MM-DD; defaults to today")
    discover_parser.add_argument("--lookback-days", type=int)
    discover_parser.add_argument("--digest-size", type=int)
    discover_parser.add_argument("--candidate-pool-size", type=int)
    discover_parser.add_argument(
        "--source",
        action="append",
        help="Run only this source; repeat for multiple sources (hf/huggingface aliases supported)",
    )
    discover_parser.add_argument("--cache-dir", default=".cache/paper-radar")
    discover_parser.add_argument("--output-root", default=".")
    discover_parser.add_argument("--offline", action="store_true", help="Use cached responses only")
    discover_parser.add_argument("--no-write", action="store_true", help="Run without creating digest files")
    discover_parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the compact digest through the configured Hermes target",
    )
    discover_parser.add_argument(
        "--publish-target",
        help="Override the configured Hermes target, for example discord:1234567890",
    )

    validate_parser = subparsers.add_parser("validate-config", help="Validate topics and seed metadata")
    validate_parser.add_argument("--config", default="config/topics.yaml")

    deep_parser = subparsers.add_parser("deep-read", help="Deep-read a daily Top-N inbox with Codex")
    _add_reading_arguments(deep_parser)
    deep_parser.add_argument("--date", help="Inbox date in YYYY-MM-DD; defaults to today")
    deep_parser.add_argument("--input-root", default=".")
    deep_parser.add_argument("--max-papers", type=int)

    read_parser = subparsers.add_parser("read", help="Deep-read one paper URL or local PDF")
    _add_reading_arguments(read_parser)
    source_group = read_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--url", help="Paper page, arXiv, or direct PDF URL")
    source_group.add_argument("--pdf", help="Local PDF path, including a Hermes attachment path")
    read_parser.add_argument("--title", default="")
    read_parser.add_argument("--canonical-id", default="")
    read_parser.add_argument("--pdf-url", default="")
    read_parser.add_argument("--topic", action="append", default=[])
    return parser


def _add_reading_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config/topics.yaml")
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--workdir", default="reading_workspace")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publish-target")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate-config":
            return validate_config(args.config)
        if args.command == "discover":
            return run_discover(args)
        if args.command == "deep-read":
            return run_deep_read(args)
        return run_read(args)
    except (DeepReadError, DeliveryError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def run_discover(args: argparse.Namespace) -> int:
    if args.publish and args.no_write:
        raise ValueError("--publish cannot be combined with --no-write")
    config = ResearchConfig.load(args.config)
    timezone = ZoneInfo(config.raw.get("profile", {}).get("timezone", "UTC"))
    now = datetime.now(timezone)
    target = date.fromisoformat(args.date) if args.date else now.date()
    # Use a stable daily boundary so cache keys are reproducible throughout the day.
    until = datetime.combine(target, time.max, tzinfo=timezone)
    lookback = args.lookback_days or int(config.daily_settings()["lookback_days"])
    since = until - timedelta(days=lookback)
    result = discover(
        config,
        since=since,
        until=until,
        cache_dir=Path(args.cache_dir).expanduser().resolve(),
        source_ids=set(args.source) if args.source else None,
        candidate_pool_size=args.candidate_pool_size,
        digest_size=args.digest_size,
        offline=args.offline,
    )
    print(
        f"fetched={result.fetched_count} unique={result.unique_count} "
        f"eligible={result.eligible_count} selected={len(result.selected)}"
    )
    for source, count in result.source_counts.items():
        print(f"source {source}: {count}")
    for source, error in result.source_errors.items():
        print(f"warning {source}: {error}", file=sys.stderr)
    if not args.no_write:
        json_path, markdown_path = write_outputs(
            result,
            output_root=Path(args.output_root).expanduser().resolve(),
            target_date=target.isoformat(),
        )
        print(f"json: {json_path}")
        print(f"digest: {markdown_path}")
    if args.publish:
        delivery = publish_with_hermes(
            config,
            render_discord(result, target_date=target.isoformat()),
            target_override=args.publish_target,
        )
        print(f"published: {delivery.provider} -> {delivery.target}")
    return 0 if result.selected or not result.source_errors else 2


def run_deep_read(args: argparse.Namespace) -> int:
    config = ResearchConfig.load(args.config)
    timezone = ZoneInfo(config.raw.get("profile", {}).get("timezone", "UTC"))
    target = date.fromisoformat(args.date) if args.date else datetime.now(timezone).date()
    settings = config.deep_read_settings()
    limit = args.max_papers or int(settings.get("max_papers", config.daily_settings()["digest_size"]))
    if limit < 1:
        raise ValueError("--max-papers must be positive")
    input_path = Path(args.input_root).expanduser().resolve() / "data" / "inbox" / f"{target.isoformat()}.json"
    runner = DeepReader(
        config,
        output_root=Path(args.output_root),
        workdir=Path(args.workdir),
        codex_executable=args.codex,
        force=args.force,
    )
    run = runner.run_daily(input_path=input_path, target_date=target.isoformat(), limit=limit)
    print(f"deep_read requested={len(run.items)} successful={run.successful_count} failed={run.failed_count}")
    for item in run.items:
        print(f"paper {item.rank}: {item.status} {item.paper_key} {item.note_path or item.error}")
    print(f"index: {run.index_path}")
    print(f"manifest: {run.manifest_path}")
    if args.publish:
        deliveries = publish_deep_read_run(config, run, target_override=args.publish_target)
        print(f"published: {len(deliveries)} Hermes messages")
    return 0 if run.items and run.failed_count == 0 else 2


def run_read(args: argparse.Namespace) -> int:
    config = ResearchConfig.load(args.config)
    local_pdf: Path | None = None
    if args.url:
        paper = paper_from_url(
            args.url,
            title=args.title,
            canonical_id=args.canonical_id,
            pdf_url=args.pdf_url,
            topics=args.topic,
        )
    else:
        local_pdf = Path(args.pdf).expanduser().resolve()
        identifier = args.canonical_id or local_pdf_canonical_id(local_pdf)
        paper = {
            "canonical_id": identifier,
            "title": args.title or local_pdf.stem,
            "url": "",
            "pdf_url": None,
            "abstract": "",
            "authors": [],
            "topics": args.topic,
            "source_ids": {},
            "metadata": {"manual_submission": True, "original_filename": local_pdf.name},
        }
    runner = DeepReader(
        config,
        output_root=Path(args.output_root),
        workdir=Path(args.workdir),
        codex_executable=args.codex,
        force=args.force,
    )
    item = runner.read_one(paper, local_pdf=local_pdf)
    print(f"paper: {item.status} {item.paper_key} {item.note_path or item.error}")
    if args.publish:
        delivery = publish_deep_read_item(config, item, target_override=args.publish_target)
        print(f"published: {delivery.provider} -> {delivery.target}")
    return 0 if item.successful else 2


def validate_config(path: str) -> int:
    config = ResearchConfig.load(path)
    identifiers: set[str] = set()
    for topic in config.topics:
        if not topic.get("include_terms"):
            raise ValueError(f"Topic {topic['id']} has no include_terms")
        for seed in topic.get("seeds", {}).get("positive", []):
            missing = {
                "canonical_id", "title", "resource_type", "tier", "origin", "url", "tags", "rationale"
            } - set(seed)
            if missing:
                raise ValueError(f"Seed in {topic['id']} is missing: {sorted(missing)}")
            identifier = str(seed["canonical_id"]).lower()
            if identifier in identifiers:
                raise ValueError(f"Duplicate seed canonical_id: {identifier}")
            identifiers.add(identifier)
    unknown_enabled = config.enabled_sources - set(SOURCE_FACTORIES)
    if unknown_enabled:
        raise ValueError(f"Enabled sources do not have adapters: {sorted(unknown_enabled)}")
    hermes = config.raw.get("delivery", {}).get("hermes", {})
    if hermes.get("enabled", False):
        server_id = str(hermes.get("server_id", ""))
        channel_id = str(hermes.get("channel_id", ""))
        target = str(hermes.get("target", ""))
        if not server_id.isdigit() or not channel_id.isdigit():
            raise ValueError("Hermes Discord server_id and channel_id must be numeric strings")
        if target != f"discord:{channel_id}":
            raise ValueError("Hermes target must match delivery.hermes.channel_id")
    print(
        f"ok: {len(config.topics)} topics, {len(identifiers)} unique seeds, "
        f"sources={','.join(sorted(config.enabled_sources))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
