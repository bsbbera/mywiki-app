"""MyWiki app: a local window over the pipeline. Standard library only.

  python app.py              open the app (starts the local server if needed; pythonw on Windows hides the console)
  python app.py --install    add MyWiki to the Start menu and desktop (Windows) or the app menu (Linux)
  python  app.py --serve      server only, no window (for testing)

The server listens on 127.0.0.1 only, answers only its own page (Host check) and only accepts
POSTs carrying the per-start token the page was served with, so other websites cannot drive it.
It quits by itself when the window has been closed for a while and nothing is running.
"""
import ctypes, datetime as dt, importlib, json, os, re, secrets, shutil, struct, subprocess, sys, threading, time, urllib.parse, urllib.request, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PIPE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPE)
import wiki  # noqa: E402  (reads the vault, never writes unless asked)

PORT = int(os.environ.get('MYWIKI_PORT') or 47821)  # a second user on the same machine needs their own
CLOSE_AFTER = 30 * 60  # quit after the window has been gone this long (and nothing runs); a slept window must not lose its server
UI = os.path.join(PIPE, 'app')
TOKEN = secrets.token_urlsafe(24)
NOWIN = wiki.NOWIN
PYW = wiki.pyw()
seen = {'client': 0.0}
cache = {}

CLAUDE_EXPORT_URL = 'https://claude.ai/settings/data-privacy-controls'


# ---------------------------------------------------------------- state the page polls

def alive(pid):
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k = ctypes.windll.kernel32  # os.kill(pid, 0) would terminate the process on Windows
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259


def running():
    p = os.path.join(wiki.STATE, 'run.lock')
    try:
        return alive(int(open(p).read().strip()))
    except (OSError, ValueError):
        return False


def cached(key, secs, fn):
    v = cache.get(key)
    if v and v[1] and time.time() - v[0] < secs:  # empty = the cli failed, ask again next time
        return v[1]
    out = fn()
    cache[key] = (time.time(), out)
    return out


def next_run():
    return wiki.schedule_next()


def titles():
    led = wiki.load_json(wiki.LEDGER, {'sessions': {}})
    out = {}
    for k, v in led['sessions'].items():
        src, _, sid = k.partition(':')
        out[f'{src}__{wiki.slug(sid, 40)}'] = v.get('title') or ''
    return out


SOURCES = {'claude-code': 'Claude Code', 'claude-ai': 'Claude', 'devin': 'Devin', 'antigravity': 'Antigravity',
           'quire': 'Quire', 'quire-research': 'Quire research', 'news': 'News'}


def unit_label(u, names):
    """2026-09-24__claude-code__6986a66c...__876-9.md -> 'ChessPlay · Claude Code · part 10'"""
    parts = u[:-3].split('__') if u.endswith('.md') else []
    if len(parts) < 4:
        return u
    when, src, sid, part = parts[:4]
    title = names.get(f'{src}__{sid}') or ('chat from ' + dt.date.fromisoformat(when).strftime('%d %b'))
    if src == 'news':
        title = sid.replace('-', ' ')
    piece = part.split('-', 1)[1] if '-' in part else ''
    n = re.match(r'\d+', piece)
    return f"{title} · {SOURCES.get(src, src)}" + (f" · part {int(n.group()) + 1}" if n and (int(n.group()) or '-' in piece[len(n.group()):]) else '')


LOG_RX = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\s+(.*)$')


def events(limit=60):
    f = os.path.join(wiki.STATE, 'logs', dt.date.today().strftime('%Y-%m') + '.log')
    try:
        lines = open(f, encoding='utf8', errors='replace').read().splitlines()[-400:]
    except OSError:
        return []
    names, out = titles(), []
    for line in lines:
        m = LOG_RX.match(line)
        if not m:
            continue
        day, t, msg = m.groups()
        ev = {'at': f'{day}T{t}', 'kind': 'info', 'text': msg.strip()}
        if (x := re.match(r'extracted (\S+) (cards|none)', msg.strip())):
            ev.update(kind='extract', unit=x.group(1), text=unit_label(x.group(1), names),
                      note='found knowledge' if x.group(2) == 'cards' else 'nothing worth keeping')
        elif (x := re.match(r'integrated (\S+) (\d+) files', msg.strip())):
            ev.update(kind='integrate', unit=x.group(1), text=unit_label(x.group(1), names),
                      note=f"{x.group(2)} note{'s' if x.group(2) != '1' else ''} written" if x.group(2) != '0' else 'nothing new to add')
        elif (x := re.match(r'failed \((\d)/3\) (\S+)\s*(.*)', msg.strip())):
            ev.update(kind='failed', unit=x.group(2), text=unit_label(x.group(2), names), note=plain_error(x.group(3)),
                      tries=int(x.group(1)))
        elif (x := re.match(r'harvest: (\d+) new units staged, (\d+) tool-run', msg.strip())):
            ev.update(kind='harvest', text=f'Collected {x.group(1)} new chat pieces', note=f'skipped {x.group(2)} tool runs')
        elif (x := re.match(r'migrate batch (\d+): chunk (\d+) -> (\d+) / (\d+)', msg.strip())):
            ev.update(kind='migrate', text=f'Tidied {x.group(3)} older notes', note=f'batch {x.group(1)}, part {x.group(2)}')
        elif msg.startswith('git push'):
            ev.update(kind='save', text='Saved and pushed' if msg[9:].strip() == 'ok' else 'Push failed', note=msg[9:].strip())
        elif msg.startswith('git commit FAILED'):
            ev.update(kind='save', text='Could not save to git', note=msg[18:].strip())
        elif msg.startswith('news:'):
            ev.update(kind='news', text=msg[5:].strip())
        elif msg.startswith('export imported'):
            ev.update(kind='export', text='Imported a claude.ai export', note=msg[16:].strip())
        out.append(ev)
    return out[-limit:]


