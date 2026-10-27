# Refinamento de Voo de Alta Precisao e Nudge de Teclado Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar overshoot de subida, deriva de altitude e offset de pouso no ArUco, tornar a selecao de alvo YOLO estavel com prioridade da classe "acidente", corrigir o Failsafe de Bateria desligavel e adicionar o overlay de velocidade por teclado (W A S D Q R) sem nunca interromper a missao autonoma.

**Architecture:** Cada correcao entra no ponto unico por onde o comando ja passa (`BenchtopDroneProxy.move_velocity`, `governed_vz`, `ArucoCenteringController`, `BatterySupervisor`), com controladores puros novos em `controllers/` e `estimation/`, parametros novos em `parameters.py` e testes antes do codigo. O nudge e um overlay aditivo no proxy, com autoridade propria, saturacao, rampa e deadman, sem caminho para o fluxo de controle da missao.

**Tech Stack:** Python 3.12 + pytest (`mvp_mission_bebop`), ROS 2 Jazzy + Nectar SDK (somente leitura), TypeScript/React/Vitest + Electron (`bebop_mission_control`).

**Spec:** Briefing do usuario de 2026-10-27 (requisitos A a G e nudge), restabelecido na tabela da secao 1, mais a imagem de pouso `/tmp/claude-1000/-home-jv-ros2-ws/cce8d6a7-fd8d-46e0-a3d9-2b2d1eda485e/images/1.jpg` (analise na secao 2.D) e os logs de voo `~/.config/bebop-mission-control/logs/mission_20261024_*.log` a `mission_20261027_*.log` (30 arquivos). Caminhos relativos a `/home/jv/ros2_ws/src/mvp_mission_bebop` (`BE`); `PY = BE/mvp_mission_bebop`, `GCS = BE/bebop_mission_control`, `TS = GCS/src`, `T = BE/test`.

## Global Constraints

- Sem emojis em codigo, comentarios, logs, docs e commits (`CLAUDE.md` secao 5).
- Comentarios so para fisica de controle, cinematica, restricao do Bebop 2 ou sincronismo IPC; docstrings NumPy/reST com `Parameters`, `Returns`, `Raises`; tipagem estrita (`Optional`, `Union`, `Final`); entradas validadas com `ValueError`/`TypeError`.
- `controllers/` e `estimation/`: matematica pura, sem relogio de parede e sem nos ROS. Relogio sempre injetavel.
- Contrato com o GCS intocado: marcadores `[STEP N: ...]`, `MILESTONE_KEYS` (`PY/telemetry/milestones.py:68-69`, fixado por `T/test_contracts.py:655-680`). Esquema de `mission_config.json` so ganha chaves novas, nunca perde nem renomeia (`T/test_contracts.py:_FROZEN_CONFIGURATION_KEYS`).
- Invariantes do voo autonomo (`vx == 0`, `vyaw == 0`, `vz <= 0` fora de janela) continuam validos para os steps; todo relaxamento e uma autoridade escopada, em janela, com teto numerico (padrao `YawAuthority` / `altitude_hold_window`).
- `nectar-sdk` e `ros2_bebop_driver` somente leitura. `BebopDrone.move_velocity` publica `Twist` direto, sem fila (`nectar/nectar/control/bebop/drone.py:216-225`); `takeoff()` dorme 3 s (`:162`).
- Todo comando Python/pytest prefixado por `source /home/jv/ros2_ws/bin/nectar-activate &&`, inclusive em subagentes.
- Commits somente com autorizacao explicita do usuario nesta sessao (memoria `no-commit-without-authorization`); Conventional Commits em ingles tecnico quando autorizado.

## Review Focus

Entradas e condicoes que o briefing implica e nenhum teste de tarefa cobriria sozinho; cada linha ganha seu teste na tarefa dona.

1. **Tecla presa por perda de foco ou de link (T10, T12).** `blur`, `visibilitychange`, ponte de comando caida ou renderer travado com tecla pressionada: o drone nao pode seguir com nudge. Esperado: backend zera em `deadman_sec` sem nova mensagem.
2. **Yaw de nudge acumulado quebra o retorno lateral (T10).** Q/R repetidos giram o corpo e o RTL lateral a direita deixa de apontar para a base. Esperado: orcamento de proa (`yaw_budget_deg`) satura o nudge de yaw e logra o motivo.
3. **Failsafe de bateria com payload antigo, string ou `null` (T2, T11).** `battery.enabled` ausente deve valer `True`; `"false"` como string vale `False` (nunca verdadeiro por ser string nao vazia) e valor nao reconhecido (`"no"`) levanta `TypeError`; limiar `0` e valido.
4. **Altitude atual igual ou acima do alvo, `NaN` ou odometria sem dado na aquisicao (T4).** Sem subida; `NaN` mantem o eixo vertical em repouso, como `AltitudeHoldGovernor._regulate`.
5. **Marcador perdido, ID errado ou pose espelhada durante a descida final (T7).** Perda acima da altura de compromisso volta para alinhar; perda abaixo dela compromete o pouso com o ultimo erro bom; ID errado nunca conta.
6. **Dois alvos "acidente" simultaneos com mesmo IoU e confianca proxima (T3).** O seletor nao pode alternar a cada quadro; troca so apos `selector_switch_frames` quadros consecutivos de vantagem.

---

## 0. Antes de qualquer edicao (leia)

1. **A arvore e compartilhada e esta suja.** Em 2026-10-27 `git status --short` lista 123 entradas sobre o HEAD `40676ec`, incluindo `PY/steps/takeoff.py`, `PY/steps/rtl.py`, `PY/mission.py`, `PY/context.py`, `PY/parameters.py`, `PY/telemetry/battery.py`, `PY/telemetry/failsafe.py`, `PY/engine/runner.py`, `TS/App.tsx`, `GCS/electron/main.cjs`. Antes de cada tarefa: `git status`, `ListAgents` e `git diff -- <arquivo>` dos arquivos que a tarefa toca. Se houver hunks alheios na mesma funcao, **pare e pergunte** ao usuario; nao use `git add -p` sobre hunks alheios. Numeros de linha deste plano foram lidos nessa arvore; **reconfira pelo nome do simbolo**.
2. **Nao suba voo, emulador nem `mission.py`** sem checar `ps -eo args | grep -E "fake_bebop|mission.py" | grep -v grep` (vazio em 2026-10-27). Nunca rode dois lotes emulados ao mesmo tempo (`ROS_DOMAIN_ID` 80-99 compartilhados). Instrua subagentes de revisao a nao lancar lotes emulados.
3. **Linha de base congelada.** Copie a arvore para `BE/.superpowers/baseline-refino-voo/` (ignorado pelo git; inclua ou ligue por symlink `mvp_mission_bebop/yolov8n.pt` e `yolov8n_openvino_model/`). Registre `python3 -m pytest test/ -q` e `cd bebop_mission_control && npx vitest run && npm run typecheck`. Testes de timing oscilam sob carga (`test_inspection_hover`, `test_recovery_and_touchdown`, `test_takeoff`, `test_fake_bebop_interlocks`, `test_announcer_calls`, emulado seed 3): rode a falha sozinha antes de chamar de regressao.
4. **Sem `git commit` nem `git push`** sem autorizacao explicita. Entregue a divisao da secao 9 e espere.
5. **`mission_config.json` nao e rastreado** (`git ls-files` so lista `COLCON_IGNORE`); e o store local da estacao. Defaults novos vivem em `parameters.py`. Nao edite o JSON do usuario, salvo instrucao explicita.
6. **Estilo e Regra de Ouro Black Bee.** Antes de cada controlador novo, confirme em `~/ros2_ws/src/nectar-sdk` que nao ha abstracao equivalente. Verificado em 2026-10-27: nao ha (`ImageCalculus` linearizado e valido so a <= 5 graus, ver `PY/controllers/geometry.py:1-30`; sem seletor de alvo; sem overlay de velocidade; `BebopDrone` so publica).

## 1. Requisitos e mapa para tarefas

| Id | Requisito do briefing | Tarefas |
|---|---|---|
| R-A | Auditoria de latencia, jitter e bloqueios | T1, T9 |
| R-B | Subida acima de 1 m sem overshoot e estabilizacao rapida | T1, T4, T5 |
| R-C | Manutencao rigida de altitude em todos os modos | T5 |
| R-D | Pouso de precisao no centro do ArUco | T6, T7 |
| R-E | Translacoes ortogonais puras | T8 |
| R-F | Prioridade absoluta da classe "acidente" e alvo estavel | T3 |
| R-G | Failsafe de bateria desligavel, limiares estritos | T2, T11 |
| R-N | Nudge por teclado aditivo W A S D Q R | T10, T12 |

## 2. Diagnostico (fatos verificados, causa-raiz, suposicoes)

Convencao: **[FATO]** verificado no codigo ou nos logs; **[SUPOSICAO]** inferencia a validar; **[A CONFIRMAR]** depende de voo ou medicao.

### 2.A Latencia, jitter e bloqueios

- **[FATO]** Nao ha fila de comandos na missao. Cada step chama `ctx.failsafe.clamp_*` e `BenchtopDroneProxy.move_velocity` (`PY/actuators/proxy.py:189-242`), que publica o `Twist` direto; o Bebop trava o ultimo `Twist`. A taxa de comando e a do laco do step (`kinematics.control_loop_hz = 15`).
- **[FATO]** A inferencia YOLO ja roda fora do laco de controle (`PY/perception/worker.py`, `LatestResultSlot`). Nos 30 logs de 2026-10-24 a 27, **todos** os `cadence_report` dos Stages 2, 3 e 4 mostram `0 overruns (0.0%)` a 15 Hz.
- **[FATO]** Stage 1 (subida, `takeoff.py` `_acquire_altitude`) e Stage 5 (`rtl.py` `_center_over_marker`, `_touchdown`) **nao emitem `cadence_report`**: sao os dois trechos sem medicao.
- **[FATO]** Nos dois, a espera de frame precede o `rate.tick()`: `ctx.grab_frame(timeout_sec=0.2)` em `takeoff.py` (laco da subida) e `_acquire_frame(ctx, _CENTERING_FRAME_TIMEOUT_SEC = 0.4)` em `rtl.py:1429`; depois `sensor.observe(frame)` (`rtl.py:1439`) roda a deteccao ArUco **inline**, porque o worker de percepcao e desengajado no Stage 5 (contrato de acesso exclusivo em `PY/perception/worker.py:17-26`). O periodo efetivo do comando e `espera de frame + deteccao + tick`.
- **[FATO]** Inventario de `time.sleep` fora de threads auxiliares: `takeoff.py:492` (poll de 10 ms do assentamento do IMU, solo), `:549` e `:583` (rajada de `land` em falha de decolagem, 50 ms x 5, deliberada), `engine/runner.py:295` (rajada de emergencia, 40 ms x 5, deliberada), `mission.py:1117,1121` (poll de standby), `actuators/proxy.py:297,302` (conexao, solo). `announce_sync(wait=True)` so em caminhos de falha (`takeoff.py:555,589`, `mission.py:894`). **Nenhum bloqueio nao deliberado em voo foi encontrado fora de `grab_frame`/deteccao ArUco.**
- **Conclusao:** o briefing supoe mais bloqueio do que existe. O trabalho e medir os dois trechos cegos (T1), limitar a espera de frame ao resto do periodo e cronometrar a deteccao ArUco (T9), e so entao decidir por worker dedicado do marcador.

### 2.B Subida acima de ~1 m: overshoot e demora

- **[FATO]** O firmware encerra o perfil de decolagem em ~0,92-0,95 m e ignora o alvo (`takeoff.py:53` `TAKEOFF_PLATEAU_M`, log `Liftoff transient decayed ... at 0.95 m`). A subida acima disso e `_acquire_altitude` (`takeoff.py:775-1008`): laco **em malha aberta quanto a dinamica** (avanco `braking_velocity` + `JerkLimitedProfile`, sem realimentacao de posicao) e **so ascendente** (`profiled = max(0.0, profile.step(...))`, `takeoff.py:933,936`).
- **[FATO] Evidencia dos logs** (alvo relativo, altitude `z_rel`):

  | Deficit inicial | Altitude final | Excesso | Resultado |
  |---|---|---|---|
  | 0,07 a 0,12 m | alvo +0,00 a +0,10 m | ate 0,10 m | converge em 1 a 4 s |
  | 0,19 m | 1,18 (alvo 1,10) | +0,08 m | converge |
  | 0,20 m (`160419`) | 1,33 (alvo 1,15) | **+0,18 m** | **nao converge, 15 s de timeout** |
  | 0,25 m | 1,28 (alvo 1,20) | +0,08 m | converge em 10 s |
  | 0,27 m (`081524`) | 1,37 (alvo 1,20) | **+0,17 m** | **nao converge, 15 s de timeout** |
  | 0,29 m | 1,28 (alvo 1,20) | +0,08 m | converge em 9 s |

  Dois dos cinco deficits >= 0,19 m estouram o prazo de 15 s com o drone parado 0,17-0,18 m acima do alvo (`cmd_vz = 0.000`, `odom_vz ~ 0`). **[SUPOSICAO]** o excesso cresce com o tamanho da subida; o relato do usuario (20 a 40 cm acima de 1 m) e a extrapolacao coerente.
- **[FATO] Causas-raiz (codigo):**
  1. O planejador de frenagem assume que o aerofolio segue o perfil comandado. A planta tem atraso de primeira ordem `tau ~ 0,35 s` e latencia de 0,10 a 0,20 s (`T/support/fake_bebop/plant.py:251,322-335`; `calibration.vertical_lag_tau_sec = 0.35`), mais a odometria a 5 Hz. O excesso fisico e `v * (tau + latencia + 1/(2*5 Hz))`, ~9 a 13 cm a 0,25 m/s. Nada no laco o antecipa.
  2. Depois do excesso, o laco **nao tem autoridade de descida** (`max(0.0, ...)`) e o criterio de assentamento exige `distance <= 2*climb_deadband_m = 0,10 m` (`takeoff.py:858`) e `climb_settle_max_speed_mps = 0.1`: com +0,17 m nunca assenta, queima os 15 s inteiros.
  3. No Stage 2 o `AltitudeHoldGovernor` herda o erro de 0,18 m e o corrige a `vz = -0,227 m/s` (`ALT_PROBE` 16:04:50), cruza o alvo e afunda 0,07 m abaixo (`Altitude hold is climbing: 1.08 m is 0.07 m below the 1.15 m setpoint`). A "demora para estabilizar" e a soma de 15 s de timeout mais uma correcao de duas fases sob varredura.
