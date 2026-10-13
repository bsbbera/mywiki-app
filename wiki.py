#!/usr/bin/env python3
"""MyWiki chat-to-knowledge pipeline. Standard library only (plus the optional packages in .deps).

The app lives in its own folder. Your settings and private state live in ~/.mywiki (MYWIKI_HOME to move it),
and your notes in the vault you chose at setup. Nothing here is tied to one machine or one vault.

  python wiki.py run                 harvest + extract + integrate + connect + migrate batch + catalog + commit (scheduler)
  python wiki.py harvest             stage new chat text from every source (no LLM)
  python wiki.py extract [N]         staged chats -> knowledge cards in raw/inbox (runner, cheap model)
  python wiki.py integrate [N]       cards -> notes (runner, writer model)
  python wiki.py connect [N]         N new cross-field connection notes in wiki/connections (runner)
  python wiki.py feed [force]             today's top stories by segment (Brave headlines, no model)
  python wiki.py capture <url>...         make notes from stories picked in today's feed
  python wiki.py social research|note <url>...  make notes from social posts (YouTube, Instagram, TikTok, X)
  python wiki.py consolidate [plan|apply]  fold or delete chat notes that break the topic rules (see state/consolidate.json)
  python wiki.py migrate step0       add field/subfield/type to existing notes from their folder (no LLM)
  python wiki.py migrate batch [N]   next N migration batches of 50 notes (runner)
  python wiki.py catalog             rebuild .meta/catalog.tsv and the auto section of wiki/index.md
  python wiki.py status              print status and rewrite wiki/_dashboards/Pipeline.md
  python wiki.py dashboard           rewrite wiki/_dashboards/Home.md and the field maps in _dashboards/Maps (no LLM)
  python wiki.py news "<topic>" [days] [quick|default|deep]   last30days research -> brief, notes, News/<topic> page
  python wiki.py import-export <zip> move a claude.ai export zip into raw/exports/claude
  python wiki.py next                in-chat mode: print the next unit to process and where its output goes
  python wiki.py done <path>         in-chat mode: mark that unit finished
"""
import atexit, csv, datetime as dt, glob, hashlib, itertools, json, os, re, shutil, sqlite3, subprocess, sys, threading, time, zipfile

APP = PIPE = os.path.dirname(os.path.abspath(__file__))  # the app: code, prompts, page, defaults
# pip --target .deps: packages in AppData are invisible to the scheduled task when installed from a sandboxed shell
sys.path.insert(0, os.path.join(APP, '.deps'))
HOME = os.path.abspath(os.path.expanduser(os.environ.get('MYWIKI_HOME') or '~/.mywiki'))  # your settings, keys, state
USER_CFG = os.path.join(HOME, 'config.json')
STATE = os.path.join(HOME, 'state')           # private: scrubbed chat text, keys, logins; never in the vault or git
if os.name != 'nt':  # keys, cookies and chat text: this account only
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    os.chmod(HOME, 0o700)


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_cfg():
    d = json.load(open(os.path.join(APP, 'defaults.json'), encoding='utf8'))
    try:
        u = json.load(open(USER_CFG, encoding='utf8'))
    except (OSError, ValueError):
        u = {}
    return _merge(d, u)


CFG = load_cfg()
ROOT = os.path.abspath(os.path.expanduser(CFG['vault'])) if CFG.get('vault') else os.path.join(HOME, 'no-vault-yet')  # the vault
STAGED = os.path.join(STATE, 'staged')
FAILED = os.path.join(STATE, 'failed')
INBOX = os.path.join(ROOT, 'raw', 'inbox')     # committed: cards travel between devices via git
DONE = os.path.join(INBOX, 'done')
WIKI = os.path.join(ROOT, 'wiki')
META = os.path.join(ROOT, '.meta')
LEDGER = os.path.join(STATE, 'ledger.json')
MIGRATION = os.path.join(META, 'migration.csv')
H = os.path.expanduser('~')
MARKER = '[mywiki-pipeline-run]'  # every runner prompt starts with this; harvest skips such sessions. Not the skill's name, or agents load the skill
NOW = time.time()
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0  # no console flashes from the nightly task

if sys.stdout:  # None under pythonw (the scheduler); a cp1252 console must not crash on chat text
    sys.stdout.reconfigure(errors='replace')


def log(*a):
    line = dt.datetime.now().strftime('%H:%M:%S ') + ' '.join(str(x) for x in a)
    print(line, flush=True)
    os.makedirs(os.path.join(STATE, 'logs'), exist_ok=True)
    with open(os.path.join(STATE, 'logs', dt.date.today().strftime('%Y-%m') + '.log'), 'a', encoding='utf8', newline='\n') as f:
        f.write(dt.date.today().isoformat() + ' ' + line + '\n')


def save_cfg(patch):
    """Merge patch into ~/.mywiki/config.json (only your choices; defaults stay in the app) and into CFG."""
    global CFG
    try:
        u = json.load(open(USER_CFG, encoding='utf8'))
    except (OSError, ValueError):
        u = {}
    for k, v in patch.items():
        if v is None:
            u.pop(k, None)
        else:
            u[k] = v
    save_json(USER_CFG, u)
    CFG = load_cfg()
    return CFG


def load_json(p, default):
    try:
        with open(p, encoding='utf8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f'{p}.{os.getpid()}.{threading.get_ident()}.tmp'  # own temp per thread: parallel workers save the same file
    with open(tmp, 'w', encoding='utf8', newline='\n') as f:
        json.dump(obj, f, indent=1)
    # Windows refuses the replace while another process (the app polling progress.json) has the file open: retry briefly
    for i in range(20):
        try:
            return os.replace(tmp, p)
        except PermissionError:
            if i == 19:
                raise
            time.sleep(.15)


PROGRESS = os.path.join(STATE, 'progress.json')


def progress(stage, state='running', done=0, total=0, unit='', **extra):
    """What the app shows. A run: harvest, extract, integrate, connect, migrate, save. A news run: research, brief, integrate, connect, save."""
    p = load_json(PROGRESS, {})
    if stage in ('harvest', 'research', 'consolidate') and state == 'running' and not done and p.get('current') != stage:
        p = {'started': time.time(), 'stages': {}, 'kind': {'research': 'news', 'consolidate': 'consolidate'}.get(stage, 'run')}
    p.setdefault('stages', {}).setdefault(stage, {}).update(state=state, done=done, total=total, unit=unit, **extra)
    p['current'], p['updated'] = stage, time.time()
    save_json(PROGRESS, p)


def step(stage, unit):
    """New words under a running stage, counts kept."""
    progress(stage, **{**load_json(PROGRESS, {}).get('stages', {}).get(stage, {}), 'unit': unit})


def finish(stage, state='done'):
    s = load_json(PROGRESS, {}).get('stages', {}).get(stage, {})
    progress(stage, state, s.get('total', 0), s.get('total', 0))


def slug(s, n=60):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:n] or 'untitled'


# ---------------------------------------------------------------- cleaning + privacy

SECRET_RES = [
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S), '[private-key]'),
    (re.compile(r'\b(sk-ant-[\w-]{10,}|sk-[\w-]{16,}|gh[pousr]_\w{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[\w-]{10,}|AIza[\w-]{30,}|hf_\w{20,})'), '[secret]'),
    (re.compile(r'\beyJ[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}'), '[jwt]'),
    (re.compile(r'(?i)\bbearer\s+[\w.~+/-]{20,}=*'), 'Bearer [secret]'),
    (re.compile(r'(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)(\s*[:=]\s*)["\']?[^\s"\',;]{4,}'), r'\1\2[redacted]'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '[email]'),
    (re.compile(r'\+\d{1,3}[ -]?\d{5}[ -]?\d{4,7}\b|\b[6-9]\d{9}\b'), '[phone]'),
]


def scrub(text):
    for rx, rep in SECRET_RES:
        text = rx.sub(rep, text)
    user = os.path.basename(H)
    text = re.sub(r'(?i)(?:[a-z]:[\\/]+users[\\/]+|/[a-z]/users/|/home/)' + re.escape(user), '~', text)
    text = re.sub(r'(?i)[a-z](?::|%3a)(?:%5c|%2f|[\\/])+users(?:%5c|%2f|[\\/])+' + re.escape(user), '~', text)  # URL-encoded
    for term in sorted(CFG.get('personal_terms', []), key=len, reverse=True):
        text = re.sub(r'(?i)\b' + re.escape(term) + r'\b', '[me]', text)
    if len(user) < 4 or user.lower() in GENERIC_USERS:  # "pi", "user", "ubuntu" are words, not names
        return text
    return re.sub(r'(?i)(?<![a-z0-9])' + re.escape(user) + r'(?![a-z0-9])', '[me]', text)  # the account name is often a real name


GENERIC_USERS = {'user', 'users', 'admin', 'administrator', 'root', 'ubuntu', 'debian', 'fedora', 'owner', 'home', 'guest',
                 'default', 'public', 'runner', 'vagrant', 'developer', 'student', 'office', 'windows', 'linux'}


def clean(text, cap=8000):
    text = re.sub(r'<(system-reminder|thinking|command-\w+|local-command-\w+|system_info|rules)[^>]*>.*?</\1>', '', text, flags=re.S)
    text = re.sub(r'<([A-Z][A-Z_]{3,})>.*?</\1>', '', text, flags=re.S)  # tool-injected blocks (Antigravity metadata)
    text = re.sub(r'```.*?```', '[code omitted]', text, flags=re.S)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if cap and len(text) > cap:  # long chat replies are mostly noise; documents keep everything
        text = text[:6500] + '\n[...]\n' + text[-1000:]
    return text


def is_human(msgs):
    """A person typed this: skip tool-driven runs (Quire stages, our own runner calls)."""
    users = [t for r, t in msgs if r == 'user']
    if not users:
        return False
    if any(MARKER in t or '[wiki-ingest]' in t for t in users[:2]):  # old marker: runs from 2026-09-23
        return False
    if any(users[0].lstrip().startswith(m) for m in CFG.get('skip_markers', [])):
        return False
    if len(users) == 1 and len(users[0]) > 1500:  # one long prompt and no follow-up = a machine prompt
        return False
    return True


def blocks_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
    return ''


# ---------------------------------------------------------------- sources
# Each yields dicts: source, id, title, where, updated (epoch s), msgs [(role, text)]

def src_claude_code():
    for f in glob.glob(os.path.join(H, '.claude', 'projects', '*', '*.jsonl')):
        msgs, entry, cwd, title = [], None, '', ''
        with open(f, encoding='utf8', errors='replace') as h:
            for line in h:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                entry = entry or d.get('entrypoint')
                cwd = cwd or d.get('cwd', '')
                title = d.get('customTitle') or d.get('aiTitle') or title
                if d.get('type') not in ('user', 'assistant') or d.get('isMeta') or d.get('isSidechain') or d.get('isCompactSummary'):
                    continue
                c = (d.get('message') or {}).get('content')
                if isinstance(c, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c):
                    continue
                t = blocks_text(c).strip()
                if t:
                    msgs.append((d['type'], t))
        if entry and str(entry).startswith('sdk'):
            msgs = []  # headless runs (Quire, this pipeline): never a person
        yield dict(source='claude-code', id=os.path.basename(f)[:-6], title=title, where=cwd,
                   updated=os.path.getmtime(f), msgs=msgs)


DEVIN_DBS = [os.path.join(H, 'AppData', 'Roaming', 'devin', 'cli', 'sessions.db'), os.path.join(H, '.config', 'devin', 'cli', 'sessions.db'),
             os.path.join(H, '.local', 'share', 'devin', 'cli', 'sessions.db'), os.path.join(H, 'Library', 'Application Support', 'devin', 'cli', 'sessions.db')]


def src_devin():
    p = next((x for x in DEVIN_DBS if os.path.exists(x)), DEVIN_DBS[0])
    if not os.path.exists(p):
        return
    db = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
    for sid, cwd, title, last in db.execute('select id, working_directory, title, last_activity_at from sessions where hidden = 0'):
        msgs, seen = [], set()
        for (m,) in db.execute('select chat_message from message_nodes where session_id = ? order by node_id', (sid,)):
            try:
                d = json.loads(m)
            except ValueError:
                continue
            if d.get('message_id') in seen or d.get('role') not in ('user', 'assistant'):
                continue
            seen.add(d.get('message_id'))
            if d['role'] == 'user' and not (d.get('metadata') or {}).get('is_user_input'):
                continue
            t = blocks_text(d.get('content')).strip()
            if t:
                msgs.append((d['role'], t))
        yield dict(source='devin', id=sid, title=title or '', where=cwd, updated=last, msgs=msgs)


def src_antigravity():
    for base in ('antigravity', 'antigravity-cli'):
        for f in glob.glob(os.path.join(H, '.gemini', base, 'brain', '*', '.system_generated', 'logs', 'transcript.jsonl')):
            msgs = []
            with open(f, encoding='utf8', errors='replace') as h:
                for line in h:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    t = (d.get('content') or '').strip() if isinstance(d.get('content'), str) else ''
                    if d.get('type') == 'USER_INPUT' and t:
                        msgs.append(('user', re.sub(r'</?USER_REQUEST>', '', t).strip()))
                    elif d.get('type') == 'PLANNER_RESPONSE' and t:
                        msgs.append(('assistant', t))
            cid = f.replace('\\', '/').split('/brain/')[1].split('/')[0]
            yield dict(source='antigravity', id=cid, title='', where='', updated=os.path.getmtime(f), msgs=msgs)


def src_quire():
    for ws in CFG.get('quire_workspaces', []):
        for f in glob.glob(os.path.join(os.path.expanduser(ws), '.quire', 'sessions', '*.jsonl')):
            msgs, kind, title = [], None, ''
            with open(f, encoding='utf8', errors='replace') as h:
                for line in h:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    kind = kind or d.get('sessionKind')
                    title = d.get('title') or title
                    if d.get('type') == 'message' and d.get('role') in ('user', 'assistant'):
                        t = blocks_text((d.get('message') or {}).get('content')).strip()
                        if t:
                            msgs.append((d['role'], t))
            if kind not in CFG.get('quire_kinds', ['chat']):
                msgs = []
            yield dict(source='quire', id=os.path.basename(f)[:-6], title=title or '', where=ws,
                       updated=os.path.getmtime(f), msgs=msgs)


def src_quire_research():
    """Web pages Quire fetched while researching (research/*.md). Same file in two workspaces = one document."""
    seen = set()
    for ws in CFG.get('quire_workspaces', []):
        for f in sorted(glob.glob(os.path.join(os.path.expanduser(ws), 'research', '*.md'))):
            name = os.path.basename(f)[:-3]
            if name in seen:
                continue
            seen.add(name)
            text = open(f, encoding='utf8', errors='replace').read()
            title = (re.search(r'^# (.+)$', text, re.M) or [None, name])[1]
            # ponytail: one document = one "message"; a research file is written once, so it is staged once
            yield dict(source='quire-research', id=name, title=title, where=ws, updated=os.path.getmtime(f),
                       msgs=[('user', f'Research document collected by Quire: {title}'), ('assistant', text)])


def src_claude_ai():
    """claude.ai export zips (web, desktop Chat tab and mobile share one account). Drop them in raw/exports/claude/."""
    for z in sorted(glob.glob(os.path.join(ROOT, 'raw', 'exports', 'claude', '*.zip'))):
        with zipfile.ZipFile(z) as zf:
            name = next((n for n in zf.namelist() if n.endswith('conversations.json')), None)
            if not name:
                continue
            for c in json.loads(zf.read(name)):
                msgs = []
                for m in c.get('chat_messages', []):
                    t = (m.get('text') or blocks_text(m.get('content'))).strip()
                    if t:
                        msgs.append(('user' if m.get('sender') == 'human' else 'assistant', t))
                upd = c.get('updated_at') or ''
                try:
                    upd = dt.datetime.fromisoformat(upd.replace('Z', '+00:00')).timestamp()
                except ValueError:
                    upd = os.path.getmtime(z)
                yield dict(source='claude-ai', id=c.get('uuid', ''), title=c.get('name', ''), where='claude.ai',
                           updated=upd, msgs=msgs)


SOURCES = {'claude-code': src_claude_code, 'devin': src_devin, 'antigravity': src_antigravity,
           'quire': src_quire, 'quire-research': src_quire_research, 'claude-ai': src_claude_ai}


# ---------------------------------------------------------------- harvest

