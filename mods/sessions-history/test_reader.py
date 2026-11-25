import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('reader', Path(__file__).with_name('reader.py'))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

class ReaderTests(unittest.TestCase):
    def test_metadata_and_subagents(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); project = root / 'project'; project.mkdir()
            sid = '12345678-1234-1234-1234-123456789abc'
            path = project / (sid + '.jsonl')
            path.write_text('bad\n' + json.dumps({'type':'user','cwd':d,'message':{'content':[{'type':'text','text':'First prompt'}]}}) + '\n' + json.dumps({'type':'custom-title','customTitle':'Named'}) + '\n')
            sub = project / 'subagents'; sub.mkdir(); (sub / 'agent.jsonl').write_text('{}')
            rows = list(reader.sessions(root))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['id'], sid)
            self.assertEqual(rows[0]['title'], 'Named')
            self.assertEqual(rows[0]['cwd'], d)
            self.assertTrue(rows[0]['exists'])
            # The file itself: Claude Code files a session under the folder it started in, which
            # isn't always the last folder it worked in.
            self.assertEqual(rows[0]['transcript'], str(path))
    def test_large_transcript_tail_and_missing_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'12345678-1234-1234-1234-123456789abc.jsonl'
            p.write_bytes(b'{"type":"user","message":{"content":"Hello"}}\n' + b'x' * 400000 + b'\n{"type":"custom-title","customTitle":"Tail"}\n')
            row = reader.metadata(p)
            self.assertEqual(row['title'], 'Tail')
            self.assertIsNone(row['cwd'])
            self.assertFalse(row['exists'])
            self.assertLessEqual(sum(map(len, reader.chunks(p))), 262144)
    def test_description_prefers_saved_agent_title(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '12345678-1234-1234-1234-123456789abc.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in [
                {'type': 'user', 'message': {'content': 'Fix the\nlogin form'}},
                {'type': 'ai-title', 'aiTitle': 'Improve the login form'},
            ]) + '\n')
            self.assertEqual(reader.metadata(p).get('description'), 'Improve the login form')

    def test_description_ignores_hook_and_command_messages(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '12345678-1234-1234-1234-123456789abc.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in [
                {'type': 'user', 'message': {'content': '<system-reminder>Skill instructions</system-reminder>'}},
                {'type': 'user', 'message': {'content': '<command-name>/effort</command-name>'}},
                {'type': 'last-prompt', 'lastPrompt': 'Make the\n sessions panel compact'},
                {'type': 'ai-title', 'aiTitle': 'Superpowers using-superpowers skill'},
            ]) + '\n')
            self.assertEqual(reader.metadata(p).get('description'), 'Make the sessions panel compact')

    def test_open_sessions_excluded_but_stale_registrations_remain(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'projects'; project = root / 'project'; project.mkdir(parents=True)
            registry = Path(d) / 'sessions'; registry.mkdir()
            live = '12345678-1234-1234-1234-123456789abc'
            closed = '22345678-1234-1234-1234-123456789abc'
            for sid, pid in [(live, 101), (closed, 202)]:
                (project / (sid + '.jsonl')).write_text('{}\n')
                (registry / (str(pid) + '.json')).write_text(json.dumps({'sessionId': sid, 'pid': pid}))
            active = reader.active_session_ids(registry, is_live=lambda pid: pid == 101)
            self.assertEqual(active, {live})
            self.assertEqual([r['id'] for r in reader.sessions(root, active)], [closed])
            (registry / 'broken.json').write_text('bad')
            self.assertEqual(reader.active_session_ids(registry, is_live=lambda pid: False), set())

    def test_details_use_saved_summary_not_prompt_fragments(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '12345678-1234-1234-1234-123456789abc.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in [
                {'type': 'user', 'message': {'content': 'Build a collapsible sessions panel for the project.'}},
                {'type': 'ai-title', 'aiTitle': 'Improve the sessions panel'},
                {'type': 'user', 'message': {'content': 'Add a context menu with session details and a copy button.'}},
            ]) + '\n')
            row = reader.metadata(p)
            self.assertIsNone(row.get('summary'))
            self.assertEqual(row['description'], 'Improve the sessions panel')
            with p.open('a') as f:
                f.write(json.dumps({'type':'summary','summary':'The sessions panel is compact now, with context details and copying the ID.'}) + '\n')
            self.assertEqual(reader.metadata(p)['summary'], 'The sessions panel is compact now, with context details and copying the ID.')
            self.assertEqual(reader.metadata(p)['summarySource'], 'saved')

    def test_context_only_exposes_measured_fields_for_matching_session(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); sid = '12345678-1234-1234-1234-123456789abc'
            p = root / (sid + '.json')
            p.write_text(json.dumps({'session': sid, 'at': 1793984533000,
                'model': 'model-id', 'context': {'percentage': 24.5, 'tokens': 49000, 'size': 200000},
                'gateway': {'private': 'never return this'}}))
            self.assertEqual(getattr(reader, 'context_snapshot', lambda *_: None)(root, sid), {
                'percentage': 24.5, 'tokens': 49000, 'size': 200000,
                'at': 1793984533000, 'model': 'model-id'})
            for value in (None, True, -1, 101, '25', float('nan')):
                p.write_text(json.dumps({'session': sid, 'at': 1793984533000, 'context': {'percentage': value}}))
                self.assertIsNone(reader.context_snapshot(root, sid))
            p.write_text(json.dumps({'session': 'different', 'at': 1793984533000, 'context': {'percentage': 20}}))
            self.assertIsNone(reader.context_snapshot(root, sid))
            p.write_text('bad')
            self.assertIsNone(reader.context_snapshot(root, sid))
            self.assertIsNone(reader.context_snapshot(root, '../outside'))
            self.assertIsNone(reader.context_snapshot(root, '22345678-1234-1234-1234-123456789abc'))

    def test_missing_root(self):
        self.assertEqual(list(reader.sessions(Path('/nonexistent-session-root'))), [])

    def write_session(self, path, middle=()):
        rows = [{'type': 'user', 'cwd': '/repo', 'gitBranch': 'main', 'message': {'content': 'Fix login'}},
                *middle,
                {'type': 'assistant', 'cwd': '/repo', 'gitBranch': 'fix/login', 'message': {'content': [{'type': 'text', 'text': 'Done'}]}}]
        path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')

    def test_facts_from_the_whole_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '12345678-1234-1234-1234-123456789abc.jsonl'
            filler = [{'type': 'user', 'message': {'content': 'x' * 1000}}] * 300  # pushes the facts out of the head and tail
            pr = {'type': 'pr-link', 'prNumber': 27, 'prUrl': 'https://github.com/o/r/pull/27', 'prRepository': 'o/r'}
            self.write_session(p, [*filler,
                {'type': 'cost-state', 'totalCostUSD': 1.5, 'totalDuration': 60000, 'startTime': 1793900000000,
                 'totalLinesAdded': 3, 'totalLinesRemoved': 1, 'modelUsage': {'claude-haiku-5-5': {}}},
                pr, pr,
                {'type': 'pr-link', 'prNumber': 9, 'prUrl': 'javascript:alert(1)', 'prRepository': 'o/r'},
                {'type': 'cost-state', 'totalCostUSD': 6.13, 'totalDuration': 7996600, 'startTime': 1793901654918,
                 'totalLinesAdded': 286, 'totalLinesRemoved': 32, 'modelUsage': {'claude-opus-5-5': {}, 'claude-haiku-5-5': {}}},
                *filler])
            row = reader.session_row(p)
            self.assertEqual(row['branch'], 'fix/login')
            self.assertEqual((row['cost'], row['duration'], row['started']), (6.13, 7996600, 1793901654918))
            self.assertEqual((row['linesAdded'], row['linesRemoved']), (286, 32))
            self.assertEqual(row['models'], ['claude-haiku-5-5', 'claude-opus-5-5'])
            self.assertEqual(row['prs'], [{'number': 27, 'url': 'https://github.com/o/r/pull/27', 'repo': 'o/r'}])

    def test_the_permission_mode_it_started_in(self):
        prompt = {'type': 'user', 'message': {'content': 'Now add search'}}
        cases = {
            # Started with --dangerously-skip-permissions, later switched with shift+tab: the start counts.
            'bypassPermissions': [{**prompt, 'permissionMode': 'bypassPermissions'}, {'type': 'permission-mode', 'permissionMode': 'default'},
                                  {**prompt, 'permissionMode': 'default'}],
            'plan': [{'type': 'permission-mode', 'permissionMode': 'plan'}, {**prompt, 'permissionMode': 'plan'}],
            None: [prompt, {'type': 'user', 'permissionMode': 42, 'message': {'content': 'x'}}],
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '12345678-1234-1234-1234-123456789abc.jsonl'
            for expected, rows in cases.items():
                p.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
                self.assertEqual(reader.metadata(p)['startMode'], expected, expected)

    def test_how_a_session_ended(self):
        answer = {'type': 'assistant', 'message': {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Done.'}]}}
        prompt = {'type': 'user', 'message': {'content': 'Now add search'}}
        cases = {
            'finished': [prompt, answer],
            # Commands and hook messages after the answer don't change how the conversation ended.
            'finished ': [prompt, answer, {'type': 'user', 'message': {'content': '<command-name>/exit</command-name>'}},
                          {'type': 'user', 'isMeta': True, 'message': {'content': 'Hook output'}},
                          {'type': 'user', 'isSidechain': True, 'message': {'content': 'sub-agent'}}],
            'unanswered': [answer, prompt],
            'finished  ': [prompt, answer, {'type': 'user', 'isCompactSummary': True, 'message': {'content': 'This session is being continued…'}}],
            'interrupted': [prompt, {'type': 'user', 'message': {'content': [{'type': 'text', 'text': '[Request interrupted by user]'}]}}],
            'mid-action': [prompt, {'type': 'assistant', 'message': {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use'}]}}],
            'mid-action ': [prompt, {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'ok'}]}}],
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '12345678-1234-1234-1234-123456789abc.jsonl'
            for expected, rows in cases.items():
                p.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
                self.assertEqual(reader.session_row(p)['ending'], expected.strip(), expected)
            # Bookkeeping after the last message, beyond what the head and tail reads see.
            p.write_text('\n'.join(json.dumps(r) for r in cases['unanswered']) + '\n'
                         + (json.dumps({'type': 'api-request-blob', 'data': 'x' * 1000}) + '\n') * 400)
            self.assertEqual(reader.session_row(p)['ending'], 'unanswered')
            p.write_text('{}\n')
            self.assertIsNone(reader.session_row(p)['ending'])

    def test_facts_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '12345678-1234-1234-1234-123456789abc.jsonl'
            p.write_text('{}\n')
            row = reader.session_row(p)
            self.assertEqual((row['branch'], row['cost'], row['duration'], row['prs'], row['models']), (None, None, None, [], []))

    def test_unchanged_sessions_come_from_the_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'projects'; (root / 'p').mkdir(parents=True); cache = Path(d) / 'meta.json'
            a = root / 'p' / '12345678-1234-1234-1234-123456789abc.jsonl'; b = root / 'p' / '22345678-1234-1234-1234-123456789abc.jsonl'
            self.write_session(a); self.write_session(b)
            read = []
            original = reader.session_row
            reader.session_row = lambda path: read.append(path.stem) or original(path)
            try:
                first = list(reader.sessions(root, (), cache))
                self.assertEqual(sorted(read), sorted([a.stem, b.stem]))
                read.clear()
                self.assertEqual(list(reader.sessions(root, (), cache)), first)
                self.assertEqual(read, [])
                with a.open('a') as f: f.write(json.dumps({'type': 'pr-link', 'prNumber': 1, 'prUrl': 'https://github.com/o/r/pull/1'}) + '\n')
                b.unlink()
                rows = list(reader.sessions(root, (), cache))
                self.assertEqual(read, [a.stem])
                self.assertEqual([r['prs'][0]['number'] for r in rows], [1])
                self.assertEqual(list(json.loads(cache.read_text())['sessions']), [str(a)])
            finally:
                reader.session_row = original

CODEX_ID = '01a20de7-3e13-7853-be86-bc956e597fbd'


def text(role, *texts):
    kind = 'input_text' if role == 'user' else 'output_text'
    return {'type': 'message', 'role': role, 'content': [{'type': kind, 'text': t} for t in texts]}


def item(payload): return {'timestamp': '2026-11-24T14:13:09Z', 'type': 'response_item', 'payload': payload}
def event(payload): return {'timestamp': '2026-11-24T14:13:20Z', 'type': 'event_msg', 'payload': payload}


def codex_file(root, rows, sid=CODEX_ID, stamp='2026-11-24T15-13-08', suffix=''):
    folder = root / '2026' / '11' / '24'; folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'rollout-{stamp}-{sid}{suffix}.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    return path


class CodexTests(unittest.TestCase):
    """Codex keeps a session in ~/.codex/sessions/<year>/<month>/<day>/rollout-<time>-<id>.jsonl."""

    def session(self, cwd, more=()):
        return [
            {'timestamp': '2026-11-24T14:13:08Z', 'type': 'session_meta', 'payload': {
                'id': CODEX_ID, 'cwd': cwd, 'git': {'branch': 'main'}, 'timestamp': '2026-11-24T14:13:08.262Z'}},
            {'type': 'turn_context', 'payload': {'cwd': cwd, 'model': 'gpt-6-luna'}},
            # Context Codex adds as a user message: not something the person wrote.
            item(text('user', '# AGENTS.md instructions for ' + cwd + '\n\n<INSTRUCTIONS>…', '<environment_context>\n  <cwd>' + cwd + '</cwd>\n</environment_context>')),
            item(text('developer', '<permissions instructions>…')),
            item(text('user', 'Add search to the list')),
            item({'type': 'function_call', 'name': 'shell', 'arguments': '{}', 'call_id': 'c1'}),
            item({'type': 'function_call_output', 'call_id': 'c1', 'output': 'ok'}),
            item(text('assistant', 'Search is in.')),
            event({'type': 'task_complete', 'duration_ms': 65000, 'last_agent_message': 'Search is in.'}),
            event({'type': 'token_count', 'info': None}),
            *more,
        ]

    def test_a_session(self):
        with tempfile.TemporaryDirectory() as d:
            path = codex_file(Path(d), self.session(d))
            row = reader.codex_row(path)
            self.assertEqual(row['id'], CODEX_ID)
            self.assertEqual(row['program'], 'codex')
            self.assertEqual(row['cwd'], d)
            self.assertEqual(row['branch'], 'main')
            self.assertEqual(row['description'], 'Add search to the list')
            self.assertEqual(row['ending'], 'finished')
            self.assertEqual(row['models'], ['gpt-6-luna'])
            self.assertEqual(row['duration'], 65000)
            self.assertEqual(row['started'], 1795529588262)  # date -u: 2026-11-24T14:13:08 = 1795529588
            self.assertEqual((row['cost'], row['prs']), (None, []))

    def test_how_a_codex_session_ended(self):
        with tempfile.TemporaryDirectory() as d:
            cases = {
                'interrupted': [event({'type': 'turn_aborted', 'reason': 'interrupted'})],
                'unanswered': [item(text('user', 'And filtering now?'))],
                'mid-action': [item(text('user', 'And filtering now?')), item({'type': 'custom_tool_call', 'name': 'apply_patch'})],
            }
            for expected, more in cases.items():
                path = codex_file(Path(d), self.session(d, more))
                self.assertEqual(reader.codex_row(path)['ending'], expected, expected)
            # An aborted turn's time counts too.
            path = codex_file(Path(d), self.session(d, [event({'type': 'turn_aborted', 'duration_ms': 5000})]))
            self.assertEqual(reader.codex_row(path)['duration'], 70000)

    def test_a_2025_session(self):
        # Codex's older files: records without the type/payload wrapper, the folder only in the context.
        with tempfile.TemporaryDirectory() as d:
            path = codex_file(Path(d), [
                {'id': CODEX_ID, 'timestamp': '2025-08-28T11:21:58.507Z', 'instructions': None, 'git': {'branch': 'dev'}},
                {'record_type': 'state'},
                text('user', '<environment_context>\n  <cwd>' + d + '</cwd>\n</environment_context>'),
                text('user', '# Context from my IDE setup:\n\n## Active file: a.ts\n\n## My request for Codex:\nupdate'),
                {'type': 'function_call', 'name': 'shell', 'arguments': '{}', 'call_id': 'c1'},
            ], stamp='2025-08-28T13-21-58')
            row = reader.codex_row(path)
            self.assertEqual((row['cwd'], row['branch'], row['description']), (d, 'dev', 'update'))
            self.assertEqual(row['ending'], 'mid-action')
            self.assertEqual(row['started'], 1756380118507)

    def test_listed_with_names_without_sub_threads(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'sessions'
            main = codex_file(root, self.session(d))
            # A sub-agent's thread: the parent's id, then its own.
            codex_file(root, self.session(d), suffix='_01a1e60e-5192-7342-9bf5-692b7074ed1a')
            names = Path(d) / 'session_index.jsonl'
            names.write_text(json.dumps({'id': CODEX_ID, 'thread_name': 'First name'}) + '\n'
                             + json.dumps({'id': CODEX_ID, 'thread_name': 'Adding search'}) + '\n')
            rows = list(reader.sessions(Path(d) / 'no-claude', codex_root=root, codex_names=names))
            self.assertEqual([(r['id'], r['description'], r['transcript']) for r in rows], [(CODEX_ID, 'Adding search', str(main))])
            self.assertEqual(list(reader.sessions(Path(d) / 'no-claude', active={CODEX_ID}, codex_root=root)), [])

    def test_open_codex_sessions(self):
        # lsof -Fn: p<pid> then n<file> for each open file.
        out = f'p123\nn/dev/null\nn/Users/me/.codex/sessions/2026/11/24/rollout-2026-11-24T15-13-08-{CODEX_ID}.jsonl\n' \
              'n/Users/me/.codex/sessions/2026/11/16/rollout-2026-11-16T21-31-00-01a1e60e-0e7d-7aa1-872c-d9e9e7d240d9_01a1e60e-5192-7342-9bf5-692b7074ed1a.jsonl\n'
        class Done: stdout = out; returncode = 0
        self.assertEqual(reader.codex_open_ids(run=lambda *a, **k: Done()), {CODEX_ID})
        def fails(*a, **k): raise OSError('no lsof')
        self.assertEqual(reader.codex_open_ids(run=fails), set())


GROK_ID = '01a20e1e-d7c2-7bf2-81b3-c426e21ee4de'


def lines(rows): return '\n'.join(json.dumps(r) for r in rows) + '\n'


def grok_turn(outcome='completed', yolo=False, relationship='primary'):
    return [{'ts': '2026-11-24T15:13:55.000Z', 'type': 'turn_started', 'turn_number': 0, 'model_id': 'grok-4.7',
             'yolo_mode': yolo, 'session_relationship': relationship},
            {'ts': '2026-11-24T15:13:56.000Z', 'type': 'tool_completed', 'outcome': 'success'},
            *([{'ts': '2026-11-24T15:14:05.300Z', 'type': 'turn_ended', 'outcome': outcome}] if outcome else [])]


class GrokTests(unittest.TestCase):
    """Grok keeps a session in its own folder: ~/.grok/sessions/<folder, URL-encoded>/<id>/."""
    CHAT = [
        {'type': 'system', 'content': 'You are Grok'},
        # Context Grok sends as user messages: not something the person wrote.
        {'type': 'user', 'content': '<user_info>\nOS Version: macos\n</user_info>'},
        {'type': 'user', 'content': [{'type': 'text', 'text': '<system-reminder>skills</system-reminder>'}], 'synthetic_reason': 'system_reminder'},
        {'type': 'user', 'content': [{'type': 'text', 'text': '<user_query>\nAdd search\n</user_query>'}], 'prompt_index': 0},
        {'type': 'reasoning', 'id': 'rs_1', 'encrypted_content': 'x', 'status': 'completed'},
        {'type': 'assistant', 'content': 'Let me look.', 'tool_calls': '[{"id": "call-1", "name": "read_file"}]', 'model_id': 'grok-4.7'},
        {'type': 'tool_result', 'tool_call_id': 'call-1', 'content': 'ok'},
        {'type': 'assistant', 'content': 'Search is in.', 'model_id': 'grok-4.7'},
    ]

    def session(self, root, cwd, chat=CHAT, events=None, sid=GROK_ID, summary=None):
        from urllib.parse import quote
        folder = root / quote(cwd, safe='') / sid; folder.mkdir(parents=True, exist_ok=True)
        (folder / 'summary.json').write_text(json.dumps({
            'info': {'id': sid, 'cwd': cwd}, 'generated_title': 'Search in the list', 'head_branch': 'main',
            'created_at': '2026-11-24T15:13:52.186172Z', 'last_active_at': '2026-11-24T15:14:09.960897Z', **(summary or {})}))
        (folder / 'chat_history.jsonl').write_text(lines(chat))
        (folder / 'events.jsonl').write_text(lines(grok_turn() if events is None else events))
        (folder / 'usage.json').write_text(json.dumps({'sessionId': sid, 'session': {'costUsdTicks': 865800000}}))
        return folder / 'chat_history.jsonl'

    def test_a_session(self):
        with tempfile.TemporaryDirectory() as d:
            row = reader.grok_row(self.session(Path(d) / 'sessions', d))
            self.assertEqual((row['id'], row['program'], row['cwd'], row['branch']), (GROK_ID, 'grok', d, 'main'))
            self.assertEqual(row['description'], 'Search in the list')  # Grok's own title
            self.assertEqual(row['ending'], 'finished')
            self.assertEqual(row['models'], ['grok-4.7'])
            self.assertEqual(row['duration'], 10300)
            self.assertEqual(row['started'], 1795533232186)
            self.assertEqual(row['activity'], 1795533249.961)  # last_active_at, to the millisecond
            self.assertAlmostEqual(row['cost'], 0.08658)  # 10^10 ticks to the dollar
            self.assertEqual(row['startMode'], 'default')

    def test_without_a_title_its_first_question(self):
        with tempfile.TemporaryDirectory() as d:
            row = reader.grok_row(self.session(Path(d) / 'sessions', d, summary={'generated_title': ''}))
            self.assertEqual(row['description'], 'Add search')

    def test_how_a_grok_session_ended(self):
        ask = {'type': 'user', 'content': [{'type': 'text', 'text': '<user_query>And filtering now?</user_query>'}], 'prompt_index': 1}
        cases = {
            'unanswered': (self.CHAT + [ask], grok_turn(None)),
            'mid-action': (self.CHAT + [ask, {'type': 'assistant', 'content': '', 'tool_calls': '[{"id": "call-2"}]'}], grok_turn(None)),
            'interrupted': (self.CHAT + [ask, {'type': 'assistant', 'content': 'First'}], grok_turn('cancelled')),
            None: (self.CHAT[:3], []),
        }
        with tempfile.TemporaryDirectory() as d:
            for expected, (chat, events) in cases.items():
                self.assertEqual(reader.grok_row(self.session(Path(d) / 'sessions', d, chat, events))['ending'], expected, expected)

    def test_started_with_yolo(self):
        with tempfile.TemporaryDirectory() as d:
            row = reader.grok_row(self.session(Path(d) / 'sessions', d, events=grok_turn(yolo=True) + grok_turn()))
            self.assertEqual(row['startMode'], 'yolo')

    def test_listed_without_sub_agents(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'sessions'
            main = self.session(root, d)
            # A sub-agent's session sits in the same tree; its turns aren't the primary ones.
            self.session(root, d, sid='01a20e1e-0000-7bf2-81b3-c426e21ee4de', events=grok_turn(relationship='subagent'))
            rows = list(reader.sessions(Path(d) / 'no-claude', grok_root=root))
            self.assertEqual([(r['id'], r['transcript']) for r in rows], [(GROK_ID, str(main))])
            self.assertEqual(list(reader.sessions(Path(d) / 'no-claude', active={GROK_ID}, grok_root=root)), [])

    def test_open_grok_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            registry = Path(d) / 'active_sessions.json'
            registry.write_text(json.dumps([{'session_id': GROK_ID, 'pid': 101, 'cwd': d},
                                            {'session_id': '01a17d3a-b930-7ae3-b1fb-48b58bcc95ae', 'pid': 102, 'cwd': d}]))
            self.assertEqual(reader.grok_open_ids(registry, is_live=lambda pid: pid == 101), {GROK_ID})
            registry.write_text('not json')
            self.assertEqual(reader.grok_open_ids(registry, is_live=lambda pid: True), set())


OPENCODE_ID = 'ses_e111fbe53ffeSVx1dkTIKqfIR6'


def opencode_db(path, sessions=(), messages=()):
    """OpenCode's database, with the columns the list reads. A message: (session, role, data, parts)."""
    import sqlite3
    db = sqlite3.connect(path)
    db.executescript('''
        create table session (id text primary key, parent_id text, directory text not null, title text not null,
          cost real default 0 not null, summary_additions integer, summary_deletions integer,
          time_created integer not null, time_updated integer not null, time_archived integer);
        create table message (id text primary key, session_id text not null, time_created integer not null, data text not null);
        create table part (id text primary key, message_id text not null, session_id text not null,
          time_created integer not null, data text not null);''')
    for s in sessions:
        db.execute('insert into session values (?,?,?,?,?,?,?,?,?,?)', (s['id'], s.get('parent'), s['directory'], s.get('title', 'Adding search'),
                   s.get('cost', 0.0), s.get('added', 0), s.get('removed', 0), 1795009036716, s.get('updated', 1795009531540), s.get('archived')))
    for n, (sid, data, texts) in enumerate(messages):
        db.execute('insert into message values (?,?,?,?)', (f'msg_{n:03}', sid, 1795009036800 + n, json.dumps(data)))
        for i, text in enumerate(texts):
            part = {'type': 'text', 'text': text} if isinstance(text, str) else text
            db.execute('insert into part values (?,?,?,?,?)', (f'prt_{n:03}_{i}', f'msg_{n:03}', sid, 1795009036800 + n, json.dumps(part)))
    db.commit(); db.close()
    return path


def asked(text, sid=OPENCODE_ID): return (sid, {'role': 'user', 'time': {'created': 1795009036800}}, [text])


def answered(text, finish='stop', error=None, sid=OPENCODE_ID):
    data = {'role': 'assistant', 'finish': finish, 'modelID': 'big-pickle', 'time': {'created': 1795009037000, 'completed': 1795009049000}}
    if error: data['error'] = {'name': error}
    return (sid, data, [{'type': 'step-start'}, text] if text else [])


class OpenCodeTests(unittest.TestCase):
    """OpenCode keeps every session in one SQLite database: ~/.local/share/opencode/opencode.db."""

    def rows(self, d, messages, **session):
        db = opencode_db(Path(d) / 'opencode.db', [{'id': OPENCODE_ID, 'directory': d, **session}], messages)
        return list(reader.opencode_rows(db))

    def test_a_session(self):
        with tempfile.TemporaryDirectory() as d:
            [row] = self.rows(d, [asked('Add search'), answered('Search is in.')], added=12, removed=3)
            self.assertEqual((row['id'], row['program'], row['cwd'], row['exists']), (OPENCODE_ID, 'opencode', d, True))
            self.assertEqual(row['description'], 'Adding search')  # OpenCode's own title
            self.assertEqual(row['ending'], 'finished')
            self.assertEqual(row['models'], ['big-pickle'])
            self.assertEqual((row['started'], row['activity'], row['duration']), (1795009036716, 1795009531.54, 12000))
            self.assertEqual((row['linesAdded'], row['linesRemoved']), (12, 3))
            self.assertIsNone(row['cost'])  # 0: a free model, not a cost
            self.assertIsNone(row['transcript'])  # no file of its own

    def test_untitled_its_first_question(self):
        with tempfile.TemporaryDirectory() as d:
            [row] = self.rows(d, [asked({'type': 'text', 'text': 'context', 'synthetic': True}), asked('Add search')],
                              title='New session - 2026-11-18T13:40:01.954Z')
            self.assertEqual(row['description'], 'Add search')

    def test_how_an_opencode_session_ended(self):
        cases = {
            'unanswered': [asked('Add search')],
            'interrupted': [asked('Add search'), answered('First', finish=None, error='MessageAbortedError')],
            'mid-action': [asked('Add search'), answered('', finish='tool-calls')],
            None: [],
        }
        for expected, messages in cases.items():
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(self.rows(d, messages)[0]['ending'], expected, expected)
        # A failed request answered nothing.
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.rows(d, [asked('x'), answered('', finish=None, error='APIError')])[0]['ending'], 'unanswered')

    def test_without_sub_agents_archived_or_open_ones(self):
        with tempfile.TemporaryDirectory() as d:
            db = opencode_db(Path(d) / 'opencode.db', [
                {'id': OPENCODE_ID, 'directory': d},
                {'id': 'ses_child00000000000000000', 'parent': OPENCODE_ID, 'directory': d},
                {'id': 'ses_archived0000000000000', 'directory': d, 'archived': 1795009600000},
                {'id': 'ses_open00000000000000000', 'directory': d}])
            rows = list(reader.sessions(Path(d) / 'no-claude', active={'ses_open00000000000000000'}, opencode_db=db))
            self.assertEqual([r['id'] for r in rows], [OPENCODE_ID])

    def test_its_conversation(self):
        with tempfile.TemporaryDirectory() as d:
            db = opencode_db(Path(d) / 'opencode.db', [{'id': OPENCODE_ID, 'directory': d}],
                             [asked({'type': 'text', 'text': 'context', 'synthetic': True}), asked('Add search'),
                              answered('', finish='tool-calls'), answered('Search is in.')])
            self.assertEqual(reader.opencode_conversation(db, OPENCODE_ID), [('user', 'Add search'), ('assistant', 'Search is in.')])

    def test_open_opencode_sessions(self):
        # A resumed one names its session; a new one is the session made in its folder since it started.
        with tempfile.TemporaryDirectory() as d:
            db = opencode_db(Path(d) / 'opencode.db', [
                {'id': OPENCODE_ID, 'directory': d, 'updated': 1795009531540},
                {'id': 'ses_new000000000000000000', 'directory': d, 'updated': 1795533700000},
                {'id': 'ses_elsewhere000000000000', 'directory': '/other', 'updated': 1795533700000}])
            running = [(1795533600000, d, 'opencode'), (1795533600000, '/x', 'opencode --session ses_e111fbe53ffeSVx1dkTIKqfIR6')]
            self.assertEqual(reader.opencode_open_ids(db, running), {OPENCODE_ID, 'ses_new000000000000000000'})
            self.assertEqual(reader.opencode_open_ids(db, []), set())

    def test_running_opencode_programs(self):
        # ps -axo pid=,etime=,args=, then lsof -Fpn for their folders.
        class Ps: stdout = '  1652       00:10 opencode --session ses_e111fbe53ffeSVx1dkTIKqfIR6\n  1700    01:02:03 /bin/zsh -l\n'; returncode = 0
        class Lsof: stdout = 'p1652\nfcwd\nn/Users/me/app\n'; returncode = 0
        calls = []
        def run(argv, **_): calls.append(argv); return Ps() if argv[0] == 'ps' else Lsof()
        self.assertEqual(reader.processes('opencode', run=run, now=1795533610000),
                         [(1795533600000, '/Users/me/app', 'opencode --session ses_e111fbe53ffeSVx1dkTIKqfIR6')])
        self.assertEqual(calls[1][-1], '1652')


PI_ID = '01a21002-bc8c-7000-8c73-9a4102cd2b7f'


def pi_says(role, at, content, **more):
    """An entry of Pi's with a message in it, written at `at`."""
    return {'type': 'message', 'id': 'a1b2c3d4', 'parentId': None, 'timestamp': at, 'message': {'role': role, 'content': content, **more}}


def pi_usage(dollars): return {'input': 10, 'output': 20, 'totalTokens': 30, 'cost': {'input': 0, 'output': dollars, 'total': dollars}}


def pi_answer(at, text, stop='stop', dollars=0.02):
    return pi_says('assistant', at, [{'type': 'thinking', 'thinking': 'private'}, {'type': 'text', 'text': text}] if text else [],
                   model='claude-opus-5-5', stopReason=stop, usage=pi_usage(dollars))


def pi_file(root, cwd, entries, sid=PI_ID, group=True):
    """A Pi session file: in a folder named after its folder (`--home-me--`), or loose in a session folder of its own."""
    folder = root / ('--' + cwd.lstrip('/').replace('/', '-') + '--') if group else root
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'2026-11-25T00-02-24-524Z_{sid}.jsonl'
    path.write_text(lines([{'type': 'session', 'version': 3, 'id': sid, 'timestamp': '2026-11-25T00:02:24.524Z', 'cwd': cwd}, *entries]))
    return path


class PiTests(unittest.TestCase):
    """Pi keeps a session as a tree of entries: ~/.pi/agent/sessions/--<folder>--/<time>_<id>.jsonl."""
    CHAT = [
        {'type': 'thinking_level_change', 'timestamp': '2026-11-25T00:02:24.718Z', 'thinkingLevel': 'off'},
        {'type': 'model_change', 'timestamp': '2026-11-25T00:04:25.351Z', 'provider': 'anthropic', 'modelId': 'claude-opus-5-5'},
        # The system prompt Pi saves with the first request: not something the person wrote.
        pi_says('system', '2026-11-25T00:05:10.298Z', '', sections={'preamble': 'You are a coding assistant'}),
        pi_says('user', '2026-11-25T00:05:10.300Z', [{'type': 'text', 'text': 'Add search'}]),
        pi_answer('2026-11-25T00:05:12.000Z', 'Let me look.', stop='toolUse', dollars=0.01),
        pi_says('toolResult', '2026-11-25T00:05:12.500Z', [{'type': 'text', 'text': 'private tool output'}], toolCallId='c1', toolName='read'),
        pi_answer('2026-11-25T00:05:20.300Z', 'Search is in.'),
    ]

    def test_a_session(self):
        with tempfile.TemporaryDirectory() as d:
            row = reader.pi_row(pi_file(Path(d), d, self.CHAT))
            self.assertEqual((row['id'], row['program'], row['cwd'], row['exists']), (PI_ID, 'pi', d, True))
            self.assertEqual(row['description'], 'Add search')
            self.assertEqual(row['ending'], 'finished')
            self.assertEqual(row['models'], ['claude-opus-5-5'])
            self.assertEqual(row['started'], 1795564944524)
            self.assertEqual(row['duration'], 10000)  # from the question to the last answer
            self.assertAlmostEqual(row['cost'], 0.03)
            self.assertEqual((row['branch'], row['startMode'], row['prs']), (None, None, []))

    def test_its_name(self):
        named = [{'type': 'session_info', 'timestamp': '2026-11-25T00:06:00.000Z', 'name': 'First name'},
                 {'type': 'session_info', 'timestamp': '2026-11-25T00:07:00.000Z', 'name': 'Search in the list'}]
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(reader.pi_row(pi_file(Path(d), d, self.CHAT + named))['description'], 'Search in the list')
            # A name taken away again: its first question.
            cleared = named + [{'type': 'session_info', 'timestamp': '2026-11-25T00:08:00.000Z'}]
            self.assertEqual(reader.pi_row(pi_file(Path(d), d, self.CHAT + cleared))['description'], 'Add search')

    def test_all_its_costs(self):
        # Usage outside the conversation counts too: cache warming, and summaries made on compacting.
        more = [{'type': 'usage', 'timestamp': '2026-11-25T00:06:00.000Z', 'kind': 'cache_warm', 'usage': pi_usage(0.005)},
                {'type': 'compaction', 'timestamp': '2026-11-25T00:07:00.000Z', 'summary': 'Search added', 'firstKeptEntryId': 'a1b2c3d4', 'usage': pi_usage(0.004)}]
        with tempfile.TemporaryDirectory() as d:
            self.assertAlmostEqual(reader.pi_row(pi_file(Path(d), d, self.CHAT + more))['cost'], 0.039)
            self.assertIsNone(reader.pi_row(pi_file(Path(d), d, self.CHAT[:4]))['cost'])

    def test_how_a_pi_session_ended(self):
        ask = pi_says('user', '2026-11-25T00:06:00.000Z', 'And filtering now?')
        cases = {
            'unanswered': [ask],
            'mid-action': [ask, pi_answer('2026-11-25T00:06:01.000Z', '', stop='toolUse')],
            'interrupted': [ask, pi_answer('2026-11-25T00:06:01.000Z', 'First', stop='aborted')],
        }
        with tempfile.TemporaryDirectory() as d:
            for expected, more in cases.items():
                self.assertEqual(reader.pi_row(pi_file(Path(d), d, self.CHAT + more))['ending'], expected, expected)
            # A failed request answered nothing.
            failed = self.CHAT + [ask, pi_answer('2026-11-25T00:06:01.000Z', '', stop='error')]
            self.assertEqual(reader.pi_row(pi_file(Path(d), d, failed))['ending'], 'unanswered')
            # Cut off at its length limit: it stopped mid-work.
            cut = self.CHAT + [ask, pi_answer('2026-11-25T00:06:01.000Z', 'Half', stop='length')]
            self.assertEqual(reader.pi_row(pi_file(Path(d), d, cut))['ending'], 'mid-action')
            # Nothing said yet.
            row = reader.pi_row(pi_file(Path(d), d, self.CHAT[:3]))
            self.assertEqual((row['ending'], row['description']), (None, 'No description available'))

    def test_listed_without_open_ones(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'sessions'
            path = pi_file(root, d, self.CHAT)
            (path.parent / 'notes.jsonl').write_text('{}\n')  # not a session file of Pi's
            rows = list(reader.sessions(Path(d) / 'no-claude', pi_root=root))
            self.assertEqual([(r['id'], r['program'], r['transcript']) for r in rows], [(PI_ID, 'pi', str(path))])
            self.assertEqual(list(reader.sessions(Path(d) / 'no-claude', active={PI_ID}, pi_root=root)), [])
            # With a session folder of its own (PI_CODING_AGENT_SESSION_DIR), Pi keeps its files loose in it.
            loose = Path(d) / 'loose'
            pi_file(loose, d, self.CHAT, group=False)
            self.assertEqual([r['id'] for r in reader.sessions(Path(d) / 'no-claude', pi_root=loose)], [PI_ID])

    def test_where_pi_keeps_them(self):
        at = reader.locations('/home/me', env={})
        self.assertEqual(at.pi, Path('/home/me/.pi/agent/sessions'))
        self.assertEqual(reader.locations('/home/me', env={'PI_CODING_AGENT_DIR': '/pi'}).pi, Path('/pi/sessions'))
        self.assertEqual(reader.locations('/home/me', env={'PI_CODING_AGENT_DIR': '/pi', 'PI_CODING_AGENT_SESSION_DIR': '/s'}).pi, Path('/s'))

    def test_open_pi_sessions(self):
        # Pi holds no file open and keeps no list, and a running pi hides its arguments: the sessions
        # in a running pi's folder whose file changed since it started are open.
        import os
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'sessions'
            new = pi_file(root, d, self.CHAT)
            old = pi_file(root, d, self.CHAT, sid='01a21002-0000-7000-8c73-9a4102cd2b7f')
            elsewhere = pi_file(root, '/other', self.CHAT, sid='01a21002-1111-7000-8c73-9a4102cd2b7f')
            os.utime(old, (1795533500, 1795533500))
            for path in (new, elsewhere): os.utime(path, (1795533700, 1795533700))
            self.assertEqual(reader.pi_open_ids(root, [(1795533600000, d, 'pi')]), {PI_ID})
            self.assertEqual(reader.pi_open_ids(root, []), set())
            loose = Path(d) / 'loose'
            os.utime(pi_file(loose, d, self.CHAT, group=False), (1795533700, 1795533700))
            pi_file(loose, '/other', self.CHAT, sid='01a21002-1111-7000-8c73-9a4102cd2b7f', group=False)
            self.assertEqual(reader.pi_open_ids(loose, [(1795533600000, d, 'pi')]), {PI_ID})

    def test_running_pi_programs(self):
        # Pi sets its own process title, so ps shows `pi` without its arguments; pi-rpc is another program.
        class Ps: stdout = '  2915       00:10 pi  \n  2916       00:10 pi-rpc\n'; returncode = 0
        class Lsof: stdout = 'p2915\nfcwd\nn/Users/me/app\n'; returncode = 0
        run = lambda argv, **_: Ps() if argv[0] == 'ps' else Lsof()
        self.assertEqual(reader.processes('pi', run=run, now=1795533610000), [(1795533600000, '/Users/me/app', 'pi  ')])


if __name__ == '__main__': unittest.main()
