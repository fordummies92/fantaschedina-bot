# Fantaschedina Bot 🤖⚽

Bot Telegram per la gestione della **Fantaschedina di Serie A**. Analizza le foto delle schedine, recupera i risultati reali e mostra quante partite sono state azzeccate.

## Come funziona

1. Manda la **foto della schedina** al bot su Telegram
2. Il bot legge i pronostici tramite AI (Gemini)
3. Recupera i risultati reali di Serie A tramite API
4. Risponde con il confronto partita per partita, le partite azzeccate e la quota

## Output esempio

```
📋 FANTASCHEDINA Serie A — Giornata 29
👤 fcpollice

✅ Torino - Parma  (13/03/2026 20:45)
   Pronostico: 1X  →  Risultato: 0-0
❌ Inter - Atalanta  (14/03/2026 15:00)
   Pronostico: 1  →  Risultato: 1-2

━━━━━━━━━━━━━━━━━━━━━━
🎯 Partite azzeccate: 7/10 giocate
💰 Quota schedina: 383.93
📊 Quota parziale (solo prese): 12.45
```

## Variabili d'ambiente richieste

| Variabile | Descrizione |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot da @BotFather |
| `GEMINI_API_KEY` | API key gratuita da aistudio.google.com |
| `FOOTBALL_DATA_TOKEN` | API key gratuita da football-data.org |

## Avvio locale

```bash
pip3 install -r requirements.txt
python3 bot.py
```

## Deploy

- **Render:** https://dashboard.render.com/web/srv-d7i0hn1j2pic73aho100/events
