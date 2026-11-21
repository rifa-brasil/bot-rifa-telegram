# Bot de rifas Telegram — versión ampliada

## Incluye
- Varias rifas independientes.
- SQLite con historial de compras, pagos, participantes, ganadores y auditoría.
- Reservas en dos fases: 5 minutos para pagar y 5 minutos adicionales para pulsar «Ya realicé el pago».
- Confirmación/rechazo de pagos con botones de administrador.
- Mensajes privados mediante una cuenta dedicada (Telethon).
- Publicación del ganador en el grupo y botón protegido `🏆 Premio Recibido`.
- Panel `/admin`, estadísticas por lista, reset por lista con confirmación y un único botón para generar Excel + JSON.
- Restauración desde JSON validado.
- Consulta del PDF oficial de Florida Lottery para PICK 3, con publicación informativa opcional.

## Instalación local
1. Instala Python 3.11 o superior.
2. Descomprime el proyecto.
3. Ejecuta `pip install -r requirements.txt`.
4. Copia `.env.example` a `.env` y completa las variables.
5. Ejecuta `python main.py`.

## Comandos
- `/start` — muestra la rifa activa del grupo.
- `/admin` — abre el panel privado del administrador.
- `/nueva Lista 2|100|5|-1001234567890|Premio|Valor del premio` — crea una lista independiente con precio y valor del premio.
- `/listas` — muestra las listas activas del grupo para que cada participante elija en cuál jugar.
- `/diagnostico` — (solo administrador) muestra el ID del chat, el GROUP_CHAT_ID configurado y cuántas listas activas hay en ese grupo.
- `/ganador ID_RIFA NUMERO` — publica el ganador en el grupo.
- `/pick3 ID_RIFA on|off` — activa/desactiva el monitor PICK 3 para una rifa.

Los participantes seleccionan la lista con `/listas` cuando hay varias, y escriben números separados por comas, por ejemplo `7, 14, 25`. Escribir `lista` muestra la lista seleccionada.

## Cuenta dedicada de Telegram
La API de bots no puede enviar mensajes aparentando ser una persona. Esta versión utiliza Telethon
con una cuenta propia y autorizada, separada del bot. Para prepararla:
1. Crea/usa una cuenta dedicada de Telegram.
2. Obtén `API_ID` y `API_HASH` en https://my.telegram.org.
3. Genera una `StringSession` de Telethon en un entorno seguro y configura las tres variables.
4. Inicia una conversación con cada participante desde esa cuenta, o asegúrate de que el participante
   haya iniciado el contacto. Telegram puede impedir mensajes privados a usuarios que nunca han
   abierto conversación con esa cuenta.
5. Protege `TELETHON_SESSION` como una contraseña: quien la tenga puede controlar esa cuenta.

Si Telethon no está configurado o Telegram bloquea el DM, el bot no simula que lo envió: registra el
error en los logs y continúa con la notificación grupal.

## Render y persistencia
Configura `DB_PATH` dentro de un disco persistente de Render, por ejemplo `/var/data/rifas.sqlite3`.
No guardes la base en el filesystem efímero del servicio. Configura `PORT` como variable de entorno.
El bot debe ser un servicio web persistente; un reinicio del proceso no debe borrar la base SQLite.

## Copias de seguridad
- Un único botón «Crear copia de seguridad» envía ambos archivos: Excel para consulta y JSON restaurable.
- JSON: copia íntegra restaurable desde el botón de restauración del panel.
Guarda copias fuera del servidor. La restauración sustituye los datos actuales y exige confirmación.

## Florida PICK 3: precaución importante
La consulta usa el PDF oficial:
https://files.floridalottery.com/exptkt/p3.pdf

El monitor registra/publica el resultado oficial de tres cifras y Fireball. No selecciona por sí mismo
el número ganador de la rifa. Tu rifa original usa números 1–100 y PICK 3 produce 000–999; no se debe
inventar una conversión (por ejemplo, últimos dos dígitos) sin que el administrador defina y apruebe
esa regla. La publicación automática está desactivada por defecto (`PICK3_AUTO_ANNOUNCE=false`).

## Pendiente antes de producción
- Definir la regla oficial de conversión del resultado PICK 3 al número 1–100 (si corresponde).
- Confirmar si las rifas nuevas deben aceptar números 1–100 siempre o cualquier cantidad.
- Definir el precio real por número, moneda y premio.
- Configurar la cuenta dedicada y probar DMs con un usuario que haya iniciado conversación.
- Probar en un grupo privado de prueba antes de usar dinero real.

## Aviso
El código es una base funcional ampliada que requiere pruebas en tu entorno, revisión de permisos,
configuración de credenciales y pruebas de restauración antes de producción.


## Flujo de reserva y pago (5 + 5 minutos)
Al enviar números, el bot publica «Solicitud en proceso» en el grupo. Durante los primeros 5 minutos el usuario realiza el pago. Al cumplirse ese plazo aparece el botón «Ya realicé el pago», disponible durante otros 5 minutos y validado por ID de Telegram. Si el usuario lo pulsa, se notifica al administrador y los números quedan reservados hasta la verificación manual. Si no lo pulsa, los números se liberan automáticamente.

## Varias listas simultáneas
Cada lista tiene ID, nombre, grupo, cantidad de números, precio por número, descripción del premio y valor del premio propios. En grupos con más de una lista, el participante debe usar `/listas` y seleccionar una; su selección se mantiene en su conversación con el bot para evitar cruces entre listas.


## Diagnóstico cuando el bot no responde
1. En los logs de Render debe aparecer `Conectado a Telegram como ...` y después `POLLING ACTIVO`.
2. Si aparece un error `Conflict: terminated by other getUpdates request`, hay otra instancia del mismo bot ejecutándose: deja solo una instancia de polling.
3. Si aparece `Unauthorized`, revisa `TELEGRAM_TOKEN`.
4. En el grupo ejecuta `/diagnostico` como administrador del bot. Compara `Chat actual` con `GROUP_CHAT_ID configurado`; deben coincidir exactamente.
5. Comprueba que el bot no esté bloqueado y que pueda enviar mensajes al grupo. Los comandos `/listas` y `/diagnostico` no requieren desactivar el modo privacidad de BotFather.
