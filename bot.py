import asyncio
import html
import io
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeEmoji,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
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

# ======================= الإعدادات =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
BOT_NAME = os.environ.get("BOT_NAME", "ConvertX")
DATA_FILE = Path(os.environ.get("DATA_FILE", "users.json"))
MAX_DOWNLOAD = 20 * 1024 * 1024   # حد تيليجرام للتنزيل
MAX_UPLOAD = 50 * 1024 * 1024     # حد تيليجرام للرفع
CONVERT_SEM = asyncio.Semaphore(2)  # أقصى عدد تحويلات متزامنة
office_lock = asyncio.Lock()        # LibreOffice لا يعمل بعمليات متزامنة

LANGS = {"ar": "🇩🇿 العربية", "en": "🇬🇧 English", "fr": "🇫🇷 Français"}

# لغات Tesseract حسب لغة المستخدم (تحتاج الحزم: tesseract-ocr-ara / eng / fra)
OCR_LANGS = {"ar": "ara+eng", "en": "eng", "fr": "fra+eng"}

# ======================= الصيغ =======================
CATEGORIES = {
    "image": {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tiff"},
    "audio": {"mp3", "wav", "ogg", "oga", "m4a", "flac", "aac", "opus"},
    "video": {"mp4", "avi", "mkv", "mov", "webm"},
    "doc": {"doc", "docx", "odt", "rtf", "txt", "ppt", "pptx", "odp"},
    "sheet": {"xls", "xlsx", "csv", "ods"},
    "pdf": {"pdf"},
}
CAT_ICONS = {"image": "🖼", "audio": "🎵", "video": "🎬", "doc": "📄", "sheet": "📊", "pdf": "📕"}

TARGETS = {
    "image": ["png", "jpg", "webp", "bmp", "gif", "pdf"],
    "audio": ["mp3", "wav", "ogg", "m4a", "flac"],
    "video": ["mp4", "avi", "mkv", "webm", "gif", "mp3"],
    "doc": ["pdf", "docx", "odt", "txt"],
    "sheet": ["pdf", "xlsx", "csv", "ods"],
    "pdf": ["docx"],
}
OFFICE_FILTERS = {"txt": "txt:Text", "csv": "csv"}

# ======================= النصوص (3 لغات) =======================
T = {
    "ar": {
        "pick_lang": "🌐 <b>اختر لغتك</b>\nChoose your language • Choisissez votre langue",
        "lang_set": "✅ تم اختيار العربية",
        "welcome": (
            "👋 أهلاً <b>{name}</b>!\n\n"
            "أنا <b>{bot}</b> ✨ بوت تحويل صيغ الملفات بسرعة وسهولة.\n\n"
            "📎 <b>أرسل لي أي ملف</b> (صورة، صوت، فيديو، مستند، جدول) "
            "وسأعرض عليك الصيغ المتاحة للتحويل.\n"
            "📝 وللصور: يمكنني أيضاً <b>استخراج النص</b> منها.\n\n"
            "📏 الحد الأقصى للحجم: <b>20 ميغابايت</b>"
        ),
        "help": (
            "📖 <b>طريقة الاستخدام</b>\n\n"
            "1️⃣ أرسل الملف الذي تريد تحويله\n"
            "2️⃣ اختر الصيغة المطلوبة من الأزرار\n"
            "3️⃣ انتظر لحظات واستلم الملف المحوّل ✅\n\n"
            "💡 <b>نصائح</b>\n"
            "• يمكنك إرسال الصور كـ «ملف» للحفاظ على الجودة الأصلية\n"
            "• لاستخراج النص من صورة اضغط «📝 استخراج النص» (الصور الواضحة تعطي نتائج أدق)\n"
            "• الفيديوهات الطويلة قد تحتاج وقتاً أطول\n\n"
            "⌨️ <b>الأوامر</b>\n"
            "/start - القائمة الرئيسية\n"
            "/formats - الصيغ المدعومة\n"
            "/lang - تغيير اللغة\n"
            "/help - المساعدة"
        ),
        "formats_title": "📚 <b>الصيغ المدعومة</b>",
        "cats": {"image": "الصور", "audio": "الصوت", "video": "الفيديو",
                 "doc": "المستندات", "sheet": "الجداول", "pdf": "PDF"},
        "btn_formats": "📚 الصيغ المدعومة",
        "btn_help": "❓ المساعدة",
        "btn_lang": "🌐 اللغة",
        "btn_back": "🔙 رجوع",
        "btn_cancel": "✖️ إلغاء",
        "btn_ocr": "📝 استخراج النص",
        "ask_target": "📁 <b>{name}</b>\n📦 الحجم: {size}\n\n👇 اختر الصيغة المطلوبة:",
        "downloading": "📥 جاري تنزيل الملف...",
        "converting": "⚙️ جاري التحويل إلى <b>{fmt}</b>...",
        "ocr_running": "🔎 جاري استخراج النص...",
        "ocr_done": "✅ <b>النص المستخرج</b>",
        "ocr_empty": "🤷 لم أجد أي نص في هذه الصورة.\nجرّب صورة أوضح.",
        "sending": "📤 جاري إرسال الملف...",
        "done": "✅ تم التحويل إلى <b>{fmt}</b> خلال {secs} ثانية",
        "cancelled": "🚫 تم إلغاء العملية.",
        "expired": "⚠️ انتهت صلاحية الطلب، أعد إرسال الملف من فضلك.",
        "too_big": "❌ حجم الملف ({size}) أكبر من الحد المسموح (20 ميغابايت).",
        "unsupported": "❌ الصيغة «{ext}» غير مدعومة.\nاستخدم /formats لعرض الصيغ المتاحة.",
        "fail": "❌ <b>فشل التحويل</b>\n<code>{err}</code>\n\nجرّب ملفاً آخر أو صيغة مختلفة.",
        "out_big": "الملف الناتج أكبر من 50 ميغابايت",
        "hello": "أهلاً بك {name} 👋\nأرسل لي ملفاً وسأحوّله لك فوراً 🚀",
        "thanks": "العفو 🌹 أنا في الخدمة دائماً!",
        "unknown": "🤔 لم أفهم رسالتك.\n📎 أرسل لي <b>ملفاً</b> لأحوّله، أو استخدم /help للمساعدة.",
        "admin_only": "⛔ هذا الأمر للمشرف فقط.",
        "stats": ("📊 <b>الإحصائيات</b>\n\n👥 المستخدمون: <b>{users}</b>\n"
                  "🔄 التحويلات: <b>{conv}</b>\n🌐 اللغات: {langs}"),
        "cmd": {"start": "القائمة الرئيسية", "formats": "الصيغ المدعومة",
                "lang": "تغيير اللغة", "help": "المساعدة"},
    },
    "en": {
        "pick_lang": "🌐 <b>Choose your language</b>\nاختر لغتك • Choisissez votre langue",
        "lang_set": "✅ English selected",
        "welcome": (
            "👋 Hello <b>{name}</b>!\n\n"
            "I'm <b>{bot}</b> ✨ a fast and easy file format converter.\n\n"
            "📎 <b>Send me any file</b> (image, audio, video, document, spreadsheet) "
            "and I'll show you the available formats.\n"
            "📝 For images, I can also <b>extract the text</b> from them.\n\n"
            "📏 Max file size: <b>20 MB</b>"
        ),
        "help": (
            "📖 <b>How to use</b>\n\n"
            "1️⃣ Send the file you want to convert\n"
            "2️⃣ Pick the target format from the buttons\n"
            "3️⃣ Wait a moment and get your converted file ✅\n\n"
            "💡 <b>Tips</b>\n"
            "• Send images as a \"file\" to keep the original quality\n"
            "• To extract text from an image tap \"📝 Extract text\" (clear images give better results)\n"
            "• Long videos may take more time\n\n"
            "⌨️ <b>Commands</b>\n"
            "/start - Main menu\n"
            "/formats - Supported formats\n"
            "/lang - Change language\n"
            "/help - Help"
        ),
        "formats_title": "📚 <b>Supported formats</b>",
        "cats": {"image": "Images", "audio": "Audio", "video": "Video",
                 "doc": "Documents", "sheet": "Spreadsheets", "pdf": "PDF"},
        "btn_formats": "📚 Supported formats",
        "btn_help": "❓ Help",
        "btn_lang": "🌐 Language",
        "btn_back": "🔙 Back",
        "btn_cancel": "✖️ Cancel",
        "btn_ocr": "📝 Extract text",
        "ask_target": "📁 <b>{name}</b>\n📦 Size: {size}\n\n👇 Choose the target format:",
        "downloading": "📥 Downloading your file...",
        "converting": "⚙️ Converting to <b>{fmt}</b>...",
        "ocr_running": "🔎 Extracting text...",
        "ocr_done": "✅ <b>Extracted text</b>",
        "ocr_empty": "🤷 No text found in this image.\nTry a clearer one.",
        "sending": "📤 Sending the file...",
        "done": "✅ Converted to <b>{fmt}</b> in {secs}s",
        "cancelled": "🚫 Operation cancelled.",
        "expired": "⚠️ This request has expired, please send the file again.",
        "too_big": "❌ File size ({size}) exceeds the limit (20 MB).",
        "unsupported": "❌ The format \"{ext}\" is not supported.\nUse /formats to see what's available.",
        "fail": "❌ <b>Conversion failed</b>\n<code>{err}</code>\n\nTry another file or a different format.",
        "out_big": "The output file is larger than 50 MB",
        "hello": "Welcome {name} 👋\nSend me a file and I'll convert it right away 🚀",
        "thanks": "You're welcome 🌹 I'm always here to help!",
        "unknown": "🤔 I didn't get that.\n📎 Send me a <b>file</b> to convert, or use /help.",
        "admin_only": "⛔ This command is for the admin only.",
        "stats": ("📊 <b>Statistics</b>\n\n👥 Users: <b>{users}</b>\n"
                  "🔄 Conversions: <b>{conv}</b>\n🌐 Languages: {langs}"),
        "cmd": {"start": "Main menu", "formats": "Supported formats",
                "lang": "Change language", "help": "Help"},
    },
    "fr": {
        "pick_lang": "🌐 <b>Choisissez votre langue</b>\nاختر لغتك • Choose your language",
        "lang_set": "✅ Français sélectionné",
        "welcome": (
            "👋 Bonjour <b>{name}</b> !\n\n"
            "Je suis <b>{bot}</b> ✨ un convertisseur de formats de fichiers rapide et simple.\n\n"
            "📎 <b>Envoyez-moi un fichier</b> (image, audio, vidéo, document, tableur) "
            "et je vous proposerai les formats disponibles.\n"
            "📝 Pour les images, je peux aussi <b>extraire le texte</b>.\n\n"
            "📏 Taille maximale : <b>20 Mo</b>"
        ),
        "help": (
            "📖 <b>Mode d'emploi</b>\n\n"
            "1️⃣ Envoyez le fichier à convertir\n"
            "2️⃣ Choisissez le format voulu avec les boutons\n"
            "3️⃣ Patientez un instant et recevez le fichier converti ✅\n\n"
            "💡 <b>Astuces</b>\n"
            "• Envoyez les images en tant que « fichier » pour garder la qualité d'origine\n"
            "• Pour extraire le texte d'une image, appuyez sur « 📝 Extraire le texte » (une image nette donne de meilleurs résultats)\n"
            "• Les longues vidéos peuvent prendre plus de temps\n\n"
            "⌨️ <b>Commandes</b>\n"
            "/start - Menu principal\n"
            "/formats - Formats pris en charge\n"
            "/lang - Changer de langue\n"
            "/help - Aide"
        ),
        "formats_title": "📚 <b>Formats pris en charge</b>",
        "cats": {"image": "Images", "audio": "Audio", "video": "Vidéo",
                 "doc": "Documents", "sheet": "Tableurs", "pdf": "PDF"},
        "btn_formats": "📚 Formats pris en charge",
        "btn_help": "❓ Aide",
        "btn_lang": "🌐 Langue",
        "btn_back": "🔙 Retour",
        "btn_cancel": "✖️ Annuler",
        "btn_ocr": "📝 Extraire le texte",
        "ask_target": "📁 <b>{name}</b>\n📦 Taille : {size}\n\n👇 Choisissez le format voulu :",
        "downloading": "📥 Téléchargement du fichier...",
        "converting": "⚙️ Conversion en <b>{fmt}</b>...",
        "ocr_running": "🔎 Extraction du texte...",
        "ocr_done": "✅ <b>Texte extrait</b>",
        "ocr_empty": "🤷 Aucun texte trouvé dans cette image.\nEssayez une image plus nette.",
        "sending": "📤 Envoi du fichier...",
        "done": "✅ Converti en <b>{fmt}</b> en {secs} s",
        "cancelled": "🚫 Opération annulée.",
        "expired": "⚠️ Cette demande a expiré, renvoyez le fichier s'il vous plaît.",
        "too_big": "❌ La taille du fichier ({size}) dépasse la limite (20 Mo).",
        "unsupported": "❌ Le format « {ext} » n'est pas pris en charge.\nUtilisez /formats pour voir la liste.",
        "fail": "❌ <b>Échec de la conversion</b>\n<code>{err}</code>\n\nEssayez un autre fichier ou un autre format.",
        "out_big": "Le fichier obtenu dépasse 50 Mo",
        "hello": "Bienvenue {name} 👋\nEnvoyez-moi un fichier et je le convertis aussitôt 🚀",
        "thanks": "Avec plaisir 🌹 Je suis toujours là !",
        "unknown": "🤔 Je n'ai pas compris.\n📎 Envoyez-moi un <b>fichier</b> à convertir, ou utilisez /help.",
        "admin_only": "⛔ Cette commande est réservée à l'administrateur.",
        "stats": ("📊 <b>Statistiques</b>\n\n👥 Utilisateurs : <b>{users}</b>\n"
                  "🔄 Conversions : <b>{conv}</b>\n🌐 Langues : {langs}"),
        "cmd": {"start": "Menu principal", "formats": "Formats pris en charge",
                "lang": "Changer de langue", "help": "Aide"},
    },
}

GREETINGS = {"مرحبا", "مرحباً", "مرحبًا", "سلام", "السلام", "اهلا", "أهلا", "أهلاً", "هلا",
             "hello", "hi", "hey", "bonjour", "salut", "coucou", "bonsoir"}
THANKS = {"شكرا", "شكراً", "شكرًا", "يعطيك", "thanks", "thank", "thx", "merci"}

# ======================= تخزين المستخدمين =======================
users: dict = {}


def load_users():
    global users
    try:
        users = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        users = {}


def save_users():
    try:
        DATA_FILE.write_text(json.dumps(users, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("could not save users: %s", e)


def get_user(uid: int) -> dict:
    return users.setdefault(str(uid), {"lang": None, "conversions": 0, "joined": int(time.time())})


def lang_of(update: Update) -> str:
    user = update.effective_user
    saved = get_user(user.id)["lang"]
    if saved in LANGS:
        return saved
    code = (user.language_code or "")[:2]
    return code if code in LANGS else "ar"


# ======================= أدوات مساعدة =======================
def get_category(ext: str):
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return None


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def formats_text(lang: str) -> str:
    t = T[lang]
    lines = [t["formats_title"], ""]
    for cat, exts in CATEGORIES.items():
        shown = sorted(e.upper() for e in exts if e not in ("jpeg", "oga"))
        lines.append(f"{CAT_ICONS[cat]} <b>{t['cats'][cat]}</b>\n<code>{' • '.join(shown)}</code>\n")
    return "\n".join(lines)


def main_keyboard(lang: str) -> InlineKeyboardMarkup:
    t = T[lang]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t["btn_formats"], callback_data="menu:formats")],
        [InlineKeyboardButton(t["btn_help"], callback_data="menu:help"),
         InlineKeyboardButton(t["btn_lang"], callback_data="menu:lang")],
    ])