def harvest():
    led = load_json(LEDGER, {'sessions': {}, 'fails': {}})
    os.makedirs(STAGED, exist_ok=True)
    idle = CFG.get('idle_hours', 2) * 3600
    staged = skipped = 0
    progress('harvest')
    for name, fn in SOURCES.items():
        if not CFG['sources'].get(name, True):
            continue
        try:
            sessions = list(fn())
        except Exception as e:  # one broken source must not stop the others
            log('harvest', name, 'FAILED', repr(e))
            continue
        for s in sessions:
            key = f"{s['source']}:{s['id']}"
            rec = led['sessions'].setdefault(key, {'n': 0})
            if s['title'] and not rec.get('title'):  # backfills chats seen before titles were kept
                rec['title'] = scrub(s['title'])[:80]
            msgs = s['msgs']
            if len(msgs) <= rec['n'] or NOW - s['updated'] < idle:
                continue  # nothing new, or still being chatted in
            if not is_human(msgs):
                rec.update(n=len(msgs), human=False)
                skipped += 1
                continue
            new = msgs[rec['n']:]
            body = '\n\n'.join(f"**{'Me' if r == 'user' else 'AI'}:** {clean(t, None if s['source'] == 'quire-research' else 8000)}" for r, t in new)
            body = scrub(body)
            if len(body) < CFG.get('min_chars', 400):
                rec['n'] = len(msgs)
                continue
            when = dt.datetime.fromtimestamp(s['updated']).strftime('%Y-%m-%d')
            head = (f"source: {s['source']}\nsession: {s['id']}\ntitle: {scrub(s['title'])}\n"
                    f"project: {scrub(s['where'])}\ndate: {when}\n"
                    f"part: messages {rec['n'] + 1}-{len(msgs)} of this session\n\n")
            # the rules and taxonomy grow over time: size pieces to what still fits on one command line, so none need splitting
            chunk = min(CFG.get('chunk_chars', 40000), CMD_MAX - len(subprocess.list2cmdline([extract_prompt('', rules_text())])) - len(head) - 1500)
            parts = [body[i:i + chunk] for i in range(0, len(body), chunk)]
            for i, part in enumerate(parts):
                fn_ = f"{when}__{s['source']}__{slug(s['id'], 40)}__{rec['n']}-{i}.md"
                open(os.path.join(STAGED, fn_), 'w', encoding='utf8', newline='\n').write(head + part)
                staged += 1
            rec.update(n=len(msgs), human=True, last=when, title=scrub(s['title'])[:80])  # the app names chats by title
        save_json(LEDGER, led)  # save per source so a crash keeps earlier work
    log(f'harvest: {staged} new units staged, {skipped} tool-run sessions skipped')
    progress('harvest', 'done', staged, staged)


# ---------------------------------------------------------------- runner

def pick(stage):
    """The model a stage uses: its own choice, else the default. {'via': cli id or api id, 'model': id}."""
    m = CFG.get('models') or {}
    return m.get(stage) or m.get('default') or {}


def run_agent(stage, prompt, timeout, choice=None):
    c = pick(stage) if choice is None else choice
    via, model = c.get('via'), c.get('model')
    if not via or not model or (via not in API and via not in CFG['runners']):
        return False, 'No model chosen yet. Open MyWiki > Settings > Models.'
    if via in API:
        return api_agent(via, model, prompt, timeout)
    r = CFG['runners'][via]
    exe = shutil.which(r['cmd'][0]) or r['cmd'][0]
    args = [exe] + [a.replace('{model}', model).replace('{home}', HOME).replace('{prompt}', prompt) for a in r['cmd'][1:]]
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf8', errors='replace',
                           timeout=timeout, creationflags=NOWIN, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, 'timeout'
    except OSError as e:  # not installed, or not on this PATH (cron runs with a short one)
        return False, f'could not start {r["cmd"][0]}: {e.strerror or e}'
    return p.returncode == 0, (p.stdout or '') + ('\n' + p.stderr if p.returncode else '')


def guide(root=None):
    """The vault's house-style file: WIKI.md, or devin.md in vaults that started with it."""
    root = root or ROOT
    return 'devin.md' if os.path.exists(os.path.join(root, 'devin.md')) and not os.path.exists(os.path.join(root, 'WIKI.md')) else 'WIKI.md'


def prompt(name, **kw):
    t = open(os.path.join(PIPE, 'prompts', name + '.md'), encoding='utf8').read()
    for k, v in {'guide': guide(), **kw}.items():
        t = t.replace('{' + k + '}', v)
    vault = ROOT.replace('\\', '/')
    return (f'{MARKER} The vault is {vault}. Paths below are relative to it; file tools need absolute paths, '
            f'so use {vault}/<path>.\n\n' + t)


def fail(led, unit, why):
    p = load_json(PROGRESS, {})
    st = p.get('stages', {}).get(p.get('current'), {})
    st['failed'] = st.get('failed', 0) + 1
    save_json(PROGRESS, p)
    n = led['fails'].get(unit, 0) + 1
    led['fails'][unit] = n
    log('  failed', f'({n}/3)', unit, why[:300].replace('\n', ' '))
    if n >= 3:
        os.makedirs(FAILED, exist_ok=True)
        for d in (STAGED, INBOX):
            if os.path.exists(os.path.join(d, unit)):
                shutil.move(os.path.join(d, unit), os.path.join(FAILED, unit))


def cards_target(staged_name):
    return os.path.join(INBOX, staged_name)


CMD_MAX = 30000 if os.name == 'nt' else 120000  # Windows: 32,767 chars in all; Linux/macOS: 128 KB per argument


def rules_text():
    """The two INGEST-AUTO sections extract needs, plus the taxonomy: put in the prompt so the model reads no files."""
    t = open(os.path.join(ROOT, 'INGEST-AUTO.md'), encoding='utf8').read()
    secs = re.findall(r'^## (?:What counts as knowledge|Card format).*?(?=^## )', t, re.S | re.M)
    tax = re.sub(r'\A---.*?---\s*', '', open(os.path.join(ROOT, 'taxonomy.md'), encoding='utf8').read(), flags=re.S)
    projects = ', '.join(f[:-3] for f in sorted(os.listdir(os.path.join(WIKI, 'projects'))) if f.endswith('.md')) \
        if os.path.isdir(os.path.join(WIKI, 'projects')) else ''
    return '\n'.join(secs).strip(), tax.strip(), projects or '(none yet)'


def extract_prompt(text, rules):
    return prompt('extract-inline', rules=rules[0], taxonomy=rules[1], projects=rules[2], chat=text)


def split_oversize(units, rules):
    """A chat too long for one command line becomes two staged units (same header, half the text each)."""
    out = []
    for u in units:
        src = os.path.join(STAGED, u)
        text = open(src, encoding='utf8').read()
        if len(subprocess.list2cmdline([extract_prompt(text, rules)])) <= CMD_MAX:
            out.append(u)
            continue
        head, _, body = text.partition('\n\n')
        cut = body.rfind('\n\n**', 0, len(body) // 2 + 1)  # split between messages when possible
        cut = cut if cut > 0 else len(body) // 2
        parts = [u[:-3] + '-a.md', u[:-3] + '-b.md']
        for name, piece in zip(parts, (body[:cut], body[cut:])):
            open(os.path.join(STAGED, name), 'w', encoding='utf8', newline='\n').write(head + '\n\n' + piece.strip())
        os.remove(src)
        out += split_oversize(parts, rules)  # halves may still be too long
    return out


def extract(limit=None):
    import concurrent.futures, threading
    led = load_json(LEDGER, {'sessions': {}, 'fails': {}})
    os.makedirs(INBOX, exist_ok=True)
    rules = rules_text()
    units = sorted(os.listdir(STAGED) if os.path.isdir(STAGED) else [], reverse=True)  # newest first
    limit = CFG['max_extract_per_run'] if limit is None else limit
    batch = split_oversize(units[:limit], rules)
    lock, done = threading.Lock(), [0]
    progress('extract', done=0, total=len(batch), unit=f'{CFG.get("extract_workers", 4)} chats at a time')

    def one(u):  # chats are independent, so several run at once; one chat's error never stops the run
        try:
            work(u)
        except Exception as e:
            with lock:
                log('  failed', u, repr(e)[:200])

    def work(u):
        src = os.path.join(STAGED, u)
        ok, out = run_agent('extract', extract_prompt(open(src, encoding='utf8').read(), rules), CFG.get('extract_timeout', 600))
        m = re.search(r'<cards>(.*?)</cards>', out, re.S)
        with lock:
            if not ok or not m:
                fail(led, u, out)
            else:
                cards = m.group(1).strip()
                if cards and cards.upper() != 'NONE':
                    open(cards_target(u), 'w', encoding='utf8', newline='\n').write(cards + '\n')
                os.remove(src)  # saved per unit: a crash later never redoes this one
                log('  extracted', u, 'cards' if cards.upper() != 'NONE' else 'none')
            save_json(LEDGER, led)
            done[0] += 1
            progress('extract', done=done[0], total=len(batch), unit=u)

    with concurrent.futures.ThreadPoolExecutor(CFG.get('extract_workers', 4)) as pool:
        list(pool.map(one, batch))


def record(unit, changed):
    """Script-side bookkeeping: monthly log + auto section of index.md. Agents never touch these."""
    os.makedirs(os.path.join(WIKI, 'logs'), exist_ok=True)
    month = os.path.join(WIKI, 'logs', dt.date.today().strftime('%Y-%m') + '.md')
    if not os.path.exists(month):
        open(month, 'w', encoding='utf8', newline='\n').write(f"# Ingest log {dt.date.today():%Y-%m}\n\n")
    with open(month, 'a', encoding='utf8', newline='\n') as f:
        f.write(f"- {dt.date.today()} `{unit}`: " + ('; '.join(changed) or 'no changes') + '\n')


def related(text, limit=8):
    """Existing notes a card set mentions by title or alias: free (no search, no model), so Write can update old notes."""
    try:
        rows = list(csv.DictReader(open(os.path.join(META, 'catalog.tsv'), encoding='utf8'), delimiter='\t'))
    except OSError:
        return []
    low, hits = text.lower(), {}
    keys = {k.strip(' []').lower() for line in text.splitlines() if line.startswith(('### ', '- links:'))
            for k in line.split(':', 1)[-1].replace('###', '').split(',')}
    for r in rows:
        if r['type'] in ('hub', 'project'):
            continue
        for name in [r['title'], *r['aliases'].split(', ')]:
            n = name.strip().lower()
            # ponytail: multi-word names match anywhere in the cards, one-word names only a card title or link (else
            # "workflows" hits everything). Ceiling: misses notes named differently from the card's wording.
            if len(n) >= 5 and (n in keys if ' ' not in n else re.search(r'(?<!\w)' + re.escape(n) + r'(?!\w)', low)):
                hits[r['path']] = max(hits.get(r['path'], 0), len(n))
    return sorted(hits, key=lambda p: -hits[p])[:limit]


def card_keys(path):
    """Names of every note a card file may touch: its card titles plus their links."""
    keys = set()
    for line in open(path, encoding='utf8'):
        if line.startswith('### '):
            keys.add(line[4:].strip().lower())
        elif line.startswith('- links:'):
            keys.update(k.strip().strip('[]').lower() for k in line[8:].split(',') if k.strip())
    return keys


def integrate(limit=None, only=None):
    import concurrent.futures, threading
    led = load_json(LEDGER, {'sessions': {}, 'fails': {}})
    catalog()
    units = only or (sorted((f for f in os.listdir(INBOX) if f.endswith('.md')), reverse=True) if os.path.isdir(INBOX) else [])
    limit = (len(units) if only else CFG['max_integrate_per_run']) if limit is None else limit
    todo = [(u, card_keys(os.path.join(INBOX, u))) for u in units]  # whole inbox, so a clash can be skipped past
    total, busy, done, started, results = min(limit, len(todo)), set(), [0], [0], {}
    active, wrote = set(), []  # what the app shows: cards being read now, notes the last card wrote
    cond = threading.Condition()
    workers = CFG.get('integrate_workers', 3)
    progress('integrate', done=0, total=total, unit=f'{workers} card files at a time')

    # ponytail: two files that share a note name never run together, so no note is edited by two agents at once.
    # Ceiling: a hub linked from every card (e.g. Claude Code) serialises those files; fine, still correct.
    def take():
        with cond:
            while todo and started[0] < total:
                for i, (u, k) in enumerate(todo):
                    if not (k & busy):
                        busy.update(k)
                        started[0] += 1
                        active.add(u)
                        progress('integrate', done=done[0], total=total, unit=u, reading=sorted(active))
                        return todo.pop(i)
                cond.wait()
            return None

    def one(_):
        while (item := take()):
            u, k = item
            rel = related(open(os.path.join(INBOX, u), encoding='utf8').read())
            ok, out = run_agent('integrate', prompt('integrate', cards=os.path.join(INBOX, u).replace('\\', '/'),
                                                    related='\n'.join('   - ' + r for r in rel) or '   - (none found)'),
                                CFG.get('integrate_timeout', 1500))
            m = re.search(r'<changed>(.*?)</changed>', out, re.S)
            with cond:
                if not ok or not m:
                    fail(led, u, out)
                else:
                    changed = [l.strip() for l in m.group(1).strip().splitlines() if l.strip()]
                    os.remove(os.path.join(INBOX, u))  # the notes hold the knowledge now; git history keeps the card
                    record(u, changed)
                    results[u] = changed
                    wrote[:] = [c.split(':', 1)[-1].strip() for c in changed]
                    log('  integrated', u, len(changed), 'files')
                save_json(LEDGER, led)
                busy.difference_update(k)
                done[0] += 1
                active.discard(u)
                progress('integrate', done=done[0], total=total, unit=u, reading=sorted(active), wrote=wrote)
                cond.notify_all()

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, range(workers)))
    catalog()
    return results


# ---------------------------------------------------------------- frontmatter, catalog

FM_RX = re.compile(r'\A---\n(.*?)\n---\n?', re.S)


def parse_fm(text):
    m = FM_RX.match(text)
    out, key = {}, None
    if not m:
        return out
    for line in m.group(1).splitlines():
        km = re.match(r'^([A-Za-z_][\w -]*):\s*(.*)$', line)
        if km:
            key, val = km.group(1).strip(), km.group(2).strip().strip('"\'')
            out[key] = val if val else []
        elif key and re.match(r'^\s+-\s+', line) and isinstance(out.get(key), list):
            out[key].append(re.sub(r'^\s+-\s+', '', line).strip().strip('"\''))
    return out


def set_fm(text, updates):
    """Add keys that are missing; never overwrite what is already there."""
    m = FM_RX.match(text)
    if not m:
        return text
    fm = parse_fm(text)
    add = ''.join(f'{k}: {v}\n' for k, v in updates.items() if k not in fm and v)
    if not add:
        return text
    return text[:m.end(1)] + '\n' + add.rstrip('\n') + text[m.end(1):]


def notes():
    for f in glob.glob(os.path.join(WIKI, '**', '*.md'), recursive=True):
        rel = os.path.relpath(f, WIKI).replace('\\', '/')
        if rel.startswith(('_dashboards/', 'logs/', 'attachments/')) or '/' not in rel and rel.startswith(('index', 'log', '_', 'Vault Stats')):
            continue
        yield f, rel


def catalog():
    os.makedirs(META, exist_ok=True)
    rows = []
    for f, rel in notes():
        fm = parse_fm(open(f, encoding='utf8').read())
        al = fm.get('aliases') or []
        rows.append(['wiki/' + rel, fm.get('title') or os.path.basename(f)[:-3],
                     ', '.join(al) if isinstance(al, list) else al, fm.get('type') or '', fm.get('field') or '',
                     fm.get('subfield') or ''])
    rows.sort()
    with open(os.path.join(META, 'catalog.tsv'), 'w', encoding='utf8', newline='') as h:
        w = csv.writer(h, delimiter='\t')
        w.writerow(['path', 'title', 'aliases', 'type', 'field', 'subfield'])
        w.writerows(rows)
    topics_file()
    # keep devin.md's manual ingest aware of auto-created notes: generated section only
    idx = os.path.join(WIKI, 'index.md')
    if os.path.exists(idx):
        t = open(idx, encoding='utf8').read()
        auto = []
        for f, rel in notes():
            fm = parse_fm(open(f, encoding='utf8').read())
            if fm.get('source') and fm.get('source') != 'manual' and not isinstance(fm.get('source'), list):
                auto.append(f"- [[{os.path.basename(f)[:-3]}]] · {fm.get('type', '')} · {fm.get('field', '')}/{fm.get('subfield', '')}")
        block = ('<!-- auto-ingest:start (generated by pipeline/wiki.py, do not edit) -->\n## Auto-ingested pages\n\n'
                 + '\n'.join(sorted(auto)) + '\n<!-- auto-ingest:end -->')
        if '<!-- auto-ingest:start' in t:
            t = re.sub(r'<!-- auto-ingest:start.*?<!-- auto-ingest:end -->', lambda _: block, t, flags=re.S)
        elif auto:
            t = t.rstrip() + '\n\n' + block + '\n'
        open(idx, 'w', encoding='utf8', newline='\n').write(t)
    return len(rows)


def topics_file():
    """One line per note for the connect step: recent notes are starred so new knowledge gets linked first."""
    week = time.time() - 7 * 86400
    lines = []
    for f, rel in notes():
        i = note_info(f, rel)
        if i['type'] in ('hub', 'project'):
            continue
        tags = ' '.join(t for t in i['tags'] if t.startswith('topic/'))
        lines.append('\t'.join((('*' if i['mtime'] > week else ' ') + i['path'], i['title'], i['field'], tags, i['summary'][:110])))
    open(os.path.join(META, 'topics.tsv'), 'w', encoding='utf8', newline='\n').write('\n'.join(sorted(lines)) + '\n')


