# Failsafe de Bateria com RTL Obrigatório, Gate de Decolagem, Mapa Offline e Cache de Voz

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Na bateria crítica, o drone em voo nunca pousa na hora: aborta a etapa corrente e executa o RTL do modo ativo. (2) O gate de decolagem respeita o limiar escolhido pelo operador. (3) O mapa de satélite do Expo Center volta, 100% offline. (4) Todas as falas faltantes ficam no cache de voz em disco.

**Architecture:** O pouso imediato atual vem de uma dupla autoridade: a estação decide RTL, mas o próprio processo da missão pousa no mesmo limiar e, por rodar no laço de controle, vence a corrida. A autoridade passa a ser única e no Python (`BatterySupervisor` pede o salto ao estágio 5 via o mesmo caminho do `goto_stage`, marcando o retorno como forçado, o que também anula `rtl.enabled = false`). A estação vira redundância e narração. O gate usa uma comparação simples com o limiar, lido de uma única fonte (`battery.land_pct` do documento de lançamento). Mapa e voz seguem planos e ferramentas já existentes.

**Tech Stack:** Python 3.12 + pytest (`mvp_mission_bebop`), TypeScript/React/Vitest, Electron (`electron/main.cjs`, `launchReadiness.cjs`), `scripts/offline_tiles.py`, `streamer/precache_speech.py`.

**Spec:** Prompt do usuário de 2026-10-25, resumido em Global Constraints. O mapa continua regido por `docs/PLANO_MAPA_OFFLINE_EXPOCENTER.md`.

Caminhos relativos a `/home/jv/ros2_ws/src/mvp_mission_bebop`; `bebop_mission_control/` abreviado como `BMG/`.

## Diagnóstico (fatos verificados no código em 2026-10-25)

**Tópico 1 - por que o drone pousa na hora.**
- GCS: `BMG/src/lib/batteryFailsafe.ts` já manda `rtl` nas etapas 2, 3 e 4 (`RETURNABLE_STAGES = {2, 3, 4}`, salto para `RTL_STAGE = 5` por `bmg:goto-stage`). Pousa no local na etapa 1, sem missão rodando, e quando a missão não confirma o estágio 5 em `RTL_ACK_TIMEOUT_MS` (5 s; watchdog em `App.tsx` ~547). Com `rtl.enabled = false`, `batteryCalls` usa `IN_PLACE_CALLS` e fala "pousando no local".
- Python, causa raiz: `BatterySupervisor.land_reason()` (`telemetry/battery.py:201`) é consultada por `FailsafeSupervisor.evaluate_system_health()` (`telemetry/failsafe.py:429`), chamada em todo laço de controle (`steps/takeoff.py`, `search.py`, `tracking.py`, `inspection.py`, `rtl.py`). Ao falhar, o passo chama `trigger_emergency_land(reason)`, que faz `move_velocity(0)` e `land()`. O limiar `battery.land_pct` é o mesmo número que a estação envia (`launchParamsJson` injeta `failsafe.thresholdPct` em `battery.land_pct`). Os dois disparam no mesmo cruzamento. Inferência (não medida): o laço de controle do Python reage antes do caminho telemetria, IPC e `goto_stage` da estação, e o resultado é o pouso cego que a regra proíbe. A Task 1 deve reproduzir isso em teste antes de corrigir.
- `ClosedLoopRTLStep.execute` (`steps/rtl.py` ~515): `if not rtl_cfg.enabled: return self._land_in_place(...)`, com o comentário "the battery failsafe jumps here and inherits the same branch". Por isso `rtl.enabled = false` anula qualquer retorno.
- O RTL por modo já existe: Simples -> `_land_over_base` (gimbal em `rtl.camera_tilt_deg`, -80 graus por padrão, centralização no ArUco, sem cruzeiro); Avançado -> cruzeiro lateral à direita buscando o marcador, depois centralização e toque. O gate de estágio (`engine/stage_gate.py`) já aceita o 5 a partir de qualquer estágio, inclusive a subida, e recusa só `5` quando já está no 5.

**Tópico 2 - gate de decolagem.**
- `BMG/src/lib/preflightGate.ts:46` e `BMG/electron/launchReadiness.cjs:145`: `Math.max((threshold ?? 20) + 10, 30)`. Com 28% e limiar 20 resulta "28% < 30%".
- Fonte do limiar fora de sincronia: `electron/main.cjs:2680` lê `doc.failsafe?.return_threshold_percent`, chave que não existe em nenhum outro ponto do repositório, então o gate de lançamento usa sempre 20; o handler `bmg:get-launch-readiness` (`main.cjs:2877`) usa `options.failsafeThreshold`. Ambos fazem `Number(x) || 20`, que transforma um limiar 0 (mínimo do slider, `FAILSAFE_MIN_PCT`) em 20.
- O Python já recusa decolar com `percentage <= battery.land_pct` (`steps/takeoff.py:326`). O gate novo deve ser coerente com isso.

**Falas do retorno por bateria (achados).** Hoje a estação fala `battery.critical` (`batteryReturn.ts`) e, em seguida, o milestone `mission.rtl_start` do Python é narrado com o pool errado para esta causa: `via_jump: true` seleciona `mission.rtl_start_jump`, cujas frases dizem "comandado pelo operador". `mission.rtl_start` nem é narrado no Simples nem com `rtl.enabled = false` (`isMilestoneAllowed`). O toque no solo e o pouso usam `payload.at_base === true && rtlEnabled`: com `rtl.enabled = false` um retorno forçado que pousa na base seria falado como "pouso no local", e o veto `RETURN_WORDING` remove toda frase que cite base ou retorno. O aviso fixo de 10% (`mission.battery_warning`, "Missão segue") contradiz um retorno já em curso. As frases de `IN_PLACE_CALLS` ("pousando no local") deixam de fazer sentido para a bateria.

