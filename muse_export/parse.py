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

# Side-chat heart markers (Gwyneth's rule, 2026-10-10): every assistant reply
# in a side chat ends with "[<heart>] [side]" on its own final line.
# No pink hearts, per her. Maps heart -> color name for session ids.
HEARTS = {
    "❤️": "red", "🧡": "orange", "💛": "yellow",
    "💚": "green", "💙": "blue", "💜": "purple",
    "🖤": "black", "🤍": "white", "🩶": "grey",
}
VS16 = "\uFE0F"
MARKER_RE = re.compile(
    r"\[(" + "[" + "".join(re.escape(h.replace(VS16, "")) for h in HEARTS)
    + r"])" + VS16 + r"?\] \[side\]\s*$"
)
# normalize away U+FE0F variation selectors for lookup
HEART_LOOKUP = {h.replace(VS16, ""): name for h, name in HEARTS.items()}


@dataclass
class Message:
    ts: datetime            # tz-aware UTC
    speaker: str            # "user" | "assistant"
    raw_speaker: str        # "You" | "Muse AI"
    text: str               # cleaned content (marker stripped)
    heart: str | None = None  # side-chat heart color name, assistant msgs only

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
        heart = None
        if cur_speaker == "assistant":
            mkr = MARKER_RE.search(cleaned)
            if mkr:
                heart = HEART_LOOKUP.get(mkr.group(1).replace(VS16, ""))
                cleaned = MARKER_RE.sub("", cleaned).rstrip()
        conv.messages.append(Message(ts=cur_ts, speaker=cur_speaker,
                                     raw_speaker=cur_raw, text=cleaned,
                                     heart=heart))
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


def chunk_by_gap(messages: list[Message], gap_hours: float = 4.0) -> list[list[Message]]:
    """Legacy: split on time gaps only. Prefer build_episodes()."""
    return [ep["messages"] for ep in build_episodes(messages, gap_hours)]


def build_episodes(messages: list[Message],
                   gap_hours: float = 4.0) -> list[dict]:
    """Group a chronological message list into episodes.

    Attribution: assistant messages carry their side-chat heart (or None
    for main). Each user message is attributed to the heart of the NEXT
    assistant message (the reply reveals which chat it was in), falling
    back to the previous assistant message, else None.

    A new episode starts when the gap between consecutive messages >=
    gap_hours, or when the attributed heart changes (None <-> H, H1 <-> H2).
    So a side chat opened mid-flow still gets its own session.

    Known ambiguity: back-to-back user messages from different chats with
    no reply between them (e.g. main-u, side-u, side-a[H]) attribute the
    earlier one to the later chat. Rare; accepted.

    Returns [{"heart": "green"|None, "messages": [...]}].
    """
    from datetime import timedelta
    gap = timedelta(hours=gap_hours)

    hearts: list[str | None] = []
    _MISSING = object()  # distinct from None (an unmarked assistant reply)
    for i, m in enumerate(messages):
        if m.speaker == "assistant":
            hearts.append(m.heart)
            continue
        h: str | None | object = _MISSING
        for j in range(i + 1, len(messages)):
            if messages[j].speaker == "assistant":
                h = messages[j].heart
                break
        if h is _MISSING:
            h = None
            for j in range(i - 1, -1, -1):
                if messages[j].speaker == "assistant":
                    h = messages[j].heart
                    break
        hearts.append(h)  # type: ignore[arg-type]

    episodes: list[dict] = []
    for m, h in zip(messages, hearts):
        if not episodes:
            episodes.append({"heart": h, "messages": []})
        else:
            prev = episodes[-1]["messages"][-1]
            if (m.ts - prev.ts) >= gap or h != episodes[-1]["heart"]:
                episodes.append({"heart": h, "messages": []})
        episodes[-1]["messages"].append(m)
    return [ep for ep in episodes if ep["messages"]]


def parse_export_zip(zip_path: str | Path) -> list[Conversation]:
    """Parse every conversation .txt in the export.

    Accepts the EYI Package .zip OR an unzipped export directory.
    Raw exports are never mutated.
    """
    convs: list[Conversation] = []
    p = Path(zip_path)
    if p.is_dir():
        txt_files = sorted(str(f) for f in p.glob("*.txt"))
        for path in txt_files:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            convs.append(parse_conversation_text(
                text,
                name=conversation_name_from_filename(path),
                source_file=Path(path).name,
            ))
        return convs
    with zipfile.ZipFile(p) as z:
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