- **[FATO] A fonte de altitude nao e confiavel.** `calibration.altitude_estimator = "legacy"` e `altitude_anchor_enabled = false`: `/bebop/odom.pose.position.z` e o `speedZ` do ARSDK integrado a 5 Hz (`PY/estimation/vertical_estimator.py:1-12`), sem medicao. Do log `160419`: com `z_rel` cravado em 1,33 m e `vz ~ 0`, a altitude do firmware `fw_alt` desce de 1,286 para 1,223 m em 10 s (afundamento sub-limiar invisivel ao integrador); no RTL `z_rel = 1,153` contra `fw_alt = 0,986` (0,17 m de divergencia); no toque, `fw_alt = 0,000` e `z_rel = 0,353`. Em 24 pousos, o `z_rel` de toque varia de **-0,65 a +0,35 m**. `fw_alt` e zero exato no solo e zero na decolagem (`0.013`).
- **[A CONFIRMAR]** Se o `fw_alt` e sonar, barometro ou fusao acima de ~1 m (`calibration.firmware_altitude_source = "unknown"`). O briefing afirma que sonar e fluxo optico perdem alcance entre 1 e 2 m; nao ha dado no repositorio que prove ou refute. T5 define o criterio objetivo para decidir.

### 2.C Manutencao de altitude em todos os modos

- **[FATO]** O regulador vivo e `AltitudeHoldGovernor` (`PY/controllers/altitude_hold.py`, `hold_enabled = true`). `AltitudeAntiClimbGovernor` (`controllers/anti_climb.py`) e o fallback legado, so descendente, selecionado por `governor.hold_enabled = false` (`mission.py:1182-1200`). **Otimiza-se o vivo e mantem-se paridade de interface no legado.**
- **[FATO]** `governor.feedforward_gain = 0` (`mission_config.json`): a antecipacao do afundamento por translacao esta **desligada**. A antecipacao so le `abs(vx_commanded)` (`altitude_hold.py:306`), nunca a guinada, e nao ve aceleracao. No perfil Simple, que guina em Stages 2 a 4, a guinada nao tem antecipacao nenhuma.
- **[FATO]** A janela `altitude_hold_window` e aberta e fechada por step (`rtl.py:889`, `tracking.py` `_servo`, etc.); entre steps o eixo vertical e so descendente (`Altitude hold authority revoked` seguido de `granted` no log). Ha lacunas de autoridade nas transicoes.
- **[FATO]** O regulador compara contra `z_rel` (integrado), cego a afundamento lento (2.B). Nao se segura o que nao se mede.

### 2.D Pouso de precisao (analise da imagem e dos logs)

- **Imagem [FATO, medida em pixels da foto 2000x1500]:** centro da tag AprilTag (quadrado preto) ~ (868, 725); aro da camera frontal (nariz) ~ (1035, 718); centroide dos quatro cubos de helice ~ (1059, 934). O nariz esta alinhado em y com o centro da tag (delta ~ -7 px) e deslocado +167 px em x; o centroide dos motores (proxy do CoG) esta deslocado **+191 px em x e +209 px em y** do centro da tag. Com o quadrado preto de 395 px igual a `rtl.tag_size = 0,20 m` **[SUPOSICAO]** (~1975 px/m), o CoG ficou ~0,097 m a direita e ~0,106 m atras do centro, e o nariz a ~0,108 m a frente do CoG. O corpo esta girado ~5 a 10 graus em relacao a tag **[SUPOSICAO]**. Leitura: o **eixo longitudinal coincide com a hipotese de braco de alavanca nao compensado** (camera sobre o alvo, CoG atras); o desvio lateral nao se explica por extrinseca e coincide com descida cega.
- **[FATO] Causa 1, a extrinseca nao existe.** `project_marker_to_body` (`PY/controllers/rtl_guidance.py:742-795`) devolve o erro do marcador **em relacao a origem da camera**, tratada como origem do corpo: `ex = z*cos(t) - y*sin(t)`, `ey = -x`. Nao ha o vetor camera->CoG. Centralizar o erro da camera a zero poe a *lente* sobre o marcador, nao o CoG.
- **[FATO] Causa 2, o portao de pouso e frouxo.** `rtl.landing_radius_m = 0.13` autoriza o pouso com erro radial residual de ate 13 cm (`parameters.py:954-965`; `rtl_guidance.py:1040,1118`). Em 18 pousos centrados dos logs a autorizacao ocorreu com erro de **0,056 a 0,127 m (mediana ~0,11 m)**, 9 deles em 0,2 s, ou seja, ja dentro do raio no primeiro ciclo: o ramo `within_tolerance` (0,04 m) nunca atua. O comentario de `parameters.py:954-964` registra essa folga como decisao ("o toque absorve o erro"); **o briefing a reverte** (D9).
- **[FATO] Causa 3, descida cega.** Apos `ACHIEVED`, `_touchdown` (`rtl.py:1839-2035`) emite `land` a ~1,0-1,2 m de altura e **nao envia mais nenhum `Twist`** ("a Twist arriving mid-sequence is the one thing that can talk the firmware out of landing", `rtl.py:1985-1988`). A descida de ~1 m (efeito solo incluso) e sem rastreamento: o drone pousa onde a deriva do firmware o leva. Confirma a hipotese do briefing de "parada do tracking em altitude muito alta".
- **[FATO]** O marcador fornece a **altura metrica sobre a base** (`h = z_cam*sin(t) + y_cam*cos(t)`, derivada dos mesmos eixos de `project_marker_to_body`), livre da deriva do integrador de `z`. E a melhor fonte para a descida final.
- **[FATO]** O gimbal confirma `-79.2` graus para `-80.0` comandado (`Return attitude: gimbal confirmed at -79.2 deg`), mas a projecao usa o valor de configuracao (`ArucoCenteringController._tilt_deg`, `rtl_guidance.py:857`).

### 2.E Translacao pura

- **[FATO]** Os steps laterais transmitem `vx` e `vyaw` como literal `0.0` (`rtl.py:_cruise_rightward`, `search.py:496,541`, `tracking.py:_apply`). Com `pitch = 0` comandado, o firmware nao segura posicao no eixo ortogonal: vento, assimetria de empuxo e erro de trim viram deriva diagonal; nao ha realimentacao da velocidade ortogonal nem da proa. Suavizacao por `JerkLimitedProfile` ja existe no eixo principal.
- **[A CONFIRMAR]** A magnitude da deriva ortogonal e da proa em cruzeiro lateral: os logs de 24 a 27/10 sao do perfil Simple (`mission_mode = "simple"`, sem translacao) e nao a medem. T1 instrumenta; T8 entrega o controlador com a parte de proa **desligada por padrao** ate haver medida (D12).

### 2.F Selecao de alvo YOLO

- **[FATO]** "acidente" nao e uma classe do modelo: `yolov8n.pt` e COCO e `context.py:495-530` (`_result_for_annotation`) so renomeia `motorcycle`/`bicycle` para "acidente" na exibicao. O alvo real vem de `vision.target_classes = ["motorcycle", "bicycle"]` (`mission_config.json`, `parameters.py:212`) via `filter_by_class` (`tracking.py:247`, `search.py:330`, `inspection.py:294,485,616`). `carro` e `pessoa` ja sao descartados por esse filtro (pessoa so entra na avaliacao de vitima, `perception/assessment.py`).
- **[FATO]** Nao ha associacao entre quadros. O alvo e `max(targets, key=confidence)` recalculado a cada quadro (`tracking.py:358-361`; `search.py:341,368`; `inspection.py:297`). Duas deteccoes de confianca proxima alternam o alvo; o `ConstantVelocityTracker` so amortece *outliers* de centroide (`estimation/target_tracker.py`, `outlier_residual_px = 180`), nao troca de identidade. O log mostra `class='bicycle'` com confiancas 0,53, 0,75, 0,89, 0,66 em quadros sucessivos.

### 2.G Failsafe de bateria

- **[FATO]** O flag `enabled` existe **so no renderer** (`TS/App.tsx:63`, `localStorage bmg.battery-failsafe.v1`) e so gateia o gatilho da propria estacao (`App.tsx:606`). Tudo o mais ignora o flag:
  - `launchParamsJson(doc, failsafe.thresholdPct)` injeta `battery.land_pct` **sempre** (`TS/lib/batteryFailsafe.ts:104-107`; chamadas `App.tsx:314,366`); `BatteryConfig` nao tem `enabled` (`parameters.py:1252-1272`).
  - O backend retorna em `land_pct` (`telemetry/battery.py:200-242`) e recusa decolar (`takeoff.py` `_check_prearm_battery`) independente do flag.
  - Os portoes de lancamento usam o limiar sempre: `TS/lib/preflightGate.ts:38-69`, `GCS/electron/launchReadiness.cjs:66-70,158-168`, `main.cjs:2584-2600,2705,2728,2903,2926` (`resolveFailsafeThreshold`), `PreflightScreen.tsx:144`. Bateria desconhecida tambem bloqueia (`preflightGate.ts:59`; `mission.py:665-667`).
- **Causa-raiz:** o flag desligado nunca chega ao backend nem aos portoes; o backend e os portoes seguem aplicando o ultimo limiar.

### 2.N Nudge: o que ja existe para reutilizar

- **[FATO]** O padrao de IPC existe: `command_bridge.py` (rclpy residente, JSON por linha em stdin) publica `Int32` em `/bebop/mission/goto_stage`; `main.cjs:2204` (`bmg:goto-stage`) valida e repassa por `sendCommand`; `mission.py:1387-1390` assina e o `StageRequestHandler` decide. O nudge copia esse caminho, com `Twist` em `/bebop/mission/nudge`.
- **[FATO]** `BenchtopDroneProxy.move_velocity` e o ponto por onde **todo** comando de movimento passa (`proxy.py:189-242`), inclusive o alimentador do dead reckoning (`:227-228`). O overlay entra ali, depois do clamp de yaw, e o dead reckoning enxerga o total.
- **[FATO]** O frontend ja trata Escape/Ctrl+C no processo principal por `before-input-event` porque o `<iframe>` do video nao entrega teclas ao `window` (`GCS/electron/abortShortcut.cjs:1-12`, `main.cjs:3417-3421`). O nudge deve provar no app real que o `window` recebe W A S D Q R com o foco no feed; se nao receber, usa a mesma interceptacao.
- **[FATO]** `CockpitScreen.tsx` ja tem `isTextInputOrTerminal` (descarta digitacao em campos e no terminal) e `abortEnabled`/`isAirborne` (`TS/lib/flightState.ts:20-44`).

## 3. Decisoes e premissas (o executor as respeita; o usuario pode reverter)

- **D1 - Perfil.** `mission_mode = "simple"` e o padrao local e nao translada. R-E e validado no perfil lateral (ADVANCED) com o emulador; no Simple, translacao so existe via nudge.
- **D2 - AntiClimb.** O briefing chama de "AntiClimbGovernor" o mantenedor de altitude; o vivo e `AltitudeHoldGovernor`. Otimiza-se este; o legado ganha so os kwargs novos (ignorados) para manter a intercambialidade.
- **D3 - "acidente".** Alias de exibicao de `motorcycle`/`bicycle`; a prioridade e uma lista configuravel (`vision.selector_class_priority`) cujo primeiro grupo e o alias "acidente"; uma classe real chamada `acidente` (modelo futuro) cai no mesmo grupo sem mudar codigo. A tupla de aliases passa a ter fonte unica (`perception/classes.py`), usada por `context.py`.
- **D4 - Nudge e invariantes.** O nudge e a unica excecao escopada a `vx == 0`/`vyaw == 0`; vem **depois** de `clamp_kinematics`/`clamp_translation` e do clamp de yaw do proxy, com teto proprio. Nenhum step muda de contrato.
- **D5 - Nudge por estagio.** Ativo nos Stages 2 e 3 e no Stage 5 ate o inicio do centramento de pouso; **suspenso** no Stage 4 (verificacao de imobilidade e foto) e na fase de pouso de precisao (centramento final, descida, toque), ambos por parametro. Mantem "proibido interromper a missao": suspensao zera o overlay, nao cancela nada.
- **D6 - Orcamento de proa do nudge.** Pergunta aberta P1. Default assumido: `nudge.yaw_budget_deg = 45` (proa acumulada pelo nudge, medida pela integral do yaw comandado; ao esgotar, a tecla de yaw naquele sentido e ignorada). O retorno lateral a direita assume proa do lancamento.
- **D7 - Bateria desligada.** Sem bloqueio de pre-voo (inclusive bateria desconhecida), sem retorno em voo, sem aviso falado dos 10%; a carga segue visivel na barra de status. Um retorno ja iniciado **nao** e cancelado ao desligar (pousar com seguranca prevalece). Pergunta aberta P2.
- **D8 - Bateria ligada em voo.** "RTL se a missao estiver habilitada" e lido como: missao rodando, o Stage 5 do perfil ativo e voado e depois o pouso (comportamento atual de `request_battery_return`, que o faz mesmo com `rtl.enabled = false`); sem missao, `land`. Mantido.
- **D9 - Portao de pouso.** `rtl.landing_radius_m` passa de 0,13 para 0,06 m e o comentario de `parameters.py:954-964` e reescrito: o briefing exige pouso no centro do marcador.
- **D10 - Extrinseca.** `rtl.camera_offset_forward_m` e `rtl.camera_offset_left_m` nascem em `0.0` (comportamento atual) e sao **medidos** com regua (protocolo em T6). Estimativa pela foto: ~ +0,10 m a frente **[SUPOSICAO]**. Nao se grava estimativa como default: sinal errado piora o pouso.
- **D11 - Estimador de altitude.** Nao trocar o default (`legacy`) sem criterio objetivo (T5.4); o criterio e calculado dos logs.
- **D12 - Proa em cruzeiro lateral.** `translation_hold.heading_hold_enabled = False` por padrao: yaw corrompe o fluxo optico (`actuators/yaw_authority.py:1-15`). Ligar so apos T1 medir a deriva de proa.
- **D13 - Contrato.** Nenhum milestone novo, nenhuma chave de `MILESTONE_KEYS` alterada. Telemetria nova entra como linhas de log (`[CMD ...]`, `[NUDGE ...]`, `[ASCENT_REPORT ...]`, `[DESCENT ...]`).
- **D14 - Defaults novos sao pontos de partida.** Todos os numeros novos (ganhos, tetos, tempos) sao valores iniciais para o emulador; a validacao fisica e a checagem F de voo da secao 7.

