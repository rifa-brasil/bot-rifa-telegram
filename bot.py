import os
import json
import uuid
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# --- SERVIDOR WEB PARA CUMPLIR CON EL PUERTO DE RENDER ---
async def handle_web(request):
    return web.Response(text="Bot de Rifa Activo y en Línea 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Servidor web corriendo en el puerto {port}")
# ---------------------------------------------------------

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", "0"))

DB_FILE = "rifa_db.json"

def inicializar_rifa():
    try:
        if not os.path.exists(DB_FILE):
            data_inicial = {
                "estado_rifa": "activa",
                "numeros": {str(i): {"estado": "disponible", "nombre": "", "user_id": "", "username": ""} for i in range(1, 101)},
                "solicitudes_pendientes": {}
            }
            with open(DB_FILE, "w") as f:
                json.dump(data_inicial, f, indent=4)
    except Exception as e:
        print(f"Error al inicializar JSON: {e}")

def borrar_y_recrear_base_datos():
    try:
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
    except Exception as e:
        print(f"Error al eliminar archivo: {e}")
    inicializar_rifa()

def obtener_data_completa():
    inicializar_rifa()
    try:
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            if "estado_rifa" not in data:
                data["estado_rifa"] = "activa"
            if "solicitudes_pendientes" not in data:
                data["solicitudes_pendientes"] = {}
            return data
    except Exception as e:
        borrar_y_recrear_base_datos()
        with open(DB_FILE, "r") as f:
            return json.load(f)

