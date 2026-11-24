import assert from 'node:assert/strict';
import { resumeArgs, filterSessions } from './logic.mjs';
// Started with --dangerously-skip-permissions, so it resumes with it.
const s = {id:'12345678-1234-1234-1234-123456789abc', cwd:'/original', exists:true, title:'Hello', startMode:'bypassPermissions'};
assert.deepEqual(resumeArgs(s, {id:'local', machine:null}), {space:'local', folder:'/original', name:'Hello', argv:['claude','--resume',s.id,'--dangerously-skip-permissions']});
assert.throws(()=>resumeArgs(s,{id:'remote',machine:'vps'}), /local/);
assert.throws(()=>resumeArgs({...s,exists:false},{id:'local',machine:null}), /Project folder/);
assert.throws(()=>resumeArgs({...s,id:'bad'},{id:'local',machine:null}), /session ID/);
// A scope: { folder } is exactly that folder (This folder), { repo } every folder of that repository and
// { under } that folder and everything in it (This space), null everything (All spaces). A folder of
// null (a terminal on another machine): nothing.
assert.equal(filterSessions([s], 'hello', {folder:'/original'}).length,1);
assert.equal(filterSessions([s], '', {folder:'/other'}).length,0);
assert.equal(filterSessions([s], '', {folder:'/original/'}).length,1);
assert.equal(filterSessions([s], '', {folder:null}).length,0);
assert.equal(filterSessions([s, {...s, cwd:'/other'}], '', null).length,2);
{
  const at = cwd => ({...s, cwd});
  const shown = where => filterSessions([at('/repo'), at('/repo/app'), at('/repository'), at('/other'), at(null)], '', where).map(x => x.cwd);
  assert.deepEqual(shown({under:'/repo'}), ['/repo', '/repo/app']);
  assert.deepEqual(shown({under:'/repo/'}), ['/repo', '/repo/app']);
  assert.deepEqual(shown({folder:'/repo'}), ['/repo']);
  assert.deepEqual(shown({under:null}), []);
  assert.deepEqual(shown({under:'/'}), ['/repo', '/repo/app', '/repository', '/other']);
}
// { repo }: the main checkout, its worktrees and every folder in them, by the repository git named for
// each folder (`repos`: folder → repository). A folder git wasn't asked about (it's gone) counts when
// it's inside the repository, or when its path names a worktree of a repository by that name.
{
  const at = cwd => ({...s, cwd});
  const repos = {'/code/shop': '/code/shop', '/code/shop/app': '/code/shop', '/w/shop/feat': '/code/shop',
    '/w/shop/feat/sub': '/code/shop', '/code/shopping': '/code/shopping', '/old/shop/x': '/old/shop', '/tmp/x': null};
  const all = ['/code/shop', '/code/shop/app', '/w/shop/feat', '/w/shop/feat/sub', '/code/shop/gone',
    '/Users/me/.herdr/worktrees/shop/gone', '/Users/me/.herdr/worktrees/other/gone', '/code/shopping', '/old/shop/x', '/tmp/x', null];
  assert.deepEqual(filterSessions(all.map(at), '', {repo:'/code/shop'}, repos).map(x => x.cwd),
    ['/code/shop', '/code/shop/app', '/w/shop/feat', '/w/shop/feat/sub', '/code/shop/gone', '/Users/me/.herdr/worktrees/shop/gone']);
  assert.deepEqual(filterSessions(all.map(at), '', {repo:null}, repos), []);
}
const described = {...s, description: 'Improve the login form'};
assert.equal(filterSessions([described], 'login form', {folder:'/original'}).length, 1);
// The formatters.
const logic = await import('./logic.mjs');
process.env.TZ = 'Europe/Amsterdam';
const date = logic.formatSessionTime ? logic.formatSessionTime(1793984533000) : new Date(1793984533000).toLocaleString();
assert.equal(date, '6 Nov 2026, 18:02:13');
assert.equal(logic.formatSessionTime?.(NaN), 'Not available');
assert.equal(logic.contextLabel?.({percentage: 24.5}), '24.5%');
assert.equal(logic.contextLabel?.(null), 'Not available');
assert.equal(logic.contextLabel?.({percentage: 0}), '0%');
assert.equal(logic.contextLabel?.({percentage: 101}), 'Not available');
const allSpaces = [
  {id:'unrelated', machine:null, name:'Other', folder:'/other', tabs:[{id:'t-other'}]},
  {id:'project', machine:null, name:'Project', folder:'/repo', tabs:[{id:'t-project'}]},
  {id:'nested', machine:null, name:'Worktree', folder:'/repo/worktree', tabs:[{id:'t-nested'}]},
  {id:'remote', machine:'vps', folder:'/repo/worktree/app', tabs:[]},
];
const work = {...s, cwd:'/repo/worktree/app', description:'Fix login'};
const plan = logic.resumePlan?.(work, allSpaces, [], 'unrelated');
assert.deepEqual(plan, {command:'tab.new', args:{space:'nested', folder:'/repo/worktree/app', name:'Fix login',
  argv:['claude','--resume',s.id,'--dangerously-skip-permissions']}});
