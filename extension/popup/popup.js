'use strict';

const Parser = globalThis.RZPTSubParser;

const ui = {
  catalogBadge: document.getElementById('catalog-badge'),
  offsiteCard: document.getElementById('offsite-card'),
  onsitePanel: document.getElementById('onsite-panel'),
  openSiteBtn: document.getElementById('open-site-btn'),
  ccWarningCard: document.getElementById('cc-warning-card'),
  turnOffCcBtn: document.getElementById('turn-off-cc-btn'),
  arcLabel: document.getElementById('arc-label'),
  epStatusPill: document.getElementById('ep-status-pill'),
  episodeSelect: document.getElementById('episode-select'),
  epMeta: document.getElementById('ep-meta'),
  toggleEnabled: document.getElementById('toggle-enabled'),
  toggleSigns: document.getElementById('toggle-signs'),
  toggleKaraoke: document.getElementById('toggle-karaoke'),
  localSubInput: document.getElementById('local-sub-input'),
  clearLocalBtn: document.getElementById('clear-local-btn'),
  localFileStatus: document.getElementById('local-file-status'),
  fontScaleRange: document.getElementById('font-scale-range'),
  fontScaleVal: document.getElementById('font-scale-val'),
  bgOpacityRange: document.getElementById('bg-opacity-range'),
  bgOpacityVal: document.getElementById('bg-opacity-val'),
  bottomOffsetRange: document.getElementById('bottom-offset-range'),
  bottomOffsetVal: document.getElementById('bottom-offset-val'),
  delayMinusBtn: document.getElementById('delay-minus-btn'),
  delayPlusBtn: document.getElementById('delay-plus-btn'),
  delayResetBtn: document.getElementById('delay-reset-btn'),
  delayMsInput: document.getElementById('delay-ms-input'),
  feedbackMsg: document.getElementById('feedback-msg'),
};

const appState = {
  tabId: null,
  pageState: null,
  selectedEpisodeKey: null,
  settings: null,
  localSubtitles: {},
};

function showFeedback(text, isError = false) {
  ui.feedbackMsg.textContent = text;
  ui.feedbackMsg.classList.toggle('is-error', isError);
  ui.feedbackMsg.classList.remove('is-hidden');
}

function updateRangeFallbackProgress(inputEl) {
  if (typeof CSS !== 'undefined' && CSS.supports && CSS.supports('accent-color', '#a78bfa')) {
    return;
  }
  const min = Number(inputEl.min || 0);
  const max = Number(inputEl.max || 100);
  const val = Number(inputEl.value || 0);
  const pct = max > min ? ((val - min) / (max - min)) * 100 : 0;
  inputEl.style.setProperty('--progress', `${pct}%`);
}

function renderSettings(settings) {
  appState.settings = settings;
  ui.toggleEnabled.checked = Boolean(settings.enabled);
  ui.toggleSigns.checked = Boolean(settings.showSigns);
  ui.toggleKaraoke.checked = Boolean(settings.showKaraoke ?? true);

  const fontPct = Math.round((settings.fontScale || 1.0) * 100);
  ui.fontScaleRange.value = String(fontPct);
  ui.fontScaleVal.textContent = `${fontPct}%`;
  updateRangeFallbackProgress(ui.fontScaleRange);

  const opacityPct = Math.round((settings.bgOpacity ?? 0.68) * 100);
  ui.bgOpacityRange.value = String(opacityPct);
  ui.bgOpacityVal.textContent = `${opacityPct}%`;
  updateRangeFallbackProgress(ui.bgOpacityRange);

  const bottomPct = Math.round(settings.bottomOffset ?? 5);
  ui.bottomOffsetRange.value = String(bottomPct);
  ui.bottomOffsetVal.textContent = `${bottomPct}%`;
  updateRangeFallbackProgress(ui.bottomOffsetRange);

  ui.delayMsInput.value = String(settings.delayMs || 0);
}

