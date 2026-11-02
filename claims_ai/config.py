"""Configuración compartida: variables de entorno, rutas del proyecto y modelo de Claude."""
import os

from dotenv import load_dotenv

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(RAIZ_PROYECTO, ".env"))

# Catálogo desacoplado de modalidades de fraude
MANUAL_PATH = os.path.join(RAIZ_PROYECTO, "data", "manual_modalidades.json")

# Modelo de Claude usado por los agentes
MODELO_CLAUDE = "claude-sonnet-4-6"

# Carpeta con los archivos estáticos del portal (vista web)
WEB_DIR = os.path.join(RAIZ_PROYECTO, "claims_ai", "views", "web")
