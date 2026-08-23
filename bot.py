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

    # 1. Anuncio en el grupo con la indicación del premio y captura de evidencia
    msg_anuncio = (
        f"🏆 *¡RESULTADO OFICIAL DE LA RIFA!* 🏆\n\n"
        f"🎯 El Resultado de la Florida Pick 3 es el: *{num_formateado}*\n\n"
        f"🎉 ¡El usuario {ganador_mencion} es el ganador de este número! Muchas felicidades. 🥳\n\n"
        f"Por favor, póngase en contacto con el administrador @yordanisr para recibir su premio. "
        f"Una vez que reciba la transferencia, le pedimos por favor que haga una captura de pantalla y la envíe a este grupo como evidencia de que recibió su pago y que todo funciona con total transparencia."
    )
    await update.message.reply_text(msg_anuncio, parse_mode="Markdown")

    # 2. Notificación en privado al ganador
    if ganador_id:
        try:
            msg_privado = (
                f"🎉 *¡FELICIDADES {ganador_nombre}!* 🎉\n\n"
                f"¡Has ganado la rifa con tu número *{num_formateado}*! 🏆\n\n"
                f"Por favor, ponte en contacto con el administrador @yordanisr para recibir tu premio. "
                f"Una vez que recibas la transferencia, haz una captura de pantalla y envíala al grupo como evidencia de que todo funciona con transparencia. 🤝"
            )
            await context.bot.send_message(chat_id=ganador_id, text=msg_privado, parse_mode="Markdown")
        except Exception as e:
            print(f"⚠️ No se pudo enviar mensaje privado al ganador: {e}")

async def bienvenida_nuevos_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for nuevo_usuario in update.message.new_chat_members:
        if nuevo_usuario.id == context.bot.id:
            continue
        
        nombre = nuevo_usuario.first_name or "Amigo"
        mencion = f"[{nombre}](tg://user?id={nuevo_usuario.id})"
        
        texto_bienvenida = (
            f"👋 ¡Bienvenido/a {mencion} al grupo de la Rifa! 🎟️\n\n"
            f"Para participar y separar tus números:\n"
            f"1️⃣ Revisa la lista enviando la palabra: `lista`.\n"
            f"2️⃣ Envía por aquí los números que deseas separados por coma (ejemplo: *7, 14*).\n"
            f"3️⃣ Sigue las instrucciones para validar tu pago.\n\n"
            f"🎲 *¿Cómo se elige el ganador?*\n"
            f"Una vez que se ocupen los 100 números de la lista, el número ganador se escogerá del resultado de la **Florida del Pick 3** al día siguiente en la noche. ¡Mucha suerte! 🍀"
        )
        
        try:
            await update.message.reply_text(texto_bienvenida, parse_mode="Markdown")
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
                f"⏳ *SOLICITUD EN PROCESO* ⏳\n\n"
                f"Hola {nombre_usuario}, tus números (*{nums_solicitados_txt}*) están *reservados temporalmente*.\n\n"
                f"Por favor, póngase en contacto con el administrador @yordanisr para realizar la transferencia.",
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







































