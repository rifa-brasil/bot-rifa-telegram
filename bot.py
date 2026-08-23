import os
import json
import uuid
import requests
import psycopg2
from urllib.parse import urlparse
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURACIÓN DE CREDENCIALES ---
EVOLUTION_API_URL = os.environ.get("EVOLUTION_API_URL", "https://mi-whatsapp-api-pobo.onrender.com").rstrip("/")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "55725d7c0b0fb17cb5e6564edac38c1f")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "mi-bot")
ADMIN_WHATSAPP_JID = os.environ.get("ADMIN_WHATSAPP_JID", "5511948824359@s.whatsapp.net")

# El JID de tu grupo de WhatsApp (Ejemplo: "120363383829101112@g.us")
# Ponlo directamente aquí si lo deseas, o déjalo configurado en las variables de Render
GRUPO_WHATSAPP_JID = os.environ.get("GRUPO_WHATSAPP_JID", "") 

# Tu URL de base de datos PostgreSQL en Render (Render te la da automáticamente como Internal o External Database URL)
DATABASE_URL = os.environ.get("DATABASE_URL", "")

VALOR_POR_NUMERO = 10

# --- CONEXIÓN Y GESTIÓN DE BASE DE DATOS POSTGRESQL (RENDER) ---
def get_db_connection():
    if DATABASE_URL:
        url = urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
        return conn
    return None

