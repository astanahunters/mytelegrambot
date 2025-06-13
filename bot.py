# Архитектура Telegram Real Estate Bot (до подтверждения «Если всё устраивает — переходи»)
# Минимальная архитектура для закрытого чата и открытого канала, готовая к масштабированию
# Интеграция с Google Sheets, баллы, публикации, жалобы, поддержка расширения

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
    InlineKeyboardMarkup, InlineKeyboardButton
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
MAIN_FILE         = PROJECT_DIR / "main.py"
CHANGELOG         = PROJECT_DIR / "changelog.md"
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
        logger.error(f"Environment variable '{name}' is not set or empty.")
        sys.exit(1)
    return value

# Обязательные переменные окружения
TOKEN              = require_env('BOT_TOKEN')
GOOGLE_CREDENTIALS = require_env('GOOGLE_CREDENTIALS')
SPREADSHEET_NAME   = require_env('SPREADSHEET_NAME')

# Чаты и админ
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
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
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

# --- 3. Утилиты для работы с Google Sheets ---
def get_user_by_id(user_id: int):
    for rec in users_ws.get_all_records():
        if str(rec.get('ID')) == str(user_id):
            return rec
    return None


def get_col_idx_by_name(ws, col_name: str):
    headers = ws.row_values(1)
    for idx, name in enumerate(headers, start=1):
        if name.strip().lower() == col_name.strip().lower():
            return idx
    return None


def update_user_score(user_id: int, delta: int, reason: str):
    records = users_ws.get_all_records()
    for row_idx, rec in enumerate(records, start=2):
        if str(rec.get('ID')) == str(user_id):
            col_bal = get_col_idx_by_name(users_ws, 'баллы')
            new = int(rec.get('баллы', 0)) + delta
            users_ws.update_cell(row_idx, col_bal, new)
            score_ws.append_row([
                user_id, reason, delta,
                datetime.utcnow().isoformat(), 'auto'
            ])
            return new
    return None

async def auto_invite_verified_users():
    records = users_ws.get_all_records()
    col_invited = get_col_idx_by_name(users_ws, 'invited')
    for row_idx, rec in enumerate(records, start=2):
        status = (rec.get('статус') or '').strip().lower()
        invited = (rec.get('invited') or '').strip().lower()
        if status == 'verified' and invited != 'yes':
            link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
            await bot.send_message(rec['ID'],
                f"✅ Верификация пройдена! Вступайте: {link.invite_link}"
            )
            users_ws.update_cell(row_idx, col_invited, 'yes')

# --- 4. FSM для публикаций ---
class PostStates(StatesGroup):
    waiting_photo = State()
    waiting_desc  = State()

# --- 5. Хелпер для проверки ЛС ---
def is_private(m: Message) -> bool:
    return m.chat.type == 'private'

# --- 6. Хендлеры ---
@dp.message(CommandStart())
async def start_cmd(message: Message):
    if not is_private(message):
        return
    user = get_user_by_id(message.from_user.id)
    if user and user.get('статус', '').strip().lower() == 'verified':
        link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
        await message.answer("✅ Вы верифицированы! " + link.invite_link)
    elif user:
        await message.answer("Ждите проверки.")
    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton("📲 Поделиться номером", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Добро пожаловать! Поделитесь номером телефона:",
            reply_markup=kb
        )

@dp.message(F.content_type == 'contact')
async def process_contact(message: Message):
    if not is_private(message):
        return
    c = message.contact
    fio = f"{c.first_name or ''} {c.last_name or ''}".strip()
    users_ws.append_row([
        c.user_id, fio, c.phone_number,
        'waiting', 0, datetime.utcnow().isoformat(),
        '', 'no', 'no'
    ])
    await message.answer("Спасибо! Ваш номер отправлен на проверку.")

@dp.message(Command('rules'))
async def send_rules(message: Message):
    if not is_private(message):
        return await message.answer("⚠️ Используйте эту команду в ЛС.")
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Ознакомился", callback_data="accept_rules")
    )
    await message.answer("📜 Правила сообщества...", reply_markup=kb)

@dp.callback_query(F.data == "accept_rules")
async def accept_rules(cb: types.CallbackQuery):
    await cb.message.edit_reply_markup(None)
    link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
    row_idx = users_ws.find(str(cb.from_user.id)).row
    col_read = get_col_idx_by_name(users_ws, 'Ознакомился')
    col_inv  = get_col_idx_by_name(users_ws, 'invited')
    users_ws.update_cell(row_idx, col_read, 'yes')
    users_ws.update_cell(row_idx, col_inv,  'yes')
    await cb.message.answer(f"✅ Добро пожаловать! {link.invite_link}")

