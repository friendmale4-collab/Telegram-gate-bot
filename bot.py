"""
Telegram Gate Bot
------------------
On /start, shows two buttons:
  1. Join Backup Channel (url button)
  2. Watch Ad (url button)
Plus a third "I've done both ✅" button.

When the user taps "I've done both", the bot:
  - Verifies channel membership via the Telegram API (getChatMember)
  - Cannot verify the ad-click (no external callback exists for that),
    so that step is trust-based unless you wire up a redirect/shortlink
    service that pings this bot's server.
  - If channel membership passes, sends the video content.

SETUP
-----
1. pip install -r requirements.txt
2. Set environment variables (see config section below) — do NOT hardcode
   your bot token in this file or paste it anywhere public.
3. Add this bot as a member (ideally admin) of your backup channel so it
   can check membership.
4. Run: python bot.py
"""

import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------- CONFIG (edit via env vars) -----------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]                      # required, no default — set this in your shell/host, not in code
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@your_backup_channel")  # channel username or numeric chat id
AD_LINK = os.environ.get("AD_LINK", "https://example.com/ad-link")
VIDEO_FILE_ID = os.environ.get("VIDEO_FILE_ID", "")      # Telegram file_id of the video, if already uploaded
VIDEO_PATH = os.environ.get("VIDEO_PATH", "")             # local file path, used if VIDEO_FILE_ID is empty
CHANNEL_INVITE_LINK = os.environ.get("CHANNEL_INVITE_LINK", "https://t.me/your_backup_channel")
# ----------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("📢 Join Backup Channel", url=CHANNEL_INVITE_LINK)],
        [InlineKeyboardButton("🎬 Watch Ad", url=AD_LINK)],
        [InlineKeyboardButton("✅ I've done both", callback_data="check_gate")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "To unlock your content, please:\n"
        "1️⃣ Join our backup channel\n"
        "2️⃣ Watch the ad\n\n"
        "Then tap the button below to confirm.",
        reply_markup=reply_markup,
    )


async def check_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()  # acknowledge the tap so the button stops spinning

    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        is_member = member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Membership check failed for {user_id}: {e}")
        is_member = False

    if not is_member:
        await query.answer(
            "You haven't joined the backup channel yet — please join and try again.",
            show_alert=True,
        )
        return

    # Note: there is no reliable way to verify the ad was watched from here.
    # This step is trust-based unless you add a redirect service that
    # notifies your server when the link is opened.

    await send_content(query, context)


async def send_content(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = query.from_user.id
    try:
        if VIDEO_FILE_ID:
            await context.bot.send_video(chat_id=chat_id, video=VIDEO_FILE_ID, caption="Here's your content 🎉")
        elif VIDEO_PATH and os.path.exists(VIDEO_PATH):
            with open(VIDEO_PATH, "rb") as f:
                await context.bot.send_video(chat_id=chat_id, video=f, caption="Here's your content 🎉")
        else:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ No video configured yet — set VIDEO_FILE_ID or VIDEO_PATH.")
    except Exception as e:
        logger.error(f"Failed to send content to {chat_id}: {e}")
        await context.bot.send_message(chat_id=chat_id, text="Something went wrong sending your content. Please try again.")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_gate, pattern="^check_gate$"))
    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