def plain_error(e):
    e = e.lower()
    if '503' in e or 'unavailable' in e:
        return "The model's service was busy. It will be tried again on the next run."
    if 'timeout' in e:
        return 'Took too long and was stopped. It will be tried again.'
    if 'quota' in e or '429' in e or 'rate' in e:
        return 'Hit a usage limit. It will be tried again later.'
    if 'denied' in e or 'permission' in e:
        return 'The agent asked for a permission it does not have.'
    if 'connection' in e:
        return 'Could not reach the model.'
    return "The model's answer could not be used. It will be tried again."


def eta(stage, st, evs, started):
    """Remaining time from the pace of this run's finished items; None until two have finished."""
    times = [dt.datetime.fromisoformat(e['at']).timestamp() for e in evs if e['kind'] == stage]
    times = [t for t in times if t >= started - 5]
    left = max(0, (st.get('total') or 0) - (st.get('done') or 0))
    if len(times) < 2 or not left:
        return None
    pace = (times[-1] - times[0]) / (len(times) - 1)
    return int(left * pace - (time.time() - times[-1]))


def state():
    p = wiki.load_json(wiki.PROGRESS, {})
    evs = events()
    ls = lambda d: len([f for f in os.listdir(d) if f.endswith('.md')]) if os.path.isdir(d) else 0
    run = running()
    names = titles() if run else {}
    for k, st in p.get('stages', {}).items():
        if st.get('state') == 'running':
            st['eta'] = eta(k, st, evs, p.get('started', 0))
            if st.get('unit', '').endswith('.md'):
                st['label'] = unit_label(st['unit'], names)
            st['reading'] = [unit_label(u, names) for u in st.get('reading', [])]
    cfg = wiki.CFG
    mig = wiki.read_migration() if os.path.exists(wiki.MIGRATION) else []
    mt = [m for m in mig if m['llm'] != 'skip']
    return {
        'running': run, 'progress': p, 'events': evs,
        'queue': {'chats': ls(wiki.STAGED), 'cards': ls(wiki.INBOX), 'failed': ls(wiki.FAILED),
                  'tidy_done': sum(m['llm'] == 'done' for m in mt), 'tidy_total': len(mt)},
        'last_run': wiki.load_json(os.path.join(wiki.STATE, 'last_run.json'), {}),
        'next_run': cached('next', 120, next_run),
        'models': cfg.get('models') or {}, 'setup_done': bool(cfg.get('setup_done')), 'vault': wiki.ROOT if cfg.get('vault') else '',
        'schedule': cfg.get('schedule') or {},
        'limits': {k: cfg.get(k) for k in ('max_extract_per_run', 'max_integrate_per_run', 'extract_workers', 'integrate_workers')},
        'now': time.time(),
    }


def models(cli):
    def get():
        if cli == 'agy':
            try:
                out = subprocess.run([shutil.which('agy') or 'agy', 'models'], capture_output=True, text=True, encoding='utf8',
                                     errors='replace', timeout=60, creationflags=NOWIN).stdout
                return [{'id': a, 'name': b} for a, _, b in (l.partition('\t') for l in out.splitlines()) if b]
            except (OSError, subprocess.TimeoutExpired):
                return []
        if cli == 'devin':
            return devin_models()
        if cli == 'claude':
            return claude_models()
        if cli in wiki.API:
            try:
                return wiki.api_models(cli)
            except Exception:
                return []
        return [{'id': m, 'name': ''} for m in (wiki.CFG['runners'].get(cli) or {}).get('suggest', [])]
    return cached('models:' + cli, 600 if cli in wiki.API else 86400, get)  # devin opens an empty draft session per ask, so ask once a day


