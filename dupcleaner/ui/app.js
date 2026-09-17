/* Duplicate Cleaner — page logic.
 *
 * The scan runs in a Python thread and reports through api.poll(), the same
 * idiom as Server Keeper. Both language dictionaries are injected into the
 * page, so switching language is instant and never touches Python.
 */
'use strict';

const S = {
  lang: 'fa',
  theme: 'dark',
  folders: [],
  groups: [],          // [{gid, confidence, methods, primary, combined, reclaimable, files:[...]}]
  selected: new Set(),  // paths marked for deletion
  collapsed: new Set(), // gid of collapsed groups
  columns: [],
  sort: { key: null, dir: 1 },
  scanning: false,
  capped: 0,
  totalGroups: 0,
  ctxPath: null,
  ctxGid: null,
};

const ALL_COLUMNS = ['name', 'method', 'tags', 'album', 'size', 'duration',
                     'bitrate', 'ext', 'folder', 'date'];
// Six by default so each column is wide enough to read. 'method' is on the
// group row anyway, and 'date'/'album'/'ext' are a click away in the menu.
const DEFAULT_COLUMNS = ['name', 'tags', 'size', 'duration', 'bitrate',
                         'folder'];
const NUMERIC = new Set(['size', 'duration', 'bitrate', 'date']);

/* Relative column widths. The table is table-layout:fixed and these are
   normalized over whichever columns are visible, so the grid always fits the
   window instead of growing a horizontal scrollbar. */
const COL_WEIGHT = {
  name: 30, method: 16, tags: 22, album: 14, size: 10,
  duration: 8, bitrate: 10, ext: 6, folder: 20, date: 11,
};

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

function t(key, vars) {
  const table = window.I18N[S.lang] || window.I18N.fa;
  let text = (table && table[key]) || (window.I18N.fa[key]) || key;
  if (vars) {
    for (const k of Object.keys(vars)) {
      text = text.split('{' + k + '}').join(vars[k]);
    }
  }
  return text;
}

const FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';
function num(n) {
  const s = String(n);
  if (S.lang !== 'fa') return s;
  return s.replace(/[0-9]/g, (d) => FA_DIGITS[+d]);
}

/* Mirrors util.human_size so the page can format sizes without a round-trip.
   Unit names come from Python, so the two cannot drift. */
function humanBytes(n) {
  if (n == null) return '—';
  const units = (window.UNITS && window.UNITS[S.lang]) || ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(n), i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  let text = (i === 0 || value >= 100) ? value.toFixed(0) : value.toFixed(1);
  if (S.lang === 'fa') return num(text).replace('.', '٫') + ' ' + units[i];
  return text + ' ' + units[i];
}

function freedIn(group) {
  return group.files.reduce(
    (sum, f) => sum + (S.selected.has(f.path) ? f.size_raw : 0), 0);
}

function esc(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ─────────────────────────── chrome ─────────────────────────── */

function applyLang() {
  const html = document.documentElement;
  html.setAttribute('lang', S.lang);
  html.setAttribute('dir', S.lang === 'fa' ? 'rtl' : 'ltr');

  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-ph]').forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.querySelectorAll('#langSeg button').forEach((b) => {
    b.classList.toggle('on', b.dataset.lang === S.lang);
  });

  buildMethods();
  buildColumnMenu();
  renderFolders();
  render();
  refreshAbout();
  if (!S.scanning) $('phaseText').textContent = t('scan.ready');
  updateTotals();
}

function applyTheme() {
  let theme = S.theme;
  if (theme === 'system') {
    theme = window.matchMedia('(prefers-color-scheme: light)').matches
      ? 'light' : 'dark';
  }
  document.documentElement.setAttribute('data-theme', theme);
  document.querySelectorAll('#themeSeg button').forEach((b) => {
    b.classList.toggle('on', b.dataset.themeChoice === S.theme);
  });
}

let toastTimer = null;
function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

function status(text) { $('status').textContent = text; }

/* ─────────────────────────── settings ─────────────────────────── */

