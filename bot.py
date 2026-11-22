"""
Bot de rifas Telegram - versión ampliada
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
DB_PATH = os.getenv("DB_PATH", "rifas.sqlite3")
PORT = int(os.getenv("PORT", "10000"))
RESERVATION_MINUTES = 5  # Primera ventana: 5 min para realizar el pago
PAY_BUTTON_MINUTES = 5   # Segunda ventana: 5 min para avisar que ya pagó
CURRENCY = "R$"  # Todos los valores de la rifa se expresan en reales brasileños
FLORIDA_PICK3_PDF = "https://files.floridalottery.com/exptkt/p3.pdf"
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
        CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            joined_at TEXT NOT NULL
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
        # Migraciones para publicación en canal y comprobante de recepción del premio.
        winner_columns = {r[1] for r in con.execute("PRAGMA table_info(winners)")}
        for col, declaration in [
            ("channel_message_id", "INTEGER"),
            ("receipt_pending", "INTEGER NOT NULL DEFAULT 0"),
            ("receipt_file_id", "TEXT DEFAULT ''")
        ]:
            if col not in winner_columns:
                con.execute(f"ALTER TABLE winners ADD COLUMN {col} {declaration}")
        # Migraciones compatibles con bases de datos creadas por versiones anteriores.
        raffle_columns = {r[1] for r in con.execute("PRAGMA table_info(raffles)")}
        if "prize_value" not in raffle_columns:
            con.execute("ALTER TABLE raffles ADD COLUMN prize_value TEXT DEFAULT ''")
        request_columns = {r[1] for r in con.execute("PRAGMA table_info(requests)")}
        for col, declaration in [
            ("group_message_id", "INTEGER"),
            ("pay_button_at", "TEXT"),
            ("payment_deadline_at", "TEXT"),
            ("reported_at", "TEXT"),
            ("claim_receipt_file_id", "TEXT DEFAULT ''"),
            ("price_per_number", "REAL DEFAULT 0"),
            ("total_amount", "REAL DEFAULT 0")
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
        # No crear rifas ficticias automáticamente: las listas se crean desde /admin.


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
        f"🏆 Premio: *{r['prize'] or 'Por anunciar'}*",
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
        "", f"📊 Ocupados: *{s['occupied']}* | Pendientes: *{s['pending']}*",
        f"🟢 Disponibles: *{s['available']}*",
        f"💰 Recaudado (pagos aprobados): *{s['revenue']:.2f} {CURRENCY}*",
        f"Estado: *{s['status']}*"
    ]
    return "\n".join(lines)


async def send_private(user_id, text):
    """Envía desde la cuenta dedicada si está conectada; nunca finge ser el admin."""
    if not user_client:
        log.warning("Cuenta dedicada no conectada; no se envió DM a %s", user_id)
        return False
    try:
        await user_client.send_message(int(user_id), text)
        return True
    except Exception:
        log.exception("No se pudo enviar DM desde la cuenta dedicada a %s", user_id)
        return False


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
    "🔄 Actualizar panel": "back",
}

def channel_join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Unirme al canal", url=CHANNEL_JOIN_URL)],
        [InlineKeyboardButton("✅ Ya me uní · Verificar", callback_data="channel:check")]
    ])


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
            f"Bot administrador: {'sí' if bot_admin else 'NO'}\n\n"
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
    """Menú privado de participantes: solo consulta y juego."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🎟️ Ver listas activas")],
         [KeyboardButton("📊 Estadísticas")]],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Elige una opción"
    )


def user_stats_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎟️ Mis jugadas", callback_data="userstats:plays")],
        [InlineKeyboardButton("🏆 Mis premios", callback_data="userstats:prizes")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="userstats:back")]
    ])


