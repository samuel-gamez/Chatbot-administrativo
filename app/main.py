"""Servidor: webhook de WhatsApp + panel web de asesores."""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import auth, bot, config, panel, whatsapp
from app.db import crear_tablas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
WEB = config.BASE_DIR / "web"


async def mantener_despierto(http: httpx.AsyncClient) -> None:
    """En el plan gratis de Render el servicio se duerme tras 15 min sin tráfico.
    Se visita a sí mismo por la URL pública cada 10 min para seguir activo 24/7."""
    url = config.URL_PUBLICA.rstrip("/") + "/salud"
    while True:
        await asyncio.sleep(config.MINUTOS_KEEPALIVE * 60)
        try:
            await http.get(url, timeout=20)
        except httpx.HTTPError as e:
            log.warning("Keep-alive falló: %s", e)


def respaldar_ahora() -> str | None:
    """Copia consistente de la base SQLite a respaldos/; conserva los últimos 14."""
    import sqlite3
    from datetime import datetime
    if not config.DATABASE_URL.startswith("sqlite"):
        return None
    origen = config.DATABASE_URL.split("sqlite:///", 1)[1]
    carpeta = config.BASE_DIR / "respaldos"
    carpeta.mkdir(exist_ok=True)
    destino = carpeta / f"respaldo-{datetime.now():%Y-%m-%d}.db"
    with sqlite3.connect(origen) as src, sqlite3.connect(destino) as dst:
        src.backup(dst)
    for viejo in sorted(carpeta.glob("respaldo-*.db"))[:-14]:
        viejo.unlink(missing_ok=True)
    return str(destino)


async def respaldos_diarios() -> None:
    while True:
        try:
            ruta = await asyncio.to_thread(respaldar_ahora)
            if ruta:
                log.info("Respaldo guardado en %s", ruta)
        except Exception as e:
            log.warning("No se pudo hacer el respaldo: %s", e)
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    crear_tablas()
    app.state.http = httpx.AsyncClient()
    faltan = [n for n in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN", "GEMINI_API_KEY")
              if not getattr(config, n)]
    if faltan:
        log.warning("Faltan variables de entorno: %s", ", ".join(faltan))
    tareas = [asyncio.create_task(respaldos_diarios())]
    if config.URL_PUBLICA and config.MINUTOS_KEEPALIVE > 0:
        tareas.append(asyncio.create_task(mantener_despierto(app.state.http)))
    yield
    for t in tareas:
        t.cancel()
    await app.state.http.aclose()


app = FastAPI(title="Admisiones por WhatsApp", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(panel.router)
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")


_ICONO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
          'fill="#4b1d6e"/><path d="M18 20h28a6 6 0 0 1 6 6v12a6 6 0 0 1-6 6H30l-10 8v-8h-2a6 6 0 0 1-6-6V26a6 6 0 0 1 '
          '6-6z" fill="#fff"/></svg>')


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(_ICONO, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/salud")
async def salud():
    from app import ia
    return {"estado": "ok", "bot": config.NOMBRE_BOT, "modelo": ia.estado["modelo_activo"] or config.GEMINI_MODEL,
            "ia": "error" if ia.estado["ultimo_error"] else "ok"}


# ---------- páginas del panel ----------
def _logueado(request: Request) -> bool:
    return auth.leer_token(request.cookies.get(auth.COOKIE)) is not None


@app.get("/", include_in_schema=False)
async def inicio(request: Request):
    return RedirectResponse("/panel" if _logueado(request) else "/login")


@app.get("/login", include_in_schema=False)
async def pagina_login():
    return FileResponse(WEB / "login.html")


@app.get("/registro", include_in_schema=False)
async def pagina_registro():
    return FileResponse(WEB / "login.html")


@app.get("/privacidad", include_in_schema=False)
async def pagina_privacidad():
    return FileResponse(WEB / "privacidad.html")


@app.get("/panel", include_in_schema=False)
async def pagina_panel(request: Request):
    if not _logueado(request):
        return RedirectResponse("/login")
    return FileResponse(WEB / "panel.html")


# ---------- webhook de WhatsApp ----------
@app.get("/webhook", response_class=PlainTextResponse)
async def verificar(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    if mode == "subscribe" and token and token == config.WHATSAPP_VERIFY_TOKEN:
        log.info("Webhook verificado")
        return challenge
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@app.post("/webhook")
async def recibir(request: Request, tareas: BackgroundTasks):
    cuerpo = await request.body()
    if not whatsapp.firma_valida(cuerpo, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Firma inválida")

    datos = await request.json()
    for entry in datos.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            meta = valor.get("metadata", {})
            nombres = {c.get("wa_id"): c.get("profile", {}).get("name") for c in valor.get("contacts", [])}
            for msg in valor.get("messages", []):
                tipo = msg.get("type")
                texto, media_id = None, None
                if tipo == "text":
                    texto = msg["text"].get("body", "").strip()
                elif tipo == "interactive":
                    inter = msg.get("interactive", {})
                    texto = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title")
                elif tipo == "button":
                    texto = msg.get("button", {}).get("text")
                elif tipo == "audio":
                    media_id = msg.get("audio", {}).get("id")
                tareas.add_task(bot.procesar_entrante, request.app.state.http, bot.Entrante(
                    telefono=msg["from"], tipo=tipo, texto=texto, nombre=nombres.get(msg["from"]),
                    wa_id=msg.get("id"), media_id=media_id,
                    linea_id=meta.get("phone_number_id", ""), linea_numero=meta.get("display_phone_number"),
                    enviado_en=bot.desde_timestamp(msg.get("timestamp")),
                ))
    return {"ok": True}  # responder rápido para que Meta no reintente