def back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(T[lang]["btn_back"], callback_data="menu:home")]])


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"lang:{code}")]
                                 for code, label in LANGS.items()])


async def safe_edit(query, text, markup=None):
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ======================= دوال التحويل =======================
async def run_cmd(*cmd, timeout=300):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Timeout")
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-400:] or "conversion failed")


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
    elif fmt == "mp4":
        cmd += ["-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd.append(str(dst))
    await run_cmd(*cmd)


async def convert_office(src: Path, dst: Path):
    fmt = dst.suffix.lower().lstrip(".")
    target = OFFICE_FILTERS.get(fmt, fmt)
    async with office_lock:
        await run_cmd("soffice", "--headless", "--convert-to", target,
                      "--outdir", str(src.parent), str(src))
    produced = src.with_suffix("." + fmt)  # LibreOffice يسمي الناتج باسم الملف الأصلي
    if not produced.exists():
        raise RuntimeError("LibreOffice produced no output")
    produced.rename(dst)


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


# ======================= استخراج النص (OCR) =======================
def prep_for_ocr(src: Path, dst: Path):
    """تحسين الصورة قبل OCR: تدوير EXIF، تدرّج رمادي، تكبير، تباين."""
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img).convert("L")
        if max(img.size) < 1500:
            scale = 1500 / max(img.size)
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        img = ImageOps.autocontrast(img)
        img.save(dst, "PNG")


