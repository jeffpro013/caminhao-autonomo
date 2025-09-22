# 🚛 Caminhão Autônomo

Sistema de monitoramento em tempo real para caminhões, integrando **Arduino + GPS** com uma interface moderna em **Python (PySide6)**.  
O projeto foi pensado para rastrear rotas, exibir velocidade, gerar alertas de segurança e oferecer uma visão completa da operação do veículo.

---

## ✨ Funcionalidades
- 📍 **Mapa interativo** com rota em tempo real (Folium + Leaflet.js)
- 📊 **Gráfico dinâmico** de velocidade (PyQtGraph)
- ⚠️ **Alertas automáticos**:
  - Caminhão parado por muito tempo
  - Saída da área segura (geofence)
- 🔌 **Integração com Arduino** via porta serial (PySerial)
- 💾 **Histórico de viagens** (opcional em CSV/SQLite)
- 🖥️ **Interface moderna** com PySide6 (Qt for Python)

---

## 🛠️ Tecnologias utilizadas
- **Python 3.11+**
- **PySide6** → Interface gráfica
- **PySerial** → Comunicação com Arduino
- **Folium** → Mapas interativos
- **PyQtGraph** → Gráficos em tempo real
- **Arduino (C++)** → Captura de dados GPS

---

## 📦 Instalação

Clone o repositório:
```bash
git clone https://github.com/jeffpro013/caminhao-autonomo.git
cd caminhao-autonomo
```

## 💾 Salvando rota automaticamente
O programa pode salvar pontos da rota em um arquivo CSV chamado "route_history.csv". Ative o salvamento pela interface (botão "Salvar rota") para registrar latitude, longitude, timestamp e velocidade para análises posteriores.
