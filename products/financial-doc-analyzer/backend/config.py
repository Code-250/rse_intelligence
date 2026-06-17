"""
Central configuration for the Financial Document Analyzer backend.

All values are read from environment variables (loaded from `.env` in local
development via python-dotenv). Nothing here is ever hardcoded with a real
secret — see `.env.example` for the full list of variables.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Typed accessor over the process environment.

    Attributes are resolved once at construction. Use `get_settings()` to obtain
    a cached singleton rather than instantiating this directly.
    """

    def __init__(self) -> None:
        # Database
        self.database_url: str = os.getenv(
            "DATABASE_URL", "postgresql://localhost:5432/rse_intelligence"
        )

        # JWT auth (consumed by FDA-003)
        self.secret_key: str = os.getenv("FDA_SECRET_KEY", "")
        self.access_token_expire_minutes: int = int(
            os.getenv("FDA_ACCESS_TOKEN_EXPIRE_MINUTES", "60")
        )
        self.refresh_token_expire_days: int = int(
            os.getenv("FDA_REFRESH_TOKEN_EXPIRE_DAYS", "30")
        )

        # Document storage / limits
        self.storage_path: str = os.getenv("FDA_STORAGE_PATH", "./data/documents")
        self.max_file_size_mb: int = int(os.getenv("FDA_MAX_FILE_SIZE_MB", "50"))
        self.free_tier_monthly_limit: int = int(
            os.getenv("FDA_FREE_TIER_MONTHLY_LIMIT", "10")
        )

        # NVIDIA NIM
        self.nim_api_key: str = os.getenv("NVIDIA_NIM_API_KEY", "")
        self.nim_model: str = os.getenv(
            "NVIDIA_NIM_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"
        )
        self.nim_longdoc_model: str = os.getenv(
            "NVIDIA_NIM_LONGDOC_MODEL", "deepseek-ai/deepseek-v4-flash"
        )

        # CORS
        self.allowed_origins: list[str] = [
            o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached `Settings` instance for the lifetime of the process."""
    return Settings()