async def ocr_image(src: Path, lang: str) -> str:
    prepared = src.with_name("ocr_in.png")
    out_base = src.with_name("ocr_out")
    await asyncio.to_thread(prep_for_ocr, src, prepared)
    await run_cmd("tesseract", str(prepared), str(out_base), "-l", OCR_LANGS.get(lang, "ara+eng"))
    return out_base.with_suffix(".txt").read_text(encoding="utf-8", errors="ignore").strip()


async def do_ocr(q, context: ContextTypes.DEFAULT_TYPE, info: dict, lang: str):
    t = T[lang]
    chat_id = q.message.chat_id
    async with CONVERT_SEM:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / f"input.{info['ext']}"

            await safe_edit(q, t["downloading"])
            tg_file = await context.bot.get_file(info["file_id"])
            await tg_file.download_to_drive(src)

            await safe_edit(q, t["ocr_running"])
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            text = await ocr_image(src, lang)

    if not text:
        await safe_edit(q, t["ocr_empty"])
        return

    if len(text) <= 3800:
        await context.bot.send_message(
            chat_id,
            f"{t['ocr_done']}\n\n<pre>{html.escape(text)}</pre>",
            parse_mode=ParseMode.HTML,
        )
    else:  # النص طويل: أرسله كملف txt
        bio = io.BytesIO(text.encode("utf-8"))
        await context.bot.send_document(
            chat_id,
            document=bio,
            filename=f"{Path(info['name']).stem or 'text'}.txt",
            caption=t["ocr_done"],
            parse_mode=ParseMode.HTML,
        )

    await q.delete_message()
    get_user(q.from_user.id)["conversions"] += 1
    save_users()


