# app/config.py
import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///dev.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # ==========================================================
    # SQLAlchemy Engine
    # Optimización para Render + PostgreSQL
    # ==========================================================
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Verifica que la conexión siga viva antes de usarla
        "pool_pre_ping": True,

        # Evita conexiones viejas que Render pueda cerrar
        "pool_recycle": 280,

        # Espera máxima para obtener una conexión
        "pool_timeout": 20,

        # Cantidad fija de conexiones abiertas
        "pool_size": 5,

        # Conexiones temporales cuando hay más carga
        "max_overflow": 2,
    }

    # Upload limits (para fotos)
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25MB

    # Timezone
    APP_TZ = os.getenv("APP_TZ", "America/Costa_Rica")

    # Storage
    STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "local").lower()

    # R2 (S3)
    R2_ENDPOINT = os.getenv("R2_ENDPOINT")
    R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
    R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
    R2_BUCKET = os.getenv("R2_BUCKET")
    R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL")  # opcional
    PRINT_AGENT_KEY = os.getenv("PRINT_AGENT_KEY", "")

    # Registra en logs las rutas que superen este tiempo.
    SLOW_REQUEST_MS = int(
        os.getenv("SLOW_REQUEST_MS", "1000")
    )

    # ==========================================================
    # Cola de impresión
    # ==========================================================
    PRINT_JOB_STALE_MINUTES = int(
        os.getenv("PRINT_JOB_STALE_MINUTES", "5")
    )

    PRINT_JOB_STALE_SWEEP_SECONDS = int(
        os.getenv("PRINT_JOB_STALE_SWEEP_SECONDS", "60")
    )