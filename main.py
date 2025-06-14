import os
import asyncio
import random
import aiohttp
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ContextTypes,
    MessageHandler, filters, ConversationHandler
)
import nest_asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
scheduler = AsyncIOScheduler()
schedule_settings = {}
bot_instance = None

# Store all previous message IDs for each chat
all_message_ids = {}

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

    await update.message.reply_text("Quote bot activated! Use /schedule to set up automatic quotes, or /new for a manual quote.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if user_sessions.pop(chat_id, None):
        await update.message.reply_text("Quote bot stopped.")
    else:
        await update.message.reply_text("Bot is not running.")

# /schedule command
SCHEDULE_WAITING = 1
# /schedule command (step 1)
async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != GROUP_CHAT_ID:
        await update.message.reply_text("Use this bot in the designated group.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Send the time for the quote (24h format, e.g. 14:30):"
    )
    return 1

# /schedule step 2: get time
async def schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        hour, minute = map(int, text.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
        context.user_data['schedule_time'] = (hour, minute)
        await update.message.reply_text(
            "How often? Reply with one of: daily, weekly, monthly, specific-days (comma-separated, e.g. mon,wed,fri)"
        )
        return 2
    except Exception:
        await update.message.reply_text("Invalid time format. Please use HH:MM (24h). Try again:")
        return 1

# /schedule step 3: get frequency
async def schedule_freq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    freq = update.message.text.strip().lower()
    context.user_data['schedule_freq'] = freq
    if freq == 'specific-days':
        await update.message.reply_text("Enter days (comma-separated, e.g. mon,wed,fri):")
        return 3
    await confirm_schedule(update, context)
    return ConversationHandler.END

# /schedule step 4: get specific days
async def schedule_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = [d.strip().lower()[:3] for d in update.message.text.split(",")]
    context.user_data['schedule_days'] = days
    await confirm_schedule(update, context)
    return ConversationHandler.END

# Confirm and set schedule
async def confirm_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    hour, minute = context.user_data['schedule_time']
    freq = context.user_data['schedule_freq']
    days = context.user_data.get('schedule_days', None)
    # Remove previous job if any
    if chat_id in schedule_settings:
        scheduler.remove_job(str(chat_id))
    # Build cron trigger
    if freq == 'daily':
        trigger = CronTrigger(hour=hour, minute=minute)
        desc = f"every day at {hour:02d}:{minute:02d}"
    elif freq == 'weekly':
        await update.message.reply_text("Which day of week? (e.g. mon, tue, ...)")
        return 3
    elif freq == 'monthly':
        await update.message.reply_text("Which day of month? (1-31)")
        return 4
    elif freq == 'specific-days':
        dow = ','.join(days)
        trigger = CronTrigger(day_of_week=dow, hour=hour, minute=minute)
        desc = f"on {dow} at {hour:02d}:{minute:02d}"
    else:
        await update.message.reply_text("Invalid frequency. Use: daily, weekly, monthly, specific-days.")
        return ConversationHandler.END
    # Schedule job
    scheduler.add_job(send_scheduled_quote, trigger, args=[chat_id], id=str(chat_id), replace_existing=True)
    schedule_settings[chat_id] = {'trigger': trigger, 'desc': desc}
    await update.message.reply_text(f"Scheduled quote {desc}.")

# For weekly/monthly extra step
async def schedule_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = update.message.text.strip().lower()[:3]
    hour, minute = context.user_data['schedule_time']
    trigger = CronTrigger(day_of_week=day, hour=hour, minute=minute)
    desc = f"every {day} at {hour:02d}:{minute:02d}"
    chat_id = update.effective_chat.id
    scheduler.add_job(send_scheduled_quote, trigger, args=[chat_id], id=str(chat_id), replace_existing=True)
    schedule_settings[chat_id] = {'trigger': trigger, 'desc': desc}
    await update.message.reply_text(f"Scheduled quote {desc}.")
    return ConversationHandler.END

async def schedule_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = int(update.message.text.strip())
    hour, minute = context.user_data['schedule_time']
    trigger = CronTrigger(day=day, hour=hour, minute=minute)
    desc = f"on day {day} of each month at {hour:02d}:{minute:02d}"
    chat_id = update.effective_chat.id
    scheduler.add_job(send_scheduled_quote, trigger, args=[chat_id], id=str(chat_id), replace_existing=True)
    schedule_settings[chat_id] = {'trigger': trigger, 'desc': desc}
    await update.message.reply_text(f"Scheduled quote {desc}.")
    return ConversationHandler.END

# Scheduled quote sender
async def send_scheduled_quote(chat_id):
    global bot_instance
    quote = await fetch_quote()
    last_info = last_message_info.get(chat_id)
    if last_info:
        if not last_info.get("has_reaction"):
            try:
                await bot_instance.delete_message(chat_id, last_info["message_id"])
            except Exception as e:
                logger.warning(f"Failed to delete previous message: {e}")
        else:
            await remove_buttons_from_previous(chat_id)
    try:
        msg = await bot_instance.send_message(
            chat_id=chat_id,
            text=quote,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❤️ SAVE", callback_data="heart_reaction")]
            ]),
            parse_mode="Markdown"
        )
        last_message_info[chat_id] = {"message_id": msg.message_id, "has_reaction": False}
    except Exception as e:
        logger.error(f"Scheduled send failed: {e}")

