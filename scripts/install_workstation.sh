#!/usr/bin/env bash
set -euo pipefail

repo_dir="${PAPER_RADAR_REPO:-$HOME/Personal_Paper_Reading}"
config_dir="$HOME/.config/paper-radar"
user_unit_dir="$HOME/.config/systemd/user"
output_dir="$HOME/.local/share/paper-radar"
cache_dir="$HOME/.cache/paper-radar"

if [[ ! -f "$repo_dir/pyproject.toml" ]]; then
  echo "paper-radar repository not found at $repo_dir" >&2
  exit 1
fi

if python3 -m venv "$repo_dir/.venv" 2>/dev/null; then
  "$repo_dir/.venv/bin/python" -m pip install --upgrade pip
  "$repo_dir/.venv/bin/python" -m pip install -e "$repo_dir"
  "$repo_dir/.venv/bin/paper-radar" validate-config --config "$repo_dir/config/topics.yaml"
else
  echo "python3-venv is unavailable; checking the system Python fallback"
  python3 -c "import yaml" || {
    echo "PyYAML is missing; install python3-venv or python3-yaml" >&2
    exit 1
  }
  PYTHONPATH="$repo_dir/src" python3 -m paper_radar validate-config --config "$repo_dir/config/topics.yaml"
fi

install -d -m 700 "$config_dir"
install -d -m 755 "$user_unit_dir" "$output_dir" "$cache_dir"
install -d -m 755 "$output_dir/library/papers" "$output_dir/readings"
if [[ ! -e "$config_dir/env" ]]; then
  install -m 600 /dev/null "$config_dir/env"
else
  chmod 600 "$config_dir/env"
fi
install -m 644 "$repo_dir/deploy/systemd/paper-radar.service" "$user_unit_dir/paper-radar.service"
install -m 644 "$repo_dir/deploy/systemd/paper-radar.timer" "$user_unit_dir/paper-radar.timer"
install -m 644 "$repo_dir/deploy/systemd/paper-radar-deep-read.service" "$user_unit_dir/paper-radar-deep-read.service"
install -m 644 "$repo_dir/deploy/systemd/paper-radar-deep-read.timer" "$user_unit_dir/paper-radar-deep-read.timer"
install -d -m 755 "$HOME/.hermes/skills/research/paper-reading"
install -m 644 "$repo_dir/deploy/hermes/skills/paper-reading/SKILL.md" "$HOME/.hermes/skills/research/paper-reading/SKILL.md"

systemctl --user daemon-reload
systemctl --user enable --now paper-radar.timer paper-radar-deep-read.timer

echo "Installed paper-radar in $repo_dir"
echo "Secret environment file: $config_dir/env"
echo "Output directory: $output_dir"
if ! command -v codex >/dev/null 2>&1; then
  echo "WARNING: codex CLI is not on PATH; daily deep reading will fail until it is installed" >&2
fi
systemctl --user list-timers paper-radar.timer paper-radar-deep-read.timer --no-pager