**Tópico 3 - mapa.** O modo `grid` forçado já foi revertido em `TacticalMap.tsx` e `scripts/offline_tiles.py` existe, mas `BMG/public/tiles/` não existe: falta obter os tiles. A decisão D-A de `PLANO_MAPA_OFFLINE_EXPOCENTER.md` (origem e licença da imagem) segue em aberto e bloqueia o download.

**Tópico 4 - voz.** `python3 BMG/streamer/precache_speech.py --dry-run` (voz Orbit, modelo `gemini-3.1-flash-live-preview`, cache `~/.cache/bmg/speech`): 745 falas, 257 em cache, **488 faltando**: `laudo.vitima` 285, `laudo.veiculo` 95, `voo.decolagem` 18, `voo.varredura` 15, `falha.aborto` 23, `falha.bateria` 12, `falha.link` 10, `voo.contagem` 9, `voo.aproximacao` 8, `falha.calibracao` 6, `falha.decolagem` 4, `voo.captura` 1, `laudo.abertura` 1, `falha.recusa_lancamento` 1. Nenhuma chave de API está no ambiente desta sessão (resolvida de arquivo pelo `announcer.py`, a verificar sem imprimir).

## Decisões (padrão adotado)

- **D1 - Autoridade única no Python** (sim): `BatterySupervisor` pede o retorno; a estação mantém o gatilho como redundância (pedido duplicado é recusado como "Stage 5 already running", sem efeito) e a narração.
- **D2 - Bateria vence `rtl.enabled = false`** (sim, regra do usuário): retorno forçado voa o RTL guiado por marcador mesmo com o plano de pousar no local.
- **D3 - Etapa 1 em voo também retorna** (sim): subida com a aeronave acima de `AIRBORNE_ALTITUDE_M` (0,25 m) pede o 5. No solo, o gatilho não dispara; vale o gate de pré-armar.
- **D4 - Gate sem margem** (sim, regra do usuário): bloqueia só se `bateria <= limiar`, o mesmo critério do Python. Risco aceito: decolar logo acima do limiar dispara o RTL logo após a subida.
- **D5 - Janela de busca do RTL inalterada** (sim, "lógica padrão do RTL"): `rtl.timeout_sec` não é encurtado na bateria crítica. Risco registrado: busca longa com carga baixa; qualquer teto específico é decisão futura.
- **D6 - Imagem do mapa**: aguardar a escolha de D-A (GeoSampa, MapTiler/Mapbox/Esri com autorização escrita, ou ortomosaico próprio). Sem resposta, não baixar nada.
- **D7 - Síntese de voz por último** (sim): só depois de congelados todos os textos (a Task 2 altera as falas de bateria), para não sintetizar duas vezes. A síntese real é autorizada pelo pedido do usuário; usa a chave de API e leva cerca de 2 s por fala.

- **D8 - Uma única voz por fase do retorno por bateria** (sim): a fala do cruzamento do limiar vem do milestone do Python (`mission.battery_return`), não do observador da estação, para não haver duas falas seguidas. A estação só fala por conta própria no caso de pouso de emergência (sem missão rodando ou salto recusado). Falas adaptadas por fase e por modo na Task 2B.
- **D9 - Aviso de bateria baixa calado durante o retorno** (sim): `mission.battery_warning` ("Missão segue") não é narrado depois de `mission.battery_return`, porque contradiz o retorno em curso.

## Global Constraints

- Sem emojis em código, comentários, logs, documentação e commits. Sem comentários redundantes (comentário só para física de controle, hardware Bebop 2 ou acordo IPC). Python: type hints estritos, docstrings NumPy, `TypeError`/`ValueError` defensivos. TypeScript strict, sem `any`.
- Antes de implementar controle ou interface de sensor, inspecionar `src/nectar-sdk` (regra de ouro do CLAUDE.md); registrar o achado. A leitura de bateria já vem do driver (`/bebop/states/battery`).
- Contrato com o GCS preservado: marcadores `[STEP N: ...]` e esquema de `mission_config.json`.
- Commits Conventional Commits em inglês; **nenhum commit nem push sem autorização explícita do usuário**. O working tree tem muitas alterações não commitadas de outras tarefas: commitar só arquivos desta tarefa, por caminho, nunca `git add -A`.
- Ambiente: `source /home/jv/ros2_ws/bin/nectar-activate` antes de qualquer Python. Nunca rodar dois lotes de voo emulado ao mesmo tempo. Subagentes precisam do mesmo `source`.
- Regras de bateria: o drone em voo nunca pousa direto no limiar; pouso só ao fim do RTL (toque confirmado sobre a base) ou no timeout padrão do RTL. Leitura ausente ou velha não é motivo para agir (comportamento atual, mantido).
- Gate: bloqueia se e somente se a carga for desconhecida ou `<= limiar`.

## Review Focus

