# Prompt de Implementacao: Voo Real 100% via Frontend (BMG)

Documento de arquitetura e prompt de execucao para o Claude Code (terminal).
Origem: auditoria somente leitura de 2026-10-01 (sem drone na rede, sem motores, sem alteracao de codigo).
Companheiros: `docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md`, `docs/IMPLEMENTACAO_PROGRESSO.md`.

---

## 0. Como usar

Abrir o Claude Code em `/home/jv/ros2_ws/src/mvp_mission_bebop` e colar o bloco da secao 9. O restante deste
documento e a especificacao que o bloco manda ler. Nada aqui foi implementado.

Base medida em 2026-10-01 (HEAD `90de3a0`, arvore com trabalho nao commitado do "lancamento instantaneo"):

| Suite | Resultado |
|---|---|
| `python3 -m pytest test/ -q` | 1221 passed (275 s) |
| `npx vitest run` | 55 arquivos, 473 passed |
| `npx tsc --noEmit` | 0 erros |

Fato x suposicao: tudo marcado "Fato" foi lido no codigo (arquivo:linha). Tudo marcado "Suposicao" depende de
comportamento do firmware do Bebop 2 e so se prova com a aeronave. Isso esta consolidado na secao 7.

---

## 1. Escopo e proibicoes absolutas

1. **Proibido armar motores.** Nenhuma etapa desta implementacao pode publicar `/bebop/takeoff`, `/bebop/cmd_vel`
   nao nulo ou `/bebop/land` no dominio ROS do driver real (`ROS_DOMAIN_ID=14`) nem falar com `192.168.42.1`.
   Tudo que exija `mission.py --fly` roda contra o **emulador de driver** (secao 3) em dominio privado.
2. Nunca iniciar o driver real (`make driver-bebop`) nem a missao em `--fly` fora do emulador.
3. Nao executar `git commit`, `git push` nem `git pull` com a arvore suja sem autorizacao explicita do usuario
   (memoria `no-commit-without-authorization`). Ao fim, listar arquivos alterados e propor a divisao em commits
   Conventional Commits, sem executa-la.
4. O repositorio `ros2_bebop_driver` e o `nectar-sdk` tem modificacoes locais sem commit que nao pertencem a esta
   tarefa. Nao reverter, nao commitar, nao reformatar. Alteracao no driver so na Fase 6 e so apos autorizacao.
5. Rodar as suites com o BMG fechado (o lock do announcer faz testes do announcer falharem com o app aberto).

---

## 2. Mapeamento: o caminho real do clique ao pouso

```text
LaunchDial (disabled se preflightBlockedReason != null)             PreflightScreen.tsx:112-125,176
  -> App.launch(): commitLaunchDocument -> mission.launch            App.tsx:207-282
  -> ipc bmg:start-mission -> startMissionProcess                    main.cjs:2421
       fullLaunch ? missionStandby.take(doc, driverUp, launchAtMs)   main.cjs:2484 / missionStandby.cjs:143
                  : spawn mission.py --fly|--no-fly --params-json
  -> mission.py (standby: SDK, camera, detector prontos; espera "go")  mission.py:854-878
  -> runner.run(): Stage 1..5, loops com ctx.interrupted()             runner.py:99-175
  -> stdout "[STEP N:" -> createStepMarkerParser -> bmg:step-change    main.cjs:2509
  -> stdout "[MILESTONE ...]" -> bmg:milestone                         main.cjs:2506
  -> exit code 0/3/4/outro -> bmg:mission-exit -> missionStateForExit  main.cjs:2535 / missionOutcome.ts

Abortar: AbortControl -> App.land/abort -> bmg:abort-mission          main.cjs:2786
  1. bridge {op:land}: cmd_vel zero + 5x land (aguarda match ate 5 s) command_bridge.py:157-166
  2. publishLandBackstop: ros2 topic pub stop, depois land            missionLifecycle.cjs:130
  3. stopMissionProcess(900): SIGINT unico; SIGKILL aos 900 ms        main.cjs:2699
       mission: EmergencyHandler -> 5x (cmd_vel 0 + land) em <=0.70 s signals.py:198-234, runner.py:221-236
       finalizer: land de novo, cleanup, nectar.shutdown, exit 3
```