def devin_models():
    """devin has no list command, but its ACP server returns the account's full model picker on session/new."""
    import tempfile
    p = subprocess.Popen([shutil.which('devin') or 'devin', 'acp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding='utf8', errors='replace', creationflags=NOWIN)
    threading.Timer(45, p.kill).start()  # it never exits on its own
    try:
        for m in ({'method': 'initialize', 'params': {'protocolVersion': 1, 'clientCapabilities': {}}},
                  {'method': 'session/new', 'params': {'cwd': tempfile.gettempdir(), 'mcpServers': []}}):
            p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 2 if m['method'] == 'session/new' else 1, **m}) + '\n')
        p.stdin.flush()
        for line in p.stdout:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get('id') == 2:
                opts = next((c for c in o.get('result', {}).get('configOptions', []) if c.get('id') == 'model'), {})
                return [{'id': x['value'], 'name': x.get('name', x['value'])} for x in opts.get('options', [])]
    except OSError:
        pass
    finally:
        p.kill()
    return []


def claude_models():
    """claude has no list command at all: aliases, current ids, and any extra options it cached for this account."""
    ids = [('fable', 'Fable (latest)'), ('opus', 'Opus (latest)'), ('sonnet', 'Sonnet (latest)'), ('haiku', 'Haiku (latest)'),
           ('claude-fable-5-1', 'Claude Fable 5.1'), ('claude-opus-5-5', 'Claude Opus 5.5'),
           ('claude-sonnet-5-5', 'Claude Sonnet 5.5'), ('claude-haiku-4-5-20251001', 'Claude Haiku 4.5')]
    extra = wiki.load_json(os.path.join(wiki.H, '.claude.json'), {}).get('additionalModelOptionsCache', [])
    ids += [(x['value'], x.get('description') or x['value']) for x in extra if x.get('value') not in dict(ids)]
    return [{'id': a, 'name': b} for a, b in ids]


# ---------------------------------------------------------------- actions

def tidy_plan():
    p = wiki.load_json(wiki.CONSOLIDATE, {})
    g = p.get('fold', [])
    return {'groups': len(g), 'fold': sum(len(x['sources']) for x in g), 'delete': len(p.get('delete', [])),
            'done': sum(bool(x.get('removed')) for x in g), 'finished': bool(g) and all(x.get('removed') for x in g) and p.get('deleted', False)}


def spawn(*args):
    if running():
        return {'ok': False, 'error': 'A run is already going. Wait for it to finish.'}
    if not os.path.isdir(wiki.ROOT):
        return {'ok': False, 'error': 'Choose a vault first (Settings > Run setup).'}
    wiki.launch([os.path.join(PIPE, 'wiki.py'), *args])
    time.sleep(0.6)
    return {'ok': True}


def save_models(body):
    """The default model and optional per-step ones: {default|extract|integrate: {via, model} | null}."""
    cur = dict(wiki.CFG.get('models') or {})
    for k in ('default', 'extract', 'integrate'):
        if k not in body:
            continue
        c = body[k]
        if c is None:
            cur[k] = None
            continue
        via, m = str(c.get('via', '')), str(c.get('model', '')).strip()
        if via not in wiki.CFG['runners'] and via not in wiki.API:
            return {'ok': False, 'error': 'Unknown agent or service'}
        if not re.fullmatch(r'[\w.:/@+-]{1,120}', m):
            return {'ok': False, 'error': f'"{m}" is not a model id'}
        cur[k] = {'via': via, 'model': m}
    wiki.save_cfg({'models': cur})
    return {'ok': True}


def save_llm(body):
    """An API key (kept in state/keys.json, never sent back) or a server address for a local / custom service."""
    pid = body.get('id')
    if pid not in wiki.API:
        return {'ok': False, 'error': 'Unknown service'}
    if 'base' in body:
        base = str(body['base']).strip().rstrip('/')
        if base and not re.fullmatch(r'https?://[\w.:\[\]-]+(/[\w./-]*)?', base):
            return {'ok': False, 'error': 'Give an address like http://127.0.0.1:11434/v1'}
        prov = dict(wiki.CFG.get('providers') or {})
        prov[pid] = {**prov.get(pid, {}), 'base': base}
        wiki.save_cfg({'providers': prov})
    if 'key' in body or body.get('remove_key'):
        keys = wiki.load_json(wiki.KEYS, {})
        key = str(body.get('key') or '').strip()
        if body.get('remove_key'):
            keys.pop('llm:' + pid, None)
        elif not re.fullmatch(r'[\w.:-]{16,300}', key):
            return {'ok': False, 'error': 'That does not look like a key. Copy it again from the service.'}
        else:
            keys['llm:' + pid] = key
        wiki.save_json(wiki.KEYS, keys)
    return {'ok': True}


def setup_info():
    c = wiki.CFG
    return {'vault': wiki.vault_check(c.get('vault') or ''), 'suggest_vault': os.path.join(wiki.H, 'MyWiki'),
            'sources': wiki.sources_found(), 'personal_terms': c.get('personal_terms', []),
            'quire_workspaces': c.get('quire_workspaces', []), 'topics': list(c.get('feed_segments') or {}),
            'topics_chosen': 'feed_segments' in c, 'suggestions': list(wiki.TOPIC_SUGGESTIONS), 'topics_max': wiki.TOPICS_MAX, 'searches': {wiki.PROVIDERS[k]['name']: v for k, v in wiki.searches_per_day().items()},
            'schedule': c.get('schedule') or {}, 'next_run': wiki.schedule_next(), 'task_target': wiki.schedule_target(),
            'app': os.path.join(PIPE, 'wiki.py'), 'home': wiki.HOME, 'os': 'windows' if os.name == 'nt' else sys.platform, 'done': bool(c.get('setup_done'))}


