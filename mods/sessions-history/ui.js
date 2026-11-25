// Small pieces of the panel: a segmented control, a header icon button and a button; and the place
// line that a row and a session's header share.

import { div, svg } from 'gpui-kit';
import { h_flex } from 'gpui-base';

// Options side by side, one chosen: [ This folder | This space | All spaces ]. `compact`: 6px side
// padding instead of 9, for a narrow panel (compactHeader). Should that still not fit, the options shrink
// with … rather than push what follows them out of the panel.
export function segmented(id, options, value, pick, t, compact = false) {
  return h_flex().min_w(0).p(2).gap(2).rounded(8).bg(t.chrome).border_1().border_color(t.border)
    .children(options.map(([key, label]) => div().id(`${id}-${key}`).min_w(0).truncate().px(compact ? 6 : 9).py(2).rounded(6).text_size(12).cursor_pointer()
      .bg(key === value ? t.background : t.chrome).text_color(key === value ? t.foreground : t.muted)
      .when(key === value, (el) => el.font_semibold())
      .hover((el) => el.text_color(t.foreground)).on_click((_e, cx) => { if (key !== value) pick(key, cx); }).child(label)));
}

// A square icon button in a header: the icon tinted like the text, turning while `spinning`.
export function iconButton(id, icon, action, t, spinning = false) {
  return div().id(id).size(22).flex_none().flex().items_center().justify_center().rounded(6).cursor_pointer()
    .text_color(t.muted).hover((el) => el.bg(t.hover).text_color(t.foreground)).on_click(action)
    .child(svg(icon).size(15).when(spinning, (el) => el.spin(900)));
}

// A plain button; `primary` draws its border in the accent, `off` greys it out and says why on hover.
export function button(id, child, action, t, primary = false, off = null) {
  const el = h_flex().id(id).gap(6).px(9).py(4).rounded(7).items_center().text_size(12).flex_none()
    .border_1().border_color(primary && !off ? t.accent : t.border);
  if (off) return el.text_color(t.dim).cursor_not_allowed().tooltip(off).child(child);
  return el.cursor_pointer().text_color(primary ? t.foreground : t.muted)
    .hover((e) => e.bg(t.hover).text_color(t.foreground)).on_click(action).child(child);
}

// Where a session ran, as the Agents panel writes a space: "repo › space", the repo grey when a
// space or worktree follows it. `gone`: the folder no longer exists, so it can't be resumed there:
// red and struck through. Used by a row and by the session's header. A worktree gets the branch
// icon before its name, as Herdr's spaces sidebar draws one.
// `branch` (branchTail): the branch after it, grey with the same icon, in the room the names leave.
export function placeLine({ repo, tree, worktree }, gone, t, branch = null) {
  const name = (text, color) => div().text_color(gone ? t.ansi1 : color).when(gone, el => el.line_through()).child(text);
  return h_flex().min_w(0).gap(4).items_center()
    .child(name(repo, tree ? t.muted : t.foreground).flex_none().max_w(tree ? 150 : 240).truncate())
    .when(Boolean(tree), el => el.child(div().flex_none().text_color(gone ? t.ansi1 : t.dim).child('›'))
      .when(Boolean(worktree), el => el.child(branchIcon(gone ? t.ansi1 : t.dim)))
      .child(name(tree, t.foreground).min_w(0).truncate()))
    // In the room the names leave, so they are never cut for it, and only when the icon and a bit of its
    // name fit (54px, 6 of them before the icon). ml(-4) takes back the line's gap, so a name with a
    // branch after it is cut no sooner than one without.
    .when(Boolean(branch), el => el.child(fitOrHide(h_flex().pl(6).gap(4).items_center()
      .child(branchIcon(gone ? t.ansi1 : t.dim))
      .child(name(branch, t.dim).min_w(0).truncate()), { least: 54 }).ml(-4)));
}

// A box for what may go when its row is short of room: it takes the room the row leaves (flex_1 from
// nothing), as high as the row, and shows `child` only when enough of it fits, never a sliver (a lone
// icon, a lone "…"). It can't measure, so the child sits in a wrapping row after a 0-wide item and
// wraps to a second line the box hides when it doesn't fit. `least`: px the child needs, from which it
// grows and may cut itself with …; without it, all of the child must fit. `end`: to the right.
export function fitOrHide(child, { least = null, end = false } = {}) {
  return div().flex_1().min_w(0).self_stretch().relative().overflow_hidden()
    .child(h_flex().absolute().inset_0().flex_wrap().items_center().when(end, el => el.justify_end())
      .child(div().w(0).h_full())
      .child(least == null ? child.flex_none() : child.flex_grow(1).flex_basis(least).min_w(0)));
}

// The branch icon at 13px, as Herdr's spaces sidebar draws it before a worktree.
export function branchIcon(color) {
  return svg('mods/sessions-history/branch.svg').size(13).flex_none().text_color(color);
}
