from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tg_bot_token: str = Field(alias="TG_BOT_TOKEN")
    tg_webhook_secret: str = Field(alias="TG_WEBHOOK_SECRET")
    tg_allowed_chat_id: int | None = Field(default=None, alias="TG_ALLOWED_CHAT_ID")
    tg_admin_ids: str = Field(default="", alias="TG_ADMIN_IDS")

    cerebras_api_key: str = Field(alias="CEREBRAS_API_KEY")
    cerebras_model: str = Field(default="qwen-3-235b-a22b-instruct-2507", alias="CEREBRAS_MODEL")
    cerebras_base_url: str = Field(default="https://api.cerebras.ai/v1", alias="CEREBRAS_BASE_URL")

    database_url: str = Field(alias="DATABASE_URL")
    steam_api_key: str | None = Field(default=None, alias="STEAM_API_KEY")

    public_base_url: str = Field(alias="PUBLIC_BASE_URL")
    port: int = Field(default=10000, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    embed_model: str = Field(default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", alias="EMBED_MODEL")
    embed_dim: int = Field(default=384, alias="EMBED_DIM")

    chat_history_limit: int = Field(default=30, alias="CHAT_HISTORY_LIMIT")
    memory_top_k: int = Field(default=5, alias="MEMORY_TOP_K")
    memory_extract_every: int = Field(default=20, alias="MEMORY_EXTRACT_EVERY")
    rate_limit_per_minute: int = Field(default=20, alias="RATE_LIMIT_PER_MINUTE")

    @field_validator("tg_allowed_chat_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.tg_admin_ids.split(",") if x.strip().isdigit()}

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.tg_webhook_secret}"

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