def save_sources(body):
    on = body.get('on') or {}
    src = {k: bool(on.get(k)) for k in wiki.SOURCE_INFO}
    terms = [t.strip() for t in body.get('personal_terms') or [] if 2 <= len(t.strip()) <= 80][:40]
    wiki.save_cfg({'sources': src, 'personal_terms': terms})
    return {'ok': True}


def save_topics(body):
    names = [re.sub(r'\s+', ' ', str(n)).strip()[:60] for n in body.get('topics') or []]
    names = list(dict.fromkeys(n for n in names if len(n) >= 2))
    if len(names) > wiki.TOPICS_MAX:
        return {'ok': False, 'error': f'Up to {wiki.TOPICS_MAX} topics.'}
    wiki.save_cfg({'feed_segments': {n: wiki.topic_row(n) for n in names}})
    return {'ok': True}


def use_vault(body):
    r = wiki.vault_use(str(body.get('path', '')), bool(body.get('create')))
    if r.get('ok'):
        importlib.reload(wiki)  # every vault path in wiki is set at import
        cache.clear()
    return r


def save_search(body):
    """Turn a search service on or off, set its daily cap, save or remove its key. Keys go to state/keys.json, never back to the page."""
    p = body.get('id')
    if p not in wiki.PROVIDERS:
        return {'ok': False, 'error': 'Unknown search service'}
    if 'key' in body or body.get('remove_key'):
        keys = wiki.load_json(wiki.KEYS, {})
        key = str(body.get('key') or '').strip()
        if body.get('remove_key'):
            keys.pop(p, None)
        elif not re.fullmatch(r'[\w.-]{16,200}', key):
            return {'ok': False, 'error': 'That does not look like a key. Copy it again from the service.'}
        else:
            keys[p] = key
        wiki.save_json(wiki.KEYS, keys)
    search = json.loads(json.dumps(wiki.CFG.get('search') or {}))
    c = search.setdefault(p, {})
    if 'on' in body:
        c['on'] = bool(body['on'])
    if 'per_day' in body:
        n = int(body['per_day'])
        if not 0 <= n <= 500:
            return {'ok': False, 'error': 'Pick 0 to 500 searches a day.'}
        c['per_day'] = n
    wiki.save_cfg({'search': search})
    return {'ok': True}


def test_search(p):
    if p not in wiki.PROVIDERS:
        return {'ok': False, 'error': 'Unknown search service'}
    c, (k, _) = wiki.search_cfg()[p], wiki.search_key(p)
    if c['key'] and not k:
        return {'ok': False, 'error': 'Add a key first.'}
    try:
        n = len(wiki.search_one(p, 'scientists discover', 'news', 'pw', 3)[1])
        return {'ok': True, 'msg': f'Works: {n} results came back.' if n else 'It answered but found nothing. Try again later.'}
    except Exception as e:
        return {'ok': False, 'error': wiki.plain_search_error(e)}


def save_cookies(data, clear=''):
    """Merge an exported cookies.txt into state/cookies.txt (one file can hold several sites), or drop one site's cookies."""
    old = open(wiki.COOKIES, encoding='utf8', errors='replace').read() if os.path.exists(wiki.COOKIES) else ''
    keep = wiki.cookie_rows(old)
    if clear:
        dom = wiki.LOGIN_SITES.get(clear, ('',))[0]
        if not dom:
            return {'ok': False, 'error': 'Unknown site'}
        keep = [r for r in keep if not (r[0] == dom or r[0].endswith('.' + dom))]
    else:
        new = wiki.cookie_rows(data.decode('utf8', 'replace'))
        if not new:
            return {'ok': False, 'error': 'That is not a cookies.txt file. Export it in "Netscape" format.'}
        seen = {(d, n) for d, n, _, _ in new}
        keep = [r for r in keep if (r[0], r[1]) not in seen] + new
    os.makedirs(wiki.STATE, exist_ok=True)
    with open(wiki.COOKIES, 'w', encoding='utf8', newline='\n') as f:
        f.write('# Netscape HTTP Cookie File\n' + ''.join(r[3] + '\n' for r in keep))
    if not keep:
        os.remove(wiki.COOKIES)
    on = [x['site'] for x in wiki.logins() if x['state'] == 'on']
    return {'ok': True, 'msg': f'Logged in to {", ".join(on)}.' if on else 'Saved, but it holds no login for the sites MyWiki reads.'}


