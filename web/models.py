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

    id = Column(BigInteger, primary_key=True)
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
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(BigInteger, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    frequency = Column(Enum('daily', 'weekly'), default='daily')
    site = Column(String(100))
    categories = Column(JSON)
    confirm_token = Column(String(64))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    confirmed_at = Column(DateTime)


class EmailLog(Base):
    __tablename__ = 'email_logs'

    id = Column(BigInteger, primary_key=True)
    subscription_id = Column(BigInteger, ForeignKey('subscriptions.id', ondelete='SET NULL'))
    email_type = Column(Enum('confirmation', 'welcome', 'digest', 'unsubscribe'), nullable=False)
    status = Column(Enum('sent', 'failed', 'bounced'), default='sent')
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FetchLog(Base):
    __tablename__ = 'fetch_logs'

    id = Column(BigInteger, primary_key=True)
    source = Column(String(255), nullable=False)
    articles_count = Column(Integer, default=0)
    status = Column(Enum('success', 'failed', 'partial'), default='success')
    error_message = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
