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

# Generate and publish a compact digest through Hermes/Discord
paper-radar discover --publish

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

## Top-8 deep reading

Discovery and deep reading are separate, restartable phases. The discovery
phase remains fast and auditable; the reading phase downloads each selected
paper and delegates a full Chinese reading note to Codex CLI.

```bash
# Deep-read today's inbox (up to the configured Top 8)
paper-radar deep-read \
  --input-root "$HOME/.local/share/paper-radar" \
  --output-root "$HOME/.local/share/paper-radar" \
  --workdir "$HOME/Personal_Paper_Reading/reading_workspace"

# Read one arXiv/publisher/PDF link and publish the result
paper-radar read --url "https://arxiv.org/abs/2210.03629" \
  --title "ReAct" --output-root "$HOME/.local/share/paper-radar" --publish

# Read a PDF attachment already downloaded by Hermes
paper-radar read --pdf "/absolute/path/to/paper.pdf" \
  --title "Paper title" --output-root "$HOME/.local/share/paper-radar" --publish
```

The managed archive has two layers:

- `library/papers/<canonical-id>/`: one canonical `source.pdf`, `metadata.json`,
  `deep-read.md`, extracted `assets/`, status, and execution log per paper;
- `readings/YYYY-MM-DD/`: a lightweight daily `index.md` and `manifest.json`
  pointing into the canonical library.

Normal reruns reuse a complete canonical note, so the same paper is not
downloaded or analyzed twice. `--force` preserves the previous note as
`deep-read.previous.md` before regeneration. A full note includes the research
question, method and objectives, data/evaluation, verified numerical results,
ablations/error analysis, limitations, and an independent assessment. Useful
source figures are extracted rather than regenerated.

When no full PDF is available (for example, a publisher paywall), the workflow
may create a visibly labeled limited-evidence reading from the accessible page
or abstract. It never presents that result as a full-paper deep read.

### Hermes and Discord delivery

`delivery.hermes` in `config/topics.yaml` records the Discord server/channel
and the `hermes send` target. Publishing is explicit: regular local discovery
does not send a message, while the workstation systemd service includes
`--publish`. Hermes reuses its existing Discord credentials and automatically
chunks the compact Markdown digest to Discord's message limit; Paper Radar does
not copy or read the bot token.

The deep-reading publisher sends a daily index followed by one message per
successful paper. Each message contains the one-sentence conclusion and the
full Markdown note as a Discord attachment; the first extracted key figure is
also attached when available. The installed Hermes `paper-reading` skill uses
the same commands for URLs and Discord PDF attachments, so interactive and
scheduled reading share one archive and quality contract.

Scheduled delivery is deduplicated after Top-8 selection. Successful sends are
recorded by canonical paper ID in
`~/.local/share/paper-radar/delivery/history.json`, with independent
`discovery` and `deep_read` lanes so the morning digest does not suppress that
day's full reading. The complete Top 8 remains in the inbox, digest, daily
index, and manifest; Discord receives only papers that have not previously
been sent in the corresponding lane. A filtered `discord-index.md` accompanies
new deep reads. If every selected paper is already in delivery history, no
empty Discord message is sent. Explicit `paper-radar read --publish` requests
still resend the requested paper and add it to deep-reading history.

On the first run after this feature is installed, history is bootstrapped from
earlier inboxes and successful reading manifests, excluding the current date.
This prevents an upgrade from immediately re-sending the existing archive.

## Workstation deployment

The production layout keeps generated data and caches outside the Git working
tree:

- repository and optional virtual environment: `~/Personal_Paper_Reading`;
- secret environment: `~/.config/paper-radar/env` (mode `0600`);
- JSON and Markdown output: `~/.local/share/paper-radar`;
- HTTP cache: `~/.cache/paper-radar`.

Generated delivery state is also outside Git at
`~/.local/share/paper-radar/delivery/history.json`; it must be preserved with
the runtime data directory when moving the service to another machine.

The workstation also needs Codex CLI. The repository includes a checksum-
verified, user-local Linux installer pinned to a known release:

```bash
bash ~/Personal_Paper_Reading/scripts/install_codex_cli.sh
codex login --device-auth
codex login status
```

`codex exec` is used non-interactively with an ephemeral session. The reading
workspace grants broad filesystem access only because the canonical library is
outside the Git repository; the generated prompt explicitly limits writes to
the current paper directory and treats all paper content as untrusted data.

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
The separate deep-reading timer starts at 08:00 with up to five minutes of
jitter and allows up to eight hours for the Top 8. This separation keeps a
single failed paper from losing the discovery digest.
The installer prefers a virtual environment and falls back to the system Python
when `python3-venv` is unavailable and PyYAML is already installed.

Useful checks:

```bash
systemctl --user status paper-radar.timer
systemctl --user status paper-radar.service
systemctl --user status paper-radar-deep-read.timer
systemctl --user status paper-radar-deep-read.service
journalctl --user -u paper-radar.service -n 100 --no-pager
journalctl --user -u paper-radar-deep-read.service -n 100 --no-pager
```
