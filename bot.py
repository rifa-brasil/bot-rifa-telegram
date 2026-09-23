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
        "❌ No se encontró TELEGRAM_TOKEN."
    )

if not ADMIN_TELEGRAM_ID:
    raise ValueError(
        "❌ No se encontró ADMIN_TELEGRAM_ID."
    )

try:
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)
except ValueError:
    raise ValueError(
        "❌ ADMIN_TELEGRAM_ID debe ser un número."
    )


# =========================================================
# COMPROBAR ADMIN
# =========================================================

def es_admin(user_id):
    return user_id == ADMIN_TELEGRAM_ID


# =========================================================
# SERVIDOR WEB
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
# MENÚ PRINCIPAL DEL USUARIO
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
# MENÚ EXCLUSIVO DEL ADMINISTRADOR
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

    # NUNCA mostrar información administrativa
    # a usuarios normales.

    if not es_admin(user.id):

        print(
            f"⚠️ Intento de acceso admin: {user.id}"
        )

        await update.message.reply_text(
            "⛔ Comando no disponible."
        )

        return

    texto = (
        "🔐 *PANEL DE ADMINISTRADOR*\n\n"
        "Esta sección es privada.\n\n"
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
# ESTADO DEL SISTEMA
# =========================================================

def obtener_estado_sistema():

    if not os.path.exists(DB_FILE):

        return (
            "📊 *ESTADO DEL SISTEMA*\n\n"
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
        f"👥 Usuarios registrados: *{usuarios}*\n"
        f"🗄️ Base de datos: `{DB_FILE}`\n"
        f"📦 Tamaño: `{tamaño} bytes`"
    )


# =========================================================
# BACKUP EXCLUSIVO DEL ADMIN
# =========================================================

async def crear_backup(
    admin_id,
    context
):

    # -----------------------------------------------------
    # SEGURIDAD
    # -----------------------------------------------------

    if not es_admin(admin_id):

        print(
            f"🚨 Intento de backup no autorizado: "
            f"{admin_id}"
        )

        return


    # -----------------------------------------------------
    # COMPROBAR BASE DE DATOS
    # -----------------------------------------------------

    if not os.path.exists(DB_FILE):

        await context.bot.send_message(
            chat_id=ADMIN_TELEGRAM_ID,
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
            f"📦 Creando backup para administrador "
            f"{ADMIN_TELEGRAM_ID}"
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


        # -------------------------------------------------
        # IMPORTANTE:
        # EL ARCHIVO SOLO SE ENVÍA AL ADMINISTRADOR
        # -------------------------------------------------

        with open(
            backup_file,
            "rb"
        ) as archivo:

            await context.bot.send_document(

                chat_id=ADMIN_TELEGRAM_ID,

                document=archivo,

                filename=backup_file,

                caption=(
                    "✅ *RESPALDO COMPLETADO*\n\n"
                    f"🗄️ Base de datos: `{DB_FILE}`\n"
                    f"📁 Archivo: `{backup_file}`\n"
                    f"📦 Tamaño: `{tamaño} bytes`\n"
                    f"📅 Fecha: `{fecha}`\n\n"
                    "🔐 Este respaldo es privado."
                ),

                parse_mode="Markdown"
            )


        print(
            "✅ Backup enviado exclusivamente "
            "al administrador."
        )


    except Exception as e:

        error = str(e)

        print(
            f"❌ ERROR DEL BACKUP: {error}"
        )

        # El error también va SOLO al administrador.

        try:

            await context.bot.send_message(

                chat_id=ADMIN_TELEGRAM_ID,

                text=(
                    "❌ *Error al crear el respaldo.*\n\n"
                    f"🔎 Error:\n`{error}`"
                ),

                parse_mode="Markdown"
            )

        except Exception as error_envio:

            print(
                f"❌ Error enviando mensaje al admin: "
                f"{error_envio}"
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
                    "🗑️ Copia temporal eliminada."
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

    user = query.from_user

    accion = query.data


    # =====================================================
    # PROTECCIÓN ADMINISTRATIVA PRIMERO
    # =====================================================

    if accion.startswith("admin_"):

        # -------------------------------------------------
        # SI NO ES ADMIN:
        # NO EJECUTAR NADA ADMINISTRATIVO
        # -------------------------------------------------

        if not es_admin(user.id):

            print(
                f"🚨 Callback admin bloqueado: "
                f"user={user.id}, "
                f"accion={accion}"
            )

            await query.answer(
                "⛔ No tienes permiso.",
                show_alert=True
            )

            return


    # =====================================================
    # AHORA SÍ RESPONDER AL CALLBACK
    # =====================================================

    await query.answer()


    # Registrar únicamente al usuario que está interactuando.

    registrar_usuario(user)


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
    # MI CUENTA
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
    # PANEL ADMIN
    # =====================================================

    if accion == "admin_inicio":

        # Esta comprobación es obligatoria.

        if not es_admin(user.id):
            return

        texto = (
            "🔐 *PANEL DE ADMINISTRADOR*\n\n"
            "Esta sección es privada.\n\n"
            "Selecciona una opción:"
        )

        await query.edit_message_text(
            texto,
            reply_markup=crear_menu_admin(),
            parse_mode="Markdown"
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

        # -------------------------------------------------
        # ESTE MENSAJE SE EDITA EN EL CHAT PRIVADO
        # DEL ADMINISTRADOR.
        # -------------------------------------------------

        await query.edit_message_text(
            "📦 Preparando respaldo..."
        )

        # El backup SIEMPRE utiliza el ID del administrador.

        await crear_backup(
            ADMIN_TELEGRAM_ID,
            context
        )

        return


    # =====================================================
    # ADMIN - ESTADO
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


# =========================================================
# /BACKUP
# =========================================================

async def backup_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    # -----------------------------------------------------
    # SOLO ADMIN
    # -----------------------------------------------------

    if not es_admin(user.id):

        print(
            f"🚨 /backup bloqueado para {user.id}"
        )

        await update.message.reply_text(
            "⛔ Comando no disponible."
        )

        return


    # -----------------------------------------------------
    # ESTE MENSAJE SOLO APARECE EN EL CHAT DEL ADMIN
    # -----------------------------------------------------

    await update.message.reply_text(
        "📦 Preparando respaldo..."
    )


    # -----------------------------------------------------
    # EL BACKUP SE ENVÍA EXCLUSIVAMENTE AL ADMIN
    # -----------------------------------------------------

    await crear_backup(
        ADMIN_TELEGRAM_ID,
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

        print(
            f"🚨 /status bloqueado para {user.id}"
        )

        await update.message.reply_text(
            "⛔ Comando no disponible."
        )

        return


    texto = obtener_estado_sistema()

    # SOLO responde al chat donde está el ADMIN.

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

        print(
            f"🚨 /usuarios bloqueado para {user.id}"
        )

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


    # SOLO RESPONDE AL ADMIN.

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
    # CREAR BOT
    # -----------------------------------------------------

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )


    # -----------------------------------------------------
    # COMANDOS
    # -----------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

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