function readOptions() {
  const exts = $('customExts').value.replace(/,/g, ' ').split(/\s+/)
    .filter(Boolean).map((e) => (e.startsWith('.') ? e : '.' + e).toLowerCase());
  const methods = {};
  document.querySelectorAll('#methodList input[type=checkbox]').forEach((c) => {
    methods[c.dataset.method] = c.checked;
  });
  return {
    folders: S.folders.slice(),
    mode: document.querySelector('input[name=mode]:checked').value,
    custom_exts: exts,
    min_kb: +$('minSize').value || 0,
    tolerance: +$('tolerance').value || 0,
    workers: +$('workers').value || 8,
    recursive: $('recursive').checked,
    skip_hidden: $('skipHidden').checked,
    use_cache: $('useCache').checked,
    methods: methods,
  };
}

function applySettings(s) {
  S.lang = s.lang || 'fa';
  S.theme = s.theme || 'dark';
  S.folders = s.folders || [];
  S.columns = (s.columns && s.columns.length) ? s.columns : DEFAULT_COLUMNS.slice();
  const mode = document.querySelector('input[name=mode][value="' + (s.mode || 'audio') + '"]');
  if (mode) mode.checked = true;
  $('customExts').value = (s.custom_exts || []).join(' ') || '.mp3 .flac .m4a';
  $('minSize').value = s.min_kb == null ? 64 : s.min_kb;
  $('tolerance').value = s.tolerance == null ? 2 : s.tolerance;
  $('workers').value = s.workers || 8;
  $('recursive').checked = s.recursive !== false;
  $('skipHidden').checked = s.skip_hidden !== false;
  $('useCache').checked = s.use_cache !== false;
  $('toTrash').checked = s.to_trash !== false;
  $('autoLevel').value = String(s.auto_level == null ? 70 : s.auto_level);
  S.savedMethods = s.methods || {};
  syncMode();
}

function saveSettings() {
  const payload = readOptions();
  payload.lang = S.lang;
  payload.theme = S.theme;
  payload.columns = S.columns;
  payload.to_trash = $('toTrash').checked;
  payload.auto_level = +$('autoLevel').value;
  try { api().save_settings(payload); } catch (e) { /* closing */ }
}

function syncMode() {
  const custom = document.querySelector('input[name=mode]:checked').value === 'custom';
  $('customExts').disabled = !custom;
}

/* ─────────────────────────── builders ─────────────────────────── */

function confClass(pct) {
  return pct >= 95 ? 'c-high' : (pct >= 70 ? 'c-mid' : 'c-low');
}

function buildMethods() {
  const list = $('methodList');
  const previous = {};
  list.querySelectorAll('input').forEach((c) => { previous[c.dataset.method] = c.checked; });
  list.innerHTML = '';
  for (const m of window.METHODS) {
    const saved = S.savedMethods ? S.savedMethods[m.key] : undefined;
    const on = previous[m.key] !== undefined ? previous[m.key]
      : (saved !== undefined ? saved : true);
    const label = document.createElement('label');
    label.className = 'method';
    label.innerHTML =
      '<input type="checkbox" data-method="' + m.key + '"' + (on ? ' checked' : '') + '>' +
      '<div class="method-body">' +
      '<div class="method-title">' +
      '<span class="pct ' + confClass(m.confidence) + '">' + num(m.confidence) + '%</span>' +
      '<b>' + esc(t('method.' + m.key)) + '</b>' +
      '</div>' +
      '<div class="method-hint">' + esc(t('method.' + m.key + '.hint')) + '</div>' +
      '</div>';
    label.querySelector('input').addEventListener('change', saveSettings);
    list.appendChild(label);
  }
}

function buildColumnMenu() {
  const box = $('colToggles');
  box.innerHTML = '';
  for (const key of ALL_COLUMNS) {
    const label = document.createElement('label');
    label.className = 'check';
    label.innerHTML = '<input type="checkbox" data-col="' + key + '"' +
      (S.columns.includes(key) ? ' checked' : '') + '>' +
      '<span>' + esc(t('col.' + key)) + '</span>';
    box.appendChild(label);
  }
  box.querySelectorAll('input').forEach((c) => {
    c.addEventListener('change', () => {
      const key = c.dataset.col;
      if (c.checked) {
        if (!S.columns.includes(key)) {
          S.columns = ALL_COLUMNS.filter((k) => S.columns.includes(k) || k === key);
        }
      } else if (S.columns.length > 1) {
        S.columns = S.columns.filter((k) => k !== key);
      } else {
        c.checked = true;
        return;
      }
      render();
      saveSettings();
    });
  });
}

