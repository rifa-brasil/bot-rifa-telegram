"""
Bot de rifas Telegram - versión 24
- Varias rifas, SQLite, reservas con caducidad, historial, pagos manuales,
  panel de administración, ganador y confirmación de premio.
- Mensajes privados opcionales desde una cuenta dedicada mediante Telethon.

IMPORTANTE:
La cuenta dedicada debe ser una cuenta propia/autorizada. Telethon inicia sesión
con API_ID/API_HASH y una sesión StringSession. No se debe compartir el archivo
.env ni la sesión con terceros.
"""
import os
import re
import io
import json
import html
import uuid
import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ChatMemberHandler, ContextTypes, MessageHandler, filters
)

load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
)
log = logging.getLogger("rifas")

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
DEFAULT_GROUP_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
CHANNEL_CHAT_ID = int(os.getenv("CHANNEL_CHAT_ID", "0"))  # ID numérico del canal
CHANNEL_JOIN_URL = "https://t.me/+sRdEndX3bwszY2Ix"  # Enlace de acceso proporcionado
# DB_PATH debe apuntar a un disco persistente del proveedor de hosting.
# Si no se configura, se conserva el archivo local existente en el directorio de trabajo.
DB_PATH = os.getenv("DB_PATH", "rifas.sqlite3")
PORT = int(os.getenv("PORT", "10000"))
RESERVATION_MINUTES = 5  # Primera ventana: 5 min para realizar el pago
PAY_BUTTON_MINUTES = 5   # Compatibilidad; el botón de pago no caduca
CURRENCY = "R$"  # Todos los valores de la rifa se expresan en reales brasileños
# Añadir las llaves cuando el administrador las facilite: (nombre visible, llave PIX).
PIX_KEYS = [
    ("PIX teléfono", "11948824359"),
    ("PIX correo 1", "yordanisreyes@gmail.com"),
    ("PIX correo 2", "yordanismydan@gmail.com"),
]
FLORIDA_PICK3_PDF = "https://files.floridalottery.com/exptkt/p3.pdf"
FLORIDA_PICK3_ANDROID_URL = "https://play.google.com/store/apps/details?id=com.flalottery.FLALOTTERY"
FLORIDA_PICK3_IOS_URL = "https://apps.apple.com/us/app/florida-lottery/id476160599"
# El enlace definitivo de descarga será configurado cuando lo proporcione el administrador.
DOWNLOAD_APP_URL = "https://play.google.com/store/apps/details?id=com.directacubana.labolita"
PICK3_AUTO_ANNOUNCE = os.getenv("PICK3_AUTO_ANNOUNCE", "false").lower() == "true"
PICK3_DRAW = os.getenv("PICK3_DRAW", "E").upper()  # M=mediodía, E=noche

# Cuenta dedicada: requiere una sesión autorizada de Telegram.
TELETHON_API_ID = os.getenv("TELETHON_API_ID", "")
TELETHON_API_HASH = os.getenv("TELETHON_API_HASH", "")
TELETHON_SESSION = os.getenv("TELETHON_SESSION", "")

user_client = None
db_lock = asyncio.Lock()


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS raffle_progress_messages (
            raffle_id INTEGER NOT NULL, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            PRIMARY KEY (raffle_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            joined_at TEXT NOT NULL,
            channel_member INTEGER NOT NULL DEFAULT 0,
            playing_blocked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS raffles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            total_numbers INTEGER NOT NULL DEFAULT 100,
            price REAL NOT NULL DEFAULT 0,
            prize TEXT DEFAULT '',
            prize_value TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            draw_source TEXT DEFAULT 'manual',
            created_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS numbers (
            raffle_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            user_id INTEGER,
            name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (raffle_id, number),
            FOREIGN KEY (raffle_id) REFERENCES raffles(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY,
            raffle_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            username TEXT DEFAULT '',
            numbers_json TEXT NOT NULL,
            origin_chat INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            processed_at TEXT,
            FOREIGN KEY (raffle_id) REFERENCES raffles(id)
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raffle_id INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            username TEXT DEFAULT '',
            numbers_json TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            payment_receipt_file_id TEXT DEFAULT '',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (raffle_id) REFERENCES raffles(id)
        );
        CREATE TABLE IF NOT EXISTS winners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raffle_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            group_message_id INTEGER,
            prize_received INTEGER NOT NULL DEFAULT 0,
            announced_at TEXT NOT NULL,
            received_at TEXT,
            draw_result TEXT DEFAULT '',
            channel_message_id INTEGER,
            receipt_pending INTEGER NOT NULL DEFAULT 0,
            receipt_file_id TEXT DEFAULT '',
            FOREIGN KEY (raffle_id) REFERENCES raffles(id)
        );
        CREATE TABLE IF NOT EXISTS lottery_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draw_date TEXT NOT NULL,
            draw_type TEXT NOT NULL,
            result TEXT NOT NULL,
            fireball TEXT DEFAULT '',
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(draw_date, draw_type)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        # Migración: registra si cada usuario conocido sigue en el canal.
        bot_user_columns = {r[1] for r in con.execute("PRAGMA table_info(bot_users)")}
        if "channel_member" not in bot_user_columns:
            con.execute("ALTER TABLE bot_users ADD COLUMN channel_member INTEGER NOT NULL DEFAULT 0")
        if "playing_blocked" not in bot_user_columns:
            con.execute("ALTER TABLE bot_users ADD COLUMN playing_blocked INTEGER NOT NULL DEFAULT 0")
        # Migraciones para publicación en canal y comprobante de recepción del premio.
        winner_columns = {r[1] for r in con.execute("PRAGMA table_info(winners)")}
        for col, declaration in [
            ("channel_message_id", "INTEGER"),
            ("receipt_pending", "INTEGER NOT NULL DEFAULT 0"),
            ("receipt_file_id", "TEXT DEFAULT ''")
        ]:
            if col not in winner_columns:
                con.execute(f"ALTER TABLE winners ADD COLUMN {col} {declaration}")
        # Flujo de entrega del premio: solicitud, llave PIX y comprobante de transferencia.
        winner_columns = {r[1] for r in con.execute("PRAGMA table_info(winners)")}
        for col, declaration in [
            ("prize_status", "TEXT NOT NULL DEFAULT 'announced'"),
            ("pix_key", "TEXT DEFAULT ''"),
            ("payout_receipt_file_id", "TEXT DEFAULT ''")
        ]:
            if col not in winner_columns:
                con.execute(f"ALTER TABLE winners ADD COLUMN {col} {declaration}")
        # Migraciones compatibles con bases de datos creadas por versiones anteriores.
        raffle_columns = {r[1] for r in con.execute("PRAGMA table_info(raffles)")}
        if "prize_value" not in raffle_columns:
            con.execute("ALTER TABLE raffles ADD COLUMN prize_value TEXT DEFAULT ''")
        purchase_columns = {r[1] for r in con.execute("PRAGMA table_info(purchases)")}
        if "payment_receipt_file_id" not in purchase_columns:
            con.execute("ALTER TABLE purchases ADD COLUMN payment_receipt_file_id TEXT DEFAULT ''")
        request_columns = {r[1] for r in con.execute("PRAGMA table_info(requests)")}
        for col, declaration in [
            ("key_message_id", "INTEGER"),
            ("group_message_id", "INTEGER"),
            ("pay_button_at", "TEXT"),
            ("payment_deadline_at", "TEXT"),
            ("reported_at", "TEXT"),
            ("claim_receipt_file_id", "TEXT DEFAULT ''"),
            ("payment_receipt_file_id", "TEXT DEFAULT ''"),
            ("price_per_number", "REAL DEFAULT 0"),
            ("total_amount", "REAL DEFAULT 0"),
            ("reported_amount", "REAL DEFAULT 0")
        ]:
            if col not in request_columns:
                con.execute(f"ALTER TABLE requests ADD COLUMN {col} {declaration}")
        # En versiones anteriores, "pending" significaba que el admin ya podía revisar la solicitud.
        con.execute("UPDATE requests SET status='payment_reported' WHERE status='pending'")
        legacy_requests = con.execute(
            "SELECT id,raffle_id,numbers_json FROM requests WHERE status='payment_reported' AND total_amount=0"
        ).fetchall()
        for legacy in legacy_requests:
            try:
                legacy_count = len(json.loads(legacy["numbers_json"]))
                legacy_raffle = con.execute("SELECT price FROM raffles WHERE id=?", (legacy["raffle_id"],)).fetchone()
                legacy_price = float(legacy_raffle["price"]) if legacy_raffle else 0.0
                con.execute(
                    "UPDATE requests SET price_per_number=?,total_amount=? WHERE id=?",
                    (legacy_price, legacy_price * legacy_count, legacy["id"])
                )
            except Exception:
                log.exception("No se pudo migrar el importe de la solicitud antigua %s", legacy["id"])
        # Cierra automáticamente listas antiguas que ya tenían todos sus números aprobados.
        con.execute(
            """UPDATE raffles SET status='sold_out',closed_at=COALESCE(closed_at,?)
               WHERE status='active' AND id IN (
                   SELECT n.raffle_id FROM numbers n
                   WHERE n.status='occupied'
                   GROUP BY n.raffle_id
                   HAVING COUNT(*) >= (SELECT r.total_numbers FROM raffles r WHERE r.id=n.raffle_id)
               )""",
            (iso_now(),)
        )
        # Impide nombres duplicados (ignorando mayúsculas/minúsculas y espacios externos).
        # El trigger protege también frente a dos creaciones casi simultáneas.
        con.executescript("""
        CREATE TRIGGER IF NOT EXISTS prevent_duplicate_raffle_name_insert
        BEFORE INSERT ON raffles
        WHEN EXISTS (SELECT 1 FROM raffles WHERE lower(trim(name))=lower(trim(NEW.name)))
        BEGIN SELECT RAISE(ABORT, 'duplicate_raffle_name'); END;
        CREATE TRIGGER IF NOT EXISTS prevent_duplicate_raffle_name_update
        BEFORE UPDATE OF name ON raffles
        WHEN EXISTS (SELECT 1 FROM raffles WHERE id<>NEW.id AND lower(trim(name))=lower(trim(NEW.name)))
        BEGIN SELECT RAISE(ABORT, 'duplicate_raffle_name'); END;
        """)
        # El administrador controla manualmente si se aceptan nuevas jugadas.
        con.execute("INSERT OR IGNORE INTO bot_settings(setting_key,setting_value) VALUES('playing_enabled','0')")
        # No se borran listas al arrancar: init_db solo crea/migra tablas y conserva los registros.


def audit(action, details, admin_id=None):
    with db() as con:
        con.execute(
            "INSERT INTO audit_log(admin_id,action,details,created_at) VALUES(?,?,?,?)",
            (admin_id, action, details, iso_now())
        )


def is_admin(user_id):
    return user_id == ADMIN_ID and ADMIN_ID != 0


def get_active_raffle(group_id=None):
    group_id = group_id or DEFAULT_GROUP_ID
    with db() as con:
        row = con.execute(
            "SELECT * FROM raffles WHERE group_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (group_id,)
        ).fetchone()
        return dict(row) if row else None


def get_raffle(raffle_id):
    with db() as con:
        row = con.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
        return dict(row) if row else None


def stats(raffle_id):
    with db() as con:
        r = con.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
        if not r:
            return None
        counts = {x["status"]: x["n"] for x in con.execute(
            "SELECT status,COUNT(*) n FROM numbers WHERE raffle_id=? GROUP BY status",
            (raffle_id,)
        )}
        occupied = counts.get("occupied", 0)
        pending = counts.get("pending", 0)
        available = counts.get("available", 0)
        revenue = con.execute(
            "SELECT COALESCE(SUM(amount),0) FROM purchases WHERE raffle_id=? AND status='approved'",
            (raffle_id,)
        ).fetchone()[0]
        return {
            "name": r["name"], "status": r["status"], "total": r["total_numbers"],
            "occupied": occupied, "pending": pending, "available": available,
            "revenue": float(revenue), "price": float(r["price"]), "group_id": r["group_id"]
        }


def progress_bar(occupied, total, width=20):
    """Barra de progreso con segmentos verdes visibles desde el primer número."""
    total = max(int(total or 0), 1)
    count = max(0, min(total, int(occupied or 0)))
    ratio = count / total
    filled = min(width, int(ratio * width + 0.999999)) if count else 0
    percent = int(round(ratio * 100))
    return f"{'🟩' * filled}{'⬜' * (width - filled)} {percent}%"


def is_playing_enabled():
    with db() as con:
        row = con.execute("SELECT setting_value FROM bot_settings WHERE setting_key='playing_enabled'").fetchone()
    return bool(row and row[0] == '1')


def set_playing_enabled(enabled):
    with db() as con:
        con.execute("INSERT INTO bot_settings(setting_key,setting_value) VALUES('playing_enabled',?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
                    ('1' if enabled else '0',))


def participant_progress_label():
    """Texto compacto y no interactivo para informar el avance hacia 100 usuarios."""
    count = eligible_user_count()
    return f"Cantidad usuarios: {count} Registrados"


def play_status_text():
    return (participant_progress_label() + "\n" +
            f"🎟️ Jugadas: {'HABILITADAS' if is_playing_enabled() else 'BLOQUEADAS'}")


def _get_setting(key):
    with db() as con:
        row = con.execute("SELECT setting_value FROM bot_settings WHERE setting_key=?", (key,)).fetchone()
    return row[0] if row else None


def _set_setting(key, value):
    with db() as con:
        con.execute(
            "INSERT INTO bot_settings(setting_key,setting_value) VALUES(?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
            (key, str(value))
        )


def admin_user_count_keyboard(count):
    """Teclado persistente con el contador integrado en una opción del panel."""
    return InlineKeyboardMarkup([])


async def update_admin_counter(context, count=None):
    """No crea mensajes sueltos: el contador se integra en el teclado del panel."""
    return


async def update_user_counters(context, count=None):
    """No crea mensajes sueltos: el contador se integra en el teclado persistente del usuario."""
    return


async def broadcast_unlocked_lists(context, raffle_names):
    """Notifica a los participantes cuando el administrador habilita listas concretas."""
    names = list(dict.fromkeys(str(name) for name in raffle_names if name))
    if not names:
        return
    with db() as con:
        user_ids = [int(r[0]) for r in con.execute(
            "SELECT user_id FROM bot_users WHERE channel_member=1 ORDER BY joined_at"
        ).fetchall()]
    names_text = "\n".join(f"🎟️ {name}" for name in names)
    message = (
        "🎉 ¡YA PUEDES JUGAR!\n\n"
        "El administrador ha desbloqueado las siguientes listas:\n\n"
        f"{names_text}\n\n"
        "Entra en «🎟️ Ver listas activas» en el menú del bot para consultar los números disponibles y participar."
    )
    sent = 0
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=message, reply_markup=user_keyboard())
            sent += 1
        except Exception:
            log.info("No se pudo avisar del desbloqueo al usuario %s", user_id, exc_info=True)
        await asyncio.sleep(0.05)
    log.info("Aviso de listas desbloqueadas enviado a %s de %s usuarios", sent, len(user_ids))


async def notify_member_count(context, count):
    """Actualiza los contadores visibles y avisa al admin una sola vez al llegar a 100."""
    await update_admin_counter(context, count)
    await update_user_counters(context, count)
    if count < 100:
        return

    with db() as con:
        already_notified = con.execute(
            "SELECT setting_value FROM bot_settings WHERE setting_key='minimum100_notified'"
        ).fetchone()
        if already_notified and already_notified[0] == '1':
            return
        con.execute(
            "INSERT INTO bot_settings(setting_key,setting_value) VALUES('minimum100_notified','1') "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value='1'"
        )
    try:
        await context.bot.send_message(
            ADMIN_ID,
            "🎉 ¡SE ALCANZÓ EL MÍNIMO DE 100 USUARIOS!\n\n"
            "Ya puedes desbloquear una lista o todas desde «🔓 Desbloquear jugadas». "
            "Al desbloquearlas, los participantes recibirán automáticamente un aviso.",
            reply_markup=admin_keyboard()
        )
    except Exception:
        log.exception("No se pudo avisar al administrador al alcanzar 100 usuarios")


async def notify_user_ready(context, user_id, count):
    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "🎉 ¡YA SE ALCANZÓ EL MÍNIMO DE 100 USUARIOS!\n\n"
                f"La comunidad tiene {count} usuarios registrados en el bot y miembros del canal. "
                "El administrador recibió el aviso para desbloquear las jugadas. Te notificaremos cuando "
                "las habilite; mientras tanto puedes consultar las listas y su disponibilidad."
            ),
            reply_markup=user_keyboard()
        )
        return True
    except Exception:
        log.info("No se pudo notificar por privado al usuario %s", user_id, exc_info=True)
        return False


async def notify_threshold_reached(context, count):
    """Avisa a todos los usuarios registrados que siguen siendo miembros del canal."""
    with db() as con:
        user_ids = [int(r[0]) for r in con.execute(
            "SELECT user_id FROM bot_users WHERE channel_member=1 ORDER BY joined_at"
        ).fetchall()]
    sent = 0
    for user_id in user_ids:
        if await notify_user_ready(context, user_id, count):
            sent += 1
        # Mantiene el envío por debajo del límite global habitual de Telegram.
        await asyncio.sleep(0.05)
    log.info("Aviso de umbral enviado a %s de %s usuarios elegibles", sent, len(user_ids))


async def sync_known_channel_members(bot):
    """Reconcilia el estado real de los usuarios ya registrados al iniciar el bot."""
    if not CHANNEL_CHAT_ID:
        return
    before_count = eligible_user_count()
    with db() as con:
        user_ids = [int(r[0]) for r in con.execute("SELECT user_id FROM bot_users").fetchall()]
    for user_id in user_ids:
        try:
            allowed, reason = await channel_access_status(bot, user_id)
            if not str(reason).startswith("api_error:") and reason != "config_missing":
                with db() as con:
                    con.execute(
                        "UPDATE bot_users SET channel_member=? WHERE user_id=?",
                        (1 if allowed else 0, user_id)
                    )
        except Exception:
            log.exception("No se pudo sincronizar membresía del usuario %s", user_id)
    after_count = eligible_user_count()
    if before_count < 100 <= after_count:
        await notify_member_count(type("Context", (), {"bot": bot})(), after_count)
    log.info("Sincronización de canal terminada: %s usuarios elegibles", after_count)


async def channel_access_status(bot, user_id):
    """Devuelve (acceso, motivo) para no confundir un fallo de API con falta de membresía."""
    if not CHANNEL_CHAT_ID:
        log.error("CHANNEL_CHAT_ID no configurado; no se puede verificar el acceso al canal")
        return False, "config_missing"
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_CHAT_ID, user_id=user_id)
        status = member.status
        # En supergrupos Telegram puede devolver 'restricted' con is_member=True.
        if status in {"member", "administrator", "creator"} or (status == "restricted" and getattr(member, "is_member", False)):
            return True, status
        return False, status
    except Exception as exc:
        log.exception("No se pudo verificar membresía del usuario %s en CHANNEL_CHAT_ID=%s", user_id, CHANNEL_CHAT_ID)
        return False, f"api_error:{type(exc).__name__}"


async def has_channel_access(bot, user_id):
    allowed, _reason = await channel_access_status(bot, user_id)
    return allowed


