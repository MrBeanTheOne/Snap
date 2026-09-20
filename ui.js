let info = null;
const api = () => pywebview.api;
const curMode = () => document.querySelector('#modeSeg input:checked').value;
const MODES = ['mp4', 'mkv', 'mp3', 'm4a', 'flac', 'wav', 'opus'];
const HEIGHTS = [2160, 1440, 1080, 720, 480, 360];
const isVideo = m => m === 'mp4' || m === 'mkv';

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

const FRIENDLY = [
  [/sign in to confirm|too many requests|429/i, 'YouTube is rate-limiting this network — wait a bit and try again'],
  [/private video/i, 'This video is private'],
  [/members-only|join this channel/i, 'Members-only video — needs a channel membership'],
  [/age.?restrict|confirm your age/i, 'Age-restricted video'],
  [/video unavailable|no longer available|removed by/i, 'Video unavailable — removed or region-locked'],
  [/does not have a .*tab|has no .*tab/i, 'This channel doesn’t have that tab'],
  [/could not find.*cookies|could not copy.*cookie|failed to decrypt/i, 'Couldn’t read Edge’s YouTube login — is Edge installed and signed in?'],
  [/403.*forbidden/i, 'YouTube refused the download — try “check for updates” in Settings'],
  [/unsupported url|is not a valid url/i, 'That link doesn’t look like something Snap can download'],
  [/getaddrinfo|timed out|connection|network is unreachable/i, 'Network problem — check your connection'],
];
function friendly(e) {
  if (!e) return '';
  for (const [re, msg] of FRIENDLY) if (re.test(e)) return msg;
  return e.replace(/^ERROR:\s*/i, '');
}

function showTab(t) {
  for (const x of ['grab', 'dls', 'ch', 'lib', 'set']) {
    document.getElementById('tab-' + x).classList.toggle('on', x === t);
    document.getElementById('nav-' + x).classList.toggle('on', x === t);
  }
  if (t === 'lib') loadLibrary();
}

// ---- library ----
let LIB = [];

async function loadLibrary() {
  libGrid.innerHTML = '<div class="empty">Scanning…</div>';
  LIB = await api().get_library();
  renderLibrary();
}

function libCard(i, idx) {
  const cov = i.cover_url
    ? `<img class="cov" src="${esc(i.cover_url)}" alt="" loading="lazy">`
    : `<div class="nocov">${esc(i.ext)}</div>`;
  return `
    <div class="libcard" onclick="playLib(${idx})" oncontextmenu="event.preventDefault(); api().reveal(LIB[${idx}].path)"
         title="${esc(i.name)} — click to play, right-click to show in Explorer">
      ${cov}
      <div class="meta">
        <div class="n">${esc(i.name)}</div>
        <div class="f">${esc(i.ext.toUpperCase())}${i.folder ? ' · ' + esc(i.folder) : ''}</div>
      </div>
    </div>`;
}

function renderLibrary() {
  const q = libSearch.value.trim().toLowerCase();
  const idx = LIB.map((i, n) => [i, n])
    .filter(([i]) => !q || i.name.toLowerCase().includes(q) || i.folder.toLowerCase().includes(q));
  libCount.textContent = `${idx.length} file${idx.length === 1 ? '' : 's'}`;
  libGrid.innerHTML = idx.length
    ? idx.map(([i, n]) => libCard(i, n)).join('')
    : `<div class="empty">${q ? 'No matches.' : 'Nothing yet — everything you download shows up here.'}</div>`;
}

function playLib(n) {
  const it = LIB[n];
  if (!it) return;
  if (it.video) {
    vidEl.src = it.media_url;
    vidModal.style.display = 'flex';
    vidEl.play();
  } else {
    audTitle.textContent = it.name;
    audEl.src = it.media_url;
    audBar.style.display = 'flex';
    audEl.play();
  }
}

modeSeg.addEventListener('change', () => { qual.style.display = isVideo(curMode()) ? '' : 'none'; });

let chTab = 'videos';
chTabs.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b && b.dataset.t !== chTab) fetchInfo(b.dataset.t);
});

let fetchTimer = null;
function startFetchProgress() {
  fetchProg.style.display = '';
  fetchTxt.textContent = 'Loading…';
  fetchTimer = setInterval(async () => {
    const p = await api().get_fetch_pages();
    if (p > 1) fetchTxt.textContent = `Loading… ~${p * 30} videos found`;
  }, 500);
}
function stopFetchProgress() {
  clearInterval(fetchTimer);
  fetchTimer = null;
  fetchProg.style.display = 'none';
}

