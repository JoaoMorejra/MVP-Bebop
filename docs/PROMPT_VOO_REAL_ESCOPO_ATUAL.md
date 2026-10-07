# Prompt Mestre (escopo atual): --fly de ponta a ponta, voz e botao de land

Documento de analise e prompt de execucao para o Claude Code (terminal). Substitui o escopo de
`docs/PROMPT_IMPLEMENTACAO_VOO_REAL_100.md` onde houver conflito; dele valem apenas a secao 4.1 (emulador de driver)
e o estilo de fases. Origem: auditoria somente leitura de 2026-10-01 (sem drone na rede, sem motores, sem alteracao
de codigo).

## Escopo definido pelo operador

| # | Ponto | Dentro | Fora (adiado) |
|---|---|---|---|
| P1 | Iniciar em `--fly` roda de ponta a ponta | Cadeia clique -> processos -> armamento -> 5 steps; saber **quando a aeronave realmente decolou**; botoes Iniciar e Land cumprem suas funcoes | Gravador de voo / gravador de telemetria (nao criar) |
| P2 | Voz do copiloto | Verificar que as falas existentes cobrem o voo real e a pericia pos-land; sem sobreposicao; verdade do evento; sem atraso; **unica fala nova: o botao Abortar**. Internet disponivel o tempo todo | Novas falas alem do abort; voz offline/local |
| P3 | Land | **Somente o botao de land** (Abortar) | Perda de Wi-Fi, aeronave ociosa, missao morta/travada, watchdog no driver, supervisor de estado em voo (ver secao 9) |

---

## 0. Premissa honesta

1. **Armamento real nao se prova sem armar.** Prova-se a cadeia de comando: `mission.py --fly` publica
   `/bebop/takeoff` exatamente uma vez, no instante certo, depois das portas de seguranca, e nunca em `--no-fly`; o
   emulador (dominio privado) recebe e responde. A reacao do firmware fica como Suposicao no checklist.
2. Nunca declarar "pronto para voo". Declarar "cadeia verificada em emulador, com os numeros X; pendencias de
   aeronave: lista (secao 8)".
3. Itens adiados (secao 9) continuam sendo riscos de voo real. O operador os assumiu conscientemente; o relatorio
   final deve lista-los, sem suaviza-los.

## 1. Proibicoes absolutas

1. **Proibido armar motores.** Nenhum `takeoff`, `cmd_vel` nao nulo ou `land` no dominio ROS 14 nem em
   `192.168.42.1`; proibido `make driver-bebop`; proibido `mission.py --fly` contra o driver real.
2. Todo teste `--fly` roda no **emulador de driver** (`PROMPT_IMPLEMENTACAO_VOO_REAL_100.md` secao 4.1) em dominio 80
   a 99 com `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, com os intertravamentos 4.1.5 (recusa dominio 14; recusa se ja
   existir no `bebop_driver`; watchdog de novos publishers em `/bebop/{takeoff,cmd_vel,land}` no dominio 14).
3. Sem `git commit`, `git push` ou `git pull` com a arvore suja (ha trabalho nao commitado do lancamento
   instantaneo). No fim, listar arquivos e propor commits Conventional Commits, sem executar.
4. Nao reverter nem reformatar modificacoes locais no monorepo, em `ros2_bebop_driver` e em `nectar-sdk`. **Nao
   alterar o driver nesta rodada.**
5. Suites com o BMG fechado. Base medida em 2026-10-01: pytest 1221, vitest 473 (55 arquivos), tsc 0.

---

## 2. P1 - Cadeia do clique ao armamento e aos 5 steps (fato, lida no codigo)

```text
Bancada OFF -> doc.no_fly=false -> App.launch (App.tsx:229) -> options.noFly=false
 -> startMissionProcess (main.cjs:2421): args --fly (2469), launchDoc.no_fly=false (2475)
 -> missionStandby.take: reaproveita a espera so se a chave bater (no_fly e demais CRITICAL_PATHS), senao spawn novo
 -> mission.py: resolve_parameters: --fly => no_fly=False (mission.py:444); no_fly nunca e persistido (334-350)
 -> BenchtopDroneProxy(no_fly=False): drone cru, odometria real de /bebop/odom (mission.py:797)
 -> Stage 1: prearm bateria (takeoff.py:178) -> gimbal -> flat trim COM ACK (138) -> z0 (219) -> contagem ate o prazo
    absoluto click+countdown (247) -> ctx.drone.takeoff (334) -> SDK publica /bebop/takeoff e dorme 3 s, retorna True