function renderFolders() {
  const list = $('folderList');
  list.innerHTML = '';
  for (const path of S.folders) {
    const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/);
    const name = parts[parts.length - 1] || path;
    const li = document.createElement('li');
    li.innerHTML =
      '<span class="fpath" title="' + esc(path) + '">' +
      '<span class="fname">' + esc(name) + '</span> — ' + esc(path) + '</span>' +
      '<button class="rm" title="' + esc(t('folders.remove')) + '">' +
      '<svg viewBox="0 0 24 24" class="ic"><path d="M6 6l12 12M18 6L6 18"/></svg></button>';
    li.querySelector('.rm').addEventListener('click', () => {
      S.folders = S.folders.filter((p) => p !== path);
      renderFolders();
      saveSettings();
    });
    list.appendChild(li);
  }
  $('folderEmpty').style.display = S.folders.length ? 'none' : '';
  $('folderCount').textContent = S.folders.length === 0 ? ''
    : (S.folders.length === 1 ? t('folders.count.one')
       : t('folders.count', { n: num(S.folders.length) }));
}

/* ─────────────────────────── the grid ─────────────────────────── */

function visibleGroups() {
  const needle = $('search').value.trim().toLowerCase();
  const filter = $('confFilter').value;
  return S.groups.filter((g) => {
    if (filter === 'sure' && g.confidence < 95) return false;
    if (filter === 'trusted' && g.confidence < 70) return false;
    if (filter === 'weak' && g.confidence >= 70) return false;
    if (!needle) return true;
    return g.files.some((f) =>
      f.path.toLowerCase().includes(needle) ||
      (f.tags || '').toLowerCase().includes(needle) ||
      (f.album || '').toLowerCase().includes(needle));
  });
}

function sortFiles(files) {
  if (!S.sort.key) return files;
  const key = S.sort.key;
  const dir = S.sort.dir;
  const copy = files.slice();
  copy.sort((a, b) => {
    let x = a[key], y = b[key];
    if (NUMERIC.has(key)) {
      x = a[key + '_raw'] != null ? a[key + '_raw'] : 0;
      y = b[key + '_raw'] != null ? b[key + '_raw'] : 0;
      return (x - y) * dir;
    }
    return String(x || '').localeCompare(String(y || '')) * dir;
  });
  return copy;
}

function headerCells() {
  let html = '<th class="c-sel"></th>';
  for (const key of S.columns) {
    const sorted = S.sort.key === key ? ' sorted' : '';
    const arrow = S.sort.key === key ? (S.sort.dir > 0 ? '▲' : '▼') : '▲';
    html += '<th class="c-' + key + (NUMERIC.has(key) ? ' num' : '') + sorted +
      '" data-sort="' + key + '">' + esc(t('col.' + key)) +
      '<span class="sort">' + arrow + '</span></th>';
  }
  return html;
}

function cellFor(file, key) {
  switch (key) {
    case 'name':
      return (file.best ? '<span class="best" title="' + esc(t('res.best')) +
              '">★</span>' : '') + '<bdi>' + esc(file.name) + '</bdi>';
    case 'folder': return '<bdi>' + esc(file.folder) + '</bdi>';
    case 'method': return esc(file.method_label);
    case 'tags': return esc(file.tags || '—');
    case 'album': return esc(file.album || '—');
    case 'size': return esc(file.size);
    case 'duration': return esc(file.duration);
    case 'bitrate': return '<bdi>' + esc(file.bitrate) + '</bdi>';
    case 'ext': return '<bdi>' + esc(file.ext) + '</bdi>';
    case 'date': return esc(file.date);
    default: return '';
  }
}

function renderColgroup() {
  const total = S.columns.reduce((sum, k) => sum + (COL_WEIGHT[k] || 10), 0);
  let html = '<col style="width:42px">';
  for (const key of S.columns) {
    const pct = ((COL_WEIGHT[key] || 10) / total * 100).toFixed(2);
    html += '<col style="width:calc((100% - 42px) * ' + pct + ' / 100)">';
  }
  let group = document.getElementById('cols');
  if (!group) {
    group = document.createElement('colgroup');
    group.id = 'cols';
    $('grid').insertBefore(group, $('grid').firstChild);
  }
  group.innerHTML = html;
}