1. Cruzamento do limiar durante a subida (etapa 1, acima de 0,25 m): retorno forçado sem pouso cego; no solo nada dispara. Teste na Task 1.
2. Cruzamento do limiar com o estágio 5 já em curso: não reinicia o RTL e não pousa por cima. Teste na Task 1.
3. As duas autoridades disparando juntas (Python e estação): um único salto, nenhuma fala duplicada, nenhum `land()` por conta da estação enquanto a missão responde. Teste na Task 2.
4. `rtl.enabled = false` com Simples e com Avançado: o retorno forçado constrói o sensor de marcador e voa o ramo do modo; sem calibração degrada para o retorno odométrico existente em vez de travar. Teste na Task 1.
5. Sequência falada do retorno por bateria sem repetição nem contradição: com `rtl.enabled = false` e retorno forçado, o pouso na base não é falado como "no local"; o aviso de 10% ("Missão segue") não toca durante o retorno; a fala do cruzamento sai uma vez. Testes na Task 2B.
6. Limiar 0 e limiar 100 no gate: 0 permite qualquer carga conhecida acima de 0; 100 bloqueia sempre; `Number(x) || 20` não pode mais converter 0 em 20. Teste na Task 3.

---

### Task 0: Check-in e baseline

**Files:** nenhum.

- [ ] **Step 1: Estado e baseline**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && cd /home/jv/ros2_ws/src/mvp_mission_bebop && git status --short | wc -l
python3 -m pytest test/test_battery_guard.py test/test_battery_rtl_jump.py test/test_failsafe.py test/test_prearm_battery_gate.py test/test_rtl_flag.py test/test_lateral_rtl.py test/test_aruco_rtl.py test/test_contracts.py -q
cd bebop_mission_control && npx vitest run src/lib/batteryFailsafe.test.ts src/lib/batteryReturn.test.ts src/lib/preflightGate.test.ts src/lib/launchReadiness.test.ts src/lib/launchReadinessMain.test.ts
```
Expected: verde; registrar falhas pré-existentes.

- [ ] **Step 2: Inspecionar `src/nectar-sdk`** por leitura de bateria ou RTL reutilizáveis (`grep -rn -i "battery\|rtl\|return_to" src/nectar-sdk --include=*.py`) e registrar o resultado no relatório.

---

### Task 1: Retorno forçado por bateria no Python

**Files:**
- Modify: `mvp_mission_bebop/telemetry/battery.py`, `mvp_mission_bebop/telemetry/failsafe.py`, `mvp_mission_bebop/context.py`, `mvp_mission_bebop/mission.py` (ligação do gancho, ~1000-1130), `mvp_mission_bebop/steps/rtl.py`, `mvp_mission_bebop/telemetry/milestones.py` (nova chave), `mvp_mission_bebop/telemetry/spoken_alerts.py` se citar `land_reason`
- Modify (tests): `test/test_battery_guard.py`, `test/test_failsafe.py`, `test/test_battery_rtl_jump.py`, `test/test_rtl_flag.py`
- Create: `test/test_battery_forced_return.py`

**Interfaces:**
- `BatterySupervisor.critical_reason(self) -> Optional[str]`: substitui `land_reason`; mesma regra (leitura fresca `<= land_pct`), texto sem "landing in place": `"Battery at {p:.0f}% <= return threshold {t:.0f}%; returning to base."`.
- `BatterySupervisor.set_return_hook(self, hook: Callable[[str], bool], airborne: Callable[[], bool]) -> None`. Em `battery_callback`, depois de `_store`, se a leitura é `<= land_pct`, o gancho ainda não disparou neste voo e `airborne()` é verdadeiro, chama `hook(reason)` uma única vez (flag `_return_requested`, sob `_lock`). `TypeError` se algum argumento não for chamável. `reset_return()` rearma para um novo voo.
- `FailsafeSupervisor.evaluate_system_health` deixa de consultar a bateria (a bateria não é mais uma falha de saúde); `trigger_emergency_land` permanece para as demais falhas.
- `MissionContext.request_battery_return(self, reason: str) -> bool`: marca `self.blackboard.battery_return = True`, loga `[BATTERY] return forced: <reason>`, emite o milestone `mission.battery_return` com `{battery_pct, threshold_pct, from_stage, mode}` (`mode` em `simple`/`advanced`, para a fala escolher o pool), e chama `request_stage(5)` pelo mesmo gate do `goto_stage` (`refuse_stage_request`). Retorna `False` quando o gate recusa; com o estágio 5 já em curso retorna `True` sem reiniciar. Quando recusado por o estágio 5 não existir na execução (CLI com `--stages` parcial), o chamador cai em `failsafe.trigger_emergency_land(reason)`, único caso de pouso direto por bateria.
- `ClosedLoopRTLStep`: `execute` lê `forced = bool(getattr(ctx.blackboard, "battery_return", False))`; o ramo `_land_in_place` só vale para `not rtl_cfg.enabled and not forced`. Com `forced`, o sensor de marcador e o controle de centralização são construídos e o ramo do modo roda (`_land_over_base` no Simples; reversão lateral à direita, centralização e toque no Avançado). `self.name` do passo passa a refletir `stage_five_name(mode, True)` quando forçado, para o marcador `[STEP 5: ...]` que a estação espera. Os milestones `mission.rtl_start`, `mission.landing` e `mission.touchdown` passam a levar `battery_return: bool` (lido de `blackboard.battery_return`), e o `mission.rtl_start` é emitido também no retorno forçado do modo Avançado com `rtl.enabled = false`. Duplicata: dois `request_stage(5)` seguidos executam o estágio 5 uma vez.

- [ ] **Step 1: Write the failing tests** (`test_battery_forced_return.py`, sem ROS, com duplos como em `test_battery_rtl_jump.py` e `test_rtl_flag.py`):
  - `test_threshold_requests_stage_5_instead_of_landing`: leituras 22, 21, 20 com `airborne=lambda: True`; o gancho é chamado uma vez; `trigger_emergency_land` simulado nunca é chamado; `evaluate_system_health()` devolve `(True, "Nominal")` com a carga em 20.
  - `test_hook_fires_once_per_flight_and_rearms_on_reset`.
  - `test_silent_on_ground_and_without_fresh_reading`: `airborne` falso, ou leitura velha (`BATTERY_STALE_TIMEOUT_SEC`) -> gancho não chamado.
  - `test_climb_in_stage_1_requests_the_return`: `refuse_stage_request(5, stage_numbers=(1,2,3,4,5), current_stage=1, takeoff_committed=True, takeoff_complete=False, airborne=True)` é `None`; com `current_stage=5` retorna a razão "Stage 5 is already running".
  - `test_forced_return_flies_the_rtl_branch_with_rtl_disabled`: `ctx.params.rtl.enabled = False`, `blackboard.battery_return = True`, sensor sintético injetado: `_land_in_place` não é chamado; no Simples `_land_over_base` é chamado; no Avançado a busca reversa e a centralização rodam.
  - `test_rtl_disabled_without_battery_still_lands_in_place` (regressão).
  - `test_return_attitude_is_the_rtl_camera_tilt`: `_engage_return_attitude` publica `rtl.camera_tilt_deg` (-80 por padrão) nos dois modos.
  - `test_marker_name_is_rtl_when_forced`: o nome do passo forçado casa com `stage_five_name(mode, True)`.
  - `test_refused_request_falls_back_to_emergency_land` com o estágio 5 ausente da execução.
  - `test_battery_return_milestone_payload`: chaves e valores do milestone, incluindo `mode`.
  - `test_return_milestones_carry_battery_flag`: no retorno forçado, `mission.rtl_start` (Avançado), `mission.landing` e `mission.touchdown` levam `battery_return: True`; no voo nominal levam `False`.
  - `test_duplicate_stage_5_request_runs_stage_once`: dois `request_stage(5)` seguidos (Python e estação) rodam o estágio 5 uma única vez, sem reiniciar.
  Ajustar `test_battery_guard.py` e `test_failsafe.py` para `critical_reason` e para a saúde sem bateria; `test_battery_rtl_jump.py` mantém o contrato do salto.

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest test/test_battery_forced_return.py test/test_battery_guard.py test/test_failsafe.py -q
```
Expected: FAIL (símbolos inexistentes).