Pontos solidos confirmados (nao mexer sem motivo): single-flight em abort/end; um unico SIGINT por processo;
loops de todos os 5 steps checam `ctx.interrupted()` (takeoff 286/398/533, search 134, tracking 173, inspection
149/307/395, rtl 888/1102/1347/1428/1509); `BenchtopDroneProxy` com intertravamento estrutural no-fly;
`flat_trim` exige ACK em voo real (takeoff.py:138-174); bateria pre-arme (takeoff.py:178-215); ceiling e health
na subida; contrato de QoS driver x missao compativel (driver publica odom reliable/10, estados transient_local/1,
camera sensor_data/1; missao assina odom com `qos_profile_sensor_data`, estados com transient_local/1; SDK publica
comandos reliable/1, driver assina comandos com depth 1: sem incompatibilidade).

---

## 3. Achados (ordenados por risco)

Severidade: C = pode deixar a aeronave sem comando de pouso ou voando sem supervisao; A = degrada seguranca;
M = robustez/UX. Cada achado tem fato, consequencia, e o requisito de correcao (R-n) que a secao 5 implementa.

### C1. Nao existe porta de confirmacao de decolagem no voo real
- Fato: `takeoff_confirmed` so alimenta o milestone `mission.takeoff` (`takeoff.py:36-81`). `_stabilize` termina
  com SUCCESS mesmo sem subir ("Continuing", `takeoff.py:~428-434`); `_ascend` sai por stall com SUCCESS
  ("Not a fault", `takeoff.py:614-644`); `_finalize` nao confere altitude nem `flying_state`
  (`takeoff.py:648-692`). `BebopDrone.takeoff` retorna True sempre apos `sleep(3)` (SDK `drone.py`).
- Consequencia: se o firmware recusar a decolagem (bateria, aeronave inclinada, helice travada), a missao percorre
  Stage 2..5 no chao, o touchdown "confirma" e a estacao pode narrar sucesso. Suposicao: o Bebop ignora PCMD em solo.
- R1.

### C2. Sem supervisor do estado de voo durante a missao
- Fato: `ctx.flying_state` (`mission.py:954-964`) e lido so por `_call_takeoff_once_confirmed`. Nenhum
  `evaluate_system_health` consulta o estado (`failsafe.py:~337-367`).
- Consequencia: pouso do firmware (bateria critica interna, perda de Wi-Fi), `emergency` (5), `landing` (4) ou
  `emergency_landing` (8) no meio de um step nao interrompem a missao; ela segue "navegando" uma aeronave no solo.
- R2.

### C3. O driver nao tem watchdog de `cmd_vel`
- Fato: `Bebop::move` chama `setPilotingPCMD` e o libARController reenvia o ultimo comando a cada 50 ms
  (`bebop.cpp:193-226`, comentario na linha 195). O proprio codigo reconhece: "The Bebop latches its last Twist
  forever" (`takeoff.py:712`). Nao ha timeout no `cmdVelCallback` (`bebop_driver_node.cpp:541-550`).
- Consequencia: crash, SIGKILL ou travamento da missao com um Twist nao nulo em curso mantem a velocidade ate
  alguem mandar hover ou land. Agrava C4. Historico registrado: SIGSEGV/SIGABRT no teardown ja ocorreu.
- R7 (driver; exige autorizacao).

### C4. Missao morta com a aeronave no ar nao dispara pouso automatico
- Fato: o handler `close` do processo apenas emite `bmg:mission-exit` e recicla a espera (`main.cjs:2535-2544`);
  `useMissionRuntime.onMissionExit` so toca o cue de falha (`useMissionRuntime.ts:~90-100`). O botao Abortar
  continua habilitado por `abortEnabled` (`flightState.ts`), mas depende de reacao humana.
- Consequencia: saida inesperada (codigo fora de 0/3/4) em voo deixa a aeronave com o ultimo comando ate o operador agir.
- R5.

### A1. Abort e malha aberta
- Fato: a ponte envia 5 lands e o backup CLI uma vez; nada confere `flying_state` depois. A estacao nao reenvia
  se a aeronave continuar em 2/3. `FailsafeSupervisor.trigger_emergency_land` envia um unico par
  `move_velocity(0)+land` (`failsafe.py:~392-399`), enquanto o caminho de SIGINT usa rajada de 5 (`runner.py:221-236`).
- Consequencia: pouso nao confirmado sem retorno claro ao operador; inconsistencia entre os dois caminhos.
- R3, R6.

### A2. Stage 4 e a estabilizacao pos-decolagem sem saude supervisionada
- Fato: `inspection.py:~280` documenta que o loop "never evaluates system health"; `_stabilize`
  (`takeoff.py:397-420`) idem. Hover de `hover_duration_sec` (7 s no store atual), espera do ACK nativo de ate 4 s e
  captura rodam sem checar bateria, odometria nem teto.