function render() {
  const head = $('headRow');
  const body = $('body');
  renderColgroup();
  head.innerHTML = headerCells();
  head.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (S.sort.key === key) S.sort.dir *= -1;
      else { S.sort.key = key; S.sort.dir = 1; }
      render();
    });
  });

  const groups = visibleGroups();
  const span = S.columns.length + 1;
  const parts = [];
  let fileCount = 0;

  for (const g of groups) {
    fileCount += g.files.length;
    const marked = g.files.filter((f) => S.selected.has(f.path)).length;
    const closed = S.collapsed.has(g.gid) ? ' closed' : '';

    let label = esc(g.method_label);
    if (g.combined) {
      label += ' <span class="combined">(' + esc(t('method.combined')) + ')</span>';
    }

    parts.push(
      '<tr class="grow' + closed + '" data-gid="' + g.gid + '">' +
      '<td class="c-sel"><input type="checkbox" class="cbox gbox" data-gid="' + g.gid + '"' +
        (marked && marked === g.files.length ? ' checked' : '') +
        (marked && marked < g.files.length ? ' data-partial="1"' : '') + '></td>' +
      '<td colspan="' + (span - 1) + '"><div class="gtitle">' +
      '<svg viewBox="0 0 24 24" class="ic caret"><path d="M6 9l6 6 6-6"/></svg>' +
      '<span>' + esc(t('res.group', { n: num(g.gid) })) + '</span>' +
      '<span class="pct ' + confClass(g.confidence) + '">' +
        esc(t('res.confidence', { n: num(g.confidence) })) + '</span>' +
      '<span class="gmethod">' + label + '</span>' +
      '<span class="gmethod">' + esc(t('res.groupfiles', { n: num(g.files.length) })) + '</span>' +
      (marked ? '<span class="gmethod marked">' +
        esc(t('res.marked', { n: num(marked), size: humanBytes(freedIn(g)) })) + '</span>' : '') +
      '<span class="spacer"></span>' +
      '<span class="gmethod">' + esc(t('res.reclaim', { size: g.reclaimable })) + '</span>' +
      '</div></td></tr>');

    if (closed) continue;

    for (const f of sortFiles(g.files)) {
      const on = S.selected.has(f.path);
      parts.push(
        '<tr class="frow ' + (on ? 'drop' : 'keep') + '" data-path="' + esc(f.path) +
          '" data-gid="' + g.gid + '">' +
        '<td class="c-sel"><input type="checkbox" class="cbox fbox" data-path="' +
          esc(f.path) + '"' + (on ? ' checked' : '') + '></td>' +
        S.columns.map((k) =>
          '<td class="c-' + k + (NUMERIC.has(k) ? ' num' : '') + '" title="' +
          esc(k === 'folder' ? f.path : '') + '">' + cellFor(f, k) + '</td>').join('') +
        '</tr>');
    }
  }

  if (S.capped) {
    parts.unshift('<tr><td colspan="' + span + '" class="capped">' +
      esc(t('res.capped', { shown: num(S.groups.length), total: num(S.totalGroups) })) +
      '</td></tr>');
  }

  body.innerHTML = parts.join('');
  $('placeholder').classList.toggle('hide', groups.length > 0);
  $('placeholder').textContent = S.groups.length
    ? t('res.nothing') : t('res.noscan');
  $('showing').textContent = groups.length
    ? t('res.showing', { groups: num(groups.length), files: num(fileCount) }) : '';

  body.querySelectorAll('.fbox').forEach((c) => {
    c.addEventListener('change', () => toggleFile(c.dataset.path, c.checked));
  });
  body.querySelectorAll('.gbox').forEach((c) => {
    c.addEventListener('click', (ev) => { ev.stopPropagation(); toggleGroup(+c.dataset.gid); });
  });
  body.querySelectorAll('tr.grow').forEach((tr) => {
    tr.addEventListener('click', (ev) => {
      if (ev.target.closest('input')) return;
      const gid = +tr.dataset.gid;
      if (S.collapsed.has(gid)) S.collapsed.delete(gid); else S.collapsed.add(gid);
      render();
    });
  });
  body.querySelectorAll('tr.frow').forEach((tr) => {
    tr.addEventListener('dblclick', () => api().play(tr.dataset.path));
    tr.addEventListener('contextmenu', (ev) => {
      ev.preventDefault();
      openCtx(ev.clientX, ev.clientY, tr.dataset.path, +tr.dataset.gid);
    });
  });
}

/* ─────────────────────── selection handling ─────────────────────── */

function groupOf(gid) { return S.groups.find((g) => g.gid === gid); }