async def channel_diagnostic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnóstico del canal para el administrador, sin exponerlo a participantes."""
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ No autorizado.")
        return
    if not CHANNEL_CHAT_ID:
        await update.effective_message.reply_text(
            "❌ CHANNEL_CHAT_ID está vacío o es 0. Configura el ID numérico del canal en las variables de entorno."
        )
        return
    try:
        chat = await context.bot.get_chat(CHANNEL_CHAT_ID)
        bot_member = await context.bot.get_chat_member(CHANNEL_CHAT_ID, context.bot.id)
        status = bot_member.status
        bot_admin = status in {"administrator", "creator"}
        await update.effective_message.reply_text(
            "🔎 DIAGNÓSTICO DEL CANAL\n"
            f"Canal detectado: {chat.title or 'sin título'}\n"
            f"ID configurado: {CHANNEL_CHAT_ID}\n"
            f"Estado del bot: {status}\n"
            f"Bot administrador: {'sí' if bot_admin else 'NO'}\n"
            f"Usuarios elegibles (iniciaron el bot y siguen en el canal): {eligible_user_count()} · mínimo de referencia: 100\n\n"
            + ("✅ El bot ve el canal y tiene permisos de administrador." if bot_admin else
               "⚠️ Añade el bot como administrador del canal y vuelve a ejecutar /diagnostico_canal.")
        )
    except Exception as exc:
        log.exception("Diagnóstico de canal fallido para CHANNEL_CHAT_ID=%s", CHANNEL_CHAT_ID)
        await update.effective_message.reply_text(
            f"❌ No puedo acceder al canal configurado (ID {CHANNEL_CHAT_ID}).\n"
            f"Error de Telegram: {type(exc).__name__}: {exc}\n\n"
            "Comprueba que CHANNEL_CHAT_ID sea el ID numérico correcto del canal (normalmente empieza por -100) "
            "y que el bot esté añadido como administrador. El enlace t.me/+... no es el ID."
        )


async def require_channel(update, context):
    user = update.effective_user
    if user and await has_channel_access(context.bot, user.id):
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            "🔒 Para participar y consultar tus listas/estadísticas, primero debes unirte al canal. "
            "Después pulsa «Ya me uní · Verificar».",
            reply_markup=channel_join_keyboard()
        )
    return False


def user_keyboard():
    """Panel fijo de opciones con contador integrado en el propio teclado."""
    count = eligible_user_count()
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(f"👥 Usuarios Registrados ({count})")],
            [KeyboardButton("🎟️ Ver listas activas"), KeyboardButton("🎟️ Mis jugadas")],
            [KeyboardButton("💳 Historial de pagos"), KeyboardButton("🏆 Mis premios")],
            [KeyboardButton("📚 Resultados y ganadores"), KeyboardButton("🆘 Ayuda / soporte")],
            [KeyboardButton("📲 Descargar aplicación")],
        ],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Selecciona una opción"
    )


async def refresh_user_progress_panels(context, raffle_id):
    """Envía la barra actualizada a usuarios registrados para renovar su teclado persistente."""
    raffle = get_raffle(raffle_id)
    if not raffle:
        return
    st = stats(raffle_id)
    in_use = st['occupied'] + st['pending']
    text = (f"📊 AVANCE DE LA LISTA «{raffle['name']}»\n"
            f"{progress_bar(in_use, st['total'])}\n"
            f"{in_use} de {st['total']} números ocupados o en proceso ({int(round(in_use*100/max(st['total'],1)))}%).\n"
            f"Quedan {st['available']} disponibles.")
    with db() as con:
        ids = [int(r[0]) for r in con.execute("SELECT user_id FROM bot_users").fetchall()]
    for user_id in ids:
        try:
            await context.bot.send_message(user_id, text, reply_markup=user_keyboard())
        except Exception:
            log.debug("No se pudo actualizar el panel de progreso del usuario %s", user_id, exc_info=True)
        await asyncio.sleep(0.035)




def build_public_results():
    """Resultados históricos completos y ganadores con enlace directo a su chat."""
    with db() as con:
        winners = con.execute(
            """SELECT w.*,r.name AS raffle_name,r.prize,r.prize_value,
                      COALESCE(NULLIF(bu.username,''),'') AS current_username
               FROM winners w
               JOIN raffles r ON r.id=w.raffle_id
               LEFT JOIN bot_users bu ON bu.user_id=w.user_id
               ORDER BY w.announced_at DESC,w.id DESC"""
        ).fetchall()
        draws = con.execute(
            "SELECT draw_date,draw_type,result,fireball FROM lottery_results ORDER BY draw_date DESC,fetched_at DESC"
        ).fetchall()
    blocks = []
    for row in winners:
        username = (row["current_username"] or "").lstrip("@")
        display = f"@{username}" if username else (row["name"] or "Ganador")
        status = "Premio recibido" if row["prize_received"] else "Entrega pendiente"
        blocks.append(
            "🏆 <b>GANADOR</b>\n"
            f"🎟️ <b>Lista:</b> {html.escape(row['raffle_name'])}\n"
            f"🎯 <b>Número ganador:</b> {int(row['number']):02d}\n"
            f"👤 <b>Usuario:</b> <a href=\"tg://user?id={int(row['user_id'])}\">{html.escape(display)}</a>\n"
            f"💰 <b>Premio:</b> {html.escape(row['prize'] or 'Transferencia PIX')} · {format_brl(raffle_prize_amount(row))}\n"
            f"📌 <b>Estado:</b> {status}\n"
            f"📅 <b>Fecha:</b> {html.escape(str(row['announced_at'] or '')[:19].replace('T',' '))}"
        )
    for row in draws:
        blocks.append(
            f"🎯 <b>Florida Pick 3</b>\n📅 {html.escape(row['draw_date'])} · "
            f"{'Mediodía' if row['draw_type']=='M' else 'Noche'}: <b>{html.escape(row['result'])}</b>"
            + (f" · Fireball: {html.escape(row['fireball'])}" if row['fireball'] else "")
        )
    return "📚 <b>RESULTADOS Y GANADORES</b>", (
        "\n\n".join(blocks) if blocks else "Todavía no hay resultados ni ganadores publicados."
    )




async def send_public_results(message):
    title, body = build_public_results()
    # Telegram limita cada mensaje; dividir por registros sin truncar el historial.
    blocks = body.split("\n\n")
    chunks, current = [], ""
    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) > 3500 and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    await message.reply_text(title, parse_mode="HTML", reply_markup=user_keyboard())
    for chunk in chunks:
        await message.reply_text(chunk, parse_mode="HTML", reply_markup=user_keyboard())
        await asyncio.sleep(0.04)

def build_user_stats(uid, action):
    """Construye el historial solicitado para mostrarlo desde el panel principal."""
    with db() as con:
        if action == "plays":
            rows = con.execute(
                "SELECT p.*,r.name AS raffle_name FROM purchases p JOIN raffles r ON r.id=p.raffle_id WHERE p.user_id=? ORDER BY p.id DESC LIMIT 30",
                (uid,)
            ).fetchall()
            pending = con.execute(
                "SELECT rq.*,r.name AS raffle_name FROM requests rq JOIN raffles r ON r.id=rq.raffle_id WHERE rq.user_id=? AND rq.status IN ('awaiting_button','key_requested','awaiting_user_click','awaiting_payment_amount','payment_reported','awaiting_payment_receipt','claim_pending','expired') ORDER BY rq.created_at DESC LIMIT 20",
                (uid,)
            ).fetchall()
            blocks = [f"🎟️ {r['raffle_name']} · Números: {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))} · {float(r['amount']):.2f} {CURRENCY} · {r['status']}" for r in rows]
            blocks += [f"⏳ {r['raffle_name']} · Números: {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))} · {float(r['total_amount'] or 0):.2f} {CURRENCY} · {r['status']}" for r in pending]
            title = "🎟️ *MIS JUGADAS*"
            empty = "Todavía no tienes jugadas registradas."
        elif action == "prizes":
            rows = con.execute(
                "SELECT w.*,r.name AS raffle_name,r.prize,r.prize_value FROM winners w JOIN raffles r ON r.id=w.raffle_id WHERE w.user_id=? ORDER BY w.id DESC LIMIT 30",
                (uid,)
            ).fetchall()
            blocks = [f"🏆 {r['raffle_name']} · Número: {int(r['number']):02d}\n💰 Monto ganado: {format_brl(raffle_prize_amount(r))} · {'Recibido' if r['prize_received'] else 'Pendiente de confirmar'}" for r in rows]
            title = "🏆 *MIS PREMIOS*"
            empty = "Todavía no tienes premios registrados."
        elif action == "payments":
            rows = con.execute(
                "SELECT p.*,r.name AS raffle_name FROM purchases p JOIN raffles r ON r.id=p.raffle_id WHERE p.user_id=? ORDER BY p.id DESC LIMIT 40",
                (uid,)
            ).fetchall()
            pending = con.execute(
                "SELECT rq.*,r.name AS raffle_name FROM requests rq JOIN raffles r ON r.id=rq.raffle_id WHERE rq.user_id=? AND rq.status IN ('awaiting_button','key_requested','awaiting_user_click','awaiting_payment_amount','payment_reported','awaiting_payment_receipt','claim_pending','expired') ORDER BY rq.created_at DESC LIMIT 20",
                (uid,)
            ).fetchall()
            blocks = [f"💳 {r['raffle_name']} · {float(r['amount']):.2f} {CURRENCY} · {r['status']} · {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))}" for r in rows]
            blocks += [f"⏳ {r['raffle_name']} · {float(r['total_amount'] or 0):.2f} {CURRENCY} · {r['status']} · {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))}" for r in pending]
            title = "💳 *HISTORIAL DE PAGOS*"
            empty = "Todavía no hay pagos ni solicitudes registrados."
        else:
            rows = con.execute(
                "SELECT w.*,r.name AS raffle_name,r.prize,r.prize_value FROM winners w JOIN raffles r ON r.id=w.raffle_id ORDER BY w.announced_at DESC LIMIT 20"
            ).fetchall()
            results = con.execute(
                "SELECT draw_date,draw_type,result,fireball FROM lottery_results ORDER BY draw_date DESC,fetched_at DESC LIMIT 10"
            ).fetchall()
            blocks = [f"🏆 {r['raffle_name']} · Número ganador: {int(r['number']):02d} · Premio: {r['prize'] or 'Premio entregado mediante transferencia PIX'} · {'Comprobante publicado' if r['prize_received'] else 'Entrega pendiente'}" for r in rows]
            blocks += [f"🎯 Florida Pick 3 · {r['draw_date']} · {'Mediodía' if r['draw_type']=='M' else 'Noche'}: {r['result']}" for r in results]
            title = "📚 *RESULTADOS Y GANADORES*"
            empty = "Todavía no hay resultados publicados."
    return title, "\n\n".join(blocks) if blocks else empty


def admin_keyboard():
    """Teclado persistente del administrador con el contador integrado."""
    count = eligible_user_count()
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(f"👥 Usuarios Registrados ({count})")],
            [KeyboardButton("➕ Crear lista"), KeyboardButton("📋 Listas activas")],
            [KeyboardButton("📊 Estadísticas"), KeyboardButton("🏆 Indicar Número Ganador")],
            [KeyboardButton("🔄 Resetear lista"), KeyboardButton("🗑️ Borrar lista")],
            [KeyboardButton("👥 Historial"), KeyboardButton("🔄 Actualizar panel")],
            [KeyboardButton("🔒 Bloquear jugadas"), KeyboardButton("🔓 Desbloquear jugadas")],
            [KeyboardButton("💾 Crear respaldo"), KeyboardButton("♻️ Restaurar respaldo")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Selecciona una opción de administración"
    )


def admin_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin:back")]])


def raffle_price_keyboard():
    values = [10, 15, 20, 25, 30, 35, 40, 45, 50]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"R$ {v}", callback_data=f"createprice:{v}") for v in values[i:i+3]]
        for i in range(0, len(values), 3)
    ])


def raffle_name_keyboard():
    """Opciones de nombres predefinidos para crear una lista con un toque."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Lista {n}", callback_data=f"createlistname:{n}")]
        for n in range(1, 6)
    ])


def raffle_percent_keyboard():
    values = (60, 65, 70, 75, 80)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{v}%", callback_data=f"createpercent:{v}") for v in values[i:i+3]]
        for i in range(0, len(values), 3)
    ])


def admin_flow_prompt(step):
    prompts = {
        "name": "Escribe el *nombre único* de la nueva lista. No se permiten nombres repetidos:",
        "price": "Selecciona el *valor de cada número* (R$).",
        "percent": "Selecciona el *porcentaje de la recaudación* que se entregará como premio."
    }
    return prompts[step]


def raffle_locked_until_prize_received(con, raffle_id):
    """Una lista completada queda protegida hasta publicar ganador y confirmar el premio."""
    raffle = con.execute("SELECT status,closed_at FROM raffles WHERE id=?", (raffle_id,)).fetchone()
    if not raffle:
        return False
    if raffle["status"] not in ("sold_out", "finished"):
        return False
    if raffle["status"] == "sold_out":
        # Ignora comprobantes de rondas anteriores después de un reinicio.
        winner = con.execute(
            "SELECT prize_received,channel_message_id FROM winners WHERE raffle_id=? AND announced_at>=? ORDER BY id DESC LIMIT 1",
            (raffle_id, raffle["closed_at"] or "9999")
        ).fetchone()
    else:
        winner = con.execute(
            "SELECT prize_received,channel_message_id FROM winners WHERE raffle_id=? ORDER BY id DESC LIMIT 1",
            (raffle_id,)
        ).fetchone()
    # Exige tanto la publicación del resultado en el canal como el comprobante
    # público de que el ganador recibió el premio.
    return not (winner and winner["prize_received"] == 1 and winner["channel_message_id"])


def active_raffles(group_id):
    with db() as con:
        rows = con.execute(
            "SELECT * FROM raffles WHERE group_id=? AND status='active' ORDER BY id",
            (group_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def raffle_selector_keyboard(raffles):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{r['name']} · {float(r['price']):.2f} {CURRENCY} · {r['total_numbers']} nums",
            callback_data=f"selectraffle:{r['id']}"
        )] for r in raffles
    ])


async def lists_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Para ver y jugar las listas, abre el chat privado del bot y pulsa /start.")
        return
    if not is_admin(update.effective_user.id) and not await require_channel(update, context):
        return
    raffles = active_raffles(DEFAULT_GROUP_ID)
    if not raffles:
        await update.effective_message.reply_text("No hay listas activas en este momento.", reply_markup=user_keyboard())
        return
    await update.effective_message.reply_text("🎟️ *LISTAS DISPONIBLES*\nConsulta cada lista y pulsa el botón que aparece justo debajo de su información.", parse_mode="Markdown", reply_markup=user_keyboard())
    for index, raffle in enumerate(raffles, 1):
        st = stats(raffle["id"])
        summary = (
            f"🎟️ *{raffle['name']}* (Lista #{index})\n"
            f"🔢 Números: {raffle['total_numbers']} · 💵 Por número: {float(raffle['price']):.2f} {CURRENCY}\n"
            f"🏆 Premio: {raffle['prize'] or 'Por anunciar'}\n"
            f"💎 Valor del premio: {raffle.get('prize_value') or 'Por definir'}\n"
            f"📊 Progreso: {progress_bar(st['occupied'] + st['pending'], st['total'])} ({st['occupied'] + st['pending']}/{st['total']})\n"
            f"🟢 Libres: {st['available']} · 🔴 Ocupados: {st['occupied']} · 🟡 Pendientes: {st['pending']}"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(f"🎟️ Jugar en {raffle['name']}", callback_data=f"selectraffle:{raffle['id']}")]])
        await update.effective_message.reply_text(summary, parse_mode="Markdown", reply_markup=keyboard)