def connect(n=None):
    """Write n new cross-field connection notes. Verified from the folder, not from what the agent says."""
    n = CFG.get('connections_per_run', 2) if n is None else n
    if not n:
        return
    folder = os.path.join(WIKI, 'connections')
    before = set(os.listdir(folder)) if os.path.isdir(folder) else set()
    progress('connect', done=0, total=n, unit='looking for links between fields')
    catalog()
    existing = ', '.join(f[:-3] for f in sorted(before) if f.endswith('.md')) or '(none yet)'
    ok, out = run_agent('integrate', prompt('connect', n=str(n), existing=existing), CFG.get('integrate_timeout', 1500))
    made = sorted((set(os.listdir(folder)) if os.path.isdir(folder) else set()) - before)
    for f in made:
        log('  connected', f)
    if not ok and not made:
        log('  connect failed:', out[:300].replace('\n', ' '))
    progress('connect', 'done' if made else 'failed', len(made), n, made[-1] if made else '')


# ---------------------------------------------------------------- consolidation: item notes -> topic notes

CONSOLIDATE = os.path.join(STATE, 'consolidate.json')


def relink(mapping):
    """Point links at folded notes to their topic note; links to deleted notes become plain text.
    mapping: lowercase note name -> new note name, or None for deleted. Returns files changed."""
    n = 0
    for f, rel in notes():
        me = os.path.basename(f)[:-3]
        t = open(f, encoding='utf8').read()

        def sub(m):
            raw = m.group(1).strip()
            esc = '\\' if raw.endswith('\\') else ''  # a link inside a table escapes its pipe: [[name\|alias]]
            name = raw.rstrip('\\').split('/')[-1]
            if name.lower() not in mapping:
                return m.group(0)
            new, alias = mapping[name.lower()], m.group(2) or name.replace('-', ' ')
            if not new or new == me:
                return alias
            return '[[' + new + esc + '|' + alias + ']]'
        u = LINK_RX.sub(sub, t)
        if u != t:
            open(f, 'w', encoding='utf8', newline='\n').write(u)
            n += 1
    return n


def consolidate(apply=False):
    """Fold or delete chat notes per state/consolidate.json. Saved per group, so a stop resumes where it left off."""
    import concurrent.futures, threading
    plan = load_json(CONSOLIDATE, {})
    groups = plan.get('fold', [])
    if not apply:
        for g in groups:
            print(('done ' if g.get('done') else 'todo ') + g['target'], '<-', len(g['sources']), 'notes')
        print('delete:', len(plan.get('delete', [])), '· keep:', len(plan.get('keep', [])))
        return
    git_commit(f'wiki: before consolidation {dt.date.today()}')  # git is the undo
    todo, lock, done = [g for g in groups if not g.get('done')], threading.Lock(), [0]
    progress('consolidate', done=0, total=len(todo), unit='topic notes')

    def one(g):
        path = os.path.join(WIKI, g['target'])
        start, existed = time.time(), os.path.exists(path)
        srcs = [s for s in g['sources'] if os.path.exists(os.path.join(WIKI, s))]
        ok, out = run_agent('integrate', prompt(
            'consolidate', target='wiki/' + g['target'], target_full='wiki/' + g['target'], title=g['title'],
            exists='It already exists: keep its good content and add to it.' if existed else 'It does not exist yet: create it.',
            sources='\n'.join(f'   - wiki/{s}' for s in srcs)), CFG.get('integrate_timeout', 1500))
        good = os.path.exists(path) and os.path.getmtime(path) >= start and \
            str(parse_fm(open(path, encoding='utf8').read()).get('schema')) == '3'  # checked from the file, not the reply
        with lock:
            g['done'] = good
            save_json(CONSOLIDATE, plan)
            done[0] += 1
            log('  consolidated' if good else '  consolidate failed', g['target'], '' if good else out[:200].replace('\n', ' '))
            progress('consolidate', done=done[0], total=len(todo), unit=g['title'])

    with concurrent.futures.ThreadPoolExecutor(CFG.get('integrate_workers', 3)) as pool:
        list(pool.map(one, todo))
    # only now remove: sources of verified groups, plus the delete list
    mapping, gone = {}, []
    for g in groups:
        if g.get('done') and not g.get('removed'):
            tname = os.path.basename(g['target'])[:-3]
            for s in g['sources']:
                if os.path.basename(s)[:-3].lower() != tname.lower():
                    mapping[os.path.basename(s)[:-3].lower()] = tname
                gone.append(s)
            g['removed'] = True
    if not plan.get('deleted'):
        for s in plan.get('delete', []):
            mapping[os.path.basename(s)[:-3].lower()] = None
            gone.append(s)
        plan['deleted'] = True
    for s in gone:
        if os.path.exists(os.path.join(WIKI, s)):
            os.remove(os.path.join(WIKI, s))
    log(f'consolidate: removed {len(gone)} notes, relinked {relink(mapping)} files')
    save_json(CONSOLIDATE, plan)
    catalog()
    dashboard()
    git_commit(f'wiki: consolidate into topic notes {dt.date.today()}')


# ---------------------------------------------------------------- migration of existing notes

FOLDER_FIELD = {'paradoxes': ('philosophy', 'paradoxes')}


def migrate_step0():
    rows = {r['path']: r for r in read_migration()}
    n = 0
    for f, rel in notes():
        parts = rel.split('/')
        if len(parts) == 1 or parts[0] == 'projects':
            continue
        text = open(f, encoding='utf8').read()
        fm = parse_fm(text)
        top = parts[0].lower()
        up = {}
        if top == 'people':
            up = {'type': 'person'}
        elif top == 'books':
            up = {'type': 'work', 'work_kind': 'book'}
        else:
            field, sub = FOLDER_FIELD.get(top, (top, '/'.join(p for p in parts[1:-1])))
            up = {'field': field, 'subfield': sub}
        hub = os.path.basename(f).endswith(' Home.md') or str(fm.get('category', '')).lower() == 'index'
        if hub:
            up.update(type='hub', schema='2')
        up['source'] = 'manual'
        new = set_fm(text, up)
        if new != text:
            open(f, 'w', encoding='utf8', newline='\n').write(new)
            n += 1
        rows['wiki/' + rel] = {'path': 'wiki/' + rel, 'step0': 'done',
                               'llm': 'skip' if hub else rows.get('wiki/' + rel, {}).get('llm', 'todo'),
                               'batch': rows.get('wiki/' + rel, {}).get('batch', '')}
    ordered = sorted(rows.values(), key=lambda r: r['path'])
    todo = [r for r in ordered if r['llm'] != 'skip']
    for i, r in enumerate(todo):
        r['batch'] = str(i // CFG.get('migrate_batch_size', 50) + 1)
    write_migration(ordered)
    log(f'migrate step0: {n} notes updated, {len(todo)} queued in {-(-len(todo) // CFG.get("migrate_batch_size", 50))} batches')


def read_migration():
    try:
        return list(csv.DictReader(open(MIGRATION, encoding='utf8')))
    except OSError:
        return []


def write_migration(rows):
    os.makedirs(META, exist_ok=True)
    with open(MIGRATION, 'w', encoding='utf8', newline='') as h:
        w = csv.DictWriter(h, fieldnames=['path', 'step0', 'llm', 'batch'])
        w.writeheader()
        w.writerows(rows)


def migrate_batch(batches=1):
    rows = read_migration()
    if not rows:
        log('migrate: run "migrate step0" first')
        return
    for r in rows:  # notes tidied before schema 3 (no topic tags) go round once more
        f = os.path.join(ROOT, r['path'])
        if r['llm'] == 'done' and os.path.exists(f) and str(parse_fm(open(f, encoding='utf8').read()).get('schema')) == '2':
            r['llm'] = 'todo'
    pending = sorted({int(r['batch']) for r in rows if r['llm'] in ('todo', 'failed') and r['batch']})
    for b in pending[:batches]:
        todo = [r for r in rows if r['batch'] == str(b) and r['llm'] in ('todo', 'failed')]
        step = CFG.get('migrate_chunk', 10)
        for i in range(0, len(todo), step):
            progress('migrate', done=i, total=len(todo), unit=f'batch {b}')
            chunk = todo[i:i + step]
            ok, out = run_agent('integrate', prompt('migrate', files='\n'.join('- ' + r['path'] for r in chunk)),
                                CFG.get('integrate_timeout', 1500))
            for r in chunk:  # verify from the file itself, not from what the agent said
                done = str(parse_fm(open(os.path.join(ROOT, r['path']), encoding='utf8').read()).get('schema')) == '3' \
                    if os.path.exists(os.path.join(ROOT, r['path'])) else False
                r['llm'] = 'done' if done else 'failed'
            write_migration(rows)  # saved per chunk
            progress('migrate', done=i + len(chunk), total=len(todo), unit=f'batch {b}')
            log(f'  migrate batch {b}: chunk {i // step + 1} ->', sum(r['llm'] == 'done' for r in chunk), '/', len(chunk),
                '' if ok else '(runner error: ' + out[:200].replace('\n', ' ') + ')')


# ---------------------------------------------------------------- status, git, run

def status(write=True):
    led = load_json(LEDGER, {'sessions': {}, 'fails': {}})
    ls = lambda d: [f for f in os.listdir(d) if f.endswith('.md')] if os.path.isdir(d) else []
    staged, inbox, done, failed = ls(STAGED), ls(INBOX), ls(DONE), ls(FAILED)
    per = {}
    for k, v in led['sessions'].items():
        s = k.split(':')[0]
        p = per.setdefault(s, [0, 0])
        p[0 if v.get('human') else 1] += 1
    mig = read_migration()
    mtodo = [r for r in mig if r['llm'] != 'skip']
    mdone = sum(r['llm'] == 'done' for r in mtodo)
    batches = sorted({r['batch'] for r in mtodo if r['batch']}, key=int)
    bdone = sum(all(r['llm'] == 'done' for r in mtodo if r['batch'] == b) for b in batches)
    last = load_json(os.path.join(STATE, 'last_run.json'), {})
    lines = [
        '---', 'title: Pipeline', 'publish: false', '---',
        '# Pipeline status', '',
        f"_Generated by `wiki.py status` · {dt.datetime.now():%Y-%m-%d %H:%M}_", '',
        f"**Last run:** {last.get('at', 'never')} · {last.get('result', '')}", '',
        '| Stage | Count |', '| --- | --- |',
        f'| Chats waiting for extraction | {len(staged)} |',
        f'| Card files waiting for integration | {len(inbox)} |',
        f'| Failed 3 times (in `~/.mywiki/state/failed/`) | {len(failed)} |', '',
        '| Source | Your chats seen | Tool runs skipped |', '| --- | --- | --- |',
        *[f'| {s} | {a} | {b} |' for s, (a, b) in sorted(per.items())], '',
        f"**Migration of existing notes:** {mdone}/{len(mtodo)} notes · {bdone}/{len(batches)} batches done"
        + (f" · {sum(r['llm'] == 'failed' for r in mtodo)} failed (retried next run)" if mtodo else ''), '',
    ]
    if write:
        os.makedirs(os.path.join(WIKI, '_dashboards'), exist_ok=True)
        open(os.path.join(WIKI, '_dashboards', 'Pipeline.md'), 'w', encoding='utf8', newline='\n').write('\n'.join(lines))
    print('\n'.join(lines[4:]))


def git_commit(msg):
    if not os.path.isdir(os.path.join(ROOT, '.git')):
        return
    g = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True, creationflags=NOWIN)
    g('add', '-A')
    if g('diff', '--cached', '--quiet').returncode:
        who = [] if g('config', 'user.email').stdout.strip() else ['-c', 'user.name=MyWiki', '-c', 'user.email=mywiki@localhost']  # a fresh machine has no git identity
        r = g(*who, 'commit', '-m', msg)
        if r.returncode:
            log('git commit FAILED:', (r.stderr or r.stdout)[:200])
        elif g('remote').stdout.strip():
            r = g('push')
            log('git push', 'ok' if r.returncode == 0 else 'FAILED: ' + r.stderr[:200])


class Lock:
    """One run at a time, whichever agent or scheduler started it."""
    path = os.path.join(STATE, 'run.lock')

    def __enter__(self):
        os.makedirs(STATE, exist_ok=True)
        if os.path.exists(self.path) and NOW - os.path.getmtime(self.path) < 6 * 3600:
            sys.exit(f'another wiki-ingest run is in progress ({self.path}); delete the file if it is stale')
        open(self.path, 'w').write(str(os.getpid()))

    def __exit__(self, *a):
        try:
            os.remove(self.path)
        except OSError:
            pass


def run():
    result = 'ok'
    try:
        if os.path.isdir(os.path.join(ROOT, '.git')):
            subprocess.run(['git', 'pull', '--rebase', '--autostash'], cwd=ROOT, capture_output=True, creationflags=NOWIN)
        if CFG.get('show_window', True):  # the app opens itself on a run; closing it never stops the run
            launch([os.path.join(APP, 'app.py')])
        harvest()
        extract()
        finish('extract')
        integrate()
        finish('integrate')
        connect()
        if CFG.get('migrate_batches_per_run', 0):
            migrate_batch(CFG['migrate_batches_per_run'])
            finish('migrate')
        catalog()
        dashboard()
    except Exception as e:
        result = 'FAILED: ' + repr(e)
        log(result)
        raise
    finally:
        save_json(os.path.join(STATE, 'last_run.json'), {'at': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'result': result})
        status()
        progress('save')
        git_commit(f'wiki-ingest {dt.date.today()}')
        finish('save', 'done' if result == 'ok' else 'failed')


# ---------------------------------------------------------------- library: Home dashboard, field maps, news

LINK_RX = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]')
NOT_PROSE = re.compile(r'^(>|#|!|\||- |-{3}|\* |`|<|\$|\d+\.\s)')  # **bold** openers are prose
DASH = os.path.join(WIKI, '_dashboards')
NEWS_DIR = os.path.join(DASH, 'News')
EXPORTS = os.path.join(ROOT, 'raw', 'exports', 'claude')


def unlink(s):
    return re.sub(r'[*_`]', '', LINK_RX.sub(lambda m: m.group(2) or m.group(1), s)).strip()


def note_info(f, rel):
    t = open(f, encoding='utf8', errors='replace').read()
    fm, body = parse_fm(t), FM_RX.sub('', t, 1)
    summary = next((re.split(r'(?<=[.!?])\s', unlink(l.strip()), maxsplit=1)[0][:240] for l in body.splitlines()
                    if l.strip() and not NOT_PROSE.match(l.strip())), '')
    m = re.search(r'^## Interesting facts\s*\n(.*?)(?=^## |\Z)', body, re.S | re.M)
    facts = [unlink(l.strip()[2:]) for l in m.group(1).splitlines() if l.strip().startswith(('- ', '* '))] if m else []
    first = lambda v: (v[0] if v else '') if isinstance(v, list) else (v or '')
    field = first(fm.get('field')) or first(fm.get('fields')).split('/')[0] or rel.split('/')[0]
    return {'name': os.path.basename(f)[:-3], 'title': first(fm.get('title')) or os.path.basename(f)[:-3], 'path': 'wiki/' + rel,
            'type': first(fm.get('type')), 'field': field, 'subfield': first(fm.get('subfield')), 'angle': first(fm.get('angle')),
            'created': first(fm.get('Created'))[:10], 'mtime': os.path.getmtime(f), 'summary': summary, 'facts': facts,
            'links': {m.group(1).strip().rstrip('\\').split('/')[-1].lower() for m in LINK_RX.finditer(body)},  # table links end in a backslash
            'contradiction': first(fm.get('contradiction')) == 'open', 'status': first(fm.get('status')),
            'last_active': first(fm.get('last_active')), 'about': first(fm.get('summary')),
            'tags': [str(t) for t in (fm.get('tags') if isinstance(fm.get('tags'), list) else [fm.get('tags')] if fm.get('tags') else [])],
            'chain': first(fm.get('chain')), 'schema': str(first(fm.get('schema')))}


