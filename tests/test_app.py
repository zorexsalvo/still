import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from still import app, health, lifespan


class AppTest(unittest.TestCase):
    def test_database_survives_app_restart(self):
        async def check(path):
            async with lifespan(app):
                self.assertEqual(health(), {"status": "ok"})
                with sqlite3.connect(path) as connection:
                    self.assertEqual(
                        connection.execute("PRAGMA journal_mode").fetchone()[0], "wal"
                    )
                    connection.execute("CREATE TABLE persistence_test (value TEXT)")
                    connection.execute("INSERT INTO persistence_test VALUES ('kept')")
            async with lifespan(app):
                with sqlite3.connect(path) as connection:
                    self.assertEqual(
                        connection.execute("SELECT value FROM persistence_test").fetchone(),
                        ("kept",),
                    )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "still.sqlite3"
            with patch.dict(os.environ, {"STILL_DB_PATH": str(path)}):
                asyncio.run(check(path))
