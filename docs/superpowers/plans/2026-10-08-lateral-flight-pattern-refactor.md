# Refatoracao Cinematica: Padrao de Voo Lateral Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar o padrao de voo longitudinal (avanco em X) por um padrao estritamente lateral (eixo Y): varredura para a esquerda, servoing lateral sem avanco, enquadramento do alvo somente pelo gimbal e retorno para a direita, com yaw travado em zero.

**Architecture:** O eixo de cruzeiro passa de `vx` para `vy` (FLU: `vy > 0` e esquerda). O Stage 3 deixa de ter fases de aproximacao e nadir e vira "centrar lateralmente e travar" (`CENTERING -> LOCKED`, mais `REACQUIRING`). O pitch do gimbal passa a pertencer exclusivamente ao Stage 4, em um controlador puro novo (`GimbalFramingController`). O Stage 5 inverte o cruzeiro de busca para `vy < 0`, e a guarda de borda do totem continua medindo a posicao "ao longo da trilha de ida", que agora e lateral. A estrutura de classes, os nomes de parametros e os marcadores `[STEP N:` sao preservados para nao quebrar o contrato com o GCS.

**Tech Stack:** Python 3.12, ROS 2 Jazzy, Nectar SDK (somente leitura), pytest, emulador `test/support/fake_bebop`, Electron/React (somente verificacao de contrato).

**Spec:** `/home/jv/ros2_ws/src/mvp_mission_bebop/docs/superpowers/plans/` (este arquivo) mais o enunciado "Refatoracao Cinematica: Padrao de Voo Lateral" da sessao de 2026-10-08. Contexto herdado: `docs/PROMPT_IMPLEMENTACAO_TOTEM_VOO_ALTITUDE.md` (pad e sonar) e `CLAUDE.md`.

---

## Global Constraints

- **Sem emojis** em codigo, comentarios, logs, docs ou commits. **Sem comentarios obvios**; comentarios apenas para fisica de controle, cinematica, restricao do Bebop 2 ou sincronismo IPC.
- Tipagem estrita (`Optional`, `Tuple`, `Final`...), docstrings NumPy/reST com `Parameters`/`Returns`/`Raises`, validacao defensiva de entradas (`ValueError`, `TypeError`), no estilo Black Bee.
- **Antes de cada tarefa**: inspecionar `/home/jv/ros2_ws/src/nectar-sdk` (regra de ouro do `CLAUDE.md`). Verificado na analise: `BebopDrone.move_velocity` (`nectar/nectar/control/bebop/drone.py:183`) publica `Twist.linear.y` com `+y = esquerda` (FLU, REP-103). O SDK nao e editado.
- Convencao de sinais: `vx > 0` frente, `vy > 0` esquerda, `vyaw == 0.0` sempre (invariante do optical flow). `ex_body_m` positivo = origem a frente; `ey_body_m` positivo = origem a esquerda.
- **Contrato com o GCS preservado**: marcadores `[STEP N:` (regex em `electron/main.cjs`), chaves de milestone (`mission.scan_start`, `mission.target_found`, `mission.approaching`, `mission.capture_done`, `mission.rtl_start`), esquema de `mission_config.json` e todos os nomes de parametros (`kinematics.forward_cruise_velocity`, `rtl.reverse_cruise_velocity`, `platform.edge_ahead_m`, etc.). Nenhum campo e removido nem renomeado; campos que perdem uso ganham docstring "inerte no padrao lateral".
- Regra E05 (`rtl.reverse_cruise_velocity == -kinematics.forward_cruise_velocity`) permanece: a velocidade de retorno e a mesma em modulo da "Velocidade de Deslocamento".
- `controllers/` continua puro (sem clock de parede, sem ROS). `steps/` faz sequencia e transmissao.
- **Decolagem sobre o totem (1 m, 60x60 cm)**: a conservacao de altura e a diferenciacao da altura do solo no meio do voo devem ter o mesmo efeito do codigo atual na saida pela esquerda e no retorno pela direita. Isso cobre: guarda de borda do altitude hold (estados `LEAVING`/`ENTERING`), `ceiling_allowance_m` e `reentry_breach_grace_sec` na reentrada, alvo de altitude `target_altitude_m - platform.height_m`, `height_above_floor_m = relative_altitude + height_m` para a projecao do alvo, limiar de pouso `touchdown_altitude_m - height_m` quando fora do totem (`_grounded_altitude`) e `departure_settle` antes de cruzar a borda. Todos consomem **um unico produtor** (`along_track_m` em `telemetry/odometry.py`); a Task 7B fixa a paridade com testes.
- Estacao de solo: `bebop_mission_control/` tem **modificacoes nao commitadas de outras sessoes** (ex.: `TacticalMap.tsx`, `ParameterSheet.tsx`, `launchReadiness.cjs`). So editar la o que a Task 8 pedir, e reler `git status` imediatamente antes (memoria: sessoes concorrentes compartilham a arvore).
- **Commits**: Conventional Commits em ingles tecnico, atomicos. **Nao commitar nem fazer push sem autorizacao explicita do usuario** (memoria `no-commit-without-authorization`, que prevalece sobre o check-out do `CLAUDE.md`). Cada tarefa termina preparando a mensagem de commit e parando no "gate de commit".
- Fim de tarefa: rodar os testes afetados. Fim do plano: `python3 -m pytest test/` com 100% de sucesso, descontadas apenas falhas comprovadamente pre-existentes registradas na Task 0.

## Decisoes de Arquitetura (justificativas)

**D1. A guarda de borda do totem nao muda de logica, so de eixo de entrada.**
`PlatformMonitor.update(along_track_m, ...)` e agnostico: recebe "posicao ao longo da trilha de ida, positiva para fora". A trilha de ida agora e a esquerda, entao o unico ponto que muda e o produtor: `telemetry/odometry.py:486-490`, que hoje calcula `along = -(cos(yaw)*dx + sin(yaw)*dy)` (projecao frontal) e passa a calcular a projecao para a esquerda `dx*sin(yaw) - dy*cos(yaw)` (com `dx = origem_x - x`, `dy = origem_y - y`). O campo `platform.edge_ahead_m` mantem o nome (contrato) e passa a significar "distancia do ponto de decolagem a borda ao longo da trilha de ida"; o valor `0.30` ja esta em `mission_config.json`. O override do marcador no RTL passa de `-ex_body_m` para `-ey_body_m`.

**D2. Stage 3 nao move o gimbal.** O enunciado diz que "a unica coisa a se movimentar e o gimbal" no Stage 4. Logo o Stage 3 mantem o tilt de busca (`gimbal.search_tilt_deg`) e comanda apenas `vy`. Como o drone nao avanca, a distancia horizontal ao alvo e constante desde a confirmacao: se o alvo esta dentro do quadro na confirmacao, continua dentro no Stage 3.

**D3. O "nadir" do Stage 4 e o tilt que centra o alvo, limitado por `gimbal.nadir_tilt_deg`.**
Sem avanco, o alvo continua a distancia horizontal `R` a frente. Descer o gimbal ate um nadir fixo (`-69` no default do codigo, `-25` no `mission_config.json` atual) so enquadra o alvo se `R = h / tan(|tilt|)`; para qualquer outro `R` o alvo sairia do quadro (meio campo vertical = 25 graus). Portanto o Stage 4 aplica uma lei de apontamento fechada: `tilt_desejado = tilt_atual - theta_y(pixel do alvo)` (angulo pinhole exato, `CameraIntrinsics.bearing`, independente de altitude), com rampa limitada por `gimbal.max_slew_limit_deg` e limites `[gimbal.nadir_tilt_deg, gimbal.search_tilt_deg]`. `nadir_tilt_deg` vira o tilt mais inclinado permitido. O criterio "enquadrado" e: erro vertical dentro da tolerancia por `vision.alignment_dwell_cycles` ciclos, OU tilt no piso `nadir_tilt_deg` (melhor enquadramento possivel).

**D4. Reuso de parametros existentes em vez de novos campos.** Nenhum campo novo em `mission_config.json`:
- dwell de centralizacao lateral: `vision.alignment_dwell_cycles`; tolerancia: `max(optical_center_tolerance_px, optical_center_tolerance_ratio * largura)` (ja calculada no controlador);
- "velocidade estabilizada" para o freeze: `inspection.settle_max_speed_mps`;
- creep de reaquisicao: `vision.reacquire_reverse_speed` / `reacquire_reverse_sec` (nomes preservados, semantica passa a ser creep lateral);
- janela do enquadramento por gimbal: derivada, `max(inspection.settle_timeout_sec, 2 * (search_tilt_deg - nadir_tilt_deg) / max_slew_limit_deg)`.

**D5. Governor recebe a demanda horizontal.** `AltitudeHoldGovernor.compute_vz(altitude, dt, vx_commanded)` usa `abs(vx_commanded)` como feedforward e varios dublês de teste tem exatamente essa assinatura. Nao renomear o kwarg: passar `vx_commanded=hypot(vx, vy)` e documentar no docstring que o argumento e a norma da demanda horizontal. `horizontal_scale()` ja e agnostico ao eixo.

**D6. `clamp_translation` ja autoriza `vy`** (satura `vx` e `vy` simetricamente em `kinematics.max_horizontal_speed`). A invariante "so esquerda na ida, so direita na volta" deve ser aplicada **no ponto de transmissao do step** (`max(0.0, vy)` e `min(0.0, vy)`), como o codigo atual faz com `vx`.

**D7. Pre-requisito fisico (para o relatorio, nao para codigo).** Com camera a `search_tilt_deg` e altitude `h` sobre o piso, um alvo so e detectavel se sua distancia horizontal a frente estiver na faixa `[h / tan(|tilt| + VFOV/2), h / tan(|tilt| - VFOV/2)]`. A trilha de varredura deve ser posicionada pelo operador a essa distancia do alvo. Ver Review Focus.

## File Structure