Codigo modificado Gran Sorteo 100


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
       f"¡Hola {nombre}! Estado actual de Gran Sorteo 100. ✨\n\n"
       f"{generar_texto_lista()}"
   )
   if estado_actual_rifa == "activa":
       respuesta += "\n\n👉 *¿Cómo comprar?* Envía los números que deseas separados por coma (ej: *7, 14*) aquí o en el grupo."
    
   await update.message.reply_text(respuesta, parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   user = update.effective_user
   if user.id != ADMIN_TELEGRAM_ID:
       await update.message.reply_text("⛔ No estás autorizado para resetear el sorteo.")
       return
    
   borrar_y_recrear_base_datos()
   await update.message.reply_text("🔄 *¡Gran Sorteo 100 ha sido reseteado con éxito!* Todos los números vuelven a estar disponibles.\n\n" + generar_texto_lista(), parse_mode="Markdown")

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

   msg_anuncio = (
       f"🏆 *¡RESULTADO OFICIAL DE GRAN SORTEO 100!* 🏆\n\n"
       f"🎯 El Resultado de la Florida Pick 3 es el: *{num_formateado}*\n\n"
       f"🎉 ¡El usuario {ganador_mencion} es el ganador de este número! Muchas felicidades. 🥳\n\n"
       f"Por favor, póngase en contacto con el administrador @yordanisr para recibir su premio. "
       f"Una vez que reciba la transferencia, le pedimos por favor que haga una captura de pantalla y la envíe a este grupo como evidencia de que recibió su pago y que todo funciona con total transparencia."
   )
   await update.message.reply_text(msg_anuncio, parse_mode="Markdown")

   if ganador_id:
       try:
           msg_privado = (
               f"🎉 *¡FELICIDADES {ganador_nombre}!* 🎉\n\n"
               f"¡Has ganado Gran Sorteo 100 con tu número *{num_formateado}*! 🏆\n\n"
               f"Por favor, ponte en contacto con el administrador @yordanisr para recibir tu premio. "
               f"Una vez que recibas la transferencia, haz una captura de pantalla y envíala al grupo como evidencia de que todo funciona con transparencia. 🤝"
           )
           await context.bot.send_message(chat_id=ganador_id, text=msg_privado, parse_mode="Markdown")
       except Exception as e:
           print(f"⚠️ No se pudo enviar mensaje privado al ganador: {e}")

async def bienvenida_nuevos_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
   for nuevo_usuario in update.message.new_chat_members:
       if nuevo_usuario.id == context.bot.id:
           continue
       
       nombre = nuevo_usuario.first_name or "Amigo"
       mencion = f"[{nombre}](tg://user?id={nuevo_usuario.id})"
       
       texto_bienvenida = (
           f"🎯 *¡Bienvenido/a {mencion} a Gran Sorteo 100!* 🎟️\n\n"
           f"Nos alegra mucho tenerte por aquí. Este es un espacio exclusivo y transparente para participar por grandes premios en efectivo.\n\n"
           f"A continuación, te explicamos cómo funciona la dinámica:\n"
           f"* 🔢 *Los Números:* Disponemos de una tabla con *100 números* (del 00 al 99). Cada participante puede elegir uno o varios números según su preferencia.\n"
           f"* 💵 *Valor:* Cada número tiene un costo de *10 a 20 reales*.\n"
           f"* 🏆 *El Premio:* Una vez que se ocupen los 100 números y se confirme el pago de todos, anunciaremos la fecha oficial del evento. El premio en efectivo va desde *400 hasta 1,000 reales*.\n"
           f"* ⚖️ *Transparencia Total:* El ganador se define de manera 100% transparente utilizando los resultados oficiales de la *Lotería de Florida*.\n\n"
           f"📌 *Reglas del Grupo:*\n"
           f"1. Respetar a todos los miembros de la comunidad.\n"
           f"2. Para apartar un número, consulta los disponibles enviando la palabra `lista` y envía tu comprobante de pago.\n"
           f"3. Los números solo se consideran vendidos y asegurados una vez confirmado el pago.\n\n"
           f"¡Mucha suerte y gracias por formar parte de *Gran Sorteo 100*! 🍀✨"
       )
       
       try:
           await update.message.reply_text(texto_bienvenida, parse_mode="Markdown")
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

   if comando in ["hola", "buenas", "lista", "inicio", "rifa", "sorteo"]:
       await update.message.reply_text(
           f"¡Hola {nombre_usuario}! Estado actual de Gran Sorteo 100:\n\n{generar_texto_lista()}",
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
               f"⏳ *SOLICITUD EN PROCESO* ⏳\n\n"
               f"Hola {nombre_usuario}, tus números (*{nums_solicitados_txt}*) están *reservados temporalmente*.\n\n"
               f"Por favor, póngase en contacto con el administrador @yordanisr para realizar la transferencia.",
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

       try:
           msg_grupo = f"🎉 *¡PAGO CONFIRMADO!* 🎉\n\n👤 *Usuario:* {usuario_mencion}\n🎟️ *Números asignados:* *{nums_formatted}*\n\n¡Muchas felicidades! 🤝"
           await context.bot.send_message(chat_id=chat_origen, text=msg_grupo, parse_mode="Markdown")
       except Exception as e:
           print(f"Error enviando aviso al grupo: {e}")

       try:
           msg_privado = f"✅ *¡PAGO APROBADO!* 🎉\n\nHola {user_nombre}, tu pago ha sido verificado. Tus números (*{nums_formatted}*) ya están registrados oficialmente a tu nombre en Gran Sorteo 100.\n\n¡Mucha suerte! 🍀"
           await context.bot.send_message(chat_id=user_id, text=msg_privado, parse_mode="Markdown")
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

       try:
           msg_cancel_grupo = f"⚠️ *SOLICITUD RECHAZADA* ⚠️\n\nEl pago de {usuario_mencion} para el/los número(s) *{nums_formatted}* no pudo ser verificado. Los números vuelven a estar 🟢 *Disponibles*."
           await context.bot.send_message(chat_id=chat_origen, text=msg_cancel_grupo, parse_mode="Markdown")
       except Exception as e:
           print(f"Error notificando rechazo en grupo: {e}")

       try:
           msg_cancel_privado = f"❌ *SOLICITUD RECHAZADA* ❌\n\nHola {user_nombre}, lamentablemente tu pago para el/los número(s) *{nums_formatted}* fue rechazado y los números han sido liberados nuevamente."
           await context.bot.send_message(chat_id=user_id, text=msg_cancel_privado, parse_mode="Markdown")
       except Exception as e:
           print(f"⚠️ No se pudo enviar privado de rechazo: {e}")

async def main():
   inicializar_rifa()
    
   await start_web_server()

   app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

   app.add_handler(CommandHandler("start", start_command))
   app.add_handler(CommandHandler("reset", reset_command))
   app.add_handler(CommandHandler("ganador", ganador_command))
   app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida_nuevos_usuarios))
   app.add_handler(CallbackQueryHandler(boton_callback))
   app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))

   print("🤖 Bot de Gran Sorteo 100 iniciado correctamente...")
   await app.initialize()
   await app.start()
   await app.updater.start_polling()

   await asyncio.Event().wait()

if __name__ == "__main__":
   asyncio.run(main())




nuevo ok


import os
import json
import uuid
import asyncio
from datetime import datetime
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
   estado_actual = data.get("estado_rifa")
   if estado_actual == "finalizada":
       texto += "\n\n🔒 *ESTADO:* Rifa cerrada/finalizada."
   elif estado_actual == "bloqueada":
       texto += "\n\n⛔ *ESTADO:* Rifa temporalmente bloqueada por el administrador."
   return texto

