import os
import json
import requests
import re
import uuid
from flask import Flask, request, jsonify

app = Flask(__name__)

# 🔑 CONFIGURACIÓN DINÁMICA DESDE LAS VARIABLES DE ENTORNO DE RENDER
SERVER_URL = os.getenv("SERVER_URL", "https://mi-whatsapp-api-pobo.onrender.com")
AUTHENTICATION_API_KEY = os.getenv("AUTHENTICATION_API_KEY", "55725d7c0b0fb17cb5e6564edac38c1f")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "mi-bot")

# 🔑 ID DE RESPALDO DE TU GRUPO
GRUPO_CHAT_ID_RESPALDO = "DyI3ISDPZjyKw3w0cD8elC@g.us"

# 👑 1. ADMINISTRADOR GENERAL
WHATSAPP_ADMIN_PHONE = "5511948824359" 
WHATSAPP_ADMIN_CHAT_ID = f"{WHATSAPP_ADMIN_PHONE}@s.whatsapp.net"
NUMERO_ADMIN_SEGURO = "48824359" 

# 🤖 2. BOT ASISTENTE ENCARGADO
BOT_ASISTENTE_PHONE = "5562993984530"

# 🔑 CLAVE SECRETA DE ADMINISTRADOR
CLAVE_RESET = "admin.resetear.rifa.99"

DB_FILE = "rifa_db.json"

mensajes_procesados_recientes = set()

def inicializar_rifa():
    try:
        if not os.path.exists(DB_FILE):
            data_inicial = {
                "estado_rifa": "activa",
                "numeros": {str(i): {"estado": "disponible", "nombre": "", "telefono": "", "enlace": "", "solicitud_id": ""} for i in range(1, 101)},
                "solicitudes_pendientes": {}
            }
            with open(DB_FILE, "w") as f:
                json.dump(data_inicial, f, indent=4)
    except Exception as e:
        print(f"🔴 Error al inicializar JSON: {e}")

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
        print(f"🔴 Error al guardar JSON: {e}")

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
            nombre_ocupante = info.get("nombre", "Participante")
            telefono_ocupante = info.get("telefono", "")
            
            if telefono_ocupante:
                # Nombre en azul con enlace directo al chat privado al lado del número
                texto += f"🔴 *{num_str}*: Ocupado por [{nombre_ocupante}](https://wa.me/{telefono_ocupante})\n"
            else:
                texto += f"🔴 *{num_str}*: Ocupado por *{nombre_ocupante}*\n"
            
    texto += f"\n📊 *Resumen:* Quedan {disponibles} números disponibles."
    if data.get("estado_rifa") == "finalizada":
        texto += "\n\n🔒 *ESTADO:* Rifa cerrada/finalizada."
    return texto

def enviar_mensaje_evolution(chat_id, texto, menciones=[]):
    url_envio = f"{SERVER_URL.rstrip('/')}/message/sendText/{INSTANCE_NAME}"
    payload = {
        "number": chat_id,
        "text": texto
    }
    if menciones:
        payload["options"] = {"mentioned": menciones}

    headers = {
        "apikey": AUTHENTICATION_API_KEY,
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(url_envio, json=payload, headers=headers)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"🔴 Error al enviar a Evolution API: {e}")
        return False

@app.route("/", methods=["GET"])
def home():
    return "Servidor conectado con Evolution API listo.", 200