| Arquivo | Acao | Responsabilidade |
|---|---|---|
| `mvp_mission_bebop/steps/search.py` | Modificar | Cruzeiro lateral a esquerda (`vy > 0`), classe `LateralSearchStep` (alias `ForwardSearchStep` mantido) |
| `mvp_mission_bebop/controllers/visual_servoing.py` | Modificar (reescrita das fases) | Fases `CENTERING`, `LOCKED`, `REACQUIRING`; `vx == 0.0` estrutural |
| `mvp_mission_bebop/steps/tracking.py` | Modificar | Loop lateral, criterio `is_laterally_centered`, freeze, `blackboard` |
| `mvp_mission_bebop/controllers/gimbal_framing.py` | **Criar** | Lei pura de apontamento vertical do Stage 4 |
| `mvp_mission_bebop/controllers/__init__.py` | Modificar | Exportar `GimbalFramingController`, `FramingCommand` |
| `mvp_mission_bebop/steps/inspection.py` | Modificar | Fase de enquadramento por gimbal com drone parado, depois captura |
| `mvp_mission_bebop/steps/rtl.py` | Modificar | Cruzeiro de busca para a direita, override do marcador por `ey`, creep lateral |
| `mvp_mission_bebop/controllers/rtl_guidance.py` | Modificar | Troca de papeis dos canais na lei odometrica; `_coast` com creep lateral |
| `mvp_mission_bebop/estimation/platform_monitor.py` | Modificar | Nova funcao pura `outbound_track_m` + docstrings |
| `mvp_mission_bebop/telemetry/odometry.py` | Modificar | Produtor da posicao ao longo da trilha passa a usar `outbound_track_m` |
| `mvp_mission_bebop/controllers/altitude_hold.py` | Modificar (docstring) | Documentar `vx_commanded` como norma horizontal |
| `mvp_mission_bebop/parameters.py` | Modificar (docstrings) | Marcar campos inertes, reescrever semantica de `edge_ahead_m`, `reverse_cruise_velocity`, etc. |
| `mvp_mission_bebop/parameter_conflicts.py` e `bebop_mission_control/src/lib/parameterConflicts.ts` | Modificar em paralelo | Neutralizar W04/W05/W06 (geometria de aproximacao), recalcular W08 |
| `mvp_mission_bebop/config_audit.py` | Modificar (mensagens) | Textos que citam "aproximacao" e "avanco" |
| `test/support/fake_bebop/plant.py`, `scene.py`, `scripts/fake_bebop_driver.py` | Modificar | Borda do totem e revelacao do alvo no eixo Y |
| `test/test_lateral_search.py`, `test_lateral_servoing.py`, `test_gimbal_framing.py`, `test_lateral_rtl.py`, `test_outbound_track.py` | **Criar** | Cobertura nova |
| `test/test_stage3_integration.py`, `test_controllers.py`, `test_nadir_freeze.py`, `test_departure_settle.py`, `test_aruco_rtl.py`, `test_inspection_hover.py`, `test_platform_monitor.py`, `test_edge_guard.py`, `test_odometry.py`, `test_contracts.py`, `test_fly_path_*.py`, `test_stage_altitude_wiring.py`, `test_parameter_conflicts.py` | Atualizar | Ver tabela de testes obsoletos na Task 9 |
| `CLAUDE.md` (secao 1), `docs/CHECKLIST_VOO_REAL.md` | Modificar | Descricao do padrao lateral |

## Review Focus

Entradas e modos de falha que o enunciado implica e que nenhum teste de tarefa cobriria sem pedido explicito. Cada linha tem teste designado na tarefa indicada.

1. **Alvo fora da faixa vertical de visada** (perto demais para o tilt de busca). Esperado: Stage 2 nunca confirma, termina por timeout sem comandar `vx`; nada de avanco "para ajudar". Teste na Task 2.
2. **Derrapagem em X (off-track) maior que a meia largura do totem (0.30 m)**: a guarda de borda mede so o eixo lateral. Esperado: o Stage 2/3 nunca comanda `vx != 0`, e o relatorio final lista o risco. Teste na Task 2 (invariante `vx == 0.0` em todo Twist) e na Task 9 (emulador com plant 2D mostrando a saida do totem).
3. **Alvo sai do quadro pela lateral durante o servoing** (drone ultrapassa). Esperado: `REACQUIRING` segura posicao e faz creep lateral na direcao do ultimo erro, nunca `vx`; esgotada a janela, Stage 4 roda em modo degradado. Teste na Task 4.
4. **Deteccao "predita" (coast do tracker) nao pode travar o freeze.** So deteccoes medidas acumulam credito de centralizacao. Teste na Task 4.
5. **Marcador ultrapassado na volta** (drone passa da base voando para a direita). Esperado: o creep de reenquadramento do `ArucoCenteringController` e lateral para a esquerda, nunca `vx`. Teste na Task 7.
6. **`lateral_normalized_to_mps` calibrado diferente de `normalized_to_mps`.** O setpoint `forward_cruise_velocity` e normalizado; o `vy` transmitido deve ter a mesma magnitude normalizada independente do ganho lateral. Teste na Task 2.
7. **Perda de alvo durante o enquadramento do gimbal (Stage 4).** Esperado: manter o tilt da solucao em malha aberta calculada a partir do ultimo pixel do Stage 3, capturar em modo degradado, nunca ir cegamente ao piso. Teste na Task 6.

---

### Task 0: Check-in e linha de base

**Files:** nenhum arquivo e alterado.

- [ ] **Step 1: Sincronizar e inspecionar o estado**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop && git status --short && git pull origin main
```
Expected: se `git pull` falhar por divergencia ou por arquivos modificados, **nao** usar stash/reset/checkout (outras sessoes editam a arvore). Registrar o estado e seguir.

- [ ] **Step 2: Ativar o ambiente e rodar a linha de base**

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/ -q -p no:cacheprovider --ignore=test/test_fly_path_emulated.py --ignore=test/test_fly_path_totem.py 2>&1 | tail -30
python3 -m pytest test/test_fly_path_emulated.py test/test_fly_path_totem.py -q -p no:cacheprovider 2>&1 | tail -15
```
Registrar **por nome** toda falha pre-existente. Na analise deste plano ja havia ao menos uma: `test/test_announcer_calls.py::test_standalone_runs_still_speak_through_the_local_announcer` (falha em baseline, independente deste trabalho). Falhas pre-existentes nao sao corrigidas aqui e nao contam contra o criterio de 100%, mas devem aparecer no relatorio final.

- [ ] **Step 3: Mapear os usos que as proximas tarefas tocam**

```bash
grep -rn "ForwardSearchStep" mvp_mission_bebop test
grep -rn "TrackingPhase\|nadir_frozen\|nadir_committed\|nadir_aligned" mvp_mission_bebop test
grep -rn "_cruise_backward\|_report_pad_position\|along_track" mvp_mission_bebop test
```
Guardar a saida: e a lista de call sites a atualizar.

---

### Task 1: Eixo da trilha de ida na guarda de borda (D1) e emulador 2D

**Files:**
- Modify: `mvp_mission_bebop/estimation/platform_monitor.py` (nova funcao pura; docstring do modulo)
- Modify: `mvp_mission_bebop/telemetry/odometry.py:486-490`
- Modify: `test/support/fake_bebop/plant.py:92-130,461,473`, `test/support/fake_bebop/scene.py:90-182`, `scripts/fake_bebop_driver.py:114,177,188`
- Test: `test/test_outbound_track.py` (criar), `test/test_platform_monitor.py`, `test/test_odometry.py`, `test/test_edge_guard.py`, `test/test_fake_bebop_plant.py`, `test/test_fake_bebop_scene.py`

**Interfaces:**
- Produces:
  `outbound_track_m(origin_dx: float, origin_dy: float, yaw_rad: float) -> float` em `platform_monitor.py`. Retorna o deslocamento do drone para a esquerda do ponto de origem, projetado no corpo, positivo para a esquerda. `origin_dx = origem_x - x`, `origin_dy = origem_y - y`. Formula: `origin_dx * sin(yaw) - origin_dy * cos(yaw)`. Valida argumentos: nao finitos levantam `ValueError`.
- Consumes: `PlatformMonitor.update(along_track_m, height, dt)` sem alteracao de assinatura.

- [ ] **Step 1: Escrever os testes que falham** em `test/test_outbound_track.py`:
  - `yaw = 0`, drone em `y = +0.5` (esquerda) com origem em `(0, 0)` (`origin_dy = -0.5`): `outbound_track_m(0.0, -0.5, 0.0) == pytest.approx(0.5)`.
  - mesmo deslocamento com drone em `x = +0.5` (a frente): resultado `0.0` (movimento frontal nao conta).
  - drone a direita (`y = -0.3`): resultado `-0.3`.
  - `yaw = pi/2` com deslocamento mundial `+x` (que e a esquerda do corpo): resultado `+0.5`.
  - entrada `nan` ou `inf` levanta `ValueError`.
  - integracao com `OdometrySupervisor`: injetar amostras sinteticas (`inject_synthetic_sample`, ver `test/test_odometry.py` para o padrao) andando em `+y` ate passar de `edge_ahead_m + h*tan(half_angle)`; o `platform_report().state` percorre `ON -> LEAVING -> OFF` e `snapshot().along_track_m` e positivo. Andando em `+x` o estado permanece `ON`.

- [ ] **Step 2: Rodar e ver falhar**

```bash
python3 -m pytest test/test_outbound_track.py -v
```
Expected: FAIL (`ImportError` em `outbound_track_m`).

- [ ] **Step 3: Implementar** `outbound_track_m` em `platform_monitor.py` e trocar a linha de calculo em `odometry.py` para usa-la. Reescrever o paragrafo do docstring do modulo que diz "O modelo e unidimensional, ao longo da trilha de ida. O Stage 3 pode mover a aeronave lateralmente..." para: o modelo e unidimensional ao longo da trilha de ida (agora a esquerda); a deriva em X nao e vista pelo monitor, e e o risco medido no campo.

