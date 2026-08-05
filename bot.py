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

def calcular_precio_total(cantidad):
    if cantidad <= 0:
        return 0
    
    total = 0
    restantes = cantidad

    # Bloques de 5 (80 reales)
    while restantes >= 5:
        total += 80
        restantes -= 5

    if restantes == 4:
        total += 70
        restantes = 0
    elif restantes == 3:
        total += 50
        restantes = 0
    elif restantes == 2:
        total += 30
        restantes = 0

    if restantes == 1:
        total += 20
        restantes = 0

    return total

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
    "2️⃣ *Números y Costo:* Disponemos de una tabla con *100 números* (del 01 al 100). Precios:\n"
    "• 1 número = *20 reales*\n"
    "• 2 números = *30 reales*\n"
    "• 3 números = *50 reales*\n"
    "• 4 números = *70 reales*\n"
    "• 5 números = *80 reales*\n"
    "(A partir del 6to número en adelante se cobran a 20 reales adicionales cada uno). Envía la palabra `lista` para ver los disponibles y escribe los que deseas separados por coma (ej: *7, 14*) aquí o en el grupo.\n"
    "3️⃣ *Condición del Sorteo:* El sorteo se realizará **únicamente cuando los 100 números estén 100% ocupados y pagados**.\n"
    "4️⃣ *Garantía de Devolución:* Si algún participante adquiere sus números pero **no desea esperar**, puede solicitar la **devolución íntegra de su dinero** con el administrador (@yordanisr).\n"
    "5️⃣ *Entrega del Premio:* El usuario ganador recibirá un premio de *1000 reales* (vía PIX o en Cuba en CUP a su familiar mediante la tasa del grupo de remesas). Grupo de WhatsApp obligatorio para tasa: https://chat.whatsapp.com/HEaEIKaEjksJRrWEKcIVEo?s=sh&p=a&ilr=0.\n"
    "6️⃣ *Transparencia:* El ganador se define utilizando los resultados oficiales de la *Lotería de Florida* (Pick 3) en el horario nocturno.\n\n"
    "🤝 *¡Ayúdanos a crecer!* Enlace de invitación al grupo: https://t.me/+didZDftOZAhmZjdh"
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

    data_rifa["estado_rifa"] = "finalizada"
    guardar_data_completa(data_rifa)

    await update.message.reply_text(f"🎯 *¡GANADOR OFICIAL!* El número es el: *{num_formateado}*\n\n¡Felicidades a {ganador_mencion}!", parse_mode="Markdown")

async def bienvenida_nuevos_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for nuevo_usuario in update.message.new_chat_members:
        if nuevo_usuario.id == context.bot.id:
            continue
        nombre = nuevo_usuario.first_name or "Amigo"
        mencion = f"[{nombre}](tg://user?id={nuevo_usuario.id})"
        texto_bienvenida = f"🎯 *¡Bienvenido/a {mencion} a Gran Sorteo 100!* 🎟️\n\n{TEXTO_REGLAS_OFICIAL}"
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

        if (ocupados or pendientes or invalidos) and not valid_para_reservar if 'valid_para_reservar' in locals() else validos_para_reservar:
            pass

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
            cantidad_numeros = len(validos_para_reservar)
            total_a_pagar = calcular_precio_total(cantidad_numeros)

            await update.message.reply_text(
                f"⏳ *SOLICITUD EN PROCESO* ⏳\n\n"
                f"Hola {nombre_usuario}, tus números (*{nums_solicitados_txt}*) están reservados temporalmente.\n\n"
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
                f"👤 *Cliente:* {nombre_usuario}\n"
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

        try:
            await context.bot.send_message(chat_id=chat_origen, text=f"🎉 *¡PAGO CONFIRMADO!* 🎉\n👤 {user_nombre} - Números: *{nums_formatted}*", parse_mode="Markdown")
            await context.bot.send_message(chat_id=user_id, text=f"✅ ¡Tu pago fue aprobado! Tus números (*{nums_formatted}*) ya son tuyos. 🍀", parse_mode="Markdown")
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
    
    # Iniciar servidor web en segundo plano
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
    
    # Inicializar y arrancar polling de manera segura
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Mantener el proceso vivo indefinidamente
    stop_signal = asyncio.Event()
    await stop_signal.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot detenido correctamente.")
