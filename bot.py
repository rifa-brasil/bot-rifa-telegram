import os
import sqlite3
import asyncio
import shutil
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
ADMIN_TELEGRAM_ID = os.environ.get("ADMIN_TELEGRAM_ID")

DB_FILE = "database.db"


# =========================================================
# COMPROBAR CONFIGURACIÓN
# =========================================================

if not TELEGRAM_TOKEN:
    raise ValueError(
        "❌ No se encontró la variable TELEGRAM_TOKEN."
    )

if not ADMIN_TELEGRAM_ID:
    raise ValueError(
        "❌ No se encontró la variable ADMIN_TELEGRAM_ID."
    )

try:
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)
except ValueError:
    raise ValueError(
        "❌ ADMIN_TELEGRAM_ID debe ser un número."
    )


# =========================================================
# SERVIDOR WEB PARA RENDER
# =========================================================

async def handle_web(request):
    return web.Response(
        text="Bot de Trading Activo y en Línea 24/7!"
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

def inicializar_base_datos():

    conexion = sqlite3.connect(
        DB_FILE
    )

    cursor = conexion.cursor()

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

    conexion.commit()

    conexion.close()

    print(
        "🗄️ Base de datos inicializada correctamente."
    )


# =========================================================
# REGISTRAR USUARIO
# =========================================================

def registrar_usuario(user):

    conexion = sqlite3.connect(
        DB_FILE
    )

    cursor = conexion.cursor()

    telegram_id = user.id

    nombre = user.full_name or ""

    username = user.username or ""

    fecha = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cursor.execute(
        """
        SELECT telegram_id
        FROM usuarios
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )

    usuario = cursor.fetchone()

    if usuario:

        cursor.execute(
            """
            UPDATE usuarios
            SET nombre = ?,
                username = ?
            WHERE telegram_id = ?
            """,
            (
                nombre,
                username,
                telegram_id
            )
        )

    else:

        cursor.execute(
            """
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
            VALUES (?, ?, ?, 0, 0, 0, ?, NULL, ?)
            """,
            (
                telegram_id,
                nombre,
                username,
                str(telegram_id),
                fecha
            )
        )

    conexion.commit()

    conexion.close()


# =========================================================
# OBTENER USUARIO
# =========================================================

def obtener_usuario(telegram_id):

    conexion = sqlite3.connect(
        DB_FILE
    )

    cursor = conexion.cursor()

    cursor.execute(
        """
        SELECT
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
        """,
        (telegram_id,)
    )

    usuario = cursor.fetchone()

    conexion.close()

    return usuario


# =========================================================
# COMPROBAR ADMINISTRADOR
# =========================================================

def es_admin(user_id):

    return user_id == ADMIN_TELEGRAM_ID


# =========================================================
# MENÚ PRINCIPAL DE USUARIOS
# =========================================================

def crear_menu_principal():

    botones = [

        [
            InlineKeyboardButton(
                "👤 Mi cuenta",
                callback_data="cuenta"
            )
        ],

        [
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
            )
        ],

        [
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
        botones
    )


# =========================================================
# MENÚ ADMINISTRADOR
# =========================================================

def crear_menu_admin():

    botones = [

        [
            InlineKeyboardButton(
                "👥 Usuarios",
                callback_data="admin_usuarios"
            )
        ],

        [
            InlineKeyboardButton(
                "🗄️ Crear respaldo",
                callback_data="admin_backup"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 Estado del sistema",
                callback_data="admin_status"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Menú usuario",
                callback_data="inicio"
            )
        ]

    ]

    return InlineKeyboardMarkup(
        botones
    )


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    registrar_usuario(user)

    nombre = user.first_name or "Usuario"

    texto = (
        f"👋 Hola, *{nombre}*.\n\n"
        "🤖 Bienvenido a nuestro bot de trading.\n\n"
        "Selecciona una opción:"
    )

    await update.message.reply_text(
        texto,
        reply_markup=crear_menu_principal(),
        parse_mode="Markdown"
    )


# =========================================================
# /ADMIN
# =========================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    # -----------------------------------------------------
    # BLOQUEAR USUARIOS NORMALES
    # -----------------------------------------------------

    if not es_admin(user.id):

        await update.message.reply_text(
            "⛔ Comando no disponible."
        )

        print(
            f"⚠️ Intento de acceso administrativo: "
            f"{user.id}"
        )

        return

    # -----------------------------------------------------
    # PANEL ADMIN
    # -----------------------------------------------------

    texto = (
        "🔐 *PANEL DE ADMINISTRADOR*\n\n"
        "Bienvenido al área administrativa.\n\n"
        "Selecciona una opción:"
    )

    await update.message.reply_text(
        texto,
        reply_markup=crear_menu_admin(),
        parse_mode="Markdown"
    )


# =========================================================
# MOSTRAR CUENTA
# =========================================================

async def mostrar_cuenta(
    query,
    user
):

    usuario = obtener_usuario(
        user.id
    )

    if not usuario:

        registrar_usuario(user)

        usuario = obtener_usuario(
            user.id
        )

    (
        telegram_id,
        nombre,
        username,
        saldo,
        invertido,
        ganancias,
        codigo_referido,
        referido_por,
        fecha_registro
    ) = usuario

    texto = (
        "👤 *MI CUENTA*\n\n"
        f"🧑 Nombre: {nombre}\n"
        f"🔹 Usuario: "
        f"@{username if username else 'Sin username'}\n"
        f"🆔 ID: `{telegram_id}`\n\n"
        f"💰 Saldo: ${saldo:.2f}\n"
        f"📊 Invertido: ${invertido:.2f}\n"
        f"📈 Ganancias: ${ganancias:.2f}\n\n"
        f"🎁 Código de referido: `{codigo_referido}`"
    )

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Volver",
                callback_data="inicio"
            )
        ]
    ])

    await query.edit_message_text(
        texto,
        reply_markup=teclado,
        parse_mode="Markdown"
    )


# =========================================================
# INFORMACIÓN DEL SISTEMA PARA ADMIN
# =========================================================

def obtener_estado_sistema():

    if not os.path.exists(
        DB_FILE
    ):

        return (
            "🔴 Base de datos no encontrada."
        )

    tamaño = os.path.getsize(
        DB_FILE
    )

    conexion = sqlite3.connect(
        DB_FILE
    )

    cursor = conexion.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM usuarios"
    )

    usuarios = cursor.fetchone()[0]

    conexion.close()

    return (
        "📊 *ESTADO DEL SISTEMA*\n\n"
        "🟢 Bot funcionando\n"
        "🟢 Base de datos funcionando\n\n"
        f"👥 Usuarios registrados: "
        f"*{usuarios}*\n"
        f"🗄️ Base de datos: `{DB_FILE}`\n"
        f"📦 Tamaño: `{tamaño} bytes`"
    )


# =========================================================
# CREAR BACKUP
# =========================================================

async def crear_backup(
    chat_id,
    context
):

    if not os.path.exists(
        DB_FILE
    ):

        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ No se encontró la base de datos."
        )

        return

    fecha = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    backup_file = (
        f"database_backup_{fecha}.db"
    )

    try:

        print(
            f"📦 Creando respaldo: "
            f"{backup_file}"
        )

        shutil.copy2(
            DB_FILE,
            backup_file
        )

        if not os.path.exists(
            backup_file
        ):

            raise Exception(
                "La copia no fue creada."
            )

        tamaño = os.path.getsize(
            backup_file
        )

        with open(
            backup_file,
            "rb"
        ) as archivo:

            await context.bot.send_document(

                chat_id=chat_id,

                document=archivo,

                filename=backup_file,

                caption=(
                    "✅ *RESPALDO COMPLETADO*\n\n"
                    f"🗄️ Base de datos: `{DB_FILE}`\n"
                    f"📁 Archivo: `{backup_file}`\n"
                    f"📦 Tamaño: `{tamaño} bytes`\n"
                    f"📅 Fecha: `{fecha}`\n\n"
                    "🔐 Guarda este archivo "
                    "en un lugar seguro."
                ),

                parse_mode="Markdown"
            )

        print(
            "✅ Backup enviado correctamente."
        )

    except Exception as e:

        error = str(e)

        print(
            f"❌ ERROR REAL DEL BACKUP: {error}"
        )

        await context.bot.send_message(

            chat_id=chat_id,

            text=(
                "❌ *Error al crear el respaldo.*\n\n"
                f"🔎 Error real:\n`{error}`"
            ),

            parse_mode="Markdown"
        )

    finally:

        if os.path.exists(
            backup_file
        ):

            try:

                os.remove(
                    backup_file
                )

                print(
                    f"🗑️ Copia temporal eliminada: "
                    f"{backup_file}"
                )

            except Exception as e:

                print(
                    f"⚠️ No se pudo eliminar "
                    f"la copia temporal: {e}"
                )


# =========================================================
# CALLBACKS
# =========================================================

async def boton_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    registrar_usuario(user)

    accion = query.data


    # =====================================================
    # MENÚ PRINCIPAL
    # =====================================================

    if accion == "inicio":

        texto = (
            "🤖 *MENÚ PRINCIPAL*\n\n"
            "Selecciona una opción:"
        )

        await query.edit_message_text(
            texto,
            reply_markup=crear_menu_principal(),
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # CUENTA
    # =====================================================

    if accion == "cuenta":

        await mostrar_cuenta(
            query,
            user
        )

        return


    # =====================================================
    # INVERSIONES
    # =====================================================

    if accion == "inversiones":

        texto = (
            "📈 *INVERSIONES*\n\n"
            "Esta sección estará disponible "
            "en la siguiente etapa."
        )

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # DEPOSITAR
    # =====================================================

    if accion == "depositar":

        texto = (
            "💰 *DEPOSITAR*\n\n"
            "La función de depósitos se configurará "
            "en la siguiente etapa."
        )

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # RETIRAR
    # =====================================================

    if accion == "retirar":

        texto = (
            "💸 *RETIRAR*\n\n"
            "La función de retiros se configurará "
            "en la siguiente etapa."
        )

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # REFERIDOS
    # =====================================================

    if accion == "referidos":

        usuario = obtener_usuario(
            user.id
        )

        codigo = (
            usuario[6]
            if usuario and usuario[6]
            else str(user.id)
        )

        texto = (
            "👥 *PROGRAMA DE REFERIDOS*\n\n"
            "Tu código de referido es:\n\n"
            f"`{codigo}`\n\n"
            "La función completa de referidos "
            "se configurará en una próxima etapa."
        )

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # HISTORIAL
    # =====================================================

    if accion == "historial":

        texto = (
            "📜 *HISTORIAL*\n\n"
            "Todavía no tienes movimientos "
            "registrados en el sistema."
        )

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # INFORMACIÓN
    # =====================================================

    if accion == "informacion":

        texto = (
            "ℹ️ *INFORMACIÓN*\n\n"
            "🤖 Plataforma de trading\n\n"
            "Esta aplicación permitirá gestionar "
            "usuarios, depósitos, inversiones, "
            "retiros y movimientos desde Telegram."
        )

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Volver",
                    callback_data="inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # PROTECCIÓN ADMINISTRATIVA
    # =====================================================
    #
    # MUY IMPORTANTE:
    # Aunque un usuario normal intente fabricar
    # manualmente un callback como "admin_usuarios",
    # "admin_backup" o "admin_status", será bloqueado.
    #

    if accion.startswith("admin_"):

        if not es_admin(user.id):

            await query.answer(
                "⛔ No tienes permiso.",
                show_alert=True
            )

            print(
                f"⚠️ Intento de acceso admin "
                f"por usuario: {user.id}"
            )

            return


    # =====================================================
    # ADMIN - USUARIOS
    # =====================================================

    if accion == "admin_usuarios":

        if not es_admin(user.id):
            return

        conexion = sqlite3.connect(
            DB_FILE
        )

        cursor = conexion.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM usuarios"
        )

        cantidad = cursor.fetchone()[0]

        conexion.close()

        texto = (
            "👥 *USUARIOS REGISTRADOS*\n\n"
            f"Total de usuarios: *{cantidad}*"
        )

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Panel admin",
                    callback_data="admin_inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # ADMIN - BACKUP
    # =====================================================

    if accion == "admin_backup":

        if not es_admin(user.id):
            return

        await query.edit_message_text(
            "📦 Preparando respaldo de la base de datos..."
        )

        await crear_backup(
            user.id,
            context
        )

        return


    # =====================================================
    # ADMIN - STATUS
    # =====================================================

    if accion == "admin_status":

        if not es_admin(user.id):
            return

        texto = obtener_estado_sistema()

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Panel admin",
                    callback_data="admin_inicio"
                )
            ]
        ])

        await query.edit_message_text(
            texto,
            reply_markup=teclado,
            parse_mode="Markdown"
        )

        return


    # =====================================================
    # VOLVER AL PANEL ADMIN
    # =====================================================

    if accion == "admin_inicio":

        if not es_admin(user.id):
            return

        texto = (
            "🔐 *PANEL DE ADMINISTRADOR*\n\n"
            "Selecciona una opción:"
        )

        await query.edit_message_text(
            texto,
            reply_markup=crear_menu_admin(),
            parse_mode="Markdown"
        )

        return


# =========================================================
# /BACKUP
# =========================================================

async def backup_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not es_admin(user.id):

        await update.message.reply_text(
            "⛔ Comando no disponible."
        )

        print(
            f"⚠️ Intento de /backup por usuario: "
            f"{user.id}"
        )

        return

    await update.message.reply_text(
        "📦 Preparando respaldo de la base de datos..."
    )

    await crear_backup(
        user.id,
        context
    )


# =========================================================
# /STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not es_admin(user.id):

        await update.message.reply_text(
            "⛔ Comando no disponible."
        )

        return

    texto = obtener_estado_sistema()

    await update.message.reply_text(
        texto,
        parse_mode="Markdown"
    )


# =========================================================
# /USUARIOS
# =========================================================

async def usuarios_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not es_admin(user.id):

        await update.message.reply_text(
            "⛔ Comando no disponible."
        )

        return

    conexion = sqlite3.connect(
        DB_FILE
    )

    cursor = conexion.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM usuarios"
    )

    cantidad = cursor.fetchone()[0]

    conexion.close()

    await update.message.reply_text(

        "👥 *USUARIOS REGISTRADOS*\n\n"
        f"Total de usuarios: *{cantidad}*",

        parse_mode="Markdown"
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    # -----------------------------------------------------
    # BASE DE DATOS
    # -----------------------------------------------------

    inicializar_base_datos()


    # -----------------------------------------------------
    # SERVIDOR WEB
    # -----------------------------------------------------

    await start_web_server()


    # -----------------------------------------------------
    # BOT
    # -----------------------------------------------------

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )


    # -----------------------------------------------------
    # COMANDOS USUARIOS
    # -----------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )


    # -----------------------------------------------------
    # COMANDOS ADMIN
    # -----------------------------------------------------

    app.add_handler(
        CommandHandler(
            "admin",
            admin_command
        )
    )

    app.add_handler(
        CommandHandler(
            "backup",
            backup_command
        )
    )

    app.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    app.add_handler(
        CommandHandler(
            "usuarios",
            usuarios_command
        )
    )


    # -----------------------------------------------------
    # BOTONES
    # -----------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            boton_callback
        )
    )


    # -----------------------------------------------------
    # INICIAR
    # -----------------------------------------------------

    print(
        "🤖 Bot de Trading iniciado correctamente..."
    )

    await app.initialize()

    await app.start()

    await app.updater.start_polling()


    # -----------------------------------------------------
    # MANTENER ACTIVO
    # -----------------------------------------------------

    await asyncio.Event().wait()


# =========================================================
# EJECUCIÓN
# =========================================================

if __name__ == "__main__":

    asyncio.run(main())