- [ ] **Step 3: Implement** as interfaces acima. Ligar o gancho em `mission.py` depois de criar o `MissionContext` (`battery_supervisor.set_return_hook(ctx.request_battery_return_or_land, airborne)`), usando o mesmo `relative_altitude > AIRBORNE_ALTITUDE_M` do `StageRequestHandler`; o log de inicialização "land in place at <= N%" passa a "return to base at <= N%". Atualizar docstrings de `battery.py`, `BatteryConfig` (`parameters.py`) e `failsafe.py` que dizem "lands in place".

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest test/test_battery_forced_return.py test/test_battery_guard.py test/test_battery_rtl_jump.py test/test_failsafe.py test/test_rtl_flag.py test/test_lateral_rtl.py test/test_aruco_rtl.py test/test_prearm_battery_gate.py test/test_contracts.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit** (se autorizado): `fix(battery): return to base on critical charge instead of landing in place`

---

### Task 2: Estação: retorno obrigatório e fala coerente

**Files:**
- Modify: `BMG/src/lib/batteryFailsafe.ts`, `BMG/src/lib/batteryReturn.ts`, `BMG/src/App.tsx` (watcher ~498-575), `BMG/src/hooks/useCopilot.ts` (`FIXED_ALERT_SENTENCES`), `BMG/src/lib/spokenCatalog.ts` se citar `IN_PLACE_CALLS`, `BMG/src/lib/parameterSchema.ts` (dica de `rtl.enabled`, se disser que a bateria pousa no local)
- Modify (tests): `BMG/src/lib/batteryFailsafe.test.ts`, `BMG/src/lib/batteryReturn.test.ts`, testes de `copilotPhrases`/`spokenCatalog` afetados
- Create: `BMG/src/App.battery.test.tsx` (padrão de `App.launchParams.test.tsx`)

**Interfaces:**
- `RETURNABLE_STAGES` passa a `{1, 2, 3, 4}`. `failsafeAction(stage: number, missionRunning: boolean): FailsafeAction` devolve `'land'` somente com `missionRunning === false`, `'none'` no estágio 5, `'rtl'` nos demais. O comentário do arquivo deixa de dizer que a etapa 1 pousa.
- Voz (D8): a estação deixa de falar `returning` e `alreadyReturning`; essas fases passam a ser narradas pelos milestones do Python (Task 2B). `batteryCalls`, `IN_PLACE_CALLS`, `SIMPLE_CALLS` e `ADVANCED_CALLS` são substituídos por `export const BATTERY_LANDING_CALL = 'Bateria crítica. Pouso de emergência iniciado.'`, única fala própria da estação, usada só no pouso de emergência (sem missão rodando, salto recusado ou sem confirmação nas duas janelas). `runCriticalBatteryReturn(action: FailsafeAction, hooks: BatteryReturnHooks): Promise<FailsafeAction>`: sem `mode` e sem `rtlEnabled`; `hooks.say` só é chamado no ramo de pouso.
- Watchdog em `App.tsx`: se o estágio 5 não for reportado em `RTL_ACK_TIMEOUT_MS`, reemitir `gotoStage(RTL_STAGE)` uma vez e rearmar; só pousar no local se a missão não estiver mais rodando ou após a segunda janela sem confirmação (último recurso, com a fala de pouso de emergência existente).

