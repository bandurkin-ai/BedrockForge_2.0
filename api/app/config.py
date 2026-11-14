import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    upload_dir: Path
    max_upload_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://geo:geo@localhost:5432/geo",
            ),
            upload_dir=Path(os.environ.get("UPLOAD_DIR", Path(tempfile.gettempdir()) / "geo-uploads")),
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_MB", "200")) * 1024 * 1024,
        )


settings = Settings.from_env()
