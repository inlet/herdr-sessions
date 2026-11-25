"""Bounded local transcript metadata; never emits transcript bodies."""
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

LIMIT = 128 * 1024
UUID = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
EMPTY_TITLE = re.compile(r'superpowers|using-\w+ skill', re.I)
MESSAGE = re.compile(rb'"type":\s*"(?:user|assistant)"')  # a cheap look before parsing a line


def chunks(path):
    with path.open('rb') as f:
        size = f.seek(0, 2); f.seek(0)
        first = f.read(LIMIT)
        if size <= LIMIT: return [first]
        f.seek(max(LIMIT, size - LIMIT))
        last = f.read(LIMIT)
        return [first.rsplit(b'\n', 1)[0], last.split(b'\n', 1)[-1]]


def one_line(text, limit=120):
    if not isinstance(text, str): return ''
    text = re.sub(r'<system-reminder\b[^>]*>.*?</system-reminder>', '', text, flags=re.S)
    if re.search(r'<(?:command-name|local-command|task-notification|tool_use|tool_result)\b', text): return ''
    text = ' '.join(text.split())
    return text[:limit - 1] + '…' if len(text) > limit else text


def ending_of(record):
    """How the conversation stood after this record: 'finished' (Claude's answer was complete),
    'unanswered' (your message got no answer), 'interrupted', or 'mid-action' (in a tool call).
    None when the record doesn't count: hooks, commands, sub-agents, other bookkeeping."""
    # A compact summary is Claude Code's, not yours: the conversation went on in a new transcript.
    if record.get('isMeta') or record.get('isSidechain') or record.get('isCompactSummary'): return None
    if record.get('type') not in ('user', 'assistant'): return None
    message = record.get('message') if isinstance(record.get('message'), dict) else {}
    if record['type'] == 'assistant':
        return 'finished' if message.get('stop_reason') in ('end_turn', 'stop_sequence') else 'mid-action'
    content = message.get('content')
    if isinstance(content, list):
        if any(isinstance(x, dict) and x.get('type') == 'tool_result' for x in content): return 'mid-action'
        content = ' '.join(x['text'] for x in content if isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str))
    if not isinstance(content, str): return None
    content = re.sub(r'<system-reminder\b[^>]*>.*?</system-reminder>', '', content, flags=re.S).strip()
    if not content: return None
    if content.startswith('[Request interrupted'): return 'interrupted'
    if content.startswith('<'): return None  # a command, its output or a notification
    return 'unanswered'


def metadata(path):
    cwd = None; title = None; prompt = None; ai = None; last_prompt = None; branch = None
    saved_summary = None; start_mode = None
    for chunk in chunks(path):
        for line in chunk.splitlines():
            try: record = json.loads(line)
            except (ValueError, UnicodeError): continue
            if not isinstance(record, dict): continue
            if isinstance(record.get('cwd'), str): cwd = record['cwd']
            if isinstance(record.get('gitBranch'), str) and record['gitBranch']: branch = record['gitBranch'][:200]
            # The permission mode it started in: bypassPermissions after --dangerously-skip-permissions.
            # The first chunk is the start, so the first mode recorded is it.
            if start_mode is None and isinstance(record.get('permissionMode'), str): start_mode = record['permissionMode'][:40]
            for key in ('customTitle', 'sessionName'):
                candidate = one_line(record.get(key))
                if candidate: title = candidate
            if record.get('type') == 'ai-title':
                candidate = one_line(record.get('aiTitle'))
                if candidate and not EMPTY_TITLE.search(candidate): ai = candidate
            if record.get('type') == 'last-prompt':
                candidate = one_line(record.get('lastPrompt'))
                if candidate and last_prompt is None: last_prompt = candidate
            if record.get('type') == 'user' and not record.get('isMeta'):
                message = record.get('message')
                content = message.get('content') if isinstance(message, dict) else None
                if isinstance(content, list):
                    content = ' '.join(x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str))
                candidate = one_line(content)
                if candidate and prompt is None: prompt = candidate
            if record.get('type') == 'summary':
                candidate = one_line(record.get('summary'), 2000)
                if candidate: saved_summary = candidate
    description = ai or prompt or last_prompt or title or 'No description available'
    return {'id': path.stem, 'title': title or (Path(cwd).name if cwd else 'Claude session'),
            'description': description, 'summary': saved_summary, 'summarySource': 'saved' if saved_summary else None, 'cwd': cwd,
            'branch': branch, 'startMode': start_mode, 'activity': path.stat().st_mtime, 'exists': bool(cwd and Path(cwd).is_dir())}


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def facts(path):
    """What only a full read finds: how the conversation ended (bookkeeping can fill the last
    hundreds of kilobytes), Claude Code's last cost record and the pull requests made in the session."""
    cost = None; prs = {}; messages = deque(maxlen=64)
    with path.open('rb') as stream:
        for line in stream:
            if MESSAGE.search(line): messages.append(line)
            if b'"cost-state"' not in line and b'"pr-link"' not in line: continue
            try: record = json.loads(line)
            except (ValueError, UnicodeError): continue
            if not isinstance(record, dict): continue
            if record.get('type') == 'cost-state': cost = record
            elif record.get('type') == 'pr-link':
                url = record.get('prUrl'); n = record.get('prNumber'); repo = record.get('prRepository')
                if isinstance(url, str) and url.startswith('https://') and url not in prs:
                    prs[url] = {'number': n if type(n) is int else None, 'url': url[:500], 'repo': repo[:200] if isinstance(repo, str) else None}
    ending = None
    for line in reversed(messages):
        try: record = json.loads(line)
        except (ValueError, UnicodeError): continue
        ending = ending_of(record) if isinstance(record, dict) else None
        if ending: break
    cost = cost or {}
    usage = cost.get('modelUsage')
    return {'ending': ending, 'cost': number(cost.get('totalCostUSD')), 'duration': number(cost.get('totalDuration')),
            'started': number(cost.get('startTime')), 'linesAdded': number(cost.get('totalLinesAdded')),
            'linesRemoved': number(cost.get('totalLinesRemoved')),
            'models': sorted(m[:100] for m in usage if isinstance(m, str)) if isinstance(usage, dict) else [],
            'prs': list(prs.values())}