**Perguntas abertas (default assumido ate resposta):**

| Id | Pergunta | Default |
|---|---|---|
| P1 | Quantos graus de proa o operador pode acumular por Q/R sem desligar o yaw de nudge? | 45 |
| P2 | Com bateria desligada, o aviso falado dos 10% continua? | Nao |
| P3 | O nudge deve atuar no Stage 4? | Nao (suspenso) |
| P4 | Valor medido `camera_offset_forward_m`/`left_m` (regua) | 0.0 ate medir |

## 4. Mapa de arquivos

**Criar (Python):** `PY/actuators/cadence.py`, `PY/actuators/nudge_overlay.py`, `PY/controllers/altitude_acquisition.py`, `PY/controllers/nudge.py`, `PY/controllers/precision_descent.py`, `PY/controllers/cross_axis_hold.py`, `PY/estimation/target_selector.py`, `PY/perception/classes.py`, `scripts/analyze_flight_logs.py`.
**Modificar (Python):** `PY/parameters.py`, `PY/actuators/proxy.py`, `PY/engine/rate.py`, `PY/engine/runner.py`, `PY/blackboard.py`, `PY/context.py`, `PY/mission.py`, `PY/telemetry/battery.py`, `PY/telemetry/failsafe.py`, `PY/controllers/altitude_hold.py`, `PY/controllers/anti_climb.py`, `PY/controllers/rtl_guidance.py`, `PY/controllers/geometry.py` (so se a funcao de altura for publica), `PY/steps/base.py`, `PY/steps/takeoff.py`, `PY/steps/rtl.py`, `PY/steps/tracking.py`, `PY/steps/search.py`, `PY/steps/inspection.py`, `PY/config_audit.py`.
**Criar (testes):** `T/test_command_cadence.py`, `T/test_analyze_flight_logs.py`, `T/test_target_selector.py`, `T/test_altitude_acquisition.py`, `T/test_camera_extrinsics.py`, `T/test_precision_descent.py`, `T/test_cross_axis_hold.py`, `T/test_nudge_overlay.py`, `T/test_nudge_integration.py`, `T/fixtures/flight_log_sample.log`.
**Modificar (GCS):** `GCS/streamer/command_bridge.py`, `GCS/electron/main.cjs`, `GCS/electron/preload.cjs`, `GCS/electron/launchReadiness.cjs`, `TS/types/bmg.ts`, `TS/lib/batteryFailsafe.ts`, `TS/lib/preflightGate.ts`, `TS/App.tsx`, `TS/components/preflight/PreflightScreen.tsx`, `TS/components/shell/StatusBar.tsx`, `TS/components/cockpit/CockpitScreen.tsx`.
**Criar (GCS):** `TS/lib/nudge.ts`, `TS/lib/nudge.test.ts`, `TS/hooks/useNudge.ts`, `TS/hooks/useNudge.test.tsx`, `GCS/electron/nudgeShortcut.cjs` (condicional, T12.7).

---

## 5. Tarefas

> Cada tarefa e TDD: teste que falha, implementacao minima, teste que passa. Ambiente em todo comando: `source /home/jv/ros2_ws/bin/nectar-activate && cd /home/jv/ros2_ws/src/mvp_mission_bebop`.

### PARTE A - Backend Python

### Task 1: Instrumentacao e linha de base numerica (R-A, R-B, R-D, R-E)

Sem mudar comportamento de voo. Produz os numeros contra os quais as tarefas seguintes sao aceitas.

**Files:**
- Create: `PY/actuators/cadence.py`, `scripts/analyze_flight_logs.py`, `T/test_command_cadence.py`, `T/test_analyze_flight_logs.py`, `T/fixtures/flight_log_sample.log`
- Modify: `PY/actuators/proxy.py:189-242` (`move_velocity`), `PY/steps/takeoff.py` (`_acquire_altitude`), `PY/steps/rtl.py` (`_center_over_marker`, `_touchdown`)

**Interfaces:**
- Produces: `class CommandCadenceMonitor(clock: Callable[[], float] = time.monotonic, window: int = 256)` com `note(vx: float, vy: float, vz: float, vyaw: float) -> None`, `report() -> str`, `intervals_ms() -> Tuple[float, ...]`, `maybe_log(logger, period_sec: float = 1.0) -> None`. Atributo `BenchtopDroneProxy.cadence: CommandCadenceMonitor`.
- Produces: linha `[CMD t=<mono> vx=<f> vy=<f> vz=<f> vyaw=<f> dt_ms=<f> p95_ms=<f> max_ms=<f>]` a 1 Hz, estavel para o analisador.
- Produces: `scripts/analyze_flight_logs.py` com `parse_log(path: str) -> FlightRecord` e CLI `python3 scripts/analyze_flight_logs.py <dir|arquivos> [--csv out.csv]`.

- [ ] **Step 1: testes de `CommandCadenceMonitor`** (`T/test_command_cadence.py`): com relogio injetado, 10 chamadas a 66,7 ms dao `p95_ms ~ 66.7` e `max_ms ~ 66.7`; uma lacuna de 400 ms entre duas chamadas aparece em `max_ms >= 400`; `maybe_log` emite no maximo 1 linha por `period_sec`; `note` com `NaN` nao levanta e e contado (campo `nonfinite`).
- [ ] **Step 2:** rodar `python3 -m pytest test/test_command_cadence.py -q`; esperado: FAIL (modulo ausente).
- [ ] **Step 3:** implementar `PY/actuators/cadence.py` (janela circular, percentis por ordenacao simples, sem numpy). Integrar em `BenchtopDroneProxy.__init__` (parametro opcional `cadence`) e chamar `self.cadence.note(vx, vy, vz, vyaw)` + `maybe_log` em `move_velocity` **depois** do clamp de yaw (`proxy.py:225`) e antes do `motion_tracker`. Nao pode lancar: envolver em `try/except Exception` com log `debug`, como o `vertical_command_listener` (`:229-233`).
- [ ] **Step 4:** emitir `rate.cadence_report()` no fim do laco de subida (`takeoff.py` `_acquire_altitude`, bloco `finally` do `with ctx.failsafe.climb_window`) e no fim de `_center_over_marker` (`rtl.py:1474`) e de `_touchdown` (`rtl.py:2000`), no formato dos Stages 2-4 (`Ascent loop cadence: ...`, `Centering loop cadence: ...`, `Touchdown loop cadence: ...`).
- [ ] **Step 5:** cronometrar a deteccao ArUco: `ArucoMarkerSensor.observe` (`rtl.py:357`) acumula `time.perf_counter()` por chamada; `diagnostics()` (`:408`) passa a incluir `detect_ms_p50` e `detect_ms_p95`; `_log_detector_summary` (`:1485`) imprime. Teste em `T/test_aruco_rtl.py` (arquivo existente): com detector falso que dorme 20 ms, `detect_ms_p50 >= 20`.
- [ ] **Step 6: testes do analisador** (`T/test_analyze_flight_logs.py`) sobre `T/fixtures/flight_log_sample.log` (copie ~60 linhas reais de `mission_20261026_160419.log`: `Closing a ...`, `Ascent progress ...`, `ALT_PROBE`, `Centred over marker`, `Touchdown ...`, `cadence`). Asserts: `record.ascents[0].deficit_m == 0.20`, `.final_m == 1.33`, `.overshoot_m == pytest.approx(0.18, abs=0.005)`, `.converged is False`; `record.landing_gate_radial_m == [0.110]`; `record.alt_divergence_m` contem `abs(z_rel - fw_alt)` por `ALT_PROBE`; `record.touchdown_z_rel == [0.35]`.
- [ ] **Step 7:** implementar `scripts/analyze_flight_logs.py` (regex sobre as linhas `Closing a`, `Ascent converged`, `Ascent did not converge`, `[ALT_PROBE ...]`, `Centred over marker`, `Touchdown confirmed`, `[CMD ...]`, `cadence`). Saida: tabela por voo e agregados (mediana, p90) de excesso de subida, divergencia `z_rel` vs `fw_alt`, erro de autorizacao de pouso e `p95_ms` de comando.
- [ ] **Step 8: baseline.** Rodar o analisador nos 30 logs de `~/.config/bebop-mission-control/logs` e colar a tabela no relatorio final (secao 8) como "antes". Rodar `python3 -m pytest test/test_command_cadence.py test/test_analyze_flight_logs.py test/test_actuator_proxy.py test/test_aruco_rtl.py test/test_contracts.py -q`; esperado: PASS.

### Task 2: Failsafe de bateria desligavel no backend (R-G)

**Files:**
- Modify: `PY/parameters.py:1252-1272` (`BatteryConfig`), `PY/telemetry/battery.py` (`BatterySupervisor`, `normalize_percentage` vizinhanca), `PY/steps/takeoff.py:313-352` (`_check_prearm_battery`), `PY/mission.py:628-670,823-828,1147-1151,1387-1402`
- Test: `T/test_battery_guard.py`, `T/test_prearm_battery_gate.py`, `T/test_battery_forced_return.py`, `T/test_contracts.py:109,208`

**Interfaces:**
- Produces: `BatteryConfig.enabled: bool = True`.
- Produces: `normalize_enabled(value: Union[bool, int, str, None], name: str = "battery.enabled") -> bool` em `battery.py`: aceita `bool`; `"true"/"false"` (case-insensitive) como string; recusa o resto com `TypeError`; `None` -> `True` (payload antigo).
- Produces: `BatterySupervisor.enabled: bool` (property) e `BatterySupervisor.configure(*, enabled: Optional[bool] = None, land_pct: Optional[float] = None) -> None` (valida, thread-safe sob `self._lock`).
- Produces: assinatura de `/bebop/mission/battery_failsafe` (`std_msgs/String`, JSON `{"enabled": bool, "land_pct": float}`) lida em `mission.py`.

- [ ] **Step 1: testes** (`T/test_battery_guard.py`): `BatterySupervisor(BatteryConfig(enabled=False, land_pct=50))` com leitura sintetica 10 e `airborne -> True` **nao** chama o hook e `critical_reason()` e `None`; `enabled=True, land_pct=20` com leitura 20 chama o hook uma vez (`<=`); limiar `0` com leitura 0 chama; `normalize_enabled("false") is False`, `normalize_enabled("no")` levanta `TypeError`, `normalize_enabled(None) is True`; `configure(enabled=False)` com leitura abaixo do limiar ja armazenada nao dispara no proximo `_store`; `configure(land_pct=101)` levanta `ValueError`; `mission.battery_warning` **nao** e emitido com `enabled=False` (D7) e e emitido com `True`.
- [ ] **Step 2:** testes de pre-voo (`T/test_prearm_battery_gate.py`): `_check_prearm_battery` com `enabled=False` e carga 5% devolve `True` sem anunciar; com `enabled=True` e carga `<= land_pct` devolve `False` e anuncia "decolagem cancelada" (como hoje).
- [ ] **Step 3:** teste de contrato (`T/test_contracts.py`): adicionar `("battery", "enabled")` a `test_configuration_keys_the_renderer_reads_still_exist` e `"battery.enabled"` ao conjunto de chaves esperadas de `to_dict()`; payload `{"battery": {"land_pct": 25}}` sem `enabled` mantem `enabled is True`.
- [ ] **Step 4:** rodar os tres arquivos; esperado: FAIL.
- [ ] **Step 5:** implementar `BatteryConfig.enabled`, `normalize_enabled`, `BatterySupervisor.enabled/configure`; `_store` so chama `_request_return` e `_raise_warning` com `enabled`; `critical_reason()` devolve `None` com `enabled False`. `mission.py:823-828` e `:1147-1151` normalizam `enabled` com a mesma disciplina de `land_pct` (falha de valor: loga erro e mantem o valor anterior, nunca lanca no meio do voo).
- [ ] **Step 6:** `takeoff._check_prearm_battery`: `if not getattr(battery, "enabled", True): return True` antes de ler a carga. `mission._check_standby_preconditions`: a regra `battery state unknown` so vale com `battery_supervisor.enabled`.
- [ ] **Step 7:** em `mission.py`, apos a assinatura de `goto_stage` (`:1387-1390`), assinar `battery_failsafe_topic = f"/{ns}/mission/battery_failsafe"` com callback que parseia o JSON e chama `battery_supervisor.configure(...)`; JSON invalido ou valor invalido apenas loga `warning` (nunca levanta no executor). Teste: callback com `{"enabled": false}` desliga; com `"x"` mantem o estado e nao levanta.
- [ ] **Step 8:** rodar `python3 -m pytest test/test_battery_guard.py test/test_prearm_battery_gate.py test/test_battery_forced_return.py test/test_battery_rtl_jump.py test/test_contracts.py -q`; esperado: PASS.

### Task 3: Seletor de alvo com prioridade de "acidente" e rastreio persistente (R-F)

**Files:**
- Create: `PY/perception/classes.py`, `PY/estimation/target_selector.py`, `T/test_target_selector.py`
- Modify: `PY/parameters.py` (`VisionConfig`, `parameters.py:212` vizinhanca), `PY/context.py:495-530` e construcao do contexto, `PY/steps/tracking.py:247,273,358-397`, `PY/steps/search.py:330-368`, `PY/steps/inspection.py:294-297,485,616`, `PY/mission.py` (criacao e injecao)

**Interfaces:**
- Produces: `ACCIDENT_CLASS_NAMES: Final[Tuple[str, ...]] = ("acidente", "accident", "motorcycle", "bicycle", "bycicle")` e `is_accident_class(name: str) -> bool` (case-insensitive) em `perception/classes.py`; `context._result_for_annotation` passa a usa-los.
- Produces: `VisionConfig.selector_class_priority: List[str] = ["acidente", "motorcycle", "bicycle"]`, `selector_iou_min: float = 0.25`, `selector_gate_px: float = 90.0`, `selector_lost_frames: int = 6`, `selector_switch_frames: int = 4`, `selector_box_alpha: float = 0.5`.
- Produces: `class TargetSelector(vision_cfg: VisionConfig)` com `select(detections: Sequence[Detection]) -> Optional[Detection]` (devolve copia suavizada: mesmo `class_name`/`class_id`/`confidence`, `xyxy` filtrado), `reset() -> None`, `track_id: Optional[int]`, `switches: int`.
- Consumes: `Detection` do SDK (`xyxy`, `confidence`, `class_id`, `class_name`, `center`) (`nectar/ai/detection/core/types.py`).