function renderSelectedEpisode() {
  const episodes = appState.pageState?.episodes || [];
  const selected = episodes.find((e) => e.episodeKey === appState.selectedEpisodeKey) || episodes[0];
  if (!selected) return;

  appState.selectedEpisodeKey = selected.episodeKey;
  ui.episodeSelect.value = selected.episodeKey;

  const localEntry = appState.localSubtitles?.[selected.episodeKey];
  ui.epStatusPill.classList.remove('pill-ok', 'pill-local', 'pill-muted');

  if (localEntry) {
    const count = localEntry.data?.cues?.length || 0;
    ui.epStatusPill.textContent = 'Arquivo local';
    ui.epStatusPill.classList.add('pill-local');
    ui.epMeta.textContent = `${count} falas ativas (arquivo local)`;
    ui.localFileStatus.textContent = `Ativo: ${localEntry.filename} (${count} falas)`;
    ui.clearLocalBtn.classList.remove('is-hidden');
  } else if (selected.hasSubtitle && selected.source === 'bundled') {
    ui.epStatusPill.textContent = 'Embutida PT-BR';
    ui.epStatusPill.classList.add('pill-ok');
    ui.epMeta.textContent = `${selected.cueCount} falas prontas em PT-BR`;
    ui.localFileStatus.textContent = 'Nenhuma legenda local carregada para este episódio.';
    ui.clearLocalBtn.classList.add('is-hidden');
  } else {
    ui.epStatusPill.textContent = 'Sem legenda ainda';
    ui.epStatusPill.classList.add('pill-muted');
    ui.epMeta.textContent = 'Ainda não traduzida no pacote — carregue um arquivo local para testar.';
    ui.localFileStatus.textContent = 'Nenhuma legenda local carregada para este episódio.';
    ui.clearLocalBtn.classList.add('is-hidden');
  }
}

async function saveSettingsPatch(patch) {
  const resp = await chrome.runtime.sendMessage({ type: 'SAVE_SETTINGS', patch });
  if (resp?.ok && resp.settings) {
    renderSettings(resp.settings);
  }
}

async function refreshPageState() {
  if (!appState.tabId) return null;
  try {
    const pageState = await chrome.tabs.sendMessage(appState.tabId, { type: 'GET_PAGE_STATE' });
    if (pageState?.onSupportedPage) {
      appState.pageState = pageState;
      return pageState;
    }
  } catch (_) {
    // Tab is not on a matching /arc-* page or content script not active
  }
  appState.pageState = null;
  return null;
}

function renderPageState() {
  const ps = appState.pageState;
  if (!ps || !ps.onSupportedPage) {
    ui.offsiteCard.classList.remove('is-hidden');
    ui.onsitePanel.classList.add('is-hidden');
    return;
  }

  ui.offsiteCard.classList.add('is-hidden');
  ui.onsitePanel.classList.remove('is-hidden');

  ui.arcLabel.textContent = (ps.arcId || 'ARC').toUpperCase();
  ui.ccWarningCard.classList.toggle('is-hidden', !ps.siteCaptionsOn);

  ui.episodeSelect.replaceChildren();
  for (const ep of ps.episodes || []) {
    const opt = document.createElement('option');
    opt.value = ep.episodeKey;
    const statusTag = ep.source === 'local' ? ' [Local]' : ep.hasSubtitle ? ' [PT-BR]' : '';
    opt.textContent = `${ep.label}${statusTag}`;
    ui.episodeSelect.appendChild(opt);
  }

  if (!appState.selectedEpisodeKey || !ps.episodes.some((e) => e.episodeKey === appState.selectedEpisodeKey)) {
    appState.selectedEpisodeKey = ps.activeEpisodeKey || ps.episodes[0]?.episodeKey || null;
  }
  renderSelectedEpisode();
}

