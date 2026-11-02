"""
Agente 2 - Pattern Investigator (punto de entrada).

La lógica está organizada en MVC dentro de claims_ai/:
  modelo       claims_ai/models/patron_model.py, claims_ai/services/pattern_investigator.py
  vista        claims_ai/views/consola_agente2.py
  controlador  claims_ai/controllers/agente2_controller.py

Uso:
    source .venv/bin/activate
    python agente2_patrones.py             # Ejecuta el pipeline completo de clustering y síntesis
    python agente2_patrones.py --ver       # Visualiza los patrones vigentes y sus hipótesis
"""
from claims_ai.controllers.agente2_controller import main

if __name__ == "__main__":
    main()