- [ ] **Step 4: Atualizar o emulador.** `surface_height` e `sonar_range` em `plant.py` passam a receber `(x, y)` e a modelar o totem como quadrado centrado no ponto de decolagem: a distancia alem da borda e `offset = max(abs(x), abs(y)) - edge_ahead_m` (positiva = fora do totem). `surface_height` devolve `0` com `offset <= 0` e `-platform_height_m` caso contrario; `sonar_range` usa o mesmo `offset` na formula do eco mais proximo (`hypot(z, offset)` enquanto `offset <= z*tan(half_angle)`). Atualizar os chamadores em `plant.py:461,473` e a docstring. O modelo do monitor continua 1D em Y (ver Review Focus 2): a plant 2D existe para que deriva em X e ultrapassagem a direita sejam fisicamente reais nos testes de emulador. Em `scene.py`, `PhaseScene` revela o alvo quando `view.y >= reveal_y_m`; renomear `reveal_x_m` para `reveal_y_m`, atualizar validacao e `build_scene`; `scripts/fake_bebop_driver.py` troca `--reveal-x` por `--reveal-y` (manter `--reveal-x` como alias deprecado que apenas mapeia para `--reveal-y`, para nao quebrar chamadores externos; remover no commit seguinte se `grep -rn "reveal-x"` nao achar usos). Atualizar `test/test_fake_bebop_plant.py` e `test/test_fake_bebop_scene.py` para o novo eixo.

- [ ] **Step 5: Atualizar testes existentes** `test_platform_monitor.py` (so docstrings: o monitor e agnostico), `test_edge_guard.py` e `test_odometry.py` (alimentar a trilha por `y`), e rodar:

```bash
python3 -m pytest test/test_outbound_track.py test/test_platform_monitor.py test/test_edge_guard.py test/test_odometry.py test/test_fake_bebop_plant.py test/test_fake_bebop_scene.py -v
```
Expected: PASS.

- [ ] **Step 6: Gate de commit** (nao commitar sem autorizacao). Mensagem proposta: `feat(platform): measure the pad edge crossing along the lateral outbound track`.

---

### Task 2: Stage 2, varredura lateral a esquerda

**Files:**
- Modify: `mvp_mission_bebop/steps/search.py` (modulo inteiro: docstring, `ForwardSearchStep`, `_command`, `_cruise`, logs)
- Modify: `mvp_mission_bebop/steps/__init__.py`, `mvp_mission_bebop/mission.py:76-90,1170-1180`
- Modify: `mvp_mission_bebop/controllers/altitude_hold.py` (docstring de `compute_vz`, D5)
- Test: `test/test_lateral_search.py` (criar), `test/test_departure_settle.py`, `test/test_stage_altitude_wiring.py`, `test/test_announcer_calls.py:106`

**Interfaces:**
- Produces: `LateralSearchStep(BaseStep)` com nome `"STEP 2: Lateral Left Search"`; `ForwardSearchStep = LateralSearchStep` (alias mantido em `search.py` e `steps/__init__.py`). Metodos privados: `_command(ctx, profile, shaper, dt, target_mps) -> None` (transmite `vx=0.0`, `vy` shaped), `_cruise`, `_halt` (mesmas assinaturas de hoje).
- Consumes: `SpeedCalibration.to_mps(v, axis="lateral")`, `SpeedCalibration.to_normalized(mps, axis="lateral")`, `QuantizedCommandShaper`, `JerkLimitedProfile`.

Regras de implementacao (instrucoes, sem codigo):

1. `cruise_mps = ctx.speed_calibration.to_mps(kinematics_cfg.forward_cruise_velocity, axis="lateral")`. O setpoint e o mesmo parametro "Velocidade de Deslocamento".
2. `_command`: `profiled = profile.step(target_mps, dt)`; `vy = shaper.shape(to_normalized(profiled, axis="lateral"), dt)`; `vz = governor.compute_vz(relative_altitude, dt, vx_commanded=abs(vy))`; `clamp_kinematics(vz, 0.0)`; `clamp_translation(0.0, max(0.0, vy))`; `move_velocity(vx=safe_vx, vy=safe_vy, ...)`. O literal `0.0` em `vx` e a invariante estrutural; `max(0.0, vy)` impede pulso sigma-delta de polaridade errada (espelho do `max(0.0, vx)` atual).
3. `_settle_before_departure`, confirmacao por histerese, `_act`, `_halt`, `_locate` e milestones permanecem; `_locate` ja descreve `bearing_deg`, `forward_m` e `lateral_m` e nao muda.
4. Logs e docstring de modulo: "Linear forward search" vira "lateral left search"; invariantes impressas: `vx == 0.0`, `vyaw == 0.0`, `vy >= 0.0`.
5. `_announce("Acidente detectado", ...)`: manter a chave de acao; ajustar o texto de `etapa` para "iniciando alinhamento lateral" somente se `test_announcer_calls.py` nao fixar o texto (verificar antes).

- [ ] **Step 1: Escrever os testes que falham** em `test/test_lateral_search.py` (reusar o `Ctx` de `test_stage_altitude_wiring.py` como `test_departure_settle.py` faz):
  - `test_every_transmitted_twist_has_vx_zero_and_vy_nonnegative`: rodar `LateralSearchStep().execute(ctx)` sem deteccoes; para todo comando de `ctx.drone.commands`: `vx == 0.0`, `vy >= 0.0`, `vyaw == 0.0`; e `any(vy > 0.0)`.
  - `test_cruise_reaches_the_configured_setpoint`: `max(vy)` fica dentro de um passo de quantizacao (`rtl.quantization_step`) de `kinematics.forward_cruise_velocity`.
  - `test_the_setpoint_is_independent_of_the_lateral_gain` (Review Focus 6): repetir com `lateral_normalized_to_mps = 2.0` e `SpeedCalibration` correspondente; o `max(vy)` normalizado continua igual.
  - `test_the_governor_is_fed_the_lateral_demand`: governor gravador com a assinatura `compute_vz(_altitude, _dt=None, vx_commanded=0.0)`; todas as chamadas durante o cruzeiro recebem valor `> 0` e nenhuma recebe `vx` nao nulo.
  - `test_the_halt_ramps_vy_to_zero_without_touching_vx`: deteccao confirmada apos N ciclos; ultimos comandos tem `vy == 0.0`, `vx == 0.0`, e a sequencia de `vy` e monotona nao crescente depois da confirmacao.
  - `test_a_target_closer_than_the_view_band_never_moves_the_drone_forward` (Review Focus 1): sem deteccoes e timeout curto, `vx == 0.0` em todo comando e `blackboard.target_confirmed is False`, status `SUCCESS` (comportamento atual de janela expirada).
  - `test_abort_and_health_failure_paths_are_unchanged`: `emergency_event.set()` retorna `ABORTED` e o ultimo Twist tem `vy == 0.0` apos a rampa.

- [ ] **Step 2: Rodar e ver falhar**

```bash
python3 -m pytest test/test_lateral_search.py -v
```
Expected: FAIL (cruzeiro ainda em `vx`).

- [ ] **Step 3: Implementar** conforme as regras acima, incluindo o alias e a atualizacao de `mission.py`/`__init__`.

- [ ] **Step 4: Atualizar testes dependentes.** `test_departure_settle.py`: o gravador `recording_move` registra `(t, vy)` no lugar de `(t, vx)` e as asserts `vx > 0.0` viram `vy > 0.0`; `test_stage_altitude_wiring.py` e `test_announcer_calls.py`: ajustar o que depender de `vx` do cruzeiro.

- [ ] **Step 5: Rodar**

```bash
python3 -m pytest test/test_lateral_search.py test/test_departure_settle.py test/test_stage_altitude_wiring.py test/test_announcer_calls.py test/test_edge_guard.py test/test_safety_invariants.py -v
```
Expected: PASS (descontada a falha pre-existente de `test_announcer_calls` registrada na Task 0).

- [ ] **Step 6: Gate de commit.** `feat(search): cruise laterally to the left with vx locked at zero`.

---

### Task 3: Documentar campos inertes e contratos de configuracao

**Files:**
- Modify: `mvp_mission_bebop/parameters.py` (docstrings de `forward_cruise_velocity`, `max_approach_forward_speed`, `max_approach_decel_mps2`, `approach_corridor_ratio`, `min_alignment_gain`, `nadir_deadband_ratio`, `nadir_alignment_ratio`, `decentered_frames_to_revert`, `nadir_exit_frames`, `nadir_arrival_tolerance_factor`, `nadir_range_threshold_m`, `centering_tilt_band_up/down_deg`, `reverse_cruise_velocity`, `max_lateral_speed`, `edge_ahead_m`)
- Modify: `mvp_mission_bebop/config_audit.py` (textos das linhas ~134 e ~225-232)
- Test: `test/test_parameters.py`, `test/test_config_audit.py`, `test/test_contracts.py`

**Interfaces:** nenhuma assinatura muda; `MissionParameters` e `mission_config.json` permanecem identicos em estrutura.

- [ ] **Step 1: Teste de contrato primeiro.** Em `test/test_contracts.py` adicionar `test_the_lateral_refactor_adds_and_removes_no_configuration_field`: carregar `MissionParameters()` e `mission_config.json`, serializar, e comparar o conjunto de chaves com o do HEAD (lista fixa dentro do teste, copiada da serializacao atual). Rodar: PASS ja agora (caracterizacao); ele guarda as tarefas seguintes.

- [ ] **Step 2: Atualizar docstrings.** Campos inertes ganham a linha `Inert in the lateral flight pattern; retained for the GCS contract.` `forward_cruise_velocity`: "Velocidade de Deslocamento; setpoint normalizado do cruzeiro lateral a esquerda (vy)". `reverse_cruise_velocity`: "sinal negativo mantido (regra E05); o retorno voa a direita com `vy = -abs(.)`". `edge_ahead_m`: "distancia do ponto de decolagem a borda do totem ao longo da trilha de ida (lateral esquerda)". `max_lateral_speed`: "teto do trim fora da trilha (eixo X do corpo) no retorno odometrico" (ver Task 7). Em `config_audit.py` ajustar apenas texto de motivo que menciona "approach"/"forward footprint" (linha ~225: "the marker search is saturated to a standstill" permanece).

