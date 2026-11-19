# Admisiones UNIMINUTO por WhatsApp — IA + panel de asesores

Los aspirantes escriben por WhatsApp y una IA (Gemini) les responde de forma natural con la información oficial.
Cuando hace falta una persona, la IA marca el chat como **"necesita asesor"** y el asesor de turno lo toma desde un panel web.

```
Aspirante ─WhatsApp─▶ Meta ─webhook─▶ FastAPI (Render) ─▶ Gemini
                                         │  ▲
                                 Postgres (Neon)   Panel web de asesores (/panel)
```

## Versión local (un PC enciende el bot) — Windows

1. Doble clic en **`INICIAR_BOT.bat`**. La primera vez instala Python, las dependencias y ngrok, y pide las claves
   (Gemini, authtoken de ngrok y, cuando exista, el token de WhatsApp). Para cambiarlas después: **`CONFIGURAR_CLAVES.bat`**.
2. Deja la ventana abierta. Muestra la dirección del panel para los asesores (`https://<dominio>.ngrok-free.dev`) y la URL del webhook para Meta.
3. Mientras la ventana esté abierta, el PC no se suspende. Si se cae el internet, el túnel se reconecta solo.
4. Los datos quedan en `local.db` dentro de la carpeta. Cada 6 horas se guarda una copia automática en `respaldos/` (se conservan las últimas 14).
5. Si el bot está apagado, WhatsApp reintenta entregar los mensajes hasta 7 días; al encenderlo, la IA responde con una disculpa por la demora.

Dos líneas de WhatsApp: cada aspirante recibe respuesta por la misma línea a la que escribió, y en el panel se ve la línea de cada chat.

## Cómo funciona

| Situación | Qué pasa |
|---|---|
| Pregunta normal | Responde la IA con la información de "Información del bot" |
| Pide una persona, está molesto, quiere inscribirse/pagar, homologar, o la IA no sabe | La IA responde y marca el chat 🔔 **Necesita asesor** |
| Hay asesores **En turno** con el panel abierto | Suena una alerta y llega una notificación del navegador |
| No hay asesores en turno | La IA sigue ayudando, avisa el horario y el chat queda en cola |
| Un asesor **toma el chat** (o simplemente escribe) | La IA se calla en ese chat; el asesor responde desde el panel y le llega por WhatsApp |
| El asesor **devuelve el chat a la IA** | La IA continúa y ve lo que habló el asesor |
| El asesor deja el chat abandonado 8 h | Vuelve solo a la IA |

### Roles
- La **primera cuenta** que se registra es **administrador**.
- Los demás asesores se registran en `/registro` y quedan **pendientes** hasta que un administrador los apruebe (pestaña *Asesores*).
- Solo administradores editan la **Información del bot** y gestionan asesores.
- Si un asesor olvida su contraseña, el administrador toca **Restablecer clave** y le da la temporal; cada quien la cambia en **Mi cuenta**.
- Si un aspirante escribe **"eliminar mis datos"**, se borra su conversación (lo exige la política de privacidad en `/privacidad`).

### Panel (`https://TU-APP.onrender.com`)
- **Chats:** bandeja en vivo con filtros (Todas, Necesitan asesor, Mis chats, Con asesor, Con IA) y búsqueda.
- **Probar bot:** conversa con la IA como si fueras aspirante, sin usar WhatsApp.
- **Información del bot:** todo lo que la IA sabe (programas, precios, contactos). Los cambios aplican al instante.
- **Asesores:** aprobar, desactivar, dar rol de administrador.
- Interruptor **En turno**: actívalo al empezar tu turno para recibir alertas. Funciona en el celular también.

## Estructura

| Archivo | Para qué |
|---|---|
| `app/main.py` | Servidor, webhook de WhatsApp, páginas, keep-alive |
| `app/bot.py` | Flujo de cada mensaje: IA o asesor |
| `app/ia.py` | Personalidad, reglas y detección de traspaso a asesor |
| `app/panel.py` | API del panel (cuentas, bandeja, tomar/devolver, simulador) |
| `app/db.py` | Tablas: asesores, conversaciones, mensajes, ajustes |
| `app/whatsapp.py` | Envío, audios, firma de Meta |
| `web/` | Panel (HTML/CSS/JS sin frameworks) |
| `conocimiento/universidad.md` | Información inicial (luego se edita desde el panel) |

## Despliegue gratis 24/7

1. **Neon** (neon.tech) → crear proyecto → copiar *connection string* → `DATABASE_URL`.
2. **Google AI Studio** (aistudio.google.com) → *Get API key* → `GEMINI_API_KEY`.
3. **GitHub** → repositorio privado con este código.
4. **Render** → *New → Blueprint* → elegir el repo (usa `render.yaml`) → llenar variables.
   El servicio gratis se visita a sí mismo cada 10 min (`MINUTOS_KEEPALIVE`) para no dormirse.
   Recomendado además: un monitor gratis (UptimeRobot) a `https://TU-APP.onrender.com/salud` cada 5 min.
5. Abrir `https://TU-APP.onrender.com/registro` y crear la cuenta de administrador **antes que nadie**.
6. **Meta for Developers** → app tipo *Empresa* → producto WhatsApp:
   - Copiar *Phone number ID* → `WHATSAPP_PHONE_NUMBER_ID`.
   - Token permanente (Usuario del sistema con permisos `whatsapp_business_messaging` y `whatsapp_business_management`) → `WHATSAPP_TOKEN`.
   - *Configuración de la app → Básica → Clave secreta* → `WHATSAPP_APP_SECRET`.
   - *WhatsApp → Configuración → Webhook*: URL `https://TU-APP.onrender.com/webhook`, token = `WHATSAPP_VERIFY_TOKEN` (en Render), suscribirse a **messages**.
   - Con el número de prueba, agregar los números que pueden escribir (máximo 5).

### Pasar a producción
- Registrar el número oficial de admisiones (que no esté en la app de WhatsApp) y verificar el negocio en Meta.
- Cambiar `WHATSAPP_PHONE_NUMBER_ID`. No hay que tocar código.
- Si el volumen crece, pasar Render a plan *Starter* y poner `MINUTOS_KEEPALIVE=0`.

### Reglas de WhatsApp a tener en cuenta
- Se puede responder libremente hasta **24 h** después del último mensaje del aspirante. Después, el panel avisa y bloquea el envío.
- Las conversaciones que inicia el aspirante (atención al cliente) no tienen costo en la API de Meta.

## Local y pruebas
```bash
python -m venv venv && venv\Scripts\activate      # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt pytest
copy .env.example .env                            # y pon tu GEMINI_API_KEY
uvicorn app.main:app --reload                     # http://localhost:8000
pytest -q
```
