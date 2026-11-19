"""API del panel de asesores."""
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import auth, bot, config
from app.db import (Ajuste, Asesor, Conversacion, Mensaje, SessionLocal, ahora, asesores_en_turno,
                    obtener_ajuste, utc)

router = APIRouter(prefix="/api")


# ---------- utilidades ----------
def _iso(dt):
    dt = utc(dt)
    return dt.isoformat() if dt else None


def _asesor_json(a: Asesor) -> dict:
    return {"id": a.id, "nombre": a.nombre, "email": a.email, "es_admin": a.es_admin,
            "estado": a.estado, "en_turno": a.en_turno, "creado": _iso(a.creado)}


def _conv_json(c: Conversacion, ultimo: Mensaje | None = None) -> dict:
    ultimo_usuario = utc(c.ultimo_msg_usuario)
    ventana = bool(ultimo_usuario and ahora() - ultimo_usuario < timedelta(hours=24)) or c.es_simulada
    return {
        "id": c.id, "telefono": c.telefono, "nombre": c.nombre, "modo": c.modo,
        "linea": c.linea_numero,
        "necesita_asesor": c.necesita_asesor, "motivo": c.motivo, "solicitado_en": _iso(c.solicitado_en),
        "asesor": {"id": c.asesor.id, "nombre": c.asesor.nombre} if c.asesor else None,
        "ultima_actividad": _iso(c.ultima_actividad), "no_leidos": c.no_leidos,
        "es_simulada": c.es_simulada, "ventana_24h": ventana,
        "ultimo": {"origen": ultimo.origen, "texto": ultimo.texto[:120]} if ultimo else None,
    }


def _msg_json(m: Mensaje) -> dict:
    return {"id": m.id, "origen": m.origen, "texto": m.texto, "creado": _iso(m.creado),
            "asesor": m.asesor.nombre if m.asesor else None, "error": m.error_envio}


def _get_conv(s, cid: int) -> Conversacion:
    c = s.get(Conversacion, cid)
    if not c:
        raise HTTPException(404, "Conversación no encontrada")
    return c


# ---------- cuentas ----------
class Registro(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=8, max_length=72)


class Login(BaseModel):
    email: str
    password: str


@router.post("/registro")
def registro(datos: Registro):
    email = datos.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Correo inválido")
    with SessionLocal() as s:
        if s.scalar(select(Asesor.id).where(Asesor.email == email)):
            raise HTTPException(400, "Ya existe una cuenta con ese correo")
        primero = s.scalar(select(func.count(Asesor.id))) == 0
        a = Asesor(nombre=datos.nombre.strip(), email=email, password_hash=auth.hash_password(datos.password),
                   es_admin=primero, estado="activo" if primero else "pendiente")
        s.add(a)
        s.commit()
    if primero:
        return {"ok": True, "mensaje": "Cuenta de administrador creada. Ya puedes iniciar sesión."}
    return {"ok": True, "mensaje": "Cuenta creada. Un administrador debe aprobarla antes de que puedas entrar."}


