import asyncio
import logging
import os
import tempfile
from pathlib import Path

from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("converter-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MAX_DOWNLOAD = 20 * 1024 * 1024  # حد تيليجرام لتنزيل الملفات عبر الـ API العادي

# ---------- تصنيف الصيغ ----------
CATEGORIES = {
    "image": {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tiff"},
    "audio": {"mp3", "wav", "ogg", "oga", "m4a", "flac", "aac", "opus"},
    "video": {"mp4", "avi", "mkv", "mov", "webm"},
    "doc": {"doc", "docx", "odt", "rtf", "txt", "ppt", "pptx", "odp"},
    "sheet": {"xls", "xlsx", "csv", "ods"},
    "pdf": {"pdf"},
}

TARGETS = {
    "image": ["png", "jpg", "webp", "bmp", "gif", "pdf"],
    "audio": ["mp3", "wav", "ogg", "m4a", "flac"],
    "video": ["mp4", "avi", "mkv", "webm", "gif", "mp3"],
    "doc": ["pdf", "docx", "odt", "txt"],
    "sheet": ["pdf", "xlsx", "csv", "ods"],
    "pdf": ["docx"],
}

OFFICE_FILTERS = {"txt": "txt:Text", "csv": "csv"}

office_lock = asyncio.Lock()  # LibreOffice لا يعمل جيداً بعمليات متزامنة


def get_category(ext: str):
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return None


# ---------- دوال التحويل ----------
async def run_cmd(*cmd, timeout=300):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("انتهت مهلة التحويل")
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-500:] or "فشل التحويل")


def convert_image(src: Path, dst: Path):
    with Image.open(src) as img:
        fmt = dst.suffix.lower().lstrip(".")
        if fmt in ("jpg", "jpeg", "pdf", "bmp") and img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        pil_format = {"jpg": "JPEG", "pdf": "PDF"}.get(fmt, fmt.upper())
        img.save(dst, pil_format)


async def convert_ffmpeg(src: Path, dst: Path):
    fmt = dst.suffix.lower().lstrip(".")
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if fmt == "gif":
        cmd += ["-vf", "fps=12,scale=480:-1:flags=lanczos", "-an"]
    elif fmt == "mp3":
        cmd += ["-vn", "-q:a", "2"]
    elif fmt in ("mp4",):
        cmd += ["-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd.append(str(dst))
    await run_cmd(*cmd)


async def convert_office(src: Path, dst: Path):
    fmt = dst.suffix.lower().lstrip(".")
    target = OFFICE_FILTERS.get(fmt, fmt)
    async with office_lock:
        await run_cmd(
            "soffice", "--headless", "--convert-to", target,
            "--outdir", str(dst.parent), str(src),
        )
    if not dst.exists():
        raise RuntimeError("لم يُنتج LibreOffice الملف المطلوب")


async def convert_pdf_to_docx(src: Path, dst: Path):
    from pdf2docx import Converter

    def work():
        cv = Converter(str(src))
        try:
            cv.convert(str(dst))
        finally:
            cv.close()

    await asyncio.to_thread(work)


async def convert(category: str, src: Path, dst: Path):
    if category == "image":
        await asyncio.to_thread(convert_image, src, dst)
    elif category in ("audio", "video"):
        await convert_ffmpeg(src, dst)
    elif category in ("doc", "sheet"):
        await convert_office(src, dst)
    elif category == "pdf":
        await convert_pdf_to_docx(src, dst)


# ---------- أوامر البوت ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك 👋\n"
        "أنا بوت تحويل صيغ الملفات.\n\n"
        "أرسل لي أي ملف وسأعرض عليك الصيغ المتاحة للتحويل.\n\n"
        "الأنواع المدعومة:\n"
        "🖼 الصور: png, jpg, webp, bmp, gif\n"
        "🎵 الصوت: mp3, wav, ogg, m4a, flac\n"
        "🎬 الفيديو: mp4, avi, mkv, webm, mov\n"
        "📄 المستندات: docx, odt, txt, pptx, pdf\n"
        "📊 الجداول: xlsx, csv, ods\n\n"
        "ملاحظة: الحد الأقصى لحجم الملف 20 ميغابايت."
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    tg_file, name = None, None

    if msg.document:
        tg_file, name = msg.document, msg.document.file_name or "file"
    elif msg.photo:
        tg_file, name = msg.photo[-1], "photo.jpg"
    elif msg.audio:
        tg_file, name = msg.audio, msg.audio.file_name or "audio.mp3"
    elif msg.voice:
        tg_file, name = msg.voice, "voice.ogg"
    elif msg.video:
        tg_file, name = msg.video, msg.video.file_name or "video.mp4"
    elif msg.video_note:
        tg_file, name = msg.video_note, "video_note.mp4"

    if tg_file is None:
        return

    if tg_file.file_size and tg_file.file_size > MAX_DOWNLOAD:
        await msg.reply_text("❌ حجم الملف أكبر من 20 ميغابايت.")
        return

    ext = Path(name).suffix.lower().lstrip(".")
    category = get_category(ext)
    if not category:
        await msg.reply_text(f"❌ الصيغة «{ext or 'غير معروفة'}» غير مدعومة حالياً.")
        return

    options = [t for t in TARGETS[category] if t != ext and not (t == "jpg" and ext == "jpeg")]
    key = str(msg.message_id)
    context.user_data[key] = {"file_id": tg_file.file_id, "name": name, "category": category}

    buttons = [InlineKeyboardButton(t.upper(), callback_data=f"{key}:{t}") for t in options]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    await msg.reply_text(
        f"📁 {name}\nاختر الصيغة المطلوبة:",
        reply_markup=InlineKeyboardMarkup(rows),
        reply_to_message_id=msg.message_id,
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    key, target = query.data.split(":")
    info = context.user_data.get(key)
    if not info:
        await query.edit_message_text("⚠️ انتهت صلاحية الطلب، أعد إرسال الملف.")
        return

    await query.edit_message_text(f"⏳ جاري التحويل إلى {target.upper()} ...")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stem = Path(info["name"]).stem or "file"
            src = tmp / f"in_{Path(info['name']).name}"
            dst = tmp / f"{stem}.{target}"

            tg_file = await context.bot.get_file(info["file_id"])
            await tg_file.download_to_drive(src)

            await convert(info["category"], src, dst)

            if dst.stat().st_size > 50 * 1024 * 1024:
                raise RuntimeError("الملف الناتج أكبر من 50 ميغابايت")

            with open(dst, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    filename=dst.name,
                    caption="✅ تم التحويل بنجاح",
                )
        await query.delete_message()
    except Exception as e:
        log.exception("conversion failed")
        await query.edit_message_text(f"❌ فشل التحويل:\n{e}")
    finally:
        context.user_data.pop(key, None)


def main():
    if not BOT_TOKEN:
        raise SystemExit("ضع توكن البوت في متغير البيئة BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(
        MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.AUDIO
            | filters.VOICE | filters.VIDEO | filters.VIDEO_NOTE,
            handle_file,
        )
    )
    app.add_handler(CallbackQueryHandler(handle_choice))
    log.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
