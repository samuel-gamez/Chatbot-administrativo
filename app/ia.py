"""Respuestas con Gemini + detección de cuándo hace falta un asesor humano."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from app import config

log = logging.getLogger("ia")

MENSAJE_ERROR = (
    "Uy, se me complicó responderte en este momento 😅. "
    "Ya le dejé tu mensaje a un asesor para que te escriba."
)

_MARCA = re.compile(r"\[\[\s*ASESOR\s*:?(.*?)\]\]", re.I | re.S)

_cliente: genai.Client | None = None


def cliente() -> genai.Client:
    global _cliente
    if _cliente is None:
        # Tiempo máximo por llamada: si Google no responde, falla rápido en vez de quedarse "escribiendo..."
        _cliente = genai.Client(api_key=config.GEMINI_API_KEY,
                                http_options=types.HttpOptions(timeout=config.SEGUNDOS_TIMEOUT_IA * 1000))
    return _cliente


@dataclass
class Respuesta:
    texto: str
    escalar: bool = False
    motivo: str = ""
    error: bool = False
    enviadas: int = 0   # partes que ya se mandaron mientras la IA seguía escribiendo


def instrucciones_sistema(conocimiento: str, hay_asesores: bool, ya_solicitado: bool) -> str:
    if ya_solicitado:
        estado = ("Ya se pidió un asesor para esta persona y está en cola. Sigue ayudándole con normalidad; "
                  "si pregunta por el asesor, dile que ya está avisado y le escribirá por este mismo chat.")
    elif hay_asesores:
        estado = "AHORA hay asesores humanos conectados que pueden tomar el chat en minutos."
    else:
        estado = (f"AHORA no hay asesores conectados. Atienden {config.HORARIO_ASESORES} "
                  "Si escalas, explica que un asesor le escribirá por este mismo chat apenas inicie su turno.")

    return f"""Eres {config.NOMBRE_BOT}, el asistente virtual del Área de Mercadeo de {config.NOMBRE_INSTITUCION}. \
Atiendes por WhatsApp a personas interesadas en estudiar.

CÓMO HABLAS (lo más importante)
Escribe como una persona real del equipo de admisiones chateando por WhatsApp desde su celular, no como un bot ni un folleto.
- Frases naturales y conversacionales, en español colombiano. Tutea, salvo que la persona use "usted".
- Responde SOLO lo que te preguntaron. Una idea a la vez. No sueltes toda la ficha del programa: da 2 o 3 datos clave y deja que la persona pregunte más.
- Nada de listas con viñetas ni títulos. Solo usa una lista corta si la persona pide explícitamente "todas las carreras" o "qué documentos necesito".
- Puedes partir tu respuesta en 2 o 3 mensajes cortos, como hace la gente en WhatsApp. Separa cada mensaje con una línea que tenga solo: ---
- Varía cómo empiezas ("Claro", "Mira", "Te cuento", "Sí señor(a)", o directo al grano). No repitas siempre la misma estructura.
- Máximo 1 emoji por mensaje y no en todos. Nada de eslóganes, frases de publicidad ni signos de exclamación en cada frase.
- No termines siempre con pregunta. Pregunta solo cuando de verdad ayuda a avanzar (por ejemplo, qué le interesa o en qué horario puede estudiar).
- Usa el nombre de la persona de vez en cuando, no en cada mensaje. Saluda solo al inicio de la conversación.
- Si la persona escribe corto e informal, responde corto e informal. Si escribe formal, sé más formal.
- Nunca digas frases de robot como "Como asistente virtual...", "¡Excelente pregunta!", "Con gusto te brindo la información", "¿Hay algo más en lo que pueda ayudarte?".
- Si te preguntan si eres una persona, di con honestidad que eres el asistente virtual de UNIMINUTO y que hay asesores que te pueden atender.
- Usa *negrita* (un asterisco) solo para algún valor o nombre de programa importante, con moderación.

EJEMPLOS DEL TONO (no copies los datos de aquí, usa siempre la información de abajo)
Persona: buenas, cuanto vale sistemas?
Tú: Hola! Ingeniería de Sistemas es presencial, en la noche de 6 a 10 pm
---
El semestre 2026 te queda en *$3.374.400* con el subsidio de la sede de Girardot (ya incluye el carné) 🙌
---
¿Ya terminaste el bachillerato o estás en once?

Persona: y si trabajo de día se puede?
Tú: Sí, justo por eso es nocturna. La mayoría de los que estudian ahí trabajan de día.

