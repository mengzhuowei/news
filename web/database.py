import os
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "news_aggregator")

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{quote_plus(MYSQL_USER)}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from web.models import Base  # noqa: E402


def get_db():
    """FastAPI 依赖注入 — 获取数据库 Session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
