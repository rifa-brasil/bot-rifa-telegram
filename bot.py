import os
import json
import uuid
import requests
import psycopg2
from urllib.parse import urlparse
from flask import Flask, request, jsonify

app = Flask(__name__)

EVOLUTION_API_URL = os.environ.get("EVOLUTION_API_URL", "https://mi-whatsapp-api-pobo.onrender.com").rstrip("/")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "55725d7c0b0fb17cb5e6564edac38c1f")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "mi-bot")
ADMIN_WHATSAPP_JID = os.environ.get("ADMIN_WHATSAPP_JID", "5511948824359@s.whatsapp.net")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
VALOR_POR_NUMERO = 10

def get_db_connection():
    if DATABASE_URL:
        url = urlparse(DATABASE_URL)
        return psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
    return None

def inicializar_bd():
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS rifa_estado (id INT PRIMARY KEY, data JSONB);")
        cur.execute("SELECT data FROM rifa_estado WHERE id = 1;")
        if not cur.fetchone():
            data_inicial = {
                "estado_rifa": "activa",
                "numeros": {str(i): {"estado": "disponible", "nombre": "", "user_id": ""} for i in range(1, 101)},
                "solicitudes_pendientes": {},
                "idiomas_usuarios": {}
            }
            cur.execute("INSERT INTO rifa_estado (id, data) VALUES (1, %s);", (json.dumps(data_inicial),))
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error BD: {e}")

def obtener_data_completa():
    conn = get_db_connection()
    if not conn:
        return {
            "estado_rifa": "activa",
            "numeros": {str(i): {"estado": "disponible", "nombre": "", "user_id": ""} for i in range(1, 101)},
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
        print(f"Error al guardar BD: {e}")

def enviar_mensaje_whatsapp(destinatario_jid, texto):
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        return
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": destinatario_jid, "text": texto, "options": {"delay": 1200, "presence": "composing"}}
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"Error enviando mensaje: {e}")

def calcular_premio_total():
    premio = (100 * VALOR_POR_NUMERO) * 0.55
    return int(premio) if premio.is_integer() else round(premio, 2)

def calcular_precio_total(cantidad, usuario_ya_tiene_compras=False):
    if cantidad <= 0: return 0
    if usuario_ya_tiene_compras: return cantidad * VALOR_POR_NUMERO
    
    total, restantes = 0, cantidad
    p5, p4, p3, p2, p1 = int(VALOR_POR_NUMERO * 4), int(VALOR_POR_NUMERO * 3.5), int(VALOR_POR_NUMERO * 2.5), int(VALOR_POR_NUMERO * 1.5), VALOR_POR_NUMERO
    
    if restantes >= 5:
        total += p5
        restantes -= 5
    elif restantes == 4: return p4
    elif restantes == 3: return p3
    elif restantes == 2: return p2
    elif restantes == 1: return p1

    if restantes > 0: total += restantes * VALOR_POR_NUMERO
    return total

def usuario_tiene_jugada_previa(user_id, data_completa):
    for _, info in data_completa.get("numeros", {}).items():
        if info.get("user_id") == user_id and info.get("estado") in ["ocupado", "pendiente"]:
            return True
    for _, sol in data_completa.get("solicitudes_pendientes", {}).items():
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
        estado = rifa[str(i)].get("estado", "disponible")
        if estado == "disponible":
            texto += f"🟢 *{num_str}*: Disponible\n"
            disponibles += 1
        elif estado == "pendiente":
            texto += f"🟡 *{num_str}*: En verificación...\n"
        else:
            nombre = rifa[str(i)].get("nombre", "Usuario")
            texto += f"🔴 *{num_str}*: Ocupado por {nombre}\n"
    texto += f"\n📊 *Resumen:* Quedan {disponibles} números disponibles."
    return texto

@app.route("/", methods=["GET"])
def index():
    return "Bot Activo", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "ignored"}), 200

        msg_data = data.get("data", {})
        key = msg_data.get("key", {})
        if key.get("fromMe"):
            return jsonify({"status": "ok"}), 200

        remitente_jid = key.get("remoteJid") or msg_data.get("sender", "")
        push_name = msg_data.get("pushName", "Usuario")
        
        message_body = msg_data.get("message", {})
        texto_mensaje = ""
        if isinstance(message_body, dict):
            texto_mensaje = message_body.get("conversation", "") or message_body.get("extendedTextMessage", {}).get("text", "") or message_body.get("text", "")
        elif isinstance(message_body, str):
            texto_mensaje = message_body

        if not texto_mensaje or not remitente_jid:
            return jsonify({"status": "ok"}), 200

        mensaje_limpio = texto_mensaje.strip().lower()
        data_rifa = obtener_data_completa()
        rifa = data_rifa["numeros"]
        solicitudes = data_rifa.get("solicitudes_pendientes", {})
        user_id = remitente_jid.split("@")[0]

        if mensaje_limpio in ["hola", "buenas", "lista", "inicio", "rifa", "sorteo"]:
            enviar_mensaje_whatsapp(remitente_jid, f"¡Hola @{user_id}! Estado actual:\n\n{generar_texto_lista()}")
            return jsonify({"status": "ok"}), 200

        if mensaje_limpio == "reglas":
            enviar_mensaje_whatsapp(remitente_jid, f"📌 *Reglas:* 100 números. Premio: *{calcular_premio_total()} reales*.")
            return jsonify({"status": "ok"}), 200

        partes = [p.strip() for p in texto_mensaje.split(",")]
        if all(p.isdigit() for p in partes) if partes else False:
            validos = [str(int(p)) for p in partes if 1 <= int(p) <= 100 and rifa[str(int(p))].get("estado") == "disponible"]
            if validos:
                ya_tiene = usuario_tiene_jugada_previa(user_id, data_rifa)
                req_id = "r" + str(uuid.uuid4().int)[:4]
                for n in validos:
                    rifa[n]["estado"] = "pendiente"
                solicitudes[req_id] = {"nombre": push_name, "user_id": remitente_jid, "numeros": validos}
                guardar_data_completa(data_rifa)
                total = calcular_precio_total(len(validos), ya_tiene)
                enviar_mensaje_whatsapp(remitente_jid, f"⏳ Solicitud recibida para: {', '.join([n.zfill(2) for n in validos])}. Total: {total} reales.")

    except Exception as e:
        print(f"Error en webhook: {e}")

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    inicializar_bd()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
