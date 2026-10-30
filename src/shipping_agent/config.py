from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    llm_model: str = "glm-4.5-flash"
    llm_timeout_seconds: float = Field(120, gt=0)
    llm_max_output_tokens: int = Field(12000, ge=100)
    llm_attempts: int = Field(2, ge=1, le=3)
    llm_disable_thinking: bool = True
    ocr_mode: Literal["remote", "local"] = "remote"
    ocr_endpoint: str = "https://gateway.pltplt.com/ai/process"
    ocr_api_key: SecretStr = SecretStr("")
    ocr_attempts: int = Field(2, ge=1, le=3)
    ocr_file_base_url: str = ""
    ocr_file_secret: SecretStr = SecretStr("")
    ocr_file_ttl_seconds: int = Field(900, ge=60, le=3600)
    libreoffice_path: str = ""
    cloudflared_path: str = ""
    credit_note_policy: Literal["review", "linked_signed_adjustment"] = "review"
    accounting_entity: str | None = None
    accounting_aliases: list[str] = []
    data_dir: Path = Path("data/private")
    max_file_bytes: int = Field(20 * 1024 * 1024, gt=0)
    max_batch_bytes: int = Field(100 * 1024 * 1024, gt=0)
    max_documents: int = Field(100, gt=0)
    max_depth: int = Field(4, ge=0, le=10)
    max_pages: int = Field(30, gt=0)
    max_text_chars: int = Field(90000, gt=100)
    api_token: SecretStr = SecretStr("")
