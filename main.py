import sys
import os
import time
import math
import threading
import queue
import serial
import serial.tools.list_ports
import folium
from folium import PolyLine

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QHBoxLayout, QFrame, QComboBox
)
from PySide6.QtCore import QTimer, QUrl, Qt, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QLinearGradient, QBrush, QPen
from PySide6.QtWebEngineWidgets import QWebEngineView
try:
    from PySide6.QtWebEngineCore import QWebEngineSettings
except Exception:
    QWebEngineSettings = None

import pyqtgraph as pg
from collections import deque
import csv
from pathlib import Path
import re
import json

# =========================
# Configurações
# =========================
START_LAT = -19.92
START_LON = -43.94
UPDATE_MS = 500
STOP_ALERT_SECONDS = 30
GEOFENCE_CENTER = (-19.92, -43.94)
GEOFENCE_RADIUS_M = 2000
ROUTE_MAX_POINTS = 500
SPEED_HISTORY_MAX = 600
MAP_REFRESH_MIN_S = 1.0
MAP_MIN_MOVE_M = 3.0
CSV_FLUSH_INTERVAL_S = 5.0
CSV_BUFFER_MAX = 100
LOG_ERRORS = False

APP_TITLE = "🚛 Caminhão Autônomo - Painel de Controle"
SPLASH_DEV_NAME = "Jefferson"
SPLASH_PROJECT_NAME = "Caminhão Autônomo"

_MAIN_WINDOW_REF = None

# =========================
# Utilitários
# =========================
def detectar_porta():
    portas = list(serial.tools.list_ports.comports())
    for porta in portas:
        desc = (getattr(porta, "description", "") or "").lower()
        name = (getattr(porta, "name", "") or "").lower()
        if any(k in desc for k in ["arduino", "ch340", "usb serial"]) or any(k in name for k in ["ttyusb", "com", "usb"]):
            return porta.device
    if portas:
        return getattr(portas[0], "device", None) or getattr(portas[0], "name", None)
    return None

def conectar_arduino(port_override=None):
    porta = port_override or detectar_porta()
    if porta:
        try:
            ser = serial.Serial(porta, 9600, timeout=1)
            return ser
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] abrir porta '{porta}': {e}")
            return None
    return None

def ler_linha_serial(ser):
    if ser:
        try:
            linha = ser.readline()
            if not linha:
                return None
            return linha.decode("utf-8", errors="ignore").strip()
        except Exception:
            return None
    return None

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def dentro_da_geofence(lat, lon, center=GEOFENCE_CENTER, radius_m=GEOFENCE_RADIUS_M):
    return haversine_m(lat, lon, center[0], center[1]) <= radius_m

def salvar_ponto_csv(lat, lon, t, spd, filename="route_history.csv"):
    try:
        p = Path(filename)
        first = not p.exists()
        with p.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if first:
                writer.writerow(["timestamp_iso", "timestamp_unix", "lat", "lon", "speed_kmh"])
            writer.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), f"{t:.3f}", f"{lat:.6f}", f"{lon:.6f}", f"{(spd or 0):.2f}"])
    except Exception as e:
        if LOG_ERRORS:
            print(f"[erro] salvar_ponto_csv: {e}")

# =========================
# Mapa (helpers)
# =========================
TILE_PROVIDERS = {
    "voyager": {
        "url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "attr": "&copy; <a href='https://carto.com/'>CARTO</a> &mdash; Dados: OSM"
    },
    "osm": {
        "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": "&copy; OpenStreetMap contributors"
    },
    "positron": {
        "url": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": "&copy; CARTO"
    }
}

def _asset_file_url(rel_path: str) -> str:
    abs_p = os.path.abspath(rel_path).replace("\\", "/")
    return f"file:///{abs_p}"

def gerar_mapa(lat, lon, route_points=None, center=GEOFENCE_CENTER, radius_m=GEOFENCE_RADIUS_M, theme="voyager"):
    """
    Gera um mapa HTML standalone usando Leaflet. Se assets/leaflet existir usa os arquivos locais,
    caso contrário usa CDN. Desenha geofence, marcador atual e rota (se houver).
    """
    try:
        # escolhe Leaflet (local ou CDN)
        local_js = os.path.exists(os.path.join("assets", "leaflet", "leaflet.js"))
        local_css = os.path.exists(os.path.join("assets", "leaflet", "leaflet.css"))
        if local_js and local_css:
            leaflet_js = _asset_file_url(os.path.join("assets", "leaflet", "leaflet.js"))
            leaflet_css = _asset_file_url(os.path.join("assets", "leaflet", "leaflet.css"))
        else:
            # CDN fallback (útil em ambiente com internet)
            leaflet_js = "https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"
            leaflet_css = "https://unpkg.com/leaflet@1.9.3/dist/leaflet.css"

        # provider de tiles
        prov = TILE_PROVIDERS.get(theme, TILE_PROVIDERS["voyager"])
        tile_url = prov["url"]
        tile_attr = prov.get("attr", "").replace("'", "\\'")

        # converte pontos de rota para JS
        if route_points:
            rp_js = "[" + ",".join(f"[{p[0]:.6f},{p[1]:.6f}]" for p in route_points) + "]"
        else:
            rp_js = "[]"

        # monta HTML seguro (usar {{ }} para chaves literais no f-string)
        html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<link rel="stylesheet" href="{leaflet_css}"/>
