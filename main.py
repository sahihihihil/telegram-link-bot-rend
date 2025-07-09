```python
# Updated main.py with webhook support for Render (no more polling conflicts)
import os
import json
import uuid
import logging
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import asyncio

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_FILE = "data.json"
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # should be set to https://your-app.onrender.com

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ Bot is alive!"

@flask_app.route('/health')
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

link_messages = {}
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r", encoding='utf-8') as f:
            link_messages = json.load(f)
    except:
        link_messages = {}

def save_data():
    with open(DATA_FILE, "w", encoding='utf-8') as f:
        json.dump(link_messages, f, ensure_ascii=False, indent=2)

def cleanup_expired_links():
    current_time = datetime.now()
    expired_codes = []
    for code, data in link_messages.items():
        created_at = datetime.fromisoformat(data['created_at'])
        if current_time - created_at > timedelta(minutes=30):
            expired_codes.append(code)
    for code in expired_codes:
        del link_messages[code]
    if expired_codes:
        save_data()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if args:
        code = args[0]
        cleanup_expired_links()
        data = link_messages.get(code)
        if data:
            created_at = datetime.fromisoformat(data['created_at'])
            if datetime.now() - created_at > timedelta(minutes=30):
                await update.message.reply_text("❌ This link has expired.")
                return
            message = data['message']
            sent_message = await update.message.reply_text(message)
            warning_message = await update.message.reply_text("⏳ This message will auto-delete in 30 minutes.")
            async def delete_later():
                await asyncio.sleep(1800)
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=sent_message.message_id)
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=warning_message.message_id)
                except:
                    pass
            asyncio.create_task(delete_later())
        else:
            await update.message.reply_text("❌ Invalid or expired link.")
    else:
        if user_id == ADMIN_ID:
            await update.message.reply_text("👋 Send a message to generate a shareable 30-minute link.")
        else:
            await update.message.reply_text("👋 Please contact admin to get your private link.")

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(f"✅ Link generated:\n{link}\n⏰ Expires in 30 minutes.")

async def main():
    if not BOT_TOKEN or not WEBHOOK_URL:
        logger.error("BOT_TOKEN and WEBHOOK_URL environment variables are required.")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message))
    bot = Bot(BOT_TOKEN)
    await bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")

    @flask_app.route('/webhook', methods=['POST'])
    def webhook():
        update = Update.de_json(request.get_json(force=True), bot)
        asyncio.create_task(app.process_update(update))
        return "OK"

    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
```