def session_row(path):
    return {**metadata(path), **facts(path)}


# ---- Codex: ~/.codex/sessions/<year>/<month>/<day>/rollout-<time>-<id>.jsonl ----

# A session's own file; a sub-agent's thread adds `_<its id>` and is left out.
CODEX_FILE = re.compile(r'rollout-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-(?P<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\.jsonl')


def epoch_ms(stamp):
    """An ISO time ('2026-11-24T14:13:08.262Z') as epoch milliseconds; None when it isn't one."""
    m = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?', stamp.strip()) if isinstance(stamp, str) else None
    if not m: return None
    try: when = datetime.fromisoformat(m[1] + ('.' + (m[2] + '00000')[:6] if m[2] else '') + ('+00:00' if m[3] in (None, 'Z') else m[3]))
    except ValueError: return None
    return round(when.timestamp() * 1000)


def codex_item(record):
    """A Codex record's kind and content: newer files wrap it as {type, payload}, 2025's are bare."""
    if isinstance(record.get('payload'), dict): return record.get('type'), record['payload']
    return 'response_item', record


def codex_message(record):
    """('user' | 'assistant', text): a person's or Codex's own words. None for anything else, including
    the context Codex sends as user messages (AGENTS.md, <environment_context>, the IDE's open files)."""
    kind, item = codex_item(record)
    if kind != 'response_item' or item.get('type') != 'message' or item.get('role') not in ('user', 'assistant'): return None
    texts = []
    for part in item.get('content') if isinstance(item.get('content'), list) else []:
        text = part.get('text') if isinstance(part, dict) else None
        if not isinstance(text, str): continue
        text = text.strip()
        if item['role'] == 'user':
            if text.startswith('# Context from my IDE setup'):
                text = text.split('## My request for Codex:', 1)[1].strip() if '## My request for Codex:' in text else ''
            if text.startswith('<') or text.startswith('# AGENTS.md instructions'): continue
        if text: texts.append(text)
    return (item['role'], '\n'.join(texts)) if texts else None


def codex_ending(record):
    """As ending_of, for a Codex record: a turn completes or is aborted; between, it calls tools."""
    kind, item = codex_item(record)
    if kind == 'event_msg': return {'task_complete': 'finished', 'turn_aborted': 'interrupted'}.get(item.get('type'))
    if kind != 'response_item': return None
    if isinstance(item.get('type'), str) and re.search(r'_call(?:_output)?$', item['type']): return 'mid-action'
    message = codex_message(record)
    if message: return 'finished' if message[0] == 'assistant' else 'unanswered'
    return None


