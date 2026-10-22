(() => {
  'use strict';

  const API = '/admin/api';
  const $ = (id) => document.getElementById(id);
  const els = Object.fromEntries([
    'adminStatus','addStreamButton','exportButton','importInput','streamSearch','streamRows','auditRows','refreshAuditButton',
    'streamsTab','auditTab','streamsPanel','auditPanel','streamDialog','streamForm','dialogTitle','closeDialogButton','cancelDialogButton',
    'saveStreamButton','streamId','streamName','streamSlug','streamUrl','streamType','streamExpiry','streamEnabled','formError','toast',
    'metricTotal','metricEnabled','metricExpiring','metricTested'
  ].map((id) => [id, $(id)]));
  const state = { streams: [], audit: [], editing: null };
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || window.CSRF_TOKEN || '';
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  const detectType = (url) => {
    const path = String(url || '').split(/[?#]/, 1)[0].toLowerCase();
    if (path.endsWith('.m3u8')) return 'HLS';
    if (path.endsWith('.mpd')) return 'DASH';
    if (path.endsWith('.webm')) return 'WebM';
    if (path.endsWith('.mp4')) return 'MP4';
    return 'Stream';
  };
  const normalize = (s) => {
    const source = s.source_url || s.url || s.source || '';
    return { ...s, id: s.id, title: s.title || s.name || s.slug || s.id || 'Untitled stream', slug: s.slug || '', source_url: source, source_type: s.source_type || s.type || detectType(source), is_public: s.is_public !== false, view_count: Number(s.view_count || 0), expires_at: s.expires_at || s.expiresAt || '' };
  };
  const setStatus = (message, error = false) => { els.adminStatus.textContent = message; els.adminStatus.classList.toggle('error', error); };
  const toast = (message, error = false) => { els.toast.textContent = message; els.toast.hidden = false; els.toast.classList.toggle('error', error); clearTimeout(toast.timer); toast.timer = setTimeout(() => { els.toast.hidden = true; }, 4000); };
  const api = async (path, options = {}) => {
    const method = (options.method || 'GET').toUpperCase();
    const headers = { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}) };
    if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = csrf();
    const response = await fetch(`${API}${path}`, { ...options, method, headers: { ...headers, ...(options.headers || {}) }, credentials: 'same-origin' });
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
    if (!response.ok) throw new Error(payload?.error || payload?.message || `Request failed (${response.status})`);
    return payload;
  };
  const listPayload = (payload, key) => Array.isArray(payload) ? payload : (Array.isArray(payload?.[key]) ? payload[key] : []);
  const dateValue = (value) => { if (!value) return ''; const d = new Date(value); return Number.isFinite(d.valueOf()) ? d : null; };
  const formatDate = (value) => { const d = dateValue(value); return d ? d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Never'; };
  const inputDate = (value) => { const d = dateValue(value); if (!d) return ''; const p = (n) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
  const status = (stream) => { if (!stream.is_public) return ['Disabled', '']; const d = dateValue(stream.expires_at); if (d && d <= new Date()) return ['Expired', 'expired']; if (d && d - Date.now() < 7 * 86400000) return ['Expiring soon', 'soon']; return ['Enabled', 'enabled']; };
  const renderMetrics = () => { els.metricTotal.textContent = state.streams.length; els.metricEnabled.textContent = state.streams.filter((s) => status(s)[1] === 'enabled').length; els.metricExpiring.textContent = state.streams.filter((s) => status(s)[1] === 'soon').length; els.metricTested.textContent = state.streams.filter((s) => s.last_tested_at || s.tested_at).length; };
  const renderStreams = () => {
    const query = els.streamSearch.value.trim().toLowerCase();
    const rows = state.streams.filter((s) => !query || `${s.title} ${s.slug} ${s.source_type}`.toLowerCase().includes(query));
    if (!rows.length) { els.streamRows.innerHTML = '<tr><td colspan="6" class="empty">No streams match your search.</td></tr>'; return; }
    els.streamRows.innerHTML = rows.map((s) => { const [label, cls] = status(s); const share = `${location.origin}/p/${encodeURIComponent(s.slug || s.id)}`; const tested = s.last_tested_at || s.tested_at; return `<tr><td><span class="stream-name">${escapeHtml(s.title)}</span><span class="stream-slug">${escapeHtml(s.slug || s.id)}</span></td><td>${escapeHtml(s.source_type)}</td><td><span class="badge ${cls}">${escapeHtml(label)}</span>${tested ? `<small class="tested">Tested ${escapeHtml(formatDate(tested))}</small>` : ''}</td><td>${escapeHtml(formatDate(s.expires_at))}</td><td>${escapeHtml(s.view_count)}</td><td><div class="row-actions"><button class="row-action" data-action="edit" data-id="${escapeHtml(s.id)}">Edit</button><button class="row-action" data-action="toggle" data-id="${escapeHtml(s.id)}">${s.is_public ? 'Disable' : 'Enable'}</button><button class="row-action" data-action="expiry" data-id="${escapeHtml(s.id)}">${s.expires_at ? 'Expiry' : 'Set expiry'}</button><button class="row-action" data-action="copy" data-url="${escapeHtml(share)}">Copy link</button><button class="row-action" data-action="test" data-id="${escapeHtml(s.id)}">Test</button><button class="row-action" data-action="delete" data-id="${escapeHtml(s.id)}">Delete</button></div></td></tr>`; }).join('');
  };
  const renderAudit = () => { const rows = state.audit.slice(0, 100); els.auditRows.innerHTML = rows.length ? rows.map((a) => `<li class="audit-item"><time class="audit-time">${escapeHtml(formatDate(a.created_at || a.timestamp))}</time><span>${escapeHtml(a.message || a.action || 'Updated stream')}${a.stream_id ? ` <code>${escapeHtml(a.stream_id)}</code>` : ''}</span><span class="audit-actor">${escapeHtml(a.actor || 'Admin')}</span></li>`).join('') : '<li class="empty">No activity recorded.</li>'; };
  const render = () => { renderMetrics(); renderStreams(); renderAudit(); };
  const load = async () => { setStatus('Loading streams…'); try { state.streams = listPayload(await api('/streams'), 'streams').map(normalize); state.audit = listPayload(await api('/audit'), 'audit'); render(); setStatus(`${state.streams.length} stream${state.streams.length === 1 ? '' : 's'}`); } catch (e) { setStatus(e.message, true); els.streamRows.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(e.message)}</td></tr>`; } };
  const openForm = (stream = null) => { state.editing = stream; els.dialogTitle.textContent = stream ? 'Edit stream' : 'Add stream'; els.saveStreamButton.textContent = stream ? 'Save changes' : 'Save stream'; els.streamId.value = stream?.id || ''; els.streamName.value = stream?.title || ''; els.streamSlug.value = stream?.slug || ''; els.streamUrl.value = stream?.source_url || ''; els.streamExpiry.value = inputDate(stream?.expires_at); els.streamEnabled.checked = stream?.is_public !== false; updateType(); els.formError.hidden = true; if (!els.streamDialog.open) els.streamDialog.showModal(); els.streamName.focus(); };
  const closeForm = () => { if (els.streamDialog.open) els.streamDialog.close(); state.editing = null; };
  const updateType = () => { els.streamType.textContent = `Type: ${detectType(els.streamUrl.value)}`; };
  const save = async (event) => { event.preventDefault(); els.formError.hidden = true; const source = els.streamUrl.value.trim(); try { const parsed = new URL(source, location.href); if (!/^https?:$/.test(parsed.protocol)) throw new Error('Use an http:// or https:// source URL.'); const payload = { title: els.streamName.value.trim(), source_url: source, source_type: detectType(source), poster_url: null, subtitle_url: null, duration: null, is_public: els.streamEnabled.checked, slug: els.streamSlug.value.trim(), expires_at: els.streamExpiry.value ? new Date(els.streamExpiry.value).toISOString() : null }; const id = els.streamId.value; await api(id ? `/streams/${encodeURIComponent(id)}` : '/streams', { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) }); closeForm(); toast(id ? 'Stream updated' : 'Stream created'); await load(); } catch (e) { els.formError.textContent = e.message; els.formError.hidden = false; } };
  const copy = async (url) => { try { await navigator.clipboard.writeText(url); toast('Share link copied'); } catch { window.prompt('Copy this share link:', url); } };
  const action = async (event) => { const button = event.target.closest('[data-action]'); if (!button) return; const stream = state.streams.find((s) => String(s.id) === String(button.dataset.id)); const kind = button.dataset.action; try { if (kind === 'copy') return copy(button.dataset.url); if (kind === 'edit' || kind === 'expiry') return openForm(stream); if (!stream) return; if (kind === 'delete' && !window.confirm(`Delete “${stream.title}”? This cannot be undone.`)) return; if (kind === 'toggle') await api(`/streams/${encodeURIComponent(stream.id)}/toggle`, { method: 'POST', body: JSON.stringify({ is_public: !stream.is_public }) }); else if (kind === 'test') { const result = await api('/test', { method: 'POST', body: JSON.stringify({ url: stream.source_url }) }); toast(result?.message || (result?.ok === false ? 'Test failed' : 'Source is reachable'), result?.ok === false); } else if (kind === 'delete') await api(`/streams/${encodeURIComponent(stream.id)}`, { method: 'DELETE' }); if (kind !== 'test') { toast(kind === 'delete' ? 'Stream deleted' : 'Stream updated'); await load(); } } catch (e) { toast(e.message, true); } };
  const setTab = (tab) => { const audit = tab === 'audit'; els.streamsPanel.hidden = audit; els.auditPanel.hidden = !audit; els.streamsTab.classList.toggle('is-active', !audit); els.auditTab.classList.toggle('is-active', audit); els.streamsTab.setAttribute('aria-selected', String(!audit)); els.auditTab.setAttribute('aria-selected', String(audit)); els.streamsTab.tabIndex = audit ? -1 : 0; els.auditTab.tabIndex = audit ? 0 : -1; };
  els.addStreamButton.addEventListener('click', () => openForm()); els.closeDialogButton.addEventListener('click', closeForm); els.cancelDialogButton.addEventListener('click', closeForm); els.streamForm.addEventListener('submit', save); els.streamUrl.addEventListener('input', updateType); els.streamSearch.addEventListener('input', renderStreams); els.streamRows.addEventListener('click', action); els.refreshAuditButton.addEventListener('click', load); els.streamDialog.addEventListener('click', (e) => { if (e.target === els.streamDialog) closeForm(); }); els.streamsTab.addEventListener('click', () => setTab('streams')); els.auditTab.addEventListener('click', () => setTab('audit')); [els.streamsTab, els.auditTab].forEach((tab) => tab.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); const next = tab === els.streamsTab ? els.auditTab : els.streamsTab; setTab(next === els.auditTab ? 'audit' : 'streams'); next.focus(); } }));
  els.exportButton.addEventListener('click', async () => { try { const response = await fetch(`${API}/export`, { credentials: 'same-origin', headers: { Accept: 'application/json' } }); if (!response.ok) throw new Error(`Export failed (${response.status})`); const blob = await response.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'streams-export.json'; a.click(); URL.revokeObjectURL(url); toast('Export downloaded'); } catch (e) { toast(e.message, true); } });
  els.importInput.addEventListener('change', async () => { const file = els.importInput.files?.[0]; if (!file) return; try { await api('/import', { method: 'POST', body: await file.text(), headers: { 'Content-Type': file.type || 'application/json' } }); toast('Import complete'); await load(); } catch (e) { toast(e.message, true); } finally { els.importInput.value = ''; } });
  load();
})();
