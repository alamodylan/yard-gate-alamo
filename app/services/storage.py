import os
import uuid
from typing import Optional

import boto3
from botocore.client import Config as BotoConfig
from flask import current_app

class StorageBase:
    def upload_bytes(self, data: bytes, key: str, content_type: str) -> str:
        raise NotImplementedError

    def upload_fileobj(self, fileobj, key: str, content_type: str) -> str:
        raise NotImplementedError

def _safe_join(*parts: str) -> str:
    return "/".join([p.strip("/").replace("\\", "/") for p in parts if p])

class LocalStorage(StorageBase):
    def __init__(self, base_dir: str = "uploads"):
        self.base_dir = base_dir

    def upload_fileobj(self, fileobj, key: str, content_type: str) -> str:
        os.makedirs(self.base_dir, exist_ok=True)
        path = os.path.join(self.base_dir, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fileobj.save(path)
        # En local devolvemos un path; luego podrías servirlo con send_from_directory si quieres
        return path

    def upload_bytes(self, data: bytes, key: str, content_type: str) -> str:
        os.makedirs(self.base_dir, exist_ok=True)
        path = os.path.join(self.base_dir, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

class R2Storage(StorageBase):
    def __init__(self):
        cfg = current_app.config
        self.bucket = cfg["R2_BUCKET"]
        self.public_base = cfg.get("R2_PUBLIC_BASE_URL")  # opcional

        self.s3 = boto3.client(
            "s3",
            endpoint_url=cfg["R2_ENDPOINT"],
            aws_access_key_id=cfg["R2_ACCESS_KEY"],
            aws_secret_access_key=cfg["R2_SECRET_KEY"],
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )

    def upload_fileobj(self, fileobj, key: str, content_type: str) -> str:
        self.s3.upload_fileobj(
            fileobj.stream,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        return self._url_for(key)

    def upload_bytes(self, data: bytes, key: str, content_type: str) -> str:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return self._url_for(key)

    def _url_for(self, key: str) -> str:
        # Si tienes public base url, úsala:
        if self.public_base:
            return _safe_join(self.public_base, key)
        # Si no, devolvemos una URL "s3 style" (puede requerir signed URLs en futuro)
        # Para este proyecto recomendamos configurar public base url.
        return f"s3://{self.bucket}/{key}"

def get_storage() -> StorageBase:
    provider = (current_app.config.get("STORAGE_PROVIDER") or "local").lower()
    if provider == "r2":
        return R2Storage()
    return LocalStorage()

def build_photo_key(container_code: str, movement_id: int, filename: str) -> str:
    # Clave ordenada y única (evita colisiones)
    ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin")
    uid = uuid.uuid4().hex[:12]
    safe_code = container_code.replace("-", "")
    return f"photos/{safe_code}/movement_{movement_id}/{uid}.{ext}"