async function fetchInfo(tab) {
  const u = url.value.trim();
  if (!u) return;
  // an explicit /shorts|/streams in the pasted link picks the starting tab
  chTab = tab || (u.match(/\/(videos|shorts|streams)\b/) || [, 'videos'])[1];
  status.textContent = '';
  fetchBtn.disabled = true; fetchBtn.textContent = 'Fetching…';
  startFetchProgress();
  info = await api().get_info(u, chTab, 0, SET.page_size ?? 100);
  stopFetchProgress();
  fetchBtn.disabled = false; fetchBtn.textContent = 'Fetch';
  if (info.error) { status.textContent = friendly(info.error); preview.style.display = 'none'; grabEmpty.style.display = ''; return; }

  info.url = u;
  preview.style.display = 'block';
  grabEmpty.style.display = 'none';
  ptitle.textContent = info.search ? `Results for “${info.title}”` : info.title;
  const isChannel = info.playlist && /\/(@|channel\/|c\/|user\/)/.test(u);
  const watchable = info.playlist && !info.search;  // channels AND playlists can be synced
  watchBtn.style.display = watchable ? '' : 'none';
  watchBtn.textContent = isChannel ? 'Watch channel' : 'Sync playlist';
  autoWrap.style.display = watchable ? '' : 'none';
  chTabs.style.display = isChannel ? 'flex' : 'none';
  for (const b of chTabs.children) b.classList.toggle('on', b.dataset.t === chTab);

  clipA.value = ''; clipB.value = '';
  if (info.playlist) {
    thumb.style.display = 'none';
    modeSeg.style.display = 'none'; qual.style.display = 'none';
    clipWrap.style.display = 'none';
    plbar.style.display = 'flex'; plist.style.display = 'block';
    selAll.checked = true;
    plFilter.value = '';
    selAll.checked = !info.search;
    plist.innerHTML = info.entries.map(entryRowHTML).join('');
    moreBtn.textContent = `Load ${SET.page_size || 100} more`;
    moreWrap.style.display = info.more ? '' : 'none';
    dlnote.textContent = '';
    updateCount();
  } else {
    thumb.style.display = ''; thumb.src = info.thumbnail || '';
    modeSeg.style.display = ''; qual.style.display = isVideo(curMode()) ? '' : 'none';
    clipWrap.style.display = '';
    plbar.style.display = 'none'; plist.style.display = 'none'; plist.innerHTML = '';
    moreWrap.style.display = 'none';
    dlBtn.textContent = 'Download';
    dlnote.textContent = info.dl ? `✓ Already downloaded on ${info.dl}` : '';
    qual.innerHTML = info.heights.map(h => `<option value="${h}">${h}p</option>`).join('');
    if (info.heights.includes(SET.def_quality)) qual.value = SET.def_quality;
  }
}

function entryRowHTML(e) {
  // search results start unchecked (you usually want one); downloaded rows follow the skip toggle
  const on = info && info.search ? false : !(e.dl && skipDl.checked);
  return `
    <div class="entry${on ? '' : ' off'}">
      <input type="checkbox" ${on ? 'checked' : ''} onchange="this.closest('.entry').classList.toggle('off', !this.checked)">
      ${e.thumb ? `<img src="${esc(e.thumb)}" alt="" loading="lazy">` : '<div class="noimg">—</div>'}
      <div class="etitle" title="${esc(e.title)}">${esc(e.title)}</div>
      ${e.dl ? `<span class="dlbadge" title="downloaded ${esc(e.dl)}">✓ downloaded</span>` : ''}
      <select class="sm emode" onchange="this.nextElementSibling.style.visibility = isVideo(this.value) ? '' : 'hidden'">
        ${MODES.map(m => `<option value="${m}" ${SET.def_mode === m ? 'selected' : ''}>${m.toUpperCase()}</option>`).join('')}
      </select>
      <select class="sm equal" ${isVideo(SET.def_mode) ? '' : 'style="visibility:hidden"'}>${HEIGHTS.map(h => `<option value="${h}" ${SET.def_quality === h ? 'selected' : ''}>${h}p</option>`).join('')}</select>
    </div>`;
}

