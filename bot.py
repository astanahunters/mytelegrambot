# main.py
import os
import sys
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import contextlib

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ContentType
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove,
    ChatMemberUpdated
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

import gspread
from google.oauth2.service_account import Credentials

# --- auto_cleaner integration ---
import auto_cleaner

# --- 1. Конфигурация и логирование ---
PROJECT_DIR       = Path(__file__).resolve().parent
BACKUP_DIR        = PROJECT_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error(f"Environment variable '{name}' is not set.")
        sys.exit(1)
    return value

TOKEN              = require_env('BOT_TOKEN')
GOOGLE_CREDENTIALS = require_env('GOOGLE_CREDENTIALS')
SPREADSHEET_NAME   = require_env('SPREADSHEET_NAME')

# Чаты и админ с дефолтами
DEFAULT_PRIVATE_CHAT_ID = -1002635314764
DEFAULT_CHANNEL_ID      = -1002643399672
DEFAULT_YOUR_ADMIN_ID   = 7796929428
PRIVATE_CHAT_ID = int(os.getenv('PRIVATE_CHAT_ID', str(DEFAULT_PRIVATE_CHAT_ID)))
CHANNEL_ID      = int(os.getenv('CHANNEL_ID', str(DEFAULT_CHANNEL_ID)))
YOUR_ADMIN_ID   = int(os.getenv('YOUR_ADMIN_ID', str(DEFAULT_YOUR_ADMIN_ID)))
CONFIRM_TIMEOUT = int(os.getenv('CONFIRM_TIMEOUT', '3600'))

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- Google Sheets ---
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
try:
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open(SPREADSHEET_NAME)
    users_ws      = sh.worksheet('users')
    posts_ws      = sh.worksheet('posts')
    complaints_ws = sh.worksheet('complaints')
    score_ws      = sh.worksheet('score_history')
    leads_ws      = sh.worksheet('leads')
    logger.info('✅ Google Sheet открыт!')
except Exception as e:
    logger.error('Не удалось подключиться к Google Sheets: %s', e)
    sys.exit(1)

# --- Helpers ---
def get_user_by_id(user_id: int):
    for rec in users_ws.get_all_records():
        if str(rec.get('ID')) == str(user_id): return rec
    return None

def get_col(ws, name: str):
    hdrs = ws.row_values(1)
    for i, h in enumerate(hdrs, start=1):
        if h.strip().lower() == name.lower(): return i
    return None

# --- One-time invite ---
async def send_one_time_invite(user_id: int):
    try:
        link = (await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)).invite_link
        await bot.send_message(user_id, f"✅ Верификация пройдена! Вступайте: {link}")
    except:
        await bot.send_message(user_id, (
            "⚠️ Не удалось создать ссылку. Обратитесь к администратору @astanahunters — подключение платное."
        ))

# --- FSM States ---
class PostFSM(StatesGroup):
    photo       = State()
    price       = State()
    district    = State()
    area        = State()
    rooms       = State()
    description = State()

class DealFSM(StatesGroup):
    post_forward = State()
    message      = State()
    timestamp    = State()

class CommissionFSM(StatesGroup):
    post_forward = State()
    message      = State()

def is_private(m: Message): return m.chat.type == 'private'

# --- Handling chat exit ---
@dp.chat_member()
async def chat_member_update(event: ChatMemberUpdated):
    if event.chat.id != PRIVATE_CHAT_ID: return
    old, new = event.old_chat_member.status, event.new_chat_member.status
    user = event.new_chat_member.user
    if old in ('member','creator','administrator') and new in ('left','kicked'):
        try:
            row = users_ws.find(str(user.id)).row
            users_ws.update_cell(row, get_col(users_ws,'статус'), 'waiting')
        except: pass
        await bot.send_message(user.id, "Вы точно хотите покинуть astanahunters?")

# --- /start ---
@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if not is_private(msg): return
    u = get_user_by_id(msg.from_user.id)
    if u:
        st = u.get('статус','').lower()
        if st=='verified': return await send_one_time_invite(msg.from_user.id)
        if st=='waiting': return await msg.answer("Вы вышли — обратитесь к администратору @astanahunters.")
        return await msg.answer("Ждите проверки.")
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("📲 Поделиться номером",request_contact=True)]],
        resize_keyboard=True
    )
    await msg.answer("Добро пожаловать!",reply_markup=kb)