def admin_keyboard():
    """Teclado persistente del administrador; permanece visible en el chat privado."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Crear lista"), KeyboardButton("📋 Listas activas")],
            [KeyboardButton("📊 Estadísticas"), KeyboardButton("🏆 Indicar Número Ganador")],
            [KeyboardButton("🔄 Resetear lista"), KeyboardButton("🗑️ Borrar lista")],
            [KeyboardButton("👥 Historial")],
            [KeyboardButton("💾 Crear respaldo"), KeyboardButton("♻️ Restaurar respaldo")],
            [KeyboardButton("🔄 Actualizar panel")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Selecciona una opción de administración"
    )


def admin_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin:back")]])


def admin_flow_prompt(step):
    prompts = {
        "name": "Escribe el *nombre* de la nueva lista:",
        "price": "¿Cuál será el *valor de cada número* en reales (R$)? Ejemplo: 15 o 15,50.",
        "prize": "Describe el *premio* que se entregará:",
        "prize_value": "¿Qué *porcentaje del total recaudado* será el valor del premio? Escribe, por ejemplo, 80 o 80%. La lista tiene 100 números."
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
    summaries = []
    for raffle in raffles:
        st = stats(raffle["id"])
        summaries.append(
            f"🎟️ *{raffle['name']}* (#{raffle['id']})\n"
            f"🔢 Números: {raffle['total_numbers']} · 💵 Por número: {float(raffle['price']):.2f} {CURRENCY}\n"
            f"🏆 Premio: {raffle['prize'] or 'Por anunciar'}\n"
            f"💎 Valor del premio: {raffle.get('prize_value') or 'Por definir'}\n"
            f"🟢 Libres: {st['available']} · 🔴 Ocupados: {st['occupied']} · 🟡 Pendientes: {st['pending']}"
        )
    await update.effective_message.reply_text(
        "🎟️ *LISTAS DISPONIBLES*\n\n" + "\n\n".join(summaries) +
        "\n\n👇 Selecciona la lista en la que deseas participar:",
        parse_mode="Markdown", reply_markup=raffle_selector_keyboard(raffles)
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Para jugar y consultar tus jugadas, abre el chat privado del bot y pulsa /start.")
        return
    user = update.effective_user
    if is_admin(user.id):
        await update.effective_message.reply_text(
            "⚙️ PANEL DE ADMINISTRACIÓN\nSelecciona una opción. Los botones permanecerán visibles aquí:",
            reply_markup=admin_keyboard()
        )
        return
    if not await require_channel(update, context):
        return

    with db() as con:
        existing = con.execute("SELECT user_id FROM bot_users WHERE user_id=?", (user.id,)).fetchone()
        con.execute(
            "INSERT INTO bot_users(user_id,first_name,username,joined_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name,username=excluded.username",
            (user.id, user.full_name or user.first_name or "Participante", user.username or "", iso_now())
        )

    if not existing:
        welcome = (
            f"👋 ¡Bienvenido/a, {user.first_name or 'participante'}!\n\n"
            "🎟️ *¿Cómo funciona la rifa?*\n"
            "• Cada lista tiene 100 números, del 01 al 100.\n"
            "• Elige tus números aquí, en este chat privado.\n"
            "• Cada número solo puede pertenecer a un participante. Si está ocupado, te avisaremos.\n"
            "• Para completar tu jugada, sigue las instrucciones de pago y espera la confirmación del bot.\n"
            "• Hay que esperar a que se completen los 100 números. Solo entonces se determina el ganador con el resultado de *Florida Pick 3* de Florida.\n\n"
            "📌 Consulta las listas activas para ver disponibilidad, precio y premio."
        )
    else:
        welcome = "¡Hola de nuevo! Puedes consultar las listas activas y tus estadísticas desde este menú."
    await update.effective_message.reply_text(welcome, parse_mode="Markdown", reply_markup=user_keyboard())


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
    await update.effective_message.reply_text(
        "⚙️ PANEL DE ADMINISTRACIÓN\nSelecciona una operación. Los botones permanecerán visibles aquí:",
        reply_markup=admin_keyboard()
    )


async def new_raffle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Creación rápida: /nueva Nombre|precio_por_numero|premio|porcentaje"""
    if not is_admin(update.effective_user.id):
        return
    raw = " ".join(context.args)
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) < 4:
        await update.effective_message.reply_text(
            "Uso: /nueva Nombre de lista|15|Descripción del premio|80%\n"
            "La lista se crea automáticamente con 100 números y el grupo configurado."
        )
        return
    try:
        name, price_text, prize, percent_text = parts[:4]
        price = Decimal(price_text.replace(",", "."))
        percent = Decimal(percent_text.replace("%", "").replace(",", "."))
        if not name or price < 0 or not Decimal("0") <= percent <= Decimal("100"):
            raise ValueError()
        amount = (price * Decimal("100") * percent / Decimal("100")).quantize(Decimal("0.01"))
        amount_fmt = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        prize_value = f"R$ {amount_fmt} ({percent}% do total)"
        group = DEFAULT_GROUP_ID
        if group == 0:
            await update.effective_message.reply_text("El ID del grupo configurado (GROUP_CHAT_ID) es 0. Configúralo en el entorno del bot.")
            return
    except (ValueError, InvalidOperation):
        await update.effective_message.reply_text("Datos inválidos. Revisa el precio y el porcentaje (0 a 100).")
        return
    with db() as con:
        cur = con.execute(
            """INSERT INTO raffles(name,group_id,total_numbers,price,prize,prize_value,status,created_at)
               VALUES(?,?,100,?,?,?,'active',?)""",
            (name, group, float(price), prize, prize_value, iso_now())
        )
        rid = cur.lastrowid
        con.executemany(
            "INSERT INTO numbers(raffle_id,number,status,updated_at) VALUES(?,?,?,?)",
            [(rid, n, "available", iso_now()) for n in range(1, 101)]
        )
    audit("new_raffle", f"Rifa #{rid}: {name}", update.effective_user.id)
    await update.effective_message.reply_text(
        f"✅ Lista #{rid} creada: {name} (100 números, del 1 al 100).\n"
        f"Precio por número: R$ {float(price):.2f}\nPremio calculado: {prize_value}"
    )