def build_welcome_message(current_count: int) -> str:
    """Mensaje oficial de bienvenida, compartido por el chat privado y el canal."""
    return (
        "👋 ¡Bienvenido/a, <b>Inversiones Cripto</b>!\n\n"
        "ℹ️ <b>INFORMACIÓN DEL CANAL Y DE LAS RIFAS</b>\n"
        "Este canal informa sobre las listas de rifas, números confirmados, resultados y ganadores. Para elegir números y gestionar tus participaciones, utiliza los botones del chat privado del bot. La unión al canal es obligatoria para utilizar el bot.\n\n"
        "🎟️ <b>¿Cómo funciona?</b>\n"
        "• Cada lista tiene 100 números, del 01 al 100. Puedes elegir uno o varios números.\n"
        "• El precio por número puede ser diferente en cada lista. Consulta cada lista para confirmar su precio, premio y disponibilidad.\n"
        "• Un número no puede reservarse o confirmarse para dos personas. Sigue las instrucciones de pago y espera la confirmación del bot.\n"
        "• <b>Mientras los 100 números de una lista no estén ocupados y los pagos confirmados, no existe resultado ganador para esa lista.</b> No se anuncia ni se determina un ganador antes de completar esas condiciones.\n"
        "• Una vez completada la lista, el sorteo se basa en el resultado nocturno de <b>Florida Pick 3</b>. Si se completa antes de las 09:00pm, se utiliza el sorteo nocturno de ese día; si se completa después, se utiliza el de la noche siguiente.\n\n"
        "🏆 <b>Premio</b>\n"
        "El premio anunciado para cada lista corresponde entre el 60% – 80% de la recaudación total, según el porcentaje definido para esa lista. Ejemplo: 100 números a R$ 10 generan R$ 1.000 y un premio de R$ 600 a R$ 800, según las condiciones publicadas.\n\n"
        "🔎 <b>Verificación del resultado</b>\n"
        "En el menú fijo encontrarás el botón para descargar la aplicación y consultar el resultado de Florida Pick 3.\n\n"
        f"👥 <b>Participantes actuales:</b> {current_count} usuarios registrados en el bot y miembros del canal. El mínimo de referencia es de 100; el administrador habilita o bloquea las jugadas manualmente.\n\n"
        "📌 Cada botón del menú principal abre directamente la consulta correspondiente, sin submenús."
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Para jugar y consultar tus jugadas, abre el chat privado del bot y pulsa /start.")
        return
    user = update.effective_user
    if is_admin(user.id):
        await update_admin_counter(context)
        await update.effective_message.reply_text(
            "⚙️ PANEL DE ADMINISTRACIÓN\nSelecciona una opción. Los botones permanecerán visibles aquí:",
            reply_markup=admin_keyboard()
        )
        return
    if not await require_channel(update, context):
        return

    before_count = eligible_user_count()
    with db() as con:
        existing = con.execute(
            "SELECT user_id,channel_member FROM bot_users WHERE user_id=?", (user.id,)
        ).fetchone()
        was_member = bool(existing and existing["channel_member"])
        con.execute(
            "INSERT INTO bot_users(user_id,first_name,username,joined_at,channel_member) VALUES(?,?,?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name,username=excluded.username,channel_member=1",
            (user.id, user.full_name or user.first_name or "Participante", user.username or "", iso_now())
        )
    current_count = eligible_user_count()
    await update_user_counters(context, current_count)

    if not existing or not was_member:
        await notify_member_count(context, current_count)

    if not existing:
        welcome = build_welcome_message(current_count)
    else:
        welcome = (
            f"¡Hola de nuevo! Actualmente hay {current_count} usuarios registrados en el bot y miembros del canal. "
            "Puedes revisar las listas y consultar tus datos. El mínimo de referencia es 100, pero el administrador puede habilitar manualmente las jugadas para pruebas. "
            "Recuerda que no hay resultado ganador hasta que los 100 números de una lista estén ocupados y los pagos confirmados."
        )
    await update.effective_message.reply_text(
        welcome, parse_mode="HTML"
    )
    await update.effective_message.reply_text(
        "👇 *PANEL PRINCIPAL*\nCada botón muestra directamente su información:",
        parse_mode="Markdown", reply_markup=user_keyboard()
    )

async def diagnostic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnóstico visible solo para el administrador del bot."""
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if not user or not is_admin(user.id):
        await message.reply_text("⛔ No autorizado.")
        return
    try:
        me = await context.bot.get_me()
        with db() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM raffles WHERE group_id=? AND status='active'",
                (chat.id,)
            ).fetchone()[0]
        webhook = await context.bot.get_webhook_info()
        await message.reply_text(
            "🔎 DIAGNÓSTICO DEL BOT\n"
            f"Bot: @{me.username or 'sin_usuario'} (ID {me.id})\n"
            f"Chat actual: {chat.id} ({chat.type})\n"
            f"GROUP_CHAT_ID configurado: {DEFAULT_GROUP_ID}\n"
            f"Listas activas en este chat: {count}\n"
            f"Webhook configurado: {'sí' if webhook.url else 'no'}\n"
            "Si el chat actual y GROUP_CHAT_ID no coinciden, corrige GROUP_CHAT_ID en Render."
        )
    except Exception:
        log.exception("Falló /diagnostico")
        await message.reply_text("No se pudo completar el diagnóstico. Revisa los logs del servicio.")


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Registra errores de handlers para que no queden fallos silenciosos."""
    log.error("Excepción procesando actualización de Telegram", exc_info=context.error)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ No autorizado.")
        return
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Por seguridad, abre el chat privado del bot y escribe /admin.")
        return
    await update_admin_counter(context)
    await update.effective_message.reply_text(
        f"🎟️ Estado general: {'JUGADAS HABILITADAS' if is_playing_enabled() else 'JUGADAS BLOQUEADAS'}"
    )
    await update.effective_message.reply_text(
        "⚙️ PANEL DE ADMINISTRACIÓN\nSelecciona una operación. Los botones permanecerán visibles aquí:",
        reply_markup=admin_keyboard()
    )


async def new_raffle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Creación rápida: /nueva Nombre|precio_por_numero|porcentaje"""
    if not is_admin(update.effective_user.id):
        return
    raw = " ".join(context.args)
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) != 3:
        await update.effective_message.reply_text(
            "Uso: /nueva Nombre de lista|15|70\n"
            "Precios permitidos: 10, 15, 20, 25, 30, 35, 40, 45 o 50. "
            "Porcentajes permitidos: 60, 70 u 80."
        )
        return
    try:
        name, price_text, percent_text = parts
        price = Decimal(price_text.replace(",", "."))
        percent = Decimal(percent_text.replace("%", "").replace(",", "."))
        if not name or len(name) > 100 or price not in {Decimal(x) for x in (10,15,20,25,30,35,40,45,50)} or percent not in {Decimal(60),Decimal(65),Decimal(70),Decimal(75),Decimal(80)}:
            raise ValueError()
        amount = (price * Decimal("100") * percent / Decimal("100")).quantize(Decimal("0.01"))
        amount_fmt = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        prize_value = f"R$ {amount_fmt} ({percent}% do total)"
        group = DEFAULT_GROUP_ID
        if group == 0:
            await update.effective_message.reply_text("El ID del grupo configurado (GROUP_CHAT_ID) es 0. Configúralo en el entorno del bot.")
            return
        with db() as con:
            if con.execute("SELECT 1 FROM raffles WHERE lower(trim(name))=lower(trim(?))", (name,)).fetchone():
                await update.effective_message.reply_text("⛔ Ya existe una lista con ese nombre. Escribe un nombre diferente.")
                return
            cur = con.execute(
                """INSERT INTO raffles(name,group_id,total_numbers,price,prize,prize_value,status,created_at)
                   VALUES(?,?,100,?,'Premio entregado mediante transferencia PIX',?, 'active',?)""",
                (name, group, float(price), prize_value, iso_now())
            )
            rid = cur.lastrowid
            con.executemany(
                "INSERT INTO numbers(raffle_id,number,status,updated_at) VALUES(?,?,?,?)",
                [(rid, n, "available", iso_now()) for n in range(1, 101)]
            )
    except (ValueError, InvalidOperation, sqlite3.IntegrityError):
        await update.effective_message.reply_text("Datos inválidos o nombre repetido. Comprueba las opciones y vuelve a intentarlo.")
        return
    audit("new_raffle", f"Rifa #{rid}: {name}", update.effective_user.id)
    await update.effective_message.reply_text(
        f"✅ Lista #{rid} creada: {name} (100 números).\n"
        f"Precio por número: R$ {float(price):.2f}\nPorcentaje del premio: {percent}%\nPremio calculado: {prize_value}"
    )


def raffle_prize_amount(raffle):
    """Calcula el premio en R$ según precio, cantidad de números y porcentaje de esta lista."""
    if not raffle:
        return Decimal("0.00")
    raw_value = str(raffle.get("prize_value") or "") if hasattr(raffle, "get") else str(raffle["prize_value"] or "")
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", raw_value)
    if match:
        percent = Decimal(match.group(1).replace(",", "."))
        price = Decimal(str(raffle["price"] or 0))
        total_numbers = Decimal(str(raffle["total_numbers"] or 100))
        return (price * total_numbers * percent / Decimal("100")).quantize(Decimal("0.01"))
    # Compatibilidad con listas antiguas cuyo campo solo contiene el monto.
    amount_match = re.search(r"(?:R\$\s*)?(\d[\d.]*,\d{2})", raw_value)
    if amount_match:
        return Decimal(amount_match.group(1).replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))
    return Decimal("0.00")


def format_brl(value):
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def verify_winner_ownership(con, winner):
    """Revalida que el ganador conserva el número y que existe una compra aprobada."""
    number_row = con.execute(
        "SELECT user_id,status FROM numbers WHERE raffle_id=? AND number=?",
        (winner["raffle_id"], winner["number"])
    ).fetchone()
    if not number_row or number_row["status"] != "occupied" or number_row["user_id"] != winner["user_id"]:
        return False
    purchases = con.execute(
        "SELECT numbers_json FROM purchases WHERE raffle_id=? AND user_id=? AND status='approved'",
        (winner["raffle_id"], winner["user_id"])
    ).fetchall()
    for purchase in purchases:
        try:
            if int(winner["number"]) in [int(n) for n in json.loads(purchase["numbers_json"])]:
                return True
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return False


async def publish_winner(update, context, raffle_id, number):
    raffle = get_raffle(raffle_id)
    if not raffle or not 1 <= number <= raffle["total_numbers"]:
        await update.effective_message.reply_text("Lista o número inválido.")
        return
    prize_amount = raffle_prize_amount(raffle)
    prize_amount_text = format_brl(prize_amount)
    with db() as con:
        row = con.execute(
            "SELECT * FROM numbers WHERE raffle_id=? AND number=? AND status='occupied'",
            (raffle_id, number)
        ).fetchone()
        if not row:
            await update.effective_message.reply_text("Ese número no está ocupado con un pago aprobado.")
            return
        if raffle["status"] not in ("active", "sold_out"):
            await update.effective_message.reply_text("Esta lista no está disponible para declarar ganador.")
            return
        cur = con.execute(
            "INSERT INTO winners(raffle_id,number,user_id,name,announced_at) VALUES(?,?,?,?,?)",
            (raffle_id, number, row["user_id"], row["name"], iso_now())
        )
        winner_id = cur.lastrowid
        con.execute("UPDATE raffles SET status='finished',closed_at=? WHERE id=?", (iso_now(), raffle_id))
    msg = (
        f"🎉 ¡FELICIDADES, {row['name']}! 🎉\n\n"
        f"🏆 Has sido el ganador del sorteo de la *{raffle['name']}*.\n"
        f"🎯 Número ganador: *{number:02d}*.\n"
        f"💰 *Monto del premio: {prize_amount_text}*\n\n"
        "Para recibir tu premio, solicita la entrega al administrador pulsando el botón de abajo."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Solicitar Premio al Administrador", callback_data=f"prizerequest:{winner_id}")]])
    try:
        sent = await context.bot.send_message(chat_id=row["user_id"], text=msg, parse_mode="Markdown", reply_markup=keyboard)
        with db() as con:
            con.execute("UPDATE winners SET group_message_id=?,prize_status='announced' WHERE id=?", (sent.message_id, winner_id))
    except Exception:
        log.exception("No se pudo enviar el resultado al ganador en privado")
        await update.effective_message.reply_text("El ganador quedó registrado, pero no pude enviarle el mensaje privado. El usuario debe iniciar el bot con /start.")
        return
    if CHANNEL_CHAT_ID:
        try:
            channel_msg = await context.bot.send_message(
                chat_id=CHANNEL_CHAT_ID, text=(
                    f"🏆 <b>RESULTADO DE LA RIFA</b>\n\n"
                    f"🎟️ Lista: <b>{html.escape(raffle['name'])}</b>\n"
                    f"🎯 Número ganador: <b>{number:02d}</b>\n"
                    f"👤 Usuario ganador: <a href=\"tg://user?id={row['user_id']}\">{html.escape(row['name'])}</a>\n"
                    f"💰 <b>Monto del premio: {prize_amount_text}</b>\n\n"
                    "El ganador recibirá las instrucciones para solicitar su premio por mensaje privado."
                ), parse_mode="HTML"
            )
            with db() as con:
                con.execute("UPDATE winners SET channel_message_id=? WHERE id=?", (channel_msg.message_id, winner_id))
        except Exception:
            log.exception("No se pudo publicar ganador en el canal configurado")
    audit("winner", f"Rifa #{raffle_id}, número {number}, usuario {row['user_id']}", update.effective_user.id)
    await update.effective_message.reply_text("✅ Resultado enviado al chat privado del ganador.")


async def winner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ No autorizado.")
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Uso: /ganador ID_RIFA NUMERO")
        return
    try:
        raffle_id, number = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("El ID y el número deben ser enteros.")
        return
    await publish_winner(update, context, raffle_id, number)


async def process_admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa respuestas de los formularios conversacionales del panel privado."""
    msg = update.effective_message
    flow = context.user_data.get("admin_flow")
    if not flow or update.effective_chat.type != "private" or not is_admin(update.effective_user.id):
        return False
    value = (msg.text or "").strip()
    step = flow.get("step")
    try:
        if step == "name":
            if not value or len(value) > 100:
                await msg.reply_text("Escribe un nombre válido (máximo 100 caracteres).")
                return True
            with db() as con:
                duplicate = con.execute("SELECT 1 FROM raffles WHERE lower(trim(name))=lower(trim(?))", (value,)).fetchone()
            if duplicate:
                await msg.reply_text("⛔ Ese nombre ya está utilizado. Escribe otro nombre para la lista:")
                return True
            flow["name"] = value
            flow["total"] = 100
            flow["group"] = DEFAULT_GROUP_ID
            flow["step"] = "price"
            await msg.reply_text(admin_flow_prompt("price"), parse_mode="Markdown", reply_markup=raffle_price_keyboard())
            return True
        elif step in {"price", "percent"}:
            await msg.reply_text("Por favor, selecciona una de las opciones que aparecen en los botones.",
                                 reply_markup=raffle_price_keyboard() if step == "price" else raffle_percent_keyboard())
            return True
        elif step == "winner_raffle":
            raffle_id = int(value)
            raffle = get_raffle(raffle_id)
            if not raffle:
                await msg.reply_text("No existe esa lista. Envía un ID válido.")
                return True
            flow["raffle_id"] = raffle_id
            flow["step"] = "winner_number"
            await msg.reply_text(f"Lista seleccionada: {raffle['name']} (#{raffle_id}).\nAhora escribe el *número ganador*:", parse_mode="Markdown")
            return True
        elif step == "winner_number":
            number = int(value)
            raffle_id = flow["raffle_id"]
            context.user_data.pop("admin_flow", None)
            await publish_winner(update, context, raffle_id, number)
            return True
        else:
            context.user_data.pop("admin_flow", None)
            await msg.reply_text("Formulario cancelado. Abre /admin para comenzar de nuevo.")
            return True
    except (ValueError, InvalidOperation):
        await msg.reply_text("Ese dato no es válido. Inténtalo de nuevo.")
        return True
    await msg.reply_text(admin_flow_prompt(flow["step"]), parse_mode="Markdown", reply_markup=admin_keyboard())
    return True


async def admin_menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    """Ejecuta las opciones del teclado fijo sin exigir comandos ni botones inline."""
    msg = update.effective_message
    if not is_admin(update.effective_user.id) or update.effective_chat.type != "private":
        await msg.reply_text("⛔ No autorizado.")
        return

    if action != "create":
        context.user_data.pop("admin_flow", None)
    if action in {"block", "unblock"}:
        target_status = "active" if action == "block" else "blocked"
        verb = "bloquear" if action == "block" else "desbloquear"
        with db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT id,name,status FROM raffles WHERE status=? ORDER BY id", (target_status,)
            ).fetchall()]
        if not rows:
            await msg.reply_text(
                "No hay listas disponibles para " + verb + ".",
                reply_markup=admin_keyboard()
            )
            return
        prefix = "raffleblock" if action == "block" else "raffleunblock"
        icon = "🔒" if action == "block" else "🔓"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"{icon} {r['name']} (#{r['id']})", callback_data=f"{prefix}:{r['id']}")] for r in rows]
            + [[InlineKeyboardButton(f"{icon} Todas las listas", callback_data=f"{prefix}:all")],
               [InlineKeyboardButton("⬅️ Cancelar", callback_data="admin:back")]]
        )
        await msg.reply_text(
            f"Selecciona la lista que deseas {verb}. También puedes elegir «Todas las listas».",
            reply_markup=keyboard
        )
        return
    if action in {"block_user", "unblock_user"}:
        context.user_data["awaiting_user_play_control"] = "block" if action == "block_user" else "unblock"
        verb = "bloquear" if action == "block_user" else "desbloquear"
        await msg.reply_text(
            f"Escribe el ID numérico o @usuario registrado al que deseas {verb}.\n"
            "Puedes obtener el ID desde el aviso de registro o desde el historial de usuarios.",
            reply_markup=admin_keyboard()
        )
        return
    if action == "users":
        await msg.reply_text("📊 BARRA INFORMATIVA\n" + participant_progress_label() + "\n\n👥 ESTADO DE PARTICIPANTES\n" + play_status_text(), reply_markup=admin_keyboard())
        return
    if action == "back":
        with db() as con:
            total = con.execute("SELECT COUNT(*) FROM raffles").fetchone()[0]
            active = con.execute("SELECT COUNT(*) FROM raffles WHERE status='active'").fetchone()[0]
        await msg.reply_text(
            f"🔄 Panel actualizado.\nListas guardadas: {total} · Listas activas: {active}\n"
            f"Base de datos: {Path(DB_PATH).expanduser().resolve()}\n"
            "La actualización del panel no reinicia ni borra las listas.",
            reply_markup=admin_keyboard()
        )
        return
    if action == "create":
        context.user_data["admin_flow"] = {"kind": "create", "step": "name"}
        await msg.reply_text(admin_flow_prompt("name"), parse_mode="Markdown", reply_markup=raffle_name_keyboard())
    elif action == "active":
        with db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM raffles WHERE status='active' ORDER BY id"
            ).fetchall()]
        if not rows:
            result = "No hay listas activas registradas."
        else:
            blocks = []
            for item in rows:
                st = stats(item["id"])
                blocks.append(
                    f"🎟️ *{item['name']}* (#{item['id']})\n"
                    f"Números: {st['total']} · Precio: {st['price']:.2f} {CURRENCY}\n"
                    f"Premio: {item['prize'] or 'Por anunciar'} · Valor: {item.get('prize_value') or 'Por definir'}\n"
                    f"Ocupados: {st['occupied']} · Pendientes: {st['pending']} · Libres: {st['available']}\n"
                    f"Recaudado: {st['revenue']:.2f} {CURRENCY} · Grupo: {item['group_id']}"
                )
            result = "📋 *LISTAS ACTIVAS*\n\n" + "\n\n".join(blocks)
        await msg.reply_text(result, parse_mode="Markdown", reply_markup=admin_keyboard())
    elif action == "stats":
        with db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM raffles ORDER BY id DESC"
            ).fetchall()]
        if not rows:
            await msg.reply_text("No hay listas registradas.", reply_markup=admin_keyboard())
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"📊 {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"adminstats:{r['id']}")] for r in rows]
                + [[InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin:back")]]
            )
            await msg.reply_text("Selecciona cualquier lista para consultar sus estadísticas, estén abiertas o cerradas:", reply_markup=keyboard)
    elif action == "winner":
        with db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM raffles WHERE status IN ('active','sold_out') ORDER BY id"
            ).fetchall()]
        if not rows:
            await msg.reply_text("No hay listas disponibles para declarar ganador.", reply_markup=admin_keyboard())
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"🏆 {r['name']} (#{r['id']})", callback_data=f"winnerchoose:{r['id']}")] for r in rows]
            )
            await msg.reply_text("¿En cuál lista vas a indicar el ganador?", reply_markup=keyboard)
    elif action == "reset":
        with db() as con:
            rows = con.execute("SELECT id,name,status FROM raffles ORDER BY id DESC LIMIT 50").fetchall()
        if not rows:
            await msg.reply_text("No hay listas registradas.", reply_markup=admin_keyboard())
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"🔄 {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"resetchoose:{r['id']}")] for r in rows]
            )
            await msg.reply_text(
                "Selecciona la lista que deseas reiniciar. El historial de compras se conserva.",
                reply_markup=keyboard
            )
    elif action == "delete":
        with db() as con:
            rows = con.execute("SELECT id,name,status FROM raffles ORDER BY id DESC LIMIT 50").fetchall()
        if not rows:
            await msg.reply_text("No hay listas registradas.", reply_markup=admin_keyboard())
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"🗑️ {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"deletechoose:{r['id']}")] for r in rows]
            )
            await msg.reply_text("Selecciona la lista que deseas borrar. Las listas completadas están protegidas hasta confirmar la entrega del premio.", reply_markup=keyboard)
    elif action == "backup":
        await send_backup(update, context)
    elif action == "restore":
        context.user_data["awaiting_restore"] = True
        await msg.reply_text(
            "Envía aquí el archivo de respaldo *.json*. El sistema lo validará y te pedirá confirmación.",
            parse_mode="Markdown", reply_markup=admin_keyboard()
        )
    elif action == "history":
        with db() as con:
            rows = con.execute("SELECT id,name,status FROM raffles ORDER BY id DESC").fetchall()
        if not rows:
            await msg.reply_text("Todavía no hay listas registradas.", reply_markup=admin_keyboard())
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"📚 {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"adminhistory:{r['id']}")] for r in rows]
                + [[InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin:back")]]
            )
            await msg.reply_text("Selecciona la lista cuyo historial de jugadas deseas revisar:", reply_markup=keyboard)
    elif action == "back":
        context.user_data.pop("admin_flow", None)
        await msg.reply_text(
            "⚙️ *PANEL DE ADMINISTRACIÓN*\nSelecciona una operación:",
            parse_mode="Markdown", reply_markup=admin_keyboard()
        )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text or msg.text.startswith("/"):
        return
    text = msg.text.strip()

    # Llave PIX del ganador: solo se acepta del usuario propietario de la solicitud vigente.
    if update.effective_chat.type == "private" and not is_admin(update.effective_user.id):
        with db() as con:
            winner = con.execute(
                "SELECT * FROM winners WHERE user_id=? AND prize_status='pix_input_pending' AND prize_received=0 ORDER BY id DESC LIMIT 1",
                (update.effective_user.id,)
            ).fetchone()
        pix_winner_id = winner["id"] if winner else None
    else:
        winner = None
        pix_winner_id = None
    if pix_winner_id:
        pix_key = text.strip()
        if len(pix_key) < 3 or len(pix_key) > 200:
            await msg.reply_text("La llave PIX no parece válida. Envíala nuevamente (máximo 200 caracteres).")
            return
        raffle = get_raffle(winner["raffle_id"])
        prize_amount_text = format_brl(raffle_prize_amount(raffle))
        try:
            with db() as con:
                con.execute("UPDATE winners SET pix_key=?,prize_status='pix_received' WHERE id=? AND user_id=? AND prize_status='pix_input_pending'",
                            (pix_key, winner["id"], update.effective_user.id))
            copy_row = []
            try:
                from telegram import CopyTextButton
                copy_row.append([InlineKeyboardButton(text="📋 Copiar llave PIX", copy_text=CopyTextButton(text=pix_key))])
            except (ImportError, TypeError):
                pass
            copy_row.append([InlineKeyboardButton("💸 Premio enviado", callback_data=f"prizepaid:{winner['id']}")])
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=("🔑 <b>LLAVE PIX RECIBIDA · PREMIO</b>\n\n"
                      f"Lista: <b>{html.escape(raffle['name'] if raffle else str(winner['raffle_id']))}</b>\n"
                      f"Ganador: <b>{html.escape(winner['name'])}</b>\n"
                      f"ID Telegram: <code>{winner['user_id']}</code>\n"
                      f"Número ganador: <b>{int(winner['number']):02d}</b>\n"
                      f"Monto del premio: <b>{prize_amount_text}</b>\n\n"
                      f"Llave PIX: <code>{html.escape(pix_key)}</code>\n\n"
                      "Usa el botón para copiar la llave. Cuando completes la transferencia, pulsa «Premio enviado» y adjunta la captura."),
                parse_mode="HTML", reply_markup=InlineKeyboardMarkup(copy_row)
            )
            await msg.reply_text("✅ Tu llave PIX fue enviada al administrador. Te avisaremos cuando se realice la transferencia.")
            audit("winner_pix_received", f"Ganador #{winner['id']} recibió llave PIX", update.effective_user.id)
        except Exception:
            log.exception("No se pudo procesar o enviar la llave PIX del ganador")
            await msg.reply_text("No pude enviar la llave PIX al administrador. Inténtalo nuevamente.")
        return

    # Las participaciones se gestionan exclusivamente en el chat privado del bot.
    if update.effective_chat.type != "private":
        if not is_admin(update.effective_user.id):
            await msg.reply_text("🔒 Para jugar, abre el chat privado del bot y pulsa /start. Las jugadas no se realizan en el grupo.")
        return

    # Bloqueo/desbloqueo individual de participantes registrados, exclusivo del administrador.
    play_control = context.user_data.get("awaiting_user_play_control")
    if is_admin(update.effective_user.id) and play_control:
        target_text = text.strip()
        with db() as con:
            if target_text.startswith("@"):
                target = con.execute("SELECT user_id,first_name,username FROM bot_users WHERE lower(username)=lower(?)", (target_text[1:],)).fetchone()
            else:
                try:
                    target_id = int(target_text)
                except ValueError:
                    target_id = 0
                target = con.execute("SELECT user_id,first_name,username FROM bot_users WHERE user_id=?", (target_id,)).fetchone()
            if not target:
                await msg.reply_text("❌ No encontré ese usuario registrado. Comprueba el ID o @usuario e inténtalo de nuevo.", reply_markup=admin_keyboard())
                return
            blocked = 1 if play_control == "block" else 0
            con.execute("UPDATE bot_users SET playing_blocked=? WHERE user_id=?", (blocked, target["user_id"]))
        context.user_data.pop("awaiting_user_play_control", None)
        action_name = "playing_user_blocked" if blocked else "playing_user_unblocked"
        audit(action_name, f"Usuario {target['user_id']} (@{target['username'] or 'sin_username'})", update.effective_user.id)
        status_text = "🔒 Tus jugadas han sido bloqueadas por el administrador. Puedes seguir consultando las listas y tu historial." if blocked else "🔓 El administrador ha desbloqueado tus jugadas. Ya puedes participar si las jugadas generales están habilitadas."
        try:
            await context.bot.send_message(int(target["user_id"]), status_text, reply_markup=user_keyboard())
        except Exception:
            log.info("No se pudo notificar al usuario %s", target["user_id"], exc_info=True)
        await msg.reply_text(
            f"{'🔒 Bloqueado' if blocked else '🔓 Desbloqueado'}: {target['first_name']} · ID {target['user_id']}.",
            reply_markup=admin_keyboard()
        )
        return

    # Captura del importe declarado antes de recibir la foto obligatoria.
    if update.effective_user and not is_admin(update.effective_user.id) and text:
        with db() as con:
            amount_req = con.execute("SELECT * FROM requests WHERE user_id=? AND status='awaiting_payment_amount' ORDER BY created_at DESC LIMIT 1", (update.effective_user.id,)).fetchone()
        if amount_req:
            normalized = text.strip().upper().replace("R$", "").replace("BRL", "").replace("$", "").replace(" ", "")
            if "," in normalized:
                normalized = normalized.replace(".", "").replace(",", ".")
            try:
                declared_decimal = Decimal(normalized).quantize(Decimal("0.01"))
                if declared_decimal <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                await msg.reply_text("❌ Monto inválido. Escribe el monto exacto, por ejemplo: 40,00.")
                return
            expected = Decimal(str(amount_req["total_amount"] or 0)).quantize(Decimal("0.01"))
            if declared_decimal != expected:
                expected_fmt = f"{expected:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                await msg.reply_text(
                    f"❌ Monto incorrecto. Según los números que seleccionaste, debes transferir *{CURRENCY} {expected_fmt}*. "
                    f"Ingresaste *{CURRENCY} {declared_decimal:.2f}*.\n\nVuelve a escribir el monto correcto para continuar.",
                    parse_mode="Markdown"
                )
                return
            declared = float(declared_decimal)
            with db() as con:
                con.execute("UPDATE requests SET reported_amount=?,status='awaiting_payment_receipt' WHERE id=? AND status='awaiting_payment_amount'", (declared, amount_req["id"]))
            await msg.reply_text(f"✅ Monto correcto registrado: {declared:.2f} {CURRENCY}.\n\n📸 Ahora adjunta obligatoriamente la captura de la transferencia en este chat privado.")
            return

    # Respuesta directa a una consulta de soporte; el botón identifica al remitente exacto.
    support_target = context.user_data.get("awaiting_support_reply")
    if is_admin(update.effective_user.id) and support_target:
        target_id = int(support_target)
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="💬 RESPUESTA DE SOPORTE\n\n" + text,
            )
            context.user_data.pop("awaiting_support_reply", None)
            await msg.reply_text(f"✅ Respuesta enviada al participante {target_id}.", reply_markup=admin_keyboard())
        except Exception:
            log.exception("No se pudo enviar la respuesta de soporte al usuario %s", target_id)
            await msg.reply_text("❌ No pude enviar la respuesta. Comprueba que el usuario haya iniciado el bot.")
        return

    if not is_admin(update.effective_user.id):
        with db() as con:
            waiting_receipt = con.execute(
                "SELECT id FROM requests WHERE user_id=? AND status IN ('awaiting_payment_amount','awaiting_payment_receipt') ORDER BY created_at DESC LIMIT 1",
                (update.effective_user.id,)
            ).fetchone()
        if waiting_receipt:
            await msg.reply_text("📸 Para continuar es obligatorio adjuntar una foto del comprobante de transferencia. Envía la captura como imagen, no como texto.")
            return

    if not is_admin(update.effective_user.id) and not await require_channel(update, context):
        return

    if not is_admin(update.effective_user.id):
        if text in {"🎟️ Ver listas activas", "listas", "lista", "hola", "buenas", "rifa", "inicio"}:
            await lists_cmd(update, context)
            return
        direct_actions = {
            "🎟️ Mis jugadas": "plays",
            "💳 Historial de pagos": "payments",
            "🏆 Mis premios": "prizes",
            "📚 Resultados y ganadores": "history",
        }
        if text == "📲 Descargar aplicación":
            if DOWNLOAD_APP_URL:
                await msg.reply_text("📲 Pulsa el enlace para descargar la aplicación:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Descargar aplicación", url=DOWNLOAD_APP_URL)]]))
            else:
                await msg.reply_text("El administrador todavía debe proporcionar el enlace de descarga de la aplicación.")
            return
        if text in direct_actions:
            if direct_actions[text] == "history":
                await send_public_results(msg)
            else:
                title, body = build_user_stats(update.effective_user.id, direct_actions[text])
                await msg.reply_text(title + "\n\n" + body, parse_mode="Markdown", reply_markup=user_keyboard())
            return
        if text == "🆘 Ayuda / soporte":
            context.user_data["awaiting_support"] = True
            await msg.reply_text("🆘 Escribe tu consulta en un solo mensaje. La enviaré al administrador para que pueda ayudarte.")
            return
        if context.user_data.get("awaiting_support"):
            context.user_data.pop("awaiting_support", None)
            try:
                support_keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Responder a este participante", callback_data=f"supportreply:{update.effective_user.id}")
                ]])
                await context.bot.send_message(ADMIN_ID,
                    "🆘 SOLICITUD DE SOPORTE\n"
                    f"Participante: {update.effective_user.full_name}\n"
                    f"ID: {update.effective_user.id}\n"
                    f"Usuario: @{update.effective_user.username or 'sin username'}\n\n{text}",
                    reply_markup=support_keyboard)
                await msg.reply_text("✅ Tu consulta fue enviada al administrador. Te responderá por este chat.", reply_markup=user_keyboard())
            except Exception:
                log.exception("No se pudo enviar la consulta de soporte")
                await msg.reply_text("No pude enviar tu consulta ahora. Inténtalo de nuevo más tarde.", reply_markup=user_keyboard())
            return

    # Panel fijo: solo responde en el chat privado y al administrador autorizado.
    if update.effective_chat.type == "private" and is_admin(update.effective_user.id):
        if text.startswith("👥 Usuarios Registrados ("):
            await msg.reply_text(
                f"👥 Usuarios Registrados: {eligible_user_count()}",
                reply_markup=admin_keyboard()
            )
            return
        action = ADMIN_MENU_ACTIONS.get(text)
        if action:
            await admin_menu_action(update, context, action)
            return

    if text.startswith("👥 Usuarios Registrados ("):
        await msg.reply_text(
            f"👥 Usuarios Registrados: {eligible_user_count()}",
            reply_markup=user_keyboard()
        )
        return

    if await process_admin_flow(update, context):
        return
    if text.lower() == "lista":
        selected_id = context.user_data.get("selected_raffle_id")
        selected = get_raffle(selected_id) if selected_id else None
        if selected and selected["group_id"] == DEFAULT_GROUP_ID and selected["status"] == "active":
            await msg.reply_text(list_text(selected_id), parse_mode="Markdown")
        else:
            await lists_cmd(update, context)
        return
    if text.lower() in {"listas", "hola", "buenas", "rifa", "inicio"}:
        await lists_cmd(update, context)
        return
    if not re.fullmatch(r"\d+(?:\s*,\s*\d+)*", text):
        return

    if not is_admin(update.effective_user.id):
        with db() as con:
            user_block = con.execute("SELECT playing_blocked FROM bot_users WHERE user_id=?", (update.effective_user.id,)).fetchone()
        if user_block and int(user_block["playing_blocked"] or 0):
            await msg.reply_text(
                "🔒 *TUS JUGADAS ESTÁN BLOQUEADAS*\n\nEl administrador ha bloqueado temporalmente tu posibilidad de reservar números. Puedes seguir consultando las listas y tu historial.",
                parse_mode="Markdown", reply_markup=user_keyboard()
            )
            return
        eligible = eligible_user_count()
        if not is_playing_enabled():
            await msg.reply_text(
                "🔒 *LAS JUGADAS ESTÁN BLOQUEADAS POR EL ADMINISTRADOR*\n\n"
                f"Actualmente hay {eligible} usuario(s) registrados y miembros del canal. "
                "Puedes consultar las listas, pero no reservar números hasta que el administrador desbloquee las jugadas.",
                parse_mode="Markdown", reply_markup=user_keyboard()
            )
            return

    group_id = DEFAULT_GROUP_ID
    selected_id = context.user_data.get("selected_raffle_id")
    raffle = get_raffle(selected_id) if selected_id else None
    if raffle and (raffle["group_id"] != group_id or raffle["status"] != "active"):
        raffle = None
        context.user_data.pop("selected_raffle_id", None)
    if not raffle:
        raffles = active_raffles(group_id)
        if len(raffles) == 1:
            raffle = raffles[0]
            context.user_data["selected_raffle_id"] = raffle["id"]
        elif len(raffles) > 1:
            await msg.reply_text(
                "Antes de escoger números, selecciona la lista correspondiente con /listas."
            )
            return
        else:
            await msg.reply_text("🔒 No hay listas activas en este grupo.")
            return

    numbers = list(dict.fromkeys(int(x.strip()) for x in text.split(",")))
    if any(n < 1 or n > raffle["total_numbers"] for n in numbers):
        await msg.reply_text(f"Los números deben estar entre 1 y {raffle['total_numbers']}.")
        return
    uid = update.effective_user.id
    uname = update.effective_user.username or ""
    name = update.effective_user.full_name or update.effective_user.first_name or "Participante"
    req_id = uuid.uuid4().hex[:12]
    created = now_utc()
    first_deadline = created + timedelta(minutes=RESERVATION_MINUTES)

    async with db_lock:
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in numbers)
            rows = con.execute(
                f"SELECT number,status,user_id,name,username FROM numbers "
                f"WHERE raffle_id=? AND number IN ({placeholders})",
                [raffle["id"], *numbers]
            ).fetchall()
            by_num = {r["number"]: dict(r) for r in rows}
            conflicts = [n for n in numbers if by_num.get(n, {}).get("status") != "available"]
            if conflicts:
                details = []
                for n in conflicts:
                    owner = by_num.get(n, {})
                    owner_id = owner.get("user_id")
                    owner_name = owner.get("name") or "otro participante"
                    owner_username = (owner.get("username") or "").strip().lstrip("@")
                    if owner_id:
                        display = f"@{owner_username}" if owner_username else owner_name
                        owner_ref = f'<a href="tg://user?id={owner_id}">{html.escape(display)}</a>'
                        details.append(f"• Número <b>{n:02d}</b> — ocupado por {owner_ref}")
                    else:
                        details.append(f"• Número <b>{n:02d}</b> — no disponible")
                await msg.reply_text(
                    "⚠️ <b>Uno o más números ya están ocupados:</b>\n\n" + "\n".join(details) +
                    "\n\nElige otros números disponibles.",
                    parse_mode="HTML", reply_markup=user_keyboard()
                )
                return
            con.executemany(
                "UPDATE numbers SET status='pending',user_id=?,name=?,username=?,updated_at=? "
                "WHERE raffle_id=? AND number=? AND status='available'",
                [(uid, name, uname, iso_now(), raffle["id"], n) for n in numbers]
            )
            con.execute(
                """INSERT INTO requests(id,raffle_id,user_id,name,username,numbers_json,
                   origin_chat,status,created_at,expires_at,price_per_number,total_amount)
                   VALUES(?,?,?,?,?,?,?,'awaiting_button',?,?,?,?)""",
                (req_id, raffle["id"], uid, name, uname, json.dumps(numbers),
                 update.effective_chat.id, created.isoformat(), first_deadline.isoformat(),
                 float(raffle["price"]), len(numbers) * float(raffle["price"]))
            )

    amount = len(numbers) * float(raffle["price"])
    number_text = ", ".join(f"{n:02d}" for n in numbers)
    request_text = (
        f"⏳ *SOLICITUD EN PROCESO*\n\n"
        f"🎟️ Lista: *{raffle['name']}*\n"
        f"👤 Participante: [{name}](tg://user?id={uid})\n"
        f"🔢 Números: *{number_text}*\n"
        f"💵 Precio por número: *{float(raffle['price']):.2f} {CURRENCY}*\n"
        f"💰 Total a pagar: *{amount:.2f} {CURRENCY}*\n"
        f"🏆 Premio: *{raffle['prize'] or 'Por anunciar'}*\n"
        f"💎 Valor del premio: *{raffle.get('prize_value') or 'Por definir'}*\n\n"
        f"Realiza el pago dentro de los próximos {RESERVATION_MINUTES} minutos. "
        "Pulsa «Solicitar Llave PIX» para recibir la llave de pago por este chat privado."
    )
    try:
        key_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔑 Solicitar Llave PIX", callback_data=f"keyrequest:{req_id}")
        ]])
        sent = await context.bot.send_message(
            chat_id=update.effective_chat.id, text=request_text, parse_mode="Markdown",
            reply_markup=key_keyboard
        )
        with db() as con:
            con.execute("UPDATE requests SET group_message_id=? WHERE id=?", (sent.message_id, req_id))
        await refresh_raffle_progress_messages(context, raffle["id"])
        await refresh_user_progress_panels(context, raffle["id"])
    except Exception:
        log.exception("No se pudo enviar la solicitud al chat privado")
        # Si no se pudo publicar, liberar la reserva para no bloquear números sin aviso.
        with db() as con:
            for n in numbers:
                con.execute(
                    "UPDATE numbers SET status='available',user_id=NULL,name='',username='',updated_at=? "
                    "WHERE raffle_id=? AND number=? AND status='pending' AND user_id=?",
                    (iso_now(), raffle["id"], n, uid)
                )
            con.execute("UPDATE requests SET status='cancelled',processed_at=? WHERE id=?", (iso_now(), req_id))
        await msg.reply_text("No se pudo publicar la solicitud. Los números fueron liberados; inténtalo de nuevo.")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id

    if data.startswith("createlistname:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede crear listas.", show_alert=True)
            return
        flow = context.user_data.get("admin_flow") or {}
        if flow.get("kind") != "create" or flow.get("step") != "name":
            await q.answer("El formulario expiró. Inicia de nuevo desde Crear lista.", show_alert=True)
            return
        try:
            index = int(data.split(":", 1)[1])
            if index not in range(1, 6):
                raise ValueError
        except ValueError:
            await q.answer("Nombre de lista inválido.", show_alert=True)
            return
        name = f"Lista {index}"
        with db() as con:
            duplicate = con.execute("SELECT 1 FROM raffles WHERE lower(trim(name))=lower(trim(?))", (name,)).fetchone()
        if duplicate:
            await q.answer(f"{name} ya existe. Selecciona otro nombre.", show_alert=True)
            await q.message.reply_text("Ese nombre ya está utilizado. Selecciona otro nombre:", reply_markup=raffle_name_keyboard())
            return
        flow["name"] = name
        flow["total"] = 100
        flow["group"] = DEFAULT_GROUP_ID
        flow["step"] = "price"
        await q.answer(f"{name} seleccionado")
        await q.message.reply_text(f"✅ Nombre seleccionado: *{name}*", parse_mode="Markdown")
        await q.message.reply_text(admin_flow_prompt("price"), parse_mode="Markdown", reply_markup=raffle_price_keyboard())
        return

    if data.startswith("createprice:") or data.startswith("createpercent:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede crear listas.", show_alert=True)
            return
        flow = context.user_data.get("admin_flow") or {}
        if flow.get("kind") != "create":
            await q.answer("El formulario expiró. Inicia de nuevo desde Crear lista.", show_alert=True)
            return
        try:
            if data.startswith("createprice:"):
                price = Decimal(data.split(":", 1)[1])
                if price not in {Decimal(x) for x in (10,15,20,25,30,35,40,45,50)} or flow.get("step") != "price":
                    raise ValueError()
                flow["price"] = float(price)
                flow["step"] = "percent"
                await q.answer("Precio seleccionado")
                await q.message.reply_text(f"✅ Valor seleccionado para cada número: *R$ {price}*", parse_mode="Markdown")
                await q.message.reply_text(admin_flow_prompt("percent"), parse_mode="Markdown", reply_markup=raffle_percent_keyboard())
                return
            percent = Decimal(data.split(":", 1)[1])
            if percent not in {Decimal(60), Decimal(65), Decimal(70), Decimal(75), Decimal(80)} or flow.get("step") != "percent":
                raise ValueError()
            group = DEFAULT_GROUP_ID
            if group == 0:
                await q.answer("Falta configurar GROUP_CHAT_ID.", show_alert=True)
                return
            amount = (Decimal(str(flow["price"])) * Decimal("100") * percent / Decimal("100")).quantize(Decimal("0.01"))
            amount_fmt = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            prize_value = f"R$ {amount_fmt} ({percent}% do total)"
            with db() as con:
                if con.execute("SELECT 1 FROM raffles WHERE lower(trim(name))=lower(trim(?))", (flow["name"],)).fetchone():
                    await q.answer("Ese nombre ya fue utilizado. Inicia otra lista con un nombre diferente.", show_alert=True)
                    context.user_data.pop("admin_flow", None)
                    return
                cur = con.execute(
                    """INSERT INTO raffles(name,group_id,total_numbers,price,prize,prize_value,status,created_at)
                       VALUES(?,?,100,?,'Premio entregado mediante transferencia PIX',?,'active',?)""",
                    (flow["name"], group, flow["price"], prize_value, iso_now())
                )
                rid = cur.lastrowid
                con.executemany(
                    "INSERT INTO numbers(raffle_id,number,status,updated_at) VALUES(?,?,?,?)",
                    [(rid, n, "available", iso_now()) for n in range(1, 101)]
                )
            audit("new_raffle", f"Rifa #{rid}: {flow['name']}", uid)
            context.user_data.pop("admin_flow", None)
            await q.answer("Porcentaje seleccionado")
            await q.message.reply_text(f"✅ Porcentaje seleccionado para el premio: *{percent}%*", parse_mode="Markdown")
            await q.message.reply_text(
                f"🎉 *Lista creada correctamente*\n\nID: *#{rid}*\nNombre: *{flow['name']}*\n"
                f"Números: *100*\nValor por número: *R$ {flow['price']:.2f}*\n"
                f"Porcentaje del premio: *{percent}%*\nPremio calculado: *{prize_value}*\n"
                "Premio entregado mediante transferencia PIX.",
                parse_mode="Markdown", reply_markup=admin_keyboard()
            )
            return
        except (ValueError, InvalidOperation, sqlite3.IntegrityError):
            await q.answer("Opción no válida o nombre repetido. Vuelve a iniciar la creación.", show_alert=True)
            return

    if data == "channel:check":
        allowed, reason = await channel_access_status(context.bot, uid)
        if allowed:
            before_count = eligible_user_count()
            q_user = q.from_user
            with db() as con:
                known = con.execute("SELECT channel_member FROM bot_users WHERE user_id=?", (uid,)).fetchone()
                con.execute(
                    "INSERT INTO bot_users(user_id,first_name,username,joined_at,channel_member) VALUES(?,?,?,?,1) "
                    "ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name,username=excluded.username,channel_member=1",
                    (uid, q_user.full_name or q_user.first_name or "Participante", q_user.username or "", iso_now())
                )
            after_count = eligible_user_count()
            if not known or not bool(known["channel_member"]):
                await notify_member_count(context, after_count)
            else:
                await update_user_counters(context, after_count)
            await q.answer("¡Acceso confirmado!")
            await q.message.reply_text("✅ Ya tienes acceso. Usa el menú para ver las listas.", reply_markup=user_keyboard())
        elif reason == "config_missing":
            await q.answer("El canal aún no está configurado. Avisa al administrador.", show_alert=True)
        elif str(reason).startswith("api_error:"):
            await q.answer("El bot no pudo consultar el canal. Avisa al administrador para revisar permisos e ID del canal.", show_alert=True)
        else:
            await q.answer("Telegram aún devuelve tu estado como no miembro. Confirma que te uniste al canal correcto y vuelve a verificar.", show_alert=True)
        return

    if data.startswith("keyrequest:"):
        req_id = data.split(":", 1)[1]
        with db() as con:
            req = con.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
        if not req or uid != req["user_id"]:
            await q.answer("Esta solicitud no te pertenece.", show_alert=True)
            return
        if req["status"] not in {"awaiting_button", "key_requested"}:
            await q.answer("La solicitud ya no está disponible para solicitar la llave.", show_alert=True)
            return
        if req["status"] == "key_requested":
            await q.answer("La solicitud de llave ya fue enviada al administrador.", show_alert=True)
            return
        raffle = get_raffle(req["raffle_id"])
        nums = ", ".join(f"{n:02d}" for n in json.loads(req["numbers_json"]))
        admin_text = (
            "🔑 *SOLICITUD DE LLAVE*\n\n"
            f"Solicitud: `{req_id}`\nLista: *{raffle['name'] if raffle else req['raffle_id']}*\n"
            f"Participante: {req['name']} (@{req['username'] or 'sin username'})\n"
            f"ID Telegram: `{req['user_id']}`\nNúmeros: *{nums}*\n"
            "Pulsa «Enviar llave PIX» y selecciona cuál deseas enviar."
        )
        key_admin_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✉️ Enviar llave PIX", callback_data=f"keyreply:{req['user_id']}:{req_id}")
        ]])
        try:
            await context.bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown", reply_markup=key_admin_keyboard)
            with db() as con:
                con.execute(
                    "UPDATE requests SET status='key_requested' WHERE id=? AND status='awaiting_button'",
                    (req_id,)
                )
            await q.answer("Solicitud de llave PIX enviada.")
        except Exception:
            log.exception("No se pudo notificar solicitud de llave")
            await q.answer("No se pudo enviar la solicitud. Inténtalo de nuevo.", show_alert=True)
        return

    if data == "download_pending":
        await q.answer("El enlace de descarga definitivo está pendiente de que lo proporcione el administrador.", show_alert=True)
        return

    if data.startswith("supportreply:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede responder al soporte.", show_alert=True)
            return
        try:
            target_id = int(data.split(":", 1)[1])
        except (TypeError, ValueError):
            await q.answer("Identificador de participante inválido.", show_alert=True)
            return
        context.user_data["awaiting_support_reply"] = target_id
        await q.answer("Puedes escribir la respuesta.")
        await q.message.reply_text(
            f"✍️ Escribe la respuesta para el participante {target_id}. Se enviará directamente a su chat privado."
        )
        return

    if data.startswith("keyreply:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede responder a esta solicitud.", show_alert=True)
            return
        _, user_id, req_id = data.split(":", 2)
        user_id = int(user_id)
        with db() as con:
            req = con.execute("SELECT * FROM requests WHERE id=? AND user_id=?", (req_id, user_id)).fetchone()
        if not req or req["status"] not in {"key_requested", "awaiting_button"}:
            await q.answer("La solicitud ya no está disponible.", show_alert=True)
            return
        if not PIX_KEYS:
            await q.answer("Aún no hay llaves PIX configuradas.", show_alert=True)
            await q.message.reply_text("⚙️ No hay llaves PIX configuradas. Cuando me proporciones las llaves, aparecerán aquí para seleccionarlas y enviarlas con un toque.")
            return
        buttons = [[InlineKeyboardButton(f"{label} · {key_text}", callback_data=f"pixkey:{i}:{user_id}:{req_id}")]
                   for i, (label, key_text) in enumerate(PIX_KEYS)]
        await q.answer("Selecciona la llave PIX.")
        await q.message.reply_text("🔑 Selecciona la llave PIX que deseas enviar:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("pixkey:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede enviar llaves PIX.", show_alert=True)
            return
        try:
            _, idx, user_text, req_id = data.split(":", 3)
            user_id = int(user_text)
            label, key_text = PIX_KEYS[int(idx)]
        except (ValueError, IndexError):
            await q.answer("Opción de llave inválida.", show_alert=True)
            return
        with db() as con:
            req = con.execute("SELECT * FROM requests WHERE id=? AND user_id=?", (req_id, user_id)).fetchone()
        if not req or req["status"] not in {"key_requested", "awaiting_button"}:
            await q.answer("La solicitud ya no está disponible.", show_alert=True)
            return
        pix_keyboard_rows = []
        try:
            from telegram import CopyTextButton
            pix_keyboard_rows.append([InlineKeyboardButton(
                text=str(key_text), copy_text=CopyTextButton(text=str(key_text))
            )])
            copy_hint = "Pulsa el botón con la llave para copiarla y pegarla en tu aplicación bancaria."
        except (ImportError, TypeError):
            # Compatibilidad con versiones antiguas de python-telegram-bot.
            copy_hint = "Mantén pulsada la llave para copiarla si tu Telegram no ofrece el botón de copiar."
        pix_keyboard_rows.append([InlineKeyboardButton("💵 Ya realicé el pago", callback_data=f"userpaid:{req_id}")])
        pix_keyboard = InlineKeyboardMarkup(pix_keyboard_rows)
        pix_kind = "teléfono" if "tel" in label.lower() else "correo"
        key_msg = await context.bot.send_message(
            user_id,
            f"🔑 LLAVE PIX · {pix_kind.upper()}\n\n<code>{html.escape(str(key_text))}</code>\n\n{copy_hint}\n\nCuando hayas realizado el pago, pulsa «Ya realicé el pago».",
            parse_mode="HTML", reply_markup=pix_keyboard
        )
        with db() as con:
            con.execute("UPDATE requests SET status='awaiting_user_click',pay_button_at=?,payment_deadline_at=NULL,key_message_id=? WHERE id=? AND user_id=? AND status IN ('key_requested','awaiting_button')", (iso_now(), key_msg.message_id, req_id, user_id))
        await q.answer(f"Llave {label} enviada.")
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text(f"✅ Llave PIX enviada al participante {user_id}.")
        audit("manual_key_sent", f"Solicitud {req_id}; llave {label}", uid)
        return

    if data.startswith("selectraffle:"):
        raffle_id = int(data.split(":", 1)[1])
        raffle = get_raffle(raffle_id)
        if not is_admin(uid) and not await has_channel_access(context.bot, uid):
            await q.answer("Primero debes unirte al canal.", show_alert=True)
            await q.message.reply_text("🔒 Únete al canal y verifica tu acceso:", reply_markup=channel_join_keyboard())
            return
        if not raffle or q.message.chat.type != "private" or raffle["status"] != "active":
            await q.answer("Esta lista ya no está disponible.", show_alert=True)
            return
        context.user_data["selected_raffle_id"] = raffle_id
        await q.answer("Lista seleccionada")
        sent = await q.message.reply_text(
            list_text(raffle_id) + "\n\n✍️ Escribe los números que deseas (por ejemplo: 05, 18, 72).",
            parse_mode="Markdown", reply_markup=user_keyboard()
        )
        with db() as con:
            con.execute("INSERT INTO raffle_progress_messages(raffle_id,user_id,chat_id,message_id) VALUES(?,?,?,?) "
                        "ON CONFLICT(raffle_id,user_id) DO UPDATE SET chat_id=excluded.chat_id,message_id=excluded.message_id",
                        (raffle_id, uid, q.message.chat_id, sent.message_id))
        return

    if data.startswith(("available:", "progress:")):
        raffle_id = int(data.split(":", 1)[1])
        raffle = get_raffle(raffle_id)
        if not raffle or (not is_admin(uid) and not await has_channel_access(context.bot, uid)):
            await q.answer("Lista no disponible o acceso no autorizado.", show_alert=True)
            return
        with db() as con:
            available = [int(x[0]) for x in con.execute(
                "SELECT number FROM numbers WHERE raffle_id=? AND status='available' ORDER BY number",
                (raffle_id,)
            ).fetchall()]
        await q.answer()
        if data.startswith("progress:"):
            st = stats(raffle_id)
            body = (f"📊 *{raffle['name']}*\n{progress_bar(st['occupied'], st['total'])} "
                    f"*{st['occupied']}/{st['total']}* números vendidos\n"
                    f"🟢 Libres: {st['available']} · 🟡 En proceso: {st['pending']}")
        else:
            if available:
                chunks = [available[i:i+40] for i in range(0, len(available), 40)]
                body = (f"🔢 *NÚMEROS DISPONIBLES · {raffle['name']}*\n\n" +
                        "\n".join(", ".join(f"{n:02d}" for n in chunk) for chunk in chunks))
            else:
                body = f"🔒 No quedan números disponibles en «{raffle['name']}»."
        # En esta versión los detalles de la lista se muestran juntos, sin botones anidados.
        await q.message.reply_text(
            list_text(raffle_id), parse_mode="Markdown", reply_markup=user_keyboard()
        )
        return

    if data.startswith("userstats:"):
        if q.message.chat.type != "private" or is_admin(uid):
            await q.answer("Esta opción es para participantes en privado.", show_alert=True)
            return
        if not await has_channel_access(context.bot, uid):
            await q.answer("Primero debes unirte al canal.", show_alert=True)
            await q.message.reply_text("🔒 Únete al canal y verifica tu acceso:", reply_markup=channel_join_keyboard())
            return
        action = data.split(":", 1)[1]
        if action == "back":
            await q.answer()
            await q.message.reply_text("Menú principal", reply_markup=user_keyboard())
            return
        with db() as con:
            if action == "plays":
                rows = con.execute(
                    "SELECT p.*,r.name AS raffle_name FROM purchases p JOIN raffles r ON r.id=p.raffle_id WHERE p.user_id=? ORDER BY p.id DESC LIMIT 30",
                    (uid,)
                ).fetchall()
                pending = con.execute(
                    "SELECT rq.*,r.name AS raffle_name FROM requests rq JOIN raffles r ON r.id=rq.raffle_id WHERE rq.user_id=? AND rq.status IN ('awaiting_button','key_requested','awaiting_user_click','awaiting_payment_amount','payment_reported','awaiting_payment_receipt','claim_pending','expired') ORDER BY rq.created_at DESC LIMIT 20",
                    (uid,)
                ).fetchall()
            elif action == "prizes":
                rows = con.execute(
                    "SELECT w.*,r.name AS raffle_name,r.prize,r.prize_value FROM winners w JOIN raffles r ON r.id=w.raffle_id WHERE w.user_id=? ORDER BY w.id DESC LIMIT 30",
                    (uid,)
                ).fetchall()
            elif action == "payments":
                rows = con.execute(
                    "SELECT p.*,r.name AS raffle_name FROM purchases p JOIN raffles r ON r.id=p.raffle_id WHERE p.user_id=? ORDER BY p.id DESC LIMIT 40",
                    (uid,)
                ).fetchall()
                pending = con.execute(
                    "SELECT rq.*,r.name AS raffle_name FROM requests rq JOIN raffles r ON r.id=rq.raffle_id WHERE rq.user_id=? AND rq.status IN ('awaiting_button','key_requested','awaiting_user_click','awaiting_payment_amount','payment_reported','awaiting_payment_receipt','claim_pending','expired') ORDER BY rq.created_at DESC LIMIT 20",
                    (uid,)
                ).fetchall()
        if action == "history":
            await q.answer()
            await send_public_results(q.message)
            return
        if action == "plays":
            blocks = [f"🎟️ {r['raffle_name']} · Números: {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))} · {float(r['amount']):.2f} {CURRENCY} · {r['status']}" for r in rows]
            blocks += [f"⏳ {r['raffle_name']} · Números: {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))} · {float(r['total_amount'] or 0):.2f} {CURRENCY} · pendiente" for r in pending]
            body = "\n\n".join(blocks) if blocks else "Todavía no tienes jugadas registradas."
            title = "🎟️ *MIS JUGADAS*"
        elif action == "prizes":
            blocks = [f"🏆 {r['raffle_name']} · Número: {int(r['number']):02d}\n💰 Monto ganado: {format_brl(raffle_prize_amount(r))} · {'Recibido' if r['prize_received'] else 'Pendiente de confirmar'}" for r in rows]
            body = "\n\n".join(blocks) if blocks else "Todavía no tienes premios registrados."
            title = "🏆 *MIS PREMIOS*"
        elif action == "payments":
            blocks = [f"💳 {r['raffle_name']} · {float(r['amount']):.2f} {CURRENCY} · {r['status']} · {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))}" for r in rows]
            blocks += [f"⏳ {r['raffle_name']} · {float(r['total_amount'] or 0):.2f} {CURRENCY} · {r['status']} · {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))}" for r in pending]
            body = "\n\n".join(blocks) if blocks else "Todavía no hay pagos ni solicitudes registrados."
            title = "💳 *HISTORIAL DE PAGOS*"
        else:
            blocks = [f"🏆 {r['raffle_name']} · Número ganador: {int(r['number']):02d} · Premio: {r['prize'] or 'Premio entregado mediante transferencia PIX'} · {'Comprobante publicado' if r['prize_received'] else 'Entrega pendiente'}" for r in rows]
            blocks += [f"🎯 Florida Pick 3 · {r['draw_date']} · {'Mediodía' if r['draw_type']=='M' else 'Noche'}: {r['result']}" for r in results]
            body = "\n\n".join(blocks) if blocks else "Todavía no hay resultados publicados."
            title = "📚 *RESULTADOS Y GANADORES*"
        await q.answer()
        await q.message.reply_text(title + "\n\n" + body, parse_mode="Markdown", reply_markup=user_keyboard())
        return

    if data.startswith("userpaid:"):
        req_id = data.split(":", 1)[1]
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            req = con.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
            if not req:
                await q.answer("Solicitud no encontrada.", show_alert=True)
                return
            if uid != req["user_id"]:
                await q.answer("Solo quien hizo esta solicitud puede pulsar el botón.", show_alert=True)
                return
            if req["status"] != "awaiting_user_click":
                await q.answer("Este botón ya no está activo.", show_alert=True)
                return
            con.execute("UPDATE requests SET status='awaiting_payment_amount',reported_at=? WHERE id=? AND status='awaiting_user_click'", (iso_now(), req_id))
        await q.answer("Indica el monto transferido.", show_alert=True)
        await q.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=uid, text="💵 MONTO TRANSFERIDO OBLIGATORIO\n\nEscribe el monto exacto que enviaste (por ejemplo: 15,00). Después te pediré la captura.")
        return

    if data.startswith("claimpay:"):
        req_id = data.split(":", 1)[1]
        async with db_lock:
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                req = con.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
                if not req:
                    await q.answer("Solicitud no encontrada.", show_alert=True)
                    return
                if uid != req["user_id"]:
                    await q.answer("Solo quien hizo la solicitud puede reclamar el pago.", show_alert=True)
                    return
                if req["status"] != "expired":
                    await q.answer("Este reclamo ya fue enviado o la solicitud no venció.", show_alert=True)
                    return
                con.execute(
                    "UPDATE requests SET status='claim_pending',reported_at=? WHERE id=? AND status='expired'",
                    (iso_now(), req_id)
                )
        raffle = get_raffle(req["raffle_id"])
        nums = ", ".join(f"{n:02d}" for n in json.loads(req["numbers_json"]))
        await q.answer("Envía la captura de la transferencia aquí, en privado.")
        await q.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            req["origin_chat"],
            "📸 Para reclamar un pago, envíame ahora una captura de la transferencia en este chat privado. "
            "No la publiques en el canal. Tapa contraseñas, códigos, saldo y datos bancarios sensibles. "
            "Cuando la reciba, se la enviaré al administrador para que revise el caso.",
            parse_mode="Markdown"
        )
        audit("payment_claimed_waiting_receipt", f"Solicitud {req_id}; usuario {uid}")
        return

    if data.startswith("prizerequest:"):
        try:
            winner_id = int(data.split(":", 1)[1])
        except ValueError:
            await q.answer("Solicitud inválida.", show_alert=True)
            return
        if q.message.chat.type != "private":
            await q.answer("Solicita el premio desde el chat privado del bot.", show_alert=True)
            return
        with db() as con:
            winner = con.execute("SELECT * FROM winners WHERE id=?", (winner_id,)).fetchone()
            if not winner or uid != winner["user_id"]:
                await q.answer("Esta solicitud no te pertenece.", show_alert=True)
                return
            if winner["prize_received"] or winner["prize_status"] != "announced":
                await q.answer("La solicitud de este premio ya está en proceso o fue completada.", show_alert=True)
                return
            if not verify_winner_ownership(con, winner):
                await q.answer("No se pudo confirmar que seas el propietario del número ganador.", show_alert=True)
                return
            con.execute("UPDATE winners SET prize_status='requested' WHERE id=?", (winner_id,))
        raffle = get_raffle(winner["raffle_id"])
        prize_amount_text = format_brl(raffle_prize_amount(raffle))
        admin_keyboard_prize = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Solicitar llave PIX al usuario", callback_data=f"prizepix:{winner_id}")]])
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=("🎁 <b>SOLICITUD DE ENTREGA DE PREMIO</b>\n\n"
                      f"Lista: <b>{html.escape(raffle['name'] if raffle else str(winner['raffle_id']))}</b>\n"
                      f"Ganador: <b>{html.escape(winner['name'])}</b>\n"
                      f"ID Telegram: <code>{winner['user_id']}</code>\n"
                      f"Número ganador: <b>{int(winner['number']):02d}</b>\n"
                      f"💰 <b>Monto del premio: {prize_amount_text}</b>\n\n"
                      "✅ El bot volvió a verificar la tabla de números y encontró una compra aprobada de este número a nombre del usuario solicitante."),
                parse_mode="HTML", reply_markup=admin_keyboard_prize
            )
            await q.answer("Solicitud verificada y enviada al administrador.")
            await q.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(uid, "✅ Tu identidad como ganador y tu número premiado fueron verificados. El administrador recibió tu solicitud.")
            audit("winner_prize_requested", f"Ganador #{winner_id} solicitó premio", uid)
        except Exception:
            log.exception("No se pudo enviar la solicitud de premio al administrador")
            await q.answer("No se pudo enviar la solicitud. Inténtalo de nuevo.", show_alert=True)
        return

    if data.startswith("prizepix:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede solicitar la llave PIX.", show_alert=True)
            return
        winner_id = int(data.split(":", 1)[1])
        with db() as con:
            winner = con.execute("SELECT * FROM winners WHERE id=?", (winner_id,)).fetchone()
            if not winner or winner["prize_status"] != "requested" or winner["prize_received"]:
                await q.answer("La solicitud ya no está disponible.", show_alert=True)
                return
            if not verify_winner_ownership(con, winner):
                await q.answer("La verificación del ganador ya no coincide. No se envió la solicitud.", show_alert=True)
                return
            con.execute("UPDATE winners SET prize_status='pix_requested' WHERE id=?", (winner_id,))
        raffle_for_pix = get_raffle(winner["raffle_id"])
        prize_amount_text = format_brl(raffle_prize_amount(raffle_for_pix))
        await context.bot.send_message(
            winner["user_id"],
            f"🔑 <b>NECESITAMOS TU LLAVE PIX</b>\n\nPara transferirte el premio de la <b>{html.escape(raffle_for_pix['name'] if raffle_for_pix else str(winner['raffle_id']))}</b>, por un monto de <b>{prize_amount_text}</b>, envía tu llave PIX pulsando el botón de abajo.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Responder: Enviar llave PIX", callback_data=f"winnerpixinput:{winner_id}")]])
        )
        await q.answer("Solicitud de llave PIX enviada al ganador.")
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(f"✅ Se solicitó la llave PIX al ganador {winner['name']}.")
        audit("winner_pix_requested", f"Ganador #{winner_id}", uid)
        return

    if data.startswith("winnerpixinput:"):
        winner_id = int(data.split(":", 1)[1])
        with db() as con:
            winner = con.execute("SELECT * FROM winners WHERE id=?", (winner_id,)).fetchone()
        if not winner or uid != winner["user_id"] or winner["prize_status"] != "pix_requested":
            await q.answer("Esta solicitud de llave PIX no te pertenece o ya no está activa.", show_alert=True)
            return
        with db() as con:
            con.execute("UPDATE winners SET prize_status='pix_input_pending' WHERE id=? AND user_id=? AND prize_status='pix_requested'", (winner_id, uid))
        await q.answer("Puedes escribir tu llave PIX.")
        await q.message.reply_text("✍️ Escribe ahora tu llave PIX en un mensaje. Comprueba que esté correcta antes de enviarla.")
        return

    if data.startswith("prizepaid:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede confirmar la transferencia.", show_alert=True)
            return
        winner_id = int(data.split(":", 1)[1])
        with db() as con:
            winner = con.execute("SELECT * FROM winners WHERE id=?", (winner_id,)).fetchone()
            if not winner or winner["prize_status"] != "pix_received" or not winner["pix_key"] or winner["prize_received"]:
                await q.answer("La operación no está lista para registrar la transferencia.", show_alert=True)
                return
            con.execute("UPDATE winners SET prize_status='payout_receipt_pending' WHERE id=?", (winner_id,))
        await q.answer("Envía la captura de la transferencia.")
        await q.message.reply_text(f"📸 Adjunta ahora la captura de la transferencia realizada al ganador {winner['name']} (lista #{winner['raffle_id']}).")
        return

    if data.startswith("prizereceived:"):
        winner_id = int(data.split(":", 1)[1])
        with db() as con:
            winner = con.execute("SELECT * FROM winners WHERE id=?", (winner_id,)).fetchone()
            if not winner or uid != winner["user_id"]:
                await q.answer("Solo el ganador registrado puede confirmar la recepción.", show_alert=True)
                return
            if winner["prize_status"] != "awaiting_user_confirmation" or winner["prize_received"]:
                await q.answer("No hay una transferencia pendiente de confirmar.", show_alert=True)
                return
            con.execute("UPDATE winners SET receipt_pending=1,prize_status='user_confirmed' WHERE id=?", (winner_id,))
        raffle = get_raffle(winner["raffle_id"])
        await context.bot.send_message(
            ADMIN_ID,
            f"✅ <b>EL GANADOR CONFIRMA QUE RECIBIÓ EL PREMIO</b>\n\nLista: <b>{html.escape(raffle['name'] if raffle else str(winner['raffle_id']))}</b>\nGanador: <b>{html.escape(winner['name'])}</b>\nNúmero: <b>{int(winner['number']):02d}</b>\nID: <code>{winner['user_id']}</code>\n\nEstá pendiente su captura de confirmación para publicarla en el canal.",
            parse_mode="HTML"
        )
        await q.answer("Gracias por confirmar. Envía la captura para publicarla en el canal.")
        await q.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            uid,
            "📸 Para completar la confirmación, envía una captura donde se vea la recepción del premio. La publicaremos en el canal. Tapa contraseñas, códigos y datos bancarios sensibles."
        )
        audit("winner_confirmed_receipt", f"Ganador #{winner_id} confirmó recepción", uid)
        return

    if data.startswith("prize:"):
        await q.answer("Este botón pertenece al flujo anterior. Usa el botón de solicitud de premio del mensaje privado.", show_alert=True)
        return

    if not is_admin(uid):
        await q.answer("⛔ No autorizado.", show_alert=True)
        return

    if data.startswith("pay:"):
        await q.answer()
        _, action, req_id = data.split(":", 2)
        async with db_lock:
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                req = con.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
                if not req or req["status"] != "payment_reported":
                    stale_text = "El usuario todavía no ha enviado el comprobante, o la solicitud ya fue procesada/venció."
                    if q.message.photo:
                        await q.edit_message_caption(caption=stale_text)
                    else:
                        await q.edit_message_text(stale_text)
                    return
                nums = json.loads(req["numbers_json"])
                if action == "ok" and not req["payment_receipt_file_id"]:
                    await q.message.reply_text("⛔ No se puede aprobar: falta la captura obligatoria del pago.")
                    return
                status = "approved" if action == "ok" else "rejected"
                # Evita aprobar parcialmente números que ya no pertenecen a esta solicitud.
                current_numbers = con.execute(
                    "SELECT number,user_id,status FROM numbers WHERE raffle_id=? AND number IN (" +
                    ",".join("?" for _ in nums) + ")", [req["raffle_id"], *nums]
                ).fetchall()
                if action == "ok" and (len(current_numbers) != len(nums) or any(
                    x["status"] != "pending" or x["user_id"] != req["user_id"] for x in current_numbers
                )):
                    error_text = "⚠️ No se puede aprobar: uno o más números ya no están reservados por esta solicitud. Revisa el historial antes de continuar."
                    if q.message.photo:
                        await q.edit_message_caption(caption=error_text)
                    else:
                        await q.edit_message_text(error_text)
                    return
                for n in nums:
                    if action == "ok":
                        con.execute(
                            "UPDATE numbers SET status='occupied',updated_at=? WHERE raffle_id=? AND number=? AND status='pending' AND user_id=?",
                            (iso_now(), req["raffle_id"], n, req["user_id"])
                        )
                    else:
                        con.execute(
                            "UPDATE numbers SET status='available',user_id=NULL,name='',username='',updated_at=? "
                            "WHERE raffle_id=? AND number=? AND status='pending'",
                            (iso_now(), req["raffle_id"], n)
                        )
                amount = float(req["reported_amount"] or req["total_amount"] or 0) if action == "ok" else 0
                con.execute("UPDATE requests SET status=?,processed_at=? WHERE id=?",
                            (status, iso_now(), req_id))
                con.execute(
                    """INSERT INTO purchases(raffle_id,request_id,user_id,name,username,numbers_json,
                       amount,payment_receipt_file_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (req["raffle_id"], req_id, req["user_id"], req["name"], req["username"],
                     json.dumps(nums), amount, req["payment_receipt_file_id"] or '', status, iso_now())
                )
                if action == "ok":
                    raffle_row = con.execute("SELECT total_numbers,status FROM raffles WHERE id=?", (req["raffle_id"],)).fetchone()
                    occupied_count = con.execute(
                        "SELECT COUNT(*) FROM numbers WHERE raffle_id=? AND status='occupied'",
                        (req["raffle_id"],)
                    ).fetchone()[0]
                    if raffle_row and raffle_row["status"] == "active" and occupied_count >= raffle_row["total_numbers"]:
                        con.execute("UPDATE raffles SET status='sold_out',closed_at=? WHERE id=?",
                                    (iso_now(), req["raffle_id"]))
        raffle = get_raffle(req["raffle_id"])
        numbers_text = ", ".join(f"{n:02d}" for n in nums)
        if action == "ok":
            result_text = (f"✅ PAGO APROBADO · Solicitud {req_id}\n"
                           f"🔢 Números: {numbers_text}\n"
                           f"🎟️ Lista: {raffle['name']}\n"
                           f"💰 Total pagado: {amount:.2f} {CURRENCY}")
            if q.message.photo:
                await q.edit_message_caption(caption=result_text)
            else:
                await q.edit_message_text(result_text)
            for chat_id, message_id in ((req["origin_chat"], req["group_message_id"]), (req["user_id"], req["key_message_id"])):
                if message_id:
                    try:
                        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
                    except Exception:
                        log.info("No se pudo retirar un teclado antiguo de la solicitud %s", req_id)
            approved_amount_text = f"{amount:.2f} {CURRENCY}"
            await context.bot.send_message(
                req["origin_chat"],
                (
                    "🎉✨ <b>¡PAGO APROBADO!</b> ✨🎉\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "✅ Tu pago fue verificado y tu participación está confirmada.\n\n"
                    f"🔢 <b>NÚMEROS JUGADOS</b>\n<code>{html.escape(numbers_text)}</code>\n\n"
                    f"🎟️ <b>LISTA</b>\n{html.escape(raffle['name'])}\n\n"
                    f"💰 <b>TOTAL PAGADO</b>\n<b>{html.escape(approved_amount_text)}</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "🍀 ¡Mucha suerte en el sorteo!"
                ),
                parse_mode="HTML"
            )
            if CHANNEL_CHAT_ID:
                try:
                    public_name = req["username"] and ("@" + req["username"].lstrip("@")) or req["name"]
                    await context.bot.send_message(
                        chat_id=CHANNEL_CHAT_ID,
                        text=(f"🎟️ <b>JUGADA CONFIRMADA</b>\n\n"
                              f"📋 Lista: <b>{html.escape(raffle['name'])}</b>\n"
                              f"👤 Participante: <a href=\"tg://user?id={req['user_id']}\">{html.escape(public_name)}</a>\n"
                              f"🔢 Números confirmados: <b>{numbers_text}</b>\n"
                              f"💵 Importe pagado: <b>{amount:.2f} {CURRENCY}</b>"),
                        parse_mode="HTML"
                    )
                except Exception:
                    log.exception("No se pudo publicar la jugada confirmada en el canal")
            await refresh_raffle_progress_messages(context, req["raffle_id"])
            await refresh_user_progress_panels(context, req["raffle_id"])
            st_after = stats(req["raffle_id"])
            if st_after and st_after["available"] <= max(5, int(st_after["total"] * 0.10)) and st_after["available"] > 0:
                milestone = f"near_completion_{req['raffle_id']}"
                with db() as con:
                    sent_before = con.execute("SELECT 1 FROM audit_log WHERE action=? LIMIT 1", (milestone,)).fetchone()
                if not sent_before:
                    notice = (f"🔥 ¡La lista «{raffle['name']}» está cerca de completarse!\n"
                              f"Quedan solo {st_after['available']} números disponibles.\n"
                              f"Progreso: {progress_bar(st_after['occupied'], st_after['total'])} {st_after['occupied']}/{st_after['total']}")
                    if CHANNEL_CHAT_ID:
                        try:
                            await context.bot.send_message(CHANNEL_CHAT_ID, notice)
                        except Exception:
                            log.exception("No se pudo publicar aviso de lista próxima a completarse")
                    audit(milestone, f"Quedan {st_after['available']} números")
        else:
            result_text = f"❌ Solicitud {req_id} rechazada."
            if q.message.photo:
                await q.edit_message_caption(caption=result_text)
            else:
                await q.edit_message_text(result_text)
            await context.bot.send_message(
                req["origin_chat"],
                f"❌ Tu solicitud para «{raffle['name']}» fue rechazada. Los números {numbers_text} vuelven a estar disponibles.",
                parse_mode="Markdown"
            )
            await refresh_raffle_progress_messages(context, req["raffle_id"])
            await refresh_user_progress_panels(context, req["raffle_id"])
        audit("payment_" + status, f"Solicitud {req_id}; números {numbers_text}", uid)
        return

    if data.startswith("raffleblock:") or data.startswith("raffleunblock:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede realizar esta acción.", show_alert=True)
            return
        block_action = data.startswith("raffleblock:")
        new_status = "blocked" if block_action else "active"
        old_status = "active" if block_action else "blocked"
        selected = data.split(":", 1)[1]
        unlocked_names = []
        with db() as con:
            if selected == "all":
                if not block_action:
                    unlocked_names = [r["name"] for r in con.execute(
                        "SELECT name FROM raffles WHERE status=? ORDER BY id", (old_status,)
                    ).fetchall()]
                cur = con.execute("UPDATE raffles SET status=? WHERE status=?", (new_status, old_status))
                changed = cur.rowcount
                label = "todas las listas"
            else:
                try:
                    raffle_id = int(selected)
                except ValueError:
                    await q.answer("Lista no válida.", show_alert=True)
                    return
                row = con.execute("SELECT name,status FROM raffles WHERE id=?", (raffle_id,)).fetchone()
                if not row or row["status"] != old_status:
                    await q.answer("La lista ya no está disponible para esta acción.", show_alert=True)
                    return
                con.execute("UPDATE raffles SET status=? WHERE id=?", (new_status, raffle_id))
                changed = 1
                label = f"{row['name']} (#{raffle_id})"
                if not block_action:
                    unlocked_names = [row["name"]]
        # El bloqueo general refleja si queda al menos una lista activa.
        with db() as con:
            active_count = int(con.execute("SELECT COUNT(*) FROM raffles WHERE status='active'").fetchone()[0])
        set_playing_enabled(active_count > 0)
        await q.answer("Listo")
        if changed:
            verb_done = "bloqueadas" if block_action else "desbloqueadas"
            await q.message.reply_text(
                f"{'🔒' if block_action else '🔓'} {label.capitalize()} {verb_done} correctamente.",
                reply_markup=admin_keyboard()
            )
            audit("raffles_blocked" if block_action else "raffles_unblocked",
                  f"{label}; cantidad={changed}", uid)
            if not block_action and unlocked_names:
                await broadcast_unlocked_lists(context, unlocked_names)
        else:
            await q.message.reply_text("No había listas que cambiar en esa selección.", reply_markup=admin_keyboard())
        return

    if data.startswith("admin:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede usar este panel en privado.", show_alert=True)
            return
        await q.answer()
        action = data.split(":", 1)[1]
        if action == "count":
            await q.answer(f"Usuarios registrados: {eligible_user_count()}")
            return
        if action == "create":
            context.user_data["admin_flow"] = {"kind": "create", "step": "name"}
            await q.message.reply_text(admin_flow_prompt("name"), parse_mode="Markdown", reply_markup=raffle_name_keyboard())
        elif action == "active":
            with db() as con:
                rows = [dict(r) for r in con.execute("SELECT * FROM raffles WHERE status='active' ORDER BY id").fetchall()]
            if not rows:
                text = "No hay listas activas registradas."
            else:
                blocks = []
                for item in rows:
                    st = stats(item["id"])
                    blocks.append(
                        f"🎟️ *{item['name']}* (#{item['id']})\n"
                        f"Números: {st['total']} · Precio: {st['price']:.2f} {CURRENCY}\n"
                        f"Premio: {item['prize'] or 'Por anunciar'} · Valor: {item.get('prize_value') or 'Por definir'}\n"
                        f"Ocupados: {st['occupied']} · Pendientes: {st['pending']} · Libres: {st['available']}\n"
                        f"Recaudado: {st['revenue']:.2f} {CURRENCY} · Grupo: {item['group_id']}"
                    )
                text = "📋 *LISTAS ACTIVAS*\n\n" + "\n\n".join(blocks)
            await q.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_back_keyboard())
        elif action == "stats":
            with db() as con:
                rows = [dict(r) for r in con.execute("SELECT * FROM raffles ORDER BY id DESC").fetchall()]
            if not rows:
                await q.message.reply_text("No hay listas registradas.", reply_markup=admin_back_keyboard())
            else:
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f"📊 {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"adminstats:{r['id']}")] for r in rows]
                    + [[InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin:back")]]
                )
                await q.message.reply_text("Selecciona cualquier lista, abierta o cerrada, para consultar todas sus estadísticas:", reply_markup=keyboard)
        elif action == "winner":
            with db() as con:
                rows = [dict(r) for r in con.execute("SELECT * FROM raffles WHERE status IN ('active','sold_out') ORDER BY id").fetchall()]
            if not rows:
                await q.message.reply_text("No hay listas disponibles para declarar ganador.", reply_markup=admin_back_keyboard())
            else:
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f"🏆 {r['name']} (#{r['id']})", callback_data=f"winnerchoose:{r['id']}")] for r in rows]
                    + [[InlineKeyboardButton("Cancelar", callback_data="admin:back")]]
                )
                await q.message.reply_text("¿En cuál lista vas a indicar el ganador?", reply_markup=keyboard)
        elif action == "reset":
            with db() as con:
                rows = con.execute("SELECT id,name,status FROM raffles ORDER BY id DESC LIMIT 50").fetchall()
            if not rows:
                await q.message.reply_text("No hay listas registradas.", reply_markup=admin_back_keyboard())
            else:
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f"🔄 {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"resetchoose:{r['id']}")] for r in rows]
                    + [[InlineKeyboardButton("Cancelar", callback_data="admin:back")]]
                )
                await q.message.reply_text("Selecciona por ID la lista que deseas reiniciar. Esto libera sus números y conserva el historial.", reply_markup=keyboard)
        elif action == "delete":
            with db() as con:
                rows = con.execute("SELECT id,name,status FROM raffles ORDER BY id DESC LIMIT 50").fetchall()
            if not rows:
                await q.message.reply_text("No hay listas registradas.", reply_markup=admin_back_keyboard())
            else:
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f"🗑️ {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"deletechoose:{r['id']}")] for r in rows]
                    + [[InlineKeyboardButton("Cancelar", callback_data="admin:back")]]
                )
                await q.message.reply_text("Selecciona la lista que deseas borrar. Las listas completas están protegidas hasta confirmar la entrega del premio.", reply_markup=keyboard)
        elif action == "backup":
            await send_backup(update, context)
        elif action == "restore":
            context.user_data["awaiting_restore"] = True
            await q.message.reply_text("Envía aquí el archivo de respaldo *.json*. El sistema lo validará y te pedirá confirmación.", parse_mode="Markdown", reply_markup=admin_back_keyboard())
        elif action == "history":
            with db() as con:
                rows = con.execute("SELECT id,name,status FROM raffles ORDER BY id DESC").fetchall()
            if not rows:
                await q.message.reply_text("Todavía no hay listas registradas.", reply_markup=admin_back_keyboard())
            else:
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f"📚 {r['name']} (#{r['id']}) · {r['status']}", callback_data=f"adminhistory:{r['id']}")] for r in rows]
                    + [[InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin:back")]]
                )
                await q.message.reply_text("Selecciona la lista cuyo historial de jugadas deseas revisar:", reply_markup=keyboard)
        elif action == "back":
            context.user_data.pop("admin_flow", None)
            await q.message.reply_text("⚙️ *PANEL DE ADMINISTRACIÓN*\nSelecciona una operación:", parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data.startswith("adminhistory:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("No autorizado.", show_alert=True)
            return
        try:
            raffle_id = int(data.split(":", 1)[1])
        except ValueError:
            await q.answer("Lista inválida.", show_alert=True)
            return
        await q.answer()
        await send_history(update, context, raffle_id)
        return

    if data.startswith("historyreceipt:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("No autorizado.", show_alert=True)
            return
        try:
            purchase_id = int(data.split(":", 1)[1])
        except ValueError:
            await q.answer("Registro inválido.", show_alert=True)
            return
        with db() as con:
            purchase = con.execute(
                "SELECT payment_receipt_file_id FROM purchases WHERE id=?", (purchase_id,)
            ).fetchone()
        if not purchase or not purchase["payment_receipt_file_id"]:
            await q.answer("No hay comprobante guardado para este registro.", show_alert=True)
            return
        await q.answer()
        await context.bot.send_photo(
            chat_id=q.message.chat_id,
            photo=purchase["payment_receipt_file_id"],
            caption=f"🧾 Comprobante de pago · Registro #{purchase_id}"
        )
        return

    if data.startswith("historyforward:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("No autorizado.", show_alert=True)
            return
        try:
            purchase_id = int(data.split(":", 1)[1])
        except ValueError:
            await q.answer("Registro inválido.", show_alert=True)
            return
        with db() as con:
            purchase = con.execute("SELECT user_id FROM purchases WHERE id=?", (purchase_id,)).fetchone()
        if not purchase:
            await q.answer("El registro ya no existe.", show_alert=True)
            return
        try:
            await context.bot.copy_message(
                chat_id=int(purchase["user_id"]),
                from_chat_id=q.message.chat_id,
                message_id=q.message.message_id
            )
            await q.answer("Registro reenviado al usuario.")
            await q.message.reply_text(f"✅ Se reenvió el registro #{purchase_id} al participante.")
        except Exception:
            log.exception("No se pudo reenviar el registro %s", purchase_id)
            await q.answer("No se pudo reenviar. El usuario debe haber iniciado el bot.", show_alert=True)
        return

    if data.startswith("adminstats:"):
        if not is_admin(uid):
            await q.answer("No autorizado.", show_alert=True)
            return
        raffle_id = int(data.split(":", 1)[1])
        st = stats(raffle_id)
        raffle = get_raffle(raffle_id)
        if not st or not raffle:
            await q.answer("Lista no encontrada.", show_alert=True)
            return
        await q.answer()
        await q.message.reply_text(
            f"📊 *{st['name']} (#{raffle_id})*\nEstado: {st['status']}\n"
            f"Total de números: {st['total']}\nOcupados: {st['occupied']}\nPendientes: {st['pending']}\n"
            f"Libres: {st['available']}\nPrecio por número: {st['price']:.2f} {CURRENCY}\n"
            f"Recaudado (pagos aprobados): {st['revenue']:.2f} {CURRENCY}\n"
            f"Premio: {raffle['prize'] or 'Por anunciar'}\nValor del premio: {raffle.get('prize_value') or 'Por definir'}",
            parse_mode="Markdown", reply_markup=admin_back_keyboard()
        )
        return

    if data.startswith("winnerchoose:"):
        if not is_admin(uid):
            await q.answer("No autorizado.", show_alert=True)
            return
        raffle_id = int(data.split(":", 1)[1])
        raffle = get_raffle(raffle_id)
        if not raffle or raffle["status"] not in ("active", "sold_out"):
            await q.answer("La lista no está disponible para declarar ganador.", show_alert=True)
            return
        await q.answer()
        context.user_data["admin_flow"] = {"kind": "winner", "step": "winner_number", "raffle_id": raffle_id}
        await q.message.reply_text(f"Lista: *{raffle['name']}* (#{raffle_id}).\nEscribe el número ganador:", parse_mode="Markdown")
        return

    if data.startswith("resetchoose:"):
        if not is_admin(uid):
            await q.answer("No autorizado.", show_alert=True)
            return
        raffle_id = int(data.split(":", 1)[1])
        raffle_to_reset = get_raffle(raffle_id)
        if not raffle_to_reset or not is_admin(uid):
            await q.edit_message_text("Lista no encontrada o sin autorización.")
            return
        with db() as con:
            locked = raffle_locked_until_prize_received(con, raffle_id)
        if locked:
            await q.answer("🔒 Lista completa: no se puede reiniciar hasta publicar el ganador y confirmar la entrega del premio.", show_alert=True)
            return
        await q.answer()
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ Confirmar reinicio", callback_data=f"resetconfirm:{raffle_id}"),
            InlineKeyboardButton("Cancelar", callback_data="admin:back")
        ]])
        await q.edit_message_text(
            f"⚠️ Se liberarán todos los números de «{raffle_to_reset['name']}». "
            "El historial de compras se conservará. ¿Continuar?",
            reply_markup=keyboard
        )
        return

    if data.startswith("resetconfirm:"):
        if not is_admin(uid):
            await q.answer("No autorizado.", show_alert=True)
            return
        raffle_id = int(data.split(":")[1])
        with db() as con:
            if raffle_locked_until_prize_received(con, raffle_id):
                await q.answer("🔒 Lista protegida hasta publicar el ganador y confirmar la entrega del premio.", show_alert=True)
                return
            exists = con.execute("SELECT 1 FROM raffles WHERE id=?", (raffle_id,)).fetchone()
            if not exists:
                await q.answer("Lista no encontrada.", show_alert=True)
                return
            con.execute("UPDATE numbers SET status='available',user_id=NULL,name='',username='',updated_at=? WHERE raffle_id=?",
                        (iso_now(), raffle_id))
            con.execute(
                "UPDATE requests SET status='cancelled',processed_at=? WHERE raffle_id=? "
                "AND status IN ('pending','awaiting_button','awaiting_user_click','payment_reported')",
                (iso_now(), raffle_id)
            )
            con.execute("UPDATE raffles SET status='active',closed_at=NULL WHERE id=?", (raffle_id,))
        await q.answer()
        audit("reset", f"Rifa #{raffle_id}", uid)
        await q.edit_message_text(f"✅ Rifa #{raffle_id} reiniciada. El historial se conserva.")
        return

    if data.startswith("deletechoose:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("No autorizado.", show_alert=True)
            return
        raffle_id = int(data.split(":", 1)[1])
        with db() as con:
            raffle = con.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
            locked = raffle_locked_until_prize_received(con, raffle_id)
        if not raffle:
            await q.answer("Lista no encontrada.", show_alert=True)
            return
        if locked:
            await q.answer("🔒 Lista completa: no se puede borrar hasta publicar el ganador y confirmar la entrega del premio.", show_alert=True)
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ Sí, borrar definitivamente", callback_data=f"deleteconfirm:{raffle_id}"),
            InlineKeyboardButton("Cancelar", callback_data="admin:back")
        ]])
        await q.edit_message_text(
            f"⚠️ ¿Borrar definitivamente «{raffle['name']}» (#{raffle_id})? Se eliminarán sus números y registros asociados. Esta acción no se puede deshacer.",
            reply_markup=keyboard
        )
        return

    if data.startswith("deleteconfirm:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("No autorizado.", show_alert=True)
            return
        raffle_id = int(data.split(":", 1)[1])
        with db() as con:
            raffle = con.execute("SELECT name FROM raffles WHERE id=?", (raffle_id,)).fetchone()
            if not raffle:
                await q.answer("La lista ya no existe.", show_alert=True)
                return
            if raffle_locked_until_prize_received(con, raffle_id):
                await q.answer("🔒 Lista protegida hasta confirmar la entrega del premio.", show_alert=True)
                return
            con.execute("DELETE FROM numbers WHERE raffle_id=?", (raffle_id,))
            con.execute("DELETE FROM requests WHERE raffle_id=?", (raffle_id,))
            con.execute("DELETE FROM purchases WHERE raffle_id=?", (raffle_id,))
            con.execute("DELETE FROM winners WHERE raffle_id=?", (raffle_id,))
            con.execute("DELETE FROM raffles WHERE id=?", (raffle_id,))
        await q.answer()
        audit("delete_raffle", f"Lista #{raffle_id}: {raffle['name']}", uid)
        await q.edit_message_text(f"🗑️ Lista «{raffle['name']}» (#{raffle_id}) borrada definitivamente.")
        return


async def send_backup(update, context):
    if update.effective_chat.type != "private":
        if update.callback_query:
            await update.callback_query.answer("Abre el panel en privado para crear la copia.", show_alert=True)
        else:
            await update.effective_message.reply_text("Abre el panel en privado para crear la copia.")
        return
    await send_excel(update, context)
    await send_json(update, context)


async def send_excel(update, context):
    if update.effective_chat.type != "private":
        if update.callback_query:
            await update.callback_query.answer("Abre el panel en privado para descargar el archivo.", show_alert=True)
        else:
            await update.effective_message.reply_text("Abre el panel en privado para descargar el archivo.")
        return
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        wb = Workbook()
        ws = wb.active
        ws.title = "Rifas"
        ws.append(["ID", "Nombre", "Grupo", "Estado", "Total", "Precio", "Premio", "Valor del premio", "Creada"])
        with db() as con:
            raffles = con.execute("SELECT * FROM raffles ORDER BY id").fetchall()
            for r in raffles:
                ws.append([r["id"], r["name"], r["group_id"], r["status"], r["total_numbers"], r["price"], r["prize"], r["prize_value"], r["created_at"]])
            for title, query in [
                ("Numeros", "SELECT r.name raffle,n.* FROM numbers n JOIN raffles r ON r.id=n.raffle_id ORDER BY r.id,n.number"),
                ("Compras", "SELECT * FROM purchases ORDER BY id"),
                ("Ganadores", "SELECT * FROM winners ORDER BY id"),
                ("Solicitudes", "SELECT * FROM requests ORDER BY created_at"),
                ("Auditoria", "SELECT * FROM audit_log ORDER BY id")
            ]:
                sh = wb.create_sheet(title)
                rows = con.execute(query).fetchall()
                if rows:
                    sh.append(list(rows[0].keys()))
                    for row in rows:
                        sh.append([str(v) if isinstance(v, (dict, list)) else v for v in row])
                else:
                    sh.append(["Sin registros"])
        for sh in wb.worksheets:
            sh.freeze_panes = "A2"
            sh.auto_filter.ref = sh.dimensions
            for cell in sh[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="217346")
            for col in sh.columns:
                letter = col[0].column_letter
                sh.column_dimensions[letter].width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 42)
        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=InputFile(bio, filename=f"respaldo_rifas_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"),
            caption="📊 Exportación Excel de las rifas."
        )
    except Exception as e:
        log.exception("Error exportando Excel")
        target = update.callback_query.message if update.callback_query else update.effective_message
        await target.reply_text(f"No se pudo crear el Excel: {e}")


async def send_json(update, context):
    if update.effective_chat.type != "private":
        if update.callback_query:
            await update.callback_query.answer("Abre el panel en privado para descargar el archivo.", show_alert=True)
        else:
            await update.effective_message.reply_text("Abre el panel en privado para descargar el archivo.")
        return
    with db() as con:
        data = {}
        for table in ["raffles", "numbers", "requests", "purchases", "winners", "lottery_results", "audit_log"]:
            rows = con.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(r) for r in rows]
    payload = {"format": "telegram_raffle_backup_v1", "created_at": iso_now(), "data": data}
    bio = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=InputFile(bio, filename=f"respaldo_rifas_{datetime.now().strftime('%Y%m%d_%H%M')}.json"),
        caption="💾 Copia completa para restauración."
    )


async def send_history(update, context, raffle_id):
    """Muestra cada operación de la lista por separado, con acceso al usuario y reenvío."""
    with db() as con:
        raffle = con.execute("SELECT id,name FROM raffles WHERE id=?", (raffle_id,)).fetchone()
        rows = con.execute(
            """SELECT p.*, COALESCE(NULLIF(p.username,''),bu.username,'') AS current_username,
                      r.name AS raffle_name, COALESCE(rq.created_at,p.created_at) AS play_created_at,
                      CASE WHEN p.status='rejected' THEN COALESCE(rq.total_amount,p.amount) ELSE p.amount END AS history_amount
               FROM purchases p
               JOIN raffles r ON r.id=p.raffle_id
               LEFT JOIN bot_users bu ON bu.user_id=p.user_id
               LEFT JOIN requests rq ON rq.id=p.request_id
               WHERE p.raffle_id=?
               ORDER BY p.id DESC""",
            (raffle_id,)
        ).fetchall()
    target = update.callback_query.message if update.callback_query else update.effective_message
    if not raffle:
        await target.reply_text("No se encontró esa lista.", reply_markup=admin_keyboard())
        return
    await target.reply_text(
        f"📚 HISTORIAL COMPLETO · {html.escape(raffle['name'])} (#{raffle_id})\n"
        f"Registros encontrados: {len(rows)}",
        parse_mode="HTML"
    )
    if not rows:
        await target.reply_text("Esta lista todavía no tiene jugadas aprobadas ni rechazadas.", reply_markup=admin_keyboard())
        return
    for row in rows:
        try:
            numbers = ", ".join(f"{int(n):02d}" for n in json.loads(row["numbers_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            numbers = str(row["numbers_json"])
        username = (row["current_username"] or row["username"] or "").lstrip("@")
        display = f"@{username}" if username else (row["name"] or "Usuario")
        status = str(row["status"]).upper()
        timestamp = str(row["play_created_at"] or "Fecha no registrada").replace("T", " ")[:19]
        receipt = "Adjunto registrado" if row["payment_receipt_file_id"] else "Sin captura registrada"
        body = (
            "🧾 <b>REGISTRO DE JUGADA</b>\n\n"
            f"📅 <b>Fecha y hora:</b> {html.escape(timestamp)}\n"
            f"👤 <b>Nombre Usuario:</b> <a href=\"tg://user?id={int(row['user_id'])}\">{html.escape(display)}</a>\n"
            f"🎟️ <b>Nombre de la lista:</b> {html.escape(row['raffle_name'])}\n"
            f"🔢 <b>Números jugados:</b> {html.escape(numbers)}\n"
            f"💰 <b>Importe de la jugada:</b> {format_brl(row['history_amount'])}\n"
            f"📌 <b>Status:</b> {'APROBADA' if row['status']=='approved' else 'RECHAZADA' if row['status']=='rejected' else html.escape(status)}\n"
            f"🧾 <b>Comprobante:</b> {receipt}\n"
            f"🆔 <b>Registro:</b> #{int(row['id'])} · Solicitud {html.escape(str(row['request_id']))}"
        )
        buttons = [
            InlineKeyboardButton("↗️ Reenviar este registro al usuario", callback_data=f"historyforward:{int(row['id'])}")
        ]
        if row["payment_receipt_file_id"]:
            buttons.append(InlineKeyboardButton("🧾 Ver comprobante", callback_data=f"historyreceipt:{int(row['id'])}"))
        keyboard = InlineKeyboardMarkup([buttons])
        await target.reply_text(body, parse_mode="HTML", reply_markup=keyboard)
        await asyncio.sleep(0.03)
    await target.reply_text("Fin del historial de esta lista.", reply_markup=admin_keyboard())


async def prize_receipt_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives the winner's transfer screenshot privately and publishes proof to the transparency channel."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.photo or message.chat.type != "private":
        return
    # Captura de la transferencia enviada por el administrador al ganador.
    if is_admin(user.id):
        with db() as con:
            payout_winner = con.execute(
                "SELECT * FROM winners WHERE prize_status='payout_receipt_pending' AND prize_received=0 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if payout_winner:
            photo_file_id = message.photo[-1].file_id
            raffle = get_raffle(payout_winner["raffle_id"])
            try:
                sent = await context.bot.send_photo(
                    chat_id=payout_winner["user_id"], photo=photo_file_id,
                    caption=(f"💸 <b>TRANSFERENCIA DEL PREMIO REALIZADA</b>\n\n"
                             f"Lista: <b>{html.escape(raffle['name'] if raffle else str(payout_winner['raffle_id']))}</b>\n"
                             f"Número ganador: <b>{int(payout_winner['number']):02d}</b>\n\n"
                             "El administrador ha enviado el comprobante de la transferencia. Cuando compruebes que recibiste el premio, pulsa el botón de abajo."),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Ya Recibí el Premio", callback_data=f"prizereceived:{payout_winner['id']}")]])
                )
                with db() as con:
                    con.execute("UPDATE winners SET payout_receipt_file_id=?,prize_status='awaiting_user_confirmation' WHERE id=? AND prize_status='payout_receipt_pending'",
                                (photo_file_id, payout_winner["id"]))
                await message.reply_text(f"✅ Comprobante enviado al ganador {payout_winner['name']}. Esperando su confirmación.")
                audit("winner_payout_receipt_sent", f"Ganador #{payout_winner['id']}; mensaje {sent.message_id}", user.id)
            except Exception:
                log.exception("No se pudo enviar el comprobante de transferencia al ganador")
                await message.reply_text("No pude enviar la captura al ganador. La operación sigue pendiente; vuelve a enviar la foto.")
            return
    # Comprobante obligatorio después de pulsar «Ya realicé el pago».
    with db() as con:
        payment_req = con.execute(
            "SELECT * FROM requests WHERE user_id=? AND status='awaiting_payment_receipt' "
            "AND COALESCE(payment_receipt_file_id,'')='' ORDER BY created_at DESC LIMIT 1", (user.id,)
        ).fetchone()
    if payment_req:
        file_id = message.photo[-1].file_id
        raffle = get_raffle(payment_req['raffle_id'])
        numbers = ', '.join(f"{n:02d}" for n in json.loads(payment_req['numbers_json']))
        caption = (
            "💵 COMPROBANTE DE PAGO · REVISIÓN OBLIGATORIA\n"
            f"Solicitud: {payment_req['id']}\nLista: {raffle['name'] if raffle else payment_req['raffle_id']}\n"
            f"Participante: {payment_req['name']} (@{payment_req['username'] or 'sin username'})\n"
            f"ID Telegram: {payment_req['user_id']}\nNúmeros: {numbers}\n"
            f"Importe esperado: {float(payment_req['total_amount'] or 0):.2f} {CURRENCY}\n"
            f"Monto declarado por el usuario: {float(payment_req['reported_amount'] or 0):.2f} {CURRENCY}\n\n"
            "Comprueba en el extracto bancario que la transferencia se haya acreditado y que importe, fecha y referencia coincidan."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟢 Aprobar pago", callback_data=f"pay:ok:{payment_req['id']}"),
            InlineKeyboardButton("🔴 Rechazar", callback_data=f"pay:no:{payment_req['id']}")
        ]])
        try:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=caption, reply_markup=keyboard)
            with db() as con:
                con.execute("UPDATE requests SET payment_receipt_file_id=?,status='payment_reported' WHERE id=? AND status='awaiting_payment_receipt'", (file_id, payment_req['id']))
            await message.reply_text("✅ Captura recibida y enviada al administrador. Tus números quedarán pendientes hasta que verifique el abono en el extracto bancario.")
            audit("payment_receipt_uploaded", f"Solicitud {payment_req['id']}", user.id)
        except Exception:
            log.exception("No se pudo enviar la captura de pago al administrador")
            await message.reply_text("❌ No pude entregar la captura. Inténtalo de nuevo; tu solicitud sigue pendiente.")
        return

    with db() as con:
        claim_req = con.execute(
            "SELECT * FROM requests WHERE user_id=? AND status='claim_pending' "
            "AND COALESCE(claim_receipt_file_id,'')='' ORDER BY created_at DESC LIMIT 1",
            (user.id,)
        ).fetchone()
    if claim_req:
        photo_file_id = message.photo[-1].file_id
        raffle = get_raffle(claim_req["raffle_id"])
        nums = ", ".join(f"{n:02d}" for n in json.loads(claim_req["numbers_json"]))
        claim_caption = (
            "🚨 RECLAMACIÓN DE PAGO · COMPROBANTE RECIBIDO\n"
            f"Solicitud: {claim_req['id']}\nLista: {raffle['name'] if raffle else claim_req['raffle_id']}\n"
            f"Participante: {claim_req['name']} (@{claim_req['username'] or 'sin username'})\n"
            f"ID Telegram: {claim_req['user_id']}\nNúmeros: {nums}\n"
            f"Importe: {float(claim_req['total_amount'] or 0):.2f} {CURRENCY}\n"
            "Revisar comprobante y gestionar devolución si corresponde."
        )
        try:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_file_id, caption=claim_caption)
            with db() as con:
                con.execute("UPDATE requests SET claim_receipt_file_id=? WHERE id=?", (photo_file_id, claim_req["id"]))
            await message.reply_text("✅ Captura recibida y enviada al administrador en privado. Revisará tu reclamación.")
            audit("payment_claim_receipt_uploaded", f"Solicitud {claim_req['id']}", user.id)
        except Exception:
            log.exception("No se pudo enviar comprobante de reclamación al administrador")
            await message.reply_text("No pude entregar la captura al administrador. Inténtalo nuevamente.")
        return

    with db() as con:
        winner = con.execute(
            "SELECT * FROM winners WHERE user_id=? AND receipt_pending=1 AND prize_received=0 ORDER BY id DESC LIMIT 1",
            (user.id,)
        ).fetchone()
    if not winner:
        await message.reply_text("No tienes una confirmación de premio pendiente. Pulsa el botón «Recibí el Premio» cuando lo hayas recibido.")
        return
    if not CHANNEL_CHAT_ID:
        await message.reply_text("El canal de transparencia aún no está configurado. No puedo publicar el comprobante todavía; avisa al administrador.")
        return
    photo_file_id = message.photo[-1].file_id
    raffle = get_raffle(winner["raffle_id"])
    prize_amount_text = format_brl(raffle_prize_amount(raffle))
    try:
        with db() as con:
            con.execute("UPDATE winners SET receipt_file_id=? WHERE id=?", (photo_file_id, winner["id"]))
        # Publicación independiente: el resultado original permanece separado e intacto.
        public_caption = (
            f"✅ <b>PREMIO RECIBIDO</b>\n\n"
            f"🎟️ Lista: <b>{html.escape(raffle['name'] if raffle else 'Rifa')}</b>\n"
            f"👤 Ganador: <a href=\"tg://user?id={winner['user_id']}\">{html.escape(winner['name'])}</a>\n"
            f"🎯 Número ganador: <b>{int(winner['number']):02d}</b>\n"
            f"💰 <b>Total del premio: {prize_amount_text}</b>\n\n"
            "📸 El ganador confirmó la recepción y envió el comprobante."
        )
        await context.bot.send_photo(chat_id=CHANNEL_CHAT_ID, photo=photo_file_id, caption=public_caption, parse_mode="HTML")
        with db() as con:
            con.execute("UPDATE winners SET prize_received=1,receipt_pending=0,prize_status='completed',received_at=? WHERE id=?", (iso_now(), winner["id"]))
        await message.reply_text("✅ Captura recibida y publicada en el canal. Tu premio quedó marcado como recibido.")
        try:
            await context.bot.send_message(ADMIN_ID, f"🏁 Premio de {winner['name']} (lista #{winner['raffle_id']}, número {int(winner['number']):02d}) completado. La captura de recepción fue publicada en el canal.")
        except Exception:
            log.exception("No se pudo enviar al administrador la confirmación final del premio")
        audit("prize_receipt_uploaded", f"Ganador #{winner['id']} comprobante enviado", user.id)
    except Exception:
        log.exception("No se pudo publicar el comprobante de premio")
        await message.reply_text("No pude publicar la captura. Inténtalo nuevamente o contacta al administrador; aún no marqué el premio como recibido.")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not is_admin(update.effective_user.id) or not context.user_data.get("awaiting_restore"):
        return
    doc = update.effective_message.document
    if not doc or not doc.file_name.lower().endswith(".json"):
        await update.effective_message.reply_text("Envía un archivo .json generado por este bot.")
        return
    tg_file = await context.bot.get_file(doc.file_id)
    raw = await tg_file.download_as_bytearray()
    try:
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("format") != "telegram_raffle_backup_v1":
            raise ValueError("Formato de respaldo no reconocido.")
        required = {"raffles", "numbers", "requests", "purchases", "winners", "lottery_results", "audit_log"}
        if not required.issubset(payload.get("data", {})):
            raise ValueError("Faltan tablas obligatorias.")
        context.user_data["restore_payload"] = payload
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ Confirmar restauración", callback_data="restore:confirm"),
            InlineKeyboardButton("Cancelar", callback_data="restore:cancel")
        ]])
        await update.effective_message.reply_text(
            "Archivo válido. La restauración sustituirá los datos actuales. "
            "Se recomienda guardar primero una copia JSON. ¿Continuar?",
            reply_markup=keyboard
        )
    except Exception as e:
        await update.effective_message.reply_text(f"Archivo inválido: {e}")


async def restore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id) or q.message.chat.type != "private":
        await q.answer("No autorizado.", show_alert=True)
        return
    if q.data == "restore:cancel":
        context.user_data.pop("restore_payload", None)
        context.user_data.pop("awaiting_restore", None)
        await q.edit_message_text("Restauración cancelada.")
        return
    payload = context.user_data.get("restore_payload")
    if not payload:
        await q.edit_message_text("No hay archivo validado.")
        return
    # Validar integridad relacional antes de reemplazar la base actual.
    data = payload["data"]
    try:
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            for table in ["winners", "purchases", "requests", "numbers", "lottery_results", "audit_log", "raffles"]:
                con.execute(f"DELETE FROM {table}")
            for table in ["raffles", "numbers", "requests", "purchases", "winners", "lottery_results", "audit_log"]:
                rows = data[table]
                if rows:
                    cols = list(rows[0].keys())
                    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
                    con.executemany(sql, [[r.get(c) for c in cols] for r in rows])
        audit("restore", "Restauración JSON")
        await q.edit_message_text("✅ Base de datos restaurada correctamente.")
    except Exception:
        log.exception("Error restaurando")
        await q.edit_message_text("❌ Falló la restauración. La transacción se revirtió si no llegó a completarse.")
    finally:
        context.user_data.pop("restore_payload", None)
        context.user_data.pop("awaiting_restore", None)


async def expire_reservations(app):
    while True:
        try:
            now = iso_now()
            # El botón «Ya realicé el pago» se entrega junto con la llave PIX
            # cuando el administrador la envía manualmente. El temporizador solo
            # controla el plazo posterior a esa entrega.

            # La llave y el botón de pago no caducan; no se liberan números por
            # demorar más de cinco minutos en informar el pago.
        except Exception:
            log.exception("Error en el temporizador de reservas")
        await asyncio.sleep(10)


async def fetch_pick3_pdf():
    """Obtiene la fuente oficial. Devuelve fecha, M/E, resultado y Fireball."""
    import pypdf
    async with aiohttp.ClientSession() as session:
        async with session.get(FLORIDA_PICK3_PDF, timeout=45) as resp:
            resp.raise_for_status()
            pdf_bytes = await resp.read()
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages[:3])
    # Formato del PDF oficial: MM/DD/YY E|M d - d - d FB d
    matches = re.findall(
        r"(\d{2}/\d{2}/\d{2})\s+([EM])\s+(\d)\s*-\s*(\d)\s*-\s*(\d)\s+FB\s+(\d)",
        text
    )
    if not matches:
        raise RuntimeError("No se pudo interpretar el PDF oficial; no se anunciará ningún resultado.")
    target = [m for m in matches if m[1] == PICK3_DRAW]
    if not target:
        raise RuntimeError("No se encontró el tipo de sorteo configurado.")
    date, draw, a, b, c, fb = target[0]
    return date, draw, f"{a}{b}{c}", fb


async def pick3_monitor(app):
    if not PICK3_AUTO_ANNOUNCE:
        return
    while True:
        try:
            date, draw, result, fireball = await fetch_pick3_pdf()
            with db() as con:
                cur = con.execute(
                    """INSERT OR IGNORE INTO lottery_results
                       (draw_date,draw_type,result,fireball,source,fetched_at)
                       VALUES(?,?,?,?,?,?)""",
                    (date, draw, result, fireball, FLORIDA_PICK3_PDF, iso_now())
                )
                is_new = cur.rowcount == 1
            if is_new:
                # Solo informa el resultado oficial; no convierte automáticamente
                # 000-999 a la lista 1-N porque esa regla debe configurarse explícitamente.
                for r in db_raffles_for_draw():
                    await app.bot.send_message(
                        ADMIN_ID,
                        f"🎲 RESULTADO OFICIAL FLORIDA PICK 3\n"
                        f"Fecha: {date} · Sorteo: {'Mediodía' if draw == 'M' else 'Noche'}\n"
                        f"Número: {result} · Fireball: {fireball}\n\n"
                        "El administrador debe confirmar qué número de la rifa corresponde "
                        "según la regla de participación configurada."
                    )
        except Exception:
            log.exception("No se pudo consultar Florida Pick 3")
        await asyncio.sleep(600)


def db_raffles_for_draw():
    with db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM raffles WHERE status='active' AND draw_source='florida_pick3'"
        ).fetchall()]


async def post_init(app: Application):
    asyncio.create_task(expire_reservations(app))
    asyncio.create_task(pick3_monitor(app))
    try:
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text="⚙️ PANEL DE ADMINISTRACIÓN\nEl panel está listo. Usa estos botones para administrar las rifas:",
            reply_markup=admin_keyboard()
        )
    except Exception:
        log.warning(
            "No se pudo enviar automáticamente el panel al administrador. "
            "Abre el chat privado del bot y pulsa Iniciar para habilitar los mensajes."
        )



async def pick3_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ No autorizado.")
        return
    if len(context.args) != 2 or context.args[1].lower() not in {"on", "off"}:
        await update.effective_message.reply_text("Uso: /pick3 ID_RIFA on|off")
        return
    try:
        raffle_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("ID de rifa inválido.")
        return
    source = "florida_pick3" if context.args[1].lower() == "on" else "manual"
    with db() as con:
        if raffle_locked_until_prize_received(con, raffle_id):
            await update.effective_message.reply_text("🔒 Esta lista está cerrada y protegida. No puedes modificar su configuración hasta que el ganador confirme la recepción del premio.")
            return
        cur = con.execute("UPDATE raffles SET draw_source=? WHERE id=?", (source, raffle_id))
    if not cur.rowcount:
        await update.effective_message.reply_text("No existe esa rifa.")
        return
    audit("pick3_config", f"Rifa #{raffle_id}: {source}", update.effective_user.id)
    await update.effective_message.reply_text(
        f"✅ Monitor PICK 3 {'activado' if source == 'florida_pick3' else 'desactivado'} "
        f"para la rifa #{raffle_id}. Configura PICK3_AUTO_ANNOUNCE=true."
    )


async def welcome_channel_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actualiza miembros conocidos y comunica al admin/usuarios el contador actual."""
    change = update.chat_member
    if not change or not change.chat or change.chat.id != CHANNEL_CHAT_ID:
        return
    member = change.new_chat_member.user
    if member.id == context.bot.id:
        return

    old_status = change.old_chat_member.status
    new_status = change.new_chat_member.status
    is_now_member = (
        new_status in {"member", "administrator", "creator"}
        or (new_status == "restricted" and getattr(change.new_chat_member, "is_member", False))
    )
    was_member = (
        old_status in {"member", "administrator", "creator"}
        or (old_status == "restricted" and getattr(change.old_chat_member, "is_member", False))
    )

    before_count = eligible_user_count()
    with db() as con:
        known = con.execute(
            "SELECT channel_member FROM bot_users WHERE user_id=?", (member.id,)
        ).fetchone()
        if known:
            con.execute(
                "UPDATE bot_users SET channel_member=? WHERE user_id=?",
                (1 if is_now_member else 0, member.id)
            )
    after_count = eligible_user_count()

    if known and is_now_member and not bool(known["channel_member"]):
        await notify_member_count(context, after_count)
    elif not known and is_now_member and not was_member:
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"👤 Nuevo miembro del canal: {member.first_name or 'Usuario'} (ID {member.id}).\n"
                f"Todavía no ha iniciado el bot; no se suma al contador hasta que pulse /start.\n"
                f"👥 Usuarios elegibles actuales: {after_count} · mínimo de referencia: 100."
            )
        except Exception:
            log.exception("No se pudo avisar al administrador del nuevo miembro del canal")
    elif known and was_member and not is_now_member:
        log.info("Usuario %s salió del canal; elegibles actuales: %s", member.id, after_count)

    if is_now_member and not was_member:
        try:
            info_text = build_welcome_message(after_count)
            await context.bot.send_message(
                chat_id=CHANNEL_CHAT_ID, text=info_text, parse_mode="HTML"
            )
        except Exception:
            log.exception("No se pudo publicar bienvenida al nuevo miembro del canal")

