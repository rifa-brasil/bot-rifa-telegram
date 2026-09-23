import os
import asyncio
import sqlite3
from datetime import datetime

from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler
)


# =========================================================
# CONFIGURACIÓN
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

ADMIN_TELEGRAM_ID = int(
    os.environ.get("ADMIN_TELEGRAM_ID", "0")
)

DB_FILE = "database.db"


# =========================================================
# COMPROBAR TOKEN
# =========================================================

if not TELEGRAM_TOKEN:
    raise ValueError(
        "❌ No existe la variable TELEGRAM_TOKEN"
    )


# =========================================================
# SERVIDOR WEB
# =========================================================

async def handle_web(request):

    return web.Response(
        text="Bot de Inversión Activo y en Línea 24/7!"
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        handle_web
    )

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"🌐 Servidor web corriendo en el puerto {port}"
    )


# =========================================================
# BASE DE DATOS
# =========================================================

def conectar_db():

    return sqlite3.connect(DB_FILE)


def inicializar_base_datos():

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            telegram_id INTEGER UNIQUE NOT NULL,

            nombre TEXT,

            username TEXT,

            saldo REAL DEFAULT 0,

            invertido REAL DEFAULT 0,

            ganancias REAL DEFAULT 0,

            codigo_referido TEXT,

            referido_por INTEGER,

            fecha_registro TEXT

        )
    """)

    conn.commit()

    conn.close()

    print("🗄️ Base de datos inicializada correctamente.")


# =========================================================
# BUSCAR USUARIO
# =========================================================

def obtener_usuario(telegram_id):

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            telegram_id,
            nombre,
            username,
            saldo,
            invertido,
            ganancias,
            codigo_referido,
            referido_por,
            fecha_registro

        FROM usuarios

        WHERE telegram_id = ?
    """, (telegram_id,))

    usuario = cursor.fetchone()

    conn.close()

    return usuario


# =========================================================
# CREAR USUARIO
# =========================================================

