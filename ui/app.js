const token = document.querySelector('meta[name="app-token"]').content;
const $ = id => document.getElementById(id);
let state = null;
let shown = 100;
let lastRevision = -1;
const manualDrafts = new Map();
const candidateDrafts = new Map();

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
function message(value, error = false) {
  const box = $('notice'); box.textContent = value; box.hidden = !value; box.classList.toggle('error', error);
}
async function post(path, data = {}) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-App-Token':token}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}
async function action(path, data, success, busy = '') {
  try { message(busy); const result = await post(path, data); if (success) message(success(result)); await refresh(true); return true; }
  catch (error) { message(error.message, true); return false; }
}
function statusLabel(status) { return ({pending:'Not searched',needs_review:'Needs review',matched:'Matched',downloaded:'Downloaded',error:'Error',ignored:'Ignored'})[status] || status; }

function render() {
  if (!state) return;
  $('connection').textContent = state.connected ? 'Connected to Spotify' : 'Not connected';
  $('connect').textContent = state.connected ? 'Reconnect Spotify' : 'Connect Spotify';
  const ignoredCount = state.tracks.filter(t => t.ignored).length;
  $('playlist-summary').textContent = state.playlist_title ? `${state.playlist_title} · ${state.tracks.length} songs${ignoredCount ? ` · ${ignoredCount} ignored` : ''}` : '';
  $('folder').textContent = `Output folder: ${state.output_dir}`;
  const job = state.job;
  const oneOff = state.one_off || {status:'idle'};
  $('one-off-submit').disabled = oneOff.status === 'working' || oneOff.status === 'downloading';
  const oneOffText = oneOff.status === 'working' ? 'Reading video details and downloading audio…' :
    oneOff.status === 'downloading' ? `Downloading ${oneOff.filename}…` :
    oneOff.status === 'done' ? `Saved ${oneOff.filename} to the Spotify folder.` :
    oneOff.status === 'existing' ? `${oneOff.filename} is already in the Spotify folder.` :
    oneOff.status === 'error' ? `Download failed: ${oneOff.error}` : '';
  $('one-off-status').textContent = oneOffText;
  $('one-off-status').hidden = !oneOffText;
  $('one-off-status').classList.toggle('error-text', oneOff.status === 'error');
  const searching = job.running && job.kind === 'match';
  $('match').textContent = searching ? (job.pausing ? 'Pausing search…' : 'Pause YouTube search') :
    state.tracks.some(t => !t.ignored && t.status === 'pending') ? 'Resume YouTube search' : 'Retry unresolved matches';
  $('match').disabled = (!searching && !state.tracks.some(t => !t.ignored)) || job.pausing || (job.running && !searching);
  $('download').disabled = state.job.running || !state.tracks.some(t => t.selected && !t.ignored);
  $('tag-wav').disabled = state.job.running || !state.tracks.some(t => !t.ignored);
  const stopReason = job.errors.find(error => error.startsWith('YouTube returned no audio formats'));
  const jobText = job.kind ? `${job.pausing ? 'Pausing' : job.running ? 'Working' : job.paused ? 'Paused' : 'Finished'} · ${job.kind === 'match' ? 'Finding YouTube matches' : job.kind === 'tag' ? 'Updating WAV tags' : `Downloading ${job.kind.toUpperCase()}`} · ${job.done}/${job.total}${job.current ? ` · ${job.current}` : ''}${job.errors.length ? ` · ${job.errors.length} errors` : ''}${stopReason ? ` · ${stopReason}` : ''}` : '';
  $('job').hidden = job.kind !== 'match';
  $('job').textContent = job.kind === 'match' ? jobText : '';
  const oneOffProgress = oneOff.status === 'working' ? 'Working · Reading YouTube details for one-off download' :
    oneOff.status === 'downloading' ? `Working · Downloading ${oneOff.filename}` : '';
  const downloadProgress = [job.kind && job.kind !== 'match' ? jobText : '', oneOffProgress].filter(Boolean).join('\n');
  $('download-job').textContent = downloadProgress;
  $('download-job').hidden = !downloadProgress;
  const q = $('filter').value.trim().toLowerCase();
  const filter = $('status-filter').value;
  const found = state.tracks.map((track,index) => ({track,index})).filter(({track}) =>
    (filter === 'all' || (track.ignored ? 'ignored' : track.status) === filter) && (!q || `${track.title} ${track.artist}`.toLowerCase().includes(q)));
  const visible = found.slice(0,shown);
  const openOverrides = new Set([...document.querySelectorAll('.track .override[open]')]
    .map(element => Number(element.closest('.track').dataset.index)));
  $('tracks').innerHTML = visible.length ? visible.map(({track,index}) => {
    const ignored = Boolean(track.ignored);
    const displayStatus = ignored ? 'ignored' : track.status;
    const chosen = candidateDrafts.get(index) ?? track.selected;
    const choices = (track.candidates || []).map(candidate => `<option value="${escapeHtml(candidate.id)}" ${chosen === candidate.id ? 'selected' : ''}>${escapeHtml(candidate.title)} · ${escapeHtml(candidate.channel)}${candidate.duration ? ` · ${Math.round(candidate.duration)}s` : ''}</option>`).join('');
    const selectedOutside = track.selected && !(track.candidates || []).some(c => c.id === track.selected);
    return `<article class="track ${ignored ? 'is-ignored' : ''}" data-index="${index}"><div class="track-heading"><div><strong>${escapeHtml(track.title)}</strong><span>${escapeHtml(track.artist)}</span></div><div class="track-controls"><span class="badge ${escapeHtml(displayStatus)}">${statusLabel(displayStatus)}</span><button type="button" class="ignore-toggle" aria-label="${ignored ? 'Restore' : 'Ignore'} ${escapeHtml(track.title)} by ${escapeHtml(track.artist)}">${ignored ? 'Restore' : 'Ignore'}</button></div></div>
      ${!ignored && track.status === 'pending' ? '<p class="match-hint">Waiting for automatic YouTube search…</p>' : ''}
      ${!ignored && (choices || selectedOutside) ? `<div class="track-actions"><select class="candidate" aria-label="YouTube match for ${escapeHtml(track.title)}"><option value="">Choose a video…</option>${choices}${selectedOutside ? `<option value="${escapeHtml(track.selected)}" ${chosen === track.selected ? 'selected' : ''}>Manual video · ${escapeHtml(track.selected)}</option>` : ''}</select><button class="save-choice secondary button">Use match</button></div>` : ''}
      ${!ignored && track.status === 'needs_review' && !choices ? '<p class="match-hint">No confident match found. You can paste a video link below.</p>' : ''}
      ${ignored ? '' : `<details class="override" ${openOverrides.has(index) ? 'open' : ''}><summary>Use a different YouTube link</summary><div class="track-actions"><input class="manual" type="url" placeholder="https://www.youtube.com/watch?v=…" value="${escapeHtml(manualDrafts.get(index) || '')}"><button class="save-manual text-button">Use link</button></div></details>${track.error ? `<p class="track-error">${escapeHtml(track.error)}</p>` : ''}`}</article>`;
  }).join('') : `<p class="empty">${state.tracks.length ? 'No songs match this filter.' : 'Load a playlist to see its songs.'}</p>`;
  $('visible-count').textContent = `${visible.length} shown of ${found.length} matching songs · ${state.tracks.filter(t=>!t.ignored && t.status==='matched').length} matched · ${state.tracks.filter(t=>!t.ignored && t.status==='downloaded').length} downloaded · ${ignoredCount} ignored`;
  $('show-more').hidden = found.length <= shown;
}
async function refresh(force = false) {
  try {
    const response = await fetch('/api/state', {cache:'no-store'});
    const next = await response.json();
    if (force || next.revision !== lastRevision || JSON.stringify(next.job) !== JSON.stringify(state?.job) || next.connected !== state?.connected) {
      state = next; lastRevision = next.revision; render();
    }
  } catch (error) { message('Local app connection lost. Keep the Terminal window open and reload this page.', true); }
}