def open_thing(what, arg=''):
    vault = os.path.basename(wiki.ROOT)
    q = urllib.request.quote
    targets = {
        'home': f'obsidian://open?vault={q(vault)}&file={q("wiki/_dashboards/Home")}',
        'note': f'obsidian://open?vault={q(vault)}&file={q(arg)}',
        'news': f'obsidian://open?vault={q(vault)}&file={q("wiki/_dashboards/News/" + arg)}',
        'exports': wiki.EXPORTS, 'downloads': os.path.join(wiki.H, 'Downloads'),
        'claude-export': CLAUDE_EXPORT_URL, 'logs': os.path.join(wiki.STATE, 'logs'),
    }
    if what == 'model-site':  # sign-up and install pages of the agents and services MyWiki knows, nothing else
        targets['model-site'] = (wiki.CFG['runners'].get(arg) or {}).get('site') or (wiki.API.get(arg) or ('', '', '', ''))[3]
    if what == 'story':  # only links from today's feed, never an arbitrary URL
        if arg not in {i['url'] for i in wiki.load_json(wiki.FEED, {}).get('items', [])}:
            return {'ok': False, 'error': "Not in today's news."}
        targets['story'] = arg
    if what == 'search-site' and arg in wiki.PROVIDERS:
        targets['search-site'] = wiki.PROVIDERS[arg]['site']
    t = targets.get(what)
    if not t:
        return {'ok': False, 'error': 'unknown target'}
    if what in ('exports', 'logs'):
        os.makedirs(t, exist_ok=True)
    open_path(t)
    return {'ok': True}


def open_path(t):
    """A file, folder or link in its usual app."""
    if os.name == 'nt':
        os.startfile(t)
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', t], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


fetching = threading.Event()


def news_feed(force=False):
    """Today's stories; a stale or forced feed is fetched in the background (a minute or two: Brave plus a model search per segment) while the old one shows."""
    f = wiki.load_json(wiki.FEED, {})
    if (force or f.get('date') != str(dt.date.today())) and not fetching.is_set():
        fetching.set()

        def go():
            try:
                wiki.feed(force=True)
            except Exception as e:
                wiki.log('feed failed:', str(e)[:200])
            finally:
                fetching.clear()
        threading.Thread(target=go, daemon=True).start()
    return {**f, 'fetching': fetching.is_set()}


def import_zip(path):
    allowed = {f['path'] for f in wiki.export_status()['found']}
    if path not in allowed:  # only zips the app itself found in Downloads
        return {'ok': False, 'error': 'That file is not a claude.ai export the app found.'}
    wiki.import_export(path)
    cache.pop('side', None)
    return {'ok': True}


def upload_zip(name, data):
    name = re.sub(r'[^\w.-]', '_', os.path.basename(name))[:120]
    if not name.endswith('.zip'):
        return {'ok': False, 'error': 'Drop the .zip file from claude.ai.'}
    tmp = os.path.join(wiki.STATE, 'upload-' + name)
    open(tmp, 'wb').write(data)
    try:
        import zipfile
        if not any(n.endswith('conversations.json') for n in zipfile.ZipFile(tmp).namelist()):
            raise ValueError
    except (ValueError, OSError, zipfile.BadZipFile):
        os.remove(tmp)
        return {'ok': False, 'error': 'This zip has no conversations.json, so it is not a claude.ai export.'}
    final = os.path.join(wiki.STATE, name)
    os.replace(tmp, final)
    wiki.import_export(final)
    cache.pop('side', None)
    return {'ok': True}


