import os
import sqlite3
import asyncio
import shutil
from datetime import datetime

from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.constants import ChatType

from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)


# =========================================================
# CONFIGURACIÓN
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_TELEGRAM_ID = os.environ.get("ADMIN_TELEGRAM_ID")

DB_FILE = "database.db"


# =========================================================
# VALIDACIÓN DE CONFIGURACIÓN
# =========================================================

if not TELEGRAM_TOKEN:
    raise ValueError("❌ Falta la variable TELEGRAM_TOKEN")

if not ADMIN_TELEGRAM_ID:
    raise ValueError("❌ Falta la variable ADMIN_TELEGRAM_ID")

try:
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)
except ValueError:
    raise ValueError("❌ ADMIN_TELEGRAM_ID debe ser un número")


# =========================================================
# SERVIDOR WEB PARA RENDER
# =========================================================

async def handle_web(request):
    return web.Response(
        text="Bot de Inversiones Activo y en Línea 24/7!"
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get("/", handle_web)

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(os.environ.get("PORT", 10000))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(f"🌐 Servidor web corriendo en el puerto {port}")


# =========================================================
# BASE DE DATOS
# =========================================================

def conectar_db():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


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
            referido_por TEXT,
            fecha_registro TEXT
        )
    """)

    conn.commit()

    conn.close()

    print("🗄️ Base de datos inicializada correctamente.")


# =========================================================
# REGISTRAR / ACTUALIZAR USUARIO
# =========================================================

def registrar_usuario(user):

    conn = conectar_db()

    cursor = conn.cursor()

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        SELECT telegram_id
        FROM usuarios
        WHERE telegram_id = ?
        """,
        (user.id,)
    )

    existe = cursor.fetchone()

    if existe:

        cursor.execute(
            """
            UPDATE usuarios
            SET nombre = ?,
                username = ?
            WHERE telegram_id = ?
            """,
            (
                user.first_name or "",
                user.username or "",
                user.id
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
                user.id,
                user.first_name or "",
                user.username or "",
                f"REF{user.id}",
                ahora
            )
        )

    conn.commit()

    conn.close()


# =========================================================
# OBTENER USUARIO
# =========================================================

def obtener_usuario(telegram_id):

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM usuarios
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )

    usuario = cursor.fetchone()

    conn.close()

    return usuario


# =========================================================
# COMPROBAR ADMIN
# =========================================================

def es_admin(user_id):

    return user_id == ADMIN_TELEGRAM_ID


# =========================================================
# COMPROBAR CHAT PRIVADO DEL ADMIN
# =========================================================

def es_chat_privado_admin(update):

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    return (
        user.id == ADMIN_TELEGRAM_ID
        and chat.id == ADMIN_TELEGRAM_ID
        and chat.type == ChatType.PRIVATE
    )


# =========================================================
# MENÚ PRINCIPAL DEL USUARIO
# =========================================================

