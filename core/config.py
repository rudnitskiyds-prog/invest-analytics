"""Общие настройки платформы."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("IP_DATA_DIR", ROOT / "data"))
CACHE_DB = DATA_DIR / "cache.sqlite"
PORTFOLIO_DB = DATA_DIR / "portfolio.sqlite"
FUNDAMENTALS_CSV = DATA_DIR / "fundamentals.csv"

ISS_BASE = "https://iss.moex.com/iss"
CBR_BASE = "https://www.cbr.ru"

# TTL кэша (сек.)
TTL_MARKET = 15 * 60          # текущие котировки / витрина
TTL_HISTORY_OPEN = 6 * 3600    # история, включающая сегодняшний день
TTL_REFERENCE = 7 * 24 * 3600  # справочники

# Параметры по умолчанию
DEFAULT_CAPITAL = 1_000_000
DEFAULT_RF = 0.0786            # 15-летняя ставка КБД Мосбиржи на 11.01.2011
DEFAULT_BENCHMARK = "MCFTR"
DEFAULT_FREQ = "M"