# ---------------------------------------------------------------- http

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def ok_host(self):
        return self.headers.get('Host', '') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def send(self, code, body, ctype='application/json'):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self.ok_host():
            return self.send(403, {'error': 'forbidden'})
        path, _, query = self.path.partition('?')
        if path == '/api/alive':
            return self.send(200, {'app': 'mywiki', 'idle': time.time() - seen['client']})
        if path.startswith('/api/') and self.headers.get('X-Token') != TOKEN:  # an <img> on any website could otherwise start a feed or a CLI
            return self.send(403, {'error': 'forbidden'})
        seen['client'] = time.time()
        if path in ('/', '/index.html'):
            html = open(os.path.join(UI, 'index.html'), encoding='utf8').read().replace('__TOKEN__', TOKEN)
            return self.send(200, html.encode(), 'text/html; charset=utf-8')
        if path in ('/icon.png', '/icon.svg'):
            f = os.path.join(UI, path[1:])
            return self.send(200, open(f, 'rb').read(), 'image/png' if path.endswith('png') else 'image/svg+xml')
        if path == '/api/state':
            return self.send(200, state())
        if path == '/api/settings':
            return self.send(200, {**wiki.settings_status(), 'phone': phone_status()})
        if path == '/api/phone-queue':
            return self.send(200, {'queue': wiki.phone_queue()})
        if path == '/api/side':  # news and export pages only; the library dashboard lives in Obsidian
            return self.send(200, cached('side', 20, lambda: {'news': wiki.news_pages()[:8], 'export': wiki.export_status(), 'tidy': tidy_plan()}))
        if path == '/api/feed':
            return self.send(200, news_feed())
        if path == '/api/models':
            cli = urllib.parse.unquote(dict(x.split('=', 1) for x in query.split('&') if '=' in x).get('cli', ''))
            return self.send(200, {'models': models(cli)})
        if path == '/api/model-center':
            return self.send(200, wiki.model_catalog())
        if path == '/api/setup':
            return self.send(200, setup_info())
        self.send(404, {'error': 'not found'})

    def do_POST(self):
        if not self.ok_host() or self.headers.get('X-Token') != TOKEN:
            return self.send(403, {'error': 'forbidden'})
        n = int(self.headers.get('Content-Length') or 0)
        if n > 2_000_000_000:
            return self.send(413, {'error': 'too large'})
        raw = self.rfile.read(n)
        path = self.path
        try:
            if path == '/api/cookies':
                return self.send(200, save_cookies(raw))
            if path == '/api/upload':
                return self.send(200, upload_zip(self.headers.get('X-Filename', 'export.zip'), raw))
            body = json.loads(raw or b'{}')
            if path == '/api/run':
                return self.send(200, spawn('run'))
            if path == '/api/feed':
                return self.send(200, news_feed(force=True))
            if path == '/api/capture':
                urls = [str(u) for u in (body.get('urls') or [body.get('url', '')])][:20]
                feed = {i['url'] for i in wiki.load_json(wiki.FEED, {}).get('items', [])}
                if not urls or any(u not in feed for u in urls):
                    return self.send(200, {'ok': False, 'error': "Those stories are not in today's news."})
                return self.send(200, spawn('capture', *urls))
            if path == '/api/consolidate':
                return self.send(200, spawn('consolidate', 'apply'))
            if path == '/api/phone':
                return self.send(200, save_phone(body))
            if path == '/api/phone-drop':
                return self.send(200, {'ok': True, 'queue': wiki.phone_queue(drop=[str(body.get('text', ''))])})
            if path == '/api/search':
                return self.send(200, save_search(body))
            if path == '/api/search-test':
                return self.send(200, test_search(body.get('id')))
            if path == '/api/cookies-clear':
                return self.send(200, save_cookies(b'', str(body.get('site', ''))))
            if path == '/api/link':  # preview a pasted post: details only, nothing downloaded
                url = str(body.get('url', '')).strip()
                if not wiki.is_link(url):
                    return self.send(200, {'ok': False, 'error': 'That is not a link.'})
                if wiki.PHOTO_RX.fullmatch(url):
                    q = next((x for x in wiki.phone_queue() if x['text'] == url), {})
                    return self.send(200, {'ok': True, 'kind': 'image', 'url': url, 'title': q.get('title', 'Photo from your phone'), 'site': 'photo',
                                           'channel': '', 'date': '', 'duration': 0, 'subs': [], 'thumb': ''})
                try:
                    return self.send(200, {'ok': True, **wiki.link_info(url), 'description': '', 'login': os.path.exists(wiki.COOKIES)})
                except Exception as e:
                    err = wiki.plain_ydl_error(e)
                    if 'needs a login' in err:
                        return self.send(200, {'ok': False, 'error': err})
                    t, host = wiki.article_title(url)
                    return self.send(200, {'ok': True, 'kind': 'article', 'url': url, 'title': t, 'site': host, 'channel': '', 'date': '', 'duration': 0, 'subs': [], 'thumb': ''})
            if path == '/api/social':
                urls = [str(u).strip() for u in body.get('urls') or []][:10]
                mode = body.get('mode')
                if not urls or mode not in ('research', 'note') or not all(wiki.is_link(u) for u in urls):
                    return self.send(200, {'ok': False, 'error': 'Paste one or more links.'})
                r = spawn('social', mode, *urls)
                if r.get('ok'):
                    wiki.phone_queue(drop=urls)
                return self.send(200, r)
            if path == '/api/news':
                topic = str(body.get('topic', '')).strip()
                days, depth = int(body.get('days') or 30), body.get('depth', 'quick')
                if not 2 <= len(topic) <= 120 or depth not in ('quick', 'default', 'deep') or not 1 <= days <= 365:
                    return self.send(200, {'ok': False, 'error': 'Give a topic of 2 to 120 characters.'})
                return self.send(200, spawn('news', topic, str(days), depth))
            if path == '/api/models-save':
                return self.send(200, save_models(body))
            if path == '/api/model-test':
                via, m = str(body.get('via', '')), str(body.get('model', '')).strip()
                if (via not in wiki.CFG['runners'] and via not in wiki.API) or not re.fullmatch(r'[\w.:/@+-]{1,120}', m):
                    return self.send(200, {'ok': False, 'error': 'Choose a model first.'})
                ok, msg = wiki.model_test(via, m)
                return self.send(200, {'ok': True, 'works': ok, 'msg': msg})
            if path == '/api/llm':
                r = save_llm(body)
                cache.pop('models:' + str(body.get('id')), None)
                return self.send(200, r)
            if path == '/api/vault-check':
                return self.send(200, wiki.vault_check(str(body.get('path', ''))))
            if path == '/api/vault-use':
                return self.send(200, use_vault(body))
            if path == '/api/pick-folder':
                p = wiki.pick_folder(str(body.get('start', '')))
                return self.send(200, {'path': p or '', 'none': p is None})
            if path == '/api/sources':
                return self.send(200, save_sources(body))
            if path == '/api/topics':
                return self.send(200, save_topics(body))
            if path == '/api/schedule':
                return self.send(200, wiki.schedule_set(bool(body.get('on')), str(body.get('time', '11:00'))))
            if path == '/api/setup-done':
                wiki.save_cfg({'setup_done': True})
                return self.send(200, {'ok': True})
            if path == '/api/open':
                return self.send(200, open_thing(body.get('what'), str(body.get('arg', ''))))
            if path == '/api/import':
                return self.send(200, import_zip(body.get('path', '')))
        except Exception as e:  # the page shows the message; the server keeps running
            return self.send(200, {'ok': False, 'error': str(e)[:300]})
        self.send(404, {'error': 'not found'})


