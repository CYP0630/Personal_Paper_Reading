from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from paper_radar.config import ResearchConfig


class DeliveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    provider: str
    target: str


def publish_with_hermes(
    config: ResearchConfig,
    message: str,
    *,
    target_override: str | None = None,
    subject_override: str | None = None,
    attachments: Iterable[str | Path] = (),
    timeout_seconds: int = 120,
) -> DeliveryResult:
    settings = config.raw.get("delivery", {}).get("hermes", {})
    if not settings.get("enabled", False):
        raise DeliveryError("Hermes delivery is disabled in config")
    target = (target_override or settings.get("target") or "").strip()
    if not target.startswith("discord:") or not target.split(":", 1)[1].isdigit():
        raise DeliveryError(f"Invalid Hermes Discord target: {target!r}")
    executable_name = str(settings.get("executable") or "hermes")
    executable = shutil.which(executable_name)
    if not executable:
        raise DeliveryError(f"Hermes executable not found on PATH: {executable_name}")
    subject = subject_override or str(settings.get("subject") or "Paper Radar")
    body = message.rstrip()
    for attachment in attachments:
        path = Path(attachment).expanduser().resolve()
        if "\n" in str(path) or "\r" in str(path):
            raise DeliveryError("Attachment path cannot contain a newline")
        if not path.is_file():
            raise DeliveryError(f"Attachment does not exist: {path}")
        body += f"\nMEDIA:{path}"
    command = [
        executable,
        "send",
        "--to",
        target,
        "--subject",
        subject,
        "--file",
        "-",
        "--quiet",
    ]
    try:
        completed = subprocess.run(
            command,
            input=body,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError(f"Hermes delivery could not run: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown Hermes error").strip()[:1000]
        raise DeliveryError(f"Hermes delivery failed with exit {completed.returncode}: {detail}")
    return DeliveryResult(provider="hermes", target=target)