async def publish_winner(update, context, raffle_id, number):
    raffle = get_raffle(raffle_id)
    if not raffle or not 1 <= number <= raffle["total_numbers"]:
        await update.effective_message.reply_text("Lista o número inválido.")
        return
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
    mention = f"[{row['name']}](tg://user?id={row['user_id']})" if row["user_id"] else row["name"]
    msg = (
        f"🏆 *¡RESULTADO OFICIAL DE LA RIFA!* 🏆\n\n"
        f"🎟️ Lista: *{raffle['name']}*\n🎯 Número ganador: *{number:02d}*\n"
        f"🎉 Ganador: {mention}\n\nCuando recibas tu premio, pulsa el botón *Premio Recibido*."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏆 Premio Recibido", callback_data=f"prize:{winner_id}")]])
    try:
        sent = await context.bot.send_message(chat_id=row["user_id"], text=msg, parse_mode="Markdown", reply_markup=keyboard)
        with db() as con:
            con.execute("UPDATE winners SET group_message_id=? WHERE id=?", (sent.message_id, winner_id))
    except Exception:
        log.exception("No se pudo enviar el resultado al ganador en privado")
        await update.effective_message.reply_text("El ganador quedó registrado, pero no pude enviarle el mensaje privado. El usuario debe iniciar el bot con /start.")
        return
    if CHANNEL_CHAT_ID:
        try:
            channel_msg = await context.bot.send_message(
                chat_id=CHANNEL_CHAT_ID, text=
                f"🏆 <b>RESULTADO DE LA RIFA</b>\n\n"
                f"🎟️ Lista: <b>{html.escape(raffle['name'])}</b>\n"
                f"🎯 Número ganador: <b>{number:02d}</b>\n"
                f"👤 Usuario ganador: <a href=\"tg://user?id={row['user_id']}\">{html.escape(row['name'])}</a>\n\n"
                "Cuando reciba el premio, el ganador debe pulsar «Recibí el Premio» y enviar el comprobante.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏆 Recibí el Premio", callback_data=f"prize:{winner_id}")]])
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
            flow["name"] = value
            flow["total"] = 100
            flow["group"] = DEFAULT_GROUP_ID
            flow["step"] = "price"
        elif step == "price":
            price = float(Decimal(value.replace(",", ".")))
            if price < 0:
                raise ValueError()
            flow["price"] = price
            flow["step"] = "prize"
        elif step == "prize":
            if not value:
                raise ValueError()
            flow["prize"] = value
            flow["step"] = "prize_value"
        elif step == "prize_value":
            if not value:
                raise ValueError()
            percent_text = value.strip().replace("%", "").replace(",", ".")
            percent = Decimal(percent_text)
            if not Decimal("0") <= percent <= Decimal("100"):
                raise ValueError()
            prize_amount = Decimal(str(flow["price"])) * Decimal("100") * percent / Decimal("100")
            prize_amount = prize_amount.quantize(Decimal("0.01"))
            # Formato monetario brasileño: punto de miles y coma decimal.
            amount_fmt = f"{prize_amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            flow["prize_percent"] = percent
            flow["prize_value"] = f"R$ {amount_fmt} ({percent}% do total)"
            group = DEFAULT_GROUP_ID
            if group == 0:
                await msg.reply_text("El ID del grupo configurado (GROUP_CHAT_ID) es 0. Configúralo en el entorno del bot.")
                return True
            with db() as con:
                cur = con.execute(
                    """INSERT INTO raffles(name,group_id,total_numbers,price,prize,prize_value,status,created_at)
                       VALUES(?,?,?,?,?,?,'active',?)""",
                    (flow["name"], group, flow["total"], flow["price"], flow["prize"], flow["prize_value"], iso_now())
                )
                rid = cur.lastrowid
                con.executemany(
                    "INSERT INTO numbers(raffle_id,number,status,updated_at) VALUES(?,?,?,?)",
                    [(rid, n, "available", iso_now()) for n in range(1, flow["total"] + 1)]
                )
            audit("new_raffle", f"Rifa #{rid}: {flow['name']}", update.effective_user.id)
            context.user_data.pop("admin_flow", None)
            await msg.reply_text(
                f"✅ *Lista creada correctamente*\n\nID: *#{rid}*\nNombre: *{flow['name']}*\n"
                f"Números: *{flow['total']}*\nValor por número: *{flow['price']:.2f} {CURRENCY}*\n"
                f"Premio: *{flow['prize']}*\nValor del premio calculado: *{flow['prize_value']}*",
                parse_mode="Markdown", reply_markup=admin_keyboard()
            )
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
    if action == "create":
        context.user_data["admin_flow"] = {"kind": "create", "step": "name"}
        await msg.reply_text(admin_flow_prompt("name"), parse_mode="Markdown", reply_markup=admin_keyboard())
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
                "SELECT * FROM raffles WHERE status='active' ORDER BY id"
            ).fetchall()]
        if not rows:
            await msg.reply_text("No hay listas activas.", reply_markup=admin_keyboard())
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"📊 {r['name']} (#{r['id']})", callback_data=f"adminstats:{r['id']}")] for r in rows]
            )
            await msg.reply_text("Selecciona la lista que quieres revisar:", reply_markup=keyboard)
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
        await send_history(update, context)
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

    # Las participaciones se gestionan exclusivamente en el chat privado del bot.
    if update.effective_chat.type != "private":
        if not is_admin(update.effective_user.id):
            await msg.reply_text("🔒 Para jugar, abre el chat privado del bot y pulsa /start. Las jugadas no se realizan en el grupo.")
        return

    # La respuesta del administrador a una solicitud de llave se envía directamente al usuario.
    pending_key = context.user_data.get("awaiting_key_delivery")
    if is_admin(update.effective_user.id) and pending_key:
        key_text = text.strip()
        if not key_text:
            await msg.reply_text("Escribe la llave que deseas enviar.")
            return
        try:
            req_id = pending_key["request_id"]
            with db() as con:
                req = con.execute(
                    "SELECT * FROM requests WHERE id=? AND user_id=?",
                    (req_id, pending_key["user_id"])
                ).fetchone()
            if not req or req["status"] not in {"key_requested", "awaiting_button"}:
                await msg.reply_text("❌ La solicitud ya no está disponible para entregar la llave.")
                context.user_data.pop("awaiting_key_delivery", None)
                return

            payment_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("💵 Ya realicé el pago", callback_data=f"userpaid:{req_id}")
            ]])
            await context.bot.send_message(
                chat_id=pending_key["user_id"],
                text=("🔑 LLAVE PIX\n\n" + key_text +
                      "\n\nCuando hayas realizado el pago, pulsa el botón «Ya realicé el pago»."),
                reply_markup=payment_keyboard
            )
            deadline = (now_utc() + timedelta(minutes=PAY_BUTTON_MINUTES)).isoformat()
            with db() as con:
                con.execute(
                    """UPDATE requests
                       SET status='awaiting_user_click',pay_button_at=?,payment_deadline_at=?
                       WHERE id=? AND user_id=? AND status IN ('key_requested','awaiting_button')""",
                    (iso_now(), deadline, req_id, pending_key["user_id"])
                )
            context.user_data.pop("awaiting_key_delivery", None)
            await msg.reply_text("✅ Llave PIX enviada al participante.")
            audit("manual_key_sent", f"Solicitud {req_id} al usuario {pending_key['user_id']}", update.effective_user.id)
        except Exception:
            log.exception("No se pudo entregar la llave al usuario")
            await msg.reply_text("❌ No se pudo enviar la llave. Comprueba que el usuario haya iniciado el bot.")
        return

    if not is_admin(update.effective_user.id) and not await require_channel(update, context):
        return

    if not is_admin(update.effective_user.id):
        if text in {"🎟️ Ver listas activas", "listas", "lista", "hola", "buenas", "rifa", "inicio"}:
            await lists_cmd(update, context)
            return
        if text == "📊 Estadísticas":
            await msg.reply_text("📊 *TUS ESTADÍSTICAS*\nElige qué quieres consultar:", parse_mode="Markdown", reply_markup=user_stats_keyboard())
            return

    # Panel fijo: solo responde en el chat privado y al administrador autorizado.
    if update.effective_chat.type == "private" and is_admin(update.effective_user.id):
        action = ADMIN_MENU_ACTIONS.get(text)
        if action:
            await admin_menu_action(update, context, action)
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

    if data == "channel:check":
        allowed, reason = await channel_access_status(context.bot, uid)
        if allowed:
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
            "Pulsa «Responder con llave» y después escribe aquí la llave que deseas enviar."
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

    if data.startswith("keyreply:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede responder a esta solicitud.", show_alert=True)
            return
        _, user_id, req_id = data.split(":", 2)
        context.user_data["awaiting_key_delivery"] = {"user_id": int(user_id), "request_id": req_id}
        await q.answer("Escribe la llave en este chat.")
        await q.message.reply_text(f"✍️ Escribe ahora la llave para el usuario {user_id}. Se enviará directamente a su chat privado.")
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
        await q.message.reply_text(list_text(raffle_id), parse_mode="Markdown", reply_markup=user_keyboard())
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
                    "SELECT rq.*,r.name AS raffle_name FROM requests rq JOIN raffles r ON r.id=rq.raffle_id WHERE rq.user_id=? AND rq.status IN ('awaiting_button','key_requested','awaiting_user_click','payment_reported','claim_pending') ORDER BY rq.created_at DESC LIMIT 20",
                    (uid,)
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT w.*,r.name AS raffle_name,r.prize,r.prize_value FROM winners w JOIN raffles r ON r.id=w.raffle_id WHERE w.user_id=? ORDER BY w.id DESC LIMIT 30",
                    (uid,)
                ).fetchall()
        if action == "plays":
            blocks = [f"🎟️ {r['raffle_name']} · Números: {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))} · {float(r['amount']):.2f} {CURRENCY} · {r['status']}" for r in rows]
            blocks += [f"⏳ {r['raffle_name']} · Números: {', '.join(f'{n:02d}' for n in json.loads(r['numbers_json']))} · {float(r['total_amount'] or 0):.2f} {CURRENCY} · pendiente" for r in pending]
            body = "\n\n".join(blocks) if blocks else "Todavía no tienes jugadas registradas."
            title = "🎟️ *MIS JUGADAS*"
        else:
            blocks = [f"🏆 {r['raffle_name']} · Número: {int(r['number']):02d} · Premio: {r['prize'] or 'Por anunciar'} · {'Recibido' if r['prize_received'] else 'Pendiente de confirmar'}" for r in rows]
            body = "\n\n".join(blocks) if blocks else "Todavía no tienes premios registrados."
            title = "🏆 *MIS PREMIOS*"
        await q.answer()
        await q.message.reply_text(title + "\n\n" + body, parse_mode="Markdown", reply_markup=user_stats_keyboard())
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
            if not req["payment_deadline_at"] or req["payment_deadline_at"] <= iso_now():
                await q.answer("El plazo terminó y los números serán liberados.", show_alert=True)
                return
            con.execute(
                "UPDATE requests SET status='payment_reported',reported_at=? WHERE id=? AND status='awaiting_user_click'",
                (iso_now(), req_id)
            )
        await q.answer("Gracias. Tu aviso de pago quedó registrado.")
        await q.edit_message_reply_markup(reply_markup=None)
        raffle = get_raffle(req["raffle_id"])
        nums = ", ".join(f"{n:02d}" for n in json.loads(req["numbers_json"]))
        admin_msg = (
            f"💵 *EL USUARIO INDICA QUE YA PAGÓ*\n\n"
            f"Solicitud: `{req_id}`\nLista: *{raffle['name']}*\n"
            f"Participante: {req['name']} (@{req['username'] or 'sin username'})\n"
            f"ID Telegram: `{req['user_id']}`\nNúmeros: *{nums}*\n"
            f"Importe: *{float(req['total_amount'] or 0):.2f} {CURRENCY}*\n\n"
            "Verifica el pago antes de aprobar."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟢 Aprobar pago", callback_data=f"pay:ok:{req_id}"),
            InlineKeyboardButton("🔴 Rechazar", callback_data=f"pay:no:{req_id}")
        ]])
        try:
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            log.exception("No se pudo avisar al administrador del pago declarado")
            await context.bot.send_message(req["origin_chat"],
                f"⚠️ [{req['name']}](tg://user?id={req['user_id']}) indicó que pagó, pero no se pudo notificar al administrador. "
                "Contacta directamente con él.", parse_mode="Markdown")
        audit("payment_reported_by_user", f"Solicitud {req_id}; usuario {uid}")
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

    if data.startswith("prize:"):
        winner_id = int(data.split(":", 1)[1])
        with db() as con:
            winner = con.execute("SELECT * FROM winners WHERE id=?", (winner_id,)).fetchone()
            if not winner:
                await q.answer("Esta confirmación ya no está disponible.", show_alert=True)
                return
            if uid != winner["user_id"]:
                await q.answer("Solo el ganador registrado puede confirmar el premio.", show_alert=True)
                return
            if winner["prize_received"]:
                await q.answer("El premio ya fue confirmado.", show_alert=True)
                return
            con.execute("UPDATE winners SET receipt_pending=1 WHERE id=?", (winner_id,))
        await q.answer("Envíame el comprobante por privado")
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=("📸 Para confirmar que recibiste el premio, envíame aquí en este chat "
                      "una *captura de la transferencia* que te hicieron. La captura se publicará "
                      "en el canal de transparencia junto con la confirmación de premio recibido. "
                      "Antes de enviarla, tapa contraseñas, claves, saldo y datos bancarios sensibles."),
                parse_mode="Markdown"
            )
        except Exception:
            await q.answer("Abre el chat privado del bot y pulsa /start para enviar la captura.", show_alert=True)
            return
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
                    await q.edit_message_text("El usuario todavía no ha notificado el pago, o la solicitud ya fue procesada/venció.")
                    return
                nums = json.loads(req["numbers_json"])
                status = "approved" if action == "ok" else "rejected"
                for n in nums:
                    if action == "ok":
                        con.execute(
                            "UPDATE numbers SET status='occupied',updated_at=? WHERE raffle_id=? AND number=? AND status='pending'",
                            (iso_now(), req["raffle_id"], n)
                        )
                    else:
                        con.execute(
                            "UPDATE numbers SET status='available',user_id=NULL,name='',username='',updated_at=? "
                            "WHERE raffle_id=? AND number=? AND status='pending'",
                            (iso_now(), req["raffle_id"], n)
                        )
                amount = float(req["total_amount"] or 0) if action == "ok" else 0
                con.execute("UPDATE requests SET status=?,processed_at=? WHERE id=?",
                            (status, iso_now(), req_id))
                con.execute(
                    """INSERT INTO purchases(raffle_id,request_id,user_id,name,username,numbers_json,
                       amount,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (req["raffle_id"], req_id, req["user_id"], req["name"], req["username"],
                     json.dumps(nums), amount, status, iso_now())
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
            await q.edit_message_text(f"✅ Solicitud {req_id} aprobada: {numbers_text}")
            await context.bot.send_message(
                req["origin_chat"],
                f"✅ ¡PAGO APROBADO! Tu pago fue verificado. Números asignados: {numbers_text}. Tu jugada en «{raffle['name']}» está confirmada.",
                parse_mode="Markdown"
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
                              f"💵 Importe: <b>{float(req['total_amount'] or 0):.2f} {CURRENCY}</b>"),
                        parse_mode="HTML"
                    )
                except Exception:
                    log.exception("No se pudo publicar la jugada confirmada en el canal")
        else:
            await q.edit_message_text(f"❌ Solicitud {req_id} rechazada.")
            await context.bot.send_message(
                req["origin_chat"],
                f"❌ Tu solicitud para «{raffle['name']}» fue rechazada. Los números {numbers_text} vuelven a estar disponibles.",
                parse_mode="Markdown"
            )
        audit("payment_" + status, f"Solicitud {req_id}; números {numbers_text}", uid)
        return

    if data.startswith("admin:"):
        if not is_admin(uid) or q.message.chat.type != "private":
            await q.answer("Solo el administrador puede usar este panel en privado.", show_alert=True)
            return
        await q.answer()
        action = data.split(":", 1)[1]
        if action == "create":
            context.user_data["admin_flow"] = {"kind": "create", "step": "name"}
            await q.message.reply_text(admin_flow_prompt("name"), parse_mode="Markdown", reply_markup=admin_keyboard())
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
                rows = [dict(r) for r in con.execute("SELECT * FROM raffles WHERE status='active' ORDER BY id").fetchall()]
            if not rows:
                await q.message.reply_text("No hay listas activas.", reply_markup=admin_back_keyboard())
            else:
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f"📊 {r['name']} (#{r['id']})", callback_data=f"adminstats:{r['id']}")] for r in rows]
                    + [[InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin:back")]]
                )
                await q.message.reply_text("Selecciona la lista que quieres revisar:", reply_markup=keyboard)
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
            await send_history(update, context)
        elif action == "back":
            context.user_data.pop("admin_flow", None)
            await q.message.reply_text("⚙️ *PANEL DE ADMINISTRACIÓN*\nSelecciona una operación:", parse_mode="Markdown", reply_markup=admin_keyboard())
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


async def send_history(update, context):
    with db() as con:
        rows = con.execute(
            """SELECT p.created_at,p.name,p.user_id,p.numbers_json,p.amount,p.status,r.name raffle
               FROM purchases p JOIN raffles r ON r.id=p.raffle_id ORDER BY p.id DESC LIMIT 50"""
        ).fetchall()
    if not rows:
        text = "Todavía no hay compras registradas."
    else:
        text = "👥 *Últimas 50 compras*\n" + "\n".join(
            f"{r['created_at'][:16]} · {r['name']} · {r['raffle']} · "
            f"{r['numbers_json']} · {r['amount']:.2f} {CURRENCY} · {r['status']}"
            for r in rows
        )
    target = update.callback_query.message if update.callback_query else update.effective_message
    await target.reply_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())


async def prize_receipt_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives the winner's transfer screenshot privately and publishes proof to the transparency channel."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.photo or message.chat.type != "private":
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
    try:
        with db() as con:
            con.execute("UPDATE winners SET receipt_file_id=? WHERE id=?", (photo_file_id, winner["id"]))
        public_caption = (
            f"📸 <b>COMPROBANTE DE PREMIO</b>\n"
            f"🎟️ Lista: <b>{html.escape(raffle['name'] if raffle else 'Rifa')}</b>\n"
            f"👤 Ganador: <a href=\"tg://user?id={winner['user_id']}\">{html.escape(winner['name'])}</a>\n"
            "✅ El ganador envió la captura de la transferencia como comprobante."
        )
        await context.bot.send_photo(chat_id=CHANNEL_CHAT_ID, photo=photo_file_id, caption=public_caption, parse_mode="HTML")
        with db() as con:
            con.execute("UPDATE winners SET prize_received=1,receipt_pending=0,received_at=? WHERE id=?", (iso_now(), winner["id"]))
        if winner["channel_message_id"]:
            try:
                await context.bot.edit_message_text(
                    chat_id=CHANNEL_CHAT_ID, message_id=winner["channel_message_id"],
                    text=(f"🏆 <b>RESULTADO DE LA RIFA</b>\n\n"
                          f"🎟️ Lista: <b>{html.escape(raffle['name'] if raffle else 'Rifa')}</b>\n"
                          f"🎯 Número ganador: <b>{int(winner['number']):02d}</b>\n"
                          f"👤 Ganador: <a href=\"tg://user?id={winner['user_id']}\">{html.escape(winner['name'])}</a>\n\n"
                          "✅ <b>PREMIO RECIBIDO</b> — comprobante publicado."),
                    parse_mode="HTML", reply_markup=None
                )
            except Exception:
                log.exception("No se pudo actualizar el mensaje del ganador en el canal")
        await message.reply_text("✅ Captura recibida y publicada en el canal. Tu premio quedó marcado como recibido.")
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

            # Stage 2: if user did not click within the second 5-minute window, release numbers.
            with db() as con:
                expired = con.execute(
                    "SELECT * FROM requests WHERE status='awaiting_user_click' AND payment_deadline_at<=?",
                    (now,)
                ).fetchall()
            for req in expired:
                nums = json.loads(req["numbers_json"])
                async with db_lock:
                    with db() as con:
                        con.execute("BEGIN IMMEDIATE")
                        current = con.execute("SELECT status FROM requests WHERE id=?", (req["id"],)).fetchone()
                        if not current or current["status"] != "awaiting_user_click":
                            continue
                        for n in nums:
                            con.execute(
                                "UPDATE numbers SET status='available',user_id=NULL,name='',username='',updated_at=? "
                                "WHERE raffle_id=? AND number=? AND status='pending' AND user_id=?",
                                (iso_now(), req["raffle_id"], n, req["user_id"])
                            )
                        con.execute(
                            "UPDATE requests SET status='expired',processed_at=? WHERE id=?",
                            (iso_now(), req["id"])
                        )
                try:
                    claim_keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("💸 Reclamar Pago", callback_data=f"claimpay:{req['id']}")
                    ]])
                    name_link = f"[{req['name']}](tg://user?id={req['user_id']})"
                    expiry_text = (
                        f"⌛ Tu reserva venció porque no avisaste el pago dentro del plazo. "
                        f"Se liberaron los números {', '.join(f'{n:02d}' for n in nums)}. "
                        "La jugada no fue completada. Si realizaste el pago, pulsa «Reclamar Pago» "
                        "para que el administrador revise tu caso."
                    )
                    if req["group_message_id"]:
                        await app.bot.edit_message_text(
                            chat_id=req["origin_chat"], message_id=req["group_message_id"],
                            text=expiry_text, parse_mode="Markdown", reply_markup=claim_keyboard
                        )
                    else:
                        await app.bot.send_message(
                            req["origin_chat"], expiry_text, parse_mode="Markdown",
                            reply_markup=claim_keyboard
                        )
                except Exception:
                    log.exception("No se pudo publicar expiración de solicitud %s", req["id"])
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
    """Da la bienvenida en el canal cuando se detecta una nueva membresía."""
    change = update.chat_member
    if not change or not change.chat or change.chat.id != CHANNEL_CHAT_ID:
        return
    old_status = change.old_chat_member.status
    new_status = change.new_chat_member.status
    if new_status in {"member", "administrator", "creator"} and old_status in {"left", "kicked", "banned"}:
        member = change.new_chat_member.user
        if member.id == context.bot.id:
            return
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_CHAT_ID,
                text=(f"👋 ¡Bienvenido/a, {html.escape(member.first_name or 'participante')}!\n\n"
                      "📌 Reglas del canal:\n"
                      "• El canal publica las jugadas confirmadas para transparencia.\n"
                      "• Para elegir números, consultar tus jugadas y recibir mensajes, usa el chat privado del bot.\n"
                      "• Únete al canal antes de utilizar las listas y estadísticas del bot.\n\n"
                      "👇 Abre el bot y pulsa /start para participar."),
                parse_mode="HTML"
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


async def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise RuntimeError("Configura TELEGRAM_TOKEN y ADMIN_TELEGRAM_ID en las variables de entorno.")
    init_db()
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
