import os
import json
import requests
import re
import uuid
from datetime import datetime, timedelta
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

# 🤖 2. BOT ASISTENTE ENCARGADO (5562993984530)
BOT_ASISTENTE_PHONE = "5562993984530"

# 🔑 CLAVE SECRETA DE ADMINISTRADOR
CLAVE_RESET = "admin.resetear.rifa.99"

DB_FILE = "rifa_db.json"

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
        print(f"🔴 Error al leer JSON (recreando base): {e}")
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
            if info.get("enlace"):
                link = info["enlace"]
            elif info.get("telefono"):
                tel_limpio = info["telefono"].replace("+", "").strip()
                link = f"wa.me/{tel_limpio}"
            else:
                link = ""

            if link:
                texto += f"🔴 *{num_str}*: Ocupado por {info['nombre']} 👉 {link}\n"
            else:
                texto += f"🔴 *{num_str}*: Ocupado por {info['nombre']}\n"
            
    texto += f"\n📊 *Resumen:* Quedan {disponibles} números disponibles."
    if data.get("estado_rifa") == "finalizada":
        texto += "\n\n🔒 *ESTADO:* Rifa cerrada/finalizada. No se permiten más modificaciones."
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
        print(f"📤 Envío a {chat_id}: Estado {r.status_code} -> Respuesta: {r.text}")
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"🔴 Error al enviar a Evolution API: {e}")
        return False

@app.route("/", methods=["GET"])
def home():
    return "Servidor conectado con Evolution API listo.", 200