<style>html,body,#map{{height:100%;margin:0;padding:0}}</style>
</head>
<body>
<div id="map"></div>
<script src="{leaflet_js}"></script>
<script>
/* Inicializa mapa centrado nas coordenadas padrão */
var DEFAULT_LAT = {lat:.6f}, DEFAULT_LON = {lon:.6f};
var map = L.map('map').setView([DEFAULT_LAT, DEFAULT_LON], 16);
L.tileLayer('{tile_url}', {{ attribution: '{tile_attr}', maxZoom: 19 }}).addTo(map);
L.circle([{center[0]:.6f},{center[1]:.6f}], {{ radius: {int(radius_m)}, color: '#ff5c5c', weight: 2, fill: true, fillOpacity: 0.04 }}).addTo(map);
var currentMarker = L.circleMarker([{lat:.6f},{lon:.6f}], {{ radius: 8, color: '#0066cc', fill: true, fillColor: '#00aaff', fillOpacity: 0.9 }}).addTo(map);
var routeLine = null;
var route = {rp_js};
if (route.length > 1) {{
    routeLine = L.polyline(route, {{ color: 'blue', weight: 4, opacity: 0.8 }}).addTo(map);
    try {{ map.fitBounds(routeLine.getBounds(), {{padding: [50,50]}}); }} catch(e){{}}
}}