async function loadMore() {
  if (!info || !info.playlist) return;
  moreBtn.disabled = true; moreBtn.textContent = 'Loading…';
  const r = await api().get_info(info.url, chTab, info.entries.length, SET.page_size || 100);
  moreBtn.disabled = false; moreBtn.textContent = `Load ${SET.page_size || 100} more`;
  if (r.error || !r.playlist) { moreWrap.style.display = 'none'; return; }
  info.entries.push(...r.entries);
  plist.insertAdjacentHTML('beforeend', r.entries.map(entryRowHTML).join(''));
  filterList();  // keep an active filter applied to the new rows
  updateCount();
  moreWrap.style.display = r.more ? '' : 'none';
}

function entryRows() { return [...plist.querySelectorAll('.entry')]; }
// with a filter active, All / Apply act on the visible rows only
function visibleRows() { return entryRows().filter(r => r.style.display !== 'none'); }

function applySkip() {
  for (const r of entryRows()) {
    if (!r.querySelector('.dlbadge')) continue;
    const c = r.querySelector('input[type=checkbox]');
    c.checked = !skipDl.checked;
    r.classList.toggle('off', !c.checked);
  }
  updateCount();
}

function filterList() {
  const q = plFilter.value.toLowerCase();
  for (const r of entryRows())
    r.style.display = r.querySelector('.etitle').textContent.toLowerCase().includes(q) ? '' : 'none';
}

function toggleAll() {
  for (const r of visibleRows()) {
    r.querySelector('input[type=checkbox]').checked = selAll.checked;
    r.classList.toggle('off', !selAll.checked);
  }
  updateCount();
}

function applyAll() {
  for (const r of visibleRows()) {
    const m = r.querySelector('.emode'), q = r.querySelector('.equal');
    m.value = allMode.value;
    q.value = allQual.value;
    q.style.visibility = isVideo(m.value) ? '' : 'hidden';
  }
}

function updateCount() {
  if (!info || !info.playlist) return;
  const n = entryRows().filter(r => r.querySelector('input[type=checkbox]').checked).length;
  dlBtn.textContent = `Download selected (${n})`;
}
plist.addEventListener('change', updateCount);

function clearPreview() {
  url.value = ''; preview.style.display = 'none'; plist.innerHTML = ''; info = null;
  moreWrap.style.display = 'none';
  grabEmpty.style.display = '';
}

async function download() {
  if (!info) return;
  const s = subs.checked;
  if (info.convert) {
    await api().enqueue(info.url, curMode(), 1080, info.title, false, '', '', clipA.value, clipB.value);
    clearPreview();
    showTab('dls');
    return;
  }
  if (info.playlist) {
    const folder = info.search ? '' : info.title;  // search results stay loose in the root
    const rows = entryRows();
    for (let i = 0; i < rows.length; i++) {
      if (!rows[i].querySelector('input[type=checkbox]').checked) continue;
      const e = info.entries[i];
      await api().enqueue(e.url, rows[i].querySelector('.emode').value,
        rows[i].querySelector('.equal').value, e.title, s, e.thumb || '', folder);
    }
  } else {
    await api().enqueue(info.url, curMode(), qual.value, info.title, s, info.thumbnail || '',
      '', clipA.value, clipB.value);
  }
  clearPreview();
  showTab('dls');
}

async function watchChannel() {
  if (!info) return;
  await api().add_watch(info.url, info.title, allMode.value, allQual.value, subs.checked, auto.checked, [chTab]);
  clearPreview();
  showTab('ch');
}

const LABEL = { queued: 'Queued', downloading: '', converting: 'Converting…', cancelling: 'Cancelling…', done: 'Saved', error: 'Failed', cancelled: 'Cancelled' };

let jobsCache = [];
function copyUrl(id) {
  const j = jobsCache.find(x => x.id === id);
  if (j) navigator.clipboard.writeText(j.url);
}

function jobRow(j) {
  const active = !['done', 'error', 'cancelled'].includes(j.status);
  const btns =
    (active ? `<button class="mini ghost" onclick="api().cancel(${j.id})" title="Cancel">✕</button>` : '') +
    (j.status === 'done' && j.filepath ? `<button class="mini ghost" onclick="api().open_file(${j.id})">Open</button>` : '') +
    (!active ? `<button class="mini ghost" onclick="api().redo(${j.id})" title="Download again">↻</button>` : '') +
    `<button class="mini ghost icon" onclick="copyUrl(${j.id})" title="Copy link">
       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
     </button>`;
  const clip = j.clip_a || j.clip_b ? ` · ✂ ${esc(j.clip_a || '0:00')}–${esc(j.clip_b || 'end')}` : '';
  const stat = (j.status === 'downloading'
    ? `${j.percent}%  ${esc(j.speed)}${j.eta ? ' · ' + esc(j.eta) + ' left' : ''}`
    : `<b>${esc(LABEL[j.status] || j.status)}</b>${j.error ? ' — ' + esc(friendly(j.error)) : ''}`) + clip;
  const img = j.thumb ? `<img class="jt" src="${esc(j.thumb)}" alt="" loading="lazy">` : `<div class="noimg">${esc(j.mode)}</div>`;
  return `
    <div class="job ${j.status}">
      <div class="body">
        ${img}
        <div class="mid">
          <div class="title">${esc(j.title)}</div>
          <div class="stat">${j.mode.toUpperCase()}${isVideo(j.mode) ? ' ' + j.quality + 'p' : ''} · ${stat}</div>
        </div>
        <div class="btns">${btns}</div>
      </div>
      <div class="track"><div style="width:${j.status === 'done' ? 100 : j.percent}%"></div></div>
    </div>`;
}

