(function (root) {
  'use strict';

  function stripBom(str) {
    return String(str || '').replace(/^\uFEFF/, '');
  }

  function parseAssTimestamp(raw) {
    const parts = String(raw || '').trim().split(':');
    if (parts.length !== 3) return NaN;
    const h = Number.parseInt(parts[0], 10);
    const m = Number.parseInt(parts[1], 10);
    const secParts = parts[2].split('.');
    const s = Number.parseInt(secParts[0], 10);
    const cs = secParts[1] ? Number.parseInt(secParts[1].padEnd(2, '0').slice(0, 2), 10) : 0;
    if ([h, m, s, cs].some(Number.isNaN)) return NaN;
    return Math.round((h * 3600 + m * 60 + s + cs / 100) * 1000) / 1000;
  }

  function parseSrtOrVttTimestamp(raw) {
    const cleaned = String(raw || '').trim().replace(',', '.');
    const parts = cleaned.split(':');
    let h = 0;
    let m = 0;
    let secStr = '0';
    if (parts.length === 2) {
      m = Number.parseInt(parts[0], 10);
      secStr = parts[1];
    } else if (parts.length === 3) {
      h = Number.parseInt(parts[0], 10);
      m = Number.parseInt(parts[1], 10);
      secStr = parts[2];
    } else {
      return NaN;
    }
    const secParts = secStr.split('.');
    const s = Number.parseInt(secParts[0], 10);
    const ms = secParts[1] ? Number.parseInt(secParts[1].padEnd(3, '0').slice(0, 3), 10) : 0;
    if ([h, m, s, ms].some(Number.isNaN)) return NaN;
    return Math.round((h * 3600 + m * 60 + s + ms / 1000) * 1000) / 1000;
  }

  function mergeRuns(runs) {
    const out = [];
    for (const run of runs) {
      if (!run.text) continue;
      const last = out[out.length - 1];
      if (last && last.i === run.i && last.b === run.b) {
        last.text += run.text;
      } else {
        out.push({ text: run.text, i: Boolean(run.i), b: Boolean(run.b) });
      }
    }
    return out;
  }

  function parseAssLineRuns(rawLine) {
    const tokenRe = /\{([^}]*)\}|<(\/?)(i|b)\b[^>]*>/gi;
    const runs = [];
    let italic = false;
    let bold = false;
    let lastIndex = 0;

    let match;
    while ((match = tokenRe.exec(rawLine)) !== null) {
      if (match.index > lastIndex) {
        runs.push({ text: rawLine.slice(lastIndex, match.index), i: italic, b: bold });
      }
      if (match[1] !== undefined) {
        const body = match[1];
        if (body.includes('\\i1')) italic = true;
        if (body.includes('\\i0') || body.trim() === '\\r') italic = false;
        if (body.includes('\\b1')) bold = true;
        if (body.includes('\\b0') || body.trim() === '\\r') bold = false;
      } else {
        const closing = match[2] === '/';
        const tag = match[3].toLowerCase();
        if (tag === 'i') italic = !closing;
        if (tag === 'b') bold = !closing;
      }
      lastIndex = tokenRe.lastIndex;
    }

    if (lastIndex < rawLine.length) {
      runs.push({ text: rawLine.slice(lastIndex), i: italic, b: bold });
    }
    return mergeRuns(runs);
  }

  function parseHtmlLineRuns(rawLine) {
    const tagRe = /<(\/?)(i|b|u|s|font|c(?:\.[^>]*)?)\b[^>]*>/gi;
    const runs = [];
    let italic = false;
    let bold = false;
    let lastIndex = 0;

    let match;
    while ((match = tagRe.exec(rawLine)) !== null) {
      if (match.index > lastIndex) {
        runs.push({ text: rawLine.slice(lastIndex, match.index), i: italic, b: bold });
      }
      const closing = match[1] === '/';
      const tag = match[2].toLowerCase();
      if (tag === 'i') italic = !closing;
      if (tag === 'b') bold = !closing;
      lastIndex = tagRe.lastIndex;
    }

    if (lastIndex < rawLine.length) {
      runs.push({ text: rawLine.slice(lastIndex), i: italic, b: bold });
    }
    return mergeRuns(runs);
  }

  function parseASS(text) {
    const rawLines = stripBom(text).replace(/\r\n?/g, '\n').split('\n');
    let section = '';
    let styleFields = [];
    let eventFields = [];
    const styleAlignments = new Map();
    const cues = [];

    for (const line of rawLines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
        section = trimmed.slice(1, -1).trim().toLowerCase();
        continue;
      }

      if (section === 'v4+ styles' || section === 'v4 styles') {
        if (/^format:/i.test(trimmed)) {
          styleFields = trimmed
            .slice(trimmed.indexOf(':') + 1)
            .split(',')
            .map((s) => s.trim().toLowerCase());
        } else if (/^style:/i.test(trimmed) && styleFields.length > 0) {
          const rawVal = trimmed.slice(trimmed.indexOf(':') + 1);
          const vals = splitMax(rawVal, ',', styleFields.length - 1);
          const row = {};
          styleFields.forEach((f, idx) => {
            row[f] = (vals[idx] || '').trim();
          });
          if (row.name) {
            const alignVal = Number.parseInt(row.alignment || '2', 10);
            styleAlignments.set(row.name, Number.isFinite(alignVal) ? alignVal : 2);
          }
        }
        continue;
      }

      if (section === 'events') {
        if (/^format:/i.test(trimmed)) {
          eventFields = trimmed
            .slice(trimmed.indexOf(':') + 1)
            .split(',')
            .map((s) => s.trim().toLowerCase());
          continue;
        }
        if (/^dialogue:/i.test(trimmed)) {
          const fields =
            eventFields.length > 0
              ? eventFields
              : ['layer', 'start', 'end', 'style', 'name', 'marginl', 'marginr', 'marginv', 'effect', 'text'];
          const rawPayload = trimmed.slice(trimmed.indexOf(':') + 1).trimStart();
          const vals = splitMax(rawPayload, ',', fields.length - 1);
          const row = {};
          fields.forEach((f, idx) => {
            row[f] = vals[idx] || '';
          });

          const start = parseAssTimestamp(row.start);
          const end = parseAssTimestamp(row.end);
          if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) continue;

          const rawText = row.text || '';
          if (/\{[^}]*\\p[1-9]\d*/.test(rawText)) continue; // skip vector drawings

          const style = (row.style || 'Default').trim();
          let align = styleAlignments.has(style) ? styleAlignments.get(style) : 2;
          let isSign = /(?:sign|title|note|caption|top)/i.test(style);

          const overrideBlocks = rawText.match(/\{([^}]*)\}/g) || [];
          for (const block of overrideBlocks) {
            const anMatches = block.match(/\\an([1-9])/g);
            if (anMatches && anMatches.length > 0) {
              const lastAn = anMatches[anMatches.length - 1];
              align = Number.parseInt(lastAn.slice(3), 10);
            }
            if (/\\(?:pos|move)\b/.test(block)) {
              isSign = true;
            }
          }
          if (align >= 7 && align <= 9) {
            isSign = true;
          }

          const cleanedText = rawText
            .replace(/\\N/g, '\n')
            .replace(/\\n/g, '\n')
            .replace(/\\h/g, '\u00A0');

          const parsedLines = cleanedText
            .split('\n')
            .map((ln) => parseAssLineRuns(ln))
            .filter((runs) => runs.length > 0);

          if (parsedLines.length === 0) continue;

          cues.push({
            start,
            end,
            align,
            style,
            isSign,
            lines: parsedLines,
          });
        }
      }
    }

    cues.sort((a, b) => a.start - b.start || a.end - b.end);
    return { format: 'ass', cues };
  }

  function splitMax(str, sep, maxSplits) {
    const result = [];
    let remaining = str;
    for (let i = 0; i < maxSplits; i++) {
      const idx = remaining.indexOf(sep);
      if (idx === -1) break;
      result.push(remaining.slice(0, idx));
      remaining = remaining.slice(idx + sep.length);
    }
    result.push(remaining);
    return result;
  }

  function parseSRT(text) {
    const blocks = stripBom(text)
      .replace(/\r\n?/g, '\n')
      .trim()
      .split(/\n\s*\n/);
    const cues = [];

    for (const block of blocks) {
      const lines = block.split('\n').filter((l) => l.trim() !== '');
      if (lines.length < 2) continue;
      let tsIdx = 0;
      if (!lines[0].includes('-->') && lines[1].includes('-->')) {
        tsIdx = 1;
      }
      if (!lines[tsIdx].includes('-->')) continue;

      const [left, rightPart] = lines[tsIdx].split('-->');
      const right = rightPart.trim().split(/\s+/)[0];
      const start = parseSrtOrVttTimestamp(left);
      const end = parseSrtOrVttTimestamp(right);
      if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) continue;

      const bodyLines = lines
        .slice(tsIdx + 1)
        .map((ln) => parseHtmlLineRuns(ln))
        .filter((runs) => runs.length > 0);

      if (bodyLines.length === 0) continue;

      cues.push({
        start,
        end,
        align: 2,
        style: 'Default',
        isSign: false,
        lines: bodyLines,
      });
    }

    cues.sort((a, b) => a.start - b.start || a.end - b.end);
    return { format: 'srt', cues };
  }

  function parseVTT(text) {
    const cleaned = stripBom(text).replace(/\r\n?/g, '\n').trim();
    const blocks = cleaned.split(/\n\s*\n/);
    const cues = [];

    for (const block of blocks) {
      const lines = block.split('\n').filter((l) => l.trim() !== '');
      if (lines.length === 0) continue;
      const head = lines[0].trim();
      if (/^(WEBVTT|NOTE|STYLE|REGION)\b/i.test(head)) continue;

      let tsIdx = -1;
      if (lines[0].includes('-->')) {
        tsIdx = 0;
      } else if (lines.length >= 2 && lines[1].includes('-->')) {
        tsIdx = 1;
      }
      if (tsIdx === -1) continue;

      const [left, rightPart] = lines[tsIdx].split('-->');
      const rightTokens = rightPart.trim().split(/\s+/);
      const right = rightTokens[0];
      const start = parseSrtOrVttTimestamp(left);
      const end = parseSrtOrVttTimestamp(right);
      if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) continue;

      // Support optional VTT line/align settings minimally
      let align = 2;
      const settingsStr = rightTokens.slice(1).join(' ');
      if (/line:\s*(?:0|[1-2]\d)%/i.test(settingsStr) || /line:\s*0\b/i.test(settingsStr)) {
        align = 8;
      }

      const bodyLines = lines
        .slice(tsIdx + 1)
        .map((ln) => parseHtmlLineRuns(ln))
        .filter((runs) => runs.length > 0);

      if (bodyLines.length === 0) continue;

      cues.push({
        start,
        end,
        align,
        style: 'Default',
        isSign: align === 8,
        lines: bodyLines,
      });
    }

    cues.sort((a, b) => a.start - b.start || a.end - b.end);
    return { format: 'vtt', cues };
  }

  function parseSubtitle(content, filenameOrFormat) {
    const hint = String(filenameOrFormat || '').toLowerCase();
    if (hint.endsWith('.json') || hint === 'json') {
      const parsed = typeof content === 'string' ? JSON.parse(stripBom(content)) : content;
      const cues = Array.isArray(parsed?.cues) ? parsed.cues : [];
      return { format: 'json', cues };
    }
    if (hint.endsWith('.ass') || hint.endsWith('.ssa') || hint === 'ass' || hint === 'ssa') {
      return parseASS(content);
    }
    if (hint.endsWith('.vtt') || hint === 'vtt') {
      return parseVTT(content);
    }
    if (hint.endsWith('.srt') || hint === 'srt') {
      return parseSRT(content);
    }

    // Fallback sniffing
    const trimmed = stripBom(content).trimStart();
    if (trimmed.startsWith('{')) {
      const parsed = JSON.parse(trimmed);
      return { format: 'json', cues: Array.isArray(parsed?.cues) ? parsed.cues : [] };
    }
    if (/^WEBVTT\b/i.test(trimmed)) return parseVTT(content);
    if (/\[Script Info\]/i.test(trimmed) || /\[Events\]/i.test(trimmed)) return parseASS(content);
    return parseSRT(content);
  }

  function findActiveCues(cues, timeSec) {
    if (!Array.isArray(cues) || cues.length === 0 || !Number.isFinite(timeSec)) {
      return [];
    }
    const active = [];
    for (let i = 0; i < cues.length; i++) {
      const cue = cues[i];
      if (cue.start > timeSec) break;
      if (timeSec >= cue.start && timeSec < cue.end) {
        active.push(cue);
      }
    }
    return active;
  }

  function normalizeArcId(rawArc) {
    const s = String(rawArc || '').trim().toLowerCase();
    if (!s) return '';
    if (/^arc\d{2}$/.test(s)) return s;
    const m = s.match(/(\d+)/);
    if (m) {
      return `arc${String(Number.parseInt(m[1], 10)).padStart(2, '0')}`;
    }
    return s;
  }

  function normalizeEpisodeKey(arcId, rawEpisode) {
    const arc = normalizeArcId(arcId);
    const epRaw = String(rawEpisode || '').trim();
    if (!arc || !epRaw) return '';
    if (/memory\s*snow|\bova\b/i.test(epRaw)) {
      return `${arc}-memory-snow`;
    }
    const numMatch = epRaw.match(/^(?:ep\s*)?0*(\d+)$/i);
    if (numMatch) {
      const num = Number.parseInt(numMatch[1], 10);
      if (arc === 'arc02' && num === 5) {
        return 'arc02-memory-snow';
      }
      return `${arc}-ep${String(num).padStart(2, '0')}`;
    }
    const slug = epRaw
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
    return `${arc}-${slug}`;
  }

  const api = {
    parseSubtitle,
    parseASS,
    parseSRT,
    parseVTT,
    findActiveCues,
    normalizeArcId,
    normalizeEpisodeKey,
  };

  root.RZPTSubParser = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : self);
