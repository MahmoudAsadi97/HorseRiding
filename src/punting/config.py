from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://punting:punting@localhost:5432/punting'
    api_token: SecretStr = SecretStr('')
    admin_username: str = 'admin'
    admin_password: SecretStr = SecretStr('')
    punting_form_api_key: SecretStr | None = None
    provider_base_url: str = 'https://api.puntingform.com.au'
    provider_config_path: Path = Path('config/provider.json')
    log_level: str = 'INFO'