const TABS = [['videos', 'Videos'], ['shorts', 'Shorts'], ['streams', 'Live']];
let watchCache = [], editWatch = null;

function renderWatches(list) {
  watches.innerHTML = list.map(watchRow).join('');
  chEmpty.style.display = list.length ? 'none' : '';
}
function openEdit(id) { editWatch = id; renderWatches(watchCache); }
function closeEdit() { editWatch = null; renderWatches(watchCache); }

async function saveEdit(id) {
  const p = document.getElementById('we-' + id);
  const tabs = [...p.querySelectorAll('.wtab:checked')].map(c => c.value);
  await api().update_watch(id, p.querySelector('.wmode').value,
    p.querySelector('.wqual').value, p.querySelector('.wsubs').checked,
    tabs.length ? tabs : ['videos']);
  closeEdit();
}

const isChannelUrl = u => /youtube\.com\/(@|channel\/|c\/|user\/)/.test(u || '');

function watchRow(w) {
  const chan = isChannelUrl(w.url);
  const tabs = w.tabs || ['videos'];
  const pend = w.pending.map(p => `
    <div class="newvid"><span>${esc(p.title)}</span>
      <button class="mini go" onclick='api().download_pending(${w.id}, ${JSON.stringify(p.url)})'>Download</button>
    </div>`).join('');
  const edit = w.id === editWatch ? `
    <div class="wedit" id="we-${w.id}">
      ${chan ? `<span class="lbl">Watch</span>
      ${TABS.map(([v, l]) => `<label class="chk"><input type="checkbox" class="wtab" value="${v}" ${tabs.includes(v) ? 'checked' : ''}>${l}</label>`).join('')}` : ''}
      <span class="lbl">save as</span>
      <select class="sm wmode">${MODES.map(m => `<option value="${m}" ${w.mode === m ? 'selected' : ''}>${m.toUpperCase()}</option>`).join('')}</select>
      <select class="sm wqual">${HEIGHTS.map(h => `<option value="${h}" ${w.quality == h ? 'selected' : ''}>${h}p</option>`).join('')}</select>
      <label class="chk"><input type="checkbox" class="wsubs" ${w.subs ? 'checked' : ''}>subs</label>
      <button class="mini go" onclick="saveEdit(${w.id})">Save</button>
      <button class="mini ghost" onclick="closeEdit()">Cancel</button>
    </div>` : '';
  const ava = w.thumb
    ? `<img class="wava" src="${esc(w.thumb)}" alt="" loading="lazy">`
    : `<div class="wava">${esc((w.title || '?').trim()[0].toUpperCase())}</div>`;
  return `
    <div class="watch">
      ${ava}
      <div class="wmain">
      <div class="top">
        <div class="wtitle">${w.pending.length ? '<span class="dot">●</span> ' : ''}${esc(w.title)}</div>
        <div class="side">
          <label class="chk"><input type="checkbox" ${w.auto ? 'checked' : ''} onchange="api().toggle_auto(${w.id})">auto</label>
          <button class="mini ghost" onclick="openEdit(${w.id})" title="Edit watch">✎ Edit</button>
          <button class="mini ghost" onclick="api().check_watch_now(${w.id})">Check now</button>
          <button class="mini ghost" onclick="api().remove_watch(${w.id})" title="Stop watching">✕</button>
        </div>
      </div>
      <div class="sub">${w.mode.toUpperCase()}${isVideo(w.mode) ? ' ' + w.quality + 'p' : ''} · ${chan ? tabs.join(' + ') : 'playlist'} · checked ${esc(w.checked || 'never')}</div>
      ${w.error ? `<div class="werr">${esc(friendly(w.error))}</div>` : ''}
      ${edit}
      ${w.pending.length > 1 ? `<div class="newvid"><span></span><button class="mini go" onclick='api().download_pending(${w.id}, "*")'>Download all ${w.pending.length}</button></div>` : ''}
      ${pend}
      </div>
    </div>`;
}