- [ ] **Step 1: testes** (`T/test_target_selector.py`), com `Detection` reais do SDK:
  - `test_accident_class_outranks_every_other_class`: quadro com `person` (conf 0.95), `car` (0.9) e `motorcycle` (0.5) -> devolve a `motorcycle`; so `person` + `car` -> `None`.
  - `test_target_identity_does_not_flip_between_frames`: dois `bicycle` A (conf 0.60) e B (0.62) em posicoes distintas, depois A sobe para 0.70 e B cai para 0.55 por 3 quadros -> continua em quem foi escolhido primeiro; `switches == 0`.
  - `test_switch_requires_consecutive_advantage`: candidato de classe de prioridade maior so assume apos `selector_switch_frames` quadros consecutivos; um quadro de ausencia reinicia a contagem.
  - `test_track_survives_short_dropouts_and_releases_after_lost_frames`: ausente por `selector_lost_frames - 1` quadros mantem a associacao; por `selector_lost_frames` libera e o proximo candidato e novo.
  - `test_association_uses_iou_then_centroid_gate`: caixa deslocada com IoU < 0.25 mas centroide a < 90 px associa; centroide a > 90 px nao.
  - `test_box_is_smoothed_without_lagging_a_real_move`: sequencia de centros com ruido +-6 px tem desvio-padrao do centro devolvido <= metade do bruto; salto real de 120 px sustentado converge em <= 5 quadros.
  - `test_equal_candidates_pick_deterministically`: empate de classe, IoU e confianca escolhe o de maior area, depois o de menor `x`, sempre o mesmo.
  - `test_invalid_config_raises`: `selector_box_alpha = 0` e `selector_iou_min = 1.5` levantam `ValueError`.
- [ ] **Step 2:** `python3 -m pytest test/test_target_selector.py -q`; esperado: FAIL.
- [ ] **Step 3:** implementar. Algoritmo: (a) manter descartados os nomes fora de `selector_class_priority` (comparacao por `is_accident_class` para o grupo "acidente"; demais por igualdade em minusculas); (b) rank = indice do grupo; (c) se ha trilha, procurar o candidato que maximiza IoU com a ultima caixa (fallback: menor distancia de centroide dentro de `selector_gate_px`); (d) um candidato de rank estritamente menor (prioridade maior) que o da trilha vira "desafiante" e so assume apos `selector_switch_frames` quadros consecutivos; (e) sem trilha, escolher por (rank, -confianca*sqrt(area), x); (f) suavizar `xyxy` por EMA com `selector_box_alpha`, reiniciando a EMA na troca de trilha.
- [ ] **Step 4:** integrar. `MissionContext` ganha `target_selector: TargetSelector` criado em `mission.py` (uma instancia por missao, compartilhada entre estagios para a identidade sobreviver ao Stage 2 -> 3 -> 4). Em `tracking.py` `_resolve_target` e `_best_detection` (`:358-397`) passam a receber o seletor: `best = ctx.target_selector.select(targets)`; no ramo sem alvo chamar o coast do tracker como hoje. `search.py:341,368` e `inspection.py:297` usam `select` so para escolher o candidato logado/localizado; a confirmacao continua por `HysteresisConfirmer` sobre "algum alvo presente". `reset()` ao inicio do Stage 2 e quando `reacquire_timeout_sec` esgota.
- [ ] **Step 5:** teste de integracao em `T/test_lateral_servoing.py` (existente): dois alvos sinteticos alternando confianca a cada quadro por 60 quadros; o `target_center` entregue ao controlador visual muda de lado no maximo 0 vezes e o desvio quadro a quadro do centro <= 15 px.
- [ ] **Step 6:** rodar `python3 -m pytest test/test_target_selector.py test/test_target_tracker.py test/test_lateral_servoing.py test/test_lateral_search.py test/test_detection_filter.py test/test_inspection_hover.py test/test_contracts.py -q`; esperado: PASS (rode `test_inspection_hover` sozinho se oscilar).

### Task 4: Aquisicao de altitude em malha fechada, bilateral, com avanco de atraso (R-B)

**Files:**
- Create: `PY/controllers/altitude_acquisition.py`, `T/test_altitude_acquisition.py`
- Modify: `PY/parameters.py` (`FlightKinematicsConfig`), `PY/steps/takeoff.py:775-1008` (`_acquire_altitude`), `PY/config_audit.py`
- Test: `T/test_takeoff_acquisition.py`, `T/test_takeoff.py`, `T/test_fly_path_emulated.py`, `T/test_fly_path_totem.py`

**Interfaces:**
- Produces: `FlightKinematicsConfig.climb_lag_sec: float = 0.35`, `climb_position_kp: float = 1.5` (1/s), `climb_decel_mps2: float = 0.12`; `climb_settle_max_speed_mps` de `0.10` para `0.05`.
- Produces: `@dataclass(frozen=True) class AcquisitionLimits(max_velocity_mps: float, max_accel_mps2: float, max_jerk_mps3: float, decel_mps2: float, lag_sec: float, position_kp: float, deadband_m: float)` com `__post_init__` validando positividade e finitude.
- Produces: `@dataclass(frozen=True) class AcquisitionCommand(vz_mps: float, reference_m: float, predicted_m: float, error_m: float, finished: bool)`.
- Produces: `class AltitudeAcquisitionController(limits: AcquisitionLimits)` com `start(altitude_m: float, target_m: float, vz_mps: float = 0.0) -> None`, `update(altitude_m: float, vz_measured_mps: float, dt: float) -> AcquisitionCommand`, `reset() -> None`.
- Consumes: `JerkLimitedProfile`, `ProfileLimits`, `braking_velocity` (`controllers/profiling.py`), `LoopRate.clamp_interval`.

**Lei de controle (decide o que testes e assinatura nao determinam):** o controlador mantem uma **referencia de posicao** `r` que parte da altitude medida e avanca por um perfil de velocidade jerk-limitado cujo alvo e `braking_velocity(target - r, decel_mps2, cruise=max_velocity, arrival_tolerance=deadband)`, independente da medida. O comando e `vz = v_ref + position_kp * (r - z_pred)` com `z_pred = z + lag_sec * vz_measured` (compensa o atraso do firmware e da odometria a 5 Hz), saturado em `+-max_velocity`. Nao ha termo integral. A saida **pode ser negativa**: `FailsafeSupervisor.clamp_kinematics` ja deixa descer livremente (`failsafe.py:326`, `min(vz, ceiling)`), so a subida exige janela.

- [ ] **Step 1: testes de controlador puro** (`T/test_altitude_acquisition.py`), com uma planta de primeira ordem (`tau = 0.35 s`, latencia 0.15 s, odometria a 5 Hz, integrada a 15 Hz) implementada no proprio teste:
  - `test_no_overshoot_for_climbs_of_0p25_0p5_1p0_1p5_m`: para cada deficit, pico de altitude `<= alvo + 0.05` e erro final `<= 0.03`.
  - `test_converges_within_distance_over_speed_plus_3s`: tempo ate `finished` `<= deficit/0.25 + 3.0`.
  - `test_descends_when_it_overshoots`: forcar altitude inicial `alvo + 0.15` -> `vz < 0` e sem subir.
  - `test_robust_to_plant_spread`: tau em `{0.25, 0.35, 0.5}` e latencia em `{0.10, 0.20}`: excesso `<= 0.08`.
  - `test_nan_altitude_holds_at_rest`: `altitude_m = float("nan")` devolve `vz_mps == 0.0` e `finished is False`.
  - `test_command_is_continuous`: `|vz[k+1] - vz[k]| <= max_accel * dt + 1e-9` no trecho de saida do perfil.
  - `test_invalid_limits_raise`: `lag_sec < 0`, `max_velocity_mps = 0` levantam `ValueError`.
- [ ] **Step 2:** testes de step (`T/test_takeoff_acquisition.py`, usando `hovering_context`/`ClimbContext`): alvo relativo +0,5 e +1,0 sobre a altura nativa com a planta do emulador (`tau 0.35`): a subida termina **sem esgotar o prazo** (`caplog` sem `Ascent did not converge`), pico `<= alvo + 0.05`, e a linha `[ASCENT_REPORT target=... peak=... overshoot=... t_converge=... err_final=...]` e emitida. Caso de descida (altitude acima do alvo) continua pelo `_descend_to_target` (`takeoff.py:1010`) sem regressao.
- [ ] **Step 3:** rodar `python3 -m pytest test/test_altitude_acquisition.py test/test_takeoff_acquisition.py -q`; esperado: FAIL.
- [ ] **Step 4:** implementar `altitude_acquisition.py` conforme a lei acima, docstring NumPy com a derivacao do termo `z_pred`.
- [ ] **Step 5:** em `_acquire_altitude`, substituir o calculo de `desired`/`profiled` (`takeoff.py:916-949`) por `AltitudeAcquisitionController`: `controller.start(altitude, target)` antes do laco; por ciclo `command = controller.update(altitude, snapshot.vz, dt)`; `safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(command.vz_mps, 0.0, allow_climb=True)` (descida passa sem janela); manter `climb_window`, o teto checado a cada ciclo (`:895`), a checagem de saude (`:903`), o prazo `_CLIMB_DEADLINE_FACTOR` e a deteccao de estagnacao. Assentamento: `SettlementCriteria.max_distance = deadband` (nao `2*deadband`), `max_speed = climb_settle_max_speed_mps` (agora 0,05); o criterio de estagnacao de 10 s (`:978`) passa a so contar quando `remaining > deadband` **e** o erro nao diminui, para nao falsear em subida lenta legitima.
- [ ] **Step 6:** ao sair do laco emitir `[ASCENT_REPORT ...]` (pico, excesso sobre o alvo, tempo ate `finished`, erro final) e o `cadence_report` (T1.4). Se nao convergir, manter o comportamento atual (aviso e prosseguir), mas **agora o laco tem autoridade de descida**, entao o caso "parado +0,17 m acima" desaparece.
- [ ] **Step 7:** `config_audit.py`: auditar `0 < climb_position_kp <= 4.0` e `0 <= climb_lag_sec <= 1.0`; teste em `T/test_config_audit.py`.
- [ ] **Step 8:** integracao emulada (so com `ps` vazio, um lote por vez): `python3 -m pytest test/test_fly_path_emulated.py test/test_fly_path_totem.py -q`. Aceitacao do emulador: `[ASCENT_REPORT]` com `overshoot <= 0.05` e `t_converge <= 15 s` em todas as sementes; rodar o analisador (`scripts/analyze_flight_logs.py`) e colar "depois" ao lado do "antes" de T1.

### Task 5: Manutencao de altitude em todos os modos e criterio da fonte de altitude (R-C, R-B)

**Files:**
- Modify: `PY/parameters.py` (`AltitudeGovernorConfig`), `PY/controllers/altitude_hold.py:261-319` (`compute_vz`), `PY/controllers/anti_climb.py:103-156`, `PY/steps/base.py:12-29` (`governed_vz`), `PY/engine/runner.py:115-205` (`run`), `PY/steps/tracking.py` (`_apply`), `PY/steps/search.py` (`_yaw_command`)
- Test: `T/test_altitude_hold.py`, `T/test_stage_altitude_wiring.py`, `T/test_simple_mode_steps.py`, `T/test_lateral_altitude_parity.py`

**Interfaces:**
- Produces: `AltitudeGovernorConfig.yaw_feedforward_gain: float = 0.0`, `accel_feedforward_gain: float = 0.0`, `accel_feedforward_cutoff_hz: float = 2.0`.
- Produces: `AltitudeHoldGovernor.compute_vz(current_relative_alt, dt=None, vx_commanded=0.0, hold_integrator=False, yaw_commanded=0.0) -> float`; `AltitudeAntiClimbGovernor.compute_vz` aceita e ignora `yaw_commanded`.
- Produces: `governed_vz(ctx, altitude, dt=None, vx_commanded=None, yaw_commanded=None) -> float`: repassa `yaw_commanded` so quando nao e `None` (duck typing dos duplos de teste).

Feedforward novo (decide o que a assinatura nao determina): `ff = feedforward_gain*|v_h| + yaw_feedforward_gain*|vyaw| + accel_feedforward_gain*lowpass(|d|v_h|/dt|, accel_feedforward_cutoff_hz)`. Os tres ganhos nascem em `0.0` (opt-in): o valor fisico vem da identificacao (T5.5), nao de chute.

