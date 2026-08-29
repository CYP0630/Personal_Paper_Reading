from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


LANES = ("discovery", "deep_read")


class DeliveryHistory:
    """Durable record of papers successfully published by each delivery lane."""

    def __init__(self, path: Path, payload: dict | None = None) -> None:
        self.path = path.expanduser().resolve()
        self.payload = payload if isinstance(payload, dict) else {}
        self.payload.setdefault("schema_version", 1)
        lanes = self.payload.get("lanes")
        if not isinstance(lanes, dict):
            lanes = {}
            self.payload["lanes"] = lanes
        for lane in LANES:
            if not isinstance(lanes.get(lane), dict):
                lanes[lane] = {}

    @classmethod
    def for_output_root(
        cls,
        output_root: Path,
        *,
        exclude_date: str = "",
    ) -> "DeliveryHistory":
        root = output_root.expanduser().resolve()
        path = root / "delivery" / "history.json"
        existed = path.is_file()
        payload: dict = {}
        if existed:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, json.JSONDecodeError):
                payload = {}
        history = cls(path, payload)
        if not existed:
            history._bootstrap(root, exclude_date=exclude_date)
        return history

    def contains(self, lane: str, canonical_id: str) -> bool:
        identifier = self._identifier(canonical_id)
        return bool(identifier) and identifier in self._lane(lane)

    def record(
        self,
        lane: str,
        canonical_id: str,
        *,
        title: str = "",
        target_date: str = "",
    ) -> bool:
        return self.record_many(
            lane,
            [(canonical_id, title)],
            target_date=target_date,
        )

    def record_many(
        self,
        lane: str,
        papers: Iterable[tuple[str, str]],
        *,
        target_date: str = "",
    ) -> bool:
        records = self._lane(lane)
        now = datetime.now(timezone.utc).isoformat()
        changed = False
        for canonical_id, title in papers:
            identifier = self._identifier(canonical_id)
            if not identifier or identifier in records:
                continue
            records[identifier] = {
                "canonical_id": canonical_id,
                "title": title,
                "target_date": target_date,
                "first_published_at": now,
            }
            changed = True
        if changed:
            self._save()
        return changed

    def _bootstrap(self, root: Path, *, exclude_date: str) -> None:
        """Seed history from earlier scheduled outputs during first deployment."""
        changed = False
        for path in sorted((root / "data" / "inbox").glob("*.json")):
            if path.stem == exclude_date:
                continue
            payload = self._read_json(path)
            papers = payload.get("papers") if isinstance(payload, dict) else None
            if isinstance(papers, list):
                changed |= self._bootstrap_papers("discovery", papers, path.stem)

        for path in sorted((root / "readings").glob("*/manifest.json")):
            target_date = path.parent.name
            if target_date == exclude_date:
                continue
            payload = self._read_json(path)
            papers = payload.get("papers") if isinstance(payload, dict) else None
            if isinstance(papers, list):
                successful = [
                    paper
                    for paper in papers
                    if isinstance(paper, dict)
                    and paper.get("status") in {"complete", "limited", "cached"}
                ]
                changed |= self._bootstrap_papers("deep_read", successful, target_date)
        if changed:
            self.payload["bootstrapped_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def _bootstrap_papers(self, lane: str, papers: list[dict], target_date: str) -> bool:
        records = self._lane(lane)
        changed = False
        for paper in papers:
            if not isinstance(paper, dict):
                continue
            canonical_id = str(paper.get("canonical_id") or "")
            identifier = self._identifier(canonical_id)
            if not identifier or identifier in records:
                continue
            records[identifier] = {
                "canonical_id": canonical_id,
                "title": str(paper.get("title") or ""),
                "target_date": target_date,
                "first_published_at": "",
                "bootstrapped": True,
            }
            changed = True
        return changed

    def _lane(self, lane: str) -> dict:
        if lane not in LANES:
            raise ValueError(f"Unknown delivery-history lane: {lane}")
        return self.payload["lanes"][lane]

    @staticmethod
    def _identifier(canonical_id: str) -> str:
        return canonical_id.strip().lower()

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self) -> None:
        self.payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