- Consequencia: ~10-20 s sem rede de seguranca da missao. Hoje so o failsafe de bateria da estacao (`App.tsx:~472`,
  salta para o Stage 5) cobre bateria.
- R2.

### A3. A trava do "Iniciar Missao" vive so no renderer e nao cobre tudo
- Fato: `startMissionProcess` (`main.cjs:2421-2430`) so recusa missao orfa e bancada pendente. `App.launch` nao
  reavalia o gate no clique (`App.tsx:229`); o gate usa o ultimo render (`preflightGate.ts`). Faltam no gate:
  `flying_state === 0`, piso de bateria acima do limiar de pouso, `states/link`, frescor do video e da odometria
  na hora do clique, `magneto_calibration.required`. O `mission_config.json` atual da estacao guarda `no_fly: true`.
- Consequencia: janela de corrida entre o render e o clique; lancamento real com aeronave em estado inesperado;
  risco de operador voar em bancada achando que e real (e o inverso).
- R4.

### M1. Standby: janela sem handler de sinal e revalidacao ausente no `go`
- Fato: `runner.install_signal_handlers()` so roda em `mission.py:1054`, depois do `go`; SIGINT na janela entre o
  `go` e essa linha cai no handler padrao (KeyboardInterrupt, saida diferente de 3, possivel SIGKILL aos 900 ms).
  A espera pode ter minutos de idade e o `go` nao revalida driver, quadro, odometria nem bateria.
- Consequencia: nada voa nessa janela (a decolagem e no prazo), mas a UI mostra "faulted" e o `go` pode cair em
  estado velho. O Stage 1 so valida bateria, flat trim e z0.
- R4b.

### M2. Caminho `--fly` nunca e exercitado sem hardware
- Fato: em `--no-fly` o simulador substitui odometria, takeoff, land e flying_state (`mission.py:681-686`,
  `796-835`; `proxy.py`). Os 1221 testes e a bancada nao cobrem: assinatura real de `/bebop/odom`, ACK de flat
  trim real, `flying_state` real, comportamento sob latencia/perda, laco fechado dos PIDs contra uma planta.
- Consequencia: o primeiro teste do caminho real seria o voo real.
- R-EMU (secao 4).

---

## 4. Arquitetura proposta

### 4.1 Emulador de driver Bebop (fundacao; habilita verificar tudo o resto sem motores)

Objetivo: executar `mission.py --fly` (proxy real, assinaturas reais, takeoff/land reais do SDK) contra um no que
cumpre o contrato do `ros2_bebop_driver`, em dominio privado, com fisica independente do `KinematicSimulator`
(para nao validar o codigo contra ele mesmo).

Estrutura (arquitetura desacoplada, regra do CLAUDE.md):

```text
test/support/fake_bebop/
  plant.py        fisica pura, sem ROS: dinamica de PCMD -> velocidade/atitude, maquina de estados de voo,
                  bateria, latencia de comando, ruido e deriva de odometria. Testavel sem rclpy.
  scene.py        gera o quadro da camera a partir da pose (ver 4.1.3). Puro numpy/cv2.
  faults.py       injecao de falhas, declarativa (cronograma por tempo ou por evento).
  node.py         adaptador rclpy: no "bebop_driver" no namespace /bebop; publica e assina exatamente o contrato.
scripts/fake_bebop_driver.py   CLI: --domain, --scenario, --fault, --seed, --log-json
```

#### 4.1.1 Contrato (derivar e fixar por teste, como `test_bench_relay.py` ja faz com o relay)
Nome do no: `bebop_driver`, namespace `/bebop` (`driver_discovery.DRIVER_NODE_NAMES`). Tabela a validar contra
`ros2_bebop_driver/src/bebop_driver_node.cpp` por um teste que parseia o fonte:

| Direcao | Topico | Tipo | QoS no driver |
|---|---|---|---|
| assina | takeoff, land, reset, flattrim | Empty | depth 1, reliable |
| assina | cmd_vel | Twist | depth 1 (grupo de callback proprio) |
| assina | move_camera | Vector3 | depth 1 |
| assina | photo, calibrate_magneto, autoflight/navigate_home | Bool | depth 1 |
| assina | flip | UInt8 | depth 1 |
| publica | odom | Odometry | reliable, depth 10 |
| publica | camera/image_raw (+ `/compressed`), camera/camera_info | Image, CameraInfo | sensor_data, depth 1 |
| publica | states/battery | BatteryState | transient_local, depth 1 |
| publica | states/flying_state | UInt8 | transient_local, depth 1 |
| publica | states/altitude, wifi_rssi, gps | Float32, Int16, NavSatFix | transient_local, depth 1 |
| publica | states/flat_trim | UInt32 | reliable, transient_local, depth 1 |
| publica | states/link, states/magneto_calibration | Bool, String | reliable, transient_local |
| publica | states/picture_event | String (JSON) | reliable, depth 10 |