async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """No anuncia jugadas en grupos; invita a los nuevos a usar el bot en privado."""
    if not update.effective_message or not update.effective_message.new_chat_members:
        return
    for member in update.effective_message.new_chat_members:
        if member.id != context.bot.id:
            await update.effective_message.reply_text(
                "👋 ¡Bienvenido/a! Para participar y consultar tus jugadas, abre el chat privado del bot y pulsa /start."
            )


async def refresh_raffle_progress_messages(context, raffle_id):
    """Actualiza los mensajes de disponibilidad que cada usuario abrió para esta lista."""
    with db() as con:
        targets = con.execute("SELECT user_id,chat_id,message_id FROM raffle_progress_messages WHERE raffle_id=?", (raffle_id,)).fetchall()
    body = list_text(raffle_id)
    for target in targets:
        try:
            await context.bot.edit_message_text(chat_id=target['chat_id'], message_id=target['message_id'],
                                                text=body, parse_mode='Markdown')
        except Exception:
            log.debug("No se pudo actualizar progreso para usuario %s", target['user_id'], exc_info=True)

def list_text(raffle_id):
    with db() as con:
        r = con.execute("SELECT * FROM raffles WHERE id=?", (raffle_id,)).fetchone()
        rows = con.execute(
            "SELECT * FROM numbers WHERE raffle_id=? ORDER BY number", (raffle_id,)
        ).fetchall()
    if not r:
        return "No existe esa rifa."
    s = stats(raffle_id)
    lines = [
        f"🎟️ *{r['name']}* (Lista #{raffle_id})",
        f"💵 Valor por número: *{float(r['price']):.2f} {CURRENCY}*",
        f"🏆 Premio: *{r['prize'] or 'Premio entregado mediante transferencia PIX'}*",
        f"💎 Valor del premio: *{r['prize_value'] or 'Por definir'}*",
        f"🔢 Cantidad de números: *{r['total_numbers']}*",
        ""
    ]
    for row in rows:
        n = int(row["number"])
        tag = f"{n:02d}" if r["total_numbers"] <= 100 else str(n)
        if row["status"] == "available":
            lines.append(f"🟢 *{tag}*: Disponible")
        elif row["status"] == "pending":
            lines.append(f"🟡 *{tag}*: En verificación")
        else:
            name = row["name"] or "Participante"
            uid = row["user_id"]
            mention = f"[{name}](tg://user?id={uid})" if uid else name
            lines.append(f"🔴 *{tag}*: {mention}")
    lines += [
        "", f"📊 Progreso: `{progress_bar(s['occupied'] + s['pending'], s['total'])}` *{s['occupied'] + s['pending']}/{s['total']}* (ocupados + pendientes)",
        f"🟢 Disponibles: *{s['available']}* | 🟡 Pendientes: *{s['pending']}*",
        f"💰 Recaudado (pagos aprobados): *{s['revenue']:.2f} {CURRENCY}*",
        f"Estado: *{s['status']}*"
    ]
    return "\n".join(lines)

