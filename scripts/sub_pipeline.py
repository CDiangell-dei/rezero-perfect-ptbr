#!/usr/bin/env python3
"""Subtitle extraction, translation-prep, validation, and extension build CLI
for Re:ZERO -Perfect Run- (PT-BR).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

# Ensure sibling imports work whether invoked as `python scripts/sub_pipeline.py`
# or imported as `scripts.sub_pipeline`.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from mkv_subtitle_extractor import (  # noqa: E402
    ExtractorError,
    TrackInfo,
    extract_subtitle_tracks,
    list_subtitle_tracks,
)

PROJECT_ROOT = _SCRIPT_DIR.parent
RAW_EN_DIR = PROJECT_ROOT / "raw_en"
TRANSLATED_DIR = PROJECT_ROOT / "translated_ptbr"
WORK_DIR = TRANSLATED_DIR / "work"
EXT_SUBS_DIR = PROJECT_ROOT / "extension" / "subtitles"
EXT_MANIFEST_PATH = EXT_SUBS_DIR / "manifest.json"

TOKEN_RE = re.compile(r"⟪T(\d+)⟫")
ASS_BLOCK_RE = re.compile(r"\{([^}]*)\}")
AN_TAG_RE = re.compile(r"\\an([1-9])")
DRAWING_TAG_RE = re.compile(r"\\p([1-9]\d*)")
SIGN_TAG_RE = re.compile(r"\\(?:pos|move)\b")
HTML_TAG_RE = re.compile(r"<(\/?)(i|b|u|s|font|c)\b[^>]*>", re.IGNORECASE)
ROMAJI_STYLE_RE = re.compile(r"(?:\b(?:romaji|karaoke)\b|(?:^|[-_\s])(?:op|ed|insert|song)\d*\s*[-_]\s*r\b)", re.IGNORECASE)
ANIMATED_STYLE_RE = re.compile(r"(?:sign|title|caption|\bop\d*\b|\bed\d*\b|song|lyric|insert|kfx)", re.IGNORECASE)
INLINE_STYLE_TAG_RE = re.compile(r"\\([ib][01])(?![0-9a-zA-Z])")
K_TAG_RE = re.compile(r"\\(?:k[fo]?|K)(\d+)")
POS_TAG_RE = re.compile(r"\\pos\(\s*([0-9.+-]+)\s*,\s*([0-9.+-]+)\s*\)")


def _build_catalog() -> list[dict[str, Any]]:
    arcs_spec = [
        ("arc01", 1, "The Tumultuous First Day", "S1 [1–3]", 2, False),
        ("arc02", 2, "The Turbulent Week", "S1 [4–11], OVA: Memory Snow", 4, True),
        ("arc03", 3, "Return to the Royal Capital", "S1 [12–25], S2 [1]", 9, False),
        ("arc04", 4, "The Everlasting Contract", "S2 [2–25]", 17, False),
        ("arc05", 5, "The Stars That Engrave History", "S3 [1–16]", 15, False),
        ("arc06", 6, "The Corridor of Memories", "S4 [1–19]", 12, False),
    ]
    catalog: list[dict[str, Any]] = []
    for arc_id, arc_num, title, coverage, ep_count, has_memory_snow in arcs_spec:
        episodes: list[dict[str, Any]] = []
        for n in range(1, ep_count + 1):
            ep_slug = f"ep{n:02d}"
            episodes.append(
                {
                    "episodeKey": f"{arc_id}-{ep_slug}",
                    "epSlug": ep_slug,
                    "episode": str(n),
                    "aliases": [str(n), ep_slug, f"EP {n}"],
                    "label": f"EP {n}",
                    "file": f"subtitles/{arc_id}/{ep_slug}.json",
                }
            )
        if has_memory_snow:
            episodes.append(
                {
                    "episodeKey": f"{arc_id}-memory-snow",
                    "epSlug": "memory-snow",
                    "episode": "memory-snow",
                    "aliases": ["5", "ova", "memory-snow", "memory snow", "Movie: Memory Snow"],
                    "label": "Movie: Memory Snow",
                    "file": f"subtitles/{arc_id}/memory-snow.json",
                }
            )
        catalog.append(
            {
                "id": arc_id,
                "number": arc_num,
                "title": title,
                "animeEpisodes": coverage,
                "episodes": episodes,
            }
        )
    return catalog


CATALOG = _build_catalog()


def resolve_arc_episode(arc_arg: str, ep_arg: str) -> tuple[dict[str, Any], dict[str, Any]]:
    arc_norm = arc_arg.strip().lower()
    if arc_norm.isdigit():
        arc_norm = f"arc{int(arc_norm):02d}"
    elif arc_norm.startswith("arc-"):
        num = arc_norm.split("-", 1)[1]
        if num.isdigit():
            arc_norm = f"arc{int(num):02d}"
    elif arc_norm.startswith("arc") and arc_norm[3:].isdigit():
        arc_norm = f"arc{int(arc_norm[3:]):02d}"

    arc_obj = next((a for a in CATALOG if a["id"] == arc_norm), None)
    if arc_obj is None:
        valid = ", ".join(a["id"] for a in CATALOG)
        raise SystemExit(f"Arco inválido: {arc_arg!r}. Use um de: {valid}")

    ep_norm = ep_arg.strip().lower()
    if ep_norm.startswith("ep"):
        ep_num = ep_norm[2:].lstrip("0") or "0"
    else:
        ep_num = ep_norm.lstrip("0") if ep_norm.isdigit() else ep_norm

    for ep_obj in arc_obj["episodes"]:
        candidates = {ep_obj["episode"].lower(), ep_obj["epSlug"].lower(), ep_obj["episodeKey"].lower()}
        candidates.update(a.lower() for a in ep_obj["aliases"])
        if ep_norm in candidates or ep_num in candidates:
            return arc_obj, ep_obj

    valid_eps = ", ".join(e["episode"] for e in arc_obj["episodes"])
    raise SystemExit(f"Episódio {ep_arg!r} inválido para {arc_obj['id']}. Opções: {valid_eps}")


def _parse_ass_time(ts: str) -> float:
    parts = ts.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Timestamp ASS inválido: {ts!r}")
    h = int(parts[0])
    m = int(parts[1])
    sec_parts = parts[2].split(".")
    s = int(sec_parts[0])
    cs = int(sec_parts[1].ljust(2, "0")[:2]) if len(sec_parts) > 1 else 0
    return round(h * 3600 + m * 60 + s + cs / 100.0, 3)


def _format_ass_time(seconds: float) -> str:
    cs_total = max(0, int(round(seconds * 100)))
    h = cs_total // 360_000
    cs_total %= 360_000
    m = cs_total // 6_000
    cs_total %= 6_000
    s = cs_total // 100
    cs = cs_total % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _parse_srt_time(ts: str) -> float:
    clean = ts.strip().replace(",", ".")
    parts = clean.split(":")
    if len(parts) == 2:
        h = 0
        m = int(parts[0])
        sec_str = parts[1]
    elif len(parts) == 3:
        h = int(parts[0])
        m = int(parts[1])
        sec_str = parts[2]
    else:
        raise ValueError(f"Timestamp SRT/VTT inválido: {ts!r}")
    sec_parts = sec_str.split(".")
    s = int(sec_parts[0])
    ms = int(sec_parts[1].ljust(3, "0")[:3]) if len(sec_parts) > 1 else 0
    return round(h * 3600 + m * 60 + s + ms / 1000.0, 3)


def _format_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    h = ms_total // 3_600_000
    ms_total %= 3_600_000
    m = ms_total // 60_000
    ms_total %= 60_000
    s = ms_total // 1_000
    ms = ms_total % 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def _split_leading_ass_tags(text: str) -> tuple[str, str]:
    idx = 0
    n = len(text)
    leading_parts: list[str] = []
    prepended_body = ""

    while idx < n and text[idx] == "{":
        close = text.find("}", idx + 1)
        if close == -1:
            break
        block = text[idx : close + 1]
        inner = text[idx + 1 : close]
        stripped = inner.strip()

        if stripped in (r"\i1", r"\i0", r"\b1", r"\b0"):
            break

        inline_matches = INLINE_STYLE_TAG_RE.findall(inner)
        if inline_matches:
            cleaned_inner = INLINE_STYLE_TAG_RE.sub("", inner)
            if cleaned_inner.strip():
                leading_parts.append("{" + cleaned_inner + "}")
            prepended_body = "".join(f"{{\\{t}}}" for t in inline_matches)
            idx = close + 1
            break

        leading_parts.append(block)
        idx = close + 1

    return "".join(leading_parts), prepended_body + text[idx:]


def _balance_inline_html_tags(text: str) -> str:
    """Ensure <i>/</i> and <b>/</b> tags are properly balanced within a line."""
    tokens = re.split(r"(</?[ib]>)", text)
    out: list[str] = []
    i_depth = 0
    b_depth = 0
    for tok in tokens:
        if tok == "<i>":
            i_depth += 1
            out.append(tok)
        elif tok == "</i>":
            if i_depth > 0:
                i_depth -= 1
                out.append(tok)
        elif tok == "<b>":
            b_depth += 1
            out.append(tok)
        elif tok == "</b>":
            if b_depth > 0:
                b_depth -= 1
                out.append(tok)
        else:
            out.append(tok)
    while i_depth > 0:
        out.append("</i>")
        i_depth -= 1
    while b_depth > 0:
        out.append("</b>")
        b_depth -= 1
    return "".join(out)


def _encode_ass_inline(text: str) -> tuple[str, list[str]]:
    """Convert inline ASS override blocks into <i>/<b> and indexed placeholders ⟪Tn⟫."""
    placeholders: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        full = match.group(0)
        inner = match.group(1).strip()
        if inner == r"\i1":
            return "<i>"
        if inner == r"\i0":
            return "</i>"
        if inner == r"\b1":
            return "<b>"
        if inner == r"\b0":
            return "</b>"
        token = f"⟪T{len(placeholders)}⟫"
        placeholders.append(full)
        return token

    encoded = ASS_BLOCK_RE.sub(_repl, text)
    return _balance_inline_html_tags(encoded), placeholders


def _decode_ass_inline(text: str, placeholders: list[str]) -> str:
    restored = (
        text.replace("<i>", r"{\i1}")
        .replace("</i>", r"{\i0}")
        .replace("<b>", r"{\b1}")
        .replace("</b>", r"{\b0}")
    )

    def _repl(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(placeholders):
            return placeholders[idx]
        return match.group(0)

    return TOKEN_RE.sub(_repl, restored)


def _ass_text_to_runs_lines(
    raw_ass_text: str, style_name: str, style_alignments: dict[str, int]
) -> tuple[int, bool, list[list[dict[str, Any]]]]:
    """Convert raw ASS text (with tags) into normalized extension cue fields."""
    align = style_alignments.get(style_name, 2)
    is_sign = False

    lower_style = style_name.lower()
    if any(k in lower_style for k in ("sign", "title", "note", "caption", "top")):
        is_sign = True

    for m in ASS_BLOCK_RE.finditer(raw_ass_text):
        tag_body = m.group(1)
        an_matches = AN_TAG_RE.findall(tag_body)
        if an_matches:
            align = int(an_matches[-1])
        if SIGN_TAG_RE.search(tag_body):
            is_sign = True

    if align in (7, 8, 9):
        is_sign = True

    # Normalize line breaks first; keep track of inline italics/bold across ASS blocks & HTML tags
    normalized = raw_ass_text.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", "\u00A0")

    lines: list[list[dict[str, Any]]] = []
    for raw_line in normalized.split("\n"):
        runs: list[dict[str, Any]] = []
        italic = False
        bold = False
        pos = 0
        # Walk {ass} and <i>/<b> tags sequentially
        combined_re = re.compile(r"\{([^}]*)\}|<(/?)(i|b)\b[^>]*>", re.IGNORECASE)
        for m in combined_re.finditer(raw_line):
            if m.start() > pos:
                chunk = raw_line[pos : m.start()]
                if chunk:
                    runs.append({"text": chunk, "i": italic, "b": bold})
            if m.group(1) is not None:
                body = m.group(1)
                if r"\i1" in body:
                    italic = True
                if r"\i0" in body or body.strip() == r"\r":
                    italic = False
                if r"\b1" in body:
                    bold = True
                if r"\b0" in body or body.strip() == r"\r":
                    bold = False
            else:
                closing = m.group(2) == "/"
                tag = m.group(3).lower()
                if tag == "i":
                    italic = not closing
                elif tag == "b":
                    bold = not closing
            pos = m.end()

        if pos < len(raw_line):
            tail = raw_line[pos:]
            if tail:
                runs.append({"text": tail, "i": italic, "b": bold})

        # Merge adjacent runs with identical styles
        merged: list[dict[str, Any]] = []
        for r in runs:
            if not r["text"]:
                continue
            if merged and merged[-1]["i"] == r["i"] and merged[-1]["b"] == r["b"]:
                merged[-1]["text"] += r["text"]
            else:
                merged.append(r)
        if merged:
            lines.append(merged)

    return align, is_sign, lines


def _html_text_to_runs_lines(raw_text: str) -> list[list[dict[str, Any]]]:
    lines: list[list[dict[str, Any]]] = []
    for raw_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        runs: list[dict[str, Any]] = []
        italic = False
        bold = False
        pos = 0
        for m in HTML_TAG_RE.finditer(raw_line):
            if m.start() > pos:
                chunk = raw_line[pos : m.start()]
                if chunk:
                    runs.append({"text": chunk, "i": italic, "b": bold})
            closing = m.group(1) == "/"
            tag = m.group(2).lower()
            if tag == "i":
                italic = not closing
            elif tag == "b":
                bold = not closing
            pos = m.end()
        if pos < len(raw_line):
            tail = raw_line[pos:]
            if tail:
                runs.append({"text": tail, "i": italic, "b": bold})
        merged: list[dict[str, Any]] = []
        for r in runs:
            if not r["text"]:
                continue
            if merged and merged[-1]["i"] == r["i"] and merged[-1]["b"] == r["b"]:
                merged[-1]["text"] += r["text"]
            else:
                merged.append(r)
        if merged:
            lines.append(merged)
    return lines


def _runs_to_plain_text(lines: list[list[dict[str, Any]]]) -> str:
    return "\n".join("".join(seg["text"] for seg in line) for line in lines)


def _is_romaji_style(style: str) -> bool:
    return bool(ROMAJI_STYLE_RE.search(style)) and not bool(
        re.search(r"\b(?:english|pt|br)\b", style, re.IGNORECASE)
    )


# Uniquely English lyric tokens (intentionally excludes common Romaji homographs like no/me/to/ni/wa)
ENGLISH_LYRIC_WORDS = {
    "the", "you", "your", "yours", "my", "mine", "we", "our", "ours",
    "for", "of", "with", "from", "this", "that", "these", "those",
    "please", "wait", "waiting", "die", "dying", "life", "live", "living",
    "wish", "now", "new", "day", "days", "touch", "end", "endless", "dead",
    "never", "always", "will", "would", "could", "should", "are", "is",
    "was", "were", "been", "have", "has", "had", "all", "and", "but",
    "not", "if", "when", "what", "who", "why", "how", "love", "heart",
    "world", "light", "dark", "night", "sky", "tears", "eyes", "soul",
    "pain", "away", "here", "there", "inside", "down", "give", "see",
    "off", "start", "let", "hold", "stay", "believe", "feel", "know",
    "time", "dream", "dreams", "again", "forever", "tonight",
}
ENGLISH_CONTRACTION_RE = re.compile(r"\b[a-z]+(?:'t|'ll|'re|'ve|'m|'d)\b", re.IGNORECASE)


def _looks_like_english_lyric(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.lower()))
    hits = len(words & ENGLISH_LYRIC_WORDS)
    has_contraction = bool(ENGLISH_CONTRACTION_RE.search(text))
    return hits >= 2 or (hits >= 1 and has_contraction)


def _song_style_prefix(style: str) -> str:
    return re.sub(r"\s*-\s*(?:romaji|karaoke|english|pt|br|r|e)\b.*$", "", style, flags=re.IGNORECASE).strip().lower()


def _resolve_lyric_tr_style(romaji_style: str, style_alignments: dict[str, int]) -> str:
    candidate = re.sub(r"\b(?:romaji|karaoke)\b", "English", romaji_style, flags=re.IGNORECASE)
    if candidate in style_alignments:
        return candidate
    prefix = _song_style_prefix(romaji_style)
    for st in style_alignments:
        if not _is_romaji_style(st) and _song_style_prefix(st) == prefix:
            return st
    return "Default"


def _synthesize_english_karaoke_entries(
    src_path: Path,
    events_list: list[dict[str, Any]],
    style_alignments: dict[str, int],
) -> list[dict[str, Any]]:
    """Create translatable lyric entries for untranslated English lines embedded in Romaji karaoke."""
    karaoke_cues = _extract_karaoke_web_cues(src_path)
    if not karaoke_cues:
        return []

    song_tr_spans: list[tuple[str, float, float]] = []
    for ev in events_list:
        if ev.get("skip"):
            continue
        st = str(ev.get("style", ""))
        if re.search(r"\b(?:op|ed\d*|lyric|song|insert|english)\b", st, re.IGNORECASE):
            song_tr_spans.append((_song_style_prefix(st), float(ev["start"]), float(ev["end"])))

    next_id = max((int(e["id"]) for e in events_list), default=0) + 1
    out: list[dict[str, Any]] = []
    for kcue in karaoke_cues:
        kplain = _runs_to_plain_text(kcue["lines"]).replace("\n", r"\N").strip()
        if not kplain or not _looks_like_english_lyric(kplain):
            continue
        ks, ke = float(kcue["start"]), float(kcue["end"])
        if ke <= ks:
            continue
        kpref = _song_style_prefix(str(kcue.get("style", "")))
        covered = False
        for spref, es, ee in song_tr_spans:
            if kpref and spref and spref != kpref:
                continue
            ov = min(ke, ee) - max(ks, es)
            if ov >= 0.4 * max(0.2, min(ke - ks, ee - es)):
                covered = True
                break
        if covered:
            continue

        tr_style = _resolve_lyric_tr_style(str(kcue.get("style", "")), style_alignments)
        out.append(
            {
                "id": next_id,
                "kind": "Dialogue",
                "start": ks,
                "end": ke,
                "style": tr_style,
                "actor": "lyric-en",
                "leading_tags": "",
                "inline_tags": [],
                "en": kplain,
                "pt": "",
                "skip": False,
                "omit_web": False,
                "collapsed": False,
                "karaoke_tr": True,
                "fields": {
                    "layer": "0",
                    "start": _format_ass_time(ks),
                    "end": _format_ass_time(ke),
                    "style": tr_style,
                    "name": "lyric-en",
                    "marginl": "0",
                    "marginr": "0",
                    "marginv": "0",
                    "effect": "",
                    "text": kplain,
                },
                "raw_line": "",
            }
        )
        next_id += 1
    return out


def _normalize_ass_plain(raw_text: str) -> str:
    plain = ASS_BLOCK_RE.sub("", raw_text)
    plain = plain.replace(r"\n", r"\N").replace("\n", r"\N")
    lines = [re.sub(r"[ \t]+", " ", part).strip() for part in plain.split(r"\N")]
    lines = [ln for ln in lines if ln]
    return r"\N".join(lines)


def _collapse_animated_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse frame-by-frame animated Signs/OP/ED lines into clean single entries."""
    non_skipped = [e for e in events if not e.get("skip")]
    skipped = [e for e in events if e.get("skip")]

    # Identify short-frame duplicate bursts even outside named Sign/OP/ED styles
    short_counts: dict[tuple[str, str], int] = {}
    for ev in non_skipped:
        if (ev["end"] - ev["start"]) <= 0.25:
            norm = _normalize_ass_plain(ev.get("raw_text", ev["en"]))
            if norm:
                key = (ev["style"], norm)
                short_counts[key] = short_counts.get(key, 0) + 1

    eligible: list[dict[str, Any]] = []
    passthrough: list[dict[str, Any]] = []

    for ev in non_skipped:
        norm = _normalize_ass_plain(ev.get("raw_text", ev["en"]))
        key = (ev["style"], norm)
        is_anim_style = bool(ANIMATED_STYLE_RE.search(ev["style"]))
        is_short_burst = short_counts.get(key, 0) >= 3 and (ev["end"] - ev["start"]) <= 0.25
        if norm and (is_anim_style or is_short_burst):
            eligible.append(ev)
        else:
            passthrough.append(ev)

    # Group eligible frames by (style, norm_plain) and merge contiguous/overlapping spans
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ev in eligible:
        norm = _normalize_ass_plain(ev.get("raw_text", ev["en"]))
        by_key.setdefault((ev["style"], norm), []).append(ev)

    collapsed_out: list[dict[str, Any]] = []
    for (style, norm), grp in by_key.items():
        grp.sort(key=lambda e: (e["start"], e["end"], e["id"]))
        bursts: list[list[dict[str, Any]]] = []
        for ev in grp:
            if not bursts:
                bursts.append([ev])
                continue
            cur_end = max(x["end"] for x in bursts[-1])
            if ev["start"] - cur_end <= 1.5:
                bursts[-1].append(ev)
            else:
                bursts.append([ev])

        for b in bursts:
            first = b[0]
            raw_sample = first.get("raw_text", "")
            has_frame_fx = bool(re.search(r"\\(?:pos|move|fsc[xy]|t|fad)\b", raw_sample))
            if len(b) >= 2 or (ANIMATED_STYLE_RE.search(style) and has_frame_fx):
                b_start = min(x["start"] for x in b)
                b_end = max(x["end"] for x in b)
                merged_ev = dict(first)
                merged_ev["start"] = b_start
                merged_ev["end"] = b_end
                merged_ev["leading_tags"] = ""
                merged_ev["inline_tags"] = []
                merged_ev["en"] = norm
                merged_ev["pt"] = ""
                merged_ev["collapsed"] = True
                merged_ev["frame_count"] = len(b)
                collapsed_out.append(merged_ev)
            else:
                collapsed_out.extend(b)

    combined = skipped + passthrough + collapsed_out
    combined.sort(key=lambda e: (e["start"], e["end"], e["id"]))
    for idx, ev in enumerate(combined, start=1):
        ev["id"] = idx
    return combined


