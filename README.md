# MyWiki

Turns the chats you already have with AI agents into a linked, Obsidian-style knowledge base, once a day, on your own computer.

- **Reads your chats, read-only:** Claude Code, Devin, Antigravity, and claude.ai export zips. Nothing is uploaded except what the model you pick needs to read.
- **Writes topic notes, not chat logs:** one note per idea, merged into what you already have, linked across fields, with "connection" notes that chain ideas together.
- **Links and posts:** paste a YouTube, Instagram, TikTok or X link, an article or a picture. MyWiki reads the caption, the speech (local speech to text), the pictures and video stills, researches the topic, and writes notes.
- **Daily news** on topics you pick, and **phone capture**: send links, photos or topics to your own Telegram bot.
- **Any model:** an agent CLI you already use (Claude Code, Devin, Gemini CLI, Codex, …) or an API (Anthropic, OpenAI, Gemini, OpenRouter, Groq, DeepSeek), including local ones (Ollama, LM Studio).

Tested on Linux (Ubuntu 24.04, Python 3.12) and Windows 11 (Python 3.14). macOS should work (cron, `open`, `osascript` are handled) but has not been tested. Python 3.10 or newer.

## Install

```bash
git clone <this repo> mywiki-app
cd mywiki-app
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

On Ubuntu and Debian, `python3 -m venv` needs `sudo apt install python3-venv` first. For the "Browse…" folder button on Linux, install `zenity` or `kdialog`; without one, type the vault path.

On Windows use `py -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt` and `.venv\Scripts\pythonw app.py`.

To add MyWiki to your app menu (Linux), Start menu and desktop (Windows): `python app.py --install`.

The app opens as its own window in Chrome, Chromium, Edge or Brave when one is installed, otherwise in your default browser. It listens on `127.0.0.1:47821` (set `MYWIKI_PORT` to change it).

## First run

A setup window asks, in order:

1. **Vault**: an existing Obsidian vault or a new folder. MyWiki adds three rule files (`WIKI.md`, `INGEST-AUTO.md`, `taxonomy.md`) if they are missing and never overwrites yours. Edit them to change how notes are written.
2. **Chats**: which chat sources were found on this computer, and words to hide from notes (your name, company, …).
3. **Model**: every agent CLI MyWiki knows is listed; the ones installed show green. Pick one, press **Test**, then use it. Or add an API key, or the address of a local server. You can give the cheap "read chats" step and the "write notes" step different models.
4. **Search**: DuckDuckGo works without a key. Brave and Tavily have free plans with a key; add one for better news.
5. **Phone** (optional): make a bot with @BotFather in Telegram, paste its token, send the bot the code shown. Only your chat is listened to.
6. **News topics**: pick from the list or type your own. The page shows what that costs a day.
7. **Daily run**: a time of day. Windows uses Task Scheduler (it runs later if the computer was asleep); Linux and macOS use cron.

Everything can be changed later in **Settings**.

## Where things live

| What | Where |
| --- | --- |
| The app | this folder |
| Your settings, keys, logins, staged chat text, logs | `~/.mywiki/` (set `MYWIKI_HOME` to move it); private to your account |
| Your notes | the vault you chose |
| Pictures being read | `<vault>/.mywiki/media`, added to the vault's `.gitignore` |

If the vault is a git repository, each run commits (as "MyWiki" when git has no name set) and pushes when there is a remote.

On Linux and macOS the daily run is a cron line. cron starts programs with a bare PATH, so MyWiki keeps the PATH of the session you set it up from in `~/.mywiki/config.json` (`path`) and uses it to find your agent CLIs. Installed a CLI somewhere new? Open MyWiki once and it is picked up.

## Privacy and safety

- Chat text is cleaned before any model sees it: keys, tokens, passwords, emails, phone numbers, your account name and the words you listed are replaced.
- Agent CLIs run without "skip permissions": Claude Code with an allow-list of file tools, Devin with read and write limited to the vault (MyWiki writes that rule to `~/.mywiki/devin-runner.json`), the others in their "accept edits" mode. API models get MyWiki's own tools, which cannot leave the vault or touch `.git`.
- Keys are stored in `~/.mywiki/state/keys.json` and never sent to the page.
- The local server answers only `127.0.0.1` and refuses any request without the token of the window it opened, so other websites cannot use it.
- Logins for Instagram and similar come only from a `cookies.txt` you export yourself; MyWiki never asks for a password.

## Command line

```bash
python wiki.py run                 # what the daily task runs
python wiki.py status              # counts, last run
python wiki.py social research <url>...
python wiki.py news "<topic>"      # needs the last30days skill for social sources; falls back to web search
python wiki.py                     # all commands
```

## Tests

```bash
python test_wiki.py
```

Runs against a throwaway home folder and vault; your settings and notes are not touched.

## Troubleshooting

- **A model test fails:** run the CLI once in a terminal and log in. The test shows the CLI's own last line of output.
- **The daily run did not happen (Linux):** `crontab -l` should show a line ending in `# mywiki-daily`, and the cron service must be running (`systemctl status cron`). A computer that was off at that time skips the day. Logs are in `~/.mywiki/state/logs/`.
- **"This post needs a login":** export cookies for that site as `cookies.txt` (Netscape format) and add it in Settings > Social media.
- **The window says MyWiki stopped:** the server quits 30 minutes after its window closes. Open MyWiki again; the old window reconnects.

## Uninstall

Turn off the daily run in Settings (or `crontab -e` / `schtasks /delete /tn MyWiki-Ingest`), then delete this folder and `~/.mywiki`. Your vault stays as it is.

## Contributors

- [Mitaash Paul](https://github.com/mitaash96): first-run setup no longer blocks on the daily-run step (#1).

## License

MIT, see `LICENSE`.
