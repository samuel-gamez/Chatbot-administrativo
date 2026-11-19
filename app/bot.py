"""Flujo de cada mensaje entrante: guardar, decidir si responde la IA o un asesor, y enviar."""
import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app import config, ia, whatsapp
from app.db import (Conversacion, Mensaje, SessionLocal, ahora, asesores_en_turno, obtener_ajuste,
                    utc)

log = logging.getLogger("bot")
_locks: dict[str, asyncio.Lock] = {}
_tareas: set = set()
_en_espera: dict[str, int] = defaultdict(int)   # mensajes de la misma persona esperando turno

MINUTOS_ATRASO = 3   # si llega con más retraso que esto, la IA se disculpa por la demora

TEXTO_NO_SOPORTADO = (
    "¡Lo recibí! 🙌 Por ahora solo puedo leer mensajes de texto y notas de voz. "
    "¿Me cuentas por escrito qué te gustaría saber?"
)


@dataclass
class Entrante:
    telefono: str
    tipo: str
    texto: str | None = None
    nombre: str | None = None
    wa_id: str | None = None
    media_id: str | None = None
    linea_id: str = ""              # phone_number_id de la línea del bot que recibió el mensaje
    linea_numero: str | None = None  # número visible de esa línea
    enviado_en: datetime | None = None
    simulada: bool = False


def _historial(s, conv: Conversacion, hasta_id: int) -> list[dict]:
    filas = s.scalars(
        select(Mensaje).where(Mensaje.conversacion_id == conv.id, Mensaje.id < hasta_id,
                              Mensaje.origen != "sistema")
        .order_by(Mensaje.id.desc()).limit(config.MENSAJES_CONTEXTO)
    ).unique().all()
    hist = []
    for m in reversed(filas):
        if m.origen == "usuario":
            hist.append({"rol": "user", "texto": m.texto})
        elif m.origen == "asesor":
            nombre = m.asesor.nombre if m.asesor else "asesor"
            hist.append({"rol": "model", "texto": f"[Asesor {nombre}] {m.texto}"})
        else:
            hist.append({"rol": "model", "texto": m.texto})
    return hist


def registrar_sistema(s, conv: Conversacion, texto: str) -> None:
    s.add(Mensaje(conversacion_id=conv.id, origen="sistema", texto=texto))


async def enviar(http: httpx.AsyncClient, conv: Conversacion, texto: str) -> str | None:
    if conv.es_simulada:
        return None
    return await whatsapp.enviar_texto(http, conv.telefono, texto, conv.linea_id or None)


async def procesar_entrante(http: httpx.AsyncClient, e: Entrante) -> None:
    clave = f"{e.linea_id}:{e.telefono}"
    _en_espera[clave] += 1
    lock = _locks.setdefault(clave, asyncio.Lock())
    try:
        async with lock:  # mensajes seguidos de la misma persona se atienden en orden
            _en_espera[clave] -= 1
            try:
                await _procesar(http, e, otros_esperando=_en_espera[clave] > 0)
            except Exception:
                log.exception("Error procesando mensaje de %s", e.telefono)
    finally:
        if _en_espera[clave] <= 0:
            _en_espera.pop(clave, None)