ADMIN_MENU_ACTIONS = {
    "➕ Crear lista": "create",
    "📋 Listas activas": "active",
    "📊 Estadísticas": "stats",
    "🏆 Indicar Número Ganador": "winner",
    "🔄 Resetear lista": "reset",
    "🗑️ Borrar lista": "delete",
    "💾 Crear respaldo": "backup",
    "♻️ Restaurar respaldo": "restore",
    "👥 Historial": "history",
    "👥 Usuarios Registrados": "users",
    "🔒 Bloquear jugadas": "block",
    "🔓 Desbloquear jugadas": "unblock",
    "🚫 Bloquear usuario": "block_user",
    "✅ Desbloquear usuario": "unblock_user",
    "🔄 Actualizar panel": "back",
}

def channel_join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Unirme al canal", url=CHANNEL_JOIN_URL)],
        [InlineKeyboardButton("✅ Ya me uní · Verificar", callback_data="channel:check")]
    ])

def eligible_user_count():
    """Usuarios que iniciaron el bot y cuya membresía actual en el canal fue verificada."""
    with db() as con:
        return int(con.execute(
            "SELECT COUNT(*) FROM bot_users WHERE channel_member=1"
        ).fetchone()[0])

async def start_web_server():
    async def health(_request):
        return web.Response(text="Bot de rifas activo")
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Servidor web activo en puerto %s", PORT)