function wireEvents() {
  ui.openSiteBtn.addEventListener('click', async () => {
    await chrome.tabs.create({ url: 'https://rezeroperfect.run/arcs' });
    window.close();
  });

  ui.turnOffCcBtn.addEventListener('click', async () => {
    if (!appState.tabId) return;
    try {
      const resp = await chrome.tabs.sendMessage(appState.tabId, { type: 'TURN_OFF_SITE_CC' });
      if (resp?.ok) {
        ui.ccWarningCard.classList.add('is-hidden');
        showFeedback('Legenda em inglês desligada! Recarregando a página...');
      }
    } catch (err) {
      showFeedback(`Erro ao desligar CC do site: ${err?.message || err}`, true);
    }
  });

  ui.episodeSelect.addEventListener('change', () => {
    appState.selectedEpisodeKey = ui.episodeSelect.value;
    renderSelectedEpisode();
  });

  ui.toggleEnabled.addEventListener('change', async () => {
    await saveSettingsPatch({ enabled: ui.toggleEnabled.checked });
  });

  ui.toggleSigns.addEventListener('change', async () => {
    await saveSettingsPatch({ showSigns: ui.toggleSigns.checked });
  });

  ui.toggleKaraoke.addEventListener('change', async () => {
    await saveSettingsPatch({ showKaraoke: ui.toggleKaraoke.checked });
  });

  ui.fontScaleRange.addEventListener('input', async () => {
    const pct = Number(ui.fontScaleRange.value);
    ui.fontScaleVal.textContent = `${pct}%`;
    updateRangeFallbackProgress(ui.fontScaleRange);
    await saveSettingsPatch({ fontScale: pct / 100 });
  });

  ui.bgOpacityRange.addEventListener('input', async () => {
    const pct = Number(ui.bgOpacityRange.value);
    ui.bgOpacityVal.textContent = `${pct}%`;
    updateRangeFallbackProgress(ui.bgOpacityRange);
    await saveSettingsPatch({ bgOpacity: pct / 100 });
  });

  ui.bottomOffsetRange.addEventListener('input', async () => {
    const pct = Number(ui.bottomOffsetRange.value);
    ui.bottomOffsetVal.textContent = `${pct}%`;
    updateRangeFallbackProgress(ui.bottomOffsetRange);
    await saveSettingsPatch({ bottomOffset: pct });
  });

  async function applyDelay(val) {
    const clamped = Math.max(-5000, Math.min(5000, Math.round(val)));
    await saveSettingsPatch({ delayMs: clamped });
  }

  ui.delayMinusBtn.addEventListener('click', async () => {
    await applyDelay((appState.settings?.delayMs || 0) - 100);
  });

  ui.delayPlusBtn.addEventListener('click', async () => {
    await applyDelay((appState.settings?.delayMs || 0) + 100);
  });

  ui.delayResetBtn.addEventListener('click', async () => {
    await applyDelay(0);
  });

  ui.delayMsInput.addEventListener('change', async () => {
    const parsed = Number.parseInt(ui.delayMsInput.value, 10);
    await applyDelay(Number.isFinite(parsed) ? parsed : 0);
  });

  ui.localSubInput.addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !appState.selectedEpisodeKey) return;

    try {
      const text = await file.text();
      const parsed = Parser.parseSubtitle(text, file.name);
      if (!parsed.cues || parsed.cues.length === 0) {
        showFeedback('Nenhuma fala válida encontrada nesse arquivo.', true);
        return;
      }

      const resp = await chrome.runtime.sendMessage({
        type: 'SAVE_LOCAL_SUBTITLE',
        episodeKey: appState.selectedEpisodeKey,
        filename: file.name,
        format: parsed.format,
        data: {
          version: 1,
          episodeKey: appState.selectedEpisodeKey,
          language: 'pt-BR',
          sourceFormat: parsed.format,
          cues: parsed.cues,
        },
      });
      if (!resp?.ok) {
        throw new Error(resp?.error || 'Falha ao salvar legenda local');
      }

      const localRes = await chrome.runtime.sendMessage({ type: 'GET_LOCAL_SUBTITLES' });
      appState.localSubtitles = localRes?.localSubtitles || {};
      await refreshPageState();
      renderPageState();
      showFeedback(`Legenda "${file.name}" aplicada (${parsed.cues.length} falas).`);
    } catch (err) {
      showFeedback(`Erro ao carregar legenda: ${err?.message || err}`, true);
    }
  });

  ui.clearLocalBtn.addEventListener('click', async () => {
    if (!appState.selectedEpisodeKey) return;
    const resp = await chrome.runtime.sendMessage({
      type: 'CLEAR_LOCAL_SUBTITLE',
      episodeKey: appState.selectedEpisodeKey,
    });
    if (resp?.ok) {
      appState.localSubtitles = resp.localSubtitles || {};
      await refreshPageState();
      renderPageState();
      showFeedback('Legenda local removida.');
    }
  });
}

async function init() {
  wireEvents();

  const [tabs, manifestRes, settingsRes, localRes] = await Promise.all([
    chrome.tabs.query({ active: true, currentWindow: true }),
    chrome.runtime.sendMessage({ type: 'GET_MANIFEST' }),
    chrome.runtime.sendMessage({ type: 'GET_SETTINGS' }),
    chrome.runtime.sendMessage({ type: 'GET_LOCAL_SUBTITLES' }),
  ]);

  appState.tabId = tabs[0]?.id || null;

  if (manifestRes?.ok && manifestRes.manifest) {
    const m = manifestRes.manifest;
    ui.catalogBadge.textContent = `${m.availableEpisodes || 0}/${m.totalEpisodes || 60}`;
  }

  if (settingsRes?.ok && settingsRes.settings) {
    renderSettings(settingsRes.settings);
  }
  if (localRes?.ok && localRes.localSubtitles) {
    appState.localSubtitles = localRes.localSubtitles;
  }

  await refreshPageState();
  renderPageState();
}

document.addEventListener('DOMContentLoaded', init);
