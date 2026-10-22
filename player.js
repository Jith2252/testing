(() => {
  'use strict';

  const video = document.querySelector('#video');
  const videoStage = document.querySelector('#videoStage');
  const playerCard = videoStage.closest('.player-card');
  const emptyState = document.querySelector('#emptyState');
  const openVideoButton = document.querySelector('#openVideoButton');
  const videoUrl = document.querySelector('#videoUrl');
  const openUrlButton = document.querySelector('#openUrlButton');
  const subtitleOverlay = document.querySelector('#subtitleOverlay');
  const subtitlePreview = document.querySelector('#subtitlePreviewText');
  const status = document.querySelector('#status');
  const playButton = document.querySelector('#playButton');
  const backButton = document.querySelector('#backButton');
  const forwardButton = document.querySelector('#forwardButton');
  const muteButton = document.querySelector('#muteButton');
  const fullscreenButton = document.querySelector('#fullscreenButton');
  const seekBar = document.querySelector('#seekBar');
  const seekPreview = document.querySelector('#seekPreview');
  const volumeBar = document.querySelector('#volumeBar');
  const speedSelect = document.querySelector('#speedSelect');
  const audioSelect = document.querySelector('#audioSelect');
  let subtitleSelect = document.getElementById('subtitleSelect');
  if (!subtitleSelect && audioSelect) {
    subtitleSelect = document.createElement('select');
    subtitleSelect.id = 'subtitleSelect';
    subtitleSelect.setAttribute('aria-label', 'Subtitle track');
    subtitleSelect.title = 'Subtitle track';
    subtitleSelect.className = audioSelect.className;
    subtitleSelect.disabled = true;
    const label = document.createElement('label');
    label.className = 'select-label';
    label.htmlFor = 'subtitleSelect';
    label.textContent = 'Subtitles';
    audioSelect.parentElement?.parentElement?.insertBefore(label, audioSelect.parentElement);
    audioSelect.parentElement?.parentElement?.insertBefore(subtitleSelect, audioSelect.parentElement);
  }
  const currentTimeLabel = document.querySelector('#currentTime');
  const durationLabel = document.querySelector('#duration');
  const videoFile = document.querySelector('#videoFile');
  const subtitleFile = document.querySelector('#subtitleFile');
  const videoFileName = document.querySelector('#videoFileName');
  const subtitleFileName = document.querySelector('#subtitleFileName');
  const subtitleFontSize = document.querySelector('#subtitleFontSize');
  const subtitleColor = document.querySelector('#subtitleColor');
  const subtitleBackgroundOpacity = document.querySelector('#subtitleBackgroundOpacity');
  const subtitlePosition = document.querySelector('#subtitlePosition');

  const params = new URLSearchParams(window.location.search);
  const RESUME_PREFIX = 'local-video-player:resume:';
  const PREFERRED_AUDIO_KEY = 'local-video-player:preferred-audio-track';
  const PREFERRED_SUBTITLE_KEY = 'local-video-player:preferred-subtitle-track';
  const objectUrls = [];
  let subtitleCues = [];
  let lastSavedTime = -1;
  let isSeeking = false;
  let activeVideoKey = '';
  let pendingAutoplay = false;
  let controlsTimer = 0;
  let hlsInstance = null;
  let dashInstance = null;
  let usingHls = false;
  let usingDash = false;
  let activeRemoteSource = false;
  let proxyEnabled = false;
  let proxyConfigLoaded = false;
  let proxyFallbackAttempted = false;
  let activeOriginalRemoteSource = '';
  let sourceGeneration = 0;
  let externalSubtitleActive = false;
  let selectedSubtitleId = 'off';
  let selectedAudioId = '';
  let trackBindings = [];
  let trackListBindings = [];
  let embeddedTrackModel = { audio: [], subtitle: [] };

  const setStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle('error', isError);
  };

  const loadPlayerConfig = async () => {
    try {
      const response = await fetch('/config', { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const config = await response.json();
      proxyEnabled = config?.proxyEnabled === true;
    } catch {
      proxyEnabled = false;
    } finally {
      proxyConfigLoaded = true;
    }
  };

  const proxySource = (url) => `/proxy?url=${encodeURIComponent(url)}`;

  const tryProxyFallback = (url, label, options = {}) => {
    if (!proxyConfigLoaded || !proxyEnabled || proxyFallbackAttempted || !activeRemoteSource) return false;
    proxyFallbackAttempted = true;
    setStatus('Direct playback failed; trying the configured server proxy…');
    const proxied = proxySource(url);
    const type = window.SourceUtils?.detectSourceType(url);
    const fallbackOptions = { ...options, isProxy: true, originalSource: options.originalSource || url };
    if (type === 'm3u8') loadHlsSource(proxied, `${label || 'stream'} (proxy)`, fallbackOptions);
    else if (type === 'mpd') loadDashSource(proxied, `${label || 'stream'} (proxy)`, fallbackOptions);
    else { setVideoSource(proxied, `${label || 'stream'} (proxy)`, fallbackOptions); activeRemoteSource = true; proxyFallbackAttempted = true; }
    return true;
  };

  const formatTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    return `${hours ? `${hours}:` : ''}${String(minutes).padStart(hours ? 2 : 1, '0')}:${String(remainder).padStart(2, '0')}`;
  };

  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  const normalizeLanguage = (value) => String(value || '').trim().toLowerCase().replace('_', '-');

  const revealControls = () => {
    playerCard.classList.remove('controls-idle');
    window.clearTimeout(controlsTimer);
    if (!video.paused && !video.ended) {
      controlsTimer = window.setTimeout(() => {
        if (!playerCard.matches(':focus-within') && !video.paused && !video.ended) playerCard.classList.add('controls-idle');
      }, 3000);
    }
  };

  const updatePlayButton = () => {
    const playing = !video.paused && !video.ended;
    playButton.textContent = playing ? '❚❚' : '▶';
    playButton.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    playButton.title = playing ? 'Pause' : 'Play';
    playButton.dataset.tooltip = playing ? 'Pause' : 'Play';
    if (playing) revealControls();
    else playerCard.classList.remove('controls-idle');
  };

  const updateMuteButton = () => {
    const muted = video.muted || video.volume === 0;
    muteButton.textContent = muted ? '🔇' : '🔊';
    muteButton.setAttribute('aria-label', muted ? 'Unmute' : 'Mute');
    muteButton.title = muted ? 'Unmute' : 'Mute';
    muteButton.dataset.tooltip = muted ? 'Unmute' : 'Mute';
  };

  const updateBufferedRange = (duration) => {
    const progress = duration ? clamp((video.currentTime / duration) * 100, 0, 100) : 0;
    let buffered = progress;
    if (duration && video.buffered.length) {
      buffered = clamp((video.buffered.end(video.buffered.length - 1) / duration) * 100, 0, 100);
    }
    seekBar.style.setProperty('--progress', `${progress}%`);
    seekBar.style.setProperty('--buffered', `${Math.max(progress, buffered)}%`);
  };

  const updateProgress = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    currentTimeLabel.textContent = formatTime(video.currentTime);
    durationLabel.textContent = formatTime(duration);
    if (!isSeeking) seekBar.value = duration ? String(Math.round((video.currentTime / duration) * 1000)) : '0';
    updateBufferedRange(duration);
    renderSubtitle(video.currentTime);
  };

  const updateSeekPreview = (event) => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (!duration) return;
    const bounds = seekBar.getBoundingClientRect();
    const percent = clamp((event.clientX - bounds.left) / bounds.width, 0, 1);
    const previewTime = duration * percent;
    seekPreview.textContent = formatTime(previewTime);
    seekPreview.style.left = `${percent * 100}%`;
    seekBar.setAttribute('aria-valuetext', `${formatTime(previewTime)} of ${formatTime(duration)}`);
  };

  const syncSubtitlePreview = () => {
    if (!subtitlePreview) return;
    const size = clamp(Number(subtitleFontSize.value) || 24, 12, 48);
    const opacity = clamp(Number(subtitleBackgroundOpacity.value) || 0, 0, 100) / 100;
    subtitlePreview.style.fontSize = `${size}px`;
    subtitlePreview.style.color = subtitleColor.value;
    subtitlePreview.style.backgroundColor = `rgba(0, 0, 0, ${opacity})`;
    subtitlePreview.dataset.position = subtitlePosition.value;
  };

  const resumeKey = (sourceKey) => `${RESUME_PREFIX}${sourceKey}`;

  const sourceIdentity = (source) => {
    if (!source) return '';
    try {
      return new URL(source, window.location.href).href;
    } catch {
      return source;
    }
  };

  const readTrackPreference = (key) => {
    try {
      const value = JSON.parse(localStorage.getItem(key) || 'null');
      return value && typeof value === 'object' ? value : {};
    } catch {
      return {};
    }
  };

  const writeTrackPreference = (key, track) => {
    if (!track) return;
    try {
      localStorage.setItem(key, JSON.stringify({
        id: track.id || '',
        language: track.language || '',
        label: track.label || '',
      }));
    } catch {
      // Storage can be disabled; track selection still works for this session.
    }
  };

  const languagePreferences = () => {
    const languages = Array.isArray(navigator.languages) && navigator.languages.length
      ? navigator.languages
      : [navigator.language];
    return languages.filter(Boolean).map((language) => String(language).toLowerCase());
  };

  const languageMatches = (trackLanguage, preferredLanguage) => {
    const track = normalizeLanguage(trackLanguage);
    const preferred = normalizeLanguage(preferredLanguage);
    return Boolean(track && preferred && (track === preferred || track.split('-')[0] === preferred.split('-')[0]));
  };

  const trackLabel = (track, index) => {
    return track?.label || track?.name || track?.labels?.[0]?.text
      || track?.roles?.[0]?.value || track?.lang || track?.language || `Track ${index + 1}`;
  };

  const trackMatchesPreference = (track, preference) => {
    if (!track || !preference) return false;
    return (preference.id && track.id && String(preference.id) === String(track.id))
      || (preference.language && languageMatches(track.language, preference.language))
      || (preference.label && String(preference.label).toLowerCase() === String(track.label || '').toLowerCase());
  };

  const choosePreferredTrack = (tracks, preference, { subtitle = false } = {}) => {
    if (!tracks.length) return null;
    if (subtitle && preference?.id === 'off') return null;
    const preferred = tracks.find((track) => trackMatchesPreference(track, preference));
    if (preferred) return preferred;
    const browserLanguage = languagePreferences().map((language) => tracks.find((track) => languageMatches(track.language, language))).find(Boolean);
    if (browserLanguage) return browserLanguage;
    const defaultTrack = tracks.find((track) => track.default || track.forced);
    if (defaultTrack) return defaultTrack;
    return subtitle ? null : tracks[0];
  };

  const clearTrackBindings = () => {
    trackBindings.forEach(({ track, listener }) => {
      try { track.removeEventListener('cuechange', listener); } catch { /* Ignore detached tracks. */ }
    });
    trackBindings = [];
    trackListBindings.forEach(({ list, type, listener }) => {
      try { list.removeEventListener(type, listener); } catch { /* Ignore detached track lists. */ }
    });
    trackListBindings = [];
  };

  const clearEmbeddedTracks = () => {
    clearTrackBindings();
    embeddedTrackModel = { audio: [], subtitle: [] };
    selectedSubtitleId = 'off';
    selectedAudioId = '';
    if (subtitleSelect) {
      subtitleSelect.replaceChildren(new Option('Subtitles: Off', 'off', true));
      subtitleSelect.disabled = false;
    }
    if (audioSelect) {
      audioSelect.replaceChildren(new Option('Audio: Original', ''));
      audioSelect.disabled = true;
    }
    subtitleOverlay.textContent = '';
  };

  const setTrackMode = (track, mode) => {
    try { track.mode = mode; } catch { /* Some browsers expose read-only track modes. */ }
  };

  const nativeSubtitleTrackForModel = (modelTrack) => {
    if (!modelTrack) return null;
    if (modelTrack.ref) return modelTrack.ref;
    const tracks = Array.from(video.textTracks || []);
    const exact = tracks.find((track) => trackLabel(track, 0) === modelTrack.label
      && normalizeLanguage(track.language || track.lang) === normalizeLanguage(modelTrack.language));
    if (exact) return exact;
    const compatible = tracks.find((track) => languageMatches(track.language || track.lang, modelTrack.language));
    return compatible || tracks[modelTrack.backendIndex] || null;
  };

  const forceSubtitleTrackModes = (modelTrack) => {
    const selected = nativeSubtitleTrackForModel(modelTrack);
    Array.from(video.textTracks || []).forEach((track) => setTrackMode(track, selected && track === selected ? 'hidden' : 'disabled'));
    renderEmbeddedSubtitle();
  };

  const renderEmbeddedSubtitle = () => {
    if (externalSubtitleActive) {
      renderSubtitle(video.currentTime);
      return;
    }
    if (selectedSubtitleId === 'off') {
      subtitleOverlay.textContent = '';
      return;
    }
    const selected = embeddedTrackModel.subtitle.find((track) => track.id === selectedSubtitleId);
    const nativeTrack = nativeSubtitleTrackForModel(selected);
    const activeCues = nativeTrack ? Array.from(nativeTrack.activeCues || []) : [];
    subtitleOverlay.textContent = activeCues.map((cue) => cue.text || '').filter(Boolean).join('\n');
  };

  const bindNativeTrackList = () => {
    [video.textTracks, video.audioTracks].filter(Boolean).forEach((list) => {
      if (trackListBindings.some((binding) => binding.list === list)) return;
      ['addtrack', 'removetrack', 'change'].forEach((type) => {
        const listener = () => {
          if (type === 'addtrack' || type === 'removetrack') {
            refreshAudioTracks();
            bindNativeSubtitleTracks();
          }
          renderEmbeddedSubtitle();
        };
        try {
          list.addEventListener(type, listener);
          trackListBindings.push({ list, type, listener });
        } catch {
          // Older browsers may not expose TextTrackList/AudioTrackList events.
        }
      });
    });
  };

  const bindNativeSubtitleTracks = () => {
    bindNativeTrackList();
    Array.from(video.textTracks || []).forEach((track) => {
      if (trackBindings.some((binding) => binding.track === track)) return;
      const listener = () => renderEmbeddedSubtitle();
      try { track.addEventListener('cuechange', listener); } catch { return; }
      trackBindings.push({ track, listener });
    });
    const selected = embeddedTrackModel.subtitle.find((track) => track.id === selectedSubtitleId);
    forceSubtitleTrackModes(selected || null);
  };

  const setSubtitleOptions = (tracks, selectedId = selectedSubtitleId) => {
    if (!subtitleSelect) return;
    subtitleSelect.replaceChildren(new Option('Subtitles: Off', 'off', selectedId === 'off'));
    if (externalSubtitleActive) subtitleSelect.append(new Option('Subtitles: External file', 'external', selectedId === 'external'));
    tracks.forEach((track) => subtitleSelect.append(new Option(`Subtitles: ${track.label}`, track.id, track.id === selectedId)));
    subtitleSelect.value = selectedId;
    subtitleSelect.disabled = tracks.length === 0 && !externalSubtitleActive;
  };

  const preservePlaybackPosition = (callback) => {
    const generation = sourceGeneration;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const wasPaused = video.paused;
    callback();
    const restore = () => {
      if (generation !== sourceGeneration) return;
      if (!Number.isFinite(time) || !Number.isFinite(video.duration) || video.duration <= 0) return;
      try { video.currentTime = Math.min(time, video.duration); } catch { /* Ignore media seek races. */ }
      if (!wasPaused && video.paused) video.play().catch(() => {});
    };
    restore();
    window.setTimeout(restore, 0);
  };

  const selectSubtitleTrack = (value, { persist = true } = {}) => {
    const track = embeddedTrackModel.subtitle.find((item) => item.id === value);
    externalSubtitleActive = value === 'external';
    selectedSubtitleId = value || 'off';
    if (value === 'off') {
      if (hlsInstance) preservePlaybackPosition(() => { hlsInstance.subtitleTrack = -1; });
      if (dashInstance && typeof dashInstance.setTextTrack === 'function') preservePlaybackPosition(() => dashInstance.setTextTrack(-1));
      forceSubtitleTrackModes(null);
    } else if (value === 'external') {
      Array.from(video.textTracks || []).forEach((item) => setTrackMode(item, 'disabled'));
      renderSubtitle(video.currentTime);
    } else if (track) {
      externalSubtitleActive = false;
      if (hlsInstance) preservePlaybackPosition(() => { hlsInstance.subtitleTrack = track.backendIndex; });
      if (dashInstance && typeof dashInstance.setTextTrack === 'function') preservePlaybackPosition(() => dashInstance.setTextTrack(track.backendIndex));
      forceSubtitleTrackModes(track);
    }
    if (persist && track) writeTrackPreference(PREFERRED_SUBTITLE_KEY, track);
    else if (persist && value === 'off') writeTrackPreference(PREFERRED_SUBTITLE_KEY, { id: 'off', language: '', label: '' });
    if (subtitleSelect) subtitleSelect.value = selectedSubtitleId;
    renderEmbeddedSubtitle();
  };

  const selectAudioTrack = (value, { persist = true } = {}) => {
    const track = embeddedTrackModel.audio.find((item) => item.id === value);
    if (!track) return;
    selectedAudioId = track.id;
    preservePlaybackPosition(() => {
      if (hlsInstance) hlsInstance.audioTrack = track.backendIndex;
      else if (dashInstance && typeof dashInstance.setCurrentTrack === 'function') dashInstance.setCurrentTrack(track.ref || track.backendTrack);
      else Array.from(video.audioTracks || []).forEach((item) => { item.enabled = item === track.ref; });
    });
    if (persist) writeTrackPreference(PREFERRED_AUDIO_KEY, track);
    if (audioSelect) audioSelect.value = selectedAudioId;
  };

  const saveResumePosition = () => {
    if (!activeVideoKey || !Number.isFinite(video.currentTime) || video.currentTime <= 0) return;
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const time = duration && duration - video.currentTime < 5 ? 0 : video.currentTime;
    if (Math.abs(time - lastSavedTime) < 1) return;
    try {
      localStorage.setItem(resumeKey(activeVideoKey), JSON.stringify({ time, savedAt: Date.now() }));
      lastSavedTime = time;
    } catch {
      // Storage can be disabled in private browsing; playback still works.
    }
  };

  const restoreResumePosition = () => {
    if (!activeVideoKey) return;
    try {
      const saved = JSON.parse(localStorage.getItem(resumeKey(activeVideoKey)) || 'null');
      if (!saved || !Number.isFinite(saved.time) || saved.time < 1 || saved.time >= video.duration - 5) return;
      video.currentTime = saved.time;
      setStatus(`Resumed at ${formatTime(saved.time)}`);
    } catch {
      // Ignore malformed or unavailable local storage entries.
    }
  };

  const stripSubtitleMarkup = (text) => text
    .replace(/<\/?(?:i|b|u|c(?:\.[^ >]+)?|font(?: [^>]*)?)>/gi, '')
    .replace(/\{\\[^}]+\}/g, '')
    .trim();

  const parseTimestamp = (value) => {
    const normalized = value.trim().replace(',', '.');
    const parts = normalized.split(':').map(Number);
    if (parts.some((part) => !Number.isFinite(part))) return NaN;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return NaN;
  };

  // Handles the same cue-oriented formats commonly emitted by the Python parser:
  // SRT blocks, WebVTT cues, and simple ASS/SSA dialogue lines.
  const parseSubtitles = (text, filename = '') => {
    const normalized = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
    const cues = [];
    const timestampPattern = /(\d{1,}:\d{2}(?::\d{2})?[.,]\d{1,3})\s*-->\s*(\d{1,}:\d{2}(?::\d{2})?[.,]\d{1,3})/;
    const blocks = normalized.split(/\n{2,}/);

    blocks.forEach((block) => {
      const lines = block.split('\n').map((line) => line.trimEnd());
      const timingIndex = lines.findIndex((line) => timestampPattern.test(line));
      if (timingIndex < 0) return;
      const timing = lines[timingIndex].match(timestampPattern);
      const start = parseTimestamp(timing[1]);
      const end = parseTimestamp(timing[2]);
      const body = lines.slice(timingIndex + 1).join('\n').trim();
      if (Number.isFinite(start) && Number.isFinite(end) && end > start && body) {
        cues.push({ start, end, text: stripSubtitleMarkup(body) });
      }
    });

    if (!cues.length && /\.(?:ass|ssa)$/i.test(filename)) {
      normalized.split('\n').forEach((line) => {
        if (!/^Dialogue\s*:/i.test(line)) return;
        const fields = line.slice(line.indexOf(':') + 1).split(',');
        if (fields.length < 10) return;
        const start = parseTimestamp(fields[1]);
        const end = parseTimestamp(fields[2]);
        const body = fields.slice(9).join(',').replace(/\\N/g, '\n');
        if (Number.isFinite(start) && Number.isFinite(end) && end > start && body.trim()) {
          cues.push({ start, end, text: stripSubtitleMarkup(body) });
        }
      });
    }

    return cues.sort((a, b) => a.start - b.start);
  };

  const renderSubtitle = (time) => {
    if (!externalSubtitleActive) {
      renderEmbeddedSubtitle();
      return;
    }
    if (!subtitleCues.length) {
      subtitleOverlay.textContent = '';
      return;
    }
    let low = 0;
    let high = subtitleCues.length - 1;
    let match = null;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      const cue = subtitleCues[middle];
      if (time < cue.start) high = middle - 1;
      else if (time >= cue.end) low = middle + 1;
      else { match = cue; break; }
    }
    subtitleOverlay.textContent = match ? match.text : '';
  };

  const loadSubtitleText = (text, sourceName) => {
    subtitleCues = parseSubtitles(text, sourceName);
    externalSubtitleActive = subtitleCues.length > 0;
    selectedSubtitleId = externalSubtitleActive ? 'external' : 'off';
    setSubtitleOptions(embeddedTrackModel.subtitle, selectedSubtitleId);
    if (subtitleCues.length) setStatus(`${subtitleCues.length} subtitle cues loaded`);
    else setStatus('Subtitle file contains no readable cues', true);
    renderSubtitle(video.currentTime);
  };

  const loadSubtitleUrl = async (url) => {
    const generation = sourceGeneration;
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const text = await response.text();
      if (generation !== sourceGeneration) return;
      loadSubtitleText(text, url);
    } catch (error) {
      if (generation !== sourceGeneration) return;
      subtitleCues = [];
      externalSubtitleActive = false;
      setSubtitleOptions(embeddedTrackModel.subtitle, 'off');
      setStatus(`Could not load subtitles: ${error.message}`, true);
    }
  };

  const renderHlsSubtitle = () => renderEmbeddedSubtitle();

  const bindHlsTextTracks = () => bindNativeSubtitleTracks();

  const refreshHlsAudioTracks = (tracks = hlsInstance?.audioTracks || []) => {
    const sourceTracks = Array.from(tracks || []);
    embeddedTrackModel.audio = sourceTracks.map((track, index) => ({
      id: `hls-audio-${track.id ?? index}`,
      label: trackLabel(track, index),
      language: track.lang || track.language || '',
      default: Boolean(track.default || track.isDefault),
      backendIndex: index,
      backendTrack: track,
      ref: track,
    }));
    const selected = choosePreferredTrack(embeddedTrackModel.audio, readTrackPreference(PREFERRED_AUDIO_KEY));
    selectedAudioId = selected?.id || '';
    if (audioSelect) {
      audioSelect.replaceChildren();
      embeddedTrackModel.audio.forEach((track) => audioSelect.append(new Option(`Audio: ${track.label}`, track.id, track.id === selectedAudioId)));
      if (!embeddedTrackModel.audio.length) audioSelect.append(new Option('Audio: Original', ''));
      audioSelect.disabled = embeddedTrackModel.audio.length < 2;
    }
    if (selected && hlsInstance && hlsInstance.audioTrack !== selected.backendIndex) {
      preservePlaybackPosition(() => { hlsInstance.audioTrack = selected.backendIndex; });
    }
  };

  const refreshHlsSubtitleTracks = (tracks = hlsInstance?.subtitleTracks || []) => {
    const sourceTracks = Array.from(tracks || []);
    embeddedTrackModel.subtitle = sourceTracks.map((track, index) => ({
      id: `hls-subtitle-${track.id ?? index}`,
      label: trackLabel(track, index),
      language: track.lang || track.language || '',
      default: Boolean(track.default || track.isDefault),
      forced: Boolean(track.forced),
      backendIndex: index,
      backendTrack: track,
      ref: null,
    }));
    const selected = choosePreferredTrack(embeddedTrackModel.subtitle, readTrackPreference(PREFERRED_SUBTITLE_KEY), { subtitle: true });
    const selectedId = externalSubtitleActive ? 'external' : (selected?.id || 'off');
    setSubtitleOptions(embeddedTrackModel.subtitle, selectedId);
    if (!externalSubtitleActive) selectSubtitleTrack(selected?.id || 'off', { persist: false });
    const generation = sourceGeneration;
    window.setTimeout(() => { if (generation === sourceGeneration) bindHlsTextTracks(); }, 0);
  };

  const destroyHls = () => {
    if (hlsInstance) hlsInstance.destroy();
    hlsInstance = null;
    usingHls = false;
  };

  const destroyDash = () => {
    if (dashInstance) dashInstance.reset();
    dashInstance = null;
    usingDash = false;
  };

  const destroyAdaptive = () => { destroyHls(); destroyDash(); };

  const hlsErrorMessage = (data) => {
    const details = String(data?.details || '').toLowerCase();
    if (/license|drm|keysystem/.test(details)) return 'This HLS stream requires DRM that this player cannot use.';
    if (data?.type === window.Hls?.ErrorTypes?.NETWORK_ERROR || /network|manifest|frag|level|audio|subtitle/.test(details)) {
      return 'Could not load the stream. Check the URL, CORS policy, and network connection. If direct playback fails, try the server proxy.';
    }
    return 'HLS playback failed. Check the stream URL and format.';
  };

  const beginSourceLoad = () => {
    sourceGeneration += 1;
    destroyAdaptive();
    clearEmbeddedTracks();
    externalSubtitleActive = false;
    subtitleCues = [];
    if (subtitleFile) subtitleFile.value = '';
    if (subtitleFileName) subtitleFileName.textContent = 'Choose a file';
    pendingAutoplay = false;
    return sourceGeneration;
  };

  const loadHlsSource = (url, label, options = {}) => {
    const generation = beginSourceLoad();
    const Hls = window.Hls;
    if (!Hls || typeof Hls.isSupported !== 'function' || !Hls.isSupported()) {
      setStatus('HLS is not supported here. Add vendor/hls.min.js or use a browser with native HLS.', true);
      return;
    }
    usingHls = true;
    activeRemoteSource = true;
    if (!options.isProxy) { proxyFallbackAttempted = false; activeOriginalRemoteSource = url; }
    activeVideoKey = sourceIdentity(options.originalSource || url);
    lastSavedTime = -1;
    pendingAutoplay = Boolean(options.autoplay);
    emptyState.hidden = true;
    playerCard.classList.remove('controls-idle');
    videoFileName.textContent = label || 'HLS stream';
    video.removeAttribute('src');
    video.load();
    setStatus(`Loading ${label || 'HLS stream'}…`);

    const hls = new Hls({ renderTextTracksNatively: true });
    hlsInstance = hls;
    hls.on(Hls.Events.MEDIA_ATTACHED, () => { if (generation === sourceGeneration && hlsInstance === hls) hls.loadSource(url); });
    hls.on(Hls.Events.MANIFEST_PARSED, (_event, data) => {
      if (generation !== sourceGeneration || hlsInstance !== hls) return;
      refreshHlsAudioTracks(data?.audioTracks || hls.audioTracks || []);
      refreshHlsSubtitleTracks(data?.subtitleTracks || hls.subtitleTracks || []);
      setStatus(`${formatTime(video.duration)} HLS stream ready`);
      if (pendingAutoplay) {
        pendingAutoplay = false;
        video.play().catch(() => setStatus('Playback was blocked; press play to start', true));
      }
    });
    hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, (_event, data) => {
      if (generation === sourceGeneration && hlsInstance === hls) refreshHlsAudioTracks(data?.audioTracks || hls.audioTracks || []);
    });
    hls.on(Hls.Events.SUBTITLE_TRACKS_UPDATED, (_event, data) => {
      if (generation === sourceGeneration && hlsInstance === hls) refreshHlsSubtitleTracks(data?.subtitleTracks || hls.subtitleTracks || []);
    });
    hls.on(Hls.Events.SUBTITLE_TRACK_LOADED, () => { if (generation === sourceGeneration && hlsInstance === hls) bindHlsTextTracks(); });
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (generation !== sourceGeneration || hlsInstance !== hls) return;
      if (data?.fatal && !tryProxyFallback(url, label, options)) setStatus(hlsErrorMessage(data), true);
    });
    hls.attachMedia(video);
  };


  const dashErrorMessage = (event) => {
    const code = event?.error?.code || event?.error?.message || '';
    if (/license|drm|protection/i.test(String(code))) return 'This DASH stream requires DRM that this player cannot use.';
    if (/cors|cross|access/i.test(String(code))) return 'DASH playback was blocked by CORS. Enable CORS on the manifest and media segments.';
    return 'Could not load the DASH stream. Check the manifest, CORS policy, and network connection. If direct playback is blocked, try the server proxy.';
  };

  const refreshDashTracks = () => {
    if (!dashInstance || typeof dashInstance.getTracksFor !== 'function') return;
    const audioTracks = Array.from(dashInstance.getTracksFor('audio') || []);
    embeddedTrackModel.audio = audioTracks.map((track, index) => ({
      id: `dash-audio-${track.id ?? index}`,
      label: trackLabel({ label: track.labels?.[0]?.text || track.roles?.[0]?.value, lang: track.lang || track.language }, index),
      language: track.lang || track.language || '',
      default: Boolean(track.default || track.isDefault || index === 0),
      backendIndex: index,
      backendTrack: track,
      ref: track,
    }));
    const selectedAudio = choosePreferredTrack(embeddedTrackModel.audio, readTrackPreference(PREFERRED_AUDIO_KEY));
    selectedAudioId = selectedAudio?.id || '';
    if (audioSelect) {
      audioSelect.replaceChildren();
      embeddedTrackModel.audio.forEach((track) => audioSelect.append(new Option(`Audio: ${track.label}`, track.id, track.id === selectedAudioId)));
      if (!embeddedTrackModel.audio.length) audioSelect.append(new Option('Audio: Original', ''));
      audioSelect.disabled = embeddedTrackModel.audio.length < 2;
    }
    if (selectedAudio && typeof dashInstance.setCurrentTrack === 'function') {
      preservePlaybackPosition(() => dashInstance.setCurrentTrack(selectedAudio.backendTrack));
    }

    const textTracks = Array.from(dashInstance.getTracksFor('text') || []);
    const fragmentedTextTracks = Array.from(dashInstance.getTracksFor('fragmentedText') || []);
    const allTextTracks = textTracks.length ? textTracks : fragmentedTextTracks;
    embeddedTrackModel.subtitle = allTextTracks.map((track, index) => ({
      id: `dash-subtitle-${track.id ?? index}`,
      label: trackLabel({ label: track.labels?.[0]?.text || track.roles?.[0]?.value, lang: track.lang || track.language }, index),
      language: track.lang || track.language || '',
      default: Boolean(track.default || track.isDefault || index === 0),
      forced: Boolean(track.forced),
      backendIndex: index,
      backendTrack: track,
      ref: null,
    }));
    const selectedSubtitle = choosePreferredTrack(embeddedTrackModel.subtitle, readTrackPreference(PREFERRED_SUBTITLE_KEY), { subtitle: true });
    const selectedId = externalSubtitleActive ? 'external' : (selectedSubtitle?.id || 'off');
    setSubtitleOptions(embeddedTrackModel.subtitle, selectedId);
    if (!externalSubtitleActive && typeof dashInstance.setTextTrack === 'function') {
      preservePlaybackPosition(() => dashInstance.setTextTrack(selectedSubtitle ? selectedSubtitle.backendIndex : -1));
      selectedSubtitleId = selectedSubtitle?.id || 'off';
      forceSubtitleTrackModes(selectedSubtitle || null);
    }
    const generation = sourceGeneration;
    window.setTimeout(() => { if (generation === sourceGeneration) bindNativeSubtitleTracks(); }, 0);
  };


  const loadDashSource = (url, label, options = {}) => {
    const generation = beginSourceLoad();
    const dash = window.dashjs;
    if (!dash || typeof dash.MediaPlayer !== 'function') {
      setStatus('DASH playback is unavailable. Add vendor/dash.all.min.js or use MP4/HLS.', true);
      return;
    }
    usingDash = true;
    activeRemoteSource = true;
    if (!options.isProxy) { proxyFallbackAttempted = false; activeOriginalRemoteSource = url; }
    activeVideoKey = sourceIdentity(options.originalSource || url);
    lastSavedTime = -1;
    pendingAutoplay = Boolean(options.autoplay);
    emptyState.hidden = true;
    playerCard.classList.remove('controls-idle');
    videoFileName.textContent = label || 'DASH stream';
    video.removeAttribute('src');
    video.load();
    setStatus(`Loading ${label || 'DASH stream'}…`);
    const player = dash.MediaPlayer().create();
    dashInstance = player;
    player.on(dash.MediaPlayer.events.STREAM_INITIALIZED, () => {
      if (generation !== sourceGeneration || dashInstance !== player) return;
      refreshDashTracks();
      setStatus('DASH stream ready');
      if (pendingAutoplay) { pendingAutoplay = false; video.play().catch(() => setStatus('Playback was blocked; press play to start', true)); }
    });
    player.on(dash.MediaPlayer.events.ERROR, (event) => {
      if (generation !== sourceGeneration || dashInstance !== player) return;
      if (!tryProxyFallback(url, label, options)) setStatus(dashErrorMessage(event), true);
    });
    player.initialize(video, url, false);
  };

  const refreshAudioTracks = () => {
    if (dashInstance && typeof dashInstance.getTracksFor === 'function') {
      refreshDashTracks();
      return;
    }
    if (hlsInstance) {
      refreshHlsAudioTracks();
      return;
    }
    const tracks = Array.from(video.audioTracks || []);
    embeddedTrackModel.audio = tracks.map((track, index) => ({
      id: `native-audio-${track.id || index}`,
      label: trackLabel(track, index),
      language: track.language || track.lang || '',
      default: Boolean(track.default || track.enabled),
      backendIndex: index,
      backendTrack: track,
      ref: track,
    }));
    const selected = choosePreferredTrack(embeddedTrackModel.audio, readTrackPreference(PREFERRED_AUDIO_KEY));
    selectedAudioId = selected?.id || '';
    if (audioSelect) {
      audioSelect.replaceChildren();
      embeddedTrackModel.audio.forEach((track) => audioSelect.append(new Option(`Audio: ${track.label}`, track.id, track.id === selectedAudioId)));
      if (!embeddedTrackModel.audio.length) audioSelect.append(new Option('Audio: Original', ''));
      audioSelect.disabled = embeddedTrackModel.audio.length < 2;
    }
    if (selected && tracks.some((track) => track.enabled !== (track === selected.ref))) {
      preservePlaybackPosition(() => tracks.forEach((track) => { track.enabled = track === selected.ref; }));
    }
    const textTracks = Array.from(video.textTracks || []);
    embeddedTrackModel.subtitle = textTracks.map((track, index) => ({
      id: `native-subtitle-${track.id || index}`,
      label: trackLabel(track, index),
      language: track.language || track.lang || '',
      default: Boolean(track.default),
      forced: Boolean(track.forced),
      backendIndex: index,
      backendTrack: track,
      ref: track,
    }));
    const selectedSubtitle = choosePreferredTrack(embeddedTrackModel.subtitle, readTrackPreference(PREFERRED_SUBTITLE_KEY), { subtitle: true });
    const selectedId = externalSubtitleActive ? 'external' : (selectedSubtitle?.id || 'off');
    setSubtitleOptions(embeddedTrackModel.subtitle, selectedId);
    if (!externalSubtitleActive) {
      selectedSubtitleId = selectedSubtitle?.id || 'off';
      forceSubtitleTrackModes(selectedSubtitle || null);
    }
    bindNativeSubtitleTracks();
  };


  const setVideoSource = (source, label = source, options = {}) => {
    if (!source) return;
    beginSourceLoad();
    activeRemoteSource = false;
    proxyFallbackAttempted = false;
    activeVideoKey = sourceIdentity(options.originalSource || source);
    lastSavedTime = -1;
    pendingAutoplay = Boolean(options.autoplay);
    video.src = source;
    video.load();
    emptyState.hidden = true;
    playerCard.classList.remove('controls-idle');
    videoFileName.textContent = label || 'Video loaded';
    setStatus(`Loading ${label || 'video'}…`);
  };

  const loadRemoteSource = (source, label = source, options = {}) => {
    if (!window.SourceUtils) {
      setStatus('URL source support is unavailable.', true);
      return;
    }
    const validation = window.SourceUtils.validateSourceUrl(source, window.location.protocol);
    if (!validation.valid || validation.mixed_content) {
      setStatus(validation.error || 'Only http:// and https:// URLs are supported.', true);
      return;
    }
    videoUrl.value = validation.url;
    activeRemoteSource = true;
    if (!options.isProxy) { proxyFallbackAttempted = false; activeOriginalRemoteSource = validation.url; }
    const displayLabel = label || validation.url;
    if (validation.type === 'mpd') {
      loadDashSource(validation.url, displayLabel, options);
      return;
    }
    if (validation.type === 'm3u8') {
      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        setVideoSource(validation.url, displayLabel, options);
        usingHls = true;
        activeRemoteSource = true;
      }
      else loadHlsSource(validation.url, displayLabel, options);
      return;
    }
    setVideoSource(validation.url, displayLabel, options);
    activeRemoteSource = true;
  };

  const togglePlay = () => {
    if (!video.src && !hlsInstance && !dashInstance) return setStatus('Choose a video first', true);
    if (video.paused) video.play().catch(() => setStatus('Playback was blocked; press play to start', true));
    else video.pause();
  };

  const seekBy = (seconds) => {
    if (!video.src && !hlsInstance && !dashInstance) return;
    video.currentTime = clamp(video.currentTime + seconds, 0, video.duration || Infinity);
    revealControls();
  };

  const toggleMute = () => {
    video.muted = !video.muted;
    updateMuteButton();
  };

  const updateFullscreenButton = () => {
    const active = Boolean(document.fullscreenElement);
    fullscreenButton.textContent = active ? '⛶' : '⛶';
    fullscreenButton.setAttribute('aria-label', active ? 'Exit fullscreen' : 'Enter fullscreen');
    fullscreenButton.title = active ? 'Exit fullscreen' : 'Enter fullscreen';
    fullscreenButton.dataset.tooltip = active ? 'Exit fullscreen' : 'Enter fullscreen';
  };

  const toggleFullscreen = () => {
    if (document.fullscreenElement) {
      document.exitFullscreen()?.catch?.(() => setStatus('Could not exit fullscreen', true));
      return;
    }
    if (!videoStage.requestFullscreen) {
      setStatus('Fullscreen is not supported by this browser', true);
      return;
    }
    videoStage.requestFullscreen().catch(() => setStatus('Fullscreen was blocked by the browser', true));
  };

  const adjustSpeed = (direction) => {
    const options = Array.from(speedSelect.options);
    const currentIndex = speedSelect.selectedIndex;
    const nextIndex = clamp(currentIndex + direction, 0, options.length - 1);
    if (nextIndex === currentIndex) return;
    speedSelect.selectedIndex = nextIndex;
    speedSelect.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const isSubtitleFile = (file) => /\.(?:srt|vtt|webvtt|ass|ssa)$/i.test(file.name || '') || /(?:text\/vtt|text\/plain)/i.test(file.type || '');
  const isVideoFile = (file) => (file.type || '').startsWith('video/') || /\.(?:mkv|avi|mov|webm|mp4|m4v)$/i.test(file.name || '');

  const dispatchFiles = (input, files) => {
    try {
      const transfer = new DataTransfer();
      files.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    } catch {
      return false;
    }
  };

  const handleDroppedFiles = (fileList) => {
    const files = Array.from(fileList || []);
    const videos = files.filter(isVideoFile);
    const subtitles = files.filter(isSubtitleFile);
    const loadedVideo = videos.length && dispatchFiles(videoFile, videos);
    const loadedSubtitle = subtitles.length && dispatchFiles(subtitleFile, [subtitles[0]]);
    if (!loadedVideo && !loadedSubtitle) setStatus('Drop a video or subtitle file', true);
  };

  playButton.addEventListener('click', togglePlay);
  video.addEventListener('click', togglePlay);
  backButton.addEventListener('click', () => seekBy(-10));
  forwardButton.addEventListener('click', () => seekBy(10));
  seekBar.addEventListener('input', () => {
    isSeeking = true;
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (duration) video.currentTime = (Number(seekBar.value) / 1000) * duration;
    updateProgress();
    revealControls();
  });
  seekBar.addEventListener('change', () => { isSeeking = false; saveResumePosition(); });
  seekBar.addEventListener('pointermove', updateSeekPreview);
  seekBar.addEventListener('mousemove', updateSeekPreview);
  volumeBar.addEventListener('input', () => { video.volume = Number(volumeBar.value); video.muted = video.volume === 0; updateMuteButton(); revealControls(); });
  muteButton.addEventListener('click', toggleMute);
  speedSelect.addEventListener('change', () => { video.playbackRate = Number(speedSelect.value); revealControls(); });
  fullscreenButton.addEventListener('click', toggleFullscreen);
  document.addEventListener('fullscreenchange', updateFullscreenButton);
  audioSelect.addEventListener('change', () => selectAudioTrack(audioSelect.value));
  subtitleSelect?.addEventListener('change', () => selectSubtitleTrack(subtitleSelect.value));
  openVideoButton.addEventListener('click', () => videoFile.click());
  const openUrl = () => loadRemoteSource(videoUrl.value);
  openUrlButton.addEventListener('click', openUrl);
  videoUrl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      openUrl();
    }
  });
  [subtitleFontSize, subtitleColor, subtitleBackgroundOpacity, subtitlePosition].forEach((control) => {
    control.addEventListener('input', syncSubtitlePreview);
    control.addEventListener('change', syncSubtitlePreview);
  });
  window.setTimeout(syncSubtitlePreview, 0);

  window.addEventListener('playlistselect', (event) => {
    const { file, autoplay } = event.detail || {};
    if (!(file instanceof File)) return;
    const url = URL.createObjectURL(file);
    objectUrls.push(url);
    setVideoSource(url, file.name, { autoplay });
  });
  subtitleFile.addEventListener('change', () => {
    const file = subtitleFile.files[0];
    const generation = sourceGeneration;
    if (!file) return;
    subtitleFileName.textContent = file.name;
    const reader = new FileReader();
    reader.addEventListener('load', () => {
      if (generation !== sourceGeneration) return;
      loadSubtitleText(String(reader.result || ''), file.name);
    });
    reader.addEventListener('error', () => setStatus('Could not read subtitle file', true));
    reader.readAsText(file);
  });

  ['dragenter', 'dragover'].forEach((eventName) => {
    videoStage.addEventListener(eventName, (event) => {
      event.preventDefault();
      videoStage.classList.add('is-dragging');
      emptyState.classList.add('is-dragging');
    });
  });
  ['dragleave', 'drop'].forEach((eventName) => {
    videoStage.addEventListener(eventName, (event) => {
      event.preventDefault();
      if (eventName === 'drop') handleDroppedFiles(event.dataTransfer.files);
      videoStage.classList.remove('is-dragging');
      emptyState.classList.remove('is-dragging');
    });
  });
  playerCard.addEventListener('pointermove', revealControls);
  playerCard.addEventListener('focusin', revealControls);
  playerCard.addEventListener('click', revealControls);

  video.addEventListener('loadedmetadata', () => {
    restoreResumePosition();
    refreshAudioTracks();
    bindNativeSubtitleTracks();
    updateProgress();
    const hasNativeTracks = embeddedTrackModel.audio.length > 0 || embeddedTrackModel.subtitle.length > 0;
    const tracksExposed = typeof video.audioTracks !== 'undefined' || embeddedTrackModel.subtitle.length > 0;
    setStatus(`${formatTime(video.duration)} ready${!usingHls && !usingDash && !hasNativeTracks && !tracksExposed ? ' — embedded audio/subtitle tracks are not exposed by this browser' : ''}`);
    if (pendingAutoplay) {
      pendingAutoplay = false;
      video.play().catch(() => setStatus('Playback was blocked; press play to start', true));
    }
  });
  video.addEventListener('durationchange', updateProgress);
  video.addEventListener('progress', updateProgress);
  video.addEventListener('timeupdate', () => { updateProgress(); saveResumePosition(); });
  video.addEventListener('play', updatePlayButton);
  video.addEventListener('pause', () => { updatePlayButton(); saveResumePosition(); });
  video.addEventListener('ended', () => {
    updatePlayButton();
    saveResumePosition();
    window.dispatchEvent(new CustomEvent('playerended'));
  });
  video.addEventListener('volumechange', updateMuteButton);
  video.addEventListener('error', () => {
    if (activeRemoteSource && !tryProxyFallback(activeOriginalRemoteSource, videoFileName.textContent)) setStatus('Could not load media. Check the URL, CORS policy, and network connection. If direct playback is blocked, try the server proxy.', true);
    else setStatus('The video could not be loaded by this browser', true);
  });
  document.addEventListener('keydown', (event) => {
    const target = event.target;
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey
      || target instanceof HTMLButtonElement || target instanceof HTMLInputElement
      || target instanceof HTMLSelectElement
      || target instanceof HTMLTextAreaElement || target.isContentEditable) return;

    if (event.code === 'Space' || event.key === ' ') {
      event.preventDefault();
      togglePlay();
      return;
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      seekBy(event.key === 'ArrowLeft' ? -10 : 10);
      return;
    }
    switch (event.key.toLowerCase()) {
      case 'm':
        event.preventDefault();
        toggleMute();
        break;
      case 'f':
        event.preventDefault();
        toggleFullscreen();
        break;
      case '[':
        event.preventDefault();
        adjustSpeed(-1);
        break;
      case ']':
        event.preventDefault();
        adjustSpeed(1);
        break;
      default:
        break;
    }
  });
  window.addEventListener('beforeunload', () => { saveResumePosition(); objectUrls.forEach((url) => URL.revokeObjectURL(url)); });

  const preloadServerStream = async () => {
    const match = window.location.pathname.match(/^\/p\/([^/]+)\/?$/);
    if (!match) return false;
    const slug = decodeURIComponent(match[1]);
    try {
       const response = await fetch(`/api/stream/${encodeURIComponent(slug)}`, { headers: { Accept: 'application/json' } });
      if (response.status === 404) { setStatus('This shared stream was not found or has expired.', true); return true; }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (payload.expired || (payload.expires_at && new Date(payload.expires_at) <= new Date())) { setStatus('This shared stream has expired.', true); return true; }
      if (payload.enabled === false || payload.is_public === false) { setStatus('This shared stream is disabled.', true); return true; }
      const source = payload.playback_url || payload.stream_url || payload.url || payload.source;
      if (!source) { setStatus('The shared stream is unavailable. Ask the owner to check its source.', true); return true; }
      loadRemoteSource(source, payload.title || payload.name || slug, { autoplay: false });
      if (payload.subtitle || payload.subtitle_url) loadSubtitleUrl(payload.subtitle || payload.subtitle_url);
    } catch (error) {
      setStatus(`Could not load shared stream: ${error.message}. Check the server or use a direct URL.`, true);
    }
    return true;
  };

  const initialVideo = params.get('video') || params.get('src');
  const initialSubtitle = params.get('subtitle') || params.get('subs');
  loadPlayerConfig();
  preloadServerStream().then((loaded) => {
    if (loaded) return;
    if (initialVideo) {
      if (window.SourceUtils?.isHttpUrl(initialVideo)) loadRemoteSource(initialVideo, initialVideo.split('/').pop() || initialVideo);
      else setVideoSource(initialVideo, initialVideo.split('/').pop() || initialVideo);
    }
    if (initialSubtitle) loadSubtitleUrl(initialSubtitle);
  });
  updatePlayButton();
  updateMuteButton();
  updateFullscreenButton();
})();
