"""
Agente 1 - Fraud Analyst (punto de entrada).

La lógica está organizada en MVC dentro de claims_ai/:
  modelo       claims_ai/models/caso_model.py, claims_ai/services/fraud_analyst.py
  vista        claims_ai/views/consola_agente1.py
  controlador  claims_ai/controllers/agente1_controller.py

Uso:
    source .venv/bin/activate
    python agente1_tipificador.py --limite 5          # Procesa 5 casos concurrentemente
    python agente1_tipificador.py --todas             # Procesa todas las pendientes
    python agente1_tipificador.py --evaluar           # Muestra métricas, tags y reglas sin llamar al LLM
"""
from claims_ai.controllers.agente1_controller import main

if __name__ == "__main__":
    main()
