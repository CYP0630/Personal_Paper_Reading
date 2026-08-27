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

python3 -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/python" -m pip install --upgrade pip
"$repo_dir/.venv/bin/python" -m pip install -e "$repo_dir"
"$repo_dir/.venv/bin/paper-radar" validate-config --config "$repo_dir/config/topics.yaml"

install -d -m 700 "$config_dir"
install -d -m 755 "$user_unit_dir" "$output_dir" "$cache_dir"
if [[ ! -e "$config_dir/env" ]]; then
  install -m 600 /dev/null "$config_dir/env"
else
  chmod 600 "$config_dir/env"
fi
install -m 644 "$repo_dir/deploy/systemd/paper-radar.service" "$user_unit_dir/paper-radar.service"
install -m 644 "$repo_dir/deploy/systemd/paper-radar.timer" "$user_unit_dir/paper-radar.timer"

systemctl --user daemon-reload
systemctl --user enable --now paper-radar.timer

echo "Installed paper-radar in $repo_dir"
echo "Secret environment file: $config_dir/env"
echo "Output directory: $output_dir"
systemctl --user list-timers paper-radar.timer --no-pager