let statusFilter = 'all';
const FILT = {
  all: () => true,
  downloading: j => ['downloading', 'converting', 'cancelling'].includes(j.status),
  queued: j => j.status === 'queued',
  completed: j => ['done', 'error', 'cancelled'].includes(j.status),
};
pills.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || !b.dataset.f) return;
  statusFilter = b.dataset.f;
  for (const x of pills.children) if (x.dataset && x.dataset.f) x.classList.toggle('on', x === b);
});

async function togglePause() {
  renderPause(await api().toggle_pause());
}
function renderPause(paused) {
  pauseBtn.textContent = paused ? '▶ Resume queue' : '⏸ Pause queue';
  pauseBtn.classList.toggle('go', paused);
}

async function poll() {
  const [jobList, watchList, paused] = await Promise.all([api().get_jobs(), api().get_watches(), api().get_paused()]);
  jobsCache = jobList;
  const anyActive = jobList.some(j => !['done', 'error', 'cancelled'].includes(j.status));
  pauseBtn.style.display = anyActive || paused ? '' : 'none';
  renderPause(paused);
  const q = jobFilter.value.trim().toLowerCase();
  const shown = jobList.filter(FILT[statusFilter])
    .filter(j => !q || (j.title || '').toLowerCase().includes(q));
  jobs.innerHTML = shown.length
    ? shown.slice().reverse().map(jobRow).join('')
    : `<div class="empty">${q || statusFilter !== 'all' ? 'No downloads match.' : 'Nothing yet — grab something from the Search tab.'}</div>`;
  const active = jobList.filter(j => !['done', 'error', 'cancelled'].includes(j.status)).length;
  dlBadge.style.display = active ? '' : 'none';
  dlBadge.textContent = active;
  const pending = watchList.reduce((n, w) => n + w.pending.length, 0);
  chBadge.style.display = pending ? '' : 'none';
  chBadge.textContent = pending;
  watchCache = watchList;
  if (editWatch === null) renderWatches(watchList);  // don't wipe an open edit panel
}

let SET = {};
async function loadSettings() {
  SET = await api().get_settings();
  sWorkers.value = SET.workers; sMode.value = SET.def_mode; sQual.value = SET.def_quality;
  sSubs.checked = SET.def_subs; sBitrate.value = SET.mp3_bitrate;
  sClip.checked = SET.clip_auto; sInterval.value = SET.watch_interval;
  sPage.value = SET.page_size ?? 100;
  sNotify.checked = SET.notify !== false;
  sTray.checked = SET.tray !== false;
  sOrg.checked = SET.organize !== false;
  sMinTray.checked = SET.min_tray === true;
  sCookies.checked = SET.cookies === true;
  sAuto.checked = SET.autostart === true;
  sSubLang.value = SET.sub_lang || 'en';
  sMb.checked = SET.mb_tag !== false;
  applyDefaults();
}
function applyDefaults() {
  document.getElementById('m-' + SET.def_mode).checked = true;
  // the single-video quality select stays hidden while a playlist/channel preview is open
  if (!info || !info.playlist) qual.style.display = isVideo(SET.def_mode) ? '' : 'none';
  subs.checked = SET.def_subs;
  allMode.value = SET.def_mode; allQual.value = SET.def_quality;
}
async function saveSettings() {
  const patch = {
    workers: +sWorkers.value, def_mode: sMode.value, def_quality: +sQual.value,
    def_subs: sSubs.checked, mp3_bitrate: +sBitrate.value,
    clip_auto: sClip.checked, watch_interval: +sInterval.value,
    page_size: +sPage.value,
    notify: sNotify.checked, tray: sTray.checked, organize: sOrg.checked,
    min_tray: sMinTray.checked, cookies: sCookies.checked,
    autostart: sAuto.checked, sub_lang: sSubLang.value, mb_tag: sMb.checked,
  };
  Object.assign(SET, patch);
  applyDefaults();
  await api().set_settings(patch);
}

let lastAuto = '';
async function pasteClipboard() {
  if (SET.clip_auto === false) return;
  if (url.value.trim()) return;
  const t = await api().get_clipboard();
  if (/^https?:\/\//.test(t) && t !== lastAuto) {
    lastAuto = t;
    url.value = t;
    fetchInfo();
  }
}