TEXTO_REGLAS_OFICIAL = (
   "📌 *REGLAS Y DINÁMICA DEL GRUPO (Gran Sorteo 100):*\n\n"
   "1️⃣ *Respeto:* Mantén un ambiente de respeto absoluto hacia todos los miembros de la comunidad y administradores.\n"
   "2️⃣ *Números y Costo:* Disponemos de una tabla con *100 números* (del 01 al 100). Cada número tiene un valor de *20 reales*. Envía la palabra `lista` para ver los disponibles y escribe los que deseas separados por coma (ej: *7, 14*) aquí o en el grupo.\n"
   "3️⃣ *Condición del Sorteo:* El sorteo se realizará **únicamente cuando los 100 números estén 100% ocupados y pagados**. Esto puede demorar varios días dependiendo de la rapidez en que los usuarios escojan y ocupen los números.\n"
   "4️⃣ *Garantía de Devolución:* Si algún participante adquiere sus números pero **no desea esperar**, puede ponerse en contacto con el administrador (@yordanisr) en cualquier momento para solicitar la **devolución íntegra de su dinero**.\n"
   "5️⃣ *Entrega del Premio:* El usuario ganador recibirá un premio de *1000 reales*. Este pago se efectuará según prefiera el ganador: mediante transferencia vía PIX, o si lo prefiere, se le hará entrega en Cuba a su familiar según el tipo de cambio vigente en el grupo de Remesas del administrador. (Para conocer esta información y tasa de cambio, es obligatorio unirse al grupo de WhatsApp del administrador: https://chat.whatsapp.com/HEaEIKaEjksJRrWEKcIVEo?s=sh&p=a&ilr=0).\n"
   "6️⃣ *Transparencia:* El ganador se define utilizando los resultados oficiales de la *Lotería de Florida* (Pick 3) en el horario nocturno.\n\n"
   "🤝 *¡Ayúdanos a crecer!* Puedes invitar a otros usuarios a unirse al grupo mediante este enlace: https://t.me/+didZDftOZAhmZjdh para que participen en el sorteo."
)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   user = update.effective_user
   nombre = user.first_name or "Participante"
   data_rifa = obtener_data_completa()
   estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

   respuesta = (
       f"¡Hola {nombre}! Estado actual de Gran Sorteo 100. ✨\n\n"
       f"{generar_texto_lista()}"
   )
   if estado_actual_rifa == "activa":
       respuesta += "\n\n👉 *¿Cómo comprar?* Envía los números que deseas separados por coma (ej: *7, 14*) aquí o en el grupo."
    
   await update.message.reply_text(respuesta, parse_mode="Markdown")

async def reglas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   mensaje_reglas = f"🎯 *REGLAS OFICIALES - Gran Sorteo 100* 🎟️\n\n{TEXTO_REGLAS_OFICIAL}"
   await update.message.reply_text(mensaje_reglas, parse_mode="Markdown")

async def bloquear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   user = update.effective_user
   if user.id != ADMIN_TELEGRAM_ID:
       await update.message.reply_text("⛔ No estás autorizado para bloquear la rifa.")
       return

   data_rifa = obtener_data_completa()
   data_rifa["estado_rifa"] = "bloqueada"
   guardar_data_completa(data_rifa)

   await update.message.reply_text("⛔ *La rifa ha sido bloqueada temporalmente.* Ningún usuario podrá escoger números nuevos hasta que el administrador la desbloquee. (Los datos existentes están seguros).", parse_mode="Markdown")