def inicializar_bd():
    conn = get_db_connection()
    if not conn:
        print("⚠️ Advertencia: No hay DATABASE_URL configurada. Usando modo temporal en memoria.")
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rifa_estado (
                id INT PRIMARY KEY,
                data JSONB
            );
        """)
        cur.execute("SELECT data FROM rifa_estado WHERE id = 1;")
        row = cur.fetchone()
        if not row:
            data_inicial = {
                "estado_rifa": "activa",
                "numeros": {str(i): {"estado": "disponible", "nombre": "", "user_id": "", "username": ""} for i in range(1, 101)},
                "solicitudes_pendientes": {},
                "idiomas_usuarios": {}
            }
            cur.execute("INSERT INTO rifa_estado (id, data) VALUES (1, %s);", (json.dumps(data_inicial),))
            conn.commit()
        cur.close()
        conn.close()
        print("🗄️ Base de datos PostgreSQL inicializada correctamente en Render.")
    except Exception as e:
        print(f"Error al inicializar PostgreSQL: {e}")

def obtener_data_completa():
    conn = get_db_connection()
    if not conn:
        return {
            "estado_rifa": "activa",
            "numeros": {str(i): {"estado": "disponible", "nombre": "", "user_id": "", "username": ""} for i in range(1, 101)},
            "solicitudes_pendientes": {},
            "idiomas_usuarios": {}
        }
    try:
        cur = conn.cursor()
        cur.execute("SELECT data FROM rifa_estado WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            data = row[0]
            if "estado_rifa" not in data: data["estado_rifa"] = "activa"
            if "solicitudes_pendientes" not in data: data["solicitudes_pendientes"] = {}
            if "idiomas_usuarios" not in data: data["idiomas_usuarios"] = {}
            return data
    except Exception as e:
        print(f"Error al leer BD: {e}")
    return {}

def guardar_data_completa(data):
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("UPDATE rifa_estado SET data = %s WHERE id = 1;", (json.dumps(data),))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error al guardar en BD: {e}")

def reiniciar_bd_completa():
    data_inicial = {
        "estado_rifa": "activa",
        "numeros": {str(i): {"estado": "disponible", "nombre": "", "user_id": "", "username": ""} for i in range(1, 101)},
        "solicitudes_pendientes": {},
        "idiomas_usuarios": {}
    }
    guardar_data_completa(data_inicial)

# --- FUNCIONES DE ENVÍO DE MENSAJES VÍA EVOLUTION API ---
def enviar_mensaje_whatsapp(destinatario_jid, texto):
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY or not INSTANCE_NAME:
        print(f"Simulando envío a {destinatario_jid}: {texto}")
        return
    
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": destinatario_jid,
        "text": texto,
        "options": {
            "delay": 1200,
            "presence": "composing"
        }
    }
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Respuesta Evolution API ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"Error enviando mensaje por WhatsApp: {e}")

# --- CÁLCULOS FINANCIEROS Y REGLAS ---
def calcular_premio_total():
    recaudacion_total = 100 * VALOR_POR_NUMERO
    premio = recaudacion_total * 0.55
    if premio.is_integer():
        return int(premio)
    return round(premio, 2)

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
        if restantes == 4: return p4
        elif restantes == 3: return p3
        elif restantes == 2: return p2
        elif restantes == 1: return p1

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
            texto += f"🟢 *{num_str}*: " + ("Disponível" if lang == "pt" else "Disponible") + "\n"
            disponibles += 1
        elif estado == "pendiente":
            texto += f"🟡 *{num_str}*: " + ("Em verificação de pagamento..." if lang == "pt" else "En verificación de pago...") + "\n"
        else:
            nombre = info.get("nombre", "Usuário")
            texto += f"🔴 *{num_str}*: Ocupado por {nombre}\n"
             
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
            "2️⃣ *Números e Promoção:* Dispomos de 100 números (de 01 a 100).\n"
            f"✨ *Valores para sua primeira jogada (Promoção):*\n"
            f"• 1 número = *{VALOR_POR_NUMERO} reais*\n"
            f"• 2 números = *{int(VALOR_POR_NUMERO * 1.5)} reais*\n"
            f"• 3 números = *{int(VALOR_POR_NUMERO * 2.5)} reais*\n"
            f"• 4 números = *{int(VALOR_POR_NUMERO * 3.5)} reais*\n"
            f"• 5 números = *{int(VALOR_POR_NUMERO * 4)} reais*\n"
            f"*(Se você pedir mais de 5 números em sua primeira jogada, os primeiros 5 têm preço promocional e a partir do 6º número cada um custa exatamente {VALOR_POR_NUMERO} reais).* \n\n"
            f"⚠️ *Atenção às jogadas adicionais!* A promoção de pacotes aplica-se **apenas à primeira jogada** de cada usuário. A partir da sua segunda jogada, **cada número tem um custo fixo de {VALOR_POR_NUMERO} reais**.\n\n"
            "Envie a palavra `lista` para ver os disponíveis e escreva os desejados separados por vírgula (ex: *7, 14*).\n"
            "3️⃣ *Condição do Sorteio:* O sorteio será realizado **apenas quando os 100 números estiverem 100% ocupados e pagos**.\n"
            "4️⃣ *Garantia de Reembolso:* Solicite reembolso integral com o administrador.\n"
            f"5️⃣ *Entrega do Prêmio:* O usuário vencedor receberá um prêmio de *{premio_actual} reais* (via PIX ou em Cuba em CUP). Grupo de WhatsApp obrigatório para taxa: https://chat.whatsapp.com/HEaEIKaEjksJRrWEKcIVEo?s=sh&p=a&ilr=0.\n"
            "6️⃣ *Transparência:* O vencedor é definido utilizando os resultados oficiais da *Loteria da Flórida* (Pick 3) no horario noturno.\n\n"
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
            f"⚠️ *¡Atención a las jugadas adicionales!* La promoción aplica **únicamente para la primera jugada**. A partir de tu segunda jugada, **cada número tiene un costo fijo de {VALOR_POR_NUMERO} reales**.\n\n"
            "Envía la palabra `lista` para ver los disponibles y escribe los que deseas separados por coma (ej: *7, 14*).\n"
            "3️⃣ *Condición del Sorteo:* El sorteo se realizará **únicamente cuando los 100 números estén 100% ocupados y pagados**.\n"
            "4️⃣ *Garantia de Devolución:* Solicita la devolución íntegra con el administrador.\n"
            f"5️⃣ *Entrega del Premio:* El usuario ganador recibirá un premio de *{premio_actual} reales* (vía PIX o en Cuba en CUP a su familiar mediante la tasa del grupo de remesas). Grupo de WhatsApp obligatorio para tasa: https://chat.whatsapp.com/HEaEIKaEjksJRrWEKcIVEo?s=sh&p=a&ilr=0.\n"
            "6️⃣ *Transparência:* El ganador se define utilizando los resultados oficiales de la *Lotería de Florida* (Pick 3) en el horario nocturno.\n\n"
            "🤝 *¡Ayúdanos a crecer!* Enlace de invitación al grupo: https://t.me/+didZDftOZAhmZjdh"
        )

# --- WEBHOOK PARA RECIBIR MENSAJES DE EVOLUTION API ---
@app.route("/", methods=["GET"])
def index():
    return "Bot de Rifa WhatsApp Activo y en Línea 24/7!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return jsonify({"status": "ignored"}), 200

    try:
        event = data.get("event")
        if event != "messages.upsert":
            return jsonify({"status": "ok"}), 200

        msg_data = data.get("data", {})
        key = msg_data.get("key", {})
        
        if key.get("fromMe"):
            return jsonify({"status": "ok"}), 200

        remitente_jid = key.get("remoteJid", "")
        push_name = msg_data.get("pushName", "Usuario")
        
        message_body = msg_data.get("message", {})
        texto_mensaje = ""
        if "conversation" in message_body:
            texto_mensaje = message_body["conversation"]
        elif "extendedTextMessage" in message_body:
            texto_mensaje = message_body["extendedTextMessage"].get("text", "")

        if not texto_mensaje:
            return jsonify({"status": "ok"}), 200

        mensaje_limpio = texto_mensaje.strip().lower()
        data_rifa = obtener_data_completa()
        rifa = data_rifa["numeros"]
        solicitudes = data_rifa.get("solicitudes_pendientes", {})
        idiomas = data_rifa.get("idiomas_usuarios", {})
        estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

        user_id = remitente_jid.split("@")[0]
        lang_usuario = idiomas.get(user_id, "es")

        # --- COMANDOS DE IDIOMA / CONFIGURACIÓN ---
        if mensaje_limpio in ["idioma es", "español", "cubano"]:
            idiomas[user_id] = "es"
            data_rifa["idiomas_usuarios"] = idiomas
            guardar_data_completa(data_rifa)
            enviar_mensaje_whatsapp(remitente_jid, f"✅ Idioma cambiado a **Español** 🇨🇺\n\n{generar_texto_lista('es')}")
            return jsonify({"status": "ok"}), 200

        if mensaje_limpio in ["idioma pt", "portugues", "brasileiro"]:
            idiomas[user_id] = "pt"
            data_rifa["idiomas_usuarios"] = idiomas
            guardar_data_completa(data_rifa)
            enviar_mensaje_whatsapp(remitente_jid, f"✅ Idioma alterado para **Português** 🇧🇷\n\n{generar_texto_lista('pt')}")
            return jsonify({"status": "ok"}), 200

        # --- COMANDOS GENERALES ---
        if mensaje_limpio in ["hola", "buenas", "lista", "inicio", "rifa", "sorteo"]:
            enviar_mensaje_whatsapp(remitente_jid, f"¡Hola @{user_id}! Estado actual de Gran Sorteo 100:\n\n{generar_texto_lista(lang_usuario)}")
            return jsonify({"status": "ok"}), 200

        if mensaje_limpio in ["reglas", "regras"]:
            enviar_mensaje_whatsapp(remitente_jid, obtener_texto_reglas(lang_usuario))
            return jsonify({"status": "ok"}), 200

        # --- COMANDOS DE ADMINISTRADOR ---
        if remitente_jid == ADMIN_WHATSAPP_JID:
            if mensaje_limpio.startswith("/bloquear"):
                data_rifa["estado_rifa"] = "bloqueada"
                guardar_data_completa(data_rifa)
                enviar_mensaje_whatsapp(remitente_jid, "⛔ *La rifa ha sido bloqueada temporalmente.*")
                return jsonify({"status": "ok"}), 200

            if mensaje_limpio.startswith("/desbloquear"):
                data_rifa["estado_rifa"] = "activa"
                guardar_data_completa(data_rifa)
                enviar_mensaje_whatsapp(remitente_jid, "🟢 *La rifa ha sido desbloqueada.*\n\n" + generar_texto_lista("es"))
                return jsonify({"status": "ok"}), 200

            if mensaje_limpio.startswith("/reset"):
                reiniciar_bd_completa()
                enviar_mensaje_whatsapp(remitente_jid, "🔄 *¡Gran Sorteo 100 ha sido reseteado con éxito!*\n\n" + generar_texto_lista("es"))
                return jsonify({"status": "ok"}), 200

            if mensaje_limpio.startswith("/liberar "):
                nombre_buscar = mensaje_limpio.replace("/liberar", "").strip().lower()
                numeros_liberados = []
                for num_str, info in rifa.items():
                    if info.get("estado") == "ocupado":
                        if nombre_buscar in info.get("nombre", "").lower():
                            numeros_liberados.append(num_str.zfill(2))
                            rifa[num_str] = {"estado": "disponible", "nombre": "", "user_id": "", "username": ""}
                
                if not numeros_liberados:
                    enviar_mensaje_whatsapp(remitente_jid, f"⚠️ No se encontraron números ocupados para: *{nombre_buscar}*.")
                else:
                    if data_rifa.get("estado_rifa") == "finalizada":
                        data_rifa["estado_rifa"] = "activa"
                    data_rifa["numeros"] = rifa
                    guardar_data_completa(data_rifa)
                    enviar_mensaje_whatsapp(remitente_jid, f"🔄 Números *{', '.join(numeros_liberados)}* liberados con éxito.")
                return jsonify({"status": "ok"}), 200

            if mensaje_limpio.startswith("/ganador "):
                num_ingresado = mensaje_limpio.replace("/ganador", "").strip()
                if not num_ingresado.isdigit() or not (1 <= int(num_ingresado) <= 100):
                    enviar_mensaje_whatsapp(remitente_jid, "⚠️ El número debe estar entre 1 y 100.")
                    return jsonify({"status": "ok"}), 200

                num_str = str(int(num_ingresado))
                info_num = rifa.get(num_str, {})
                if info_num.get("estado") != "ocupado":
                    enviar_mensaje_whatsapp(remitente_jid, f"⚠️ El número *{num_ingresado.zfill(2)}* no está ocupado.")
                    return jsonify({"status": "ok"}), 200

                ganador_nombre = info_num.get("nombre")
                ganador_jid = info_num.get("user_id")
                num_formateado = num_str.zfill(2)
                premio_actual = calcular_premio_total()

                data_rifa["estado_rifa"] = "finalizada"
                guardar_data_completa(data_rifa)

                msj_ganador_oficial = (
                    f"🎯 *¡RESULTADO OFICIAL DE LA LOTERÍA / RESULTADO OFICIAL DA LOTERIA!* 🎯\n\n"
                    f"El número ganador de la Florida Pick 3 es el / O número vencedor da Florida Pick 3 é o: *{num_formateado}*"
                )
                msj_ganador_felicitacion = (
                    f"🎉 *¡Felicidades al Ganador! / Parabéns ao Vencedor!* 🎉\n\n"
                    f"El usuario @{ganador_jid.split('@')[0]} ha ganado con el número {num_formateado} un premio de {premio_actual} reales. ¡Muchas felicidades! 🥳\n\n"
                    f"Por favor, póngase en contacto con el administrador para recibir su premio (puede elegir que se le transfiera vía PIX o que se le envíe a su familiar en Cuba). Una vez que reciba la transferencia, le pedimos por favor que haga una captura de pantalla y la envíe a este grupo como evidencia de que recibió su pago y que todo funciona con total transparencia."
                )

                if GRUPO_WHATSAPP_JID:
                    enviar_mensaje_whatsapp(GRUPO_WHATSAPP_JID, msj_ganador_oficial)
                    enviar_mensaje_whatsapp(GRUPO_WHATSAPP_JID, msj_ganador_felicitacion)
                enviar_mensaje_whatsapp(ganador_jid, msj_ganador_oficial)
                enviar_mensaje_whatsapp(ganador_jid, msj_ganador_felicitacion)
                return jsonify({"status": "ok"}), 200

            # --- APROBACIÓN O RECHAZO RÁPIDO POR COMANDO DEL ADMIN ---
            if mensaje_limpio.startswith("/aprobar "):
                req_id = mensaje_limpio.replace("/aprobar", "").strip()
                if req_id in solicitudes:
                    sol = solicitudes[req_id]
                    u_nombre = sol["nombre"]
                    u_jid = sol["user_id"]
                    u_nums = sol["numeros"]
                    nums_formatted = ", ".join([n.zfill(2) for n in u_nums])

                    for n in u_nums:
                        rifa[n]["estado"] = "ocupado"
                        rifa[n]["nombre"] = u_nombre
                        rifa[n]["user_id"] = u_jid

                    del solicitudes[req_id]
                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes

                    # Bloquear rifa automáticamente si se llenaron los 100 números
                    if all(rifa[str(n)]["estado"] == "ocupado" for n in range(1, 101)):
                        data_rifa["estado_rifa"] = "finalizada"

                    guardar_data_completa(data_rifa)

                    texto_pago_confirmado = (
                        f"🎉 *¡PAGO CONFIRMADO! / PAGAMENTO CONFIRMADO!* 🎉\n\n"
                        f"👤 *Usuario/Usuário:* @{u_jid.split('@')[0]}\n"
                        f"🎟️ *Números:* {nums_formatted}\n\n"
                        f"¡Muchas felicidades! / Parabéns! 🤝"
                    )

                    if GRUPO_WHATSAPP_JID:
                        enviar_mensaje_whatsapp(GRUPO_WHATSAPP_JID, texto_pago_confirmado)
                    enviar_mensaje_whatsapp(u_jid, texto_pago_confirmado)
                    enviar_mensaje_whatsapp(remitente_jid, f"✅ Solicitud `{req_id}` aprobada con éxito.")
                else:
                    enviar_mensaje_whatsapp(remitente_jid, f"⚠️ Solicitud `{req_id}` no encontrada o ya procesada.")
                return jsonify({"status": "ok"}), 200

            if mensaje_limpio.startswith("/rechazar "):
                req_id = mensaje_limpio.replace("/rechazar", "").strip()
                if req_id in solicitudes:
                    sol = solicitudes[req_id]
                    u_jid = sol["user_id"]
                    u_nums = sol["numeros"]
                    nums_formatted = ", ".join([n.zfill(2) for n in u_nums])

                    for n in u_nums:
                        rifa[n] = {"estado": "disponible", "nombre": "", "user_id": "", "username": ""}

                    del solicitudes[req_id]
                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes
                    guardar_data_completa(data_rifa)

                    enviar_mensaje_whatsapp(u_jid, f"❌ Tu solicitud para los números *{nums_formatted}* fue rechazada / foi rejeitada.")
                    enviar_mensaje_whatsapp(remitente_jid, f"❌ Solicitud `{req_id}` rechazada.")
                else:
                    enviar_mensaje_whatsapp(remitente_jid, f"⚠️ Solicitud `{req_id}` no encontrada.")
                return jsonify({"status": "ok"}), 200

        # --- SELECCIÓN DE NÚMEROS POR EL CLIENTE ---
        partes = [p.strip() for p in texto_mensaje.split(",")]
        es_lista_numeros = all(p.isdigit() for p in partes) if partes else False

        if es_lista_numeros:
            if estado_actual_rifa in ["finalizada", "bloqueada"]:
                msg_bloq = "⛔ Lo sentimos, la lista se encuentra cerrada o bloqueada en este momento." if lang_usuario == "es" else "⛔ Desculpe, a lista está fechada ou bloqueada no momento."
                enviar_mensaje_whatsapp(remitente_jid, msg_bloq)
                return jsonify({"status": "ok"}), 200

            validos_para_reservar = []
            for p in partes:
                num_elegido = int(p)
                if 1 <= num_elegido <= 100:
                    num_str = str(num_elegido)
                    est = rifa[num_str].get("estado", "disponible")
                    if est == "disponible":
                        validos_para_reservar.append(num_str)

            if validos_para_reservar:
                ya_tiene_compras = usuario_tiene_jugada_previa(user_id, data_rifa)
                req_id = "r" + str(uuid.uuid4().int)[:4]

                for n in validos_para_reservar:
                    rifa[n]["estado"] = "pendiente"

                solicitudes[req_id] = {
                    "nombre": push_name,
                    "user_id": remitente_jid,
                    "numeros": validos_para_reservar
                }

                data_rifa["numeros"] = rifa
                data_rifa["solicitudes_pendientes"] = solicitudes
                guardar_data_completa(data_rifa)

                nums_solicitados_txt = ", ".join([n.zfill(2) for n in validos_para_reservar])
                cantidad_numeros = len(validos_para_reservar)
                total_a_pagar = calcular_precio_total(cantidad_numeros, usuario_ya_tiene_compras=ya_tiene_compras)

                if lang_usuario == "pt":
                    aviso_promocion = f"\n⚠️ *Aviso importante:* Jogada com preço padrão ({VALOR_POR_NUMERO} reais cada).\n" if ya_tiene_compras else f"\n✨ *Primeira jogada detectada!* Tarifa promocional aplicada.\n"
                    msj_cliente = (
                        f"⏳ *SOLICITAÇÃO EM ANDAMENTO* ⏳\n\n"
                        f"Olá @{user_id}, seus números (*{nums_solicitados_txt}*) estão reservados temporariamente.\n"
                        f"{aviso_promocion}"
                        f"💰 Quantidade: *{cantidad_numeros}*\n"
                        f"💵 Total a transferir: *{total_a_pagar} reais*\n\n"
                        f"Entre em contato com o administrador para pagar."
                    )
                else:
                    aviso_promocion = f"\n⚠️ *Aviso importante:* Jugada a precio estándar ({VALOR_POR_NUMERO} reales cada uno).\n" if ya_tiene_compras else f"\n✨ *¡Primera jugada detectada!* Tarifa promocional aplicada.\n"
                    msj_cliente = (
                        f"⏳ *SOLICITUD EN PROCESO* ⏳\n\n"
                        f"Hola @{user_id}, tus números (*{nums_solicitados_txt}*) están reservados temporalmente.\n"
                        f"{aviso_promocion}"
                        f"💰 Cantidad: *{cantidad_numeros}*\n"
                        f"💵 Total a transferir: *{total_a_pagar} reais*\n\n"
                        f"Contacta al administrador para pagar."
                    )

                enviar_mensaje_whatsapp(remitente_jid, msj_cliente)

                txt_admin = (
                    f"📥 *NUEVA SOLICITUD* (ID: `{req_id}`)\n\n"
                    f"👤 *Cliente:* @{user_id} ({push_name}) {'*(Jugada Posterior)*' if ya_tiene_compras else '*(1era Jugada)*'}\n"
                    f"🎟️ *Números:* *{nums_solicitados_txt}*\n"
                    f"💵 *Total:* *{total_a_pagar} reales* ({cantidad_numeros} núm.)\n\n"
                    f"👉 Para aprobar responde con:\n`/aprobar {req_id}`\n\n"
                    f"👉 Para rechazar responde con:\n`/rechazar {req_id}`"
                )
                if ADMIN_WHATSAPP_JID:
                    enviar_mensaje_whatsapp(ADMIN_WHATSAPP_JID, txt_admin)

    except Exception as e:
        print(f"Error procesando webhook: {e}")

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    inicializar_bd()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
