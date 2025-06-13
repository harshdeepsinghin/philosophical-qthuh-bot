import os
import asyncio
import random
import aiohttp
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ContextTypes
)
import nest_asyncio

# === Setup === #
nest_asyncio.apply()
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "-1001571487413"))
QUOTE_INTERVAL = int(os.getenv("QUOTE_INTERVAL", "3"))  # seconds, default 3 mins
ALLOWED_USER_IDS = [1861017597]  # Update with your Telegram user ID(s)

last_message_info = {}
user_sessions = {}
PHILOSOPHER_NAMES = {}

# === Logging === #
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Load Philosophers === #
async def load_philosophers():
    url = "https://philosophersapi.com/api/philosophers"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data:
                        PHILOSOPHER_NAMES[item['id']] = item['name']
                else:
                    logger.warning(f"Failed to fetch philosophers: {resp.status}")
    except Exception as e:
        logger.error(f"Error loading philosophers: {e}")

# === Fetch Quote === #
async def fetch_quote():
    if not PHILOSOPHER_NAMES:
        await load_philosophers()

    url = "https://philosophersapi.com/api/quotes"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data:
                        return "_No quotes found._"

                    quote_data = random.choice(data)
                    quote = quote_data.get("quote", "").strip()
                    philosopher_id = quote_data.get("philosopher", {}).get("id", "")
                    name = PHILOSOPHER_NAMES.get(philosopher_id, "Unknown")
                    return f'_"{quote}"_\n\n*–{name}*'
                return "_Quote service unavailable._"
    except Exception as e:
        logger.error(f"Quote fetch error: {e}")
        return "_Failed to retrieve quote._"

# === Commands === #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != GROUP_CHAT_ID:
        await update.message.reply_text("Use this bot in the designated group.")
        return

    if user_sessions.get(chat_id):
        await update.message.reply_text("Bot is already running.")
    else:
        user_sessions[chat_id] = True
        await update.message.reply_text("Quote bot activated! ✨")
        asyncio.create_task(quote_loop(context, chat_id))

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if user_sessions.pop(chat_id, None):
        await update.message.reply_text("Quote bot stopped.")
    else:
        await update.message.reply_text("Bot is not running.")

# === Heart Reaction === #
async def handle_heart_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in ALLOWED_USER_IDS:
        await query.answer("Permission denied.", show_alert=True)
        return

    await query.answer()
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    save, unsave = "❤️ SAVE", "🖤 UNSAVE"
    info = last_message_info.get(chat_id, {})
    current = info.get("has_reaction", False)
    new_label = save if current else unsave
    new_state = not current

    last_message_info[chat_id] = {"message_id": msg_id, "has_reaction": new_state}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(new_label, callback_data="heart_reaction")]
    ])

    try:
        await context.bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=keyboard)
        await query.answer("❤️ Saved!" if not current else "🖤 Removed!")
    except Exception as e:
        logger.error(f"Markup edit error: {e}")

# === Quote Loop === #
async def quote_loop(context, chat_id):
    while user_sessions.get(chat_id):
        quote = await fetch_quote()

        # Clean previous message
        last_info = last_message_info.get(chat_id)
        if last_info:
            try:
                if last_info.get("has_reaction"):
                    await context.bot.edit_message_reply_markup(chat_id, last_info["message_id"], reply_markup=None)
                else:
                    await context.bot.delete_message(chat_id, last_info["message_id"])
            except Exception as e:
                logger.warning(f"Message cleanup failed: {e}")

        # Send new quote
        try:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=quote,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❤️ SAVE", callback_data="heart_reaction")]
                ]),
                parse_mode="Markdown"
            )
            last_message_info[chat_id] = {"message_id": msg.message_id, "has_reaction": False}
        except Exception as e:
            logger.error(f"Send message failed: {e}")

        await asyncio.sleep(QUOTE_INTERVAL)

# === Main === #
async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN missing from environment.")
        return

    await load_philosophers()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(handle_heart_reaction, pattern="^heart_reaction$"))

    logger.info("Bot running. Use /start in the group chat.")
    await app.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "already running" in str(e):
            loop = asyncio.get_event_loop()
            loop.run_until_complete(main())
        else:
            raise