- [ ] **Step 1: testes** (`T/test_altitude_hold.py`): com `yaw_feedforward_gain = 0.1` e `yaw_commanded = 0.08`, o `vz` de regime sobe em `0.008` em relacao a `yaw_commanded = 0`; com `accel_feedforward_gain = 0.2` e degrau de `|v_h|` de 0 a 0.15, o termo aparece no ciclo do degrau e decai (filtro) em < 1 s; com os ganhos em `0.0` a saida e **identica** a atual (teste de nao-regressao contra valores fixados); `AltitudeAntiClimbGovernor.compute_vz(..., yaw_commanded=0.5)` devolve o mesmo valor que sem o kwarg.
- [ ] **Step 2:** testes de fiacao (`T/test_stage_altitude_wiring.py`): `governed_vz` repassa `yaw_commanded` quando fornecido e nao o repassa a um governor falso que nao o aceita; no perfil Simple, `tracking._apply` e `search._yaw_command` passam `yaw_commanded=abs(yaw)`.
- [ ] **Step 3:** teste de janela continua (`T/test_stage_altitude_wiring.py`): com `MissionRunner` e tres steps falsos de estagios 2, 3 e 4 que registram `ctx.failsafe.altitude_hold_ceiling` no inicio e no fim de cada `execute`, o teto e `> 0` em **todas** as leituras (sem lacuna entre estagios); fora do `runner.run` volta a `0.0`.
- [ ] **Step 4:** rodar; esperado: FAIL.
- [ ] **Step 5:** implementar. Em `MissionRunner.run`, envolver o laco de estagios com `with self.ctx.failsafe.altitude_hold_window(self.ctx.governor.climb_authority):` aberto **apos o Stage 1** (a janela nasce quando `index` passa de 0 ou quando o primeiro estagio nao e o 1; Stage 1 tem sua propria `climb_window`); a janela de cada step aninha (`altitude_hold_window` salva e restaura o teto previo, `failsafe.py:217-229`). `AltitudeHoldGovernor` guarda `_last_horizontal` e um filtro de primeira ordem para a aceleracao; `reset()` os zera. Steps de translacao e de guinada passam `yaw_commanded`.
- [ ] **Step 6: criterio da fonte de altitude (decisao, nao codigo).** Rodar `scripts/analyze_flight_logs.py` sobre todos os logs de voo real e registrar no relatorio: (a) mediana e p90 de `|z_rel - fw_alt|` em pairar; (b) `fw_alt` no toque; (c) `z_rel` no toque. **Regra:** se a mediana de (a) em pairar `> 0.05 m` **e** `fw_alt` no toque esta em `[-0.03, 0.03]` m em >= 80% dos pousos **e** `z_rel` no toque tem dispersao `> 0.15 m`, recomendar ao usuario `calibration.altitude_anchor_enabled = true` com `altitude_anchor_gate_m = 0.30` e `altitude_anchor_tau_sec = 2.0` (teste no emulador com `T/support/fake_bebop/faults.py` injetando deriva de `speedZ`), e **perguntar** antes de gravar. Nao trocar o default em `parameters.py` sem aprovacao (D11). O numero que o usuario ja observou (24 pousos, `z_rel` de -0,65 a +0,35 m) satisfaz a terceira condicao.
- [ ] **Step 7: identificacao dos ganhos de feedforward (checagem F-C).** Em voo lateral (ADVANCED) a 0,10 e 0,15 normalizado e em guinada de 0,08, registrar `[CMD ...]` (T1) e `[ALT_PROBE ...]`; regredir `d(fw_alt)/dt` contra `|v_h|`, `|vyaw|` e `|d|v_h|/dt|` com `scripts/analyze_flight_logs.py --identify-sink` (estender o script: minimos quadrados de 3 variaveis, sem numpy: normal equations 3x3). Gravar os ganhos medidos como defaults so com aprovacao.
- [ ] **Step 8:** rodar `python3 -m pytest test/test_altitude_hold.py test/test_stage_altitude_wiring.py test/test_simple_mode_steps.py test/test_lateral_altitude_parity.py test/test_safety_invariants.py -q`; esperado: PASS.

### Task 6: Extrinseca camera-corpo e portao de pouso estrito (R-D)

**Files:**
- Create: `T/test_camera_extrinsics.py`
- Modify: `PY/parameters.py:950-965` (`ReturnToLaunchConfig`), `PY/controllers/rtl_guidance.py:742-795` (`project_marker_to_body`), `:844-1145` (`ArucoCenteringController`), `PY/steps/rtl.py:909-930,1372-1482`, `PY/config_audit.py:237`
- Test: `T/test_aruco_rtl.py`, `T/test_config_audit.py`

**Interfaces:**
- Produces: `ReturnToLaunchConfig.camera_offset_forward_m: float = 0.0`, `camera_offset_left_m: float = 0.0`; `landing_radius_m` de `0.13` para `0.06`.
- Produces: `project_marker_to_body(x_cam_m, y_cam_m, z_cam_m, camera_tilt_deg, offset_forward_m: float = 0.0, offset_left_m: float = 0.0) -> Tuple[float, float]` = `(offset_forward_m + z*cos(t) - y*sin(t), offset_left_m - x)`. Derivacao: posicao do marcador no corpo = `t_cam + R(t)*p_cam`; `t_cam = (offset_forward, offset_left, *)` (a componente vertical nao entra nas horizontais).
- Produces: `MarkerObservation.height_above_marker_m(camera_tilt_deg: float) -> float` = `z_cam*sin(t) + y_cam*cos(t)` com `t = radians(abs(tilt))`.
- Produces: `ArucoCenteringController.update(observation, dt, tilt_deg: Optional[float] = None)`: `tilt_deg` da ultima confirmacao do gimbal substitui o valor de configuracao quando fornecido e finito.

- [ ] **Step 1: testes** (`T/test_camera_extrinsics.py`, parametrizados):
  - `ex_body` e `ey_body` com offsets `(0,0)` sao **identicos** aos de hoje para 200 pontos aleatorios (nao-regressao numerica).
  - Marcador na origem da camera (`x=y=0`, `z=1.0`, `t=80`): `ex = 1.0*cos(80) + offset_forward`; `ey = offset_left`. Com `offset_forward = 0.10`, `ex = 0.2736`; com `t = 90`, `ex = 0.10`.
  - Camera 0,10 m a frente do CoG e erro de camera zero: o controlador comanda **avanco** (vx > 0) ate a lente passar do marcador por `offset_forward`; no equilibrio `ex_cam == -offset_forward` (a lente 0,10 m depois do marcador, CoG sobre ele).
  - Simetria: `offset_left = +0.05` desloca `ey_body` em `+0.05` (marcador a esquerda do CoG), comanda `vy > 0`.
  - `height_above_marker_m` a `t = 90` vale `z_cam`; a `t = 80` vale `z*sin80 + y*cos80`; coincide com a altura geometrica de um marcador sintetico projetado por `cv2.projectPoints` (tolerancia 1 mm).
  - `ReturnToLaunchConfig(camera_offset_forward_m=float("nan"))` e `"0.1"` (string) levantam `ValueError`/`TypeError` em `ArucoCenteringController.__init__`; `abs(offset) > 0.5` levanta `ValueError` (sinal de erro de unidade).
  - Portao: com `landing_radius_m = 0.06`, um erro radial estacionario de 0,11 m **nao** autoriza o pouso (`settled is False` por 100 ciclos); 0,05 m a 0,01 m/s autoriza em `centering_settle_cycles` ciclos.
- [ ] **Step 2:** `python3 -m pytest test/test_camera_extrinsics.py -q`; esperado: FAIL.
- [ ] **Step 3:** implementar. `ArucoCenteringController.__init__` valida os offsets e os guarda; `update` repassa-os a `project_marker_to_body`. `rtl.py` `_center_over_marker` passa `ctx.current_tilt_deg` (preenchido por `_assert_tilt`, `rtl.py:930`, e `_confirm_gimbal`, `:770`) como `tilt_deg`. Reescrever o comentario de `parameters.py:954-964` (D9): o portao e a declaracao operacional de "centrado", agora estrita.
- [ ] **Step 4:** `config_audit.py:237`: avisar se `landing_radius_m < centering_tolerance_m` (ja existe) e se `abs(camera_offset_*) > 0.3`; teste.
- [ ] **Step 5: protocolo de medicao da extrinseca (anexo operacional, nao codigo).** Drone no chao, helices fora, hull nivelado: medir com regua **a distancia horizontal do centro da lente da camera frontal ao ponto medio dos quatro cubos de motor** (`camera_offset_forward_m`, positivo a frente) e o desvio lateral do mesmo ponto (`camera_offset_left_m`, positivo a esquerda). Registrar em `docs/CHECKLIST_VOO_REAL.md` (nova secao "Extrinseca camera-CoG"). Validar por 3 pousos: o offset medio de toque (CoG a centro do marcador, regua) deve ficar `<= 0.05 m`; se sobrar vies constante, somar o vies ao offset (o parametro e residual-inclusivo, nao puramente geometrico).
- [ ] **Step 6:** rodar `python3 -m pytest test/test_camera_extrinsics.py test/test_aruco_rtl.py test/test_config_audit.py test/test_recovery_and_touchdown.py -q`; esperado: PASS.

### Task 7: Descida de precisao rastreada em estagios (R-D)

**Files:**
- Create: `PY/controllers/precision_descent.py`, `T/test_precision_descent.py`
- Modify: `PY/parameters.py` (`ReturnToLaunchConfig`), `PY/blackboard.py`, `PY/steps/rtl.py:837-906,1565-1627` (chamadores de `_center_over_marker` + `_touchdown`), `PY/config_audit.py`
- Test: `T/test_recovery_and_touchdown.py`, `T/test_aruco_rtl.py`, `T/test_fly_path_emulated.py`

**Interfaces:**
- Produces: `ReturnToLaunchConfig.visual_descent_enabled: bool = True`, `descent_speed_mps: float = 0.12`, `descent_gate_radius_m: float = 0.05`, `descent_hysteresis: float = 1.5`, `commit_height_m: float = 0.40`, `commit_radius_m: float = 0.05`, `ground_effect_height_m: float = 0.60`, `ground_effect_gain_scale: float = 0.6`, `descent_marker_lost_hold_sec: float = 0.6`, `descent_timeout_sec: float = 20.0`.
- Produces: `class DescentPhase(Enum): ALIGN, DESCEND, COMMIT`; `@dataclass(frozen=True) class DescentCommand(vx: float, vy: float, vz_mps: float, phase: DescentPhase, height_m: Optional[float], radial_error_m: float, commit: bool, note: str)`.
- Produces: `class PrecisionDescentController(rtl_cfg: ReturnToLaunchConfig, centering: ArucoCenteringController)` com `update(observation: Optional[MarkerObservation], fallback_height_m: Optional[float], dt: float, tilt_deg: Optional[float] = None) -> DescentCommand`, `reset() -> None`.
- Produces: `MissionBlackboard.precision_landing_active: bool = False` (consumido por T10).

**Maquina de estados (decide o que a assinatura nao determina):**
- `ALIGN`: mantem a altura; `centering.update(...)` ativo; muda para `DESCEND` quando `radial <= descent_gate_radius_m` por `centering_settle_cycles` ciclos a velocidade residual (mesma regra de `settled`, com o raio de descida).
- `DESCEND`: `vz = -min(descent_speed_mps, kv*(h - commit_height_m))` com `kv = 1.5` 1/s (desacelera ate o compromisso), via `JerkLimitedProfile`; o centramento continua com ganho multiplicado por `ground_effect_gain_scale` abaixo de `ground_effect_height_m`; se `radial > descent_gate_radius_m * descent_hysteresis`, `vz -> 0` (segura altura) e volta a `ALIGN` apos reestabelecer. `h` = `observation.height_above_marker_m(tilt)` quando ha observacao; sem observacao, `fallback_height_m` por `descent_marker_lost_hold_sec`, depois `ALIGN` se `h_ultimo > commit_height_m + 0.10`, ou `COMMIT` se abaixo.
- `COMMIT`: `commit=True` quando `h <= commit_height_m` e `radial <= commit_radius_m` (ultimo erro bom), ou marcador perdido abaixo de `commit_height_m + 0.10`. O step entao zera a translacao e chama `_touchdown` (ja pousa cego daqui, ~0,4 m).

- [ ] **Step 1: testes de maquina de estados puros** (`T/test_precision_descent.py`), observacoes sinteticas:
  - `test_does_not_descend_until_gate_radius_is_met`: erro 0,10 m por 100 ciclos -> `phase is ALIGN`, `vz_mps == 0.0`.
  - `test_descends_with_decelerating_speed_to_commit_height`: de `h = 1.1` a `0.40`, `|vz|` nunca excede `0.12`, decresce monotonicamente nos ultimos 0,3 m e `commit` so e `True` em `h <= 0.40`.
  - `test_pauses_descent_when_error_grows`: erro sobe a `0.09` (> 0,05*1,5) em `h = 0.8` -> `vz_mps == 0.0` e `phase is ALIGN`; ao voltar a 0,03 retoma.
  - `test_marker_lost_above_commit_returns_to_align`: perda por mais de `descent_marker_lost_hold_sec` em `h = 0.9` -> `ALIGN`, sem `commit`.
  - `test_marker_lost_below_commit_commits_with_last_good_error`: perda em `h = 0.45` com ultimo erro 0,04 -> `COMMIT`.
  - `test_ground_effect_scales_lateral_gain`: abaixo de 0,60 m o `|vx|,|vy|` comandados para o mesmo erro sao `0.6` do valor acima.
  - `test_wrong_marker_id_never_counts`: observacao de outro ID (ja rejeitada por `MarkerObservation.from_pose_estimate`) nao altera `h` nem o estado.
  - `test_invalid_config_raises`: `commit_height_m <= 0`, `descent_speed_mps <= 0`, `descent_gate_radius_m <= 0` levantam `ValueError`.
- [ ] **Step 2:** `python3 -m pytest test/test_precision_descent.py -q`; esperado: FAIL.
- [ ] **Step 3:** implementar `precision_descent.py` (NumPy docstrings, sem relogio, `dt` injetado).
- [ ] **Step 4:** em `rtl.py`, novo metodo `_descend_over_marker(self, ctx, centering, sensor) -> _PhaseOutcome` com a mesma estrutura de `_center_over_marker` (saude, interrupcao, `_acquire_frame` limitado ao resto do periodo conforme T9, `sensor.observe`, `descent.update`, transmissao pelo **mesmo** limite de seguranca: `clamp_kinematics(vz, 0.0)`, `clamp_translation`, `vz` normalizado por `ctx.speed_calibration.to_normalized(vz, axis="vertical")`; o governador de altitude **nao** e chamado nesta fase, o `vz` vem do controlador). Prazo `descent_timeout_sec`; ao esgotar, `COMMIT` forcado com log `warning`. `_land_over_base` (`:906`) e o caminho avancado (`:725`/`:747`, `:1596`/`:1627`) chamam `_descend_over_marker` entre `_center_over_marker` e `_touchdown` quando `rtl_cfg.visual_descent_enabled` e o marcador existe; senao, comportamento atual.
- [ ] **Step 5:** `blackboard.precision_landing_active = True` ao entrar em `_center_over_marker` e mantido ate o fim do step (para o nudge, T10); teste em `T/test_aruco_rtl.py`.
- [ ] **Step 6:** log `[DESCENT t=... phase=... h=... err=(ex,ey) vz=...]` a 3 Hz (decimacao `_STREAM_DECIMATION`) para analise; estender `scripts/analyze_flight_logs.py` para extrair "erro no commit".
- [ ] **Step 7:** `config_audit.py`: `commit_height_m` em `[0.20, 0.80]`, `descent_speed_mps <= 0.25`, `descent_gate_radius_m >= centering_tolerance_m`; teste.
- [ ] **Step 8: aceitacao no emulador** (so com `ps` vazio, um lote): em `T/test_fly_path_emulated.py`, acrescentar a metrica de pouso `touchdown_offset_m` (distancia do CoG simulado ao centro do marcador no toque; se `FakeBebopHarness` nao a expoe, adicionar `harness.touchdown_xy()` em `T/support/fake_bebop/harness.py`) e afirmar `touchdown_offset_m <= 0.06` em todas as sementes, com a descida visual ligada, contra o valor de `visual_descent_enabled = False` como controle.
- [ ] **Step 9:** rodar `python3 -m pytest test/test_precision_descent.py test/test_recovery_and_touchdown.py test/test_aruco_rtl.py test/test_config_audit.py -q`; esperado: PASS.

