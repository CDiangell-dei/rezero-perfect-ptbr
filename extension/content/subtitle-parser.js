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

  function assBgrToHex(rawCol, skipNearBlack = false) {
    const digits = String(rawCol || '').replace(/[^0-9A-Fa-f]/g, '');
    if (digits.length < 2) return null;
    const bbggrr = digits.padStart(6, '0').slice(-6);
    const bb = bbggrr.slice(0, 2);
    const gg = bbggrr.slice(2, 4);
    const rr = bbggrr.slice(4, 6);
    const hex = `#${rr}${gg}${bb}`.toLowerCase();
    if (hex === '#ffffff' || hex === '#000000') return null;
    if (
      skipNearBlack &&
      Math.max(Number.parseInt(rr, 16), Number.parseInt(gg, 16), Number.parseInt(bb, 16)) < 35
    ) {
      return null;
    }
    return hex;
  }

  function normalizeHtmlColor(rawCol) {
    const c = String(rawCol || '').trim().toLowerCase();
    if (/^#[0-9a-f]{3}$/.test(c)) {
      const full = `#${c[1]}${c[1]}${c[2]}${c[2]}${c[3]}${c[3]}`;
      return full === '#ffffff' || full === '#000000' ? null : full;
    }
    if (/^#[0-9a-f]{6}$/.test(c)) {
      return c === '#ffffff' || c === '#000000' ? null : c;
    }
    return null;
  }

  function classifyCueRole(styleName, isKaraoke = false) {
    const low = String(styleName || '').trim().toLowerCase();
    if (low.includes('title')) return 'title';
    if (low.includes('overlap')) return 'overlap';
    if (low.includes('flashback')) return 'flashback';
    if (
      !isKaraoke &&
      /(?:\b(?:op\d*[re]?|ed\d*[re]?|song|lyric|insert|english)\b|[-_](?:tl|e)\b)/i.test(low)
    ) {
      return 'song';
    }
    return null;
  }

  function mergeRuns(runs) {
    const out = [];
    for (const run of runs) {
      if (!run.text) continue;
      const last = out[out.length - 1];
      if (last && last.i === run.i && last.b === run.b && (last.color || null) === (run.color || null)) {
        last.text += run.text;
      } else {
        const obj = { text: run.text, i: Boolean(run.i), b: Boolean(run.b) };
        if (run.color) obj.color = run.color;
        out.push(obj);
      }
    }
    return out;
  }

  function parseAssLineRuns(rawLine, defaultItalic = false, defaultColor = null) {
    const tokenRe = /\{([^}]*)\}|<(\/?)(i|b)\b[^>]*>/gi;
    const runs = [];
    let italic = Boolean(defaultItalic);
    let bold = false;
    let color = defaultColor || null;
    let hidden = false;
    let lastIndex = 0;

    let match;
    while ((match = tokenRe.exec(rawLine)) !== null) {
      if (match.index > lastIndex && !hidden) {
        const obj = { text: rawLine.slice(lastIndex, match.index), i: italic, b: bold };
        if (color) obj.color = color;
        runs.push(obj);
      }
      if (match[1] !== undefined) {
        const body = match[1];
        if (body.includes('\\i1')) italic = true;
        if (body.includes('\\i0')) italic = false;
        if (body.includes('\\b1')) bold = true;
        if (body.includes('\\b0')) bold = false;
        if (/\\r(?![a-zA-Z0-9])/.test(body)) {
          italic = Boolean(defaultItalic);
          bold = false;
          color = defaultColor || null;
          hidden = false;
        }
        if (/\\(?:1a|alpha)&H[0-9a-f]*FF&?/i.test(body)) {
          hidden = true;
        } else if (/\\(?:1a|alpha)(?:&H00&?|(?![&0-9a-z]))/i.test(body)) {
          hidden = false;
        }
        const colMatches = body.match(/\\1?c&H([0-9a-f]{2,8})&?/gi);
        if (colMatches && colMatches.length > 0) {
          const parsedHex = assBgrToHex(colMatches[colMatches.length - 1]);
          color = parsedHex || defaultColor || null;
        } else if (/\\1?c(?![&0-9a-z])/i.test(body)) {
          color = defaultColor || null;
        }
      } else {
        const closing = match[2] === '/';
        const tag = match[3].toLowerCase();
        if (tag === 'i') italic = !closing;
        if (tag === 'b') bold = !closing;
      }
      lastIndex = tokenRe.lastIndex;
    }

    if (lastIndex < rawLine.length && !hidden) {
      const obj = { text: rawLine.slice(lastIndex), i: italic, b: bold };
      if (color) obj.color = color;
      runs.push(obj);
    }
    return mergeRuns(runs);
  }

  function parseHtmlLineRuns(rawLine) {
    const tagRe = /<(\/?)(i|b|u|s|font|c(?:\.[^>]*)?)\b([^>]*)>/gi;
    const runs = [];
    let italic = false;
    let bold = false;
    const colorStack = [];
    let lastIndex = 0;

    let match;
    while ((match = tagRe.exec(rawLine)) !== null) {
      if (match.index > lastIndex) {
        const curCol = colorStack.length > 0 ? colorStack[colorStack.length - 1] : null;
        const obj = { text: rawLine.slice(lastIndex, match.index), i: italic, b: bold };
        if (curCol) obj.color = curCol;
        runs.push(obj);
      }
      const closing = match[1] === '/';
      const tag = match[2].toLowerCase();
      const attrs = match[3] || '';
      if (tag === 'i') italic = !closing;
      if (tag === 'b') bold = !closing;
      if (tag === 'font') {
        if (closing) {
          if (colorStack.length > 0) colorStack.pop();
        } else {
          const cm = attrs.match(/\bcolor\s*=\s*["']?([^"'\s>]+)/i);
          colorStack.push(cm ? normalizeHtmlColor(cm[1]) : null);
        }
      }
      lastIndex = tagRe.lastIndex;
    }

    if (lastIndex < rawLine.length) {
      const curCol = colorStack.length > 0 ? colorStack[colorStack.length - 1] : null;
      const obj = { text: rawLine.slice(lastIndex), i: italic, b: bold };
      if (curCol) obj.color = curCol;
      runs.push(obj);
    }
    return mergeRuns(runs);
  }

  function parseASS(text) {
    const rawLines = stripBom(text).replace(/\r\n?/g, '\n').split('\n');
    let section = '';
    let styleFields = [];
    let eventFields = [];
    const styleAlignments = new Map();
    const styleItalics = new Map();
    const styleColors = new Map();
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
            const itRaw = String(row.italic || '0').trim();
            styleItalics.set(row.name, (itRaw !== '' && itRaw !== '0') || /italic/i.test(row.name));
            const pcol = assBgrToHex(row.primarycolour || '', true);
            if (pcol) styleColors.set(row.name, pcol);
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
          if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;

          const rawText = row.text || '';
          if (/\{[^}]*\\p[1-9]\d*/.test(rawText)) continue; // skip vector drawings

          const style = (row.style || 'Default').trim();
          const lowerStyle = style.toLowerCase();
          let align = styleAlignments.has(style) ? styleAlignments.get(style) : 2;
          const nonSignDialogue = /(?:^default(?:\s*[-_].*)?$|^(?:main|top|italics?(?:top)?|flashback(?:\s*italics?|top|italicstop)?|overlap(?:\s*italics?|top)?)$|\b(?:op\d*[re]?|ed\d*[re]?|song|lyric|insert)\b|[-_](?:tl|e|r)\b)/i.test(lowerStyle);
          let isSign = /(?:sign|title|note|caption)/i.test(lowerStyle);

          const overrideBlocks = rawText.match(/\{([^}]*)\}/g) || [];
          for (const block of overrideBlocks) {
            const anMatches = block.match(/\\an([1-9])/g);
            if (anMatches && anMatches.length > 0) {
              const lastAn = anMatches[anMatches.length - 1];
              align = Number.parseInt(lastAn.slice(3), 10);
            }
            if (/\\(?:pos|move)\b/.test(block) && !nonSignDialogue) {
              isSign = true;
            }
          }

          const defaultItalic = styleItalics.has(style) ? styleItalics.get(style) : /italic/i.test(lowerStyle);
          const defaultColor = styleColors.get(style) || null;

          const cleanedText = rawText
            .replace(/\\N/g, '\n')
            .replace(/\\n/g, '\n')
            .replace(/\\h/g, '\u00A0');

          const parsedLines = cleanedText
            .split('\n')
            .map((ln) => parseAssLineRuns(ln, defaultItalic, defaultColor))
            .filter((runs) => runs.length > 0);

          if (parsedLines.length === 0) continue;

          const cueObj = {
            start,
            end,
            align,
            style,
            isSign,
            lines: parsedLines,
          };
          const role = classifyCueRole(style, false);
          if (role) cueObj.role = role;
          cues.push(cueObj);
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