async def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise RuntimeError("Configura TELEGRAM_TOKEN y ADMIN_TELEGRAM_ID en las variables de entorno.")
    init_db()
    log.info("Base SQLite activa: %s", Path(DB_PATH).expanduser().resolve())
    if not CHANNEL_CHAT_ID:
        log.warning("CHANNEL_CHAT_ID no configurado: publicaciones públicas de jugadas/ganador/comprobante desactivadas.")
    await start_web_server()

    global user_client
    if TELETHON_API_ID and TELETHON_API_HASH and TELETHON_SESSION:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            user_client = TelegramClient(
                StringSession(TELETHON_SESSION),
                int(TELETHON_API_ID), TELETHON_API_HASH
            )
            await user_client.start()
            log.info("Cuenta dedicada conectada.")
        except Exception:
            user_client = None
            log.exception("No se pudo iniciar la cuenta dedicada; los DMs privados quedarán pendientes.")
    else:
        log.warning("Telethon no configurado: no se enviarán mensajes privados desde la cuenta dedicada.")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("listas", lists_cmd))
    app.add_handler(CommandHandler("diagnostico", diagnostic_cmd))
    app.add_handler(CommandHandler("diagnostico_canal", channel_diagnostic_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_error_handler(telegram_error_handler)
    app.add_handler(CommandHandler("nueva", new_raffle_cmd))
    app.add_handler(CommandHandler("ganador", winner_cmd))
    app.add_handler(CommandHandler("pick3", pick3_cmd))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    app.add_handler(ChatMemberHandler(welcome_channel_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(restore_callback, pattern=r"^restore:"))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, prize_receipt_photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    try:
        await app.initialize()
        me = await app.bot.get_me()
        log.info("Conectado a Telegram como @%s (ID %s)", me.username, me.id)
        # Elimina un webhook anterior: getUpdates (polling) no funciona mientras haya uno activo.
        webhook = await app.bot.get_webhook_info()
        if webhook.url:
            log.warning("Había un webhook configurado (%s); se eliminará para activar polling.", webhook.url)
            await app.bot.delete_webhook(drop_pending_updates=False)
        await app.start()
        # post_init no se ejecuta al usar initialize/start manualmente.
        await post_init(app)
        await sync_known_channel_members(app.bot)
        startup_context = type("Context", (), {"bot": app.bot})()
        await update_admin_counter(startup_context)
        await update_user_counters(startup_context)
        if app.updater is None:
            raise RuntimeError("Application no tiene Updater; no se puede iniciar polling.")
        await app.updater.start_polling(
            drop_pending_updates=False,
            bootstrap_retries=5,
            allowed_updates=Update.ALL_TYPES,
        )
        log.info("POLLING ACTIVO: el bot ya está escuchando mensajes y comandos.")
        await asyncio.Event().wait()
    except Exception:
        log.exception("Error fatal al iniciar o mantener el bot de Telegram")
        raise
    finally:
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
        if user_client:
            await user_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
