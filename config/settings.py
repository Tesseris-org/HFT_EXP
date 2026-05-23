import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).parent.parent
load_dotenv(_project_root / ".env")

# Execution mode
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper")

# Polymarket
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")

# AWS
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# News
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Database
DB_PATH = os.getenv("DB_PATH", str(_project_root / "agent.db"))

# Market filters
MARKET_FILTERS = {
    "min_volume_24h": 5000,   # lowered for testing - more markets available
    "min_liquidity": 1000,    # lowered for testing - more markets available
    "min_time_to_resolution_hours": 2,
    "max_time_to_resolution_days": 90,
    "price_range": (0.10, 0.90),
    "categories": None,  # None = all
}

# Signal weights (initial, calibrated over time)
SIGNAL_WEIGHTS = {
    "llm": 0.50,
    "cross_platform": 0.35,
    "news": 0.15,
}

# Signal freshness (minutes)
MAX_SIGNAL_AGE = {
    "llm": 30,
    "cross_platform": 60,
    "news": 15,
}

# Risk limits
RISK_LIMITS = {
    "max_position_per_market": 100,
    "max_positions_total": 10,
    "max_exposure_total": 500,
    "max_exposure_per_category": 200,
    "max_daily_loss": 50,
    "max_weekly_loss": 150,
    "max_drawdown_pct": 0.15,
    "max_signal_age_minutes": 30,
    "max_price_age_seconds": 60,
    "min_bankroll": 100,
    "max_orders_per_hour": 20,
}

# Paper trading
INITIAL_PAPER_BALANCE = 1000.0
ORDER_TIMEOUT_MINUTES = 15

# Task intervals (seconds)
INTERVALS = {
    "market_data_poll": 60,
    "news_ingestion": 300,
    "cross_platform_fetch": 600,
    "scheduled_analysis": 120,  # 2 min for paper trading feedback; increase to 300 for live
    "order_management": 10,
    "risk_monitoring": 30,
    "position_reconciliation": 900,
    "health_check": 60,
}