@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    print("🚨 ¡ALGUIEN TOCÓ LA RUTA /WEBHOOK! 🚨")
    try:
        data_webhook = request.get_json(silent=True)
        if not data_webhook:
            # Por si acaso llega en formato form o texto plano
            data_webhook = request.form.to_dict()

        if not data_webhook:
            print("⚠️ Webhook recibido sin datos JSON legibles.")
            return "No data", 200

        # REGISTRO CLAVE PARA VER QUÉ RECIBE RENDER
        print(f"📥 JSON RECIBIDO: {json.dumps(data_webhook, indent=2)}")

        event = data_webhook.get("event", "").lower()
        if "messages.upsert" not in event and "messages_upsert" not in event:
            print(f"⚠️ Evento ignorado o desconocido: {event}")
            return "Ignored event", 200

        data_msg = data_webhook.get("data", {})
        
        if data_msg.get("key", {}).get("fromMe", False):
            return "Ignored fromMe", 200

        message_content = data_msg.get("message", {})
        mensaje_texto = message_content.get("conversation", "") or message_content.get("extendedTextMessage", {}).get("text", "")
        mensaje_texto = mensaje_texto.strip()
        comando = mensaje_texto.lower()

        if not mensaje_texto:
            return "No text", 200

        if "lista oficial de la rifa" in comando or "participantes convocados" in comando or "tenemos un ganador" in comando:
            return "Ignored loop", 200

        remote_jid = data_msg.get("key", {}).get("remoteJid", "")
        
        numero_persona = re.sub(r'\D', '', remote_jid.split("@")[0])
        
        push_name = data_msg.get("pushName", "")
        nombre_usuario = push_name.strip() or f"+{numero_persona}"

        data_rifa = obtener_data_completa()
        rifa = data_rifa["numeros"]
        solicitudes = data_rifa.get("solicitudes_pendientes", {})
        estado_actual_rifa = data_rifa.get("estado_rifa", "activa")
        
        respuesta = ""

        es_admin_general = (NUMERO_ADMIN_SEGURO in numero_persona) or (WHATSAPP_ADMIN_PHONE in numero_persona)

        if comando == CLAVE_RESET:
            if not es_admin_general:
                return "OK", 200
            borrar_y_recrear_base_datos()
            respuesta = "🔄 *¡La rifa ha sido reseteada con éxito!* Todos los 100 números vuelven a estar disponibles y el sistema está abierto.\n\n" + generar_texto_lista()

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
                user_phone = sol["telefono"]
                user_nums = sol["numeros"]
                grupo_origen = sol["grupo_id"]

                nums_formatted = ", ".join([n.zfill(2) for n in user_nums])

                if accion == "confirmar":
                    for n in user_nums:
                        rifa[n]["estado"] = "ocupado"
                        rifa[n]["nombre"] = user_nombre
                        rifa[n]["telefono"] = user_phone
                        rifa[n]["enlace"] = f"wa.me/{user_phone.replace('+', '').strip()}"

                    del solicitudes[req_id_encontrado]
                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes

                    todos_ocupados = all(rifa[str(n)]["estado"] == "ocupado" for n in range(1, 101))
                    if todos_ocupados:
                        data_rifa["estado_rifa"] = "finalizada"

                    guardar_data_completa(data_rifa)

                    numero_limpio = user_phone.replace("+", "").strip()
                    user_chat_id = f"{numero_limpio}@s.whatsapp.net"

                    enviar_mensaje_evolution(remote_jid, f"✅ *Solicitud {req_id_encontrado} APROBADA.* Los números ({nums_formatted}) fueron asignados exitosamente a {user_nombre}.")

                    msg_grupo = f"🎉 *¡PAGO CONFIRMADO!* 🎉\n\n👤 *Usuario:* @{numero_limpio}\n🎟️ *Números asignados:* *{nums_formatted}*\n\n¡Gracias por tu compra y mucha suerte! 🤝"
                    enviar_mensaje_evolution(grupo_origen, msg_grupo, menciones=[user_chat_id])
                    
                    msg_privado = f"🎉 *¡Hola {user_nombre}!* 🎉\n\nTe confirmo que tu pago ha sido verificado con éxito. Tus números (*{nums_formatted}*) ya están registrados oficialmente a tu nombre en el grupo de la rifa.\n\n¡Mucha suerte! 🍀"
                    enviar_mensaje_evolution(user_chat_id, msg_privado)

                elif accion == "rechazar":
                    for n in user_nums:
                        rifa[n] = {"estado": "disponible", "nombre": "", "telefono": "", "enlace": "", "solicitud_id": ""}

                    del solicitudes[req_id_encontrado]
                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes
                    guardar_data_completa(data_rifa)

                    enviar_mensaje_evolution(remote_jid, f"❌ *Solicitud {req_id_encontrado} RECHAZADA.* Los números ({nums_formatted}) vuelven a estar disponibles.")
                    
                    msg_grupo = f"⚠️ *SOLICITUD CANCELADA* ⚠️\n\nHola {user_nombre}, tu solicitud para el/los número(s) *{nums_formatted}* fue rechazada. Los números vuelven a estar 🟢 *Disponibles* para los demás participantes."
                    enviar_mensaje_evolution(grupo_origen, msg_grupo)

            else:
                enviar_mensaje_evolution(remote_jid, f"⚠️ No se encontró la solicitud ID: `{req_id_input}` o ya fue procesada.")
            return "OK", 200

        elif comando in ["hola", "buenas", "lista", "inicio", "rifa"]:
            respuesta = (
                f"¡Hola {nombre_usuario}! Aquí tienes el estado actual de la Rifa. ✨\n\n"
                f"💵 *Compra uno o varios números por un valor de 10 reales y gana 400 reales.*\n"
                f"🏆 El premio se entregará aquí en Brasil mediante transferencia PIX o al familiar en Cuba en CUP.\n\n"
                f"{generar_texto_lista()}"
            )
            if estado_actual_rifa == "activa":
                respuesta += "\n\n👉 *¿Cómo comprar?* Responde escribiendo el número que deseas (ej: *7, 14*)."

        else:
            partes = [p.strip() for p in mensaje_texto.split(",")]
            es_lista_numeros = all(p.isdigit() for p in partes) if partes and mensaje_texto else False

            if es_lista_numeros:
                if estado_actual_rifa == "finalizada":
                    respuesta = "🔒 *Lo sentimos, el sistema está cerrado.* El sorteo ya concluyó o está congelado."
                    enviar_mensaje_evolution(remote_jid, respuesta)
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
                    mensajes_conflicto.append(f"🟡 El/los número(s) {', '.join(pendientes)} está(n) *EN PROCESO DE VERIFICACIÓN DE PAGO* por otro participante.")
                if invalidos:
                    mensajes_conflicto.append(f"⚠️ El/los número(s) {', '.join(invalidos)} está(n) fuera del rango (1 al 100).")

                if mensajes_conflicto and not validos_para_reservar:
                    respuesta = f"Hola {nombre_usuario}:\n" + "\n".join(mensajes_conflicto)
                    enviar_mensaje_evolution(remote_jid, respuesta)
                    return "OK", 200

                if validos_para_reservar:
                    req_id = "r" + str(uuid.uuid4().int)[:4]

                    for n in validos_para_reservar:
                        rifa[n]["estado"] = "pendiente"
                        rifa[n]["solicitud_id"] = req_id

                    solicitudes[req_id] = {
                        "nombre": nombre_usuario,
                        "telefono": f"+{numero_persona}",
                        "numeros": validos_para_reservar,
                        "grupo_id": remote_jid if "@g.us" in remote_jid else GRUPO_CHAT_ID_RESPALDO
                    }

                    data_rifa["numeros"] = rifa
                    data_rifa["solicitudes_pendientes"] = solicitudes
                    guardar_data_completa(data_rifa)

                    nums_solicitados_txt = ", ".join([n.zfill(2) for n in validos_para_reservar])

                    txt_grupo = (
                        f"⏳ *SOLICITUD RECIBIDA* ⏳\n\n"
                        f"Hola {nombre_usuario}, recibimos tu pedido para el/los número(s): *{nums_solicitados_txt}*.\n\n"
                        f"🟡 Quedan *reservados temporalmente* mientras el administrador verifica tu transferencia."
                    )
                    if mensajes_conflicto:
                        txt_grupo += "\n\n📌 *Nota:* " + " \n".join(mensajes_conflicto)

                    enviar_mensaje_evolution(remote_jid, txt_grupo)

                    link_confirmar = f"wa.me/{BOT_ASISTENTE_PHONE}?text=confirmar%20{req_id}"
                    link_rechazar = f"wa.me/{BOT_ASISTENTE_PHONE}?text=rechazar%20{req_id}"

                    txt_admin = (
                        f"📥 *NUEVA SOLICITUD DE COMPRA* (ID: `{req_id}`)\n\n"
                        f"👤 *Cliente:* {nombre_usuario}\n"
                        f"📱 *Teléfono:* wa.me/{numero_persona}\n"
                        f"🎟️ *Números:* *{nums_solicitados_txt}*\n\n"
                        f"-----------------------------------\n"
                        f"Toca una opción para responder:\n\n"
                        f"🟢 *[ CONFIRMAR PAGO ]*\n{link_confirmar}\n\n"
                        f"🔴 *[ RECHAZAR PAGO ]*\n{link_rechazar}"
                    )
                    
                    enviar_mensaje_evolution(WHATSAPP_ADMIN_CHAT_ID, txt_admin)
                    return "OK", 200

        if respuesta:
            enviar_mensaje_evolution(remote_jid, respuesta)

    except Exception as e_global:
        print(f"💥 ERROR CRÍTICO: {e_global}")

    return "OK", 200

if __name__ == "__main__":
    inicializar_rifa()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
