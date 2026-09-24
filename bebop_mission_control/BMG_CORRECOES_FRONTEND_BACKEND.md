# Especificação Técnica e Guia de Implementação: BMG Mission Control

> **Projeto:** Tech for Humans — Bebop Mission Control (BMG)  
> **Caminho da Aplicação Electron/React:** `/home/joaomoreira/ros2_ws/bebop_mission_control`  
> **Caminho dos Pacotes ROS 2:** `/home/joaomoreira/ros2_ws/src/`  
> **Versão:** 5.0 (Correções Críticas de Streaming, Telemetria, Copiloto, Mapa e Modo Bancada)

---

## 1. Problema 1: Transmissão de Vídeo (Câmera do Bebop Não Exibe Imagem)

### Diagnóstico de Causa-Raiz
1. **Pipeline de Câmera do Bebop (RTP/H.264 via UDP):**
   - O nó `ros2_bebop_driver` (`src/ros2_bebop_driver/src/bebop_driver_node.cpp`) recebe vídeo da câmera frontal do Bebop 2 via RTP/UDP na porta `55004` (ou `5004`).
   - Se o firewall do Linux (`ufw`) bloquear portas UDP ou se a rota padrão não souber alcançar `192.168.42.1` na interface Wi-Fi, `bebop->getFrontCameraFrame()` não recebe pacotes e nenhuma imagem é publicada no tópico `/bebop/camera/image_raw`.
2. **QoS do Tópico `/bebop/camera/image_raw`:**
   - O driver publica em `camera/image_raw` com perfil `sensor_data` (`ReliabilityPolicy.BEST_EFFORT`, `depth=1`).
   - O servidor `streamer/mjpeg_server.py` precisa estar inscrito com perfil idêntico e converter para MJPEG na porta 9090 (`http://127.0.0.1:9090/stream`).
3. **Decodificação de Imagem (`cv_bridge`):**
   - Em certas versões de NumPy e OpenCV, `imgmsg_to_cv2(msg, desired_encoding="bgr8")` pode lançar exceção se o cabeçalho de encoding for diferente ou ausente, fazendo com que o `mjpeg_server.py` descarte os frames.

### Arquivos Alvo
- `streamer/mjpeg_server.py`
- `electron/main.cjs`
- `src/components/cockpit/OpticalFeed.tsx`

### Instruções de Implementação
1. **Em `streamer/mjpeg_server.py`:**
   - Adicione tratamento de fallback para decodificação caso `cv_bridge` lance erro:
     ```python
     try:
         frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
     except Exception as error:
         # Fallback para decodificação de buffer raw ou conversão direta de array
         try:
             np_arr = np.frombuffer(msg.data, dtype=np.uint8)
             if len(np_arr) == msg.height * msg.step:
                 frame = np_arr.reshape((msg.height, msg.width, 3))
             else:
                 frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
         except Exception:
             frame = None
     ```
   - Emita log explícito no console ao receber o primeiro frame válido de `/bebop/camera/image_raw` ou `/bebop/camera/detections`.
2. **Em `electron/main.cjs` (`startDriverProcess`):**
   - Garanta que a rota IP do Bebop seja configurada caso esteja ausente:
     `execSync('ip route add 192.168.42.1 dev $(iwgetid -r 2>/dev/null || echo wlan0) 2>/dev/null || true')`
   - Monitore a saúde do `mjpeg_server.py` e garanta que o processo seja reiniciado automaticamente se cair.

---

## 2. Problema 2: Telemetria Congelada Quando o Drone é Desconectado

### Diagnóstico de Causa-Raiz
1. **Falta de Watchdog no Processo Principal (`main.cjs`):**
   - A função `ingestTelemetryLine()` em `main.cjs` faz merge de estado: `latestTelemetry = { ...latestTelemetry, ...parsed };`.
   - Se o drone desligar ou o driver morrer, o `telemetry_bridge.py` para de publicar linhas. Como não há watchdog periódico emitindo evento de desconexão, `latestTelemetry` permanece com os últimos valores congelados (ex: bateria em 94%, altitude em 1.2m, SSID antigo).
2. **Valores Residuais em `streamer/telemetry_bridge.py`:**
   - Quando `connected` cai para `False`, `to_payload()` continuava enviando `self.battery_pct` e `self.speed` que haviam sido armazenados na memória.