# ---------------------------------------------------------------- window, icon, install

def edge():
    """A Chromium-family browser that can open a bare app window."""
    found = [shutil.which(n) for n in ('msedge', 'microsoft-edge', 'microsoft-edge-stable', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'brave-browser')]
    if os.name == 'nt':
        found += [os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'), os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe')]
    for p in found:
        if p and os.path.exists(p):
            return p


def open_window():
    """The app window. Returns its browser process when it has one of its own (it lives as long as the window)."""
    url = f'http://127.0.0.1:{PORT}/'
    e = edge()
    if e:  # its own profile: a clean app window that remembers its size
        return subprocess.Popen([e, f'--app={url}', '--window-size=1280,860', f'--user-data-dir={os.path.join(wiki.STATE, "app-window")}',
                                 '--no-first-run', '--disable-features=Translate,IntensiveWakeUpThrottling', '--disable-background-timer-throttling',
                                 '--disable-renderer-backgrounding'], creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import webbrowser
    webbrowser.open(url)


def server_up():
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/alive', timeout=1) as r:
            d = json.load(r)
            return d['idle'] if d.get('app') == 'mywiki' else False  # seconds since the window last polled
    except (OSError, ValueError, KeyError):
        return False


def png(size, pixel):
    rows = b''.join(b'\0' + bytes(c for x in range(size) for c in pixel(x, y)) for y in range(size))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) + \
        chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


def icon_pixel(size):
    """Coral sun with a violet moon on its rim, on a cream tile; 4x4 supersampled."""
    import math
    sun, moon = (.45, .56), (.71, .3)

    offs = [((sx + .5) / 4, (sy + .5) / 4) for sy in range(4) for sx in range(4)]

    def colour(u, v):
        r = .23
        cx, cy = min(max(u, r), 1 - r), min(max(v, r), 1 - r)
        if math.dist((u, v), (cx, cy)) > r:
            return (0, 0, 0, 0)
        if math.dist((u, v), moon) < .13:
            return (124, 108, 246, 255)
        if math.dist((u, v), sun) < .3 and math.dist((u, v), moon) > .17:  # a cream gap keeps the moon apart
            return (255, 126, 107, 255)
        return (255, 244, 218, 255)

    def px(x, y):  # ponytail: flat loop; a nested generator here crashed CPython 3.14.4 after a few thousand calls
        cols = [colour((x + a) / size, (y + b) / size) for a, b in offs]
        return tuple(round(sum(c[i] for c in cols) / 16) for i in range(4))
    return px


def make_icons():
    os.makedirs(UI, exist_ok=True)
    big = png(256, icon_pixel(256))
    open(os.path.join(UI, 'icon.png'), 'wb').write(png(64, icon_pixel(64)))
    ico = struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(big), 22) + big
    open(os.path.join(UI, 'mywiki-sun.ico'), 'wb').write(ico)


def install():
    make_icons()
    if os.name != 'nt':
        d = os.path.join(wiki.H, '.local', 'share', 'applications')
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, 'mywiki.desktop')
        open(f, 'w', encoding='utf8').write(f'[Desktop Entry]\nType=Application\nName=MyWiki\nComment=Chats to knowledge\n'
                                            f'Exec="{sys.executable}" "{os.path.abspath(__file__)}"\nIcon={os.path.join(UI, "icon.png")}\nTerminal=false\nCategories=Office;\n')
        print('menu entry:', f)
        return
    lnk_dirs = [os.path.join(os.environ['APPDATA'], r'Microsoft\Windows\Start Menu\Programs'),
                os.path.join(wiki.H, 'Desktop')]
    for d in lnk_dirs:
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{os.path.join(d, 'MyWiki.lnk')}');"
              f"$s.TargetPath='{PYW}';$s.Arguments='\"{os.path.abspath(__file__)}\"';$s.WorkingDirectory='{PIPE}';"
              f"$s.IconLocation='{os.path.join(UI, 'mywiki-sun.ico')}';$s.Description='MyWiki: chats to knowledge';$s.Save()")
        subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True, creationflags=NOWIN)
        print('shortcut:', os.path.join(d, 'MyWiki.lnk'))