```

O `mission_config.json` da estacao guarda `no_fly: true`; o voo real exige a Bancada desligada.

### 2.1 Lacunas que quebram P1

| # | Lacuna | Evidencia | Requisito |
|---|---|---|---|
| G1 | **Nao ha como saber que a aeronave decolou**: `takeoff_confirmed` so alimenta o milestone; `_stabilize` e `_ascend` terminam em SUCCESS sem subir; `BebopDrone.takeoff` retorna True sempre apos `sleep(3)` | `takeoff.py:36-81,397-420,614-644`; SDK `drone.py` | R1, R12 |
| G2 | Gate de pre-voo so no renderer; nao reavalia no clique; faltam estado em solo, piso de bateria, link, frescor | `PreflightScreen.tsx:112`, `main.cjs:2421-2430`, `App.tsx:229`, `preflightGate.ts` | R4, R8 |
| G3 | Standby: SIGINT entre o `go` e `runner.install_signal_handlers()` cai no handler padrao; o `go` nao revalida o estado | `mission.py:855-878,1054` | R4b |
| G4 | O caminho `--fly` nunca roda em teste: o simulador substitui odometria, takeoff, land e estado | `mission.py:681-686,796-835` | Emulador |

### 2.2 R12 - Decolagem real, medida pela aeronave

Linha do tempo mantida no blackboard da missao e escrita no log (`logger.info` por instante, linhas `[TIMING]`);
o payload de `mission.takeoff` apenas ganha os instantes, sem nova fala e sem arquivo de gravacao:

| Instante | Definicao | Fonte |
|---|---|---|
| `t_click` | clique do operador | renderer (`launchAtMs`) |
| `t_takeoff_cmd` | publicacao de `/bebop/takeoff` | missao (proxy), com relogio monotonico e de parede |
| `t_takeoff_started` | primeira transicao de `flying_state` para 7 (motor_ramping) ou 1 (takingoff) apos o comando | aeronave (`states/flying_state`) |
| `t_takeoff_confirmed` | `flying_state` em {1,2} **ou** altitude relativa > `takeoff_settle_min_altitude_m` | aeronave/odometria (`takeoff_confirmed`) |
| `t_hover` | `flying_state == 2` estavel e subida concluida | aeronave |
| `t_land_cmd` | primeira publicacao de `/bebop/land` (missao, ponte ou CLI) | missao/estacao (log) |
| `t_touchdown` | `flying_state == 0` apos `landing` (4) | aeronave |

Regra: **a missao so avanca do Stage 1 para o Stage 2 depois de `t_takeoff_confirmed`.** Detalhes em R1.

### 2.3 R1 - Porta de confirmacao de decolagem (somente `--fly`)

- Novo `timeouts.takeoff_confirm_timeout_sec` (padrao 6,0 s; aditivo em `parameters.py`).
- Apos `ctx.drone.takeoff`, esperar `takeoff_confirmed(...)` com leitura **fresca** de `flying_state` (a leitura
  anterior ao comando nao vale: usar a primeira amostra com carimbo posterior a `t_takeoff_cmd`).
- Nao confirmou no prazo: zerar comando, rajada de land, alerta CRITICAL ja existente "Falha na decolagem" (detalhe: decolagem
  nao confirmada pela aeronave; sem frase nova), FAILURE, `em_voo=false`; Stage 2 nunca inicia; nenhuma fala de varredura.
- Confirmou: grava `t_takeoff_confirmed`, emite `mission.takeoff` (verdadeiro) e segue.
- Bancada (`--no-fly`) mantem a confirmacao por altitude simulada, sem regressao.

### 2.4 R4 / R8 - Botao Iniciar cumpre sua funcao: veredito unico de prontidao

Funcao pura `evaluateLaunchReadiness` no processo principal; o botao reflete em tempo real e o clique **reavalia**
com leitura fresca. So libera o voo real se todos forem verdadeiros, cada um com motivo textual no dial:

- driver no ar; `states/link` verdadeiro; telemetria < 1,5 s; odometria e video frescos; topicos obrigatorios
  recebendo (`REQUIRED_TOPICS`, `main.cjs:1349`);
- `flying_state === 0`; bateria conhecida e >= `max(limiar do failsafe da estacao + 10, 30)` (D1);
  calibracao magnetica nao `required`;
- ponte de comando `ready` (`createBridgeGate`) e `ros2` CLI de backup disponivel;
- **voz pronta**: daemon vivo, dispositivo de audio abre, backend de voz alcancavel (online) - ver P2;
- sem missao orfa; `mission_config.json` valido; `no_fly` coerente com o modo;
- espera `[STANDBY] ready` com chave igual: se nao houver, aviso "contagem comeca no clique", nao bloqueia.

Armamento explicito: com `no_fly` falso, o lancamento exige gesto deliberado (segurar para armar) com o texto
"VOO REAL: motores serao armados". Bancada nao exige. O dial ja muda de cor por modo; manter.
**Funcao do botao Iniciar, em uma frase testavel:** com a prontidao verdadeira inicia exatamente uma missao no modo
exibido; com qualquer item falso nao inicia nada e diz o motivo; clique duplo nunca inicia duas.

### 2.5 R4b - Janela do standby
(a) Handler minimo de SIGINT/SIGTERM durante a espera e entre o `go` e o `install_signal_handlers`: sai com codigo 3,
sem publicar comando algum quando `takeoff_committed` e falso. (b) Revalidacao no `go`: driver no grafo, quadro
< 1 s, odometria dentro do heartbeat, bateria conhecida em `--fly`, `flying_state == 0`; falha sai com
`EXIT_STANDBY_STALE = 6` e a estacao cai para spawn novo (como no codigo 5 de `missionStandby.cjs`).

### 2.6 Aceite de P1 (emulador; `test/test_fly_path_emulated.py`)

1. `--fly` completo, 3 seeds de planta: `takeoff` unico; ordem exata: bateria lida, flat trim + ACK, z0, prazo
   click+countdown (erro < 0,3 s), `takeoff`, estados 7/1/2, `t_takeoff_confirmed`, subida, STEP 1..5, `land`
   final, zero `cmd_vel` apos o pouso, exit 0; instantes de R12 coerentes com a linha do tempo da planta (< 50 ms).
2. `--no-fly` no mesmo cenario: zero `takeoff` e zero `cmd_vel` nao nulo.
3. `takeoff_rejected` e `stuck_on_ground`: nenhum Stage 2; land; exit 1; sem fala de varredura.
4. Standby com `no_fly` divergente: exit 5 -> spawn novo -> `--fly` correto. Standby velho: exit 6.
5. Botao Iniciar: tabela de bloqueios (cada item falso bloqueia com o motivo certo), reavaliacao no clique,
   clique duplo, armamento explicito (vitest + teste com o `main.cjs` real).
6. Convergencia dos PIDs (secao 7).

---

## 3. P2 - Voz do copiloto: cobertura do voo real e da pericia pos-land

Arquitetura (fato): milestones e alertas saem em stdout da missao; `milestones.cjs` -> `bmg:milestone` -> fila unica
no renderer (`narrationQueue.ts`) -> daemon unico de fala (`main.cjs ensureSpeechProcess`) -> Gemini Live
(`announcer.py:54,688,729`) -> `RawOutputStream`. Sob `BMG_GCS_SESSION=1` a missao nao fala sozinha. Internet e
premissa; a falha de rede nao e requisito de projeto, mas **nao pode travar o controle nem a fila**.

### 3.1 Cobertura: as falas existentes bastam; a unica fala nova e o abort

Decisao do operador: nao adicionar falas. Cobertas hoje (fato): inicio, contagem (`countdown_3`), decolagem
(`mission.takeoff`), inicio da varredura, alvo encontrado, aproximacao, registro concluido, inicio do retorno, pouso
(`landing`), toque (`touchdown`, com variantes "no local" e "nao confirmado"), bateria baixa, alertas de falha e o
laudo pos-land. A tarefa nao e criar frases, e **provar que as existentes disparam no instante verdadeiro, uma de
cada vez** (matriz verdade 3.4). Os `_announce` do Python descartados sob a estacao (por exemplo "Decolagem
concluida", "Marcador da base localizado") continuam descartados: nao promover a milestone.

A unica fala nova: **botao Abortar** (V1). A decolagem nao confirmada (R1) reutiliza o alerta CRITICAL "Falha na
decolagem" que o codigo ja possui (`takeoff.py:345-353`), hoje inalcancavel em `--fly`; nao e frase nova.

### 3.2 Achados e requisitos de voz

| # | Achado (fato) | Requisito |
|---|---|---|
| V1 | **Abortar/land/Finalizar com a missao rodando nao fala nada**: `signals.py:198-234` nao anuncia; o alerta de `runner.py:141-143` nao e alcancado (SystemExit passa por ele); `main.cjs:2870-2885` so fala se `!hadMission`; sons desligados em `cues.ts:22-30` | A estacao enfileira `mission.abort` (classe LAND, URGENT) em **todo** abort, com ou sem processo; sem duplicar o da missao (dedupe por chave). Reutilizar a frase existente "Missao abortada. Pouso imediato comandado." (`main.cjs:2879`); com a aeronave comprovadamente em solo, dizer so "Missao abortada." (sem afirmar pouso) |
| V3 | `mission.scan_start` sai sem decolagem confirmada (`search.py:92` incondicional; renderer nao exige `mission.takeoff`). Nao e fala nova, e condicao de verdade | Com R1 o Stage 2 nao inicia sem decolagem confirmada; alem disso emitir so com `blackboard.takeoff_complete` e o renderer descarta `scan_start` sem `mission.takeoff` previo |
| V5 | Daemon de fala morre: sem reinicio proativo; escrita em stdin morto pode virar EPIPE nao tratado no processo principal (Suposicao) | `child.stdin.on('error')`, filtro de EPIPE em `uncaughtException`, reinicio proativo no `close`, re-aquecimento antes da proxima fala; teste que mata o daemon no meio de uma fala |
| V6 | Conexao ao backend sem limite; a fila inteira, alertas incluidos, espera; `warm_cache` disputa a sessao (`announcer.py:689,730,873-897`) | `wait_for` (3 s) no connect; `warm_cache` cede ao primeiro alerta e e cancelavel; alerta nunca espera preenchimento de cache |
| V7 | Falha do dispositivo de audio e silenciosa e reportada como sucesso: `spoke` vem dos bytes sintetizados (`announcer.py:254,363-366,1002`) | `spoke`/`heard` so apos escrita bem-sucedida no stream; reabrir 1 vez; WARNING e estado em `speech.ready` |
| V8 | Alerta nao corta alerta (pior caso ~14 s + 14 s); fila sem TTL; milestone descartado nunca e dito depois (`narrationQueue.ts:138-151`, `useCopilot.ts:249-253`) | Classes LAND > URGENT > normal > forense; LAND corta qualquer fala; TTL por chave (fala de stage N descartada se o stage ja avancou); nenhuma fala normal inicia com alerta pendente |
| V9 | `bmg-announcer.lock` so grava o pid (nao exclusivo), `announcer.py:1189-1197` | `flock` ou validacao de `/proc/<pid>/cmdline` |
| V10 | Frases de alerta dinamicas (failsafe, "Falha na etapa/decolagem") nao estao no cache: sintese ao vivo (1,6-3,2 s) | Pre-sintetizar no aquecimento a frase do abort e as fixas de seguranca ja existentes, para latencia, **nao** para uso offline |
| V0 | Nao ha verificacao de voz no pre-voo | `speech.ready` no veredito R8: daemon vivo, audio abre, backend alcancavel (chamada curta com timeout) |

### 3.3 Pericia pos-land (laudo falado) - mapeamento e requisitos

Fato (`App.tsx:159-165,543-547`, `lib/forensics.ts`, `finishLock.ts:97`): o laudo e **sorteado** por voo (decisao D1:
quatro conclusoes fixas "sem necessidade de policia / Samu / vitima nao grave / veiculo sem danos", ordem e
redacao variaveis, sem analise de imagem). Ele so e **desenhado** quando chega a evidencia (`mission.latestCapture`)
e so **comeca** quando `reportMayStart(finishLock)` e verdadeiro, isto e, a aeronave esta em solo confirmado
(`flying_state == 0` continuo >= 2 s, telemetria < 1,5 s, processo encerrado). A fila usa o grupo `forensic` e
descarta o laudo por Finalizar ou alerta.

Requisitos:
- **F1 - Cobertura do voo real.** Teste ponta a ponta com o emulador (`--fly`, alvo capturado): depois do pouso, a
  sequencia e: `mission.touchdown` -> "Aeronave em solo" -> as 4 conclusoes em ordem sorteada -> encerramento, sem
  sobreposicao, sem laudo antes do solo, sem laudo se nao houve captura.
- **F2 - Abort apos captura.** Se houve captura e o operador pousa por abort, o laudo ocorre quando em solo, sem
  cortar a fala `mission.abort`. Sem captura, nenhum laudo.
- **F3 - Sem fala nova.** O laudo nao ganha frases novas. Sem captura, continua sem laudo (comportamento atual, a preservar por teste).
- **F4 - Convivencia com o download das fotos nativas** (`nativePhotos.cjs`, apos `mission.touchdown`): o laudo nao
  espera o download e o download nao muda o instante de inicio; o evento de foto nativa nao gera fala concorrente.
- **F5 - Latencia.** Inicio do laudo <= 1,5 s apos a confirmacao de solo; cada conclusao usa a frase pre-sintetizada
  (`allFindingSentences`), pausa entre conclusoes definida pelo fim real da fala.
- **F6 - Ordem registrada em log.** O sorteio (ordem e redacao) ja e logado pelas linhas `[SPEECH]`/`[SPEECH_DONE]`; sem arquivo novo.

### 3.4 Matriz verdade e orcamento de latencia

Para cada milestone/alerta, teste que compara o instante da fala com a verdade da planta no emulador. Obrigatorios:
`mission.takeoff` apos `flying_state` em {1,2}; `scan_start` apos takeoff completo; `target_found` apos confirmacao
por histerese; `capture_done` apenas com `record.captured`; `landing` antes do primeiro `land`; `touchdown`
confirmado apenas com estado 0 e `confirmed`; nenhuma fala de "pouso seguro" com a aeronave ja no solo; nenhuma fala
de stage N depois do inicio de N+1; laudo so em solo. Para a sintese, o harness injeta um **sintetizador falso
deterministico** (duracao por caractere) e um **dispositivo de audio falso** que registra buffers; assim mede-se
sobreposicao e ordem sem depender da rede. A latencia contra o Gemini real continua em
`scripts/bench_speech_latency.py` (manual, com internet) e entra no checklist.

| Caso | Limite (inicio do audio a partir do evento/linha de log), N >= 20 |
|---|---|
| `mission.abort` (LAND) com cache | p95 <= 1,0 s; corte da fala em curso < 150 ms |
| Alerta de falha de voo | p95 <= 3,2 s (sintese ao vivo) ou <= 1,0 s se fixa em cache |
| Fala normal em cache | <= 0,5 s |
| Qualquer fala | teto 14 s; nunca bloqueia o laco de controle |
| Sobreposicao de audio | 0 em todo o voo emulado completo, incluindo o laudo |

---

## 4. P3 - Land: somente o botao Abortar

### 4.1 O que ja funciona (fato; nao regredir)
Dispara no `pointer-down` (`AbortControl.tsx`), sem espera; habilitado com missao rodando OU aeronave no ar
(`abortEnabled`); `singleFlight`; tres caminhos de pouso: ponte residente (cmd_vel zero + 5x land, espera o match ate
5 s), backup CLI stop->land, SIGINT da missao (orcamento 0,70 s, rajada de 5); um SIGINT por processo.

### 4.2 Lacunas do botao

| # | Lacuna | Evidencia | Requisito |
|---|---|---|---|
| L1 | Malha aberta: nada confere `flying_state` apos o land nem reenvia se a aeronave continua em 2/3 | `main.cjs:2786-2827` | R6 |
| L2 | Depois do primeiro clique o botao fica desabilitado (`busy`) mesmo se o pouso nao se confirmar | `AbortControl.tsx` (`busy`) | R6 |
| L3 | Abort nao fala com a missao rodando | ver V1 | V1 |
| L4 | Sem atalho de teclado global | `AbortControl.tsx` so no botao | R11 |
| L5 | Janela go -> primeiro step sem handler (UI mostra `faulted`, nao `aborted`) | `mission.py:855-878,1054` | R4b |
| L6 | Estados de pouso nao aparecem na UI (apenas rotulo de `flying_state`) | `flightState.ts`, `CockpitScreen` | R6 |

### 4.3 Requisitos

- **R6 - Supervisor de pouso da estacao** (`electron/landSupervisor.cjs`, funcao pura de decisao + executor): apos
  qualquer pouso comandado (abort, Finalizar com aeronave no ar), a cada 500 ms por ate 15 s: se `flying_state`
  ainda for {1,2,3,7}, reenviar land pela ponte (<= 1/s) e uma unica vez o backup CLI em +3 s; parar em {0,4,5,8}.
  Emite `bmg:land-progress {phase: commanded|landing|landed|unconfirmed, t}`, somente para a UI e o log; **sem fala nova** alem do abort.
  UI: "Pouso comandado", "Pousando", "Pousada", "Pouso nao confirmado: reenviar". **Em `unconfirmed` o botao volta a
  ser habilitado** com o rotulo "REENVIAR POUSO" (L2).
- **R11 - Atalho global.** `Escape` (D8) dispara o mesmo `onAbort` em qualquer tela do cockpit quando
  `abortEnabled`, sem confirmacao; ignorado dentro de campo de texto e do terminal embutido.
- **V1** (voz do abort) e **R4b** (janela do standby), como acima.
- **Funcao do botao Land, em uma frase testavel:** em qualquer instante em que haja missao ou aeronave no ar, um
  toque publica o pouso (zero `cmd_vel` e `land`) uma vez por caminho, para a missao sem segundo SIGINT, fala, mostra
  o progresso e so se considera cumprido com `flying_state` em solo; nunca deixa a missao comandando depois do land.

### 4.4 Aceite do botao no emulador (N >= 20 por caso; registrar numeros)

| Cenario | Esperado |
|---|---|
| Abort na contagem, na subida, no hover do Stage 1, nos Stages 2, 3, 4 (hover e espera do ACK), 5 (busca, centralizacao, descida) e no toque | primeiro `land` recebido; exit 3; **zero `cmd_vel` nao nulo depois do primeiro `land`**; estacao `aborted`; fala `mission.abort`; progresso ate "Pousada" |
| Abort entre o clique e o primeiro step (spawn/standby) | exit 3, zero comandos publicados |
| Clique duplo / Escape + clique | um unico SIGINT, rajada unica por caminho, sem `os._exit(130)` |
| Abort sem processo de missao (aeronave no ar, processo ausente) | land pela ponte; supervisor ate solo |
| Ponte indisponivel | backup CLI entrega o land |
| `ignore_first_n_lands=3` | R6 reenvia e pousa; para ao confirmar; nao reenvia sem fim |
| Land nao confirmado em 15 s (`land` ignorado) | fase `unconfirmed` e botao "REENVIAR POUSO" habilitado (aviso visual, sem fala nova) |
| Latencias | clique -> primeiro `land` no emulador p95 < 200 ms (ponte); SIGINT -> `land` p95 < 150 ms; SIGINT -> saida < 900 ms (se exceder, ajustar a graca do SIGKILL de `stopMissionProcess` e justificar) |
| Abort apos captura | laudo so em solo (F2) |

---

## 5. Emulador (pre-requisito)

`PROMPT_IMPLEMENTACAO_VOO_REAL_100.md` secoes 4.1.1 a 4.1.6. Falhas necessarias nesta rodada: `takeoff_rejected`,
`stuck_on_ground`, `ignore_first_n_lands`, `flat_trim_no_ack`, `photo_no_ack`, `battery_step` (fala de bateria),
`forced_landing` (pouso do firmware, para validar a UI de progresso). Fora do escopo agora: `link_drop`,
`controller_hang`, `odom_freeze` com acao. O emulador publica os mesmos topicos do contrato e registra cada comando com
relogio proprio, que serve de verdade-terreno para as medicoes dos testes.

## 6. Fases (cada uma termina com suites verdes e progresso atualizado)

| Fase | Conteudo | Aceite |
|---|---|---|
| 0 | `git status`, `git fetch` (sem pull), HEAD `90de3a0`, reproduzir 1221/473/0 | numeros iguais |
| 1 | Emulador + `test_fly_path_emulated.py` (itens 1 e 2 de 2.6) | `--fly` completo exit 0; `takeoff` unico |
| 2 | R1, R12 (linha do tempo), R4b | itens 3 e 4 de 2.6 |
| 3 | Instantes de R12 no blackboard/log e no payload de `mission.takeoff` | instantes batem com a planta (< 50 ms) |
| 4 | R6, R11, V1, UI de progresso de pouso | secao 4.4 verde com numeros |
| 5 | Voz: V1 (abort), V3, V5 a V10, V0, laudo F1 a F6, matriz verdade (3.4) | secao 3.4 verde; sobreposicao 0 |
| 6 | R4/R8: veredito de prontidao, reavaliacao no clique, armamento explicito | item 5 de 2.6 |
| 7 | Medicoes completas e PIDs (secao 7); numeros em `docs/` | tabela de numeros |
| 8 | E2E com o `main.cjs` real + emulador (dominio privado; sem `ensure-link`, `nmcli`, `make driver-bebop`): clique -> 5 steps na UI -> pouso -> laudo -> Finalizar; aborts | sem processos orfaos; `git status` sem arquivos soltos |
| 9 | `docs/CHECKLIST_VOO_REAL.md`, `IMPLEMENTACAO_PROGRESSO.md`, suites finais, `npm run build`, `colcon build --symlink-install --packages-select mvp_mission_bebop`; commits propostos, nao executados | numeros reportados |

## 7. Convergencia dos PIDs (calibrar com a planta e registrar como `Ruling:`)
Stage 3 e centralizacao do Stage 5: erro monotonicamente decrescente apos o transiente; <= 2 inversoes de sinal do
comando na janela de assentamento; sem saturacao sustentada > 1 s; variacao por ciclo <= limite de jerk; converge
dentro do `timeout` do step. Altitude (Stages 1, 2, 4): desvio <= `climb_deadband_m` + 0,1 m apos assentar e nunca
acima do teto. Falha e bug a corrigir ou limite a justificar; nao relaxar sem registro.

## 8. O que nenhum teste sem a aeronave prova (vai para `CHECKLIST_VOO_REAL.md`)
Suposicoes a medir no primeiro voo curto controlado: (1) o firmware ignora PCMD em solo e durante o pouso;
(2) `land` durante `takingoff`/`motor_ramping` e honrado; (3) fidelidade da dinamica do emulador; (4) deriva do
odometro por fluxo optico no piso real e `normalized_to_mps`; (5) calibracao magnetica e enquadramento da camera;
(6) latencia real da voz online com o uplink do tethering; (7) taxa real de entrega de `cmd_vel`; (8) o tempo real
entre `takeoff` e `flying_state` 1/2 (calibra o `takeoff_confirm_timeout_sec`).
Bancada com a aeronave ligada **sem motores** (com autorizacao, no dominio do driver): `flying_state == 0`, ACK de
flat trim, gimbal e foto. Proibido: takeoff, `cmd_vel` nao
nulo, land.

## 9. Adiado por decisao do operador (nao implementar agora; registrar como riscos abertos)
| Item | Risco que permanece |
|---|---|
| Perda de Wi-Fi: assinatura de `states/link`, `flying_state == 255`, reacao da estacao, banner | Link perdido so e visto pela odometria (3 s); a missao nao reage imediatamente |
| Aeronave ociosa ou missao travada: heartbeat do laco de controle | Missao viva e travada nao dispara land |
| Missao morta em voo: pouso automatico da estacao | Saida inesperada em voo exige o operador apertar Abortar |
| Supervisor de `flying_state` durante a missao; saude no Stage 4 e na estabilizacao | Pouso/emergencia do firmware no meio de um step nao interrompe; Stage 4 sem rede de seguranca |
| Rajada e confirmacao de pouso unificadas no failsafe automatico | Failsafe envia um unico par `cmd_vel` zero + `land` |
| Driver: watchdog de `cmd_vel`, reconexao ARSDK, `ReturnHomeOnDisconnect` | O ultimo `cmd_vel` fica travado ate novo comando |
| Voz offline | Sem internet, alertas dinamicos ficam mudos |
| Gravador de voo/telemetria | Nao havera registro persistente do voo; so logs de stdout e a trilha em memoria da tela |
| Novas falas (decolagem concluida, marcador localizado etc.) | Essas etapas continuam sem anuncio |
O relatorio final deve repetir esta tabela como "riscos aceitos para o primeiro voo".

## 10. Decisoes (adotar o padrao e registrar como `Ruling:`; perguntar so se algo tocar a aeronave real)
| # | Decisao | Padrao |
|---|---|---|
| D1 | Piso de bateria para liberar o voo real | `max(limiar do failsafe da estacao + 10, 30)` por cento |
| D2 | Timeout de confirmacao de decolagem | 6,0 s (recalibrar com a medicao 8.8) |
| D3 | Armamento com gesto deliberado | Sim |
| D4 | Cena do emulador | v1 (fase da pose) + v2 (homografia de plano do solo); v1 como fallback |
| D8 | Tecla do atalho de abort | `Escape` |

---

## 11. Prompt de partida (colar no Claude Code, em `/home/jv/ros2_ws/src/mvp_mission_bebop`)

```text
Implemente e verifique o plano de docs/PROMPT_VOO_REAL_ESCOPO_ATUAL.md. Leia-o inteiro; depois leia a secao 4.1
(emulador de driver) de docs/PROMPT_IMPLEMENTACAO_VOO_REAL_100.md, CLAUDE.md, docs/IMPLEMENTACAO_PROGRESSO.md e
docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md, antes de qualquer edicao. Onde houver conflito, vale o escopo atual.