### Task 8: Translacao pura: retencao do eixo ortogonal (R-E)

**Files:**
- Create: `PY/controllers/cross_axis_hold.py`, `T/test_cross_axis_hold.py`
- Modify: `PY/parameters.py` (nova `TranslationHoldConfig` em `MissionParameters`), `PY/actuators/yaw_authority.py` (sem mudanca de contrato), `PY/steps/search.py:496,541`, `PY/steps/rtl.py` (`_cruise_rightward`, `_brake`), `PY/steps/tracking.py` (`_apply`), `PY/mission.py`
- Test: `T/test_lateral_search.py`, `T/test_lateral_rtl.py`, `T/test_lateral_servoing.py`, `T/test_safety_invariants.py`, `T/test_fly_path_emulated.py`

**Interfaces:**
- Produces: `TranslationHoldConfig(enabled: bool = True, kp_velocity: float = 0.4, ki: float = 0.1, max_correction: float = 0.03, min_active_speed: float = 0.03, heading_hold_enabled: bool = False, heading_kp: float = 0.6, heading_max_vyaw: float = 0.04, heading_deadband_deg: float = 1.5)`; `MissionParameters.translation_hold`.
- Produces: `class CrossAxisHold(cfg: TranslationHoldConfig)` com `update(main_axis: str, main_command: float, cross_velocity_mps: float, yaw_error_deg: Optional[float], dt: float) -> Tuple[float, float]` devolvendo `(cross_correction, yaw_correction)` normalizados; `reset() -> None`. `main_axis in {"vx", "vy"}`.
- Produces: `FailsafeSupervisor.clamp_translation` inalterado; a correcao ortogonal passa por ele (envelope) e por `clamp_kinematics` (yaw) como todo comando.

**Racional:** com o eixo principal comandado, o eixo ortogonal passa de literal `0.0` para `0.0 + correcao`, `correcao = -(kp*v_cross + ki*integral(v_cross))`, saturada em `max_correction` (0,03 normalizado, ~3x o piso de quantizacao de 0,01 do driver) e ativa so enquanto `|main_command| >= min_active_speed` (em pairar o firmware segura posicao). A parte de proa so existe com `heading_hold_enabled`, sob uma `YawAuthority` aberta so durante o cruzeiro lateral e limitada a `heading_max_vyaw` (D12).

- [ ] **Step 1: testes puros** (`T/test_cross_axis_hold.py`): vento lateral constante simulado (planta de 1a ordem) -> o desvio ortogonal acumulado em 5 s com a retencao e `<= 25%` do sem retencao; saturacao em `+-0.03`; inativo abaixo de `min_active_speed`; `ki` com anti-windup (integral limitada a `max_correction/ki`); `yaw_error_deg = None` ou `heading_hold_enabled False` -> `yaw_correction == 0.0`; entrada `NaN` devolve `(0.0, 0.0)` e nao contamina o integrador; `reset` zera.
- [ ] **Step 2:** testes de invariantes: com `translation_hold.enabled = True`, em `T/test_lateral_search.py` e `T/test_lateral_rtl.py` o comando transmitido tem `|vx| <= 0.03` (e nao mais o literal `0.0`), `vyaw == 0.0` com `heading_hold_enabled False`, e o eixo principal e identico ao de hoje; com `enabled = False` o comportamento e **bit a bit** o atual. Ajustar as asserts `vx == 0` dos testes existentes de padrao lateral para `abs(vx) <= translation_hold.max_correction`, citando D4/D12 no comentario do teste.
- [ ] **Step 3:** `python3 -m pytest test/test_cross_axis_hold.py -q`; esperado: FAIL.
- [ ] **Step 4:** implementar o controlador e integrar nos tres pontos de cruzeiro lateral: `search.py` (varredura a esquerda), `rtl.py` `_cruise_rightward`/`_brake` (retorno a direita), `tracking.py` `_apply` (servo lateral, perfil lateral). A velocidade ortogonal vem de `OdometrySnapshot.vx/vy` (`odometry.py:104-105`; converter para o corpo com `snapshot.yaw` se a odometria for mundial; **confirmar o referencial lendo `OdometrySupervisor` antes**, [A CONFIRMAR]); a proa vem de `snapshot.yaw` menos a proa de referencia gravada ao entrar no cruzeiro.
- [ ] **Step 5:** `FailsafeSupervisor` ganha `heading_hold_window(max_vyaw)` espelhando `yaw_window` (`failsafe.py:262-268`), usada so quando `heading_hold_enabled`; teste de que fora da janela o yaw continua zerado pelo proxy e pelo failsafe (`T/test_yaw_authority.py`).
- [ ] **Step 6:** emulador: com falha de vento lateral injetada (`T/support/fake_bebop/faults.py`, estender com `crosswind(mps)` se ausente), cruzeiro lateral de 2 m: desvio ortogonal final `<= 0.08 m` e deriva de proa `<= 2 graus`; metrica no teste, nao so log.
- [ ] **Step 7:** rodar `python3 -m pytest test/test_cross_axis_hold.py test/test_lateral_search.py test/test_lateral_rtl.py test/test_lateral_servoing.py test/test_safety_invariants.py test/test_yaw_authority.py -q`; esperado: PASS.

### Task 9: Ritmo do laco: espera de frame limitada e deteccao medida (R-A)

**Files:**
- Modify: `PY/engine/rate.py:98-147`, `PY/steps/rtl.py:190,1429,2204`, `PY/steps/takeoff.py` (laco da subida)
- Test: `T/test_rate.py`, `T/test_aruco_rtl.py`, `T/test_takeoff_timing.py`

**Interfaces:**
- Produces: `LoopRate.remaining_sec() -> float`: tempo restante do periodo corrente, `>= 0`, sem dormir.
- Consumes: `CommandCadenceMonitor` (T1).

- [ ] **Step 1: testes** (`T/test_rate.py`): com relogio injetado, `remaining_sec()` logo apos `tick()` e `~period`; apos consumir metade do periodo e `~period/2`; nunca negativo; nao altera `ticks` nem `overruns`.
- [ ] **Step 2:** testes de ritmo (`T/test_aruco_rtl.py`): `_center_over_marker` com `ctx.grab_frame` falso que bloqueia pelo timeout recebido: o timeout recebido e `<= rate.period_sec + 0.05` (nao `0.4`); ciclo total `<= 1.5 * period` no laco sem deteccao.
- [ ] **Step 3:** rodar; esperado: FAIL.
- [ ] **Step 4:** implementar `remaining_sec`; em `_center_over_marker` e no laco da subida passar `timeout_sec = max(0.02, rate.remaining_sec())` a `ctx.grab_frame`/`_acquire_frame` (mantendo `_CENTERING_FRAME_TIMEOUT_SEC` como teto). A ordem dos passos do ciclo nao muda: o comando continua saindo a cada ciclo mesmo sem frame novo (reenvia o ultimo estado governado, como Stages 2 e 3 ja fazem em `tracking.py:236-241`).
- [ ] **Step 5: decisao medida, nao implementada aqui.** Depois de T1, se `detect_ms_p95 > 0.5 * period_sec` no Stage 5 (reais) ou `[CMD] p95_ms > 1.5 * period`, abrir tarefa separada para um `MarkerWorker` com slot de ultimo resultado (mesmo padrao de `perception/worker.py`). Registrar a medida no relatorio; nao construir o worker especulativamente.
- [ ] **Step 6:** rodar `python3 -m pytest test/test_rate.py test/test_aruco_rtl.py test/test_takeoff_timing.py test/test_stage3_integration.py -q`; esperado: PASS.

### Task 10: Overlay de nudge no backend (R-N)

**Files:**
- Create: `PY/controllers/nudge.py`, `PY/actuators/nudge_overlay.py`, `T/test_nudge_overlay.py`, `T/test_nudge_integration.py`
- Modify: `PY/parameters.py` (`NudgeConfig`), `PY/actuators/proxy.py:92-242`, `PY/context.py`, `PY/blackboard.py`, `PY/steps/base.py:12-29`, `PY/steps/takeoff.py:1114-1170` (`_finalize`), `PY/steps/rtl.py` (`_touchdown`, entrada de `_center_over_marker`), `PY/telemetry/failsafe.py:454-500`, `PY/engine/runner.py:282-334`, `PY/mission.py:1380-1402`
- Test: `T/test_actuator_proxy.py`, `T/test_safety_invariants.py`, `T/test_contracts.py`

**Interfaces:**
- Produces: `NudgeConfig(enabled: bool = True, max_horizontal: float = 0.08, max_vyaw: float = 0.15, attack_sec: float = 0.25, release_sec: float = 0.15, deadman_sec: float = 0.35, allow_in_inspection: bool = False, allow_in_precision_landing: bool = False, yaw_budget_deg: float = 45.0)`; `MissionParameters.nudge`. Todos normalizados no dominio do driver `[-1, 1]`.
- Produces: `class NudgeShaper(cfg: NudgeConfig)` (puro) com `step(intent: Tuple[float, float, float], dt: float) -> Tuple[float, float, float]` (rampa linear: `attack` a `max/attack_sec`, `release` a `max/release_sec`, saturacao em `max_horizontal` no vetor `(vx, vy)` e `max_vyaw` em yaw) e `reset()`.
- Produces: `class NudgeOverlay(cfg: NudgeConfig, *, clock: Callable[[], float] = time.monotonic, permit: Callable[[], bool] = lambda: True, scale_source: Optional[Callable[[], float]] = None, yaw_rate_per_unit_rad_s: float = 1.75, max_total_horizontal: float = 0.2)` com:
  - `set_intent(vx: float, vy: float, vyaw: float) -> None` (thread-safe; chamado pelo callback ROS; valida finitude e satura cada eixo em `[-1, 1]`; carimba o instante);
  - `arm() -> None`, `disarm(reason: str) -> None` (latch: apos `disarm` so `arm()` reabilita), `armed: bool`;
  - `apply(vx: float, vy: float, vyaw: float) -> Tuple[float, float, float]`: devolve o comando **total** `autonomo + nudge`, com o vetor horizontal total limitado a `max_total_horizontal` por escala (preserva direcao) e yaw total limitado a `max(|yaw autonomo|, max_vyaw)`; identidade quando inativo;
  - `horizontal_magnitude() -> float` (modulo do nudge aplicado no ultimo `apply`, para o governor);
  - `snapshot() -> NudgeState` (`intent`, `applied`, `armed`, `permitted`, `yaw_budget_left_deg`).
- Produces: `BenchtopDroneProxy(..., nudge_overlay: Optional[NudgeOverlay] = None)`; `MissionContext.nudge: NudgeOverlay`.
- Produces: topico `/bebop/mission/nudge`, `geometry_msgs/Twist`: `linear.x` = intencao frente (+W), `linear.y` = esquerda (+A), `angular.z` = guinada a esquerda (+Q); cada eixo em `[-1, 1]` (a estacao envia -1, 0, +1; o backend e quem escala e satura).

**Semantica (decide o que a assinatura nao determina):**
- Sem mensagem por `deadman_sec` -> intencao efetiva `0` (a rampa de release leva a zero em `release_sec`; nunca degrau).
- `permit()` falso ou `armed` falso -> intencao efetiva `0`, mesma rampa. A suspensao por estagio (Stage 4, pouso de precisao) e inteiramente a funcao `permit` injetada em `mission.py`; o overlay nao conhece estagios.
- Orcamento de proa: integra `applied_vyaw * yaw_rate_per_unit_rad_s * dt` em graus; ao exceder `yaw_budget_deg` no sentido da tecla, a componente de yaw naquele sentido vai a `0` e e logada uma vez; o sentido oposto continua livre.
- O fator `scale_source()` (`ctx.governor.horizontal_scale`) multiplica o nudge **horizontal**: o erro de altitude que reduz a demanda autonoma reduz o nudge igualmente.
- `apply` calcula `dt` pelo relogio injetado entre chamadas, limitado por `LoopRate.clamp_interval`.
- Nenhum metodo toca `emergency_event`, `stage_jump_event`, `failsafe`, `land`, `takeoff` ou `request_*`: o overlay nao tem referencia a eles.

- [ ] **Step 1: testes do `NudgeShaper`** (`T/test_nudge_overlay.py`): degrau de intencao `+1` em `vx` atinge `max_horizontal` em `attack_sec` (+-5%) sem exceder; soltar leva a zero em `release_sec` (+-5%) sem cruzar o zero; `(1, 1, 0)` e saturado como **vetor** em `max_horizontal` (modulo `<= 0.08 + 1e-9`, direcao 45 graus); `NaN` e `inf` no `step` devolvem a saida anterior decaindo, nunca a propagam.
- [ ] **Step 2: testes do `NudgeOverlay`:**
  - `test_inactive_overlay_is_identity`: sem `arm`, `apply(0.1, -0.05, 0.0, ...)` devolve a entrada exata.
  - `test_nudge_is_additive_to_autonomous_command`: autonomo `vy = -0.15` (RTL a direita) + intencao `vx = -1` (S, para tras) por 1 s -> `apply` devolve `vy == -0.15` exato e `vx == -0.08`; a trajetoria autonoma nao e alterada.
  - `test_total_is_clamped_to_the_horizontal_envelope`: autonomo `vy = -0.19` + nudge `vy = -0.08` -> modulo total `<= 0.2`.
  - `test_deadman_zeroes_after_timeout`: sem nova mensagem por `deadman_sec + release_sec` o overlay devolve a identidade (relogio injetado).
  - `test_disarm_latches_and_arm_reenables`: apos `disarm("landing")` mensagens novas nao tem efeito ate `arm()`.
  - `test_permit_false_zeroes_the_nudge_with_a_ramp`: `permit` falso zera o efeito com rampa de `release_sec`; voltar a `True` com a tecla ja solta (deadman expirado) nao reativa o nudge.
  - `test_yaw_budget_saturates_one_direction`: com `yaw_budget_deg = 10`, Q mantido ate esgotar bloqueia mais giro a esquerda e permite R; `snapshot().yaw_budget_left_deg` coerente.
  - `test_governor_scale_reduces_nudge`: com `scale_source = lambda: 0.5` o nudge horizontal aplicado cai a metade.
  - `test_overlay_has_no_reference_to_mission_control_flow`: o modulo `nudge_overlay` nao importa `context`, `failsafe` nem `engine` (assert sobre `sys.modules`/AST do arquivo).
