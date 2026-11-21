'use strict';

const REMOTE_BASE_URL =
  'https://raw.githubusercontent.com/CDiangell-dei/rezero-perfect-ptbr/main/extension/';
const REMOTE_MANIFEST_TTL_MS = 30 * 60 * 1000;
const REMOTE_TIMEOUT_MS = 5000;
const MAX_REMOTE_SUB_CACHE_ENTRIES = 25;

const DEFAULT_SETTINGS = {
  enabled: true,
  fontScale: 1.0,
  bgOpacity: 0.68,
  bottomOffset: 5,
  delayMs: 0,
  showSigns: true,
  showKaraoke: true,
  autoRemoteSync: true,
  boxStyle: 'box',
  textColor: '#ffffff',
  fontFamily: 'sans',
  styleColors: true,
};

const STORAGE_KEYS = {
  SETTINGS: 'rzptSettings',
  LOCAL_SUBTITLES: 'rzptLocalSubtitles',
  REMOTE_MANIFEST_CACHE: 'rzptRemoteManifestCache',
  REMOTE_SUB_CACHE: 'rzptRemoteSubCache',
};

function validateSubtitleRelPath(relPath) {
  if (
    typeof relPath !== 'string' ||
    !relPath.startsWith('subtitles/') ||
    relPath.includes('..') ||
    !relPath.endsWith('.json')
  ) {
    throw new Error(`Caminho inválido de legenda: ${relPath}`);
  }
}

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
  validateSubtitleRelPath(relPath);
  const url = chrome.runtime.getURL(relPath);
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Falha ao carregar ${relPath} (HTTP ${res.status})`);
  }
  return await res.json();
}

async function fetchRemoteJson(relPath) {
  validateSubtitleRelPath(relPath);
  const controller = new AbortController();
  const timerId = setTimeout(() => controller.abort(), REMOTE_TIMEOUT_MS);
  try {
    const res = await fetch(`${REMOTE_BASE_URL}${relPath}`, {
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return await res.json();
  } finally {
    clearTimeout(timerId);
  }
}

function isNewerTimestamp(candidateIso, baselineIso) {
  if (!candidateIso) return false;
  if (!baselineIso) return true;
  const cand = Date.parse(candidateIso);
  const base = Date.parse(baselineIso);
  if (Number.isFinite(cand) && Number.isFinite(base)) {
    return cand > base;
  }
  return String(candidateIso) > String(baselineIso);
}

function mergeManifests(bundled, remote) {
  if (!remote || !Array.isArray(remote.arcs)) {
    return bundled;
  }
  const remoteMap = new Map();
  for (const arc of remote.arcs) {
    for (const ep of arc.episodes || []) {
      remoteMap.set(ep.episodeKey, ep);
    }
  }

  let availableCount = 0;
  let totalCount = 0;
  const mergedArcs = (bundled.arcs || []).map((arc) => {
    const eps = (arc.episodes || []).map((ep) => {
      totalCount += 1;
      const rEp = remoteMap.get(ep.episodeKey);
      const useRemote =
        Boolean(rEp?.available) &&
        (!ep.available || isNewerTimestamp(rEp.updatedAt, ep.updatedAt));
      if (useRemote) {
        availableCount += 1;
        return {
          ...ep,
          available: true,
          cueCount: rEp.cueCount ?? ep.cueCount,
          updatedAt: rEp.updatedAt ?? ep.updatedAt,
          remoteAvailable: true,
        };
      }
      if (ep.available) availableCount += 1;
      return { ...ep, remoteAvailable: false };
    });
    return { ...arc, episodes: eps };
  });

  return {
    ...bundled,
    totalEpisodes: totalCount || bundled.totalEpisodes,
    availableEpisodes: availableCount,
    arcs: mergedArcs,
  };
}

async function getMergedManifest(forceRefresh = false) {
  const [bundled, settings, cachedStored] = await Promise.all([
    fetchExtensionJson('subtitles/manifest.json'),
    getSettings(),
    chrome.storage.local.get(STORAGE_KEYS.REMOTE_MANIFEST_CACHE),
  ]);

  if (!settings.autoRemoteSync && !forceRefresh) {
    return {
      manifest: bundled,
      syncInfo: { source: 'bundled', lastSyncAt: null, error: null },
    };
  }

  const cached = cachedStored[STORAGE_KEYS.REMOTE_MANIFEST_CACHE];
  const cacheFresh =
    Boolean(cached?.manifest) &&
    Number.isFinite(cached.fetchedAt) &&
    Date.now() - cached.fetchedAt < REMOTE_MANIFEST_TTL_MS;

  if (!forceRefresh && cacheFresh) {
    return {
      manifest: mergeManifests(bundled, cached.manifest),
      syncInfo: {
        source: 'cache',
        lastSyncAt: new Date(cached.fetchedAt).toISOString(),
        error: null,
      },
    };
  }

  try {
    const remoteManifest = await fetchRemoteJson('subtitles/manifest.json');
    const fetchedAt = Date.now();
    await chrome.storage.local.set({
      [STORAGE_KEYS.REMOTE_MANIFEST_CACHE]: { fetchedAt, manifest: remoteManifest },
    });
    return {
      manifest: mergeManifests(bundled, remoteManifest),
      syncInfo: {
        source: 'remote',
        lastSyncAt: new Date(fetchedAt).toISOString(),
        error: null,
      },
    };
  } catch (err) {
    const fallbackManifest = cached?.manifest
      ? mergeManifests(bundled, cached.manifest)
      : bundled;
    return {
      manifest: fallbackManifest,
      syncInfo: {
        source: cached?.manifest ? 'cache' : 'bundled',
        lastSyncAt: cached?.fetchedAt ? new Date(cached.fetchedAt).toISOString() : null,
        error: err?.message || String(err),
      },
    };
  }
}

async function getBundledOrRemoteSubtitle({ path, updatedAt = null, forceRefresh = false, remotePreferred = false }) {
  validateSubtitleRelPath(path);
  const settings = await getSettings();
  const canUseRemote = settings.autoRemoteSync !== false || forceRefresh;

  if (canUseRemote) {
    const stored = await chrome.storage.local.get(STORAGE_KEYS.REMOTE_SUB_CACHE);
    const subCache = stored[STORAGE_KEYS.REMOTE_SUB_CACHE] || {};
    const cachedEntry = subCache[path];

    if (
      !forceRefresh &&
      cachedEntry?.data?.cues &&
      (!updatedAt || !isNewerTimestamp(updatedAt, cachedEntry.updatedAt))
    ) {
      return { data: cachedEntry.data, source: 'remote' };
    }

    if (remotePreferred || forceRefresh) {
      try {
        const remoteData = await fetchRemoteJson(path);
        if (remoteData && Array.isArray(remoteData.cues)) {
          const nextCache = { ...subCache };
          nextCache[path] = {
            updatedAt: updatedAt || new Date().toISOString(),
            fetchedAt: Date.now(),
            data: remoteData,
          };
          const keys = Object.keys(nextCache);
          if (keys.length > MAX_REMOTE_SUB_CACHE_ENTRIES) {
            keys.sort((a, b) => (nextCache[a].fetchedAt || 0) - (nextCache[b].fetchedAt || 0));
            for (const oldKey of keys.slice(0, keys.length - MAX_REMOTE_SUB_CACHE_ENTRIES)) {
              delete nextCache[oldKey];
            }
          }
          try {
            await chrome.storage.local.set({ [STORAGE_KEYS.REMOTE_SUB_CACHE]: nextCache });
          } catch (_) {
            // Ignore quota errors on optional cache; return remote data directly
          }
          return { data: remoteData, source: 'remote' };
        }
      } catch (_) {
        // Fall back to bundled file below if remote fetch fails
      }
    }
  }

  const bundledData = await fetchExtensionJson(path);
  return { data: bundledData, source: 'bundled' };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      switch (message?.type) {
        case 'GET_MANIFEST': {
          const res = await getMergedManifest(Boolean(message.forceRefresh));
          sendResponse({ ok: true, manifest: res.manifest, syncInfo: res.syncInfo });
          break;
        }
        case 'SYNC_REMOTE_NOW': {
          await chrome.storage.local.remove(STORAGE_KEYS.REMOTE_SUB_CACHE);
          const res = await getMergedManifest(true);
          sendResponse({ ok: true, manifest: res.manifest, syncInfo: res.syncInfo });
          break;
        }
        case 'GET_BUNDLED_SUBTITLE': {
          const res = await getBundledOrRemoteSubtitle(message);
          sendResponse({ ok: true, data: res.data, source: res.source });
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