# /new command
async def new_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != GROUP_CHAT_ID:
        await update.message.reply_text("Use this bot in the designated group.")
        return
    quote = await fetch_quote()
    last_info = last_message_info.get(chat_id)
    if last_info:
        if not last_info.get("has_reaction"):
            try:
                await context.bot.delete_message(chat_id, last_info["message_id"])
            except Exception as e:
                logger.warning(f"Failed to delete previous message: {e}")
        else:
            await remove_buttons_from_previous(chat_id, bot=context.bot)
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

# === Help Command === #
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "/start - Start automatic quote posting\n"
        "/stop - Stop automatic quote posting\n"
        "/schedule - Schedule quotes (set time and frequency)\n"
        "/new - Send a new quote immediately\n"
        "/help - Show this help message\n"
        "\n"
        "You can schedule quotes daily, weekly, monthly, or on specific days.\n"
        "Use the ❤️ SAVE button to save a quote, or /new to get a new one."
    )
    await update.message.reply_text(help_text)

# Remove buttons from all previous messages
async def remove_buttons_from_all_previous(chat_id, except_message_id=None, bot=None):
    ids = all_message_ids.get(chat_id, [])
    for msg_id in ids:
        if msg_id != except_message_id:
            try:
                if bot:
                    await bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
                else:
                    await bot_instance.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
            except Exception as e:
                logger.warning(f"Failed to remove buttons from message {msg_id}: {e}")

# Remove buttons from the previous message only
async def remove_buttons_from_previous(chat_id, bot=None):
    last_info = last_message_info.get(chat_id)
    if last_info and last_info.get("message_id"):
        try:
            if bot:
                await bot.edit_message_reply_markup(chat_id, last_info["message_id"], reply_markup=None)
            else:
                await bot_instance.edit_message_reply_markup(chat_id, last_info["message_id"], reply_markup=None)
        except Exception as e:
            logger.warning(f"Failed to remove buttons from message {last_info['message_id']}: {e}")

# === Main === #
async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN missing from environment.")
        return

    await load_philosophers()

    global bot_instance
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_instance = app.bot
    scheduler.configure(event_loop=asyncio.get_running_loop())
    scheduler.start()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(handle_heart_reaction, pattern="^heart_reaction$"))

    schedule_conv = ConversationHandler(
        entry_points=[CommandHandler("schedule", schedule)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_time)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_freq)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_weekly)],
            4: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_monthly)],
        },
        fallbacks=[]
    )
    app.add_handler(schedule_conv)
    app.add_handler(CommandHandler("new", new_quote))
    app.add_handler(CommandHandler("help", help_command))
    # Set bot commands for Telegram UI
    await app.bot.set_my_commands([
        ("start", "Start automatic quote posting"),
        ("stop", "Stop automatic quote posting"),
        ("schedule", "Schedule quotes (set time and frequency)"),
        ("new", "Send a new quote immediately"),
        ("help", "Show help message")
    ])

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
