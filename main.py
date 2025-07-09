import os
import json
import uuid
import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import Conflict, NetworkError

# Configuration from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_FILE = "data.json"
PORT = int(os.getenv("PORT", "10000"))  # Render.com uses PORT env var

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app for health check
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ Bot is alive!"

@flask_app.route('/health')
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)

def keep_alive():
    Thread(target=run_flask, daemon=True).start()

def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded {len(data)} links from storage")
                return data
        return {}
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        return {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding='utf-8') as f:
            json.dump(link_messages, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(link_messages)} links to storage")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def cleanup_expired_links():
    current_time = datetime.now()
    expired_codes = []
    for code, data in link_messages.items():
        if isinstance(data, dict) and 'created_at' in data:
            created_at = datetime.fromisoformat(data['created_at'])
            if current_time - created_at > timedelta(minutes=30):
                expired_codes.append(code)
    for code in expired_codes:
        del link_messages[code]
        logger.info(f"Removed expired link: {code}")
    if expired_codes:
        save_data()

# Global data
link_messages = load_data()
app_instance = None

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    if app_instance:
        asyncio.create_task(app_instance.stop())
        asyncio.create_task(app_instance.shutdown())
    sys.exit(0)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if args:
            code = args[0]
            cleanup_expired_links()
            link_data = link_messages.get(code)
            if link_data:
                message = link_data.get('message', '') if isinstance(link_data, dict) else link_data
                created_at = datetime.fromisoformat(link_data['created_at']) if isinstance(link_data, dict) else datetime.now()
                if datetime.now() - created_at > timedelta(minutes=30):
                    await update.message.reply_text("❌ This link has expired.")
                    return
                main_msg = await update.message.reply_text(message)
                warning_msg = await update.message.reply_text("⏳ This message will be deleted after 30 minutes.")
                async def delete_messages():
                    await asyncio.sleep(1800)
                    try:
                        await context.bot.delete_message(update.effective_chat.id, main_msg.message_id)
                        await context.bot.delete_message(update.effective_chat.id, warning_msg.message_id)
                        logger.info(f"Deleted messages for code: {code}")
                    except Exception as e:
                        logger.error(f"Error deleting messages: {e}")
                asyncio.create_task(delete_messages())
            else:
                await update.message.reply_text("❌ Invalid or expired link.")
        else:
            if user_id == ADMIN_ID:
                await update.message.reply_text("👋 Welcome Admin! Send a message to generate a link.")
            else:
                await update.message.reply_text("👋 Please contact the admin to get your private link.")
    except Exception as e:
        logger.error(f"Error in start handler: {e}")
        await update.message.reply_text("❌ An error occurred.")

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Unauthorized.")
            return
        text = update.message.text.strip()
        if not text:
            await update.message.reply_text("⚠️ Please send a non-empty message.")
            return
        cleanup_expired_links()
        code = str(uuid.uuid4())[:8]
        link_messages[code] = {'message': text, 'created_at': datetime.now().isoformat()}
        save_data()
        bot_username = (await context.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start={code}"
        await update.message.reply_text(f"✅ Link generated: {link}\n⏰ Expires in 30 minutes.")
    except Exception as e:
        logger.error(f"Error creating link: {e}")
        await update.message.reply_text("❌ An error occurred while creating the link.")

async def run_bot():
    global app_instance
    if not BOT_TOKEN or ADMIN_ID == 0:
        logger.error("BOT_TOKEN and ADMIN_ID are required!")
        return
    keep_alive()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    retry_count = 0
    while retry_count < 3:
        try:
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            app_instance = app
            app.add_handler(CommandHandler("start", start))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message))
            await app.run_polling(poll_interval=3.0, timeout=30, drop_pending_updates=True)
            break
        except (Conflict, NetworkError) as e:
            logger.error(f"Error (attempt {retry_count + 1}): {e}")
            await asyncio.sleep(5)
            retry_count += 1
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            await asyncio.sleep(5)
            retry_count += 1

if __name__ == "__main__":
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