/* updateData function (used by Python runJavaScript) */
function updateData(lat, lon, route) {{
    try {{
        if (typeof lat !== 'number' || typeof lon !== 'number') return;
        if (currentMarker) currentMarker.setLatLng([lat, lon]); else currentMarker = L.circleMarker([lat, lon]).addTo(map);
        if (Array.isArray(route) && route.length > 0) {{
            if (routeLine) routeLine.setLatLngs(route);
            else routeLine = L.polyline(route, {{ color: 'blue', weight: 4, opacity: 0.8 }}).addTo(map);
            if (route.length > 1) try {{ map.fitBounds(routeLine.getBounds(), {{padding: [50,50]}}); }} catch(e){{}}
        }} else {{
            try {{ map.panTo([lat, lon]); }} catch(e){{}}
        }}
    }} catch(err) {{ console.error('updateData error', err); }}
}}
</script>
</body>
</html>"""
        with open("mapa.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        if LOG_ERRORS:
            print(f"[erro] gerar_mapa (fallback): {e}")
        try:
            with open("mapa.html", "w", encoding="utf-8") as f:
                f.write(f"<html><body><p>Erro ao gerar mapa: {e}</p></body></html>")
        except Exception:
            pass

def rewrite_map_html_offline(html_path):
    try:
        js_local_p = os.path.join("assets", "leaflet", "leaflet.js")
        css_local_p = os.path.join("assets", "leaflet", "leaflet.css")
        js_local = os.path.exists(js_local_p)
        css_local = os.path.exists(css_local_p)
        if not (js_local and css_local):
            return False
        js_file_url = _asset_file_url(js_local_p)
        css_file_url = _asset_file_url(css_local_p)
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        html = re.sub(r'https?://[^"\'>]*leaflet[^"\'>]*\.js', js_file_url, html, flags=re.IGNORECASE)
        html = re.sub(r'https?://[^"\'>]*leaflet[^"\'>]*\.css', css_file_url, html, flags=re.IGNORECASE)
        html = html.replace('assets/leaflet/leaflet.js', js_file_url)
        html = html.replace('assets/leaflet/leaflet.css', css_file_url)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        if LOG_ERRORS:
            print(f"[debug] rewrite_map_html_offline: rewrote {html_path} to use local leaflet assets")
        return True
    except Exception as e:
        if LOG_ERRORS:
            print(f"[erro] rewrite_map_html_offline: {e}")
        return False

# =========================
# Interface
# =========================
class SerialReader(threading.Thread):
    """Leitura contínua da serial em background, coloca linhas na queue."""
    def __init__(self, ser, q, stop_event):
        super().__init__(daemon=True)
        self.ser = ser
        self.q = q
        self.stop_event = stop_event

    def run(self):
        # leitura robusta da serial, checa is_open, trata exceções e evita busy-loop
        while not self.stop_event.is_set():
            try:
                if not self.ser or (hasattr(self.ser, "is_open") and not getattr(self.ser, "is_open")):
                    # porta fechada: sair
                    break
                # tenta ler linha com timeout já configurado no objeto serial
                linha = None
                try:
                    raw = self.ser.readline()
                    if raw:
                        linha = raw.decode("utf-8", errors="ignore").strip()
                except Exception as e:
                    if LOG_ERRORS:
                        print(f"[erro] leitura serial (raw): {e}")
                    linha = None

                if linha:
                    self.q.put(linha)
                else:
                    # evita tight-loop quando nada lido
                    time.sleep(0.01)
            except Exception as e:
                if LOG_ERRORS:
                    print(f"[erro] leitura serial (thread): {e}")
                time.sleep(0.1)

class MainWindow(QMainWindow):
    def _file_url_with_ts(self, rel_path: str) -> QUrl:
        """Retorna QUrl file:/// absoluto com timestamp para evitar cache do WebEngineView."""
        abs_p = os.path.abspath(rel_path).replace("\\", "/")
        return QUrl(f"file:///{abs_p}?t={int(time.time()*1000)}")

    def _update_map_dynamic(self, lat: float, lon: float):
        """
        Tenta atualizar o mapa no WebEngine via JavaScript chamando updateData(lat, lon, route).
        Retorna True se enfileirou/executou com sucesso, False caso não.
        """
        try:
            # prepara rota como lista Python
            route_list = list(self.route)
            # se página ainda não carregou, guarda o update para executar depois (fila)
            if not getattr(self, "map_ready", False):
                # cap da fila para evitar crescimento ilimitado
                try:
                    if len(self._pending_map_updates) >= 1000:
                        # descarta o mais antigo para abrir espaço
                        self._pending_map_updates.popleft()
                except Exception:
                    pass
                self._pending_map_updates.append((lat, lon, route_list))
                return False

            # serializa e chama JS
            route_json = json.dumps(route_list)
            js = f"try {{ updateData({lat:.6f}, {lon:.6f}, {route_json}); }} catch(e) {{ console.error('updateData error', e); }}"
            try:
                self.webview.page().runJavaScript(js)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _check_leaflet_assets(self):
        """Verifica se existem assets locais do Leaflet (JS/CSS)."""
        try:
            js_ok = os.path.exists(os.path.join("assets", "leaflet", "leaflet.js"))
            css_ok = os.path.exists(os.path.join("assets", "leaflet", "leaflet.css"))
            return js_ok and css_ok
        except Exception:
            return False

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1400, 800)

        # Estado
        self.arduino = None
        self.last_point = None          # (lat, lon, t)
        self.last_movement_time = time.time()
        self.route = deque(maxlen=ROUTE_MAX_POINTS)
        self.speed_history = deque(maxlen=SPEED_HISTORY_MAX)

        # Controle de atualização do mapa
        self.last_map_update = 0.0

        # serial background
        self.serial_queue = queue.Queue()
        self.serial_stop = threading.Event()
        self.serial_thread = None

        # Layout raiz
        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # Painel lateral
        side = QVBoxLayout()
        title = QLabel("📡 Dados do Caminhão")
        title.setObjectName("title")  # necessário para o seletor QLabel#title no stylesheet
        title.setStyleSheet("color: white; font-size: 18px; font-weight: 600;")

        self.status_label = QLabel("🔴 Desconectado")
        self.status_label.setStyleSheet("color: cyan; font-size: 14px;")

        self.pos_label = QLabel(f"Lat: {START_LAT:.5f} | Lon: {START_LON:.5f}")
        self.pos_label.setStyleSheet("color: #a0e7ff;")

        self.spd_label = QLabel("Velocidade: 0.0 km/h")
        self.spd_label.setStyleSheet("color: #a0ffb0;")

        self.alert_label = QLabel("Alertas: OK")
        self.alert_label.setStyleSheet("color: #ffd166;")

        # Seletor de porta serial
        ports_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setEditable(False)
        self.btn_refresh_ports = QPushButton("Atualizar portas")
        self.btn_refresh_ports.clicked.connect(self.refresh_ports)
        ports_row.addWidget(QLabel("Porta:"))
        ports_row.addWidget(self.port_combo, 1)
        ports_row.addWidget(self.btn_refresh_ports)

        self.btn_connect = QPushButton("Conectar")
        self.btn_connect.clicked.connect(self.conectar)

        self.btn_disconnect = QPushButton("Desconectar")
        self.btn_disconnect.clicked.connect(self.desconectar)

        # Simulação (novo botão)
        self.btn_simulate = QPushButton("Iniciar simulação")
        self.btn_simulate.clicked.connect(self.toggle_simulation)

        # Botão para seguir o mapa (novo)
        self.btn_follow = QPushButton("Seguir mapa: ON")
        self.btn_follow.setCheckable(True)
        self.btn_follow.setChecked(True)
        self.btn_follow.clicked.connect(self.toggle_follow)
        self.follow_map = True

        # Botão para salvar rota em CSV
        self.btn_save = QPushButton("Salvar rota: OFF")
        self.btn_save.setCheckable(True)
        self.btn_save.setChecked(False)
        self.btn_save.clicked.connect(self.toggle_save)
        self.save_route = False

        # Buffer de CSV
        self.csv_buffer = []  # lista de tuplas (lat, lon, t, spd)
        self.csv_last_flush = time.time()

        # Alternância de logs
        self.btn_logs = QPushButton("Logs: OFF")
        self.btn_logs.setCheckable(True)
        self.btn_logs.setChecked(False)
        self.btn_logs.clicked.connect(self.toggle_logs)

        # Gráfico de velocidade
        self.plot = pg.PlotWidget(background=(15, 15, 25))
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel('left', 'Velocidade', units='km/h', color='#FFFFFF')
        self.plot.setLabel('bottom', 'Tempo', units='s', color='#FFFFFF')
        self.speed_curve = self.plot.plot(pen=pg.mkPen('#00f7ff', width=2))

        # Organização painel lateral
        side.addWidget(title)
        side.addLayout(ports_row)
        side.addWidget(self.btn_connect)
        side.addWidget(self.btn_disconnect)
        side.addWidget(self.btn_simulate)  # adiciona botão de simulação
        side.addWidget(self.btn_follow)    # adiciona botão seguir mapa
        side.addWidget(self.btn_save)      # adiciona botão salvar rota
        side.addWidget(self.btn_logs)      # alternância de logs
        side.addSpacing(8)
        side.addWidget(self.status_label)
        side.addWidget(self.pos_label)
        side.addWidget(self.spd_label)
        side.addWidget(self.alert_label)
        side.addSpacing(8)
        side.addWidget(QLabel("Velocidade em tempo real"))
        side.addWidget(self.plot, stretch=1)
        side.addStretch()

        # Painel do mapa
        self.webview = QWebEngineView()

        # controle para saber quando a página do mapa está pronta para receber JS
        self.map_ready = False
        # fila de updates pendentes (lat, lon, route_list)
        self._pending_map_updates = deque()

        # configurações importantes para permitir que HTML local acesse arquivos locais/remotos
        try:
            settings = self.webview.settings()
            if QWebEngineSettings is not None:
                settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
                settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        except Exception:
            pass

        # conectar signal de carregamento para debug e para marcar page pronta
        try:
            self.webview.loadFinished.connect(self._on_map_load_finished)
        except Exception:
            pass

        # controles do mapa: tema, recarregar, abrir externo (adicionados ao painel lateral)
        self.map_theme = "voyager"
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["voyager", "positron", "osm"])
        self.btn_reload_map = QPushButton("Recarregar mapa")
        self.btn_reload_map.clicked.connect(self.reload_map)
        self.btn_open_map = QPushButton("Abrir mapa (navegador)")
        self.btn_open_map.clicked.connect(self.open_map_external)
        side.addWidget(self.theme_combo)
        side.addWidget(self.btn_reload_map)
        side.addWidget(self.btn_open_map)

        # gera e carrega mapa.html
        gerar_mapa(START_LAT, START_LON, [], theme=self.map_theme)
        try:
            rewrite_map_html_offline("mapa.html")
        except Exception:
            pass
        self.webview.load(self._file_url_with_ts("mapa.html"))

        # Layout principal
        root.addLayout(side, 2)
        # separador vertical
        sep = QFrame(); sep.setFrameShape(QFrame.VLine); sep.setStyleSheet("color: rgba(255,255,255,40);")
        root.addWidget(sep)
        root.addWidget(self.webview, 5)

        # Timer de leitura
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(UPDATE_MS)

        # Estilo vidro (melhorado)
        self.setStyleSheet("""
            QMainWindow { 
                background: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1, stop:0 rgba(18,18,24,230), stop:1 rgba(28,28,48,230));
                color: #e6f7ff;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 18);
                border: 1px solid rgba(0, 167, 255, 160);
                border-radius: 10px;
                color: #e6f7ff;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:checked {
                background-color: rgba(0, 167, 255, 140);
                color: #00121a;
            }
            QPushButton:hover { background-color: rgba(255,255,255,30); }
            QLabel#title { color: white; font-size: 18px; font-weight: 700; }
            QLabel { color: #dfefff; font-size: 13px; }
        """)

        # Dados para o gráfico
        self.t0 = time.time()

        # Estado da simulação
        self.simulating = False
        self.sim_angle = 0.0
        self.sim_center = (START_LAT, START_LON)
        self.sim_radius_m = 40.0
        self.sim_speed_target_kmh = 20.0
        self.sim_last_time = time.time()
        self.last_reconnect_attempt = 0.0

        # Inicializa lista de portas
        self.refresh_ports()

        # Estados iniciais dos botões e arquivo CSV da sessão
        self._update_buttons()
        self.csv_filename = f"route_history_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        self.last_map_pos = None
        self.offline_assets_ok = self._check_leaflet_assets()

    # ----- Conexão -----
    def conectar(self):
        # tenta usar a porta selecionada, se houver
        selected_port = None
        try:
            sel_text = self.port_combo.currentText().strip()
            if sel_text:
                # formato "COM3 - Arduino Uno" -> pega a primeira palavra como device
                selected_port = sel_text.split()[0]
        except Exception:
            selected_port = None
        self.arduino = conectar_arduino(selected_port)
        if self.arduino:
            try:
                port = getattr(self.arduino, "port", "serial")
            except Exception as e:
                if LOG_ERRORS:
                    print(f"[erro] obter porta: {e}")
                port = "serial"
            self.status_label.setText(f"✅ Conectado ({port})")
            # inicia thread de leitura se não estiver rodando
            if not self.serial_thread or not self.serial_thread.is_alive():
                self.serial_stop.clear()
                self.serial_thread = SerialReader(self.arduino, self.serial_queue, self.serial_stop)
                self.serial_thread.start()
            self._update_buttons()
        else:
            self.status_label.setText("❌ Nenhum Arduino encontrado")
            self._update_buttons()

    def desconectar(self):
        # para thread e fecha serial
        try:
            self.serial_stop.set()
            if self.serial_thread and self.serial_thread.is_alive():
                self.serial_thread.join(timeout=1.0)
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] parar thread serial: {e}")
            pass
        if self.arduino:
            try:
                self.arduino.close()
            except Exception as e:
                if LOG_ERRORS:
                    print(f"[erro] fechar serial: {e}")
                pass
            self.arduino = None
        # esvazia fila
        try:
            while not self.serial_queue.empty():
                self.serial_queue.get_nowait()
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] esvaziar fila: {e}")
            pass
        self.status_label.setText("🔴 Desconectado")
        self._update_buttons()

    # ----- Salvamento de rota -----
    def toggle_save(self):
        self.save_route = not self.save_route
        self.btn_save.setText("Salvar rota: ON" if self.save_route else "Salvar rota: OFF")
        # se ativou, salva rota já existente rapidamente
        if self.save_route and self.route:
            now = time.time()
            for idx, (lat, lon) in enumerate(self.route):
                # aproxima timestamp por deslocamento simples (não crítico)
                t = now - (len(self.route) - idx)
                # pré-preenche o buffer; flush ocorrerá pelo ciclo
                self.csv_buffer.append((lat, lon, t, None))

    def refresh_ports(self):
        self.port_combo.clear()
        try:
            items = []
            for p in serial.tools.list_ports.comports():
                dev = getattr(p, "device", None) or getattr(p, "name", "")
                desc = getattr(p, "description", "") or ""
                label = f"{dev} - {desc}".strip()
                items.append(label)
            if items:
                self.port_combo.addItems(items)
            else:
                # evita adicionar string vazia que causa parsing problemático
                self.port_combo.addItem("(nenhuma porta detectada)")
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] listar portas: {e}")
            self.port_combo.addItem("(erro listar portas)")

    def toggle_logs(self):
        global LOG_ERRORS
        LOG_ERRORS = not LOG_ERRORS
        self.btn_logs.setText("Logs: ON" if LOG_ERRORS else "Logs: OFF")

    # ----- Loop principal -----
    def tick(self):
        try:
            # lê linha da queue (preenchida pela SerialReader) — não bloqueante
            linha = None
            if self.arduino:
                try:
                    linha = self.serial_queue.get_nowait()
                except queue.Empty:
                    linha = None
            now = time.time()

            # tentativa de reconexão automática quando desconectado
            if (self.arduino is None) and (now - self.last_reconnect_attempt > 5.0) and (not self.simulating):
                self.last_reconnect_attempt = now
                try:
                    self.conectar()
                except Exception as e:
                    if LOG_ERRORS:
                        print(f"[erro] reconectar: {e}")

            if (not linha) and self.simulating:
                linha = self.gerar_leitura_simulada(now)

            lat, lon, spd = None, None, None

            if linha:
                # Formato esperado: LAT:...,LON:...,SPD:...
                if linha.startswith("LAT:") and "LON:" in linha:
                    try:
                        parts = linha.split(",")
                        lat = float(parts[0].split(":")[1])
                        lon = float(parts[1].split(":")[1])
                        if len(parts) > 2 and "SPD:" in parts[2]:
                            spd = float(parts[2].split(":")[1])
                    except Exception as e:
                        if LOG_ERRORS:
                            print(f"[erro] parse linha: '{linha}' -> {e}")

            # Se não veio velocidade, estima pela distância entre pontos
            if lat is not None and lon is not None:
                if self.last_point:
                    lat0, lon0, t0 = self.last_point
                    dt = max(1e-6, now - t0)
                    dist_m = haversine_m(lat0, lon0, lat, lon)
                    est_kmh = (dist_m / dt) * 3.6
                    if spd is None:
                        spd = est_kmh

                    # Detecta movimento
                    if dist_m > 1.5:  # moveu mais que 1.5 m entre leituras
                        self.last_movement_time = now
                else:
                    # primeiro ponto
                    self.last_movement_time = now

                self.last_point = (lat, lon, now)
                self.pos_label.setText(f"Lat: {lat:.5f} | Lon: {lon:.5f}")

                # Atualiza rota
                self.route.append((lat, lon))
                # Bufferiza CSV se habilitado
                if self.save_route:
                    self.csv_buffer.append((lat, lon, now, spd))
                self._flush_csv_if_needed(now)

                # Atualiza mapa com limitação de frequência
                if self.follow_map and self._should_refresh_map(now) and self._moved_enough(lat, lon):
                    self._refresh_map(lat, lon)

            # Velocidade e gráfico
            if spd is not None:
                self.spd_label.setText(f"Velocidade: {spd:.1f} km/h")
                t_rel = now - self.t0
                self.speed_history.append((t_rel, spd))
                xs = [p[0] for p in self.speed_history]
                ys = [p[1] for p in self.speed_history]
                self.speed_curve.setData(xs, ys)

            # Alertas
            alerts = []
            # Parado por muito tempo
            if now - self.last_movement_time > STOP_ALERT_SECONDS:
                alerts.append(f"Parado há {int(now - self.last_movement_time)}s")

            # Fora da geofence
            if self.last_point:
                if not dentro_da_geofence(self.last_point[0], self.last_point[1]):
                    alerts.append("Fora da área segura")

            # Atualiza label de alertas com cores dependendo da gravidade
            if alerts:
                color = "#ff6b6b" if any("Fora" in a for a in alerts) else ("#ffb74d" if any("Parado" in a for a in alerts) else "#ffd166")
                self.alert_label.setStyleSheet(f"color: {color}; font-weight: 700;")
                self.alert_label.setText("Alertas: " + "; ".join(alerts))
            else:
                self.alert_label.setStyleSheet("color: #8ee3a9;")
                self.alert_label.setText("Alertas: OK")
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] tick: {e}")

    def _should_refresh_map(self, now_ts):
        return (now_ts - self.last_map_update) >= MAP_REFRESH_MIN_S

    def _refresh_map(self, lat, lon):
        # tenta atualizar dinamicamente via JS (mais rápido e confiável)
        try:
            ok = self._update_map_dynamic(lat, lon)
            if ok:
                self.last_map_update = time.time()
                self.last_map_pos = (lat, lon)
                return
        except Exception:
            pass

        # fallback: regenerar HTML e recarregar (mantido para compatibilidade)
        try:
            gerar_mapa(lat, lon, list(self.route))
            try:
                self._try_make_map_offline("mapa.html")
            except Exception as e:
                if LOG_ERRORS:
                    print(f"[erro] offline map rewrite: {e}")
            self.webview.load(self._file_url_with_ts("mapa.html"))
            self.last_map_update = time.time()
            self.last_map_pos = (lat, lon)
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] refresh map: {e}")

    def reload_map(self):
        """Regenera mapa com o tema selecionado e recarrega o WebView."""
        try:
            self.map_theme = self.theme_combo.currentText() or "voyager"
            gerar_mapa(START_LAT, START_LON, list(self.route), theme=self.map_theme)
            try:
                rewrite_map_html_offline("mapa.html")
            except Exception:
                pass
            # tenta usar atualização dinâmica se disponível
            if not self._update_map_dynamic(START_LAT, START_LON):
                # força recarregamento evitando cache
                self.webview.load(self._file_url_with_ts("mapa.html"))
            try:
                self._log(f"reload_map: regenerated mapa.html with theme={self.map_theme}")
            except Exception:
                pass
        except Exception as e:
            try:
                self._log(f"reload_map error: {e}")
            except Exception:
                pass

    def open_map_external(self):
        """Abre mapa.html no navegador externo (útil para debug)."""
        try:
            import webbrowser
            webbrowser.open('file://' + os.path.abspath('mapa.html'))
            self._log("open_map_external: opened mapa.html in external browser")
        except Exception as e:
            self._log(f"open_map_external error: {e}")

    def _flush_csv_if_needed(self, now_ts):
        if not self.csv_buffer:
            return
        if (now_ts - self.csv_last_flush) >= CSV_FLUSH_INTERVAL_S or len(self.csv_buffer) >= CSV_BUFFER_MAX:
            try:
                for lat, lon, t, spd in self.csv_buffer:
                    salvar_ponto_csv(lat, lon, t, spd, filename=self.csv_filename)
            except Exception as e:
                if LOG_ERRORS:
                    print(f"[erro] flush csv: {e}")
            finally:
                self.csv_buffer.clear()
                self.csv_last_flush = now_ts

    # ----- Simulação -----
    def toggle_simulation(self):
        self.simulating = not self.simulating
        self.sim_last_time = time.time()
        self.btn_simulate.setText("Parar simulação" if self.simulating else "Iniciar simulação")
        if self.simulating:
            # reset pequeno para evitar saltos
            self.sim_angle = 0.0
        self._update_buttons()

    def gerar_leitura_simulada(self, now):
        dt = max(1e-6, now - self.sim_last_time)
        self.sim_last_time = now
        # avança ângulo (rad/s)
        self.sim_angle += 0.8 * dt

        # posição ao redor do centro (círculo pequeno)
        ang = self.sim_angle
        dx = math.cos(ang) * self.sim_radius_m
        dy = math.sin(ang) * self.sim_radius_m

        center_lat, center_lon = self.sim_center
        # conversão aproximada metros -> graus
        dlat = dy / 111320.0
        dlon = dx / (111320.0 * math.cos(math.radians(center_lat)) + 1e-12)

        lat = center_lat + dlat
        lon = center_lon + dlon

        # velocidade simulada varia com o ângulo (apenas para visualização)
        spd = self.sim_speed_target_kmh * (0.6 + 0.4 * math.sin(ang))

        return f"LAT:{lat:.6f},LON:{lon:.6f},SPD:{spd:.2f}"

    # ----- Seguir mapa -----
    def toggle_follow(self):
        self.follow_map = not self.follow_map
        self.btn_follow.setText("Seguir mapa: ON" if self.follow_map else "Seguir mapa: OFF")
        # se acabou de ativar, atualiza o mapa uma vez imediatamente
        if self.follow_map and self.last_point:
            lat, lon, _ = self.last_point
            self._refresh_map(lat, lon)

    def _update_buttons(self):
        is_connected = self.arduino is not None
        # Conexão
        try:
            self.btn_connect.setEnabled(not is_connected)
            self.btn_disconnect.setEnabled(is_connected)
            self.port_combo.setEnabled(not is_connected)
            self.btn_refresh_ports.setEnabled(not is_connected)
        except Exception:
            pass

    # ----- Encerramento -----
    def closeEvent(self, event):
        # salvar rota final se habilitado
        if self.save_route and self.route:
            now = time.time()
            for idx, (lat, lon) in enumerate(self.route):
                t = now - (len(self.route) - idx)
                # empilha restante no buffer
                self.csv_buffer.append((lat, lon, t, None))
        # flush final de CSV
        try:
            self._flush_csv_if_needed(time.time() + CSV_FLUSH_INTERVAL_S)
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] flush final csv: {e}")
        # parar thread serial e fechar porta
        try:
            self.serial_stop.set()
            if self.serial_thread and self.serial_thread.is_alive():
                self.serial_thread.join(timeout=1.0)
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] finalizar thread serial: {e}")
            pass
        if self.arduino:
            try:
                self.arduino.close()
            except Exception as e:
                if LOG_ERRORS:
                    print(f"[erro] fechar serial no closeEvent: {e}")
                pass
        super().closeEvent(event)

    # utilitário simples de log (arquivo) para debugging do mapa
    def _log(self, msg: str):
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open("map_debug.log", "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    # callback para loadFinished do WebEngineView
    def _on_map_load_finished(self, ok: bool):
        try:
            if ok:
                self.map_ready = True
                # drena e aplica todos os updates pendentes (se houver)
                try:
                    while self._pending_map_updates:
                        item = self._pending_map_updates.popleft()
                        try:
                            lat, lon, route_list = item
                            route_json = json.dumps(route_list)
                            js = f"try {{ updateData({lat:.6f}, {lon:.6f}, {route_json}); }} catch(e) {{ console.error('updateData error', e); }}"
                            try:
                                self.webview.page().runJavaScript(js)
                            except Exception:
                                # se falhar, re-enfileira e sai (será tentado no próximo load)
                                self._pending_map_updates.appendleft(item)
                                break
                        except Exception:
                            continue
                except Exception:
                    # qualquer erro ao drenar não deve travar a UI
                    pass
                # se havia um update pendente, executa agora
                # antigo campo _pending_map_update removido (usamos fila)

                self.status_label.setText("✅ Mapa carregado")
                self._log("mapa.html carregado com sucesso no QWebEngineView")
            else:
                self.map_ready = False
                self.status_label.setText("❌ Erro ao carregar mapa")
                self._log("falha ao carregar mapa.html no QWebEngineView")
                # abre no navegador externo para ajudar no debug
                try:
                    import webbrowser
                    webbrowser.open('file://' + os.path.abspath('mapa.html'))
                    self._log("mapa.html aberto no navegador externo para debug")
                except Exception as e:
                    self._log(f"erro ao abrir navegador externo: {e}")
        except Exception as e:
            if LOG_ERRORS:
                print(f"[erro] _on_map_load_finished: {e}")
            try:
                self._log(f"_on_map_load_finished exception: {e}")
            except Exception:
                pass

# =========================
# Splash Screen e execução
# =========================
class SplashScreen(QWidget):
    def __init__(self, developer_name: str, project_name: str, width: int = 720, height: int = 420):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.resize(width, height)

        self.developer_name = developer_name
        self.project_name = project_name

        self._t0 = time.time()
        self._comet_x = -120.0
        self._comet_y = height * 0.35
        self._vx = 6.0  # px/frame
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60 FPS

        # centra na tela principal
        try:
            screen = QApplication.primaryScreen().geometry()
            self.move(int((screen.width() - width) / 2), int((screen.height() - height) / 2))
        except Exception:
            pass

    def start(self, duration_ms: int, on_finished):
        QTimer.singleShot(duration_ms, lambda: self._finish(on_finished))
        self.show()

    def _finish(self, on_finished):
        try:
            self._timer.stop()
        except Exception:
            pass
        self.close()
        if callable(on_finished):
            on_finished()

    def _tick(self):
        w = self.width()
        self._comet_x += self._vx
        if self._comet_x > w + 120:
            self._comet_x = -120
        self.repaint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # fundo gradiente "noite"
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(10, 10, 24))
        grad.setColorAt(1.0, QColor(18, 18, 40))
        painter.fillRect(self.rect(), QBrush(grad))

        # estrelas simples
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 180))
        for i in range(40):
            x = (i * 97) % (self.width())
            y = (i * 61) % (self.height())
            r = 1 + (i % 3)
            painter.drawEllipse(QRectF(x, y, r, r))

        # cometa (cabeça)
        comet_color = QColor(0, 200, 255)
        painter.setBrush(comet_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(self._comet_x, self._comet_y, 16, 16))

        # cauda do cometa
        tail_len = 140
        for j in range(12):
            alpha = max(0, 180 - j * 14)
            painter.setBrush(QColor(0, 200, 255, alpha))
            painter.drawEllipse(QRectF(self._comet_x - j * (tail_len / 12.0), self._comet_y + j * 0.6, 14 - j * 0.8, 14 - j * 0.8))

        # texto do projeto e autor
        painter.setPen(QColor(230, 247, 255))
        title_font = QFont("Segoe UI", 22, QFont.Bold)
        painter.setFont(title_font)
        painter.drawText(self.rect(), Qt.AlignHCenter | Qt.AlignVCenter, self.project_name)

        painter.setPen(QColor(160, 220, 255))
        sub_font = QFont("Segoe UI", 12)
        painter.setFont(sub_font)
        painter.drawText(0, int(self.height()*0.65), self.width(), 30, Qt.AlignHCenter, f"por {self.developer_name}")


