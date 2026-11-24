const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
// How Claude Code starts in a mode again, by the mode a session started in.
const CLAUDE_MODE_FLAGS = {
  bypassPermissions: ['--dangerously-skip-permissions'],
  auto: ['--permission-mode', 'auto'],
  plan: ['--permission-mode', 'plan'],
};
// Each harness the list reads, by its key in the app's program table: how to resume one of its
// sessions (a row from reader.py), run in the session's folder. A row names its harness in
// `program` (reader.py sets 'codex'; Claude Code's rows leave it out). Another harness is one more entry.
export const HARNESSES = {
  // Back in the mode it started in (its first recorded permissionMode): permissions bypassed only
  // after a bypassed start (--dangerously-skip-permissions), auto and plan as they were; any other
  // start resumes as Claude Code asks.
  claude: { name: 'Claude Code', resume: s => ['claude', '--resume', s.id, ...(CLAUDE_MODE_FLAGS[s.startMode] ?? [])] },
  // As Herdr starts Codex: a shared daemon would make later sessions use the first one's identity.
  // No permission flag: Codex brings back the session's own approval and sandbox settings itself.
  codex: { name: 'Codex', resume: s => ['codex', '--no-daemon', 'resume', s.id] },
  // Always approving again only after a start with it (--yolo, recorded as 'yolo'), as Claude Code's bypass.
  grok: { name: 'Grok', resume: s => ['grok', '--resume', s.id, ...(s.startMode === 'yolo' ? ['--always-approve'] : [])] },
  // OpenCode's ids are its own (ses_…), not UUIDs.
  opencode: { name: 'OpenCode', id: /^ses_[0-9A-Za-z]{10,60}$/, resume: s => ['opencode', '--session', s.id] },
};
// A permission mode for people, as Claude Code's mode switch names them; an unknown one as it is.
const MODES = { bypassPermissions: 'Bypass permissions', acceptEdits: 'Accept edits', plan: 'Plan', auto: 'Auto',
  default: 'Default', manual: 'Manual', dontAsk: "Don't ask", yolo: 'YOLO (always approve)' };