def codex_row(path):
    """A Codex session's row, with the same fields as a Claude Code session's."""
    sid = CODEX_FILE.fullmatch(path.name)['id']
    cwd = None; branch = None; started = None; prompt = None; ending = None; duration = None; models = set()
    with path.open('rb') as stream:
        for line in stream:
            try: record = json.loads(line)
            except (ValueError, UnicodeError): continue
            if not isinstance(record, dict): continue
            kind, item = codex_item(record)
            git = item.get('git') if isinstance(item.get('git'), dict) else {}
            if kind == 'session_meta' or 'instructions' in item:  # the first line, new and 2025 files
                if isinstance(item.get('cwd'), str): cwd = item['cwd']
                if isinstance(git.get('branch'), str): branch = git['branch'][:200]
                started = epoch_ms(item.get('timestamp')) or started
            elif kind == 'turn_context':
                if isinstance(item.get('cwd'), str): cwd = item['cwd']
                if isinstance(item.get('model'), str): models.add(item['model'][:100])
            elif kind == 'event_msg' and item.get('type') in ('task_complete', 'turn_aborted') and number(item.get('duration_ms')) is not None:
                duration = (duration or 0) + item['duration_ms']
            if cwd is None and item.get('type') == 'message':  # 2025 files: only in the context
                found = re.search(r'<cwd>([^<]+)</cwd>', json.dumps(item.get('content'), ensure_ascii=False))
                if found: cwd = found[1].strip()
            message = codex_message(record)
            if prompt is None and message and message[0] == 'user': prompt = one_line(message[1]) or None
            ending = codex_ending(record) or ending
    return {'id': sid, 'program': 'codex', 'title': Path(cwd).name if cwd else 'Codex session',
            'description': prompt or 'No description available', 'summary': None, 'summarySource': None,
            'cwd': cwd, 'branch': branch, 'activity': path.stat().st_mtime, 'exists': bool(cwd and Path(cwd).is_dir()),
            'ending': ending, 'cost': None, 'duration': duration, 'started': started, 'linesAdded': None,
            'linesRemoved': None, 'models': sorted(models), 'prs': []}


def codex_thread_names(index):
    """Codex's names for its sessions (session_index.jsonl, the last name of each), read on every
    listing: a rename doesn't touch the transcript, so a cached row can't know it."""
    names = {}
    try:
        with index.open('rb') as stream:
            for line in stream:
                try: record = json.loads(line)
                except (ValueError, UnicodeError): continue
                if isinstance(record, dict) and isinstance(record.get('id'), str) and one_line(record.get('thread_name')):
                    names[record['id']] = one_line(record['thread_name'])
    except OSError: pass
    return names


def codex_open_ids(run=subprocess.run):
    """Codex sessions open now: the transcripts a running Codex holds open. None found when lsof fails."""
    try: result = run(['lsof', '-w', '-Fn', '-c', '/^codex/i'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired): return set()
    ids = set()
    for line in (result.stdout or '').splitlines():
        found = CODEX_FILE.fullmatch(line[1:].rsplit('/', 1)[-1]) if line.startswith('n') else None
        if found: ids.add(found['id'])
    return ids


def records(path):
    """A JSONL file's objects; none when it's missing."""
    try:
        with path.open('rb') as stream:
            for line in stream:
                try: record = json.loads(line)
                except (ValueError, UnicodeError): continue
                if isinstance(record, dict): yield record
    except OSError: return


def small_json(path, limit=LIMIT * 8):
    """A small JSON object file; None when it's missing, too big or not one."""
    try:
        if path.stat().st_size > limit: return None
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError): return None


# ---- Grok: ~/.grok/sessions/<folder, URL-encoded>/<id>/, its chat in chat_history.jsonl ----

def grok_text(content):
    if isinstance(content, list):
        content = '\n'.join(x['text'] for x in content if isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str))
    return content.strip() if isinstance(content, str) else ''


def grok_message(record):
    """('user' | 'assistant', text): a person's question or Grok's answer. None for anything else,
    including the context Grok sends as user messages (<user_info>, system reminders): only a
    question has a prompt_index."""
    if record.get('type') == 'user' and 'prompt_index' in record:
        text = grok_text(record.get('content'))
        asked = re.fullmatch(r'<user_query>\s*(.*?)\s*</user_query>', text, re.S)
        text = asked[1] if asked else text
        return ('user', text) if text else None
    if record.get('type') == 'assistant':
        text = grok_text(record.get('content'))
        return ('assistant', text) if text else None
    return None


def grok_ending(record):
    """As ending_of, for a chat record of Grok's: an answer that calls tools, or their results, is mid-action."""
    if record.get('type') == 'tool_result': return 'mid-action'
    if record.get('type') == 'assistant' and record.get('tool_calls') not in (None, '', '[]', []): return 'mid-action'
    message = grok_message(record)
    if message: return 'finished' if message[0] == 'assistant' else 'unanswered'
    return None


def grok_folder(group):
    """A group's folder: its name, URL-encoded; a long one is a slug, with the folder in a .cwd file."""
    try: return (group / '.cwd').read_text().strip() or None
    except OSError: pass
    name = unquote(group.name)
    return name if name.startswith('/') else None


