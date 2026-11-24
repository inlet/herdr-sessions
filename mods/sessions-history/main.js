import { View, div, image, svg } from 'gpui-kit';
import { v_flex, h_flex, InputState, Input, Scrollbar, VirtualListScrollHandle, v_virtual_list } from 'gpui-base';
import { exec, home, appHome, checking, theme, run, focused, spaces, panes, layout, knownAgents, settings,
  props, openOverlay, closeOverlay, notify } from 'herdr';
import { resumePlan, matchingSpace, filterSessions, spaceScope, repoFromGit, ago, sessionState, placeFromGit, placeFromPath,
  spaceFolders, spacePlaces, labelPlace, branchTail, sidebarSpace, COPIES, copyValues } from './logic.mjs';
import { sessionDetail } from './details.js';
import { segmented, iconButton, placeLine } from './ui.js';

const ROW = 46;
const SCOPES = [['folder', 'This folder'], ['space', 'This space'], ['all', 'All spaces']];

// The nodes from the layout's root down to this mod's panel block.
function panelPath(node, path = []) {
  if (!node) return null;
  path = [...path, node];
  if (node.id === 'session-history-panel') return path;
  for (const child of node.children ?? []) {
    const found = panelPath(child, path);
    if (found) return found;
  }
  return null;
}

// The harness's icon from the app's program table, as the Agents panel draws it: a one-color glyph or a picture.
function icon(p, size, color) {
  if (!p?.icon) return div().size(size).flex_none();
  return p.tint ? svg(p.icon).size(size).flex_none().text_color(color) : image(p.icon).w(size).h(size).flex_none();
}

