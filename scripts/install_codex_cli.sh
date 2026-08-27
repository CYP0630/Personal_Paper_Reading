#!/usr/bin/env bash
set -euo pipefail

codex_version="${CODEX_VERSION:-0.150.1}"
machine_arch="$(uname -m)"
case "$machine_arch" in
  x86_64) codex_target="x86_64-unknown-linux-musl" ;;
  aarch64|arm64) codex_target="aarch64-unknown-linux-musl" ;;
  *)
    echo "Unsupported Linux architecture: $machine_arch" >&2
    exit 1
    ;;
esac

codex_asset="codex-package-${codex_target}.tar.gz"
codex_release="https://github.com/openai/codex/releases/download/rust-v${codex_version}"
codex_tmp="$(mktemp -d)"
trap 'rm -rf "$codex_tmp"' EXIT

cd "$codex_tmp"
curl -fsSL --retry 3 -o "$codex_asset" "$codex_release/$codex_asset"
curl -fsSL --retry 3 -o SHA256SUMS "$codex_release/codex-package_SHA256SUMS"
codex_checksum="$(awk -v asset="$codex_asset" '$2 == asset {print $1}' SHA256SUMS)"
if [[ -z "$codex_checksum" ]]; then
  echo "Official checksum does not list $codex_asset" >&2
  exit 1
fi
printf '%s  %s\n' "$codex_checksum" "$codex_asset" | sha256sum -c -
tar -xzf "$codex_asset"

install -d -m 755 "$HOME/.local/bin"
install -m 755 ./bin/codex "$HOME/.local/bin/codex"
"$HOME/.local/bin/codex" --version
echo "Codex CLI installed. Authenticate with: codex login --device-auth"