async def desbloquear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   user = update.effective_user
   if user.id != ADMIN_TELEGRAM_ID:
       await update.message.reply_text("⛔ No estás autorizado para desbloquear la rifa.")
       return

   data_rifa = obtener_data_completa()
   if data_rifa.get("estado_rifa") == "finalizada":
       await update.message.reply_text("⚠️ La rifa se encuentra finalizada por un sorteo completado. Si deseas reiniciar todo desde cero, usa `/reset`.", parse_mode="Markdown")
       return

   data_rifa["estado_rifa"] = "activa"
   guardar_data_completa(data_rifa)

   await update.message.reply_text("🟢 *La rifa ha sido desbloqueada.* Ya se pueden escoger números nuevamente con total normalidad.\n\n" + generar_texto_lista(), parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   user = update.effective_user
   if user.id != ADMIN_TELEGRAM_ID:
       await update.message.reply_text("⛔ No estás autorizado para resetear el sorteo.")
       return
    
   borrar_y_recrear_base_datos()
   await update.message.reply_text("🔄 *¡Gran Sorteo 100 ha sido reseteado con éxito!* Todos los números vuelven a estar disponibles.\n\n" + generar_texto_lista(), parse_mode="Markdown")

async def liberar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   user = update.effective_user
   if user.id != ADMIN_TELEGRAM_ID:
       await update.message.reply_text("⛔ No estás autorizado para liberar números.")
       return

   if not context.args:
       await update.message.reply_text("⚠️ Por favor indica el nombre o parte del nombre del usuario a liberar. Ejemplo: `/liberar Juan`", parse_mode="Markdown")
       return

   nombre_buscar = " ".join(context.args).strip().lower()
   data_rifa = obtener_data_completa()
   rifa = data_rifa["numeros"]

   numeros_liberados = []
   for num_str, info in rifa.items():
       if info.get("estado") == "ocupado":
           nombre_usuario_reg = info.get("nombre", "").lower()
           if nombre_buscar in nombre_usuario_reg:
               numeros_liberados.append(num_str.zfill(2))
               rifa[num_str] = {"estado": "disponible", "nombre": "", "user_id": "", "username": ""}

   if not numeros_liberados:
       await update.message.reply_text(f"⚠️ No se encontraron números ocupados asociados a un usuario que coincida con: *{nombre_buscar}*.", parse_mode="Markdown")
       return

   if data_rifa.get("estado_rifa") == "finalizada":
       data_rifa["estado_rifa"] = "activa"

   data_rifa["numeros"] = rifa
   guardar_data_completa(data_rifa)

   nums_str_lib = ", ".join(numeros_liberados)
   msg_liberar = (
       f"🔄 *DEVOLUCIÓN Y LIBERACIÓN DE NÚMEROS* 🔄\n\n"
       f"Se han procesado las devoluciones correspondientes. El/los número(s) *{nums_str_lib}* (del usuario *{nombre_buscar.capitalize()}*) han sido liberados y vuelven a estar 🟢 *Disponibles* en la lista."
   )
   await update.message.reply_text(msg_liberar, parse_mode="Markdown")

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

   data_rifa["estado_rifa"] = "finalizada"
   guardar_data_completa(data_rifa)

   msg_resultado = (
       f"🎯 *¡RESULTADO OFICIAL DE LA LOTERÍA!* 🎯\n\n"
       f"El número ganador de la Florida Pick 3 es el: *{num_formateado}*"
   )
   await update.message.reply_text(msg_resultado, parse_mode="Markdown")

   msg_ganador = (
       f"🎉 *¡Felicidades al Ganador!* 🎉\n\n"
       f"El usuario {ganador_mencion} ha ganado con el número *{num_formateado}* un premio de *1000 reales*. ¡Muchas felicidades! 🥳\n\n"
       f"Por favor, póngase en contacto con el administrador @yordanisr para recibir su premio (puede elegir que se le transfiera vía PIX o que se le envíe a su familiar en Cuba). "
       f"Una vez que reciba la transferencia, le pedimos por favor que haga una captura de pantalla y la envíe a este grupo como evidencia de que recibió su pago y que todo funciona con total transparencia."
   )
   await update.message.reply_text(msg_ganador, parse_mode="Markdown")

   if ganador_id:
       try:
           msg_privado = (
               f"🎉 *¡FELICIDADES {ganador_nombre}!* 🎉\n\n"
               f"¡Has ganado Gran Sorteo 100 con tu número *{num_formateado}* llevándote un premio de *1000 reales*! 🏆\n\n"
               f"Por favor, ponte en contacto con el administrador @yordanisr para recibir tu premio (puedes elegir transferencia vía PIX o envío a tu familiar en Cuba). "
               f"Una vez que recibas la transferencia, haz una captura de pantalla y envíala al grupo como evidencia de que todo funciona con transparencia. 🤝"
           )
           await context.bot.send_message(chat_id=ganador_id, text=msg_privado, parse_mode="Markdown")
       except Exception as e:
           print(f"⚠️ No se pudo enviar mensaje privado al ganador: {e}")

async def bienvenida_nuevos_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
   for nuevo_usuario in update.message.new_chat_members:
       if nuevo_usuario.id == context.bot.id:
           continue
       
       nombre = nuevo_usuario.first_name or "Amigo"
       mencion = f"[{nombre}](tg://user?id={nuevo_usuario.id})"
       
       texto_bienvenida = (
           f"🎯 *¡Bienvenido/a {mencion} a Gran Sorteo 100!* 🎟️\n\n"
           f"Nos alegra mucho tenerte por aquí. Este es un espacio exclusivo y transparente para participar por grandes premios en efectivo.\n\n"
           f"{TEXTO_REGLAS_OFICIAL}\n\n"
           f"¡Mucha suerte y gracias por formar parte de *Gran Sorteo 100*! 🍀✨"
       )
       
       try:
           await update.message.reply_text(texto_bienvenida, parse_mode="Markdown")
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

   if comando in ["hola", "buenas", "lista", "inicio", "rifa", "sorteo"]:
       await update.message.reply_text(
           f"¡Hola {nombre_usuario}! Estado actual de Gran Sorteo 100:\n\n{generar_texto_lista()}",
           parse_mode="Markdown"
       )
       return

   partes = [p.strip() for p in mensaje_texto.split(",")]
   es_lista_numeros = all(p.isdigit() for p in partes) if partes else False

   if es_lista_numeros:
       if estado_actual_rifa == "finalizada":
           await update.message.reply_text(
               f"🔒 *Lo sentimos {nombre_usuario}, la lista está cerrada.* "
               f"No se permite escoger números hasta que el administrador dé los resultados y proceda a resetear la lista.",
               parse_mode="Markdown"
           )
           return
       
       if estado_actual_rifa == "bloqueada":
           await update.message.reply_text(
               f"⛔ *Lo sentimos {nombre_usuario}, la rifa se encuentra temporalmente bloqueada.* "
               f"En este momento no se pueden apartar números hasta que el administrador la habilite nuevamente.",
               parse_mode="Markdown"
           )
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
               f"⏳ *SOLICITUD EN PROCESO* ⏳\n\n"
               f"Hola {nombre_usuario}, tus números (*{nums_solicitados_txt}*) están *reservados temporalmente*.\n\n"
               f"Por favor, póngase en contacto con el administrador @yordanisr para realizar la transferencia de los 20 reales por número.",
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

       lista_completa = all(rifa[str(n)]["estado"] == "ocupado" for n in range(1, 101))
       if lista_completa:
           data_rifa["estado_rifa"] = "finalizada"

       guardar_data_completa(data_rifa)

       await query.edit_message_text(f"✅ *Solicitud {req_id} APROBADA.* Números: {nums_formatted}", parse_mode="Markdown")

       try:
           msg_grupo = f"🎉 *¡PAGO CONFIRMADO!* 🎉\n\n👤 *Usuario:* {usuario_mencion}\n🎟️ *Números asignados:* *{nums_formatted}*\n\n¡Muchas felicidades! 🤝"
           await context.bot.send_message(chat_id=chat_origen, text=msg_grupo, parse_mode="Markdown")
       except Exception as e:
           print(f"Error enviando aviso al grupo: {e}")

       if lista_completa:
           hora_actual = datetime.now().hour
           if hora_actual < 21:
               aviso_tiempo = "será **esta misma noche** en el tiro de la Florida"
           else:
               aviso_tiempo = "será **al día siguiente** en el tiro de la Florida de la noche"

           msg_lista_llena = (
               f"🚨 *¡ATENCIÓN COMUNIDAD!* 🚨\n\n"
               f"🎟️ ¡Se han ocupado todos los números de la lista!\n"
               f"🔒 La lista ha sido **bloqueada automáticamente** y permanecerá cerrada hasta que se dé el resultado y el administrador proceda a resetearla.\n\n"
               f"🎯 El resultado {aviso_tiempo}. ¡Estén atentos! 🍀"
           )
           try:
               await context.bot.send_message(chat_id=chat_origen, text=msg_lista_llena, parse_mode="Markdown")
           except Exception as e:
               print(f"Error enviando aviso de lista llena: {e}")

       try:
           msg_privado = f"✅ *¡PAGO APROBADO!* 🎉\n\nHola {user_nombre}, tu pago ha sido verificado. Tus números (*{nums_formatted}*) ya están registrados oficialmente a tu nombre en Gran Sorteo 100.\n\n¡Mucha suerte! 🍀"
           await context.bot.send_message(chat_id=user_id, text=msg_privado, parse_mode="Markdown")
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

       try:
           msg_cancel_grupo = f"⚠️ *SOLICITUD RECHAZADA* ⚠️\n\nEl pago de {usuario_mencion} para el/los número(s) *{nums_formatted}* no pudo ser verificado. Los números vuelven a estar 🟢 *Disponibles*."
           await context.bot.send_message(chat_id=chat_origen, text=msg_cancel_grupo, parse_mode="Markdown")
       except Exception as e:
           print(f"Error notificando rechazo en grupo: {e}")

       try:
           msg_cancel_privado = f"❌ *SOLICITUD RECHAZADA* ❌\n\nHola {user_nombre}, lamentablemente tu pago para el/los número(s) *{nums_formatted}* fue rechazado y los números han sido liberados nuevamente."
           await context.bot.send_message(chat_id=user_id, text=msg_cancel_privado, parse_mode="Markdown")
       except Exception as e:
           print(f"⚠️ No se pudo enviar privado de rechazo: {e}")

async def main():
   inicializar_rifa()
    
   await start_web_server()

   app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

   app.add_handler(CommandHandler("start", start_command))
   app.add_handler(CommandHandler("reglas", reglas_command))
   app.add_handler(CommandHandler("bloquear", bloquear_command))
   app.add_handler(CommandHandler("desbloquear", desbloquear_command))
   app.add_handler(CommandHandler("reset", reset_command))
   app.add_handler(CommandHandler("ganador", ganador_command))
   app.add_handler(CommandHandler("liberar", liberar_command))
   app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida_nuevos_usuarios))
   app.add_handler(CallbackQueryHandler(boton_callback))
   app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))

   print("🤖 Bot de Gran Sorteo 100 iniciado correctamente...")
   await app.initialize()
   await app.start()
   await app.updater.start_polling()

   await asyncio.Event().wait()