export default class SessionHistory extends View {
  init(_props, cx) {
    // A copy opened as the copy menu (openMenu) is only that menu.
    if (props()?.copyMenu) { this.at = 0; this.handle = cx.focus_handle(); this.handle.focus(); return; }
    this.rows = []; this.scope = 'space'; this.loading = false; this.error = ''; this.pending = false; this.lastLaunch = 0;
    this.opened = null; // { session, summary, summaryLoading, context, contextLoading, error }
    this.listScroll = VirtualListScrollHandle.new();
    this.pointerY = null; // the pointer over the list, in list px from its first row's top, unscrolled
    this.active = new Set(); this.scanning = false;
    this.prefetched = new Set(); this.prefetching = false; this.prefetchAgain = false;
    this.summarizer = appHome() + '/mods/sessions-history/summarize.py';
    this.summaries = appHome() + '/data/sessions-history/summaries';
    this.metaCache = appHome() + '/data/sessions-history/meta.json';
    this.input = InputState.new({ placeholder: 'Filter sessions…' });
    this.input.on('change', (_e, cx) => cx.notify());
    this.helper = appHome() + '/mods/sessions-history/reader.py';
    this.places = {}; this.locating = false; // folder → { repo, tree }, from git
    this.repos = {}; // folder → its repository (a worktree's main checkout), from git (null outside one)
    this.programs = {}; // program key → its entry in the app's program table (name, icon)
    if (checking()) return;
    cx.spawn(async cx => {
      try { this.programs = Object.fromEntries((await knownAgents()).map(p => [p.key, p])); cx.notify(); }
      catch (_e) { /* no icons: the rows keep their place for one */ }
    });
    this.refresh(cx);
    cx.timer.every(60000, cx => { if (this.visible()) this.refresh(cx); });
    cx.timer.every(10000, cx => { if (this.visible()) this.scanActive(cx); });
  }
  // Shown now? Not while a node on the way is hidden, or a stack on the way shows another child
  // (the tab bar's side panel shows Files, Changes, GitHub or this).
  visible() {
    const path = panelPath(layout());
    return Boolean(path) && path.every((node, i) => !node.hidden &&
      (node.type !== 'stack' || (node.active ?? node.children?.[0]?.id) === path[i + 1]?.id));
  }
  scanActive(cx) {
    if (this.scanning) return;
    this.scanning = true;
    cx.spawn(async cx => {
      try {
        const r = await exec('python3', ['-I', this.helper, '--active'], { timeout: 10000 });
        if (r.code !== 0) throw new Error(r.stderr || 'Checking open sessions failed');
        this.active = new Set(JSON.parse(r.stdout));
        cx.notify();
      } catch (e) { this.error = String(e); cx.notify(); }
      finally { this.scanning = false; }
    });
  }
  refresh(cx) {
    if (this.loading) return;
    this.loading = true; this.error = ''; if (!this.rendering) cx.notify();
    this.scanActive(cx);
    cx.spawn(async cx => {
      try {
        // Every harness's sessions: Claude Code's, Codex's, Grok's and OpenCode's (reader.py knows where).
        const r = await exec('python3', ['-I', this.helper, this.metaCache], { timeout: 120000 });
        if (r.code !== 0) throw new Error(r.stderr || 'Loading sessions failed');
        this.rows = r.stdout.split('\n').filter(Boolean).map(line => JSON.parse(line)).sort((a,b) => b.activity-a.activity);
        this.prefetch(cx);
        this.locate(cx);
      } catch (e) { this.error = String(e); }
      this.loading = false; cx.notify();
    });
  }
  // The top 20 closed sessions of the list get their summary made in the background, one by one,
  // so opening one shows it at once. summarize.py skips sessions with a recap or a cached summary.
  prefetch(cx) {
    if (this.prefetching) { this.prefetchAgain = true; return; }
    const ids = filterSessions(this.rows, '', this.where(), this.repos)
      .filter(s => !this.active.has(s.id)).slice(0, 20).map(s => s.id).filter(id => !this.prefetched.has(id));
    if (!ids.length) return;
    this.prefetching = true;
    cx.spawn(async cx => {
      try {
        await exec('python3', ['-I', this.summarizer, '--prefetch', this.summaries, ...ids], { timeout: 600000 });
        for (const id of ids) this.prefetched.add(id);
      } catch (_e) { /* best effort: a right-click still makes its own */ }
      this.prefetching = false;
      if (this.prefetchAgain) { this.prefetchAgain = false; this.prefetch(cx); }
    });
  }
  // "repo › worktree" for every session folder that still exists and every space's folder (to find
  // the space a session ran in), asked of git once per folder, one at a time. Until it answers, and
  // for folders that are gone, the path tells what it can (placeFromPath). Runs on every draw, which
  // costs nothing once every folder is known; a new space's folder is asked the draw after it shows.
  locate(cx) {
    if (this.locating) return;
    const want = [...this.rows.filter(s => s.exists && s.cwd).map(s => s.cwd), ...spaceFolders(spaces(), panes())];
    const folders = [...new Set(want.filter(f => !(f in this.places)))];
    if (!folders.length) return;
    this.locating = true;
    cx.spawn(async cx => {
      for (const cwd of folders) {
        try {
          const r = await exec('git', ['rev-parse', '--path-format=absolute', '--git-common-dir', '--show-toplevel'], { cwd, timeout: 5000 });
          this.places[cwd] = r.code === 0 ? placeFromGit(r.stdout, cwd) : placeFromPath(cwd, home());
          this.repos[cwd] = r.code === 0 ? repoFromGit(r.stdout) : null;
        } catch (_e) { this.places[cwd] = placeFromPath(cwd, home()); }
      }
      this.locating = false; cx.notify();
    });
  }
  place(s) { return this.places[s.cwd] ?? placeFromPath(s.cwd, home()); }
  // What the scope shows, for filterSessions: This folder is exactly the focused terminal's folder,
  // This space the focused space's repository with its worktrees, or its folder (see spaceScope), All
  // spaces everything.
  // A terminal on another machine has no local folder: nothing.
  where() {
    if (this.scope === 'all') return null;
    const f = focused(), space = spaces().find(p => p.id === f?.space);
    if (space?.machine) return { folder: null };
    if (this.scope === 'folder' || !space) return { folder: f?.cwd ?? null };
    return spaceScope(space, panes(), this.repos, home());
  }
  // A click shows the session in the list's place; only its Resume button opens it.
  open(s, cx) {
    const d = this.opened = { session: s, summary: null, summaryLoading: true, context: null, contextLoading: true, error: '' };
    cx.notify();
    // Each answer lands only if this session is still the one shown.
    cx.spawn(async cx => {
      try {
        const r = await exec('python3', ['-I', this.summarizer, s.id, this.summaries], { timeout: 180000 });
        if (r.code !== 0) throw new Error(r.stderr || 'Loading the summary failed');
        d.summary = JSON.parse(r.stdout);
      } catch (e) { d.error = String(e); }
      d.summaryLoading = false; if (this.opened === d) cx.notify();
    });
    cx.spawn(async cx => {
      try {
        const r = await exec('python3', ['-I', this.helper, '--context', appHome() + '/data/sessions-history/context', s.id], { timeout: 10000 });
        if (r.code === 0) d.context = JSON.parse(r.stdout);
      } catch (_e) { /* no measurement: the row is left out */ }
      d.contextLoading = false; if (this.opened === d) cx.notify();
    });
  }
  back(cx) { this.opened = null; cx.notify(); }
  // The copy menu at the pointer, as the Agents panel's right-click menu: session ID, resume
  // command, transcript path, folder. Another copy of this mod draws it (menu) from its props.
  openMenu(s) {
    const { repo, tree } = labelPlace(this.place(s), spacePlaces(spaces(), panes(), this.places));
    const where = tree ? `${repo} › ${tree}` : repo, about = s.description || s.title;
    // Header, rows of 31, padding.
    openOverlay({ mod: 'sessions-history', kind: 'popup', width: 340, height: 26 + COPIES.length * 31 + 12,
      props: { copyMenu: true, title: about && about !== where ? `${where} · ${about}` : where, values: copyValues(s) } });
  }
  choose(item, cx) {
    const p = props(), value = p.values[item.key];
    if (!value) return;
    cx.write_to_clipboard(value);
    closeOverlay(p.overlay);
    // The menu is gone at once, so a short toast says what's on the clipboard.
    notify({ title: item.label.replace(/^Copy/, 'Copied'), body: value, level: 'success', timeout: 2500 });
  }
  menu(t) {
    const p = props(), pick = (i, cx) => this.choose(COPIES[i], cx);
    return v_flex().id('copy-menu').track_focus(this.handle).w_full().p(5).bg(t.popover).text_size(12.8)
      .on_key_down((e, cx) => {
        if (e.key === 'down') this.at = (this.at + 1) % COPIES.length;
        else if (e.key === 'up') this.at = (this.at + COPIES.length - 1) % COPIES.length;
        else if (e.key === 'enter') pick(this.at, cx);
        else if (e.key === 'escape') closeOverlay(p.overlay);
        else return;
        cx.notify();
      })
      .child(div().px(8).pt(4).pb(6).text_size(11).text_color(t.dim).truncate().child(p.title))
      .children(COPIES.map((it, i) => {
        const value = p.values[it.key], off = !value;
        return h_flex().id(`copy-${it.key}`).h(31).px(8).gap(9).rounded(7).items_center()
          .text_color(off ? t.dim : t.foreground)
          .when(i === this.at && !off, el => el.bg(t.selected))
          .when(!off, el => el.hover(s => s.bg(t.hover)).on_click((_e, cx) => pick(i, cx)))
          .child(svg(`mods/sessions-history/${it.icon}.svg`).size(14).flex_none().text_color(off ? t.dim : t.muted))
          .child(div().flex_none().child(it.label))
          .child(div().flex_1().min_w(0).truncate().text_right().text_size(11.5).text_color(t.dim).child(value ?? 'not found'));
      }));
  }
  resume(s, cx) {
    if (this.pending || Date.now() - this.lastLaunch < 2000) return;
    const d = this.opened;
    this.pending = true; if (d) d.error = ''; cx.notify();
    cx.spawn(async cx => {
      try {
        resumePlan(s, spaces(), panes(), focused()?.space);
        const r = await exec('python3', ['-I', this.helper, '--validate', s.cwd, s.id]);
        if (r.code !== 0) throw new Error(r.stderr || 'Project folder or session missing');
        // Resolve again after validation: spaces and their working folders may have changed.
        const plan = resumePlan(s, spaces(), panes(), focused()?.space);
        run(plan.command, plan.args);
        this.lastLaunch = Date.now();
        // It runs now, so it leaves the list of closed sessions: back to the list.
        if (this.opened === d) this.opened = null;
      } catch (e) { if (d) d.error = String(e); else this.error = String(e); }
      this.pending = false; cx.notify();
    });
  }
  render(cx) {
    const t = theme();
    if (props()?.copyMenu) return this.menu(t);
    // Opening the panel again reloads the list: sessions may have closed meanwhile.
    const shown = this.visible();
    if (shown && this.shown === false && !checking()) {
      this.rendering = true;
      try { this.refresh(cx); } finally { this.rendering = false; }
    }
    this.shown = shown;
    if (shown && !checking()) this.locate(cx);
    const f = focused();
    const query = this.input.value();
    const rows = filterSessions(this.rows, query, this.where(), this.repos).filter(s => !this.active.has(s.id));
    const count = this.pending ? 'Opening…' : this.loading && !this.rows.length ? '' : `${rows.length} closed`;
    // The card, header and list in the style of Herdr's side panels.
    const card = v_flex().id('session-history-root').size_full().min_h(0).rounded(10).overflow_hidden()
      .bg(t.background).border_1().border_color(t.border).text_color(t.foreground).text_size(13);
    const d = this.opened;
    if (d) {
      // The space as Herdr's sidebar lists it: a worktree space under its main checkout's space.
      const s = d.session, all = spaces(), ps = panes();
      const space = sidebarSpace(matchingSpace(s.cwd, all, ps, f?.space), all, ps, this.places, this.repos);
      const at = this.place(s), place = labelPlace(at, spacePlaces(all, ps, this.places));
      return card.child(sessionDetail(s, { ...d, pending: this.pending, spaceName: space?.name, place,
        branch: branchTail(s.branch, at, place) }, t, {
        back: (_e, cx) => this.back(cx), resume: (_e, cx) => this.resume(s, cx), copy: () => this.openMenu(s) }));
    }
    return card
      .child(h_flex().h(42).flex_none().px(10).gap(8).items_center().border_b(1).border_color(t.border)
        .child(segmented('scope', SCOPES, this.scope, (k, cx) => { this.scope = k; this.prefetch(cx); cx.notify(); }, t))
        .child(div().flex_1().min_w(0).truncate().text_right().text_size(12).text_color(t.dim).child(count))
        .child(iconButton('reload', 'mods/sessions-history/refresh.svg', (_e, cx) => this.refresh(cx), t, this.loading)))
      .child(v_flex().flex_none().px(10).pt(10)
        .child(h_flex().h(30).flex_none().px(8).items_center().rounded(8).border_1().border_color(t.border).bg(t.background)
          .child(Input.new(this.input).flex_1().h_full().text_size(12.5).text_color(t.foreground))))
      .when(Boolean(this.error), el => el.child(div().flex_none().px(12).pt(8).text_size(12).text_color(t.blocked).child(this.error)))
      .child(this.list(rows, query, t));
  }
  // The list: a virtual list, so hundreds of sessions cost what the visible rows cost.
  list(rows, query, t) {
    if (!rows.length) {
      const text = this.loading ? 'Loading sessions…' : query.trim() ? 'No matches'
        : { all: 'No closed sessions', space: 'No closed sessions in this space. Choose All spaces.',
          folder: 'No closed sessions in this folder. Choose This space or All spaces.' }[this.scope];
      return v_flex().flex_1().min_h(0).p(6).child(div().p(8).text_size(12).text_color(t.dim).child(text));
    }
    const at = Object.fromEntries(rows.map(s => [s.id, s]));
    const names = spacePlaces(spaces(), panes(), this.places);
    // Hover: a row's own .hover() isn't redrawn inside a virtual list, and rows can't have handlers,
    // so the list follows the pointer and says which row is under it. The rows are rebuilt on every
    // frame the list scrolls, so the highlight follows a scroll under a still pointer too.
    const PAD = 6;
    return v_flex().id('history-area').relative().flex_1().min_h(0).p(PAD).cursor_pointer()
      .on_mouse_move((e, cx) => {
        if (!e.local_position) return;
        const before = this.hoverIndex();
        this.pointerY = e.local_position.y / (settings().zoom || 1) - PAD;
        if (this.hoverIndex() !== before) cx.notify();
      })
      .on_hover((on, cx) => { if (!on && this.pointerY != null) { this.pointerY = null; cx.notify(); } })
      .child(v_virtual_list('history-list', rows.length, ROW, i => rows[i].id,
        range => { const h = this.hoverIndex(); return rows.slice(range.start, range.end).map((s, k) => this.row(s, t, range.start + k === h, names)); })
        .track_scroll(this.listScroll)
        .on_item_click((key, cx) => { if (at[key]) this.open(at[key], cx); })
        // Right-click: the copy menu, as on an agent in the Agents panel.
        .on_item_secondary_click((key) => { if (at[key]) this.openMenu(at[key]); }))
      .child(Scrollbar.vertical('history-list').absolute().inset_0());
  }
  // The row under the pointer, by index in the shown list; -1 when the pointer is elsewhere.
  hoverIndex() {
    if (this.pointerY == null || this.pointerY < 0) return -1;
    return Math.floor((this.pointerY + this.listScroll.offset().y) / ROW);
  }
  // One row, as the Agents panel draws an agent: the harness's icon;
  // "repo › worktree", and under it, dim, what the session was about; then how long ago and a mark.
  // Drawn inside the virtual list, so no handlers here (the list has them).
  // `names`: space names by place (spacePlaces), so a session where a space is shows its name.
  row(s, t, hovered = false, names = new Map()) {
    const state = sessionState(s).key;
    const at = this.place(s), place = labelPlace(at, names);
    const top = placeLine(place, state === 'missing', t, branchTail(s.branch, at, place));
    return h_flex().h(ROW).flex_none().px(8).gap(9).items_center().rounded(8).when(hovered, el => el.bg(t.hover))
      // The icon on the title's line, as in the Agents panel; the description under the title (16 + 9 in).
      // Centred on the line box it sits a pixel above the letters' middle, so it moves down one.
      .child(v_flex().flex_1().min_w(0).gap(2)
        .child(h_flex().min_w(0).gap(9).items_center()
          .child(div().flex_none().relative().top(1).child(icon(this.programs[s.program ?? 'claude'], 16, t.foreground)))
          .child(top.flex_1()))
        .child(div().min_w(0).pl(25).truncate().text_size(11.5).text_color(t.dim).child(s.description || s.title)))
      .child(div().flex_none().text_size(11).text_color(t.dim).child(ago(s.activity)))
      // Left mid-work: a yellow dot, where Agents shows a finished agent's. Otherwise the place stays empty.
      .child(div().size(7).mx(3).flex_none().rounded(4).when(state === 'stopped', el => el.bg(t.ansi3)));
  }
}
