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
GRUPO_WHATSAPP_JID = os.environ.get("GRUPO_WHATSAPP_JID", "") 

# Tu URL de base de datos PostgreSQL en Render
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
            f"• 5 números = *{int(VALOR_POR_NUMERO * 4)} reais*\n\n"
            "Envie a palavra `lista` para ver os disponíveis e escreva os desejados separados por vírgula (ex: *7, 14*).\n"
            "3️⃣ *Condição do Sorteio:* O sorteio será realizado **apenas quando os 100 números estiverem 100% ocupados e pagos**.\n"
            f"4️⃣ *Entrega do Prêmio:* O usuário vencedor receberá um prêmio de *{premio_actual} reais*."
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
            f"• 5 números = *{int(VALOR_POR_NUMERO * 4)} reales*\n\n"
            "Envía la palabra `lista` para ver los disponibles y escribe los que deseas separados por coma (ej: *7, 14*).\n"
            "3️⃣ *Condición del Sorteo:* El sorteo se realizará **únicamente cuando los 100 números estén 100% ocupados y pagados**.\n"
            f"4️⃣ *Entrega del Premio:* El usuario ganador recibirá un premio de *{premio_actual} reales*."
        )

# --- WEBHOOK PARA RECIBIR MENSAJES DE EVOLUTION API ---
@app.route("/", methods=["GET"])
def index():
    return "Bot de Rifa WhatsApp Activo y en Línea 24/7!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📥 WEBHOOK RECIBIDO:", json.dumps(data, indent=2))
    
    if not data:
        return jsonify({"status": "ignored"}), 200

    try:
        event = data.get("event")
        print(f"🔍 Evento detectado: {event}")
        
        if event != "messages.upsert":
            return jsonify({"status": "ok"}), 200

        msg_data = data.get("data", {})
        key = msg_data.get("key", {})
        
        if key.get("fromMe"):
            print("⚠️ Mensaje ignorado por ser 'fromMe' (enviado por el propio bot)")
            return jsonify({"status": "ok"}), 200

        remitente_jid = key.get("remoteJid", "")
        push_name = msg_data.get("pushName", "Usuario")
        print(f"👤 Remitente JID: {remitente_jid} | Nombre: {push_name}")
        
        message_body = msg_data.get("message", {})
        texto_mensaje = ""
        if "conversation" in message_body:
            texto_mensaje = message_body["conversation"]
        elif "extendedTextMessage" in message_body:
            texto_mensaje = message_body["extendedTextMessage"].get("text", "")

        print(f"💬 Texto extraído: '{texto_mensaje}'")

        if not texto_mensaje:
            print("⚠️ El mensaje no contiene texto válido para procesar.")
            return jsonify({"status": "ok"}), 200

        mensaje_limpio = texto_mensaje.strip().lower()
        data_rifa = obtener_data_completa()
        rifa = data_rifa["numeros"]
        solicitudes = data_rifa.get("solicitudes_pendientes", {})
        idiomas = data_rifa.get("idiomas_usuarios", {})
        estado_actual_rifa = data_rifa.get("estado_rifa", "activa")

        user_id = remitente_jid.split("@")[0]
        lang_usuario = idiomas.get(user_id, "es")

        # --- COMANDOS DE IDIOMA ---
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
            print(f"🚀 Enviando lista general a {remitente_jid}")
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

                    if all(rifa[str(n)]["estado"] == "ocupado" for n in range(1, 101)):
                        data_rifa["estado_rifa"] = "finalizada"

                    guardar_data_completa(data_rifa)

                    texto_pago_confirmado = (
                        f"🎉 *¡PAGO CONFIRMADO! / PAGAMENTO CONFIRMADO!* 🎉\n\n"
                        f"👤 *Usuario:* @{u_jid.split('@')[0]}\n"
                        f"🎟️ *Números:* {nums_formatted}\n\n"
                        f"¡Muchas felicidades! / Parabéns! 🤝"
                    )

                    if GRUPO_WHATSAPP_JID:
                        enviar_mensaje_whatsapp(GRUPO_WHATSAPP_JID, texto_pago_confirmado)
                    enviar_mensaje_whatsapp(u_jid, texto_pago_confirmado)
                    enviar_mensaje_whatsapp(remitente_jid, f"✅ Solicitud `{req_id}` aprobada con éxito.")
                else:
                    enviar_mensaje_whatsapp(remitente_jid, f"⚠️ Solicitud `{req_id}` no encontrada.")
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

                    enviar_mensaje_whatsapp(u_jid, f"❌ Tu solicitud para los números *{nums_formatted}* fue rechazada.")
                    enviar_mensaje_whatsapp(remitente_jid, f"❌ Solicitud `{req_id}` rechazada.")
                else:
                    enviar_mensaje_whatsapp(remitente_jid, f"⚠️ Solicitud `{req_id}` no encontrada.")
                return jsonify({"status": "ok"}), 200

        # --- SELECCIÓN DE NÚMEROS POR EL CLIENTE ---
        partes = [p.strip() for p in texto_mensaje.split(",")]
        es_lista_numeros = all(p.isdigit() for p in partes) if partes else False
        print(f"🔢 ¿Es selección de números?: {es_lista_numeros} ({partes})")

        if es_lista_numeros:
            if estado_actual_rifa in ["finalizada", "bloqueada"]:
                enviar_mensaje_whatsapp(remitente_jid, "⛔ La lista se encuentra cerrada o bloqueada en este momento.")
                return jsonify({"status": "ok"}), 200

            validos_para_reservar = []
            for p in partes:
                num_elegido = int(p)
                if 1 <= num_elegido <= 100:
                    num_str = str(num_elegido)
                    est = rifa[num_str].get("estado", "disponible")
                    if est == "disponible":
                        validos_para_reservar.append(num_str)

            print(f"✅ Números válidos reservados: {validos_para_reservar}")
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

                total_a_pagar = calcular_precio_total(len(validos_para_reservar), usuario_ya_tiene_compras=ya_tiene_compras)

                msj_cliente = (
                    f"⏳ *SOLICITUD EN PROCESO* ⏳\n\n"
                    f"Hola @{user_id}, tus números (*{', '.join([n.zfill(2) for n in validos_para_reservar])}*) están reservados.\n"
                    f"💵 Total a transferir: *{total_a_pagar} reales*\n\n"
                    f"Contacta al administrador para pagar."
                )
                enviar_mensaje_whatsapp(remitente_jid, msj_cliente)

                txt_admin = (
                    f"📥 *NUEVA SOLICITUD* (ID: `{req_id}`)\n\n"
                    f"👤 *Cliente:* @{user_id} ({push_name})\n"
                    f"🎟️ *Números:* *{', '.join([n.zfill(2) for n in validos_para_reservar])}*\n"
                    f"💵 *Total:* *{total_a_pagar} reales*\n\n"
                    f"👉 Aprobar: `/aprobar {req_id}`\n"
                    f"👉 Rechazar: `/rechazar {req_id}`"
                )
                if ADMIN_WHATSAPP_JID:
                    enviar_mensaje_whatsapp(ADMIN_WHATSAPP_JID, txt_admin)

    except Exception as e:
        print(f"❌ Error procesando webhook: {e}")

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    inicializar_bd()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