function toggleFile(path, on) {
  if (on) {
    const group = S.groups.find((g) => g.files.some((f) => f.path === path));
    if (group) {
      const survivors = group.files.filter(
        (f) => f.path !== path && !S.selected.has(f.path));
      if (!survivors.length) {
        toast(t('msg.lastfile'));
        render();
        return;
      }
    }
    S.selected.add(path);
  } else {
    S.selected.delete(path);
  }
  render();
  updateTotals();
}

function toggleGroup(gid) {
  const g = groupOf(gid);
  if (!g) return;
  const marked = g.files.filter((f) => S.selected.has(f.path)).length;
  if (marked) {
    g.files.forEach((f) => S.selected.delete(f.path));
  } else {
    g.files.forEach((f) => { if (!f.best) S.selected.add(f.path); });
  }
  render();
  updateTotals();
}

function updateTotals() {
  const marked = [];
  let bytes = 0;
  for (const g of S.groups) {
    for (const f of g.files) {
      if (S.selected.has(f.path)) { marked.push(f.path); bytes += f.size_raw; }
    }
  }
  const btn = $('deleteBtn');
  btn.disabled = marked.length === 0;
  btn.textContent = marked.length
    ? t('act.delete.n', { n: num(marked.length) }) : t('act.delete');
  if (marked.length) {
    status(t('msg.selected', { n: num(marked.length), size: humanBytes(bytes) }));
  }
  return marked;
}

/* ─────────────────────── context menu ─────────────────────── */

function openCtx(x, y, path, gid) {
  S.ctxPath = path;
  S.ctxGid = gid;
  const menu = $('ctx');
  menu.classList.add('open');
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = Math.min(x, window.innerWidth - w - 8) + 'px';
  menu.style.top = Math.min(y, window.innerHeight - h - 8) + 'px';
}

function closeCtx() { $('ctx').classList.remove('open'); }

/* ─────────────────────────── scanning ─────────────────────────── */

async function startScan() {
  if (S.scanning) return;
  const opts = readOptions();
  if (!opts.folders.length) { toast(t('scan.nofolders')); return; }
  if (!Object.values(opts.methods).some(Boolean)) { toast(t('scan.nomethods')); return; }
  if (opts.mode === 'custom' && !opts.custom_exts.length) { toast(t('scan.noexts')); return; }

  S.groups = [];
  S.selected.clear();
  S.collapsed.clear();
  S.capped = 0;
  render();
  saveSettings();

  const reply = await api().start_scan(opts);
  if (!reply || !reply.ok) { toast((reply && reply.message) || 'error'); return; }
  S.scanning = true;
  $('scanBtn').disabled = true;
  $('stopBtn').disabled = false;
  $('progressBar').style.width = '0%';
  status('');
}

function scanFinished() {
  S.scanning = false;
  $('scanBtn').disabled = false;
  $('stopBtn').disabled = true;
  document.querySelector('.progress').classList.remove('indeterminate');
  $('progressBar').style.width = '0%';
  $('phaseText').textContent = t('scan.ready');
  $('etaText').textContent = '';
}

function handleEvent(ev) {
  if (ev.t === 'progress') {
    const bar = document.querySelector('.progress');
    $('phaseText').textContent = t(ev.phase) +
      (ev.total ? '' : (ev.done ? ' — ' + t('scan.files', { n: num(ev.done) }) : ''));
    if (ev.total) {
      bar.classList.remove('indeterminate');
      $('progressBar').style.width = Math.round(100 * ev.done / ev.total) + '%';
    } else {
      bar.classList.add('indeterminate');
    }
    $('etaText').textContent = ev.eta ? t('scan.eta', { t: ev.eta }) : '';
  } else if (ev.t === 'done') {
    S.groups = ev.groups || [];
    S.totalGroups = ev.total_groups || S.groups.length;
    S.capped = ev.capped || 0;
    scanFinished();
    autoSelect();
    if (!S.groups.length) {
      status(t('scan.empty') + ' ' + t('scan.emptyhint', { n: num(ev.stats.scanned) }));
      toast(t('scan.empty'));
    } else {
      status(t('scan.summary', {
        files: num(ev.stats.scanned), groups: num(S.totalGroups),
        dupes: num(ev.stats.dupes), size: ev.stats.reclaimable,
      }) + (ev.stats.cache_hits
        ? ' · ' + t('scan.cachehit', { n: num(ev.stats.cache_hits) }) : ''));
    }
    refreshCache();
  } else if (ev.t === 'relabel') {
    // language changed: Python re-sent the rows with new dates, sizes, labels
    S.groups = ev.groups || S.groups;
    render();
    updateTotals();
  } else if (ev.t === 'cancelled') {
    scanFinished();
    status(t('scan.stopped'));
  } else if (ev.t === 'failed') {
    scanFinished();
    status(ev.message || 'error');
    toast(ev.message || 'error');
  }
}