@dp.message(F.chat.id == PRIVATE_CHAT_ID)
async def delete_in_private_chat(msg: Message):
    with contextlib.suppress(Exception):
        await msg.delete()

@dp.message(Command('newpost'))
async def cmd_newpost(message: Message, state: FSMContext):
    if not is_private(message): return
    user = get_user_by_id(message.from_user.id)
    if not user or user.get('статус', '').strip().lower() != 'verified':
        return await message.answer("Публикация доступна только верифицированным.")
    await message.answer("Пришлите фото объекта.")
    await state.set_state(PostStates.waiting_photo)

@dp.message(PostStates.waiting_photo, F.photo)
async def got_photo(message: Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("Теперь пришлите описание:")
    await state.set_state(PostStates.waiting_desc)

@dp.message(PostStates.waiting_desc)
async def got_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    await bot.send_photo(chat_id=CHANNEL_ID, photo=data['photo'], caption=message.text)
    posts_ws.append_row([message.from_user.id, message.text, datetime.utcnow().isoformat()])
    update_user_score(message.from_user.id, +10, 'post')
    await message.answer("✅ Объект опубликован.")
    await state.clear()

@dp.message(Command('cabinet'))
async def show_cabinet(message: Message):
    if not is_private(message):
        return await message.answer("⚠️ Используйте эту команду в ЛС.")
    user = get_user_by_id(message.from_user.id)
    if not user:
        return await message.answer("Вы не зарегистрированы.")
    bal = user.get('баллы', 0)
    status = user.get('статус', '')
    text = (
        f"👤 Ваш профиль:\n"
        f"Баллы: <b>{bal}</b>\n"
        f"Статус: {status}\n"
    )
    await message.answer(text)

@dp.message(Command('approve'))
async def approve_user(message: Message):
    if not is_private(message): return
    if message.from_user.id != YOUR_ADMIN_ID:
        return await message.answer("🚫 Нет доступа.")
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Использование: /approve <ID>")
    uid = int(parts[1])
    row_idx = users_ws.find(str(uid)).row
    col_status  = get_col_idx_by_name(users_ws, 'статус')
    col_invited = get_col_idx_by_name(users_ws, 'invited')
    users_ws.update_cell(row_idx, col_status, 'verified')
    link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
    users_ws.update_cell(row_idx, col_invited, 'yes')
    await bot.send_message(uid, f"✅ Вы верифицированы! {link.invite_link}")
    await message.answer(f"Пользователь {uid} приглашён.")

@dp.message(Command('autoinvite'))
async def autoinvite_command(message: Message):
    if not is_private(message): return
    if message.from_user.id != YOUR_ADMIN_ID:
        return await message.answer("🚫 Нет доступа.")
    await auto_invite_verified_users()
    await message.answer("✅ Приглашения разосланы.")

@dp.message(Command('clean'))
async def clean_cmd(message: Message):
    if message.from_user.id != YOUR_ADMIN_ID:
        return
    await message.answer("Запускаю авто-чистку...")
    await auto_cleaner.main()
    await message.answer("✅ Auto-cleaner завершён.")

# --- 7. Запуск бота ---
if __name__ == '__main__':
    logger.info("🚀 Bot started")
    asyncio.run(dp.start_polling(bot))



"""
ВАЖНО: Это skeleton — основа архитектуры. Все бизнес-правила и сложные FSM-механики можно наращивать шаг за шагом. Код легко расширяется: добавляй обработчики для шаблонов постов, кнопки, баллы, жалобы и т.д. Все хранилища (Google Sheets) — отдельные листы, как обсуждали.

• Для блокировки пересылки постов: бот публикует объекты только сам (без кнопок forward/reply), можно технически ограничить пересылку, делая сообщения не пересылаемыми.
• Для Google Sheets — интеграция через gspread.
• Для реальных фото, FSM, мультишаговых форм — используем aiogram FSMContext (если понадобится, добавлю пример под конкретную бизнес-логику).
• Для публикации в канал — отдельный обработчик (по ссылке на канал).
• Все действия фиксируются (баллы, жалобы, истории).
"""
# === OLD BLOCK (перенесён 2025-06-08) ===



# === OLD BLOCK (перенесён 2025-06-08) ===