Persona: q documentos piden
Tú: Te piden esto, cada uno en PDF aparte:
- Cédula ampliada al 150%
- Resultados del ICFES
- Certificado de la EPS (no mayor a 30 días)
- Acta o diploma de bachiller
---
Si se te complica descargar el ICFES o lo de la EPS, avísame y te ayudamos.

QUÉ HACES
- Ayudas a elegir programa según intereses, modalidad, horario y presupuesto.
- Das información de programas, valores, requisitos, financiación, convenios, beneficios y pagos.
- Si la persona quiere inscribirse: dile qué documentos debe preparar y pásala a un asesor para que le envíe el formulario de inscripción.
- Si la consulta es de otra área (notas, certificados, recibos de estudiantes antiguos, soporte tecnológico), da el contacto de esa área.

CUÁNDO PASAR A UN ASESOR HUMANO
Tu trabajo principal es conversar y resolver tú misma. Solo pasa a un asesor cuando:
- la persona pide expresamente hablar con una persona o asesor;
- está molesta, insatisfecha o reclama algo;
- pregunta algo que NO está en la información de abajo (fechas que no aparecen, homologaciones, casos personales, descuentos especiales);
- quiere inscribirse (después de darle la lista de documentos) o necesita ayuda personal para pagar;
- después de dos intentos no logras resolver su duda.
NO pases a un asesor por saludos, preguntas generales, programas, precios, requisitos o financiación que sí están en la información.
Para pasarlo, responde normal y agrega AL FINAL, en una línea aparte, exactamente: [[ASESOR: motivo breve]]
Esa marca la lee el sistema, la persona no la ve. No la uses para preguntas que sí puedes responder.
Estado de asesores: {estado}

REGLAS
- Usa SOLO la información de abajo. Si un dato no está, dilo con naturalidad y pasa a un asesor. NUNCA inventes precios, fechas, programas ni porcentajes, y no hagas cuentas con los valores: usa los que aparecen.
- No ofrezcas enviar archivos ni planes de estudio adjuntos; si los piden, pásalo a un asesor.
- Si preguntan algo sin relación con la universidad, responde breve y amable y vuelve al tema.
- No pidas datos personales (número de documento, dirección, estrato, datos bancarios, contraseñas). Eso lo maneja el asesor en la inscripción.
- Si la persona quiere que borremos sus datos o no recibir más mensajes, dile que escriba exactamente: eliminar mis datos
- Si recibes un audio, responde a su contenido como si fuera un mensaje escrito.
- Los mensajes que empiezan con "[Asesor ...]" los escribió un asesor humano en este chat; continúa con coherencia.

