import { div } from 'gpui-kit';
import { v_flex, h_flex } from 'gpui-base';
import { home } from 'herdr';
import { formatSessionTime, contextLabel, compactDescription, ago, formatDuration, formatCost, modelName, sessionState, stateColor, harnessName, modeLabel } from './logic.mjs';
import { button, placeLine, branchIcon } from './ui.js';

// The full folder, with home as ~.
function folder(path) {
  const h = home().replace(/\/+$/, '');
  if (typeof path !== 'string' || !path) return 'Unknown';
  return path === h || path.startsWith(h + '/') ? '~' + path.slice(h.length) : path;
}

/**
 * One session in the panel, in the list's place: a header
 * back to the list, what it was about, Resume and the copy menu, then everything known about it.
 * Nothing happens to the session until Resume is pressed.
 * `st`: { summary, summaryLoading, context, contextLoading, error, pending, spaceName,
 *   place: { repo, tree } as the row shows it }.
 */
export function sessionDetail(s, st, t, { back, resume, copy }) {
  const info = { ...s, ...(st.summary ?? {}) };
  const short = st.summaryLoading ? 'Loading summary…' : compactDescription(info);
  const fact = (label, value) => h_flex().gap(10).items_start()
    .child(div().w(84).flex_none().text_color(t.dim).child(label))
    .child(div().flex_1().min_w(0).text_color(t.foreground).child(value));
  const started = typeof s.started === 'number' ? formatSessionTime(s.started) : null;
  const duration = formatDuration(s.duration), cost = formatCost(s.cost);
  const changes = typeof s.linesAdded === 'number' || typeof s.linesRemoved === 'number';
  const ctx = st.contextLoading ? null : contextLabel(st.context);
  const prs = s.prs ?? [], models = (s.models ?? []).map(modelName);
  const off = !s.exists ? 'The project folder no longer exists' : st.pending ? 'Opening…' : null;
  const state = sessionState(s);
  return v_flex().size_full().min_h(0)
    .child(h_flex().h(42).flex_none().px(10).gap(8).items_center().border_b(1).border_color(t.border)
      .child(div().id('back').w(20).flex_none().cursor_pointer().text_color(t.muted).hover(x => x.text_color(t.foreground))
        .on_click(back).child('←'))
      // Where it ran, as its row says it: "repo › space".
      .child(div().flex_1().min_w(0).font_semibold().child(placeLine(st.place ?? { repo: s.title, tree: null }, !s.exists, t)))
      .child(div().flex_none().text_size(12).text_color(t.dim).child(`${ago(s.activity)} ago`)))
    .child(div().id('session-detail').flex_1().min_h(0).p(14).overflow_y_scrollbar()
      .child(v_flex().gap(12)
        .child(div().text_size(14).font_semibold().child(s.description || s.title || `${harnessName(s)} session`))
        .child(h_flex().gap(6).items_center()
          .child(button('session-resume', st.pending ? 'Opening…' : '▶ Resume session', resume, t, true, off))
          // The copy menu, as a right-click on the row opens it.
          .child(button('session-copy', 'Copy ▾', copy, t)))
        .when(Boolean(st.error), el => el.child(div().text_size(12).text_color(t.blocked).child(st.error)))
        // A recap is Claude Code's own "where we left off", in the session's language.
        .child(v_flex().gap(3)
          .child(div().text_size(11).text_color(t.dim).child(info.summarySource === 'recap' && !st.summaryLoading ? 'Recap' : 'Summary'))
          .child(div().text_size(12.5).text_color(st.summaryLoading ? t.dim : t.foreground).child(short)))
        .child(v_flex().gap(6).pt(12).border_t(1).border_color(t.border).text_size(12)
          // What the row's dot means, in words.
          .child(fact('Status', h_flex().gap(6).items_center()
            .child(div().flex_none().text_color(stateColor(state.key, t)).child('●')).child(state.label)))
          // The permission mode it started in (Claude Code's, Grok's YOLO): Resume starts it in that mode again.
          .when(Boolean(s.startMode), el => el.child(fact('Started in', modeLabel(s.startMode))))
          // min_w(0): a long path wraps instead of running out of the panel.
          .child(fact('Folder', h_flex().gap(6).flex_wrap().child(div().min_w(0).child(folder(s.cwd)))
            .when(!s.exists, el => el.child(div().text_color(t.ansi1).child('missing')))))
          .when(Boolean(s.branch), el => el.child(fact('Branch', s.branch === 'HEAD' ? 'none (detached HEAD)'
            // The icon on the first line of a branch that wraps: 13px centered in a 12px line (×1.618).
            : h_flex().gap(5).items_start().child(branchIcon(t.dim).mt(3)).child(div().min_w(0).child(s.branch)))))
          .when(Boolean(st.spaceName), el => el.child(fact('Space', st.spaceName)))
          .child(fact('Last active', formatSessionTime(s.activity * 1000)))
          .when(Boolean(started), el => el.child(fact('Started', started)))
          .when(Boolean(duration), el => el.child(fact('Duration', duration)))
          .when(Boolean(cost), el => el.child(fact('Cost', `${cost} at API rates`)))
          .when(changes, el => el.child(fact('Changes', h_flex().gap(6)
            .child(div().text_color(t.ansi2).child(`+${s.linesAdded ?? 0}`))
            .child(div().text_color(t.ansi1).child(`−${s.linesRemoved ?? 0}`)))))
          .when(models.length > 0, el => el.child(fact('Models', models.join(', '))))
          .when(prs.length > 0, el => el.child(fact('Pull requests', h_flex().gap(4).flex_wrap()
            .children(prs.map((p, i) => div().id(`session-pr-${i}`).px(5).rounded(4).border_1().border_color(t.ansi5).text_color(t.ansi5)
              .cursor_pointer().hover(x => x.bg(t.hover)).on_click((_e, cx) => cx.open_url(p.url))
              .child(`#${p.number ?? '?'}${p.repo ? ' ' + p.repo : ''} ↗`))))))
          .when(Boolean(ctx) && ctx !== 'Not available', el => el.child(fact('Context', ctx)))
          .child(fact('Session ID', div().text_color(t.muted).child(s.id))))));
}