if __name__ == "__main__":
   asyncio.run(main())







Codigo nuevo ok con promocion


import os
import json
import uuid
import asyncio
from datetime import datetime
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

# --- VALOR DE CADA NÚMERO Y CÁLCULO AUTOMÁTICO DEL PREMIO (55%) ---
VALOR_POR_NUMERO = 10

def calcular_premio_total():
    recaudacion_total = 100 * VALOR_POR_NUMERO
    premio = recaudacion_total * 0.55
    # Si el resultado es entero, lo mostramos sin decimales molestos
    if premio.is_integer():
        return int(premio)
    return round(premio, 2)
# -----------------------------------------------------------------

def calcular_precio_total(cantidad, usuario_ya_tiene_compras=False):
    if cantidad <= 0:
        return 0
    
    if usuario_ya_tiene_compras:
        return cantidad * VALOR_POR_NUMERO

    total = 0
    restantes = cantidad

    # Paquete de 5 números con descuento promocional proporcional
    p5 = int(VALOR_POR_NUMERO * 4)
    p4 = int(VALOR_POR_NUMERO * 3.5)
    p3 = int(VALOR_POR_NUMERO * 2.5)
    p2 = int(VALOR_POR_NUMERO * 1.5)
    p1 = VALOR_POR_NUMERO

    if restantes >= 5:
        total += p5
        restantes -= 5
    else:
        if restantes == 4:
            return p4
        elif restantes == 3:
            return p3
        elif restantes == 2:
            return p2
        elif restantes == 1:
            return p1

    if restantes > 0:
        total += restantes * VALOR_POR_NUMERO

    return total

