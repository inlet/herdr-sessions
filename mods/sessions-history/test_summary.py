import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('summarize', Path(__file__).with_name('summarize.py'))
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


class SummaryTests(unittest.TestCase):
    def transcript(self, path, text='Make the panel compact.'):
        path.write_text('\n'.join(json.dumps(row) for row in [
            {'type':'user','message':{'content':text}},
            {'type':'user','isMeta':True,'message':{'content':'Hook instructions'}},
            {'type':'assistant','message':{'content':[
                {'type':'thinking','thinking':'private reasoning'},
                {'type':'tool_use','name':'Bash','input':{'command':'secret'}},
                {'type':'text','text':'The panel is changed. The tests pass.'}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','content':'private tool output'}]}},
            {'type':'assistant','message':{'content':[{'type':'text','text':'Result: opening in the right Herdr space and copying the ID.'}]}},
        ]) + '\n')

    def test_claude_gets_complete_conversation_not_an_extract(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'session.jsonl'; self.transcript(p)
            captured = []
            def generate(text):
                captured.append(text)
                return 'The sessions panel is compact now, opens in the right space and copies the ID.'
            result = summary.cached_summary(p, Path(d) / 'cache', generate=generate) if hasattr(summary, 'conversation_text') else {}
            self.assertEqual(result.get('summarySource'), 'claude')
            self.assertEqual(len(captured), 1)
            for text in ('Make the panel compact.', 'The tests pass.', 'right Herdr space'):
                self.assertIn(text, captured[0])
            for text in ('Hook instructions', 'private reasoning', 'private tool output', 'secret'):
                self.assertNotIn(text, captured[0])

    def test_cache_reuses_success_and_invalidates_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p = root / 'session.jsonl'; self.transcript(p)
            calls = []
            def generate(text): calls.append(text); return 'A real summary.'
            if not hasattr(summary, 'conversation_text'):
                self.fail('Claude-based summary not implemented')
            first = summary.cached_summary(p, root / 'cache', generate=generate)
            self.assertEqual(summary.cached_summary(p, root / 'cache', generate=generate), first)
            self.assertEqual(len(calls), 1)
            self.transcript(p, 'Now add search.')
            summary.cached_summary(p, root / 'cache', generate=generate)
            self.assertEqual(len(calls), 2)

    def test_errors_and_empty_results_are_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p = root / 'session.jsonl'; self.transcript(p)
            if not hasattr(summary, 'conversation_text'): self.fail('Missing Claude summary')
            for generate in (lambda _: '', lambda _: (_ for _ in ()).throw(RuntimeError('API failed'))):
                with self.assertRaises(RuntimeError): summary.cached_summary(p, root / 'cache', generate=generate)
            self.assertFalse(list((root / 'cache').glob('*.json')))

    def test_word_limit(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'session.jsonl'; self.transcript(p)
            if not hasattr(summary, 'conversation_text'): self.fail('Missing Claude summary')
            result = summary.cached_summary(p, Path(d) / 'cache', generate=lambda _: 'woord ' * 150)
            self.assertEqual(len(result['summary'].split()), 60)

    def test_large_and_empty_conversation_fail_before_paid_call(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'session.jsonl'; self.transcript(p, 'word ' * 300000)
            if not hasattr(summary, 'conversation_text'): self.fail('Missing Claude summary')
            with self.assertRaisesRegex(RuntimeError, 'too large'): summary.conversation_text(p)
            p.write_text('bad\n{}\n')
            with self.assertRaisesRegex(RuntimeError, 'conversation'): summary.conversation_text(p)

    def test_cli_has_no_tools_and_does_not_resume_source_session(self):
        if not hasattr(summary, 'claude_arguments'): self.fail('Missing Claude CLI adapter')
        args = summary.claude_arguments()
        self.assertIn('--no-session-persistence', args)
        self.assertIn('--strict-mcp-config', args)
        self.assertEqual(args[args.index('--tools') + 1], '')
        self.assertNotIn('--resume', args)
        self.assertNotIn('--dangerously-skip-permissions', args)
        self.assertEqual(args[args.index('--effort') + 1], 'low')
        self.assertIn('--model', args)
        self.assertEqual(args[args.index('--model') + 1], 'claude-haiku-5-5')

    def append(self, path, *rows):
        with path.open('a') as f:
            for row in rows: f.write(json.dumps(row) + '\n')

    def test_claude_codes_own_recap_needs_no_paid_call(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'session.jsonl'; self.transcript(p)
            self.append(p,
                {'type':'system','subtype':'away_summary','content':'Earlier state.','isMeta':False},
                {'type':'user','message':{'content':'One more question'}},
                {'type':'system','subtype':'away_summary','content':' The panel is done; next step: merge. ','isMeta':False},
                # Tool output, hooks and commands after the recap are no new request.
                {'type':'user','message':{'content':[{'type':'tool_result','content':'output'}]}},
                {'type':'user','isMeta':True,'message':{'content':'Hook instructions'}},
                {'type':'user','message':{'content':'<command-name>/exit</command-name>'}},
                {'type':'assistant','message':{'content':[{'type':'text','text':'Goodbye.'}]}})
            calls = []
            result = summary.session_summary(p, Path(d) / 'cache', generate=lambda text: calls.append(text) or 'x')
            self.assertEqual(result, {'summary': 'The panel is done; next step: merge.', 'summarySource': 'recap'})
            self.assertEqual(calls, [])

    def test_a_request_after_the_recap_makes_it_stale(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'session.jsonl'; self.transcript(p)
            self.append(p,
                {'type':'system','subtype':'away_summary','content':'The panel is done.','isMeta':False},
                {'type':'user','message':{'content':[{'type':'text','text':'Now add search.'}]}})
            result = summary.session_summary(p, Path(d) / 'cache', generate=lambda _: 'Search is added.')
            self.assertEqual(result, {'summary': 'Search is added.', 'summarySource': 'claude'})

    def test_without_recap_claude_summarizes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'session.jsonl'; self.transcript(p)
            result = summary.session_summary(p, Path(d) / 'cache', generate=lambda _: 'A summary.')
            self.assertEqual(result['summarySource'], 'claude')

    def test_cli_can_use_a_claude_ai_login(self):
        # --bare reads only ANTHROPIC_API_KEY or apiKeyHelper, never OAuth or the keychain:
        # someone logged in with claude.ai gets "Not logged in".
        self.assertNotIn('--bare', summary.claude_arguments())
        self.assertEqual(summary.claude_arguments()[summary.claude_arguments().index('--setting-sources') + 1], '')

    def test_a_codex_session(self):
        sid = '01a20de7-3e13-7853-be86-bc956e597fbd'
        def message(role, text):
            kind = 'input_text' if role == 'user' else 'output_text'
            return {'type': 'response_item', 'payload': {'type': 'message', 'role': role, 'content': [{'type': kind, 'text': text}]}}
        with tempfile.TemporaryDirectory() as d:
            claude, codex = Path(d) / 'projects', Path(d) / 'sessions'
            claude.mkdir(); (codex / '2026' / '11' / '24').mkdir(parents=True)
            path = codex / '2026' / '11' / '24' / f'rollout-2026-11-24T15-13-08-{sid}.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in [
                {'type': 'session_meta', 'payload': {'id': sid, 'cwd': d}},
                message('user', '# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>Never push.</INSTRUCTIONS>'),
                message('user', 'Add search'),
                message('assistant', 'Search is in.'),
            ]) + '\n')
            # A sub-agent's thread of the same session isn't it.
            (path.parent / f'rollout-2026-11-24T15-13-09-{sid}_01a1e60e-5192-7342-9bf5-692b7074ed1a.jsonl').write_text('{}\n')
            self.assertEqual(summary.session_file(claude, sid, codex), path)
            self.assertEqual(summary.conversation_text(path), 'User:\nAdd search\n\nAssistant:\nSearch is in.')
            self.assertIsNone(summary.recap(path))
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                summary.session_file(claude, '12345678-1234-1234-1234-123456789abc', codex)

    def test_a_grok_session(self):
        sid = '01a20e1e-d7c2-7bf2-81b3-c426e21ee4de'
        with tempfile.TemporaryDirectory() as d:
            at = summary.reader.locations(d)
            folder = at.grok / '%2Frepo' / sid; folder.mkdir(parents=True)
            path = folder / 'chat_history.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in [
                {'type': 'system', 'content': 'You are Grok'},
                {'type': 'user', 'content': '<user_info>\nOS Version: macos\n</user_info>'},
                {'type': 'user', 'content': [{'type': 'text', 'text': '<user_query>\nAdd search\n</user_query>'}], 'prompt_index': 0},
                {'type': 'assistant', 'content': '', 'tool_calls': '[{"id": "call-1"}]'},
                {'type': 'tool_result', 'tool_call_id': 'call-1', 'content': 'private tool output'},
                {'type': 'assistant', 'content': 'Search is in.'},
            ]) + '\n')
            source = summary.session_source(sid, at)
            self.assertEqual(source.key, str(path.resolve()))
            self.assertEqual(source.text(), 'User:\nAdd search\n\nAssistant:\nSearch is in.')

    def test_a_pi_session(self):
        sid = '01a21002-bc8c-7000-8c73-9a4102cd2b7f'
        with tempfile.TemporaryDirectory() as d:
            at = summary.reader.locations(d, env={})
            folder = at.pi / '--repo--'; folder.mkdir(parents=True)
            path = folder / f'2026-11-25T00-02-24-524Z_{sid}.jsonl'
            said = lambda role, content, **more: {'type': 'message', 'message': {'role': role, 'content': content, **more}}
            path.write_text('\n'.join(json.dumps(r) for r in [
                {'type': 'session', 'version': 3, 'id': sid, 'cwd': '/repo'},
                said('system', '', sections={'preamble': 'You are a coding assistant'}),
                said('user', 'Add search'),
                said('assistant', [{'type': 'thinking', 'thinking': 'private'}, {'type': 'toolCall', 'id': 'c1', 'name': 'bash'}], stopReason='toolUse'),
                said('toolResult', [{'type': 'text', 'text': 'private tool output'}], toolCallId='c1'),
                said('assistant', [{'type': 'text', 'text': 'Search is in.'}], stopReason='stop'),
            ]) + '\n')
            source = summary.session_source(sid, at)
            self.assertEqual(source.key, str(path.resolve()))
            self.assertEqual(source.text(), 'User:\nAdd search\n\nAssistant:\nSearch is in.')
            self.assertIsNone(source.recap())

    def test_an_opencode_session(self):
        import sqlite3
        sid = 'ses_e111fbe53ffeSVx1dkTIKqfIR6'
        with tempfile.TemporaryDirectory() as d:
            at = summary.reader.locations(d); at.opencode.parent.mkdir(parents=True)
            db = sqlite3.connect(at.opencode)
            db.executescript('''create table session (id text, parent_id text, time_updated integer);
                create table message (id text, session_id text, time_created integer, data text);
                create table part (id text, message_id text, session_id text, data text);''')
            db.execute('insert into session values (?, null, 1)', (sid,))
            for n, (role, part) in enumerate([('user', {'type': 'text', 'text': 'context', 'synthetic': True}), ('user', {'type': 'text', 'text': 'Add search'}),
                                              ('assistant', {'type': 'tool', 'tool': 'bash'}), ('assistant', {'type': 'text', 'text': 'Search is in.'})]):
                db.execute('insert into message values (?, ?, ?, ?)', (f'msg_{n}', sid, n, json.dumps({'role': role})))
                db.execute('insert into part values (?, ?, ?, ?)', (f'prt_{n}', f'msg_{n}', sid, json.dumps(part)))
            db.commit()
            calls = []
            generate = lambda text: calls.append(text) or 'Search is added.'
            source = summary.session_source(sid, at)
            first = summary.session_summary(source, Path(d) / 'cache', generate=generate)
            self.assertEqual(first, {'summary': 'Search is added.', 'summarySource': 'claude'})
            self.assertEqual(calls, ['User:\nAdd search\n\nAssistant:\nSearch is in.'])
            # Cached until OpenCode changes the session.
            self.assertEqual(summary.session_summary(summary.session_source(sid, at), Path(d) / 'cache', generate=generate), first)
            self.assertEqual(len(calls), 1)
            db.execute('update session set time_updated = 2'); db.commit(); db.close()
            summary.session_summary(summary.session_source(sid, at), Path(d) / 'cache', generate=generate)
            self.assertEqual(len(calls), 2)
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                summary.session_source('ses_000000000000000000000', at)


if __name__ == '__main__': unittest.main()
