# Fantaschedina Bot 🤖⚽

A Telegram bot that manages the **Fantaschedina** prediction game for Serie A. It reads betting-slip photos, fetches the real match results, and tells you how many predictions were correct.

## How it works

1. Send a **photo of your betting slip** to the bot on Telegram
2. The bot reads the predictions using AI (Gemini)
3. It fetches the real Serie A results via API
4. It replies with a match-by-match comparison, the number of correct picks, and the odds

## Example output

```
📋 FANTASCHEDINA Serie A — Matchday 29
👤 fcpollice

✅ Torino - Parma  (13/03/2026 20:45)
   Prediction: 1X  →  Result: 0-0
❌ Inter - Atalanta  (14/03/2026 15:00)
   Prediction: 1  →  Result: 1-2

━━━━━━━━━━━━━━━━━━━━━━
🎯 Correct picks: 7/10 played
💰 Slip odds: 383.93
📊 Partial odds (correct picks only): 12.45
```

## Required environment variables

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `GEMINI_API_KEY` | Free API key from aistudio.google.com |
| `FOOTBALL_DATA_TOKEN` | Free API key from football-data.org |

## Run locally

```bash
pip3 install -r requirements.txt
python3 bot.py
```

## Deployment

Deployed on [Render](https://render.com).

## Tech stack

- **Bot framework** — python-telegram-bot
- **AI parsing** — Google Gemini (vision)
- **Match data** — football-data.org API
- **Hosting** — Render
