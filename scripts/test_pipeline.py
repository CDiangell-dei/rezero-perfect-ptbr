#!/usr/bin/env python3
"""Unit and integration tests for the MKV subtitle extractor and PT-BR pipeline."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mkv_subtitle_extractor as mse  # noqa: E402
import sub_pipeline as sp  # noqa: E402


def _vint_size(n: int) -> bytes:
    """Encode an integer as a minimal EBML VINT data size."""
    for width in range(1, 8):
        max_val = (1 << (7 * width)) - 2  # reserve all-1s for unknown size
        if n <= max_val:
            encoded = n | (1 << (7 * width))
            return encoded.to_bytes(width, "big")
    raise ValueError(f"Size too large for EBML VINT: {n}")


def _ebml_elem(elem_id: int, payload: bytes, unknown_size: bool = False) -> bytes:
    id_len = max(1, (elem_id.bit_length() + 7) // 8)
    id_bytes = elem_id.to_bytes(id_len, "big")
    size_bytes = b"\xff" if unknown_size else _vint_size(len(payload))
    return id_bytes + size_bytes + payload


def _uint_bytes(val: int) -> bytes:
    if val == 0:
        return b"\x00"
    length = (val.bit_length() + 7) // 8
    return val.to_bytes(length, "big")


def _make_block_data(track_num: int, rel_ts: int, payload: bytes) -> bytes:
    track_vint = _vint_size(track_num)
    return track_vint + struct.pack(">hB", rel_ts, 0x00) + payload


def build_synthetic_mkv(dest: Path, cue_count: int = 12) -> Path:
    """Create a valid minimal MKV with zlib-compressed ASS track 1 + plain SRT track 2."""
    ebml_header = _ebml_elem(
        mse.ID_EBML,
        _ebml_elem(0x4286, b"\x01")
        + _ebml_elem(0x42F7, b"\x01")
        + _ebml_elem(0x42F2, b"\x04")
        + _ebml_elem(0x42F3, b"\x08")
        + _ebml_elem(0x4282, b"matroska")
        + _ebml_elem(0x4287, b"\x04")
        + _ebml_elem(0x4285, b"\x02"),
    )

    ass_private = (
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
        "Style: SignTop,Arial,44,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,"
        "0,0,1,2.5,1,8,30,30,30,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ).encode("utf-8")

    track1 = _ebml_elem(
        mse.ID_TRACK_ENTRY,
        _ebml_elem(mse.ID_TRACK_NUMBER, _uint_bytes(1))
        + _ebml_elem(mse.ID_TRACK_UID, _uint_bytes(101))
        + _ebml_elem(mse.ID_TRACK_TYPE, _uint_bytes(mse.TRACK_TYPE_SUBTITLE))
        + _ebml_elem(mse.ID_LANGUAGE, b"eng")
        + _ebml_elem(mse.ID_NAME, b"English (Japanese Audio)")
        + _ebml_elem(mse.ID_FLAG_DEFAULT, _uint_bytes(1))
        + _ebml_elem(mse.ID_FLAG_FORCED, _uint_bytes(0))
        + _ebml_elem(mse.ID_CODEC_ID, b"S_TEXT/ASS")
        + _ebml_elem(mse.ID_CODEC_PRIVATE, ass_private)
        + _ebml_elem(
            mse.ID_CONTENT_ENCODINGS,
            _ebml_elem(
                mse.ID_CONTENT_ENCODING,
                _ebml_elem(mse.ID_CONTENT_ENCODING_SCOPE, _uint_bytes(1))
                + _ebml_elem(
                    mse.ID_CONTENT_COMPRESSION,
                    _ebml_elem(mse.ID_CONTENT_COMP_ALGO, _uint_bytes(0)),
                ),
            ),
        ),
    )

    track2 = _ebml_elem(
        mse.ID_TRACK_ENTRY,
        _ebml_elem(mse.ID_TRACK_NUMBER, _uint_bytes(2))
        + _ebml_elem(mse.ID_TRACK_UID, _uint_bytes(102))
        + _ebml_elem(mse.ID_TRACK_TYPE, _uint_bytes(mse.TRACK_TYPE_SUBTITLE))
        + _ebml_elem(mse.ID_LANGUAGE, b"eng")
        + _ebml_elem(mse.ID_NAME, b"English (English Audio)")
        + _ebml_elem(mse.ID_FLAG_DEFAULT, _uint_bytes(0))
        + _ebml_elem(mse.ID_FLAG_FORCED, _uint_bytes(0))
        + _ebml_elem(mse.ID_CODEC_ID, b"S_TEXT/UTF8"),
    )

    info = _ebml_elem(mse.ID_INFO, _ebml_elem(mse.ID_TIMESTAMP_SCALE, _uint_bytes(1_000_000)))
    tracks = _ebml_elem(mse.ID_TRACKS, track1 + track2)

    # Cluster 1 (explicit size) with dummy video SimpleBlock (track 99) + ASS cues + SRT cues
    cluster1_children = bytearray()
    cluster1_children.extend(_ebml_elem(mse.ID_CLUSTER_TIMESTAMP, _uint_bytes(0)))
    cluster1_children.extend(_ebml_elem(mse.ID_SIMPLE_BLOCK, _make_block_data(99, 0, b"\x00\x01\x02\x03video")))

    sample_lines = [
        ("SignTop", r"{\an8\pos(320,40)}Lugunica Capital"),
        ("Default", r"Hello {\i1}world{\i0}!\NSecond line."),
        ("Default", r"Look {\c&HFF0000&}there!"),
        ("Default", r"{\p1}m 0 0 l 10 10{\p0}"),
    ]
    for idx in range(cue_count):
        style, text = sample_lines[idx] if idx < len(sample_lines) else ("Default", f"Dialogue line {idx + 1}.")
        ass_payload = f"{idx + 1},0,{style},,0,0,0,,{text}".encode("utf-8")
        compressed = zlib.compress(ass_payload)
        rel_ts = idx * 2500
        bg1 = _ebml_elem(
            mse.ID_BLOCK_GROUP,
            _ebml_elem(mse.ID_BLOCK, _make_block_data(1, rel_ts, compressed))
            + _ebml_elem(mse.ID_BLOCK_DURATION, _uint_bytes(2000)),
        )
        cluster1_children.extend(bg1)

        srt_payload = f"EN Dub line {idx + 1}".encode("utf-8")
        bg2 = _ebml_elem(
            mse.ID_BLOCK_GROUP,
            _ebml_elem(mse.ID_BLOCK, _make_block_data(2, rel_ts, srt_payload))
            + _ebml_elem(mse.ID_BLOCK_DURATION, _uint_bytes(1800)),
        )
        cluster1_children.extend(bg2)

    cluster1 = _ebml_elem(mse.ID_CLUSTER, bytes(cluster1_children))

    # Cluster 2 (unknown size) to verify unknown-size cluster boundary handling
    cluster2_children = (
        _ebml_elem(mse.ID_CLUSTER_TIMESTAMP, _uint_bytes(60000))
        + _ebml_elem(
            mse.ID_BLOCK_GROUP,
            _ebml_elem(
                mse.ID_BLOCK,
                _make_block_data(1, 500, zlib.compress(b"99,0,Default,,0,0,0,,Unknown size cluster cue")),
            )
            + _ebml_elem(mse.ID_BLOCK_DURATION, _uint_bytes(2000)),
        )
    )
    cluster2 = _ebml_elem(mse.ID_CLUSTER, cluster2_children, unknown_size=True)
    cues_tail = _ebml_elem(mse.ID_CUES, b"")

    segment = _ebml_elem(mse.ID_SEGMENT, info + tracks + cluster1 + cluster2 + cues_tail)
    dest.write_bytes(ebml_header + segment)
    return dest


class PipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self._orig_paths = (
            sp.PROJECT_ROOT,
            sp.RAW_EN_DIR,
            sp.TRANSLATED_DIR,
            sp.WORK_DIR,
            sp.EXT_SUBS_DIR,
            sp.EXT_MANIFEST_PATH,
        )
        sp.PROJECT_ROOT = self.root
        sp.RAW_EN_DIR = self.root / "raw_en"
        sp.TRANSLATED_DIR = self.root / "translated_ptbr"
        sp.WORK_DIR = sp.TRANSLATED_DIR / "work"
        sp.EXT_SUBS_DIR = self.root / "extension" / "subtitles"
        sp.EXT_MANIFEST_PATH = sp.EXT_SUBS_DIR / "manifest.json"

    def tearDown(self) -> None:
        (
            sp.PROJECT_ROOT,
            sp.RAW_EN_DIR,
            sp.TRANSLATED_DIR,
            sp.WORK_DIR,
            sp.EXT_SUBS_DIR,
            sp.EXT_MANIFEST_PATH,
        ) = self._orig_paths
        self.tmp.cleanup()

    def test_mkv_inspect_and_python_extract(self) -> None:
        mkv = build_synthetic_mkv(self.root / "ep1.mkv", cue_count=12)
        tracks = mse.list_subtitle_tracks(mkv)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0].track_number, 1)
        self.assertEqual(tracks[0].codec_id, "S_TEXT/ASS")
        self.assertEqual(tracks[0].comp_algo, 0)
        self.assertEqual(tracks[1].track_number, 2)
        self.assertEqual(tracks[1].codec_id, "S_TEXT/UTF8")

        out_dir = self.root / "extracted"
        outputs = mse.extract_subtitle_tracks(mkv, out_dir, engine="python")
        self.assertTrue(outputs[1].is_file())
        self.assertTrue(outputs[2].is_file())

        ass_parsed = sp.parse_ass_file(outputs[1])
        # 12 cues in cluster 1 (1 is vector drawing -> skipped) + 1 cue in unknown-size cluster 2 = 13 events, 12 translatable
        self.assertEqual(len(ass_parsed["events"]), 13)
        translatable = [e for e in ass_parsed["events"] if not e["skip"]]
        self.assertEqual(len(translatable), 12)
        self.assertEqual(translatable[0]["leading_tags"], r"{\an8\pos(320,40)}")
        self.assertEqual(translatable[1]["en"], r"Hello <i>world</i>!\NSecond line.")
        self.assertEqual(translatable[2]["en"], "Look ⟪T0⟫there!")
        self.assertEqual(translatable[2]["inline_tags"], [r"{\c&HFF0000&}"])
        self.assertEqual(translatable[-1]["start"], 60.5)

    def test_delete_source_safety_and_full_pipeline(self) -> None:
        mkv = build_synthetic_mkv(self.root / "arc01_ep01.mkv", cue_count=12)

        # 1) Default extract keeps source MKV
        rc = sp.main(["extract", str(mkv), "--arc", "arc01", "--ep", "1", "--engine", "python"])
        self.assertEqual(rc, 0)
        self.assertTrue(mkv.is_file())
        self.assertTrue((sp.RAW_EN_DIR / "arc01-ep01.ass").is_file())
        self.assertTrue((sp.RAW_EN_DIR / "arc01-ep01.track2.srt").is_file())

        # 2) Failed validation with --delete-source MUST NOT delete MKV
        with self.assertRaises(SystemExit) as ctx:
            sp.main(
                [
                    "extract",
                    str(mkv),
                    "--arc",
                    "arc01",
                    "--ep",
                    "1",
                    "--engine",
                    "python",
                    "--min-cues",
                    "999",
                    "--delete-source",
                ]
            )
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertTrue(mkv.is_file())

        # 3) Successful validation with --delete-source deletes MKV
        rc = sp.main(
            [
                "extract",
                str(mkv),
                "--arc",
                "arc01",
                "--ep",
                "1",
                "--engine",
                "python",
                "--delete-source",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertFalse(mkv.exists())

        # 4) Prepare chunks
        rc = sp.main(["prepare", "--arc", "arc01", "--ep", "1", "--chunk-size", "5"])
        self.assertEqual(rc, 0)
        work_ep = sp.WORK_DIR / "arc01-ep01"
        chunks = sorted(work_ep.glob("chunk_*.json"))
        self.assertGreaterEqual(len(chunks), 2)

        # 5) Validate fails before translating
        self.assertEqual(sp.main(["validate", "--arc", "arc01", "--ep", "1"]), 1)

        # 6) Break placeholder in first chunk and confirm validator catches it
        first_data = json.loads(chunks[0].read_text(encoding="utf-8"))
        for item in first_data:
            if not item["skip"]:
                item["pt"] = "Texto sem placeholder"
        chunks[0].write_text(json.dumps(first_data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(sp.main(["validate", "--arc", "arc01", "--ep", "1"]), 1)

        # 7) Translate all chunks properly preserving placeholders & tags
        for cpath in chunks:
            cdata = json.loads(cpath.read_text(encoding="utf-8"))
            for item in cdata:
                if item["skip"]:
                    continue
                if "⟪T0⟫" in item["en"]:
                    item["pt"] = "Olhe ⟪T0⟫ali!"
                elif "<i>world</i>" in item["en"]:
                    item["pt"] = r"Olá <i>mundo</i>!\NSegunda linha."
                else:
                    item["pt"] = f"Traduzido: {item['en']}"
            cpath.write_text(json.dumps(cdata, indent=2, ensure_ascii=False), encoding="utf-8")

        self.assertEqual(sp.main(["validate", "--arc", "arc01", "--ep", "1"]), 0)

        # 8) Re-running prepare without --force preserves existing PT translations
        rc = sp.main(["prepare", "--arc", "arc01", "--ep", "1", "--chunk-size", "80"])
        self.assertEqual(rc, 0)
        self.assertEqual(sp.main(["validate", "--arc", "arc01", "--ep", "1"]), 0)

        # 9) Build outputs (.ass, .vtt, extension json, manifest.json)
        rc = sp.main(["build", "--arc", "arc01", "--ep", "1"])
        self.assertEqual(rc, 0)

        ass_out = sp.TRANSLATED_DIR / "arc01-ep01.ass"
        vtt_out = sp.TRANSLATED_DIR / "arc01-ep01.vtt"
        ext_json = sp.EXT_SUBS_DIR / "arc01" / "ep01.json"
        self.assertTrue(ass_out.is_file())
        self.assertTrue(vtt_out.is_file())
        self.assertTrue(ext_json.is_file())
        self.assertTrue(sp.EXT_MANIFEST_PATH.is_file())

        ass_text = ass_out.read_text(encoding="utf-8")
        self.assertIn(r"Olhe {\c&HFF0000&}ali!", ass_text)
        self.assertIn(r"Olá {\i1}mundo{\i0}!\NSegunda linha.", ass_text)
        self.assertIn(r"{\p1}m 0 0 l 10 10{\p0}", ass_text)

        ext_data = json.loads(ext_json.read_text(encoding="utf-8"))
        self.assertEqual(ext_data["episodeKey"], "arc01-ep01")
        self.assertEqual(len(ext_data["cues"]), 12)
        self.assertEqual(ext_data["cues"][0]["align"], 8)
        self.assertTrue(ext_data["cues"][0]["isSign"])
        self.assertEqual(
            ext_data["cues"][1]["lines"],
            [
                [
                    {"text": "Olá ", "i": False, "b": False},
                    {"text": "mundo", "i": True, "b": False},
                    {"text": "!", "i": False, "b": False},
                ],
                [{"text": "Segunda linha.", "i": False, "b": False}],
            ],
        )

        manifest = json.loads(sp.EXT_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["totalEpisodes"], 60)
        self.assertEqual(manifest["availableEpisodes"], 1)
        self.assertTrue(manifest["arcs"][0]["episodes"][0]["available"])
        self.assertEqual(manifest["arcs"][0]["episodes"][0]["cueCount"], 12)

    def test_real_world_ass_fixes_and_collapsing(self) -> None:
        # 1) CodecPrivate with pre-muxed late Dialogue line merged & sorted with earlier Cluster cue
        cp = (
            "[Script Info]\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, Alignment\n"
            "Style: Default,Arial,54,2\nStyle: Signs,Arial,60,2\nStyle: Re:Zero OP - Romaji,Arial,60,8\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:05:00.00,0:05:02.00,Default,,0,0,0,,Late embedded line\n"
        ).encode("utf-8")
        tr = mse.TrackInfo(
            track_number=1,
            track_uid=1,
            codec_id="S_TEXT/ASS",
            language="eng",
            name="English (Japanese Audio)",
            is_default=True,
            is_forced=False,
            codec_private=cp,
        )
        raw_ass = self.root / "merged.ass"
        mse._write_track_file(
            tr,
            [mse._RawCue(start_ms=1000, end_ms=3000, payload=b"1,0,Default,,0,0,0,,Early cue", seq=1)],
            raw_ass,
        )
        lines = [ln for ln in raw_ass.read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")]
        self.assertEqual(len(lines), 2)
        self.assertIn("Early cue", lines[0])
        self.assertIn("Late embedded line", lines[1])

        # 2) Leading {\i1}, {\an8\i1}, unclosed {\i1}, Romaji skip, and 60-frame burst collapse
        sp.RAW_EN_DIR.mkdir(parents=True, exist_ok=True)
        ass_lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, Alignment",
            "Style: Default,Arial,54,2",
            "Style: Signs,Arial,60,2",
            "Style: Re:Zero OP - Romaji,Arial,60,8",
            "Style: Re:Zero OP - English,Arial,60,2",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\i1}Why's she here?{\i0}",
            r"Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\an8\i1}Flashback line{\i0}",
            r"Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,{\i1}Unclosed italic",
            "Dialogue: 0,0:00:10.00,0:00:12.00,Re:Zero OP - Romaji,,0,0,0,,kono sekai de",
        ]
        for f_idx in range(60):
            s_t = sp._format_ass_time(20.0 + f_idx * 0.04)
            e_t = sp._format_ass_time(20.0 + (f_idx + 1) * 0.04)
            ass_lines.append(
                f"Dialogue: 0,{s_t},{e_t},Signs,Title,0,0,0,,{{\\pos(900,900)\\fscy{100 + f_idx * 0.1:.1f}}}Reunion with the   Witch"
            )
            ass_lines.append(
                f"Dialogue: 0,{s_t},{e_t},Re:Zero OP - English,,0,0,0,,{{\\fad(50,50)}}Trapped in my past"
            )

        (sp.RAW_EN_DIR / "arc01-ep02.ass").write_text("\n".join(ass_lines) + "\n", encoding="utf-8")
        self.assertEqual(sp.main(["prepare", "--arc", "arc01", "--ep", "2", "--chunk-size", "80"]), 0)

        meta, entries = sp._load_work("arc01-ep02")
        translatable = [e for e in entries if not e["skip"]]
        # 3 Default lines + 1 collapsed Sign + 1 collapsed OP-English = 5 translatable!
        self.assertEqual(len(translatable), 5)
        self.assertEqual(translatable[0]["en"], "<i>Why's she here?</i>")
        self.assertEqual(translatable[1]["leading_tags"], r"{\an8}")
        self.assertEqual(translatable[1]["en"], "<i>Flashback line</i>")
        self.assertEqual(translatable[2]["en"], "<i>Unclosed italic</i>")
        self.assertEqual(translatable[3]["en"], "Reunion with the Witch")
        self.assertEqual(translatable[4]["en"], "Trapped in my past")

        romaji_items = [e for e in entries if e["style"] == "Re:Zero OP - Romaji"]
        self.assertEqual(len(romaji_items), 1)
        self.assertTrue(romaji_items[0]["skip"])
        self.assertTrue(romaji_items[0]["omit_web"])

        # Translate the 5 entries and verify validate + build omit Romaji and emit 5 cues
        chunk_path = sp.WORK_DIR / "arc01-ep02" / "chunk_001.json"
        chunk_data = json.loads(chunk_path.read_text(encoding="utf-8"))
        for item in chunk_data:
            if not item["skip"]:
                item["pt"] = item["en"].replace("Why's she here?", "Por que ela está aqui?").replace(
                    "Reunion with the Witch", "Reencontro com a Bruxa"
                )
        chunk_path.write_text(json.dumps(chunk_data, indent=2, ensure_ascii=False), encoding="utf-8")

        self.assertEqual(sp.main(["validate", "--arc", "arc01", "--ep", "2"]), 0)
        self.assertEqual(sp.main(["build", "--arc", "arc01", "--ep", "2"]), 0)

        ext_data = json.loads((sp.EXT_SUBS_DIR / "arc01" / "ep02.json").read_text(encoding="utf-8"))
        # 5 translated PT-BR cues + 1 Romaji karaoke cue
        self.assertEqual(len(ext_data["cues"]), 6)
        karaoke_cues = [c for c in ext_data["cues"] if c.get("isKaraoke")]
        self.assertEqual(len(karaoke_cues), 1)
        self.assertEqual(karaoke_cues[0]["align"], 8)
        self.assertEqual(karaoke_cues[0]["lines"][0][0]["text"], "kono sekai de")
        vtt_text = (sp.TRANSLATED_DIR / "arc01-ep02.vtt").read_text(encoding="utf-8")
        self.assertNotIn("kono sekai de", vtt_text)
        self.assertIn("Reencontro com a Bruxa", vtt_text)

    def test_karaoke_extraction_formats(self) -> None:
        ass_path = self.root / "karaoke_sample.ass"
        ass_content = "\n".join(
            [
                "[Script Info]",
                "ScriptType: v4.00+",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, Alignment",
                "Style: NativeKaraoke - Romaji,Arial,64,8",
                "Style: ED - Romaji,Adobe Caslon Pro Bold,98,7",
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
                r"Dialogue: 0,0:00:10.00,0:00:12.00,NativeKaraoke - Romaji,,0,0,0,,{\k50}ki{\k50}mi {\k100}to",
                r"Dialogue: 0,0:01:00.00,0:01:00.30,ED - Romaji,,0,0,0,,{\pos(100,90)\fad(200,0)\bord0}oh,",
                r"Dialogue: 0,0:01:00.00,0:01:00.60,ED - Romaji,,0,0,0,,{\pos(220,90)\fad(200,0)\bord0}please",
                r"Dialogue: 0,0:01:00.00,0:01:00.90,ED - Romaji,,0,0,0,,{\pos(370,90)\fad(200,0)\bord0}stay",
                r"Dialogue: 0,0:01:01.20,0:01:01.50,ED - Romaji,,0,0,0,,{\pos(100,90)\fad(0,200)\bord0}oh,",
                # Regression: mixed native \k + redundant animation frames / empty template marker
                r"Dialogue: 0,0:02:00.00,0:02:02.00,Mixed - Romaji,,0,0,0,,{OP First Frame}",
                r"Dialogue: 0,0:02:00.00,0:02:02.00,Mixed - Romaji,,0,0,0,,{\k40}yo{\k60}ru",
                r"Dialogue: 0,0:02:00.00,0:02:00.04,Mixed - Romaji,,0,0,0,,{\pos(100,90)}yoru",
                r"Dialogue: 0,0:02:00.04,0:02:00.08,Mixed - Romaji,,0,0,0,,{\pos(100,90)}yoru",
                # Regression: sparse native \k on chorus + full per-syllable \fad(200,0)+\pos frames across verses
                r"Dialogue: 0,0:03:00.00,0:03:02.00,EDMixed - Romaji,,0,0,0,,{\k50}sta{\k50}rt",
                r"Dialogue: 0,0:03:00.00,0:03:00.50,EDMixed - Romaji,,0,0,0,,{\pos(100,90)\fad(200,0)}sta",
                r"Dialogue: 0,0:03:00.00,0:03:01.00,EDMixed - Romaji,,0,0,0,,{\pos(160,90)\fad(200,0)}rt",
                r"Dialogue: 0,0:03:02.00,0:03:02.50,EDMixed - Romaji,,0,0,0,,{\pos(100,90)\fad(200,0)}en",
                r"Dialogue: 0,0:03:02.00,0:03:03.00,EDMixed - Romaji,,0,0,0,,{\pos(160,90)\fad(200,0)}d",
                # Degenerate zero-duration fade collision should be skipped cleanly
                r"Dialogue: 0,0:01:00.00,0:01:00.00,ED - Romaji,,0,0,0,,{\pos(100,90)\fad(0,200)\bord0}oh,",
            ]
        )
        ass_path.write_text(ass_content + "\n", encoding="utf-8")
        cues = sp._extract_karaoke_web_cues(ass_path)
        self.assertEqual(len(cues), 5)

        # English karaoke lines without a matching - English event synthesize translatable cues; pure Romaji does not
        parsed = sp.parse_ass_file(ass_path)
        evs = sp._collapse_animated_events(parsed["events"])
        synthesized = sp._synthesize_english_karaoke_entries(ass_path, evs, parsed["style_alignments"])
        self.assertEqual([s["en"] for s in synthesized], ["oh, please stay"])
        self.assertEqual(synthesized[0]["style"], "Default")


        native = cues[0]
        self.assertTrue(native["isKaraoke"])
        self.assertEqual(native["lines"][0][0]["text"], "kimi to")
        self.assertEqual(
            native["karaoke"],
            [{"text": "ki", "t": 10.0}, {"text": "mi ", "t": 10.5}, {"text": "to", "t": 11.0}],
        )

        ed = cues[1]
        self.assertTrue(ed["isKaraoke"])
        self.assertEqual(ed["align"], 7)
        self.assertEqual(ed["start"], 60.0)
        self.assertEqual(ed["end"], 61.5)
        self.assertEqual(ed["lines"][0][0]["text"], "oh, please stay")
        self.assertEqual(
            ed["karaoke"],
            [{"text": "oh, ", "t": 60.3}, {"text": "please ", "t": 60.6}, {"text": "stay", "t": 60.9}],
        )

    def test_export_release_zips(self) -> None:
        ext_root = self.root / "extension"
        (ext_root / "subtitles").mkdir(parents=True, exist_ok=True)
        (ext_root / "manifest.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
        (ext_root / "subtitles" / "manifest.json").write_text("{}", encoding="utf-8")
        (ext_root / "subtitles" / ".gitkeep").write_text("", encoding="utf-8")
        sp.TRANSLATED_DIR.mkdir(parents=True, exist_ok=True)
        (sp.TRANSLATED_DIR / "arc01-ep01.ass").write_text("[Script Info]\n", encoding="utf-8")
        (sp.TRANSLATED_DIR / "arc01-ep01.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (sp.WORK_DIR / "arc01-ep01").mkdir(parents=True, exist_ok=True)
        (sp.WORK_DIR / "arc01-ep01" / "chunk_001.json").write_text("[]", encoding="utf-8")
        (self.root / "GLOSSARIO.md").write_text("# Glossario", encoding="utf-8")

        out_dir = self.root / "dist_out"
        self.assertEqual(sp.main(["export-release", "--out-dir", str(out_dir)]), 0)
        ext_zip = out_dir / "rezero-perfect-ptbr-extensao-v9.9.9.zip"
        subs_zip = out_dir / "rezero-perfect-ptbr-legendas-ass-vtt.zip"
        self.assertTrue(ext_zip.is_file())
        self.assertTrue(subs_zip.is_file())

        with zipfile.ZipFile(ext_zip, "r") as zf:
            names = set(zf.namelist())
            self.assertIn("extension/manifest.json", names)
            self.assertNotIn("extension/subtitles/.gitkeep", names)
        with zipfile.ZipFile(subs_zip, "r") as zf:
            names = set(zf.namelist())
            self.assertIn("GLOSSARIO.md", names)
            self.assertIn("legendas/arc01-ep01.ass", names)
            self.assertIn("legendas/arc01-ep01.vtt", names)
            self.assertFalse(any("chunk_001" in n for n in names))

    def test_continuous_context_roundtrip(self) -> None:
        sp.RAW_EN_DIR.mkdir(parents=True, exist_ok=True)
        ass_path = sp.RAW_EN_DIR / "arc01-ep02.ass"
        ass_content = "\n".join(
            [
                "[Script Info]",
                "ScriptType: v4.00+",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, Alignment",
                "Style: Default,Arial,20,2",
                "Style: Re:Zero ED01 - English,Arial,20,7",
                "Style: Re:Zero ED01 - Romaji,Arial,20,7",
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
                "Dialogue: 0,0:00:01.00,0:00:03.00,Default,Subaru,0,0,0,,Even if I fail,",
                "Dialogue: 0,0:00:03.10,0:00:05.00,Default,Subaru,0,0,0,,I won't give up.",
                "Dialogue: 0,0:00:10.00,0:00:12.00,Default,Emilia,0,0,0,,Stay safe.",
                r"Dialogue: 0,0:01:00.00,0:01:02.00,Re:Zero ED01 - Romaji,,0,0,0,,{\k50}no, don't {\k50}give up",
                r"Dialogue: 0,0:01:02.20,0:01:04.50,Re:Zero ED01 - Romaji,,0,0,0,,{\k50}this endless {\k50}dead end",
            ]
        )
        ass_path.write_text(ass_content + "\n", encoding="utf-8")

        self.assertEqual(sp.main(["prepare", "--arc", "arc01", "--ep", "2"]), 0)
        ctx_path = sp.WORK_DIR / "arc01-ep02" / "script_context.txt"
        self.assertTrue(ctx_path.is_file())
        ctx_text = ctx_path.read_text(encoding="utf-8")
        # Consecutive dialogue lines by Subaru are grouped cleanly into one block
        self.assertIn("[1] Even if I fail, / [2] I won't give up.", ctx_text)
        # Consecutive song lines (synthesized from English karaoke) are grouped in one block
        self.assertIn("[6] no, don't give up / [7] this endless dead end", ctx_text)

        edited = (
            ctx_text.replace(
                "PT: [1] ... / [2] ...",
                r"PT: [1] Mesmo que eu falhe,\Ne eu não / [2] vou desistir.",
            )
            .replace("PT: [3] ...", "PT: [3] Fique seguro.")
            .replace(
                "PT: [6] ... / [7] ...",
                "PT: [6] não desista / [7] neste beco sem saída",
            )
        )
        ctx_path.write_text(edited, encoding="utf-8")

        self.assertEqual(sp.main(["import-context", "--arc", "arc01", "--ep", "2"]), 0)
        self.assertEqual(sp.main(["validate", "--arc", "arc01", "--ep", "2"]), 0)

        chunk_data = json.loads((sp.WORK_DIR / "arc01-ep02" / "chunk_001.json").read_text(encoding="utf-8"))
        by_id = {item["id"]: item["pt"] for item in chunk_data}
        self.assertEqual(by_id[1], r"Mesmo que eu falhe,\Ne eu não")
        self.assertEqual(by_id[2], "vou desistir.")
        self.assertEqual(by_id[6], "não desista")
        self.assertEqual(by_id[7], "neste beco sem saída")


if __name__ == "__main__":
    unittest.main()