$('playlist-form').addEventListener('submit', async event => {event.preventDefault(); const button = event.submitter; button.disabled = true; try { await action('/api/playlist',{url:$('playlist-url').value}, r => `Loaded ${r.count} songs. YouTube matching started automatically.`, 'Loading playlist from Spotify…'); } finally {button.disabled = false;}});
$('one-off-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const started = await action('/api/one-off', {url:$('one-off-url').value, title:$('one-off-title').value,
      artist:$('one-off-artist').value, format:$('one-off-format').value}, () => 'One-off download started.');
    if (started) {
      $('one-off-url').value = '';
      $('one-off-title').value = '';
      $('one-off-artist').value = '';
    }
  } finally { button.disabled = state?.one_off?.status === 'working' || state?.one_off?.status === 'downloading'; }
});
$('match').addEventListener('click', () => state?.job.running && state.job.kind === 'match' ?
  action('/api/pause',{}, () => 'Search will pause after the current song.') :
  action('/api/match',{}, r => `Searching ${r.count} unresolved songs.`));
$('download').addEventListener('click', () => action('/api/download',{format:$('format').value}, r => `Downloading ${r.count} selected songs to the Spotify folder.`));
$('tag-wav').addEventListener('click', () => action('/api/tag-wav',{}, r => `Updating tags in ${r.count} existing WAV files without using YouTube.`));
$('filter').addEventListener('input', () => {shown=100;render();});
$('status-filter').addEventListener('change', () => {shown=100;render();});
$('show-more').addEventListener('click', () => {shown+=100;render();});
$('tracks').addEventListener('input', event => {
  if (event.target.matches('.manual')) manualDrafts.set(Number(event.target.closest('.track').dataset.index), event.target.value);
});
$('tracks').addEventListener('change', event => {
  if (event.target.matches('.candidate')) candidateDrafts.set(Number(event.target.closest('.track').dataset.index), event.target.value);
});
$('tracks').addEventListener('click', async event => {
  const card = event.target.closest('.track'); if (!card) return;
  const index = Number(card.dataset.index);
  if (event.target.matches('.ignore-toggle')) {
    const ignored = !Boolean(state.tracks[index]?.ignored);
    await action('/api/ignore',{index,ignored}, () => ignored ? 'Song excluded from matching, downloads, and WAV tagging.' : 'Song restored to the pipeline.');
  } else if (event.target.matches('.save-choice')) {
    const video_id = card.querySelector('.candidate').value;
    if (!video_id) return message('Choose a video first.', true);
    if (await action('/api/choose',{index,video_id}, () => 'YouTube match saved.')) candidateDrafts.delete(index);
  } else if (event.target.matches('.save-manual')) {
    const video_id = card.querySelector('.manual').value.trim();
    if (!video_id) return message('Paste a YouTube video link first.', true);
    if (await action('/api/choose',{index,video_id}, () => 'YouTube link saved.')) manualDrafts.delete(index);
  }
});
refresh(true); setInterval(() => refresh(), 2500);
