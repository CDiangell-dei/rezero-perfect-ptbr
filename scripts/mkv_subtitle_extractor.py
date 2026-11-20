#!/usr/bin/env python3
"""Matroska (.mkv) subtitle track inspector & extractor.

Works with zero external binaries using a pure-Python EBML/Matroska streaming
scanner, while also supporting `mkvextract` and `ffmpeg` automatically if present.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# EBML / Matroska Element IDs
ID_EBML = 0x1A45DFA3
ID_SEGMENT = 0x18538067
ID_SEEK_HEAD = 0x114D9B74
ID_INFO = 0x1549A966
ID_TIMESTAMP_SCALE = 0x2AD7B1
ID_TRACKS = 0x1654AE6B
ID_TRACK_ENTRY = 0xAE
ID_TRACK_NUMBER = 0xD7
ID_TRACK_UID = 0x73C5
ID_TRACK_TYPE = 0x83
ID_FLAG_DEFAULT = 0x88
ID_FLAG_FORCED = 0x55AA
ID_DEFAULT_DURATION = 0x23E383
ID_NAME = 0x536E
ID_LANGUAGE = 0x22B59C
ID_LANGUAGE_IETF = 0x22B59D
ID_CODEC_ID = 0x86
ID_CODEC_PRIVATE = 0x63A2
ID_CONTENT_ENCODINGS = 0x6D80
ID_CONTENT_ENCODING = 0x6240
ID_CONTENT_ENCODING_SCOPE = 0x5032
ID_CONTENT_ENCODING_TYPE = 0x5033
ID_CONTENT_COMPRESSION = 0x5034
ID_CONTENT_COMP_ALGO = 0x4254
ID_CONTENT_COMP_SETTINGS = 0x4255

ID_CLUSTER = 0x1F43B675
ID_CLUSTER_TIMESTAMP = 0xE7
ID_SIMPLE_BLOCK = 0xA3
ID_BLOCK_GROUP = 0xA0
ID_BLOCK = 0xA1
ID_BLOCK_DURATION = 0x9B

ID_CUES = 0x1C53BB6B
ID_TAGS = 0x1254C367
ID_CHAPTERS = 0x1043A770
ID_ATTACHMENTS = 0x1941A469

TOP_LEVEL_SEGMENT_CHILDREN = {
    ID_SEEK_HEAD,
    ID_INFO,
    ID_TRACKS,
    ID_CLUSTER,
    ID_CUES,
    ID_TAGS,
    ID_CHAPTERS,
    ID_ATTACHMENTS,
}

TRACK_TYPE_SUBTITLE = 0x11

TEXT_CODEC_EXTENSIONS = {
    "S_TEXT/ASS": ".ass",
    "S_TEXT/SSA": ".ssa",
    "S_TEXT/UTF8": ".srt",
    "S_TEXT/ASCII": ".srt",
    "S_TEXT/WEBVTT": ".vtt",
}

IMAGE_CODECS = {
    "S_HDMV/PGS",
    "S_VOBSUB",
    "S_DVBSUB",
}


class ExtractorError(RuntimeError):
    """Raised when MKV parsing or subtitle extraction fails."""


@dataclass
class TrackInfo:
    track_number: int
    track_uid: int | None
    codec_id: str
    language: str
    name: str
    is_default: bool
    is_forced: bool
    default_duration_ns: int | None = None
    codec_private: bytes = b""
    comp_algo: int | None = None
    comp_settings: bytes = b""
    comp_scope: int = 1

    @property
    def is_text_subtitle(self) -> bool:
        return self.codec_id in TEXT_CODEC_EXTENSIONS

    @property
    def extension(self) -> str:
        return TEXT_CODEC_EXTENSIONS.get(self.codec_id, ".sub")


@dataclass
class _RawCue:
    start_ms: int
    end_ms: int
    payload: bytes
    seq: int


def _read_vint(f: BinaryIO, keep_marker: bool) -> tuple[int, int, bool] | None:
    """Read an EBML VINT from stream.

    Returns (value, byte_length, is_unknown_size) or None on EOF.
    """
    first_b = f.read(1)
    if not first_b:
        return None
    first = first_b[0]
    if first == 0:
        raise ExtractorError(f"Invalid EBML VINT leading zero byte at offset {f.tell() - 1}")

    length = 1
    mask = 0x80
    while (first & mask) == 0:
        mask >>= 1
        length += 1
        if length > 8:
            raise ExtractorError(f"Invalid EBML VINT length > 8 at offset {f.tell() - 1}")

    rest = f.read(length - 1)
    if len(rest) != length - 1:
        raise ExtractorError("Unexpected EOF while reading EBML VINT")

    if keep_marker:
        val = first
        for b in rest:
            val = (val << 8) | b
        return val, length, False

    val = first & (mask - 1)
    for b in rest:
        val = (val << 8) | b
    unknown_sentinel = (1 << (7 * length)) - 1
    return val, length, (val == unknown_sentinel)


def _read_vint_from_bytes(buf: bytes, offset: int = 0) -> tuple[int, int]:
    """Read an EBML VINT (marker cleared) from bytes, returning (value, new_offset)."""
    if offset >= len(buf):
        raise ExtractorError("Unexpected end of block buffer reading track VINT")
    first = buf[offset]
    if first == 0:
        raise ExtractorError("Invalid zero VINT inside block")
    length = 1
    mask = 0x80
    while (first & mask) == 0:
        mask >>= 1
        length += 1
        if length > 8:
            raise ExtractorError("Invalid VINT length > 8 inside block")
    if offset + length > len(buf):
        raise ExtractorError("Truncated VINT inside block")
    val = first & (mask - 1)
    for b in buf[offset + 1 : offset + length]:
        val = (val << 8) | b
    return val, offset + length


def _read_element_header(f: BinaryIO) -> tuple[int, int | None, int] | None:
    """Read EBML element (id, size_or_none, header_start_pos). Returns None at EOF."""
    pos = f.tell()
    id_res = _read_vint(f, keep_marker=True)
    if id_res is None:
        return None
    elem_id, _, _ = id_res
    size_res = _read_vint(f, keep_marker=False)
    if size_res is None:
        raise ExtractorError("Unexpected EOF reading EBML element size")
    size, _, is_unknown = size_res
    return elem_id, (None if is_unknown else size), pos


def _read_uint(data: bytes) -> int:
    val = 0
    for b in data:
        val = (val << 8) | b
    return val


def _read_str(data: bytes) -> str:
    return data.rstrip(b"\x00").decode("utf-8", errors="replace")


def _parse_content_encodings(f: BinaryIO, end_pos: int) -> tuple[int | None, bytes, int]:
    comp_algo: int | None = None
    comp_settings = b""
    comp_scope = 1
    while f.tell() < end_pos:
        hdr = _read_element_header(f)
        if hdr is None:
            break
        eid, size, _ = hdr
        if size is None:
            raise ExtractorError("Unsupported unknown size inside ContentEncodings")
        child_end = min(end_pos, f.tell() + size)
        if eid == ID_CONTENT_ENCODING:
            while f.tell() < child_end:
                ch = _read_element_header(f)
                if ch is None:
                    break
                ceid, csize, _ = ch
                if csize is None:
                    raise ExtractorError("Unsupported unknown size inside ContentEncoding")
                cend = min(child_end, f.tell() + csize)
                if ceid == ID_CONTENT_ENCODING_SCOPE:
                    comp_scope = _read_uint(f.read(csize))
                elif ceid == ID_CONTENT_COMPRESSION:
                    while f.tell() < cend:
                        cch = _read_element_header(f)
                        if cch is None:
                            break
                        cceid, ccsize, _ = cch
                        if ccsize is None:
                            break
                        if cceid == ID_CONTENT_COMP_ALGO:
                            comp_algo = _read_uint(f.read(ccsize))
                        elif cceid == ID_CONTENT_COMP_SETTINGS:
                            comp_settings = f.read(ccsize)
                        else:
                            f.seek(ccsize, 1)
                else:
                    f.seek(cend)
        else:
            f.seek(child_end)
    return comp_algo, comp_settings, comp_scope


def _parse_track_entry(f: BinaryIO, end_pos: int) -> TrackInfo | None:
    track_number: int | None = None
    track_uid: int | None = None
    track_type: int | None = None
    codec_id = ""
    codec_private = b""
    language = "eng"
    language_ietf = ""
    name = ""
    is_default = True
    is_forced = False
    default_duration_ns: int | None = None
    comp_algo: int | None = None
    comp_settings = b""
    comp_scope = 1

    while f.tell() < end_pos:
        hdr = _read_element_header(f)
        if hdr is None:
            break
        eid, size, _ = hdr
        if size is None:
            raise ExtractorError("Unsupported unknown size inside TrackEntry")
        child_end = min(end_pos, f.tell() + size)

        if eid == ID_TRACK_NUMBER:
            track_number = _read_uint(f.read(size))
        elif eid == ID_TRACK_UID:
            track_uid = _read_uint(f.read(size))
        elif eid == ID_TRACK_TYPE:
            track_type = _read_uint(f.read(size))
        elif eid == ID_FLAG_DEFAULT:
            is_default = bool(_read_uint(f.read(size)))
        elif eid == ID_FLAG_FORCED:
            is_forced = bool(_read_uint(f.read(size)))
        elif eid == ID_DEFAULT_DURATION:
            default_duration_ns = _read_uint(f.read(size))
        elif eid == ID_NAME:
            name = _read_str(f.read(size))
        elif eid == ID_LANGUAGE:
            language = _read_str(f.read(size)) or "und"
        elif eid == ID_LANGUAGE_IETF:
            language_ietf = _read_str(f.read(size))
        elif eid == ID_CODEC_ID:
            codec_id = _read_str(f.read(size))
        elif eid == ID_CODEC_PRIVATE:
            codec_private = f.read(size)
        elif eid == ID_CONTENT_ENCODINGS:
            comp_algo, comp_settings, comp_scope = _parse_content_encodings(f, child_end)
        else:
            f.seek(child_end)

    if track_type != TRACK_TYPE_SUBTITLE or track_number is None:
        return None

    final_lang = language_ietf or language
    if comp_algo is not None and (comp_scope & 2):
        codec_private = _decompress_payload(codec_private, comp_algo, comp_settings)

    return TrackInfo(
        track_number=track_number,
        track_uid=track_uid,
        codec_id=codec_id,
        language=final_lang,
        name=name,
        is_default=is_default,
        is_forced=is_forced,
        default_duration_ns=default_duration_ns,
        codec_private=codec_private,
        comp_algo=comp_algo,
        comp_settings=comp_settings,
        comp_scope=comp_scope,
    )


def _decompress_payload(payload: bytes, comp_algo: int | None, comp_settings: bytes) -> bytes:
    if comp_algo is None:
        return payload
    if comp_algo == 0:
        return zlib.decompress(payload)
    if comp_algo == 3:
        return comp_settings + payload
    raise ExtractorError(
        f"Unsupported Matroska ContentCompAlgo={comp_algo}. "
        "Try installing mkvextract or ffmpeg to extract this track."
    )


def _read_metadata_python(mkv_path: Path) -> tuple[int, list[TrackInfo], int, int | None]:
    """Read Segment TimestampScale, subtitle TrackInfo list, and Segment bounds."""
    with mkv_path.open("rb") as f:
        f.seek(0, 2)
        file_size = f.tell()
        f.seek(0)

        hdr = _read_element_header(f)
        if hdr is None or hdr[0] != ID_EBML:
            raise ExtractorError(f"Not a valid EBML/Matroska file: {mkv_path}")
        _, ebml_size, _ = hdr
        if ebml_size is None:
            raise ExtractorError("Invalid unknown size on EBML header")
        f.seek(f.tell() + ebml_size)

        seg_hdr = _read_element_header(f)
        if seg_hdr is None or seg_hdr[0] != ID_SEGMENT:
            raise ExtractorError(f"Missing Matroska Segment element in: {mkv_path}")
        _, seg_size, _ = seg_hdr
        seg_data_start = f.tell()
        seg_end = file_size if seg_size is None else min(file_size, seg_data_start + seg_size)

        timestamp_scale = 1_000_000
        tracks: list[TrackInfo] = []
        saw_info = False
        saw_tracks = False

        while f.tell() < seg_end:
            elem = _read_element_header(f)
            if elem is None:
                break
            eid, esize, epos = elem
            if eid == ID_CLUSTER:
                # Clusters come after Info/Tracks in virtually all MKVs; stop metadata scan once Tracks is read.
                if saw_tracks:
                    break
            if esize is None:
                # Unknown size on non-Cluster top-level element is abnormal; bail safely.
                break
            child_end = min(seg_end, f.tell() + esize)

            if eid == ID_INFO:
                saw_info = True
                while f.tell() < child_end:
                    ih = _read_element_header(f)
                    if ih is None:
                        break
                    ieid, isize, _ = ih
                    if isize is None:
                        break
                    if ieid == ID_TIMESTAMP_SCALE:
                        timestamp_scale = _read_uint(f.read(isize)) or 1_000_000
                    else:
                        f.seek(isize, 1)
            elif eid == ID_TRACKS:
                saw_tracks = True
                while f.tell() < child_end:
                    th = _read_element_header(f)
                    if th is None:
                        break
                    teid, tsize, _ = th
                    if tsize is None:
                        break
                    t_end = min(child_end, f.tell() + tsize)
                    if teid == ID_TRACK_ENTRY:
                        tinfo = _parse_track_entry(f, t_end)
                        if tinfo is not None:
                            tracks.append(tinfo)
                    f.seek(t_end)
            else:
                f.seek(child_end)

            if saw_info and saw_tracks:
                break

        return timestamp_scale, tracks, seg_data_start, seg_end


def list_subtitle_tracks(mkv_path: Path | str, engine: str = "auto") -> list[TrackInfo]:
    """List all subtitle tracks inside a Matroska file."""
    path = Path(mkv_path)
    if not path.is_file():
        raise ExtractorError(f"File not found: {path}")
    # Pure Python metadata parser is fast (milliseconds) and captures CodecPrivate + compression too.
    _scale, tracks, _, _ = _read_metadata_python(path)
    return tracks


def _format_ass_time(ms: int) -> str:
    ms = max(0, int(ms))
    cs = (ms + 5) // 10
    hours = cs // 360_000
    cs %= 360_000
    minutes = cs // 6_000
    cs %= 6_000
    seconds = cs // 100
    centis = cs % 100
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _format_srt_time(ms: int) -> str:
    ms = max(0, int(ms))
    hours = ms // 3_600_000
    ms %= 3_600_000
    minutes = ms // 60_000
    ms %= 60_000
    seconds = ms // 1_000
    millis = ms % 1_000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _format_vtt_time(ms: int) -> str:
    return _format_srt_time(ms).replace(",", ".")


def _parse_ass_time_ms(ts: str) -> int:
    parts = ts.strip().split(":")
    if len(parts) != 3:
        return 0
    try:
        h = int(parts[0])
        m = int(parts[1])
        sec_parts = parts[2].split(".")
        s = int(sec_parts[0])
        cs = int(sec_parts[1].ljust(2, "0")[:2]) if len(sec_parts) > 1 else 0
        return ((h * 3600 + m * 60 + s) * 1000) + (cs * 10)
    except ValueError:
        return 0


def _split_ass_codec_private(codec_private: bytes) -> tuple[str, list[tuple[int, int, int, str]]]:
    raw = codec_private.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        raw = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            "PlayResX: 1920\n"
            "PlayResY: 1080\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,"
            "0,0,1,2.5,1,2,30,30,30,1\n"
        )

    header_lines: list[str] = []
    embedded_rows: list[tuple[int, int, int, str]] = []
    in_events = False
    saw_events_header = False
    saw_events_format = False

    for line in raw.split("\n"):
        stripped = line.strip()
        lower = stripped.lower()
        if lower == "[events]":
            in_events = True
            if not saw_events_header:
                header_lines.append("[Events]")
                saw_events_header = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = False

        if in_events:
            if lower.startswith("format:"):
                if not saw_events_format:
                    header_lines.append(stripped)
                    saw_events_format = True
                continue
            if lower.startswith("dialogue:"):
                payload = stripped.split(":", 1)[1].lstrip()
                parts = payload.split(",", 9)
                start_ms = _parse_ass_time_ms(parts[1]) if len(parts) >= 2 else 0
                embedded_rows.append((start_ms, -1, len(embedded_rows), stripped))
                continue

        header_lines.append(line)

    while header_lines and header_lines[-1] == "":
        header_lines.pop()

    if not saw_events_header:
        header_lines.append("")
        header_lines.append("[Events]")
    if not saw_events_format:
        header_lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    return "\n".join(header_lines) + "\n", embedded_rows


def _write_track_file(track: TrackInfo, cues: list[_RawCue], out_path: Path) -> Path:
    if track.codec_id in IMAGE_CODECS:
        raise ExtractorError(
            f"Track {track.track_number} ({track.codec_id}) is image-based (PGS/VobSub) "
            "and requires OCR before text translation."
        )
    if not track.is_text_subtitle:
        raise ExtractorError(f"Unsupported subtitle codec {track.codec_id!r} on track {track.track_number}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if track.codec_id in ("S_TEXT/ASS", "S_TEXT/SSA"):
        header, embedded_rows = _split_ass_codec_private(track.codec_private)
        parsed_rows: list[tuple[int, int, int, str]] = list(embedded_rows)
        for cue in cues:
            raw_text = cue.payload.decode("utf-8", errors="replace").rstrip("\r\n\x00")
            parts = raw_text.split(",", 8)
            if len(parts) == 9:
                read_order_s, layer, style, name, ml, mr, mv, effect, text = parts
                try:
                    read_order = int(read_order_s.strip())
                except ValueError:
                    read_order = cue.seq
            else:
                read_order = cue.seq
                layer, style, name, ml, mr, mv, effect = "0", "Default", "", "0", "0", "0", ""
                text = raw_text
            line = (
                f"Dialogue: {layer},{_format_ass_time(cue.start_ms)},{_format_ass_time(cue.end_ms)},"
                f"{style},{name},{ml},{mr},{mv},{effect},{text}"
            )
            parsed_rows.append((cue.start_ms, read_order, cue.seq, line))

        parsed_rows.sort(key=lambda item: (item[0], item[1], item[2]))
        seen_lines: set[tuple[int, str]] = set()
        dedup_lines: list[str] = []
        for row in parsed_rows:
            key = (row[0], row[3])
            if key not in seen_lines:
                seen_lines.add(key)
                dedup_lines.append(row[3])
        body = "\n".join(dedup_lines)
        content = header + body + ("\n" if body else "")
        out_path.write_text(content, encoding="utf-8")
        return out_path

    if track.codec_id in ("S_TEXT/UTF8", "S_TEXT/ASCII"):
        sorted_cues = sorted(cues, key=lambda c: (c.start_ms, c.seq))
        blocks: list[str] = []
        for idx, cue in enumerate(sorted_cues, start=1):
            text = cue.payload.decode("utf-8", errors="replace").strip("\r\n\x00")
            blocks.append(
                f"{idx}\n{_format_srt_time(cue.start_ms)} --> {_format_srt_time(cue.end_ms)}\n{text}"
            )
        out_path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")
        return out_path

    if track.codec_id == "S_TEXT/WEBVTT":
        sorted_cues = sorted(cues, key=lambda c: (c.start_ms, c.seq))
        blocks = ["WEBVTT"]
        for cue in sorted_cues:
            text = cue.payload.decode("utf-8", errors="replace").strip("\r\n\x00")
            blocks.append(f"{_format_vtt_time(cue.start_ms)} --> {_format_vtt_time(cue.end_ms)}\n{text}")
        out_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        return out_path

    raise ExtractorError(f"Unhandled text subtitle codec: {track.codec_id}")


def _unpack_laced_frames(data: bytes, lacing: int) -> list[bytes]:
    if lacing == 0:
        return [data]
    if not data:
        return []
    num_frames = data[0] + 1
    pos = 1
    if lacing == 1:  # Xiph lacing
        sizes: list[int] = []
        for _ in range(num_frames - 1):
            sz = 0
            while pos < len(data):
                b = data[pos]
                pos += 1
                sz += b
                if b != 255:
                    break
            sizes.append(sz)
        frames = []
        for sz in sizes:
            frames.append(data[pos : pos + sz])
            pos += sz
        frames.append(data[pos:])
        return frames
    if lacing == 2:  # Fixed-size lacing
        rem = len(data) - pos
        sz = rem // num_frames
        return [data[pos + i * sz : pos + (i + 1) * sz] for i in range(num_frames)]
    if lacing == 3:  # EBML lacing
        sizes = []
        first_sz, pos = _read_vint_from_bytes(data, pos)
        sizes.append(first_sz)
        prev = first_sz
        for _ in range(num_frames - 2):
            raw_delta, pos = _read_vint_from_bytes(data, pos)
            # Signed EBML delta offset: find length used by checking prev advance
            # Compute signed bias for VINT of that byte width
            # Recover byte length:
            bias = (1 << (7 * _ebml_vint_width(raw_delta) - 1)) - 1
            prev = prev + (raw_delta - bias)
            sizes.append(prev)
        frames = []
        for sz in sizes:
            frames.append(data[pos : pos + sz])
            pos += sz
        frames.append(data[pos:])
        return frames
    return [data]


def _ebml_vint_width(val: int) -> int:
    for width in range(1, 9):
        if val < (1 << (7 * width)):
            return width
    return 8


def _extract_with_python(
    mkv_path: Path,
    target_tracks: dict[int, TrackInfo],
    timestamp_scale: int,
    seg_data_start: int,
    seg_end: int,
    output_dir: Path,
    filename_prefix: str = "track",
) -> dict[int, Path]:
    cues_by_track: dict[int, list[_RawCue]] = {tn: [] for tn in target_tracks}
    seq = 0

    with mkv_path.open("rb") as f:
        f.seek(seg_data_start)
        while f.tell() < seg_end:
            hdr = _read_element_header(f)
            if hdr is None:
                break
            eid, esize, _ = hdr

            if eid != ID_CLUSTER:
                if esize is None:
                    # Unexpected unknown size on non-cluster element; cannot skip safely.
                    break
                f.seek(min(seg_end, f.tell() + esize))
                continue

            cluster_end = seg_end if esize is None else min(seg_end, f.tell() + esize)
            cluster_ts = 0

            while f.tell() < cluster_end:
                ch = _read_element_header(f)
                if ch is None:
                    break
                ceid, csize, cpos = ch

                # If Cluster had unknown size, seeing a top-level Segment child means Cluster ended.
                if esize is None and ceid in TOP_LEVEL_SEGMENT_CHILDREN:
                    f.seek(cpos)
                    break
                if csize is None:
                    raise ExtractorError(f"Unsupported unknown size inside Cluster at offset {cpos}")

                child_end = min(cluster_end, f.tell() + csize)

                if ceid == ID_CLUSTER_TIMESTAMP:
                    cluster_ts = _read_uint(f.read(csize))
                elif ceid == ID_SIMPLE_BLOCK:
                    # Peek track number first to skip 99.9% of audio/video blocks immediately
                    block_start = f.tell()
                    t_res = _read_vint(f, keep_marker=False)
                    if t_res is None:
                        break
                    track_num, t_len, _ = t_res
                    if track_num in target_tracks and csize >= t_len + 3:
                        rel_ts = struct.unpack(">h", f.read(2))[0]
                        flags = f.read(1)[0]
                        lacing = (flags >> 1) & 0x03
                        raw_payload = f.read(child_end - f.tell())
                        track = target_tracks[track_num]
                        start_ns = (cluster_ts + rel_ts) * timestamp_scale
                        dur_ns = track.default_duration_ns or 2_000_000_000
                        for frame in _unpack_laced_frames(raw_payload, lacing):
                            if track.comp_algo is not None and (track.comp_scope & 1):
                                frame = _decompress_payload(frame, track.comp_algo, track.comp_settings)
                            seq += 1
                            cues_by_track[track_num].append(
                                _RawCue(
                                    start_ms=round(start_ns / 1_000_000),
                                    end_ms=round((start_ns + dur_ns) / 1_000_000),
                                    payload=frame,
                                    seq=seq,
                                )
                            )
                    else:
                        f.seek(block_start + csize)
                elif ceid == ID_BLOCK_GROUP:
                    block_rel_ts: int | None = None
                    block_track_num: int | None = None
                    block_frames: list[bytes] = []
                    block_duration_units: int | None = None
                    skip_group = False

                    while f.tell() < child_end:
                        bh = _read_element_header(f)
                        if bh is None:
                            break
                        beid, bsize, _ = bh
                        if bsize is None:
                            break
                        b_end = min(child_end, f.tell() + bsize)

                        if beid == ID_BLOCK:
                            t_res = _read_vint(f, keep_marker=False)
                            if t_res is None:
                                skip_group = True
                                break
                            track_num, t_len, _ = t_res
                            if track_num not in target_tracks:
                                skip_group = True
                                f.seek(child_end)
                                break
                            block_track_num = track_num
                            if bsize >= t_len + 3:
                                block_rel_ts = struct.unpack(">h", f.read(2))[0]
                                flags = f.read(1)[0]
                                lacing = (flags >> 1) & 0x03
                                raw_payload = f.read(b_end - f.tell())
                                block_frames = _unpack_laced_frames(raw_payload, lacing)
                        elif beid == ID_BLOCK_DURATION:
                            block_duration_units = _read_uint(f.read(bsize))
                        else:
                            f.seek(b_end)

                        f.seek(b_end)

                    if not skip_group and block_track_num is not None and block_rel_ts is not None:
                        track = target_tracks[block_track_num]
                        start_ns = (cluster_ts + block_rel_ts) * timestamp_scale
                        if block_duration_units is not None:
                            dur_ns = block_duration_units * timestamp_scale
                        elif track.default_duration_ns is not None:
                            dur_ns = track.default_duration_ns
                        else:
                            dur_ns = 2_000_000_000
                        for frame in block_frames:
                            if track.comp_algo is not None and (track.comp_scope & 1):
                                frame = _decompress_payload(frame, track.comp_algo, track.comp_settings)
                            seq += 1
                            cues_by_track[block_track_num].append(
                                _RawCue(
                                    start_ms=round(start_ns / 1_000_000),
                                    end_ms=round((start_ns + dur_ns) / 1_000_000),
                                    payload=frame,
                                    seq=seq,
                                )
                            )
                else:
                    f.seek(child_end)

                f.seek(child_end)

            if esize is not None:
                f.seek(cluster_end)

    outputs: dict[int, Path] = {}
    for tnum, track in target_tracks.items():
        out_path = output_dir / f"{filename_prefix}_{tnum}{track.extension}"
        outputs[tnum] = _write_track_file(track, cues_by_track[tnum], out_path)
    return outputs


def _extract_with_mkvextract(
    mkv_path: Path,
    target_tracks: dict[int, TrackInfo],
    output_dir: Path,
    filename_prefix: str,
) -> dict[int, Path]:
    mkvmerge_bin = shutil.which("mkvmerge")
    mkvextract_bin = shutil.which("mkvextract")
    if not mkvextract_bin:
        raise ExtractorError("mkvextract not found on PATH")

    # Map Matroska 1-based TrackNumber -> mkvmerge 0-based track ID
    id_map: dict[int, int] = {}
    if mkvmerge_bin:
        proc = subprocess.run(
            [mkvmerge_bin, "-J", str(mkv_path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        info = json.loads(proc.stdout)
        for tr in info.get("tracks", []):
            props = tr.get("properties", {})
            num = props.get("number")
            tid = tr.get("id")
            if isinstance(num, int) and isinstance(tid, int):
                id_map[num] = tid

    args = [mkvextract_bin, str(mkv_path), "tracks"]
    outputs: dict[int, Path] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    for tnum, track in target_tracks.items():
        if not track.is_text_subtitle:
            raise ExtractorError(f"Track {tnum} ({track.codec_id}) is not a text subtitle track")
        mkv_id = id_map.get(tnum, tnum - 1)
        out_path = output_dir / f"{filename_prefix}_{tnum}{track.extension}"
        args.append(f"{mkv_id}:{out_path}")
        outputs[tnum] = out_path

    subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8")
    return outputs


def _extract_with_ffmpeg(
    mkv_path: Path,
    target_tracks: dict[int, TrackInfo],
    output_dir: Path,
    filename_prefix: str,
) -> dict[int, Path]:
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        raise ExtractorError("ffmpeg/ffprobe not found on PATH")

    probe = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "s",
            str(mkv_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    streams = json.loads(probe.stdout).get("streams", [])
    # ffprobe stream index corresponds to TrackNumber - 1 in MKVs; match by stream index or tags/order
    sub_order_by_track_num: dict[int, int] = {}
    all_tracks = list_subtitle_tracks(mkv_path, engine="python")
    for sub_idx, tr in enumerate(all_tracks):
        if sub_idx < len(streams):
            sub_order_by_track_num[tr.track_number] = sub_idx

    output_dir.mkdir(parents=True, exist_ok=True)
    args = [ffmpeg_bin, "-y", "-v", "error", "-i", str(mkv_path)]
    outputs: dict[int, Path] = {}
    for tnum, track in target_tracks.items():
        if not track.is_text_subtitle:
            raise ExtractorError(f"Track {tnum} ({track.codec_id}) is not a text subtitle track")
        sub_idx = sub_order_by_track_num.get(tnum)
        if sub_idx is None:
            raise ExtractorError(f"Could not map track {tnum} in ffprobe output")
        out_path = output_dir / f"{filename_prefix}_{tnum}{track.extension}"
        args.extend(["-map", f"0:s:{sub_idx}", "-c:s", "copy", str(out_path)])
        outputs[tnum] = out_path

    subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8")
    return outputs


def extract_subtitle_tracks(
    mkv_path: Path | str,
    output_dir: Path | str,
    track_numbers: list[int] | None = None,
    engine: str = "auto",
    filename_prefix: str = "track",
) -> dict[int, Path]:
    """Extract requested text subtitle tracks from a Matroska (.mkv) file."""
    path = Path(mkv_path)
    out_dir = Path(output_dir)
    if not path.is_file():
        raise ExtractorError(f"MKV file not found: {path}")

    timestamp_scale, all_tracks, seg_data_start, seg_end = _read_metadata_python(path)
    if not all_tracks:
        raise ExtractorError(f"No subtitle tracks found inside: {path}")

    by_num = {t.track_number: t for t in all_tracks}
    if track_numbers is None:
        selected = {t.track_number: t for t in all_tracks if t.is_text_subtitle}
        if not selected:
            codecs = ", ".join(f"{t.track_number}:{t.codec_id}" for t in all_tracks)
            raise ExtractorError(f"No extractable text subtitle tracks found (found: {codecs})")
    else:
        selected = {}
        for tn in track_numbers:
            if tn not in by_num:
                available = ", ".join(str(k) for k in sorted(by_num))
                raise ExtractorError(f"Subtitle track {tn} not found. Available: {available}")
            if not by_num[tn].is_text_subtitle:
                raise ExtractorError(
                    f"Track {tn} uses codec {by_num[tn].codec_id!r}, which cannot be extracted as plain text"
                )
            selected[tn] = by_num[tn]

    chosen_engine = engine.lower()
    if chosen_engine == "auto":
        if shutil.which("mkvextract"):
            chosen_engine = "mkvextract"
        elif shutil.which("ffmpeg") and shutil.which("ffprobe"):
            chosen_engine = "ffmpeg"
        else:
            chosen_engine = "python"

    if chosen_engine == "mkvextract":
        return _extract_with_mkvextract(path, selected, out_dir, filename_prefix)
    if chosen_engine == "ffmpeg":
        return _extract_with_ffmpeg(path, selected, out_dir, filename_prefix)
    if chosen_engine == "python":
        return _extract_with_python(
            path,
            selected,
            timestamp_scale=timestamp_scale,
            seg_data_start=seg_data_start,
            seg_end=seg_end,
            output_dir=out_dir,
            filename_prefix=filename_prefix,
        )
    raise ExtractorError(f"Unknown extraction engine: {engine}")
