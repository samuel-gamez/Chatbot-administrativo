"""Modelos y conexión a la base de datos (Postgres en la nube, SQLite en local)."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app import config


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Asesor(Base):
    __tablename__ = "asesores"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    es_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente | activo | inactivo
    en_turno: Mapped[bool] = mapped_column(Boolean, default=False)
    ultimo_ping: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


class Conversacion(Base):
    __tablename__ = "conversaciones"
    __table_args__ = (UniqueConstraint("telefono", "linea_id", name="uq_telefono_linea"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    telefono: Mapped[str] = mapped_column(String(40), index=True)
    # Línea de WhatsApp del bot por la que escribió (phone_number_id de Meta) y su número visible
    linea_id: Mapped[str] = mapped_column(String(40), default="")
    linea_numero: Mapped[str | None] = mapped_column(String(40), nullable=True)
    nombre: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # bot: responde la IA | humano: la atiende un asesor y la IA calla
    modo: Mapped[str] = mapped_column(String(10), default="bot")
    necesita_asesor: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    solicitado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    asesor_id: Mapped[int | None] = mapped_column(ForeignKey("asesores.id"), nullable=True)
    ultima_actividad: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora, index=True)
    ultimo_msg_usuario: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_msg_asesor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    no_leidos: Mapped[int] = mapped_column(Integer, default=0)
    es_simulada: Mapped[bool] = mapped_column(Boolean, default=False)

    asesor: Mapped[Asesor | None] = relationship(lazy="joined")
    mensajes: Mapped[list["Mensaje"]] = relationship(back_populates="conversacion", order_by="Mensaje.id")


class Mensaje(Base):
    __tablename__ = "mensajes"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversacion_id: Mapped[int] = mapped_column(ForeignKey("conversaciones.id"), index=True)
    origen: Mapped[str] = mapped_column(String(10))  # usuario | bot | asesor | sistema
    texto: Mapped[str] = mapped_column(Text)
    asesor_id: Mapped[int | None] = mapped_column(ForeignKey("asesores.id"), nullable=True)
    wa_id: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    error_envio: Mapped[str | None] = mapped_column(String(300), nullable=True)
    enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # hora real en WhatsApp
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora, index=True)

    conversacion: Mapped[Conversacion] = relationship(back_populates="mensajes")
    asesor: Mapped[Asesor | None] = relationship(lazy="joined")


class Ajuste(Base):
    """Valores editables desde el panel (p. ej. la base de conocimiento)."""
    __tablename__ = "ajustes"
    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora, onupdate=ahora)


_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_args, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def crear_tablas() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if not s.get(Ajuste, "conocimiento"):
            texto = config.ARCHIVO_CONOCIMIENTO.read_text(encoding="utf-8") \
                if config.ARCHIVO_CONOCIMIENTO.exists() else ""
            s.add(Ajuste(clave="conocimiento", valor=texto))
            s.commit()


def obtener_ajuste(s: Session, clave: str, defecto: str = "") -> str:
    a = s.get(Ajuste, clave)
    return a.valor if a else defecto


def asesores_en_turno(s: Session) -> int:
    """Asesores marcados 'En turno' con el panel abierto (ping en los últimos 3 min)."""
    from datetime import timedelta
    limite = ahora() - timedelta(minutes=3)
    filas = s.scalars(select(Asesor).where(Asesor.en_turno.is_(True), Asesor.estado == "activo")).all()
    return sum(1 for a in filas if a.ultimo_ping and utc(a.ultimo_ping) >= limite)


def utc(dt: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona; las normalizamos a UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
