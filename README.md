# ⏰ Discord Alarm Bot

A Discord bot that lets users set personal DM alarms with an interactive panel — dropdowns, buttons, modals — no commands to memorize.

## Slash Commands

| Command   | What it does                              |
|-----------|-------------------------------------------|
| `/alarm`  | Opens the interactive alarm setup panel   |
| `/alarms` | Lists all your active alarms              |
| `/delete` | Opens a dropdown to delete your alarm(s)  |

## Deploy to Railway (free)

### Step 1 — Create a Discord Bot
1. Go to https://discord.com/developers/applications
2. Click **New Application** → give it a name
3. Go to **Bot** tab → **Add Bot**
4. Under **Privileged Gateway Intents** — enable **Message Content Intent**
5. Click **Reset Token** → copy the token (save it!)
6. Go to **OAuth2 → URL Generator**
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Use Slash Commands`
7. Open the generated URL → invite bot to your server

### Step 2 — Push to GitHub
Create a new repo and push all these files:
```
alarm-bot/
├── bot.py
├── requirements.txt
├── railway.json
└── Procfile
```

### Step 3 — Deploy on Railway
1. Go to https://railway.app → sign in with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your alarm-bot repo
4. Go to **Variables** tab → add:
   - `DISCORD_BOT_TOKEN` = your bot token from Step 1
5. Click **Deploy** — done!

Railway free tier gives you 500 hours/month — enough to run 24/7.

## How Users Use It

1. Type `/alarm` in any channel
2. Pick day(s) from the dropdown (Daily, Mon, Tue… or multiple)
3. Click **Set Time & Message**
4. Enter time in IST (e.g. `08:00`) and their reminder message
5. Bot DMs them at that time every selected day ✅

To view alarms: `/alarms`
To delete: `/delete` → pick from dropdown
