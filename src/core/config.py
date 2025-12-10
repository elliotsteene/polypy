from enum import StrEnum

from pydantic_settings import BaseSettings


class Environment(StrEnum):
    development = "development"
    production = "production"


class Settings(BaseSettings):
    ENV: Environment = Environment.development
    LOG_LEVEL: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.ENV == Environment.production


settings = Settings()