def teclado_usuario():

    keyboard = [

        [
            InlineKeyboardButton(
                "👤 Mi cuenta",
                callback_data="cuenta"
            )
        ],

        [
            InlineKeyboardButton(
                "💰 Inversiones",
                callback_data="inversiones"
            )
        ],

        [
            InlineKeyboardButton(
                "💵 Depositar",
                callback_data="depositar"
            )
        ],

        [
            InlineKeyboardButton(
                "🏧 Retirar",
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

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# MENÚ ADMINISTRATIVO
# =========================================================

def teclado_admin():

    keyboard = [

        [
            InlineKeyboardButton(
                "👥 Usuarios",
                callback_data="admin_usuarios"
            )
        ],

        [
            InlineKeyboardButton(
                "📦 Crear respaldo",
                callback_data="admin_backup"
            )
        ],

        [
            InlineKeyboardButton(
                "🖥️ Estado del sistema",
                callback_data="admin_status"
            )
        ],

        [
            InlineKeyboardButton(
                "👤 Menú usuario",
                callback_data="admin_usuario"
            )
        ]

    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user

    registrar_usuario(user)

    nombre = user.first_name or "Usuario"

    texto = (
        f"👋 ¡Hola {nombre}!\n\n"
        "Bienvenido a tu cuenta.\n\n"
        "Selecciona una opción:"
    )

    await update.message.reply_text(
        texto,
        reply_markup=teclado_usuario()
    )


# =========================================================
# /ADMIN
# =========================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user

    chat = update.effective_chat

    # Nunca mostrar panel admin en grupos
    if (
        user.id != ADMIN_TELEGRAM_ID
        or chat.id != ADMIN_TELEGRAM_ID
        or chat.type != ChatType.PRIVATE
    ):
        return

    texto = (
        "🔐 *PANEL ADMINISTRATIVO*\n\n"
        "Selecciona una opción:"
    )

    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=teclado_admin()
    )


# =========================================================
# MOSTRAR MENÚ USUARIO
# =========================================================

async def mostrar_menu_usuario(
    chat_id,
    context
):

    texto = (
        "🏠 *MENÚ PRINCIPAL*\n\n"
        "Selecciona una opción:"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=texto,
        parse_mode="Markdown",
        reply_markup=teclado_usuario()
    )


# =========================================================
# MOSTRAR PANEL ADMIN
# =========================================================

async def mostrar_panel_admin(
    chat_id,
    context
):

    # Seguridad adicional
    if chat_id != ADMIN_TELEGRAM_ID:
        return

    texto = (
        "🔐 *PANEL ADMINISTRATIVO*\n\n"
        "Selecciona una opción:"
    )

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=texto,
        parse_mode="Markdown",
        reply_markup=teclado_admin()
    )


# =========================================================
# /USUARIOS
# =========================================================

async def usuarios_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not es_chat_privado_admin(update):
        return

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM usuarios
        """
    )

    resultado = cursor.fetchone()

    total = resultado["total"]

    conn.close()

    texto = (
        "👥 *USUARIOS REGISTRADOS*\n\n"
        f"Total de usuarios: *{total}*"
    )

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=texto,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Volver al panel",
                    callback_data="admin_inicio"
                )
            ]
        ])
    )


# =========================================================
# /STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not es_chat_privado_admin(update):
        return

    existe_db = os.path.exists(DB_FILE)

    tamano_db = 0

    if existe_db:
        tamano_db = os.path.getsize(DB_FILE)

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) AS total FROM usuarios"
    )

    total_usuarios = cursor.fetchone()["total"]

    conn.close()

    texto = (
        "🖥️ *ESTADO DEL SISTEMA*\n\n"
        "🤖 Bot: 🟢 Activo\n"
        f"🗄️ Base de datos: {'🟢 OK' if existe_db else '🔴 No existe'}\n"
        f"📁 Tamaño DB: {tamano_db} bytes\n"
        f"👥 Usuarios: {total_usuarios}\n"
        "🌐 Servidor web: 🟢 Activo"
    )

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=texto,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Volver al panel",
                    callback_data="admin_inicio"
                )
            ]
        ])
    )


# =========================================================
# CREAR RESPALDO
# =========================================================

async def crear_backup(
    context: ContextTypes.DEFAULT_TYPE
):

    # Seguridad absoluta:
    # el backup SIEMPRE va al admin
    chat_id = ADMIN_TELEGRAM_ID

    backup_file = (
        f"backup_database_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )

    try:

        if not os.path.exists(DB_FILE):

            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ No existe la base de datos para crear el respaldo."
            )

            return False

        # Copia física de SQLite
        shutil.copy2(
            DB_FILE,
            backup_file
        )

        # Enviar SOLO al administrador
        with open(backup_file, "rb") as archivo:

            await context.bot.send_document(
                chat_id=chat_id,
                document=archivo,
                filename=backup_file,
                caption=(
                    "📦 *Respaldo creado correctamente.*\n\n"
                    f"📁 Archivo: `{backup_file}`"
                ),
                parse_mode="Markdown"
            )

        # Eliminar copia temporal
        try:
            os.remove(backup_file)
        except Exception:
            pass

        return True

    except Exception as e:

        print(
            f"❌ Error creando respaldo: {e}"
        )

        try:

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "❌ *Error creando el respaldo.*\n\n"
                    f"Detalles: `{str(e)}`"
                ),
                parse_mode="Markdown"
            )

        except Exception as error_envio:

            print(
                f"❌ Error enviando error al admin: {error_envio}"
            )

        return False


# =========================================================
# CALLBACK PRINCIPAL
# =========================================================

async def boton_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    user = query.from_user

    accion = query.data or ""

    # =====================================================
    # SEGURIDAD ADMINISTRATIVA
    # =====================================================

    if accion.startswith("admin_"):

        # El callback administrativo solo puede ejecutarse
        # desde el chat privado del administrador.

        if (
            user.id != ADMIN_TELEGRAM_ID
            or not query.message
            or query.message.chat.id != ADMIN_TELEGRAM_ID
            or query.message.chat.type != ChatType.PRIVATE
        ):

            # Solo responde al usuario que hizo clic.
            # NO manda ningún mensaje al grupo.
            await query.answer(
                "⛔ Acción no autorizada.",
                show_alert=True
            )

            return

    # Confirmamos callback
    await query.answer()

    # =====================================================
    # PANEL ADMIN
    # =====================================================

    if accion == "admin_inicio":

        await query.edit_message_text(
            "🔐 *PANEL ADMINISTRATIVO*\n\n"
            "Selecciona una opción:",
            parse_mode="Markdown",
            reply_markup=teclado_admin()
        )

        return


    # =====================================================
    # USUARIOS
    # =====================================================

    if accion == "admin_usuarios":

        conn = conectar_db()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM usuarios
            """
        )

        total = cursor.fetchone()["total"]

        conn.close()

        texto = (
            "👥 *USUARIOS REGISTRADOS*\n\n"
            f"Total: *{total}*"
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver al panel",
                        callback_data="admin_inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # ESTADO DEL SISTEMA
    # =====================================================

    if accion == "admin_status":

        existe_db = os.path.exists(DB_FILE)

        tamano_db = (
            os.path.getsize(DB_FILE)
            if existe_db
            else 0
        )

        conn = conectar_db()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) AS total FROM usuarios"
        )

        total_usuarios = cursor.fetchone()["total"]

        conn.close()

        texto = (
            "🖥️ *ESTADO DEL SISTEMA*\n\n"
            "🤖 Bot: 🟢 Activo\n"
            f"🗄️ Base de datos: "
            f"{'🟢 OK' if existe_db else '🔴 No existe'}\n"
            f"📁 Tamaño: {tamano_db} bytes\n"
            f"👥 Usuarios: {total_usuarios}\n"
            "🌐 Servidor web: 🟢 Activo"
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver al panel",
                        callback_data="admin_inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # CREAR BACKUP
    # =====================================================

    if accion == "admin_backup":

        # Este mensaje SOLO existe en el chat privado del admin
        await query.edit_message_text(
            "📦 *Preparando respaldo...*",
            parse_mode="Markdown"
        )

        resultado = await crear_backup(context)

        # Después de enviar el archivo,
        # volver automáticamente al panel.
        if resultado:

            await mostrar_panel_admin(
                ADMIN_TELEGRAM_ID,
                context
            )

        else:

            await mostrar_panel_admin(
                ADMIN_TELEGRAM_ID,
                context
            )

        return


    # =====================================================
    # MENÚ USUARIO DESDE ADMIN
    # =====================================================

    if accion == "admin_usuario":

        await query.edit_message_text(
            "👤 *MENÚ DE USUARIO*\n\n"
            "Este es el mismo menú que verá un usuario normal.",
            parse_mode="Markdown",
            reply_markup=teclado_usuario()
        )

        return


    # =====================================================
    # VOLVER AL MENÚ USUARIO
    # =====================================================

    if accion == "inicio":

        await query.edit_message_text(
            "🏠 *MENÚ PRINCIPAL*\n\n"
            "Selecciona una opción:",
            parse_mode="Markdown",
            reply_markup=teclado_usuario()
        )

        return


    # =====================================================
    # MI CUENTA
    # =====================================================

    if accion == "cuenta":

        registrar_usuario(user)

        usuario = obtener_usuario(user.id)

        if not usuario:

            await query.edit_message_text(
                "❌ No se pudo encontrar tu cuenta.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 Volver",
                            callback_data="inicio"
                        )
                    ]
                ])
            )

            return

        texto = (
            "👤 *MI CUENTA*\n\n"
            f"🆔 ID: `{usuario['telegram_id']}`\n"
            f"👤 Nombre: {usuario['nombre'] or 'Sin nombre'}\n"
            f"📱 Usuario: "
            f"@{usuario['username'] or 'Sin username'}\n\n"
            f"💰 Saldo: *{usuario['saldo']:.2f}*\n"
            f"📊 Invertido: *{usuario['invertido']:.2f}*\n"
            f"📈 Ganancias: *{usuario['ganancias']:.2f}*"
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver",
                        callback_data="inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # INVERSIONES
    # =====================================================

    if accion == "inversiones":

        texto = (
            "💰 *INVERSIONES*\n\n"
            "Aquí aparecerán las opciones de inversión "
            "disponibles para tu cuenta.\n\n"
            "🔒 Esta sección será configurada en la siguiente etapa."
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver",
                        callback_data="inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # DEPOSITAR
    # =====================================================

    if accion == "depositar":

        texto = (
            "💵 *DEPOSITAR*\n\n"
            "La sección de depósitos será configurada "
            "en la siguiente etapa.\n\n"
            "Cada solicitud quedará asociada exclusivamente "
            "a tu cuenta de Telegram."
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver",
                        callback_data="inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # RETIRAR
    # =====================================================

    if accion == "retirar":

        texto = (
            "🏧 *RETIRAR*\n\n"
            "La sección de retiros será configurada "
            "en la siguiente etapa.\n\n"
            "Las solicitudes estarán vinculadas únicamente "
            "a tu cuenta."
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver",
                        callback_data="inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # REFERIDOS
    # =====================================================

    if accion == "referidos":

        registrar_usuario(user)

        usuario = obtener_usuario(user.id)

        codigo = usuario["codigo_referido"]

        texto = (
            "👥 *REFERIDOS*\n\n"
            f"🔗 Tu código de referido:\n"
            f"`{codigo}`\n\n"
            "Aquí aparecerán tus referidos y las "
            "comisiones correspondientes."
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver",
                        callback_data="inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # HISTORIAL
    # =====================================================

    if accion == "historial":

        texto = (
            "📜 *HISTORIAL*\n\n"
            "Todavía no hay movimientos registrados "
            "para mostrar.\n\n"
            "Tus movimientos futuros aparecerán "
            "exclusivamente en tu cuenta."
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver",
                        callback_data="inicio"
                    )
                ]
            ])
        )

        return


    # =====================================================
    # INFORMACIÓN
    # =====================================================

    if accion == "informacion":

        texto = (
            "ℹ️ *INFORMACIÓN*\n\n"
            "Bienvenido a nuestra plataforma.\n\n"
            "Desde aquí podrás consultar las diferentes "
            "funciones disponibles para tu cuenta."
        )

        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Volver",
                        callback_data="inicio"
                    )
                ]
            ])
        )

        return