# =========================
# Ícone do aplicativo
# =========================
def create_app_icon_pixmap(size: int = 128) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    # fundo
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(0, 120, 200))
    grad.setColorAt(1.0, QColor(0, 180, 140))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, 24, 24)

    # caminhão simples
    p.setBrush(QColor(255, 255, 255))
    p.setPen(QPen(QColor(255, 255, 255), 3))
    body = QRectF(size*0.18, size*0.40, size*0.64, size*0.28)
    p.drawRoundedRect(body, 8, 8)
    cab = QRectF(size*0.58, size*0.32, size*0.20, size*0.20)
    p.drawRoundedRect(cab, 6, 6)

    # rodas
    p.setBrush(QColor(30, 30, 30))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(size*0.28, size*0.64, size*0.16, size*0.16))
    p.drawEllipse(QRectF(size*0.56, size*0.64, size*0.16, size*0.16))

    # detalhe luz
    p.setBrush(QColor(255, 230, 120))
    p.drawEllipse(QRectF(size*0.76, size*0.44, size*0.06, size*0.06))

    p.end()
    return pm
#
# =========================
# Execução
# =========================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Define ícone do app
    try:
        icon_pm = create_app_icon_pixmap(256)
        app.setWindowIcon(QIcon(icon_pm))
    except Exception as e:
        if LOG_ERRORS:
            print(f"[erro] criar ícone: {e}")

    # Splash com cometa
    splash = SplashScreen(SPLASH_DEV_NAME, SPLASH_PROJECT_NAME, 720, 420)
    # Acelera o pyqtgraph com antialias
    pg.setConfigOptions(antialias=True)
    def _after_splash():
        global _MAIN_WINDOW_REF
        win = MainWindow()
        try:
            win.setWindowIcon(QIcon(icon_pm))
        except Exception:
            pass
        win.show()
        _MAIN_WINDOW_REF = win
    splash.start(2000, _after_splash)
    sys.exit(app.exec())
