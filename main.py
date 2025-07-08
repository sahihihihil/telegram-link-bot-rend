import os
import json
import uuid
import asyncio
import logging
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
    """Run Flask app for health checks"""
    flask_app.run(host='0.0.0.0', port=PORT)

def keep_alive():
    """Start Flask server in background thread"""
    Thread(target=run_flask, daemon=True).start()

def load_data():
    """Load data from JSON file with error handling"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded {len(data)} links from storage")
                return data
        return {}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading data: {e}")
        return {}

def save_data():
    """Save data to JSON file with error handling"""
    try:
        with open(DATA_FILE, "w", encoding='utf-8') as f:
            json.dump(link_messages, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(link_messages)} links to storage")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def cleanup_expired_links():
    """Remove expired links from storage"""
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

# Global data storage
link_messages = load_data()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command and process shared links"""
    try:
        args = context.args
        user_id = update.effective_user.id
        
        if args:
            code = args[0]
            
            # Clean up expired links before processing
            cleanup_expired_links()
            
            link_data = link_messages.get(code)
            if link_data:
                # Handle both old format (string) and new format (dict)
                if isinstance(link_data, str):
                    message = link_data
                elif isinstance(link_data, dict):
                    message = link_data.get('message', '')
                    created_at = datetime.fromisoformat(link_data['created_at'])
                    if datetime.now() - created_at > timedelta(minutes=30):
                        await update.message.reply_text("❌ This link has expired.")
                        return
                else:
                    await update.message.reply_text("❌ Invalid link format.")
                    return
                
                main_msg = await update.message.reply_text(message)
                warning_msg = await update.message.reply_text("⏳ This file will be deleted after 30 minutes.")
                
                async def delete_messages():
                    await asyncio.sleep(1800)  # 30 minutes
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
            # Different messages for admin and regular users
            if user_id == ADMIN_ID:
                await update.message.reply_text(
                    "👋 Welcome Admin!\n\n"
                    "Available commands:\n"
                    "• Send any message to create a shareable link\n"
                    "• /list - View all active links\n"
                    "• /delete <code> - Delete a specific link\n"
                    "• /cleanup - Remove expired links"
                )
            else:
                await update.message.reply_text("👋 Welcome! Please contact the admin for access.")
    except Exception as e:
        logger.error(f"Error in start handler: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages from admin to create new links"""
    try:
        user_id = update.effective_user.id
        logger.info(f"Message from user {user_id}, admin is {ADMIN_ID}")
        
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔ Unauthorized access.")
            return
        
        text = update.message.text.strip()
        if not text:
            await update.message.reply_text("⚠️ Please send a non-empty message.")
            return
        
        logger.info(f"Creating link for message: {text[:50]}...")
        
        # Clean up expired links before creating new one
        cleanup_expired_links()
        
        code = str(uuid.uuid4())[:8]
        link_messages[code] = {
            'message': text,
            'created_at': datetime.now().isoformat()
        }
        
        logger.info(f"Saving data with code: {code}")
        save_data()
        
        # Get bot username
        try:
            bot_info = await context.bot.get_me()
            bot_username = bot_info.username
            logger.info(f"Bot username: {bot_username}")
        except Exception as e:
            logger.error(f"Error getting bot info: {e}")
            await update.message.reply_text("❌ Error getting bot information.")
            return
        
        link = f"https://t.me/{bot_username}?start={code}"
        
        # Create response without any special formatting that might cause parsing issues
        response_text = (
            "✅ Link generated:\n"
            f"{link}\n\n"
            f"🔗 Code: {code}\n"
            f"⏰ Expires in 30 minutes"
        )
        
        logger.info(f"Sending response: {repr(response_text)}")
        await update.message.reply_text(response_text)
        
        logger.info(f"Created new link with code: {code}")
        
    except Exception as e:
        logger.error(f"Error in admin message handler: {e}")
        logger.error(f"Error details: {str(e)}")
        await update.message.reply_text("❌ An error occurred while creating the link.")

async def list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active links (admin only)"""
    try:
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Unauthorized access.")
            return
        
        # Clean up expired links before listing
        cleanup_expired_links()
        
        if not link_messages:
            await update.message.reply_text("📭 No active links.")
            return
        
        bot_username = (await context.bot.get_me()).username
        response = "📋 Active Links:\n\n"
        
        for code, data in link_messages.items():
            link = f"https://t.me/{bot_username}?start={code}"
            
            if isinstance(data, dict):
                message = data.get('message', '')
                created_at = datetime.fromisoformat(data['created_at'])
                time_left = timedelta(minutes=30) - (datetime.now() - created_at)
                time_left_str = f"{int(time_left.total_seconds() // 60)}m {int(time_left.total_seconds() % 60)}s"
            else:
                message = data
                time_left_str = "Unknown"
            
            # Truncate long messages
            display_message = message[:50] + "..." if len(message) > 50 else message
            
            response += f"🔗 {code}: {link}\n"
            response += f"📝 {display_message}\n"
            response += f"⏰ {time_left_str} remaining\n\n"
        
        await update.message.reply_text(response, disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Error in list handler: {e}")
        await update.message.reply_text("❌ An error occurred while listing links.")

async def delete_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a specific link (admin only)"""
    try:
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Unauthorized access.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Usage: /delete <code>")
            return
        
        code = context.args[0]
        if code in link_messages:
            del link_messages[code]
            save_data()
            await update.message.reply_text(f"✅ Link {code} deleted.")
            logger.info(f"Manually deleted link: {code}")
        else:
            await update.message.reply_text("⚠️ Link not found.")
            
    except Exception as e:
        logger.error(f"Error in delete handler: {e}")
        await update.message.reply_text("❌ An error occurred while deleting the link.")

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually clean up expired links (admin only)"""
    try:
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Unauthorized access.")
            return
        
        initial_count = len(link_messages)
        cleanup_expired_links()
        final_count = len(link_messages)
        removed_count = initial_count - final_count
        
        await update.message.reply_text(f"🧹 Cleanup completed!\nRemoved {removed_count} expired links.")
        
    except Exception as e:
        logger.error(f"Error in cleanup handler: {e}")
        await update.message.reply_text("❌ An error occurred during cleanup.")

async def run_bot():
    """Main bot function"""
    # Validate environment variables
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required!")
        return
    
    if ADMIN_ID == 0:
        logger.error("ADMIN_ID environment variable is required!")
        return
    
    # Start Flask server for health checks
    keep_alive()
    
    try:
        # Build and configure the bot
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Add command handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("list", list_links))
        app.add_handler(CommandHandler("delete", delete_link))
        app.add_handler(CommandHandler("cleanup", cleanup_command))
        
        # Add message handler for admin messages
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message))
        
        logger.info("🤖 Bot is starting...")
        logger.info(f"🔧 Admin ID: {ADMIN_ID}")
        logger.info(f"🌐 Flask server running on port: {PORT}")
        
        # Start the bot with polling
        await app.run_polling(
            poll_interval=3.0,
            timeout=30,
            close_loop=False,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Fatal error starting bot: {e}")
        raise

if __name__ == "__main__":
    # Apply nest_asyncio for environments that need it
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        logger.warning("nest_asyncio not available")
    
    # Run the bot
    try:
        asyncio.run(run_bot())
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise
