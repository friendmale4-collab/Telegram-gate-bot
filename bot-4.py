"""
Telegram Gate Bot — multi-video, per-video backup channel
------------------------------------------------------------
Supports sharing MANY different videos from ONE bot using Telegram deep
links, where EACH video can require joining a DIFFERENT backup channel.

    https://t.me/your_bot_username?start=clip1   (may require Channel A)
    https://t.me/your_bot_username?start=clip2   (may require Channel B)

FLOW
----
1. User taps a link like t.me/your_bot?start=clip1
2. Bot shows: Join Backup Channel (specific to that clip) + Watch Ad +
   "I've done both" buttons
3. On "I've done both", bot verifies membership in THAT clip's channel via
   the Telegram API. Ad-watching can't be verified — that step is
   trust-based unless you add a redirect/shortlink service.
4. If membership passes, bot sends the correct video, with forwarding/
   saving disabled (protect_content).

ADMIN WORKFLOW (no coding needed after setup)
-----------------------------------------------
1. Set your channel for the next batch of videos:
     /setchannel @channelusername https://t.me/channelusername
2. Send your videos directly to the bot (one after another). Each gets
   auto-tagged with the channel you just set, and assigned the next
   available clipN key.
3. Switch channels anytime with /setchannel again before your next batch.
4. When done, send /export — it replies with the full VIDEO_MAP JSON
   (old + new videos, each with their own channel) ready to paste into
   the VIDEO_MAP variable in Railway.

SETUP
-----
1. pip install -r requirements.txt
2. Set environment variables: BOT_TOKEN, AD_LINK, ADMIN_USER_ID,
   VIDEO_MAP (JSON, see below), and optionally CHANNEL_ID /
   CHANNEL_INVITE_LINK as a fallback default channel.
3. Add the bot as admin in every backup channel you plan to use, so it
   can check membership in each one.
4. Run: python bot.py

VIDEO_MAP FORMAT
-----------------
Each entry can be either:
  - a plain file_id string (uses the default CHANNEL_ID / CHANNEL_INVITE_LINK), or
  - an object with its own channel:
      {"clip1": {"file_id": "...", "channel_id": "@channelA", "channel_invite": "https://t.me/channelA"}}
The /export command always outputs the object form so each clip keeps its
own channel correctly.
"""

import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------- CONFIG (edit via env vars) -----------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]  # required — set in your host's env vars, not in code
AD_LINK = os.environ.get("AD_LINK", "https://example.com/ad-link")

# Fallback/default channel, used only for plain-string VIDEO_MAP entries
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@your_backup_channel")
CHANNEL_INVITE_LINK = os.environ.get("CHANNEL_INVITE_LINK", "https://t.me/your_backup_channel")

# VIDEO_MAP: maps a short key (used in the deep link) to either a file_id
# string, or an object with its own file_id + channel. Env var takes
# priority over the dict below if set.
VIDEO_MAP = {}
if os.environ.get("VIDEO_MAP"):
    try:
        VIDEO_MAP = json.loads(os.environ["VIDEO_MAP"])
    except json.JSONDecodeError:
        logger.error("VIDEO_MAP env var is not valid JSON — starting with an empty map.")

DEFAULT_VIDEO_KEY = os.environ.get("DEFAULT_VIDEO_KEY", "")  # optional: video to send if no ?start= payload given

# ADMIN_USER_ID: your personal Telegram numeric user ID. Only this user can
# use the auto-collect feature and /setchannel. Get your ID by messaging
# @RawDataBot with /start — it shows your Chat ID.
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "")
# ----------------------------------------------------------------------------

# Videos collected during this running session via admin auto-collect mode.
# NOTE: this resets whenever the bot restarts/redeploys — export before that.
_collected_videos = {}

# The channel currently set via /setchannel, applied to newly collected videos.
_current_channel = {"channel_id": CHANNEL_ID, "channel_invite": CHANNEL_INVITE_LINK}


def _entry_file_id(entry) -> str:
    if isinstance(entry, dict):
        return entry.get("file_id", "")
    return entry or ""


def _entry_channel(entry) -> tuple:
    if isinstance(entry, dict):
        return entry.get("channel_id", CHANNEL_ID), entry.get("channel_invite", CHANNEL_INVITE_LINK)
    return CHANNEL_ID, CHANNEL_INVITE_LINK


