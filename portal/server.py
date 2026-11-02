"""
Portal web (punto de entrada).

La lógica está organizada en MVC dentro de claims_ai/:
  modelo       claims_ai/models/dashboard_model.py
  vista        claims_ai/views/web/ (index.html, app.js, style.css)
  controlador  claims_ai/controllers/portal_controller.py

Uso:
    python portal/server.py        # http://localhost:8080
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claims_ai.controllers import portal_controller  # noqa: E402

PORT = portal_controller.PORT


def run_server():
    portal_controller.run_server(PORT)


if __name__ == "__main__":
    run_server()