- [ ] **Step 1: Write the failing tests** (Vitest):
  - `batteryFailsafe.test.ts`: `failsafeAction(1, true) === 'rtl'`, `(2|3|4, true) === 'rtl'`, `(5, true) === 'none'`, `(1|2|3|4|5, false) === 'land'`.
  - `batteryReturn.test.ts`: `runCriticalBatteryReturn('rtl', hooks)` com `gotoStage` bem-sucedido não chama `say` nem `land`; com `'none'` também não fala; com `gotoStage` falhando, e com `'land'`, fala `BATTERY_LANDING_CALL` e pousa (fallback legítimo); nenhuma frase da bateria casa `/no local/`.
  - `App.battery.test.tsx`: lançamento com `rtl.enabled = false`, telemetria em voo na etapa 3 cruzando o limiar -> `bridge.gotoStage` chamado com `5`, `bridge.land`/equivalente não chamado dentro da janela; sem confirmação, uma segunda tentativa de `gotoStage(5)` antes de qualquer pouso (timers falsos); com o estágio 5 confirmado, nenhum pouso. Mesma cobertura para etapa 1 em voo.
  - Teste de unicidade: com a missão respondendo (estágio 5 confirmado) a estação não fala nada nem pousa; a fala do cruzamento sai uma única vez, do milestone `mission.battery_return` (Task 2B).

- [ ] **Step 2: Run to verify they fail**: `cd bebop_mission_control && npx vitest run src/lib/batteryFailsafe.test.ts src/lib/batteryReturn.test.ts src/App.battery.test.tsx` -> FAIL.

- [ ] **Step 3: Implement.** Antes de remover `batteryCalls`, `grep -rn "batteryCalls\|BATTERY_CALLS\|runCriticalBatteryReturn\|IN_PLACE_CALLS" BMG/src` e ajustar cada uso, inclusive `FIXED_ALERT_SENTENCES` e o catálogo de falas (as frases "retornando", "já em curso" e "pousando no local" da estação saem do manifesto; o total a sintetizar diminui).

- [ ] **Step 4: Run tests e build**: `npx vitest run src/lib src/hooks src/App.battery.test.tsx && npm run build` -> PASS, `build exit=0`.

- [ ] **Step 5: Commit** (se autorizado): `fix(bmg): always return on critical battery and keep the landing fallback as last resort`

---

### Task 2B: Falas do retorno e pouso por bateria

Depende da Task 1 (`blackboard.battery_return`, milestone `mission.battery_return`, flag `battery_return` nos milestones do RTL) e da Task 2 (a estação deixa de falar a fase de retorno).

**Files:**
- Modify (TS): `BMG/src/lib/copilotPhrases.ts` (pools, `poolForMilestone`, `isMilestoneAllowed`, `vetoFor`, `poolsForPlan`, `MILESTONE_KEYS`), `BMG/src/hooks/useCopilot.ts` (`useFlightNarration`), `BMG/src/lib/spokenCatalog.ts`
- Modify (Python): `mvp_mission_bebop/telemetry/milestones.py` (chave `mission.battery_return`), `mvp_mission_bebop/steps/rtl.py` (texto de `_announce` no retorno forçado), `mvp_mission_bebop/telemetry/announcer.py` (`_format_telemetry_statement`), `mvp_mission_bebop/telemetry/spoken_alerts.py`
- Modify (tests): `BMG/src/lib/copilotPhrases.test.ts`, `BMG/src/hooks/useFlightNarration.test.ts`, `BMG/src/lib/spokenCatalog.test.ts`, `test/test_announcer_calls.py`, `test/test_spoken_alerts.py`, o teste de milestones existente em `test/`

**Sequência falada de um retorno por bateria** (uma voz por fase, D8; nenhuma repete "bateria crítica" duas vezes seguidas):

1. Cruzamento do limiar, `URGENT`, interrompe a narração: pool `mission.battery_return` (Avançado) ou `mission.battery_return_simple` (Simples), escolhido por `payload.mode`.
2. Início da busca do marcador (só Avançado; `mission.rtl_start` com `battery_return: true`): pool `mission.rtl_start_battery`.
3. Pouso: `mission.landing` com `battery_return`, na base ou fora dela.
4. Toque: `mission.touchdown` com `battery_return`; sem confirmação, a frase ditada `mission.touchdown_unconfirmed` já existente.
5. Se a missão não responder e a estação precisar pousar: `BATTERY_LANDING_CALL` (Task 2).

**Frases novas** (texto exato; cada pool sem duplicatas, terminando em ponto, sem a palavra proibida; todas em `falha.bateria` no manifesto da voz):