- [ ] **Step 3: Rodar**

```bash
python3 -m pytest test/test_parameters.py test/test_config_audit.py test/test_contracts.py test/test_parameter_persistence.py -v
```
Expected: PASS.

- [ ] **Step 4: Gate de commit.** `docs(parameters): mark approach parameters inert and restate lateral semantics`.

---

### Task 4: Controlador de servoing lateral (CENTERING -> LOCKED -> REACQUIRING)

**Files:**
- Modify: `mvp_mission_bebop/controllers/visual_servoing.py` (reescrita das fases; docstring de modulo)
- Test: `test/test_lateral_servoing.py` (criar); substituir os testes de aproximacao em `test/test_controllers.py` (ver Task 9)

**Interfaces (assinaturas exatas):**

```python
class TrackingPhase(str, Enum):
    CENTERING = "centering"
    LOCKED = "locked"
    REACQUIRING = "reacquiring"

@dataclass(frozen=True)
class ServoCommand:
    vx: float                      # sempre 0.0
    vy: float
    tilt_deg: float                # sempre igual ao current_tilt_deg recebido
    pixel_error: float
    ground_range_m: Optional[float]
    depression_deg: Optional[float]
    phase: TrackingPhase
    laterally_centered: bool       # |err_x| dentro da tolerancia neste ciclo
    locked: bool                   # freeze latched
    note: str
    tolerance_px: float = 0.0
    alignment: float = 1.0         # 1.0 dentro da tolerancia, decai com o erro lateral (diagnostico)
    recovery_exhausted: bool = False

class VisualServoingController:
    def __init__(self, gimbal_config, gimbal_pid_cfg, lateral_pid_cfg, vision_cfg, kinematics_cfg, calibration=None) -> None: ...   # inalterado
    def compute(self, target_center, frame_dimensions, current_tilt_deg, relative_altitude_m, dt, *,
                speed_scale: float = 1.0, measured: bool = True, airframe_at_rest: bool = True) -> ServoCommand: ...
    def note_target_lost(self, current_tilt_deg: float, dt: float) -> ServoCommand: ...
    @property
    def locked(self) -> bool: ...
    def reset(self) -> None: ...
```

Os campos removidos (`nadir_aligned`, `nadir_frozen`, `nadir_committed`) e as fases `APPROACHING`/`NADIR` saem. O construtor nao muda (dublês de teste o usam). O `speed_scale` e aceito por compatibilidade de chamada e ignorado (nao ha avanco), documentar.

Regras de implementacao:

1. **Lei lateral (inalterada):** `vy_demand = FilteredPID(norm_err_x)`, formatada por `_lateral_shaper`; sinal: alvo a direita do centro (`err_x > 0`) gera `vy < 0`. Manter `_lateral_command` e `_shape_lateral` e seus docstrings.
2. **`vx == 0.0` e estrutural:** o `ServoCommand` e construido com literal `0.0`; apagar `_forward_profile`, `_approach_speed`, `_alignment_gain` de avanco, `_pitch_alignment_gain`, `_fine_speed`, `inspection_standoff_m`, `_reached_nadir`, `_enter_approach`, `_enter_nadir`, `_pointing_tilt` e `_slew` (o gimbal e do Stage 4). `alignment` continua calculado pela mesma rampa linear de `_alignment_gain` apenas para o HUD.
3. **Centralizacao:** `in_gate = abs(err_x) <= self._tolerance_px`. Credito `_centered_frames`: `+1` quando `in_gate and measured`; `-1` (piso 0) quando fora do gate; inalterado em quadro nao medido (predito). Lock quando `_centered_frames >= max(1, alignment_dwell_cycles)` **e** `airframe_at_rest`. Review Focus 4.
4. **`_enter_lock`:** `_phase = LOCKED`, `_locked = True`, `self._lateral.reset()`, `self._lateral_shaper.reset()` (o estado do shaper e zerado junto com a saida, pelo motivo documentado no `_enter_nadir` atual: um deslocamento acumulado seria descarregado como pulso em uma fase cuja premissa e que o drone parou). Em `LOCKED`, todo ciclo devolve `vx=vy=0.0`, `locked=True`, sem reversao.
5. **Reaquisicao lateral:** `note_target_lost` mantem a maquina (`_begin_reacquire`, `_reacquired`, `reacquire_confirm_frames`, `reacquire_timeout_sec`, `recovery_exhausted`), com estas mudancas: `tilt_deg = current_tilt_deg` (sem sweep de gimbal); `vx = 0.0`; `vy` e um creep de modulo `abs(vision.reacquire_reverse_speed)` na direcao do ultimo erro (`anchor.target_px[0] - width/2`): erro positivo (alvo saiu pela direita) gera `vy < 0`, negativo gera `vy > 0`; creep somente enquanto `_reacquire_elapsed_sec <= vision.reacquire_reverse_sec` e somente se `abs(ultimo_err_x) > tolerance_px`; senao `vy = 0.0` (queda de deteccao no centro nao autoriza deslocamento). Passar o creep pelo `_lateral_shaper`. O `TrackAnchor` perde o campo `phase`/`depression` se nao usados (manter `ground_range_m` para o HUD).
6. Atualizar o docstring do modulo: tres fases, sem aproximacao, e a justificativa fisica (D2).

- [ ] **Step 1: Escrever os testes que falham** em `test/test_lateral_servoing.py` com o `VisualServoingController` real e `SpeedCalibration(1.0)`:
  - `test_vx_is_exactly_zero_in_every_phase`: sequencia que cobre `CENTERING`, `LOCKED` e `REACQUIRING`; `cmd.vx == 0.0` sempre.
  - `test_the_tilt_is_never_changed`: `cmd.tilt_deg == current_tilt_deg` em todas as fases (D2).
  - `test_lateral_command_opposes_the_horizontal_error`: `err_x > 0` gera `vy < 0` e vice-versa (migrar `test_lateral_command_opposes_horizontal_error`).
  - `test_the_lock_needs_dwell_cycles_inside_the_gate`: alvo no centro, `airframe_at_rest=True`: `locked` falso ate o `alignment_dwell_cycles`-esimo quadro, verdadeiro nele.
  - `test_predicted_frames_earn_no_credit` (Review Focus 4): `measured=False` por muitos quadros no centro nunca trava.
  - `test_the_lock_waits_for_the_airframe_to_be_at_rest`: `airframe_at_rest=False` impede o lock mesmo com credito cheio; passa a `True` e trava no ciclo seguinte.
  - `test_the_lock_is_latched_and_silences_the_loop`: apos o lock, qualquer erro devolve `vy == 0.0`, `locked is True`, fase `LOCKED`; o shaper nao descarrega pulso (rodar 20 ciclos e checar `vy == 0.0` em todos).
  - `test_a_jittered_frame_decays_credit_instead_of_resetting_it`: um quadro fora do gate subtrai 1, nao zera.
  - `test_reacquisition_creeps_toward_the_last_seen_side` (Review Focus 3): alvo visto em `x = 800` (direita), depois perdido: `vy < 0` durante `reacquire_reverse_sec`; visto em `x = 50`: `vy > 0`; visto no centro: `vy == 0.0`; sempre `vx == 0.0` e `tilt` inalterado.
  - `test_reacquisition_exhausts_and_reports_it`: apos `reacquire_timeout_sec`, `recovery_exhausted is True` e `vy == 0.0`.
  - `test_reset_returns_to_centering`.

- [ ] **Step 2: Rodar e ver falhar**

```bash
python3 -m pytest test/test_lateral_servoing.py -v
```
Expected: FAIL (`TrackingPhase.LOCKED` nao existe).

- [ ] **Step 3: Implementar** conforme as regras. Verificar com `grep -n "APPROACHING\|NADIR\|nadir_" mvp_mission_bebop` que so restam referencias legitimas (`gimbal.nadir_tilt_deg`).

- [ ] **Step 4: Rodar**

```bash
python3 -m pytest test/test_lateral_servoing.py -v
```
Expected: PASS. (Os testes antigos de `test_controllers.py` e `test_nadir_freeze.py` falharao ate a Task 9; nao rodar aqui.)

- [ ] **Step 5: Gate de commit.** `refactor(servoing): reduce the visual servo to lateral centering with a latched freeze`.

---

### Task 5: Step 3, loop lateral e criterio de conclusao

**Files:**
- Modify: `mvp_mission_bebop/steps/tracking.py` (`_servo`, `_apply`, `_nadir_dwell_earned`, `execute`, docstrings)
- Test: `test/test_stage3_integration.py` (reescrever o cenario), `test/test_stage3_parameter_authority.py`, `test/test_stage_altitude_wiring.py`

**Interfaces:**
- Consumes: `VisualServoingController.compute(..., measured=..., airframe_at_rest=...)`, `ServoCommand.locked`, `ServoCommand.laterally_centered` (Task 4).
- Produces: `ctx.blackboard.approach_finished = True` quando o Stage 3 termina travado; `ctx.blackboard.confirmed_target_px` e `ctx.blackboard.confirmed_tilt_deg` **atualizados no instante do lock** com o ultimo pixel medido e o tilt atual (o Stage 4 os usa como solucao de apontamento em malha aberta, Review Focus 7).

Regras:

1. `_servo` passa `measured` (ja retornado por `_resolve_target`) e `airframe_at_rest = snapshot.horizontal_speed <= ctx.params.inspection.settle_max_speed_mps` (usar `getattr` defensivo como o resto do arquivo faz com o snapshot).
2. O loop termina com `approach_finished = True` no ciclo em que `command.locked` e verdadeiro; nesse ciclo o step transmite `_hold(ctx)` (`vx=vy=0`, governor engajado) imediatamente: e o "freeze horizontal imediato" do enunciado. Remover a contagem `aligned_cycles` e `_nadir_dwell_earned`: o dwell vive no controlador.
3. `_apply`: nao comanda gimbal (remover o bloco `camera_control`); `vx` e sempre `0.0`; `clamp_translation(0.0, command.vy)` (bilateral: o servo corrige nos dois sentidos); `compute_vz(..., vx_commanded=abs(command.vy))`.
4. `_hold` e `_publish` inalterados; o texto de status passa a `STEP 3: CENTERING|LOCKED|REACQUIRING ... | ERR_X`. Manter o prefixo `STEP 3:` e a presenca de `ERR:`.
5. `_horizontal_scale` deixa de ser usada pelo controlador; remover o helper e a passagem de `speed_scale`.
6. Docstring de modulo reescrita: loop lateral fechado, sem avanco, altitude two-sided mantida (a translacao lateral tambem custa sustentacao), termina no freeze.
7. `execute` mantem: `if not target_confirmed: approach_finished=False; SUCCESS`; milestone `mission.approaching` com a mesma chave; ao final do loop sem lock (timeout ou `recovery_exhausted`): `approach_finished = False` e log de aviso.

