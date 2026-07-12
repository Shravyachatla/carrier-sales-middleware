from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    middleware_auth_token: str

    tms_host: str
    tms_port: int
    tms_auth_token: str
    tms_timeout_seconds: float = 5.0
    tms_max_retries: int = 2

    fmcsa_api_key: str

    otp_provider: str = "console"
    otp_expiry_seconds: int = 300
    otp_max_attempts: int = 3

    negotiation_max_rounds: int = 3


settings = Settings()