- [ ] **Step 3: testes de integracao** (`T/test_nudge_integration.py`, planta falsa do emulador/simulador):
  - `test_nudge_never_interrupts_the_mission`: com nudge ativo durante `SearchStep`/`ClosedLoopRTLStep` simulados, `ctx.interrupted()` e `False`, `ctx.stage_jump_event` nao setado, `ctx.emergency_event` nao setado, `ctx.failsafe.failsafe_active is False`, `StepStatus` do step igual ao de uma execucao sem nudge.
  - `test_no_effect_before_takeoff_is_complete`: antes de `TakeoffStep._finalize` retornar `SUCCESS`, `set_intent` nao muda o `Twist` publicado; apos, muda.
  - `test_no_effect_on_ground_or_during_final_landing`: com `blackboard.landing_started = True`, ou `precision_landing_active = True` (e `allow_in_precision_landing False`), ou `flying_state` em solo, o `Twist` e o autonomo puro.
  - `test_stage4_is_suspended_by_default`: com `ctx.current_stage = 4` o nudge e zero e a verificacao de imobilidade (`settle_max_speed_mps`) nao e perturbada por uma tecla mantida; com `allow_in_inspection = True` o nudge atua.
  - `test_emergency_landing_ignores_nudge`: `trigger_emergency_land` e `_transmit_emergency_landing` publicam `Twist` zero exato mesmo com nudge ativo (o `disarm("emergency")` ocorre antes do primeiro `move_velocity`).
  - `test_governor_feedforward_sees_the_nudge`: `governed_vz` soma `ctx.nudge.horizontal_magnitude()` ao `vx_commanded`; com `feedforward_gain > 0`, nudge de 0.08 eleva o `vz` de regime proporcionalmente.
  - `test_dead_reckoning_receives_the_total_command`: `motion_tracker.command` e chamado com o comando total; deslocamento acumulado inclui o nudge.
- [ ] **Step 4:** `python3 -m pytest test/test_nudge_overlay.py test/test_nudge_integration.py -q`; esperado: FAIL.
- [ ] **Step 5:** implementar `controllers/nudge.py` (puro) e `actuators/nudge_overlay.py`. Em `BenchtopDroneProxy.move_velocity`, **depois** do clamp de yaw (`proxy.py:217-225`) e **antes** de `motion_tracker`/`vertical_command_listener`/simulador/driver: `vx, vy, vyaw = self.nudge.apply(vx, vy, vyaw)` quando houver overlay. `vz` nao e tocado. `BenchtopDroneProxy.land()` (`proxy.py:142-165`) chama `self.nudge.disarm("landing")` antes de zerar o `motion_tracker`. `BenchtopDroneProxy.takeoff()` nao arma.
- [ ] **Step 6: janela de armamento e desarmamento.** `TakeoffStep._finalize` (`takeoff.py:1114`): `ctx.nudge.arm()` imediatamente antes de devolver `SUCCESS` (decolagem 100% concluida e assentada); falha ou aborto nao armam. `FailsafeSupervisor.trigger_emergency_land` (`failsafe.py:454-500`, **os dois caminhos**, repeticao e primeira): `getattr(self.actuator, "nudge", None)` -> `disarm("failsafe")` antes do primeiro `move_velocity`. `MissionRunner._transmit_emergency_landing` e `_finalize` (`runner.py:282-334`): `ctx.nudge.disarm("emergency")` antes do laco. `ClosedLoopRTLStep._touchdown` (`rtl.py:1864`): `ctx.nudge.disarm("touchdown")`. `_center_over_marker`: `ctx.blackboard.precision_landing_active = True` (T7), lido pelo `permit`.
- [ ] **Step 7:** `permit` em `mission.py`: `lambda: not ctx.failsafe.failsafe_active and not ctx.emergency_event.is_set() and not ctx.blackboard.landing_started and ctx.current_stage in permitted_stages and (cfg.allow_in_precision_landing or not ctx.blackboard.precision_landing_active)`, com `permitted_stages = {2, 3, 5}` mais `{4}` quando `cfg.allow_in_inspection`.
- [ ] **Step 8:** assinatura ROS em `mission.py` apos a de `goto_stage` (`:1387-1390`): `telemetry_node.create_subscription(Twist, f"/{ns}/mission/nudge", cb, 10)` com `cb` -> `ctx.nudge.set_intent(msg.linear.x, msg.linear.y, msg.angular.z)`; `try/except` que apenas loga. Log `[NUDGE intent=(...) applied=(...) budget=...]` a 2 Hz enquanto ativo, no overlay (nao em milestone: D13). `no_fly`: o overlay atua antes do simulador (`proxy.py:235-241`), testavel na bancada.
- [ ] **Step 9:** `governed_vz` (`steps/base.py:12-29`): somar `ctx.nudge.horizontal_magnitude()` a `vx_commanded` (criar o argumento quando `None`), preservando a chamada exata para duplos sem `nudge`. 
- [ ] **Step 10:** `T/test_contracts.py`: `MissionParameters().to_dict()` contem `nudge.*` e `translation_hold.*`; `MILESTONE_KEYS` inalterado. Rodar `python3 -m pytest test/test_nudge_overlay.py test/test_nudge_integration.py test/test_actuator_proxy.py test/test_safety_invariants.py test/test_contracts.py test/test_simple_mode_steps.py -q`; esperado: PASS.

---

### PARTE B - Frontend TypeScript e Electron

### Task 11: Failsafe de bateria desligavel no GCS (R-G)

**Files:**
- Modify: `TS/lib/batteryFailsafe.ts:104-107`, `TS/lib/preflightGate.ts:38-69`, `GCS/electron/launchReadiness.cjs:66-70,158-168`, `GCS/electron/main.cjs:2584-2600,2705,2728,2903,2926` (e novo handler), `GCS/electron/preload.cjs`, `TS/types/bmg.ts:543-547,599-602`, `TS/App.tsx:314,366,494-501,605-620`, `TS/components/preflight/PreflightScreen.tsx:144`, `TS/components/shell/StatusBar.tsx:301,333-336,403-433`, `GCS/streamer/command_bridge.py`
- Test: `TS/lib/batteryFailsafe.test.ts`, `TS/lib/batteryGateParity.test.ts`, `TS/lib/preflightGate.test.ts`, `TS/lib/launchReadiness.test.ts`, `TS/lib/launchReadinessMain.test.ts`, `TS/App.battery.test.tsx`, `TS/App.launchParams.test.tsx`

**Interfaces:**
- Produces: `launchParamsJson(doc: unknown, failsafe: Pick<BatteryFailsafe, 'enabled' | 'thresholdPct'>): string`, escrevendo `battery.enabled` (booleano) e `battery.land_pct`. Constantes `ENABLED_PATH = 'battery.enabled'`.
- Produces: `PreflightGateInput.batteryFailsafeEnabled?: boolean` e `LaunchReadinessInput.batteryFailsafeEnabled?: boolean` (default `true`).
- Produces: `main.cjs` `resolveBatteryGate(doc, options) -> { enabled: boolean; thresholdPct: number }` (substitui `resolveFailsafeThreshold`; `enabled` vem de `doc.battery.enabled`, depois `options.failsafeEnabled`, default `true`; `0` continua valido para o limiar).
- Produces: IPC `bmg:set-battery-failsafe` -> `bridge.setBatteryFailsafe(enabled: boolean, thresholdPct: number): Promise<{ success: boolean }>`; `command_bridge.py` op `{"op": "battery", "enabled": bool, "land_pct": float}` publicando `std_msgs/String` JSON em `/bebop/mission/battery_failsafe`.

- [ ] **Step 1: testes (vitest).**
  - `batteryFailsafe.test.ts`: `launchParamsJson(doc, {enabled: false, thresholdPct: 35})` produz `battery.enabled === false` e `battery.land_pct === 35`; com `enabled: true` ambos refletem; `doc` `null` funciona; a contagem continua fixa em `LAUNCH_COUNTDOWN_SEC`.
  - `preflightGate.test.ts`: com `batteryFailsafeEnabled: false`, carga `5` e carga desconhecida (`batteryKnown: false`) **nao** bloqueiam; com `true`, carga `20` e limiar `20` bloqueia (`<=`) com `Bateria insuficiente para voo real (20% <= limiar 20%)`; carga `21` libera.
  - `launchReadiness.test.ts` e `launchReadinessMain.test.ts`: mesmas duas regras no lado CommonJS; `resolveBatteryGate` com `doc.battery.enabled = false` e `land_pct` string `"35"` -> `{enabled: false, thresholdPct: 35}`; `doc.battery.enabled = "false"` (string) -> tratado como `false`; ausente -> `true`.
  - `batteryGateParity.test.ts`: paridade `preflightGate.ts` x `launchReadiness.cjs` x `takeoff._check_prearm_battery` para `enabled in {true,false}` e cargas `{null, 0, 19, 20, 21}`.
  - `App.battery.test.tsx`: desligar o failsafe no `StatusBar` e lancar -> `mission.launch` recebe `paramsJson` com `battery.enabled === false`; em voo com carga abaixo do limiar **nao** chama `gotoStage`; ligado, chama uma vez; alternar para desligado **durante o voo** chama `bridge.setBatteryFailsafe(false, thresholdPct)`; um retorno ja iniciado nao e cancelado ao desligar.
- [ ] **Step 2:** `cd GCS && npx vitest run src/lib/batteryFailsafe.test.ts src/lib/batteryGateParity.test.ts src/lib/preflightGate.test.ts src/lib/launchReadiness.test.ts src/lib/launchReadinessMain.test.ts src/App.battery.test.tsx src/App.launchParams.test.tsx`; esperado: FAIL.
- [ ] **Step 3:** implementar. `App.tsx:314,366` passam `failsafe` inteiro; `PreflightScreen.tsx:144` passa `batteryFailsafeEnabled: props.failsafe?.enabled`; `StatusBar.tsx`: com `enabled` falso o titulo do indicador vira "Failsafe de bateria desligado" e o botao de limiar segue desabilitado (ja e) — a coloracao `critical` por `charge < 25` permanece (a carga continua visivel). `changeFailsafe` (`App.tsx:494`) chama `bridge.setBatteryFailsafe` quando ha missao em execucao.
- [ ] **Step 4:** `main.cjs`: `ipcMain.handle('bmg:set-battery-failsafe', ...)` valida `typeof enabled === 'boolean'`, `thresholdPct` numero finito em `[0, 100]`, exige `missionProcess`; `sendCommand({op: 'battery', enabled, land_pct})`. `preload.cjs`: `setBatteryFailsafe`. `bmg.ts`: tipo no `BmgBridge`.
- [ ] **Step 5:** `command_bridge.py`: `battery_cfg = node.create_publisher(String, TOPIC_BATTERY_FAILSAFE, 10)`; op `battery` valida tipos e publica `json.dumps({"enabled": ..., "land_pct": ...})`; atualizar o docstring do protocolo e `T/test_command_bridge.py` (`op=battery` publica uma `String` JSON valida; `enabled` nao booleano e ignorado).
- [ ] **Step 6:** rodar os testes do Step 2, `T/test_command_bridge.py` e `cd GCS && npm run typecheck`; esperado: PASS.

### Task 12: Nudge de teclado no cockpit (R-N)

**Files:**
- Create: `TS/lib/nudge.ts`, `TS/lib/nudge.test.ts`, `TS/hooks/useNudge.ts`, `TS/hooks/useNudge.test.tsx`, `GCS/electron/nudgeShortcut.cjs` (condicional, Step 7)
- Modify: `GCS/streamer/command_bridge.py`, `GCS/electron/main.cjs` (handler e limite de taxa), `GCS/electron/preload.cjs`, `TS/types/bmg.ts`, `TS/components/cockpit/CockpitScreen.tsx`, `T/test_command_bridge.py`
- Test: `TS/components/cockpit/CockpitScreen.test.tsx`, `TS/lib/mainHarness.test.ts` (padrao em `TS/lib/__fixtures__/mainHarness.ts`)

**Interfaces:**
- Produces: `type NudgeIntent = { vx: -1 | 0 | 1; vy: -1 | 0 | 1; vyaw: -1 | 0 | 1 }` em `bmg.ts`; `BmgBridge.sendNudge(intent: NudgeIntent): void` (fire-and-forget, `ipcRenderer.send('bmg:nudge', intent)`).
- Produces (`nudge.ts`): `NUDGE_KEY_MAP: Readonly<Record<string, { axis: keyof NudgeIntent; sign: 1 | -1 }>>` por `event.code`: `KeyW -> vx +1`, `KeyS -> vx -1`, `KeyA -> vy +1`, `KeyD -> vy -1`, `KeyQ -> vyaw +1`, `KeyR -> vyaw -1`; `reduceNudgeKeys(pressed: ReadonlySet<string>, event: Pick<KeyboardEvent, 'type' | 'code' | 'repeat' | 'ctrlKey' | 'metaKey' | 'altKey'>): ReadonlySet<string>` (ignora `repeat`, combinacoes com Ctrl/Meta/Alt e teclas fora do mapa); `nudgeIntent(pressed): NudgeIntent` (teclas opostas se anulam: A+D = 0); `nudgeEnabled(input: { missionState: MissionState; flyingState: number | null | undefined; stage: number; landProgress?: LandProgressPhase | null }): boolean` = missao `running` **e** `flyingState in {2, 3}` (pairando ou voando) **e** `stage >= 2` **e** `landProgress` nulo; `NUDGE_HEARTBEAT_MS = 100`; `NUDGE_MAX_RATE_HZ = 20`.
- Produces (`useNudge.ts`): `useNudge(bridge, enabled: boolean): { pressed: ReadonlySet<string>; intent: NudgeIntent; active: boolean }`.
- Produces (backend da estacao): `main.cjs` `ipcMain.on('bmg:nudge', (_e, intent) => ...)` e `command_bridge.py` op `{"op": "nudge", "vx": n, "vy": n, "vyaw": n}` publicando `Twist` em `/bebop/mission/nudge`.