def export_status():
    """claude.ai is the one source that needs a manual monthly export; everything else is read from disk."""
    zips = glob.glob(os.path.join(EXPORTS, '*.zip'))
    last = max(map(os.path.getmtime, zips)) if zips else 0
    found = []
    for z in glob.glob(os.path.join(H, 'Downloads', '*.zip')):
        if os.path.getmtime(z) <= last or os.path.basename(z) in map(os.path.basename, zips):
            continue
        try:
            if any(n.endswith('conversations.json') for n in zipfile.ZipFile(z).namelist()):
                found.append({'path': z, 'name': os.path.basename(z), 'mb': round(os.path.getsize(z) / 1e6, 1),
                              'date': dt.datetime.fromtimestamp(os.path.getmtime(z)).strftime('%Y-%m-%d')})
        except (OSError, zipfile.BadZipFile):
            pass
    days = int((time.time() - last) // 86400) if last else None
    return {'last': dt.datetime.fromtimestamp(last).strftime('%Y-%m-%d') if last else None, 'days': days,
            'due': days is None or days >= 30, 'found': found, 'folder': EXPORTS}


def import_export(path):
    os.makedirs(EXPORTS, exist_ok=True)
    zipfile.ZipFile(path).namelist()  # refuse anything that is not a zip
    dest = os.path.join(EXPORTS, os.path.basename(path))
    shutil.move(path, dest)
    os.utime(dest)  # import time is what the monthly reminder counts from
    log('export imported', os.path.basename(dest))
    return dest


def news_pages():
    out = []
    for f in glob.glob(os.path.join(NEWS_DIR, '*.md')):
        t = open(f, encoding='utf8').read()
        fm = parse_fm(t)
        brief = re.search(r'^## \d{4}-\d{2}-\d{2}.*?\n+(.+?)\n', FM_RX.sub('', t, 1), re.S | re.M)
        out.append({'topic': fm.get('topic') or os.path.basename(f)[:-3], 'page': os.path.basename(f)[:-3],
                    'date': fm.get('last_researched', ''), 'brief': unlink(brief.group(1))[:400] if brief else ''})
    return sorted(out, key=lambda n: n['date'], reverse=True)


def library():
    infos = [note_info(f, rel) for f, rel in notes()]
    recent = sorted(infos, key=lambda i: i['mtime'], reverse=True)
    week = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    fields, topics = {}, {}
    for i in infos:
        fields[i['field']] = fields.get(i['field'], 0) + 1
        for t in i['tags']:
            if t.startswith('topic/'):
                topics.setdefault(t[6:], []).append(i)
    conns = [i for i in recent if i['type'] == 'connection']
    return {
        'stats': {'notes': len(infos), 'new_week': sum(i['created'] >= week for i in infos), 'connections': len(conns),
                  'topic_notes': sum(i['schema'] == '3' for i in infos), 'contradictions': sum(i['contradiction'] for i in infos),
                  'fields': dict(sorted(fields.items(), key=lambda x: -x[1]))},
        'connections': conns, 'topics': dict(sorted(topics.items(), key=lambda x: -len(x[1]))),
        'latest': [i for i in recent if i['type'] not in ('project', 'connection', 'hub')][:12],
        'projects': sorted((i for i in infos if i['type'] == 'project'), key=lambda i: i['last_active'], reverse=True)[:6],
        'news': news_pages()[:6], 'export': export_status(),
    }


def dashboard(lib=None):
    """Home.md, a topic map and one map per field. Plain markdown, so it also reads on mobile and on GitHub."""
    lib = lib or library()
    s, ex = lib['stats'], lib['export']
    ln = lambda n: f"[[{n['name']}|{n['title']}]]"
    last = load_json(os.path.join(STATE, 'last_run.json'), {})
    out = ['---', 'title: Home', 'publish: false', 'cssclasses:', '  - wide-page', '---', '# MyWiki', '',
           f"_Updated {dt.datetime.now():%Y-%m-%d %H:%M} · last run {last.get('at', 'never')}_", '']
    if ex['due']:
        out += ['> [!tip] Monthly chat export is due',
                f"> Last claude.ai export: {ex['last'] or 'never'}. Open the MyWiki app, then **Export**, and follow the three steps.", '']
    out += ['## At a glance', '',
            '| Notes | New this week | Topic notes | Connections | Open contradictions |', '| --- | --- | --- | --- | --- |',
            f"| {s['notes']} | {s['new_week']} | {s['topic_notes']} | {s['connections']} | {s['contradictions']} |", '',
            'Fields: ' + ' · '.join(f"[[Maps/{f.title()}|{f.title()}]] {n}" for f, n in s['fields'].items() if f), '',
            'Topics: ' + (' · '.join(f"[[Maps/Topics#{t}|{t.replace('-', ' ')}]] {len(v)}" for t, v in list(lib['topics'].items())[:16])
                          or '_none tagged yet; new and tidied notes carry `topic/…` tags_'), '',
            '## Meaningful connections', '_Chains of ideas across fields, and why each step leads to the next._', '']
    out += [f"- **{ln(c)}**  \n  {c['chain'] or c['summary']}" for c in lib['connections'][:10]] or \
        ['_None yet. Each run\'s Connect step writes new ones._']
    out += ['']
    today = load_json(FEED, {})
    if today.get('items'):
        out += [f"## Today's news", f"_Top stories {today['date']}. Make a note from any of them in the MyWiki app, under News._", '',
                *[f"- **{i['segment']}** · [{i['title'].replace('[', '(').replace(']', ')')}]({i['url']}) · {i['source']}"
                  + (f" · noted in [[{i['noted'].split(', ')[0]}]]" if i.get('noted') and i['noted'] != 'nothing new' else '')
                  for i in today['items'][:12]], '']
    if lib['news']:
        out += ['## Research', *[f"- [[News/{n['page']}|{n['topic']}]] · {n['date']} · {n['brief'][:180]}" for n in lib['news']], '']
    out += ['## Latest notes', *[f"- {ln(n)} · {n['field']} · {n['summary'][:150]}" for n in lib['latest']], '']
    if lib['projects']:
        out += ['## Projects', *[f"- {ln(p)} · {p['status'] or 'active'} · last active {p['last_active'] or '?'}" for p in lib['projects']], '']
    os.makedirs(os.path.join(DASH, 'Maps'), exist_ok=True)
    open(os.path.join(DASH, 'Home.md'), 'w', encoding='utf8', newline='\n').write('\n'.join(out))
    body = ['---', 'title: Topics map', 'publish: false', '---', '# Topics', '',
            '_Notes grouped by their `topic/…` tag, across folders. Generated, do not edit._', '']
    for t, ns in lib['topics'].items():
        body += [f"## {t}", '', *[f"- {ln(i)} · {i['field']}" for i in sorted(ns, key=lambda i: i['title'].lower())], '']
    open(os.path.join(DASH, 'Maps', 'Topics.md'), 'w', encoding='utf8', newline='\n').write('\n'.join(body))
    groups = {}
    for f, rel in notes():
        i = note_info(f, rel)
        groups.setdefault(i['field'], {}).setdefault(i['subfield'] or 'general', []).append(i)
    for field, subs in groups.items():
        if not field:
            continue
        body = ['---', f'title: {field.title()} map', 'publish: false', '---', f'# {field.title()}', '',
                f"_{sum(map(len, subs.values()))} notes in {len(subs)} areas. Generated, do not edit._", '']
        for sub in sorted(subs):
            body += [f"## {sub.replace('-', ' ').replace('/', ' / ').title()}", '',
                     *[f"- {ln(i)}{' · ' + i['summary'][:160] if i['summary'] else ''}" for i in sorted(subs[sub], key=lambda i: i['title'].lower())], '']
        open(os.path.join(DASH, 'Maps', field.title() + '.md'), 'w', encoding='utf8', newline='\n').write('\n'.join(body))
    return lib


def research(topic, days=30, depth='quick'):
    """last30days engine, called directly (its documented scripted path). Returns its agent-profile JSON."""
    exe = sys.executable.replace('pythonw.exe', 'python.exe')
    eng = os.path.expanduser(CFG.get('last30days', '~/.agents/skills/last30days/scripts/last30days.py'))
    args = [exe, eng, topic, '--emit=json', f'--days={days}'] + {'quick': ['--quick'], 'deep': ['--deep']}.get(depth, [])
    p = subprocess.run(args, capture_output=True, text=True, encoding='utf8', errors='replace', timeout=1800, creationflags=NOWIN,
                       stdin=subprocess.DEVNULL, env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'))
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise RuntimeError('last30days failed: ' + (p.stderr or p.stdout)[-400:])


KEYS = os.path.join(STATE, 'keys.json')          # search keys typed in Settings; gitignored, never sent to the page
SEARCH_USE = os.path.join(STATE, 'search.json')  # {date, <provider>: calls today, status: {<provider>: last result}}
PROVIDERS = {  # tried in this order; each stops at its own daily cap, then the model searches for itself
    'brave': {'name': 'Brave Search', 'on': True, 'per_day': 9, 'key': True,
              'about': 'Free plan: about 2,000 searches a month, one a second. Also fixes misspelt topics.', 'site': 'https://brave.com/search/api/'},
    'tavily': {'name': 'Tavily', 'on': False, 'per_day': 30, 'key': True,
               'about': 'Free plan: 1,000 searches a month, no card needed. Built for AI research.', 'site': 'https://app.tavily.com/'},
    'ddg': {'name': 'DuckDuckGo', 'on': False, 'per_day': 30, 'key': False,
            'about': 'Free, no key. Unofficial, so it can slow down or refuse when used a lot.', 'site': 'https://duckduckgo.com/'},
}


def search_cfg():
    return {p: {**v, **CFG.get('search', {}).get(p, {})} for p, v in PROVIDERS.items()}


def search_key(p):
    """(key, where it came from). Settings > environment > (Brave only) the Brave MCP entry already set up for Windsurf."""
    k = load_json(KEYS, {}).get(p)
    if k:
        return k, 'saved in MyWiki'
    env = {'brave': 'BRAVE_API_KEY', 'tavily': 'TAVILY_API_KEY'}.get(p)
    if env and os.environ.get(env):
        return os.environ[env], 'from the environment'
    if p == 'brave':
        try:
            cfg = json.load(open(os.path.expanduser(CFG.get('brave_mcp_config', '~/.codeium/windsurf/mcp_config.json')), encoding='utf8'))
            return next(v['env']['BRAVE_API_KEY'] for v in cfg.get('mcpServers', {}).values() if 'BRAVE_API_KEY' in (v.get('env') or {})), "from Windsurf's Brave settings"
        except (OSError, ValueError, StopIteration, KeyError):
            pass
    return '', ''


def search_usage():
    u = load_json(SEARCH_USE, {})
    if u.get('date') != str(dt.date.today()):
        u = {'date': str(dt.date.today()), 'status': u.get('status', {})}
    return u


def provider_left(p):
    c = search_cfg()[p]
    if not c['on'] or (c['key'] and not search_key(p)[0]):
        return 0
    return max(0, int(c['per_day']) - search_usage().get(p, 0))


def search_left():
    """Searches left today across all providers. None left: the model searches for itself."""
    return sum(provider_left(p) for p in PROVIDERS)


def _brave(q, kind, fresh, count):
    import html, urllib.request, urllib.parse
    time.sleep(1.1)  # free plan: one request a second
    u = f'https://api.search.brave.com/res/v1/{kind}/search?' + urllib.parse.urlencode({'q': q, 'count': count, **({'freshness': fresh} if fresh else {})})
    d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'X-Subscription-Token': search_key('brave')[0], 'Accept': 'application/json'}), timeout=30))
    rows = d.get('results') if kind == 'news' else d.get('web', {}).get('results', [])
    return (d.get('query') or {}).get('altered'), [
        {'title': html.unescape(r.get('title') or ''), 'url': r.get('url'), 'source': (r.get('meta_url') or {}).get('hostname') or 'web',
         'published_at': (r.get('page_age') or r.get('age') or '')[:10], 'relevance_score': .6 - i * .01,
         'summary': html.unescape(re.sub(r'<[^>]+>', '', r.get('description') or ''))} for i, r in enumerate(rows or [])]


def _tavily(q, kind, fresh, count):
    import urllib.request, urllib.parse
    body = {'query': q, 'topic': 'news' if kind == 'news' else 'general', 'max_results': min(count, 20),
            **({'time_range': {'pd': 'day', 'pw': 'week', 'pm': 'month', 'py': 'year'}[fresh]} if fresh else {})}
    req = urllib.request.Request('https://api.tavily.com/search', json.dumps(body).encode(),
                                 {'Authorization': 'Bearer ' + search_key('tavily')[0], 'Content-Type': 'application/json'})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    return None, [{'title': r.get('title') or '', 'url': r.get('url'), 'source': urllib.parse.urlparse(r.get('url') or '').hostname or 'web',
                   'published_at': (r.get('published_date') or '')[:10], 'relevance_score': .6 - i * .01, 'summary': (r.get('content') or '')[:500]}
                  for i, r in enumerate(d.get('results') or [])]


def _ddg(q, kind, fresh, count):
    import urllib.parse
    from ddgs import DDGS
    t = {'pd': 'd', 'pw': 'w', 'pm': 'm', 'py': 'y'}.get(fresh)
    try:
        rows = DDGS().news(q, timelimit=t, max_results=count) if kind == 'news' else DDGS().text(q, timelimit=t, max_results=count)
    except Exception as e:
        if 'No results found' in str(e):  # ddgs raises for an empty search; that is an answer, not a failure
            return None, []
        raise
    return None, [{'title': r.get('title') or '', 'url': r.get('url') or r.get('href'), 'published_at': (r.get('date') or '')[:10],
                   'source': r.get('source') or urllib.parse.urlparse(r.get('url') or r.get('href') or '').hostname or 'web',
                   'relevance_score': .6 - i * .01, 'summary': r.get('body') or ''} for i, r in enumerate(rows or [])]


USE_LOCK = threading.Lock()


def search_one(p, q, kind='news', fresh='pm', count=20):
    """One call to one provider; counts it and records how it went for Settings. Raises on failure."""
    with USE_LOCK:
        u = search_usage()
        u[p] = u.get(p, 0) + 1
        save_json(SEARCH_USE, u)
    try:
        alt, rows = {'brave': _brave, 'tavily': _tavily, 'ddg': _ddg}[p](q, kind, fresh, count)
        st = {'ok': True, 'msg': f'{len(rows)} results'}
        return alt, rows
    except Exception as e:
        st = {'ok': False, 'msg': plain_search_error(e)}
        raise
    finally:
        with USE_LOCK:
            u = search_usage()
            u.setdefault('status', {})[p] = dict(st, at=dt.datetime.now().strftime('%Y-%m-%d %H:%M'))
            save_json(SEARCH_USE, u)


def plain_search_error(e):
    s = str(e)
    code = getattr(e, 'code', None)
    if code in (401, 403) or 'Unauthorized' in s or 'invalid' in s.lower() and 'key' in s.lower():
        return 'The key was refused. Check it is copied in full and still active.'
    if code == 429 or 'Ratelimit' in s or 'rate' in s.lower():
        return 'Too many searches: the plan limit or the per-second limit was hit. It resets on its own.'
    if code == 432 or 'usage limit' in s.lower():
        return 'The monthly free searches are used up.'
    if isinstance(e, (OSError,)) and not code:
        return 'Could not reach the service. Check the internet connection.'
    return s[:200]


def search(q, kind='news', fresh='pm', count=20):
    """Web or news search through the first provider with searches left today.
    Returns (spell-corrected query or None, results shaped like last30days items); ([], None) once all are used."""
    for p in PROVIDERS:
        if provider_left(p) <= 0:
            continue
        try:
            alt, rows = search_one(p, q, kind, fresh, count)
        except Exception as e:
            log(f'  {p} failed:', str(e)[:200])
            continue
        if rows:
            return alt, rows
    if not search_left():
        log('  search: no searches left today, skipping', q[:60])
    return None, []


SEARCH_TOOLS = ('Use only your web search and web fetch tools. Do not read or write files, run commands or open skills.')
FETCH_TOOLS = ('Use only your web fetch tool, to open links given below. Do not search the web, read or write files, run commands or open skills.')
LOOK_TOOLS = ('Use only your file read tool, to open the pictures listed below. Do not search the web, write files, run commands or open skills.')
NO_TOOLS = ("Everything you need is in this message. Do not use any tools: don't read files, run commands, open skills, or search the web. Just reply.")


# ---------------------------------------------------------------- daily news feed (Brave + one model web search per segment)

FEED = os.path.join(STATE, 'feed.json')
FEED_SEGMENTS = {  # segment: (Brave news query, freshness, what the model searches for). Your own pick lives in "feed_segments" (setup or Settings > News topics).
    'Science': ('scientists discover', 'pd', 'new scientific discoveries and findings'),
    'Space': ('astronomers discover', 'pd', 'astronomy, space science and space missions'),
    'Body & health': ('new study reveals', 'pd', 'medicine, biology and the human body'),
    'Mind': ('site:psypost.org OR site:sciencedaily.com psychology', 'pw', 'psychology and neuroscience'),
    'History': ('archaeologists discover', 'pd', 'archaeology and history finds'),
    'Maths': ('mathematicians', 'pw', 'mathematics: proofs, problems solved, new results'),
    'AI': ('AI research breakthrough', 'pd', 'AI research results (not product launches or funding)'),
    'Design': ('site:dezeen.com OR site:itsnicethat.com OR site:creativebloq.com', 'pw', 'graphic, product and architecture design'),
    'Indian culture': ('India heritage history culture', 'pw', 'Indian heritage, history, art and archaeology'),
}
PRESS = ('openpr.', 'prnewswire.', 'globenewswire.', 'einpresswire.', 'businesswire.', 'newsy-today.')


