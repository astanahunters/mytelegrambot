# Архитектура Telegram Real Estate Bot (до подтверждения «Если всё устраивает — переходи»)
# Минимальная архитектура для закрытого чата и открытого канала, готовая к масштабированию
# Интеграция с Google Sheets, баллы, публикации, жалобы, поддержка расширения

# main.py
import os
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
BACKUP_DIR        = PROJECT_DIR / "backups"; BACKUP_DIR.mkdir(exist_ok=True)

# Основные переменные из .env
TOKEN             = os.getenv('BOT_TOKEN')
GOOGLE_CREDENTIALS= os.getenv('GOOGLE_CREDENTIALS').strip()
SPREADSHEET_NAME  = os.getenv('SPREADSHEET_NAME')
PRIVATE_CHAT_ID   = int(os.getenv('PRIVATE_CHAT_ID'))
CHANNEL_ID        = int(os.getenv('CHANNEL_ID'))
YOUR_ADMIN_ID     = int(os.getenv('YOUR_ADMIN_ID'))

# Переменные для авто-чистки
CLEANER_ADMIN_ID  = YOUR_ADMIN_ID  # единый админ
CONFIRM_TIMEOUT   = int(os.getenv('CONFIRM_TIMEOUT', 3600))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

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
creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open(SPREADSHEET_NAME)
users_ws      = sh.worksheet('users')
posts_ws      = sh.worksheet('posts')
complaints_ws = sh.worksheet('complaints')
score_ws      = sh.worksheet('score_history')
leads_ws      = sh.worksheet('leads')
logger.info('✅ Google Sheet открыт!')

# --- 3. Утилиты для таблицы ---
def get_user_by_id(user_id: int):
    for rec in users_ws.get_all_records():
        if str(rec.get('ID') or rec.get('user_id')) == str(user_id):
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
    for idx, rec in enumerate(records, start=2):
        if str(rec.get('ID') or rec.get('user_id')) == str(user_id):
            col = get_col_idx_by_name(users_ws, 'баллы') or get_col_idx_by_name(users_ws, 'score')
            new = int(rec.get('баллы', rec.get('score', 0))) + delta
            users_ws.update_cell(idx, col, new)
            score_ws.append_row([user_id, reason, delta, datetime.utcnow().isoformat(), 'auto'])
            return new
    return None


async def auto_invite_verified_users():
    records = users_ws.get_all_records()
    inv_col = get_col_idx_by_name(users_ws, 'invited')
    status_col = get_col_idx_by_name(users_ws, 'status') or get_col_idx_by_name(users_ws, 'verified')
    for i, rec in enumerate(records, start=2):
        if rec.get('status','').strip().lower()=='verified' and rec.get('invited','').strip().lower()!='yes':
            link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
            # Исправлено: f-string на одной строке
            await bot.send_message(rec['ID'], f"✅ Верификация пройдена! Вступайте: {link.invite_link}")
            users_ws.update_cell(i, inv_col, 'yes')

# --- 4. FSM для публикаций ---
class PostStates(StatesGroup):
    waiting_photo = State()
    waiting_desc  = State()

# --- 5. Проверка ЛС ---
def is_private(m: Message) -> bool:
    return m.chat.type == 'private'

# --- 6. Хендлеры ---
@dp.message(CommandStart())
async def start_cmd(message: Message):
    if not is_private(message): return
    user = get_user_by_id(message.from_user.id)
    if user:
        if user.get('status','').strip().lower()=='verified':
            link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
            await message.answer(f"✅ Вы верифицированы!\n{link.invite_link}")
        else:
            await message.answer("Ждите проверки.")
    else:
        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(KeyboardButton("📲 Поделиться номером", request_contact=True))
        await message.answer("Поделитесь номером:", reply_markup=kb)


