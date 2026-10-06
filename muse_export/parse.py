"""Parse a Muse "Download your agent data" export into per-conversation message blocks.

Export layout (verified against a real 2026-10-06 export):
    EYI Package_<date>_<id>.zip
    ├── Conversation with Muse AI_<date>_<id>.txt   # one .txt per conversation
    ├── manifest.json
    └── workspace/...                               # attachments/artifacts (ignored)

Transcript line format (timestamps are UTC — verified: last message lands
minutes before the export's exported_at):
    [2026-09-22 06:39:25] Muse AI: hello...
    [2026-09-22 06:40:05] You: hey...

Continuation lines carry no prefix. In-chat UI widgets appear as
[[hatch_widget:widget-<uuid>]] placeholders and are stripped.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MSG_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (Muse AI|You): ?(.*)$"
)
WIDGET_RE = re.compile(r"\[\[hatch_widget:[^\]]+\]\]")

SPEAKER_PEER = {"You": "user", "Muse AI": "assistant"}


@dataclass
class Message:
    ts: datetime            # tz-aware UTC
    speaker: str            # "user" | "assistant"
    raw_speaker: str        # "You" | "Muse AI"
    text: str               # cleaned content

    @property
    def msg_id(self) -> str:
        h = hashlib.sha1()
        h.update(self.ts.isoformat().encode())
        h.update(self.speaker.encode())
        h.update(self.text.encode())
        return h.hexdigest()[:16]


@dataclass
class Conversation:
    name: str               # e.g. "Conversation with Muse AI"
    source_file: str
    messages: list = field(default_factory=list)


def clean_text(text: str) -> str:
    """Strip widget placeholders; collapse trailing whitespace."""
    text = WIDGET_RE.sub("", text)
    # drop lines that became empty, keep the rest intact
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def parse_conversation_text(text: str, name: str, source_file: str) -> Conversation:
    conv = Conversation(name=name, source_file=source_file)
    cur_ts: datetime | None = None
    cur_speaker: str | None = None
    cur_raw: str | None = None
    buf: list[str] = []

    def flush():
        if cur_ts is None:
            return
        cleaned = clean_text("\n".join(buf))
        conv.messages.append(Message(ts=cur_ts, speaker=cur_speaker,
                                     raw_speaker=cur_raw, text=cleaned))
        buf.clear()

    for line in text.split("\n"):
        m = MSG_RE.match(line)
        if m:
            flush()
            cur_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            cur_raw = m.group(2)
            cur_speaker = SPEAKER_PEER[cur_raw]
            buf = [m.group(3)]
        elif cur_ts is not None:
            buf.append(line)
        # lines before the first message (the title header) are ignored
    flush()
    return conv


def conversation_name_from_filename(filename: str) -> str:
    """'Conversation with Muse AI_10-06-2026_1791319307.txt' -> 'Conversation with Muse AI'."""
    stem = Path(filename).stem
    return re.sub(r"_\d{2}-\d{2}-\d{4}_\d+$", "", stem)


def parse_export_zip(zip_path: str | Path) -> list[Conversation]:
    """Parse every conversation .txt in the export zip. Raw exports are never mutated."""
    convs: list[Conversation] = []
    with zipfile.ZipFile(zip_path) as z:
        txt_files = sorted(n for n in z.namelist()
                           if n.endswith(".txt") and "/" not in n)
        for name in txt_files:
            text = z.read(name).decode("utf-8", errors="replace")
            convs.append(parse_conversation_text(
                text,
                name=conversation_name_from_filename(name),
                source_file=name,
            ))
    return convs