def guardar_data_completa(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error al guardar JSON: {e}")

def generar_texto_lista():
    data = obtener_data_completa()
    rifa = data["numeros"]
    texto = "🎟️ *LISTA OFICIAL DE LA RIFA (1 al 100)* 🎟️\n\n"
    disponibles = 0
    for i in range(1, 101):
        num_str = str(i).zfill(2)
        info = rifa[str(i)]
        estado = info.get("estado", "disponible")

        if estado == "disponible":
            texto += f"🟢 *{num_str}*: Disponible\n"
            disponibles += 1
        elif estado == "pendiente":
            texto += f"🟡 *{num_str}*: En verificación de pago...\n"
        else:
            nombre = info.get("nombre", "Usuario")
            user_id = info.get("user_id")
            if user_id:
                texto += f"🔴 *{num_str}*: Ocupado por [{nombre}](tg://user?id={user_id})\n"
            else:
                texto += f"🔴 *{num_str}*: Ocupado por {nombre}\n"
            
    texto += f"\n📊 *Resumen:* Quedan {disponibles} números disponibles."
    if data.get("estado_rifa") == "finalizada":
        texto += "\n\n🔒 *ESTADO:* Rifa cerrada/finalizada."
    return texto

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    nombre = user.first_name or "Participante"
    data_rifa = obtener_data_completa()
    estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

    respuesta = (
        f"¡Hola {nombre}! Estado actual de la Rifa. ✨\n\n"
        f"{generar_texto_lista()}"
    )
    if estado_actual_rifa == "activa":
        respuesta += "\n\n👉 *¿Cómo comprar?* Envía los números que deseas separados por coma (ej: *7, 14*) aquí o en el grupo."
    
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado para resetear la rifa.")
        return
    
    borrar_y_recrear_base_datos()
    await update.message.reply_text("🔄 *¡La rifa ha sido reseteada con éxito!* Todos los números vuelven a estar disponibles.\n\n" + generar_texto_lista(), parse_mode="Markdown")

async def ganador_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado para dar el resultado ganador.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Por favor indica el número ganador. Ejemplo: `/ganador 14`", parse_mode="Markdown")
        return

    num_ingresado = context.args[0].strip()
    if not num_ingresado.isdigit() or not (1 <= int(num_ingresado) <= 100):
        await update.message.reply_text("⚠️ El número debe estar entre 1 y 100.", parse_mode="Markdown")
        return

    num_str = str(int(num_ingresado))
    data_rifa = obtener_data_completa()
    info_num = data_rifa["numeros"].get(num_str, {})

    estado = info_num.get("estado")
    if estado != "ocupado":
        await update.message.reply_text(f"⚠️ El número *{num_ingresado.zfill(2)}* no está ocupado por ningún usuario (su estado es: *{estado}*).", parse_mode="Markdown")
        return

    ganador_nombre = info_num.get("nombre")
    ganador_id = info_num.get("user_id")
    num_formateado = num_str.zfill(2)
    ganador_mencion = f"[{ganador_nombre}](tg://user?id={ganador_id})" if ganador_id else ganador_nombre

    # 1. Anuncio en el grupo
    msg_anuncio = (
        f"🏆 *¡RESULTADO OFICIAL DE LA RIFA!* 🏆\n\n"
        f"🎯 El número ganador es el: *{num_formateado}*\n\n"
        f"🎉 ¡El usuario {ganador_mencion} es el ganador de este número! Muchas felicidades. 🥳"
    )
    await update.message.reply_text(msg_anuncio, parse_mode="Markdown")

    # 2. Notificación en privado al ganador
    if ganador_id:
        try:
            msg_privado = (
                f"🎉 *¡FELICIDADES {ganador_nombre}!* 🎉\n\n"
                f"¡Has ganado la rifa con tu número *{num_formateado}*! 🏆\n\n"
                f"Por favor, ponte en contacto con el administrador lo antes posible para reclamar tu premio. 🤝"
            )
            await context.bot.send_message(chat_id=ganador_id, text=msg_privado, parse_mode="Markdown")
            print(f"Mensaje privado de ganador enviado con éxito a ID: {ganador_id}")
        except Exception as e:
            print(f"⚠️ No se pudo enviar mensaje privado al ganador: {e}")

async def bienvenida_nuevos_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for nuevo_usuario in update.message.new_chat_members:
        if nuevo_usuario.id == context.bot.id:
            continue
        
        nombre = nuevo_usuario.first_name or "Amigo"
        mencion = f"[{nombre}](tg://user?id={nuevo_usuario.id})"
        bot_username = context.bot.username
        
        texto_bienvenida = (
            f"👋 ¡Bienvenido/a {mencion} al grupo de la Rifa! 🎟️\n\n"
            f"Para participar y separar tus números:\n"
            f"1️⃣ Revisa la lista enviando o pidiendo la `lista`.\n"
            f"2️⃣ Envía por aquí los números que deseas separados por coma (ejemplo: *7, 14*).\n"
            f"3️⃣ Sigue las instrucciones para validar tu pago.\n\n"
            f"⚠️ *IMPORTANTE:* Para que el bot pueda confirmarte tus jugadas por privado, toca el botón de abajo para iniciar el chat privado con él y dale a **Iniciar**."
        )

        # Botón interactivo que lleva directo al chat privado del bot
        keyboard = [[InlineKeyboardButton("🤖 Iniciar Bot en Privado", url=f"https://t.me/{bot_username}?start=welcome")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await update.message.reply_text(texto_bienvenida, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            print(f"Error enviando bienvenida: {e}")

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    mensaje_texto = update.message.text.strip()
    comando = mensaje_texto.lower()
    user = update.effective_user
    user_id = user.id
    nombre_usuario = user.first_name or "Usuario"
    chat_id = update.effective_chat.id

    data_rifa = obtener_data_completa()
    rifa = data_rifa["numeros"]
    solicitudes = data_rifa.get("solicitudes_pendientes", {})
    estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

    if comando in ["hola", "buenas", "lista", "inicio", "rifa"]:
        await update.message.reply_text(
            f"¡Hola {nombre_usuario}! Estado actual de la Rifa:\n\n{generar_texto_lista()}",
            parse_mode="Markdown"
        )
        return

    partes = [p.strip() for p in mensaje_texto.split(",")]
    es_lista_numeros = all(p.isdigit() for p in partes) if partes else False

    if es_lista_numeros:
        if estado_actual_rifa == "finalizada":
            await update.message.reply_text("🔒 *Lo sentimos, el sistema está cerrado.*", parse_mode="Markdown")
            return

        ocupados, pendientes, validos_para_reservar, invalidos = [], [], [], []

        for p in partes:
            num_elegido = int(p)
            if 1 <= num_elegido <= 100:
                num_str = str(num_elegido)
                info = rifa[num_str]
                est = info.get("estado", "disponible")

                if est == "ocupado":
                    ocupados.append(f"*{num_str.zfill(2)}*")
                elif est == "pendiente":
                    pendientes.append(f"*{num_str.zfill(2)}*")
                else:
                    validos_para_reservar.append(num_str)
            else:
                invalidos.append(p)

        mensajes_conflicto = []
        if ocupados:
            mensajes_conflicto.append(f"🔴 El/los número(s) {', '.join(ocupados)} ya está(n) *OCUPADO(S)*.")
        if pendientes:
            mensajes_conflicto.append(f"🟡 El/los número(s) {', '.join(pendientes)} está(n) *EN PROCESO DE VERIFICACIÓN*.")
        if invalidos:
            mensajes_conflicto.append(f"⚠️ El/los número(s) {', '.join(invalidos)} está(n) fuera del rango (1 al 100).")

        if mensajes_conflicto and not validos_para_reservar:
            await update.message.reply_text(f"Hola {nombre_usuario}:\n" + "\n".join(mensajes_conflicto), parse_mode="Markdown")
            return

        if validos_para_reservar:
            req_id = "r" + str(uuid.uuid4().int)[:4]

            for n in validos_para_reservar:
                rifa[n]["estado"] = "pendiente"

            solicitudes[req_id] = {
                "nombre": nombre_usuario,
                "user_id": user_id,
                "username": user.username or "",
                "numeros": validos_para_reservar,
                "chat_origen": chat_id
            }

            data_rifa["numeros"] = rifa
            data_rifa["solicitudes_pendientes"] = solicitudes
            guardar_data_completa(data_rifa)

            nums_solicitados_txt = ", ".join([n.zfill(2) for n in validos_para_reservar])

            await update.message.reply_text(
                f"⏳ *SOLICITUD RECIBIDA* ⏳\n\nHola {nombre_usuario}, tus números (*{nums_solicitados_txt}*) están *reservados temporalmente* mientras el administrador verifica tu pago.",
                parse_mode="Markdown"
            )

            keyboard = [
                [
                    InlineKeyboardButton("🟢 Confirmar Pago", callback_data=f"conf_{req_id}"),
                    InlineKeyboardButton("🔴 Rechazar Pago", callback_data=f"rech_{req_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            txt_admin = (
                f"📥 *NUEVA SOLICITUD DE COMPRA* (ID: `{req_id}`)\n\n"
                f"👤 *Cliente:* {nombre_usuario}\n"
                f"💬 *Username:* @{user.username if user.username else 'Sin alias'}\n"
                f"🎟️ *Números:* *{nums_solicitados_txt}*\n\n"
                f"¿Deseas confirmar el pago?"
            )

            try:
                await context.bot.send_message(
                    chat_id=ADMIN_TELEGRAM_ID,
                    text=txt_admin,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Error enviando mensaje al admin: {e}")

async def boton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.edit_message_text("⛔ No estás autorizado.")
        return

    data_callback = query.data
    accion, req_id = data_callback.split("_", 1)

    data_rifa = obtener_data_completa()
    rifa = data_rifa["numeros"]
    solicitudes = data_rifa.get("solicitudes_pendientes", {})

    if req_id not in solicitudes:
        await query.edit_message_text(f"⚠️ La solicitud `{req_id}` ya fue procesada o no existe.", parse_mode="Markdown")
        return

    sol = solicitudes[req_id]
    user_nombre = sol["nombre"]
    user_id = sol["user_id"]
    user_nums = sol["numeros"]
    chat_origen = sol["chat_origen"]

    nums_formatted = ", ".join([n.zfill(2) for n in user_nums])
    usuario_mencion = f"[{user_nombre}](tg://user?id={user_id})"

    if accion == "conf":
        for n in user_nums:
            rifa[n]["estado"] = "ocupado"
            rifa[n]["nombre"] = user_nombre
            rifa[n]["user_id"] = user_id

        del solicitudes[req_id]
        data_rifa["numeros"] = rifa
        data_rifa["solicitudes_pendientes"] = solicitudes

        if all(rifa[str(n)]["estado"] == "ocupado" for n in range(1, 101)):
            data_rifa["estado_rifa"] = "finalizada"

        guardar_data_completa(data_rifa)

        await query.edit_message_text(f"✅ *Solicitud {req_id} APROBADA.* Números: {nums_formatted}", parse_mode="Markdown")

        # 1. Aviso en el grupo
        try:
            msg_grupo = f"🎉 *¡PAGO CONFIRMADO!* 🎉\n\n👤 *Usuario:* {usuario_mencion}\n🎟️ *Números asignados:* *{nums_formatted}*\n\n¡Muchas felicidades! 🤝"
            await context.bot.send_message(chat_id=chat_origen, text=msg_grupo, parse_mode="Markdown")
        except Exception as e:
            print(f"Error enviando aviso al grupo: {e}")

        # 2. Notificación en privado al usuario
        try:
            msg_privado = f"✅ *¡PAGO APROBADO!* 🎉\n\nHola {user_nombre}, tu pago ha sido verificado. Tus números (*{nums_formatted}*) ya están registrados oficialmente a tu nombre.\n\n¡Mucha suerte en la rifa! 🍀"
            await context.bot.send_message(chat_id=user_id, text=msg_privado, parse_mode="Markdown")
            print(f"Privado de aprobación enviado a ID: {user_id}")
        except Exception as e:
            print(f"⚠️ No se pudo enviar privado de aprobación: {e}")

    elif accion == "rech":
        for n in user_nums:
            rifa[n] = {"estado": "disponible", "nombre": "", "user_id": "", "username": ""}

        del solicitudes[req_id]
        data_rifa["numeros"] = rifa
        data_rifa["solicitudes_pendientes"] = solicitudes
        guardar_data_completa(data_rifa)

        await query.edit_message_text(f"❌ *Solicitud {req_id} RECHAZADA.*", parse_mode="Markdown")

        # 1. Aviso en el grupo
        try:
            msg_cancel_grupo = f"⚠️ *SOLICITUD RECHAZADA* ⚠️\n\nEl pago de {usuario_mencion} para el/los número(s) *{nums_formatted}* no pudo ser verificado. Los números vuelven a estar 🟢 *Disponibles*."
            await context.bot.send_message(chat_id=chat_origen, text=msg_cancel_grupo, parse_mode="Markdown")
        except Exception as e:
            print(f"Error notificando rechazo en grupo: {e}")

        # 2. Notificación en privado al usuario
        try:
            msg_cancel_privado = f"❌ *SOLICITUD RECHAZADA* ❌\n\nHola {user_nombre}, lamentablemente tu pago para el/los número(s) *{nums_formatted}* fue rechazado y los números han sido liberados nuevamente."
            await context.bot.send_message(chat_id=user_id, text=msg_cancel_privado, parse_mode="Markdown")
            print(f"Privado de rechazo enviado a ID: {user_id}")
        except Exception as e:
            print(f"⚠️ No se pudo enviar privado de rechazo: {e}")

async def main():
    inicializar_rifa()
    
    # 1. Levanta el servidor web para satisfacer a Render
    await start_web_server()

    # 2. Inicia el bot de Telegram
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("ganador", ganador_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida_nuevos_usuarios))
    app.add_handler(CallbackQueryHandler(boton_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))

    print("🤖 Bot de Rifa iniciado correctamente...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Mantiene el proceso activo
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())