# ======================= الأوامر =======================
async def send_welcome(target, update: Update, lang: str):
    name = html.escape(update.effective_user.first_name or "")
    text = T[lang]["welcome"].format(name=name, bot=BOT_NAME)
    await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if user["lang"] is None:
        await update.message.reply_text(T["ar"]["pick_lang"], parse_mode=ParseMode.HTML,
                                        reply_markup=lang_keyboard())
        return
    await send_welcome(update.message, update, lang_of(update))


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(T[lang_of(update)]["pick_lang"], parse_mode=ParseMode.HTML,
                                    reply_markup=lang_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(update)
    await update.message.reply_text(T[lang]["help"], parse_mode=ParseMode.HTML)


async def cmd_formats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(update)
    await update.message.reply_text(formats_text(lang), parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(update)
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(T[lang]["admin_only"])
        return
    total_conv = sum(u.get("conversions", 0) for u in users.values())
    counts = {}
    for u in users.values():
        counts[u.get("lang") or "?"] = counts.get(u.get("lang") or "?", 0) + 1
    langs = " | ".join(f"{k}: {v}" for k, v in counts.items()) or "-"
    await update.message.reply_text(
        T[lang]["stats"].format(users=len(users), conv=total_conv, langs=langs),
        parse_mode=ParseMode.HTML,
    )


# ======================= الأزرار =======================
async def on_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    code = q.data.split(":")[1]
    if code not in LANGS:
        await q.answer()
        return
    get_user(q.from_user.id)["lang"] = code
    save_users()
    await q.answer(T[code]["lang_set"])
    name = html.escape(q.from_user.first_name or "")
    await safe_edit(q, T[code]["welcome"].format(name=name, bot=BOT_NAME), main_keyboard(code))


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = lang_of(update)
    action = q.data.split(":")[1]
    if action == "formats":
        await safe_edit(q, formats_text(lang), back_keyboard(lang))
    elif action == "help":
        await safe_edit(q, T[lang]["help"], back_keyboard(lang))
    elif action == "lang":
        await safe_edit(q, T[lang]["pick_lang"], lang_keyboard())
    else:
        name = html.escape(q.from_user.first_name or "")
        await safe_edit(q, T[lang]["welcome"].format(name=name, bot=BOT_NAME), main_keyboard(lang))


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.pop(q.data.split(":")[1], None)
    await safe_edit(q, T[lang_of(update)]["cancelled"])


# ======================= استقبال الملفات =======================
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    lang = lang_of(update)
    t = T[lang]
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

    try:  # تفاعل سريع على رسالة المستخدم
        await msg.set_reaction(ReactionTypeEmoji("👀"))
    except Exception:
        pass

    size = tg_file.file_size or 0
    if size > MAX_DOWNLOAD:
        await msg.reply_text(t["too_big"].format(size=human_size(size)))
        return

    ext = Path(name).suffix.lower().lstrip(".")
    category = get_category(ext)
    if not category:
        await msg.reply_text(t["unsupported"].format(ext=html.escape(ext or "?")),
                             parse_mode=ParseMode.HTML)
        return

    options = [x for x in TARGETS[category] if x != ext and not (x == "jpg" and ext == "jpeg")]
    key = str(msg.message_id)
    context.user_data[key] = {"file_id": tg_file.file_id, "name": name, "category": category, "ext": ext}

    buttons = [InlineKeyboardButton(x.upper(), callback_data=f"conv:{key}:{x}") for x in options]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    if category == "image":  # زر استخراج النص يظهر مع الصور فقط
        rows.insert(0, [InlineKeyboardButton(t["btn_ocr"], callback_data=f"conv:{key}:ocr")])
    rows.append([InlineKeyboardButton(t["btn_cancel"], callback_data=f"cancel:{key}")])

    await msg.reply_text(
        t["ask_target"].format(name=html.escape(name), size=human_size(size)),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        reply_to_message_id=msg.message_id,
    )


async def on_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    lang = lang_of(update)
    t = T[lang]
    _, key, target = q.data.split(":")
    info = context.user_data.get(key)
    await q.answer()
    if not info:
        await safe_edit(q, t["expired"])
        return

    started = time.monotonic()
    try:
        if target == "ocr":
            await do_ocr(q, context, info, lang)
            return

        async with CONVERT_SEM:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                src = tmp / f"input.{info['ext']}"
                dst = tmp / f"output.{target}"

                await safe_edit(q, t["downloading"])
                tg_file = await context.bot.get_file(info["file_id"])
                await tg_file.download_to_drive(src)

                await safe_edit(q, t["converting"].format(fmt=target.upper()))
                await context.bot.send_chat_action(q.message.chat_id, ChatAction.UPLOAD_DOCUMENT)
                await convert(info["category"], src, dst)

                if dst.stat().st_size > MAX_UPLOAD:
                    raise RuntimeError(t["out_big"])

                await safe_edit(q, t["sending"])
                secs = round(time.monotonic() - started, 1)
                with open(dst, "rb") as f:
                    await context.bot.send_document(
                        chat_id=q.message.chat_id,
                        document=f,
                        filename=f"{Path(info['name']).stem or 'file'}.{target}",
                        caption=t["done"].format(fmt=target.upper(), secs=secs),
                        parse_mode=ParseMode.HTML,
                    )
        await q.delete_message()
        get_user(q.from_user.id)["conversions"] += 1
        save_users()
    except Exception as e:
        log.exception("conversion failed")
        await safe_edit(q, t["fail"].format(err=html.escape(str(e)[:300])))
    finally:
        context.user_data.pop(key, None)


# ======================= الرسائل النصية =======================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(update)
    t = T[lang]
    text = (update.message.text or "").lower()
    words = set(re.findall(r"\w+", text))
    name = html.escape(update.effective_user.first_name or "")

    if words & GREETINGS:
        await update.message.reply_text(t["hello"].format(name=name))
    elif words & THANKS:
        try:
            await update.message.set_reaction(ReactionTypeEmoji("❤"))
        except Exception:
            pass
        await update.message.reply_text(t["thanks"])
    else:
        await update.message.reply_text(t["unknown"], parse_mode=ParseMode.HTML,
                                        reply_markup=main_keyboard(lang))


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


async def post_init(app: Application):
    for code in LANGS:
        cmds = [BotCommand(c, d) for c, d in T[code]["cmd"].items()]
        await app.bot.set_my_commands(cmds, language_code=code)
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in T["en"]["cmd"].items()])


def main():
    if not BOT_TOKEN:
        raise SystemExit("ضع توكن البوت في متغير البيئة BOT_TOKEN")

    load_users()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("formats", cmd_formats))
    app.add_handler(CommandHandler("stats", cmd_stats))

    app.add_handler(CallbackQueryHandler(on_lang, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(on_menu, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(on_cancel, pattern=r"^cancel:"))
    app.add_handler(CallbackQueryHandler(on_convert, pattern=r"^conv:"))

    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO | filters.AUDIO
        | filters.VOICE | filters.VIDEO | filters.VIDEO_NOTE,
        handle_file,
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)

    log.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
