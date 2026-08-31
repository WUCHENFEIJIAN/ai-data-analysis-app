from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self._ensure_sqlite_parent(database_url)
        self.engine = create_engine(database_url, connect_args=connect_args)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @staticmethod
    def _ensure_sqlite_parent(database_url: str) -> None:
        url = make_url(database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            return
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        # The MVP uses create_all rather than a migration runner. Keep additive
        # fields compatible with workspaces created earlier.
        columns = {item["name"] for item in inspect(self.engine).get_columns("analysis_runs")}
        if "analysis_topic" not in columns:
            with self.engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE analysis_runs ADD COLUMN analysis_topic VARCHAR(300)")
                )
        if "complete_analysis_repair_state_json" not in columns:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE analysis_runs ADD COLUMN "
                        "complete_analysis_repair_state_json TEXT"
                    )
                )
        artifact_columns = {
            item["name"] for item in inspect(self.engine).get_columns("artifacts")
        }
        if "report_schema_json" not in artifact_columns:
            with self.engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE artifacts ADD COLUMN report_schema_json TEXT")
                )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
