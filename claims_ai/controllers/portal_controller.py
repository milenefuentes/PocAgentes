"""
Controlador del portal web: rutas HTTP.
  GET  /api/data     -> datos del dashboard (modelo) en JSON
  GET  /api/casos    -> casos analizados por el Agente 1
  GET  /api/caso?call_id=N -> entradas y salidas del Agente 1 para una llamada
  GET  /api/export?tipo=casos|patrones|decisiones -> archivo CSV para Excel
  GET  / , /app.js, /style.css -> archivos de la vista web
  POST /api/validar  -> registra la decisión del analista sobre una hipótesis (modelo)
  POST /api/caso/revisar -> decisión del analista sobre la ficha de un caso
  GET  /api/patron?patron_id=N -> corrida anterior del patrón e historial de decisiones
  GET  /api/configuracion -> parámetros de negocio, catálogo de modalidades y últimas corridas
  POST /api/parametros    -> cambia parámetros de negocio del Agente 2 (aplican en la próxima corrida)
"""
import csv
import http.server
import io
import json
import os
import socketserver
import urllib.parse
from datetime import date, datetime
from decimal import Decimal

from claims_ai.config import WEB_DIR
from claims_ai.models import dashboard_model

PORT = 8080

ARCHIVOS_ESTATICOS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


def json_serial(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


class PortalRequestHandler(http.server.SimpleHTTPRequestHandler):
    def _responder_json(self, status: int, contenido):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(contenido, default=json_serial, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            data = dashboard_model.obtener_datos_dashboard()
            self.wfile.write(json.dumps(data, default=json_serial, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/patron":
            try:
                patron_id = int(urllib.parse.parse_qs(parsed_url.query).get("patron_id", [""])[0])
            except ValueError:
                self._responder_json(400, {"error": "patron_id debe ser un número"})
                return
            detalle = dashboard_model.detalle_patron(patron_id)
            if detalle is None:
                self._responder_json(404, {"error": f"No existe el patrón {patron_id}"})
            else:
                self._responder_json(200, detalle)
            return

        if path == "/api/configuracion":
            self._responder_json(200, dashboard_model.obtener_configuracion())
            return

        if path == "/api/casos":
            self._responder_json(200, dashboard_model.listar_casos())
            return

        if path == "/api/caso":
            params = urllib.parse.parse_qs(parsed_url.query)
            try:
                call_id = int(params.get("call_id", [""])[0])
            except ValueError:
                self._responder_json(400, {"error": "call_id debe ser un número"})
                return
            detalle = dashboard_model.detalle_caso(call_id)
            if detalle is None:
                self._responder_json(404, {"error": f"La llamada {call_id} no fue analizada"})
            else:
                self._responder_json(200, detalle)
            return

        if path == "/api/export":
            params = urllib.parse.parse_qs(parsed_url.query)
            tipo = params.get("tipo", [""])[0]
            try:
                patron = params.get("patron_id", [""])[0]
                nombre, encabezados, filas = dashboard_model.exportar(tipo, int(patron) if patron else None)
            except ValueError as e:
                self._responder_json(400, {"error": str(e)})
                return
            buffer = io.StringIO()
            escritor = csv.writer(buffer)
            escritor.writerow(encabezados)
            escritor.writerows([[json_serial(v) if isinstance(v, (Decimal, date, datetime)) else v for v in fila]
                                for fila in filas])
            contenido = ("\ufeff" + buffer.getvalue()).encode("utf-8")   # BOM: Excel detecta UTF-8 (tildes)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{nombre}"')
            self.send_header("Content-Length", str(len(contenido)))
            self.end_headers()
            self.wfile.write(contenido)
            return

        if path in ARCHIVOS_ESTATICOS:
            archivo, tipo = ARCHIVOS_ESTATICOS[path]
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.end_headers()
            with open(os.path.join(WEB_DIR, archivo), "rb") as f:
                self.wfile.write(f.read())
            return

        super().do_GET()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path == "/api/parametros":
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                cambios = payload.get("cambios")
                if not isinstance(cambios, dict) or not cambios:
                    raise ValueError("No hay cambios que guardar")
                dashboard_model.guardar_parametros(
                    cambios, payload.get("analista") or dashboard_model.ANALISTA_DEFAULT)
                self._responder_json(200, {"success": True})
            except (TypeError, ValueError) as e:
                self._responder_json(400, {"error": str(e)})
            except Exception as e:
                self._responder_json(500, {"error": str(e)})
            return

        if parsed_url.path == "/api/caso/revisar":
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                resultado = dashboard_model.guardar_revision_caso(
                    int(payload.get("call_id")), payload.get("accion"), payload.get("comentario", ""),
                    payload.get("analista") or dashboard_model.ANALISTA_DEFAULT, payload.get("tipologia"))
                self._responder_json(200, {"success": True, **resultado})
            except (TypeError, ValueError) as e:
                self._responder_json(400, {"error": str(e)})
            except Exception as e:
                self._responder_json(500, {"error": str(e)})
            return
        if parsed_url.path == "/api/validar":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(body)
                hipotesis_id = int(payload.get("hipotesis_id"))
                estado = payload.get("estado", "Validado")
                comentario = payload.get("comentario", "")
                analista = payload.get("analista") or dashboard_model.ANALISTA_DEFAULT

                dashboard_model.guardar_validacion(hipotesis_id, estado, comentario, analista)
                status, respuesta = 200, {"success": True}
            except (TypeError, ValueError) as e:
                status, respuesta = 400, {"error": str(e)}
            except Exception as e:
                status, respuesta = 500, {"error": str(e)}

            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(respuesta, ensure_ascii=False).encode("utf-8"))
            return


class PortalServer(socketserver.TCPServer):
    # Permite reiniciar el portal de inmediato tras cerrarlo (sin esperar el TIME_WAIT del puerto)
    allow_reuse_address = True


def run_server(port: int = PORT):
    os.chdir(WEB_DIR)
    with PortalServer(("", port), PortalRequestHandler) as httpd:
        print("===============================================================")
        print(f"  PORTAL WEB MVP CLAIMS AI - SERVIDOR INICIADO EN PUERTO {port} ")
        print(f"  URL Local: http://localhost:{port}")
        print("===============================================================")
        httpd.serve_forever()