@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    try:
        data_webhook = request.get_json(silent=True)
        if not data_webhook:
            return "No data", 200

        event = data_webhook.get("event", "").lower()
        if "messages.upsert" not in event and "messages_upsert" not in event:
            return "Ignored event", 200

        data_msg = data_webhook.get("data", {})
        
        if data_msg.get("key", {}).get("fromMe", False):
            return "Ignored fromMe", 200

        msg_id = data_msg.get("key", {}).get("id", "")
        if msg_id in mensajes_procesados_recientes:
            return "Ignored duplicate", 200
        if msg_id:
            mensajes_procesados_recientes.add(msg_id)
            if len(mensajes_procesados_recientes) > 100:
                mensajes_procesados_recientes.pop()

        message_content = data_msg.get("message", {})
        mensaje_texto = message_content.get("conversation", "") or message_content.get("extendedTextMessage", {}).get("text", "")
        mensaje_texto = mensaje_texto.strip()
        comando = mensaje_texto.lower()

        if not mensaje_texto:
            return "No text", 200

        # Extracción segura y directa del emisor real del mensaje para evitar confusiones con el admin
        key_data = data_msg.get("key", {})
        remote_jid = key_data.get("remoteJid", "")
        participant = key_data.get("participant", "")
        
        jid_crudo = participant if participant else remote_jid
        digitos_puros = re.sub(r'\D', '', jid_crudo)
        
        if len(digitos_puros) >= 8:
            if "@s.whatsapp.net" in jid_crudo or "@c.us" in jid_crudo or "@g.us" not in jid_crudo:
                numero_persona = digitos_puros[-13:] if len(digitos_puros) >= 13 else digitos_puros
            else:
                numero_persona = digitos_puros[-11:]
        else:
            numero_persona = digitos_puros

        user_chat_id = f"{numero_persona}@s.whatsapp.net"

        push_name = data_msg.get("pushName", "")
        nombre_usuario = push_name.strip() if push_name else f"Usuario_{numero_persona[-4:]}"

        data_rifa = obtener_data_completa()
        rifa = data_rifa["numeros"]
        solicitudes = data_rifa.get("solicitudes_pendientes", {})
        estado_actual_rifa = data_rifa.get("estado_rifa", "activa")
        
        es_admin_general = (NUMERO_ADMIN_SEGURO in numero_persona) or (WHATSAPP_ADMIN_PHONE in numero_persona)

        if comando == CLAVE_RESET:
            if not es_admin_general:
                return "OK", 200
            borrar_y_recrear_base_datos()
            respuesta = "🔄 *¡La rifa ha sido reseteada con éxito!* Todos los números vuelven a estar disponibles.\n\n" + generar_texto_lista()
            enviar_mensaje_evolution(remote_jid, respuesta)
            return "OK", 200

        elif comando.startswith("confirmar ") or comando.startswith("rechazar "):
            if not es_admin_general:
                return "OK", 200
            
            partes_cmd = mensaje_texto.strip().split()
            accion = partes_cmd[0].lower()
            req_id_input = partes_cmd[1].strip() if len(partes_cmd) > 1 else ""

            req_id_encontrado = None
            for key in solicitudes.keys():
                if key.lower() == req_id_input.lower():
                    req_id_encontrado = key
                    break

            if req_id_encontrado:
                sol = solicitudes[req_id_encontrado]
                user_nombre = sol["nombre"]
                user_phone_clean = sol["telefono_limpio"]
                user_nums = sol["numeros"]
                grupo_origen = sol["grupo_id"]
                target_chat_id = sol["chat_id"]

                nums_formatted = ", ".join([n.zfill(2) for n in user_nums])

                if accion == "confirmar":
                    for n in user_nums:
                        rifa[n]["estado"] = "ocupado"
                        rifa[n]["nombre"] = user_nombre
                        rifa[n]["telefono"] = user_phone_clean
                        rifa[n]["enlace"] = f"https://wa.me/{user_phone_clean}"

                    del solicitudes[req_id_encontrado]
                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes

                    if all(rifa[str(n)]["estado"] == "ocupado" for n in range(1, 101)):
                        data_rifa["estado_rifa"] = "finalizada"

                    guardar_data_completa(data_rifa)

                    enviar_mensaje_evolution(remote_jid, f"✅ *Solicitud {req_id_encontrado} APROBADA.*")

                    msg_grupo = f"🎉 *¡PAGO CONFIRMADO!* 🎉\n\n👤 *Participante:* [{user_nombre}](https://wa.me/{user_phone_clean})\n🎟️ *Números:* *{nums_formatted}*\n\n¡Felicidades! 🤝"
                    enviar_mensaje_evolution(grupo_origen, msg_grupo, menciones=[target_chat_id])
                    
                    msg_privado = f"🎉 *¡Hola {user_nombre}!* 🎉\n\nTu pago fue verificado. Tus números (*{nums_formatted}*) ya están registrados a tu nombre."
                    enviar_mensaje_evolution(target_chat_id, msg_privado)

                elif accion == "rechazar":
                    for n in user_nums:
                        rifa[n] = {"estado": "disponible", "nombre": "", "telefono": "", "enlace": "", "solicitud_id": ""}

                    del solicitudes[req_id_encontrado]
                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes
                    guardar_data_completa(data_rifa)

                    enviar_mensaje_evolution(remote_jid, f"❌ *Solicitud {req_id_encontrado} RECHAZADA.*")
                    msg_grupo = f"⚠️ *SOLICITUD CANCELADA* ⚠️\n\nHola *{user_nombre}*, tu solicitud para el/los número(s) *{nums_formatted}* fue rechazada."
                    enviar_mensaje_evolution(grupo_origen, msg_grupo, menciones=[target_chat_id])

            else:
                enviar_mensaje_evolution(remote_jid, f"⚠️ No se encontró la solicitud ID: `{req_id_input}`.")
            return "OK", 200

        elif comando in ["hola", "buenas", "lista", "inicio", "rifa"]:
            respuesta = (
                f"¡Hola *{nombre_usuario}*! Aquí tienes el estado actual de la Rifa. ✨\n\n"
                f"💵 *Compra uno o varios números por 10 reales y gana 400 reales.*\n\n"
                f"{generar_texto_lista()}"
            )
            if estado_actual_rifa == "activa":
                respuesta += "\n\n👉 *¿Cómo comprar?* Responde escribiendo el número que deseas (ej: *7, 14*)."
            
            enviar_mensaje_evolution(remote_jid, respuesta)
            return "OK", 200

        else:
            partes = [p.strip() for p in mensaje_texto.split(",")]
            es_lista_numeros = all(p.isdigit() for p in partes) if partes and mensaje_texto else False

            if es_lista_numeros:
                if estado_actual_rifa == "finalizada":
                    enviar_mensaje_evolution(remote_jid, "🔒 *Lo sentimos, el sistema está cerrado.*")
                    return "OK", 200

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
                    mensajes_conflicto.append(f"🟡 El/los número(s) {', '.join(pendientes)} está(n) *EN PROCESO*.")
                if invalidos:
                    mensajes_conflicto.append(f"⚠️ El/los número(s) {', '.join(invalidos)} fuera de rango.")

                if mensajes_conflicto and not validos_para_reservar:
                    resp_conflicto = f"Hola *{nombre_usuario}*:\n" + "\n".join(mensajes_conflicto)
                    enviar_mensaje_evolution(remote_jid, resp_conflicto)
                    return "OK", 200

                if validos_para_reservar:
                    req_id = "r" + str(uuid.uuid4().int)[:4]

                    for n in validos_para_reservar:
                        rifa[n]["estado"] = "pendiente"
                        rifa[n]["solicitud_id"] = req_id

                    solicitudes[req_id] = {
                        "nombre": nombre_usuario,
                        "telefono_limpio": numero_persona,
                        "chat_id": user_chat_id,
                        "numeros": validos_para_reservar,
                        "grupo_id": remote_jid if "@g.us" in remote_jid else GRUPO_CHAT_ID_RESPALDO
                    }

                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes
                    guardar_data_completa(data_rifa)

                    nums_solicitados_txt = ", ".join([n.zfill(2) for n in validos_para_reservar])

                    # Mensaje al grupo sin enlaces sueltos para evitar la tarjeta de vista previa
                    txt_grupo = (
                        f"⏳ *SOLICITUD RECIBIDA* ⏳\n\n"
                        f"Hola @{numero_persona}, recibimos tu pedido para el/los número(s): *{nums_solicitados_txt}*.\n\n"
                        f"🟡 Quedan *reservados temporalmente* mientras el administrador verifica tu pago."
                    )
                    if mensajes_conflicto:
                        txt_grupo += "\n\n📌 *Nota:* " + " \n".join(mensajes_conflicto)

                    enviar_mensaje_evolution(remote_jid, txt_grupo, menciones=[user_chat_id])

                    # Mensaje al administrador con el nombre del usuario convertido en enlace interactivo (sin URLs sueltas)
                    txt_admin = (
                        f"📥 *NUEVA SOLICITUD DE COMPRA* (ID: `{req_id}`)\n\n"
                        f"👤 *Cliente:* [{nombre_usuario}](https://wa.me/{numero_persona})\n"
                        f"🎟️ *Números:* *{nums_solicitados_txt}*\n\n"
                        f"Para responder, haz clic en:\n"
                        f"🟢 [CONFIRMAR PAGO](https://wa.me/{BOT_ASISTENTE_PHONE}?text=confirmar%20{req_id})\n\n"
                        f"🔴 [RECHAZAR PAGO](https://wa.me/{BOT_ASISTENTE_PHONE}?text=rechazar%20{req_id})"
                    )
                    
                    enviar_mensaje_evolution(WHATSAPP_ADMIN_CHAT_ID, txt_admin)
                    return "OK", 200

    except Exception as e_global:
        print(f"💥 ERROR CRÍTICO: {e_global}")

    return "OK", 200

if __name__ == "__main__":
    inicializar_rifa()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
