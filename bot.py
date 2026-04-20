import asyncio
import logging
import os
import threading
import time

from flask import Flask
from dotenv import load_dotenv
from telegram import Update
from telegram.error import RetryAfter
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
    user_id = update.effective_user.id if update.effective_user else "?"
    req_id = f"{user_id}-{int(time.time() * 1000)}"
    t0 = time.perf_counter()
    logger.info("[%s] process_image: start (image_size=%d bytes)", req_id, len(image_bytes))

    try:
        async with gemini_semaphore:
            await msg.edit_text("🔍 Leggo la schedina...")
            t_parse = time.perf_counter()
            schedina = await asyncio.to_thread(parse_schedina, image_bytes)
            logger.info(
                "[%s] parse_schedina: %.2fs (partite=%d, used_fallback=%s)",
                req_id,
                time.perf_counter() - t_parse,
                len(schedina.get("partite", [])) if isinstance(schedina, dict) else -1,
                schedina.get("used_fallback") if isinstance(schedina, dict) else None,
            )

        if not schedina.get("is_schedina", True):
            await msg.delete()
            logger.info("[%s] not a schedina, total=%.2fs", req_id, time.perf_counter() - t0)
            return

        if not schedina.get("partite"):
            await msg.edit_text("❌ Non sono riuscito a leggere le partite dalla schedina. Riprova con una foto più nitida.")
            logger.info("[%s] no partite, total=%.2fs", req_id, time.perf_counter() - t0)
            return

        await msg.edit_text("⚽ Recupero i risultati di Serie A...")
        t_results = time.perf_counter()
        results = await asyncio.to_thread(get_results_for_matches, schedina["partite"])
        logger.info(
            "[%s] get_results_for_matches: %.2fs",
            req_id,
            time.perf_counter() - t_results,
        )

        t_fmt = time.perf_counter()
        output = format_output(schedina, results)
        logger.info("[%s] format_output: %.3fs", req_id, time.perf_counter() - t_fmt)
        if schedina.get("used_fallback"):
            output += "\n\n⚠️ <i>Lettura tramite OCR di riserva (quota Gemini esaurita). Alcune partite potrebbero essere mancanti — verifica la schedina.</i>"
        await msg.delete()
        await update.message.reply_text(output, parse_mode="HTML")
        logger.info("[%s] process_image: DONE total=%.2fs", req_id, time.perf_counter() - t0)

    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await msg.edit_text("⚠️ Troppe richieste simultanee. Riprova tra qualche secondo.")
    except Exception as e:
        logger.exception("Errore nell'elaborazione della schedina")
        await msg.edit_text(f"❌ Errore: {str(e)}\n\nRiprova o controlla che la foto sia leggibile.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t_dl = time.perf_counter()
    photo = update.message.photo[-1]  # risoluzione più alta
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()
    logger.info(
        "handle_photo: download %.2fs (size=%d bytes, %dx%d)",
        time.perf_counter() - t_dl,
        len(image_bytes),
        photo.width,
        photo.height,
    )
    await process_image(update, context, bytes(image_bytes))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Inviami un'immagine della schedina.")
        return
    t_dl = time.perf_counter()
    file = await context.bot.get_file(doc.file_id)
    image_bytes = await file.download_as_bytearray()
    logger.info(
        "handle_document: download %.2fs (size=%d bytes)",
        time.perf_counter() - t_dl,
        len(image_bytes),
    )
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
