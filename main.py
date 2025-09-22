import sys, os, time, math
import threading, queue
import serial
import serial.tools.list_ports
import folium
from folium import PolyLine

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QHBoxLayout, QFrame
)
from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView

import pyqtgraph as pg
from collections import deque
import csv
from pathlib import Path

# =========================
# Configurações #essa lisa e muito linda 
# =========================
START_LAT = -19.92
START_LON = -43.94
UPDATE_MS = 500
STOP_ALERT_SECONDS = 30            # alerta se ficar parado por mais de 30s
GEOFENCE_CENTER = (-19.92, -43.94) # centro da cerca (BH como exemplo)
GEOFENCE_RADIUS_M = 2000           # raio da cerca em metros (2 km)
ROUTE_MAX_POINTS = 500             # limitar tamanho da rota no mapa
SPEED_HISTORY_MAX = 600            # ~5min em 0.5s

# =========================
# Utilitários
# =========================
def detectar_porta():
    portas = serial.tools.list_ports.comports()
    for porta in portas:
        desc = (getattr(porta, "description", "") or "").lower()
        name = (getattr(porta, "name", "") or "").lower()
        # aceita descrições e nomes comuns de portas USB/Arduino
        if any(k in desc for k in ["arduino", "ch340", "usb serial"]) or any(k in name for k in ["ttyusb", "com", "usb"]):
            return porta.device
    return None

def conectar_arduino():
    porta = detectar_porta()
    if porta:
        try:
            ser = serial.Serial(porta, 9600, timeout=1)
            return ser
        except Exception as e:
            return None
    return None

def ler_linha_serial(ser):
    if ser:
        try:
            # usar readline mesmo que in_waiting seja 0 (drivers/timeout podem variar)
            linha = ser.readline()
            if not linha:
                return None
            return linha.decode("utf-8", errors="ignore").strip()
        except:
            return None
    return None

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1 = math.radians(lat1); phi2 = math.radians(lat2)
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
    except Exception:
        # falha ao salvar não deve quebrar o app
        pass

