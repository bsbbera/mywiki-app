"""Smallest checks that fail if scrubbing, filtering or frontmatter handling breaks. Run: python test_wiki.py
Runs against a throwaway home and a vault made from the template: your settings and notes are never touched."""
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_home = tempfile.mkdtemp()
_vault = os.path.join(_home, 'vault')
shutil.copytree(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vault-template'), _vault)
json.dump({'vault': _vault, 'personal_terms': ['Jane Doe']}, open(os.path.join(_home, 'config.json'), 'w'))
os.environ['MYWIKI_HOME'] = _home
import wiki
assert wiki.ROOT == _vault and wiki.STATE.startswith(_home)

s = wiki.scrub('key sk-ant-api03-abcdefghijklmnop mail a.b@x.com path C:\\Users\\' + os.path.basename(wiki.H) + '\\x api_key=hunter22 +91 98765 43210 Jane Doe said')
assert 'sk-ant' not in s and '[secret]' in s, s
assert '[email]' in s and '~\\x' in s and 'hunter22' not in s and '[phone]' in s and '[me] said' in s, s
# the account name is scrubbed as a whole word, and never when it is an everyday word
H1, wiki.H = wiki.H, os.path.join('home', 'janedoe')
assert wiki.scrub('janedoe wrote to janedoes') == '[me] wrote to janedoes'
wiki.H = os.path.join('home', 'user')
assert wiki.scrub('two users and one user') == 'two users and one user'
wiki.H = H1

assert '[code omitted]' in wiki.clean('see ```py\nprint(1)\n``` ok')
assert wiki.clean('<system-reminder>x</system-reminder>hi') == 'hi'

assert wiki.is_human([('user', 'what is BigQuery?'), ('assistant', 'a warehouse')])
assert not wiki.is_human([('user', 'x' * 2000), ('assistant', 'ok')])                    # one long machine prompt
assert not wiki.is_human([('user', '[wiki-ingest] extract'), ('assistant', 'ok')])       # our own runner
assert not wiki.is_human([('assistant', 'hello')])

t = '---\ntitle: A\naliases:\n  - B\n---\nbody\n'
fm = wiki.parse_fm(t)
assert fm['title'] == 'A' and fm['aliases'] == ['B'], fm
t2 = wiki.set_fm(t, {'type': 'person', 'title': 'IGNORED'})
assert 'type: person' in t2 and t2.count('title:') == 1 and t2.endswith('---\nbody\n'), t2
u = os.path.basename(wiki.H)
assert u.lower() not in wiki.scrub('file:///c%3A%5CUsers%5C' + u + '%5CDesktop').lower()
assert wiki.clean('<ADDITIONAL_METADATA>\ntime\n</ADDITIONAL_METADATA>hi') == 'hi'
# harvest reads a chat only when it changed since the last run, so run time does not grow with chat history
reads = []
def _src():
    yield dict(source='t', id='1', title='T', where='', updated=wiki.NOW - 99999,
               load=lambda s: reads.append(1) or [('user', 'what is a lazy reader? ' * 30), ('assistant', 'one that waits ' * 30)])
SRC0, wiki.SOURCES = wiki.SOURCES, {'t': _src}
wiki.harvest(); wiki.harvest()
assert len(reads) == 1, reads
wiki.SOURCES = SRC0
r = wiki.rules_text()  # fails if INGEST-AUTO.md headings get renamed and extract would lose its rules
assert 'What counts as knowledge' in r[0] and 'Card format' in r[0] and 'type' in r[1], r[0][:200]
room = wiki.CMD_MAX - len(subprocess.list2cmdline([wiki.extract_prompt('', r)])) - 1500  # harvest's piece size
assert room > 12000 and len(subprocess.list2cmdline([wiki.extract_prompt('x' * (room - 400), r)])) < wiki.CMD_MAX, room
import tempfile
k = os.path.join(tempfile.mkdtemp(), 'c.md'); open(k, 'w', encoding='utf8').write('### Flutter\n- links: Dart, [[Atlas]]\n- facts:\n')
assert wiki.card_keys(k) == {'flutter', 'dart', 'atlas'}, wiki.card_keys(k)  # integrate's no-clash rule depends on this

# dashboard: summary skips the infobox, facts come from their section, table links lose their escaping backslash
d = tempfile.mkdtemp()
n = os.path.join(d, 'kolam.md')
open(n, 'w', encoding='utf8').write('---\ntitle: Kolam\nfield: culture\ntype: concept\n---\n> [!infobox]\n> x\n\n'
                                    '**Kolam** is a [[rice-flour|rice flour]] drawing. More here.\n\n| a | [[gift-siromoney\\|Gift]] |\n\n'
                                    '## Interesting facts\n- Drawn at dawn by [[women]].\n\n## Related pages\n')
i = wiki.note_info(n, 'culture/kolam.md')
assert i['summary'] == 'Kolam is a rice flour drawing.', i['summary']
assert i['facts'] == ['Drawn at dawn by women.'] and {'rice-flour', 'gift-siromoney', 'women'} <= i['links'], i

# news: evidence stops at the budget and keeps the best-ranked items first
ev, used = wiki.evidence({'results': [{'title': 'low', 'relevance_score': .1}, {'title': 'high', 'relevance_score': .9}]}, 30)
assert ev.startswith('[1] high') and len(used) == 1, ev

# export: only a zip holding conversations.json counts as a claude.ai export
import zipfile
H0, wiki.H = wiki.H, d
os.makedirs(os.path.join(d, 'Downloads'))
with zipfile.ZipFile(os.path.join(d, 'Downloads', 'data-2026.zip'), 'w') as z:
    z.writestr('conversations.json', '[]')
with zipfile.ZipFile(os.path.join(d, 'Downloads', 'other.zip'), 'w') as z:
    z.writestr('photo.jpg', '')
assert [f['name'] for f in wiki.export_status()['found']] == ['data-2026.zip']
wiki.H = H0

# consolidate: links to folded notes point at the topic, links to deleted notes become plain text, tables keep their escape
W0, wiki.WIKI = wiki.WIKI, tempfile.mkdtemp()
os.makedirs(os.path.join(wiki.WIKI, 'arts'))
n = os.path.join(wiki.WIKI, 'arts', 'x.md')
open(n, 'w', encoding='utf8').write('See [[kolam]], [[alpona|Alpona art]], [[canva]] and | [[kolam\\|K]] |, plus [[other]].')
wiki.relink({'kolam': 'indian-folk-art', 'alpona': 'indian-folk-art', 'canva': None})
assert open(n, encoding='utf8').read() == 'See [[indian-folk-art|kolam]], [[indian-folk-art|Alpona art]], canva and | [[indian-folk-art\\|K]] |, plus [[other]].', open(n, encoding='utf8').read()
wiki.WIKI = W0


# related: multi-word names match anywhere, one-word names only as a card title or link, hubs never
M0, wiki.META = wiki.META, tempfile.mkdtemp()
open(os.path.join(wiki.META, 'catalog.tsv'), 'w', encoding='utf8').write('path\ttitle\taliases\ttype\tfield\tsubfield\n'
    'wiki/a.md\tImage generation\t\tconcept\t\t\nwiki/b.md\tWorkflows\t\tconcept\t\t\nwiki/c.md\tGlicko\tRating systems\tconcept\t\t\nwiki/d.md\tArt Home\tArt\thub\t\t\n')
assert wiki.related('### Diffusion\n- links: Glicko\nbetter image generation workflows for art') == ['wiki/a.md', 'wiki/c.md'], wiki.related('### Diffusion\n- links: Glicko\nbetter image generation workflows for art')
wiki.META = M0

# feed: model story lines parse, junk lines don't; Brave stops at the daily cap
r = wiki.parse_stories('NONE\n- Comet seen | https://x.org/a | X News | 2026-10-06 | it glows | bright\nno pipes here\nT | ftp://x | a | b | c')
assert r == [{'title': 'Comet seen', 'url': 'https://x.org/a', 'source': 'X News', 'published_at': '2026-10-06', 'summary': 'it glows | bright'}], r
# search: providers go in order, each stops at its daily cap; none left -> nothing (the model searches itself)
U0, K0, C0, f0 = wiki.SEARCH_USE, wiki.KEYS, wiki.CFG.get('search'), (wiki._brave, wiki._tavily, wiki._ddg)
t = tempfile.mkdtemp()
wiki.SEARCH_USE, wiki.KEYS = os.path.join(t, 's.json'), os.path.join(t, 'k.json')
wiki.save_json(wiki.KEYS, {'brave': 'k' * 20})
wiki.CFG['search'] = {'brave': {'per_day': 1}, 'tavily': {'on': False}, 'ddg': {'on': True, 'per_day': 1}}
L0, wiki.log = wiki.log, lambda *a: None  # keep test lines out of the real log
wiki._brave = lambda *a: ('fixed', [{'url': 'b'}])
wiki._ddg = lambda *a: (None, [{'url': 'd'}])
assert wiki.search_left() == 2 and wiki.search('x') == ('fixed', [{'url': 'b'}])
assert wiki.search('x') == (None, [{'url': 'd'}]) and wiki.search('x') == (None, []) and wiki.search_left() == 0
assert wiki.load_json(wiki.SEARCH_USE, {})['status']['ddg']['ok']
wiki.SEARCH_USE, wiki.KEYS, (wiki._brave, wiki._tavily, wiki._ddg) = U0, K0, f0
wiki.log = L0
wiki.CFG.pop('search') if C0 is None else wiki.CFG.update(search=C0)

# parallel saves of one file must not trip over each other's temp file
import concurrent.futures
P = os.path.join(t, 'p.json')
list(concurrent.futures.ThreadPoolExecutor(8).map(lambda i: wiki.save_json(P, {'i': i}), range(40)))
assert 'i' in wiki.load_json(P, {})

# logins: cookies.txt rows (with #HttpOnly_) say which sites are logged in, and until when
assert wiki.cookie_rows('# Netscape\n#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1900000000\tsessionid\tv\n.x.com\tTRUE\t/\tTRUE\t1\tauth_token\tv\n') == [
    ('instagram.com', 'sessionid', 1900000000, '#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1900000000\tsessionid\tv'), ('x.com', 'auth_token', 1, '.x.com\tTRUE\t/\tTRUE\t1\tauth_token\tv')]
CK0, wiki.COOKIES = wiki.COOKIES, os.path.join(t, 'c.txt')
open(wiki.COOKIES, 'w').write('#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1900000000\tsessionid\tv\n.x.com\tTRUE\t/\tTRUE\t1\tauth_token\tv\n')
st = {x['site']: x['state'] for x in wiki.logins()}
assert st['Instagram'] == 'on' and st['X (Twitter)'] == 'expired' and st['TikTok'] == 'off', st
wiki.COOKIES = CK0

# social: captions lose timestamps, tags and the rolling repeats auto-captions make
assert wiki.vtt_text('WEBVTT\nKind: captions\n\n00:00:01.000 --> 00:00:02.000\nhello <c>there</c>\n\n00:00:02.000 --> 00:00:03.000\nhello there\nfriend\n1\n') == 'hello there friend'
assert 'login' in wiki.plain_ydl_error(Exception('ERROR: [Instagram] x: Requested content is not available, rate-limit reached or login required'))

# phone: only the chat that sends the pairing code is listened to; links queue once; commands answer
PH0, tg0, de0, L0 = wiki.PHONE, wiki.tg, wiki.describe, wiki.log
wiki.PHONE, wiki.log = os.path.join(t, 'phone.json'), lambda *a: None
code = wiki.phone_state()['code']
inbox, sent = [], []
def fake_tg(method, http_timeout=20, **kw):
    if method == 'getUpdates':
        out, inbox[:] = list(inbox), []
        return out
    sent.append((kw.get('chat_id'), kw.get('text', '')))
wiki.tg, wiki.describe = fake_tg, lambda u: ('Title of ' + u, 'site', 'post')
def say(chat, text):
    inbox.append({'update_id': len(sent) + 100, 'message': {'chat': {'id': chat}, 'text': text}})
    sent.clear(); wiki.phone_poll(); return sent[-1]
assert 'private' in say(7, 'https://a.com/x')[1] and not wiki.phone_queue()
assert say(5, f'/start {code}') == (5, sent[-1][1]) and 'Linked' in sent[-1][1] and wiki.load_json(wiki.PHONE, {})['owner'] == 5
assert 'private' in say(7, code)[1]  # the code is used up
assert '1 waiting' in say(5, 'look https://a.com/x')[1] and '1 waiting' in say(5, 'https://a.com/x')[1]
assert '2 waiting' in say(5, 'kolam geometry')[1] and [q['kind'] for q in wiki.phone_queue()] == ['post', 'topic']
assert 'Title of https://a.com/x' in say(5, '/list')[1] and 'closed' in say(5, '/research')[1]
assert [q['text'] for q in wiki.phone_queue(drop=['https://a.com/x'])] == ['kolam geometry']
# a photo (no caption) queues as an image; a photo caption is never read as a command
pp0 = wiki.phone_photo
wiki.phone_photo = lambda p, cap: 'photo:' + p['file_unique_id'] + '.jpg'
inbox.append({'update_id': 300, 'message': {'chat': {'id': 5}, 'photo': [{'file_id': 's', 'file_unique_id': 'small'}, {'file_id': 'b', 'file_unique_id': 'big'}]}})
inbox.append({'update_id': 301, 'message': {'chat': {'id': 5}, 'caption': '/research', 'photo': [{'file_id': 'c', 'file_unique_id': 'cap'}]}})
sent.clear(); wiki.phone_poll()
assert [(q['text'], q['kind']) for q in wiki.phone_queue()][-2:] == [('photo:big.jpg', 'image'), ('photo:cap.jpg', 'image')], wiki.phone_queue()
wiki.phone_photo = pp0
assert wiki.is_link('photo:big.jpg') and wiki.is_link('https://a.com') and not wiki.is_link('photo:../x') and not wiki.is_link('kolam')
wiki.PHONE, wiki.tg, wiki.describe, wiki.log = PH0, tg0, de0, L0

# links: a phone photo is read with its caption; the speech is squeezed so the prompt fits one command line
from PIL import Image
M0 = wiki.MEDIA
wiki.MEDIA = t
os.makedirs(os.path.join(t, 'phone'), exist_ok=True)
Image.new('RGB', (2000, 500), 'red').save(os.path.join(t, 'phone', 'p1.jpg'))
open(os.path.join(t, 'phone', 'p1.jpg.txt'), 'w', encoding='utf8').write('Kolam at dawn')
g = wiki.collect('photo:p1.jpg')
assert g['title'] == 'Kolam at dawn' and len(g['pics']) == 1 and Image.open(g['pics'][0]).size == (1024, 256), g
wiki.MEDIA = M0
g.update(transcript='word ' * 20000)
w = wiki.words(g, 5000)
assert len(w) <= 5000 and '[...]' in w and 'Caption:\nKolam at dawn' in w
assert len(subprocess.list2cmdline([wiki.fits(lambda x: 'P ' + x, 'a"b ' * 20000)])) <= wiki.CMD_MAX - 1200
assert wiki.block('x <title>\n Kolam \n</title>', 'title') == 'Kolam' and wiki.block('none', 'about') == ''

# setup: a new vault gets the rule files, never loses existing ones; settings merge into ~/.mywiki/config.json
nv = os.path.join(_home, 'new vault')
r = wiki.vault_use(nv, create=True)
assert r['ok'] and r['ready'] and os.path.isdir(os.path.join(nv, '.mywiki', 'media')) and '.mywiki/' in open(os.path.join(nv, '.gitignore')).read()
open(os.path.join(nv, 'taxonomy.md'), 'w').write('mine')
wiki.vault_use(nv)
assert open(os.path.join(nv, 'taxonomy.md')).read() == 'mine'
assert json.load(open(os.path.join(_home, 'devin-runner.json')))['permissions']['allow'] == [f"Read({nv.replace(os.sep, '/')}/**)", f"Write({nv.replace(os.sep, '/')}/**)"]
wiki.save_cfg({'vault': _vault, 'models': {'default': {'via': 'fake', 'model': 'm1'}}})
assert wiki.CFG['personal_terms'] == ['Jane Doe'] and wiki.CFG['runners']['claude']['cmd'][0] == 'claude' and wiki.pick('extract')['model'] == 'm1'
assert [x['id'] for x in wiki.sources_found()] == list(wiki.SOURCE_INFO) and wiki.topic_row('Kolam') == ['Kolam', 'pw', 'Kolam']
# the house-style file: WIKI.md in new vaults, devin.md kept in vaults that began with it (no WIKI.md added beside it)
assert wiki.guide(nv) == 'WIKI.md' and os.path.exists(os.path.join(nv, 'WIKI.md'))
ov = os.path.join(_home, 'old vault')
os.makedirs(ov)
open(os.path.join(ov, 'devin.md'), 'w').write('mine')
r = wiki.vault_use(ov)
assert r['ready'] and not r['missing'] and wiki.guide(ov) == 'devin.md' and not os.path.exists(os.path.join(ov, 'WIKI.md')), r
wiki.save_cfg({'vault': _vault})
R0, wiki.ROOT = wiki.ROOT, ov
assert '`devin.md`' in wiki.prompt('integrate', cards='c', related='r') and '{guide}' not in wiki.prompt('connect', n='1', existing='e')
wiki.ROOT = R0
assert '`WIKI.md`' in wiki.prompt('integrate', cards='c', related='r')
# an agent CLI that is not installed (or not on cron's PATH) is a plain failure, not a crash
wiki.CFG['runners']['ghost'] = {'cmd': ['no-such-agent-cli', '{prompt}']}
ok, out = wiki.run_agent('x', 'p', 5, {'via': 'ghost', 'model': 'm'})
assert not ok and 'could not start no-such-agent-cli' in out, out
del wiki.CFG['runners']['ghost']
# cron: the line carries this PATH, survives spaces in paths, and schedule_target finds the script in it
line = wiki.cron_line('07:05', ['/usr/bin/python3', '/opt/My Wiki/wiki.py'])
assert line.startswith('5 7 * * * cd ') and 'PATH' not in line and len(line) < 400 and line.endswith(wiki.CRON_TAG), line  # cron refuses long lines
if os.name != 'nt':  # the PATH cron lacks is kept in config and added back on start
    P0 = os.environ['PATH']
    os.environ['PATH'] = P0 + os.pathsep + '/opt/agents/bin'
    wiki.remember_path()
    assert '/opt/agents/bin' in wiki.CFG['path'].split(os.pathsep) and set(P0.split(os.pathsep)) <= set(wiki.CFG['path'].split(os.pathsep))
    os.environ['PATH'] = P0
# a CLI and an API never share an id: run_agent checks the APIs first, so a shared id would send CLI runs to the API
assert not set(wiki.API) & set(wiki.CFG['runners']), set(wiki.API) & set(wiki.CFG['runners'])
assert "'/opt/My Wiki/wiki.py' run" in line and wiki.cron_target('0 1 * * * x\n' + line) == '/opt/My Wiki/wiki.py', line
assert wiki.cron_target('5 7 * * * cd /v && "/usr/bin/python3" "/old/wiki.py" run >/dev/null 2>&1 ' + wiki.CRON_TAG) == '/old/wiki.py'

# API agent: tools stay inside the vault; a tool call is run and its result goes back; pictures are attached
assert wiki.vault_path('wiki/a.md') and not wiki.vault_path('../x.md') and not wiki.vault_path(os.path.join(_home, 'config.json'))
assert not wiki.vault_path('.git/config', write=True) and wiki.agent_tool('write_file', {'path': '../evil.md', 'content': 'x'}, []).startswith('Refused')
import http.server, threading
got = []
class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        got.append((self.headers.get('Authorization'), b))
        if len(got) == 1:
            m = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'c1', 'type': 'function', 'function': {'name': 'write_file', 'arguments': json.dumps({'path': 'wiki/t.md', 'content': 'hi'})}},
                {'id': 'c2', 'type': 'function', 'function': {'name': 'read_file', 'arguments': json.dumps({'path': 'p.png'})}}]}
        else:
            m = {'role': 'assistant', 'content': 'done OK'}
        out = json.dumps({'choices': [{'message': m}]}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(out))); self.end_headers(); self.wfile.write(out)