# --- /post ---
@dp.message(Command('post'))
async def cmd_post(msg: Message):
    if not is_private(msg): return
    await msg.answer("Пришлите фото файлами:", reply_markup=ReplyKeyboardRemove())
    await PostFSM.photo.set()
# ... PostFSM handlers omitted for brevity ...

# --- /deal ---
@dp.message(Command('deal'), F.chat.id==PRIVATE_CHAT_ID)
async def cmd_deal(msg: Message):
    await msg.answer("Перешлите, пожалуйста, пост из канала для запроса просмотра:")
    await DealFSM.post_forward.set()

@dp.message(DealFSM.post_forward, F.forward_from_chat.id==CHANNEL_ID)
async def deal_forward(msg: Message, state: FSMContext):
    orig_id = msg.forward_from_message_id
    # extract author id from forwarded caption
    lines = msg.caption.splitlines()
    user_id = int(lines[0].split(': ')[1])
    await state.update_data(post_id=orig_id, author=user_id)
    await msg.answer("Напишите текст или отправьте голосовое сообщение с запросом показа:")
    await DealFSM.message.set()

@dp.message(DealFSM.message, F.content_type.in_([ContentType.TEXT, ContentType.VOICE]))
async def deal_message(msg: Message, state: FSMContext):
    data = await state.get_data()
    author = data['author']
    # send to author
    if msg.text:
        await bot.send_message(author, f"Запрос на просмотр от {msg.from_user.id}: {msg.text}")
    else:
        await bot.send_voice(author, voice=msg.voice.file_id,
                              caption=f"Запрос на просмотр от {msg.from_user.id}")
    # set timestamp
    now = datetime.utcnow()
    await state.update_data(timestamp=now.isoformat())
    await msg.answer("Запрос отправлен. Показы возможны не ранее чем через час после согласия автора.")
    await state.clear()

# --- /commission ---
@dp.message(Command('commission'), F.chat.id==PRIVATE_CHAT_ID)
async def cmd_commission(msg: Message):
    await msg.answer("Перешлите, пожалуйста, пост из канала для обсуждения комиссионных:")
    await CommissionFSM.post_forward.set()

@dp.message(CommissionFSM.post_forward, F.forward_from_chat.id==CHANNEL_ID)
async def commission_forward(msg: Message, state: FSMContext):
    lines = msg.caption.splitlines()
    user_id = int(lines[0].split(': ')[1])
    await state.update_data(post_forward=msg.forward_from_message_id, author=user_id)
    await msg.answer("Напишите текст или отправьте голосовое сообщение по комиссионным:")
    await CommissionFSM.message.set()

@dp.message(CommissionFSM.message, F.content_type.in_([ContentType.TEXT, ContentType.VOICE]))
async def commission_message(msg: Message, state: FSMContext):
    data = await state.get_data()
    author = data['author']
    if msg.text:
        await bot.send_message(author, f"Обсуждение комиссионных от {msg.from_user.id}: {msg.text}")
    else:
        await bot.send_voice(author, voice=msg.voice.file_id,
                              caption=f"Обсуждение комиссионных от {msg.from_user.id}")
    await msg.answer("Сообщение отправлено автору поста.")
    await state.clear()

# --- /help ---
@dp.message(Command('help'), F.chat.id==PRIVATE_CHAT_ID)
async def cmd_help(message: Message):
    text = (
        "/start — регистрация и вход\n"
        "/post — создать новый пост (модерация)\n"
        "/deal — запрос на показ объекта\n"
        "/commission — обсуждение комиссионных\n"
        "/complain — подать жалобу анонимно\n"
        "/cabinet — ваш кабинет и баллы\n"
        "/help — справка по командам"
    )
    await message.answer(text)

if __name__=='__main__':
    logger.info("🚀 Bot started")
    asyncio.run(dp.start_polling(bot))