- [ ] **Step 1: Reescrever `test/test_stage3_integration.py`** mantendo os fixtures (`FakeClock`, `Scene`, `Ctx`, `SynchronousPerception`): ajustar `Scene.advance` para ignorar `vx` (o `ground_range_m` e constante) e manter `lateral_m -= vy * dt`. Novos testes (substituem os de standoff, com os nomes abaixo):
  - `test_stage3_converges_with_lateral_corrections_only`: alvo com `lateral_m = +0.6` e depois `-0.6`; `approach_finished is True`; **todo** comando em `ctx.drone.commands` tem `vx == 0.0` e `vyaw == 0.0`; `Scene.ground_range_m` inalterado.
  - `test_the_airframe_is_frozen_on_the_cycle_the_stage_completes`: o ultimo comando tem `vx == vy == 0.0`.
  - `test_the_gimbal_is_not_commanded_in_stage_3`: `ctx.drone.tilts == []`.
  - `test_a_laterally_offset_target_is_centred_on_either_side` (parametrizado em `+/-`): o erro em pixels no ultimo quadro medido esta dentro da tolerancia.
  - `test_the_stage_completes_well_inside_its_deadline`: tempo simulado `< 0.5 * timeouts.tracking_timeout_sec` para offset de 0.6 m com os ganhos do `mission_config.json` (`lateral_pid.output_limits = +/-0.04`). **Se falhar, nao mexer no codigo**: registrar no relatorio que o ganho/limite lateral precisa de ajuste de bancada (a correcao e de calibracao, nao de logica).
  - `test_a_brief_occlusion_does_not_trigger_reacquisition` e `test_the_step_recovers_from_a_dropout_instead_of_ending` (migrados): com `blackout`, a fase passa por `REACQUIRING` e volta a `CENTERING`; `vx == 0.0` sempre.
  - `test_a_permanent_loss_terminates_with_approach_unfinished`: `approach_finished is False`, `SUCCESS`.
  - `test_an_emergency_aborts_the_stage_without_commanding_motion` (mantido).
  - `test_the_vertical_invariant_holds_for_every_transmitted_command` (mantido).
  - `test_tracking_apply_feeds_the_horizontal_demand_to_the_governor`: `RecordingGovernor` recebe `abs(vy)` (substitui `test_tracking_apply_feeds_the_commanded_vx_to_the_governor`).
  - `test_the_lock_publishes_the_last_pixel_and_tilt_for_stage_4`: `blackboard.confirmed_target_px` e `confirmed_tilt_deg` refletem o ultimo quadro medido.
  - `test_a_stationary_check_gates_the_lock`: odometria dublê com `horizontal_speed` acima de `inspection.settle_max_speed_mps` impede o lock ate cair.

- [ ] **Step 2: Rodar e ver falhar**

```bash
python3 -m pytest test/test_stage3_integration.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implementar** o step conforme as regras.

- [ ] **Step 4: Rodar**

```bash
python3 -m pytest test/test_stage3_integration.py test/test_stage3_parameter_authority.py test/test_lateral_servoing.py test/test_stage_altitude_wiring.py -v
```
Expected: PASS.

- [ ] **Step 5: Gate de commit.** `feat(tracking): servo laterally and freeze on lateral centering`.

---

### Task 6: Stage 4, enquadramento exclusivo por gimbal

**Files:**
- Create: `mvp_mission_bebop/controllers/gimbal_framing.py`
- Modify: `mvp_mission_bebop/controllers/__init__.py`, `mvp_mission_bebop/steps/inspection.py` (`execute`, nova `_frame_with_gimbal`, docstring de modulo)
- Test: `test/test_gimbal_framing.py` (criar), `test/test_inspection_hover.py`, `test/test_nadir_freeze.py` (substituir, ver Task 9)

**Interfaces (assinaturas exatas):**

```python
@dataclass(frozen=True)
class FramingCommand:
    tilt_deg: float                 # tilt absoluto a comandar
    vertical_error_px: float        # err_y do quadro (nan sem alvo)
    framed: bool                    # enquadrado, seja por erro vertical ou por piso
    at_floor: bool                  # tilt no piso nadir_tilt_deg (+ tolerancia)
    note: str

class GimbalFramingController:
    def __init__(self, gimbal_cfg: GimbalConstraintsConfig, vision_cfg: VisionConfig) -> None: ...
    def reset(self) -> None: ...
    def open_loop_tilt(self, confirmed_px: Tuple[float, float], confirmed_tilt_deg: float,
                       frame_dimensions: Tuple[int, int]) -> float: ...
    def compute(self, target_center: Optional[Tuple[float, float]], frame_dimensions: Tuple[int, int],
                current_tilt_deg: float, dt: float) -> FramingCommand: ...