def grok_row(path):
    """A Grok session's row, with the same fields as a Claude Code session's, from its folder: the
    chat (`path`), summary.json (title, folder, branch, times), events.jsonl (its turns) and usage.json.
    None for a sub-agent's session: it lives in the same tree, but its turns aren't the primary ones."""
    folder = path.parent
    summary = small_json(folder / 'summary.json') or {}
    info = summary.get('info') if isinstance(summary.get('info'), dict) else {}
    cwd = info['cwd'] if isinstance(info.get('cwd'), str) else grok_folder(folder.parent)
    relationship = None; start_mode = None; models = set(); duration = None; began = None; outcome = None
    for event in records(folder / 'events.jsonl'):
        if event.get('type') == 'turn_started':
            if relationship is None: relationship = event.get('session_relationship')
            # Started with --yolo (always approve): resumed with it again, as Claude Code's bypass is.
            if start_mode is None: start_mode = 'yolo' if event.get('yolo_mode') is True else 'default'
            if isinstance(event.get('model_id'), str): models.add(event['model_id'][:100])
            began = epoch_ms(event.get('ts')); outcome = None
        elif event.get('type') == 'turn_ended':
            ended = epoch_ms(event.get('ts')); outcome = event.get('outcome')
            if began is not None and ended is not None and ended >= began: duration = (duration or 0) + ended - began
            began = None
    if relationship not in (None, 'primary'): return None
    prompt = None; ending = None
    for record in records(path):
        message = grok_message(record)
        if prompt is None and message and message[0] == 'user': prompt = one_line(message[1]) or None
        ending = grok_ending(record) or ending
    # A last turn that ended otherwise than completed: cancelled, or failed.
    if outcome not in (None, 'completed'): ending = 'interrupted'
    usage = small_json(folder / 'usage.json') or {}
    ticks = number((usage.get('session') or {}).get('costUsdTicks')) if isinstance(usage.get('session'), dict) else None
    title = one_line(summary.get('generated_title')) or one_line(summary.get('session_summary'))
    active = epoch_ms(summary.get('last_active_at')) or epoch_ms(summary.get('updated_at'))
    branch = summary.get('head_branch')
    return {'id': folder.name, 'program': 'grok', 'title': Path(cwd).name if cwd else 'Grok session',
            'description': title or prompt or 'No description available', 'summary': None, 'summarySource': None,
            'cwd': cwd, 'branch': branch[:200] if isinstance(branch, str) and branch else None, 'startMode': start_mode,
            'activity': active / 1000 if active else path.stat().st_mtime, 'exists': bool(cwd and Path(cwd).is_dir()),
            'ending': ending, 'cost': ticks / 1e10 if ticks else None,  # 10^10 ticks to the dollar
            'duration': duration, 'started': epoch_ms(summary.get('created_at')), 'linesAdded': None, 'linesRemoved': None,
            'models': sorted(models), 'prs': []}


def program_runs(pid, names):
    """Whether process `pid` still runs one of these programs (not a later process with its number)."""
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True, timeout=3)
        return result.returncode == 0 and Path(result.stdout.strip()).name.lower() in names
    except (OSError, subprocess.TimeoutExpired): return False


def grok_open_ids(registry, is_live=lambda pid: program_runs(pid, ('grok', 'agent'))):
    """Grok sessions open now: Grok lists them in active_sessions.json, each with its program's pid."""
    try: rows = json.loads(registry.read_bytes())
    except (OSError, ValueError, UnicodeError): return set()
    if not isinstance(rows, list): return set()
    return {r['session_id'] for r in rows if isinstance(r, dict) and isinstance(r.get('session_id'), str)
            and type(r.get('pid')) is int and r['pid'] > 0 and is_live(r['pid'])}


# ---- OpenCode: every session in one SQLite database, ~/.local/share/opencode/opencode.db ----

OPENCODE_ID = re.compile(r'\bses_[0-9A-Za-z]{10,60}\b')
UNTITLED = re.compile(r'New session - \d{4}-\d\d-\d\dT[\d:.]+Z')  # OpenCode's name until it names one


def opencode_connect(db):
    """Read-only: the list never changes OpenCode's sessions."""
    return sqlite3.connect(Path(db).absolute().as_uri() + '?mode=ro', uri=True, timeout=2)


def opencode_texts(con, message_id):
    """A message's own words: its text parts, without those OpenCode adds itself (synthetic)."""
    texts = []
    for (data,) in con.execute('select data from part where message_id = ? order by id', (message_id,)):
        try: part = json.loads(data)
        except (ValueError, TypeError): continue
        if isinstance(part, dict) and part.get('type') == 'text' and not part.get('synthetic') and isinstance(part.get('text'), str) and part['text'].strip():
            texts.append(part['text'].strip())
    return '\n'.join(texts)