### Arquivos Alvo
- `electron/main.cjs`
- `streamer/telemetry_bridge.py`
- `src/hooks/useTelemetry.ts`
- `src/components/shell/StatusBar.tsx`

### Instruções de Implementação
1. **Em `electron/main.cjs`:**
   - Adicione um watchdog periódico (`setInterval` de 1000 ms):
     ```javascript
     setInterval(() => {
       const isStale = !latestTelemetryAt || (Date.now() - latestTelemetryAt > 3000);
       if (isStale && latestTelemetry.connected) {
         latestTelemetry = {
           ...latestTelemetry,
           connected: false,
           driver_running: false,
           battery_known: false,
           battery_pct: 0,
           wifi_ssid: '',
           wifi_signal_dbm: -100,
           speed: 0.0,
           altitude: 0.0,
           flight_time_sec: 0,
           flying_state: null,
           flying_state_label: 'disconnected',
           gps_fix: false,
         };
         send('bmg:telemetry-update', latestTelemetry);
       }
     }, 1000);
     ```
2. **Em `streamer/telemetry_bridge.py` (`to_payload`):**
   - Se `not self.connected`: force estritamente `battery_known = False`, `battery_pct = 0`, `speed = 0.0`, `altitude = 0.0`, `wifi_ssid = ""`, `wifi_signal_dbm = -100`.
3. **Em `src/hooks/useTelemetry.ts` e `StatusBar.tsx`:**
   - Se `!telemetry.connected`: exiba estritamente `—` para bateria, texto em cor âmbar `Desconectado` e `0` barras de sinal Wi-Fi.

---

## 3. Problema 3: Controle de Volume da Voz do Copiloto Não Funciona

### Diagnóstico de Causa-Raiz
1. **Dessincronização de Ciclo de Vida do Daemon de Áudio:**
   - No `electron/main.cjs`, `pushVoiceLevel()` executa `if (!speechProcess) return false;`. Como o processo `announcer.py` é iniciado sob demanda apenas quando a missão é disparada, qualquer alteração de volume realizada antes na tela inicial é ignorada.
   - Quando o `speechProcess` finalmente é instanciado em `ensureSpeechProcess()`, o volume armazenado (`voiceVolume` e `voiceMuted`) **não era enviado** para o processo recém-criado.
2. **Ausência do Controle de Volume na Cabine:**
   - Em `src/components/cockpit/CockpitScreen.tsx` (e `StageBar.tsx`), o componente de controle de voz não está presente. Durante o voo, o operador não consegue alterar o volume nem mutar o copiloto.
3. **UX do Botão em `StatusBar.tsx`:**
   - O clique no ícone de volume acionava diretamente o mute (`onToggleMute`) enquanto o slider só aparecia com hover de mouse (`pointerEnter`), impedindo o controle direto em cliques ou telas touch.

### Arquivos Alvo
- `electron/main.cjs`
- `src/components/shell/StatusBar.tsx`
- `src/components/cockpit/CockpitScreen.tsx`
- `src/components/cockpit/StageBar.tsx`
- `src/mvp_mission_bebop/mvp_mission_bebop/telemetry/announcer.py`

### Instruções de Implementação
1. **Em `electron/main.cjs`:**
   - No `ensureSpeechProcess()`, logo após iniciar o processo filho, chame `pushVoiceLevel()` no handshake `"ready"`.
   - Certifique-se de enviar o comando via stdin:
     ```javascript
     speechProcess.stdin.write(
       JSON.stringify({ op: 'level', volume: voiceVolume, muted: voiceMuted }) + '\n'
     );
     ```
2. **Em `src/components/shell/StatusBar.tsx` (`VoiceControl`):**
   - Permita alternar a abertura do slider ao clicar no ícone (`setOpen((v) => !v)`).
   - Mantenha o slider de 0% a 100% responsivo e chame `onVolume(val)`.
3. **Em `src/components/cockpit/StageBar.tsx` e `CockpitScreen.tsx`:**
   - Adicione o `<VoiceControl />` no cabeçalho da Cabine (por exemplo, na barra superior ao lado dos estágios), permitindo ao operador controlar o volume durante o voo.

---

## 4. Problema 4: Mapa Tático Não Funciona (Instruções e Correções)

### Diagnóstico de Causa-Raiz
1. **Falta de Acesso à Internet no Wi-Fi do Bebop:**
   - O drone Parrot Bebop 2 fornece uma rede Wi-Fi local sem acesso à internet (`192.168.42.1`).
   - Se o computador estiver usando apenas a placa Wi-Fi conectada ao drone, o navegador/Electron não consegue baixar os tiles do Mapbox, MapTiler ou OpenStreetMap (erros de DNS e timeout).
