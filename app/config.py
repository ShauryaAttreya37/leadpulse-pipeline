from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from pathlib import Path


class Settings(BaseSettings):
    # Application Config
    APP_NAME: str = "LeadPulse"
    APP_ENV: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    
    # Storage / Database
    DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
    DATABASE_URL: str = f"sqlite:///{Path(__file__).resolve().parent.parent}/data/leads.db"
    
    # Playwright Crawler Settings
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_TIMEOUT_MS: int = 25000
    SCRAPE_DELAY_MS: int = 800
    MAX_CONCURRENT_PAGES: int = 4
    BLOCK_ASSETS: bool = True  # Block images, fonts, media to 5x scrape speeds
    
    # Slack Integration
    SLACK_WEBHOOK_URL: Optional[str] = None
    SLACK_CHANNEL: Optional[str] = "#b2b-leads"
    
    # Google Sheets Integration
    GOOGLE_SERVICE_ACCOUNT_FILE: Optional[str] = None
    GOOGLE_SHEET_ID: Optional[str] = None
    GOOGLE_SHEET_TITLE: str = "B2B Real Estate Agencies - Clean Leads"
    
    # Simulation / Safe Fallback Mode
    # If credentials for Slack or Google Sheets are absent, fallback to local mirror & formatted terminal logs
    ENABLE_SIMULATION_FALLBACK: bool = True
    
    # Optional Webhook Security Token
    WEBHOOK_SECRET_TOKEN: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