srv = http.server.HTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
open(os.path.join(_vault, 'p.png'), 'wb').write(b'\x89PNG')
wiki.save_cfg({'providers': {'custom': {'base': f'http://127.0.0.1:{srv.server_port}/v1'}}})
wiki.save_json(wiki.KEYS, {'llm:custom': 'k' * 20})
ok, out = wiki.run_agent('x', 'write it', 30, {'via': 'custom', 'model': 'm'})
assert ok and out == 'done OK' and open(os.path.join(_vault, 'wiki', 't.md')).read() == 'hi', out
assert got[0][0] == 'Bearer ' + 'k' * 20 and got[0][1]['tools'] and got[1][1]['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/png')
assert [m['role'] for m in got[1][1]['messages']] == ['user', 'assistant', 'tool', 'tool', 'user']
srv.shutdown()
assert wiki.run_agent('x', 'p', 5, {'via': 'custom', 'model': 'm'})[0] is False  # server gone: a plain failure, no crash
assert 'No model chosen' in wiki.run_agent('x', 'p', 5, {})[1]

# app: unit file names become words a person recognises
import app
assert app.unit_label('2026-09-24__claude-code__abc__876-9.md', {'claude-code__abc': 'ChessPlay'}) == 'ChessPlay · Claude Code · part 10'
assert app.unit_label('2026-10-06__news__obsidian-plugins__171.md', {}).startswith('obsidian plugins · News')
assert 'busy' in app.plain_error('UNAVAILABLE (code 503)')
# git: a vault on a machine with no git identity still gets its commit; a commit never reads as saved when it failed
import shutil as _sh
if _sh.which('git'):
    gv = os.path.join(_home, 'git vault')
    os.makedirs(gv)
    env0 = {k: os.environ.get(k) for k in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM')}
    os.environ.update(GIT_CONFIG_GLOBAL=os.path.join(_home, 'no-gitconfig'), GIT_CONFIG_NOSYSTEM='1')  # as on a fresh machine
    subprocess.run(['git', 'init', '-q', gv], check=True)
    open(os.path.join(gv, 'a.md'), 'w').write('x')
    R0, wiki.ROOT = wiki.ROOT, gv
    wiki.git_commit('test')
    wiki.ROOT = R0
    assert subprocess.run(['git', 'log', '--format=%an %s'], cwd=gv, capture_output=True, text=True).stdout.strip() == 'MyWiki test'
    for k, v in env0.items():
        os.environ.pop(k) if v is None else os.environ.update({k: v})
for m in ('git push ok', 'git push FAILED: no token', 'git commit FAILED: x'):
    wiki.log(m)
assert [e['text'] for e in app.events()[-3:]] == ['Saved and pushed', 'Push failed', 'Could not save to git'], app.events()[-3:]
# app server: other websites can neither read nor trigger the API (no token), a wrong Host is refused, the page carries the token
import urllib.request, urllib.error
srv = app.ThreadingHTTPServer(('127.0.0.1', 0), app.H)
app.PORT = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()
def call(path, host=None, token=None, data=None):
    h = {'Host': host or f'127.0.0.1:{app.PORT}', **({'X-Token': token} if token else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{app.PORT}{path}', data, h), timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b''
assert call('/api/alive')[0] == 200 and call('/api/phone-queue')[0] == 403 and call('/api/feed')[0] == 403
assert call('/api/phone-queue', token=app.TOKEN)[0] == 200 and call('/api/phone-queue', host='evil.com', token=app.TOKEN)[0] == 403
assert call('/api/run', data=b'{}')[0] == 403 and app.TOKEN.encode() in call('/')[1]
srv.shutdown()
print('ok')