assert.equal(logic.resumePlan(work, allSpaces, [{tab:'t-project',cwd:'/repo/worktree/app'}], 'unrelated').args.space, 'project');
// A moved terminal's live folder replaces the stale original space folder.
assert.equal(logic.resumePlan(work, allSpaces, [{tab:'t-nested',cwd:'/elsewhere'}], 'unrelated').args.space, 'project');
assert.equal(logic.resumePlan({...work,cwd:'/repository'}, allSpaces, [], 'unrelated').command, 'space.new');
const newPlan = logic.resumePlan({...work,cwd:'/missing/project'}, allSpaces, [], 'remote');
assert.deepEqual(newPlan, {command:'space.new',args:{machine:'',folder:'/missing/project',name:'project',tab:'Fix login',
  argv:['claude','--resume',s.id,'--dangerously-skip-permissions']}});
assert.equal(logic.resumePlan(work, [...allSpaces,{id:'same',machine:null,folder:'/repo/worktree',tabs:[]}], [], 'same').args.space, 'same');
assert.equal(logic.resumePlan({...work,cwd:'/repo/worktree/'}, allSpaces, [], null).args.space, 'nested');
assert.throws(()=>logic.resumePlan({...work,exists:false}, allSpaces, [], null), /Project folder/);
assert.equal(logic.compactDescription?.({summary:'The login form is fixed. Tests added and error messages checked.', summarySource:'saved'}),
  'The login form is fixed. Tests added and error messages checked.');
const longSummary = 'word '.repeat(200);
assert.ok((logic.compactDescription?.({summary:longSummary, summarySource:'saved'}) || '').split(/\s+/).filter(Boolean).length <= 60);
assert.equal(logic.compactDescription?.({description:'The first prompt'}), 'No session summary saved.');
assert.equal(logic.compactDescription?.({summary:'Old prompt fragments'}), 'No session summary saved.');
assert.equal(logic.compactDescription({summary:'A summary of every phase of the session.', summarySource:'claude'}), 'A summary of every phase of the session.');
assert.equal(logic.compactDescription({summary:'Done; next step: merge.', summarySource:'recap'}), 'Done; next step: merge.');
// Row and popup helpers.
const now = 1793984533000;
assert.equal(logic.ago(now / 1000 - 30, now), 'now');
assert.equal(logic.ago(now / 1000 - 50 * 60, now), '50m');
assert.equal(logic.ago(now / 1000 - 3 * 86400, now), '3d');
// "repo › worktree", as the Agents panel names a place: from git, else from the folder's path.
assert.deepEqual(logic.placeFromGit('/code/herdr-app/.git\n/code/herdr-app\n', '/code/herdr-app'), {repo:'herdr-app', tree:null});
assert.deepEqual(logic.placeFromGit('/code/webshop/.git\n/Users/me/.herdr/worktrees/webshop/fix-login\n', '/Users/me/.herdr/worktrees/webshop/fix-login/app'),
  {repo:'webshop', tree:'fix-login'});
