'use strict';

const DEFAULT_SETTINGS = {
  enabled: true,
  fontScale: 1.0,
  bgOpacity: 0.68,
  bottomOffset: 5,
  delayMs: 0,
  showSigns: true,
  showKaraoke: true,
};

const STORAGE_KEYS = {
  SETTINGS: 'rzptSettings',
  LOCAL_SUBTITLES: 'rzptLocalSubtitles',
};

async function getSettings() {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.SETTINGS);
  return { ...DEFAULT_SETTINGS, ...(stored[STORAGE_KEYS.SETTINGS] || {}) };
}

async function saveSettings(patch) {
  const current = await getSettings();
  const next = { ...current, ...patch };
  await chrome.storage.local.set({ [STORAGE_KEYS.SETTINGS]: next });
  return next;
}

async function getLocalSubtitles() {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.LOCAL_SUBTITLES);
  return stored[STORAGE_KEYS.LOCAL_SUBTITLES] || {};
}

async function saveLocalSubtitle(payload) {
  const { episodeKey, filename, format, data } = payload || {};
  if (!episodeKey || !data || !Array.isArray(data.cues)) {
    throw new Error('Dados de legenda local inválidos.');
  }
  const current = await getLocalSubtitles();
  const next = {
    ...current,
    [episodeKey]: {
      episodeKey,
      filename: filename || `${episodeKey}.${format || 'srt'}`,
      format: format || 'srt',
      updatedAt: new Date().toISOString(),
      data,
    },
  };
  await chrome.storage.local.set({ [STORAGE_KEYS.LOCAL_SUBTITLES]: next });
  return next[episodeKey];
}

async function clearLocalSubtitle(episodeKey) {
  const current = await getLocalSubtitles();
  if (!episodeKey) {
    await chrome.storage.local.remove(STORAGE_KEYS.LOCAL_SUBTITLES);
    return {};
  }
  const next = { ...current };
  delete next[episodeKey];
  await chrome.storage.local.set({ [STORAGE_KEYS.LOCAL_SUBTITLES]: next });
  return next;
}

async function fetchExtensionJson(relPath) {
  if (
    typeof relPath !== 'string' ||
    !relPath.startsWith('subtitles/') ||
    relPath.includes('..') ||
    !relPath.endsWith('.json')
  ) {
    throw new Error(`Caminho inválido de legenda: ${relPath}`);
  }
  const url = chrome.runtime.getURL(relPath);
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Falha ao carregar ${relPath} (HTTP ${res.status})`);
  }
  return await res.json();
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      switch (message?.type) {
        case 'GET_MANIFEST': {
          const manifest = await fetchExtensionJson('subtitles/manifest.json');
          sendResponse({ ok: true, manifest });
          break;
        }
        case 'GET_BUNDLED_SUBTITLE': {
          const data = await fetchExtensionJson(message.path);
          sendResponse({ ok: true, data });
          break;
        }
        case 'GET_SETTINGS': {
          const settings = await getSettings();
          sendResponse({ ok: true, settings });
          break;
        }
        case 'SAVE_SETTINGS': {
          const settings = await saveSettings(message.patch || {});
          sendResponse({ ok: true, settings });
          break;
        }
        case 'GET_LOCAL_SUBTITLES': {
          const localSubtitles = await getLocalSubtitles();
          sendResponse({ ok: true, localSubtitles });
          break;
        }
        case 'SAVE_LOCAL_SUBTITLE': {
          const entry = await saveLocalSubtitle(message);
          sendResponse({ ok: true, entry });
          break;
        }
        case 'CLEAR_LOCAL_SUBTITLE': {
          const localSubtitles = await clearLocalSubtitle(message.episodeKey);
          sendResponse({ ok: true, localSubtitles });
          break;
        }
        default: {
          sendResponse({ ok: false, error: `Mensagem desconhecida: ${message?.type}` });
        }
      }
    } catch (err) {
      sendResponse({ ok: false, error: err?.message || String(err) });
    }
  })();
  return true;
});