def parse_stories(out):
    """Model reply lines "title | url | publisher | date | summary" -> feed rows."""
    import urllib.parse
    rows = []
    for line in out.splitlines():
        p = [x.strip() for x in line.strip().strip('-*').split('|')]
        if len(p) < 5 or not p[1].startswith('http'):
            continue
        rows.append({'title': p[0], 'url': p[1], 'source': p[2] or urllib.parse.urlparse(p[1]).hostname or 'web',
                     'published_at': p[3][:10] if re.fullmatch(r'\d{4}-\d\d-\d\d.*', p[3]) else '', 'summary': ' | '.join(p[4:])})
    return rows


def model_stories(seg, hint, fresh):
    days = '2 days' if fresh == 'pd' else '7 days'
    ok, out = run_agent('extract', prompt('feed', segment=seg, hint=hint, days=days, tools=SEARCH_TOOLS), CFG.get('extract_timeout', 600))
    rows = parse_stories(out) if ok else []
    log(f'  feed search {seg}: {len(rows)} stories' + ('' if ok else ' (failed)'))
    return rows


def feed(force=False):
    """Top ~20 fresh stories across segments, once a day: one Brave call and one model web search per segment."""
    old = load_json(FEED, {})
    if old.get('date') == str(dt.date.today()) and old.get('items') and not force:
        return old
    noted = {i['url']: i['noted'] for i in old.get('items', []) if i.get('noted')}
    segs = CFG.get('feed_segments', FEED_SEGMENTS)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(CFG.get('extract_workers', 4)) as pool:
        found = {seg: pool.submit(model_stories, seg, (v[2:] or [seg])[0], v[1]) for seg, v in segs.items()}
        bfound = {seg: search(v[0], 'news', v[1], 12)[1] for seg, v in segs.items()}  # web search runs while the model searches
        found = {seg: f.result() for seg, f in found.items()}
    oldest = str(dt.date.today() - dt.timedelta(days=8))
    picks, seen = {}, set()
    for seg in segs:
        b = sorted(bfound[seg], key=lambda r: r['published_at'], reverse=True)
        m = [r for r in found[seg] if not r['published_at'] or r['published_at'] >= oldest]
        for r in [x for pair in itertools.zip_longest(m, b) for x in pair if x]:  # model and Brave take turns
            k = re.sub(r'\W+', ' ', r['title'].lower())[:50]
            if r['url'] in seen or k in seen or any(p in r['source'] for p in PRESS) or len(r['title']) < 20:
                continue
            seen.update((r['url'], k))
            picks.setdefault(seg, []).append(dict(r, segment=seg, noted=noted.get(r['url'], '')))
    # ponytail: two per segment first, then the freshest leftovers up to 20
    items = [i for v in picks.values() for i in v[:2]]
    items += sorted((i for v in picks.values() for i in v[2:]), key=lambda i: i['published_at'], reverse=True)[:max(0, 20 - len(items))]
    for i in items:
        i.pop('relevance_score', None)
    out = {'date': str(dt.date.today()), 'items': items}
    save_json(FEED, out)
    log(f'feed: {len(items)} stories in {len(picks)} segments')
    return out


def capture(*urls):
    """Notes from the feed stories the user picked. The model reads each story and its topic (Brave results while
    today's Brave calls last, its own web search after), writes cards; then one Write, one connection, one save."""
    f = load_json(FEED, {})
    items = [i for i in f.get('items', []) if i['url'] in urls]
    if not items:
        sys.exit('not in today\'s feed: ' + ' '.join(urls))
    topic = items[0]['title'] if len(items) == 1 else f'{len(items)} stories'
    progress('research', total=len(items), unit=items[0]['title'])
    p = load_json(PROGRESS, {})
    p.update(kind='capture', topic=topic, stories=[i['url'] for i in items])
    save_json(PROGRESS, p)
    import threading
    rules = rules_text()
    names, lock, done = {}, threading.Lock(), [0]

    def one(item):
        web = search(item['title'], 'web', None, 8)[1]  # empty once today's searches are used
        ev, _ = evidence({'results': [dict(item, relevance_score=1)] + web}, 12000)
        ok, out = run_agent('extract', prompt('capture', title=item['title'], rules=rules[0], taxonomy=rules[1], evidence=ev,
                                              tools=FETCH_TOOLS if web else SEARCH_TOOLS,
                                              gather='Open the story [1] and the two or three most useful results below and read them.' if web else
                                              'Open the story [1] and read it, then search the web for two to four good sources on the topic behind it (what it is, how it works) and read them.'),
                            CFG.get('extract_timeout', 900))
        cards = re.search(r'<cards>(.*?)</cards>', out, re.S)
        with lock:
            done[0] += 1
            progress('research', done=done[0], total=len(items), unit=item['title'])
            if not ok or not cards or cards.group(1).strip().upper() == 'NONE':
                log('capture: no cards for', item['url'], out[-200:].replace('\n', ' '))
                return
            name = f"{dt.date.today()}__news__{slug(item['title'], 40)}__{int(time.time())}{done[0]}.md"
            os.makedirs(INBOX, exist_ok=True)
            open(os.path.join(INBOX, name), 'w', encoding='utf8', newline='\n').write(
                cards.group(1).strip() + f"\n\n<!-- from: {item['title']} · {item['source']} · {item['url']} -->\n")
            names[name] = item['url']

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(CFG.get('extract_workers', 4)) as pool:
        list(pool.map(one, items))
    progress('research', 'done', len(items), len(items))
    res = integrate(only=list(names)) if names else {}
    finish('integrate')
    noted = {}
    for name, url in names.items():
        noted[url] = ', '.join(os.path.basename(c.split(':', 1)[-1].strip())[:-3] for c in res.get(name, [])) or 'nothing new'
    for i in items:
        noted.setdefault(i['url'], 'nothing new')
    if any(res.values()):
        connect(1)
    f = load_json(FEED, {})  # re-read: the feed may have been refetched meanwhile
    for i in f.get('items', []):
        if i['url'] in noted:
            i['noted'] = noted[i['url']]
    save_json(FEED, f)
    progress('save')
    catalog()
    dashboard()
    git_commit(f'news: notes from {len(items)} stories' if len(items) > 1 else f'news: note from "{items[0]["title"][:60]}"')
    finish('save')
    log(f'capture: {len(items)} stories -> {sum(len(v) for v in res.values())} notes')


# ---------------------------------------------------------------- social links (YouTube, Instagram, TikTok, X...): yt-dlp + local speech-to-text

MEDIA = os.path.join(ROOT, '.mywiki', 'media')  # in the vault so agents scoped to it can open the pictures; .gitignored
COOKIES = os.path.join(STATE, 'cookies.txt')  # Netscape cookies.txt exported from a browser where you are logged in (Instagram)
URL_RX = re.compile(r'https?://\S+')


class _Hush:  # yt-dlp prints its errors even when quiet; MyWiki reports them itself, in plain words
    debug = info = warning = error = staticmethod(lambda *a, **k: None)


def ydl_opts(**kw):
    o = {'quiet': True, 'no_warnings': True, 'noprogress': True, 'noplaylist': True, 'socket_timeout': 30, 'logger': _Hush(), **kw}
    if os.path.exists(COOKIES):
        o['cookiefile'] = COOKIES
    elif CFG.get('cookies_browser'):  # e.g. "firefox"; Edge/Chrome lock their cookie file while open
        o['cookiesfrombrowser'] = (CFG['cookies_browser'],)
    return o


def plain_ydl_error(e):
    s = re.sub(r'\x1b\[[0-9;]*m', '', str(e))
    if re.search(r'login|log in|cookies|rate-limit|private|Requested content is not available', s, re.I):
        return ('This post needs a login. Log in to the site in your browser, export its cookies as cookies.txt '
                '(for example with the "Get cookies.txt LOCALLY" extension) and add it in MyWiki > Settings > Social media.')
    if 'Unsupported URL' in s:
        return 'That link is not a video or post MyWiki can read.'
    return s.replace('ERROR: ', '')[:300]


def link_info(url):
    """Details of a post without downloading it: what the app previews."""
    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts(skip_download=True)) as y:
        i = y.extract_info(url, download=False)
    top, ents = i, [e for e in (i.get('entries') or []) if e] if i.get('_type') == 'playlist' else []
    if ents:
        i = {**ents[0], 'description': ents[0].get('description') or top.get('description')}
    pics = [e.get('thumbnail') for e in ents if e.get('thumbnail')] or ([i['thumbnail']] if i.get('thumbnail') else [])
    video = bool(i.get('duration')) or any((f.get('vcodec') or 'none') != 'none' for e in (ents or [i]) for f in e.get('formats') or [])
    subs = {**(i.get('automatic_captions') or {}), **(i.get('subtitles') or {})}
    d = i.get('upload_date') or ''
    return {'url': url, 'title': i.get('title') or i.get('description', '')[:80] or url, 'site': i.get('extractor_key') or '',
            'channel': i.get('channel') or i.get('uploader') or '', 'date': f'{d[:4]}-{d[4:6]}-{d[6:]}' if len(d) == 8 else '',
            'duration': int(i.get('duration') or 0), 'description': (i.get('description') or '')[:3000],
            'tags': (i.get('tags') or [])[:15], 'chapters': [c.get('title') for c in (i.get('chapters') or [])][:40],
            'subs': sorted(subs)[:30], 'thumb': i.get('thumbnail') or '', 'pics': pics[:10], 'video': video}


def vtt_text(raw):
    """WebVTT/SRT -> plain text, without timestamps and the rolling duplicates auto-captions repeat."""
    out = []
    for line in raw.splitlines():
        line = re.sub(r'<[^>]+>', '', line).strip()
        if not line or '-->' in line or line.isdigit() or line.startswith(('WEBVTT', 'Kind:', 'Language:', 'NOTE')):
            continue
        if not out or out[-1] != line:
            out.append(line)
    return ' '.join(out)


def audio_16k(path):
    """Any audio/video file -> 16 kHz mono float32, decoded here because faster-whisper's own decoder breaks on PyAV 19."""
    import av, numpy as np
    with av.open(path) as c:
        rs = av.AudioResampler(format='s16', layout='mono', rate=16000)
        parts = [f.to_ndarray().ravel() for fr in c.decode(audio=0) for f in rs.resample(fr)]
        parts += [f.to_ndarray().ravel() for f in rs.resample(None)]
    return np.concatenate(parts).astype(np.float32) / 32768 if parts else np.zeros(1, np.float32)


def transcript(url, info):
    """What is said in the post: its subtitles if it has any, else its audio through faster-whisper on the CPU."""
    import yt_dlp
    subs = info['subs']
    lang = next((l for l in subs if l in ('en', 'en-orig', 'en-US', 'en-GB')), None) or next((l for l in subs if l.startswith('en')), None) or (subs[0] if subs else None)
    if lang:
        with yt_dlp.YoutubeDL(ydl_opts(skip_download=True, writesubtitles=True, writeautomaticsub=True,
                                       subtitleslangs=[lang], subtitlesformat='vtt/srt/best')) as y:
            i = y.extract_info(url, download=False)
            s = (i.get('requested_subtitles') or {}).get(lang)
            if s and s.get('url'):
                text = vtt_text(y.urlopen(s['url']).read().decode('utf8', 'replace'))
                if len(text) > 40:
                    return text, f'subtitles ({lang})'
    if info['duration'] > CFG.get('transcribe_max_minutes', 90) * 60:
        return '', 'too long to transcribe'
    os.makedirs(MEDIA, exist_ok=True)
    with yt_dlp.YoutubeDL(ydl_opts(format='bestaudio/best', outtmpl=os.path.join(MEDIA, '%(id)s.%(ext)s'))) as y:
        f = y.prepare_filename(y.extract_info(url, download=True))
    try:
        from faster_whisper import WhisperModel  # decodes audio itself (PyAV): no ffmpeg needed
        # ponytail: CPU int8 on purpose (the laptop has powered off under GPU load); "whisper_device": "cuda" if that changes
        m = WhisperModel(CFG.get('whisper_model', 'small'), device=CFG.get('whisper_device', 'cpu'),
                         compute_type='int8' if CFG.get('whisper_device', 'cpu') == 'cpu' else 'float16')
        hint = re.sub(r'#\w+|\s+', ' ', info.get('description') or '')[:200].strip() or None
        a = audio_16k(f)
        segs, meta = m.transcribe(a, vad_filter=True, initial_prompt=hint)
        text = ' '.join(s.text.strip() for s in segs)
        lang = CFG.get('whisper_language', 'en')
        if meta.language != lang and meta.language_probability < 0.6:  # short clips over music get a wrong guess
            log(f'  speech: {meta.language} only {meta.language_probability:.0%} sure, reading it as {lang}')
            segs, meta = m.transcribe(a, vad_filter=True, initial_prompt=hint, language=lang)
            text = ' '.join(s.text.strip() for s in segs)
        return text, f'speech to text ({meta.language})'
    finally:
        try:
            os.remove(f)
        except OSError:
            pass


LOGIN_SITES = {  # site: (cookie domain, the cookie that means "logged in", why it matters)
    'Instagram': ('instagram.com', 'sessionid', 'Reels and posts need a login.'),
    'X (Twitter)': ('x.com', 'auth_token', 'Most posts need a login.'),
    'TikTok': ('tiktok.com', 'sessionid', 'No login needed for public videos; private ones need it.'),
    'Facebook': ('facebook.com', 'c_user', 'Most videos need a login.'),
    'YouTube': ('youtube.com', 'SID', 'No login needed, except for age-restricted or members-only videos.'),
}


def cookie_rows(text):
    """Netscape cookies.txt -> [(domain, name, expiry, line)]; the #HttpOnly_ prefix is part of the format."""
    out = []
    for line in text.splitlines():
        p = line.split('\t')
        if len(p) == 7 and (not line.startswith('#') or line.startswith('#HttpOnly_')):
            out.append((p[0].replace('#HttpOnly_', '').lstrip('.').lower(), p[5], int(p[4]) if p[4].isdigit() else 0, line))
    return out


def logins():
    """Which sites cookies.txt logs in to, and until when. Never the cookie values."""
    rows = cookie_rows(open(COOKIES, encoding='utf8', errors='replace').read()) if os.path.exists(COOKIES) else []
    out = []
    for site, (dom, name, why) in LOGIN_SITES.items():
        exp = [e for d, n, e, _ in rows if n == name and (d == dom or d.endswith('.' + dom))]
        e = max(exp) if exp else None
        out.append({'site': site, 'why': why, 'state': 'off' if e is None else 'expired' if e and e < time.time() else 'on',
                    'until': dt.date.fromtimestamp(e).isoformat() if e else ''})
    return out


def settings_status():
    u = search_usage()
    out = {'search': [], 'left': search_left()}
    for p, c in search_cfg().items():
        k, src = search_key(p)
        out['search'].append({'id': p, 'name': c['name'], 'about': c['about'], 'site': c['site'], 'on': c['on'], 'per_day': c['per_day'],
                              'needs_key': c['key'], 'key_from': src if k else '', 'used': u.get(p, 0),
                              'left': provider_left(p), 'last': u.get('status', {}).get(p)})
    try:
        import yt_dlp
        v = yt_dlp.version.__version__
        age = (dt.date.today() - dt.date(*map(int, v.split('.')[:3]))).days
    except Exception:
        v, age = '', None
    try:
        import faster_whisper  # noqa: F401
        fw = True
    except ImportError:
        fw = False
    model = CFG.get('whisper_model', 'small')
    hub = os.path.join(H, '.cache', 'huggingface', 'hub', f'models--Systran--faster-whisper-{model}')
    out['social'] = {'logins': logins(), 'cookies': os.path.exists(COOKIES), 'ytdlp': v, 'ytdlp_age': age,
                     'whisper': {'installed': fw, 'model': model, 'downloaded': os.path.isdir(hub), 'device': CFG.get('whisper_device', 'cpu'),
                                 'max_minutes': CFG.get('transcribe_max_minutes', 90)}}
    return out


# ---------------------------------------------------------------- phone: a private Telegram bot that queues links and topics

PHONE = os.path.join(STATE, 'phone.json')  # {owner, code, offset, bot, queue: [...], last: {...}}; the token lives in keys.json
PHONE_LOCK = threading.Lock()
PHONE_HELP = ('Send me a link (YouTube, Instagram, TikTok, X, an article, a picture), a photo or a topic, and it waits in MyWiki > Research.\n'
              '/list  what is waiting\n/research  research everything waiting and make notes\n/note  make notes from the links only, no web search')


def tg(method, http_timeout=20, **params):
    import urllib.request
    token = load_json(KEYS, {}).get('telegram')
    if not token:
        raise RuntimeError('no Telegram token')
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}', json.dumps(params).encode(), {'Content-Type': 'application/json'})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=http_timeout))
    except urllib.error.HTTPError as e:
        d = json.loads(e.read() or b'{}') or {'description': str(e)}
        d.setdefault('error_code', e.code)
    if not d.get('ok'):
        raise RuntimeError(f"{d.get('error_code', '')} {d.get('description', 'Telegram said no')}".strip())
    return d['result']


def phone_state():
    st = load_json(PHONE, {})
    if not st.get('code'):
        import secrets
        st['code'] = f'{secrets.randbelow(900000) + 100000}'
        save_json(PHONE, st)
    return st


