"""
auto_cleaner.py
Авто-чистка OLD-блоков из bot.py + резерв + отчёт в Telegram.

Помечайте устаревший код так:
# === OLD BLOCK START === vX.Y 2025-06-08 (описание)
... старый код ...
# === OLD BLOCK END ===
"""
from dotenv import load_dotenv
load_dotenv()
import re, shutil, datetime, os, asyncio, contextlib
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ── CONFIG ────────────────────────────────────────────────────────────────
PROJECT_DIR    = Path(__file__).resolve().parent
MAIN_FILE      = PROJECT_DIR / "bot.py"            # Поменяйте, если файл иной
CHANGELOG      = PROJECT_DIR / "changelog.md"
BACKUP_DIR     = PROJECT_DIR / "backups"; BACKUP_DIR.mkdir(exist_ok=True)

TELEGRAM_TOKEN = os.getenv(
    "CLEANER_BOT_TOKEN",
    "7980807441:AAEYAi8P0dZ-MmiRLjaIuT6rSjs6XyPcvV8"  # ваш токен
)
ADMIN_ID       = int(os.getenv("CLEANER_ADMIN_ID", "7796929428"))  # ваш ID
CONFIRM_TIMEOUT = 60 * 60   # 1 ч ожидания
# ───────────────────────────────────────────────────────────────────────────

START_RE = re.compile(r"^\s*# === OLD BLOCK START ===.*$")
END_RE   = re.compile(r"^\s*# === OLD BLOCK END ===.*$")


def extract_old_blocks(lines: list[str]) -> list[str]:
    blocks, current, in_block = [], [], False
    for line in lines:
        if START_RE.match(line):
            in_block, current = True, [line]
        elif END_RE.match(line) and in_block:
            current.append(line)
            blocks.append("".join(current))
            in_block = False
        elif in_block:
            current.append(line)
    return blocks


def remove_blocks_and_stub(lines: list[str]) -> list[str]:
    out, in_block = [], False
    for line in lines:
        if START_RE.match(line):
            in_block = True
            out.append(f"# === OLD BLOCK (перенесён {datetime.date.today()}) ===\n")
        elif END_RE.match(line) and in_block:
            in_block = False
        elif not in_block:
            out.append(line)
    return out


async def send_report(bot: Bot, blocks: list[str]) -> bool:
    if not blocks:
        await bot.send_message(ADMIN_ID, "⚠️ Чистка: OLD-блоки не найдены")
        return False

    lines = [f"<b>AstanaHunters auto-clean</b>",
             f"Найдено <b>{len(blocks)}</b> блок(ов):"]
    for i, blk in enumerate(blocks, 1):
        lines.append(f"{i}. {blk.splitlines()[0]}")
    lines.append("\nПодтвердить перенос в <i>changelog.md</i>?\n"
                 "Ответьте: <code>/ok</code> или <code>/cancel</code>")
    await bot.send_message(ADMIN_ID, "\n".join(lines), parse_mode=ParseMode.HTML)
    return True


async def wait_confirmation(bot: Bot) -> bool:
    dp = Dispatcher()
    fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()

    @dp.message(F.chat.id == ADMIN_ID, F.text.in_(['/ok', '/cancel']))
    async def _(m):
        if not fut.done():
            fut.set_result(m.text.lower() == '/ok')
        # FIX: не отвечаем здесь, ответим позже, после stop_polling()

    polling = asyncio.create_task(dp.start_polling(bot))

    try:
        result = await asyncio.wait_for(fut, timeout=CONFIRM_TIMEOUT)
    except asyncio.TimeoutError:
        result = False
    finally:
        await dp.stop_polling()
        with contextlib.suppress(Exception):
            await polling

    # Ответ после корректного закрытия polling
    await bot.send_message(
        ADMIN_ID,
        "Принято ✅" if result else "Отмена ❌"
    )
    return result


async def main():
    bot = Bot(token=TELEGRAM_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    lines = MAIN_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    blocks = extract_old_blocks(lines)

    if not await send_report(bot, blocks):
        await bot.session.close()
        return

    if not await wait_confirmation(bot):
        await bot.session.close()
        return

    # 1. Резервная копия
    backup = BACKUP_DIR / f"bot_backup_{datetime.date.today()}.py"
    shutil.copy(MAIN_FILE, backup)

    # 2. Запись в changelog
    with open(CHANGELOG, "a", encoding="utf-8") as ch:
        ch.write(f"\n\n=== AUTO CLEAN {datetime.datetime.now()} ===\n")
        for blk in blocks:
            ch.write(blk + "\n")

    # 3. Обновляем bot.py
    MAIN_FILE.write_text("".join(remove_blocks_and_stub(lines)), encoding="utf-8")

    await bot.send_message(
        ADMIN_ID,
        f"✅ Чистка завершена.\nРезерв: {backup.name}"
    )
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
