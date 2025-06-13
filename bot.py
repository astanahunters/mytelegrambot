# main.py
import os
import sys
import logging
import asyncio
from datetime import datetime
from pathlib import Path
import contextlib

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ENV loader
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error(f"Environment variable '{name}' is not set.")
        sys.exit(1)
    return value

# Обязательные переменные окружения
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

# Инициализация бота
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# --- 2. Google Sheets подключение ---
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

# --- 3. Утилиты для Google Sheets ---
def get_user_by_id(user_id: int):
    for rec in users_ws.get_all_records():
        if str(rec.get('ID')) == str(user_id):
            return rec
    return None


def get_col_idx_by_name(ws, col_name: str):
    for idx, name in enumerate(ws.row_values(1), start=1):
        if name.strip().lower() == col_name.strip().lower():
            return idx
    return None


def update_user_score(user_id: int, delta: int, reason: str):
    for row_idx, rec in enumerate(users_ws.get_all_records(), start=2):
        if str(rec.get('ID')) == str(user_id):
            col_bal = get_col_idx_by_name(users_ws, 'баллы')
            new = int(rec.get('баллы', 0)) + delta
            users_ws.update_cell(row_idx, col_bal, new)
            score_ws.append_row([user_id, reason, delta, datetime.utcnow().isoformat(), 'auto'])
            return new
    return None

# --- 4. Отправка одноразовой ссылки ---
async def send_one_time_invite_to_user(user_id: int):
    try:
        link = (await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)).invite_link
        await bot.send_message(user_id, f"✅ Верификация пройдена! Вступайте: {link}")
    except Exception:
        await bot.send_message(user_id, (
            "⚠️ Не удалось создать ссылку. "
            "Обратитесь к администратору @astanahunters — подключение платное."
        ))

# --- 5. FSM для постов ---
class PostStates(StatesGroup):
    waiting_photo = State()
    waiting_desc  = State()

# --- 6. FSM для жалоб ---
class ComplainStates(StatesGroup):
    target     = State()
    evidence   = State()
    description= State()

# --- 7. FSM для сделок ---
class DealStates(StatesGroup):
    partner = State()
    terms   = State()

# --- 8. Проверка лички ---
def is_private(m: Message) -> bool:
    return m.chat.type == 'private'

# --- 9. Обработка ухода из чата ---
@dp.chat_member()
async def on_chat_member_update(event: ChatMemberUpdated):
    if event.chat.id != PRIVATE_CHAT_ID:
        return
    old, new = event.old_chat_member.status, event.new_chat_member.status
    user = event.new_chat_member.user
    if old in ('member','administrator','creator') and new in ('left','kicked'):
        # обновляем в таблице статус waiting
        try:
            row = users_ws.find(str(user.id)).row
            col = get_col_idx_by_name(users_ws, 'статус')
            users_ws.update_cell(row, col, 'waiting')
        except:
            pass
        # спрашиваем подтверждение
        await bot.send_message(user.id, "Вы точно хотите покинуть astanahunters?")

# --- 10. Хендлеры команд ---
@dp.message(CommandStart())
async def start_cmd(message: Message):
    if not is_private(message): return
    user = get_user_by_id(message.from_user.id)
    if user:
        status = user.get('статус','').strip().lower()
        if status == 'verified':
            await send_one_time_invite_to_user(message.from_user.id)
        elif status == 'waiting':
            await message.answer("Вы вышли из чата — обратитесь к администратору @astanahunters.")
        else:
            await message.answer("Ждите проверки.")
    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📲 Поделиться номером", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await message.answer("Добро пожаловать! Поделитесь номером телефона:", reply_markup=kb)

@dp.message(F.content_type == 'contact')
async def process_contact(message: Message):
    if not is_private(message): return
    c = message.contact
    fio = f"{c.first_name or ''} {c.last_name or ''}".strip()
    users_ws.append_row([c.user_id, fio, c.phone_number, 'waiting', 0,
                         datetime.utcnow().isoformat(), '', 'no', 'no'])
    await message.answer("Спасибо! Ваш номер отправлен на проверку.")

@dp.message(Command('rules'))
async def send_rules(message: Message):
    if not is_private(message): return await message.answer("⚠️ В ЛС бота.")
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Ознакомился", callback_data="accept_rules")
    )
    await message.answer("📜 Правила сообщества...", reply_markup=kb)

@dp.callback_query(F.data=='accept_rules')
async def accept_rules(cb: types.CallbackQuery):
    await cb.message.edit_reply_markup(None)
    await send_one_time_invite_to_user(cb.from_user.id)
    row = users_ws.find(str(cb.from_user.id)).row
    users_ws.update_cell(row, get_col_idx_by_name(users_ws,'Ознакомился'), 'yes')
    users_ws.update_cell(row, get_col_idx_by_name(users_ws,'invited'), 'yes')

@dp.message(F.chat.id==PRIVATE_CHAT_ID)
async def delete_in_private(msg: Message):
    with contextlib.suppress(Exception): await msg.delete()

# /newpost handled elsewhere...

# --- /complain ---
@dp.message(Command('complain'))
async def cmd_complain(message: Message):
    if not is_private(message): return
    await message.answer("Введите ID или @username участника, на которого жалуетесь:")
    await message.delete()
    await message.answer("Команда принята.")
    await message.answer("Пожалуйста, прикрепите доказательства (фото, скриншоты, переписку):")
    await dp.current_state().set_state(ComplainStates.evidence)

@dp.message(ComplainStates.evidence, F.any())
async def complain_evidence(message: Message, state: FSMContext):
    await state.update_data(evidence=message)
    await message.answer("Опишите ситуацию:")
    await state.set_state(ComplainStates.description)

@dp.message(ComplainStates.description)
async def complain_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    # сохраняем в таблицу complaints
    complaints_ws.append_row([
        message.from_user.id,
        data.get('evidence').message_id,
        message.text,
        datetime.utcnow().isoformat()
    ])
    await message.answer("Спасибо, ваша жалоба отправлена администраторам.")
    await state.clear()

# --- /deal ---
@dp.message(Command('deal'))
async def cmd_deal(message: Message):
    if not is_private(message): return
    await message.answer("Введите ID контрагента сделки:")
    await dp.current_state().set_state(DealStates.partner)

@dp.message(DealStates.partner)
async def deal_partner(message: Message, state: FSMContext):
    await state.update_data(partner=message.text)
    await message.answer("Опишите договорённость (комиссия, показ и т.д.):")
    await state.set_state(DealStates.terms)

@dp.message(DealStates.terms)
async def deal_terms(message: Message, state: FSMContext):
    data = await state.get_data()
    leads_ws.append_row([
        message.from_user.id,
        data.get('partner'),
        message.text,
        datetime.utcnow().isoformat()
    ])
    await message.answer("Сделка зафиксирована.")
    await state.clear()

# --- /help ---
@dp.message(Command('help'))
async def cmd_help(message: Message):
    text = (
        "/start — регистрация и вход\n"
        "/rules — правила и приглашение\n"
        "/complain — подать жалобу анонимно\n"
        "/deal — зафиксировать договорённость\n"
        "/cabinet — ваш кабинет и баллы\n"
        "/help — справка"
    )
    await message.answer(text)

# --- 11. Запуск ---
if __name__ == '__main__':
    logger.info("🚀 Bot started")
    asyncio.run(dp.start_polling(bot))