phone_last = {}


def phone_command(mode):
    """/research or /note from the phone: start the waiting links (topics stay for the app)."""
    links = [q['text'] for q in wiki.phone_queue() if q['kind'] != 'topic']
    if not links:
        return 'No links are waiting. Topics are researched one at a time from MyWiki > Research.'
    r = spawn('social', mode, *links[:10])
    if not r.get('ok'):
        return 'Not started: ' + r.get('error', 'MyWiki is busy') + ' Try again when it finishes.'
    wiki.phone_queue(drop=links[:10])
    return f"Started: {'researching' if mode == 'research' else 'noting'} {len(links[:10])} links. I will message you when the notes are ready."


def phone_loop():
    """Long-polls the Telegram bot while the app is open. Quiet when no token is set."""
    while True:
        if not wiki.load_json(wiki.KEYS, {}).get('telegram'):
            time.sleep(10)
            continue
        try:
            n = wiki.phone_poll(wait=50, on_command=phone_command)
            phone_last.update(ok=True, at=time.time(), msg=f'{n} new messages' if n else 'listening')
        except Exception as e:
            msg = str(e)
            phone_last.update(ok=False, at=time.time(), msg=msg)
            if '409' not in msg:  # 409: the other MyWiki process is polling right now; not a fault
                wiki.log('phone:', msg[:200])
            time.sleep(30)


def phone_status():
    st = wiki.load_json(wiki.PHONE, {})
    token = bool(wiki.load_json(wiki.KEYS, {}).get('telegram'))
    return {'token': token, 'bot': st.get('bot', ''), 'paired': bool(st.get('owner')), 'code': st.get('code', '') if token and not st.get('owner') else '',
            'waiting': len(st.get('queue', [])), 'last': {**phone_last, 'ago': int(time.time() - phone_last['at'])} if phone_last else None}


def save_phone(body):
    if body.get('token') is not None:
        token = str(body['token']).strip()
        if not re.fullmatch(r'\d{5,15}:[\w-]{30,60}', token):
            return {'ok': False, 'error': 'That is not a bot token. It looks like 123456789:AAF... and comes from @BotFather.'}
        keys = wiki.load_json(wiki.KEYS, {})
        keys['telegram'] = token
        wiki.save_json(wiki.KEYS, keys)
        try:
            me = wiki.tg('getMe')
        except Exception as e:
            keys.pop('telegram')
            wiki.save_json(wiki.KEYS, keys)
            return {'ok': False, 'error': 'Telegram does not know this token. Copy it again from @BotFather.' if '401' in str(e) or '404' in str(e)
                    else 'Could not reach Telegram: ' + str(e)[:120]}
        with wiki.PHONE_LOCK:
            st = wiki.phone_state()
            st.update(bot=me.get('username', ''), owner=None, offset=0)
            wiki.save_json(wiki.PHONE, st)
    if body.get('unpair') or body.get('remove_token'):
        with wiki.PHONE_LOCK:
            st = wiki.load_json(wiki.PHONE, {})
            st.update(owner=None, code='')
            if body.get('remove_token'):
                st['bot'] = ''
            wiki.save_json(wiki.PHONE, st)
            wiki.phone_state()  # fresh pairing code
        if body.get('remove_token'):
            keys = wiki.load_json(wiki.KEYS, {})
            keys.pop('telegram', None)
            wiki.save_json(wiki.KEYS, keys)
    return {'ok': True, **phone_status()}


def serve(window=True):
    wiki.remember_path()  # this session's PATH, for the daily run under cron
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=phone_loop, daemon=True).start()
    seen['client'] = time.time()
    win = open_window() if window else None
    while True:  # ponytail: idle exit by polling; the page pings every few seconds while open, Edge may slow a hidden window to once a minute
        time.sleep(15)
        if win and win.poll() is None:  # our own window is still open: stay, however long the page goes without asking
            seen['client'] = time.time()
        if time.time() - seen['client'] > CLOSE_AFTER and not running():
            wiki.log(f'app: closed, no window for {CLOSE_AFTER // 60} min')
            srv.shutdown()
            return


if __name__ == '__main__':
    if '--install' in sys.argv:
        install()
    elif '--icons' in sys.argv:
        make_icons()
    elif (up := server_up()) is not False:
        if '--serve' not in sys.argv and up > 6:  # a window that polled in the last few seconds is already open
            open_window()
    else:
        try:
            serve(window='--serve' not in sys.argv)
        except Exception as e:  # pythonw has no console: leave the reason where Settings > Logs shows it
            wiki.log('app: stopped by an error:', repr(e)[:300])
            raise