```

Lei (D3), em `compute`:
1. Com alvo: `theta_y = CameraIntrinsics(...).bearing(target_center)[1]` (rad, positivo abaixo do eixo); `desired = current - degrees(theta_y)`; `tilt = current + gimbal.tracking_gain * (desired - current)`; rampa e limites: `max_step = gimbal.max_slew_limit_deg * dt`, `clamp(tilt, [nadir_tilt_deg, search_tilt_deg])`.
2. Sem alvo (`None`): `tilt = current_tilt_deg` (segura), `vertical_error_px = nan`, `framed = False`.
3. `framed` quando `abs(err_y) <= tolerance_px` por `max(1, alignment_dwell_cycles)` quadros consecutivos (contador no controlador, zerado em quadro sem alvo), ou quando `tilt <= nadir_tilt_deg + nadir_tilt_tolerance_deg` com alvo presente.
4. `open_loop_tilt`: mesma formula aplicada ao ultimo pixel do Stage 3, com os mesmos limites; e o ponto de partida e o fallback sem deteccao.
5. Validacao defensiva: `dt <= 0`, dimensoes nao positivas ou entradas nao finitas levantam `ValueError`; `target_center` fora do tipo levanta `TypeError`.
6. Modulo puro: sem clock, sem ROS.

Regras do step (`inspection.py`):
1. Remover do `execute` o trecho que comanda `nadir_tilt_deg` incondicionalmente e atribui `ctx.current_tilt_deg`.
2. Nova fase `_frame_with_gimbal(ctx) -> bool` (retorna `framed`), chamada dentro da `altitude_hold_window` antes de `_await_stillness`:
   - Tilt inicial: `controller.open_loop_tilt(blackboard.confirmed_target_px, blackboard.confirmed_tilt_deg, frame_dims)` aplicado como primeiro setpoint (a rampa do controlador cuida do passo); se `confirmed_target_px` for `None`, partir do tilt atual.
   - Laco a `kinematics.control_loop_hz` com `Deadline(janela)`, janela derivada em D4: **a cada ciclo** `_hold(ctx)` (`vx=vy=0`, governor), checagem de `ctx.interrupted()` e de saude (`ctx.failsafe.evaluate_system_health()` fora de `no_fly`, como o Stage 3), amostra de perception dentro de `ctx.perception.session(conf=vision.confidence_threshold, imgsz=vision.inference_imgsz)`; novo quadro: `controller.compute(...)`; se `abs(cmd.tilt_deg - ctx.current_tilt_deg) > 1e-3`: `ctx.drone.camera_control(tilt=cmd.tilt_deg, pan=0.0)` e `ctx.current_tilt_deg = cmd.tilt_deg`. Sai com `framed` ou no timeout.
   - Nenhum `vx`, `vy`, `vyaw` nao nulo e transmitido em toda a fase (invariante do enunciado).
   - Timeout sem `framed`: manter o tilt em malha aberta (Review Focus 7), `degraded = True` (reaproveita o caminho existente: `settled = settled and not degraded`, que grava `settled: false` no sidecar; **nao** adicionar campos ao sidecar).
   - Antes de `_capture`, a sessao de perception e fechada (`ROSCam` serve um consumidor so, como o `_hover` ja documenta).
3. `degraded` passa a ser `not approach_finished or not framed`.
4. Docstring de modulo: aeronave em pairado absoluto, unico atuador em movimento e o gimbal, captura apos estabilizacao estatica.

- [ ] **Step 1: Escrever os testes que falham** em `test/test_gimbal_framing.py`:
  - `test_a_target_below_the_axis_pitches_the_gimbal_down`: `err_y > 0` gera `tilt_deg < current`; `err_y < 0` gera `tilt_deg > current`.
  - `test_closed_loop_converges_on_a_simulated_target`: usar a classe `Scene` de `test_stage3_integration.py` (alvo a `R = 4 m`, `h = 1.8 m`, tilt inicial `-10`); iterar `compute` com `dt = 1/16` ate `framed`; `abs(err_y_final) <= tolerance_px` e `tilt_final ≈ -degrees(atan(h/R))` dentro de 1 grau. Valida o sinal de `bearing()`.
  - `test_the_slew_limit_is_respected`: `abs(tilt_k - tilt_{k-1}) <= max_slew_limit_deg * dt + 1e-9` em todo ciclo.
  - `test_the_gimbal_never_leaves_its_band`: com `R` pequeno, o tilt para no piso `nadir_tilt_deg`; `at_floor is True` e `framed is True` com alvo ainda visivel.
  - `test_no_target_holds_the_tilt`: `target_center=None` devolve `tilt_deg == current`, `framed is False`, `vertical_error_px` e `nan`.
  - `test_open_loop_tilt_matches_the_closed_loop_fixed_point`: `open_loop_tilt(px, tilt, dims)` aplicado ao pixel de um alvo conhecido ≈ tilt de convergencia (tolerancia 1 grau).
  - `test_invalid_inputs_raise`: `dt = 0`, dimensoes `(0, 480)`, `nan` levantam `ValueError`.

  Em `test/test_inspection_hover.py` (reusar seus dublês e `SynchronousPerception`), adicionar:
  - `test_stage4_never_translates`: `execute` completo: todo Twist tem `vx == vy == vyaw == 0.0`.
  - `test_stage4_moves_only_the_gimbal`: `ctx.drone.tilts` nao vazio e monotono nao crescente (so desce) e dentro de `[nadir_tilt_deg, search_tilt_deg]`.
  - `test_framing_failure_degrades_the_capture_without_aborting` (Review Focus 7): perception sem deteccoes, `approach_finished=True`; o stage ainda captura, retorna `SUCCESS`, e o tilt final e o da solucao em malha aberta (nao o piso).
  - `test_an_abort_during_framing_returns_aborted_without_capturing`.
  - Os testes existentes de `_await_stillness`, captura e ACK nativo continuam passando sem alteracao de comportamento (ajustar apenas os dublês para ter `perception`, `frame_width/height`, `blackboard.confirmed_target_px/tilt`).

- [ ] **Step 2: Rodar e ver falhar**

```bash
python3 -m pytest test/test_gimbal_framing.py test/test_inspection_hover.py -v
```
Expected: FAIL (modulo inexistente).

- [ ] **Step 3: Implementar** o controlador, o export em `controllers/__init__.py` e o step.

- [ ] **Step 4: Rodar**

```bash
python3 -m pytest test/test_gimbal_framing.py test/test_inspection_hover.py test/test_stage_altitude_wiring.py -v
```
Expected: PASS.

- [ ] **Step 5: Gate de commit.** `feat(inspection): frame the target with the gimbal alone before the forensic capture`.

---

### Task 7: Stage 5, retorno lateral a direita

**Files:**
- Modify: `mvp_mission_bebop/steps/rtl.py` (`_reverse_search`, `_cruise_backward` -> `_cruise_rightward`, `_brake`, `_report_pad_position`, textos de log, docstrings, `execute` logs)
- Modify: `mvp_mission_bebop/controllers/rtl_guidance.py` (`RTLGuidanceController.compute/_compute_longitudinal/_compute_lateral`, `ArucoCenteringController._coast`, docstrings)
- Test: `test/test_lateral_rtl.py` (criar), `test/test_aruco_rtl.py`, `test/test_rtl_guidance.py`, `test/test_rtl_guidance_bugfix.py`, `test/test_centering_recovery.py`, `test/test_recovery_and_touchdown.py`, `test/test_dead_reckoning.py`

**Interfaces:**
- Produces: `ClosedLoopRTLStep._cruise_rightward(ctx, profile, shaper, dt, cruise_mps) -> float` (retorna o `vy` transmitido, `<= 0`); `_report_pad_position(self, ctx, ey_body_m: float) -> None`.
- Consumes: `CenteringCommand.ey_body_m`, `PlatformMonitor.override_along_track` via `ctx.odom_supervisor.override_along_track`.

Regras:

1. **Cruzeiro de busca** (`_reverse_search`): `cruise_mps = abs(to_mps(rtl.reverse_cruise_velocity, axis="lateral"))`. O perfil segue `profile.step(-permitted, dt)`; `vy = shaper.shape(to_normalized(profiled, axis="lateral"), dt)`; `clamp_translation(0.0, min(0.0, vy))` (so direita, estrutural); `vx` literal `0.0`; governor com `vx_commanded=abs(vy)`; retorna o `vy` seguro. `_hud(ctx, ..., vx=0.0, vy=vy, ...)` e os logs `"Reverse search: ... cmd vx=%.3f"` passam a `cmd vy=%.3f`. `_brake` chama o novo cruzeiro com demanda `0.0`.
2. **Override do marcador:** o marcador esta no ponto de decolagem; com a trilha de ida a esquerda, `along_track = -ey_body_m`. `_report_pad_position(ctx, command.ey_body_m)` chama `override_along_track(-ey_body_m, ttl_sec=_MARKER_POSITION_TTL_SEC)`. Atualizar o call site em `_center_over_marker`.
3. **Centralizacao 2D:** `ArucoCenteringController.update` e `_transmit_centering` permanecem (fecham malha em 2D). Unica mudanca no controlador: `_coast`. O creep de reenquadramento era `-vx` (voltar), porque o marcador saia pela traseira do campo de visao. Voando para a direita, o marcador ultrapassado sai pelo lado esquerdo do quadro. `_coast` passa a criar `lateral_target = +creep_speed` (esquerda) e `longitudinal_target = 0.0`; os gates (`_last_radial_m <= reacquire_creep_range_m`, `_last_speed_mps > settle`, `reacquire_creep_sec`) e as notas ("creeping left to reframe") mudam de texto. Review Focus 5.
4. **Retorno odometrico (caminho degradado, `RTLGuidanceController`)**: o canal de cruzeiro passa a ser o transversal ao corpo (Y). Concretamente:
   - `_compute_lateral(ey, ...)` assume o papel de `_compute_longitudinal`: deadband, demanda `FilteredPID(-ey)`, teto de frenagem `braking_velocity(abs(ey), ...)` com `cruise_velocity = speed_cap` (cap de cruzeiro = `rtl.max_speed` convertido), e recuperacao de ultrapassagem espelhada: `ey > 0` (origem a esquerda, drone ultrapassou a direita) permite creep a esquerda limitado a `rtl.overshoot_recovery_speed` dentro de `overshoot_recovery_radius_m`; alem disso, eixo retido com o mesmo aviso unico.
   - `_compute_longitudinal(ex, ...)` vira o trim fora da trilha: PD bilateral com teto `rtl.max_lateral_speed` convertido, deadband `rtl.deadband_m`, sem recuperacao de ultrapassagem.
   - Ganhos: `rtl.lateral_kp/kd` ficam no canal Y (cruzeiro) e `rtl.kp/kd` no canal X (trim). **Os nomes dos campos nao mudam**; o docstring de `ReturnToLaunchConfig` explica que `max_speed` cobre o canal de retorno (Y) e `max_lateral_speed` o trim em X. Registrar essa divida de nomenclatura no relatorio.
   - Saida: `vy = max(-1.0, min(rtl.overshoot_recovery_speed, vy))` (so a recuperacao limitada pode ser positiva), `vx = clamp(vx, +/-max_lateral_speed normalizado)`, `vyaw = 0.0` estrutural.
   - `GuidanceCommand.target_speed_mps` passa a ser a velocidade-alvo do canal Y; ajustar nome nos logs de `_navigate_home` ("cmd=(vx=..., vy=...)" ja imprime os dois).
   - `steps/rtl.py` mensagens `"Invariants: vyaw == 0.0 (locked), vx <= 0.0 (backward only)"` (linhas ~550 e ~1276) passam a `vx == 0.0 (off-track trim only), vy <= 0.0 (rightward return)`.

- [ ] **Step 1: Escrever os testes que falham** em `test/test_lateral_rtl.py`:
  - `test_the_reverse_search_flies_right_with_vx_zero`: reproduzir `test_the_reverse_cruise_never_commands_forward_flight` com `ScriptedSensor` (de `test_aruco_rtl.py`): todo `(vx, vy)` em `ctx.horizontal` tem `vx == 0.0` e `vy <= 0.0`, existe `vy < 0.0`, resultado `exhausted`.
  - `test_the_search_speed_equals_the_outbound_speed_in_magnitude`: `max(abs(vy))` ≈ `abs(rtl.reverse_cruise_velocity)` (regra E05).
  - `test_the_search_never_commands_leftward_flight`: forcar `reverse_cruise_velocity` positivo (erro de configuracao): ainda `vy <= 0.0` em todo comando (invariante estrutural).
  - `test_the_governor_is_fed_the_lateral_magnitude` (substitui `test_aruco_rtl.py:1936`, que exige `vx` nao positivo).
  - `test_the_marker_offset_feeds_the_platform_monitor_along_the_lateral_axis`: `ey_body_m = -0.4` (marcador a direita, drone a esquerda do totem) produz `override_along_track(+0.4, ttl)`; `ey_body_m = +0.2` produz `-0.2`.
  - `test_overshooting_the_marker_creeps_left_not_aft` (Review Focus 5): `ArucoCenteringController` alimentado com observacoes que se aproximam em `vy` negativo e depois somem; durante `reacquire_creep_sec` o comando tem `vy > 0.0` e `vx == 0.0`.
  - `test_the_marker_leaving_after_a_rest_does_not_creep`: ultimo `_last_speed_mps <= settle`: sem creep.
  - `test_the_odometric_return_navigates_right_when_the_origin_is_to_the_right`: `RTLGuidanceController` com `ReturnReference(ex=0.0, ey=-1.5, ...)`: `vy < 0.0`, `vx == 0.0`, e o modulo converge ao teto de cruzeiro.
  - `test_the_odometric_return_recovers_a_bounded_overshoot_to_the_left`: `ey = +0.3`: `0 < vy <= overshoot_recovery_speed`; `ey = +3.0` (alem do raio): `vy == 0.0` e o aviso e emitido uma vez.
  - `test_off_track_error_is_trimmed_with_a_small_bilateral_cap`: `ex = +/-0.2`: `abs(vx) <= max_lateral_speed` e sinais corretos.
  - `test_dead_reckoning_of_a_lateral_outbound_gives_a_rightward_return`: armar `DeadReckoningTracker`, aplicar `command(vy=0.15)` por 5 s de relogio injetado; `state().body_frame_origin_error()` tem `ey < 0` e `ex ≈ 0`.
  - `test_a_complete_rtl_from_the_left_lands_over_the_marker`: `ClosedLoopRTLStep` com sensor roteirizado (marcador visivel apos N ciclos) retorna `SUCCESS` e o ultimo comando antes do pouso tem `vx == vy == 0.0`.

- [ ] **Step 2: Rodar e ver falhar**

```bash
python3 -m pytest test/test_lateral_rtl.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implementar** conforme as regras 1 a 4.