- `mission.battery_return`: `Bateria crítica. Missão interrompida. Retornando à base para pouso.` / `Limite de bateria atingido. Abortando a etapa e voltando à base.` / `Bateria crítica. Interrompendo a operação. Retorno à base engajado.` / `Carga no limite de segurança. Retornando à base para pousar.`
- `mission.battery_return_simple`: `Bateria crítica. Missão interrompida. Pouso na base iniciado.` / `Limite de bateria atingido. Abortando a etapa e pousando sobre a base.` / `Bateria crítica. Interrompendo a operação. Centralizando sobre a base para pousar.` / `Carga no limite de segurança. Pousando sobre a base.`
- `mission.rtl_start_battery`: `Retorno por bateria engajado. Buscando o marcador de pouso.` / `Rota de volta à base em curso por limite de bateria.` / `Regressando ao ponto de lançamento. Bateria no limite de segurança.` / `Retorno à base em curso. Bateria crítica.` / `Voltando à base para pouso. Procurando o marcador.`
- `mission.landing_battery` (na base): `Bateria crítica. Iniciando pouso na base. Mantenham a área livre.` / `Base localizada. Pouso por bateria crítica em andamento.` / `Descida final na base por limite de bateria. Área livre, por favor.` / `Bateria crítica. Pousando sobre a base.` / `Alinhado com a base. Pouso de segurança iniciado.`
- `mission.landing_battery_in_place` (base não localizada): `Bateria crítica. Marcador da base não localizado. Pouso no local, mantenham a área livre.` / `Limite de bateria atingido sem localizar a base. Pousando no local.` / `Bateria crítica. Pouso de segurança no ponto atual. A área deve estar desobstruída.` / `Pouso de segurança no local por bateria crítica. Atenção à área.`
- `mission.touchdown_battery` (na base): `Pouso confirmado na base. Missão encerrada por bateria crítica.` / `Aeronave em solo na base. Retorno por bateria concluído.` / `Toque no solo confirmado na base após retorno por bateria.` / `Pouso seguro na base concluído. Bateria crítica.`
- `mission.touchdown_battery_in_place`: `Pouso confirmado no local. Missão encerrada por bateria crítica.` / `Aeronave em solo no local. Pouso de segurança por bateria concluído.` / `Toque no solo confirmado no local após limite de bateria.` / `Pouso seguro no local concluído. Bateria crítica.`

Total: 30 frases novas (a Task 5 recontará o que falta sintetizar).

**Interfaces (TS):**
- Novas chaves de pool (`PoolKey`) acima; `MILESTONE_KEYS` ganha `'mission.battery_return'`; um conjunto `BATTERY_POOLS` alcançável em todos os planos (`poolsForPlan` o inclui sempre, para o manifesto cobrir os quatro planos).
- `poolForMilestone(key, payload, plan)`: com `payload.battery_return === true` a escolha vem antes de qualquer regra de `rtlEnabled` ou `via_jump`: `mission.rtl_start` -> `mission.rtl_start_battery`; `mission.landing` -> `mission.landing_battery` se `at_base === true`, senão `mission.landing_battery_in_place`; `mission.touchdown` -> `confirmed !== true` => `mission.touchdown_unconfirmed`, senão `mission.touchdown_battery` se `at_base === true`, senão `mission.touchdown_battery_in_place`; `mission.battery_return` -> `mission.battery_return_simple` se `payload.mode === 'simple'` (ou, na falta, o modo do plano), senão `mission.battery_return`.
- `isMilestoneAllowed(key: MilestoneKey, plan: PlanLike, payload?: Readonly<Record<string, unknown>>): boolean`: libera `mission.rtl_start` quando `payload?.battery_return === true`.
- `vetoFor`: pools de bateria nunca são vetados por `RETURN_WORDING` (o plano com `rtl.enabled = false` não pode censurar "base"); `rtl_start_battery` veta variantes com "marcador" quando `payload.marker_guided !== true`.
- `useFlightNarration`: `mission.battery_return` vai por `queue.preempt(key, text, 'URGENT')` e liga `batteryReturnActive`; enquanto ativo, `mission.battery_warning` não é narrado (D9); `mission.start` zera o estado. As chamadas a `isMilestoneAllowed` passam o payload.

**Interfaces (Python):** `_format_telemetry_statement` mapeia a ação `Retorno por bateria crítica` para `Bateria crítica. Missão interrompida. Retornando à base para pouso.` (Simples, com `details["modo"] == "simple"`: `Bateria crítica. Missão interrompida. Pouso na base iniciado.`), para o caso do processo da missão narrar sozinho (`station_narrates` falso); o texto de `_announce` do retorno forçado em `rtl.py` usa essa ação. As duas frases entram em `spoken_alerts.ALERT_SENTENCES` com categoria `falha.bateria`.

- [ ] **Step 1: Write the failing tests**
  - `copilotPhrases.test.ts`: cada pool novo é não vazio, sem duplicatas, termina em ponto, sem `{` e sem a palavra proibida; `poolForMilestone('mission.rtl_start', { battery_return: true, via_jump: true, marker_guided: true }, { mode: 'advanced', rtlEnabled: false })` é `'mission.rtl_start_battery'` (nunca `rtl_start_jump`), idem com `rtlEnabled: true`; `mission.touchdown` com `{ confirmed: true, at_base: true, battery_return: true }` e `rtlEnabled: false` é `'mission.touchdown_battery'`, com `at_base: false` é `'..._in_place'`, com `confirmed: false` é `'mission.touchdown_unconfirmed'`; `mission.landing` análogo; sem `battery_return` a escolha não muda (regressão dos testes existentes); `isMilestoneAllowed('mission.rtl_start', { mode: 'simple', rtlEnabled: false }, { battery_return: true })` é `true` e sem payload é `false`; com `marker_guided: false` nenhuma variante sorteada de `rtl_start_battery` casa `/marcador/i` (12 valores de `random`); nenhuma frase de pool de bateria casa `/operador|comandad/i` e, com `rtlEnabled: false`, as de pouso na base não casam `/no local/`.
  - `useFlightNarration.test.ts`: sequência `mission.battery_return` (`mode: 'advanced'`) -> `mission.battery_warning` -> `mission.rtl_start` (`battery_return`, `via_jump`, `marker_guided`) -> `mission.landing` -> `mission.touchdown`: o primeiro texto sai por `preempt` com prioridade `URGENT` e pertence ao pool `mission.battery_return`; o aviso de bateria não é falado; os demais pertencem aos pools de bateria na ordem; repetido com `mode: 'simple'` e `rtlEnabled: false` (sem `mission.rtl_start`); `mission.start` rearma o aviso.
  - `spokenCatalog.test.ts`: o manifesto contém as 30 frases em `falha.bateria` nos quatro planos; nenhuma frase de bateria da estação além de `BATTERY_LANDING_CALL`.
  - Python: `_format_telemetry_statement('Retorno por bateria crítica', {...})` devolve as frases acima nos dois modos; `ALERT_SENTENCES` as contém; o teste de milestones aceita a chave `mission.battery_return`.

