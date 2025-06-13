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

# --- 1. Configuration and Logging ---
PROJECT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = PROJECT_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Required environment variables helper
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error(f"Environment variable '{name}' is not set.")
        sys.exit(1)
    return value

# Load configuration
TOKEN = require_env('BOT_TOKEN')
GOOGLE_CREDENTIALS = require_env('GOOGLE_CREDENTIALS')
SPREADSHEET_NAME = require_env('SPREADSHEET_NAME')

# Chat and admin IDs (defaults used if ENV missing)
DEFAULT_PRIVATE_CHAT_ID = -1002635314764
DEFAULT_CHANNEL_ID = -1002643399672
DEFAULT_YOUR_ADMIN_ID = 7796929428
PRIVATE_CHAT_ID = int(os.getenv('PRIVATE_CHAT_ID', str(DEFAULT_PRIVATE_CHAT_ID)))
CHANNEL_ID = int(os.getenv('CHANNEL_ID', str(DEFAULT_CHANNEL_ID)))
YOUR_ADMIN_ID = int(os.getenv('YOUR_ADMIN_ID', str(DEFAULT_YOUR_ADMIN_ID)))
CONFIRM_TIMEOUT = int(os.getenv('CONFIRM_TIMEOUT', '3600'))

# Initialize bot and dispatcher
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- 2. Google Sheets setup ---
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
try:
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open(SPREADSHEET_NAME)
    users_ws = sh.worksheet('users')
    posts_ws = sh.worksheet('posts')
    complaints_ws = sh.worksheet('complaints')
    score_ws = sh.worksheet('score_history')
    leads_ws = sh.worksheet('leads')
    logger.info('✅ Google Sheet opened')
except Exception as e:
    logger.error('Failed to connect to Google Sheets: %s', e)
    sys.exit(1)

# --- 3. Helpers ---
def get_user_by_id(user_id: int):
    for rec in users_ws.get_all_records():
        if str(rec.get('ID')) == str(user_id):
            return rec
    return None


def get_col(ws, name: str):
    for idx, header in enumerate(ws.row_values(1), start=1):
        if header.strip().lower() == name.lower():
            return idx
    return None

# --- 4. Invite link ---
async def send_one_time_invite(user_id: int):
    try:
        link = (await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)).invite_link
        await bot.send_message(user_id, f"✅ Верификация пройдена! Вступайте: {link}")
    except Exception:
        await bot.send_message(
            user_id,
            "⚠️ Не удалось создать ссылку. Обратитесь к администратору @astanahunters — подключение платное."
        )

# --- 5. FSM States ---
class PostFSM(StatesGroup):
    photo = State()
    price = State()
    district = State()
    area = State()
    rooms = State()
    description = State()

class DealFSM(StatesGroup):
    post_forward = State()
    message = State()

class CommissionFSM(StatesGroup):
    post_forward = State()
    message = State()

# --- 6. Utility ---
def is_private(m: Message):
    return m.chat.type == 'private'

# --- 7. Chat exit handling ---
@dp.chat_member()
async def chat_member_update(event: ChatMemberUpdated):
    if event.chat.id != PRIVATE_CHAT_ID:
        return
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    user = event.new_chat_member.user
    if old in ('member', 'administrator', 'creator') and new in ('left', 'kicked'):
        try:
            row = users_ws.find(str(user.id)).row
            users_ws.update_cell(row, get_col(users_ws, 'статус'), 'waiting')
        except Exception:
            pass
        await bot.send_message(user.id, "Вы точно хотите покинуть astanahunters?")

# --- 8. /start command ---
@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if not is_private(msg):
        return
    user = get_user_by_id(msg.from_user.id)
    if user:
        st = user.get('статус', '').lower()
        if st == 'verified':
            await send_one_time_invite(msg.from_user.id)
            return
        if st == 'waiting':
            await msg.answer("Вы вышли — обратитесь к администратору @astanahunters.")
            return
        await msg.answer("Ждите проверки.")
        return
    # создаём клавиатуру с запросом контакта
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 Поделиться номером", request_contact=True)]],
        resize_keyboard=True
    )
    await msg.answer("Добро пожаловать! Поделитесь номером телефона:", reply_markup=kb)

# --- 9. /post with moderation ---
@dp.message(Command('post'))
async def cmd_post(msg: Message):
    if not is_private(msg):
        return
    await msg.answer("Пришлите фото файлами:", reply_markup=ReplyKeyboardRemove())
    await PostFSM.photo.set()

# PostFSM handlers (photo->price->district->area->rooms->description) omitted for brevity
# They send the post to admin with Yes/No buttons and handle moderation callbacks

# --- 10. /deal command ---
@dp.message(Command('deal'), F.chat.id == PRIVATE_CHAT_ID)
async def cmd_deal(msg: Message):
    await msg.answer("Перешлите пост из канала для запроса просмотра:")
    await DealFSM.post_forward.set()

@dp.message(DealFSM.post_forward, F.forward_from_chat.id == CHANNEL_ID)
async def deal_forward(msg: Message, state: FSMContext):
    # parse original author ID from caption
    lines = msg.caption.splitlines()
    author_id = int(lines[0].split(': ')[1])
    await state.update_data(author=author_id)
    await msg.answer("Напишите текст или отправьте голосовое сообщение с запросом показа:")
    await DealFSM.message.set()

@dp.message(DealFSM.message, F.content_type.in_([ContentType.TEXT, ContentType.VOICE]))
async def deal_message(msg: Message, state: FSMContext):
    data = await state.get_data()
    author = data['author']
    content = msg.text or f"[voice message]"
    await bot.send_message(author, f"Запрос на показ от {msg.from_user.id}: {content}")
    await msg.answer(
        "Запрос отправлен. Показы возможны не ранее чем через час после согласия автора."
    )
    await state.clear()

# --- 11. /commission command ---
@dp.message(Command('commission'), F.chat.id == PRIVATE_CHAT_ID)
async def cmd_commission(msg: Message):
    await msg.answer("Перешлите пост из канала для обсуждения комиссионных:")
    await CommissionFSM.post_forward.set()

@dp.message(CommissionFSM.post_forward, F.forward_from_chat.id == CHANNEL_ID)
async def commission_forward(msg: Message, state: FSMContext):
    lines = msg.caption.splitlines()
    author_id = int(lines[0].split(': ')[1])
    await state.update_data(author=author_id)
    await msg.answer("Напишите текст или отправьте голосовое сообщение по комиссионным:")
    await CommissionFSM.message.set()

@dp.message(CommissionFSM.message, F.content_type.in_([ContentType.TEXT, ContentType.VOICE]))
async def commission_message(msg: Message, state: FSMContext):
    data = await state.get_data()
    author = data['author']
    content = msg.text or f"[voice message]"
    await bot.send_message(author, f"Обсуждение комиссионных от {msg.from_user.id}: {content}")
    await msg.answer("Сообщение отправлено автору поста.")
    await state.clear()

# --- 12. /help command ---
@dp.message(Command('help'), F.chat.id == PRIVATE_CHAT_ID)
async def cmd_help(msg: Message):
    text = (
        "/start — регистрация и вход\n"
        "/post — создать новый пост (модерация)\n"
        "/deal — запрос на показ объекта\n"
        "/commission — обсуждение комиссионных\n"
        "/complain — подать жалобу анонимно\n"
        "/cabinet — ваш кабинет и баллы\n"
        "/help — справка по командам"
    )
    await msg.answer(text)

# --- 13. Run bot ---
if __name__ == '__main__':
    logger.info("🚀 Bot started")
    asyncio.run(dp.start_polling(bot))
