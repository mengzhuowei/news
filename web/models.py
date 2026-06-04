from datetime import datetime, timezone
from sqlalchemy import (
    Column, BigInteger, Integer, String, Text,
    Boolean, DateTime, ForeignKey, Enum,
)
from sqlalchemy.dialects.mysql import JSON, MEDIUMTEXT
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Article(Base):
    __tablename__ = 'articles'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url = Column(String(768), unique=True, nullable=False)
    title = Column(String(1024), nullable=False)
    summary = Column(Text, default='')
    content = Column(MEDIUMTEXT, default='')
    source = Column(String(255), nullable=False)
    category = Column(String(100))
    author = Column(Text, default='Unknown')
    image_url = Column(String(1024), default='')
    slug = Column(String(256), default='')
    is_rewritten = Column(Boolean, default=False)
    is_built = Column(Boolean, default=False)

    # 翻译字段：简体中文、繁体中文、日语、俄语、德语、法语
    title_zh_cn = Column(String(1024), default='')
    title_zh_tw = Column(String(1024), default='')
    title_ja = Column(String(1024), default='')
    title_ru = Column(String(1024), default='')
    title_de = Column(String(1024), default='')
    title_fr = Column(String(1024), default='')

    summary_zh_cn = Column(Text, default='')
    summary_zh_tw = Column(Text, default='')
    summary_ja = Column(Text, default='')
    summary_ru = Column(Text, default='')
    summary_de = Column(Text, default='')
    summary_fr = Column(Text, default='')

    content_zh_cn = Column(MEDIUMTEXT, default='')
    content_zh_tw = Column(MEDIUMTEXT, default='')
    content_ja = Column(MEDIUMTEXT, default='')
    content_ru = Column(MEDIUMTEXT, default='')
    content_de = Column(MEDIUMTEXT, default='')
    content_fr = Column(MEDIUMTEXT, default='')
    is_translated = Column(Boolean, default=False)

    published_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Subscription(Base):
    __tablename__ = 'subscriptions'
    __table_args__ = (
        # 至少要有 email 或 chat_id 之一，防止孤儿记录
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=True, unique=True)
    chat_id = Column(BigInteger, unique=True, nullable=True)
    is_active = Column(Boolean, default=True)
    frequency = Column(Enum('daily', 'weekly'), default='daily')
    push_time = Column(String(8), default='09:00:00')
    timezone = Column(String(50), default='UTC')
    push_channel = Column(Enum('telegram', 'email'), default='telegram')
    site = Column(String(100))
    categories = Column(JSON)
    confirm_token = Column(String(64))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    confirmed_at = Column(DateTime)
    last_pushed_at = Column(DateTime)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.email and not self.chat_id:
            raise ValueError("Subscription must have at least email or chat_id")


class EmailLog(Base):
    __tablename__ = 'email_logs'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    subscription_id = Column(BigInteger, ForeignKey('subscriptions.id', ondelete='SET NULL'))
    email_type = Column(Enum('confirmation', 'welcome', 'digest', 'unsubscribe'), nullable=False)
    status = Column(Enum('sent', 'failed', 'bounced'), default='sent')
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FetchLog(Base):
    __tablename__ = 'fetch_logs'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source = Column(String(255), nullable=False)
    articles_count = Column(Integer, default=0)
    status = Column(Enum('success', 'failed', 'partial'), default='success')
    error_message = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
