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

## ⬆️ Como atualizar (push) o repositório no GitHub

1. Certifique-se de que o remote está configurado:
   - git remote -v

2. Comitar e enviar alterações:
   - git add .
   - git commit -m "Descrição das alterações"
   - git push origin main
   (substitua "main" pelo nome do seu branch principal, ex: master)

3. Se ainda não existir um remote GitHub:
   - gh repo create OWNER/REPO --public --source=. --remote=origin
   ou crie o repositório no GitHub e então:
   - git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git
   - git push -u origin main

4. Dicas:
   - Se pedir credenciais, use token pessoal (PAT) ou o gh CLI (gh auth login).
   - Para verificar status: git status
   - Para ver histórico: git log --oneline