INFORMACIÓN DE LA INSTITUCIÓN
<<<
{conocimiento}
>>>"""


def extraer_marca(texto: str) -> tuple[str, bool, str]:
    m = _MARCA.search(texto)
    if not m:
        return texto.strip(), False, ""
    limpio = _MARCA.sub("", texto).strip()
    return limpio, True, (m.group(1) or "").strip()[:300] or "La IA considera que necesita un asesor"


def _a_contents(historial: list[dict], partes_nuevas: list[types.Part]) -> list[types.Content]:
    contents: list[types.Content] = []
    for m in historial:
        rol = "user" if m["rol"] == "user" else "model"
        if contents and contents[-1].role == rol:   # Gemini prefiere turnos alternados
            contents[-1].parts.append(types.Part.from_text(text=m["texto"]))
        else:
            contents.append(types.Content(role=rol, parts=[types.Part.from_text(text=m["texto"])]))
    if contents and contents[-1].role == "user":
        contents[-1].parts.extend(partes_nuevas)
    else:
        contents.append(types.Content(role="user", parts=partes_nuevas))
    return contents


# Modelos de respaldo: si el configurado no existe, está saturado o sin cuota, se prueba el siguiente.
MODELOS_RESPALDO = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MINUTOS_VOLVER_AL_PRINCIPAL = 10   # tras usar un respaldo, se reintenta el modelo principal pasado este tiempo
estado = {"modelo_activo": None, "ultimo_error": "", "no_disponibles": set(), "volver_al_principal": 0.0}

_TRANSITORIOS = ("503", "UNAVAILABLE", "overloaded", "504", "DEADLINE", "500", "INTERNAL", "Server disconnected")
_SIN_CUOTA = ("429", "RESOURCE_EXHAUSTED", "quota")
_CLAVE_MALA = ("API key", "API_KEY", "PERMISSION_DENIED", "UNAUTHENTICATED")


def _modelos() -> list[str]:
    lista = []
    activo = estado["modelo_activo"]
    if activo and time.monotonic() < estado["volver_al_principal"]:
        lista.append(activo)
    for m in [config.GEMINI_MODEL, *MODELOS_RESPALDO]:
        if m and m not in lista and m not in estado["no_disponibles"]:
            lista.append(m)
    return lista or [config.GEMINI_MODEL]


def _con_razonamiento_minimo(cfg):
    if config.GEMINI_RAZONAMIENTO in ("", "NORMAL", "DEFAULT"):
        return cfg
    try:
        nivel = types.ThinkingLevel[config.GEMINI_RAZONAMIENTO]
        return cfg.model_copy(update={"thinking_config": types.ThinkingConfig(thinking_level=nivel)})
    except Exception:
        return cfg


def _exito(modelo: str) -> None:
    if modelo != estado["modelo_activo"]:
        log.info("Usando el modelo %s", modelo)
        principal = _modelos_principal()
        estado["volver_al_principal"] = (0.0 if modelo == principal
                                         else time.monotonic() + MINUTOS_VOLVER_AL_PRINCIPAL * 60)
    estado["modelo_activo"], estado["ultimo_error"] = modelo, ""


def _modelos_principal() -> str:
    return next((m for m in [config.GEMINI_MODEL, *MODELOS_RESPALDO] if m not in estado["no_disponibles"]),
                config.GEMINI_MODEL)


async def _con_respaldo(ejecutar):
    """Prueba los modelos en orden. Si Google está saturado (503/504) reintenta una vez el mismo modelo;
    si no existe, no tiene cuota o no responde a tiempo, pasa al siguiente."""
    errores = []
    limite = time.monotonic() + config.SEGUNDOS_TOTAL_IA
    for modelo in _modelos():
        for intento in range(2):
            if time.monotonic() > limite:
                break
            try:
                resultado = await ejecutar(modelo)
                _exito(modelo)
                return resultado
            except asyncio.TimeoutError:
                msg, reintentar = f"Google no respondió en {config.SEGUNDOS_TIMEOUT_IA} s", False
            except Exception as e:
                msg = str(e) or e.__class__.__name__
                if any(x in msg for x in _CLAVE_MALA):
                    errores.append(f"{modelo}: {msg[:300]}")
                    estado["ultimo_error"] = " | ".join(errores)
                    raise RuntimeError(estado["ultimo_error"])   # otro modelo no lo arregla
                if "404" in msg or "NOT_FOUND" in msg:
                    estado["no_disponibles"].add(modelo)
                reintentar = intento == 0 and any(x in msg for x in _TRANSITORIOS)
            errores.append(f"{modelo}: {msg[:300]}")
            log.warning("Falló el modelo %s: %s", modelo, msg[:300])
            if reintentar:
                await asyncio.sleep(1.5)
                continue
            break
    if time.monotonic() > limite:
        errores.append(f"Se agotó el tiempo total ({config.SEGUNDOS_TOTAL_IA} s); revisa internet, antivirus o firewall")
    estado["ultimo_error"] = " | ".join(errores)
    raise RuntimeError(estado["ultimo_error"])


async def _llamar(modelo, contents, cfg):
    return await asyncio.wait_for(
        cliente().aio.models.generate_content(model=modelo, contents=contents, config=cfg),
        timeout=config.SEGUNDOS_TIMEOUT_IA + 5)


async def _generar(contents, cfg):
    rapido = _con_razonamiento_minimo(cfg)

    async def ejecutar(modelo):
        inicio = time.monotonic()
        try:
            r = await _llamar(modelo, contents, rapido)
        except Exception as e:
            if rapido is not cfg and "think" in str(e).lower():
                r = await _llamar(modelo, contents, cfg)   # el modelo no acepta ese ajuste
            else:
                raise
        log.info("Respuesta de Gemini (%s) en %.1f s", modelo, time.monotonic() - inicio)
        return r

    return await _con_respaldo(ejecutar)


# --- Respuesta "en vivo": cada mensaje corto sale apenas la IA lo termina de escribir ---
_SEPARADOR = re.compile(r"(?:^|\n)[ \t]*-{3,}[ \t]*\n")
MAX_PARTES = 4


async def _flujo(modelo, contents, cfg):
    it = await asyncio.wait_for(
        cliente().aio.models.generate_content_stream(model=modelo, contents=contents, config=cfg),
        timeout=config.SEGUNDOS_TIMEOUT_IA)
    while True:
        try:
            trozo = await asyncio.wait_for(it.__anext__(), timeout=config.SEGUNDOS_TIMEOUT_IA)
        except StopAsyncIteration:
            return
        try:
            yield trozo.text or ""
        except Exception:   # trozos sin texto (p. ej. solo razonamiento)
            yield ""


async def _generar_en_partes(contents, cfg, al_parte) -> tuple[str, str, int]:
    """Devuelve (texto completo, lo que falta por enviar, partes ya enviadas)."""
    rapido = _con_razonamiento_minimo(cfg)

    async def una_vez(modelo, usar):
        texto, pendiente, enviadas = "", "", 0
        inicio = time.monotonic()
        try:
            async for trozo in _flujo(modelo, contents, usar):
                texto += trozo
                pendiente += trozo
                while enviadas < MAX_PARTES - 1:
                    m = _SEPARADOR.search(pendiente)
                    if not m:
                        break
                    parte, pendiente = pendiente[:m.start()], pendiente[m.end():]
                    limpio = extraer_marca(parte)[0]
                    if limpio:
                        await al_parte(limpio)
                        enviadas += 1
        except Exception as e:
            if not enviadas:
                raise
            # ya salió parte de la respuesta: no empezar de nuevo, se queda con lo enviado
            log.warning("Se cortó la respuesta de %s: %s", modelo, str(e)[:300] or e.__class__.__name__)
            return texto, "", enviadas
        log.info("Respuesta de Gemini (%s) en %.1f s", modelo, time.monotonic() - inicio)
        return texto, pendiente, enviadas

    async def ejecutar(modelo):
        try:
            return await una_vez(modelo, rapido)
        except Exception as e:
            if rapido is not cfg and "think" in str(e).lower():
                return await una_vez(modelo, cfg)   # el modelo no acepta ese ajuste
            raise

    return await _con_respaldo(ejecutar)

async def responder(historial: list[dict], conocimiento: str, hay_asesores: bool, ya_solicitado: bool,
                    texto: str | None = None, audio: bytes | None = None,
                    audio_mime: str = "audio/ogg", al_parte=None) -> Respuesta:
    """Si se pasa al_parte (función async), cada mensaje corto se entrega apenas está listo
    y la Respuesta trae solo lo que faltó por enviar."""
    partes: list[types.Part] = []
    if audio:
        partes.append(types.Part.from_bytes(data=audio, mime_type=audio_mime))
    partes.append(types.Part.from_text(text=texto or "(La persona envió un audio)"))
    try:
        cfg = types.GenerateContentConfig(
            system_instruction=instrucciones_sistema(conocimiento, hay_asesores, ya_solicitado),
            temperature=config.GEMINI_TEMPERATURE,
            max_output_tokens=8192,   # holgado: los modelos que "piensan" gastan tokens antes de responder
        )
        if al_parte:
            completo, resto, enviadas = await _generar_en_partes(_a_contents(historial, partes), cfg, al_parte)
            _, escalar, motivo = extraer_marca(completo)
            resto = extraer_marca(resto)[0]
            if not resto and not enviadas:
                log.error("La IA devolvió una respuesta vacía")
                return Respuesta(MENSAJE_ERROR, True, "La IA no generó respuesta", error=True)
            return Respuesta(resto, escalar, motivo, enviadas=enviadas)
        r = await _generar(_a_contents(historial, partes), cfg)
        limpio, escalar, motivo = extraer_marca(r.text or "")
        if not limpio:
            log.error("La IA devolvió una respuesta vacía")
            return Respuesta(MENSAJE_ERROR, True, "La IA no generó respuesta", error=True)
        return Respuesta(limpio, escalar, motivo)
    except Exception as e:
        log.error("Error de Gemini: %s", e)
        return Respuesta(MENSAJE_ERROR, True, "Error de la IA al responder", error=True)


async def transcribir(audio: bytes, mime: str) -> str:
    try:
        r = await _generar([types.Content(role="user", parts=[
            types.Part.from_bytes(data=audio, mime_type=mime),
            types.Part.from_text(text="Transcribe este audio en español, sin comentarios."),
        ])], types.GenerateContentConfig(temperature=0, max_output_tokens=4096))
        return f"🎤 {(r.text or '').strip()}"
    except Exception:
        return "🎤 (audio sin transcribir)"


async def diagnostico() -> dict:
    """Prueba rápida de la conexión con Gemini, para el panel."""
    if not config.GEMINI_API_KEY:
        return {"ok": False, "error": "No hay GEMINI_API_KEY configurada (CONFIGURAR_CLAVES.bat)."}
    try:
        r = await _generar([types.Content(role="user", parts=[types.Part.from_text(
            text="Responde solo: funcionando")])], types.GenerateContentConfig(temperature=0, max_output_tokens=1024))
        return {"ok": True, "modelo": estado["modelo_activo"], "respuesta": (r.text or "").strip()[:100]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:1500]}
