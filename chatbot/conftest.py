import sys
import threading
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from tests.helpers import Handler


@pytest.fixture()
def fixture_site():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    Handler.base = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield Handler.base
    server.shutdown()
