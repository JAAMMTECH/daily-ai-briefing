# Daily AI + Ontario briefing

A morning briefing that reads the same places a well-informed person checks (Hacker News, Hugging Face, r/LocalLLM, plus a small hardware watchlist) and adds Ontario and Canadian technology news. The point is to stay current, not to produce a company briefing. The full digest goes out by email.

A Cursor automation runs it each morning. That run collects candidates, edits the digest, sends the email, and commits `digests/` and `data/seen.json`. The GitHub Action is manual only. `python -m briefing.main` is the older Claude path, and it also sends Telegram.

## What you will need

- A GitHub repo with this code on the default branch. The manual workflow runs from there.
- An [Anthropic API key](https://console.anthropic.com/settings/keys).
- A Gmail account with 2-step verification, so you can create an app password. Another SMTP provider works if you set the host and port.
- A Telegram bot.

The model in [config.yaml](config.yaml) is `claude-sonnet-5-5`. That is the right default for a daily edit of sources you already collected: it is fast, and its token price is about half of Opus 5.5 ($2 and $10 per million tokens, against $4 and $20). Opus 5.5 is the stronger model, and it thinks on every request, so a morning run that also searches the web stops being "a few cents." Set `model` to `claude-opus-5-5` if you want that anyway. `effort: low` stays cheaper. `medium` or `high` thinks longer.

GitHub Actions is free on a public repo. A private repo includes 2,000 minutes a month. Nothing in this repo starts that workflow on a timer anymore.

## 1. Put the repo on GitHub

Create a repository, then from this folder:

```bash
git add .
git commit -m "Add daily AI and Ontario briefing"
git remote add origin https://github.com/YOU/AI-IT-Updates.git
git push -u origin main
```

The manual workflow is available once that push is on the default branch. See "Run it once now" below.

## 2. Add repository secrets

In the repo on GitHub: Settings, Secrets and variables, Actions, New repository secret.

| Secret | What it is |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SMTP_USER` | Gmail address that sends the mail |
| `SMTP_APP_PASSWORD` | Gmail app password, not your normal password |
| `EMAIL_TO` | Who should get the email. One address, or several separated by commas. You can also list people in `delivery.email_to` in `config.yaml`; the two lists are combined |
| `EMAIL_FROM` | Optional. Defaults to `SMTP_USER` |
| `SMTP_HOST` | Optional. Defaults to `smtp.gmail.com` |
| `SMTP_PORT` | Optional. Defaults to `465` |
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id, from the step below. Several ids, separated by commas, each get the same message. A group chat id works too |

Leave `SMTP_HOST` and `SMTP_PORT` unset to use Gmail. To use [Resend](https://resend.com) instead, set `SMTP_HOST` to `smtp.resend.com`, `SMTP_PORT` to `465`, `SMTP_USER` to `resend`, and `SMTP_APP_PASSWORD` to a Resend API key. `EMAIL_FROM` must be an address on a domain you have verified with Resend.

### Gmail app password

1. Turn on 2-step verification for the Google account: https://myaccount.google.com/signinoptions/two-step-verification
2. Create an app password: https://myaccount.google.com/apppasswords
3. Name it "AI briefing" and paste the 16-character password into `SMTP_APP_PASSWORD`.

If the account is Google Workspace and app passwords are missing, an admin has to allow them. Use Resend in that case.

### Telegram

1. In Telegram, open @BotFather and send `/newbot`. Copy the token into `TELEGRAM_BOT_TOKEN`.
2. Open the bot you just created and send it any message, such as `/start`. A bot cannot message you until you do this.
3. In a browser, open `https://api.telegram.org/bot<token>/getUpdates` with your token in place of `<token>`.
4. Find `"chat":{"id":123456789` and put that number in `TELEGRAM_CHAT_ID`. To reach more than one person, have each of them message the bot, then add every chat id, separated by commas. Or add the bot to a group and use that group's id.

### Sending it to other people

Put addresses in either place. Both are used, and the same address is not mailed twice.

In [config.yaml](config.yaml), which you can edit and push without touching secrets:

```yaml
delivery:
  email_to:
    - you@example.com
    - colleague@example.com
```

Or in the `EMAIL_TO` secret: `you@example.com, colleague@example.com`.

Everyone on the list gets the same email, and they can see the other recipients. Telegram is separate: each chat id in `TELEGRAM_CHAT_ID` or `delivery.telegram_chat_ids` gets the short version. A person only receives Telegram if they have started the bot.

## 3. Run it once now

On GitHub: Actions, Daily briefing, Run workflow. The workflow is disabled, so enable it first. That button skips the clock check and sends immediately through the Claude path. Links included in that run are marked seen, so they will not be repeated in the next briefing. A run between 7:00am and noon counts as that day's briefing, so a later run does not send a second one.

Tick **preview** in the Run workflow form to build a briefing without sending it. The run attaches `briefing-preview` with the email HTML, the markdown, and the Telegram text, and does not mark any story as seen.

You can also run it on your machine. From the repo root:

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env            # then fill in the values
python -m briefing.main --dry-run --force
```

`--dry-run` writes `out/digest.html`, `out/digest.md`, and `out/telegram.txt`. It does not send anything and does not update `data/seen.json`. Without `ANTHROPIC_API_KEY`, the dry run is an unsummarized list of what the sources returned, which is enough to confirm collection works. With a key, the dry run calls Claude and spends a few cents.

`--force` sends even when the clock check would exit, for example before 7:00am or after the day's briefing already went out.

To send for real from your machine:

```bash
python -m briefing.main --force
```

## When it sends

One briefing a day, at 7:00am Toronto time, from the Cursor automation.

The GitHub Action no longer has a schedule, and [vercel/vercel.json](vercel/vercel.json) no longer has cron jobs. The old timers called the Claude path. Leave that workflow disabled. Re-enabling it does not bring the schedule back. A manual run still uses Claude and can also send Telegram.

`local_hour` and `latest_hour` in `config.yaml` are the window for a manual run that follows the clock. The Cursor automation has its own schedule.

## What it reads

Edit [config.yaml](config.yaml). You do not need to change Python.

- Hacker News front page. Stories that look like AI coverage also get the article text and the top comments, because that is where the useful part usually is.
- Hacker News search for the watchlist terms (`ROCm`, `Vulkan`, `Strix Halo`, `llama.cpp`) over the last day, so a relevant post still shows up when it is not on the front page.
- Hugging Face trending models and recent daily papers.
- Top posts of the day from r/LocalLLM. Reddit sometimes blocks GitHub's servers. When it does, the rest of the briefing still goes out.
- New GitHub releases from `ggml-org/llama.cpp` and `ROCm/ROCm`.
- Google News searches limited to Canada, over the last 14 days, because Ontario policy news does not publish every morning. BetaKit, the Ontario Newsroom (filtered so highway announcements do not crowd out technology and education), and Canadian Centre for Cyber Security alerts. Each link is still sent only once.
- New federal tender notices from [CanadaBuys](https://canadabuys.canada.ca/en/procurement-and-contracting-data), kept only when the title or description matches IT or education (software, cloud, AI, schools, and similar). Ontario notices are listed ahead of the rest.
- A short web search, up to 5 searches, for Ontario school-board and IT tenders and Canadian technology news that the fixed feeds missed. Anthropic charges about $10 per 1,000 searches, so this cap is roughly five cents plus the tokens. The scan is skipped when `ANTHROPIC_API_KEY` is not set, and only links the search actually returned are kept. Set `canada.market_scan.enabled` to `false` to turn it off.

Claude follows the audience note at the top of `config.yaml`. It keeps what is worth knowing: new models and tools, and Ontario or Canadian technology news. A short "Why it's worth knowing" line is general context, not a note about any one company.

Under each story, a Terms box explains the acronyms, project names, and jargon in it: first what the term stands for, or what kind of name it is, then a short technical explanation. Each term is explained once per briefing, at its first appearance. Telegram leaves the terms out to stay short.

The email is the full digest. Telegram is the top 5 items plus a link to the markdown copy committed under `digests/`.

## Files

| Path | Role |
| --- | --- |
| `config.yaml` | Sources, watchlist, audience, recipients, model, schedule |
| `briefing/main.py` | Runs one briefing |
| `data/seen.json` | Links already sent, and which day's slot was delivered |
| `briefing/cursor_job.py` | Prepare and finish steps the Cursor automation runs |
| `digests/` | One markdown file per briefing |
| `vercel/` | Leftover project. It no longer starts the briefing |

## If a run fails

Open the failed Actions run and read the log.

- Missing settings: a secret name is empty or not set.
- Email failed: the app password is wrong, or Gmail blocked the sign-in. The script does not mark links as seen when email fails, so the next start can retry.
- Telegram failed: you have not sent `/start` to the bot, or the chat id is wrong. If email succeeded, those links are marked seen so you are not mailed twice. The digest is still in `digests/` and in your inbox.
- Reddit failed: expected sometimes. The log says the run continued.
- A manual run is green but you got nothing: it exited because it was outside 7:00am to noon, or today's briefing was already sent. Check the log for "Not due". Run it again with the clock check off to send now.
