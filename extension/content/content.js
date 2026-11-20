(function () {
  'use strict';

  const Parser = globalThis.RZPTSubParser;
  if (!Parser) return;

  const DEFAULT_SETTINGS = {
    enabled: true,
    fontScale: 1.0,
    bgOpacity: 0.68,
    bottomOffset: 5,
    delayMs: 0,
    showSigns: true,
    showKaraoke: true,
    autoRemoteSync: true,
  };

  const state = {
    arcId: detectArcId(),
    manifest: null,
    settings: { ...DEFAULT_SETTINGS },
    localSubtitles: {},
    controllers: new Map(), // playerEl -> PlayerController
    lastActiveEpisodeKey: null,
  };

  function detectArcId() {
    const bodyArc = document.body?.dataset?.arc;
    if (bodyArc) return Parser.normalizeArcId(bodyArc);
    const match = window.location.pathname.match(/arc-?(\d+)/i);
    if (match) return Parser.normalizeArcId(match[1]);
    return '';
  }

  function isSiteCaptionsEnabled() {
    try {
      const raw = localStorage.getItem('perfectRunWatchSettings');
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && parsed.captions === 'cc') return true;
      }
    } catch (_) {
      // ignore storage parse failure
    }
    for (const ctrl of state.controllers.values()) {
      const srcPath = ctrl.videoEl?.dataset?.sourcePath || '';
      if (/BURNED(?:%20|\s)CC/i.test(srcPath)) return true;
    }
    return false;
  }

  function findManifestEpisode(episodeKey, rawEpisode, label) {
    if (!state.manifest || !Array.isArray(state.manifest.arcs)) return null;
    const arcObj = state.manifest.arcs.find((a) => a.id === state.arcId);
    if (!arcObj || !Array.isArray(arcObj.episodes)) return null;

    const rawLower = String(rawEpisode || '').trim().toLowerCase();
    const labelLower = String(label || '').trim().toLowerCase();

    for (const ep of arcObj.episodes) {
      if (ep.episodeKey === episodeKey) return ep;
      const aliases = Array.isArray(ep.aliases) ? ep.aliases.map((x) => String(x).toLowerCase()) : [];
      if (
        (rawLower && (ep.episode.toLowerCase() === rawLower || aliases.includes(rawLower))) ||
        (labelLower && (ep.label.toLowerCase() === labelLower || aliases.includes(labelLower)))
      ) {
        return ep;
      }
    }
    return null;
  }

  class PlayerController {
    constructor(videoItemEl, playerEl, videoEl) {
      this.videoItemEl = videoItemEl;
      this.playerEl = playerEl;
      this.videoEl = videoEl;

      this.rawEpisode = videoItemEl.dataset.episode || '';
      this.label =
        videoItemEl.querySelector('.video-label')?.textContent?.trim() ||
        (this.rawEpisode ? `EP ${this.rawEpisode}` : 'Episódio');

      const computedKey = Parser.normalizeEpisodeKey(state.arcId, this.rawEpisode || this.label);
      this.manifestEntry = findManifestEpisode(computedKey, this.rawEpisode, this.label);
      this.episodeKey = this.manifestEntry?.episodeKey || computedKey;

      this.cues = [];
      this.subtitleSource = 'none'; // 'local' | 'bundled' | 'none'
      this.subtitleFilename = '';
      this.lastSignature = '';
      this.karaokeRefs = [];
      this.rafId = 0;
      this.rectRafId = 0;
      this.toastTimer = 0;

      this._buildOverlay();
      this._bindEvents();
      this.applySettings();
      this.refreshSubtitle();
      this.scheduleRectSync();
    }

    _buildOverlay() {
      const existing = this.playerEl.querySelector('.rzpt-overlay');
      if (existing) existing.remove();
      const existingToast = this.playerEl.querySelector('.rzpt-toast');
      if (existingToast) existingToast.remove();

      this.overlayEl = document.createElement('div');
      this.overlayEl.className = 'rzpt-overlay';
      this.overlayEl.setAttribute('aria-live', 'off');

      this.zoneTop = document.createElement('div');
      this.zoneTop.className = 'rzpt-zone rzpt-zone-top';

      this.zoneMid = document.createElement('div');
      this.zoneMid.className = 'rzpt-zone rzpt-zone-mid';

      this.zoneBot = document.createElement('div');
      this.zoneBot.className = 'rzpt-zone rzpt-zone-bot';

      this.overlayEl.append(this.zoneTop, this.zoneMid, this.zoneBot);

      this.toastEl = document.createElement('div');
      this.toastEl.className = 'rzpt-toast';

      this.playerEl.append(this.overlayEl, this.toastEl);
    }

    _bindEvents() {
      this._onPlay = () => {
        state.lastActiveEpisodeKey = this.episodeKey;
        this._startLoop();
        this.renderAtCurrentTime();
      };
      this._onPauseOrEnded = () => {
        this._stopLoop();
        this.renderAtCurrentTime();
      };
      this._onTimeOrSeek = () => {
        if (!this.videoEl.paused || this.videoEl.seeking) {
          state.lastActiveEpisodeKey = this.episodeKey;
        }
        this.renderAtCurrentTime();
      };
      this._onMetadata = () => {
        this.scheduleRectSync();
        this.renderAtCurrentTime();
      };

      this.videoEl.addEventListener('play', this._onPlay);
      this.videoEl.addEventListener('playing', this._onPlay);
      this.videoEl.addEventListener('pause', this._onPauseOrEnded);
      this.videoEl.addEventListener('ended', this._onPauseOrEnded);
      this.videoEl.addEventListener('timeupdate', this._onTimeOrSeek);
      this.videoEl.addEventListener('seeked', this._onTimeOrSeek);
      this.videoEl.addEventListener('loadedmetadata', this._onMetadata);

      this.resizeObserver = new ResizeObserver(() => this.scheduleRectSync());
      this.resizeObserver.observe(this.playerEl);
      this.resizeObserver.observe(this.videoEl);
    }

    _startLoop() {
      if (this.rafId) return;
      const tick = () => {
        this.renderAtCurrentTime();
        if (!this.videoEl.paused && !this.videoEl.ended) {
          this.rafId = window.requestAnimationFrame(tick);
        } else {
          this.rafId = 0;
        }
      };
      this.rafId = window.requestAnimationFrame(tick);
    }

    _stopLoop() {
      if (this.rafId) {
        window.cancelAnimationFrame(this.rafId);
        this.rafId = 0;
      }
    }

    scheduleRectSync() {
      if (this.rectRafId) return;
      this.rectRafId = window.requestAnimationFrame(() => {
        this.rectRafId = 0;
        this._syncRectNow();
      });
    }

    _syncRectNow() {
      const playerRect = this.playerEl.getBoundingClientRect();
      const videoRect = this.videoEl.getBoundingClientRect();
      if (videoRect.width <= 0 || videoRect.height <= 0) return;

      const relLeft = videoRect.left - playerRect.left;
      const relTop = videoRect.top - playerRect.top;
      let renderW = videoRect.width;
      let renderH = videoRect.height;
      let offsetX = 0;
      let offsetY = 0;

      const vw = this.videoEl.videoWidth;
      const vh = this.videoEl.videoHeight;
      if (vw > 0 && vh > 0) {
        const videoAR = vw / vh;
        const boxAR = renderW / renderH;
        if (Math.abs(videoAR - boxAR) > 0.01) {
          if (boxAR > videoAR) {
            renderW = renderH * videoAR;
            offsetX = (videoRect.width - renderW) / 2;
          } else {
            renderH = renderW / videoAR;
            offsetY = (videoRect.height - renderH) / 2;
          }
        }
      }

      this.overlayEl.style.left = `${Math.round(relLeft + offsetX)}px`;
      this.overlayEl.style.top = `${Math.round(relTop + offsetY)}px`;
      this.overlayEl.style.width = `${Math.round(renderW)}px`;
      this.overlayEl.style.height = `${Math.round(renderH)}px`;
    }

    applySettings() {
      const s = state.settings;
      this.overlayEl.style.setProperty('--rzpt-font-scale', String(s.fontScale));
      this.overlayEl.style.setProperty('--rzpt-bg-opacity', String(s.bgOpacity));
      this.overlayEl.style.setProperty('--rzpt-bottom-offset', `${s.bottomOffset}%`);
      this.overlayEl.classList.toggle('is-hidden', !s.enabled);
      this.lastSignature = '';
      this.renderAtCurrentTime();
    }

    showToast(text) {
      this.toastEl.textContent = text;
      this.toastEl.classList.add('is-visible');
      if (this.toastTimer) window.clearTimeout(this.toastTimer);
      this.toastTimer = window.setTimeout(() => {
        this.toastEl.classList.remove('is-visible');
      }, 1700);
    }

    async refreshSubtitle() {
      this.manifestEntry = findManifestEpisode(
        Parser.normalizeEpisodeKey(state.arcId, this.rawEpisode || this.label),
        this.rawEpisode,
        this.label
      );
      if (this.manifestEntry?.episodeKey) {
        this.episodeKey = this.manifestEntry.episodeKey;
      }

      const localEntry = state.localSubtitles?.[this.episodeKey];
      if (localEntry?.data?.cues) {
        this.cues = localEntry.data.cues;
        this.subtitleSource = 'local';
        this.subtitleFilename = localEntry.filename || '';
        this.lastSignature = '';
        this.renderAtCurrentTime();
        return;
      }

      if (this.manifestEntry?.available && this.manifestEntry.file) {
        try {
          const resp = await chrome.runtime.sendMessage({
            type: 'GET_BUNDLED_SUBTITLE',
            path: this.manifestEntry.file,
            updatedAt: this.manifestEntry.updatedAt || null,
            remotePreferred: Boolean(this.manifestEntry.remoteAvailable),
          });
          if (resp?.ok && Array.isArray(resp.data?.cues)) {
            this.cues = resp.data.cues;
            this.subtitleSource = resp.source === 'remote' ? 'remote' : 'bundled';
            this.subtitleFilename = this.manifestEntry.file;
            this.lastSignature = '';
            this.renderAtCurrentTime();
            return;
          }
        } catch (err) {
          console.warn('[RZPT] Erro ao buscar legenda:', err);
        }
      }

      this.cues = [];
      this.subtitleSource = 'none';
      this.subtitleFilename = '';
      this.lastSignature = '';
      this.renderAtCurrentTime();
    }

    renderAtCurrentTime() {
      if (!state.settings.enabled || this.cues.length === 0) {
        if (this.lastSignature !== 'empty') {
          this.zoneTop.replaceChildren();
          this.zoneMid.replaceChildren();
          this.zoneBot.replaceChildren();
          this.karaokeRefs = [];
          this.lastSignature = 'empty';
        }
        return;
      }

      const adjustedTime = this.videoEl.currentTime + state.settings.delayMs / 1000;
      const activeRaw = Parser.findActiveCues(this.cues, adjustedTime);
      const active = activeRaw.filter((c) => {
        if (c.isKaraoke) return Boolean(state.settings.showKaraoke);
        if (c.isSign) return Boolean(state.settings.showSigns);
        return true;
      });

      if (active.length === 0) {
        if (this.lastSignature !== 'empty') {
          this.zoneTop.replaceChildren();
          this.zoneMid.replaceChildren();
          this.zoneBot.replaceChildren();
          this.karaokeRefs = [];
          this.lastSignature = 'empty';
        }
        return;
      }

      const sig = active
        .map((c) => `${c.start}:${c.end}:${c.align}:${c.isSign ? 1 : 0}:${c.isKaraoke ? 1 : 0}`)
        .join('|');
      if (sig === this.lastSignature) {
        this._updateKaraokeProgress(adjustedTime);
        return;
      }
      this.lastSignature = sig;
      this.karaokeRefs = [];

      const topNodes = [];
      const midNodes = [];
      const botNodes = [];

      for (const cue of active) {
        const row = this._createCueElement(cue);
        const align = Number(cue.align || 2);
        if (align >= 7 && align <= 9) {
          topNodes.push(row);
        } else if (align >= 4 && align <= 6) {
          midNodes.push(row);
        } else {
          botNodes.push(row);
        }
      }

      this.zoneTop.replaceChildren(...topNodes);
      this.zoneMid.replaceChildren(...midNodes);
      this.zoneBot.replaceChildren(...botNodes);
      this._updateKaraokeProgress(adjustedTime);
    }

    _updateKaraokeProgress(timeSec) {
      if (!this.karaokeRefs || this.karaokeRefs.length === 0) return;
      for (const group of this.karaokeRefs) {
        for (const item of group.syls) {
          const sung = timeSec >= item.t;
          const active = sung && timeSec < item.endT;
          item.el.classList.toggle('is-sung', sung);
          item.el.classList.toggle('is-active', active);
        }
      }
    }

    _createCueElement(cue) {
      const rowEl = document.createElement('div');
      const align = Number(cue.align || 2);
      let alignClass = 'rzpt-align-center';
      if (align === 1 || align === 4 || align === 7) alignClass = 'rzpt-align-left';
      else if (align === 3 || align === 6 || align === 9) alignClass = 'rzpt-align-right';
      rowEl.className = `rzpt-cue-row ${alignClass}`;

      const cueBox = document.createElement('div');
      if (cue.isKaraoke) {
        cueBox.className = 'rzpt-cue rzpt-cue-karaoke';
      } else if (cue.isSign) {
        cueBox.className = 'rzpt-cue rzpt-cue-sign';
      } else {
        cueBox.className = 'rzpt-cue';
      }

      if (cue.isKaraoke && Array.isArray(cue.karaoke) && cue.karaoke.length > 0) {
        const sylRefs = [];
        for (let idx = 0; idx < cue.karaoke.length; idx++) {
          const syl = cue.karaoke[idx];
          const text = String(syl?.text || '');
          if (!text) continue;
          const span = document.createElement('span');
          span.className = 'rzpt-k-syl';
          span.appendChild(document.createTextNode(text));
          cueBox.appendChild(span);

          const startT = Number(syl.t || cue.start);
          const rawNext =
            idx + 1 < cue.karaoke.length ? Number(cue.karaoke[idx + 1].t) : Number(cue.end);
          const endT = Number.isFinite(rawNext) && rawNext > startT ? rawNext : Number(cue.end);
          sylRefs.push({ el: span, t: startT, endT });
        }
        this.karaokeRefs.push({ syls: sylRefs });
        rowEl.appendChild(cueBox);
        return rowEl;
      }

      const lines = Array.isArray(cue.lines) ? cue.lines : [];
      lines.forEach((runs, lineIdx) => {
        if (lineIdx > 0) {
          cueBox.appendChild(document.createElement('br'));
        }
        for (const run of runs) {
          const text = String(run?.text || '');
          if (!text) continue;
          if (!run.i && !run.b) {
            cueBox.appendChild(document.createTextNode(text));
          } else {
            const span = document.createElement('span');
            if (run.i) span.classList.add('rzpt-italic');
            if (run.b) span.classList.add('rzpt-bold');
            span.appendChild(document.createTextNode(text));
            cueBox.appendChild(span);
          }
        }
      });

      rowEl.appendChild(cueBox);
      return rowEl;
    }

    destroy() {
      this._stopLoop();
      if (this.rectRafId) window.cancelAnimationFrame(this.rectRafId);
      if (this.toastTimer) window.clearTimeout(this.toastTimer);
      this.resizeObserver?.disconnect();
      this.videoEl.removeEventListener('play', this._onPlay);
      this.videoEl.removeEventListener('playing', this._onPlay);
      this.videoEl.removeEventListener('pause', this._onPauseOrEnded);
      this.videoEl.removeEventListener('ended', this._onPauseOrEnded);
      this.videoEl.removeEventListener('timeupdate', this._onTimeOrSeek);
      this.videoEl.removeEventListener('seeked', this._onTimeOrSeek);
      this.videoEl.removeEventListener('loadedmetadata', this._onMetadata);
      this.overlayEl?.remove();
      this.toastEl?.remove();
    }
  }

  function scanPlayers() {
    state.arcId = detectArcId();
    const items = document.querySelectorAll('.video-item');
    for (const item of items) {
      const playerEl = item.querySelector('.custom-player');
      const videoEl = playerEl?.querySelector('video.custom-video');
      if (!playerEl || !videoEl) continue;
      if (state.controllers.has(playerEl)) continue;

      const ctrl = new PlayerController(item, playerEl, videoEl);
      state.controllers.set(playerEl, ctrl);
      if (!state.lastActiveEpisodeKey) {
        state.lastActiveEpisodeKey = ctrl.episodeKey;
      }
      if (playerEl.classList.contains('is-fullscreen')) {
        state.lastActiveEpisodeKey = ctrl.episodeKey;
      }
    }
  }

  function getActiveController() {
    for (const ctrl of state.controllers.values()) {
      if (ctrl.playerEl.classList.contains('is-fullscreen')) return ctrl;
    }
    for (const ctrl of state.controllers.values()) {
      if (!ctrl.videoEl.paused && !ctrl.videoEl.ended) return ctrl;
    }
    if (state.lastActiveEpisodeKey) {
      for (const ctrl of state.controllers.values()) {
        if (ctrl.episodeKey === state.lastActiveEpisodeKey) return ctrl;
      }
    }
    const first = state.controllers.values().next();
    return first.done ? null : first.value;
  }

  async function updateSettingsPatch(patch, toastMessageBuilder) {
    const resp = await chrome.runtime.sendMessage({ type: 'SAVE_SETTINGS', patch });
    if (resp?.ok && resp.settings) {
      state.settings = resp.settings;
      for (const ctrl of state.controllers.values()) {
        ctrl.applySettings();
      }
      if (toastMessageBuilder) {
        const active = getActiveController();
        if (active) active.showToast(toastMessageBuilder(state.settings));
      }
    }
  }

  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      const target = e.target;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }

      const key = e.key.toLowerCase();
      if (key === 's') {
        e.preventDefault();
        updateSettingsPatch({ enabled: !state.settings.enabled }, (s) =>
          s.enabled ? 'Legendas PT-BR: Ligadas' : 'Legendas PT-BR: Desligadas'
        );
      } else if (e.key === '[' || e.code === 'BracketLeft') {
        e.preventDefault();
        const nextDelay = Math.max(-5000, state.settings.delayMs - 100);
        updateSettingsPatch({ delayMs: nextDelay }, (s) => `Sincronia PT-BR: ${s.delayMs} ms`);
      } else if (e.key === ']' || e.code === 'BracketRight') {
        e.preventDefault();
        const nextDelay = Math.min(5000, state.settings.delayMs + 100);
        updateSettingsPatch(
          { delayMs: nextDelay },
          (s) => `Sincronia PT-BR: ${s.delayMs > 0 ? '+' : ''}${s.delayMs} ms`
        );
      }
    });
  }

  function setupRuntimeListeners() {
    chrome.storage.onChanged.addListener((changes, areaName) => {
      if (areaName !== 'local') return;
      if (changes.rzptSettings?.newValue) {
        state.settings = { ...DEFAULT_SETTINGS, ...changes.rzptSettings.newValue };
        for (const ctrl of state.controllers.values()) {
          ctrl.applySettings();
        }
      }
      if (changes.rzptLocalSubtitles) {
        state.localSubtitles = changes.rzptLocalSubtitles.newValue || {};
        for (const ctrl of state.controllers.values()) {
          ctrl.refreshSubtitle();
        }
      }
    });

    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === 'GET_PAGE_STATE') {
        scanPlayers();
        const active = getActiveController();
        const episodes = [];
        for (const ctrl of state.controllers.values()) {
          episodes.push({
            episodeKey: ctrl.episodeKey,
            rawEpisode: ctrl.rawEpisode,
            label: ctrl.label,
            hasSubtitle: ctrl.cues.length > 0,
            source: ctrl.subtitleSource,
            filename: ctrl.subtitleFilename,
            cueCount: ctrl.cues.length,
            isPlaying: !ctrl.videoEl.paused && !ctrl.videoEl.ended,
            isFullscreen: ctrl.playerEl.classList.contains('is-fullscreen'),
          });
        }
        sendResponse({
          onSupportedPage: true,
          arcId: state.arcId,
          activeEpisodeKey: active?.episodeKey || episodes[0]?.episodeKey || null,
          siteCaptionsOn: isSiteCaptionsEnabled(),
          episodes,
        });
        return false;
      }

      if (message?.type === 'REFRESH_FROM_MANIFEST') {
        (async () => {
          try {
            const manifestRes = await chrome.runtime.sendMessage({ type: 'GET_MANIFEST' });
            if (manifestRes?.ok && manifestRes.manifest) {
              state.manifest = manifestRes.manifest;
            }
            await Promise.all(
              Array.from(state.controllers.values()).map((ctrl) => ctrl.refreshSubtitle())
            );
            sendResponse({ ok: true });
          } catch (err) {
            sendResponse({ ok: false, error: err?.message || String(err) });
          }
        })();
        return true;
      }

      if (message?.type === 'TURN_OFF_SITE_CC') {
        try {
          const raw = localStorage.getItem('perfectRunWatchSettings');
          const parsed = raw ? JSON.parse(raw) : {};
          parsed.captions = 'off';
          localStorage.setItem('perfectRunWatchSettings', JSON.stringify(parsed));
          sendResponse({ ok: true, reloadRecommended: true });
          window.setTimeout(() => window.location.reload(), 60);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err) });
        }
        return false;
      }

      return false;
    });

    document.addEventListener('fullscreenchange', () => {
      for (const ctrl of state.controllers.values()) {
        if (ctrl.playerEl.classList.contains('is-fullscreen')) {
          state.lastActiveEpisodeKey = ctrl.episodeKey;
        }
        ctrl.scheduleRectSync();
      }
    });
  }

  async function init() {
    try {
      const [manifestRes, settingsRes, localRes] = await Promise.all([
        chrome.runtime.sendMessage({ type: 'GET_MANIFEST' }),
        chrome.runtime.sendMessage({ type: 'GET_SETTINGS' }),
        chrome.runtime.sendMessage({ type: 'GET_LOCAL_SUBTITLES' }),
      ]);
      if (manifestRes?.ok) state.manifest = manifestRes.manifest;
      if (settingsRes?.ok && settingsRes.settings) {
        state.settings = { ...DEFAULT_SETTINGS, ...settingsRes.settings };
      }
      if (localRes?.ok && localRes.localSubtitles) {
        state.localSubtitles = localRes.localSubtitles;
      }
    } catch (err) {
      console.warn('[RZPT] Falha na inicialização:', err);
    }

    scanPlayers();

    let scanTimer = 0;
    const observer = new MutationObserver(() => {
      if (scanTimer) return;
      scanTimer = window.requestAnimationFrame(() => {
        scanTimer = 0;
        scanPlayers();
      });
    });
    const observeRoot = document.getElementById('episode-list') || document.body;
    if (observeRoot) {
      observer.observe(observeRoot, { childList: true, subtree: true });
    }

    setupKeyboardShortcuts();
    setupRuntimeListeners();
  }

  init();
})();