assert.deepEqual(logic.placeFromGit('/srv/tools.git\n\n', '/srv/tools.git'), {repo:'tools', tree:null});
assert.deepEqual(logic.placeFromPath('/Users/me/.herdr/worktrees/api-server/fix-auth/src'), {repo:'api-server', tree:'fix-auth'});
// Orca's workspaces and Claude Code's worktrees: often left without .git, so git can't tell.
assert.deepEqual(logic.placeFromPath('/Users/me/orca/workspaces/portfolio/dark-mode'), {repo:'portfolio', tree:'dark-mode'});
assert.deepEqual(logic.placeFromPath('/code/docs-site/.claude/worktrees/send-under-way/src'),
  {repo:'docs-site', tree:'send-under-way'});
assert.deepEqual(logic.placeFromPath('/Volumes/config/'), {repo:'config', tree:null});
assert.deepEqual(logic.placeFromPath(null), {repo:'Unknown folder', tree:null});
assert.deepEqual(logic.placeFromPath('/Users/me/', '/Users/me'), {repo:'~', tree:null});
// A space where a session ran names its place, as in the Agents panel: "repo › space" when the
// space's name isn't the repo's. Matched by place (repo and worktree), so a space at home doesn't
// name every session under it.
{
  const places = { '/Volumes/config': {repo:'config', tree:null}, '/Users/me': {repo:'~', tree:null},
    '/w/webshop/fix-x': {repo:'webshop', tree:'fix-x'}, '/code/herdr-app': {repo:'herdr-app', tree:null} };
  const sp = (id, name, folder) => ({id, name, machine:null, folder, tabs:[{id:`t-${id}`}]});
  const named = logic.spacePlaces([sp('ha', 'home-server', '/Volumes/config'), sp('home', '~', '/Users/me'),
    sp('fix', 'fix login', '/w/webshop/fix-x'), sp('herdr', 'herdr-app', '/code/herdr-app'),
    {...sp('vps', 'remote', '/Volumes/config'), machine:'vps'}], [], places);
  const label = place => logic.labelPlace(place, named);
  // `worktree`: the second part is a worktree (drawn with a branch icon), not a space at a main checkout.
  assert.deepEqual(label({repo:'config', tree:null}), {repo:'config', tree:'home-server', worktree:false});
  assert.deepEqual(label({repo:'webshop', tree:'fix-x'}), {repo:'webshop', tree:'fix login', worktree:true});
  assert.deepEqual(label({repo:'herdr-app', tree:null}), {repo:'herdr-app', tree:null, worktree:false});
  assert.deepEqual(label({repo:'api-server', tree:'gone'}), {repo:'api-server', tree:'gone', worktree:true});
  // A space whose terminal moved is where that terminal is now.
  const moved = logic.spacePlaces([sp('ha', 'home-server', '/old')], [{tab:'t-ha', terminal:'t1', cwd:'/Volumes/config'}], places);
  assert.deepEqual(logic.labelPlace({repo:'config', tree:null}, moved), {repo:'config', tree:'home-server', worktree:false});
  assert.deepEqual(logic.spaceFolders([sp('ha', 'x', '/Volumes/config/'), {...sp('vps', 'y', '/srv'), machine:'vps'}], []), ['/Volumes/config']);
}
assert.equal(logic.formatDuration(7996600), '2h 13m');
assert.equal(logic.formatDuration(45 * 60000), '45m');
assert.equal(logic.formatDuration(20000), '20s');
assert.equal(logic.formatDuration(null), null);
assert.equal(logic.formatCost(6.1306), '$6.13');
assert.equal(logic.formatCost(0.001), '<$0.01');
assert.equal(logic.formatCost(undefined), null);
assert.equal(logic.modelName('claude-opus-5-5'), 'Opus 5.5');
assert.equal(logic.modelName('claude-haiku-5-5-20261001'), 'Haiku 5.5');
assert.equal(logic.modelName('gpt-x'), 'gpt-x');
assert.equal(filterSessions([{...s, branch:'fix/login'}], 'fix/log', {folder:'/original'}).length, 1);
// This space: the repository the space is in (from git, for the folder its first terminal is in),
// with all its worktrees; outside a repository that folder with everything in it. A space at home or
// at / covers only that folder: everything under home is nearly every session.
{
  const space = {id:'w', machine:null, folder:'/repo', tabs:[{id:'t-w'}]};
  const inSrc = [{tab:'t-w', terminal:'t1', cwd:'/repo/app/src'}];
  const scope = (sp, panes, repos) => logic.spaceScope(sp, panes, repos, '/Users/me');
  assert.deepEqual(scope(space, inSrc, {'/repo/app/src': '/repo'}), {repo:'/repo'});
  assert.deepEqual(scope(space, [{tab:'t-w', terminal:'t1', cwd:'/w/repo/feat'}], {'/w/repo/feat': '/repo'}), {repo:'/repo'});
  assert.deepEqual(scope(space, inSrc, {}), {under:'/repo/app/src'});
  assert.deepEqual(scope(space, [], {}), {under:'/repo'});
  assert.deepEqual(scope({...space, folder:'/Users/me/dots'}, [], {'/Users/me/dots': '/Users/me'}), {under:'/Users/me/dots'});
  assert.deepEqual(scope({...space, folder:'/Users/me'}, [], {'/Users/me': '/Users/me'}), {folder:'/Users/me'});
  assert.deepEqual(scope({...space, folder:null}, [], {}), {folder:null});
  assert.deepEqual(scope({...space, folder:'/Users/me/'}, [], {}), {folder:'/Users/me'});
  assert.deepEqual(scope({...space, folder:'/'}, [], {}), {folder:'/'});
  assert.deepEqual(scope({...space, folder:'/Users/me/notes'}, [], {}), {under:'/Users/me/notes'});
}
// The copy menu: what each item copies, from the session and its harness.
{
  const t = '/Users/me/.claude/projects/-Users-me-My-Project/' + s.id + '.jsonl';
  const c = logic.copyValues({...s, cwd:'/Users/me/My Project', transcript:t});
  assert.deepEqual(c, {session:s.id, resume:`cd '/Users/me/My Project' && claude --resume ${s.id} --dangerously-skip-permissions`,
    transcript:t, folder:'/Users/me/My Project'});
  // The copied command starts what Resume starts.
  assert.equal(c.resume.split(' && ')[1], resumeArgs(s, {id:'local', machine:null}).argv.join(' '));
  assert.equal(logic.copyValues({...s, id:'bad'}).resume, null);
  assert.equal(logic.copyValues({...s, program:'gemini'}).resume, null); // no adapter for it yet
  assert.equal(logic.copyValues({id:s.id}).folder, null);
  assert.equal(logic.copyValues({id:s.id}).resume, `claude --resume ${s.id}`);
  // The flag only for a session that started with it; any other start resumes as Claude Code asks.
  assert.equal(logic.modeLabel('bypassPermissions'), 'Bypass permissions');
  assert.equal(logic.modeLabel('acceptEdits'), 'Accept edits');
  assert.equal(logic.modeLabel('somethingNew'), 'somethingNew');
  for (const startMode of ['default', 'acceptEdits', null, undefined])
    assert.deepEqual(resumeArgs({...s, startMode}, {id:'local', machine:null}).argv, ['claude', '--resume', s.id], String(startMode));
  // Grok: --resume, and always approving again only after a start with it (--yolo).
  const grok = {...s, program:'grok', startMode:'default'};
  assert.deepEqual(resumeArgs(grok, {id:'local', machine:null}).argv, ['grok', '--resume', s.id]);
  assert.deepEqual(resumeArgs({...grok, startMode:'yolo'}, {id:'local', machine:null}).argv, ['grok', '--resume', s.id, '--always-approve']);
  // OpenCode: its own ids, ses_…; a UUID isn't one of them, and its ids aren't Claude's.
  const opencode = {...s, program:'opencode', id:'ses_e111fbe53ffeSVx1dkTIKqfIR6', startMode:undefined};
  assert.deepEqual(resumeArgs(opencode, {id:'local', machine:null}).argv, ['opencode', '--session', opencode.id]);
  assert.equal(logic.copyValues({...opencode, cwd:'/repo'}).resume, `cd /repo && opencode --session ${opencode.id}`);
  assert.throws(() => resumeArgs({...opencode, id:s.id}, {id:'local', machine:null}), /session ID/);
  assert.throws(() => resumeArgs({...s, id:opencode.id}, {id:'local', machine:null}), /session ID/);
  assert.equal(logic.harnessName(opencode), 'OpenCode');
  assert.equal(logic.modeLabel('yolo'), 'YOLO (always approve)');
  // A session that started in auto or plan mode comes back in it.
  for (const startMode of ['auto', 'plan'])
    assert.deepEqual(resumeArgs({...s, startMode}, {id:'local', machine:null}).argv, ['claude', '--resume', s.id, '--permission-mode', startMode]);
  assert.equal(logic.copyValues({...s, cwd:'/repo', startMode:'plan'}).resume, `cd /repo && claude --resume ${s.id} --permission-mode plan`);
  assert.deepEqual(logic.COPIES.map(x => x.key), ['session', 'resume', 'transcript', 'folder']);
  assert.throws(() => resumeArgs({...s, program:'gemini'}, {id:'local', machine:null}), /gemini/);
  // Codex: resumed as Herdr starts it, without a shared daemon; no permission flags of ours.
  const codex = {...s, program:'codex', cwd:'/repo'};
  assert.equal(logic.copyValues(codex).resume, `cd /repo && codex --no-daemon resume ${s.id}`);
  assert.deepEqual(resumeArgs(codex, {id:'local', machine:null}).argv, ['codex', '--no-daemon', 'resume', s.id]);
  // Without a description or title, a tab or space is named after the harness.
  const bare = {...codex, cwd:'/repo/app', description:'', title:''};
  assert.equal(logic.resumePlan(bare, allSpaces, [], null).args.name, 'Codex session');
  assert.equal(logic.resumePlan({...bare, program:undefined}, allSpaces, [], null).args.name, 'Claude Code session');
}
// The dot: can it be resumed, and was it left mid-work?
assert.equal(logic.sessionState({exists:false, ending:'unanswered'}).key, 'missing');
assert.equal(logic.sessionState({exists:true, ending:'finished'}).key, 'finished');
for (const ending of ['unanswered', 'interrupted', 'mid-action']) assert.equal(logic.sessionState({exists:true, ending}).key, 'stopped');
assert.match(logic.sessionState({exists:true, ending:'unanswered'}).label, /no answer/);
assert.equal(logic.sessionState({exists:true, ending:null}).key, 'empty');
console.log('all pure assertions passed; no agent launched');
// The repository a folder is in: git's common folder without its /.git (a worktree's is the main
// checkout's), else that common folder itself (a bare repository).
assert.equal(logic.repoFromGit('/code/shop/.git\n/w/shop/feat\n'), '/code/shop');
assert.equal(logic.repoFromGit('/srv/shop.git\n/w/shop/feat\n'), '/srv/shop.git');
assert.equal(logic.repoFromGit(''), null);
