import asyncio
import logging
import os
import threading

from flask import Flask
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from formatter import format_output
from parser import parse_schedina
from results import get_results_for_matches

load_dotenv()

# Max 10 elaborazioni simultanee per rispettare i limiti Gemini
gemini_semaphore = asyncio.Semaphore(10)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Ciao! Sono il bot della Fantaschedina.\n\n"
        "Mandami la <b>foto della schedina</b> e ti dico subito:\n"
        "• quante partite sono state azzeccate\n"
        "• la quota della schedina\n"
        "• il confronto partita per partita\n\n"
        "<i>Puoi anche inviarla come documento per qualità migliore.</i>",
        parse_mode="HTML",
    )


async def process_image(update: Update, context: ContextTypes.DEFAULT_TYPE, image_bytes: bytes):
    msg = await update.message.reply_text("📸 Schedina ricevuta, elaboro...")

    try:
        async with gemini_semaphore:
            await msg.edit_text("🔍 Leggo la schedina...")
            schedina = await asyncio.to_thread(parse_schedina, image_bytes)

        if not schedina.get("is_schedina", True):
            await msg.delete()
            return

        await msg.edit_text("⚽ Recupero i risultati di Serie A...")
        results = await asyncio.to_thread(get_results_for_matches, schedina["partite"])

        output = format_output(schedina, results)
        await msg.delete()
        await update.message.reply_text(output, parse_mode="HTML")

    except Exception as e:
        logger.exception("Errore nell'elaborazione della schedina")
        await msg.edit_text(f"❌ Errore: {str(e)}\n\nRiprova o controlla che la foto sia leggibile.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # risoluzione più alta
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()
    await process_image(update, context, bytes(image_bytes))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Inviami un'immagine della schedina.")
        return
    file = await context.bot.get_file(doc.file_id)
    image_bytes = await file.download_as_bytearray()
    await process_image(update, context, bytes(image_bytes))


def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_TOKEN non trovato nel file .env")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

    logger.info("Bot avviato. In ascolto...")
    app.run_polling(drop_pending_updates=True)


def run_health_server():
    app = Flask(__name__)

    @app.route("/")
    def health():
        return "OK", 200

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
