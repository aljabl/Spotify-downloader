const token = document.querySelector('meta[name="app-token"]').content;
const $ = id => document.getElementById(id);
let state = null;
let shown = 100;
let lastRevision = -1;

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
async function action(path, data, success) {
  try { message(''); const result = await post(path, data); if (success) message(success(result)); await refresh(true); }
  catch (error) { message(error.message, true); }
}
function statusLabel(status) { return ({pending:'Not searched',needs_review:'Needs review',matched:'Matched',downloaded:'Downloaded',error:'Error'})[status] || status; }

function render() {
  if (!state) return;
  $('connection').textContent = state.connected ? 'Connected to Spotify' : 'Not connected';
  $('connect').textContent = state.connected ? 'Reconnect Spotify' : 'Connect Spotify';
  $('playlist-summary').textContent = state.playlist_title ? `${state.playlist_title} · ${state.tracks.length} songs` : '';
  $('folder').textContent = `Output folder: ${state.output_dir}`;
  $('match').disabled = state.job.running || !state.tracks.length;
  $('download').disabled = state.job.running || !state.tracks.some(t => t.selected);
  const job = state.job;
  $('job').hidden = !job.kind;
  $('job').textContent = job.kind ? `${job.running ? 'Working' : 'Finished'} · ${job.kind === 'match' ? 'Finding matches' : `Downloading ${job.kind.toUpperCase()}`} · ${job.done}/${job.total}${job.current ? ` · ${job.current}` : ''}${job.errors.length ? ` · ${job.errors.length} errors` : ''}` : '';
  const q = $('filter').value.trim().toLowerCase();
  const filter = $('status-filter').value;
  const found = state.tracks.map((track,index) => ({track,index})).filter(({track}) =>
    (filter === 'all' || track.status === filter) && (!q || `${track.title} ${track.artist}`.toLowerCase().includes(q)));
  const visible = found.slice(0,shown);
  $('tracks').innerHTML = visible.length ? visible.map(({track,index}) => {
    const choices = (track.candidates || []).map(candidate => `<option value="${escapeHtml(candidate.id)}" ${track.selected === candidate.id ? 'selected' : ''}>${escapeHtml(candidate.title)} · ${escapeHtml(candidate.channel)}${candidate.duration ? ` · ${Math.round(candidate.duration)}s` : ''}</option>`).join('');
    const selectedOutside = track.selected && !(track.candidates || []).some(c => c.id === track.selected);
    return `<article class="track" data-index="${index}"><div class="track-heading"><div><strong>${escapeHtml(track.title)}</strong><span>${escapeHtml(track.artist)}</span></div><span class="badge ${escapeHtml(track.status)}">${statusLabel(track.status)}</span></div>
      <div class="track-actions"><select class="candidate" aria-label="YouTube match for ${escapeHtml(track.title)}"><option value="">Choose a video…</option>${choices}${selectedOutside ? `<option value="${escapeHtml(track.selected)}" selected>Manual video · ${escapeHtml(track.selected)}</option>` : ''}</select><button class="save-choice secondary button">Use match</button></div>
      <div class="track-actions"><input class="manual" type="url" placeholder="Or paste a YouTube video link"><button class="save-manual text-button">Use link</button></div>${track.error ? `<p class="track-error">${escapeHtml(track.error)}</p>` : ''}</article>`;
  }).join('') : `<p class="empty">${state.tracks.length ? 'No songs match this filter.' : 'Load a playlist to see its songs.'}</p>`;
  $('visible-count').textContent = `${visible.length} shown of ${found.length} matching songs · ${state.tracks.filter(t=>t.status==='matched').length} matched · ${state.tracks.filter(t=>t.status==='downloaded').length} downloaded`;
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

$('playlist-form').addEventListener('submit', async event => {event.preventDefault(); await action('/api/playlist',{url:$('playlist-url').value}, r => `Loaded ${r.count} songs.`);});
$('match').addEventListener('click', () => action('/api/match',{}, r => `Searching ${r.count} songs. Review low-confidence results before downloading.`));
$('download').addEventListener('click', () => action('/api/download',{format:$('format').value}, r => `Downloading ${r.count} selected songs to the Spotify folder.`));
$('filter').addEventListener('input', () => {shown=100;render();});
$('status-filter').addEventListener('change', () => {shown=100;render();});
$('show-more').addEventListener('click', () => {shown+=100;render();});
$('tracks').addEventListener('click', async event => {
  const card = event.target.closest('.track'); if (!card) return;
  const index = Number(card.dataset.index);
  if (event.target.matches('.save-choice')) {
    const video_id = card.querySelector('.candidate').value;
    if (!video_id) return message('Choose a video first.', true);
    await action('/api/choose',{index,video_id}, () => 'YouTube match saved.');
  } else if (event.target.matches('.save-manual')) {
    const video_id = card.querySelector('.manual').value.trim();
    if (!video_id) return message('Paste a YouTube video link first.', true);
    await action('/api/choose',{index,video_id}, () => 'YouTube link saved.');
  }
});
refresh(true); setInterval(() => refresh(), 2500);
