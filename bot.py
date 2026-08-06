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
                "solicitudes_pendientes": {},
                "idiomas_usuarios": {}
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
            if "idiomas_usuarios" not in data:
                data["idiomas_usuarios"] = {}
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

def generar_texto_lista(lang="es"):
    data = obtener_data_completa()
    rifa = data["numeros"]
    
    if lang == "pt":
        texto = "🎟️ *LISTA OFICIAL DA RIFA (1 ao 100)* 🎟️\n\n"
    else:
        texto = "🎟️ *LISTA OFICIAL DE LA RIFA (1 al 100)* 🎟️\n\n"
        
    disponibles = 0
    for i in range(1, 101):
        num_str = str(i).zfill(2)
        info = rifa[str(i)]
        estado = info.get("estado", "disponible")
  
        if estado == "disponible":
            if lang == "pt":
                texto += f"🟢 *{num_str}*: Disponível\n"
            else:
                texto += f"🟢 *{num_str}*: Disponible\n"
            disponibles += 1
        elif estado == "pendiente":
            if lang == "pt":
                texto += f"🟡 *{num_str}*: Em verificação de pagamento...\n"
            else:
                texto += f"🟡 *{num_str}*: En verificación de pago...\n"
        else:
            nombre = info.get("nombre", "Usuário")
            user_id = info.get("user_id")
            if lang == "pt":
                ocupado_txt = "Ocupado por"
            else:
                ocupado_txt = "Ocupado por"
                
            if user_id:
                texto += f"🔴 *{num_str}*: {ocupado_txt} [{nombre}](tg://user?id={user_id})\n"
            else:
                texto += f"🔴 *{num_str}*: {ocupado_txt} {nombre}\n"
             
    if lang == "pt":
        texto += f"\n📊 *Resumo:* Restam {disponibles} números disponíveis."
    else:
        texto += f"\n📊 *Resumen:* Quedan {disponibles} números disponibles."
        
    estado_actual = data.get("estado_rifa")
    if estado_actual == "finalizada":
        texto += "\n\n🔒 *ESTADO:* " + ("Rifa encerrada/finalizada." if lang == "pt" else "Rifa cerrada/finalizada.")
    elif estado_actual == "bloqueada":
        texto += "\n\n⛔ *ESTADO:* " + ("Rifa temporariamente bloqueada pelo administrador." if lang == "pt" else "Rifa temporalmente bloqueada por el administrador.")
    return texto

