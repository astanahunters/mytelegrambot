# Архитектура Telegram Real Estate Bot (до подтверждения «Если всё устраивает — переходи»)
# Минимальная архитектура для закрытого чата и открытого канала, готовая к масштабированию
# Интеграция с Google Sheets, баллы, публикации, жалобы, поддержка расширения

import logging
import os
from datetime import datetime  # <-- добавь это!
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder


# Google Sheets (gspread)
import gspread
from google.oauth2.service_account import Credentials

# --- Конфигурация ---
TOKEN = os.getenv('BOT_TOKEN', '7824358394:AAFQ9Kz4G760C4qU_4NYyRgc9IOfs7qN3NA')  # Рекомендуется хранить в переменных окружения
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "/etc/secrets/GOOGLE_CREDENTIALS.json").strip()
print(f"GOOGLE_CREDENTIALS = {GOOGLE_CREDENTIALS}")
SPREADSHEET_NAME = 'astanahunters_template'  # Название вашей таблицы
PRIVATE_CHAT_ID = -1001234567890  # Заменить на свой chat_id закрытого чата

# --- Google Sheets подключение ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open(SPREADSHEET_NAME)
users_ws = sh.worksheet('users')
print('Sheet открыт!') # Эксперимент
posts_ws = sh.worksheet('posts')
complaints_ws = sh.worksheet('complaints')
score_ws = sh.worksheet('score_history')
leads_ws = sh.worksheet('leads')

# --- Логирование (для отладки и аудита) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# --- Инициализация бота и диспетчера ---
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# --- Помощник: получить пользователя из Google Sheets ---
def get_user_by_id(user_id: int):
    records = users_ws.get_all_records()
    for rec in records:
        if str(rec['ID']) == str(user_id):
            return rec
    return None

# --- Помощник: обновить баллы пользователя ---
def update_user_score(user_id: int, delta: int, reason: str):
    records = users_ws.get_all_records()
    for idx, rec in enumerate(records):
        if str(rec['ID']) == str(user_id):
            new_score = int(rec['баллы']) + delta
            users_ws.update_cell(idx+2, 5, new_score)  # Столбец баллы (5)
            score_ws.append_row([user_id, reason, delta, types.datetime.datetime.now().isoformat(), 'auto'])
            return new_score
    return None

# --- ВСТАВЬ СЮДА функцию ---
async def send_invite_if_verified(user_id: int, status: str):
    if status == "verified":
        invite = await bot.create_chat_invite_link(
            chat_id=PRIVATE_CHAT_ID,  # <- chat_id из переменной
            member_limit=1
        )
        await bot.send_message(
            user_id,
            f"Ваша заявка одобрена! Вот ссылка: {invite.invite_link}\nСсылка работает только 1 раз."
        )
    else:
        await bot.send_message(user_id, "Ваш номер не подтверждён.")

# --- Команда /start ---
@dp.message(CommandStart())
async def start_cmd(message: Message):
    user = get_user_by_id(message.from_user.id)
    if user:
        await message.answer('Вы уже верифицированы. Для помощи используйте /help')
    else:
        # Ввод номера телефона (правильная ReplyKeyboard!)
        from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
        reply_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Поделиться номером", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            'Добро пожаловать! Пожалуйста, подтвердите ваш рабочий телефон для доступа к чату.',
            reply_markup=reply_kb
        )

# --- Обработка контакта (ввод телефона) ---
@dp.message(F.contact)
async def process_contact(message: Message):
    contact = message.contact
    # TODO: Ручная проверка админом! Пока просто сохраняем
    users_ws.append_row([
        contact.user_id, contact.first_name, contact.phone_number, 'waiting', 20, datetime.now().isoformat(), ''
    ])
    await message.answer('Спасибо, ваш номер отправлен на проверку администратору.')

# --- Шаблон публикации объекта ---
async def ask_post_info(message: Message):
    # Тут будут кнопки выбора типа объекта, района, ввод фото и т.д.
    # Для примера: только фото и текст
    await message.answer('Загрузите фото объекта:')

# --- Обработка фото объекта ---
@dp.message(F.photo)
async def handle_photo(message: Message):
    # Проверить: верифицирован ли пользователь
    user = get_user_by_id(message.from_user.id)
    if not user or user['статус'] != 'verified':
        await message.answer('Публикация доступна только верифицированным участникам.')
        return
    # Запросить описание
    await message.answer('Введите короткое описание объекта (без номера и агентства):')
    # Сохранить фото во временное хранилище (реализовать FSM, если нужно)
    # ...

# --- Просмотр “Личный кабинет” ---
@dp.message(Command('cabinet'))
async def show_cabinet(message: Message):
    user = get_user_by_id(message.from_user.id)
    if user:
        await message.answer(
            f'👤 Ваш профиль\nБаллы: <b>{user["баллы"]}</b>\nСтатус: {user["статус"]}\nЖалобы: {user["жалобы"]}'
        )
    else:
        await message.answer('Вы не зарегистрированы.')

# --- ВСТАВЬ СЮДА хендлер ---
@dp.message(Command("invite"))
async def invite_user(message: Message):
    user = get_user_by_id(message.from_user.id)
    if user:
        await send_invite_if_verified(message.from_user.id, user["статус"])
    else:
        await message.answer("Пользователь не найден.")


# --- Ошибка: общий обработчик ---
#@dp.errors()
#async def error_handler(update, exception):
#    logger.error(f"Ошибка: {exception} | update: {update}")
#    if isinstance(update, types.Message):
#        await update.answer("Произошла ошибка, команда уже знает!")
#    return True

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