- [ ] **Step 1: testes de `nudge.ts`** (`TS/lib/nudge.test.ts`): cada `code` mapeia ao eixo e sinal da tabela; `keydown` com `repeat: true` nao altera o conjunto; `ctrlKey`/`metaKey`/`altKey` ignoram (nao captura Ctrl+R, Ctrl+C nem Alt+Q); `keyup` remove; `A`+`D` -> `vy = 0`; `W`+`S` -> `vx = 0`; `Q`+`R` -> `vyaw = 0`; `W`+`D`+`Q` -> `{vx: 1, vy: -1, vyaw: 1}`; `nudgeEnabled` falso em solo (`flyingState 0`), em decolagem (`1`), em pouso (`4`), com `stage 1`, com `missionState` `idle`/`arming`/`faulted`/`finished`, com `landProgress` nao nulo; verdadeiro em `{running, 2, stage 2}` e `{running, 3, stage 5}`.
- [ ] **Step 2: testes do hook** (`TS/hooks/useNudge.test.tsx`, `vi.useFakeTimers`, `bridge` falso com `sendNudge = vi.fn()`):
  - `keydown KeyW` (habilitado) envia `{vx:1,vy:0,vyaw:0}` **uma vez** na mudanca e depois a cada `NUDGE_HEARTBEAT_MS`; em 1 s ha entre 9 e 11 mensagens (nunca por evento de teclado repetido).
  - `keydown` com `repeat: true` nao gera mensagem extra.
  - `keyup` de todas envia **exatamente** `{0,0,0}` imediatamente e para o heartbeat; apos 300 ms nao ha mais envio.
  - `window blur` e `document.visibilitychange` (hidden) limpam o conjunto e enviam `{0,0,0}`.
  - `enabled` indo de `true` para `false` com tecla pressionada envia `{0,0,0}` e ignora novos `keydown` ate rehabilitar; tecla ja pressionada ao habilitar **nao** reativa sem novo `keydown`.
  - Alvo em `INPUT type=text`, `TEXTAREA` ou terminal (`isTextInputOrTerminal`) nao captura; foco em `input[type=range]` captura.
  - `preventDefault` so nas seis teclas mapeadas e so habilitado (nao rouba digitacao, nem `Ctrl+R`).
  - Desmontar o hook envia `{0,0,0}`.
- [ ] **Step 3: testes do `main.cjs` e da ponte:** (a) `bmg:nudge` rejeita componente fora de `{-1,0,1}` (satura em `[-1,1]`, descarta `NaN`), descarta sem `missionProcess`, e limita a `NUDGE_MAX_RATE_HZ` (mensagens a mais de 20 Hz sao descartadas **exceto** `{0,0,0}`, que sempre passa); (b) `command_bridge.py`: `{"op":"nudge","vx":1,"vy":0,"vyaw":-1}` publica `Twist(linear.x=1, linear.y=0, angular.z=-1)` em `/bebop/mission/nudge`; valores nao numericos ou fora de `[-1,1]` sao saturados/ignorados sem excecao e **nao** publicam em `/bebop/cmd_vel`.
- [ ] **Step 4: teste de cockpit** (`TS/components/cockpit/CockpitScreen.test.tsx`): com `missionState='running'`, `flying_state=3`, `stage=2`: `userEvent.keyboard('{w>}')` chama `bridge.sendNudge` com `vx: 1`; com `flying_state=0` ou `stage=1` nao chama; Escape e Ctrl+C continuam abortando (nao regridem); o chip de nudge exibe as teclas pressionadas e "NUDGE ARMADO" quando `nudgeEnabled` e "NUDGE INATIVO" caso contrario, com a dica "W A S D deslocam; Q R giram; ativo em voo apos a decolagem".
- [ ] **Step 5:** `cd GCS && npx vitest run src/lib/nudge.test.ts src/hooks/useNudge.test.tsx src/components/cockpit/CockpitScreen.test.tsx src/lib/mainHarness.test.ts`; esperado: FAIL.
- [ ] **Step 6:** implementar `nudge.ts`, `useNudge.ts` (listeners em `window`: `keydown`, `keyup`, `blur`, `visibilitychange`; estado em `useRef`; heartbeat por `setInterval(NUDGE_HEARTBEAT_MS)` ativo so com tecla pressionada; envio na mudanca de intencao e no heartbeat), o chip no `CockpitScreen` (componentes `Indicator`/`Plate` existentes, sem tokens novos), `bmg.ts`, `preload.cjs` (`sendNudge: (intent) => ipcRenderer.send('bmg:nudge', intent)`), `main.cjs` e `command_bridge.py` (op `nudge`, topico `TOPIC_NUDGE = f"{NAMESPACE}/mission/nudge"`, publisher `Twist` com profundidade 1: so a ultima intencao importa, como no gimbal).
- [ ] **Step 7: verificar no app real e, se preciso, interceptar no processo principal.** `npm run build` e abrir o cockpit com a ponte de comando e o simulador de bancada (`--no-fly`); clicar no feed de video (iframe) e pressionar W: se o `window` **nao** receber `keydown`, criar `GCS/electron/nudgeShortcut.cjs` espelhando `abortShortcut.cjs` (`before-input-event`, `armed` publicado por `bmg:cockpit-nudge-armed` a partir de `nudgeEnabled`, entrega `bmg:nudge-key` ao renderer com `{type, code}`), com testes no mesmo padrao de `TS/lib/abortShortcutMain.test.ts`. Se o `window` receber, **nao** criar o arquivo (YAGNI) e registrar o resultado no relatorio.
- [ ] **Step 8:** rodar `cd GCS && npx vitest run && npm run typecheck && npm run build` e `python3 -m pytest test/test_command_bridge.py -q`; esperado: PASS.

---

### PARTE C - Paridade, integracao e verificacao

### Task 13: Verificacao final

- [ ] **Step 1: contratos.** `python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py test/test_mission_parameters.py test/test_command_bridge.py -q`; esperado: PASS, sem mudar `MILESTONE_KEYS` nem marcadores `[STEP N: ...]`.
- [ ] **Step 2: suite completa**, na ordem: `python3 -m pytest test/ -q`; depois `cd bebop_mission_control && npx vitest run && npm run typecheck && npm run build`. Compare com a linha de base da secao 0; falhas de timing listadas em 0.3 rodam sozinhas antes de serem chamadas de regressao.
- [ ] **Step 3: integracao emulada** (so com `ps -eo args | grep -E "fake_bebop|mission.py" | grep -v grep` vazio, um lote por vez): `python3 -m pytest test/test_fly_path_emulated.py -q`; `python3 -m pytest test/test_fly_path_totem.py -q`; `python3 scripts/bench_rehearsal.py`. Aceitacao: `[ASCENT_REPORT overshoot <= 0.05]` em todas as sementes (T4); `touchdown_offset_m <= 0.06` (T7); desvio ortogonal `<= 0.08 m` e proa `<= 2 graus` com vento lateral (T8); `[CMD p95_ms <= 1.5 * period]` nos Stages 1, 2, 3 e 5 (T9).
- [ ] **Step 4: tabela antes/depois.** Rodar `scripts/analyze_flight_logs.py` sobre os logs novos do emulador e comparar com a tabela de T1: excesso de subida, divergencia `z_rel` vs `fw_alt`, erro de autorizacao de pouso, `p95_ms`.

## 6. Comandos exatos

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop

# Python por componente
python3 -m pytest test/test_command_cadence.py test/test_analyze_flight_logs.py test/test_actuator_proxy.py -q
python3 -m pytest test/test_battery_guard.py test/test_prearm_battery_gate.py test/test_battery_forced_return.py test/test_contracts.py -q
python3 -m pytest test/test_target_selector.py test/test_target_tracker.py test/test_lateral_servoing.py -q
python3 -m pytest test/test_altitude_acquisition.py test/test_takeoff_acquisition.py test/test_takeoff.py test/test_takeoff_timing.py -q
python3 -m pytest test/test_altitude_hold.py test/test_stage_altitude_wiring.py test/test_lateral_altitude_parity.py test/test_safety_invariants.py -q
python3 -m pytest test/test_camera_extrinsics.py test/test_precision_descent.py test/test_aruco_rtl.py test/test_recovery_and_touchdown.py -q
python3 -m pytest test/test_cross_axis_hold.py test/test_lateral_search.py test/test_lateral_rtl.py test/test_yaw_authority.py -q
python3 -m pytest test/test_nudge_overlay.py test/test_nudge_integration.py test/test_simple_mode_steps.py -q

# Frontend
cd bebop_mission_control
npx vitest run src/lib/batteryFailsafe.test.ts src/lib/batteryGateParity.test.ts src/lib/preflightGate.test.ts src/lib/launchReadiness.test.ts src/lib/launchReadinessMain.test.ts src/App.battery.test.tsx src/App.launchParams.test.tsx
npx vitest run src/lib/nudge.test.ts src/hooks/useNudge.test.tsx src/components/cockpit/CockpitScreen.test.tsx
npm run typecheck && npm run build

# Suite completa e analise
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/ -q
python3 scripts/analyze_flight_logs.py ~/.config/bebop-mission-control/logs --csv .superpowers/baseline-refino-voo/antes.csv
```

## 7. Checagens de campo (nenhum teste sem a aeronave prova)

Executar so com operador presente, area livre de 5 m x 5 m x 3 m e a estacao pronta para abortar (Escape).

| Id | Checagem | Aceite |
|---|---|---|
| F-B | 5 decolagens com subida de +0,3, +0,6 e +1,0 m acima do hover nativo | `[ASCENT_REPORT overshoot <= 0.05 m]`, sem timeout de 15 s; `fw_alt` no fim a `<= 0.07 m` do alvo |
| F-C | Cruzeiro lateral a 0,10 e 0,15, guinada a 0,08 e transicoes de estagio, 60 s cada | perda de altura transiente `<= 0.10 m`; `[CMD]` e `[ALT_PROBE]` guardados para T5.7 |
| F-D1 | Medir a extrinseca camera-CoG com regua (T6.5) | valores em `docs/CHECKLIST_VOO_REAL.md` e gravados em `rtl.camera_offset_*` |
| F-D2 | 5 pousos em marcador, da altura de operacao, com descida visual | distancia CoG-centro do marcador `<= 0.05 m` por regua, em 5 de 5 |
| F-E | Cruzeiro lateral de 2 m no perfil ADVANCED | desvio ortogonal `<= 0.10 m`; proa registrada para decidir `heading_hold_enabled` |
| F-N1 | Nudge no Stage 2: W, S, A, D, Q, R isolados e combinados | `Twist` aditivo registrado em `[NUDGE]`; soltar zera em `<= 0.5 s`; missao segue em `[STEP 2]` |
| F-N2 | Fechar a janela do GCS ou travar o renderer com tecla pressionada | deadman zera em `<= 0.5 s` |
| F-N3 | Nudge em solo, em decolagem, em Stage 4 e no pouso final | zero efeito em todos |
| F-G | Failsafe desligado com 10% de carga: decola e voa; ligado a 20% com 20%: recusa e avisa; em voo a 20%: Stage 5 e pouso | os tres comportamentos conforme R-G |

## 8. Riscos, limites e relatorio final

1. **Fonte de altitude (D11).** Sem F-ID e sem o criterio de T5.6 aprovado pelo usuario, `legacy` permanece e a manutencao de altitude continua limitada pela medida; o nucleo de T4 (aquisicao bilateral com avanco) ja elimina o excesso de subida independentemente dela.
2. **Extrinseca em zero (D10).** Ate a medicao, T6 corrige so o portao e o rastreio da descida; o vies longitudinal de ~0,1 m so some com o valor medido.
3. **Yaw do nudge e RTL lateral (D6).** O orcamento limita, mas nao elimina, a mudanca de proa; o retorno por marcador a direita assume proa de lancamento.
4. **Nudge e retorno autonomo brigam.** O RTL odometrico tenta cancelar o deslocamento do nudge ao soltar a tecla; e o comportamento esperado de um *trim* de velocidade, nao um defeito.
5. **Perfil Simple (D1).** Translacao pura (R-E) so e exercitavel em ADVANCED e por nudge.
6. **Defaults de ganho (D14).** `climb_position_kp`, `translation_hold.*`, `nudge.*` e `descent_*` sao valores iniciais calibrados no emulador (planta `tau 0.25-0.5 s`, latencia 0.10-0.20 s).
7. **Outras sessoes** editam `parameters.py`, `takeoff.py`, `rtl.py`, `mission.py`, `context.py`, `battery.py`, `failsafe.py`, `runner.py`, `App.tsx` e `main.cjs`: reconfira `git status`/`git diff` antes de cada tarefa que toque nesses arquivos.
8. **Voz offline.** O aviso de bateria desligada e silencioso; nenhuma frase nova de copiloto e criada (D13); o cache de fala nao muda.

**Relatorio final do executor:** arquivos criados e alterados; linha de base vs resultado (testes e tabela do analisador, antes/depois); o que foi e o que nao foi rodado e por que; respostas aplicadas a P1 a P4; resultado do Step 7 de T12 (iframe recebe ou nao as teclas); criterio de T5.6 calculado dos logs e a recomendacao ao usuario sobre o estimador de altitude; confirmacao de que `MILESTONE_KEYS`, marcadores `[STEP N: ...]` e as chaves de `mission_config.json` nao mudaram.

## 9. Commits propostos (atomicos; so com autorizacao)

1. `chore(telemetry): add command cadence monitor, marker detection timing and flight log analyzer` (T1)
2. `feat(battery): honour a disabled failsafe in the mission, the launch gates and the station` (T2, T11)
3. `feat(perception): persistent target selector with absolute priority for the accident class` (T3)
4. `feat(takeoff): closed-loop two-sided altitude acquisition with actuator lag lead` (T4)
5. `feat(altitude): yaw and acceleration feedforward and a continuous hold window across stages` (T5)
6. `feat(rtl): camera-to-body extrinsics and a strict landing gate` (T6)
7. `feat(rtl): marker-tracked staged descent with ground-effect gain schedule` (T7)
8. `feat(flight): cross-axis hold for pure translational legs` (T8)
9. `perf(loop): bound the frame wait to the remaining control period` (T9)
10. `feat(nudge): additive velocity overlay with its own authority, ramp and deadman` (T10)
11. `feat(bmg): keyboard nudge in the cockpit with throttled IPC` (T12)
12. `docs: checklist de voo real com extrinseca camera-CoG e checagens de campo` (T6.5, secao 7)

Rodape de commit conforme a sessao. **Nao commitar nem enviar sem pedido do usuario.**