- [ ] **Step 2: Run to verify they fail**

```bash
cd bebop_mission_control && npx vitest run src/lib/copilotPhrases.test.ts src/hooks/useFlightNarration.test.ts src/lib/spokenCatalog.test.ts
cd .. && python3 -m pytest test/test_announcer_calls.py test/test_spoken_alerts.py -q
```
Expected: FAIL (pools e mapeamentos inexistentes).

- [ ] **Step 3: Implement** os pools, a escolha por payload, a narração e o mapeamento Python. Se `PoolKey`/`HistoryKey` listarem as chaves à mão, acrescentá-las também ali (o histórico entre voos guarda índices por chave).

- [ ] **Step 4: Run tests e build**: os arquivos acima mais `npx vitest run src/lib src/hooks && npm run build` e `python3 -m pytest test/test_announcer_calls.py test/test_spoken_alerts.py test/test_contracts.py -q` -> PASS.

- [ ] **Step 5: Commit** (se autorizado): `feat(copilot): narrate the battery return, landing and touchdown with battery-specific wording`

---

### Task 3: Gate de decolagem pelo limiar do operador

**Files:**
- Modify: `BMG/src/lib/preflightGate.ts` (linha 46 e comentário), `BMG/electron/launchReadiness.cjs` (linha 145 e comentário do cabeçalho), `BMG/electron/main.cjs` (linhas 2680 e 2877)
- Modify (tests): `BMG/src/lib/preflightGate.test.ts`, `BMG/src/lib/launchReadiness.test.ts`, `BMG/src/lib/launchReadinessMain.test.ts`

**Interfaces:** nova função pura compartilhada de regra em cada lado (TS e CJS, sem importar entre si): `batteryBlockedReason(pct: number | null | undefined, threshold: number): string | null`. Bloqueia quando `pct` não é número finito ou `pct <= threshold`, com a mensagem `Bateria insuficiente para voo real (${pct}% <= limiar ${threshold}%)`; `batteryKnown === false` mantém `Bateria da aeronave desconhecida`. O limiar vem de uma única fonte, `battery.land_pct` do documento de lançamento (o mesmo valor que `launchParamsJson` injeta a partir de `failsafe.thresholdPct`): em `main.cjs`, `resolveFailsafeThreshold(doc, options)` lê `getPath(doc, 'battery.land_pct')`, depois `options.failsafeThreshold`, e só então o padrão 20, usando `Number.isFinite` (0 é válido). A chave inexistente `failsafe.return_threshold_percent` é removida.

- [ ] **Step 1: Write the failing tests**:
  - `preflightGate.test.ts`: 28% com limiar 20 -> `null`; 20% com limiar 20 -> `Bateria insuficiente para voo real (20% <= limiar 20%)`; 21% com limiar 20 -> `null`; limiar 0 e carga 5% -> `null`; limiar 100 -> sempre bloqueado; sem limiar informado usa 20.
  - `launchReadiness.test.ts`: os mesmos casos em `evaluateLaunchReadiness`.
  - `launchReadinessMain.test.ts`: `battery_pct: 25` sem limiar no documento -> pronto (antes bloqueava); `battery_pct: 15` -> bloqueado; documento com `battery.land_pct: 30` e `battery_pct: 28` -> bloqueado; `battery.land_pct: 10` e `battery_pct: 25` -> pronto; `battery.land_pct: 0` é respeitado (não vira 20).
  - Paridade: um teste que executa os mesmos vetores contra `preflightGate.ts` e `launchReadiness.cjs` e exige veredito igual.

- [ ] **Step 2: Run to verify they fail**: `npx vitest run src/lib/preflightGate.test.ts src/lib/launchReadiness.test.ts src/lib/launchReadinessMain.test.ts` -> FAIL.
- [ ] **Step 3: Implement** a regra e a fonte única do limiar.
- [ ] **Step 4: Run tests**: os três arquivos acima mais `npm run build` -> PASS. Conferir também, no Python, que `test/test_prearm_battery_gate.py` segue verde (mesmo critério `<=`).
- [ ] **Step 5: Commit** (se autorizado): `fix(bmg): gate takeoff on the operator battery threshold only`

---

### Task 4: Mapa de satélite offline (continuação do plano do mapa)

**Files:** conforme `docs/PLANO_MAPA_OFFLINE_EXPOCENTER.md`. Estado: a Task 1 desse plano (reversão do modo grade) está aplicada e `scripts/offline_tiles.py` existe; faltam obter os tiles, servir `/tiles/`, a fonte local em `TacticalMap.tsx` e a verificação sem rede.

- [ ] **Step 1: Ler `docs/PLANO_MAPA_OFFLINE_EXPOCENTER.md` por inteiro e conferir o estado** (`git diff` dos arquivos do mapa, `ls BMG/public/tiles`).
- [ ] **Step 2: Perguntar ao usuário a decisão D-A (origem da imagem) e esperar.** Não baixar nada antes. Os termos do MapTiler proíbem baixar tiles em massa sem acordo escrito; não usar a chave MapTiler embutida. O pedido "satélite/rua" fica restrito a satélite: mapas de rua trazem nomes embutidos no raster, motivo pelo qual o código atual usa só imagem de satélite.
- [ ] **Step 3: Executar as Tasks 2 (a partir do Step 5), 3, 4, 5 e 6 do plano do mapa**, com os testes e a verificação visual sem rede definidos lá (a desconexão da rede é ação do usuário).

