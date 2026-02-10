# app/services/storage.py
import os
import uuid
import boto3
from botocore.config import Config


class Storage:
    def __init__(self):
        # Bucket
        self.bucket = os.environ.get("R2_BUCKET") or os.environ.get("S3_BUCKET")
        if not self.bucket:
            raise RuntimeError("Bucket no configurado (R2_BUCKET o S3_BUCKET).")

        # Endpoint (soporta nombres viejos y nuevos)
        self.endpoint_url = (
            os.environ.get("R2_ENDPOINT_URL")
            or os.environ.get("R2_ENDPOINT")
            or os.environ.get("S3_ENDPOINT_URL")
        )
        if not self.endpoint_url:
            raise RuntimeError("Endpoint no configurado (R2_ENDPOINT_URL o R2_ENDPOINT o S3_ENDPOINT_URL).")

        # Credenciales (soporta nombres viejos y nuevos)
        self.access_key = (
            os.environ.get("R2_ACCESS_KEY_ID")
            or os.environ.get("R2_ACCESS_KEY")
            or os.environ.get("AWS_ACCESS_KEY_ID")
        )
        self.secret_key = (
            os.environ.get("R2_SECRET_ACCESS_KEY")
            or os.environ.get("R2_SECRET_KEY")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
        )
        if not self.access_key or not self.secret_key:
            raise RuntimeError(
                "Credenciales no configuradas (R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY o R2_ACCESS_KEY/R2_SECRET_KEY o AWS_*)."
            )

        # Base pública opcional para guardar URLs que sí abren en el navegador (R2.dev / custom domain)
        self.public_base_url = os.environ.get("R2_PUBLIC_BASE_URL") or os.environ.get("PUBLIC_BASE_URL")

        # Config recomendado para S3-compatible (Cloudflare R2)
        cfg = Config(
            region_name="auto",
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 5, "mode": "standard"},
        )

        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=cfg,
            use_ssl=True,
            verify=True,
        )

    def upload_fileobj(self, fileobj, key: str, content_type: str | None = None) -> str:
        """
        Sube un archivo (file-like) a R2/S3 y retorna una URL para guardar en BD.
        - Si existe R2_PUBLIC_BASE_URL (recomendado), devuelve: {public_base}/{key}
        - Si no, devuelve: {endpoint}/{bucket}/{key} (puede no ser accesible públicamente)
        """
        extra = {"ContentType": content_type} if content_type else {}

        # Nota: boto3 usa streaming. Si fileobj es werkzeug.FileStorage, sirve directo.
        self.s3.upload_fileobj(
            Fileobj=fileobj,
            Bucket=self.bucket,
            Key=key,
            ExtraArgs=extra,
        )

        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/{key}"

        base = self.endpoint_url.rstrip("/")
        return f"{base}/{self.bucket}/{key}"


def get_storage() -> Storage:
    return Storage()


def build_photo_key(container_code: str, movement_id: int, filename: str) -> str:
    """
    Genera key estable y segura:
      photos/{CONTENEDOR_SIN_GUIONES}/movement_{id}/{rand}.{ext}
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()
    safe = (container_code or "").replace("-", "").replace(" ", "")
    rand = uuid.uuid4().hex[:12]
    return f"photos/{safe}/movement_{movement_id}/{rand}.{ext}"


