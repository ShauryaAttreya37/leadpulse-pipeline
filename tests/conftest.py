import pytest
import tempfile
import os
from pathlib import Path
import app.main as main_module
from app.deduper import LeadDeduper


@pytest.fixture(autouse=True)
def isolate_test_database():
    """
    Ensures every test runs against a clean, isolated SQLite database
    so tests remain 100% deterministic and free of state pollution.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    test_deduper = LeadDeduper(db_path=temp_db_path)
    old_deduper = main_module._deduper
    main_module._deduper = test_deduper

    yield test_deduper

    # Teardown
    main_module._deduper = old_deduper
    for ext in ["", "-wal", "-shm"]:
        p = temp_db_path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