# =========================================================
# COMANDOS ADMINISTRATIVOS
# SOLO CHAT PRIVADO DEL ADMIN
# =========================================================

async def backup_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not es_chat_privado_admin(update):
        return

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text="📦 *Preparando respaldo...*",
        parse_mode="Markdown"
    )

    await crear_backup(context)

    await mostrar_panel_admin(
        ADMIN_TELEGRAM_ID,
        context
    )


async def admin_status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not es_chat_privado_admin(update):
        return

    await status_command(
        update,
        context
    )

    await mostrar_panel_admin(
        ADMIN_TELEGRAM_ID,
        context
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    inicializar_base_datos()

    # Servidor web
    await start_web_server()

    # Aplicación Telegram
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    # -----------------------------------------
    # COMANDOS DE USUARIO
    # -----------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    # -----------------------------------------
    # COMANDOS ADMIN
    # -----------------------------------------

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
            "usuarios",
            usuarios_command
        )
    )

    app.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    # -----------------------------------------
    # CALLBACKS
    # -----------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            boton_callback
        )
    )

    print(
        "🤖 Bot iniciado correctamente..."
    )

    # Inicialización
    await app.initialize()

    await app.start()

    await app.updater.start_polling(
        drop_pending_updates=True
    )

    print(
        "🟢 Bot funcionando 24/7..."
    )

    # Mantener proceso activo
    await asyncio.Event().wait()


# =========================================================
# EJECUTAR
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except (
        KeyboardInterrupt,
        SystemExit
    ):

        print(
            "🛑 Bot detenido correctamente."
        )