Escopo: (P1) clicar em Iniciar em --fly executa a cadeia completa e os 5 steps, a missao SABE quando a aeronave
realmente decolou (medido pelo flying_state da aeronave); os botoes Iniciar e
Land cumprem suas funcoes; (P2) as falas EXISTENTES cobrem o voo real e a pericia pos-land, sem sobreposicao, so quando o
evento e verdadeiro, com internet disponivel; a UNICA fala nova e a do botao Abortar; (P3) SOMENTE o botao de land. Perda de Wi-Fi, aeronave ociosa, missao
morta/travada, alteracoes no driver, voz offline, GRAVADOR DE VOO/TELEMETRIA e novas falas estao FORA do escopo
(secao 9): nao implemente.

Regras inegociaveis:
1. NUNCA arme motores nem comande a aeronave real: nada de takeoff, cmd_vel nao nulo ou land no dominio ROS 14 ou em
   192.168.42.1; nada de make driver-bebop; nada de mission.py --fly contra o driver real. Todo teste --fly roda no
   emulador de driver (dominio 80-99, discovery LOCALHOST) com os intertravamentos da secao 4.1.5.
2. Sem git commit, git push ou git pull com a arvore suja. No fim liste os arquivos alterados e proponha commits
   Conventional Commits sem executar. Nao reverta nem reformate modificacoes locais. Nao altere ros2_bebop_driver nem
   nectar-sdk.
