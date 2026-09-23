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
    filters,
)


# =========================================================
# CONFIGURACIÓN
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_TELEGRAM_ID = os.environ.get("ADMIN_TELEGRAM_ID")

DB_FILE = "database.db"


# =========================================================
# VALIDAR VARIABLES
# =========================================================

if not TELEGRAM_TOKEN:
    raise ValueError(
        "❌ Falta la variable TELEGRAM_TOKEN"
    )

if not ADMIN_TELEGRAM_ID:
    raise ValueError(
        "❌ Falta la variable ADMIN_TELEGRAM_ID"
    )

try:
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)
except ValueError:
    raise ValueError(
        "❌ ADMIN_TELEGRAM_ID debe ser un número"
    )


# =========================================================
# SERVIDOR WEB
# =========================================================

async def handle_web(request):

    return web.Response(
        text="Bot activo y funcionando 24/7."
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
        f"🌐 Servidor web corriendo en puerto {port}"
    )


# =========================================================
# BASE DE DATOS
# =========================================================

def conectar_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def inicializar_base_datos():

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute(
        """
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
        """
    )

    conn.commit()

    conn.close()

    print(
        "🗄️ Base de datos inicializada."
    )


# =========================================================
# REGISTRAR USUARIO
# =========================================================

def registrar_usuario(user):

    conn = conectar_db()

    cursor = conn.cursor()

    fecha = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

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

        codigo = f"REF{user.id}"

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

            VALUES (
                ?,
                ?,
                ?,
                0,
                0,
                0,
                ?,
                NULL,
                ?
            )
            """,
            (
                user.id,
                user.first_name or "",
                user.username or "",
                codigo,
                fecha
            )
        )

    conn.commit()

    conn.close()


# =========================================================
# OBTENER USUARIO
# =========================================================

def obtener_usuario(
    telegram_id
):

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

def es_admin(
    user_id
):

    return user_id == ADMIN_TELEGRAM_ID


# =========================================================
# COMPROBAR CHAT PRIVADO
# =========================================================

def es_chat_privado(
    update
):

    chat = update.effective_chat

    if not chat:
        return False

    return chat.type == ChatType.PRIVATE


# =========================================================
# COMPROBAR CHAT PRIVADO DEL ADMIN
# =========================================================

def es_chat_privado_admin(
    update
):

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
# TECLADO USUARIO
# =========================================================

def teclado_usuario():

    return InlineKeyboardMarkup(

        [

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
            ],

        ]
    )


# =========================================================
# TECLADO ADMIN
# =========================================================

def teclado_admin():

    return InlineKeyboardMarkup(

        [

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
            ],

        ]
    )


# =========================================================
# BOTÓN VOLVER USUARIO
# =========================================================

def teclado_volver_usuario():

    return InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "🔙 Volver",
                    callback_data="inicio"
                )
            ]

        ]
    )


# =========================================================
# BOTÓN VOLVER ADMIN
# =========================================================

def teclado_volver_admin():

    return InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "🔙 Volver al panel",
                    callback_data="admin_inicio"
                )
            ]

        ]
    )


# =========================================================
# MOSTRAR MENÚ USUARIO
# =========================================================

async def mostrar_menu_usuario(
    chat_id,
    context
):

    # SEGURIDAD
    # Solo puede enviar el menú al propio usuario.

    if chat_id == ADMIN_TELEGRAM_ID:

        pass

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
    context
):

    # SIEMPRE exclusivamente al admin.

    await context.bot.send_message(

        chat_id=ADMIN_TELEGRAM_ID,

        text=(
            "🔐 *PANEL ADMINISTRATIVO*\n\n"
            "Selecciona una opción:"
        ),

        parse_mode="Markdown",

        reply_markup=teclado_admin()
    )


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # =====================================================
    # MUY IMPORTANTE:
    # NO FUNCIONA EN GRUPOS
    # =====================================================

    if not es_chat_privado(update):

        return

    user = update.effective_user

    if not user:

        return

    registrar_usuario(
        user
    )

    nombre = (
        user.first_name
        or "Usuario"
    )

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

    # Solo privado del admin.

    if not es_chat_privado_admin(update):

        return

    await update.message.reply_text(

        "🔐 *PANEL ADMINISTRATIVO*\n\n"
        "Selecciona una opción:",

        parse_mode="Markdown",

        reply_markup=teclado_admin()
    )


# =========================================================
# CREAR RESPALDO
# =========================================================

async def crear_backup(
    context: ContextTypes.DEFAULT_TYPE
):

    backup_file = (
        "backup_database_"
        + datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        + ".db"
    )

    try:

        if not os.path.exists(
            DB_FILE
        ):

            await context.bot.send_message(

                chat_id=ADMIN_TELEGRAM_ID,

                text=(
                    "❌ No existe la base de datos."
                )
            )

            return False


        # Crear copia
        shutil.copy2(

            DB_FILE,

            backup_file
        )


        # Enviar EXCLUSIVAMENTE al admin
        with open(
            backup_file,
            "rb"
        ) as archivo:

            await context.bot.send_document(

                chat_id=ADMIN_TELEGRAM_ID,

                document=archivo,

                filename=backup_file,

                caption=(
                    "📦 *Respaldo creado correctamente.*\n\n"
                    f"📁 `{backup_file}`"
                ),

                parse_mode="Markdown"
            )


        # Borrar archivo temporal

        try:

            os.remove(
                backup_file
            )

        except Exception:

            pass


        return True


    except Exception as e:

        print(
            f"❌ Error backup: {e}"
        )

        try:

            await context.bot.send_message(

                chat_id=ADMIN_TELEGRAM_ID,

                text=(
                    "❌ *Error creando respaldo.*\n\n"
                    f"`{str(e)}`"
                ),

                parse_mode="Markdown"
            )

        except Exception:

            pass

        return False


# =========================================================
# /BACKUP
# =========================================================

async def backup_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not es_chat_privado_admin(update):

        return

    await update.message.reply_text(
        "📦 *Preparando respaldo...*",
        parse_mode="Markdown"
    )

    await crear_backup(
        context
    )

    # Volver automáticamente
    await mostrar_panel_admin(
        context
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

    total = cursor.fetchone()["total"]

    conn.close()

    await update.message.reply_text(

        (
            "👥 *USUARIOS REGISTRADOS*\n\n"
            f"Total: *{total}*"
        ),

        parse_mode="Markdown",

        reply_markup=teclado_volver_admin()
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

    existe_db = os.path.exists(
        DB_FILE
    )

    tamano = 0

    if existe_db:

        tamano = os.path.getsize(
            DB_FILE
        )

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
        "🖥️ *ESTADO DEL SISTEMA*\n\n"
        "🤖 Bot: 🟢 Activo\n"
        f"🗄️ Base de datos: "
        f"{'🟢 OK' if existe_db else '🔴 Error'}\n"
        f"📁 Tamaño: {tamano} bytes\n"
        f"👥 Usuarios: {total}\n"
        "🌐 Servidor web: 🟢 Activo"
    )

    await update.message.reply_text(

        texto,

        parse_mode="Markdown",

        reply_markup=teclado_volver_admin()
    )


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

    if not user:

        return

    accion = query.data or ""

    message = query.message

    # =====================================================
    # SI NO HAY MENSAJE
    # =====================================================

    if not message:

        await query.answer()

        return


    chat = message.chat


    # =====================================================
    # REGLA ABSOLUTA:
    #
    # NINGÚN BOTÓN FUNCIONA EN GRUPOS.
    # =====================================================

    if chat.type != ChatType.PRIVATE:

        await query.answer()

        return


    # =====================================================
    # ACCIONES ADMIN
    # =====================================================

    if accion.startswith(
        "admin_"
    ):

        # Solo el admin
        if not es_admin(
            user.id
        ):

            await query.answer(
                "⛔ No autorizado.",
                show_alert=True
            )

            return


        # El mensaje tiene que estar
        # en el chat privado del admin.

        if chat.id != ADMIN_TELEGRAM_ID:

            await query.answer(
                "⛔ No autorizado.",
                show_alert=True
            )

            return


        # ---------------------------------------------
        # PANEL PRINCIPAL
        # ---------------------------------------------

        if accion == "admin_inicio":

            await query.answer()

            await query.edit_message_text(

                "🔐 *PANEL ADMINISTRATIVO*\n\n"
                "Selecciona una opción:",

                parse_mode="Markdown",

                reply_markup=teclado_admin()
            )

            return


        # ---------------------------------------------
        # USUARIOS
        # ---------------------------------------------

        if accion == "admin_usuarios":

            await query.answer()

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
                f"Total de usuarios: *{total}*"
            )

            await query.edit_message_text(

                texto,

                parse_mode="Markdown",

                reply_markup=teclado_volver_admin()
            )

            return


        # ---------------------------------------------
        # ESTADO
        # ---------------------------------------------

        if accion == "admin_status":

            await query.answer()

            existe_db = os.path.exists(
                DB_FILE
            )

            tamano = (
                os.path.getsize(DB_FILE)
                if existe_db
                else 0
            )

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
                "🖥️ *ESTADO DEL SISTEMA*\n\n"
                "🤖 Bot: 🟢 Activo\n"
                f"🗄️ DB: "
                f"{'🟢 OK' if existe_db else '🔴 Error'}\n"
                f"📁 Tamaño: {tamano} bytes\n"
                f"👥 Usuarios: {total}\n"
                "🌐 Web: 🟢 Activo"
            )

            await query.edit_message_text(

                texto,

                parse_mode="Markdown",

                reply_markup=teclado_volver_admin()
            )

            return


        # ---------------------------------------------
        # BACKUP
        # ---------------------------------------------

        if accion == "admin_backup":

            await query.answer()

            # Este mensaje SOLO existe
            # en el privado del admin.

            await query.edit_message_text(

                "📦 *Preparando respaldo...*",

                parse_mode="Markdown"
            )


            # Crear y enviar backup
            resultado = await crear_backup(
                context
            )


            # Después del backup,
            # volver automáticamente
            # al panel ADMIN.

            if resultado:

                await mostrar_panel_admin(
                    context
                )

            else:

                await mostrar_panel_admin(
                    context
                )

            return


        # ---------------------------------------------
        # MENÚ USUARIO
        # ---------------------------------------------

        if accion == "admin_usuario":

            await query.answer()

            await query.edit_message_text(

                "👤 *MENÚ DE USUARIO*\n\n"
                "Este es el menú normal:",

                parse_mode="Markdown",

                reply_markup=teclado_usuario()
            )

            return


        return


    # =====================================================
    # ACCIONES DE USUARIO
    # =====================================================

    # -----------------------------------------------------
    # SEGURIDAD FUNDAMENTAL
    #
    # El chat donde está el botón DEBE pertenecer
    # al usuario que lo pulsó.
    # -----------------------------------------------------

    if chat.id != user.id:

        await query.answer()

        return


    # Registrar/actualizar usuario

    registrar_usuario(
        user
    )

    await query.answer()


    # =====================================================
    # INICIO
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
    # CUENTA
    # =====================================================

    if accion == "cuenta":

        usuario = obtener_usuario(
            user.id
        )

        if not usuario:

            await query.edit_message_text(

                "❌ No se encontró tu cuenta.",

                reply_markup=teclado_volver_usuario()
            )

            return


        texto = (
            "👤 *MI CUENTA*\n\n"

            f"🆔 ID: `{usuario['telegram_id']}`\n"

            f"👤 Nombre: "
            f"{usuario['nombre'] or 'Sin nombre'}\n"

            f"📱 Usuario: "
            f"@{usuario['username'] or 'Sin username'}\n\n"

            f"💰 Saldo: "
            f"*{usuario['saldo']:.2f}*\n"

            f"📊 Invertido: "
            f"*{usuario['invertido']:.2f}*\n"

            f"📈 Ganancias: "
            f"*{usuario['ganancias']:.2f}*"
        )


        await query.edit_message_text(

            texto,

            parse_mode="Markdown",

            reply_markup=teclado_volver_usuario()
        )

        return


    # =====================================================
    # INVERSIONES
    # =====================================================

    if accion == "inversiones":

        texto = (
            "💰 *INVERSIONES*\n\n"

            "Aquí aparecerán las opciones "
            "de inversión disponibles.\n\n"

            "🔒 Esta sección está preparada "
            "para la siguiente etapa."
        )

        await query.edit_message_text(

            texto,

            parse_mode="Markdown",

            reply_markup=teclado_volver_usuario()
        )

        return


    # =====================================================
    # DEPOSITAR
    # =====================================================

    if accion == "depositar":

        texto = (
            "💵 *DEPOSITAR*\n\n"

            "Aquí podrás realizar una solicitud "
            "de depósito.\n\n"

            "🔒 Esta sección será conectada "
            "con el sistema de depósitos."
        )

        await query.edit_message_text(

            texto,

            parse_mode="Markdown",

            reply_markup=teclado_volver_usuario()
        )

        return


    # =====================================================
    # RETIRAR
    # =====================================================

    if accion == "retirar":

        texto = (
            "🏧 *RETIRAR*\n\n"

            "Aquí podrás solicitar un retiro.\n\n"

            "🔒 Esta sección será conectada "
            "con el sistema de retiros."
        )

        await query.edit_message_text(

            texto,

            parse_mode="Markdown",

            reply_markup=teclado_volver_usuario()
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
            usuario["codigo_referido"]
            if usuario
            else f"REF{user.id}"
        )

        texto = (
            "👥 *REFERIDOS*\n\n"

            "🔗 Tu código:\n"
            f"`{codigo}`\n\n"

            "Tus referidos estarán asociados "
            "exclusivamente a tu cuenta."
        )

        await query.edit_message_text(

            texto,

            parse_mode="Markdown",

            reply_markup=teclado_volver_usuario()
        )

        return


    # =====================================================
    # HISTORIAL
    # =====================================================

    if accion == "historial":

        texto = (
            "📜 *HISTORIAL*\n\n"

            "Todavía no tienes movimientos "
            "registrados para mostrar.\n\n"

            "🔒 Tus movimientos serán visibles "
            "únicamente para ti."
        )

        await query.edit_message_text(

            texto,

            parse_mode="Markdown",

            reply_markup=teclado_volver_usuario()
        )

        return


    # =====================================================
    # INFORMACIÓN
    # =====================================================

    if accion == "informacion":

        texto = (
            "ℹ️ *INFORMACIÓN*\n\n"

            "Bienvenido a la plataforma.\n\n"

            "Desde este menú podrás acceder "
            "a las funciones disponibles "
            "para tu cuenta."
        )

        await query.edit_message_text(

            texto,

            parse_mode="Markdown",

            reply_markup=teclado_volver_usuario()
        )

        return


# =========================================================
# MANEJADOR DE MENSAJES NO AUTORIZADOS
# =========================================================

async def mensaje_no_autorizado(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # No hacemos absolutamente nada
    # en grupos.
    #
    # Esto evita que el bot publique mensajes
    # que puedan ser vistos por otros usuarios.

    if not update.effective_chat:

        return

    if (
        update.effective_chat.type
        != ChatType.PRIVATE
    ):

        return


# =========================================================
# MAIN
# =========================================================

async def main():

    print(
        "🚀 Iniciando bot..."
    )

    # Base de datos
    inicializar_base_datos()

    # Servidor web
    await start_web_server()

    # Crear aplicación
    app = (
        ApplicationBuilder()
        .token(
            TELEGRAM_TOKEN
        )
        .build()
    )


    # =====================================================
    # /START
    #
    # SOLO CHAT PRIVADO
    # =====================================================

    app.add_handler(

        CommandHandler(

            "start",

            start_command,

            filters=filters.ChatType.PRIVATE
        )
    )


    # =====================================================
    # /ADMIN
    #
    # SOLO CHAT PRIVADO
    # =====================================================

    app.add_handler(

        CommandHandler(

            "admin",

            admin_command,

            filters=filters.ChatType.PRIVATE
        )
    )


    # =====================================================
    # /BACKUP
    # =====================================================

    app.add_handler(

        CommandHandler(

            "backup",

            backup_command,

            filters=filters.ChatType.PRIVATE
        )
    )


    # =====================================================
    # /USUARIOS
    # =====================================================

    app.add_handler(

        CommandHandler(

            "usuarios",

            usuarios_command,

            filters=filters.ChatType.PRIVATE
        )
    )


    # =====================================================
    # /STATUS
    # =====================================================

    app.add_handler(

        CommandHandler(

            "status",

            status_command,

            filters=filters.ChatType.PRIVATE
        )
    )


    # =====================================================
    # BOTONES
    # =====================================================

    app.add_handler(

        CallbackQueryHandler(
            boton_callback
        )
    )


    # =====================================================
    # MENSAJES
    # =====================================================

    app.add_handler(

        # Este handler solamente captura mensajes privados
        # que no sean comandos.
        #
        # No modifica datos ni envía mensajes automáticamente.

        __import__(
            "telegram.ext",
            fromlist=[
                "MessageHandler"
            ]
        ).MessageHandler(

            filters.TEXT
            & ~filters.COMMAND
            & filters.ChatType.PRIVATE,

            mensaje_no_autorizado
        )
    )


    # =====================================================
    # INICIAR
    # =====================================================

    await app.initialize()

    await app.start()

    await app.updater.start_polling(
        drop_pending_updates=True
    )

    print(
        "🟢 BOT FUNCIONANDO 24/7"
    )

    print(
        "🔒 Modo privado activado"
    )

    print(
        f"👨‍💼 Admin ID: {ADMIN_TELEGRAM_ID}"
    )


    # Mantener proceso
    await asyncio.Event().wait()


# =========================================================
# EJECUTAR
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "🛑 Bot detenido."
        )

    except Exception as e:

        print(
            f"❌ Error crítico: {e}"
        )
