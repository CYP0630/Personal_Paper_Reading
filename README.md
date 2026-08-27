# Personal Paper Reading

Configuration and automation for a personal AI-paper discovery, reading, and
management workflow.

## Research profile

[`config/topics.yaml`](config/topics.yaml) is the source of truth for:

- core method and application-domain interests;
- search terms, exclusions, arXiv categories, and preferred venues;
- daily and weekly discovery quotas;
- multi-topic intersection boosts;
- evaluation, safety, data, and systems facets;
- frontier-topic detection and feedback-driven seed updates.

The profile is intentionally multi-label. A paper can be tagged as, for
example, agentic, multimodal, post-training, and medical without being copied
into separate topic databases.

## Adding seed papers

A canonical link is normally enough. Prefer an arXiv abstract URL, DOI landing
page, OpenReview forum URL, or publisher article page; the workflow can use it
to resolve metadata and the PDF. Attach the PDF only when the paper is behind a
login or paywall, the link is unstable, or analysis must include page-specific
figures, equations, tables, or supplementary material.

Positive seeds in `config/topics.yaml` record a canonical ID, title, resource
type, tier (`core` or `recent`), origin, URL, tags, and a short rationale. This
keeps long-term topic anchors separate from fast-moving frontier examples and
makes later recommendations auditable.

## Discovery MVP

The first runnable version fetches from arXiv, OpenReview, PubMed, the Nature
journal watch, and Hugging Face Daily Papers. It normalizes records, merges
duplicates, assigns multiple research topics, scores relevance/heat/confidence,
and writes a bounded daily digest. GitHub, OpenAlex, Semantic Scholar, and PMC
are explicitly disabled until their v2 adapters are added.

### Setup

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
paper-radar validate-config
```

For public metadata, Hugging Face authentication is optional. If authentication
is needed, create a dedicated fine-grained/read-only token and inject it on the
runtime machine; do not paste it into source code or commit it.

```bash
export HF_TOKEN="..."
export PAPER_RADAR_EMAIL="you@example.com"
```

See `.env.example` for all optional environment variables.

### Run

```bash
# All enabled sources, four-day lookback, Top 8
paper-radar discover

# A fast source-specific smoke test
paper-radar discover --source hf --digest-size 3 --no-write

# Rebuild from cached responses without network access
paper-radar discover --offline
```

Each normal run writes:

- `data/inbox/YYYY-MM-DD.json` for automation and Discord workflows;
- `digests/YYYY-MM-DD.md` for human review and Obsidian.

A failed source does not abort other sources. The CLI and Markdown digest both
report per-source counts and errors so partial results remain auditable.

## Workstation deployment

The production layout keeps generated data and caches outside the Git working
tree:

- repository and virtual environment: `~/Personal_Paper_Reading`;
- secret environment: `~/.config/paper-radar/env` (mode `0600`);
- JSON and Markdown output: `~/.local/share/paper-radar`;
- HTTP cache: `~/.cache/paper-radar`.

On a Linux workstation with systemd user services:

```bash
git clone git@github.com:CYP0630/Personal_Paper_Reading.git ~/Personal_Paper_Reading
bash ~/Personal_Paper_Reading/scripts/install_workstation.sh
```

An `export` in an interactive shell is not inherited by the timer. From the
same shell in which the new token is already exported, persist it without
placing the value in shell history:

```bash
install -d -m 700 "$HOME/.config/paper-radar"
(umask 077; printf 'HF_TOKEN=%s\n' "$HF_TOKEN" > "$HOME/.config/paper-radar/env")
systemctl --user start paper-radar.service
```

The timer runs daily at 07:30 in `America/New_York`, catches up after downtime,
and applies a delay of up to ten minutes to avoid synchronized API traffic.

Useful checks:

```bash
systemctl --user status paper-radar.timer
systemctl --user status paper-radar.service
journalctl --user -u paper-radar.service -n 100 --no-pager
```