Conferir antes de codar: escala de `BatteryState.percentage` (0-1 ou 0-100) e conteudo do JSON de
`picture_event` lendo `ardrone3_state_callbacks.cpp`/`telemetry_state.cpp`; o emulador reproduz o que o driver faz.

#### 4.1.2 Fisica e maquina de estados
- `flying_state` ARSDK: 0 landed, 1 takingoff, 2 hovering, 3 flying, 4 landing, 5 emergency, 6 usertakeoff,
  7 motor_ramping, 8 emergency_landing. Transicoes: `takeoff` em 0 -> 7 -> 1 -> 2 (~3-4 s, sobe ate ~1 m);
  PCMD com flag roll/pitch -> 3; PCMD nulo -> 2; `land` em 1/2/3 -> 4 -> 0 (descida ~0,4 m/s).
  PCMD e `takeoff` em estado 0/4 sao ignorados (suposicao do firmware; parametrizavel para provar o gate C1).
- Velocidade: PCMD normalizado -> velocidade alvo com ganho que **difere de proposito** de `normalized_to_mps`
  (ex.: eficiencia 0,8 e 1,2 em sorteios diferentes por seed), atraso de primeira ordem (tau 0,25-0,5 s), latencia
  de 100-200 ms, saturacao. Odometria ~5 Hz so com amostra nova (como o driver real), com ruido e deriva lenta.
- Bateria: queda linear parametrizavel; degrau injetavel.
- ACK de flat trim: incrementa `states/flat_trim` em ate 0,5 s se a aeronave esta em solo.
- Foto: responde `picture_event` com atraso de ~2,5 s (medido ao vivo) e sequencias que podem coalescer.

#### 4.1.3 Cena da camera (decisao de custo, ver secao 8)
- v1 (obrigatoria): quadro 856x480 a 30 Hz escolhido por fase da pose: busca (alvo ausente, depois visivel ao
  passar de uma distancia), aproximacao, nadir e marcador ArUco no retorno, usando as imagens que o repositorio ja
  tem (`accident_raw_*.png`, quadro de bancada). Cobre fluxo, tempos e abort. Nao fecha o laco visual.
- v2 (recomendada): renderizador de plano do solo por homografia (pose, tilt do gimbal, intrinsecas de 537 px):
  textura do acidente e um marcador AprilTag 36h11 posicionados no mundo. Fecha o laco do IBVS (Stage 3) e da
  centralizacao fina do RTL (Stage 5) contra uma planta. Se o YOLO nao detectar o recorte projetado de forma
  estavel, cair para v1 e registrar.

#### 4.1.4 Injecao de falhas
`faults.py` aceita cronograma JSON: `takeoff_rejected`, `stuck_on_ground`, `ignore_first_n_lands`, `odom_freeze`,
`video_freeze`, `link_drop`, `battery_step`, `forced_landing{state}`, `flat_trim_no_ack`, `photo_no_ack`,
`high_latency`. Cada falha registra o instante em `--log-json`.

#### 4.1.5 Intertravamentos estruturais do emulador (analogos ao `bench_relay.py`)
1. Recusa iniciar se `ROS_DOMAIN_ID` for 14, ausente ou fora de 80-99 reservado ao emulador.
2. Exige `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`.
3. Na partida, varre o grafo: se ja existe um no `bebop_driver`, sai com codigo 2 (nunca coexiste com outro).
4. Nao importa nem usa nada que abra socket para `192.168.42.x`.
5. O teste de integracao falha se o dominio 14 mostrar qualquer publisher novo em `/bebop/{takeoff,cmd_vel,land}`
   durante a execucao (watchdog do `bench_relay` reutilizado).

#### 4.1.6 Instrumentacao
O emulador grava, com relogio proprio, cada comando recebido (instante, tipo, valor) e a trajetoria. Isso mede
latencia de abort (SIGINT -> primeiro `land`) e o maior intervalo entre comandos por step (insumo do R7).

### 4.2 Camada de seguranca da missao (Python)

