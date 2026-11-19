"""Registro, inicio de sesión y sesiones con cookie firmada."""
import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app import config
from app.db import Asesor, SessionLocal

COOKIE = "sesion_asesor"
_firmador = TimestampSigner(config.SECRET_KEY, salt="panel-asesores")


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def verificar_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], h.encode())
    except ValueError:
        return False


def crear_token(asesor_id: int) -> str:
    return _firmador.sign(str(asesor_id)).decode()


def leer_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        return int(_firmador.unsign(token, max_age=config.HORAS_SESION * 3600))
    except (BadSignature, SignatureExpired, ValueError):
        return None


def asesor_actual(request: Request) -> Asesor:
    aid = leer_token(request.cookies.get(COOKIE))
    if aid is None:
        raise HTTPException(401, "Sesión vencida, inicia sesión de nuevo")
    with SessionLocal() as s:
        a = s.get(Asesor, aid)
    if not a or a.estado != "activo":
        raise HTTPException(401, "Tu cuenta no está activa")
    return a


def solo_admin(a: Asesor = Depends(asesor_actual)) -> Asesor:
    if not a.es_admin:
        raise HTTPException(403, "Solo un administrador puede hacer esto")
    return a
