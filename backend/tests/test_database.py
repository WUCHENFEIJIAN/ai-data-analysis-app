from app.core.database import Database


def test_sqlite_database_creates_parent_directory(tmp_path) -> None:
    database_path = tmp_path / "nested" / "data" / "app.db"
    database = Database(f"sqlite:///{database_path}")

    database.create_all()

    assert database_path.parent.is_dir()
    database.dispose()