def _next_clip_number() -> int:
    """Find the next unused clipN number across VIDEO_MAP and _collected_videos."""
    used_numbers = []
    for key in list(VIDEO_MAP.keys()) + list(_collected_videos.keys()):
        if key.startswith("clip") and key[4:].isdigit():
            used_numbers.append(int(key[4:]))
    return max(used_numbers, default=0) + 1


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # context.args holds whatever comes after ?start= in the deep link
    video_key = context.args[0] if context.args else DEFAULT_VIDEO_KEY

    entry = VIDEO_MAP.get(video_key) if video_key else None
    if entry is None:
        entry = _collected_videos.get(video_key) if video_key else None

    if not video_key or entry is None:
        await update.message.reply_text(
            "This link doesn't point to a valid video. Please use the exact link shared under the post."
        )
        return

    channel_id, channel_invite = _entry_channel(entry)

    # Remember which video (and its channel) this user is unlocking.
    # Reset ad_watched so each new /start requires watching the ad again.
    context.user_data["pending_video"] = video_key
    context.user_data["ad_watched"] = False

    keyboard = [
        [InlineKeyboardButton("📢 Join Backup Channel", url=channel_invite)],
        [InlineKeyboardButton("🎬 Watch Ad", callback_data="watch_ad")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "To unlock your content, please:\n"
        "1️⃣ Join our backup channel\n"
        "2️⃣ Tap \"Watch Ad\" below\n\n"
        "The confirm button will appear after you watch the ad.",
        reply_markup=reply_markup,
    )


async def watch_ad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tracks that the user tapped the Watch Ad button at least once, then
    reveals the real ad link plus the 'I've done both' confirm button."""
    query = update.callback_query
    await query.answer()

    context.user_data["ad_watched"] = True

    keyboard = [
        [InlineKeyboardButton("🎬 Open Ad", url=AD_LINK)],
        [InlineKeyboardButton("✅ I've done both", callback_data="check_gate")],
    ]
    await query.edit_message_text(
        "Great — tap below to open the ad, then tap \"I've done both\" once "
        "you've joined the channel and watched it.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def check_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()  # acknowledge the tap so the button stops spinning

    video_key = context.user_data.get("pending_video")
    entry = VIDEO_MAP.get(video_key) if video_key else None
    if entry is None:
        entry = _collected_videos.get(video_key) if video_key else None

    if not video_key or entry is None:
        await query.answer(
            "Something went wrong — please open the original link again.",
            show_alert=True,
        )
        return

    if not context.user_data.get("ad_watched"):
        await query.answer(
            "Please tap \"Watch Ad\" first.",
            show_alert=True,
        )
        return

    channel_id, _ = _entry_channel(entry)

    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        is_member = member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Membership check failed for {user_id} in {channel_id}: {e}")
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

    await send_content(query, context, video_key, entry)


async def send_content(query, context: ContextTypes.DEFAULT_TYPE, video_key: str, entry) -> None:
    chat_id = query.from_user.id
    file_id = _entry_file_id(entry)
    try:
        if file_id and file_id != "PASTE_FILE_ID_HERE":
            await context.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption="Here's your content 🎉",
                protect_content=True,  # disables the save/forward option on this message
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ This video isn't configured yet — check back soon.")
    except Exception as e:
        logger.error(f"Failed to send content to {chat_id}: {e}")
        await context.bot.send_message(chat_id=chat_id, text="Something went wrong sending your content. Please try again.")


async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: /setchannel @channelusername https://t.me/channelusername
    Sets the channel that will be attached to videos sent from now on."""
    user_id = str(update.effective_user.id)
    if not ADMIN_USER_ID or user_id != ADMIN_USER_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n/setchannel @channelusername https://t.me/channelusername\n\n"
            f"Current channel: {_current_channel['channel_id']}"
        )
        return

    _current_channel["channel_id"] = context.args[0]
    _current_channel["channel_invite"] = context.args[1]

    await update.message.reply_text(
        f"✅ Channel set to {context.args[0]}\n"
        f"Videos you send from now on will require joining this channel.\n"
        f"(Make sure the bot is an admin in that channel!)"
    )


async def collect_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: any video sent directly to the bot by ADMIN_USER_ID gets
    auto-assigned the next clipN key, tagged with the current channel
    (set via /setchannel), and stored for later export."""
    user_id = str(update.effective_user.id)

    if not ADMIN_USER_ID or user_id != ADMIN_USER_ID:
        return  # silently ignore videos from anyone who isn't the admin

    file_id = update.message.video.file_id
    key = f"clip{_next_clip_number()}"
    _collected_videos[key] = {
        "file_id": file_id,
        "channel_id": _current_channel["channel_id"],
        "channel_invite": _current_channel["channel_invite"],
    }

    await update.message.reply_text(
        f"✅ Added as {key} (channel: {_current_channel['channel_id']})\n"
        f"Link: https://t.me/{context.bot.username}?start={key}\n\n"
        f"Total collected this session: {len(_collected_videos)}\n"
        f"Send /export when you're done to get the full list."
    )


async def export_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: dumps VIDEO_MAP (existing + collected this session) as
    ready-to-paste JSON for the VIDEO_MAP variable in Railway."""
    user_id = str(update.effective_user.id)

    if not ADMIN_USER_ID or user_id != ADMIN_USER_ID:
        return

    merged = {**VIDEO_MAP, **_collected_videos}

    if not merged:
        await update.message.reply_text("No videos collected yet — send some videos to me first.")
        return

    text = json.dumps(merged)

    chunk_size = 3800
    if len(text) <= chunk_size:
        await update.message.reply_text(f"Paste this into VIDEO_MAP:\n\n<code>{text}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"List is long ({len(merged)} videos) — sending in parts. "
            f"You'll need to manually stitch these into one JSON object."
        )
        for i in range(0, len(text), chunk_size):
            await update.message.reply_text(f"<code>{text[i:i+chunk_size]}</code>", parse_mode="HTML")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("export", export_videos))
    app.add_handler(CommandHandler("setchannel", set_channel))
    app.add_handler(CallbackQueryHandler(watch_ad, pattern="^watch_ad$"))
    app.add_handler(CallbackQueryHandler(check_gate, pattern="^check_gate$"))
    app.add_handler(MessageHandler(filters.VIDEO, collect_video))
    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