def parse_ass_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    section = ""
    style_fields: list[str] = []
    style_alignments: dict[str, int] = {}
    style_fontsizes: dict[str, float] = {}
    event_fields: list[str] = []
    header_lines: list[str] = []
    events: list[dict[str, Any]] = []
    in_events = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            if section == "events":
                in_events = True
                header_lines.append(line)
                continue
            else:
                in_events = False

        if not in_events:
            header_lines.append(line)
            if section in ("v4+ styles", "v4 styles"):
                if stripped.lower().startswith("format:"):
                    style_fields = [f.strip().lower() for f in stripped.split(":", 1)[1].split(",")]
                elif stripped.lower().startswith("style:") and style_fields:
                    vals = [v.strip() for v in stripped.split(":", 1)[1].split(",", len(style_fields) - 1)]
                    row = dict(zip(style_fields, vals))
                    sname = row.get("name", "")
                    try:
                        style_alignments[sname] = int(row.get("alignment", "2"))
                    except ValueError:
                        style_alignments[sname] = 2
                    try:
                        style_fontsizes[sname] = float(row.get("fontsize", "72"))
                    except ValueError:
                        style_fontsizes[sname] = 72.0
            continue

        # Inside [Events]
        if stripped.lower().startswith("format:"):
            event_fields = [f.strip().lower() for f in stripped.split(":", 1)[1].split(",")]
            header_lines.append(line)
            continue

        if stripped.lower().startswith("dialogue:") or stripped.lower().startswith("comment:"):
            kind, payload = line.split(":", 1)
            kind_clean = kind.strip()
            if not event_fields:
                event_fields = [
                    "layer",
                    "start",
                    "end",
                    "style",
                    "name",
                    "marginl",
                    "marginr",
                    "marginv",
                    "effect",
                    "text",
                ]
            parts = payload.lstrip().split(",", len(event_fields) - 1)
            row = dict(zip(event_fields, parts))
            start_s = _parse_ass_time(row.get("start", "0:00:00.00"))
            end_s = _parse_ass_time(row.get("end", "0:00:00.00"))
            style = row.get("style", "Default")
            actor = row.get("name", "")
            raw_text = row.get("text", "")

            is_comment = kind_clean.lower() == "comment"
            is_drawing = bool(DRAWING_TAG_RE.search(raw_text))
            omit_web = _is_romaji_style(style)
            leading_tags, body_text = _split_leading_ass_tags(raw_text)
            encoded_en, inline_tags = _encode_ass_inline(body_text)
            skip = is_comment or is_drawing or omit_web or (encoded_en.strip() == "")

            events.append(
                {
                    "id": len(events) + 1,
                    "kind": kind_clean,
                    "start": start_s,
                    "end": end_s,
                    "start_raw": row.get("start", "0:00:00.00"),
                    "end_raw": row.get("end", "0:00:00.00"),
                    "style": style,
                    "actor": actor,
                    "fields": row,
                    "leading_tags": leading_tags,
                    "inline_tags": inline_tags,
                    "en": encoded_en,
                    "pt": encoded_en if skip else "",
                    "skip": skip,
                    "omit_web": omit_web,
                    "collapsed": False,
                    "raw_text": raw_text,
                    "raw_line": line,
                }
            )
        else:
            if not events:
                header_lines.append(line)

    return {
        "format": "ass",
        "header_lines": header_lines,
        "event_fields": event_fields
        or ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"],
        "style_alignments": style_alignments,
        "style_fontsizes": style_fontsizes,
        "events": events,
    }


