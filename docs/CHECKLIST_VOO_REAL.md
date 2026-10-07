# Checklist de Voo Real e Matriz de Riscos — MVP Bebop

Este documento consolida o checklist operacional de pré-voo, as suposições críticas a validar no primeiro voo controlado e a matriz de riscos técnicos aceitos, conforme as Seções 8 e 9 de `docs/PROMPT_VOO_REAL_ESCOPO_ATUAL.md`.

---

## 1. O Que Nenhum Teste Sem a Aeronave Prova (Suposições de Hardware / Firmware)

Nenhum simulador ou emulador substitui a resposta real do firmware ARSDK e da física aerodinâmica. Os itens a seguir constituem **suposições** que devem ser validadas e medidas no primeiro voo curto controlado:

| # | Suposição | Impacto se Divergente | Método de Validação em Voo Controlado |
|---|---|---|---|
| **S1** | O firmware ARSDK ignora comandos PCMD (`cmd_vel`) enquanto a aeronave está em solo (`flying_state == 0`) e durante a descida final (`landing`). | Movimentação espúria dos motores antes da decolagem ou durante o toque. | Observação visual e telemetria: conferir se `cmd_vel` publicado em solo não induz rotação dos motores. |
| **S2** | O comando `land` emitido durante `motor_ramping` (7) ou `takingoff` (1) é respeitado pelo firmware. | Atraso no aborto durante a subida inicial. | Teste de aborto voluntário aos 0.5s após `takeoff` em bancada com hélice removida ou voo raso. |
| **S3** | Fidelidade da dinâmica do modelo emulado (constantes de tempo e inércia do Bebop 2). | PIDs podem apresentar sobre-sinal ou oscilação maior que o simulado. | Medição da curva real de subida e resposta aos degraus de velocidade. |
| **S4** | Deriva do odômetro por fluxo óptico no piso real e escala `normalized_to_mps`. | Erro acumulado na navegação relativa e centralização de pouso. | Comparação entre deslocamento estimado por odometria e deslocamento físico marcado no solo. |
| **S5** | Calibração do magnetômetro e enquadramento real da câmera frontal/nadir. | Erro de mira ArUco e IBVS no Stage 3 e Stage 5. | Validação visual da detecção de marcadores em bancada antes do voo. |
| **S6** | Latência real da síntese de voz Gemini Live com o uplink do tethering 4G/5G. | Atraso nos anúncios se a rede móvel oscilar. | Medição p95 dos eventos `[SPEECH_DONE]` em campo antes da decolagem. |
| **S7** | Taxa real de entrega de tópicos `cmd_vel` via Wi-Fi do Bebop 2. | Controle com jerk se houver perda de pacotes UDP. | Monitoramento via `ros2 topic hz /bebop/cmd_vel` durante voo curto. |
| **S8** | Tempo real entre o comando `takeoff` e os estados 1/2 (`takingoff`/`hovering`). | Se exceder 6.0s, o failsafe de decolagem abortará erroneamente. | Medição com cronômetro e telemetria no primeiro salto curto (calibra `takeoff_confirm_timeout_sec`). |

---

## 2. Riscos Aceitos para o Primeiro Voo (Seção 9)

Por decisão de escopo, os itens a seguir foram **adiados** e permanecem como riscos conhecidos e aceitos pelo operador:

