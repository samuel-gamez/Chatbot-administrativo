"""Cliente mínimo para la WhatsApp Cloud API de Meta."""
import hashlib
import hmac
import logging
import re

import httpx

from app import config

log = logging.getLogger("whatsapp")

LIMITE_CARACTERES = 4000


def _base() -> str:
    return f"https://graph.facebook.com/{config.GRAPH_API_VERSION}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}", "Content-Type": "application/json"}


def firma_valida(cuerpo: bytes, firma_header: str | None) -> bool:
    if not config.WHATSAPP_APP_SECRET:
        return True
    if not firma_header or not firma_header.startswith("sha256="):
        return False
    esperado = hmac.new(config.WHATSAPP_APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, firma_header.removeprefix("sha256="))


def formatear_para_whatsapp(texto: str) -> str:
    texto = re.sub(r"\*\*(.+?)\*\*", r"*\1*", texto)
    texto = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", texto, flags=re.M)
    texto = re.sub(r"^\s*[-•]\s+", "• ", texto, flags=re.M)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def dividir(texto: str, limite: int = LIMITE_CARACTERES) -> list[str]:
    if len(texto) <= limite:
        return [texto]
    partes, actual = [], ""
    for parrafo in texto.split("\n\n"):
        if len(actual) + len(parrafo) + 2 > limite and actual:
            partes.append(actual.strip())
            actual = ""
        while len(parrafo) > limite:
            partes.append(parrafo[:limite])
            parrafo = parrafo[limite:]
        actual += parrafo + "\n\n"
    if actual.strip():
        partes.append(actual.strip())
    return partes


def _linea(linea_id: str | None) -> str:
    return linea_id or config.WHATSAPP_PHONE_NUMBER_ID


async def enviar_texto(cliente: httpx.AsyncClient, para: str, texto: str, linea_id: str | None = None) -> str | None:
    """Envía el texto desde la línea indicada. Devuelve None si salió bien o el error."""
    if not _linea(linea_id):
        return "No hay línea de WhatsApp configurada"
    error = None
    for parte in dividir(formatear_para_whatsapp(texto)):
        payload = {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": para,
            "type": "text", "text": {"preview_url": True, "body": parte},
        }
        try:
            r = await cliente.post(f"{_base()}/{_linea(linea_id)}/messages",
                                   headers=_headers(), json=payload, timeout=20)
        except httpx.HTTPError as e:
            error = f"Sin conexión con WhatsApp: {e}"
            log.error(error)
            continue
        if r.status_code >= 400:
            try:
                detalle = r.json().get("error", {})
                msg = detalle.get("error_user_msg") or detalle.get("message") or r.text
                if detalle.get("code") == 131047:
                    msg = "Pasaron más de 24 h desde el último mensaje del aspirante; WhatsApp no permite escribirle libremente."
            except ValueError:
                msg = r.text
            error = str(msg)[:290]
            log.error("Error enviando a %s: %s", para, r.text)
    return error


async def descargar_media(cliente: httpx.AsyncClient, media_id: str) -> tuple[bytes, str] | None:
    try:
        info = await cliente.get(f"{_base()}/{media_id}", headers=_headers(), timeout=15)
        info.raise_for_status()
        datos = info.json()
        archivo = await cliente.get(datos["url"], headers=_headers(), timeout=30)
        archivo.raise_for_status()
        return archivo.content, datos.get("mime_type", "audio/ogg").split(";")[0].strip()
    except (httpx.HTTPError, KeyError) as e:
        log.error("No se pudo descargar media %s: %s", media_id, e)
        return None


async def marcar_leido(cliente: httpx.AsyncClient, msg_id: str, escribiendo: bool = True,
                       linea_id: str | None = None) -> None:
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": msg_id}
    if escribiendo:
        payload["typing_indicator"] = {"type": "text"}
    try:
        await cliente.post(f"{_base()}/{_linea(linea_id)}/messages",
                           headers=_headers(), json=payload, timeout=10)
    except httpx.HTTPError as e:
        log.warning("No se pudo marcar como leído: %s", e)
