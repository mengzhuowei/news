import os
import time
from pathlib import Path
from urllib.parse import quote_plus
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from loguru import logger

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "news_aggregator")

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{quote_plus(MYSQL_USER)}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
)

_MYSQL_SERVER_URL = (
    f"mysql+pymysql://{quote_plus(MYSQL_USER)}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}"
)

PROJECT_ROOT = Path(__file__).parent.parent
SCHEMA_SQL_PATH = PROJECT_ROOT / "database" / "schema.sql"
MIGRATIONS_DIR = PROJECT_ROOT / "database" / "migrations"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from web.models import Base  # noqa: E402

_DB_CONNECT_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "10"))
_DB_CONNECT_RETRY_INTERVAL = int(os.getenv("DB_CONNECT_RETRY_INTERVAL", "3"))

_SKIPPABLE_ERROR_CODES = {1060, 1061, 1091}

_DUPLICATE_COLUMN = 1060
_DUPLICATE_KEY = 1061
_CANT_DROP_MISSING = 1091


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


from contextlib import contextmanager


@contextmanager
def get_session():
    """线程安全的数据库 session 上下文管理器 — 自动 commit/rollback/close"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    _wait_for_database()
    _ensure_database_exists()
    _ensure_schema_migrations_table()
    _run_schema_if_needed()
    _run_pending_migrations()


def _wait_for_database():
    server_engine = create_engine(_MYSQL_SERVER_URL, echo=False)
    try:
        for attempt in range(1, _DB_CONNECT_RETRIES + 1):
            try:
                with server_engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                logger.info("[init_db] MySQL server is reachable")
                return
            except Exception as e:
                logger.warning(
                    f"[init_db] MySQL not ready (attempt {attempt}/{_DB_CONNECT_RETRIES}): {e}"
                )
                if attempt < _DB_CONNECT_RETRIES:
                    time.sleep(_DB_CONNECT_RETRY_INTERVAL)
        raise RuntimeError(
            f"Cannot connect to MySQL after {_DB_CONNECT_RETRIES} attempts"
        )
    finally:
        server_engine.dispose()


def _ensure_database_exists():
    server_engine = create_engine(_MYSQL_SERVER_URL, echo=False, isolation_level="AUTOCOMMIT")
    try:
        with server_engine.connect() as conn:
            result = conn.execute(
                text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :name"),
                {"name": MYSQL_DATABASE},
            )
            if result.fetchone() is None:
                logger.info(f"[init_db] Database '{MYSQL_DATABASE}' does not exist, creating...")
                conn.execute(
                    text(
                        f"CREATE DATABASE `{MYSQL_DATABASE}` "
                        f"DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
                    )
                )
                logger.info(f"[init_db] Database '{MYSQL_DATABASE}' created successfully")
            else:
                logger.info(f"[init_db] Database '{MYSQL_DATABASE}' already exists")
    finally:
        server_engine.dispose()


def _ensure_schema_migrations_table():
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  id INT AUTO_INCREMENT PRIMARY KEY,"
            "  migration_name VARCHAR(255) NOT NULL UNIQUE,"
            "  executed_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        ))
        conn.commit()


def _run_schema_if_needed():
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = :db"),
            {"db": MYSQL_DATABASE},
        )
        table_count = result.scalar()

    if table_count <= 1:
        if SCHEMA_SQL_PATH.exists():
            logger.info("[init_db] No tables found, executing schema.sql...")
            _execute_sql_file(SCHEMA_SQL_PATH, skip_create_database=True)
            logger.info("[init_db] schema.sql executed successfully")
        else:
            logger.info("[init_db] No tables found, creating from ORM models...")
            Base.metadata.create_all(bind=engine)
            logger.info("[init_db] Tables created from ORM models")
    else:
        logger.info("[init_db] Tables already exist, skipping schema.sql")


def _run_pending_migrations():
    if not MIGRATIONS_DIR.exists():
        logger.info("[init_db] No migrations directory found, skipping migrations")
        return

    migration_files = sorted(
        f for f in MIGRATIONS_DIR.iterdir()
        if f.is_file() and f.suffix == ".sql"
    )

    if not migration_files:
        logger.info("[init_db] No migration files found")
        return

    with engine.connect() as conn:
        executed = conn.execute(text("SELECT migration_name FROM schema_migrations")).fetchall()
        executed_names = {row[0] for row in executed}

    for migration_file in migration_files:
        migration_name = migration_file.name
        if migration_name in executed_names:
            logger.info(f"[init_db] Migration '{migration_name}' already executed, skipping")
            continue

        logger.info(f"[init_db] Executing migration '{migration_name}'...")
        try:
            _execute_migration_file(migration_file)
            with engine.connect() as conn:
                conn.execute(
                    text("INSERT IGNORE INTO schema_migrations (migration_name) VALUES (:name)"),
                    {"name": migration_name},
                )
                conn.commit()
            logger.info(f"[init_db] Migration '{migration_name}' executed successfully")
        except Exception as e:
            logger.error(f"[init_db] Migration '{migration_name}' failed: {e}")
            raise


def _execute_migration_file(sql_path: Path):
    sql_content = sql_path.read_text(encoding="utf-8")
    statements = _split_sql(sql_content)
    with engine.connect() as conn:
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                conn.execute(text(stmt))
            except Exception as e:
                err_code = _get_mysql_error_code(e)
                if err_code in _SKIPPABLE_ERROR_CODES:
                    logger.debug(f"[init_db] Skipped idempotent statement (err {err_code}): {stmt[:80]}...")
                else:
                    raise
        conn.commit()


def _get_mysql_error_code(exc: Exception) -> int:
    args = getattr(exc, "args", None) or getattr(exc, "orig", None)
    if args is None:
        return -1
    if hasattr(args, "args"):
        args = args.args
    if isinstance(args, (list, tuple)) and len(args) >= 1:
        code = args[0]
        if isinstance(code, int):
            return code
    orig = getattr(exc, "orig", None)
    if orig:
        return _get_mysql_error_code(orig)
    return -1


def _execute_sql_file(sql_path: Path, skip_create_database: bool = False):
    sql_content = sql_path.read_text(encoding="utf-8")
    statements = _split_sql(sql_content)
    with engine.connect() as conn:
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            if skip_create_database and _is_database_level_statement(stmt):
                logger.debug(f"[init_db] Skipping database-level statement: {stmt[:60]}...")
                continue
            conn.execute(text(stmt))
        conn.commit()


def _is_database_level_statement(stmt: str) -> bool:
    upper = stmt.lstrip().upper()
    return upper.startswith("CREATE DATABASE") or upper.startswith("USE ")


def _split_sql(sql: str) -> list[str]:
    sql = _strip_sql_comments(sql)
    parts = sql.split(";")
    return [p.strip() for p in parts if p.strip()]


def _strip_sql_comments(sql: str) -> str:
    result = []
    i = 0
    n = len(sql)
    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    while i < n:
        ch = sql[i]

        if in_single_quote:
            result.append(ch)
            if ch == "'" and i + 1 < n and sql[i + 1] == "'":
                result.append(sql[i + 1])
                i += 2
                continue
            if ch == "'":
                in_single_quote = False
            i += 1
            continue

        if in_double_quote:
            result.append(ch)
            if ch == '"' and i + 1 < n and sql[i + 1] == '"':
                result.append(sql[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double_quote = False
            i += 1
            continue

        if in_backtick:
            result.append(ch)
            if ch == "`" and i + 1 < n and sql[i + 1] == "`":
                result.append(sql[i + 1])
                i += 2
                continue
            if ch == "`":
                in_backtick = False
            i += 1
            continue

        if ch == "'":
            in_single_quote = True
            result.append(ch)
            i += 1
            continue

        if ch == '"':
            in_double_quote = True
            result.append(ch)
            i += 1
            continue

        if ch == "`":
            in_backtick = True
            result.append(ch)
            i += 1
            continue

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue

        if ch == "#":
            while i < n and sql[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            closed = False
            while i + 1 < n:
                if sql[i] == "*" and sql[i + 1] == "/":
                    i += 2
                    closed = True
                    break
                i += 1
            if not closed:
                logger.warning("[init_db] Unterminated block comment in SQL, rest of input ignored")
            continue

        result.append(ch)
        i += 1

    return "".join(result)
