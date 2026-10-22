// Run with: node tests/remote_tracks.test.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

export async function runTests() {
  const code = await readFile(new URL('../remote_tracks.js', import.meta.url), 'utf8');
  class Media {
    constructor() { this.listeners = {}; this.currentTime = 35; this.volume = 0.7; this.muted = false; this.paused = false; this.readyState = 4; this.duration = 30; this.playbackRate = 1; }
    addEventListener(type, fn) { this.listeners[type] = fn; }
    pause() { this.paused = true; }
    play() { this.paused = false; return Promise.resolve(); }
    removeAttribute() {}
    load() {}
  }
  const window = {};
  let reply;
  const captions = [];
  const tracks = [];
  const messages = [];
  const fakeURL = { createObjectURL: () => 'blob:test', revokeObjectURL: () => {} };
  vm.runInNewContext(code, {
    window, Audio: Media, AbortController, URL: fakeURL, URLSearchParams,
    fetch: (...args) => reply(...args),
  });
  const video = new Media();
  const player = new window.RemoteTracks(video, {
    tracks: (data) => tracks.push(data), captions: (...args) => captions.push(args),
    status: (message) => messages.push(message), audioFailed: () => {},
  });
  reply = async () => ({ ok: true, json: async () => ({ token: 'test', tracks: [], chunkSeconds: 30 }) });
  await player.inspect('https://example.com/test.mkv');
  assert.equal(tracks.length, 1);
  assert.equal(player.inspectionState, 'ready');
  reply = async () => ({ ok: false, json: async () => ({ error: 'Install FFmpeg and FFprobe on the server.' }) });
  await player.inspect('https://example.com/test.mkv');
  assert.equal(player.inspectionState, 'error');
  assert.match(player.inspectionMessage, /Install FFmpeg/);
  player.reset();
  assert.equal(player.inspectionState, 'idle');

  let finish;
  reply = () => new Promise((resolve) => { finish = resolve; });
  const stale = player.inspect('https://example.com/old.mkv');
  player.reset();
  finish({ ok: true, json: async () => ({ token: 'stale', tracks: [], chunkSeconds: 30 }) });
  await stale;
  assert.equal(player.token, null);
  assert.equal(tracks.length, 1);

  player.token = 'test'; player.chunkSeconds = 30;
  reply = async () => ({ ok: true, text: async () => 'WEBVTT\n', blob: async () => ({ audio: true }) });
  const subtitle = { index: 2 };
  player.selectSubtitle(subtitle);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(captions.at(-1)[1], 30, 'subtitle window follows absolute video time');
  player.selectSubtitle(null);
  assert.equal(player.subtitle, null);

  const audio = { index: 1 };
  player.selectAudio(audio);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(video.muted, true, `original sound is suppressed: ${messages.join('; ')}`);
  player.audio.onloadedmetadata();
  assert.equal(player.audio.currentTime, 5, 'extracted audio follows video position');
  assert.equal(player.audio.volume, 0.7);
  player.setMuted(true);
  assert.equal(player.audio.muted, true);
  player.setMuted(false);
  video.paused = true;
  player.syncAudio();
  assert.equal(player.audio.paused, true);
  player.stopAudio();
  assert.equal(video.muted, false, 'original mute state is restored');

  reply = () => new Promise((resolve) => { finish = resolve; });
  player.subCache.clear();
  player.selectSubtitle(subtitle);
  player.reset();
  const count = captions.length;
  finish({ ok: true, text: async () => 'stale captions' });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(captions.length, count, 'old source captions never replace new source state');
  return 'Remote-track discovery, stale responses, subtitle timing, audio synchronization, mute, and pause checks passed.';
}

await runTests();
