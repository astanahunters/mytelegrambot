# Архитектура Telegram Real Estate Bot (до подтверждения «Если всё устраивает — переходи»)
# Минимальная архитектура для закрытого чата и открытого канала, готовая к масштабированию
# Интеграция с Google Sheets, баллы, публикации, жалобы, поддержка расширения

import logging
import os
from datetime import datetime
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage

import gspread
from google.oauth2.service_account import Credentials

# --- Конфигурация ---
TOKEN = os.getenv('BOT_TOKEN', '7824358394:AAFQ9Kz4G760C4qU_4NYyRgc9IOfs7qN3NA')
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "/etc/secrets/GOOGLE_CREDENTIALS.json").strip()
print(f"GOOGLE_CREDENTIALS = {GOOGLE_CREDENTIALS}")
SPREADSHEET_NAME = 'astanahunters_template'
PRIVATE_CHAT_ID = -1002635314764  # Закрытый чат
CHANNEL_ID = -1002643399672       # <-- сюда публикуются посты
INVITE_LINK = "Теперь вход платный! Пишите админу ЛС @astanahunters"
YOUR_ADMIN_ID = 7796929428

# --- Google Sheets подключение ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open(SPREADSHEET_NAME)
users_ws = sh.worksheet('users')
print('Sheet открыт!')
posts_ws = sh.worksheet('posts')
complaints_ws = sh.worksheet('complaints')
score_ws = sh.worksheet('score_history')
leads_ws = sh.worksheet('leads')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# --- Проверка: команда пришла из лички? ---
def is_private(message: Message) -> bool:
    return message.chat.type == 'private'

# --- Вспомогательные функции ---
def get_user_by_id(user_id: int):
    records = users_ws.get_all_records()
    for rec in records:
        if str(rec['ID']) == str(user_id):
            return rec
    return None

def update_user_score(user_id: int, delta: int, reason: str):
    records = users_ws.get_all_records()
    for idx, rec in enumerate(records):
        if str(rec['ID']) == str(user_id):
            new_score = int(rec['баллы']) + delta
            users_ws.update_cell(idx + 2, 5, new_score)  # Столбец баллы (5)
            score_ws.append_row([user_id, reason, delta, datetime.now().isoformat(), 'auto'])
            return new_score
    return None

# Получить номер столбца по заголовку (для invited)
def get_col_idx_by_name(ws, col_name):
    headers = ws.row_values(1)
    for idx, name in enumerate(headers):
        if name.strip().lower() == col_name.strip().lower():
            return idx + 1
    return None

# --- Авторассылка приглашений verified пользователям ---
async def auto_invite_verified_users():
    records = users_ws.get_all_records()
    invited_col = get_col_idx_by_name(users_ws, 'invited')
    for idx, rec in enumerate(records):
        if rec['статус'].strip().lower() == 'verified' and not rec.get('invited'):
            try:
                await bot.send_message(rec['ID'], f"✅ Вы верифицированы!\nВступите в закрытый чат: {INVITE_LINK}")
                users_ws.update_cell(idx + 2, invited_col, 'yes')
                logging.info(f"Приглашение отправлено: {rec['ID']}")
            except Exception as e:
                logging.error(f"Ошибка при отправке приглашения {rec['ID']}: {e}")

# --- Автоудаление команд и любых сообщений в группе/канале ---
@dp.message(F.text.startswith('/'))
async def delete_commands_in_group(message: Message):
    if not is_private(message):
        try:
            await message.delete()
        except Exception:
            pass

@dp.message(F.text)
async def delete_messages_in_group(message: Message):
    if not is_private(message):
        try:
            await message.delete()
        except Exception:
            pass

@dp.message(F.photo)
async def delete_photos_in_group(message: Message):
    if not is_private(message):
        try:
            await message.delete()
        except Exception:
            pass

# --- Команды (отвечают только в ЛС) ---
@dp.message(CommandStart())
async def start_cmd(message: Message):
    if not is_private(message):
        return
    try:
        user = get_user_by_id(message.from_user.id)
        if user:
            if user['статус'].strip().lower() == 'verified':
                await message.answer(
                    f'✅ Вы верифицированы! Вот ссылка для вступления в закрытый чат:\n{INVITE_LINK}'
                )
            else:
                await message.answer('Вы уже отправили номер. Ожидайте проверки.')
        else:
            reply_kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Поделиться номером", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await message.answer(
                'Добро пожаловать! Пожалуйста, подтвердите ваш рабочий телефон для доступа к чату.',
                reply_markup=reply_kb
            )
    except Exception as e:
        await message.answer(f"Произошла ошибка: {e}")