```text
FailsafeSupervisor.evaluate_system_health(include_camera: bool = True)
   + checagem de flying_state (R2) via provider injetado: Callable[[], Optional[int]] e instante da ultima leitura
   + variante sem heartbeat de camera para Stage 4 e _stabilize
engine/landing.py (novo, puro + proxy)
   transmit_landing_burst(drone, count, interval_sec, budget_sec)  <- usado por runner e failsafe
   LandingConfirmer(read_state, resend, clock): reenvia land a cada 1 s enquanto o estado for {1,2,3,7},
   encerra em {0,4,5,8}; limite de tempo; sem bloquear o handler de sinal (roda no caminho do failsafe, nao no SIGINT)
steps/takeoff.py
   TakeoffGate: apos takeoff(), espera takeoff_confirmed() ate timeouts.takeoff_confirm_timeout_sec
   falha -> halt, burst de land, alerta, FAILURE
```

### 4.3 Camada da estacao (Electron/React)

```text
electron/launchGuard.cjs (novo, puro)       evaluateLaunchGuard({readiness, telemetry, doc, nowMs}) -> {ok, reasons[]}
electron/deadMissionGuard.cjs (novo, puro)  decideOnMissionExit({code, signal, flyingState, telemetryAgeMs, bench}) -> acao
electron/landSupervisor.cjs (novo)          malha fechada de pouso apos abort/end/guard, emite bmg:land-progress
src/lib/launchGuard.ts                      espelho tipado do guard, usado no clique do renderer
```

Regra de ouro: cada decisao e uma funcao pura testavel em vitest; o `main.cjs` so a invoca.

---

## 5. Requisitos por fase (com criterio de aceite)

Metodo obrigatorio por item: RED (teste que falha pelo motivo certo) -> GREEN -> suite relevante. Antes de cada
item, inspecionar `nectar-sdk` (regra 2 do CLAUDE.md) e registrar o que foi reutilizado. Estilo Black Bee:
docstrings NumPy, validacao defensiva, `Final`/`Optional`, sem emojis, comentarios so para fisica/hardware/IPC.
Parametros novos entram em `parameters.py` de forma aditiva com default, sem quebrar `mission_config.json`
(`test_contracts.py`, `test_parameter_persistence.py`) e, se editaveis, em `parameterSchema.ts`.

### Fase 0 - Check-in e linha de base
- `git status`, `git fetch origin` (sem pull com arvore suja), confirmar HEAD `90de3a0` e a lista de modificados.
- BMG fechado. pytest/vitest/tsc devem reproduzir 1221/473/0. Registrar em `docs/IMPLEMENTACAO_PROGRESSO.md`
  uma secao "Voo real 100%" com o ponto de retomada.

### Fase 1 - Emulador (R-EMU)
Itens: 1.1 `plant.py` + testes puros (transicoes, ganho, atraso, ignorar PCMD em solo); 1.2 teste de contrato que
parseia `bebop_driver_node.cpp` e compara com `node.py`; 1.3 `node.py` + `scripts/fake_bebop_driver.py`;
1.4 intertravamentos 4.1.5 com testes; 1.5 `scene.py` v1 (v2 conforme decisao); 1.6 `faults.py`.
Aceite: `mission.py --fly` completa os 5 stages contra o emulador em dominio 8x com exit 0, sem nenhuma excecao,
e o log do emulador mostra `takeoff` unico, `land` final, zero `cmd_vel` apos o pouso.

### Fase 2 - Porta de decolagem e supervisor de estado de voo (R1, R2)
- R1: `parameters.TimeoutsConfig.takeoff_confirm_timeout_sec` (default proposto 6,0 s; ver secao 8).
  Apos `ctx.drone.takeoff`, o Stage 1 espera `takeoff_confirmed` (estado {1,2} ou altitude acima do minimo). Falha:
  zera comando, rajada de land, alerta CRITICAL falando o motivo real ("decolagem nao confirmada"), FAILURE,
  `em_voo=false`. So em `--fly`; a bancada ja confirma por altitude simulada.
- R2: `flying_state` no `FailsafeSupervisor` com provider injetado e histerese (>= 1,0 s no mesmo estado), ativo
  somente depois de `blackboard.takeoff_committed` e da confirmacao, e **desarmado** quando a propria missao
  comanda pouso (`blackboard.landing_commanded`, setado em `_touchdown`/failsafe). Razoes mapeadas em `spoken_reason`.
  Estados 0/5: aeronave ja no chao -> parar de comandar, nao reenviar land, FAILURE. Estados 4/8: pouso em curso ->
  parar de comandar e sair. Chamar tambem no Stage 4 e no `_stabilize` pela variante `include_camera=False`
  (a camera nao e consumida nesses loops, `inspection.py:~280`).
