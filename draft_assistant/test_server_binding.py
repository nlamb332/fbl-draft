"""Local server listener ownership checks."""

from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import patch

from .coordinator import Coordinator
from .server import create_server, serve


class ServerBindingTests(unittest.TestCase):
    def test_browser_opens_only_after_the_port_is_listening(self) -> None:
        coordinator = Coordinator(":memory:", recover=False)
        server = create_server(coordinator, port=0)

        def open_panel(url: str) -> bool:
            self.assertEqual(url, f"http://127.0.0.1:{server.server_port}/")
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=1):
                pass
            return True

        try:
            with patch("draft_assistant.server.create_server", return_value=server), patch.object(server, "serve_forever"), patch("webbrowser.open", side_effect=open_panel) as browser:
                serve(coordinator, open_browser=True)
                browser.assert_called_once()
        finally:
            server.server_close()
            coordinator.close()

    def test_bind_failure_does_not_open_a_broken_browser_page(self) -> None:
        coordinator = Coordinator(":memory:", recover=False)
        try:
            with patch("draft_assistant.server.create_server", side_effect=OSError("occupied")), patch("webbrowser.open") as browser:
                with self.assertRaises(OSError):
                    serve(coordinator, open_browser=True)
                browser.assert_not_called()
        finally:
            coordinator.close()

    @unittest.skipUnless(os.name == "nt", "SO_EXCLUSIVEADDRUSE is Windows-specific")
    def test_second_server_cannot_bind_an_active_local_port(self) -> None:
        coordinator = Coordinator(":memory:", recover=False)
        first = create_server(coordinator, port=0)
        try:
            with self.assertRaises(OSError):
                create_server(coordinator, port=first.server_port)
        finally:
            first.server_close()
            coordinator.close()


if __name__ == "__main__":
    unittest.main()
