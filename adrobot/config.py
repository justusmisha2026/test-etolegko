from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADROBOT_", env_file=".env", extra="ignore"
    )

    demo: bool = False
    database_url: str = "sqlite:///./adrobot.db"
    keitaro_url: str = "https://tlgk.host"
    api_key: SecretStr = SecretStr("")
    campaign_prefix: str = ""
    writes_enabled: bool = False
    editor_enabled: bool = True
    timeout_seconds: float = Field(default=15, gt=0, le=120)

    @field_validator("keitaro_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Укажите HTTPS origin Keitaro без пути и учётных данных")
        return value.rstrip("/")

    def configuration_error(self) -> str | None:
        if self.demo:
            return None
        if not self.api_key.get_secret_value():
            return "Не задан ADROBOT_API_KEY в серверной конфигурации"
        return None

    def creation_error(self) -> str | None:
        prefix = self.campaign_prefix
        if self.demo:
            return None
        if (
            not prefix.startswith("test_")
            or not prefix.endswith("_")
            or len(prefix) < 7
        ):
            return "Задайте ADROBOT_CAMPAIGN_PREFIX в формате test_фамилия_"
        return None