// pasting or dropping a link fetches it right away — no Fetch click needed
url.addEventListener('paste', () => setTimeout(() => {
  if (/^https?:\/\//.test(url.value.trim())) fetchInfo();
}, 0));
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => {
  e.preventDefault();
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f && f.pywebviewFullPath) { showConvert(f.pywebviewFullPath, f.name); return; }
  const t = (e.dataTransfer.getData('text') || '').trim();
  if (/^https?:\/\//.test(t)) { url.value = t; showTab('grab'); fetchInfo(); }
});

// local file dropped on the window → convert with the bundled ffmpeg
function showConvert(path, name) {
  info = { convert: true, url: path, title: name };
  showTab('grab');
  preview.style.display = 'block';
  grabEmpty.style.display = 'none';
  thumb.style.display = 'none';
  ptitle.textContent = `Convert  ${name}`;
  dlnote.textContent = 'Local file — converts with ffmpeg (video formats remux, audio re-encodes)';
  plbar.style.display = 'none'; plist.style.display = 'none'; plist.innerHTML = '';
  moreWrap.style.display = 'none';
  chTabs.style.display = 'none';
  watchBtn.style.display = 'none'; autoWrap.style.display = 'none';
  modeSeg.style.display = ''; qual.style.display = 'none';
  clipWrap.style.display = ''; clipA.value = ''; clipB.value = '';
  dlBtn.textContent = 'Convert';
}

const STAGE_TXT = { checking: 'Checking…', downloading: 'Downloading', unpacking: 'Unpacking…', restarting: 'Restarting…' };

function updButton(label, run, withProgress) {
  const b = document.createElement('button');
  b.className = 'mini go';
  b.textContent = label;
  b.onclick = async () => {
    b.disabled = true; b.textContent = 'Updating…';
    const fill = updBar.firstElementChild;
    let timer = 0;
    if (withProgress) {
      fill.style.width = '0%';
      updBar.style.display = '';
      // the js_api call below blocks its own thread; pywebview serves this poll on another
      timer = setInterval(async () => {
        const p = await api().get_update_progress();
        fill.style.width = p.pct + '%';
        b.textContent = p.stage === 'downloading'
          ? `Downloading… ${p.pct}%` : (STAGE_TXT[p.stage] || 'Updating…');
      }, 400);
    }
    const res = await run();
    clearInterval(timer);
    b.remove();
    if (res.restart) {
      fill.style.width = '100%';
      updMsg.textContent = 'Update installed — Snap will restart itself now…';
      api().restart_app();
      return;
    }
    updBar.style.display = 'none';
    updMsg.textContent = res.manual ? 'That release has no installable build — opened the download page'
      : res.ok ? 'Updated — restart Snap to apply'
      : 'Update failed: ' + friendly(res.error);
    updMsg.title = res.error || '';  // the full reason is too long for one line
  };
  updMsg.appendChild(b);
}

function renderUpdate(r) {
  updMsg.innerHTML = ''; updMsg.title = '';
  setBadge.style.display = r.app_available ? '' : 'none';
  if (r.error) {
    updMsg.textContent = 'Couldn’t check for updates';
    updMsg.title = r.error;
    return;
  }
  if (r.available) updButton(`Update yt-dlp to ${r.latest}`, () => api().update_ytdlp());
  if (r.app_available) updButton(`Update Snap to ${r.app_latest}`, () => api().update_app(), true);
  if (!r.available && !r.app_available) updMsg.textContent = `Up to date (yt-dlp ${r.latest})`;
}

async function checkUpdate() {
  updMsg.innerHTML = 'checking…';
  renderUpdate(await api().check_update());
}

// the launch check runs on a background thread — show its result as soon as it lands
async function pickUpLaunchCheck(tries = 60) {
  const r = await api().get_update_info();
  if (r) renderUpdate(r);
  else if (tries) setTimeout(() => pickUpLaunchCheck(tries - 1), 500);
}

window.addEventListener('pywebviewready', async () => {
  outdir.textContent = await api().get_out_dir();
  ydlv.textContent = await api().get_version();
  appv.textContent = 'v' + await api().get_app_version();
  await loadSettings();
  const pending = await api().get_pending_url();  // snap:// link that launched the app
  if (pending) { url.value = pending; fetchInfo(); }
  else pasteClipboard();
  pickUpLaunchCheck();
  setInterval(poll, 500);
});
window.addEventListener('focus', () => window.pywebview && pasteClipboard());
