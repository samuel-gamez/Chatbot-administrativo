"""Configuración leída de variables de entorno (.env en local, Environment en Render)."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# --- WhatsApp Cloud API (Meta) ---
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION") or "v27.0"

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.6-flash"
if GEMINI_MODEL == "gemini-3.7-flash":   # nombre equivocado de versiones anteriores
    GEMINI_MODEL = "gemini-3.6-flash"
# Cuánto "piensa" la IA antes de responder: MINIMAL = lo más rápido (suficiente para admisiones)
GEMINI_RAZONAMIENTO = (os.getenv("GEMINI_RAZONAMIENTO") or "MINIMAL").upper()
SEGUNDOS_TIMEOUT_IA = int(os.getenv("SEGUNDOS_TIMEOUT_IA", "20"))   # espera máxima por cada intento
SEGUNDOS_TOTAL_IA = int(os.getenv("SEGUNDOS_TOTAL_IA", "60"))       # espera máxima sumando reintentos y respaldos
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.8"))

# --- Base de datos ---
# En Render: la "Internal/External Database URL" de Postgres. En local: SQLite.
# (una variable vacía en .env cuenta como "no definida")
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip() or f"sqlite:///{(BASE_DIR / 'local.db').as_posix()}"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# --- Panel de asesores ---
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)  # en Render se genera fija
HORAS_SESION = int(os.getenv("HORAS_SESION", "12"))
URL_PUBLICA = os.getenv("URL_PUBLICA", os.getenv("RENDER_EXTERNAL_URL", ""))  # Render la define sola
MINUTOS_KEEPALIVE = int(os.getenv("MINUTOS_KEEPALIVE", "10"))  # 0 = desactivado (planes pagos)

# --- Comportamiento del bot ---
NOMBRE_BOT = os.getenv("NOMBRE_BOT") or "Sofía"
NOMBRE_INSTITUCION = os.getenv("NOMBRE_INSTITUCION") or "UNIMINUTO Girardot"
MENSAJES_CONTEXTO = int(os.getenv("MENSAJES_CONTEXTO", "16"))       # mensajes previos que ve la IA
HORAS_AUTO_DEVOLVER_BOT = float(os.getenv("HORAS_AUTO_DEVOLVER_BOT", "8"))  # si el asesor deja el chat abandonado
HORARIO_ASESORES = os.getenv(
    "HORARIO_ASESORES",
    "lunes, martes y jueves de 8:00 a. m. a 6:00 p. m.; miércoles y viernes de 8:00 a. m. a 5:00 p. m. "
    "(cerrado de 1:00 a 2:00 p. m.); sábados de 8:00 a. m. a 12:00 m."
)
ARCHIVO_CONOCIMIENTO = BASE_DIR / "conocimiento" / "universidad.md"