def _est_glyph_width(text: str, fs: float) -> float:
    total = 0.0
    for ch in text:
        if ch in "ilIjtfr.,'\"!:;":
            total += 0.145 * fs
        elif ch == "s":
            total += 0.21 * fs
        elif ch in "mwMW":
            total += 0.40 * fs
        elif ch.isupper():
            total += 0.33 * fs
        else:
            total += 0.265 * fs
    return total


def _parse_native_k_karaoke(start_s: float, raw_text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse native ASS {\\k...} syllables into spaced text and timed syllable entries."""
    cur_t = start_s
    syls: list[dict[str, Any]] = []
    pos = 0
    n = len(raw_text)

    while pos < n:
        if raw_text[pos] == "{":
            close = raw_text.find("}", pos + 1)
            if close == -1:
                break
            body = raw_text[pos + 1 : close]
            k_vals = [int(v) for v in K_TAG_RE.findall(body)]
            dur_s = sum(k_vals) / 100.0 if k_vals else 0.0
            pos = close + 1
            next_brace = raw_text.find("{", pos)
            seg_end = n if next_brace == -1 else next_brace
            seg_text = raw_text[pos:seg_end].replace(r"\N", " ").replace(r"\n", " ").replace(r"\h", "\u00A0")
            pos = seg_end
            if seg_text:
                syls.append({"text": seg_text, "t": round(cur_t, 3)})
            cur_t += dur_s
        else:
            next_brace = raw_text.find("{", pos)
            seg_end = n if next_brace == -1 else next_brace
            seg_text = raw_text[pos:seg_end].replace(r"\N", " ").replace(r"\n", " ").replace(r"\h", "\u00A0")
            pos = seg_end
            if seg_text:
                syls.append({"text": seg_text, "t": round(cur_t, 3)})

    joined = "".join(s["text"] for s in syls).strip()
    return joined, syls


def _extract_karaoke_web_cues(raw_ass_path: Path) -> list[dict[str, Any]]:
    """Extract clean timed Romaji karaoke cues (line-level or per-syllable) from raw .ass."""
    parsed = parse_ass_file(raw_ass_path)
    style_alignments = parsed.get("style_alignments", {})
    style_fontsizes = parsed.get("style_fontsizes", {})

    by_style: dict[str, list[dict[str, Any]]] = {}
    for ev in parsed["events"]:
        if ev.get("kind", "Dialogue").lower() == "comment":
            continue
        raw_text = ev.get("raw_text", "")
        if DRAWING_TAG_RE.search(raw_text):
            continue
        style = ev.get("style", "Default")
        if not _is_romaji_style(style):
            continue
        if not _normalize_ass_plain(raw_text):
            continue
        by_style.setdefault(style, []).append(ev)

    karaoke_cues: list[dict[str, Any]] = []

    for style, evs in by_style.items():
        align = style_alignments.get(style, 8)
        fs = float(style_fontsizes.get(style, 72.0) or 72.0)

        # Inspect Format 1 (native ASS \k tags) and Format 2 (\fad(200,0)+\pos per-syllable frames)
        native_k_evs = [e for e in evs if K_TAG_RE.search(e.get("raw_text", ""))]
        fad_in_groups: dict[float, dict[tuple[float, float, str], tuple[float, float, float, str]]] = {}
        fad_out_spans: list[tuple[float, float]] = []
        for ev in evs:
            raw_text = ev.get("raw_text", "")
            plain = ASS_BLOCK_RE.sub("", raw_text).replace(r"\N", " ").replace(r"\n", " ").strip()
            if not plain:
                continue
            m_pos = POS_TAG_RE.search(raw_text)
            if r"\fad(200,0)" in raw_text and m_pos:
                x = float(m_pos.group(1))
                y = float(m_pos.group(2))
                st = round(ev["start"], 3)
                key = (round(x, 1), round(y, 1), plain)
                grp = fad_in_groups.setdefault(st, {})
                if key not in grp:
                    grp[key] = (x, y, ev["end"], plain)
            elif r"\fad(0,200)" in raw_text:
                fad_out_spans.append((ev["start"], ev["end"]))

        is_per_syllable_fad = any(len(g) >= 2 for g in fad_in_groups.values())

        # Prefer Format 2 when it covers at least as many lyric lines as sparse native \k templates (e.g. ED01)
        if native_k_evs and not (is_per_syllable_fad and len(fad_in_groups) >= len(native_k_evs)):
            seen_native: set[tuple[float, float, str]] = set()
            for ev in sorted(native_k_evs, key=lambda e: (e["start"], e["end"], e["id"])):
                raw_text = ev.get("raw_text", "")
                joined, syls = _parse_native_k_karaoke(ev["start"], raw_text)
                if not joined:
                    continue
                sig = (round(ev["start"], 2), round(ev["end"], 2), joined)
                if sig in seen_native:
                    continue
                seen_native.add(sig)
                cue: dict[str, Any] = {
                    "start": ev["start"],
                    "end": ev["end"],
                    "align": align,
                    "style": style,
                    "isSign": False,
                    "isKaraoke": True,
                    "lines": [[{"text": joined, "i": False, "b": False}]],
                }
                if syls:
                    cue["karaoke"] = syls
                karaoke_cues.append(cue)
            continue

        # Format 2: per-syllable \fad(200,0) + \pos(x, y) pre-rendered frames (e.g. ED01)
        if is_per_syllable_fad and fad_in_groups:
            starts_sorted = sorted(fad_in_groups.keys())
            row_band = max(20.0, fs * 0.35)
            for idx, st_s in enumerate(starts_sorted):
                items = sorted(fad_in_groups[st_s].values(), key=lambda t: (round(t[1] / row_band), t[0]))
                max_k = max(t[2] for t in items)
                next_st = starts_sorted[idx + 1] if idx + 1 < len(starts_sorted) else max_k + 5.0
                cand_ends = [e for (s, e) in fad_out_spans if s >= st_s and e > st_s + 0.1 and e <= next_st + 0.5]
                line_end = max(cand_ends) if cand_ends else round(max_k + 0.6, 3)
                if line_end < max_k:
                    line_end = round(max_k + 0.3, 3)
                if line_end <= st_s + 0.05:
                    continue

                # Build spaced syllables: attach trailing space to preceding syllable
                syls_out: list[dict[str, Any]] = []
                for s_idx, (x, y, k_end, raw_p) in enumerate(items):
                    if s_idx > 0:
                        px, py, _, prev_p = items[s_idx - 1]
                        same_row = abs(y - py) <= row_band
                        if not same_row:
                            syls_out[-1]["text"] += " "
                        else:
                            expected_dx = (_est_glyph_width(prev_p, fs) + _est_glyph_width(raw_p, fs)) / 2.0
                            gap = (x - px) - expected_dx
                            if gap > 0.125 * fs and not prev_p.endswith(":"):
                                syls_out[-1]["text"] += " "
                    syls_out.append({"text": raw_p, "t": round(k_end, 3)})

                joined = "".join(s["text"] for s in syls_out).strip()
                if joined:
                    karaoke_cues.append(
                        {
                            "start": st_s,
                            "end": line_end,
                            "align": align,
                            "style": style,
                            "isSign": False,
                            "isKaraoke": True,
                            "lines": [[{"text": joined, "i": False, "b": False}]],
                            "karaoke": syls_out,
                        }
                    )
            continue

        # Format 3 fallback / Format 4: static per-syllable \pos(x, y) spans held >= 0.4s (e.g. ED - R)
        pos_span_groups: dict[tuple[float, float], dict[tuple[float, float, str], tuple[float, float, str]]] = {}
        for ev in evs:
            if float(ev["end"]) - float(ev["start"]) < 0.4:
                continue
            raw_text = ev.get("raw_text", "")
            plain = ASS_BLOCK_RE.sub("", raw_text).replace(r"\N", " ").replace(r"\n", " ").strip()
            m_pos = POS_TAG_RE.search(raw_text)
            if not plain or not m_pos:
                continue
            x = float(m_pos.group(1))
            y = float(m_pos.group(2))
            span_key = (round(float(ev["start"]), 2), round(float(ev["end"]), 2))
            grp = pos_span_groups.setdefault(span_key, {})
            item_key = (round(x, 1), round(y, 1), plain)
            if item_key not in grp:
                grp[item_key] = (x, y, plain)

        if pos_span_groups and any(len(g) >= 2 for g in pos_span_groups.values()):
            row_band = max(20.0, fs * 0.35)
            for (st_s, en_s), grp in sorted(pos_span_groups.items()):
                items = sorted(grp.values(), key=lambda t: (round(t[1] / row_band), t[0]))
                parts_out: list[str] = []
                for s_idx, (x, y, raw_p) in enumerate(items):
                    if s_idx > 0:
                        px, py, prev_p = items[s_idx - 1]
                        if abs(y - py) > row_band:
                            parts_out.append(" ")
                        else:
                            expected_dx = (_est_glyph_width(prev_p, fs) + _est_glyph_width(raw_p, fs)) / 2.0
                            if (x - px) - expected_dx > 0.125 * fs and not prev_p.endswith(":"):
                                parts_out.append(" ")
                    parts_out.append(raw_p)
                joined = "".join(parts_out).strip()
                if joined:
                    karaoke_cues.append(
                        {
                            "start": st_s,
                            "end": en_s,
                            "align": align,
                            "style": style,
                            "isSign": False,
                            "isKaraoke": True,
                            "lines": [[{"text": joined, "i": False, "b": False}]],
                        }
                    )
            continue

        # Format 3: full-line animated Romaji frames (e.g. OP - Romaji)
        by_plain: dict[str, list[dict[str, Any]]] = {}
        for ev in evs:
            norm = _normalize_ass_plain(ev.get("raw_text", ""))
            if norm:
                by_plain.setdefault(norm, []).append(ev)

        for norm, grp in by_plain.items():
            grp.sort(key=lambda e: (e["start"], e["end"], e["id"]))
            bursts: list[list[dict[str, Any]]] = []
            for ev in grp:
                if not bursts:
                    bursts.append([ev])
                    continue
                cur_end = max(x["end"] for x in bursts[-1])
                if ev["start"] - cur_end <= 1.5:
                    bursts[-1].append(ev)
                else:
                    bursts.append([ev])
            for b in bursts:
                b_start = min(x["start"] for x in b)
                b_end = max(x["end"] for x in b)
                lines_runs = [[{"text": ln, "i": False, "b": False}] for ln in norm.split(r"\N") if ln]
                if lines_runs:
                    karaoke_cues.append(
                        {
                            "start": b_start,
                            "end": b_end,
                            "align": align,
                            "style": style,
                            "isSign": False,
                            "isKaraoke": True,
                            "lines": lines_runs,
                        }
                    )

    karaoke_cues.sort(key=lambda c: (c["start"], c["end"]))
    return karaoke_cues


def parse_srt_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", raw.strip())
    events: list[dict[str, Any]] = []
    for block in blocks:
        block_lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if len(block_lines) < 2:
            continue
        ts_idx = 0
        if "-->" not in block_lines[0] and len(block_lines) >= 2 and "-->" in block_lines[1]:
            ts_idx = 1
        if "-->" not in block_lines[ts_idx]:
            continue
        ts_line = block_lines[ts_idx]
        left, right = ts_line.split("-->", 1)
        right = right.strip().split()[0]
        start_s = _parse_srt_time(left)
        end_s = _parse_srt_time(right)
        text = "\n".join(block_lines[ts_idx + 1 :])
        skip = text.strip() == ""
        events.append(
            {
                "id": len(events) + 1,
                "kind": "Dialogue",
                "start": start_s,
                "end": end_s,
                "style": "Default",
                "actor": "",
                "leading_tags": "",
                "inline_tags": [],
                "en": text,
                "pt": text if skip else "",
                "skip": skip,
            }
        )
    return {
        "format": "srt",
        "header_lines": [],
        "event_fields": [],
        "style_alignments": {"Default": 2},
        "events": events,
    }


def parse_subtitle_file(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    if ext in (".ass", ".ssa"):
        return parse_ass_file(path)
    if ext == ".srt":
        return parse_srt_file(path)
    raise ExtractorError(f"Formato de legenda não suportado no pipeline: {path.suffix}")


def choose_primary_track(tracks: list[TrackInfo]) -> TrackInfo:
    text_tracks = [t for t in tracks if t.is_text_subtitle]
    if not text_tracks:
        raise ExtractorError("Nenhuma faixa de legenda de texto encontrada neste MKV.")

    def _score(t: TrackInfo) -> tuple[int, int]:
        lang = t.language.lower()
        name = t.name.lower()
        score = 0
        if lang in ("en", "eng") or lang.startswith("en-"):
            score += 100
        if any(k in name for k in ("jp", "japan", "japanese audio", "full", "dialogue")):
            score += 30
        if any(k in name for k in ("en audio", "english audio", "dub", "cc")):
            score -= 25
        if any(k in name for k in ("sign", "song", "forced")) and "full" not in name and "dialogue" not in name:
            score -= 40
        if t.is_forced:
            score -= 50
        if t.is_default:
            score += 10
        return (score, -t.track_number)

    return max(text_tracks, key=_score)


def _find_raw_source(episode_key: str) -> Path:
    for ext in (".ass", ".ssa", ".srt"):
        candidate = RAW_EN_DIR / f"{episode_key}{ext}"
        if candidate.is_file():
            return candidate
    raise SystemExit(
        f"Arquivo de legenda base não encontrado em {RAW_EN_DIR} para {episode_key}. "
        "Execute primeiro `extract` ou salve o arquivo em raw_en/."
    )


def _load_manifest() -> dict[str, Any]:
    if EXT_MANIFEST_PATH.is_file():
        return json.loads(EXT_MANIFEST_PATH.read_text(encoding="utf-8"))
    return _create_empty_manifest()


def _create_empty_manifest() -> dict[str, Any]:
    arcs_out = []
    for arc in CATALOG:
        eps_out = []
        for ep in arc["episodes"]:
            eps_out.append(
                {
                    "episodeKey": ep["episodeKey"],
                    "epSlug": ep["epSlug"],
                    "episode": ep["episode"],
                    "aliases": ep["aliases"],
                    "label": ep["label"],
                    "file": ep["file"],
                    "available": False,
                    "cueCount": 0,
                    "updatedAt": None,
                }
            )
        arcs_out.append(
            {
                "id": arc["id"],
                "number": arc["number"],
                "title": arc["title"],
                "animeEpisodes": arc["animeEpisodes"],
                "episodes": eps_out,
            }
        )
    return {
        "version": 1,
        "language": "pt-BR",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "totalEpisodes": sum(len(a["episodes"]) for a in arcs_out),
        "availableEpisodes": 0,
        "arcs": arcs_out,
    }


def _save_manifest(manifest: dict[str, Any]) -> None:
    EXT_SUBS_DIR.mkdir(parents=True, exist_ok=True)
    avail = sum(1 for a in manifest["arcs"] for e in a["episodes"] if e.get("available"))
    total = sum(len(a["episodes"]) for a in manifest["arcs"])
    manifest["totalEpisodes"] = total
    manifest["availableEpisodes"] = avail
    manifest["generatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    EXT_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_init_manifest(_args: argparse.Namespace) -> int:
    manifest = _create_empty_manifest()
    if EXT_MANIFEST_PATH.is_file():
        old = json.loads(EXT_MANIFEST_PATH.read_text(encoding="utf-8"))
        old_eps = {
            ep["episodeKey"]: ep
            for arc in old.get("arcs", [])
            for ep in arc.get("episodes", [])
        }
        for arc in manifest["arcs"]:
            for ep in arc["episodes"]:
                prev = old_eps.get(ep["episodeKey"])
                if prev and prev.get("available"):
                    ep["available"] = True
                    ep["cueCount"] = prev.get("cueCount", 0)
                    ep["updatedAt"] = prev.get("updatedAt")
    _save_manifest(manifest)
    print(f"Manifesto inicializado em: {EXT_MANIFEST_PATH} ({manifest['availableEpisodes']}/{manifest['totalEpisodes']})")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    print("Re:ZERO -Perfect Run- — Progresso da Tradução PT-BR\n")
    total = 0
    done = 0
    next_ep: str | None = None

    for arc in manifest["arcs"]:
        eps = arc["episodes"]
        arc_done = sum(1 for e in eps if e.get("available"))
        total += len(eps)
        done += arc_done
        if next_ep is None:
            for e in eps:
                if not e.get("available"):
                    next_ep = e["episodeKey"]
                    break
        print(f"  {arc['id'].upper():<6} | {arc_done:>2}/{len(eps):<2} prontos | {arc['title']} ({arc['animeEpisodes']})")

    print(f"\nTotal: {done}/{total} episódios prontos.")
    if next_ep:
        print(f"Próximo sugerido: {next_ep}")
    else:
        print("Todos os episódios do catálogo estão concluídos!")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    mkv_path = Path(args.mkv_path)
    tracks = list_subtitle_tracks(mkv_path, engine=args.engine)
    if not tracks:
        print("Nenhuma faixa de legenda encontrada.")
        return 1
    print(f"Faixas de legenda em {mkv_path.name}:")
    print(f"  {'Track':<5} {'Lang':<8} {'Codec':<15} {'Def':<4} {'Frc':<4} Nome")
    print("  " + "-" * 72)
    for t in tracks:
        print(
            f"  {t.track_number:<5} {t.language:<8} {t.codec_id:<15} "
            f"{'Sim' if t.is_default else 'Não':<4} {'Sim' if t.is_forced else 'Não':<4} {t.name or '(sem nome)'}"
        )
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    arc_obj, ep_obj = resolve_arc_episode(args.arc, args.ep)
    episode_key = ep_obj["episodeKey"]
    mkv_path = Path(args.mkv_path)
    if not mkv_path.is_file():
        raise SystemExit(f"Arquivo MKV não encontrado: {mkv_path}")

    tracks = list_subtitle_tracks(mkv_path, engine=args.engine)
    print(f"Faixas encontradas em {mkv_path.name}:")
    for t in tracks:
        print(f"  - Track {t.track_number}: lang={t.language}, codec={t.codec_id}, name={t.name!r}, default={t.is_default}, forced={t.is_forced}")

    if args.track is not None:
        primary_track = next((t for t in tracks if t.track_number == args.track), None)
        if primary_track is None:
            raise SystemExit(f"Track {args.track} não encontrada no arquivo.")
        target_nums = [args.track]
    else:
        primary_track = choose_primary_track(tracks)
        target_nums = [t.track_number for t in tracks if t.is_text_subtitle]

    RAW_EN_DIR.mkdir(parents=True, exist_ok=True)
    tmp_extract_dir = RAW_EN_DIR / f"_tmp_{episode_key}"
    extracted = extract_subtitle_tracks(
        mkv_path,
        tmp_extract_dir,
        track_numbers=target_nums,
        engine=args.engine,
        filename_prefix=episode_key,
    )

    primary_out: Path | None = None
    for tnum, tmp_file in extracted.items():
        if tnum == primary_track.track_number:
            dest = RAW_EN_DIR / f"{episode_key}{tmp_file.suffix}"
            tmp_file.replace(dest)
            primary_out = dest
        else:
            dest = RAW_EN_DIR / f"{episode_key}.track{tnum}{tmp_file.suffix}"
            tmp_file.replace(dest)
    if tmp_extract_dir.exists():
        tmp_extract_dir.rmdir()

    if primary_out is None or not primary_out.is_file():
        raise SystemExit("Falha: arquivo de legenda principal não foi gerado.")

    parsed = parse_subtitle_file(primary_out)
    dialogue_cues = [e for e in parsed["events"] if not e["skip"]]
    print(f"\nFaixa principal selecionada: Track {primary_track.track_number} ({primary_track.name or primary_track.language}) -> {primary_out}")
    print(f"Total de eventos: {len(parsed['events'])} | Falas/textos traduzíveis: {len(dialogue_cues)}")

    if len(dialogue_cues) < args.min_cues:
        raise SystemExit(
            f"Validação falhou: apenas {len(dialogue_cues)} falas encontradas (mínimo exigido: {args.min_cues}). "
            "O arquivo MKV original NÃO foi removido."
        )

    print("\nPrimeiras 5 falas extraídas:")
    for item in dialogue_cues[:5]:
        print(f"  [{item['start']:.2f}s -> {item['end']:.2f}s] {item['en']}")

    if args.delete_source:
        size_mb = mkv_path.stat().st_size / (1024 * 1024)
        mkv_path.unlink()
        print(f"\nLimpeza concluída: {mkv_path.name} removido ({size_mb:.2f} MB liberados).")
    else:
        print(f"\nArquivo fonte mantido: {mkv_path} (passe --delete-source para apagar automaticamente após validar).")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    arc_obj, ep_obj = resolve_arc_episode(args.arc, args.ep)
    episode_key = ep_obj["episodeKey"]
    src_path = _find_raw_source(episode_key)
    parsed = parse_subtitle_file(src_path)
    events_list = (
        _collapse_animated_events(parsed["events"])
        if parsed["format"] == "ass"
        else parsed["events"]
    )
    if parsed["format"] == "ass":
        events_list.extend(
            _synthesize_english_karaoke_entries(src_path, events_list, parsed["style_alignments"])
        )

    ep_work_dir = WORK_DIR / episode_key
    ep_work_dir.mkdir(parents=True, exist_ok=True)

    # Preserve existing translations unless --force is passed
    existing_pt: dict[int, str] = {}
    existing_kar_pt: dict[tuple[str, float, str], str] = {}
    if not args.force:
        for chunk_path in sorted(ep_work_dir.glob("chunk_*.json")):
            chunk_data = json.loads(chunk_path.read_text(encoding="utf-8"))
            for item in chunk_data:
                if item.get("pt") and not item.get("skip"):
                    if item.get("karaoke_tr"):
                        sig = (str(item.get("style", "")), round(float(item["start"]), 2), str(item.get("en", "")))
                        existing_kar_pt[sig] = str(item["pt"])
                    else:
                        existing_pt[int(item["id"])] = str(item["pt"])

    for old_chunk in ep_work_dir.glob("chunk_*.json"):
        old_chunk.unlink()

    entries = []
    for ev in events_list:
        pt_val = ev["pt"]
        if not ev["skip"]:
            if ev.get("karaoke_tr"):
                sig = (str(ev.get("style", "")), round(float(ev["start"]), 2), str(ev.get("en", "")))
                if sig in existing_kar_pt:
                    pt_val = existing_kar_pt[sig]
            elif ev["id"] in existing_pt:
                pt_val = existing_pt[ev["id"]]
        entry_obj = {
            "id": ev["id"],
            "kind": ev["kind"],
            "start": ev["start"],
            "end": ev["end"],
            "style": ev["style"],
            "actor": ev["actor"],
            "leading_tags": ev["leading_tags"],
            "inline_tags": ev["inline_tags"],
            "en": ev["en"],
            "pt": pt_val,
            "skip": ev["skip"],
            "omit_web": ev.get("omit_web", False),
            "collapsed": ev.get("collapsed", False),
            "fields": ev.get("fields", {}),
            "raw_line": ev.get("raw_line", ""),
        }
        if ev.get("karaoke_tr"):
            entry_obj["karaoke_tr"] = True
        entries.append(entry_obj)

    chunk_size = max(1, int(args.chunk_size))
    chunk_files: list[str] = []
    for idx in range(0, len(entries), chunk_size):
        chunk_num = (idx // chunk_size) + 1
        fname = f"chunk_{chunk_num:03d}.json"
        chunk_path = ep_work_dir / fname
        chunk_path.write_text(
            json.dumps(entries[idx : idx + chunk_size], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        chunk_files.append(fname)

    meta = {
        "episodeKey": episode_key,
        "arc": arc_obj["id"],
        "epSlug": ep_obj["epSlug"],
        "episode": ep_obj["episode"],
        "label": ep_obj["label"],
        "sourceFile": str(src_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "format": parsed["format"],
        "header_lines": parsed["header_lines"],
        "event_fields": parsed["event_fields"],
        "style_alignments": parsed["style_alignments"],
        "style_fontsizes": parsed.get("style_fontsizes", {}),
        "chunks": chunk_files,
        "totalEvents": len(entries),
        "translatableEvents": sum(1 for e in entries if not e["skip"]),
    }
    (ep_work_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ctx_path, block_count = _write_script_context(ep_work_dir, episode_key, entries)

    print(
        f"Preparação concluída para {episode_key}: {meta['translatableEvents']} falas em "
        f"{len(chunk_files)} arquivo(s) ({block_count} blocos contínuos em {ctx_path.name}) em {ep_work_dir}"
    )
    return 0


def _load_work(episode_key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ep_work_dir = WORK_DIR / episode_key
    meta_path = ep_work_dir / "meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"Trabalho não encontrado em {meta_path}. Execute `prepare` primeiro.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []
    for cname in meta["chunks"]:
        cpath = ep_work_dir / cname
        if not cpath.is_file():
            raise SystemExit(f"Chunk ausente: {cpath}")
        entries.extend(json.loads(cpath.read_text(encoding="utf-8")))
    return meta, entries


def _entry_context_kind(item: dict[str, Any]) -> str:
    style = str(item.get("style", ""))
    if item.get("karaoke_tr") or re.search(r"\b(?:op\d*|ed\d*|song|lyric|insert)\b", style, re.IGNORECASE):
        return "song"
    if re.search(r"(?:sign|title|caption|note)", style, re.IGNORECASE) or SIGN_TAG_RE.search(
        str(item.get("leading_tags", ""))
    ):
        return "sign"
    return "dialogue"


def _group_context_blocks(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group chronologically ordered active entries into continuous phrases or song stanzas."""
    active = [e for e in entries if not e.get("skip") and str(e.get("en", "")).strip()]
    active.sort(key=lambda e: (float(e["start"]), float(e["end"]), int(e["id"])))

    blocks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for item in active:
        if not cur:
            cur = [item]
            continue

        prev = cur[-1]
        prev_kind = _entry_context_kind(prev)
        curr_kind = _entry_context_kind(item)
        if prev_kind != curr_kind:
            blocks.append(cur)
            cur = [item]
            continue

        gap = float(item["start"]) - float(prev["end"])
        prev_plain = _normalize_ass_plain(str(prev["en"])).replace(r"\N", " ").strip()
        curr_plain = _normalize_ass_plain(str(item["en"])).replace(r"\N", " ").strip()

        if curr_kind == "song":
            same_song = _song_style_prefix(str(prev.get("style", ""))) == _song_style_prefix(str(item.get("style", "")))
            should_join = same_song and len(cur) < 4 and gap <= 4.0
        elif curr_kind == "dialogue":
            prev_actor = str(prev.get("actor", "")).strip()
            curr_actor = str(item.get("actor", "")).strip()
            same_actor = (not curr_actor) or (curr_actor == prev_actor)
            open_sentence = bool(prev_plain) and prev_plain[-1] not in '.?!"”'
            starts_cont = bool(curr_plain) and (curr_plain[0].islower() or curr_plain.startswith("..."))
            should_join = same_actor and len(cur) < 5 and gap <= 2.0 and (open_sentence or starts_cont or gap <= 0.6)
        else:
            should_join = False

        if should_join:
            cur.append(item)
        else:
            blocks.append(cur)
            cur = [item]

    if cur:
        blocks.append(cur)
    return blocks


def _write_script_context(ep_work_dir: Path, episode_key: str, entries: list[dict[str, Any]]) -> tuple[Path, int]:
    blocks = _group_context_blocks(entries)
    lines = [
        f"# Contexto contínuo de tradução — {episode_key}",
        "# Edite cada linha `PT: [id] texto 1 / [id] texto 2` preservando os marcadores `[id]` e `\\N` se quiser quebra manual.",
        "# Depois aplique com: python scripts/sub_pipeline.py import-context --arc <arc> --ep <ep>",
        "",
    ]
    for idx, grp in enumerate(blocks, start=1):
        kind = _entry_context_kind(grp[0])
        t_start = _format_ass_time(float(grp[0]["start"]))
        t_end = _format_ass_time(max(float(x["end"]) for x in grp))
        actors = []
        for x in grp:
            a = str(x.get("actor", "")).strip()
            if a and a not in actors:
                actors.append(a)
        tag = f"Música: {grp[0].get('style', '')}" if kind == "song" else (", ".join(actors) or grp[0].get("style", "Default"))

        en_parts = []
        pt_parts = []
        for x in grp:
            en_clean = re.sub(r"[ \t]+", " ", str(x["en"]).replace(r"\N", " ").replace(r"\n", " ")).strip()
            pt_val = str(x.get("pt") or "").strip() or "..."
            en_parts.append(f"[{x['id']}] {en_clean}")
            pt_parts.append(f"[{x['id']}] {pt_val}")

        lines.append(f"=== BLOCO {idx:03d} ({t_start} -> {t_end}) | {tag} ===")
        lines.append("EN: " + " / ".join(en_parts))
        lines.append("PT: " + " / ".join(pt_parts))
        lines.append("")

    out_path = ep_work_dir / "script_context.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path, len(blocks)


def _parse_context_pt_blocks(text: str) -> dict[int, str]:
    updates: dict[int, str] = {}
    id_marker_re = re.compile(r"\[(\d+)\]\s*")
    for raw_line in text.splitlines():
        if not raw_line.startswith("PT:"):
            continue
        body = raw_line[3:].strip()
        matches = list(id_marker_re.finditer(body))
        for i, m in enumerate(matches):
            cid = int(m.group(1))
            seg_start = m.end()
            seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            val = re.sub(r"\s*/\s*$", "", body[seg_start:seg_end]).strip()
            if val == "...":
                continue
            updates[cid] = val
    return updates


def cmd_context(args: argparse.Namespace) -> int:
    _, ep_obj = resolve_arc_episode(args.arc, args.ep)
    episode_key = ep_obj["episodeKey"]
    _, entries = _load_work(episode_key)
    out_path, block_count = _write_script_context(WORK_DIR / episode_key, episode_key, entries)
    print(f"Contexto contínuo gerado para {episode_key}: {block_count} bloco(s) -> {out_path}")
    return 0


def cmd_import_context(args: argparse.Namespace) -> int:
    _, ep_obj = resolve_arc_episode(args.arc, args.ep)
    episode_key = ep_obj["episodeKey"]
    ep_work_dir = WORK_DIR / episode_key
    meta, _ = _load_work(episode_key)

    src_file = Path(args.file) if args.file else (ep_work_dir / "script_context.txt")
    if not src_file.is_file():
        raise SystemExit(f"Arquivo de contexto não encontrado: {src_file}")

    updates = _parse_context_pt_blocks(src_file.read_text(encoding="utf-8-sig"))
    if not updates:
        raise SystemExit(f"Nenhuma linha `PT: [id] ...` preenchida encontrada em {src_file}")

    applied = 0
    for cname in meta["chunks"]:
        cpath = ep_work_dir / cname
        data = json.loads(cpath.read_text(encoding="utf-8"))
        changed = False
        for item in data:
            if item.get("skip"):
                continue
            cid = int(item["id"])
            if cid in updates:
                if not updates[cid]:
                    raise SystemExit(f"Tradução vazia para ID {cid} em {src_file}")
                item["pt"] = updates[cid]
                applied += 1
                changed = True
        if changed:
            cpath.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Contexto aplicado para {episode_key}: {applied} legenda(s) atualizada(s) a partir de {src_file.name}")
    return 0


def validate_episode_data(meta: dict[str, Any], entries: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for item in entries:
        cid = item["id"]
        start = float(item["start"])
        end = float(item["end"])
        if start < 0 or end < start:
            errors.append(f"ID {cid}: timestamps inválidos (start={start}, end={end}).")

        if item.get("skip"):
            continue

        pt = str(item.get("pt", ""))
        en = str(item.get("en", ""))
        if not pt.strip():
            errors.append(f"ID {cid}: tradução 'pt' vazia (original: {en!r}).")
            continue

        # Check placeholders ⟪Tn⟫
        en_tokens = sorted(TOKEN_RE.findall(en))
        pt_tokens = sorted(TOKEN_RE.findall(pt))
        if en_tokens != pt_tokens:
            errors.append(f"ID {cid}: placeholders alterados (esperado {en_tokens}, obtido {pt_tokens}).")

        # Check balanced <i> / <b>
        for tag in ("i", "b"):
            opens = len(re.findall(rf"<{tag}>", pt))
            closes = len(re.findall(rf"</{tag}>", pt))
            if opens != closes:
                errors.append(f"ID {cid}: tag <{tag}> desbalanceada ({opens} aberturas, {closes} fechamentos).")

        # Check stray curly braces
        stripped_tokens = TOKEN_RE.sub("", pt)
        if "{" in stripped_tokens or "}" in stripped_tokens:
            errors.append(f"ID {cid}: chaves ASS cruas '{{' ou '}}' encontradas no texto 'pt': {pt!r}")

        # Soft readability warnings
        plain = re.sub(r"</?(?:i|b)>", "", stripped_tokens).replace(r"\N", "\n").replace(r"\n", "\n")
        for line in plain.split("\n"):
            if len(line) > 52:
                warnings.append(f"ID {cid}: linha longa ({len(line)} chars): {line!r}")
        dur = max(0.2, end - start)
        cps = len(plain.replace("\n", "")) / dur
        if cps > 25.0 and len(plain.strip()) > 8:
            warnings.append(f"ID {cid}: velocidade alta ({cps:.1f} CPS)")

    return errors, warnings


def cmd_validate(args: argparse.Namespace) -> int:
    _arc_obj, ep_obj = resolve_arc_episode(args.arc, args.ep)
    episode_key = ep_obj["episodeKey"]
    meta, entries = _load_work(episode_key)
    errors, warnings = validate_episode_data(meta, entries)

    if warnings:
        print(f"Avisos ({len(warnings)}):")
        for w in warnings[:15]:
            print(f"  [AVISO] {w}")
        if len(warnings) > 15:
            print(f"  ... e mais {len(warnings) - 15} aviso(s).")

    if errors:
        print(f"\nErros de validação ({len(errors)}):")
        for e in errors[:20]:
            print(f"  [ERRO] {e}")
        return 1

    print(f"Validação OK para {episode_key}: {meta['translatableEvents']} falas verificadas, 0 erros.")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    arc_obj, ep_obj = resolve_arc_episode(args.arc, args.ep)
    episode_key = ep_obj["episodeKey"]
    meta, entries = _load_work(episode_key)
    errors, warnings = validate_episode_data(meta, entries)
    if errors:
        for e in errors[:20]:
            print(f"[ERRO] {e}")
        raise SystemExit(f"Build abortado: {len(errors)} erro(s) encontrado(s) em {episode_key}.")

    if warnings:
        print(f"Build prosseguindo com {len(warnings)} aviso(s) de estilo/CPS.")

    TRANSLATED_DIR.mkdir(parents=True, exist_ok=True)
    style_alignments = meta.get("style_alignments", {"Default": 2})

    ext_cues: list[dict[str, Any]] = []
    vtt_blocks: list[str] = ["WEBVTT"]

    if meta["format"] == "ass":
        event_fields = meta["event_fields"]
        ass_lines = list(meta["header_lines"])

        for item in entries:
            if item.get("kind", "Dialogue").lower() == "comment":
                if item.get("raw_line"):
                    ass_lines.append(item["raw_line"])
                continue

            if item.get("skip"):
                if item.get("raw_line"):
                    ass_lines.append(item["raw_line"])
                continue

            pt_restored = _decode_ass_inline(str(item["pt"]), item.get("inline_tags", []))
            full_ass_text = f"{item.get('leading_tags', '')}{pt_restored}"

            fields_map = dict(item.get("fields", {}))
            fields_map["start"] = _format_ass_time(item["start"])
            fields_map["end"] = _format_ass_time(item["end"])
            fields_map["text"] = full_ass_text
            ordered = [str(fields_map.get(f, "")) for f in event_fields]
            ass_lines.append(f"{item['kind']}: " + ",".join(ordered))

            align, is_sign, cue_lines = _ass_text_to_runs_lines(
                full_ass_text, item.get("style", "Default"), style_alignments
            )
            if cue_lines and not item.get("omit_web"):
                ext_cues.append(
                    {
                        "start": item["start"],
                        "end": item["end"],
                        "align": align,
                        "style": item.get("style", "Default"),
                        "isSign": is_sign,
                        "lines": cue_lines,
                    }
                )

        ass_out = TRANSLATED_DIR / f"{episode_key}.ass"
        ass_out.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")

        source_rel = meta.get("sourceFile")
        if source_rel:
            raw_ass_path = PROJECT_ROOT / source_rel
            if raw_ass_path.is_file():
                ext_cues.extend(_extract_karaoke_web_cues(raw_ass_path))
    else:
        srt_blocks: list[str] = []
        cue_idx = 0
        for item in entries:
            if item.get("skip"):
                continue
            cue_idx += 1
            pt_text = str(item["pt"])
            srt_blocks.append(
                f"{cue_idx}\n{_format_srt_time(item['start'])} --> {_format_srt_time(item['end'])}\n{pt_text}"
            )
            cue_lines = _html_text_to_runs_lines(pt_text)
            if cue_lines and not item.get("omit_web"):
                ext_cues.append(
                    {
                        "start": item["start"],
                        "end": item["end"],
                        "align": 2,
                        "style": "Default",
                        "isSign": False,
                        "lines": cue_lines,
                    }
                )

        srt_out = TRANSLATED_DIR / f"{episode_key}.srt"
        srt_out.write_text("\n\n".join(srt_blocks) + ("\n" if srt_blocks else ""), encoding="utf-8")

    # Sort karaoke cues ahead of translation cues at the same timestamp so Romaji sits cleanly above PT-BR
    ext_cues.sort(key=lambda c: (c["start"], 0 if c.get("isKaraoke") else 1, c["end"]))
    dedup_cues: list[dict[str, Any]] = []
    seen_cues: set[tuple[float, float, bool, str]] = set()
    for cue in ext_cues:
        plain_text = _runs_to_plain_text(cue["lines"])
        is_k = bool(cue.get("isKaraoke"))
        sig = (round(cue["start"], 2), round(cue["end"], 2), is_k, plain_text)
        if sig in seen_cues:
            continue
        seen_cues.add(sig)
        dedup_cues.append(cue)
        if not is_k:
            vtt_blocks.append(f"{_format_vtt_time(cue['start'])} --> {_format_vtt_time(cue['end'])}\n{plain_text}")
    ext_cues = dedup_cues

    vtt_out = TRANSLATED_DIR / f"{episode_key}.vtt"
    vtt_out.write_text("\n\n".join(vtt_blocks) + "\n", encoding="utf-8")
    ext_ep_path = PROJECT_ROOT / "extension" / ep_obj["file"]
    ext_ep_path.parent.mkdir(parents=True, exist_ok=True)
    ext_payload = {
        "version": 1,
        "episodeKey": episode_key,
        "arc": arc_obj["id"],
        "episode": ep_obj["episode"],
        "language": "pt-BR",
        "sourceFormat": meta["format"],
        "cues": ext_cues,
    }
    ext_ep_path.write_text(json.dumps(ext_payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    manifest = _load_manifest()
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    for a in manifest["arcs"]:
        if a["id"] == arc_obj["id"]:
            for e in a["episodes"]:
                if e["episodeKey"] == episode_key:
                    e["available"] = True
                    e["cueCount"] = len(ext_cues)
                    e["updatedAt"] = now_iso
    _save_manifest(manifest)

    print(
        f"Build concluído para {episode_key}:\n"
        f"  - VTT: {vtt_out}\n"
        f"  - Extensão JSON: {ext_ep_path} ({len(ext_cues)} legendas)\n"
        f"  - Manifesto: {EXT_MANIFEST_PATH} ({manifest['availableEpisodes']}/{manifest['totalEpisodes']})"
    )
    return 0


def cmd_export_release(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir) if args.out_dir else (PROJECT_ROOT / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)

    ext_dir = PROJECT_ROOT / "extension"
    manifest_file = ext_dir / "manifest.json"
    version = "0.2.0"
    if manifest_file.is_file():
        version = str(json.loads(manifest_file.read_text(encoding="utf-8")).get("version", version))

    ext_zip_path = out_dir / f"rezero-perfect-ptbr-extensao-v{version}.zip"
    ext_count = 0
    with zipfile.ZipFile(ext_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for fp in sorted(ext_dir.rglob("*")):
            if not fp.is_file():
                continue
            if fp.name in (".gitkeep", ".DS_Store") or "__pycache__" in fp.parts:
                continue
            arcname = str(Path("extension") / fp.relative_to(ext_dir)).replace("\\", "/")
            zf.write(fp, arcname)
            ext_count += 1

    subs_zip_path = out_dir / "rezero-perfect-ptbr-legendas-ass-vtt.zip"
    subs_count = 0
    with zipfile.ZipFile(subs_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for doc_name in ("GLOSSARIO.md", "README.md"):
            doc_path = PROJECT_ROOT / doc_name
            if doc_path.is_file():
                zf.write(doc_path, doc_name)
                subs_count += 1
        if TRANSLATED_DIR.is_dir():
            for fp in sorted(TRANSLATED_DIR.iterdir()):
                if fp.is_file() and fp.suffix.lower() in (".ass", ".srt", ".vtt"):
                    zf.write(fp, f"legendas/{fp.name}")
                    subs_count += 1

    print(
        f"Pacotes de distribuição gerados em {out_dir}:\n"
        f"  - Extensão ({ext_count} arquivos): {ext_zip_path} ({ext_zip_path.stat().st_size / 1024:.1f} KB)\n"
        f"  - Legendas para o site ({subs_count} arquivos): {subs_zip_path} ({subs_zip_path.stat().st_size / 1024:.1f} KB)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pipeline PT-BR para Re:ZERO -Perfect Run- (extração leve, preparação, validação e build)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-manifest", help="Inicializa extension/subtitles/manifest.json com os 60 episódios.")
    p_init.set_defaults(func=cmd_init_manifest)

    p_status = sub.add_parser("status", help="Mostra o progresso de tradução dos 6 arcos.")
    p_status.set_defaults(func=cmd_status)

    p_inspect = sub.add_parser("inspect", help="Lista faixas de legenda dentro de um .mkv.")
    p_inspect.add_argument("mkv_path", help="Caminho para o arquivo .mkv")
    p_inspect.add_argument("--engine", default="auto", choices=["auto", "python", "mkvextract", "ffmpeg"])
    p_inspect.set_defaults(func=cmd_inspect)

    p_extract = sub.add_parser("extract", help="Extrai legendas do .mkv e opcionalmente apaga o .mkv após validar.")
    p_extract.add_argument("mkv_path", help="Caminho para o arquivo .mkv")
    p_extract.add_argument("--arc", required=True, help="Ex: arc01 .. arc06")
    p_extract.add_argument("--ep", required=True, help="Número do episódio (ex: 1) ou memory-snow")
    p_extract.add_argument("--track", type=int, default=None, help="Número específico da faixa de legenda (opcional)")
    p_extract.add_argument("--engine", default="auto", choices=["auto", "python", "mkvextract", "ffmpeg"])
    p_extract.add_argument("--min-cues", type=int, default=10, help="Mínimo de falas para validar extração (padrão: 10)")
    p_extract.add_argument("--delete-source", action="store_true", help="Apaga o .mkv após validar a legenda extraída")
    p_extract.set_defaults(func=cmd_extract)

    p_prepare = sub.add_parser("prepare", help="Prepara blocos JSON para tradução preservando tags ASS.")
    p_prepare.add_argument("--arc", required=True)
    p_prepare.add_argument("--ep", required=True)
    p_prepare.add_argument("--chunk-size", type=int, default=80)
    p_prepare.add_argument("--force", action="store_true", help="Sobrescreve traduções já preenchidas nos chunks")
    p_prepare.set_defaults(func=cmd_prepare)

    p_ctx = sub.add_parser("context", help="Gera script_context.txt agrupando frases contínuas e letras de música.")
    p_ctx.add_argument("--arc", required=True)
    p_ctx.add_argument("--ep", required=True)
    p_ctx.set_defaults(func=cmd_context)

    p_imp_ctx = sub.add_parser("import-context", help="Aplica traduções editadas em script_context.txt de volta aos chunks.")
    p_imp_ctx.add_argument("--arc", required=True)
    p_imp_ctx.add_argument("--ep", required=True)
    p_imp_ctx.add_argument("--file", default=None, help="Arquivo alternativo (padrão: work/<ep>/script_context.txt)")
    p_imp_ctx.set_defaults(func=cmd_import_context)

    p_val = sub.add_parser("validate", help="Valida tradução PT-BR (tags, placeholders, timestamps).")
    p_val.add_argument("--arc", required=True)
    p_val.add_argument("--ep", required=True)
    p_val.set_defaults(func=cmd_validate)

    p_build = sub.add_parser("build", help="Valida e gera arquivos finais (.ass/.srt, .vtt, JSON da extensão e manifest.json).")
    p_build.add_argument("--arc", required=True)
    p_build.add_argument("--ep", required=True)
    p_build.set_defaults(func=cmd_build)

    p_export = sub.add_parser("export-release", help="Gera arquivos .zip limpos da extensão e das legendas (.ass/.vtt) para GitHub Releases.")
    p_export.add_argument("--out-dir", default=None, help="Pasta de saída (padrão: dist/)")
    p_export.set_defaults(func=cmd_export_release)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ExtractorError as exc:
        print(f"Erro de extração: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