- Prova: cenarios `takeoff_rejected`, `stuck_on_ground`, `forced_landing{4,5,8}` em cada stage; esperado exit 1,
  nenhum `cmd_vel` nao nulo depois da deteccao, narracao sem "pouso seguro" quando ja estava no solo.
- Aceite adicional: nenhum falso positivo no voo completo emulado e no Stage 5 normal (touchdown comandado).

### Fase 3 - Landing unificado e confirmacao (R3)
- `engine/landing.py`: um unico `transmit_landing_burst` usado por `runner._transmit_emergency_landing`
  e `FailsafeSupervisor.trigger_emergency_land`. O failsafe passa a rajada de 5 e a `LandingConfirmer`.
- O caminho de SIGINT mantem o orcamento de 0,70 s e **nao** espera confirmacao (a estacao confirma, Fase 4).
- Aceite: teste de rajada identico nos dois caminhos; no emulador com `ignore_first_n_lands=3` o failsafe pousa.

### Fase 4 - Estacao: guard de lancamento, missao morta, pouso em malha fechada (R4, R4b, R5, R6)
- R4 `launchGuard`: bloqueia voo real (nao a bancada) se: driver fora; telemetria velha; `connected` falso;
  topicos sem trafego; `states/link` falso; `flying_state` diferente de 0 (ou desconhecido); bateria desconhecida ou
  abaixo de `max(failsafe.thresholdPct + 10, 30)` (proposta, secao 8); odometria/video velhos; calibracao
  magnetica `required`. Aplicado em `startMissionProcess` (processo principal, autoridade final) e reavaliado no
  clique em `App.launch` com `getLinkReadiness` fresco. Motivo exibido no dial. Nunca afrouxa o gate atual.
- R4 armamento explicito: com `no_fly` falso, o lancamento exige confirmacao deliberada (segurar para armar) com
  texto "VOO REAL: motores serao armados"; bancada continua sem ela (decisao na secao 8).
- R4b standby: (a) instalar um handler minimo de SIGINT/SIGTERM durante a espera e a janela pos-`go` que sai com
  codigo 3 sem publicar comando algum quando `takeoff_committed` e falso; (b) revalidacao no `go`
  (driver no grafo, quadro < 1 s, odometria < heartbeat, bateria conhecida em `--fly`); falha sai com novo
  `EXIT_STANDBY_STALE = 6` e a estacao cai para spawn novo, como no codigo 5 (`missionStandby.cjs`).
- R5 `deadMissionGuard`: ao `close` do processo, se nao for bancada e `flying_state` fresco for aereo (apos debounce
  de 300 ms) com codigo fora de {0,3,4}, ou com 0/4 mas ainda aereo apos 5 s: executar o mesmo caminho de abort
  (ponte + backup CLI), emitir alerta `mission.lost` pelo canal de milestones e logar. Sem acao se a telemetria
  estiver velha (nao ha o que confirmar) e sem acao em bancada.
- R6 `landSupervisor`: apos qualquer pouso comandado, a cada 500 ms ate 15 s: se o estado ainda for {1,2,3,7},
  reenviar land pela ponte (limitado a 1 reenvio/s) e, uma unica vez em +3 s, o backup CLI; parar em {0,4,5,8}.
  Emite `bmg:land-progress {phase: commanded|landing|landed|unconfirmed}` que a UI mostra
  ("Pouso comandado", "Pousando", "Pousada", "Pouso nao confirmado: reenviar"). `unconfirmed` reabilita o Abortar
  com texto de reenvio. Estados `aborted` e finish lock permanecem como estao.
- Aceite: vitest cobrindo cada funcao pura; teste com o `main.cjs` real (harness `mainHarness`) para os guards.

### Fase 5 - Matriz de verificacao com o emulador (prova sem motores)
Executar e registrar medicoes (nao basta passar; registrar numeros em `docs/`):

| Cenario | Esperado |
|---|---|
| Voo completo, 3 seeds de planta | exit 0, STEP 1..5 em ordem, sem timeout, sem oscilacao (criterios abaixo) |
| Abort no clique durante a contagem, cada um dos 5 stages, na subida, no hover do Stage 4, na espera do ACK, no touchdown | primeiro `land` recebido; exit 3; zero `cmd_vel` nao nulo apos o primeiro `land`; estacao em `aborted` |
| Abort na janela entre `go` e o primeiro step (standby) | exit 3 sem nenhum comando publicado |
| `takeoff_rejected`, `stuck_on_ground` | FAILURE em <= timeout + 1 s, land enviado, nenhum Stage 2 |
| `forced_landing` 4/5/8 em cada stage | missao encerra em <= 2 s, sem comando novo, alerta correto |
| `odom_freeze`, `video_freeze` por stage | failsafe pousa (inclusive Stage 4 e `_stabilize` para odom) |
| `battery_step` abaixo do limiar por stage | pouso no local; Stage 4 incluso |
| Missao morta (kill -9) com Twist nao nulo | station pousa via R5 em <= 1 s; com o watchdog R7 o emulador mostra hover em <= timeout |
| `ignore_first_n_lands` | R6 reenvia e a aeronave pousa; UI passa por "Pouso nao confirmado" se exceder |
| Standby velho (driver cai e volta antes do go) | exit 6, spawn novo, sem decolar com estado velho |

