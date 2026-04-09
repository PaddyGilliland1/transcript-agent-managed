"""
Transcript file format detection and parsing.
Supports VTT (WebVTT), SRT (SubRip), and plain TXT.
"""

import re


def parse_vtt(content: str) -> str:
    """Strip VTT headers, timestamps, and voice tags to produce clean text."""
    lines: list[str] = []
    for line in content.splitlines():
        # Skip VTT header, blank lines, and timestamp lines
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d{2}:\d{2}", line) and "-->" in line:
            continue
        if not line.strip():
            continue
        # Strip <v Speaker>...</v> voice tags but keep speaker name
        cleaned = re.sub(r"<v\s+([^>]+)>", r"\1: ", line)
        cleaned = re.sub(r"</v>", "", cleaned)
        # Strip other HTML-like tags
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        if cleaned.strip():
            lines.append(cleaned.strip())
    return "\n".join(lines)


def parse_srt(content: str) -> str:
    """Strip SRT sequence numbers and timestamps to produce clean text."""
    lines: list[str] = []
    for line in content.splitlines():
        # Skip sequence numbers (just digits)
        if re.match(r"^\d+$", line.strip()):
            continue
        # Skip timestamp lines
        if re.match(r"^\d{2}:\d{2}", line) and "-->" in line:
            continue
        if not line.strip():
            continue
        lines.append(line.strip())
    return "\n".join(lines)


def detect_and_parse(filename: str, content: str) -> str:
    """Auto-detect format by file extension and return cleaned transcript text."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    if ext == "vtt":
        return parse_vtt(content)
    elif ext == "srt":
        return parse_srt(content)
    else:
        # Plain text — return as-is
        return content.strip()
