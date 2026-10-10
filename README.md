# muse-honcho-backfill

Load your [Muse](https://muse.ai) agent data exports into [Honcho](https://github.com/plastic-labs/honcho) (self-hosted, by Plastic Labs) so your whole chat history becomes long-term memory — backdated to the original timestamps, one Honcho session per conversation.

## Why

Muse is great in the moment, but its memory lives in per-chat context. Honcho is persistent memory that survives nuked chat windows. A weekly export + this loader keeps Honcho's picture of you and your projects complete without you having to log anything by hand.

## How it works

1. **Export** (weekly): Muse app → Settings → Data Controls → "Download your agent data". You get a zip (or folder) containing one `.txt` transcript per conversation plus your workspace files.
2. **Run**: `python scripts/backfill.py path/to/export`
3. The loader parses each transcript into messages, splits it into **episodes** (a gap of 4+ hours between messages starts a new episode — see "Sessions" below), and loads messages oldest-first in batches of 100 — each with its original UTC timestamp (`created_at`), so history lands backdated, not "everything happened today".
4. Re-running is safe: a state file records every loaded message hash, so weekly uploads only append what's new. Zero duplicates.

## Setup

```bash
pip install -r requirements.txt
export HONCHO_BASE_URL="http://localhost:8000"   # your Honcho
# export HONCHO_API_KEY="..."                     # only if your Honcho needs one
python scripts/backfill.py ~/Downloads/'EYI Package_10-06-2026_1791319307.zip' \
  --workspace my-workspace --user-peer me --agent-peer muse
```

If your Honcho is only reachable through a proxy:

```bash
export HONCHO_HTTP_PROXY="http://proxy:3128" HONCHO_HTTPS_PROXY="http://proxy:3128"
```

## What lands in Honcho

- **Sessions**: the export does **not** delineate side chats from the main chat (verified: one transcript, everything chronological, manifest marks every turn `channels=["main"]`). So each conversation is split into **episodes** — a gap of ≥4 hours (tunable via `--gap-hours`) between consecutive messages starts a new session, named `muse-<slug>-<yyyymmdd>-<hhmm>` of the episode's first message. Every session is tagged with the `muse-export` scope; metadata carries conversation title, source file, and episode start/end.
  - Known limitation: a side chat opened mid-flow (no 4h gap) lands in the surrounding episode. The content is all there, in order — the boundary is just heuristic.
- **Peers**: your user peer + agent peer (defaults `gwyneth`/`muse` — override with `--user-peer` / `--agent-peer`), both added to every session.
- **Messages**: `You` → user peer, `Muse AI` → agent peer. Chat-widget placeholders (`[[hatch_widget:…]]`) are stripped; empty turns (tool/background activity with no chat text — about half of all turns) load as `[no message text in export]` placeholders so the true turn order survives; messages over the 25k-char Honcho cap are truncated with a note in metadata.

## Privacy

Your export contains your actual conversations. **It is never committed.** The loader reads from wherever you point it, the state file defaults to `data/` (gitignored), and tests run against a hand-written anonymized fixture in `tests/fixtures/`.

## License

[Mutualist License v1.2](LICENSE) (MutuaL-1.2).
