"""
Telegram Gate Bot — multi-video version
----------------------------------------
Supports sharing MANY different videos from ONE bot using Telegram deep
links. Each video gets its own shareable link:

    https://t.me/your_bot_username?start=clip1
    https://t.me/your_bot_username?start=clip2
    https://t.me/your_bot_username?start=clip3

The "clip1" / "clip2" part is the key you use in the VIDEO_MAP variable
below (or in the VIDEO_MAP env var) to point to that video's file_id.

FLOW
----
1. User taps a link like t.me/your_bot?start=clip1
2. Bot shows: Join Backup Channel + Watch Ad + "I've done both" buttons
3. Bot remembers which video ("clip1") this particular user is unlocking
4. On "I've done both", bot verifies channel membership (real check via
   Telegram API). Ad-watching can't be verified — that step is trust-based
   unless you add a redirect/shortlink service that pings your server.
5. If membership passes, bot sends the correct video for that link.

SETUP
-----
1. pip install -r requirements.txt
2. Set environment variables (see CONFIG section) — never hardcode your
   bot token in this file or paste it anywhere public.
3. Add this bot as a member (ideally admin) of your backup channel so it
   can check membership.
4. Define your videos in VIDEO_MAP below (or via the VIDEO_MAP env var,
   see note under CONFIG) — one entry per video, key -> Telegram file_id.
5. Run: python bot.py
6. Share links like https://t.me/your_bot_username?start=clip1 under each
   post, using a different key per video.

GETTING A FILE_ID FOR A NEW VIDEO
----------------------------------
Send the video to your bot in a DM once, then forward that same message to
@RawDataBot on Telegram — it replies with JSON containing the file_id.
"""

import os
import json
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
BOT_TOKEN = os.environ["BOT_TOKEN"]                      # required — set this in your host's env vars, not in code
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@your_backup_channel")  # channel username or numeric chat id
AD_LINK = os.environ.get("AD_LINK", "https://example.com/ad-link")
CHANNEL_INVITE_LINK = os.environ.get("CHANNEL_INVITE_LINK", "https://t.me/your_backup_channel")

# VIDEO_MAP: maps a short key (used in the deep link) to a Telegram file_id.
# Easiest way to manage this day-to-day: edit VIDEO_MAP below and redeploy,
# OR set an env var called VIDEO_MAP with JSON like:
#   {"clip1": "FILE_ID_ONE", "clip2": "FILE_ID_TWO"}
# The env var (if set) takes priority over the dict below.
VIDEO_MAP = {
    "clip1": "PASTE_FILE_ID_HERE",
    "clip2": "PASTE_FILE_ID_HERE",
}
if os.environ.get("VIDEO_MAP"):
    try:
        VIDEO_MAP = json.loads(os.environ["VIDEO_MAP"])
    except json.JSONDecodeError:
        logger.error("VIDEO_MAP env var is not valid JSON — falling back to in-code VIDEO_MAP.")

DEFAULT_VIDEO_KEY = os.environ.get("DEFAULT_VIDEO_KEY", "")  # optional: video to send if no ?start= payload given
# ----------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # context.args holds whatever comes after ?start= in the deep link
    video_key = context.args[0] if context.args else DEFAULT_VIDEO_KEY

    if not video_key or video_key not in VIDEO_MAP:
        await update.message.reply_text(
            "This link doesn't point to a valid video. Please use the exact link shared under the post."
        )
        return

    # Remember which video this user is unlocking, for when they tap the button later
    context.user_data["pending_video"] = video_key

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

    video_key = context.user_data.get("pending_video")
    if not video_key or video_key not in VIDEO_MAP:
        await query.answer(
            "Something went wrong — please open the original link again.",
            show_alert=True,
        )
        return

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

    await send_content(query, context, video_key)


async def send_content(query, context: ContextTypes.DEFAULT_TYPE, video_key: str) -> None:
    chat_id = query.from_user.id
    file_id = VIDEO_MAP.get(video_key, "")
    try:
        if file_id and file_id != "PASTE_FILE_ID_HERE":
            await context.bot.send_video(chat_id=chat_id, video=file_id, caption="Here's your content 🎉")
        else:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ This video isn't configured yet — check back soon.")
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