Medicoes de abort (N >= 20 por stage): latencia clique -> `land` no emulador (alvo p95 < 200 ms por ponte, < 150 ms
SIGINT -> land no processo da missao), SIGINT -> exit (alvo < 900 ms; se exceder, ajustar a graca do SIGKILL
em `stopMissionProcess` e justificar), contagem de `land` entregues.

Criterios de PID (propostos; calibrar com a planta e registrar como decisao):
- Stage 3 e Stage 5 (centralizacao): erro de pixel/metros monotonicamente decrescente apos o transiente; no maximo
  2 inversoes de sinal do comando na janela de assentamento; sem saturacao sustentada > 1 s; variacao de comando por
  ciclo <= limite de jerk configurado; convergencia dentro do `timeout` do step.
- Altitude (Stage 1/2/4): desvio <= `climb_deadband_m` + 0,1 m apos assentar; sem ultrapassar o teto.

### Fase 6 - Watchdog de `cmd_vel` no driver (R7)  [exige autorizacao do usuario]
- Ativar a Fase 6 somente apos Fase 5 medir, por stage, o **maior intervalo entre comandos** da missao
  (instrumentacao 4.1.6). Default do timeout = max(300 ms, 3x o maior intervalo medido) e documentado.
- Logica pura em header proprio (padrao `telemetry_state.hpp`), gtest: se aeronave em {2,3} e nenhum `cmd_vel`
  por `cmd_vel_timeout_ms`, enviar PCMD de hover uma vez e marcar; novo `cmd_vel` desarma. Nao pousa (hover e o
  estado seguro nativo; o pouso e decisao de R5/R6). Excluir estados 1 e 7 (decolagem). Parametro ROS
  `cmd_vel_timeout_ms` (0 desativa). Compilar com `colcon build --symlink-install --packages-select ros2_bebop_driver`
  e rodar os gtests. O emulador implementa a mesma regra para provar a integracao missao x watchdog.
- Esta fase mexe em repositorio com modificacoes locais ja existentes: trabalhar por cima delas, sem commit.

### Fase 7 - E2E pelo Electron real contra o emulador
Harness que sobe o `main.cjs` real (como a medicao de dominio 97 do progresso) com `ROS_DOMAIN_ID` privado e o
emulador no lugar do driver; sem `bmg:ensure-link`, sem `nmcli`, sem `make driver-bebop`. Fluxo: salvar parametros,
esperar standby pronta, `start-mission` em modo `--fly`, observar `bmg:step-change` 1..5, `bmg:milestone`,
telemetria refletindo estado real do emulador, `bmg:mission-exit`; repetir com abort em cada stage e com as falhas
de R5/R6. Aceite: nenhum processo `mission.py`/bridge orfao ao final; `git status` sem arquivos soltos.

### Fase 8 - Documentacao e entrega
- Atualizar `docs/IMPLEMENTACAO_PROGRESSO.md` (itens, decisoes no formato `Ruling:`).
- Criar `docs/CHECKLIST_VOO_REAL.md` (operador): pre-voo, o que a estacao bloqueia sozinha, procedimento de abort,
  o que fazer em `Pouso nao confirmado`, e a lista de verificacoes que **so** a aeronave prova (secao 7), cada uma
  com um procedimento que nao arma motor quando possivel.
- Suites finais (pytest, vitest, tsc, `npm run build`, `colcon build --symlink-install --packages-select
  mvp_mission_bebop`). Reportar numeros. Listar arquivos alterados e propor commits; **nao executar**.

---

## 6. Ordem e dependencias

```text
Fase 0 -> Fase 1 (emulador) -> Fase 2 -> Fase 3 -> Fase 4 -> Fase 5 -> Fase 7 -> Fase 8
                                  \--------------------------^
Fase 6 (driver) depende da Fase 5 (medicao) e de autorizacao; pode ser feita apos a Fase 5 e antes da 7.
```

