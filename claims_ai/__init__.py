"""
Claims AI – MVP de agentes de fraude, organizado en Modelo-Vista-Controlador.

  models/       Modelo: acceso a datos (PostgreSQL, Manual de Modalidades) y reglas del dominio.
  services/     Modelo: lógica de negocio de cada agente (análisis, clustering, llamadas a Claude).
  views/        Vista: presentación en consola y portal web (HTML/JS/CSS).
  controllers/  Controlador: orquesta cada flujo (argumentos, orden de pasos, rutas HTTP).

Los puntos de entrada de siempre siguen funcionando:
  python agente1_tipificador.py   python agente2_patrones.py   python portal/server.py
"""
