import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const Parser = require(path.resolve(__dirname, '../extension/content/subtitle-parser.js'));

test('parseASS handles alignments, italics/bold, line breaks, signs, and skips drawings/comments', () => {
  const sampleAss = `\uFEFF[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, Alignment
Style: Default,Arial,54,2
Style: PlacaTop,Arial,40,8

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Ignored comment
Dialogue: 0,0:00:02.00,0:00:05.50,Default,,0,0,0,,{\\fad(100,100)}Olá {\\i1}Subaru{\\i0}!\\N{\\b1}Tudo bem?{\\b0}
Dialogue: 0,0:00:03.00,0:00:06.00,PlacaTop,,0,0,0,,{\\pos(960,100)}Reino de Lugunica
Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,{\\p1}m 0 0 l 50 50{\\p0}
`;

  const res = Parser.parseSubtitle(sampleAss, 'ep01.ass');
  assert.equal(res.format, 'ass');
  assert.equal(res.cues.length, 2);

  assert.equal(res.cues[0].start, 2);
  assert.equal(res.cues[0].end, 5.5);
  assert.equal(res.cues[0].align, 2);
  assert.equal(res.cues[0].isSign, false);
  assert.deepEqual(res.cues[0].lines, [
    [
      { text: 'Olá ', i: false, b: false },
      { text: 'Subaru', i: true, b: false },
      { text: '!', i: false, b: false },
    ],
    [{ text: 'Tudo bem?', i: false, b: true }],
  ]);

  assert.equal(res.cues[1].align, 8);
  assert.equal(res.cues[1].isSign, true);
  assert.deepEqual(res.cues[1].lines, [[{ text: 'Reino de Lugunica', i: false, b: false }]]);
});

test('parseSRT handles BOM, CRLF, inline HTML tags, and strips unsupported tags', () => {
  const sampleSrt =
    '\uFEFF1\r\n00:00:10,500 --> 00:00:13,250\r\n<i>Retorno através</i> da <font color="#fff"><b>Morte</b></font>\r\n\r\n2\r\n00:00:14,000 --> 00:00:15,000\r\nSegunda fala\r\n';
  const res = Parser.parseSubtitle(sampleSrt, 'sub.srt');
  assert.equal(res.format, 'srt');
  assert.equal(res.cues.length, 2);
  assert.equal(res.cues[0].start, 10.5);
  assert.equal(res.cues[0].end, 13.25);
  assert.deepEqual(res.cues[0].lines, [
    [
      { text: 'Retorno através', i: true, b: false },
      { text: ' da ', i: false, b: false },
      { text: 'Morte', i: false, b: true },
    ],
  ]);
});

test('parseVTT handles MM:SS.mmm, HH:MM:SS.mmm, NOTE blocks, and cue settings', () => {
  const sampleVtt = `WEBVTT

NOTE comentário ignorado

01:05.250 --> 01:08.000 line:10%
Título no topo

00:02:00.000 --> 00:02:03.500 align:middle
Linha <b>normal</b>
`;
  const res = Parser.parseSubtitle(sampleVtt, 'ep.vtt');
  assert.equal(res.format, 'vtt');
  assert.equal(res.cues.length, 2);
  assert.equal(res.cues[0].start, 65.25);
  assert.equal(res.cues[0].align, 8);
  assert.equal(res.cues[0].isSign, true);
  assert.equal(res.cues[1].start, 120);
  assert.equal(res.cues[1].align, 2);
});

test('findActiveCues handles overlapping dialogue + sign cues and exact boundary semantics', () => {
  const cues = [
    { start: 1.0, end: 10.0, align: 8, isSign: true, lines: [[{ text: 'Placa', i: false, b: false }]] },
    { start: 3.0, end: 5.0, align: 2, isSign: false, lines: [[{ text: 'Fala', i: false, b: false }]] },
  ];

  assert.equal(Parser.findActiveCues(cues, 0.99).length, 0);
  assert.equal(Parser.findActiveCues(cues, 1.0).length, 1);
  assert.equal(Parser.findActiveCues(cues, 3.0).length, 2);
  assert.equal(Parser.findActiveCues(cues, 5.0).length, 1);
  assert.equal(Parser.findActiveCues(cues, 10.0).length, 0);
});

test('normalizeEpisodeKey handles numeric episodes and special OVA titles', () => {
  assert.equal(Parser.normalizeEpisodeKey('arc01', '1'), 'arc01-ep01');
  assert.equal(Parser.normalizeEpisodeKey('arc-2', 'EP 4'), 'arc02-ep04');
  assert.equal(Parser.normalizeEpisodeKey('arc02', 'Movie: Memory Snow'), 'arc02-memory-snow');
  assert.equal(Parser.normalizeEpisodeKey('arc02', '5'), 'arc02-memory-snow');
  assert.equal(Parser.normalizeEpisodeKey('arc04', '17'), 'arc04-ep17');
});
