# app/services/storage.py
import os
import uuid
import boto3
from botocore.config import Config


class Storage:
    def __init__(self):
        self.bucket = os.environ.get("R2_BUCKET") or os.environ.get("S3_BUCKET")
        self.endpoint_url = os.environ.get("R2_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL")
        self.access_key = os.environ.get("R2_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret_key = os.environ.get("R2_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")

        if not self.bucket:
            raise RuntimeError("Bucket no configurado (R2_BUCKET o S3_BUCKET).")
        if not self.endpoint_url:
            raise RuntimeError("Endpoint no configurado (R2_ENDPOINT_URL o S3_ENDPOINT_URL).")
        if not self.access_key or not self.secret_key:
            raise RuntimeError("Credenciales no configuradas (R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY o AWS_*).")

        # Fuerza región para S3 compatible
        os.environ.setdefault("AWS_DEFAULT_REGION", "auto")

        cfg = Config(
            region_name="auto",
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
            tcp_keepalive=True,
        )

        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url.strip(),
            aws_access_key_id=self.access_key.strip(),
            aws_secret_access_key=self.secret_key.strip(),
            config=cfg,
            use_ssl=True,
            verify=True,  # PROD
        )

    def upload_fileobj(self, fileobj, key: str, content_type: str):
        extra = {"ContentType": content_type} if content_type else {}

        self.s3.upload_fileobj(
            Fileobj=fileobj,
            Bucket=self.bucket,
            Key=key,
            ExtraArgs=extra,
        )

        base = self.endpoint_url.rstrip("/")
        return f"{base}/{self.bucket}/{key}"


def get_storage() -> Storage:
    return Storage()


def build_photo_key(container_code: str, movement_id: int, filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()
    safe = container_code.replace("-", "")
    rand = uuid.uuid4().hex[:12]
    return f"photos/{safe}/movement_{movement_id}/{rand}.{ext}"

