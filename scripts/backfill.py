#!/usr/bin/env python3
"""Weekly Muse export -> Honcho backfill.

Usage:
    python scripts/backfill.py data/incoming/'EYI Package_10-06-2026_1791319307.zip'
    python scripts/backfill.py /path/to/export_dir   # unzipped export also works

The export does not delineate side chats from the main chat (verified: the
transcript concatenates everything chronologically, and the manifest marks
every turn channels=["main"]). So each conversation is split into episodes:
a gap of >= --gap-hours (default 4) between consecutive messages starts a new
Honcho session. A side chat opened mid-flow lands in the surrounding episode —
that's a known limitation of the heuristic, not a bug.

Messages load oldest-first in batches of 100 with original UTC timestamps
(true backdating). Re-running only appends new messages — a state file
(default data/state.json) records every loaded message hash, so weekly
uploads never duplicate.

Config via env: HONCHO_BASE_URL, HONCHO_API_KEY (optional),
HONCHO_HTTP_PROXY / HONCHO_HTTPS_PROXY (optional).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muse_export.honcho import Honcho, truncate
from muse_export.parse import chunk_by_gap, parse_export_zip

SESSION_SCOPES = ["muse-export"]
EMPTY_PLACEHOLDER = "[no message text in export]"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return f"muse-{slug}" or "muse-chat"


def chunk_session_id(conv_slug: str, chunk: list) -> str:
    return f"{conv_slug}-{chunk[0].ts.strftime('%Y%m%d-%H%M')}"


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def load_chunk(client: Honcho, session_id: str, chunk: list, conv,
               user_peer: str, agent_peer: str, seen: set[str]) -> list[str]:
    new_msgs = [m for m in chunk if m.msg_id not in seen]
    if not new_msgs:
        return []
    client.ensure_session(
        session_id,
        metadata={"source": "muse-export",
                  "conversation": conv.name,
                  "source_file": conv.source_file,
                  "chunk_start": chunk[0].ts.isoformat(),
                  "chunk_end": chunk[-1].ts.isoformat()},
        scopes=SESSION_SCOPES,
    )
    client.add_peers(session_id, [user_peer, agent_peer])
    batch, loaded = [], []
    for m in new_msgs:
        text = m.text.strip()
        was_empty = not text
        # Empty turns (tool/background activity with no chat text) still
        # load as placeholders so the true turn sequence is preserved.
        content, was_truncated = truncate(text or EMPTY_PLACEHOLDER)
        batch.append({
            "peer_id": user_peer if m.speaker == "user" else agent_peer,
            "content": content,
            "created_at": m.ts.isoformat(),
            "metadata": {
                "source": "muse-export",
                "raw_speaker": m.raw_speaker,
                "truncated": was_truncated,
                "empty_in_export": was_empty,
            },
        })
        loaded.append(m.msg_id)
        if len(batch) == 100:
            client.add_messages(session_id, batch)
            batch = []
    if batch:
        client.add_messages(session_id, batch)
    return loaded


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill a Muse export into Honcho.")
    ap.add_argument("export_path", help="Path to the EYI Package .zip (or unzipped dir)")
    ap.add_argument("--workspace", default="muse",
                    help="Honcho workspace id (default: muse)")
    ap.add_argument("--user-peer", default="gwyneth",
                    help="Honcho peer id for the human side (default: gwyneth)")
    ap.add_argument("--agent-peer", default="muse",
                    help="Honcho peer id for the assistant side (default: muse)")
    ap.add_argument("--gap-hours", type=float, default=4.0,
                    help="Gap that starts a new session (default: 4.0)")
    ap.add_argument("--state", default="data/state.json",
                    help="Idempotency state file (default: data/state.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse and chunk only; print what would load.")
    args = ap.parse_args()

    convs = parse_export_zip(args.export_path)
    total_msgs = sum(len(c.messages) for c in convs)
    print(f"parsed {len(convs)} conversation(s), {total_msgs} messages")

    if args.dry_run:
        for c in convs:
            conv_slug = slugify(c.name)
            for chunk in chunk_by_gap(c.messages, args.gap_hours):
                print(f"  {chunk_session_id(conv_slug, chunk)}: {len(chunk)} msgs, "
                      f"{chunk[0].ts.isoformat()} -> {chunk[-1].ts.isoformat()}")
        return 0

    state = load_state(Path(args.state))
    client = Honcho(workspace=args.workspace)
    client.ensure_peer(args.user_peer, {"source": "muse-export"})
    client.ensure_peer(args.agent_peer, {"source": "muse-export"})

    for conv in convs:
        conv_slug = slugify(conv.name)
        for chunk in chunk_by_gap(conv.messages, args.gap_hours):
            session_id = chunk_session_id(conv_slug, chunk)
            seen = set(state.get(session_id, {}).get("loaded", []))
            loaded = load_chunk(client, session_id, chunk, conv,
                                args.user_peer, args.agent_peer, seen)
            if not loaded and seen:
                print(f"{session_id}: up to date ({len(seen)} loaded)")
                continue
            state[session_id] = {
                "conversation": conv.name,
                "chunk_start": chunk[0].ts.isoformat(),
                "loaded": sorted(seen | set(loaded)),
            }
            save_state(Path(args.state), state)
            print(f"{session_id}: +{len(loaded)} messages")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