- [ ] **Step 4: Atualizar testes existentes** (ver tabela na Task 9 para os de RTL) e rodar:

```bash
python3 -m pytest test/test_lateral_rtl.py test/test_aruco_rtl.py test/test_rtl_guidance.py test/test_rtl_guidance_bugfix.py test/test_centering_recovery.py test/test_recovery_and_touchdown.py test/test_dead_reckoning.py test/test_battery_rtl_jump.py -v
```
Expected: PASS.

- [ ] **Step 5: Gate de commit.** `feat(rtl): return laterally to the right with vx locked at zero`.

---

### Task 7B: Paridade de conservacao de altura sobre o totem (ida a esquerda, volta pela direita)

**Files:**
- Test: `test/test_lateral_altitude_parity.py` (criar), `test/test_edge_guard.py`, `test/test_floor_reference.py`, `test/test_fly_path_totem.py`
- Modify (somente se um teste revelar divergencia): `telemetry/odometry.py`, `steps/rtl.py`, `controllers/altitude_hold.py`

**Interfaces:** nenhuma nova. Consome `outbound_track_m` (Task 1), `PlatformMonitor`, `AltitudeHoldGovernor`, `OdometrySupervisor.platform_report()`, `OdometrySnapshot.height_above_floor_m`, `ClosedLoopRTLStep._grounded_altitude`.

Objetivo: provar que o efeito do codigo atual (guarda de borda, reentrada, referencia de piso) e identico quando a trilha de ida e Y. Estrategia: o mesmo percurso numerico, antes projetado em X, e agora projetado em Y, deve produzir **a mesma sequencia de estados e as mesmas decisoes do governor**.

- [ ] **Step 1: Escrever os testes** em `test/test_lateral_altitude_parity.py` (config de totem como em `test_fly_path_totem.py`: `height_m=1.0`, `edge_ahead_m=0.30` e tambem `1.0`, `sonar_half_angle_deg=20`):
  - `test_the_state_sequence_is_the_same_on_the_lateral_axis`: alimentar `OdometrySupervisor.inject_synthetic_sample` com um percurso ida e volta (0 -> 3 m -> 0) uma vez em `+x` com o produtor antigo (formula frontal replicada no teste como referencia) e outra em `+y` com o novo; a sequencia de `platform_report().state` e `guard_active` tem de ser identica (`ON, LEAVING, OFF` na ida; `OFF, ENTERING, ON` na volta).
  - `test_motion_along_x_never_arms_the_guard`: percurso so em `x` mantem `ON` (o monitor mede apenas a trilha lateral; documenta a limitacao do Review Focus 2).
  - `test_the_return_from_the_right_enters_the_pad_through_entering`: voando para a direita (`vy < 0`) o estado passa por `ENTERING` e termina em `ON`; durante `ENTERING` `guard_active` e `True` e `ceiling_allowance_m` e somada ao teto (`altitude_ceiling`); a quebra de teto dentro de `reentry_breach_grace_sec` e inibida e `reentry_descent_demanded()` e `True`.
  - `test_the_governor_engages_the_edge_guard_laterally`: `AltitudeHoldGovernor` com `guard_source=odom_supervisor.platform_report` entra em guarda em `LEAVING` e `ENTERING` com `guard_descent_cap`/`guard_climb_authority`/`guard_kp_scale`, e nao em `ON`/`OFF` (adaptar `test_edge_guard.py`).
  - `test_the_marker_override_dominates_odometry_drift`: com odometria derivada em 1 m, `override_along_track(-ey_body_m)` do marcador coloca o monitor no estado correto (`ON` com o marcador sobre a base) por `_MARKER_POSITION_TTL_SEC`, e expira depois.
  - `test_the_floor_reference_follows_the_pad_height_in_lateral_flight`: `snapshot.height_above_floor_m == relative_altitude + platform.height_m` durante Stage 2/3/4 e o alvo de altitude do governor e `target_altitude_m - height_m` (ver `test_floor_reference.py`); `ForwardSearchStep._locate` e o Stage 3 usam `height_above_floor_m` para projetar o alvo.
  - `test_the_touchdown_threshold_depends_on_the_platform_state_only`: `_grounded_altitude` devolve `touchdown_altitude_m - height_m` com `platform_state == "off"` e `at_base=False`, e `touchdown_altitude_m` com `at_base=True` ou estado `on`.
  - `test_departure_settle_precedes_the_lateral_edge_crossing`: com `edge_ahead_m` definido, nenhum `vy > 0` e transmitido ate o erro de altitude estabilizar na banda (migracao do `test_departure_settle.py`, ja feita na Task 2; aqui basta o caso de integracao com `platform_state`).
  - `test_stage_3_corrections_back_toward_the_pad_do_not_flap_the_guard` (hysteresis): servo lateral que oscila `+/-0.15 m` em torno da janela de guarda gera no maximo uma transicao por cruzamento real (usar `HYSTERESIS_M` e `DIRECTION_CONFIRM_SAMPLES`).

- [ ] **Step 2: Rodar**

```bash
python3 -m pytest test/test_lateral_altitude_parity.py test/test_edge_guard.py test/test_floor_reference.py -v
```
Expected: PASS. Se qualquer teste de paridade falhar, a causa esta no produtor (`odometry.py`), no override (`rtl.py`) ou em uma suposicao frontal escondida; corrigir na origem, nunca relaxando o teste.

- [ ] **Step 3: Voo de emulador com totem** (`test_fly_path_totem.py`, plant quadrada da Task 1): voo completo com guarda ligada e desligada. Asserts: perda de altura na saida pela esquerda `<= MAX_EXIT_LOSS_M` com guarda; sem guarda a plant reproduz o efeito (a diferenca prova que a guarda esta armada no eixo certo); reentrada pela direita sem rompimento de teto e sem `emergency land`; pouso sobre o marcador a altura de totem (`relative_altitude` perto de 0 sobre a base). Registrar numericamente as perdas de altura e a altura de entrada no relatorio final, lado a lado com a baseline da Task 0.

```bash
python3 -m pytest test/test_fly_path_totem.py -v
```
Expected: PASS.

- [ ] **Step 4: Gate de commit.** `test(platform): pin altitude conservation parity across the lateral pad crossings`.

---

### Task 8: Regras de conflito de parametros, GCS e telemetria

**Files:**
- Modify: `mvp_mission_bebop/parameter_conflicts.py` (W04, W05, W06, W08; `approach_distance`)
- Modify: `bebop_mission_control/src/lib/parameterConflicts.ts` (mesmas regras, em paralelo)
- Test: `test/test_parameter_conflicts.py`, `bebop_mission_control/src/lib/parameterConflicts.test.ts`
- Verify only: `bebop_mission_control/electron/main.cjs` (regex `[STEP N:`), `bebop_mission_control/src/lib/stepMarkers.test.ts`, `src/hooks/useFlightNarration.test.ts`

**Interfaces:** os ids das regras (`W04`...) e sua ordem nao mudam (paridade Python/TS).

Racional: W04, W05 e W06 derivam de `approach_distance` (geometria entre o tilt de busca e o nadir ao longo do avanco frontal), que nao existe mais; mantidas, produziriam avisos falsos ao operador. W08 estima o tempo de retorno somando `d` da aproximacao.

- [ ] **Step 1: Testes primeiro.** Em `test/test_parameter_conflicts.py`: para os parametros do `mission_config.json` atual, `W04`, `W05`, `W06` nao emitem achado (regra neutralizada devolve `None` incondicionalmente com docstring explicando); `W08` calcula `needed = (vf * search_timeout) / abs(vr)` (distancia de ida = cruzeiro lateral de `search_timeout`, sem `d`) e emite quando `rtl.timeout_sec` e menor. Espelhar os mesmos casos em `parameterConflicts.test.ts`.

- [ ] **Step 2: Rodar e ver falhar**

```bash
python3 -m pytest test/test_parameter_conflicts.py -v
cd bebop_mission_control && npx vitest run src/lib/parameterConflicts.test.ts
```
Expected: FAIL.

- [ ] **Step 3: Implementar em Python e TS.** Antes de editar o `.ts`, `git status bebop_mission_control/src/lib/parameterConflicts.ts` e `git diff` do arquivo: se houver alteracoes de outra sessao, aplicar a edicao minima sobre elas, sem reverter nada.

- [ ] **Step 4: Verificar o contrato do GCS** (somente leitura/execucao):

```bash
cd bebop_mission_control && npx vitest run src/lib/stepMarkers.test.ts src/hooks/useFlightNarration.test.ts src/lib/parameterConflicts.test.ts && npx tsc --noEmit
cd .. && python3 -m pytest test/test_contracts.py test/test_telemetry_frame.py test/test_bench_telemetry.py test/test_mission_parameters.py -v
```
Expected: PASS. Confirmar que o nome do step em `[STEP 2: ...]` mudou sem afetar o parser (`stepMarkers` casa so `[STEP N:`), e que `telemetry/mission_parameters.py:56` (`cruise_mps`) segue coerente: avaliar se deve usar `axis="lateral"` na conversao; se sim, ajustar com teste em `test_mission_parameters.py`.

- [ ] **Step 5: Gate de commit.** `refactor(conflicts): retire the approach-geometry warnings and recompute the return window`.

---

### Task 9: Testes obsoletos, emulador e bancada `--no-fly`

**Files:**
- Modify/Delete: tabela abaixo
- Modify: `test/test_fly_path_emulated.py`, `test/test_fly_path_totem.py`, `test/support/fake_bebop/harness.py`
- Verify: `scripts/bench_rehearsal.py` e `test/test_bench_rehearsal.py`

**Tabela de testes obsoletos (acao obrigatoria):**