# =========================
# Mapa (Folium)
# =========================
def gerar_mapa(lat, lon, route_points=None, center=GEOFENCE_CENTER, radius_m=GEOFENCE_RADIUS_M):
    m = folium.Map(location=[lat, lon], zoom_start=16, tiles="CartoDB positron")
    # Geofence (círculo leve)
    folium.Circle(
        location=[center[0], center[1]],
        radius=radius_m,
        color="#ff5c5c",
        weight=2,
        fill=True,
        fill_opacity=0.04,
        tooltip="Geofence"
    ).add_to(m)

    # Marcador da posição atual mais visível
    folium.CircleMarker(
        location=[lat, lon],
        radius=8,
        color="#0066cc",
        fill=True,
        fill_color="#00aaff",
        fill_opacity=0.9,
        tooltip=f"Lat:{lat:.5f} Lon:{lon:.5f}"
    ).add_to(m)

    if route_points and len(route_points) > 1:
        # Desenha a rota (polilinha azul)
        PolyLine(route_points, color="blue", weight=4, opacity=0.8).add_to(m)

    m.save("mapa.html")

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
        while not self.stop_event.is_set():
            try:
                linha = self.ser.readline()
                if not linha:
                    continue
                try:
                    s = linha.decode("utf-8", errors="ignore").strip()
                except:
                    s = None
                if s:
                    self.q.put(s)
            except Exception:
                # em caso de erro com serial, evita tight-loop
                time.sleep(0.1)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🚛 Caminhão Autônomo - Painel de Controle")
        self.resize(1400, 800)

        # Estado
        self.arduino = None
        self.last_point = None          # (lat, lon, t)
        self.last_movement_time = time.time()
        self.route = deque(maxlen=ROUTE_MAX_POINTS)
        self.speed_history = deque(maxlen=SPEED_HISTORY_MAX)

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

        btn_connect = QPushButton("Conectar")
        btn_connect.clicked.connect(self.conectar)

        btn_disconnect = QPushButton("Desconectar")
        btn_disconnect.clicked.connect(self.desconectar)

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

        # Gráfico de velocidade
        self.plot = pg.PlotWidget(background=(15, 15, 25))
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel('left', 'Velocidade', units='km/h', color='#FFFFFF')
        self.plot.setLabel('bottom', 'Tempo', units='s', color='#FFFFFF')
        self.speed_curve = self.plot.plot(pen=pg.mkPen('#00f7ff', width=2))

        # Organização painel lateral
        side.addWidget(title)
        side.addWidget(btn_connect)
        side.addWidget(btn_disconnect)
        side.addWidget(self.btn_simulate)  # adiciona botão de simulação
        side.addWidget(self.btn_follow)    # adiciona botão seguir mapa
        side.addWidget(self.btn_save)      # adiciona botão salvar rota
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
        gerar_mapa(START_LAT, START_LON, [])
        self.webview.load(QUrl.fromLocalFile(os.path.abspath("mapa.html")))

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

    # ----- Conexão -----
    def conectar(self):
        self.arduino = conectar_arduino()
        if self.arduino:
            try:
                port = getattr(self.arduino, "port", "serial")
            except:
                port = "serial"
            self.status_label.setText(f"✅ Conectado ({port})")
            # inicia thread de leitura se não estiver rodando
            if not self.serial_thread or not self.serial_thread.is_alive():
                self.serial_stop.clear()
                self.serial_thread = SerialReader(self.arduino, self.serial_queue, self.serial_stop)
                self.serial_thread.start()
        else:
            self.status_label.setText("❌ Nenhum Arduino encontrado")

    def desconectar(self):
        # para thread e fecha serial
        try:
            self.serial_stop.set()
            if self.serial_thread and self.serial_thread.is_alive():
                self.serial_thread.join(timeout=1.0)
        except:
            pass
        if self.arduino:
            try:
                self.arduino.close()
            except:
                pass
            self.arduino = None
        # esvazia fila
        try:
            while not self.serial_queue.empty():
                self.serial_queue.get_nowait()
        except:
            pass
        self.status_label.setText("🔴 Desconectado")

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
                salvar_ponto_csv(lat, lon, t, None)

    # ----- Loop principal -----
    def tick(self):
        # lê linha da queue (preenchida pela SerialReader) — não bloqueante
        linha = None
        if self.arduino:
            try:
                linha = self.serial_queue.get_nowait()
            except queue.Empty:
                linha = None
        now = time.time()
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
                except:
                    pass

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
            # salva imediatamente se habilitado
            if self.save_route:
                try:
                    salvar_ponto_csv(lat, lon, now, spd)
                except:
                    pass
            # Só recarrega o mapa se o usuário estiver no modo "seguir"
            if self.follow_map:
                gerar_mapa(lat, lon, list(self.route))
                self.webview.load(QUrl.fromLocalFile(os.path.abspath("mapa.html")))

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
            # cor vermelha se contém "Fora", laranja se só parado, caso contrário amarelo
            color = "#ff6b6b" if any("Fora" in a for a in alerts) else ("#ffb74d" if any("Parado" in a for a in alerts) else "#ffd166")
            self.alert_label.setStyleSheet(f"color: {color}; font-weight: 700;")
            self.alert_label.setText("Alertas: " + "; ".join(alerts))
        else:
            self.alert_label.setStyleSheet("color: #8ee3a9;")
            self.alert_label.setText("Alertas: OK")

    # ----- Simulação -----
    def toggle_simulation(self):
        self.simulating = not self.simulating
        self.sim_last_time = time.time()
        self.btn_simulate.setText("Parar simulação" if self.simulating else "Iniciar simulação")
        if self.simulating:
            # reset pequeno para evitar saltos
            self.sim_angle = 0.0

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

    # ----- Encerramento -----
    def closeEvent(self, event):
        # salvar rota final se habilitado
        if self.save_route and self.route:
            now = time.time()
            for idx, (lat, lon) in enumerate(self.route):
                t = now - (len(self.route) - idx)
                salvar_ponto_csv(lat, lon, t, None)
        # parar thread serial e fechar porta
        try:
            self.serial_stop.set()
            if self.serial_thread and self.serial_thread.is_alive():
                self.serial_thread.join(timeout=1.0)
        except:
            pass
        if self.arduino:
            try:
                self.arduino.close()
            except:
                pass
        super().closeEvent(event)
#
# =========================
# Execução
# =========================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Acelera o pyqtgraph com antialias
    pg.setConfigOptions(antialias=True)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
