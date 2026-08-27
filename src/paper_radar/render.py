from __future__ import annotations

import json
from pathlib import Path

from paper_radar.pipeline import DiscoveryResult


def write_outputs(result: DiscoveryResult, *, output_root: Path, target_date: str) -> tuple[Path, Path]:
    json_path = output_root / "data" / "inbox" / f"{target_date}.json"
    markdown_path = output_root / "digests" / f"{target_date}.md"
    _atomic_write(json_path, json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
    _atomic_write(markdown_path, render_markdown(result, target_date=target_date))
    return json_path, markdown_path


def render_markdown(result: DiscoveryResult, *, target_date: str) -> str:
    lines = [
        f"# Paper Radar — {target_date}",
        "",
        f"> 抓取 {result.fetched_count} 条，去重后 {result.unique_count} 条，"
        f"符合阈值 {result.eligible_count} 条，推荐 {len(result.selected)} 条。",
        "",
        "## Source health",
        "",
    ]
    for source, count in result.source_counts.items():
        suffix = f" — ⚠️ {result.source_errors[source]}" if source in result.source_errors else ""
        lines.append(f"- {source}: {count}{suffix}")
    if not result.source_counts:
        lines.append("- No sources ran.")
    lines.extend(["", "## Today’s papers", ""])

    if not result.selected:
        lines.append("没有论文达到当前相关性阈值。可检查来源错误或调整 `minimum_fit`。")
    for index, paper in enumerate(result.selected, start=1):
        title = paper.title.replace("[", "\\[").replace("]", "\\]")
        lines.extend(
            [
                f"### {index}. [{title}]({paper.url})",
                "",
                f"- Topics: {', '.join(paper.topics)}",
                f"- Sources: {', '.join(paper.discovered_by)}",
                f"- Published: {paper.published_at or 'unknown'}",
                f"- Scores: fit `{paper.scores.get('fit', 0):.2f}` · heat `{paper.scores.get('heat', 0):.2f}` "
                f"· confidence `{paper.scores.get('confidence', 0):.2f}` · rank `{paper.scores.get('rank', 0):.2f}`",
                f"- Lane: {paper.metadata.get('selection_lane', 'core_fit')}",
            ]
        )
        if paper.pdf_url:
            lines.append(f"- [PDF]({paper.pdf_url})")
        if paper.reasons:
            lines.append(f"- Why: {'；'.join(paper.reasons)}")
        if paper.abstract:
            abstract = paper.abstract[:600].strip()
            if len(paper.abstract) > 600:
                abstract += "…"
            lines.extend(["", abstract, ""])
        else:
            lines.append("")

    if result.source_errors:
        lines.extend(["", "## Source errors", ""])
        for source, error in result.source_errors.items():
            lines.append(f"- **{source}**: `{error}`")
    lines.append("")
    return "\n".join(lines)


def render_discord(result: DiscoveryResult, *, target_date: str) -> str:
    """Render a compact Discord digest; Hermes handles platform chunking."""
    lines = [
        f"**{target_date} · Top {len(result.selected)}**",
        f"抓取 {result.fetched_count} 条 · 去重 {result.unique_count} 条 · 达标 {result.eligible_count} 条",
        "",
    ]
    for index, paper in enumerate(result.selected, start=1):
        title = paper.title.replace("[", "\\[").replace("]", "\\]")
        topics = " · ".join(paper.topics)
        reason = "；".join(paper.reasons[:2]) if paper.reasons else "主题与种子相关"
        lines.extend(
            [
                f"**{index}. [{title}]({paper.url})**",
                f"`{topics}` · fit `{paper.scores.get('fit', 0):.2f}` · heat `{paper.scores.get('heat', 0):.2f}`",
                f"{reason}",
                "",
            ]
        )
    if result.source_errors:
        failed = ", ".join(sorted(result.source_errors))
        lines.append(f"⚠️ 部分来源失败：{failed}")
    return "\n".join(lines).strip() + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
