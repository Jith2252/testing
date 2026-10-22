/* Server-assisted tracks for containers the browser cannot demux. */
(() => {
  'use strict';
  class RemoteTracks {
    constructor(video, callbacks) {
      this.video = video;
      this.callbacks = callbacks;
      this.generation = 0;
      this.audio = new Audio();
      this.audio.preload = 'auto';
      this.audio.addEventListener('error', () => {
        if (this.audioTrack) this.failAudio('Could not play the extracted audio. Select another track or Original.');
      });
      video.addEventListener('play', () => this.syncAudio());
      video.addEventListener('playing', () => this.syncAudio());
      video.addEventListener('pause', () => this.audio.pause());
      video.addEventListener('waiting', () => this.audio.pause());
      video.addEventListener('seeking', () => this.audio.pause());
      video.addEventListener('seeked', () => this.tick());
      video.addEventListener('ratechange', () => { this.audio.playbackRate = video.playbackRate; });
      video.addEventListener('timeupdate', () => this.tick());
      this.reset();
    }

    reset() {
      this.generation += 1;
      this.controller?.abort();
      this.controller = new AbortController();
      this.stopAudio();
      this.token = null;
      this.inspectionState = 'idle';
      this.inspectionMessage = '';
      this.subtitle = null;
      this.subtitleWindow = null;
      this.subtitlePending = null;
      this.subCache = new Map();
      this.audioCache = new Map();
      this.audioPending = new Map();
    }

    async inspect(url) {
      const generation = this.generation;
      this.inspectionState = 'loading';
      this.inspectionMessage = 'Inspecting embedded tracks on the server...';
      this.callbacks.status(this.inspectionMessage);
      try {
        const response = await fetch('/api/media/inspect', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }), signal: this.controller.signal,
        });
        let data;
        try { data = await response.json(); }
        catch { throw new Error('The server does not expose media inspection. Deploy the latest code and reload the page.'); }
        if (generation !== this.generation) return;
        if (!response.ok) throw new Error(data.error || 'Media inspection failed.');
        this.token = data.token;
        this.chunkSeconds = data.chunkSeconds;
        this.inspectionState = 'ready';
        this.inspectionMessage = `Server inspection found ${data.tracks.filter((track) => track.kind === 'subtitle').length} subtitle and ${data.tracks.filter((track) => track.kind === 'audio').length} audio tracks.`;
        this.callbacks.tracks(data.tracks);
      } catch (error) {
        if (generation === this.generation && error.name !== 'AbortError') {
          this.inspectionState = 'error';
          this.inspectionMessage = error.message;
          this.callbacks.status(this.inspectionMessage, true);
        }
      }
    }

    async fetchChunk(track, start) {
      const query = new URLSearchParams({ token: this.token, track: track.index, start });
      const response = await fetch(`/api/media/chunk?${query}`, { signal: this.controller.signal });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || 'Could not extract the media track.');
      }
      return response;
    }

    selectSubtitle(track) {
      this.subtitle = track;
      this.subtitleWindow = null;
      this.subtitlePending = null;
      this.callbacks.captions('', 0, track);
      if (track) this.tick();
    }

    stopAudio() {
      if (this.audioTrack) this.video.muted = this.originalMuted;
      this.audioTrack = null;
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
      if (this.audioUrl) URL.revokeObjectURL(this.audioUrl);
      this.audioUrl = null;
      this.audioWindow = null;
      this.loadingAudio = null;
    }

    selectAudio(track) {
      const muted = this.audioTrack ? this.audio.muted : this.video.muted;
      this.stopAudio();
      this.audioTrack = track;
      this.loadingAudio = null;
      if (track) {
        this.originalMuted = muted;
        this.video.muted = true;
        this.audio.muted = muted;
        this.audio.volume = this.video.volume;
        this.audio.playbackRate = this.video.playbackRate;
        this.tick();
      }
    }

    setMuted(muted) {
      this.audio.muted = muted;
      this.originalMuted = muted;
    }

    failAudio(message) {
      this.stopAudio();
      this.callbacks.audioFailed();
      this.callbacks.status(message, true);
    }

    syncAudio() {
      if (!this.audioTrack || this.audioWindow == null) return;
      const position = this.video.currentTime - this.audioWindow;
      if (position < 0 || position >= this.chunkSeconds) { this.audio.pause(); return; }
      this.audio.volume = this.video.volume;
      this.audio.playbackRate = this.video.playbackRate;
      if (Number.isFinite(this.audio.duration) && Math.abs(this.audio.currentTime - position) > 0.25) {
        this.audio.currentTime = Math.min(position, this.audio.duration);
      }
      if (!this.video.paused && !this.video.seeking && this.video.readyState >= 3) {
        this.audio.play().catch(() => {
          if (this.audioTrack && !this.video.paused) this.callbacks.status('Press play to allow the selected audio track.', true);
        });
      } else this.audio.pause();
    }

    async audioChunk(track, start) {
      const key = `${track.index}:${start}`;
      if (this.audioCache.has(key)) return this.audioCache.get(key);
      if (this.audioPending.has(key)) return this.audioPending.get(key);
      const generation = this.generation;
      const pending = this.fetchChunk(track, start).then((response) => response.blob()).then((blob) => {
        if (generation !== this.generation) return null;
        this.audioCache.set(key, blob);
        if (this.audioCache.size > 4) this.audioCache.delete(this.audioCache.keys().next().value);
        return blob;
      }).finally(() => { if (generation === this.generation) this.audioPending.delete(key); });
      this.audioPending.set(key, pending);
      return pending;
    }

    tick() {
      if (!this.token) return;
      const time = this.video.currentTime || 0;
      const start = Math.floor(time / this.chunkSeconds) * this.chunkSeconds;
      const generation = this.generation;
      const subtitle = this.subtitle;
      if (subtitle && this.subtitleWindow !== start) {
        const key = `${subtitle.index}:${start}`;
        if (this.subCache.has(key)) {
          this.subtitleWindow = start;
          this.callbacks.captions(this.subCache.get(key), start, subtitle);
        } else if (this.subtitlePending !== key) {
          this.subtitlePending = key;
          this.callbacks.captions('', start, subtitle);
          this.fetchChunk(subtitle, start).then((response) => response.text()).then((text) => {
            if (generation !== this.generation || this.subtitle !== subtitle || this.subtitlePending !== key) return;
            this.subCache.set(key, text);
            if (this.subCache.size > 8) this.subCache.delete(this.subCache.keys().next().value);
            this.subtitleWindow = start;
            this.callbacks.captions(text, start, subtitle);
          }).catch((error) => {
            if (generation === this.generation && error.name !== 'AbortError' && this.subtitle === subtitle) {
              this.subtitleWindow = start; // Avoid retry storms; seek or reselect to retry.
              this.callbacks.status(error.message, true);
            }
          });
        }
      }
      const track = this.audioTrack;
      if (!track) return;
      if (this.audioWindow !== start && this.loadingAudio !== `${track.index}:${start}`) {
        const key = `${track.index}:${start}`;
        this.loadingAudio = key;
        this.audio.pause();
        this.callbacks.status('Preparing selected audio...');
        this.audioChunk(track, start).then((blob) => {
          if (!blob || generation !== this.generation || this.audioTrack !== track || this.loadingAudio !== key) return;
          if (Math.floor(this.video.currentTime / this.chunkSeconds) * this.chunkSeconds !== start) return;
          if (this.audioUrl) URL.revokeObjectURL(this.audioUrl);
          this.audioUrl = URL.createObjectURL(blob);
          this.audioWindow = start;
          this.audio.src = this.audioUrl;
          this.audio.onloadedmetadata = () => this.syncAudio();
        }).catch((error) => {
          if (generation === this.generation && this.audioTrack === track && error.name !== 'AbortError') this.failAudio(error.message);
        }).finally(() => { if (this.loadingAudio === key) this.loadingAudio = null; });
      }
      this.syncAudio();
      if (time - start > this.chunkSeconds - 10) {
        this.audioChunk(track, start + this.chunkSeconds).catch(() => {});
      }
    }
  }
  window.RemoteTracks = RemoteTracks;
})();