@dp.message(F.contact)
async def process_contact(message: Message):
    if not is_private(message):
        return
    contact = message.contact
    users_ws.append_row([
        contact.user_id, contact.first_name, contact.phone_number, 'waiting', 20, datetime.now().isoformat(), '', ''
    ])
    await message.answer('Спасибо, ваш номер отправлен на проверку администратору.')

@dp.message(Command('rules'))
async def send_rules(message: types.Message):
    if not is_private(message):
        await message.answer("⚠️ Используйте эту команду в личке с ботом.")
        return
    rules_text = "Тут текст правил...\n\nНажмите кнопку, если согласны:"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Ознакомился", callback_data="accept_rules")]
        ]
    )
    await message.answer(rules_text, reply_markup=keyboard)

@dp.callback_query(F.data == "accept_rules")
async def process_accept_rules(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    # TODO: Сохрани в Google Sheets или базе, что user_id ознакомился
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Спасибо! Вы ознакомились с правилами и получили доступ к чату.")

# --- Публикация фото объекта: только в ЛС! Публикуется в канал ---
@dp.message(F.photo)
async def handle_photo(message: Message):
    if not is_private(message):
        return
    user = get_user_by_id(message.from_user.id)
    if not user or user['статус'].strip().lower() != 'verified':
        await message.answer('Публикация доступна только верифицированным участникам.')
        return
    await message.answer('Введите короткое описание объекта (без номера и агентства):')
    # Здесь можно реализовать FSM: ожидание следующего сообщения для публикации в канал

    # Пример публикации фото в канал (раскомментируй после добавления FSM для описания):
    # await bot.send_photo(CHANNEL_ID, photo=message.photo[-1].file_id, caption="Описание")

@dp.message(Command('cabinet'))
async def show_cabinet(message: Message):
    if not is_private(message):
        await message.answer("⚠️ Используйте эту команду в личке с ботом.")
        return
    user = get_user_by_id(message.from_user.id)
    if user:
        await message.answer(
            f'👤 Ваш профиль\nБаллы: <b>{user["баллы"]}</b>\nСтатус: {user["статус"]}\nЖалобы: {user.get("жалобы","-")}'
        )
    else:
        await message.answer('Вы не зарегистрированы.')

# Генерация одноразовой ссылки
async def generate_one_time_invite():
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=PRIVATE_CHAT_ID,
            member_limit=1
        )
        return invite_link.invite_link
    except Exception as e:
        logger.error(f"Ошибка при создании пригласительной ссылки: {e}")
        return None

@dp.message(Command('approve'))
async def approve_user(message: types.Message):
    if not is_private(message):
        await message.answer("⚠️ Используйте эту команду в личке с ботом.")
        return
    if message.from_user.id != YOUR_ADMIN_ID:
        await message.answer("Недостаточно прав.")
        return
    try:
        user_id = int(message.text.split()[1])
        user = get_user_by_id(user_id)
        if user and user['статус'].strip().lower() == 'verified':
            invite_link = await generate_one_time_invite()
            if invite_link:
                await bot.send_message(user_id, f"✅ Вы верифицированы!\nВступите в закрытый чат: {invite_link}")
                await message.answer(f"Пользователь с ID {user_id} приглашён в чат.")
            else:
                await message.answer("Не удалось сгенерировать ссылку.")
        else:
            await message.answer("Пользователь не найден или не верифицирован.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(Command('autoinvite'))
async def autoinvite_command(message: types.Message):
    if not is_private(message):
        await message.answer("⚠️ Используйте эту команду в личке с ботом.")
        return
    if message.from_user.id != YOUR_ADMIN_ID:
        await message.answer("Недостаточно прав для выполнения этой команды.")
        return
    await auto_invite_verified_users()
    await message.answer("Рассылка приглашений завершена!")

# --- Запуск ---
async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())


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