| Item Adiado | Risco que Permanece | Mitigação Operacional |
|---|---|---|
| **Perda de Wi-Fi:** assinatura de `states/link`, `flying_state == 255`, banner na UI | O link perdido só é detectado pela interrupção da odometria (após 3 s); a missão não reage instantaneamente na camada de controle. | Operador deve manter linha de visada próxima (< 10 m) e acionar Abortar ou RTH manual no Skycontroller se houver desvio. |
| **Aeronave ociosa ou missão travada:** heartbeat do laço de controle | Se o nó da missão congelar sem emitir exceção, a aeronave permanece em hover até esgotar a bateria ou comando externo. | Failsafe de bateria do firmware da Parrot (RTH automático com bateria baixa). |
| **Missão morta em voo:** pouso automático da estação | Saída inesperada ou crash do processo `mission.py` em voo exige que o operador aperte o botão "ABORTAR" na estação. | Botão "ABORTAR MISSÃO" permanece habilitado mesmo sem processo de missão ativo (envia `land` direto via ponte/CLI). |
| **Supervisor de `flying_state` durante a missão; saúde no Stage 4** | Pouso ou emergência espontânea do firmware durante um step não é tratado como evento proativo da missão. | Operador monitora visualmente e encerra via Abortar ou desligamento de emergência. |
| **Rajada e confirmação de pouso unificadas no failsafe automático** | Failsafe automático de bateria da estação envia par único `cmd_vel` zero + `land`. | Supervisor de pouso da estação (R6) reenvia `land` a cada 500 ms se o estado continuar em voo. |
| **Driver:** watchdog de `cmd_vel`, reconexão ARSDK, `ReturnHomeOnDisconnect` | O último `cmd_vel` publicado pode persistir temporariamente se o driver cair. | Operador preparado para corte de emergência no Skycontroller/FreeFlight. |
| **Voz offline** | Sem internet móvel, alertas dinâmicos não são sintetizados pelo Gemini Live. | A estação exibe todos os alertas textualmente no banner de diagnóstico e cards visuais; fila não trava. |
| **Gravador de voo/telemetria** | Não haverá gravação binária em disco da telemetria completa em voo real; apenas stdout e logs da estação. | Logs de texto gravados em `~/.config/bmg/logs/` e fotos capturadas salvas no workspace. |
| **Novas falas (decolagem concluída, marcador localizado, etc.)** | Anúncios vocais cobrem apenas eventos existentes do MVP e o novo anúncio de aborto. | Feedback visual no Cockpit (StageBar e cards de telemetria) supre os passos intermediários. |

---

## 3. Checklist Pré-Voo do Operador (Passo a Passo)

### 3.1 Inspeção Mecânica e Espaço Físico
- [ ] **Área de voo:** Raio livre mínimo de 5m horizontal e 2.5m vertical, sem obstáculos, pessoas ou animais.
- [ ] **Iluminação:** Ambiente bem iluminado para permitir rastreamento pelo sensor óptico de fluxo e câmera.
- [ ] **Piso:** Superfície com textura visual rica (não usar piso reflexivo ou monótono) para ancoragem do fluxo óptico.
- [ ] **Aeronave:** Hélices fixadas e travadas nas posições corretas (duas horárias, duas anti-horárias); sem trincas.
- [ ] **Lentes:** Lente da câmera frontal e sensor vertical (nadir) limpos e desobstruídos.
- [ ] **Bateria da Aeronave:** Encaixada mecanicamente até o travamento do clipe traseiro.

### 3.2 Estação de Controle e Conectividade
- [ ] **Laptop GCS:** Conectado à internet via tethering USB de celular (para voz e telemetria).
- [ ] **Rede Wi-Fi:** Conectar interface Wi-Fi do laptop à rede `Bebop2-xxxxxx`.
- [ ] **Ping de teste:** `ping 192.168.42.1` respondendo com RTT < 10 ms.
- [ ] **Driver ROS 2:** Iniciado via GCS ou `make driver-bebop`.
- [ ] **Áudio:** Volume do laptop habilitado e desmutado para ouvir as orientações do copiloto.

### 3.3 Verificação no Dial de Pré-Voo (BMG)
- [ ] **Modo de Operação:** Voo Real (mint), NÃO Bancada (cyan).
- [ ] **Texto do Dial:** `"VOO REAL: motores serão armados"`.
- [ ] **Status dos Tópicos:** Todos os tópicos obrigatórios verdes (odometria, câmera, bateria, cmd_vel, land).
- [ ] **Bateria:** Leitura conhecida e >= 35% (piso de segurança).
- [ ] **Magnetômetro:** Calibração não requerida.
- [ ] **Ponte de Comando:** Status `ready`.
- [ ] **Copiloto:** Voz pronta (online).

### 3.4 Procedimento de Lançamento
1. Conferir que todos ao redor estão cientes da decolagem iminente.
2. Posicionar o cursor sobre o dial central.
3. **Pressionar e segurar** o dial por **1,2 segundos** até o anel de armamento completar 100% e o rótulo mudar de `"VOO REAL: motores serão armados"` para `"ARMANDO"`.
4. Soltar o botão: a missão entrará em contagem regressiva sonora de 3 segundos, executando flat trim e referência de solo.
5. Manter as mãos prontas sobre a tecla **Escape** ou o botão vermelho **ABORTAR MISSÃO**.

### 3.5 Procedimento de Aborto de Emergência
- A qualquer momento durante a contagem, subida, voo ou aproximação:
  - Pressionar a tecla **Escape** no teclado OU
  - Clicar imediatamente no botão **ABORTAR MISSÃO** no topo do Cockpit.
- A estação cortará a velocidade instantaneamente, emitirá rajada de comandos `land` e acompanhará o pouso até a confirmação em solo (`flying_state == 0`).