Cada fase termina com suites verdes e atualizacao do arquivo de progresso. Nao iniciar a seguinte com RED aberto.

---

## 7. O que nao se prova sem a aeronave (registrar no checklist, nao inventar)

Todos "Suposicao" ate medir:
1. Se o firmware ignora PCMD em solo e durante `landing` (base do achado C1 e da ordem do abort).
2. Se um `land` durante `takingoff` (1) ou `motor_ramping` (7) e honrado.
3. Comportamento do firmware com perda de Wi-Fi (auto-pouso/retorno) e a latencia real de `flying_state`.
4. Deriva do odometro por fluxo optico em piso real e a fidelidade do `normalized_to_mps` (calibracao).
5. Calibracao magnetica e estabilizacao da camera no enquadramento da calibracao intrinseca.
6. A taxa real de entrega de `cmd_vel` nos stages (para o default do watchdog).
Verificacoes de bancada com a aeronave ligada **sem motores**, apenas com autorizacao e no dominio do driver:
`flying_state == 0`, ACK de flat trim, gimbal, foto/`picture_event`, derrubar o Wi-Fi e ver `states/link` falso e a
estacao bloquear o lancamento. Proibido: takeoff, cmd_vel nao nulo e land.

---

## 8. Decisoes pendentes do usuario (com padrao recomendado se nao houver resposta)

| # | Decisao | Padrao recomendado |
|---|---|---|
| D1 | Piso de bateria para liberar o lancamento real | `max(limiar do failsafe da estacao + 10, 30)` por cento |
| D2 | Timeout de confirmacao de decolagem | 6,0 s |
| D3 | Confirmacao explicita de armamento ("segurar para armar") no voo real | Sim |
| D4 | Cena do emulador: v1 apenas, ou v1 + v2 (renderizador de plano do solo) | v1 + v2, com v1 como fallback |
| D5 | Autorizar a Fase 6 (watchdog no driver, repo com alteracoes locais) e o comportamento (hover, nao pouso) | Sim, hover |
| D6 | Se `forced_landing`/`emergency` detectado deve tambem cancelar a fala "pouso seguro" | Sim |

Se o Claude Code precisar de uma decisao para seguir, adotar o padrao, registrar como `Ruling:` e continuar;
parar e perguntar apenas em D5 (autorizacao de repositorio) e em qualquer coisa que toque a aeronave real.

---

## 9. Prompt de partida (colar no Claude Code, na raiz do monorepo)

```text
Voce vai implementar o plano de /home/jv/ros2_ws/src/mvp_mission_bebop/docs/PROMPT_IMPLEMENTACAO_VOO_REAL_100.md.
Leia esse arquivo inteiro, depois CLAUDE.md, docs/IMPLEMENTACAO_PROGRESSO.md e
docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md, antes de qualquer edicao.

Regras inegociaveis:
1. Voce NUNCA arma motores nem comanda a aeronave real. Nada de takeoff, cmd_vel nao nulo ou land no dominio ROS 14
   ou em 192.168.42.1; nada de make driver-bebop. Todo teste de --fly roda contra o emulador (Fase 1) em dominio
   privado, com os intertravamentos da secao 4.1.5.
2. Sem git commit, git push ou git pull com a arvore suja. No fim, liste os arquivos alterados e proponha a divisao
   em commits Conventional Commits, sem executar.
3. Siga o CLAUDE.md: analise previa do nectar-sdk antes de cada implementacao, estilo Black Bee (docstrings NumPy,
   tipagem estrita, validacao defensiva), sem emojis, comentarios so para fisica/hardware/IPC, contrato
   [STEP N:] e mission_config.json preservados (mudancas de esquema so aditivas).
4. TDD: cada item comeca por um teste RED que falha pelo motivo certo, depois GREEN, depois a suite relevante.
   Rode as suites com o BMG fechado. Linha de base: pytest 1221, vitest 473 (55 arquivos), tsc 0.
5. Nao reverta nem reformate as modificacoes locais ja existentes (monorepo, ros2_bebop_driver, nectar-sdk).

Execute as Fases 0 a 8 na ordem da secao 6. Ao fim de cada fase: suites verdes, atualize
docs/IMPLEMENTACAO_PROGRESSO.md (itens e Ruling:), e informe os numeros medidos. Para decisoes da secao 8, adote o
padrao recomendado e registre. Pare e pergunte apenas na Fase 6 (autorizacao do repositorio do driver) e se algo exigir
tocar a aeronave real. Comece pela Fase 0 e relate a linha de base antes de seguir.
```