def obtener_texto_reglas(lang="es"):
    premio_actual = calcular_premio_total()
    if lang == "pt":
        return (
            "📌 *REGRAS E DINÂMICA DO GRUPO (Grande Sorteio 100):*\n\n"
            "1️⃣ *Respeito:* Mantenha um ambiente de respeito absoluto para com todos os membros da comunidade e administradores.\n"
            "2️⃣ *Números e Promoção:* Dispomose de 100 números (de 01 a 100).\n"
            f"✨ *Valores para sua primeira jogada (Promoção):*\n"
            f"• 1 número = *{VALOR_POR_NUMERO} reais*\n"
            f"• 2 números = *{int(VALOR_POR_NUMERO * 1.5)} reais*\n"
            f"• 3 números = *{int(VALOR_POR_NUMERO * 2.5)} reais*\n"
            f"• 4 números = *{int(VALOR_POR_NUMERO * 3.5)} reais*\n"
            f"• 5 números = *{int(VALOR_POR_NUMERO * 4)} reais*\n"
            f"*(Se você pedir mais de 5 números em sua primeira jogada, os primeiros 5 têm preço promocional e a partir do 6º número cada um custa exatamente {VALOR_POR_NUMERO} reais).* \n\n"
            f"⚠️ *Atenção às jogadas adicionais!* A promoção de pacotes aplica-se **apenas à primeira jogada** de cada usuário. A partir da sua segunda jogada (mesmo se você enviar 2, 3, 4 ou mais números), **cada número tem um custo fixo de {VALOR_POR_NUMERO} reais**, pois não entra na promoção.\n\n"
            "Envie a palavra `lista` para ver os disponíveis e escreva os desejados separados por vírgula (ex: *7, 14*) aqui ou no grupo.\n"
            "3️⃣ *Condição do Sorteio:* O sorteio será realizado **apenas quando os 100 números estiverem 100% ocupados e pagos**.\n"
            "4️⃣ *Garantia de Reembolso:* Se algum participante adquirir seus números mas **não quiser esperar**, pode solicitar o **reembolso integral de seu dinheiro** com o administrador (@yordanisr).\n"
            f"5️⃣ *Entrega do Prêmio:* O usuário vencedor receberá um prêmio de *{premio_actual} reais* (via PIX ou em Cuba em CUP para seu familiar através da taxa do grupo de remessas). Grupo de WhatsApp obrigatório para taxa: https://chat.whatsapp.com/HEaEIKaEjksJRrWEKcIVEo?s=sh&p=a&ilr=0.\n"
            "6️⃣ *Transparência:* O vencedor é definido utilizando os resultados oficiais da *Loteria da Flórida* (Pick 3) no horário noturno.\n\n"
            "🤝 *Ajude-nos a crescer!* Link de convite para o grupo: https://t.me/+didZDftOZAhmZjdh"
        )
    else:
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
    keyboard = [
        [
            InlineKeyboardButton("🇪🇸 Español", callback_data="lang_es"),
            InlineKeyboardButton("🇧🇷 Português", callback_data="lang_pt")
        ]
    ]
    await update.message.reply_text(
        "🌍 *Selecciona tu idioma / Escolha seu idioma:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def reglas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇪🇸 Ver en Español", callback_data="rules_es"),
            InlineKeyboardButton("🇧🇷 Ver em Português", callback_data="rules_pt")
        ]
    ]
    await update.message.reply_text(
        "🌍 *Selecciona el idioma para las reglas / Escolha o idioma para as regras:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

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

    await update.message.reply_text("🟢 *La rifa ha sido desbloqueada.*\n\n" + generar_texto_lista("es"), parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ No estás autorizado para resetear el sorteo.")
        return
     
    borrar_y_recrear_base_datos()
    await update.message.reply_text("🔄 *¡Gran Sorteo 100 ha sido reseteado con éxito!*\n\n" + generar_texto_lista("es"), parse_mode="Markdown")

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
        f"🎯 *¡RESULTADO OFICIAL DE LA LOTERÍA / RESULTADO OFICIAL DA LOTERIA!* 🎯\n\n"
        f"El número ganador de la Florida Pick 3 es el / O número vencedor da Florida Pick 3 é o: *{num_formateado}*",
        parse_mode="Markdown"
    )

    await update.message.reply_text(
        f"🎉 *¡Felicidades al Ganador! / Parabéns ao Vencedor!* 🎉\n\n"
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
        texto_bienvenida = f"🎯 *¡Bienvenido/a {mencion} a Gran Sorteo 100! / Bem-vindo/a!* 🎟️\n\n{obtener_texto_reglas('es')}\n\n---\n\n{obtener_texto_reglas('pt')}"
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
    idiomas = data_rifa.get("idiomas_usuarios", {})
    estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

    lang_usuario = idiomas.get(str(user_id), "es")

    if comando in ["hola", "buenas", "lista", "inicio", "rifa", "sorteo"]:
        await update.message.reply_text(
            f"¡Hola {usuario_mencion}! Estado actual de Gran Sorteo 100:\n\n{generar_texto_lista(lang_usuario)}",
            parse_mode="Markdown"
        )
        return

    partes = [p.strip() for p in mensaje_texto.split(",")]
    es_lista_numeros = all(p.isdigit() for p in partes) if partes else False

    if es_lista_numeros:
        if estado_actual_rifa in ["finalizada", "bloqueada"]:
            msg_bloq = "⛔ Lo sentimos, la lista se encuentra cerrada o bloqueada en este momento." if lang_usuario == "es" else "⛔ Desculpe, a lista está fechada ou bloqueada no momento."
            await update.message.reply_text(msg_bloq, parse_mode="Markdown")
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

            if lang_usuario == "pt":
                if ya_tiene_compras:
                    aviso_promocion = f"\n⚠️ *Aviso importante:* Como você já tem uma jogada anterior registrada, esta nova jogada de {cantidad_numeros} número(s) **não se aplica à promoção** e é cobrada pelo preço padrão (*{VALOR_POR_NUMERO} reais cada número*).\n"
                else:
                    aviso_promocion = f"\n✨ *Primeira jogada detectada!* Aplica-se a tarifa promocional para seus {cantidad_numeros} número(s).\n"
                
                await update.message.reply_text(
                    f"⏳ *SOLICITAÇÃO EM ANDAMENTO* ⏳\n\n"
                    f"Olá {usuario_mencion}, seus números (*{nums_solicitados_txt}*) estão reservados temporariamente.\n"
                    f"{aviso_promocion}"
                    f"💰 Quantidade: *{cantidad_numeros}*\n"
                    f"💵 Total a transferir: *{total_a_pagar} reais*\n\n"
                    f"Entre em contato com o administrador @yordanisr para pagar.",
                    parse_mode="Markdown"
                )
            else:
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

    data_callback = query.data

    # Manejo de selección de idioma o reglas por idioma para cualquier usuario
    if data_callback.startswith("lang_") or data_callback.startswith("rules_"):
        lang = data_callback.split("_")[1]
        user_id = query.from_user.id
        
        data_rifa = obtener_data_completa()
        if "idiomas_usuarios" not in data_rifa:
            data_rifa["idiomas_usuarios"] = {}
        data_rifa["idiomas_usuarios"][str(user_id)] = lang
        guardar_data_completa(data_rifa)

        if data_callback.startswith("lang_"):
            texto_resp = f"✅ Idioma cambiado a **Español**." if lang == "es" else f"✅ Idioma alterado para **Português**."
            texto_resp += f"\n\n{generar_texto_lista(lang)}"
            await query.edit_message_text(texto_resp, parse_mode="Markdown")
        else:
            texto_reglas = obtener_texto_reglas(lang)
            titulo = "🎯 *REGLAS OFICIALES*" if lang == "es" else "🎯 *REGRAS OFICIAIS*"
            await query.edit_message_text(f"{titulo}\n\n{texto_reglas}", parse_mode="Markdown")
        return

    # De aquí en adelante, acciones exclusivas del administrador
    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.edit_message_text("⛔ No estás autorizado. / Não autorizado.")
        return

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
            f"🎉 *¡PAGO CONFIRMADO! / PAGAMENTO CONFIRMADO!* 🎉\n\n"
            f"👤 *Usuario/Usuário:* {user_mencion}\n"
            f"🎟️ *Números:* {nums_formatted}\n\n"
            f"¡Muchas felicidades! / Parabéns! 🤝"
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
        await query.edit_message_text(f"❌ *Rechazado / Rejeitado.*", parse_mode="Markdown")

        try:
            await context.bot.send_message(chat_id=user_id, text=f"❌ Tu solicitud para los números / Sua solicitação para os números *{nums_formatted}* fue rechazada / foi rejeitada.", parse_mode="Markdown")
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
