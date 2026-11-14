import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    upload_dir: Path
    max_upload_bytes: int
    # Claude assistant. Credentials are read by the Anthropic SDK from the environment.
    assistant_configured: bool
    assistant_model: str
    assistant_effort: str
    # Host headers accepted by the MCP endpoint (protection against DNS rebinding).
    mcp_allowed_hosts: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://geo:geo@localhost:5432/geo",
            ),
            upload_dir=Path(os.environ.get("UPLOAD_DIR", Path(tempfile.gettempdir()) / "geo-uploads")),
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_MB", "200")) * 1024 * 1024,
            assistant_configured=bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            assistant_model=os.environ.get("ASSISTANT_MODEL", "claude-opus-5-5"),
            assistant_effort=os.environ.get("ASSISTANT_EFFORT", "medium"),
            mcp_allowed_hosts=tuple(
                h.strip()
                for h in os.environ.get("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,api:*").split(",")
                if h.strip()
            ),
        )


settings = Settings.from_env()