def usuario_tiene_jugada_previa(user_id, data_completa):
    rifa = data_completa.get("numeros", {})
    solicitudes = data_completa.get("solicitudes_pendientes", {})
    
    for num_str, info in rifa.items():
        if info.get("user_id") == user_id and info.get("estado") in ["ocupado", "pendiente"]:
            return True
            
    for req_id, sol in solicitudes.items():
        if sol.get("user_id") == user_id:
            return True
            
    return False

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
    estado_actual = data.get("estado_rifa")
    if estado_actual == "finalizada":
        texto += "\n\n🔒 *ESTADO:* Rifa cerrada/finalizada."
    elif estado_actual == "bloqueada":
        texto += "\n\n⛔ *ESTADO:* Rifa temporalmente bloqueada por el administrador."
    return texto

def obtener_texto_reglas():
    premio_actual = calcular_premio_total()
    return (
        "📌 *REGLAS Y DINÁMICA DEL GRUPO (Gran Sorteo 100):*\n\n"
        "1️⃣ *Respeto:* Mantén un ambiente de respeto absoluto hacia todos los miembros de la comunidad y administradores.\n"
        "2️⃣ *Números y Promoción:* Disponemos de 100 números (del 01 al 100).\n"
        f"✨ *Valores para tu primera jugada (Promoción):*\n"
        f"• 1 número = *{VALOR_POR_NUMERO} reales*\n"
        f"• 2 números = *{int(VALOR_POR_NUMERO * 1.5)} reales*\n"
        f"• 3 números = *{int(VALOR_POR_NUMERO * 2.5)} reales*\n"
        f"• 4 números = *{int(VALOR_POR_NUMERO * 3.5)} reales*\n"
        f"• 5 números = *{int(VALOR_POR_NUMERO * 4)} reales*\n"
        f"*(Si pides más de 5 números en tu primera jugada, los primeros 5 tienen precio promocional y a partir del 6to número cada uno cuesta exactamente {VALOR_POR_NUMERO} reales).* \n\n"
        f"⚠️ *¡Atención a las jugadas adicionales!* La promoción de paquetes aplica **únicamente para la primera jugada** de cada usuario. A partir de tu segunda jugada (incluso si mandas 2, 3, 4 o más números), **cada número tiene un costo fijo de {VALOR_POR_NUMERO} reales**, ya que no entra en la promoción.\n\n"
        "Envía la palabra `lista` para ver los disponibles y escribe los que deseas separados por coma (ej: *7, 14*) aquí o en el grupo.\n"
        "3️⃣ *Condición del Sorteo:* El sorteo se realizará **únicamente cuando los 100 números estén 100% ocupados y pagados**.\n"
        "4️⃣ *Garantía de Devolución:* Si algún participante adquiere sus números pero **no desea esperar**, puede solicitar la **devolución íntegra de su dinero** con el administrador (@yordanisr).\n"
        f"5️⃣ *Entrega del Premio:* El usuario ganador recibirá un premio de *{premio_actual} reales* (vía PIX o en Cuba en CUP a su familiar mediante la tasa del grupo de remesas). Grupo de WhatsApp obligatorio para tasa: https://chat.whatsapp.com/HEaEIKaEjksJRrWEKcIVEo?s=sh&p=a&ilr=0.\n"
        "6️⃣ *Transparencia:* El ganador se define utilizando los resultados oficiales de la *Lotería de Florida* (Pick 3) en el horario nocturno.\n\n"
        "🤝 *¡Ayúdanos a crecer!* Enlace de invitación al grupo: https://t.me/+didZDftOZAhmZjdh"
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    nombre = user.first_name or "Participante"
    mencion = f"[{nombre}](tg://user?id={user.id})"
    data_rifa = obtener_data_completa()
    estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

    respuesta = (
        f"¡Hola {mencion}! Estado actual de Gran Sorteo 100. ✨\n\n"
        f"{generar_texto_lista()}"
    )
    if estado_actual_rifa == "activa":
        respuesta += "\n\n👉 *¿Cómo comprar?* Envía los números que deseas separados por coma (ej: *7, 14*) aquí o en el grupo."
     
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def reglas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje_reglas = f"🎯 *REGLAS OFICIALES - Gran Sorteo 100* 🎟️\n\n{obtener_texto_reglas()}"
    await update.message.reply_text(mensaje_reglas, parse_mode="Markdown")

async def bloquear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado para bloquear la rifa.")
        return

    data_rifa = obtener_data_completa()
    data_rifa["estado_rifa"] = "bloqueada"
    guardar_data_completa(data_rifa)

    await update.message.reply_text("⛔ *La rifa ha sido bloqueada temporalmente.*", parse_mode="Markdown")

async def desbloquear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado para desbloquear la rifa.")
        return

    data_rifa = obtener_data_completa()
    if data_rifa.get("estado_rifa") == "finalizada":
        await update.message.reply_text("⚠️ La rifa se encuentra finalizada. Usa `/reset` para reiniciar.", parse_mode="Markdown")
        return

    data_rifa["estado_rifa"] = "activa"
    guardar_data_completa(data_rifa)

    await update.message.reply_text("🟢 *La rifa ha sido desbloqueada.*\n\n" + generar_texto_lista(), parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado para resetear el sorteo.")
        return
     
    borrar_y_recrear_base_datos()
    await update.message.reply_text("🔄 *¡Gran Sorteo 100 ha sido reseteado con éxito!*\n\n" + generar_texto_lista(), parse_mode="Markdown")

async def liberar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado para liberar números.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Por favor indica el nombre del usuario a liberar. Ejemplo: `/liberar Juan`", parse_mode="Markdown")
        return

    nombre_buscar = " ".join(context.args).strip().lower()
    data_rifa = obtener_data_completa()
    rifa = data_rifa["numeros"]

    numeros_liberados = []
    for num_str, info in rifa.items():
        if info.get("estado") == "ocupado":
            nombre_usuario_reg = info.get("nombre", "").lower()
            if nombre_buscar in nombre_usuario_reg:
                numeros_liberados.append(num_str.zfill(2))
                rifa[num_str] = {"estado": "disponible", "nombre": "", "user_id": "", "username": ""}

    if not numeros_liberados:
        await update.message.reply_text(f"⚠️ No se encontraron números ocupados para: *{nombre_buscar}*.", parse_mode="Markdown")
        return

    if data_rifa.get("estado_rifa") == "finalizada":
        data_rifa["estado_rifa"] = "activa"

    data_rifa["numeros"] = rifa
    guardar_data_completa(data_rifa)

    nums_str_lib = ", ".join(numeros_liberados)
    await update.message.reply_text(f"🔄 Números *{nums_str_lib}* liberados con éxito.", parse_mode="Markdown")

async def ganador_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Indica el número ganador. Ejemplo: `/ganador 14`", parse_mode="Markdown")
        return

    num_ingresado = context.args[0].strip()
    if not num_ingresado.isdigit() or not (1 <= int(num_ingresado) <= 100):
        await update.message.reply_text("⚠️ El número debe estar entre 1 y 100.", parse_mode="Markdown")
        return

    num_str = str(int(num_ingresado))
    data_rifa = obtener_data_completa()
    info_num = data_rifa["numeros"].get(num_str, {})

    if info_num.get("estado") != "ocupado":
        await update.message.reply_text(f"⚠️ El número *{num_ingresado.zfill(2)}* no está ocupado.", parse_mode="Markdown")
        return

    ganador_nombre = info_num.get("nombre")
    ganador_id = info_num.get("user_id")
    num_formateado = num_str.zfill(2)
    
    ganador_mencion = f"[{ganador_nombre}](tg://user?id={ganador_id})" if ganador_id else ganador_nombre
    premio_actual = calcular_premio_total()

    data_rifa["estado_rifa"] = "finalizada"
    guardar_data_completa(data_rifa)

    await update.message.reply_text(
        f"🎯 *¡RESULTADO OFICIAL DE LA LOTERÍA!* 🎯\n\n"
        f"El número ganador de la Florida Pick 3 es el: *{num_formateado}*",
        parse_mode="Markdown"
    )

    await update.message.reply_text(
        f"🎉 *¡Felicidades al Ganador!* 🎉\n\n"
        f"El usuario {ganador_mencion} ha ganado con el número {num_formateado} un premio de {premio_actual} reales. ¡Muchas felicidades! 🥳\n\n"
        f"Por favor, póngase en contacto con el administrador @yordanisr para recibir su premio (puede elegir que se le transfiera vía PIX o que se le envíe a su familiar en Cuba). Una vez que reciba la transferencia, le pedimos por favor que haga una captura de pantalla y la envíe a este grupo como evidencia de que recibió su pago y que todo funciona con total transparencia.",
        parse_mode="Markdown"
    )

async def bienvenida_nuevos_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for nuevo_usuario in update.message.new_chat_members:
        if nuevo_usuario.id == context.bot.id:
            continue
        nombre = nuevo_usuario.first_name or "Amigo"
        mencion = f"[{nombre}](tg://user?id={nuevo_usuario.id})"
        texto_bienvenida = f"🎯 *¡Bienvenido/a {mencion} a Gran Sorteo 100!* 🎟️\n\n{obtener_texto_reglas()}"
        try:
            await update.message.reply_text(texto_bienvenida, parse_mode="Markdown")
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
    
    usuario_mencion = f"[{nombre_usuario}](tg://user?id={user_id})"
    chat_id = update.effective_chat.id

    data_rifa = obtener_data_completa()
    rifa = data_rifa["numeros"]
    solicitudes = data_rifa.get("solicitudes_pendientes", {})
    estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

    if comando in ["hola", "buenas", "lista", "inicio", "rifa", "sorteo"]:
        await update.message.reply_text(
            f"¡Hola {usuario_mencion}! Estado actual de Gran Sorteo 100:\n\n{generar_texto_lista()}",
            parse_mode="Markdown"
        )
        return

    partes = [p.strip() for p in mensaje_texto.split(",")]
    es_lista_numeros = all(p.isdigit() for p in partes) if partes else False

    if es_lista_numeros:
        if estado_actual_rifa in ["finalizada", "bloqueada"]:
            await update.message.reply_text(f"⛔ Lo sentimos, la lista se encuentra cerrada o bloqueada en este momento.", parse_mode="Markdown")
            return

        ocupados, pendientes, validos_para_reservar, invalidos = [], [], [], []

        for p in partes:
            num_elegido = int(p)
            if 1 <= num_elegido <= 100:
                num_str = str(num_elegido)
                est = rifa[num_str].get("estado", "disponible")
                if est == "ocupado":
                    ocupados.append(f"*{num_str.zfill(2)}*")
                elif est == "pendiente":
                    pendientes.append(f"*{num_str.zfill(2)}*")
                else:
                    validos_para_reservar.append(num_str)
            else:
                invalidos.append(p)

        if validos_para_reservar:
            ya_tiene_compras = usuario_tiene_jugada_previa(user_id, data_rifa)

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
            cantidad_numeros = len(validos_para_reservar)
            
            total_a_pagar = calcular_precio_total(cantidad_numeros, usuario_ya_tiene_compras=ya_tiene_compras)

            if ya_tiene_compras:
                aviso_promocion = f"\n⚠️ *Aviso importante:* Como ya tienes una jugada previa registrada, esta nueva jugada de {cantidad_numeros} número(s) **no aplica para la promoción** y se cobra a precio estándar (*{VALOR_POR_NUMERO} reales cada número*).\n"
            else:
                aviso_promocion = f"\n✨ *¡Primera jugada detectada!* Aplica la tarifa promocional para tus {cantidad_numeros} número(s).\n"

            await update.message.reply_text(
                f"⏳ *SOLICITUD EN PROCESO* ⏳\n\n"
                f"Hola {usuario_mencion}, tus números (*{nums_solicitados_txt}*) están reservados temporalmente.\n"
                f"{aviso_promocion}"
                f"💰 Cantidad: *{cantidad_numeros}*\n"
                f"💵 Total a transferir: *{total_a_pagar} reales*\n\n"
                f"Contacta al administrador @yordanisr para pagar.",
                parse_mode="Markdown"
            )

            keyboard = [[
                InlineKeyboardButton("🟢 Confirmar Pago", callback_data=f"conf_{req_id}"),
                InlineKeyboardButton("🔴 Rechazar Pago", callback_data=f"rech_{req_id}")
            ]]
            
            txt_admin = (
                f"📥 *NUEVA SOLICITUD* (ID: `{req_id}`)\n\n"
                f"👤 *Cliente:* {usuario_mencion} {'*(Jugada Posterior - Precio Normal)*' if ya_tiene_compras else '*(1era Jugada - Promoción)*'}\n"
                f"🎟️ *Números:* *{nums_solicitados_txt}*\n"
                f"💵 *Total:* *{total_a_pagar} reales* ({cantidad_numeros} núm.)"
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_TELEGRAM_ID,
                    text=txt_admin,
                    reply_markup=InlineKeyboardMarkup(keyboard),
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
        await query.edit_message_text(f"⚠️ La solicitud `{req_id}` ya fue procesada.", parse_mode="Markdown")
        return

    sol = solicitudes[req_id]
    user_nombre = sol["nombre"]
    user_id = sol["user_id"]
    user_nums = sol["numeros"]
    chat_origen = sol["chat_origen"]
    nums_formatted = ", ".join([n.zfill(2) for n in user_nums])

    user_mencion = f"[{user_nombre}](tg://user?id={user_id})"

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
        await query.edit_message_text(f"✅ *Aprobado.* Números: {nums_formatted}", parse_mode="Markdown")

        texto_pago_confirmado = (
            f"🎉 *¡PAGO CONFIRMADO!* 🎉\n\n"
            f"👤 *Usuario:* {user_mencion}\n"
            f"🎟️ *Números asignados:* {nums_formatted}\n\n"
            f"¡Muchas felicidades! 🤝"
        )

        try:
            await context.bot.send_message(chat_id=chat_origen, text=texto_pago_confirmado, parse_mode="Markdown")
            await context.bot.send_message(chat_id=user_id, text=texto_pago_confirmado, parse_mode="Markdown")
        except Exception as e:
            print(f"Error notificando aprobación: {e}")

    elif accion == "rech":
        for n in user_nums:
            rifa[n] = {"estado": "disponible", "nombre": "", "user_id": "", "username": ""}

        del solicitudes[req_id]
        data_rifa["numeros"] = rifa
        data_rifa["solicitudes_pendientes"] = solicitudes
        guardar_data_completa(data_rifa)
        await query.edit_message_text(f"❌ *Rechazado.*", parse_mode="Markdown")

        try:
            await context.bot.send_message(chat_id=user_id, text=f"❌ Tu solicitud para los números *{nums_formatted}* fue rechazada.", parse_mode="Markdown")
        except Exception as e:
            print(f"Error notificando rechazo: {e}")

async def main():
    inicializar_rifa()
    
    await start_web_server()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reglas", reglas_command))
    app.add_handler(CommandHandler("bloquear", bloquear_command))
    app.add_handler(CommandHandler("desbloquear", desbloquear_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("ganador", ganador_command))
    app.add_handler(CommandHandler("liberar", liberar_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida_nuevos_usuarios))
    app.add_handler(CallbackQueryHandler(boton_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))

    print("🤖 Bot de Gran Sorteo 100 iniciado correctamente...")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    stop_signal = asyncio.Event()
    await stop_signal.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot detenido correctamente.")