3. CLAUDE.md: analise previa do nectar-sdk antes de cada implementacao; estilo Black Bee (docstrings NumPy, tipagem
   estrita, validacao defensiva); sem emojis; comentarios so para fisica/hardware/IPC; contrato "[STEP N:]" e
   mission_config.json preservados (esquema so aditivo, com default).
4. TDD: RED que falha pelo motivo certo, depois GREEN, depois a suite relevante. Suites com o BMG fechado. Base:
   pytest 1221, vitest 473 (55 arquivos), tsc 0. Nao avance com teste vermelho ou flaky sem explicacao.
5. Honestidade: separe Fato (medido/lido) de Suposicao (firmware). Nao diga "validado em voo" para o que so o
   emulador provou e nao diga "pronto para voo": entregue a cadeia verificada com numeros, a tabela de riscos aceitos
   (secao 9) e as pendencias de aeronave (secao 8).

Execute as Fases 0 a 9 da secao 6, na ordem. Adote os padroes da secao 10 e registre cada um como "Ruling:". Ao fim
de cada fase: suites verdes, docs/IMPLEMENTACAO_PROGRESSO.md atualizado e os numeros medidos informados (latencias,
sobreposicoes, convergencia, instantes de decolagem). Comece pela Fase 0 e relate a linha de base antes de seguir.
```