2. **Rate Limit de Geolocalização:**
   - O serviço `ipapi.co` possui limite de requisições rígido (erro 429), fazendo a localização aproximada falhar quando o GPS do drone não tem fix.

### Arquivos Alvo
- `src/components/cockpit/TacticalMap.tsx`
- `electron/main.cjs`
- `bebop_mission_control/.env`

### Instruções de Implementação e Operação
1. **Em `src/components/cockpit/TacticalMap.tsx`:**
   - Mantenha o suporte a `VITE_MAP_API_KEY` (MapTiler Dataviz Dark) e `VITE_MAPBOX_TOKEN` (Mapbox Dark).
   - Se os tiles online não carregarem (offline ou sem internet), exiba a **Grade Tática Local** com grade milimétrica, ponto de lançamento (RTL), anel de dispersão, escala métrica e posição relativa do drone em tempo real via odometria.
   - Caso as coordenadas do GPS sejam `(0, 0)`, use o centro operacional padrão `-19.8703, -43.9678`.
2. **Em `electron/main.cjs`:**
   - Mantenha `ipwho.is` como provedor primário de geolocalização com fallback para evitar erros de rate limit.
3. **Como Conectar e Ter Mapa Funcional (Instruções Operacionais):**
   - **Passo 1 (Chave de API):** Crie uma conta gratuita em [MapTiler Cloud](https://cloud.maptiler.com/) e adicione sua chave no arquivo `bebop_mission_control/.env`:
     ```env
     VITE_MAP_API_KEY=sua_chave_maptiler_aqui
     ```
   - **Passo 2 (Rede Dupla / Acesso Simultâneo à Internet):**
     * Conecte o Wi-Fi do computador ao Bebop 2 (`Bebop2-xxxxxx`).
     * Conecte um cabo Ethernet (ou tethering USB do celular) ao computador para manter internet ativa enquanto controla o drone pelo Wi-Fi. Dessa forma, os tiles de alta resolução carregarão 100% em tempo real.

---

## 5. Problema 5: Modo Bancada Deve Permitir Selecionar Qualquer Estágio Livremente

### Diagnóstico de Causa-Raiz
- No fluxo anterior, ativar o modo bancada e clicar em "Iniciar" rodava o pipeline linear inteiro (`mission.py --no-fly`), obrigando a execução sequencial do estágio 1 ao 5.
- O objetivo do modo bancada é funcionar como uma **bancada de testes interativa**: o operador deve poder escolher qual rotina executar individualmente (ex.: somente testar detecção de sinistro YOLO, somente testar leitura de ArUco, ou somente ajustar a inclinação da câmera) a qualquer momento, sem ser forçado a seguir uma sequência linear.

### Arquivos Alvo
- `src/components/cockpit/StageBar.tsx`
- `src/components/cockpit/OpticalFeed.tsx`
- `src/App.tsx`
- `electron/main.cjs`

### Instruções de Implementação
1. **Em `src/components/cockpit/StageBar.tsx`:**
   - Quando `benchMode === true`, todos os botões de estágio (`1: Calibração`, `2: Varredura`, `3: Tracking IBVS`, `4: Inspeção Nadir`, `5: RTL & Pouso`) devem estar interativos e destacados como opções executáveis.
   - Clicar em qualquer estágio deve chamar `onRunStage(step)`, enviando o comando para executar isoladamente aquela rotina com `--stages <step> --no-fly`.
2. **Em `src/App.tsx`:**
   - Ao concluir a rotina de bancada selecionada, o sistema **não** deve fechar o cockpit nem resetar a missão. O estado deve retornar para pronto na bancada, permitindo ao operador clicar em outro estágio imediatamente.
3. **Em `src/components/cockpit/OpticalFeed.tsx`:**
   - A barra vertical de inclinação da câmera (`cameraTilt`) deve responder em tempo real tanto aos movimentos automatizados da rotina quanto ao ajuste manual contínuo pelo operador.

---

## 6. Verificação e Compilação

Após realizar as alterações, execute:
```bash
cd /home/joaomoreira/ros2_ws/bebop_mission_control
npm run build
```
Certifique-se de que a compilação termine com código 0 e sem nenhum erro de TypeScript.
