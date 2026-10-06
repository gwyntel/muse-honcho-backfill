# muse-honcho-backfill — design notes

## Locked decisions (Gwyneth, 2026-10-06)

- **Public repo**, Mutualist License v1.2 (same license as her other public repos), so other Muse users can run this.
- **Her export data is never committed.** Loader reads from a gitignored `data/` dir; tests use a hand-written anonymized fixture.
- One Honcho session per exported conversation; weekly re-runs append only new messages (state file, zero duplicates).
- Her workspace: `hermes`; peers `gwyneth` + `muse`.

## Export format (verified against the real 2026-10-06 export, 78MB)

- Zip: `EYI Package_<MM-DD-YYYY>_<id>.zip`
  - `Conversation with Muse AI_<date>_<id>.txt` — **the main chat only; side chats are NOT exported.** One `.txt` per conversation; the parser already handles N files → N sessions for whenever that changes.
  - `manifest.json` (`exported_at`, `workspace_files` list)
  - `workspace/…` — attachments/artifacts/uploads (ignored by the loader)
- Transcript lines: `[YYYY-MM-DD HH:MM:SS] Muse AI: …` / `[YYYY-MM-DD HH:MM:SS] You: …`; continuation lines have no prefix.
- **Timestamps are UTC** (verified: last message 2026-10-06 20:33:10, export at 20:41:47Z).
- In-chat widgets appear as `[[hatch_widget:widget-<uuid>]]` placeholders → stripped.
- Real data point: 2,129 messages (1,643 assistant / 486 user), 2026-09-22 → 2026-10-06.
- **Empty turns:** 1,060 of the 2,129 messages are empty in the export itself (all assistant-side — tool/background turns with no chat text). They load as `[no message text in export]` placeholders with `empty_in_export: true` metadata, preserving the true turn sequence. First attempt skipped them and the session came up 1,060 short; fixed by placeholder + session re-create.

## Verified Honcho API facts (live, v3.2.2, 2026-10-06)

- `POST /v3/workspaces/{id}/sessions` takes an **explicit session id** (`^[a-zA-Z0-9_-]+$`, ≤512 chars) + free metadata + scopes.
- `POST …/sessions/{sid}/messages` takes `MessageBatchCreate` (**≤100/call**); each `MessageCreate` accepts optional **`created_at`** → true backdating. CLI doesn't expose it; REST only.
- `POST …/sessions/{sid}/peers` takes `{peer_id: {SessionPeerConfig}}`.
- Message content cap: **25,000 chars** (loader truncates at 24k + `truncated` metadata flag).
- Peer ids are free-form (`gwyneth`, `muse` need no encoding).

## Session layout

- Session id: `muse-<slug(conversation name)>` (stable across weekly runs → appends land in the same session).
- Scopes: `["muse-export"]`.
- Session metadata: `{source: muse-export, conversation, source_file}`.
- Message metadata: `{source: muse-export, raw_speaker, truncated}`.

## Idempotency

`data/state.json`: `{session_id: {conversation, loaded: [msg_hash…]}}` where `msg_hash = sha1(ts|speaker|text)[:16]`. Empty-after-cleaning messages are marked loaded but skipped (no empty rows in Honcho).

## Prior art

`~/workspace/fluxer-honcho-backfill/` — the Discord/Fluxer→Honcho backfill (63k messages, same batch+`created_at` pattern, same verification approach). Its `loader/honcho_client.py` was the template for `muse_export/honcho.py`.
