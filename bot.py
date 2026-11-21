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
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
)
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters
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
DB_PATH = os.getenv("DB_PATH", "rifas.sqlite3")
PORT = int(os.getenv("PORT", "10000"))
RESERVATION_MINUTES = 5  # Primera ventana: 5 min para realizar el pago
PAY_BUTTON_MINUTES = 5   # Segunda ventana: 5 min para avisar que ya pagó
CURRENCY = os.getenv("CURRENCY", "USD")
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
        count = con.execute("SELECT COUNT(*) FROM raffles").fetchone()[0]
        if count == 0:
            group_id = DEFAULT_GROUP_ID
            cur = con.execute(
                """INSERT INTO raffles(name,group_id,total_numbers,price,prize,status,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                ("Rifa principal", group_id, 100,
                 float(os.getenv("DEFAULT_NUMBER_PRICE", "0")),
                 os.getenv("DEFAULT_PRIZE", ""), "active", iso_now())
            )
            rid = cur.lastrowid
            con.executemany(
                "INSERT INTO numbers(raffle_id,number,status,updated_at) VALUES(?,?,?,?)",
                [(rid, n, "available", iso_now()) for n in range(1, 101)]
            )


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


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Estado y estadísticas", callback_data="admin:stats")],
        [InlineKeyboardButton("🎟️ Administrar rifas", callback_data="admin:raffles")],
        [InlineKeyboardButton("🏆 Dar número ganador", callback_data="admin:helpwinner")],
        [InlineKeyboardButton("🔄 Resetear lista", callback_data="admin:reset")],
        [InlineKeyboardButton("💾 Crear copia de seguridad (Excel + JSON)", callback_data="admin:backup")],
        [InlineKeyboardButton("♻️ Restaurar base de datos", callback_data="admin:restore")],
        [InlineKeyboardButton("👥 Historial de participantes", callback_data="admin:history")]
    ])


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
            f"{r['name']} · {float(r['price']):.2f} {CURRENCY}",
            callback_data=f"selectraffle:{r['id']}"
        )] for r in raffles
    ])


async def lists_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raffles = active_raffles(update.effective_chat.id)
    if not raffles:
        await update.effective_message.reply_text("No hay listas activas en este grupo.")
        return
    await update.effective_message.reply_text(
        "🎟️ *Selecciona la lista en la que deseas participar:*",
        parse_mode="Markdown", reply_markup=raffle_selector_keyboard(raffles)
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raffles = active_raffles(update.effective_chat.id)
    if not raffles:
        await update.effective_message.reply_text("No hay listas activas en este grupo.")
    elif len(raffles) == 1:
        context.user_data["selected_raffle_id"] = raffles[0]["id"]
        await update.effective_message.reply_text(
            "¡Bienvenido/a!\n\n" + list_text(raffles[0]["id"]), parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            "¡Bienvenido/a! Hay varias rifas activas. Selecciona una lista:",
            reply_markup=raffle_selector_keyboard(raffles)
        )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ No autorizado.")
        return
    await update.effective_message.reply_text(
        "⚙️ PANEL DE ADMINISTRACIÓN\nSelecciona una operación:",
        reply_markup=admin_keyboard()
    )


async def new_raffle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # Uso: /nueva Nombre|cantidad|precio|group_id|premio
    raw = " ".join(context.args)
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) < 4:
        await update.effective_message.reply_text(
            "Uso: /nueva Lista 2|100|5|-100123456789|Premio|Valor del premio\n"
            "El group_id debe ser el ID numérico del grupo."
        )
        return
    try:
        name, total, price, group = parts[:4]
        prize = parts[4] if len(parts) > 4 else ""
        prize_value = parts[5] if len(parts) > 5 else ""
        total, price, group = int(total), float(Decimal(price)), int(group)
        if not name or not 1 <= total <= 10000 or price < 0:
            raise ValueError()
    except (ValueError, InvalidOperation):
        await update.effective_message.reply_text("Datos inválidos. Revisa cantidad, precio e ID del grupo.")
        return
    with db() as con:
        cur = con.execute(
            """INSERT INTO raffles(name,group_id,total_numbers,price,prize,prize_value,status,created_at)
               VALUES(?,?,?,?,?,?,'active',?)""",
            (name, group, total, price, prize, prize_value, iso_now())
        )
        rid = cur.lastrowid
        con.executemany(
            "INSERT INTO numbers(raffle_id,number,status,updated_at) VALUES(?,?,?,?)",
            [(rid, n, "available", iso_now()) for n in range(1, total + 1)]
        )
    audit("new_raffle", f"Rifa #{rid}: {name}", update.effective_user.id)
    await update.effective_message.reply_text(f"✅ Rifa #{rid} creada: {name} ({total} números).")


async def winner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ No autorizado.")
        return
    # Uso: /ganador ID_RIFA NUMERO
    if len(context.args) != 2:
        await update.effective_message.reply_text("Uso: /ganador ID_RIFA NUMERO")
        return
    try:
        raffle_id, number = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("El ID y el número deben ser enteros.")
        return
    raffle = get_raffle(raffle_id)
    if not raffle or not 1 <= number <= raffle["total_numbers"]:
        await update.effective_message.reply_text("Rifa o número inválido.")
        return
    with db() as con:
        row = con.execute(
            "SELECT * FROM numbers WHERE raffle_id=? AND number=? AND status='occupied'",
            (raffle_id, number)
        ).fetchone()
        if not row:
            await update.effective_message.reply_text("Ese número no está ocupado con un pago aprobado.")
            return
        if con.execute("SELECT 1 FROM winners WHERE raffle_id=?", (raffle_id,)).fetchone():
            await update.effective_message.reply_text("Esta rifa ya tiene un ganador registrado.")
            return
        cur = con.execute(
            """INSERT INTO winners(raffle_id,number,user_id,name,announced_at)
               VALUES(?,?,?,?,?)""",
            (raffle_id, number, row["user_id"], row["name"], iso_now())
        )
        winner_id = cur.lastrowid
        con.execute("UPDATE raffles SET status='finished',closed_at=? WHERE id=?",
                    (iso_now(), raffle_id))
    mention = f"[{row['name']}](tg://user?id={row['user_id']})"
    msg = (
        f"🏆 *¡RESULTADO OFICIAL DE LA RIFA!* 🏆\n\n"
        f"🎟️ Rifa: *{raffle['name']}*\n"
        f"🎯 Número ganador: *{number:02d}*\n"
        f"🎉 Ganador: {mention}\n\n"
        "Cuando recibas tu premio, pulsa el botón *Premio Recibido*."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏆 Premio Recibido", callback_data=f"prize:{winner_id}")
    ]])
    try:
        sent = await context.bot.send_message(
            chat_id=raffle["group_id"], text=msg,
            parse_mode="Markdown", reply_markup=keyboard
        )
        with db() as con:
            con.execute("UPDATE winners SET group_message_id=? WHERE id=?",
                        (sent.message_id, winner_id))
    except Exception:
        log.exception("No se pudo publicar el ganador en el grupo")
        await update.effective_message.reply_text(
            "El ganador quedó registrado, pero no pude publicar en el grupo. "
            "Comprueba GROUP_CHAT_ID y que el bot sea administrador."
        )
        return
    await send_private(
        row["user_id"],
        f"¡Felicidades, {row['name']}! Has ganado {raffle['name']} con el número {number:02d}. "
        "Contacta con el administrador para coordinar la entrega del premio."
    )
    audit("winner", f"Rifa #{raffle_id}, número {number}, usuario {row['user_id']}", update.effective_user.id)
    await update.effective_message.reply_text("✅ Ganador publicado en el grupo y notificación privada intentada desde la cuenta dedicada.")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text or msg.text.startswith("/"):
        return
    text = msg.text.strip()
    if text.lower() == "lista":
        selected_id = context.user_data.get("selected_raffle_id")
        selected = get_raffle(selected_id) if selected_id else None
        if selected and selected["group_id"] == update.effective_chat.id and selected["status"] == "active":
            await msg.reply_text(list_text(selected_id), parse_mode="Markdown")
        else:
            await lists_cmd(update, context)
        return
    if text.lower() in {"listas", "hola", "buenas", "rifa", "inicio"}:
        await lists_cmd(update, context)
        return
    if not re.fullmatch(r"\d+(?:\s*,\s*\d+)*", text):
        return

    group_id = update.effective_chat.id
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
                f"SELECT number,status FROM numbers WHERE raffle_id=? AND number IN ({placeholders})",
                [raffle["id"], *numbers]
            ).fetchall()
            by_num = {r["number"]: r["status"] for r in rows}
            conflicts = [n for n in numbers if by_num.get(n) != "available"]
            if conflicts:
                await msg.reply_text("Estos números ya no están disponibles: " +
                                     ", ".join(f"{n:02d}" for n in conflicts))
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
                 group_id, created.isoformat(), first_deadline.isoformat(),
                 float(raffle["price"]), len(numbers) * float(raffle["price"]))
            )

    amount = len(numbers) * float(raffle["price"])
    number_text = ", ".join(f"{n:02d}" for n in numbers)
    request_text = (
        f"⏳ *SOLICITUD EN PROCESO*\n\n"
        f"🎟️ Lista: *{raffle['name']}*\n"
        f"👤 Participante: {name}\n"
        f"🔢 Números: *{number_text}*\n"
        f"💵 Precio por número: *{float(raffle['price']):.2f} {CURRENCY}*\n"
        f"💰 Total a pagar: *{amount:.2f} {CURRENCY}*\n"
        f"🏆 Premio: *{raffle['prize'] or 'Por anunciar'}*\n"
        f"💎 Valor del premio: *{raffle.get('prize_value') or 'Por definir'}*\n\n"
        f"Realiza el pago dentro de los próximos {RESERVATION_MINUTES} minutos. "
        "Después aparecerá un botón para avisar que ya pagaste."
    )
    try:
        sent = await context.bot.send_message(
            chat_id=group_id, text=request_text, parse_mode="Markdown"
        )
        with db() as con:
            con.execute("UPDATE requests SET group_message_id=? WHERE id=?", (sent.message_id, req_id))
        await msg.reply_text(
            f"✅ Tu solicitud de la {raffle['name']} está publicada en el grupo. "
            "Realiza el pago y espera a que aparezca el botón para notificarlo."
        )
    except Exception:
        log.exception("No se pudo publicar la solicitud en el grupo")
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

    if data.startswith("selectraffle:"):
        raffle_id = int(data.split(":", 1)[1])
        raffle = get_raffle(raffle_id)
        if not raffle or raffle["group_id"] != q.message.chat_id or raffle["status"] != "active":
            await q.answer("Esta lista ya no está disponible.", show_alert=True)
            return
        context.user_data["selected_raffle_id"] = raffle_id
        await q.answer("Lista seleccionada")
        await q.message.reply_text(list_text(raffle_id), parse_mode="Markdown")
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
        await q.answer("Aviso de pago enviado al administrador.")
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
                f"⚠️ {req['name']} indicó que pagó, pero no se pudo notificar al administrador. "
                "Contacta directamente con él.")
        await context.bot.send_message(
            req["origin_chat"],
            f"📨 {req['name']} ha indicado que realizó el pago de la {raffle['name']}. "
            "El pago está pendiente de verificación del administrador."
        )
        audit("payment_reported_by_user", f"Solicitud {req_id}; usuario {uid}")
        return

    if data.startswith("prize:"):
        winner_id = int(data.split(":")[1])
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
            con.execute("UPDATE winners SET prize_received=1,received_at=? WHERE id=?",
                        (iso_now(), winner_id))
        await q.answer("Confirmación registrada. ¡Gracias!")
        await q.edit_message_reply_markup(reply_markup=None)
        raffle = get_raffle(winner["raffle_id"])
        await context.bot.send_message(
            chat_id=raffle["group_id"],
            text=f"🏆 ¡PREMIO RECIBIDO CONFIRMADO!\n\n"
                 f"{winner['name']} ha confirmado que recibió su premio en la rifa «{raffle['name']}»."
        )
        audit("prize_received", f"Ganador #{winner_id}, usuario {uid}")
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
        raffle = get_raffle(req["raffle_id"])
        numbers_text = ", ".join(f"{n:02d}" for n in nums)
        if action == "ok":
            await q.edit_message_text(f"✅ Solicitud {req_id} aprobada: {numbers_text}")
            await context.bot.send_message(
                req["origin_chat"],
                f"🎉 Pago confirmado para {req['name']}. Números asignados: {numbers_text}."
            )
            await send_private(
                req["user_id"],
                f"✅ ¡PAGO APROBADO!\nHola {req['name']}, tu pago ha sido verificado. "
                f"Tus números ({numbers_text}) ya están registrados en «{raffle['name']}»."
            )
        else:
            await q.edit_message_text(f"❌ Solicitud {req_id} rechazada.")
            await context.bot.send_message(
                req["origin_chat"],
                f"⚠️ La solicitud de {req['name']} fue rechazada. Números liberados: {numbers_text}."
            )
            await send_private(
                req["user_id"],
                f"❌ Tu solicitud para {raffle['name']} fue rechazada. "
                f"Los números {numbers_text} vuelven a estar disponibles."
            )
        audit("payment_" + status, f"Solicitud {req_id}; números {numbers_text}", uid)
        return

    if data.startswith("admin:"):
        await q.answer()
        action = data.split(":", 1)[1]
        raffle = get_active_raffle()
        if action == "stats":
            rows = active_raffles(DEFAULT_GROUP_ID)
            if not rows:
                text = "No hay listas activas en el grupo configurado."
            else:
                blocks = []
                for item in rows:
                    s = stats(item["id"])
                    blocks.append(
                        f"📊 *{s['name']}* (#{item['id']})\n"
                        f"Ocupados: {s['occupied']}/{s['total']} · Pendientes: {s['pending']}\n"
                        f"Disponibles: {s['available']}\n"
                        f"Recaudado: {s['revenue']:.2f} {CURRENCY} · Precio: {s['price']:.2f} {CURRENCY}"
                    )
                text = "\n\n".join(blocks)
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        elif action == "raffles":
            with db() as con:
                rows = con.execute("SELECT id,name,status,total_numbers FROM raffles ORDER BY id DESC LIMIT 20").fetchall()
            text = "🎟️ *Rifas registradas*\n" + "\n".join(
                f"#{r['id']} · {r['name']} · {r['status']} · {r['total_numbers']} números" for r in rows
            ) + "\n\nCrear: /nueva Lista 2|100|5|ID_GRUPO|Premio|Valor del premio"
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        elif action == "helpwinner":
            await q.edit_message_text("Para declarar el ganador usa:\n/ganador ID_RIFA NUMERO\n\nEjemplo: /ganador 1 14", reply_markup=admin_keyboard())
        elif action == "reset":
            rows = active_raffles(DEFAULT_GROUP_ID)
            if not rows:
                await q.edit_message_text("No hay listas activas para resetear.", reply_markup=admin_keyboard())
                return
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"🔄 {r['name']} (#{r['id']})", callback_data=f"resetchoose:{r['id']}")]
                 for r in rows] +
                [[InlineKeyboardButton("Cancelar", callback_data="admin:back")]]
            )
            await q.edit_message_text("Selecciona qué lista deseas resetear:", reply_markup=keyboard)
        elif action == "backup":
            await send_backup(update, context)
        elif action == "restore":
            await q.edit_message_text(
                "Envía el archivo JSON de respaldo en este chat privado. "
                "El sistema validará el archivo y pedirá confirmación."
            )
            context.user_data["awaiting_restore"] = True
        elif action == "history":
            await send_history(update, context)
        elif action == "back":
            await q.edit_message_text("⚙️ PANEL DE ADMINISTRACIÓN", reply_markup=admin_keyboard())
        return

    if data.startswith("resetchoose:"):
        await q.answer()
        raffle_id = int(data.split(":", 1)[1])
        raffle_to_reset = get_raffle(raffle_id)
        if not raffle_to_reset or not is_admin(uid):
            await q.edit_message_text("Lista no encontrada o sin autorización.")
            return
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
        await q.answer()
        raffle_id = int(data.split(":")[1])
        with db() as con:
            con.execute("UPDATE numbers SET status='available',user_id=NULL,name='',username='',updated_at=? WHERE raffle_id=?",
                        (iso_now(), raffle_id))
            con.execute(
                "UPDATE requests SET status='cancelled',processed_at=? WHERE raffle_id=? "
                "AND status IN ('pending','awaiting_button','awaiting_user_click','payment_reported')",
                (iso_now(), raffle_id)
            )
            con.execute("UPDATE raffles SET status='active',closed_at=NULL WHERE id=?", (raffle_id,))
        audit("reset", f"Rifa #{raffle_id}", uid)
        await q.edit_message_text(f"✅ Rifa #{raffle_id} reiniciada. El historial se conserva.")
        return


async def send_backup(update, context):
    if update.effective_chat.type != "private":
        await update.callback_query.answer("Abre el panel en privado para crear la copia.", show_alert=True)
        return
    await send_excel(update, context)
    await send_json(update, context)


async def send_excel(update, context):
    if update.effective_chat.type != "private":
        await update.callback_query.answer("Abre el panel en privado para descargar el archivo.", show_alert=True)
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
        await update.callback_query.message.reply_text(f"No se pudo crear el Excel: {e}")


async def send_json(update, context):
    if update.effective_chat.type != "private":
        await update.callback_query.answer("Abre el panel en privado para descargar el archivo.", show_alert=True)
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
    await update.callback_query.message.reply_text(text, parse_mode="Markdown")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.user_data.get("awaiting_restore"):
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
    if not is_admin(q.from_user.id):
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
            # Stage 1: five minutes after request creation, reveal the "I paid" button.
            with db() as con:
                ready = con.execute(
                    "SELECT * FROM requests WHERE status='awaiting_button' AND expires_at<=?",
                    (now,)
                ).fetchall()
            for req in ready:
                raffle = get_raffle(req["raffle_id"])
                nums = ", ".join(f"{n:02d}" for n in json.loads(req["numbers_json"]))
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("💵 YA REALICÉ EL PAGO", callback_data=f"userpaid:{req['id']}")
                ]])
                try:
                    if req["group_message_id"]:
                        await app.bot.edit_message_text(
                            chat_id=req["origin_chat"], message_id=req["group_message_id"],
                            text=(
                                f"⏳ *SOLICITUD EN PROCESO*\n\nLista: *{raffle['name']}*\n"
                                f"Participante: {req['name']}\nNúmeros: *{nums}*\n"
                                f"Precio por número: *{float(req['price_per_number'] or 0):.2f} {CURRENCY}*\n"
                                f"Total a pagar: *{float(req['total_amount'] or 0):.2f} {CURRENCY}*\n"
                                f"Premio: *{raffle['prize'] or 'Por anunciar'}*\n"
                                f"Valor del premio: *{raffle['prize_value'] or 'Por definir'}*\n\n"
                                "Si ya realizaste el pago, pulsa el botón. Tienes 5 minutos para avisar. "
                                "Si no lo haces, la reserva se cancelará y los números se liberarán."
                            ), parse_mode="Markdown", reply_markup=keyboard
                        )
                    with db() as con:
                        con.execute(
                            "UPDATE requests SET status='awaiting_user_click',pay_button_at=?,payment_deadline_at=? "
                            "WHERE id=? AND status='awaiting_button'",
                            (iso_now(), (now_utc() + timedelta(minutes=PAY_BUTTON_MINUTES)).isoformat(), req["id"])
                        )
                except Exception:
                    log.exception("No se pudo mostrar botón de pago para solicitud %s", req["id"])
                    # Si no se pudo mostrar el botón, no se libera inmediatamente: se reintentará.

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
                    await app.bot.send_message(
                        req["origin_chat"],
                        f"⌛ La reserva de {req['name']} venció porque no avisó el pago dentro del plazo. "
                        f"Se liberaron los números {', '.join(f'{n:02d}' for n in nums)}."
                    )
                    if req["group_message_id"]:
                        await app.bot.edit_message_reply_markup(
                            chat_id=req["origin_chat"], message_id=req["group_message_id"], reply_markup=None
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
                        r["group_id"],
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


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_message.new_chat_members:
        return
    for member in update.effective_message.new_chat_members:
        if member.id != context.bot.id:
            name = member.first_name or "Participante"
            await update.effective_message.reply_text(
                f"👋 ¡Bienvenido/a {name}!\\n"
                "Para consultar los números escribe `lista`. Para solicitar números, "
                "envíalos separados por comas (por ejemplo: 7, 14).",
                parse_mode="Markdown"
            )


async def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise RuntimeError("Configura TELEGRAM_TOKEN y ADMIN_TELEGRAM_ID en las variables de entorno.")
    init_db()
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
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("nueva", new_raffle_cmd))
    app.add_handler(CommandHandler("ganador", winner_cmd))
    app.add_handler(CommandHandler("pick3", pick3_cmd))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    app.add_handler(CallbackQueryHandler(restore_callback, pattern=r"^restore:"))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    log.info("Bot de rifas iniciado.")
    await app.initialize()
    await app.start()
    # post_init no se ejecuta al usar initialize/start manualmente.
    await post_init(app)
    await app.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        if user_client:
            await user_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