def phone_say(text):
    """Message the paired phone. Never fails the caller."""
    owner = load_json(PHONE, {}).get('owner')
    if owner:
        try:
            tg('sendMessage', chat_id=owner, text=text[:4000], disable_web_page_preview=True)
        except Exception as e:
            log('  phone: could not send', str(e)[:120])


UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36',
      'Accept-Language': 'en'}
PHOTO_RX = re.compile(r'photo:[\w.-]+')  # a picture sent from the phone, kept in state/media/phone


def is_link(u):
    return bool(URL_RX.fullmatch(u) or PHOTO_RX.fullmatch(u))


def page_read(url, budget=6000):
    """A web page read here: title, caption, main text and its pictures. A direct image link is just a picture."""
    import html.parser, urllib.request, urllib.parse
    host = (urllib.parse.urlparse(url).hostname or 'web').removeprefix('www.')
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20)
    if r.headers.get_content_type().startswith('image/'):
        return {'title': os.path.basename(urllib.parse.urlparse(url).path) or url, 'site': host, 'caption': '', 'text': '', 'pics': [url]}
    raw = r.read(3_000_000).decode(r.headers.get_content_charset() or 'utf8', 'replace')
    SKIP = ('script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'form', 'aside', 'button')

    class P(html.parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.skip, self.text, self.meta, self.title, self.intitle = 0, [], {}, '', False

        def handle_starttag(self, t, a):
            a = dict(a)
            if t == 'meta' and a.get('content'):
                self.meta.setdefault((a.get('property') or a.get('name') or '').lower(), a['content'])
            elif t in SKIP:
                self.skip += 1
            elif t == 'title':
                self.intitle = True

        def handle_endtag(self, t):
            if t in SKIP and self.skip:
                self.skip -= 1
            elif t == 'title':
                self.intitle = False

        def handle_data(self, d):
            if self.intitle:
                self.title += d
            elif not self.skip and d.strip():  # ponytail: menus outside nav/header/footer come along; fine for a model
                self.text.append(' '.join(d.split()))
    h = P()
    h.feed(raw)
    m = h.meta
    pics = list(dict.fromkeys(urllib.parse.urljoin(url, m[k]) for k in ('og:image', 'og:image:url', 'twitter:image') if m.get(k)))
    return {'title': ' '.join((m.get('og:title') or h.title).split()), 'site': host, 'caption': m.get('og:description') or m.get('description') or '',
            'text': ' '.join(h.text)[:budget], 'pics': pics}


def save_pic(src, dest):
    """A picture (link or file) saved as a small jpg the model can open; None when it cannot be read."""
    import io, urllib.request
    from PIL import Image
    try:
        raw = open(src, 'rb').read() if os.path.isfile(src) else urllib.request.urlopen(urllib.request.Request(src, headers=UA), timeout=30).read(15_000_000)
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        im.thumbnail((1024, 1024))
        im.save(dest, quality=85)
        return dest
    except Exception as e:
        log('  picture not read:', str(src)[:80], str(e)[:100])


def stills(path, out, n):
    """n frames spread through a video, where on-screen text and the things shown live."""
    import av
    got = []
    with av.open(path) as c:
        s = c.streams.video[0]
        total = float(s.duration * s.time_base) if s.duration else (c.duration or 0) / 1e6
        for k in range(n if total else 1):
            c.seek(int(total * (k + .5) / n / s.time_base), stream=s)
            f = next(c.decode(s), None)
            if f is not None:
                im = f.to_image()
                im.thumbnail((768, 768))
                im.save(os.path.join(out, f'still{k}.jpg'), quality=85)
                got.append(os.path.join(out, f'still{k}.jpg'))
    return got


def collect(u):
    """Everything a link holds: its words (caption, page text, speech) and its pictures (images, video stills) as jpg files."""
    import yt_dlp
    d = os.path.join(MEDIA, slug(u.split('://')[-1], 80))
    os.makedirs(d, exist_ok=True)
    g = {'url': u, 'title': u, 'site': '', 'channel': '', 'date': '', 'duration': 0, 'description': '', 'tags': [], 'chapters': [],
         'transcript': '', 'text': '', 'how': '', 'dir': d}
    shots = []
    if PHOTO_RX.fullmatch(u):
        f = os.path.join(MEDIA, 'phone', u[6:])
        cap = open(f + '.txt', encoding='utf8').read() if os.path.exists(f + '.txt') else ''
        g.update(title=cap[:80] or 'Photo from your phone', site='photo', description=cap, how='a photo')
        src = [f]
    else:
        try:
            info = link_info(u)
        except Exception as e:
            if 'needs a login' in plain_ydl_error(e):
                raise
            info = None
        if info:
            g.update(info, how='caption and pictures')
            src = info['pics']
            if info['video']:
                step('research', f'{info["title"][:60]} · getting the words')
                g['transcript'], g['how'] = transcript(u, info)
                if info['duration'] <= CFG.get('stills_max_minutes', 20) * 60:
                    step('research', f'{info["title"][:60]} · taking stills')
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts(format='bv*[height<=480]/wv*/b', outtmpl=os.path.join(d, 'video.%(ext)s'))) as y:
                            v = y.prepare_filename(y.extract_info(u, download=True))
                        shots = stills(v, d, CFG.get('stills', 6))
                        os.remove(v)
                    except Exception as e:
                        log('  no stills:', plain_ydl_error(e)[:150])
        else:  # not a post yt-dlp knows: a page or a picture, read here
            pg = page_read(u)
            g.update(title=pg['title'] or u, site=pg['site'], description=pg['caption'], text=pg['text'],
                     how='article' if len(pg['text']) > 300 else 'a picture' if pg['pics'] else 'a page')
            src = pg['pics']
    pics = [p for n, x in enumerate(src[:CFG.get('pics_max', 4)]) if (p := save_pic(x, os.path.join(d, f'pic{n}.jpg')))]
    g['pics'] = pics + shots
    return g


def words(g, budget):
    """The post's words for a prompt: details and caption first, then page text, then speech squeezed to fit."""
    head = (f"Title: {g['title']}\nSite: {' · '.join(x for x in (g['site'], g['channel'], g['date']) if x)}"
            + (f" · {g['duration'] // 60} min" if g['duration'] else '') + f"\nLink: {g['url']}\n"
            + (f"Chapters: {'; '.join(g['chapters'])}\n" if g['chapters'] else '') + (f"Tags: {', '.join(g['tags'])}\n" if g['tags'] else '')
            + (f"Caption:\n{g['description'][:1500]}\n" if g['description'] else '') + (f"\nPage text:\n{g['text']}\n" if g['text'] else ''))
    said = g['transcript']
    left = budget - len(head) - 60
    if len(said) > left:
        said = said[:max(left, 0) * 2 // 3] + ' [...] ' + said[-max(left, 0) // 3:] if left > 300 else ''
    return (head + (f"\nWhat is said ({g['how']}):\n{said}" if said else ''))[:budget]


def sources(queries, budget=7500):
    """Search the queries and read the best pages here; numbered [1].. for the notes step."""
    seen, rows = set(), []
    for q in queries[:2]:
        for r in search(q, 'web', None, 6)[1]:
            u = r.get('url') or ''
            if u and u not in seen and not re.search(r'instagram\.|tiktok\.|facebook\.|youtube\.|youtu\.be|//x\.com|twitter\.', u):
                seen.add(u)
                rows.append(r)
    out = []
    for r in rows:
        if len(out) == 3:
            break
        try:
            pg = page_read(r['url'], budget // 3)
        except Exception:
            continue
        if len(pg['text']) >= 400:
            out.append(f"[{len(out) + 1}] {pg['title'] or r.get('title')} | {r['url']}\n{pg['text']}")
    if not out and rows:  # no page could be read: the search snippets at least
        return evidence({'results': rows}, budget)[0]
    return '\n\n'.join(out)


def fits(build, body):
    """Shrink body until the prompt fits the command line (prompts travel as one argument)."""
    while body and len(subprocess.list2cmdline([build(body)])) > CMD_MAX - 1200:
        body = body[:int(len(body) * .8)]
    return build(body)


def block(out, tag):
    m = re.search(rf'<{tag}>(.*?)</{tag}>', out, re.S)
    return m.group(1).strip() if m else ''


def article_title(url):
    import html, urllib.request, urllib.parse
    host = (urllib.parse.urlparse(url).hostname or 'web').removeprefix('www.')
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15).read(300000).decode('utf8', 'replace')
        t = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S | re.I)
        return (html.unescape(re.sub(r'\s+', ' ', t.group(1))).strip() if t else '') or url, host
    except Exception:
        return url, host


def describe(url):
    """(title, site, kind) for a queued link: a post yt-dlp can read, or an article the model opens itself."""
    try:
        i = link_info(url)
        return i['title'], i['site'] or 'post', 'post'
    except Exception as e:
        if 'needs a login' in plain_ydl_error(e):
            return url, 'needs a login', 'post'
        t, host = article_title(url)
        return t, host, 'article'


def phone_photo(p, caption):
    """A picture sent to the bot, saved under media/phone; its caption beside it. Returns its photo: link."""
    import urllib.request
    f = tg('getFile', file_id=p['file_id'])
    os.makedirs(os.path.join(MEDIA, 'phone'), exist_ok=True)
    name = re.sub(r'[^\w-]', '', p['file_unique_id']) + '.jpg'
    dest = os.path.join(MEDIA, 'phone', name)
    url = f"https://api.telegram.org/file/bot{load_json(KEYS, {})['telegram']}/{f['file_path']}"
    open(dest, 'wb').write(urllib.request.urlopen(url, timeout=60).read())
    if caption:
        open(dest + '.txt', 'w', encoding='utf8').write(caption)
    return 'photo:' + name


def phone_queue(drop=()):
    with PHONE_LOCK:
        st = phone_state()
        if drop:
            st['queue'] = [q for q in st.get('queue', []) if q['text'] not in drop]
            save_json(PHONE, st)
        return st.get('queue', [])


def phone_poll(wait=0, on_command=None):
    """Read new Telegram messages once (long-polls up to `wait` s). Only the paired chat is listened to.
    on_command(mode) starts /research or /note and returns the reply; without it (no app) those wait."""
    st = phone_state()
    ups = tg('getUpdates', http_timeout=wait + 15, offset=st.get('offset', 0), timeout=wait, allowed_updates=['message'])
    for u in ups:
        msg = u.get('message') or {}
        chat, text = (msg.get('chat') or {}).get('id'), (msg.get('text') or msg.get('caption') or '').strip()
        with PHONE_LOCK:
            st = phone_state()
            st['offset'] = u['update_id'] + 1
            save_json(PHONE, st)
        doc = msg.get('document') or {}
        pic = (msg.get('photo') or [None])[-1] or (doc if doc.get('mime_type', '').startswith('image/') else None)
        if not chat or not (text or pic):
            continue
        if chat != st.get('owner'):
            if st['code'] in text:
                with PHONE_LOCK:
                    st = phone_state()
                    st.update(owner=chat, code='')
                    save_json(PHONE, st)
                tg('sendMessage', chat_id=chat, text='Linked to MyWiki.\n\n' + PHONE_HELP)
            else:
                tg('sendMessage', chat_id=chat, text='This is a private bot. To link it, send the code shown in MyWiki > Settings > Phone.')
            continue
        cmd = text.split()[0].lower().split('@')[0] if text.startswith('/') and not pic else ''
        if cmd in ('/start', '/help'):
            reply = PHONE_HELP
        elif cmd == '/list':
            q = phone_queue()
            reply = ('Waiting:\n' + '\n'.join(f"• {x['title'][:80]}" + (f" ({x['site']})" if x.get('site') else '') for x in q)) if q else 'Nothing is waiting.'
        elif cmd in ('/research', '/note'):
            reply = on_command(cmd[1:]) if on_command else 'MyWiki is closed. Open it on the PC and this starts there.'
        else:
            urls = URL_RX.findall(text)
            items = []
            for url in urls[:10]:
                title, site, kind = describe(url)
                items.append({'text': url, 'title': title, 'site': site, 'kind': kind})
            if pic:
                items.insert(0, {'text': phone_photo(pic, text), 'title': text[:80] or 'Photo from your phone', 'site': 'photo', 'kind': 'image'})
            elif not urls:
                items = [{'text': text[:120], 'title': text[:120], 'site': '', 'kind': 'topic'}]
            with PHONE_LOCK:
                st = phone_state()
                have = {q['text'] for q in st.get('queue', [])}
                new = [dict(i, at=dt.datetime.now().strftime('%Y-%m-%d %H:%M')) for i in items if i['text'] not in have]
                st['queue'] = st.get('queue', []) + new
                save_json(PHONE, st)
            n = len(st['queue'])
            bad = [i for i in items if i['site'] == 'needs a login']
            reply = ('Saved: ' + '; '.join(i['title'][:70] for i in items) + f'.\n{n} waiting. Send /research to start, or pick them in MyWiki > Research.'
                     + ('\nNote: a link needs a login. Add one in MyWiki > Settings > Social media.' if bad else ''))
        tg('sendMessage', chat_id=chat, text=reply[:4000], disable_web_page_preview=True)
    return len(ups)


def social(mode, *urls):
    """Notes from social posts the user pasted. mode "research": the model reads the post's text and checks it on the web;
    "note": the post's own text only. Then one Write, one connection, a source page per post, one save."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    topic = urls[0] if len(urls) == 1 else f'{len(urls)} links'
    progress('research', total=len(urls), unit=urls[0])
    p = load_json(PROGRESS, {})
    p.update(kind='social', topic=topic)
    save_json(PROGRESS, p)
    got, lock = {}, threading.Lock()
    for u in urls:  # one at a time: speech to text and video stills keep the CPU busy
        progress('research', done=len(got), total=len(urls), unit=u)
        try:
            g = got[u] = collect(u)
            log(f"social: {g['title'][:60]} · {g['how']} · {len(g['transcript']) + len(g['text'])} chars · {len(g['pics'])} pictures")
        except Exception as e:
            got[u] = None
            log('social: failed', u, plain_ydl_error(e))
    progress('research', 'done', len(urls), len(urls))
    posts = [g for g in got.values() if g]
    if not posts:
        progress('research', 'failed', 0, len(urls))
        raise RuntimeError('could not read any of the links')
    rules = rules_text()
    progress('brief', total=len(posts), unit=posts[0]['title'][:60])
    names, done = {}, [0]

    def pictures(g):
        return '\n'.join(p.replace('\\', '/') for p in g['pics']) or '(none)'

    def one(g):
        about, src = '', ''
        if mode == 'research':  # look at everything first, then search for what the post is really about
            ok, out = run_agent('extract', fits(lambda w: prompt('look', tools=LOOK_TOOLS if g['pics'] else NO_TOOLS, pictures=pictures(g), evidence=w),
                                                words(g, 20000)), CFG.get('extract_timeout', 900))
            about, g['topic'] = block(out, 'about'), block(out, 'title')
            queries = [q.strip(' -•"') for q in block(out, 'queries').splitlines() if q.strip()] or [g.get('topic') or g['title']]
            src = sources(queries)
            log(f"  looked: {g.get('topic') or '?'} · searched {queries[:2]} · {src.count(chr(10) + '[') + bool(src)} sources")
        tools = (NO_TOOLS if src else SEARCH_TOOLS) if mode == 'research' else LOOK_TOOLS if g['pics'] else NO_TOOLS
        check = '' if mode == 'note' else (
            'Check its main claims and fill in the background from the sources below.' if src else
            'Check its main claims and fill in the background: search the web for two to four good sources on the topic.')
        extra = (f"\n\n=== What the pictures and words show (looked at already) ===\n{about}" if about else '') + (
            f"\n\n=== Sources ===\n{src}" if src else '')
        pics = '(described above)' if mode == 'research' else pictures(g)
        ok, out = run_agent('extract', fits(lambda w: prompt('social', tools=tools, check=check, rules=rules[0], taxonomy=rules[1], pictures=pics,
                                                             evidence=w + extra), words(g, 20000)), CFG.get('extract_timeout', 900))
        cards = block(out, 'cards')
        with lock:
            done[0] += 1
            progress('brief', done=done[0], total=len(posts), unit=g['title'][:60])
            g['summary'], g['about'] = block(out, 'summary'), about
            g['topic'] = block(out, 'title') or g.get('topic') or g['title']
            if not ok or not cards or cards.upper() == 'NONE':
                log('social: no cards for', g['url'], out[-200:].replace('\n', ' '))
                return
            name = f"{dt.date.today()}__social__{slug(g['topic'], 40)}__{int(time.time())}{done[0]}.md"
            os.makedirs(INBOX, exist_ok=True)
            open(os.path.join(INBOX, name), 'w', encoding='utf8', newline='\n').write(
                cards + f"\n\n<!-- from: {g['title']} · {g['channel']} · {g['url']} -->\n")
            names[name] = g

    try:
        with ThreadPoolExecutor(CFG.get('extract_workers', 4)) as pool:
            list(pool.map(one, posts))
    finally:
        for g in posts:  # ponytail: pictures are thrown away once read; phone photos stay in media/phone
            shutil.rmtree(g['dir'], ignore_errors=True)
    progress('brief', 'done', len(posts), len(posts))
    res = integrate(only=list(names)) if names else {}
    finish('integrate')
    if any(res.values()):
        connect(1)
    os.makedirs(NEWS_DIR, exist_ok=True)
    for name, g in names.items():
        notes_md = [f"- [[{os.path.basename(c.split(':', 1)[-1].strip())[:-3]}]]" for c in res.get(name, []) if c.strip().endswith('.md')]
        page = os.path.join(NEWS_DIR, re.sub(r'[\\/:*?"<>|#\[\]]', '', g['topic']).strip()[:60] + '.md')
        open(page, 'w', encoding='utf8', newline='\n').write('\n'.join(
            ['---', f'title: "{g["topic"][:120].replace(chr(34), "")}"', f'topic: "{g["topic"][:120].replace(chr(34), "")}"',
             f'last_researched: {dt.date.today()}', f'source_url: {g["url"]}', 'publish: false', '---',
             f'# {g["topic"]}', f'_{g["title"][:100]} · {g["site"]} · {g["channel"]} · {g["date"]} · [open]({g["url"]}) · read from {g["how"]}, {len(g["pics"])} pictures_', '',
             f'## {dt.date.today()} · {"researched" if mode == "research" else "noted"}', '', g['summary'] or '(no summary)', '',
             *(['### Notes written', '', *notes_md, ''] if notes_md else []),
             *(['### What it shows', '', g['about'], ''] if g.get('about') else []),
             '### What is said', '', '> [!quote]- Transcript', '> ' + (g['transcript'][:30000] or g['description'][:3000] or '(none)'), '']))
    progress('save')
    catalog()
    dashboard()
    git_commit(f'social: notes from {len(names)} links' if len(names) != 1 else f'social: note from "{posts[0].get("topic", posts[0]["title"])[:60]}"')
    finish('save')
    log(f'social: {len(urls)} links -> {sum(len(v) for v in res.values())} notes')
    made = sorted({os.path.basename(c.split(':', 1)[-1].strip())[:-3] for v in res.values() for c in v if c.strip().endswith('.md')})
    phone_say(f'MyWiki: {len(names)} of {len(urls)} links became notes.' + (' Notes: ' + ', '.join(made[:12]) if made else '')
              + (f'\n{len(urls) - len(posts)} could not be read; see MyWiki > Now.' if len(posts) < len(urls) else ''))


def evidence(data, budget):
    rows = sorted(data.get('results', []), key=lambda r: -(r.get('relevance_score') or 0))
    out, used = [], 0
    for n, r in enumerate(rows, 1):
        eng = ', '.join(f'{k} {v}' for k, v in (r.get('engagement') or {}).items() if v)
        line = (f"[{n}] {(r.get('title') or '')[:200]} | {r.get('source')} | {r.get('published_at') or ''} | {eng}\n"
                f"    {(r.get('summary') or '')[:400]}")
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line)
    return '\n'.join(out), rows[:len(out)]


def news(topic, days=30, depth='quick'):
    topic = topic.strip()
    progress('research', unit=topic)
    fixed, bnews = search(topic, 'news', 'pm' if days <= 31 else 'py')  # also fixes spelling: "Autoimmune Deases" -> autoimmune diseases
    if fixed and fixed.lower() != topic.lower():
        log(f'news: reading "{topic}" as "{fixed}"')
        topic = fixed[:1].upper() + fixed[1:]
    p = load_json(PROGRESS, {})
    p['topic'] = topic
    save_json(PROGRESS, p)
    log(f'news: researching "{topic}" ({days} days, {depth})')
    try:
        data = research(topic, days, depth)
    except RuntimeError as e:  # last30days down: Brave alone still gives a run
        log('  ' + str(e)[:300])
        data = {'results': [], 'source_status': {'last30days': 'failed'}}
    # ponytail: job boards are never news; fewer than 6 real items -> Brave news for the window, then the latest of any age
    data['results'] = [r for r in data.get('results', []) if r.get('source') != 'jobs']
    st = data.setdefault('source_status', {})
    st.pop('jobs', None)
    if len(data['results']) < 6:
        data['results'] += bnews
        st['brave'] = f'{len(bnews)} news'
    if len(data['results']) < 6:
        older = search(topic, 'news', None)[1] + search(topic, 'web', None, 10)[1]
        data['results'] += older
        data['older'] = True
        st['brave'] = f'{len(bnews)} news + {len(older)} latest of any age'
    seen = set()
    data['results'] = [r for r in data['results'] if not (r.get('url') in seen or seen.add(r.get('url')))]
    os.makedirs(os.path.join(STATE, 'news'), exist_ok=True)
    save_json(os.path.join(STATE, 'news', f'{slug(topic, 40)}-{dt.date.today()}.json'), data)
    progress('research', 'done', len(data.get('results', [])), len(data.get('results', [])))
    progress('brief', unit='reading what people said')
    rules = rules_text()
    budget = 20000
    while True:  # the prompt travels on the command line; shrink the evidence until it fits
        ev, used = evidence(data, budget)
        text = prompt('news', topic=topic, days=str(days), rules=rules[0], taxonomy=rules[1], evidence=ev or '(no results)',
                      tools=NO_TOOLS if len(data['results']) >= 6 or search_left() > 0 else
                      SEARCH_TOOLS + ' Little evidence came with this message and the web searches for today are used up: search the web yourself for the latest on the topic, read the best sources, and number them after the evidence below.',
                      older='Little was found inside the window, so the evidence also holds the latest items of any age. Say so in one sentence, then cover what is latest.' if data.get('older') else '')
        if len(subprocess.list2cmdline([text])) <= CMD_MAX or budget < 2000:
            break
        budget -= 2500
    ok, out = run_agent('extract', text, CFG.get('extract_timeout', 600))
    brief = re.search(r'<brief>(.*?)</brief>', out, re.S)
    if not ok or not brief:
        progress('brief', 'failed', 0, 1)
        raise RuntimeError('news brief failed: ' + out[-300:])
    themes = re.search(r'<themes>(.*?)</themes>', out, re.S)
    cards = re.search(r'<cards>(.*?)</cards>', out, re.S)
    progress('brief', 'done', 1, 1)
    changed = []
    if cards and cards.group(1).strip().upper() != 'NONE':
        name = f"{dt.date.today()}__news__{slug(topic, 40)}__{int(time.time())}.md"
        os.makedirs(INBOX, exist_ok=True)
        open(os.path.join(INBOX, name), 'w', encoding='utf8', newline='\n').write(cards.group(1).strip() + '\n')
        changed = integrate(only=[name]).get(name, [])
        finish('integrate')
    if changed:
        connect(1)
    sources = [f"{n}. [{(r.get('title') or r.get('url'))[:140].replace(']', ')')}]({r.get('url')}) · {r.get('source')} · {r.get('published_at') or ''}"
               for n, r in enumerate(used, 1)]
    notes_md = [f"- [[{os.path.basename(c.split(':', 1)[-1].strip())[:-3]}]]" for c in changed if c.split(':', 1)[-1].strip().endswith('.md')]
    section = [f'## {dt.date.today()} · last {days} days', '', brief.group(1).strip(), '',
               *(['### Themes', '', themes.group(1).strip(), ''] if themes else []),
               *(['### Notes written', '', *notes_md, ''] if notes_md else []), '### Sources', '', *sources, '']
    os.makedirs(NEWS_DIR, exist_ok=True)
    page = os.path.join(NEWS_DIR, re.sub(r'[\\/:*?"<>|]', '', topic).strip()[:60] + '.md')
    old = FM_RX.sub('', open(page, encoding='utf8').read(), 1) if os.path.exists(page) else ''
    old = re.sub(r'\A# .*?\n(_.*?_\n)?\s*', '', old)
    status = ', '.join(f'{k} {v}' for k, v in (data.get('source_status') or {}).items())
    open(page, 'w', encoding='utf8', newline='\n').write('\n'.join(
        ['---', f'title: {topic}', f'topic: {topic}', f'last_researched: {dt.date.today()}', 'publish: false', '---',
         f'# {topic}', f'_Researched with last30days · sources: {status}_', '', *section]) + ('\n' + old if old else ''))
    progress('save')
    catalog()
    dashboard()
    git_commit(f'news: {topic}')
    finish('save')
    log(f'news: "{topic}" done, {len(used)} sources, {len(changed)} notes')


def next_unit():
    for d, stage in ((STAGED, 'extract'), (INBOX, 'integrate')):
        fs = sorted((f for f in os.listdir(d) if f.endswith('.md')), reverse=True) if os.path.isdir(d) else []
        if fs:
            p = os.path.join(d, fs[0])
            print(f'stage: {stage}\nunit: {p}')
            if stage == 'extract':
                print(f'write cards to: {cards_target(fs[0])}  (skip writing if the chat holds no durable knowledge)')
            print(f'then run: python "{os.path.abspath(__file__)}" done "{p}"')
            print(f'pending: {len(os.listdir(STAGED)) if os.path.isdir(STAGED) else 0} to extract, '
                  f'{len([f for f in os.listdir(INBOX) if f.endswith(".md")]) if os.path.isdir(INBOX) else 0} to integrate')
            return
    print('nothing pending')


def done_unit(p):
    p = os.path.abspath(p)
    name = os.path.basename(p)
    if os.path.dirname(p) == os.path.abspath(STAGED):
        os.remove(p)
    elif os.path.dirname(p) == os.path.abspath(INBOX):
        os.remove(p)
        record(name, ['(in-chat)'])
        catalog()
    else:
        sys.exit('not a pending unit: ' + p)
    print('done:', name)



# ---------------------------------------------------------------- models through an API: one OpenAI-compatible client, its own file tools

API = {  # id: (name, base url, 'key' | 'local' | 'custom', where to get a key or the app)
    'anthropic': ('Anthropic', 'https://api.anthropic.com/v1', 'key', 'https://console.anthropic.com/settings/keys'),
    'openai': ('OpenAI', 'https://api.openai.com/v1', 'key', 'https://platform.openai.com/api-keys'),
    'google': ('Google Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai', 'key', 'https://aistudio.google.com/apikey'),  # not "gemini": that is the CLI's id
    'openrouter': ('OpenRouter', 'https://openrouter.ai/api/v1', 'key', 'https://openrouter.ai/keys'),
    'groq': ('Groq', 'https://api.groq.com/openai/v1', 'key', 'https://console.groq.com/keys'),
    'deepseek': ('DeepSeek', 'https://api.deepseek.com/v1', 'key', 'https://platform.deepseek.com/api_keys'),
    'ollama': ('Ollama', 'http://127.0.0.1:11434/v1', 'local', 'https://ollama.com/download'),
    'lmstudio': ('LM Studio', 'http://127.0.0.1:1234/v1', 'local', 'https://lmstudio.ai'),
    'custom': ('Other (OpenAI-compatible)', '', 'custom', ''),
}


def api_base(pid):
    return ((CFG.get('providers') or {}).get(pid, {}).get('base') or API[pid][1]).rstrip('/')


def api_call(pid, path, body=None, timeout=60):
    import urllib.request, urllib.error
    key = load_json(KEYS, {}).get('llm:' + pid, '')
    h = {'Content-Type': 'application/json', 'User-Agent': 'MyWiki'}
    if key:  # Anthropic reads x-api-key on its own endpoints; the others ignore it
        h.update({'Authorization': 'Bearer ' + key, 'x-api-key': key, 'anthropic-version': '2023-06-01'})
    req = urllib.request.Request(api_base(pid) + path, json.dumps(body).encode() if body is not None else None, h)
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf8', 'replace')
        try:
            j = json.loads(raw)
            j = j[0] if isinstance(j, list) else j
            msg = (j.get('error') or {}).get('message') if isinstance(j.get('error'), dict) else j.get('error') or j.get('message')
        except ValueError:
            msg = raw[:200]
        raise RuntimeError(f'{e.code} {msg or e.reason}') from None
    except OSError as e:
        raise RuntimeError(f'cannot reach {api_base(pid)}: {getattr(e, "reason", e)}') from None


def api_models(pid):
    d = api_call(pid, '/models', timeout=20)
    rows = d.get('data') or d.get('models') or []
    return sorted(({'id': (m.get('id') or m.get('name', '')).removeprefix('models/'), 'name': m.get('display_name') or m.get('name') or ''}
                   for m in rows if m.get('id') or m.get('name')), key=lambda m: m['id'])


AGENT_TOOLS = [
    ('read_file', 'Read a file in the vault (text), or open a picture (jpg/png/webp) to look at it.', {'path': 'path relative to the vault, or absolute inside it'}),
    ('list_files', 'List vault files matching a glob pattern, e.g. wiki/**/*.md', {'pattern': 'glob relative to the vault'}),
    ('search_files', 'Find lines containing some text in the vault notes (case-insensitive).', {'text': 'text to find'}),
    ('write_file', 'Create or overwrite a file in the vault.', {'path': 'path relative to the vault', 'content': 'the full new content'}),
    ('edit_file', 'Replace one exact piece of text in a vault file.', {'path': 'path relative to the vault', 'old': 'exact text now in the file', 'new': 'replacement'}),
    ('web_search', 'Search the web; returns titles, links and snippets.', {'query': 'what to search for'}),
    ('web_fetch', 'Read the text of a web page.', {'url': 'the page link'}),
]
TOOL_SPEC = [{'type': 'function', 'function': {'name': n, 'description': d, 'parameters': {
    'type': 'object', 'properties': {k: {'type': 'string', 'description': v} for k, v in ps.items()}, 'required': list(ps)}}} for n, d, ps in AGENT_TOOLS]


def vault_path(path, write=False):
    """A path the API agent may touch: inside the vault only, never .git; None otherwise."""
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(ROOT, path))
    root = os.path.realpath(ROOT)
    if os.path.commonpath([p, root]) != root or (write and os.sep + '.git' in p[len(root):] + os.sep):
        return None
    return p


def agent_tool(name, a, pics):
    import base64
    if name in ('read_file', 'write_file', 'edit_file'):
        p = vault_path(str(a.get('path', '')), write=name != 'read_file')
        if not p:
            return 'Refused: only files inside the vault.'
        if name == 'read_file':
            if not os.path.isfile(p):
                return 'No such file.'
            if p.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                pics.append('data:image/%s;base64,%s' % ('png' if p.lower().endswith('.png') else 'jpeg', base64.b64encode(open(p, 'rb').read()).decode()))
                return 'The picture is attached in the next message.'
            return open(p, encoding='utf8', errors='replace').read()[:60000]
        if name == 'write_file':
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w', encoding='utf8', newline='\n').write(str(a.get('content', '')))
            return 'Written.'
        t = open(p, encoding='utf8').read() if os.path.isfile(p) else ''
        if str(a.get('old', '')) not in t or not a.get('old'):
            return 'The old text is not in the file; read it again.'
        open(p, 'w', encoding='utf8', newline='\n').write(t.replace(str(a['old']), str(a.get('new', '')), 1))
        return 'Edited.'
    if name == 'list_files':
        pat = str(a.get('pattern', '*'))
        out = [os.path.relpath(f, ROOT).replace('\\', '/') for f in glob.glob(os.path.join(ROOT, pat), recursive=True) if vault_path(f)]
        return '\n'.join(sorted(out)[:300]) or 'Nothing matches.'
    if name == 'search_files':
        t, out = str(a.get('text', '')).lower(), []
        for f in glob.glob(os.path.join(WIKI, '**', '*.md'), recursive=True):
            for n, line in enumerate(open(f, encoding='utf8', errors='replace'), 1):
                if t and t in line.lower():
                    out.append(f'{os.path.relpath(f, ROOT)}:{n}: {line.strip()[:200]}')
                    if len(out) >= 60:
                        return '\n'.join(out)
        return '\n'.join(out) or 'Nothing found.'
    if name == 'web_search':
        rows = search(str(a.get('query', '')), 'web', None, 8)[1]
        return evidence({'results': rows}, 4000)[0] + '\n' + '\n'.join(r.get('url') or '' for r in rows) if rows else 'No results.'
    if name == 'web_fetch':
        try:
            pg = page_read(str(a.get('url', '')), 8000)
            return f"{pg['title']}\n{pg['text']}"
        except Exception as e:
            return f'Could not open it: {e}'
    return 'Unknown tool.'


def api_agent(pid, model, prompt, timeout, steps=60):
    """A small agent loop: the model calls the vault and web tools until it answers. Returns (ok, final text)."""
    end, msgs = time.time() + timeout, [{'role': 'user', 'content': prompt}]
    try:
        for _ in range(steps):
            if time.time() > end:
                return False, 'timeout'
            r = api_call(pid, '/chat/completions', {'model': model, 'messages': msgs, 'tools': TOOL_SPEC}, timeout=max(30, end - time.time()))
            m = r['choices'][0]['message']
            msgs.append({'role': 'assistant', 'content': m.get('content') or '', **({'tool_calls': m['tool_calls']} if m.get('tool_calls') else {})})
            if not m.get('tool_calls'):
                return True, m.get('content') or ''
            pics = []
            for c in m['tool_calls']:
                try:
                    args = json.loads(c['function'].get('arguments') or '{}')
                except ValueError:
                    args = {}
                msgs.append({'role': 'tool', 'tool_call_id': c['id'], 'content': agent_tool(c['function']['name'], args, pics)})
            if pics:
                msgs.append({'role': 'user', 'content': [{'type': 'text', 'text': 'The pictures you opened:'},
                                                         *({'type': 'image_url', 'image_url': {'url': u}} for u in pics)]})
        return False, 'the model kept calling tools without answering'
    except (RuntimeError, KeyError, IndexError) as e:
        return False, str(e)


# ---------------------------------------------------------------- the model center: what is installed, what answered

MODELS_STATE = os.path.join(STATE, 'models.json')  # {"<via>|<model>": {ok, msg, at, secs}}


def model_catalog():
    """Every CLI agent (found or not) and every API, with whether it can be used and the last test."""
    keys, tests = load_json(KEYS, {}), load_json(MODELS_STATE, {})
    out = []
    for i, r in CFG['runners'].items():
        path = shutil.which(r['cmd'][0])
        out.append({'id': i, 'kind': 'cli', 'name': r.get('name', i), 'found': bool(path), 'where': path or '', 'site': r.get('site', ''),
                    'sign_in': r.get('sign_in', ''), 'suggest': r.get('suggest', [])})
    for i, (name, base, how, site) in API.items():
        out.append({'id': i, 'kind': how, 'name': name, 'found': bool(keys.get('llm:' + i)) if how == 'key' else bool(api_base(i)),
                    'base': api_base(i), 'site': site, 'key': bool(keys.get('llm:' + i))})
    return {'list': out, 'tests': tests, 'models': CFG.get('models') or {}}


def model_test(via, model):
    """One tiny call; for an API it also proves the endpoint accepts tools. Saved for the model center."""
    t = time.time()
    ok, out = run_agent('test', 'Reply with exactly the word OK and nothing else. Use no tools.', 180, {'via': via, 'model': model})
    ok = ok and 'OK' in out.upper()
    msg = 'Answered in %d s.' % (time.time() - t) if ok else (out.strip().splitlines() or ['no answer'])[-1][:240]
    st = load_json(MODELS_STATE, {})
    st[f'{via}|{model}'] = {'ok': ok, 'msg': msg, 'at': dt.datetime.now().strftime('%Y-%m-%d %H:%M')}
    save_json(MODELS_STATE, st)
    return ok, msg


# ---------------------------------------------------------------- first-run setup: vault, chats, agents' scope

VAULT_FILES = ['INGEST-AUTO.md', 'WIKI.md', 'taxonomy.md', 'wiki/index.md', 'wiki/log.md', '.gitignore']


def vault_files(p):
    return [guide(p) if f == 'WIKI.md' else f for f in VAULT_FILES]


def vault_check(path):
    p = os.path.abspath(os.path.expanduser(path or ''))
    if not path or not os.path.isdir(p):
        return {'path': p, 'exists': False}
    notes = sum(len([f for f in fs if f.endswith('.md')]) for d, _, fs in os.walk(p) if '.git' not in d and '.obsidian' not in d)
    vf = vault_files(p)
    return {'path': p, 'exists': True, 'notes': notes, 'git': os.path.isdir(os.path.join(p, '.git')),
            'obsidian': os.path.isdir(os.path.join(p, '.obsidian')), 'ready': all(os.path.exists(os.path.join(p, f)) for f in vf[:3]),
            'missing': [f for f in vf if not os.path.exists(os.path.join(p, f))]}


def vault_use(path, create=False):
    """Point MyWiki at a vault. Missing rule files come from the template; nothing that exists is overwritten."""
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(p):
        if not create:
            return {'ok': False, 'error': 'That folder does not exist.'}
        os.makedirs(p)
    tpl = os.path.join(APP, 'vault-template')
    for f in VAULT_FILES:
        dst = os.path.join(p, f)
        if f == '.gitignore' and os.path.exists(dst):
            t = open(dst, encoding='utf8').read()
            if '.mywiki/' not in t:
                open(dst, 'a', encoding='utf8', newline='\n').write(('' if t.endswith('\n') else '\n') + '.mywiki/\n')
        elif not os.path.exists(dst) and not (f == 'WIKI.md' and guide(p) == 'devin.md'):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(tpl, f), dst)
    for d in ('wiki/connections', 'wiki/projects', 'raw/inbox', '.mywiki/media'):
        os.makedirs(os.path.join(p, d), exist_ok=True)
    save_cfg({'vault': p})
    devin_scope(p)
    return {'ok': True, **vault_check(p)}


def devin_scope(vault):
    """Devin's headless config: your own Devin settings, with file access limited to the vault."""
    base = next((d for d in (load_json(os.path.join(H, '.config', 'devin', 'config.json'), None),
                             load_json(os.path.join(os.environ.get('APPDATA', H), 'devin', 'config.json'), None)) if d), {})
    v = vault.replace('\\', '/')
    base['permissions'] = {'allow': [f'Read({v}/**)', f'Write({v}/**)']}
    save_json(os.path.join(HOME, 'devin-runner.json'), base)


SOURCE_INFO = {  # id: (name, what it reads)
    'claude-code': ('Claude Code', 'chats in ~/.claude/projects'),
    'devin': ('Devin', 'its local session database'),
    'antigravity': ('Antigravity', 'chats in ~/.gemini/antigravity*'),
    'quire': ('Quire', 'chats in your Quire workspaces'),
    'quire-research': ('Quire research', 'research notes in your Quire workspaces'),
    'claude-ai': ('claude.ai', 'export zips you import (claude.ai has no other way out)'),
}


def sources_found():
    """Which chat sources exist on this computer and roughly how many chats each has. Reads file names only."""
    ws = [os.path.expanduser(w) for w in CFG.get('quire_workspaces', [])]
    counts = {
        'claude-code': len(glob.glob(os.path.join(H, '.claude', 'projects', '*', '*.jsonl'))),
        'devin': sum(os.path.exists(x) for x in DEVIN_DBS),
        'antigravity': sum(len(glob.glob(os.path.join(H, '.gemini', b, 'brain', '*'))) for b in ('antigravity', 'antigravity-cli')),
        'quire': sum(len(glob.glob(os.path.join(w, '.quire', 'sessions', '*.jsonl'))) for w in ws),
        'quire-research': sum(len(glob.glob(os.path.join(w, 'research', '*.md'))) for w in ws),
        'claude-ai': len(glob.glob(os.path.join(EXPORTS, '*.zip'))),
    }
    return [{'id': i, 'name': n, 'reads': r, 'found': counts[i], 'on': bool(CFG['sources'].get(i))} for i, (n, r) in SOURCE_INFO.items()]


# ---------------------------------------------------------------- news topics: chosen at setup, editable in Settings

TOPIC_SUGGESTIONS = {  # name: (search query, freshness, what the model looks for)
    **FEED_SEGMENTS,
    'Technology': ('technology breakthrough', 'pd', 'new technology and engineering (not product launches)'),
    'Climate & environment': ('climate study finds', 'pd', 'climate, ecology and the environment'),
    'Animals & nature': ('researchers discover species', 'pw', 'animals, plants and the natural world'),
    'Physics': ('physicists discover', 'pw', 'physics results'),
    'Economics': ('economists study finds', 'pw', 'economics research and ideas (not markets news)'),
    'Philosophy & ideas': ('philosophy essay', 'pw', 'philosophy and big ideas'),
    'Language': ('linguists language study', 'pw', 'language, linguistics and writing systems'),
    'Books': ('new book review nonfiction', 'pw', 'notable new books and what they argue'),
    'Art & museums': ('museum exhibition art history', 'pw', 'art, museums and art history'),
    'Architecture': ('site:archdaily.com OR site:dezeen.com architecture', 'pw', 'architecture and cities'),
    'Film & music': ('film music culture review', 'pw', 'film, music and their history'),
    'Food & nutrition': ('nutrition study finds', 'pw', 'food science and nutrition'),
    'Programming': ('site:news.ycombinator.com OR site:lwn.net', 'pd', 'software engineering and programming ideas'),
    'Security': ('security researchers discover vulnerability', 'pd', 'cybersecurity research'),
    'Business & startups': ('startup founders lessons', 'pw', 'how companies are built and run'),
}
TOPICS_MAX = 50  # ponytail: a sanity cap only; each topic costs one search and one model call a day, the page shows the total


def searches_per_day():
    """Web searches available a day across the services that are on and usable."""
    return {p: c['per_day'] for p, c in search_cfg().items() if c['on'] and (not c['key'] or search_key(p)[0])}


def topic_row(name):
    """A topic someone typed: search its name; the model looks for news about it."""
    return list(TOPIC_SUGGESTIONS.get(name) or (name, 'pw', name))


# ---------------------------------------------------------------- this computer: start things, the daily run, the app shortcut

def pyw():
    """The windowless Python on Windows, the same Python elsewhere."""
    w = sys.executable.replace('python.exe', 'pythonw.exe')
    return w if os.name == 'nt' and os.path.exists(w) else sys.executable


def launch(args, cwd=None):
    """Start a Python script detached from this one (no console window on Windows)."""
    kw = {'creationflags': NOWIN | 0x00000008} if os.name == 'nt' else {'start_new_session': True}
    return subprocess.Popen([pyw(), *args], cwd=cwd or (ROOT if os.path.isdir(ROOT) else APP), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


TASK = 'MyWiki-Ingest'
CRON_TAG = '# mywiki-daily'


def cron_line(at, job):
    """One short line (cron refuses long ones); the PATH it lacks comes from config, see remember_path."""
    import shlex
    h, m = at.split(':')
    env = f" MYWIKI_HOME={shlex.quote(HOME)}" if os.environ.get('MYWIKI_HOME') else ''
    return f'{int(m)} {int(h)} * * * cd {shlex.quote(ROOT)} &&{env} {shlex.quote(job[0])} {shlex.quote(job[1])} run >/dev/null 2>&1 {CRON_TAG}'


def remember_path():
    """cron runs with PATH=/usr/bin:/bin, where agent CLIs in ~/.local/bin or an npm prefix are missing:
    keep the PATH of the session that set things up, and add it back when started without it."""
    if os.name == 'nt':
        return
    have = (CFG.get('path') or '').split(os.pathsep)
    now = [p for p in os.environ.get('PATH', '').split(os.pathsep) if p and p not in have]
    if now:
        save_cfg({'path': os.pathsep.join([p for p in have if p] + now)})


if os.name != 'nt' and CFG.get('path'):
    os.environ['PATH'] = os.pathsep.join(dict.fromkeys(os.environ.get('PATH', '').split(os.pathsep) + CFG['path'].split(os.pathsep)))


def schedule_set(on, at='11:00'):
    """The daily run: Task Scheduler on Windows (runs late if the PC slept), cron elsewhere."""
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', at):
        return {'ok': False, 'error': 'Give a time like 11:00.'}
    job = [pyw(), os.path.join(APP, 'wiki.py'), 'run']
    if os.name == 'nt':
        if not on:
            subprocess.run(['schtasks', '/delete', '/tn', TASK, '/f'], capture_output=True, creationflags=NOWIN)
        else:
            xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>MyWiki: saved chats to knowledge notes, once a day. Manual run: schtasks /run /tn {TASK}</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>{dt.date.today()}T{at}:00</StartBoundary><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Settings><StartWhenAvailable>true</StartWhenAvailable><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings>
  <Actions><Exec><Command>{job[0]}</Command><Arguments>"{job[1]}" run</Arguments><WorkingDirectory>{ROOT}</WorkingDirectory></Exec></Actions>
</Task>'''
            f = os.path.join(STATE, 'task.xml')
            os.makedirs(STATE, exist_ok=True)
            open(f, 'w', encoding='utf-16').write(xml)
            r = subprocess.run(['schtasks', '/create', '/tn', TASK, '/xml', f, '/f'], capture_output=True, text=True, creationflags=NOWIN)
            os.remove(f)
            if r.returncode:
                return {'ok': False, 'error': (r.stderr or r.stdout).strip()[:300]}
    else:
        cur = subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout if shutil.which('crontab') else None
        if cur is None and not on:
            pass  # nothing to remove
        elif cur is None:
            return {'ok': False, 'error': 'No crontab on this computer. Run "python wiki.py run" daily with your own scheduler.'}
        if cur is not None:
            lines = [l for l in cur.splitlines() if CRON_TAG not in l]
            if on:
                remember_path()
                lines.append(cron_line(at, job))
            r = subprocess.run(['crontab', '-'], input='\n'.join(lines) + '\n', capture_output=True, text=True)
            if r.returncode:
                return {'ok': False, 'error': r.stderr.strip()[:300]}
    save_cfg({'schedule': {'on': bool(on), 'time': at}})
    return {'ok': True}


def schedule_next():
    """When the daily run fires next ("DD-MM-YYYY HH:MM"), or '' when no task or cron line exists.
    Worked out here: schtasks prints dates in the system locale's order."""
    s = CFG.get('schedule') or {}
    if not s.get('on') or not re.fullmatch(r'\d\d:\d\d', s.get('time', '')) or not schedule_target():
        return ''
    now = dt.datetime.now()
    n = now.replace(hour=int(s['time'][:2]), minute=int(s['time'][3:]), second=0, microsecond=0)
    return (n if n > now else n + dt.timedelta(days=1)).strftime('%d-%m-%Y %H:%M')


def schedule_target():
    """The script the daily run starts now (to spot a task still pointing at an old copy)."""
    if os.name == 'nt':
        out = subprocess.run(['schtasks', '/query', '/tn', TASK, '/xml'], capture_output=True, text=True, creationflags=NOWIN).stdout
        m = re.search(r'<Arguments>"?([^"<]+)', out)
        return m.group(1).strip() if m else ''
    return cron_target(subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout if shutil.which('crontab') else '')


def cron_target(crontab):
    m = re.search(r"""(?:'([^']+wiki\.py)'|"([^"]+wiki\.py)"|(\S+wiki\.py)) run .*""" + CRON_TAG, crontab)
    return next(g for g in m.groups() if g) if m else ''


def pick_folder(start=''):
    """The system's folder dialog: the folder, '' when cancelled, None when there is no dialog (the page then takes a typed path)."""
    try:
        if os.name == 'nt':
            ps = ('Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.FolderBrowserDialog;'
                  '$d.Description="Choose your vault folder";$d.ShowNewFolderButton=$true;'
                  + (f'$d.SelectedPath="{start}";' if start and '"' not in start else '') + 'if($d.ShowDialog() -eq "OK"){$d.SelectedPath}')
            return subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', ps], capture_output=True, text=True, timeout=600, creationflags=NOWIN).stdout.strip()
        for cmd in (['zenity', '--file-selection', '--directory', '--title=Choose your vault folder'], ['kdialog', '--getexistingdirectory', start or H],
                    ['osascript', '-e', 'POSIX path of (choose folder with prompt "Choose your vault folder")']):
            if shutil.which(cmd[0]):
                return subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout.strip()
        return None
    except (OSError, subprocess.TimeoutExpired):
        return ''


if __name__ == '__main__':
    a = sys.argv[1:] or ['status']
    num = lambda i: int(a[i]) if len(a) > i else None
    cmd = a[0]
    if cmd not in ('status',) and not os.path.isdir(ROOT):
        sys.exit('No vault yet. Open the MyWiki app (python app.py) and finish setup.')
    if cmd in ('run', 'harvest', 'extract', 'integrate', 'connect', 'migrate', 'news', 'capture', 'social'):  # writers: one at a time, whoever started it
        lock = Lock()
        lock.__enter__()
        atexit.register(lock.__exit__)
    if cmd == 'run': run()
    elif cmd == 'harvest': harvest()
    elif cmd == 'extract': extract(num(1))
    elif cmd == 'integrate': integrate(num(1))
    elif cmd == 'catalog': print(catalog(), 'notes in .meta/catalog.tsv')
    elif cmd == 'migrate' and a[1:2] == ['step0']: migrate_step0()
    elif cmd == 'migrate' and a[1:2] == ['batch']: migrate_batch(num(2) or 1)
    elif cmd == 'status': status()
    elif cmd == 'dashboard': dashboard()
    elif cmd == 'connect': connect(num(1))
    elif cmd == 'consolidate':
        if a[1:2] == ['apply']:
            with Lock():
                try:
                    consolidate(True)
                finally:
                    finish('consolidate')
        else:
            consolidate()
    elif cmd == 'feed': print(len(feed(force=a[1:2] == ['force'])['items']), 'stories in state/feed.json')
    elif cmd == 'capture': capture(*a[1:])
    elif cmd == 'social': social(a[1], *a[2:])
    elif cmd == 'news': news(a[1], int(a[2]) if len(a) > 2 else 30, a[3] if len(a) > 3 else 'quick')
    elif cmd == 'import-export': print(import_export(a[1]))
    elif cmd == 'next': next_unit()
    elif cmd == 'done': done_unit(a[1])
    else: print(__doc__)