---

### Task 5: Síntese e cache das falas faltantes (por último)

**Files:** nenhum arquivo de código; escreve em `~/.cache/bmg/speech`.

Pré-condição: Tasks 1, 2, 2B e 3 concluídas e textos congelados (as falas de bateria mudam nas Tasks 2 e 2B: saem as da estação, entram 30 frases novas). Se alguma alteração de texto ocorrer depois, repetir este passo.

- [ ] **Step 1: Recontar o que falta, sem rede**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 bebop_mission_control/streamer/precache_speech.py --dry-run
```
Expected: lista por categoria. O total muda em relação aos 488 iniciais (saem as falas de bateria da estação, entram as 30 da Task 2B, e `falha.bateria` passa a ter só as frases novas mais o pouso de emergência): informar o número final e a categoria `falha.bateria` em particular.

- [ ] **Step 2: Verificar a chave de API sem imprimi-la.** Se o `announcer.py` não resolver token (variáveis `SPEECH_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` ou o arquivo que ele consulta), parar e pedir ao usuário que a forneça no ambiente. Nunca ecoar a chave em log, comando ou resposta. Confirmar com o usuário que a máquina tem internet nesta etapa.

- [ ] **Step 3: Sintetizar**

```bash
python3 bebop_mission_control/streamer/precache_speech.py
python3 bebop_mission_control/streamer/precache_speech.py --check
```
Expected: o primeiro termina com 0 falhas (ou lista as falhas e repete só o que falta); `--check` sai com código 0 e `faltam: 0`, `curtas: 0`. Voz e modelo devem ser os mesmos mostrados no `--dry-run` (Orbit, `gemini-3.1-flash-live-preview`); outra voz ou modelo gera chaves de cache diferentes.

- [ ] **Step 4: Empacotar para a máquina da feira**: `python3 bebop_mission_control/streamer/precache_speech.py --pack ~/bmg-speech-feira.tar.gz`; na outra máquina, `--unpack` e `--check`.

---

### Task 6: Verificação ponta a ponta

**Files:** `docs/CHECKLIST_VOO_REAL.md` (atualizar a seção de bateria).

- [ ] **Step 1: Suítes completas**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/ -q
cd bebop_mission_control && npm test && npm run build; echo "build exit=$?"
```
Expected: verde, `build exit=0`. Falhas pré-existentes da Task 0 são reportadas à parte.

- [ ] **Step 2: Voo emulado do retorno forçado (um lote por vez).** Descobrir como o emulador injeta carga (`scripts/fake_bebop_driver.py`, `test/test_fake_bebop_faults.py`, `test/test_fly_path_emulated.py`). Se publicar `BatteryState`, rodar quatro casos: limiar cruzado nas etapas 2, 3 e 4 e durante a subida, em Avançado e em Simples, com `rtl.enabled` verdadeiro e falso, e conferir `[STEP 5: ...]`, gimbal em -80, nenhum pouso antes do toque sobre a base e o toque confirmado, mais a sequência falada no log do copiloto (`[SPEECH]` com `source=cache`): cruzamento, início do retorno, pouso e toque com as frases de bateria da Task 2B, sem "no local" quando pousa na base e sem o aviso de 10%. Se o emulador não publicar a carga, declarar a lacuna e cobrir só pelos testes unitários.

- [ ] **Step 3: Atualizar `docs/CHECKLIST_VOO_REAL.md`** com a nova regra de bateria, o gate e o procedimento de voz e mapa offline.

- [ ] **Step 4: Fechar** (somente com autorização): commits atômicos por tarefa, só dos arquivos de cada uma.

---

## Self-review (cobertura do pedido)

| Requisito | Tarefa |
|---|---|
| Bateria no limiar: abortar a etapa e saltar ao RTL, sem pouso instantâneo | 1 (Python), 2 (estação) |
| Falas adaptadas ao failsafe: cruzamento, retorno, pouso e toque, por modo, na base ou fora dela, sem contradição | 2B (frases e seleção), 5 (síntese) |
| RTL por modo: Simples (gimbal -80 e ArUco) e Avançado (retorno à direita, gimbal -80, ArUco) | 1 (ramos existentes de `ClosedLoopRTLStep`, validados por teste) |
| Bateria sobrepõe `rtl.enabled` | 1, 2 |
| Pouso só ao centralizar no marcador ou no timeout padrão do RTL | 1 (D5) |
| Gate de decolagem sem piso de 30% e sem +10%; limiar sincronizado | 3 |
| Remover a grade e restaurar o mapa offline com zoom | 4 |
| Sintetizar e validar as 488 falas (`--check` com 0 faltantes) | 5 |
| Testes `pytest` e `vitest` | 1, 2, 3, 6 |

Nomes consistentes entre tarefas: `critical_reason`/`set_return_hook`/`request_battery_return` (1), `failsafeAction`/`BATTERY_LANDING_CALL` (2), pools `mission.battery_return*`, `mission.rtl_start_battery`, `mission.landing_battery*`, `mission.touchdown_battery*` e a flag `battery_return` (1, 2B; usados no catálogo de falas da Task 5), `batteryBlockedReason`/`resolveFailsafeThreshold` (3).

## Pendências

- Resultado de D-A (origem da imagem do mapa) e a licença correspondente.
- Rotacionar a chave MapTiler exposta no histórico do Git.
- Registrar a lacuna do emulador, se ele não publicar `BatteryState`.
