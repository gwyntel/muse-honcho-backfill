#!/usr/bin/env python3
"""Weekly Muse export -> Honcho backfill.

Usage:
    python scripts/backfill.py data/incoming/'EYI Package_10-06-2026_1791319307.zip'

One Honcho session per exported conversation. Messages are loaded oldest-first
in batches of 100 with their original UTC timestamps (true backdating).
Re-running against the same or a newer export only appends new messages —
a state file (default data/state.json) records every loaded message hash,
so weekly uploads never duplicate.

Config via env: HONCHO_BASE_URL, HONCHO_API_KEY (optional).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muse_export.honcho import Honcho, truncate
from muse_export.parse import parse_export_zip

SESSION_SCOPES = ["muse-export"]


EMPTY_PLACEHOLDER = "[no message text in export]"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return f"muse-{slug}" or "muse-chat"


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill a Muse export into Honcho.")
    ap.add_argument("export_zip", help="Path to the EYI Package .zip")
    ap.add_argument("--workspace", default="muse",
                    help="Honcho workspace id (default: muse)")
    ap.add_argument("--user-peer", default="gwyneth",
                    help="Honcho peer id for the human side (default: gwyneth)")
    ap.add_argument("--agent-peer", default="muse",
                    help="Honcho peer id for the assistant side (default: muse)")
    ap.add_argument("--state", default="data/state.json",
                    help="Idempotency state file (default: data/state.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse only; print what would load.")
    args = ap.parse_args()

    convs = parse_export_zip(args.export_zip)
    total_msgs = sum(len(c.messages) for c in convs)
    print(f"parsed {len(convs)} conversation(s), {total_msgs} messages")

    if args.dry_run:
        for c in convs:
            first = c.messages[0].ts.isoformat() if c.messages else "-"
            last = c.messages[-1].ts.isoformat() if c.messages else "-"
            print(f"  {slugify(c.name)}: {len(c.messages)} msgs, {first} -> {last}")
        return 0

    state = load_state(Path(args.state))
    client = Honcho(workspace=args.workspace)
    client.ensure_peer(args.user_peer, {"source": "muse-export"})
    client.ensure_peer(args.agent_peer, {"source": "muse-export"})

    for conv in convs:
        session_id = slugify(conv.name)
        seen = set(state.get(session_id, {}).get("loaded", []))
        new_msgs = [m for m in conv.messages if m.msg_id not in seen]
        if not new_msgs and seen:
            print(f"{session_id}: up to date ({len(seen)} loaded)")
            continue

        client.ensure_session(
            session_id,
            metadata={"source": "muse-export",
                      "conversation": conv.name,
                      "source_file": conv.source_file},
            scopes=SESSION_SCOPES,
        )
        client.add_peers(session_id, [args.user_peer, args.agent_peer])

        batch, loaded = [], []
        for m in new_msgs:
            text = m.text.strip()
            was_empty = not text
            # Empty turns (tool/background activity with no chat text) still
            # load as placeholders so the true turn sequence is preserved.
            content, was_truncated = truncate(text or EMPTY_PLACEHOLDER)
            batch.append({
                "peer_id": args.user_peer if m.speaker == "user" else args.agent_peer,
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

        state[session_id] = {
            "conversation": conv.name,
            "loaded": sorted(seen | set(loaded)),
        }
        save_state(Path(args.state), state)
        print(f"{session_id}: +{len(loaded)} messages "
              f"({client.count_messages(session_id)} total in session)")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