export const modeLabel = mode => MODES[mode] ?? mode;
const harnessOf = session => HARNESSES[session.program ?? 'claude'] ?? null;
// Whether a session's id is one its harness gives: a UUID, unless the harness says otherwise.
const validId = (harness, session) => (harness.id ?? UUID).test(session.id ?? '');
// For people: "Codex"; a harness without an entry by its key.
export const harnessName = session => harnessOf(session)?.name ?? session.program;
export function resumeArgs(session, space) {
  if (!space || space.machine) throw new Error('Select a local space to resume in.');
  if (!session.cwd || !session.exists) throw new Error('Project folder missing or cwd unknown.');
  const harness = harnessOf(session);
  if (!harness) throw new Error(`Can't resume ${session.program} sessions yet.`);
  if (!validId(harness, session)) throw new Error('Invalid session ID.');
  return { space: space.id, folder: session.cwd, name: session.title, argv: harness.resume(session) };
}
// A word for a shell command: quoted only when it has to be.
const shellWord = s => /^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`;
// The copy menu, as the Agents panel's: its items, with this mod's own icons (Lucide; see ICONS-LICENSE.txt).
export const COPIES = [
  { key: 'session', label: 'Copy session ID', icon: 'files' },
  { key: 'resume', label: 'Copy resume command', icon: 'terminal' },
  { key: 'transcript', label: 'Copy transcript path', icon: 'file-text' },
  { key: 'folder', label: 'Copy folder', icon: 'folder' },
];
// What each item copies; null when the session doesn't have it. The resume command goes to the
// session's folder first (harnesses find a session by where it ran), then starts what Resume starts.
export function copyValues(session) {
  const harness = harnessOf(session), cwd = session.cwd || null;
  const argv = harness && validId(harness, session) ? harness.resume(session) : null;
  return {
    session: session.id ?? null,
    resume: argv && `${cwd ? `cd ${shellWord(cwd)} && ` : ''}${argv.map(shellWord).join(' ')}`,
    transcript: session.transcript ?? null,
    folder: cwd,
  };
}
const normalizeFolder = folder => typeof folder === 'string' ? folder.replace(/\/+$/, '') || '/' : '';
// Where a space is now: its first terminal's current folder, not its stale launch folder.
function spaceFolder(space, allPanes) {
  const tabs = new Set((space.tabs ?? []).map(tab => tab.id));
  const live = allPanes.find(pane => tabs.has(pane.tab) && pane.terminal !== null && pane.cwd)?.cwd;
  return normalizeFolder(live || space.folder);
}
// What This space shows, as a scope for filterSessions: the repository the space's folder is in, with
// all its worktrees (from `repos`: folder → repository, as main.js's locate() keeps them); outside a
// repository that folder with everything in it. A space at home or at / covers only that folder
// itself, and a repository there doesn't count: everything under home is nearly every session.
export function spaceScope(space, allPanes, repos = {}, home = '') {
  const folder = spaceFolder(space, allPanes);
  if (!folder) return { folder: null };
  const wide = f => f === '/' || f === normalizeFolder(home);
  const repo = normalizeFolder(repos[folder]);
  if (repo && !wide(repo)) return { repo };
  return wide(folder) ? { folder } : { under: folder };
}
// The folders of this computer's spaces, to ask git where they are.
export function spaceFolders(allSpaces, allPanes) {
  return allSpaces.filter(space => !space.machine).map(space => spaceFolder(space, allPanes)).filter(Boolean);
}
const placeKey = place => `${place.repo}\u0000${place.tree ?? ''}`;
// Space names by place ("repo\0worktree"), for this computer's spaces whose folder's place is known.
// `places`: folder → { repo, tree }, as main.js's locate() keeps them. The first space wins.
export function spacePlaces(allSpaces, allPanes, places) {
  const names = new Map();
  for (const space of allSpaces) {
    if (space.machine || !space.name) continue;
    const at = places[spaceFolder(space, allPanes)];
    if (at && !names.has(placeKey(at))) names.set(placeKey(at), space.name);
  }
  return names;
}
// A session's place as the Agents panel names it: "repo › space" when a space is there and isn't
// named after the repo; otherwise the worktree git or the path gave.
// `worktree`: the second part stands for a worktree (drawn with a branch icon), also when a space's
// name replaces it; a space at a main checkout isn't one.
export function labelPlace(place, names) {
  const name = names.get(placeKey(place)), worktree = Boolean(place.tree);
  return name && name !== place.repo ? { repo: place.repo, tree: name, worktree } : { ...place, worktree };
}
export function matchingSpace(cwd, allSpaces, allPanes, preferredId) {
  const target = normalizeFolder(cwd);
  if (!target.startsWith('/')) return null;
  let best = null; let specificity = -1;
  for (const space of allSpaces) {
    if (space.machine) continue;
    const folder = spaceFolder(space, allPanes);
    if (!folder || folder === '/' || (target !== folder && !target.startsWith(folder + '/'))) continue;
    if (folder.length > specificity || (folder.length === specificity && space.id === preferredId)) {
      best = space; specificity = folder.length;
    }
  }
  return best;
}
// The space a space is listed under in Herdr's spaces sidebar: a worktree space (a linked worktree to
// git) sits under the first local space at its repository's main checkout; any other space is its own.
// `places` and `repos`: folder → { repo, tree } and folder → repository, as main.js's locate() keeps them.
export function sidebarSpace(space, allSpaces, allPanes, places, repos) {
  if (!space) return null;
  const folder = spaceFolder(space, allPanes), repo = repos[folder];
  if (!repo || !places[folder]?.tree) return space;
  return allSpaces.find(other => {
    if (other.machine) return false;
    const at = spaceFolder(other, allPanes);
    return repos[at] === repo && places[at]?.tree === null;
  }) ?? space;
}
export function resumePlan(session, allSpaces, allPanes, preferredId) {
  const space = matchingSpace(session.cwd, allSpaces, allPanes, preferredId);
  const args = resumeArgs(session, space || {id:'', machine:null});
  const tabName = session.description || session.title || `${harnessName(session)} session`;
  if (space) return {command:'tab.new', args:{...args, name:tabName}};
  const folder = normalizeFolder(session.cwd);
  return {command:'space.new', args:{machine:'', folder:session.cwd, name:folder.split('/').pop() || `${harnessName(session)} sessions`,
    tab:tabName, argv:args.argv}};
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function formatSessionTime(milliseconds) {
  if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds)) return 'Not available';
  const d = new Date(milliseconds);
  if (!Number.isFinite(d.getTime())) return 'Not available';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
export function contextLabel(context) {
  const pct = context?.percentage;
  if (typeof pct !== 'number' || !Number.isFinite(pct) || pct < 0 || pct > 100) return 'Not available';
  return `${Math.round(pct * 10) / 10}%`;
}
export function compactDescription(session) {
  if (!['saved', 'claude', 'recap'].includes(session.summarySource) || typeof session.summary !== 'string' || !session.summary.trim())
    return 'No session summary saved.';
  const words = session.summary.trim().split(/\s+/).filter(Boolean);
  const result = words.length > 60 ? words.slice(0, 60).join(' ') + '…' : words.join(' ');
  return result;
}
// Whether `folder` is `root` or inside it.
function inside(folder, root) {
  const f = normalizeFolder(folder), r = normalizeFolder(root);
  return Boolean(f && r) && (f === r || f.startsWith(r === '/' ? '/' : r + '/'));
}
// Whether a session's folder is in repository `repo`: as git said for that folder (`repos`), or, for a
// folder git wasn't asked about (it's gone), when it's inside the repository or its path names a
// worktree of a repository by that name (~/.herdr/worktrees/<repo>/…).
function inRepo(cwd, repo, repos) {
  const f = normalizeFolder(cwd), r = normalizeFolder(repo);
  if (!f || !r) return false;
  if (Object.hasOwn(repos, cwd)) return normalizeFolder(repos[cwd]) === r;
  if (inside(f, r)) return true;
  const place = placeFromPath(f, '');
  return Boolean(place.tree) && place.repo === base(r).replace(/\.git$/, '');
}
// The sessions in scope `where` whose text has the query. `where`: { folder } is exactly that folder
// (This folder); { repo } every folder of that repository, its worktrees included, and { under } that
// folder and everything in it (This space, see spaceScope); null everything (All spaces). `repos`:
// folder → repository, as main.js's locate() keeps them.
export function filterSessions(rows, query, where, repos = {}) {
  const q = query.trim().toLowerCase();
  const inScope = s => !where || (where.repo !== undefined ? inRepo(s.cwd, where.repo, repos)
    : where.under !== undefined ? inside(s.cwd, where.under)
    : Boolean(normalizeFolder(where.folder)) && normalizeFolder(s.cwd) === normalizeFolder(where.folder));
  return rows.filter(s => inScope(s) && `${s.title} ${s.description || ''} ${s.cwd || ''} ${s.branch || ''} ${s.id}`.toLowerCase().includes(q));
}
// How long ago, in short: now, 50m, 3h, 2d, 4mo, 1y. `seconds`: epoch seconds.
export function ago(seconds, now = Date.now()) {
  const s = now / 1000 - seconds;
  for (const [n, u] of [[31536000, 'y'], [2592000, 'mo'], [86400, 'd'], [3600, 'h'], [60, 'm']]) if (s >= n) return `${Math.floor(s / n)}${u}`;
  return 'now';
}
const base = path => path.replace(/\/+$/, '').split('/').pop();
// Where a session ran, named as the Agents panel names a space: "repo › worktree".
// From `git rev-parse --path-format=absolute --git-common-dir --show-toplevel` run in its folder.
export function placeFromGit(stdout, cwd) {
  const top = (stdout.split('\n')[1] ?? '').trim(), root = repoFromGit(stdout) ?? '';
  const repo = (root ? base(root) : base(cwd)).replace(/\.git$/, '');
  return { repo, tree: top && root && top !== root ? base(top) : null };
}
// The repository a folder is in, from the same git output: the common folder without its /.git (a
// worktree's is its main checkout's), else that folder itself (a bare repository); null without one.
export function repoFromGit(stdout) {
  const common = (stdout.split('\n')[0] ?? '').trim();
  return !common ? null : common.endsWith('/.git') ? common.slice(0, -5) : common;
}
// Where worktree tools put their checkouts; the folders tell repo and worktree even after
// git forgot them (removed, or left behind without their .git link).
const WORKTREES = [
  /\/\.herdr\/worktrees\/(?<repo>[^/]+)\/(?<tree>[^/]+)/,   // Herdr: ~/.herdr/worktrees/<repo>/<branch>
  /\/orca\/workspaces\/(?<repo>[^/]+)\/(?<tree>[^/]+)/,     // Orca: ~/orca/workspaces/<repo>/<name>
  /\/(?<repo>[^/]+)\/\.claude\/worktrees\/(?<tree>[^/]+)/,  // Claude Code: <repo>/.claude/worktrees/<name>
];
// Without git (the folder is gone, or no repository): a worktree folder above still tells both;
// home is ~; anything else is its folder's name.
export function placeFromPath(cwd, home) {
  if (typeof cwd !== 'string' || !cwd.replace(/\/+$/, '')) return { repo: 'Unknown folder', tree: null };
  if (home && cwd.replace(/\/+$/, '') === home.replace(/\/+$/, '')) return { repo: '~', tree: null };
  for (const pattern of WORKTREES) {
    const m = pattern.exec(cwd);
    if (m) return { repo: m.groups.repo, tree: m.groups.tree };
  }
  return { repo: base(cwd), tree: null };
}
export function formatDuration(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return null;
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return `${Math.max(1, Math.round(ms / 1000))}s`;
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
}
export function formatCost(usd) {
  if (typeof usd !== 'number' || !Number.isFinite(usd) || usd < 0) return null;
  return usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`;
}
// What a row's dot says: can the session be resumed, and was it left mid-work?
// `ending` comes from reader.py: finished, unanswered, interrupted, mid-action, or null.
export function sessionState(s) {
  if (!s.exists) return { key: 'missing', label: "Folder missing, can't be resumed" };
  if (s.ending === 'unanswered') return { key: 'stopped', label: 'Stopped mid-work: your last message got no answer' };
  if (s.ending === 'interrupted') return { key: 'stopped', label: 'Stopped mid-work: interrupted' };
  if (s.ending === 'mid-action') return { key: 'stopped', label: 'Stopped mid-work: during an action' };
  if (s.ending === 'finished') return { key: 'finished', label: 'Finished' };
  return { key: 'empty', label: 'No conversation' };
}
// The dot's color for a state, from the theme: green, yellow, red, or dim.
export function stateColor(key, t) {
  return { finished: t.ansi2, stopped: t.ansi3, missing: t.ansi1 }[key] ?? t.dim;
}
// claude-opus-5-5 → Opus 5.5; anything else as it is.
export function modelName(id) {
  const m = /^claude-([a-z]+)-(\d+)-(\d+)\b/.exec(id ?? '');
  return m ? `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}` : id;
}
