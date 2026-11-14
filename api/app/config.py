import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://geo:geo@localhost:5432/geo",
            ),
        )


settings = Settings.from_env()