def opencode_messages(con, sid):
    """(id, data) of a session's messages, oldest first."""
    for message_id, data in con.execute('select id, data from message where session_id = ? order by time_created, id', (sid,)):
        try: data = json.loads(data)
        except (ValueError, TypeError): continue
        if isinstance(data, dict): yield message_id, data


def opencode_ending(data):
    """As ending_of, for an OpenCode message."""
    if data.get('role') == 'user': return 'unanswered'
    if data.get('role') != 'assistant': return None
    error = data['error'].get('name') if isinstance(data.get('error'), dict) else None
    if error == 'MessageAbortedError': return 'interrupted'
    if error: return 'unanswered'  # the request failed: nothing answered it
    # An answer that asks for tools, or one that never finished, stopped mid-work.
    return 'finished' if data.get('finish') not in (None, 'tool-calls') else 'mid-action'


def opencode_rows(db):
    """Every OpenCode session's row, with the same fields as a Claude Code session's. Sub-agents'
    (parent_id) and archived sessions are left out; there's no transcript file."""
    try: con = opencode_connect(db)
    except sqlite3.Error: return
    try:
        sessions = con.execute('select id, directory, title, cost, summary_additions, summary_deletions, time_created, time_updated '
                               'from session where parent_id is null and time_archived is null').fetchall()
        for sid, cwd, title, cost, added, removed, created, updated in sessions:
            prompt = None; ending = None; duration = None; models = set()
            for message_id, data in opencode_messages(con, sid):
                ending = opencode_ending(data) or ending
                if data.get('role') == 'user' and prompt is None: prompt = one_line(opencode_texts(con, message_id)) or None
                if data.get('role') != 'assistant': continue
                if isinstance(data.get('modelID'), str): models.add(data['modelID'][:100])
                when = data.get('time') if isinstance(data.get('time'), dict) else {}
                if number(when.get('created')) is not None and number(when.get('completed')) is not None and when['completed'] >= when['created']:
                    duration = (duration or 0) + when['completed'] - when['created']
            title = one_line(title) if isinstance(title, str) and not UNTITLED.fullmatch(title) else None
            cwd = cwd if isinstance(cwd, str) and cwd else None
            changed = (number(added) or 0) + (number(removed) or 0) > 0
            yield {'id': sid, 'program': 'opencode', 'title': Path(cwd).name if cwd else 'OpenCode session',
                   'description': title or prompt or 'No description available', 'summary': None, 'summarySource': None,
                   'cwd': cwd, 'branch': None, 'startMode': None, 'activity': updated / 1000 if number(updated) else 0,
                   'exists': bool(cwd and Path(cwd).is_dir()), 'ending': ending,
                   'cost': cost if number(cost) else None,  # 0: a free model, not a cost
                   'duration': duration, 'started': number(created), 'linesAdded': added if changed else None,
                   'linesRemoved': removed if changed else None, 'models': sorted(models), 'prs': [], 'transcript': None}
    except sqlite3.Error: return
    finally: con.close()


def opencode_conversation(db, sid):
    """[(role, text)]: what was said in an OpenCode session, for its summary."""
    con = opencode_connect(db)
    try:
        said = ((data.get('role'), opencode_texts(con, message_id)) for message_id, data in opencode_messages(con, sid))
        return [(role, text) for role, text in said if role in ('user', 'assistant') and text]
    finally: con.close()


def opencode_updated(db, sid):
    """When an OpenCode session last changed (epoch ms); None when there's no such session."""
    try:
        con = opencode_connect(db)
        try: found = con.execute('select time_updated from session where id = ?', (sid,)).fetchone()
        finally: con.close()
    except sqlite3.Error: return None
    return found[0] if found else None


def elapsed_ms(etime):
    """ps's elapsed time, [[days-]hours:]minutes:seconds, in milliseconds."""
    m = re.fullmatch(r'(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)', etime)
    if not m: return None
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return (((days * 24 + hours) * 60 + minutes) * 60 + seconds) * 1000


