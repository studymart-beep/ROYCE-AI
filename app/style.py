"""Voice rules for every reply Royce AI writes, plus a sanitizer that strips LLM markdown artifacts.

VOICE_RULES is appended to every system prompt. sanitize() runs on every outgoing text
(belt and suspenders: the prompt asks for plain text, sanitize() enforces it).
"""
import re

VOICE_RULES = (
    "Write like you text a smart friend. "
    "Plain text only: NO markdown, no asterisks, no bold, no headers, no numbered or bulleted lists "
    "unless the user explicitly asks for a list. "
    "Short paragraphs, natural flow, one idea per line. Vary sentence length. "
    "Never open with 'Great', 'Sure', 'Certainly' or restate the question. "
    "Give your actual take, specific numbers and names, like someone who has run campaigns for years."
)


def sanitize(text):
    """Strip markdown artifacts from an outgoing reply. Keeps emoji, keeps hashtags mid-line and at line ends."""
    if not text:
        return text
    t = str(text).replace("\r\n", "\n")
    # fenced code markers and inline backticks
    t = re.sub(r"^\s*```[a-zA-Z]*\s*$", "", t, flags=re.M)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    # headings at line start: "### Title" -> "Title" (only the # markers, never "#tag" mid-text)
    t = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", t, flags=re.M)
    # bold / italic / underline emphasis
    t = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"\1", t)
    t = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"\1", t)
    t = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", t)
    # markdown "* item" / "- item" bullets at line start become plain lines (our own "•" bullets are kept)
    t = re.sub(r"^[ \t]*\*[ \t]+", "", t, flags=re.M)
    t = re.sub(r"^[ \t]*-[ \t]+(?=\S)", "", t, flags=re.M)
    # any stray asterisks or underscores pairs left over
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    # collapse runs of blank lines (more than one blank line -> one blank line)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
