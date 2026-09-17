"""应用配置：通过环境变量注入，密钥绝不硬编码。"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "dev"
    SECRET_KEY: str = "dev-only-insecure-secret-change-me"
    DATABASE_URL: str = "sqlite:///./dev.db"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "prod"

    @property
    def deepseek_ready(self) -> bool:
        return bool(self.DEEPSEEK_API_KEY)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # 生产环境必须有强 SECRET_KEY
    if s.is_prod and (not s.SECRET_KEY or s.SECRET_KEY.startswith("change-me")):
        raise RuntimeError("生产环境必须设置强随机 SECRET_KEY")
    if s.is_prod and not s.DEEPSEEK_API_KEY:
        print("⚠️ 警告：生产环境未配置 DEEPSEEK_API_KEY，AI 出题功能将不可用")
    return s