| Arquivo | Testes | Acao |
|---|---|---|
| `test/test_nadir_freeze.py` | todos (standoff, freeze apos nadir, corredor, rampa open-loop) | Substituir o arquivo por testes do freeze lateral e do enquadramento do gimbal (migrar `test_the_inspection_stage_points_the_gimbal_at_the_same_attitude` para a regra D3). Se o conteudo ficar identico a `test_lateral_servoing.py`/`test_gimbal_framing.py`, apagar o arquivo. |
| `test/test_controllers.py` | `test_starts_in_centering_with_no_forward_motion` (manter), `test_transitions_to_approaching_after_the_acquisition_dwell`, `test_approach_speed_follows_the_measured_range`, `test_gimbal_tracks_the_target_downward_during_approach`, `test_gimbal_respects_the_slew_rate_limit`, `test_forward_motion_freezes_outside_the_lateral_corridor`, `test_sustained_drift_withholds_forward_motion_without_changing_phase`, `test_nadir_phase_is_reachable_and_exits_on_sustained_excursion`, `test_nadir_alignment_is_reported_when_centred`, `test_falls_back_to_open_loop_when_altitude_is_untrustworthy` | Apagar os de avanco/nadir (cobertos por `test_lateral_servoing.py` e `test_gimbal_framing.py`); manter `test_lateral_command_opposes_horizontal_error`, `test_lateral_deadband_is_not_defeated`, `test_geometry_is_reported_for_diagnostics`, `test_reset_returns_to_acquisition` ajustando nomes de fase; os de governor altitude nao mudam. |
| `test/test_stage3_integration.py` | cenarios de standoff | Ja reescrito na Task 5. |
| `test/test_aruco_rtl.py` | `test_the_reverse_cruise_never_commands_forward_flight`, `test_the_reverse_cruise_holds_the_lateral_and_yaw_axes_at_zero` (:1077-1105), checagem de `governor.calls <= 0` (:1936), constantes de `reverse_cruise_velocity` | Substituir pelos equivalentes laterais (cobertos em `test_lateral_rtl.py`); manter todo o resto de pose, centralizacao e pouso. |
| `test/test_rtl_guidance.py`, `test_rtl_guidance_bugfix.py` | testes que assumem `vx` como canal de cruzeiro e overshoot em `vx` | Reescrever para o canal Y conforme Task 7, mantendo a cobertura de frenagem, jerk e settlement. |
| `test/test_departure_settle.py`, `test_stage_altitude_wiring.py`, `test_announcer_calls.py` | uso de `vx` no cruzeiro | Ja ajustados na Task 2. |
| `test/test_fly_path_totem.py` | `REVEAL_X_M`, `EDGE_M` no eixo X, argumento `--reveal-x` | Trocar por eixo Y (`--reveal-y`), comentario do docstring e constantes. |
| `test/test_fly_path_emulated.py` | assert de ordem de steps | Manter; acrescentar asserts de sinal (abaixo). |

- [ ] **Step 1: Aplicar a tabela** e rodar:

```bash
python3 -m pytest test/ -q -p no:cacheprovider --ignore=test/test_fly_path_emulated.py --ignore=test/test_fly_path_totem.py 2>&1 | tail -30
```
Expected: somente as falhas pre-existentes registradas na Task 0.

- [ ] **Step 2: Asserts de sinal no emulador.** Em `test/test_fly_path_emulated.py` (voo `--fly` com seeds 1 a 3), adicionar `test_the_outbound_leg_flies_left_and_the_return_leg_flies_right`: usando `result.lines` e `result.nonzero_twists()`, particionar os Twists nao nulos por janela de step (marcadores `[STEP N:`, ver `FlightResult.steps()`):
  - janela do Step 2: todo Twist com `linear.x == 0.0`, `linear.y >= 0.0`, `angular.z == 0.0`, e ao menos um `linear.y > 0.0`;
  - janela do Step 3: `linear.x == 0.0` em todo Twist;
  - janela do Step 4: todo Twist com `linear.x == linear.y == angular.z == 0.0` (apenas `linear.z` do governor e permitido) e ao menos um `move_camera` com tilt descendente;
  - janela do Step 5: durante a busca, `linear.x == 0.0` e `linear.y <= 0.0`; a centralizacao 2D final pode ter ambos os eixos.
  - `test_fly_path_no_fly...` continua exigindo zero takeoff e zero Twists nao nulos.
  Observacao: o emulador renderiza o quadro do alvo a partir de uma imagem fixa (`PhaseScene`), entao o Stage 3 nao converge no emulador (termina por timeout/degradado); o teste de emulador valida sequencia, sinais e seguranca, nao a convergencia, que e coberta em `test_stage3_integration.py`.

- [ ] **Step 3: Rodar os voos emulados**

```bash
python3 -m pytest test/test_fly_path_emulated.py test/test_fly_path_totem.py -v
```
Expected: PASS. Em `test_fly_path_totem.py`, confirmar que a perda de altitude na saida do totem pelo lado esquerdo permanece `<= MAX_EXIT_LOSS_M` com a guarda ligada, e que sem guarda o plant ainda reproduz o efeito (a guarda precisa estar "armada" no eixo certo; se a guarda nunca ativa, o produtor de `along_track_m` (Task 1) esta no eixo errado).

- [ ] **Step 4: Validacao de bancada `--no-fly`** (CLAUDE.md e PROMPT_INICIO: **proibido armar motores**; so `--no-fly` em dominio ROS isolado, relay unidirecional de sensores, `Publisher count: 0` em `/bebop/{takeoff,cmd_vel,land}` no dominio do driver):

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_bench_rehearsal.py test/test_no_fly_interlock.py test/test_fly_path_emulated.py::test_fly_path_no_fly_issues_zero_takeoff_and_zero_nonzero_cmd_vel -v
python3 scripts/bench_rehearsal.py --help
```
Executar o ensaio completo com o emulador (nao com o drone real) pelo harness `EmulatedFlight(fly=False)` e, se o usuario autorizar bancada com o drone conectado, via `scripts/bench_rehearsal.py` conforme `docs/CHECKLIST_VOO_REAL.md`. Do log, extrair e anexar ao relatorio a sequencia: `[STEP 1]` decolagem simulada, `[STEP 2]` varredura (`cmd vy>0`), `mission.target_found`, `[STEP 3]` centralizando e `LOCKED`, `[STEP 4]` tilt descendo e `mission.capture_done`, `[STEP 5]` busca (`cmd vy<0`), `Centred over marker`, touchdown simulado. Conferir tambem que o simulador (`actuators/simulator.py`) integra `vy` pelo ganho lateral e que o mapa tatico recebe odometria (`test_bench_telemetry.py`, `test_telemetry_frame.py`).

- [ ] **Step 5: Gate de commit.** `test(lateral): migrate obsolete forward-flight tests and add lateral emulator sign checks`.

---

### Task 10: Documentacao e fechamento

**Files:**
- Modify: `/home/jv/ros2_ws/src/mvp_mission_bebop/CLAUDE.md` (secao 1: descricao dos 5 estagios), `docs/CHECKLIST_VOO_REAL.md` (itens que citam avanco/recuo e distancia a frente da borda)
- Modify: `docs/IMPLEMENTACAO_PROGRESSO.md` (uma linha por tarefa deste plano)
- Atencao: `docs/CHECKLIST_VOO_REAL.md` ja tinha modificacoes nao commitadas de outra sessao em 2026-10-08. Reler `git diff` do arquivo e aplicar edicao minima por cima, sem reverter.

- [ ] **Step 1: Atualizar o `CLAUDE.md` secao 1** com o padrao lateral (varredura a esquerda, servoing lateral e freeze, enquadramento por gimbal, retorno a direita) mantendo o tom tecnico e sem emojis. Atualizar o checklist de voo real: posicionar a trilha de varredura na faixa de visada (D7), medir a borda do totem a esquerda (`edge_ahead_m = 0.30`), e o ensaio de bancada.

- [ ] **Step 2: Suite completa e relatorio**

```bash
python3 -m pytest test/ -q -p no:cacheprovider 2>&1 | tail -40
cd bebop_mission_control && npx vitest run 2>&1 | tail -15 && npx tsc --noEmit
```
Expected: pytest 100% exceto falhas pre-existentes nomeadas; vitest e tsc limpos (se vitest/tsc ja falhavam na Task 0 por mudancas de outras sessoes, registrar e nao corrigir).

- [ ] **Step 3: Relatorio final ao usuario** (entrega obrigatoria do enunciado): resumo tecnico por modulo, o relatorio da suite (totais, falhas pre-existentes por nome), o log do ensaio de bancada, a lista de riscos abertos (ver abaixo) e a proposta de commits atomicos (um por gate de commit acima). Perguntar se autoriza os commits e o push.

---

## Riscos Abertos para o Relatorio Final

1. **Ganho lateral do servoing**: `lateral_pid.output_limits = +/-0.04` limita a correcao a 0.04 normalizado; um erro inicial de 0.6 m leva da ordem de 15 a 25 s. Cabe na janela de 110 s, mas a calibracao real (`lateral_normalized_to_mps`) precisa de voo de bancada.
2. **Deriva em X (fora da trilha)** nao e vista pela guarda de borda (monitora so Y); o totem tem 0.30 m de meia largura nos dois eixos. Considerar guarda 2D em trabalho futuro.
3. **Faixa de visada** (D7): a eficacia da varredura depende do posicionamento da trilha; considerar uma regra de conflito nova que avise quando a faixa for estreita.
4. **Dividas de nomenclatura** mantidas por contrato: `forward_cruise_velocity`, `reverse_cruise_velocity`, `edge_ahead_m`, `max_lateral_speed` (trim em X), `reacquire_reverse_*` (creep lateral). Renomear exige migrar `mission_config.json`, GCS (`parameterSchema.ts`, `launchDocument.ts`) e fixtures em um unico commit coordenado.
5. **Rotulos do GCS** ("Velocidade de Deslocamento", textos que citam aproximacao/nadir) nao foram alterados; atualizar a copia da UI e trabalho do frontend, fora deste plano por haver edicoes concorrentes em `ParameterSheet.tsx`.