def registrar_usuario(
    telegram_id,
    nombre,
    username
):

    usuario = obtener_usuario(
        telegram_id
    )

    # Si ya existe, actualizamos
    # nombre y username.

    if usuario:

        conn = conectar_db()

        cursor = conn.cursor()

        cursor.execute("""
            UPDATE usuarios

            SET nombre = ?,
                username = ?

            WHERE telegram_id = ?
        """, (
            nombre,
            username,
            telegram_id
        ))

        conn.commit()

        conn.close()

        return False


    # Código de referido basado
    # en el ID de Telegram.

    codigo_referido = str(
        telegram_id
    )

    fecha = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO usuarios (

            telegram_id,
            nombre,
            username,
            saldo,
            invertido,
            ganancias,
            codigo_referido,
            referido_por,
            fecha_registro

        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

    """, (
        telegram_id,
        nombre,
        username,
        0,
        0,
        0,
        codigo_referido,
        None,
        fecha
    ))

    conn.commit()

    conn.close()

    return True


# =========================================================
# MENÚ PRINCIPAL
# =========================================================

def menu_principal():

    keyboard = [

        [
            InlineKeyboardButton(
                "👤 Mi cuenta",
                callback_data="cuenta"
            ),

            InlineKeyboardButton(
                "📈 Inversiones",
                callback_data="inversiones"
            )
        ],

        [
            InlineKeyboardButton(
                "💰 Depositar",
                callback_data="depositar"
            ),

            InlineKeyboardButton(
                "💸 Retirar",
                callback_data="retirar"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 Referidos",
                callback_data="referidos"
            ),

            InlineKeyboardButton(
                "📜 Historial",
                callback_data="historial"
            )
        ],

        [
            InlineKeyboardButton(
                "ℹ️ Información",
                callback_data="informacion"
            )
        ]

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# TEXTO DEL MENÚ
# =========================================================

def texto_inicio(telegram_id):

    usuario = obtener_usuario(
        telegram_id
    )

    if not usuario:

        return (
            "❌ Usuario no encontrado."
        )

    nombre = usuario[2] or "Usuario"

    saldo = usuario[4] or 0
    invertido = usuario[5] or 0
    ganancias = usuario[6] or 0

    return (
        "💰 *PLATAFORMA DE INVERSIÓN*\n"
        "\n"
        f"👋 Bienvenido, *{nombre}*\n"
        "\n"
        f"💵 *Saldo:* ${saldo:.2f}\n"
        f"📈 *Invertido:* ${invertido:.2f}\n"
        f"💎 *Ganancias:* ${ganancias:.2f}\n"
        "\n"
        "Selecciona una opción:"
    )


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    nombre = user.first_name or "Usuario"

    username = user.username or ""

    telegram_id = user.id

    # Registrar o actualizar usuario.

    nuevo = registrar_usuario(
        telegram_id,
        nombre,
        username
    )

    if nuevo:

        print(
            f"👤 Nuevo usuario registrado: "
            f"{nombre} | {telegram_id}"
        )

    else:

        print(
            f"🔄 Usuario actualizado: "
            f"{nombre} | {telegram_id}"
        )

    texto = texto_inicio(
        telegram_id
    )

    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=menu_principal()
    )


# =========================================================
# BOTONES
# =========================================================

async def boton_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    telegram_id = query.from_user.id

    accion = query.data


    # =====================================================
    # INICIO
    # =====================================================

    if accion == "inicio":

        texto = texto_inicio(
            telegram_id
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=menu_principal()
        )


    # =====================================================
    # MI CUENTA
    # =====================================================

    elif accion == "cuenta":

        usuario = obtener_usuario(
            telegram_id
        )

        if not usuario:

            await query.edit_message_text(
                "❌ No se encontró tu cuenta.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ Volver",
                            callback_data="inicio"
                        )
                    ]
                ])
            )

            return

        nombre = usuario[2] or "Sin nombre"

        username = usuario[3]

        saldo = usuario[4] or 0

        invertido = usuario[5] or 0

        ganancias = usuario[6] or 0

        codigo = usuario[7] or "N/A"

        fecha = usuario[9] or "N/A"

        if username:

            username_text = (
                f"@{username}"
            )

        else:

            username_text = (
                "Sin usuario"
            )

        texto = (
            "👤 *MI CUENTA*\n"
            "\n"
            f"👤 *Nombre:* {nombre}\n"
            f"🧑‍💻 *Usuario:* {username_text}\n"
            f"🆔 *ID:* `{telegram_id}`\n"
            "\n"
            f"💵 *Saldo:* ${saldo:.2f}\n"
            f"📈 *Invertido:* ${invertido:.2f}\n"
            f"💎 *Ganancias:* ${ganancias:.2f}\n"
            "\n"
            f"🔗 *Código de referido:* `{codigo}`\n"
            f"📅 *Registro:* {fecha}"
        )

        teclado = [

            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]

        ]

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                teclado
            )
        )


    # =====================================================
    # INVERSIONES
    # =====================================================

    elif accion == "inversiones":

        texto = (
            "📈 *INVERSIONES*\n"
            "\n"
            "Actualmente no tienes inversiones activas.\n"
            "\n"
            "Los planes de inversión se "
            "configurarán en la siguiente etapa."
        )

        teclado = [

            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]

        ]

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                teclado
            )
        )


    # =====================================================
    # DEPOSITAR
    # =====================================================

    elif accion == "depositar":

        texto = (
            "💰 *DEPOSITAR*\n"
            "\n"
            "La función de depósitos será "
            "configurada en la siguiente etapa.\n"
            "\n"
            "Aquí posteriormente podremos "
            "mostrar la información necesaria "
            "para solicitar un depósito."
        )

        teclado = [

            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]

        ]

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                teclado
            )
        )


    # =====================================================
    # RETIRAR
    # =====================================================

    elif accion == "retirar":

        texto = (
            "💸 *RETIRAR*\n"
            "\n"
            "La función de retiros será "
            "configurada en la siguiente etapa."
        )

        teclado = [

            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]

        ]

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                teclado
            )
        )


    # =====================================================
    # REFERIDOS
    # =====================================================

    elif accion == "referidos":

        usuario = obtener_usuario(
            telegram_id
        )

        codigo = "N/A"

        if usuario:

            codigo = usuario[7] or "N/A"

        texto = (
            "👥 *REFERIDOS*\n"
            "\n"
            f"🔗 *Tu código:* `{codigo}`\n"
            "\n"
            "El sistema de referidos será "
            "activado en una próxima etapa."
        )

        teclado = [

            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]

        ]

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                teclado
            )
        )


    # =====================================================
    # HISTORIAL
    # =====================================================

    elif accion == "historial":

        texto = (
            "📜 *HISTORIAL*\n"
            "\n"
            "Todavía no existen movimientos "
            "registrados en tu cuenta."
        )

        teclado = [

            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]

        ]

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                teclado
            )
        )


    # =====================================================
    # INFORMACIÓN
    # =====================================================

    elif accion == "informacion":

        texto = (
            "ℹ️ *INFORMACIÓN*\n"
            "\n"
            "Bienvenido a nuestra plataforma.\n"
            "\n"
            "Desde este bot podrás gestionar "
            "tu cuenta, depósitos, inversiones, "
            "retiros y referidos.\n"
            "\n"
            "⚠️ Esta versión corresponde a la "
            "etapa inicial de desarrollo."
        )

        teclado = [

            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]

        ]

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                teclado
            )
        )


# =========================================================
# MAIN
# =========================================================

async def main():

    # Crear base de datos automáticamente.

    inicializar_base_datos()

    # Levantar servidor web.

    await start_web_server()

    # Crear aplicación de Telegram.

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    # /start

    app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    # Botones

    app.add_handler(
        CallbackQueryHandler(
            boton_callback
        )
    )

    print(
        "🤖 Bot de Inversión iniciado correctamente..."
    )

    # Inicializar Telegram.

    await app.initialize()

    await app.start()

    await app.updater.start_polling()

    # Mantener proceso activo.

    await asyncio.Event().wait()


# =========================================================
# EJECUTAR
# =========================================================

if __name__ == "__main__":

    asyncio.run(main())
