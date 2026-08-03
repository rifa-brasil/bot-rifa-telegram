import os
import json
import re
import uuid
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

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
        return
    borrar_y_recrear_base_datos()
    await update.message.reply_text("🔄 *¡La rifa ha sido reseteada con éxito!* Todos los números vuelven a estar disponibles.\n\n" + generar_texto_lista(), parse_mode="Markdown")

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
            msg_grupo = f"🎉 *¡PAGO CONFIRMADO!* 🎉\n\n👤 *Usuario:* {user_nombre}\n🎟️ *Números asignados:* *{nums_formatted}*\n\n¡Muchas felicidades! 🤝"
            await context.bot.send_message(chat_id=chat_origen, text=msg_grupo, parse_mode="Markdown")
        except Exception as e:
            print(f"Error enviando aviso: {e}")

        try:
            msg_privado = f"🎉 *¡Hola {user_nombre}!* 🎉\n\nTu pago ha sido verificado. Tus números (*{nums_formatted}*) ya están registrados oficialmente.\n\n¡Mucha suerte! 🍀"
            await context.bot.send_message(chat_id=user_id, text=msg_privado, parse_mode="Markdown")
        except Exception as e:
            print(f"Error enviando privado: {e}")

    elif accion == "rech":
        for n in user_nums:
            rifa[n] = {"estado": "disponible", "nombre": "", "user_id": "", "username": ""}

        del solicitudes[req_id]
        data_rifa["numeros"] = rifa
        data_rifa["solicitudes_pendientes"] = solicitudes
        guardar_data_completa(data_rifa)

        await query.edit_message_text(f"❌ *Solicitud {req_id} RECHAZADA.*", parse_mode="Markdown")

        try:
            msg_cancel = f"⚠️ *SOLICITUD CANCELADA* ⚠️\n\nHola {user_nombre}, tu solicitud para el/los número(s) *{nums_formatted}* fue rechazada. Vuelven a estar 🟢 *Disponibles*."
            await context.bot.send_message(chat_id=chat_origen, text=msg_cancel, parse_mode="Markdown")
            await context.bot.send_message(chat_id=user_id, text=msg_cancel, parse_mode="Markdown")
        except Exception as e:
            print(f"Error notificando rechazo: {e}")

async def main():
    inicializar_rifa()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CallbackQueryHandler(boton_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))

    print("🤖 Bot iniciado correctamente...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())




