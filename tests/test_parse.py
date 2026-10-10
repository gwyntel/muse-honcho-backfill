"""Parser tests run against a hand-written anonymized fixture — never real exports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muse_export.parse import (
    clean_text,
    conversation_name_from_filename,
    parse_conversation_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_conversation.txt"


def test_parse_fixture():
    conv = parse_conversation_text(
        FIXTURE.read_text(), name="Conversation with Muse AI",
        source_file="sample_conversation.txt")
    assert conv.name == "Conversation with Muse AI"
    assert len(conv.messages) == 5

    m0 = conv.messages[0]
    assert m0.speaker == "assistant" and m0.raw_speaker == "Muse AI"
    assert m0.ts.isoformat() == "2026-10-01T12:00:00+00:00"
    assert m0.text == "Hey! I'm your personal agent."

    # multiline user message joins continuation lines
    m3 = conv.messages[3]
    assert m3.speaker == "user"
    assert m3.text == "the coast sounds nice\ndefinitely"

    # widget placeholder stripped, surrounding text kept
    m2 = conv.messages[2]
    assert "hatch_widget" not in m2.text
    assert "the mountains" in m2.text

    # empty assistant message parses but keeps empty text (loader skips it)
    assert conv.messages[4].text == ""

    # message ids are stable and unique
    ids = [m.msg_id for m in conv.messages]
    assert len(set(ids)) == 5


def test_clean_text():
    assert clean_text("hi [[hatch_widget:widget-abc]] bye") == "hi  bye"
    assert clean_text("\n\nhello\n\n") == "hello"


def test_conversation_name():
    assert conversation_name_from_filename(
        "Conversation with Muse AI_10-06-2026_1791319307.txt"
    ) == "Conversation with Muse AI"


SIDECHAT_TXT = """Conversation with Muse AI

[2026-10-10 12:00:00] You: main chat topic here
[2026-10-10 12:01:00] Muse AI: main chat reply
[2026-10-10 12:05:00] You: this is a side chat opener
[2026-10-10 12:05:30] Muse AI: side chat reply one
[💚] [side]
[2026-10-10 12:06:00] You: side chat followup
[2026-10-10 12:06:30] Muse AI: side chat reply two
[💚] [side]
[2026-10-10 12:07:00] You: back to main chat
[2026-10-10 12:07:30] Muse AI: main chat reply two
"""


def test_marker_extraction():
    from muse_export.parse import build_episodes
    conv = parse_conversation_text(SIDECHAT_TXT, name="t", source_file="t")
    assert len(conv.messages) == 8
    hearts = [m.heart for m in conv.messages]
    assert hearts == [None, None, None, "green", None, "green", None, None]
    # marker stripped from loaded text
    assert "[side]" not in conv.messages[3].text
    assert conv.messages[3].text == "side chat reply one"


def test_heart_episodes_and_pullback():
    from muse_export.parse import build_episodes
    conv = parse_conversation_text(SIDECHAT_TXT, name="t", source_file="t")
    eps = build_episodes(conv.messages, gap_hours=4.0)
    assert len(eps) == 3, [(e["heart"], len(e["messages"])) for e in eps]
    assert eps[0]["heart"] is None
    assert eps[1]["heart"] == "green"
    assert eps[2]["heart"] is None
    # pull-back: the side chat opener (unmarked user msg) joins the green episode
    green_texts = [m.text for m in eps[1]["messages"]]
    assert green_texts[0] == "this is a side chat opener"
    assert len(eps[1]["messages"]) == 4  # opener + 3 marked-flow messages


if __name__ == "__main__":
    test_parse_fixture()
    test_clean_text()
    test_conversation_name()
    test_marker_extraction()
    test_heart_episodes_and_pullback()
    print("all parser tests passed")