@dp.message(F.content_type=='contact')
async def process_contact(message: Message):
    if not is_private(message): return
    c = message.contact
    users_ws.append_row([c.user_id, c.phone_number, 'waiting', 'no', 0, datetime.utcnow().isoformat()])
    await message.answer("Номер отправлен на проверку.")


@dp.message(Command('rules'))
async def send_rules(message: Message):
    if not is_private(message): return await message.answer("Команда в ЛС.")
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Ознакомился", callback_data="accept_rules"))
    await message.answer("📜 Правила...", reply_markup=kb)


@dp.callback_query(F.data=="accept_rules")
async def accept_rules(cb: types.CallbackQuery):
    await cb.message.edit_reply_markup(None)
    link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
    idx = users_ws.find(str(cb.from_user.id)).row
    users_ws.update_cell(idx, get_col_idx_by_name(users_ws,'invited'), 'yes')
    await cb.message.answer(f"✅ Добро пожаловать!\n{link.invite_link}")


@dp.message(F.chat.id==PRIVATE_CHAT_ID)
async def delete_in_private_chat(msg: Message):
    with contextlib.suppress(Exception):
        await msg.delete()


@dp.message(Command('newpost'))
async def cmd_newpost(msg: Message, state: FSMContext):
    if not is_private(msg): return
    u = get_user_by_id(msg.from_user.id)
    if not u or u.get('status','').strip().lower()!='verified':
        return await msg.answer("Только для verified.")
    await msg.answer("Пришлите фото.")
    await state.set_state(PostStates.waiting_photo)


@dp.message(PostStates.waiting_photo, F.photo)
async def got_photo(msg: Message, state: FSMContext):
    await state.update_data(photo=msg.photo[-1].file_id)
    await msg.answer("Теперь описание:")
    await state.set_state(PostStates.waiting_desc)


@dp.message(PostStates.waiting_desc)
async def got_desc(msg: Message, state: FSMContext):
    data = await state.get_data()
    await bot.send_photo(chat_id=CHANNEL_ID, photo=data['photo'], caption=msg.text)
    posts_ws.append_row([msg.from_user.id, msg.text, datetime.utcnow().isoformat()])
    update_user_score(msg.from_user.id, +10, 'post')
    await msg.answer("Опубликовано.")
    await state.clear()


@dp.message(Command('cabinet'))
async def show_cabinet(msg: Message):
    if not is_private(msg): return
    u = get_user_by_id(msg.from_user.id)
    if not u: return await msg.answer("Не зарегистрированы.")
    score = u.get('score') or u.get('баллы',0)
    text = f"👤 Рейтинг: <b>{score}</b>\n"
    await msg.answer(text)


@dp.message(Command('approve'))
async def approve_user(message: Message):
    if not is_private(message): return
    if message.from_user.id!=YOUR_ADMIN_ID: return
    parts=message.text.split()
    if len(parts)!=2: return await message.answer("/approve <id>")
    uid=int(parts[1]); row=users_ws.find(str(uid)).row
    users_ws.update_cell(row, get_col_idx_by_name(users_ws,'status'),'verified')
    link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHAT_ID, member_limit=1)
    users_ws.update_cell(row, get_col_idx_by_name(users_ws,'invited'),'yes')
    await bot.send_message(uid, f"✅ Verified!\n{link.invite_link}")
    await message.answer(f"Пользователь {uid} приглашён.")


@dp.message(Command('autoinvite'))
async def autoinvite_command(message: Message):
    if not is_private(message): return
    if message.from_user.id!=YOUR_ADMIN_ID: return
    await auto_invite_verified_users()
    await message.answer("Рассылка завершена.")


@dp.message(Command('clean'))
async def clean_cmd(message: Message):
    if message.from_user.id!=YOUR_ADMIN_ID: return
    await message.answer("Запускаю авто-чистку...")
    await auto_cleaner.main()
    await message.answer("Auto-cleaner завершён.")

# --- 7. Старт поллинга и backup-trigger ---
if __name__=='__main__':
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


