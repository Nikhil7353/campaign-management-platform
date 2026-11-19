from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Communication Desk"
    app_env: str = "development"
    database_url: str = "sqlite:///./storage/database.sqlite"
    frontend_origin: str = "http://localhost:5173"
    automatic_sending_enabled: bool = True
    mail_dry_run: bool = True
    smtp_host: str = "smtp-relay.brevo.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    mail_from_address: str = ""
    mail_from_name: str = "Communication Desk"
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    inbox_auto_sync_enabled: bool = False
    inbox_auto_sync_interval_seconds: int = 300
    whatsapp_base_url: str = ""
    whatsapp_access_token: str = ""
    whatsapp_api_version: str = "v23.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/google/callback"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