async function pollLoop() {
  for (;;) {
    try {
      const events = await api().poll();
      for (const ev of events) handleEvent(ev);
    } catch (e) { /* window closing */ }
    await new Promise((r) => setTimeout(r, 140));
  }
}

/* ─────────────────────────── actions ─────────────────────────── */

async function autoSelect() {
  if (!S.groups.length) return;
  const threshold = +$('autoLevel').value;
  S.selected.clear();
  let auto = 0, skipped = 0;
  for (const g of S.groups) {
    if (g.confidence < threshold) { skipped++; continue; }
    auto++;
    for (const f of g.files) if (!f.best) S.selected.add(f.path);
  }
  render();
  updateTotals();
  let text = t('msg.autodone', { auto: num(auto) });
  if (skipped) text += ' ' + t('msg.autoskipped', { n: num(skipped) });
  status(text + ' ' + t('msg.review'));
}

async function doDelete() {
  const marked = [];
  for (const g of S.groups) {
    for (const f of g.files) if (S.selected.has(f.path)) marked.push(f.path);
  }
  if (!marked.length) return;
  const reply = await api().delete_files(marked, $('toTrash').checked);
  if (!reply) return;
  if (reply.cancelled) return;
  if (reply.deleted && reply.deleted.length) {
    const gone = new Set(reply.deleted);
    for (const g of S.groups) g.files = g.files.filter((f) => !gone.has(f.path));
    S.groups = S.groups.filter((g) => g.files.length > 1);
    S.groups.forEach((g, i) => { g.gid = i + 1; });
    gone.forEach((p) => S.selected.delete(p));
    render();
    updateTotals();
    status(t('msg.deleted', { n: num(reply.deleted.length), size: reply.freed }));
    toast(t('msg.deleted', { n: num(reply.deleted.length), size: reply.freed }));
  }
  if (reply.failed && reply.failed.length) {
    toast(t('dlg.failed', { n: num(reply.failed.length) }));
  }
}

async function refreshCache() {
  try {
    const info = await api().cache_info();
    $('cacheInfo').textContent = t('ui.cache.rows',
      { n: num(info.rows), size: info.human });
  } catch (e) { /* ignore */ }
}

function refreshAbout() {
  $('aboutText').textContent = t('ui.version', { v: num(window.APP_VERSION) });
  $('warnText').textContent = window.HAVE_MUTAGEN ? '' : t('ui.nomutagen');
}

/* ─────────────────────────── wiring ─────────────────────────── */

