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
- [ ] **Magnetômetro:** Calibração não requerida, verificada manualmente fora da estação (aplicativo da Parrot). A estação não lê nem bloqueia o lançamento por este estado.
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

## 4. Procedimento de Campo: Decolagem e Pouso no Totem de 1,0 m

Referência: `docs/PROMPT_IMPLEMENTACAO_TOTEM_VOO_ALTITUDE.md`, seções 1.5 e 6. Requisito: voar a 1,8 m
sobre o piso (0,8 m sobre o topo do totem). Os itens abaixo decidem os pontos **[A CONFIRMAR]** do
modelo de sonar; nenhum teste sem a aeronave os prova.

### 4.1 Pré-requisitos
- [ ] Totem de 1,0 m estável; marcador ID 8 no topo, no ponto de decolagem.
- [ ] Medir com trena `platform.edge_ahead_m`: ponto de decolagem -> borda do totem **à ESQUERDA** (eixo lateral
  da trilha de saída; valor esperado 0,30 m para o totem de 60 x 60 cm com decolagem no centro).
- [ ] **Posicionar a trilha de varredura na faixa de visada (D7).** O padrão de voo é estritamente lateral: a
  aeronave não avança nem recua, de modo que o alvo deve estar à frente da câmera na faixa de detecção
  `[h / tan(|tilt| + VFOV/2), h / tan(|tilt| - VFOV/2)]`, com `h` a altura sobre o alvo e `tilt` =
  `gimbal.search_tilt_deg`. Medir a distância da trilha ao alvo e conferir que cai dentro da faixa; fora dela
  o alvo não entra no quadro durante a varredura.
- [ ] **Alcance do retorno (Estágio 3 + RTL).** O Estágio 3 voa lateralmente até ~0,7 x R (R = distância ao
  alvo) ao longo do eixo de retorno, e o W08 (`needed = forward_cruise_velocity * search_timeout_sec /
  |reverse_cruise_velocity|`) não conta esse trecho: `rtl.timeout_sec` deve cobrir o Estágio 2 mais o
  deslocamento lateral do Estágio 3. Com a configuração padrão (0,15 normalizado, 30 s) o retorno cobre
  ~4,5 m; alvos além de ~4-5 m de distância exigem `rtl.timeout_sec` maior.
- [ ] Medir o teto do local sobre o piso e registrar em `platform.venue_ceiling_agl_m`.
- [ ] `mission_config.json`: `platform.height_m = 1.0`, `kinematics.target_altitude_m = 1.8`.
  O arquivo é o store local da estação e não é versionado: conferir os valores nesta máquina.
- [ ] Sonda ativa (`calibration.altitude_probe_enabled = true`): linhas `[ALT_PROBE ...]` a 1 Hz
  e `[PLATFORM ...]` a cada transição no log da missão.

### 4.2 Ensaios
- [ ] **F-0 Decolagem:** registrar a altura em que o firmware encerra o launch profile (esperado
  ~1,0 m sobre o totem) e o tempo até o governor assentar em 0,8 m relativos
  (`[PLATFORM departure_settle err=... elapsed=...]`). Confirmar que não passa de `venue_ceiling_agl_m`.
- [ ] **F-1 Linha de base:** pairar 60 s a 0,8 m sobre o totem; `z_rel` e `fw_alt` com divergência
  estável < 0,10 m; registrar o ruído de ambos.
- [ ] **F-2 Saída do totem (critério de aceite):** cruzeiro lateral de 2 m para a esquerda (`vy > 0`, `vx = 0`,
  sem guinada) e retorno parando antes do totem. 3 repetições com a guarda **desligada** (`edge_ahead_m = null`), 3 com a guarda ligada,
  3 com `guard_vertical_shaper = false`. Registrar a perda máxima transiente de altura sobre o piso,
  o tempo até +/-0,10 m, o `vz` mínimo, o `s` em que o degrau de `fw_alt` ocorre contra o `s_star`
  previsto (valida `sonar_half_angle_deg`) e a velocidade horizontal real na borda. Repetir sobre
  uma bancada. **Meta: perda transiente <= 0,25 m com a guarda ligada.** Se o firmware afundar mais
  rápido que a autoridade de subida da guarda (0,2 normalizado), a meta não é atingível por software
  e o relatório deve dizê-lo.
- [ ] **F-3 Reentrada:** retorno lateral para a direita (`vy < 0`) cruzando a borda com o RTL atual; registrar o pico de `z_rel` (falso
  climb), se `[PLATFORM state=ENTERING ...]` ocorreu antes do degrau, o erro lateral acumulado e o
  pico contra o teto efetivo. Dimensiona `ceiling_allowance_m` e `rise_feedforward_mps`.
  Em `ENTERING`, um estouro de teto **não** pousa a aeronave por até `reentry_breach_grace_sec` (3 s):
  a guarda comanda a descida máxima (`[PLATFORM reentry_descent ...]`). Passado esse tempo, ou fora de
  `ENTERING` ainda acima do teto, o pouso de emergência volta. Manter a mão no ABORTAR durante a reentrada.
- [ ] **F-4 Feedforwards:** a partir de F-2/F-3, ajustar `sag_feedforward_mps` e
  `rise_feedforward_mps`; repetir 3 vezes; manter só se reduzirem a perda.

### 4.3 Decisões após os ensaios
- Divergência `z_rel` x `fw_alt` estável e sem degrau de `vz`: manter `altitude_estimator = "legacy"`.
- Degrau ou spike de `vz` na borda: promover `"vz_gated"`.
- `fw_alt` imune ao sonar e `z_rel` com deriva: habilitar `altitude_anchor_enabled`.

### 4.4 Limite da bancada `--no-fly`
Ensaiar o padrão lateral antes do voo com `mission.py --no-fly` (nunca armar motores nesse ensaio): conferir
no log a sequência `[STEP 2: Lateral Left Search]` com `vy > 0` e `vx = 0`, o freeze do Stage 3, o
enquadramento por gimbal no Stage 4 e a busca do marcador com `vy < 0` no Stage 5.
O `KinematicSimulator` não tem sonar, solo nem totem: o `PlatformMonitor` roda sobre a odometria
sintética, mas não há degrau. A bancada valida a máquina de estados e a ausência de regressão, não a
reação do firmware nem a manutenção de altitude na borda.

### 4.5 Expectativas no Eixo Lateral
- [ ] **Stage 2:** deslocamento somente para a esquerda a `kinematics.forward_cruise_velocity`; `vx` e `vyaw`
  nulos; câmera em `gimbal.search_tilt_deg`. Qualquer avanço ou guinada visível é falha.
- [ ] **Stage 3:** apenas correções laterais (`vy`); sem avanço e sem movimento do gimbal; a aeronave congela
  (`vx = vy = 0`) ao centralizar lateralmente com velocidade abaixo de `inspection.settle_max_speed_mps`.
- [ ] **Stage 4:** aeronave em pairado absoluto; somente o gimbal se move (lei de apontamento limitada por
  `max_slew_limit_deg`, entre `nadir_tilt_deg` e `search_tilt_deg`); captura e JSON pericial marcados
  `degraded`/`settled = false` se o alvo não for enquadrado.
- [ ] **Stage 5:** busca do marcador voando para a **direita** (`vy < 0`, `|vy| = |rtl.reverse_cruise_velocity|`,
  regra E05); se o marcador for ultrapassado, o reenquadramento é um creep para a esquerda.