@router.post("/login")
def login(datos: Login, response: Response, request: Request):
    with SessionLocal() as s:
        a = s.scalar(select(Asesor).where(Asesor.email == datos.email.strip().lower()))
    if not a or not auth.verificar_password(datos.password, a.password_hash):
        raise HTTPException(400, "Correo o contraseña incorrectos")
    if a.estado == "pendiente":
        raise HTTPException(403, "Tu cuenta está pendiente de aprobación por un administrador")
    if a.estado != "activo":
        raise HTTPException(403, "Tu cuenta está desactivada")
    response.set_cookie(auth.COOKIE, auth.crear_token(a.id), httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", max_age=config.HORAS_SESION * 3600)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response, request: Request):
    aid = auth.leer_token(request.cookies.get(auth.COOKIE))
    if aid:
        with SessionLocal() as s:
            a = s.get(Asesor, aid)
            if a:
                a.en_turno = False
                s.commit()
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/yo")
def yo(a: Asesor = Depends(auth.asesor_actual)):
    return _asesor_json(a)


class Turno(BaseModel):
    en_turno: bool


@router.post("/turno")
def turno(datos: Turno, a: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        yo_ = s.get(Asesor, a.id)
        yo_.en_turno = datos.en_turno
        yo_.ultimo_ping = ahora()
        s.commit()
    return {"en_turno": datos.en_turno}


# ---------- bandeja ----------
@router.get("/bandeja")
def bandeja(filtro: str = "todas", q: str = "", a: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        yo_ = s.get(Asesor, a.id)
        yo_.ultimo_ping = ahora()  # sirve para saber quién tiene el panel abierto
        s.commit()

        consulta = select(Conversacion)
        if filtro == "pendientes":
            consulta = consulta.where(Conversacion.necesita_asesor.is_(True), Conversacion.modo == "bot")
        elif filtro == "mias":
            consulta = consulta.where(Conversacion.modo == "humano", Conversacion.asesor_id == a.id)
        elif filtro == "humano":
            consulta = consulta.where(Conversacion.modo == "humano")
        elif filtro == "bot":
            consulta = consulta.where(Conversacion.modo == "bot")
        if q.strip():
            patron = f"%{q.strip()}%"
            consulta = consulta.where(or_(Conversacion.nombre.ilike(patron), Conversacion.telefono.ilike(patron)))
        convs = s.scalars(consulta.order_by(Conversacion.necesita_asesor.desc(),
                                            Conversacion.ultima_actividad.desc()).limit(200)).unique().all()

        ultimos = {}
        if convs:
            sub = (select(func.max(Mensaje.id)).where(Mensaje.conversacion_id.in_([c.id for c in convs]),
                                                      Mensaje.origen != "sistema")
                   .group_by(Mensaje.conversacion_id))
            for m in s.scalars(select(Mensaje).where(Mensaje.id.in_(sub))).unique():
                ultimos[m.conversacion_id] = m

        pendientes = s.scalar(select(func.count(Conversacion.id)).where(
            Conversacion.necesita_asesor.is_(True), Conversacion.modo == "bot"))
        mias = s.scalar(select(func.count(Conversacion.id)).where(
            Conversacion.modo == "humano", Conversacion.asesor_id == a.id))
        mias_sin_leer = s.scalar(select(func.coalesce(func.sum(Conversacion.no_leidos), 0)).where(
            Conversacion.modo == "humano", Conversacion.asesor_id == a.id))
        return {
            "conversaciones": [_conv_json(c, ultimos.get(c.id)) for c in convs],
            "contadores": {"pendientes": pendientes, "mias": mias, "mias_sin_leer": mias_sin_leer,
                           "en_turno": asesores_en_turno(s)},
            "yo": _asesor_json(yo_),
        }


@router.get("/conversaciones/{cid}")
def ver_conversacion(cid: int, desde: int = 0, a: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        c = _get_conv(s, cid)
        msgs = s.scalars(select(Mensaje).where(Mensaje.conversacion_id == cid, Mensaje.id > desde)
                         .order_by(Mensaje.id)).unique().all()
        if c.no_leidos and (c.modo == "bot" or c.asesor_id == a.id):
            c.no_leidos = 0
            s.commit()
        return {"conversacion": _conv_json(c), "mensajes": [_msg_json(m) for m in msgs]}


@router.post("/conversaciones/{cid}/tomar")
def tomar(cid: int, a: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        c = _get_conv(s, cid)
        if c.modo == "humano" and c.asesor_id and c.asesor_id != a.id and not a.es_admin:
            raise HTTPException(409, f"Este chat ya lo tiene {c.asesor.nombre}")
        c.modo, c.asesor_id, c.necesita_asesor = "humano", a.id, False
        c.ultimo_msg_asesor = ahora()
        bot.registrar_sistema(s, c, f"👤 {a.nombre} tomó el chat. La IA deja de responder.")
        s.commit()
        presentacion = armar_presentacion(s, a, c)
    return {"ok": True, "presentacion": presentacion}


PRESENTACION_POR_DEFECTO = (
    "Hola {aspirante}, te saluda {asesor} del Área de Mercadeo de UNIMINUTO Girardot 😊 "
    "Ya revisé tu conversación con nuestro asistente. ¿En qué te puedo ayudar?"
)


def _primer_nombre(nombre: str | None) -> str:
    return (nombre or "").strip().split(" ")[0] if nombre else ""


def armar_presentacion(s, asesor: Asesor, conv: Conversacion) -> str:
    """Mensaje de presentación del asesor. Variables: {asesor} y {aspirante}."""
    plantilla = obtener_ajuste(s, "presentacion_asesor", PRESENTACION_POR_DEFECTO)
    aspirante = "" if conv.es_simulada else _primer_nombre(conv.nombre)
    try:
        texto = plantilla.format(asesor=_primer_nombre(asesor.nombre) or asesor.nombre, aspirante=aspirante)
    except (KeyError, IndexError, ValueError):
        return plantilla.strip()
    # Si no hay nombre del aspirante, que no quede "Hola , te saluda..."
    texto = re.sub(r"[ \t]+([,!?.])", r"\1", texto)
    return re.sub(r"[ \t]{2,}", " ", texto).strip()


@router.post("/conversaciones/{cid}/devolver")
def devolver(cid: int, a: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        c = _get_conv(s, cid)
        if c.asesor_id not in (None, a.id) and not a.es_admin:
            raise HTTPException(403, "Solo quien tiene el chat puede devolverlo")
        c.modo, c.asesor_id, c.necesita_asesor = "bot", None, False
        bot.registrar_sistema(s, c, f"🤖 {a.nombre} devolvió el chat a la IA.")
        s.commit()
    return {"ok": True}


@router.post("/conversaciones/{cid}/descartar")
def descartar(cid: int, a: Asesor = Depends(auth.asesor_actual)):
    """Quita la alerta 'necesita asesor' sin tomar el chat (p. ej. la IA ya lo resolvió)."""
    with SessionLocal() as s:
        c = _get_conv(s, cid)
        c.necesita_asesor = False
        bot.registrar_sistema(s, c, f"{a.nombre} marcó la solicitud como atendida sin tomar el chat.")
        s.commit()
    return {"ok": True}


@router.delete("/conversaciones/{cid}")
def eliminar_conversacion(cid: int, a: Asesor = Depends(auth.asesor_actual)):
    """Borra el chat y sus mensajes del panel. Si la persona vuelve a escribir, se crea un chat nuevo."""
    with SessionLocal() as s:
        c = _get_conv(s, cid)
        if c.modo == "humano" and c.asesor_id not in (None, a.id) and not a.es_admin:
            raise HTTPException(403, f"Este chat lo está atendiendo {c.asesor.nombre}; solo esa persona o un administrador puede eliminarlo")
        for m in s.scalars(select(Mensaje).where(Mensaje.conversacion_id == c.id)).unique().all():
            s.delete(m)
        s.delete(c)
        s.commit()
    return {"ok": True}


class Envio(BaseModel):
    texto: str = Field(min_length=1, max_length=4000)


@router.post("/conversaciones/{cid}/enviar")
async def enviar(cid: int, datos: Envio, request: Request, a: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        c = _get_conv(s, cid)
        if c.modo == "humano" and c.asesor_id not in (None, a.id) and not a.es_admin:
            raise HTTPException(409, f"Este chat lo está atendiendo {c.asesor.nombre}")
        if c.modo != "humano" or c.asesor_id != a.id:   # escribir = tomar el chat
            c.modo, c.asesor_id, c.necesita_asesor = "humano", a.id, False
            bot.registrar_sistema(s, c, f"👤 {a.nombre} tomó el chat. La IA deja de responder.")
        m = Mensaje(conversacion_id=c.id, origen="asesor", texto=datos.texto.strip(), asesor_id=a.id)
        s.add(m)
        c.ultimo_msg_asesor = c.ultima_actividad = ahora()
        c.no_leidos = 0
        s.commit()
        error = await bot.enviar(request.app.state.http, c, datos.texto.strip())
        if error:
            m.error_envio = error
            s.commit()
    if error:
        raise HTTPException(502, f"No se pudo entregar por WhatsApp: {error}")
    return {"ok": True}


# ---------- administración ----------
@router.get("/asesores")
def listar_asesores(_: Asesor = Depends(auth.solo_admin)):
    with SessionLocal() as s:
        filas = s.scalars(select(Asesor).order_by(Asesor.estado != "pendiente", Asesor.nombre)).all()
        return [_asesor_json(x) for x in filas]


class CambioAsesor(BaseModel):
    estado: str | None = None
    es_admin: bool | None = None


@router.post("/asesores/{aid}")
def cambiar_asesor(aid: int, datos: CambioAsesor, admin: Asesor = Depends(auth.solo_admin)):
    with SessionLocal() as s:
        x = s.get(Asesor, aid)
        if not x:
            raise HTTPException(404, "Asesor no encontrado")
        if x.id == admin.id and (datos.estado not in (None, "activo") or datos.es_admin is False):
            raise HTTPException(400, "No puedes desactivarte ni quitarte el rol de administrador")
        if datos.estado is not None:
            if datos.estado not in ("activo", "inactivo", "pendiente"):
                raise HTTPException(400, "Estado inválido")
            x.estado = datos.estado
            if datos.estado != "activo":
                x.en_turno = False
        if datos.es_admin is not None:
            x.es_admin = datos.es_admin
        s.commit()
        return _asesor_json(x)


@router.post("/asesores/{aid}/restablecer")
def restablecer_clave(aid: int, admin: Asesor = Depends(auth.solo_admin)):
    """Genera una contraseña temporal para un asesor que olvidó la suya."""
    temporal = "-".join(secrets.token_hex(2) for _ in range(3))
    with SessionLocal() as s:
        x = s.get(Asesor, aid)
        if not x:
            raise HTTPException(404, "Asesor no encontrado")
        x.password_hash = auth.hash_password(temporal)
        s.commit()
        return {"nombre": x.nombre, "temporal": temporal}


class CambioClave(BaseModel):
    actual: str
    nueva: str = Field(min_length=8, max_length=72)


@router.post("/mi-clave")
def cambiar_mi_clave(datos: CambioClave, a: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        yo_ = s.get(Asesor, a.id)
        if not auth.verificar_password(datos.actual, yo_.password_hash):
            raise HTTPException(400, "La contraseña actual no es correcta")
        yo_.password_hash = auth.hash_password(datos.nueva)
        s.commit()
    return {"ok": True}


class TextoConocimiento(BaseModel):
    texto: str = Field(max_length=200_000)


@router.get("/diagnostico-ia")
async def diagnostico_ia(_: Asesor = Depends(auth.asesor_actual)):
    from app import ia
    return await ia.diagnostico()


class TextoPlantilla(BaseModel):
    texto: str = Field(min_length=1, max_length=1000)


@router.get("/presentacion")
def ver_presentacion(_: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        return {"texto": obtener_ajuste(s, "presentacion_asesor", PRESENTACION_POR_DEFECTO)}


@router.put("/presentacion")
def guardar_presentacion(datos: TextoPlantilla, _: Asesor = Depends(auth.solo_admin)):
    with SessionLocal() as s:
        a = s.get(Ajuste, "presentacion_asesor") or Ajuste(clave="presentacion_asesor", valor="")
        a.valor = datos.texto.strip()
        a.actualizado = ahora()
        s.merge(a)
        s.commit()
    return {"ok": True}


@router.get("/conocimiento")
def ver_conocimiento(_: Asesor = Depends(auth.asesor_actual)):
    with SessionLocal() as s:
        a = s.get(Ajuste, "conocimiento")
        return {"texto": a.valor if a else "", "actualizado": _iso(a.actualizado) if a else None}


@router.post("/conocimiento/desde-archivo")
def cargar_conocimiento_archivo(_: Asesor = Depends(auth.solo_admin)):
    """Reemplaza la información del bot por la del archivo conocimiento/universidad.md de la carpeta."""
    if not config.ARCHIVO_CONOCIMIENTO.exists():
        raise HTTPException(404, "No se encontró conocimiento/universidad.md en la carpeta del bot")
    texto = config.ARCHIVO_CONOCIMIENTO.read_text(encoding="utf-8")
    with SessionLocal() as s:
        a = s.get(Ajuste, "conocimiento") or Ajuste(clave="conocimiento", valor="")
        a.valor = texto
        a.actualizado = ahora()
        s.merge(a)
        s.commit()
    return {"ok": True, "caracteres": len(texto)}


@router.put("/conocimiento")
def guardar_conocimiento(datos: TextoConocimiento, _: Asesor = Depends(auth.solo_admin)):
    with SessionLocal() as s:
        a = s.get(Ajuste, "conocimiento") or Ajuste(clave="conocimiento", valor="")
        a.valor = datos.texto
        a.actualizado = ahora()
        s.merge(a)
        s.commit()
    return {"ok": True}


# ---------- simulador (probar sin WhatsApp) ----------
class Simulacion(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)
    sesion: str = Field(default="1", max_length=20)


@router.post("/simular")
def simular(datos: Simulacion, request: Request, tareas: BackgroundTasks,
            a: Asesor = Depends(auth.asesor_actual)):
    telefono = f"prueba-{a.id}-{re.sub(r'[^a-zA-Z0-9]', '', datos.sesion) or '1'}"
    tareas.add_task(bot.procesar_entrante, request.app.state.http, bot.Entrante(
        telefono=telefono, tipo="text", texto=datos.texto.strip(), nombre=f"Prueba de {a.nombre}",
        wa_id=f"sim-{secrets.token_hex(8)}", linea_id="prueba", simulada=True))
    return {"telefono": telefono}


@router.get("/simular/{telefono}")
def ver_simulacion(telefono: str, a: Asesor = Depends(auth.asesor_actual)):
    if not telefono.startswith(f"prueba-{a.id}-"):
        raise HTTPException(403, "Solo puedes ver tus propias pruebas")
    with SessionLocal() as s:
        c = s.scalar(select(Conversacion).where(Conversacion.telefono == telefono,
                                                   Conversacion.linea_id == "prueba"))
        if not c:
            return {"conversacion": None, "mensajes": []}
        msgs = s.scalars(select(Mensaje).where(Mensaje.conversacion_id == c.id,
                                               Mensaje.origen != "sistema").order_by(Mensaje.id)).unique().all()
        return {"conversacion": _conv_json(c), "mensajes": [_msg_json(m) for m in msgs]}


@router.delete("/simular/{telefono}")
def borrar_simulacion(telefono: str, a: Asesor = Depends(auth.asesor_actual)):
    if not telefono.startswith(f"prueba-{a.id}-"):
        raise HTTPException(403, "Solo puedes borrar tus propias pruebas")
    with SessionLocal() as s:
        c = s.scalar(select(Conversacion).where(Conversacion.telefono == telefono,
                                                   Conversacion.linea_id == "prueba"))
        if c:
            for m in s.scalars(select(Mensaje).where(Mensaje.conversacion_id == c.id)).unique():
                s.delete(m)
            s.delete(c)
            s.commit()
    return {"ok": True}
