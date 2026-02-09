import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///dev.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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