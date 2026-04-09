"""
Application settings — loaded from .env file or environment variables.
Users must provide their own ANTHROPIC_API_KEY.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-6"
    port: int = 8000

    def validate_api_key(self) -> None:
        if not self.anthropic_api_key or self.anthropic_api_key.startswith("sk-ant-your"):
            raise SystemExit(
                "\n[ERROR] ANTHROPIC_API_KEY is not set.\n"
                "Copy .env.example to .env and paste your API key:\n"
                "  cp .env.example .env\n"
                "  # Edit .env and replace the placeholder with your key\n"
            )


settings = Settings()