def processes(name, run=subprocess.run, now=None):
    """Running programs called `name` (opencode, pi): (started, epoch ms; folder; arguments)."""
    now = time.time() * 1000 if now is None else now
    try: ps = run(['ps', '-axo', 'pid=,etime=,args='], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired): return []
    found = {}
    for line in (ps.stdout or '').splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or Path(parts[2].split()[0]).name != name: continue
        elapsed = elapsed_ms(parts[1])
        if elapsed is not None: found[parts[0]] = (now - elapsed, parts[2])
    if not found: return []
    try: lsof = run(['lsof', '-a', '-d', 'cwd', '-Fpn', '-p', ','.join(found)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired): return []
    folders = {}; pid = None
    for line in (lsof.stdout or '').splitlines():
        if line.startswith('p'): pid = line[1:]
        elif line.startswith('n') and pid in found: folders[pid] = line[1:]
    return [(found[pid][0], folders[pid], found[pid][1]) for pid in found if pid in folders]


def opencode_open_ids(db, running):
    """OpenCode sessions open now. OpenCode keeps no list of them, so from its running programs: one
    resumed with --session names its session; a new one is the session in its folder that changed
    since it started (OpenCode can switch sessions inside, so each of those)."""
    ids = set()
    if not running: return ids
    for _, _, args in running: ids.update(OPENCODE_ID.findall(args))
    try:
        con = opencode_connect(db)
        try: rows = con.execute('select id, directory, time_updated from session where parent_id is null').fetchall()
        finally: con.close()
    except sqlite3.Error: return ids
    for started, folder, args in running:
        if OPENCODE_ID.search(args): continue
        ids.update(sid for sid, directory, updated in rows if directory == folder and number(updated) is not None and updated >= started)
    return ids


# ---- Pi: ~/.pi/agent/sessions/--<folder, / as ->--/<time>_<id>.jsonl, a tree of entries ----

PI_FILE = re.compile(r'\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-\d{3}Z_(?P<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\.jsonl')


def pi_message(record):
    """('user' | 'assistant', text): a person's or Pi's own words. None for anything else: the system
    prompt Pi saves, tool calls and their results, thinking, what extensions add."""
    message = record.get('message') if record.get('type') == 'message' else None
    if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'): return None
    text = grok_text(message.get('content'))  # a string, or text parts among images, thinking and tool calls
    return (message['role'], text) if text else None


def pi_ending(record):
    """As ending_of, for an entry of Pi's: an answer ends with its stopReason; a tool's result is mid-action."""
    message = record.get('message') if record.get('type') == 'message' else None
    role = message.get('role') if isinstance(message, dict) else None
    if role == 'user': return 'unanswered'
    if role == 'toolResult': return 'mid-action'
    if role != 'assistant': return None
    # A failed request answered nothing; toolUse, length (cut off) and deferred stopped mid-work.
    return {'stop': 'finished', 'aborted': 'interrupted', 'error': 'unanswered'}.get(message.get('stopReason'), 'mid-action')


def pi_cost(usage):
    """What a usage record cost in dollars, as Pi priced it; None without one."""
    cost = usage.get('cost') if isinstance(usage, dict) else None
    return number(cost.get('total')) if isinstance(cost, dict) else None


def pi_row(path):
    """A Pi session's row, with the same fields as a Claude Code session's. Pi only appends to the
    file, so in file order the last entries are where it was left."""
    sid = PI_FILE.fullmatch(path.name)['id']
    cwd = None; started = None; name = None; prompt = None; ending = None; cost = None; duration = None; began = None; models = set()
    for record in records(path):
        kind = record.get('type')
        if kind == 'session':
            if isinstance(record.get('cwd'), str): cwd = record['cwd']
            started = epoch_ms(record.get('timestamp'))
        elif kind == 'session_info': name = one_line(record.get('name')) or None  # its last name; none when taken away
        message = record.get('message') if kind == 'message' and isinstance(record.get('message'), dict) else {}
        # Every usage counts, as Pi's own total: answers, cache warming, summaries made on compacting.
        spent = pi_cost(message.get('usage') if message else record.get('usage'))
        if spent is not None: cost = (cost or 0) + spent
        if message.get('role') == 'assistant' and isinstance(message.get('model'), str): models.add(message['model'][:100])
        said = pi_message(record)
        if prompt is None and said and said[0] == 'user': prompt = one_line(said[1]) or None
        end = pi_ending(record); ending = end or ending
        # A turn: from your message to the answer that ends it (the entry's time: when Pi wrote it).
        when = epoch_ms(record.get('timestamp'))
        if end == 'unanswered' and message.get('role') == 'user':
            if began is None: began = when
        elif end in ('finished', 'interrupted', 'unanswered') and began is not None and when is not None and when >= began:
            duration = (duration or 0) + when - began; began = None
    return {'id': sid, 'program': 'pi', 'title': Path(cwd).name if cwd else 'Pi session',
            'description': name or prompt or 'No description available', 'summary': None, 'summarySource': None,
            'cwd': cwd, 'branch': None, 'startMode': None, 'activity': path.stat().st_mtime, 'exists': bool(cwd and Path(cwd).is_dir()),
            'ending': ending, 'cost': cost, 'duration': duration, 'started': started, 'linesAdded': None,
            'linesRemoved': None, 'models': sorted(models), 'prs': []}


def pi_files(root):
    """Pi's session files: in a folder per working folder, or loose in a session folder set for it."""
    if not root or not root.is_dir(): return
    for path in (*root.glob('*.jsonl'), *root.glob('*/*.jsonl')):
        found = PI_FILE.fullmatch(path.name)
        if found: yield path, found['id']


def pi_open_ids(root, running):
    """Pi sessions open now. Pi holds no file open and keeps no list, and a running pi shows no
    arguments in ps (it sets its own process title), so from its running programs: the sessions
    made in a pi's folder whose file changed since it started. A resumed one counts once it writes."""
    ids = set()
    for started, folder, _ in running:
        # Pi's folder for a working folder's sessions, as it names it; loose files say theirs inside.
        group = root / ('--' + re.sub(r'[/\\:]', '-', re.sub(r'^[/\\]', '', folder)) + '--')
        for path in (*group.glob('*.jsonl'), *root.glob('*.jsonl')):
            found = PI_FILE.fullmatch(path.name)
            try:
                if not found or path.stat().st_mtime * 1000 < started: continue
                if path.parent == root:
                    with path.open('rb') as stream: header = json.loads(stream.readline())
                    if not isinstance(header, dict) or header.get('cwd') != folder: continue
            except (OSError, ValueError, UnicodeError): continue
            ids.add(found['id'])
    return ids


def claude_is_live(pid):
    return program_runs(pid, ('claude', 'claude-code'))


def active_session_ids(registry, is_live=claude_is_live):
    active = set()
    for path in registry.glob('*.json'):
        if not path.stem.isdigit(): continue
        try:
            record = json.loads(path.read_text())
            if not isinstance(record, dict): continue
            sid = record.get('sessionId'); pid = record.get('pid', int(path.stem))
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and pid == int(path.stem) and isinstance(sid, str) and UUID.fullmatch(sid) and is_live(pid):
                active.add(sid)
        except (ValueError, OSError): continue
    return active


def context_snapshot(root, sid):
    """Last recorded statusline measurement, not a live or estimated context size."""
    if not isinstance(sid, str) or not UUID.fullmatch(sid): return None
    try:
        path = root / (sid + '.json')
        if path.stat().st_size > LIMIT: return None
        saved = json.loads(path.read_text())
        if not isinstance(saved, dict) or saved.get('session') != sid: return None
        context = saved.get('context')
        if not isinstance(context, dict): return None
        pct = context.get('percentage'); at = saved.get('at')
        if type(pct) not in (int, float) or not math.isfinite(pct) or not 0 <= pct <= 100: return None
        if type(at) not in (int, float) or not math.isfinite(at) or at <= 0: return None
        def tokens(key):
            value = context.get(key)
            return value if type(value) is int and 0 <= value <= 9007199254740991 else None
        return {'percentage': pct, 'tokens': tokens('tokens'), 'size': tokens('size'), 'at': at,
                'model': saved.get('model')[:160] if isinstance(saved.get('model'), str) else None}
    except (OSError, ValueError): return None


CACHE_VERSION = 6  # raise when a row gets a new field, so every session is read again


def load_cache(cache_file):
    try:
        saved = json.loads(cache_file.read_text())
        if saved.get('version') == CACHE_VERSION and isinstance(saved.get('sessions'), dict): return saved['sessions']
    except (OSError, ValueError, AttributeError): pass
    return {}


def save_cache(cache_file, kept):
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix('.' + str(os.getpid()) + '.tmp')
        tmp.write_text(json.dumps({'version': CACHE_VERSION, 'sessions': kept}, ensure_ascii=False))
        tmp.replace(cache_file)
    except OSError: pass


def transcripts(root, codex_root=None, grok_root=None, pi_root=None):
    """(file, session id, how to read it) for every session with a file: Claude Code's, Codex's, Grok's, Pi's."""
    if root and root.is_dir():
        for project in root.iterdir():
            if not project.is_dir(): continue
            for path in project.glob('*.jsonl'):
                if UUID.fullmatch(path.stem): yield path, path.stem, session_row
    if codex_root and codex_root.is_dir():
        for path in codex_root.rglob('rollout-*.jsonl'):
            found = CODEX_FILE.fullmatch(path.name)
            if found: yield path, found['id'], codex_row
    if grok_root and grok_root.is_dir():
        for path in grok_root.glob('*/*/chat_history.jsonl'):
            if UUID.fullmatch(path.parent.name): yield path, path.parent.name, grok_row
    for path, sid in pi_files(pi_root): yield path, sid, pi_row


def stamp(path):
    """What a cached row is checked against: its file's size and time, and for Grok's chat those of
    the files beside it that the row reads too (a title can come after the last message)."""
    stat = path.stat()  # gone: no row
    beside = [path.with_name(name) for name in ('summary.json', 'events.jsonl', 'usage.json')] if path.name == 'chat_history.jsonl' else []
    result = [[stat.st_size, stat.st_mtime_ns]]
    for other in beside:
        try: other = other.stat(); result.append([other.st_size, other.st_mtime_ns])
        except OSError: result.append(None)
    return result


def sessions(root, active=(), cache_file=None, codex_root=None, codex_names=None, grok_root=None, opencode_db=None, pi_root=None):
    """Every closed session's row. A closed transcript doesn't change, so with a cache file only
    new or changed ones are read again; the rest (gigabytes of them) cost a stat each. OpenCode's
    are queried from its database each time."""
    saved = load_cache(cache_file) if cache_file else {}
    names = codex_thread_names(codex_names) if codex_names else {}
    kept = {}
    for path, sid, read in transcripts(root, codex_root, grok_root, pi_root):
        if sid in active: continue
        try:
            now = stamp(path); key = str(path); hit = saved.get(key)
            # A row of None is cached too: a Grok sub-agent's session, left out.
            row = hit['row'] if isinstance(hit, dict) and hit.get('stamp') == now and 'row' in hit else read(path)
        except OSError: continue
        kept[key] = {'stamp': now, 'row': row}
        if not isinstance(row, dict): continue
        # The transcript: the file the row was read from.
        row = {**row, 'exists': bool(row.get('cwd') and Path(row['cwd']).is_dir()), 'transcript': key}
        if names.get(sid): row['description'] = names[sid]
        yield row
    if cache_file: save_cache(cache_file, kept)
    if opencode_db:
        for row in opencode_rows(opencode_db):
            if row['id'] not in active: yield row


def locations(home=None, env=os.environ):
    """Where each harness keeps its sessions, as each moves them with its own variable."""
    home = Path(home or Path.home())
    claude = Path(env.get('CLAUDE_CONFIG_DIR') or home / '.claude')
    codex = Path(env.get('CODEX_HOME') or home / '.codex')
    grok = Path(env.get('GROK_HOME') or home / '.grok')
    data = Path(env.get('XDG_DATA_HOME') or home / '.local/share')
    pi = Path(env.get('PI_CODING_AGENT_SESSION_DIR') or Path(env.get('PI_CODING_AGENT_DIR') or home / '.pi/agent') / 'sessions')
    return SimpleNamespace(claude=claude / 'projects', registry=claude / 'sessions', codex=codex / 'sessions',
                           codex_names=codex / 'session_index.jsonl', grok=grok / 'sessions',
                           grok_open=grok / 'active_sessions.json', opencode=data / 'opencode/opencode.db', pi=pi)


def session_id(sid):
    """Whether `sid` can be a session's id: a UUID (Claude Code, Codex, Grok, Pi) or OpenCode's ses_…."""
    return isinstance(sid, str) and bool(UUID.fullmatch(sid) or OPENCODE_ID.fullmatch(sid))


def open_ids(at):
    """Every harness's sessions that are open now, by id."""
    return (active_session_ids(at.registry) | codex_open_ids() | grok_open_ids(at.grok_open)
            | opencode_open_ids(at.opencode, processes('opencode')) | pi_open_ids(at.pi, processes('pi')))


def known(sid, at):
    """Whether a session with this id is kept anywhere."""
    if OPENCODE_ID.fullmatch(sid): return opencode_updated(at.opencode, sid) is not None
    return any(found == sid for _, found, _ in transcripts(at.claude, at.codex, at.grok, at.pi))


if __name__ == '__main__':
    at = locations()
    if len(sys.argv) == 2 and sys.argv[1] == '--active':
        print(json.dumps(sorted(open_ids(at))))
    elif len(sys.argv) == 4 and sys.argv[1] == '--context':
        print(json.dumps(context_snapshot(Path(sys.argv[2]), sys.argv[3])))
    elif len(sys.argv) == 4 and sys.argv[1] == '--validate':
        sid = sys.argv[3]
        if not session_id(sid): sys.exit('Invalid session ID')
        if sid in open_ids(at): sys.exit('This session is already open.')
        if not Path(sys.argv[2]).is_dir(): sys.exit('Project folder missing: ' + sys.argv[2])
        if not known(sid, at): sys.exit('Session file missing')
    else:
        # reader.py [cache file]: every harness's closed sessions, one JSON row a line.
        cache = Path(sys.argv[1]) if len(sys.argv) > 1 else None
        for row in sessions(at.claude, open_ids(at), cache, at.codex, at.codex_names, at.grok, at.opencode, at.pi):
            print(json.dumps(row, ensure_ascii=False), flush=True)