async def _procesar(http: httpx.AsyncClient, e: Entrante, otros_esperando: bool) -> None:
    # 1) Duplicados: Meta reenvía el mismo webhook si no respondimos a tiempo
    if e.wa_id:
        with SessionLocal() as s:
            if s.scalar(select(Mensaje.id).where(Mensaje.wa_id == e.wa_id)):
                return

    texto = e.texto
    if e.tipo == "audio" and e.media_id and not e.simulada:
        descargado = await whatsapp.descargar_media(http, e.media_id)
        texto = await ia.transcribir(*descargado) if descargado else "🎤 (no se pudo descargar el audio)"

    # 2) Derecho a eliminar sus datos (lo promete la política de privacidad)
    if texto and es_pedido_borrado(texto):
        with SessionLocal() as s:
            borrar_datos(s, e.telefono)
            s.commit()
        if not e.simulada:
            await whatsapp.enviar_texto(http, e.telefono, TEXTO_DATOS_BORRADOS, e.linea_id or None)
        log.info("Datos eliminados a pedido de %s***", e.telefono[:5])
        return

    enviado = utc(e.enviado_en) or ahora()
    with SessionLocal() as s:
        conv = s.scalar(select(Conversacion).where(Conversacion.telefono == e.telefono,
                                                   Conversacion.linea_id == e.linea_id))
        if not conv:
            conv = Conversacion(telefono=e.telefono, nombre=e.nombre, es_simulada=e.simulada,
                                linea_id=e.linea_id, linea_numero=e.linea_numero)
            s.add(conv)
            s.flush()
        else:
            if e.nombre and not conv.nombre:
                conv.nombre = e.nombre
            if e.linea_numero:
                conv.linea_numero = e.linea_numero

        # Si un asesor tomó el chat y lo dejó abandonado, vuelve la IA
        if conv.modo == "humano":
            ref = utc(conv.ultimo_msg_asesor) or utc(conv.solicitado_en) or utc(conv.ultima_actividad)
            if ref and ahora() - ref > timedelta(hours=config.HORAS_AUTO_DEVOLVER_BOT):
                conv.modo, conv.asesor_id = "bot", None
                registrar_sistema(s, conv, "El chat volvió a la IA por inactividad del asesor.")

        entrada = Mensaje(conversacion_id=conv.id, origen="usuario", wa_id=e.wa_id,
                          texto=texto or f"({e.tipo} no soportado)", enviado_en=enviado)
        s.add(entrada)
        previo = utc(conv.ultimo_msg_usuario)
        conv.ultimo_msg_usuario = max(previo, enviado) if previo else enviado  # ventana de 24 h real
        conv.ultima_actividad = ahora()
        conv.no_leidos += 1
        s.commit()

        modo, conv_id, entrada_id = conv.modo, conv.id, entrada.id
        if modo == "bot" and texto and not otros_esperando:
            historial = _historial(s, conv, entrada_id)
            conocimiento = obtener_ajuste(s, "conocimiento")
            hay_asesores = asesores_en_turno(s) > 0
            ya_solicitado = conv.necesita_asesor

    # 2) Con asesor: la IA no responde; el asesor lo ve en el panel
    if modo == "humano":
        if e.wa_id and not e.simulada:
            await whatsapp.marcar_leido(http, e.wa_id, escribiendo=False, linea_id=e.linea_id or None)
        return

    # 3) Si la persona mandó varios mensajes seguidos (o llegaron juntos tras una caída),
    #    respondemos una sola vez al último, que ya incluye todo lo anterior en el historial.
    if otros_esperando and texto:
        return

    if e.wa_id and not e.simulada:   # "escribiendo..." en paralelo, sin esperar a Meta
        _tareas.add(t := asyncio.create_task(whatsapp.marcar_leido(http, e.wa_id, linea_id=e.linea_id or None)))
        t.add_done_callback(_tareas.discard)

    envio = {"ultimo": None, "cortado": False}

    async def mandar(parte: str) -> None:
        """Envía un mensaje corto; entre uno y otro deja una pausa de "escribiendo..." como una persona."""
        if envio["cortado"] or not parte.strip():
            return
        if envio["ultimo"] is not None:
            if e.wa_id and not e.simulada:
                await whatsapp.marcar_leido(http, e.wa_id, linea_id=e.linea_id or None)
            falta = pausa_escritura(parte) - (time.monotonic() - envio["ultimo"])
            if falta > 0:
                await asyncio.sleep(falta)
        with SessionLocal() as s:
            conv = s.get(Conversacion, conv_id)
            if conv.modo == "humano":   # un asesor tomó el chat mientras la IA respondía
                envio["cortado"] = True
                return
            salida = Mensaje(conversacion_id=conv.id, origen="bot", texto=parte)
            s.add(salida)
            conv.ultima_actividad = ahora()
            s.commit()
            error = await enviar(http, conv, parte)
            if error:
                salida.error_envio = error
                s.commit()
        envio["ultimo"] = time.monotonic()

    if not texto:
        respuesta = ia.Respuesta(TEXTO_NO_SOPORTADO)
    else:
        atraso = ahora() - enviado
        texto_ia = texto
        if atraso > timedelta(minutes=MINUTOS_ATRASO):
            minutos = int(atraso.total_seconds() // 60)
            hace = f"{minutos} minutos" if minutos < 120 else f"{minutos // 60} horas"
            texto_ia = (f"{texto}\n\n[Nota del sistema, no la menciones literalmente: este y los mensajes "
                        f"anteriores sin respuesta se enviaron hace unos {hace} y no se respondieron por una falla "
                        f"técnica. Empieza disculpándote brevemente por la demora y responde todo lo pendiente "
                        f"en un solo mensaje.]")
        respuesta = await ia.responder(historial, conocimiento, hay_asesores, ya_solicitado, texto_ia,
                                       al_parte=mandar)

    # 4) Registrar la solicitud de asesor (si aplica)
    with SessionLocal() as s:
        conv = s.get(Conversacion, conv_id)
        if conv.modo == "humano" or envio["cortado"]:   # un asesor lo tomó mientras la IA pensaba
            return
        if respuesta.escalar and not conv.necesita_asesor:
            conv.necesita_asesor = True
            conv.motivo = respuesta.motivo
            conv.solicitado_en = ahora()
            registrar_sistema(s, conv, f"🔔 Se solicitó un asesor: {respuesta.motivo}")
        s.commit()

    # 5) Enviar lo que falte (si la IA no lo fue entregando en vivo)
    if respuesta.texto.strip():
        for parte in partir_mensajes(respuesta.texto, max(1, 4 - respuesta.enviadas)):
            await mandar(parte)


def partir_mensajes(texto: str, maximo: int = 4) -> list[str]:
    """La IA separa sus mensajes con una línea '---'. Se envían como mensajes de WhatsApp distintos."""
    import re
    partes = [p.strip() for p in re.split(r"^\s*-{3,}\s*$", texto, flags=re.M) if p.strip()]
    if not partes:
        return [texto.strip()]
    if len(partes) > maximo:
        partes = partes[:maximo - 1] + ["\n\n".join(partes[maximo - 1:])]
    return partes


def pausa_escritura(texto: str) -> float:
    """Segundos de "escribiendo..." antes de un mensaje, según su largo (entre 0,8 y 2,5 s)."""
    return min(2.5, 0.8 + len(texto) / 100)


TEXTO_DATOS_BORRADOS = (
    "Listo, eliminamos tu conversación y tus datos de nuestro sistema. "
    "Si más adelante quieres información, puedes escribirnos de nuevo cuando quieras."
)


def _normalizar(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join("".join(c if c.isalnum() else " " for c in t).split())


def es_pedido_borrado(texto: str) -> bool:
    t = _normalizar(texto)
    return t in {"eliminar mis datos", "borrar mis datos", "elimina mis datos", "borra mis datos",
                 "eliminar datos", "borrar datos"}


def borrar_datos(s, telefono: str) -> int:
    convs = s.scalars(select(Conversacion).where(Conversacion.telefono == telefono)).unique().all()
    for c in convs:
        for m in s.scalars(select(Mensaje).where(Mensaje.conversacion_id == c.id)).unique().all():
            s.delete(m)
        s.delete(c)
    return len(convs)


def desde_timestamp(ts: str | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    except (TypeError, ValueError):
        return None