function wire() {
  $('addFolder').addEventListener('click', async () => {
    const picked = await api().pick_folder();
    if (picked) {
      for (const p of picked) if (!S.folders.includes(p)) S.folders.push(p);
      renderFolders();
      saveSettings();
    }
  });
  $('clearFolders').addEventListener('click', () => {
    S.folders = []; renderFolders(); saveSettings();
  });

  document.querySelectorAll('input[name=mode]').forEach((r) => {
    r.addEventListener('change', () => { syncMode(); saveSettings(); });
  });
  ['minSize', 'tolerance', 'workers', 'customExts', 'recursive', 'skipHidden',
   'useCache', 'toTrash', 'autoLevel'].forEach((id) => {
    $(id).addEventListener('change', saveSettings);
  });

  $('scanBtn').addEventListener('click', startScan);
  $('stopBtn').addEventListener('click', () => {
    api().cancel_scan();
    $('phaseText').textContent = t('scan.stopping');
  });

  $('search').addEventListener('input', render);
  $('confFilter').addEventListener('change', render);

  $('colBtn').addEventListener('click', (ev) => {
    ev.stopPropagation();
    $('colDrop').classList.toggle('open');
  });

  $('autoBtn').addEventListener('click', autoSelect);
  $('clearSel').addEventListener('click', () => {
    S.selected.clear(); render(); updateTotals(); status('');
  });
  $('expandAll').addEventListener('click', () => { S.collapsed.clear(); render(); });
  $('collapseAll').addEventListener('click', () => {
    S.groups.forEach((g) => S.collapsed.add(g.gid)); render();
  });
  $('exportBtn').addEventListener('click', async () => {
    const reply = await api().export_report(Array.from(S.selected));
    if (reply && reply.ok) toast(t('msg.exported'));
    else if (reply && reply.message) toast(reply.message);
  });
  $('deleteBtn').addEventListener('click', doDelete);

  // language and theme
  document.querySelectorAll('#langSeg button').forEach((b) => {
    b.addEventListener('click', () => {
      S.lang = b.dataset.lang;
      api().set_lang(S.lang);
      applyLang();
      saveSettings();
      refreshCache();
    });
  });
  document.querySelectorAll('#themeSeg button').forEach((b) => {
    b.addEventListener('click', () => {
      S.theme = b.dataset.themeChoice;
      applyTheme();
      saveSettings();
    });
  });
  window.matchMedia('(prefers-color-scheme: light)')
    .addEventListener('change', () => { if (S.theme === 'system') applyTheme(); });

  // settings sheet
  $('settingsBtn').addEventListener('click', () => {
    $('sheet').classList.add('open');
    $('sheetBackdrop').classList.add('open');
    refreshCache();
  });
  const closeSheet = () => {
    $('sheet').classList.remove('open');
    $('sheetBackdrop').classList.remove('open');
  };
  $('sheetClose').addEventListener('click', closeSheet);
  $('sheetBackdrop').addEventListener('click', closeSheet);
  $('clearCache').addEventListener('click', async () => {
    await api().clear_cache();
    refreshCache();
    toast(t('msg.cachecleared'));
  });

  // context menu
  $('ctx').querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', async () => {
      const act = b.dataset.act;
      const path = S.ctxPath;
      const g = groupOf(S.ctxGid);
      closeCtx();
      if (!path) return;
      if (act === 'play') api().play(path);
      else if (act === 'reveal') api().reveal(path);
      else if (act === 'copy') {
        await navigator.clipboard.writeText(path).catch(() => api().copy_path(path));
        toast(t('msg.pathcopied'));
      } else if (act === 'keeponly' && g) {
        g.files.forEach((f) => {
          if (f.path === path) S.selected.delete(f.path);
          else S.selected.add(f.path);
        });
        render(); updateTotals();
      } else if (act === 'keep') toggleFile(path, false);
      else if (act === 'drop') toggleFile(path, true);
      else if (act === 'wholegroup' && g) {
        const ok = await api().confirm(t('dlg.wholegroup', { n: num(g.files.length) }));
        if (ok) { g.files.forEach((f) => S.selected.add(f.path)); render(); updateTotals(); }
      }
    });
  });

  document.addEventListener('click', () => {
    closeCtx();
    $('colDrop').classList.remove('open');
  });

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { closeCtx(); closeSheet(); }
    if (ev.key === 'F5' || (ev.ctrlKey && ev.key === 'r')) { ev.preventDefault(); startScan(); }
    if (ev.ctrlKey && ev.key === 'f') { ev.preventDefault(); $('search').focus(); }
  });

  // folder drag and drop
  let dragDepth = 0;
  window.addEventListener('dragenter', (ev) => {
    ev.preventDefault(); dragDepth++; $('drop').classList.add('on');
  });
  window.addEventListener('dragover', (ev) => ev.preventDefault());
  window.addEventListener('dragleave', () => {
    if (--dragDepth <= 0) { dragDepth = 0; $('drop').classList.remove('on'); }
  });
  window.addEventListener('drop', async (ev) => {
    ev.preventDefault();
    dragDepth = 0;
    $('drop').classList.remove('on');
    const paths = [];
    for (const file of Array.from(ev.dataTransfer.files || [])) {
      if (file.path) paths.push(file.path);
    }
    if (!paths.length) return;
    const folders = await api().folders_of(paths);
    for (const p of folders) if (!S.folders.includes(p)) S.folders.push(p);
    renderFolders();
    saveSettings();
  });
}

/* ─────────────────────────── boot ─────────────────────────── */

window.addEventListener('pywebviewready', async () => {
  const boot = await api().bootstrap();
  applySettings(boot.settings);
  applyTheme();
  applyLang();
  wire();
  status(t('msg.start'));
  refreshCache();
  pollLoop();
});
