# Plano: Altitude de até 3,0 m, Subida pós-decolagem e Contagem Fixa de 10 s

> **Para o executor (Claude Code):** use `superpowers:test-driven-development` e, havendo subagentes, `superpowers:subagent-driven-development`. Cada tarefa tem passos com caixa de seleção. Este documento é a fonte da verdade; não amplie o escopo. Ordem global: **Backend Python, depois Frontend TypeScript, depois testes de paridade e integração** (a seção 6). Dentro de cada componente, o teste vem antes do código.

**Objetivo:**
1. Permitir `kinematics.target_altitude_m` até **3,0 m sobre o piso**; subida controlada pós-decolagem (`_acquire_altitude`) quando o alvo passa da altura nativa do firmware, no piso e no totem; **margem do teto liberada no totem acima de 2,0 m**.
2. Retirar da estação o **parâmetro ajustável** da contagem regressiva. **A contagem continua e é fixa em 10 s**: overlay, ticker, milestones e backend não mudam.

**Decisões do usuário (2026-10-24), já resolvidas:** a contagem **mantém** (só some o parâmetro ajustável) e tem de ser **10 s**; o teto real do local é **3,2 m**; o aviso falado de afastamento fica como está; o código do overlay e da contagem na estação fica onde está.

**Pilha:** Python 3.12 + pytest (`mvp_mission_bebop`), TypeScript/React/Vitest (`bebop_mission_control`), Electron.

Caminhos relativos a `/home/jv/ros2_ws/src/mvp_mission_bebop` (`BE`). `PY = BE/mvp_mission_bebop`, `GCS = BE/bebop_mission_control`, `TS = GCS/src`, `T = BE/test`.

Código lido em 2026-10-24 sobre o HEAD `40676ec`, com a árvore suja por outras sessões. O `CLAUDE.md` atual descreve o padrão de voo **lateral** (`vx == 0`, varredura à esquerda, RTL à direita, guarda de borda medida ao longo da trilha lateral); este plano não toca nesse padrão.

---

## 0. Antes de qualquer edição (leia)

1. **A árvore é compartilhada e está suja** (48 entradas em `git status` na auditoria; outras sessões editam `parameters.py`, `mission.py`, `takeoff.py` e o frontend). Rode `git status` e `ListAgents` no início e antes de tocar em arquivo compartilhado. Se um arquivo que você vai editar tiver alterações de terceiros, **pare e pergunte** ao usuário. Não use `git add -p` sobre hunks alheios.
2. **Há uma `mission.py --standby ... --fly` rodando** (processo observado). Não suba voo, emulador nem `mission.py`. Antes de qualquer voo emulado: `ps -eo args | grep fake_bebop | grep -v grep` deve ficar vazio. **Nunca rode dois lotes emulados ao mesmo tempo** (`ROS_DOMAIN_ID` 80-99 compartilhados).
3. **Todo comando Python/pytest** é prefixado por `source /home/jv/ros2_ws/bin/nectar-activate &&`, inclusive nos subagentes.
4. **Linha de base congelada:** copie a árvore para `BE/.superpowers/baseline-altitude-contagem/` (ignorado pelo git; inclua ou ligue por symlink `mvp_mission_bebop/yolov8n.pt` e `yolov8n_openvino_model/`) e registre `python3 -m pytest test/ -q` e `cd bebop_mission_control && npx vitest run && npm run typecheck`. Testes de timing oscilam sob carga (`test_inspection_hover`, `test_recovery_and_touchdown`, `test_takeoff`, `test_fake_bebop_interlocks`, `test_announcer_calls`, emulado seed 3): rode a falha sozinha antes de chamá-la de regressão.
5. **Sem `git commit` nem `git push`** sem autorização explícita do usuário nesta sessão (prevalece sobre o check-out do `CLAUDE.md`). Entregue a divisão de commits da seção 9 e espere.
6. `nectar-sdk` e o driver são somente leitura. Regra de ouro 2.1 do `CLAUDE.md` verificada: `BebopDrone.takeoff(altitude)` só publica `Empty`, registra o argumento e dorme 3 s (`nectar/nectar/control/bebop/drone.py:146-163`); nada a reutilizar para altitude.
7. Contrato com o GCS: marcadores `[STEP N: ...]` e `MILESTONE_KEYS` (`PY/telemetry/milestones.py:68-69`, fixado por `T/test_contracts.py:656-680`) não mudam; `mission_config.json` mantém o esquema.
8. Estilo: sem emojis, sem comentários óbvios, docstrings NumPy/reST, tipagem estrita, Conventional Commits em inglês técnico.

## 1. Decisões e fatos que corrigem o pedido

- **D1 - Contagem: o backend não muda.** O pedido original listava `countdown_sec = 0.0` no backend, `mission_config.json` em `0.0` e retorno imediato de `_countdown`. O usuário depois decidiu que a contagem **mantém** e é de **10 s**. Logo: `parameters.py` fica em `10.0`, `mission_config.json` fica em `10.0`, `_countdown`/`launch_deadline`/`CountdownTicker`/`--countdown`/`_validate_countdown` ficam intactos. O que sai é só o **campo editável** na folha de parâmetros. Para o overlay (estação) e a missão (backend) contarem a mesma janela, a estação passa a **fixar** `kinematics.countdown_sec = 10` no documento (seção 5).
- **D2 - `takeoff(altitude=...)` não decola até o patamar de 2,0 m.** O argumento é ignorado pelo SDK (só log), pelo driver (`ros2_bebop_driver/src/bebop.cpp:133-138` só envia `takeOff()`) e pelo firmware, que encerra o perfil de lançamento em ~1,0 m sobre a superfície (`PY/mission_mode.py:67` `NATIVE_HOVER_HEIGHT_M = 1.0`; `PY/actuators/simulator.py:58`; `T/support/fake_bebop/plant.py:254`). A "fase 1" do pedido é sempre ~1,0 m; a "fase 2" (subida) já é `_acquire_altitude`. A regra "até 2,0 m direto, acima de 2,0 m em duas etapas" vira, no código, apenas o argumento enviado e o log: `min(alvo_relativo, 2,0)`, sem efeito físico.
- **D3 - Não há restrição de subida a partir do totem em `takeoff.py`.** Nenhum ramo por superfície (`takeoff_from_ground`, `platform`, `height_m`) existe em `takeoff.py` além do import de `launches_from_ground` e do payload do milestone. A restrição é indireta: `ALTITUDE_MAX_M = 2.0` (no totem de 1,0 m o alvo relativo máximo é 1,0 m, igual à altura nativa, então o ramo de subida nunca roda) e o teste `T/test_takeoff_acquisition.py:152`. O que se remove: o teto de 2,0 m, esse teste invariante e a docstring `takeoff.py:753`.
- **D4 - Teto do local = 3,2 m (resposta do usuário).** Gravar `platform.venue_ceiling_agl_m = 3.2` em `PY/mission_config.json` (hoje `2.5`). Consequências, todas calculadas sobre o código:
  - máximo efetivo do alvo: `min(ALTITUDE_MAX_M, 3.2 - 0.10) = min(3.0, 3.1) = 3.0`;
  - teto relativo no totem limitado a `3.2 - 1.0 = 2.2 m`;
  - folga real entre o alvo e o teto: **2,0 m de alvo no totem = 1,0 m relativo → teto 1,6; 3,0 m = 2,0 m relativo → teto cortado em 2,2, ou seja, só 0,2 m acima do alvo**. A margem de 0,2 m é menor que o piso de auditoria `climb_deadband_m + 2*climb_settle_max_position_sigma_m = 0,21 m` (`config_audit.py:157-170`). A auditoria vai avisar; isso é esperado e deve ser relatado, não "corrigido".
  - **Recomendação operacional a registrar:** manter o alvo em até ~2,7 m (≥ 0,5 m sob o teto). O limite de 3,0 m fica disponível, como pedido.
- **D5 - Voz offline.** `docs/superpowers/plans/2026-10-24-bmg-offline-feira.md` (D7) pré-sintetiza a fala da altitude para 2 m. Outra altitude vira falha de cache e a linha sai muda offline. Registre no relatório que, ao fixar uma altitude nova para a feira, é preciso repetir `streamer/precache_speech.py`. Não é bloqueio.
- **D6 - Aviso falado de afastamento: sem mudança.** Com contagem de 10 s o aviso (`mission.countdown_3`, em 3,2 s do fim) continua sendo falado com antecedência normal.
- **D7 - Código da contagem na estação: mantido.** O overlay, `useMissionCountdown`, `benchCountdown.cjs` e os ticks de `milestones.cjs` continuam alcançáveis (a contagem existe). Nada fica inalcançável por este plano.

## 2. Estado encontrado (fatos verificados)

### Altitude

- Constantes: `PY/launch_envelope.py:34` `ALTITUDE_MIN_M = 0.10`, `:36` `ALTITUDE_MAX_M = 2.00`, `:38` `ALTITUDE_STEP_M = 0.05`, `:42` `PAD_CLEARANCE_M = 0.30`, `:44` `VENUE_HEADROOM_M = 0.10`. Espelho: `TS/lib/altitudeEnvelope.ts:18-27` (`ALTITUDE_MAX_M = 2.0` em `:19`, `NATIVE_HOVER_HEIGHT_M = 1.0`, `TOTEM_CEILING_HEADROOM_M = 0.6`). Slider: literal `max: 2` em `TS/lib/parameterSchema.ts:112`, sobrescrito em execução por `rangeFor: altitudeSliderRange` (`altitudeEnvelope.ts:82`).
- `altitude_envelope` (`launch_envelope.py:124-151`): mínimo `ALTITUDE_MIN_M` do piso ou `max(0.10, pad + pad_clearance_m)` no totem; máximo `ALTITUDE_MAX_M` limitado por `venue - 0.10`. `validate_launch_envelope` (`:154-197`) recusa com `LaunchEnvelopeError("kinematics.target_altitude_m", ...)`; chamado em `PY/mission.py:548` (partida a frio) e `:1113` (standby `go`). `PY/parameter_conflicts.py:26` e `TS/lib/parameterConflicts.ts:317` importam `ALTITUDE_MAX_M` (regras E10/E11).
- Paridade Python/TS por fixture: `T/fixtures/altitude_envelope_cases.json` (84 casos; grade modo x superfície x rtl x altitudes `{0.05, 0.1, 0.35, 1, 1.25, 1.3, 1.8, 2, 2.05}`; os casos de totem usam `venue_ceiling_agl_m: 2.5` e `altitude_ceiling_margin_m: 0.4`), lido por `T/test_launch_envelope_parity.py` e `TS/lib/altitudeEnvelope.test.ts`. Constantes fixadas em `T/test_launch_envelope.py:33` e `altitudeEnvelope.test.ts:44,48`.
- Teto: `PY/estimation/ceiling_schedule.py`. **`effective_margin_m` é propriedade, não método** (`:115-119`): `TOTEM_CEILING_HEADROOM_M = 0.60` no totem, `altitude_ceiling_margin_m` do piso. `nominal_m(guard_active=False)` (`:129-139`): totem `max(target_m, native_height_m) + 0.60`; piso `target_m + margin`; soma `ceiling_allowance_m` se a guarda estiver ativa. `value()` (`:181-193`): fase NATIVE devolve só o venue; fase RATCHET `min(transiente, venue)`; senão `min(nominal, venue)`. Espelho TS: `ceilingFloorFrame` (`altitudeEnvelope.ts:129-143`).
- Trava da margem: `ceilingMarginLock` (`TS/lib/parameterSchema.ts:87-97`, função **privada**) devolve `null` só no piso; campo `kinematics.altitude_ceiling_margin_m` em `:124-135` (0 a 2, passo 0,05, `disabledReason: ceilingMarginLock`). Consumidores: `ParameterSheet.tsx:203`; `TS/lib/missionMode.ts:102` (`inactivePaths`: margem inativa no totem). Regras de conflito só do piso: E04 e W02 (`PY/parameter_conflicts.py:260,361`; `TS/lib/parameterConflicts.ts:209,356`). Auditoria: `PY/config_audit.py:157-170` (piso da margem, só se `not schedule.on_pad_top`) e `:304-310` (folga do venue de 0,2 m, idem).
- `relative_target_altitude_m` (`PY/parameters.py:1499-1537`) não tem limite superior. `platform.takeoff_from_ground` existe (`:1309`, `bool`, default `False`).

### Contagem

- `PY/parameters.py:525-533` `COUNTDOWN_MIN_SEC = 0.0`; `:584-590` `countdown_sec = 10.0`; `PY/mission_config.json:107` `10.0`. **Nada disso muda.**
- A **missão lê `kinematics.countdown_sec` do documento** (`--params-json`), não a opção `countdown` do lançamento. A estação usa o mesmo valor para o overlay: `TS/App.tsx:281` (`launchCountdownSec(doc) ?? 0`), `:294` (`if (seconds >= 1) setCounting(true)`), `:306-307` (`countdown: seconds`, `paramsJson: launchParamsJson(doc, ...)`).
- O documento chega ao lançamento por `commitLaunchDocument` (`App.tsx:223-250`), que passa o documento de trabalho por **`reconcileCoupledPaths`** (`TS/lib/launchDocument.ts:330`), também aplicado ao carregar e ao restaurar padrões (`TS/hooks/useMissionParameters.ts:105,142,254,346`), e nas edições de campos espelhados (`:180`). É o ponto idempotente que mantém caminhos derivados coerentes em todo o documento: o local certo para **fixar** a contagem.
- `launchParamsJson` (`TS/lib/batteryFailsafe.ts:75-77`) serializa o documento inteiro, só injetando `battery.land_pct`.
- Campo na folha: `TS/lib/parameterSchema.ts:246-257` (grupo `timeouts`, "Contagem Regressiva", 0 a 30); resumo do grupo em `:202-258` ("... e a contagem antes da decolagem"; variante do modo simples com o mesmo trecho). `TS/lib/launchDocument.ts:74` lista `kinematics.countdown_sec` em `REQUIRED_LAUNCH_NUMBERS`; os limites vêm de `NUMBER_PARAMETERS` (a folha) ou de `EXTRA_BOUNDS` (`:92-101`); **ao sair da folha o caminho perde seus limites e precisa de uma entrada em `EXTRA_BOUNDS`**.
- Regra W10 (aviso, nunca bloqueia): `PY/parameter_conflicts.py:416-423` e `TS/lib/parameterConflicts.ts:455-475`; dispara se `countdown < flat_trim_ack_timeout_sec + 2,0`. `timeouts.flat_trim_ack_timeout_sec` (3,0) **não está na folha**; com 10 s fixos, `ground = 5,0` e W10 nunca dispara no uso normal. **Não alterar W10** (mantém a paridade e os fixtures).
- `PY/telemetry/mission_parameters.py:30,64` emite `countdown_sec` em `mission.parameters`; a estação não o consome. **Não mexer.**

## 3. Especificação: transição `takeoff -> stabilize -> acquire_altitude/ascend`

Ordem real em `PY/steps/takeoff.py` (`TakeoffStep.execute`, `:144`):

```
execute
 |- _arm_cold_countdown                        (partida a frio; standby usa launch_deadline)
 |- sessão do detector abre
 |- _ground_sequence (:213)
 |    bateria (portão) -> gimbal -> _flat_trim (ack até 3 s) -> _calibrate (z0) -> _countdown (10 s) -> _await_ground_interlocks (acomodação do IMU, 2,0 s)
 |- takeoff_committed = True; detector é liberado
 |- _command_takeoff (:496)       fase de teto NATIVE (só o venue); drone.takeoff(altitude=...)
 |- _flight_sequence (:231)
      |- _confirm_takeoff (:543)  flying_state em {1,2} OU altitude relativa > takeoff_settle_min_altitude_m (0,30)
      |- _stabilize (:621)        hover do firmware (~1,0 m) até o SettlementDetector assentar (teto de 4,0 s);
      |                           ao terminar, end_native_profile -> fase de teto RATCHET (:719, :728)
      |- _acquire_altitude (:747) ver tabela
      |- _finalize (:1077)        saúde do sistema, congela a origem horizontal, arma dead reckoning,
                                  release_ceiling_transient -> fase NOMINAL (:1108)
```

`_acquire_altitude` decide pelo alvo **relativo** `T_rel = relative_target_altitude_m = target_altitude_m - altura_do_totem` e pela altitude `a` ao fim de `_stabilize` (~1,0 m, a nativa):

| Condição | Ramo | Efeito |
|---|---|---|
| `a > T_rel + governor.deadband_m` | `_descend_to_target` (`:973`) | desce sob o governor |
| `a >= T_rel - climb_deadband_m` | mantém | "No ascent required" |
| senão | **subida controlada** | `climb_window(max_climb_speed_mps = 0.25)`, `JerkLimitedProfile(max_velocity=0.25, max_accel=0.30, max_jerk=1.20)`, `braking_velocity(..., arrival_tolerance=climb_deadband_m)`, `SettlementDetector`, estagnação em 10 s, teto checado a cada ciclo, `vz = clamp_kinematics(..., allow_climb=True)` |

Casos (nativo 1,0 m; totem 1,0 m):

| Superfície | Alvo (piso) | `T_rel` | Ramo | Subida |
|---|---|---|---|---|
| Totem | 1,8 | 0,8 | desce | 0,2 m para baixo |
| Totem | 2,0 | 1,0 | mantém | 0 |
| Totem | 2,5 | 1,5 | **sobe** | 0,5 m |
| Totem | 3,0 | 2,0 | **sobe** | 1,0 m (~4 s a 0,25 m/s) |
| Piso | 1,8 | 1,8 | **sobe** | 0,8 m |
| Piso | 3,0 | 3,0 | **sobe** | 2,0 m (~10 a 11 s) |

**Mudanças nesta transição (e só estas):**
1. `_command_takeoff` passa a enviar `altitude = min(T_rel, TAKEOFF_PLATEAU_M)` com `TAKEOFF_PLATEAU_M = 2.0` (constante de módulo; documentada como puramente informativa por D2) e o log diz isso.
2. O prazo da subida: `Deadline(kinematics.climb_timeout_sec)` (15,0 s, `takeoff.py:~838`) cobre ~2 m com pouca folga. Passa a `Deadline(max(climb_timeout_sec, 1.5 * remaining / max_climb_speed_mps + 4.0))`, com `remaining = T_rel - a` calculado uma vez antes do laço (piso, 3,0 m: 16 s). Subidas curtas mantêm 15 s.
3. O teto efetivo (seção 4) é o que `is_ceiling_breached()` e a fase RATCHET aplicam; nada mais muda em `arm_ceiling_transient`/`end_native_profile`/`release_ceiling_transient`.
4. Docstrings/comentários: remover "On the pad top the target is at or below the native height (D1), so only a flight from the floor can climb" (`:753`) e verificar `:977`.

Tetos por fase para o caso mais apertado (totem, alvo 3,0, margem 0,60, venue 3,2 -> `V - pad = 2,2`): NATIVE 2,2; RATCHET `min(max(2,6; 1,0+0,6), 2,2) = 2,2`; NOMINAL `min(2,6; 2,2) = 2,2`. Ou seja, a subida de 1,0 m termina com 0,2 m de folga ao teto.

## 4. Especificação: margem do teto liberada no totem acima de 2,0 m

**Regra única** (Python e TypeScript, idêntica, tolerância `1e-9`):

```
margin_unlocked = launches_from_ground  OR  target_altitude_m > TOTEM_MARGIN_UNLOCK_ABOVE_M (2.0)
```

- `target <= 2,0` no totem: margem **travada**; teto como hoje (`max(T_rel, native) + 0,60`).
- `target > 2,0` no totem: teto = `max(T_rel, native) + altitude_ceiling_margin_m`. Com a margem padrão (0,60) o teto é contínuo em 2,0.
- Piso: inalterado (`T_rel + margem`).
- O cap do venue (`V - pad`) vale em todos os ramos.

Exemplos (totem de 1,0 m, margem 0,60; teto relativo / sobre o piso):

| Alvo | `T_rel` | Sem venue | Venue 3,2 (cap 2,2 relativos) |
|---|---|---|---|
| 2,0 | 1,0 | 1,6 / 2,6 (travada) | 1,6 / 2,6 |
| 2,05 | 1,05 | 1,65 / 2,65 | 1,65 / 2,65 |
| 2,5 | 1,5 | 2,1 / 3,1 | 2,1 / 3,1 |
| 2,7 | 1,7 | 2,3 / 3,3 | **2,2 / 3,2** (cortado) |
| 3,0 | 2,0 | 2,6 / 3,6 | **2,2 / 3,2** (cortado) |

**Python:**
- `launch_envelope.py`: `TOTEM_MARGIN_UNLOCK_ABOVE_M: Final[float] = 2.00` e `margin_unlocked(params) -> bool` (valor não numérico: `False`; usa `launches_from_ground`).
- `ceiling_schedule.py`: nova propriedade `margin_unlocked` (usa `on_pad_top` e `kinematics_cfg.target_altitude_m`); `effective_margin_m` devolve a margem do operador se liberada, senão `TOTEM_CEILING_HEADROOM_M`; `nominal_m` usa `effective_margin_m` também no ramo do totem. `arm()` já usa `effective_margin_m`. Atualizar a docstring (`:40-49`).
- `config_audit.py`: trocar `not schedule.on_pad_top` por `schedule.margin_unlocked` em `:157-170` e em `:304-310`.
- `parameter_conflicts.py`: E04 e W02 trocam `self.from_ground()` por "margem liberada" (mesma regra).

**TypeScript:**
- `altitudeEnvelope.ts`: `TOTEM_MARGIN_UNLOCK_ABOVE_M = 2.0` e `marginUnlocked(doc): boolean`; `ceilingFloorFrame` usa a margem do documento no totem quando `marginUnlocked(doc)` (devolve `null` se não for finita nesse ramo) e `TOTEM_CEILING_HEADROOM_M` caso contrário; cap do venue mantido.
- `parameterSchema.ts`: `ceilingMarginLock` -> `if (marginUnlocked(doc)) return null;` (importar `marginUnlocked`); o texto "Teto fixo do perfil de totem..." só para o totem com alvo `<= 2,0`.
- `missionMode.ts:102`: `if (!marginUnlocked(doc)) paths.add('kinematics.altitude_ceiling_margin_m')`.
- `parameterConflicts.ts`: E04 e W02 trocam `launchesFromGround(doc)` por `marginUnlocked(doc)`.

## 5. Especificação: contagem fixa em 10 s (só o parâmetro ajustável sai)

- **Constante única da estação:** `LAUNCH_COUNTDOWN_SEC = 10` em `TS/lib/launchDocument.ts` (exportada), comentada como espelho de `FlightKinematicsConfig.countdown_sec` (`parameters.py:590`).
- **Fixar o valor no documento, em `reconcileCoupledPaths`:** além do que já faz, `kinematics.countdown_sec` é forçado a `LAUNCH_COUNTDOWN_SEC` quando difere (idempotente; devolve o mesmo objeto se já for 10). Efeito: o documento de trabalho (carregado, editado, salvo) e o de lançamento sempre levam 10, o overlay e a missão contam a mesma janela, e um `mission_config.json` antigo com outro valor (por exemplo `3`) é corrigido sem migração.
- **Defesa na saída:** `launchParamsJson` também força `kinematics.countdown_sec = LAUNCH_COUNTDOWN_SEC` no clone enviado ao `--params-json`.
- **Sai da folha:** remover o item `kinematics.countdown_sec` de `parameterSchema.ts:246-256`; reescrever o resumo do grupo `timeouts` e a variante do modo simples sem mencionar contagem (por exemplo: "Quanto tempo a busca e o retorno podem durar" e "Quanto tempo a busca por giro pode durar").
- **Limites ao sair da folha:** acrescentar a `EXTRA_BOUNDS` (`launchDocument.ts:92-101`) a entrada `'kinematics.countdown_sec': { low: 0, high: 30 }` e manter o caminho em `REQUIRED_LAUNCH_NUMBERS` (`:74`): sem a entrada, `inEnvelope` aceita qualquer finito; com ela, mantém-se a checagem anterior.
- **`App.tsx` não precisa mudar o fluxo:** `launchCountdownSec(doc)` passa a devolver sempre 10 (o documento está fixado), então `seconds = 10`, o overlay abre, o backend recebe a mesma janela. Apenas atualize o comentário em `:278-280` ("o parâmetro deixou de ser editável; a janela é fixa").
- **Backend: nenhuma alteração.** `parameters.py`, `mission_config.json` (`10.0`), `mission.py`, `engine/launch.py`, `steps/takeoff.py` permanecem. W10 permanece.
- **Documentação:** `BE/docs/CHECKLIST_VOO_REAL.md:72` fala em contagem de 3 s, desatualizada; corrija para a janela fixa de 10 s (sequência de solo, aviso em 3,2 s antes do zero, abortar a qualquer momento).

---

## 6. Ordem de implementação (Backend Python -> Frontend TS -> Testes)

> Cada passo é TDD: escreva o teste que falha, implemente, rode. Os arquivos de teste citados existem hoje (verificado).

### PARTE A - Backend Python

**A1. Envelope de altitude (`PY/launch_envelope.py`).** Arquivos de teste: `T/test_launch_envelope.py`, `T/test_launch_surface.py`, `T/test_launch_refusal.py`, `T/test_parameter_conflicts.py`.
- [ ] A1.1 Testes: `(ALTITUDE_MIN_M, ALTITUDE_MAX_M, ALTITUDE_STEP_M) == (0.10, 3.00, 0.05)` (`test_launch_envelope.py:33`); piso sem venue aceita `3.0` e recusa `3.05`; totem de 1,0 m sem venue aceita `1.3`, `2.5`, `3.0`, recusa `1.25` e `3.05`; **com venue 3,2 o máximo é 3,0** (aceita `3.0`, recusa `3.05`); com venue 2,5 o máximo é 2,4 (aceita `2.4`, recusa `2.45`).
- [ ] A1.2 `ALTITUDE_MAX_M: Final[float] = 3.00`; atualizar a docstring de `altitude_envelope` e o comentário de `:35`.
- [ ] A1.3 `TOTEM_MARGIN_UNLOCK_ABOVE_M` e `margin_unlocked(params)` (seção 4). Testes: piso `True`; totem `2.0 -> False`, `2.05 -> True`, `3.0 -> True`; alvo não numérico `False`.
- [ ] A1.4 `mission.py:123-127` (`--height`): ajustar o help para "Altura de operação sobre o piso, em metros (limites em `launch_envelope.py`)". Sem mudança de comportamento.

**A2. Teto e auditoria.** Arquivos: `PY/estimation/ceiling_schedule.py`, `PY/config_audit.py`, `PY/parameter_conflicts.py`. Testes: `T/test_ceiling_schedule.py`, `T/test_odometry.py`, `T/test_config_audit.py`, `T/test_parameter_conflicts.py`, `T/test_platform_config.py`.
- [ ] A2.1 Testes de `CeilingSchedule` (totem 1,0 m, nativo 1,0): alvo `2.0` ignora a margem (margem 0,4 não muda; teto relativo 1,6); alvo `2.05` com margem `0.4` -> `max(1.05, 1.0) + 0.4 = 1.45`; alvo `2.5` margem `0.6` -> 2,1; alvo `3.0` margem `0.6` -> 2,6; com `venue_ceiling_agl_m = 3.2` o teto de 2,7 m e de 3,0 m é cortado em 2,2; piso inalterado; `arm()` com margem `0.30` e alvo `2.5` mantém `transient >= native + margem`. O teste `test_on_the_pad_top_the_ceiling_is_fixed_and_ignores_the_margin` passa a usar alvo `<= 2.0` (e ganha um par para `> 2.0`).
- [ ] A2.2 Implementar `margin_unlocked`, `effective_margin_m`, `nominal_m` (seção 4).
- [ ] A2.3 Testes e implementação da auditoria (`config_audit.py:157-170`, `:304-310`): totem com alvo `2.5` e margem `0.1` gera a divergência `kinematics.altitude_ceiling_margin_m`; totem com alvo `2.0` não gera; totem com alvo `3.0` e venue `3.2` gera a divergência de folga do venue (`ceiling_floor >= venue - 0.2`): **esperado** (D4).
- [ ] A2.4 `E04` e `W02` em `parameter_conflicts.py` com a regra de margem liberada; acrescentar casos ao fixture `T/fixtures/parameter_conflict_cases.json` (totem alvo `2.5` com margem pequena dispara `E04`; totem alvo `2.0` não). O fixture é compartilhado com o TS: aqui só o lado Python; o lado TS fecha em B2 e a paridade em C2.

**A3. Decolagem e subida (`PY/steps/takeoff.py`).** Testes: `T/test_takeoff_acquisition.py`, `T/test_takeoff.py`.
- [ ] A3.1 Substituir o teste `test_on_the_pad_top_every_accepted_target_is_at_or_below_the_native_height` (`:152`) por: para cada alvo aceito sobre o totem acima da altura nativa (relativo > 1,0), `_acquire_altitude` sobe e converge sem violar o teto efetivo; para relativo `<= 1,0` continua descendo ou mantendo.
- [ ] A3.2 Testes de subida: totem alvo `2.5` (relativo 1,5) e `3.0` (relativo 2,0), piso alvo `3.0`: `drone.takeoff` recebe `min(relativo, 2.0)`; há `vz > 0` com `|vz| <= max_climb_speed_mps` e perfil jerk-limitado; `is_ceiling_breached()` nunca dispara; o prazo adaptativo cobre a subida de 2,0 m com planta simples.
- [ ] A3.3 Implementar `TAKEOFF_PLATEAU_M`, o argumento de `takeoff`, o prazo adaptativo e as docstrings (seção 3). Subidas curtas mantêm 15 s (teste).

**A4. Configuração (`PY/mission_config.json`).**
- [ ] A4.1 `platform.venue_ceiling_agl_m = 3.2`. **Não** tocar em `countdown_sec` (continua `10.0`) nem na `kinematics.target_altitude_m` (2). Rodar `T/test_platform_config.py` e `T/test_mission_modes.py`.
- [ ] A4.2 Fixture `T/fixtures/mission_factory_document.json`: **não muda** (a contagem continua 10.0).

### PARTE B - Frontend TypeScript

**B1. Envelope e slider.** Arquivos: `TS/lib/altitudeEnvelope.ts`, `TS/lib/parameterSchema.ts:112`. Testes: `altitudeEnvelope.test.ts`, `missionMode.test.ts`, `parameterConflicts.test.ts`, `launchDocument.test.ts`.
- [ ] B1.1 `ALTITUDE_MAX_M = 3.0`; `TOTEM_MARGIN_UNLOCK_ABOVE_M = 2.0` e `marginUnlocked(doc)` (espelho exato de A1); slider `max: 3`.
- [ ] B1.2 Atualizar os testes que citam `2`: `altitudeEnvelope.test.ts:44` (`[0.1, 3.0, 0.05]`) e `:48`; bloco `altitudeSliderRange` (`:87-101`); `missionMode.test.ts:93,102` (`{min:1.3, max:3}`; com venue 3,2 o slider vai de 1,30 a 3,00; com 2,5 a 2,40); `parameterConflicts.test.ts:76` (E10 dispara em `3.05`, não em `2.05`); `launchDocument.test.ts:485` (`launchBlockers` para `1.25`, `1.8`, `3.05` no totem de 1 m).

**B2. Margem liberada.** Arquivos: `altitudeEnvelope.ts` (`ceilingFloorFrame`), `parameterSchema.ts` (`ceilingMarginLock`), `missionMode.ts:102`, `parameterConflicts.ts` (E04, W02).
- [ ] B2.1 Testes: `missionMode.test.ts` (`'locks the margin on the pad top ...'` mantém as legendas `Teto fixo do perfil de totem: 2,50 m sobre o piso` e `...2,60 m...` para alvo `<= 2.0`; novo caso alvo `2.5`: `ceilingMarginLock` devolve `null`, margem ativa), `ParameterSheet.test.tsx` (campo habilitado no totem com alvo `2.5`, travado com `1.8`; `'unlocks the margin from the floor'` continua), `altitudeEnvelope.test.ts` (`ceilingFloorFrame` com margem liberada).
- [ ] B2.2 Implementar conforme a seção 4.

**B3. Contagem fixa.** Arquivos: `launchDocument.ts`, `batteryFailsafe.ts`, `parameterSchema.ts:246-256` e resumo do grupo, `App.tsx` (só comentário).
- [ ] B3.1 Testes primeiro: (a) `reconcileCoupledPaths` fixa `kinematics.countdown_sec = 10` quando o documento traz `3`, `0`, `30`, ausente ou `NaN`, e devolve o **mesmo objeto** quando já é 10 (`launchDocument.test.ts`); (b) `launchParamsJson` sempre envia `countdown_sec: 10` (`batteryFailsafe.test.ts`); (c) o schema não contém `kinematics.countdown_sec` e nenhum grupo menciona contagem (`ParameterSheet.test.tsx:81,271`: o teste "lists the countdown with the other figures" passa a afirmar que **não** lista; `missionMode.test.ts:63,78`); (d) `launchBlockers` não bloqueia um documento com `countdown_sec: 10` e bloqueia `-1` ou `31` via `EXTRA_BOUNDS`; (e) `App.launchParams.test.tsx`: com `3` no documento guardado, o `paramsJson` enviado tem `10`, a opção `countdown` é `10` e o overlay recebe `seconds: 10`; ajustar `:317-329` ("sends no per-field option and the countdown literally, zero included": passa a "a contagem é fixa em 10").
- [ ] B3.2 `LAUNCH_COUNTDOWN_SEC`, o pino em `reconcileCoupledPaths`, `EXTRA_BOUNDS`, a defesa em `launchParamsJson`, remoção do item e dos textos do schema, comentário de `App.tsx` (seção 5). Conferir `useMissionParameters.test.tsx:186` (`edit('kinematics.countdown_sec', 4)`): trocar por outro campo, pois o campo não é mais editável.
- [ ] B3.3 Fixtures TS que usam `countdown_sec` como enchimento (`mainHarness`, `missionStandby.test.ts`, `__fixtures__/missionDocument.ts`) continuam válidos com `10`.

**B4. Fim de linha do frontend.**
- [ ] B4.1 `npx vitest run` completo e `npm run typecheck && npm run build`.

### PARTE C - Paridade, integração e verificação

- [ ] C1 **Fixture de altitude** `T/fixtures/altitude_envelope_cases.json`: estender a grade com `2.05, 2.4, 2.5, 3.0, 3.05` e os venues `null`, `2.5`, `3.2` (piso e totem). Gerar os esperados a partir do Python (script descartável no scratchpad, não versionado) e **revisar o diff à mão**: os casos antigos com venue 2,5 mudam de `max 2.0` para `max 2.4`; `2.05` deixa de ser recusado; os tetos de totem com alvo `> 2.0` usam a margem do documento (`0.4` nos casos) em vez de `0,6` fixo. Rodar o lado Python e o lado TS **juntos**: `test_the_mission_computes_the_fixture_ceiling_over_the_floor` e o equivalente TS devem concordar.
- [ ] C2 **Fixture de conflitos** `T/fixtures/parameter_conflict_cases.json`: casos novos de E04/W02 no totem (A2.4) verificados nos dois lados (`T/test_parameter_conflicts.py` e `parameterConflicts.test.ts`). Os casos de W10 **não mudam**.
- [ ] C3 **Contratos:** `T/test_contracts.py` e `T/test_mission_parameters.py` verdes sem alteração (milestones e as 9 chaves de `mission.parameters` não mudam).
- [ ] C4 **Integração:** seção 7.
- [ ] C5 **Relatório final** (seção 8).

---

## 7. Comandos exatos

Ambiente (sempre):

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
```

**Unitários Python, por componente (rodar após cada passo da Parte A):**

```bash
python3 -m pytest test/test_launch_envelope.py test/test_launch_envelope_parity.py test/test_launch_surface.py test/test_launch_refusal.py -q
python3 -m pytest test/test_ceiling_schedule.py test/test_odometry.py test/test_config_audit.py test/test_platform_config.py -q
python3 -m pytest test/test_parameter_conflicts.py test/test_parameters.py test/test_parameter_persistence.py test/test_mission_modes.py -q
python3 -m pytest test/test_takeoff_acquisition.py test/test_takeoff.py test/test_takeoff_timing.py test/test_launch_go.py -q
```

**Unitários TypeScript (Parte B):**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/altitudeEnvelope.test.ts src/lib/missionMode.test.ts src/lib/parameterConflicts.test.ts src/lib/launchDocument.test.ts src/lib/batteryFailsafe.test.ts
npx vitest run src/components/preflight/ParameterSheet.test.tsx src/App.launchParams.test.tsx src/hooks/useMissionParameters.test.tsx
```

**Paridade Python/TS (Parte C1, C2; rodar os dois lados em sequência):**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_launch_envelope_parity.py test/test_parameter_conflicts.py -q
cd bebop_mission_control
npx vitest run src/lib/altitudeEnvelope.test.ts src/lib/parameterConflicts.test.ts
```

**Contratos GCS <-> backend e RTL (sempre antes de encerrar):**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py test/test_mission_parameters.py -q
```

**Suíte completa, na ordem (Python depois frontend):**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/ -q
cd bebop_mission_control
npx vitest run
npm run typecheck
npm run build
```

**Integração emulada** (somente com `ps -eo args | grep fake_bebop | grep -v grep` vazio, um lote por vez, depois de checar o processo em standby com `--fly`):

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_fly_path_emulated.py -q
python3 -m pytest test/test_fly_path_totem.py -q
python3 scripts/bench_rehearsal.py
```

Se houver qualquer dúvida sobre processos de voo ou lotes emulados em andamento, **não rode** a integração emulada e diga isso no relatório.

## 8. Riscos, limites e relatório final

**Registrar no relatório (não resolver aqui):**
1. **Folga ao teto de 3,2 m (D4).** Alvo de 3,0 m no totem deixa 0,2 m entre a altitude de operação e o teto efetivo, abaixo do piso de auditoria de 0,21 m: a auditoria avisa. Recomenda-se operar até ~2,7 m.
2. **Detecção do ArUco a 2-3 m (RTL).** Com `fx = 537` e tag de 0,20 m (`rtl.tag_size`), o marcador ocupa ~54 px a 2,0 m e ~36 px a 3,0 m. A 36 px uma tag 36h11 (grade 8x8) fica com ~4,5 px por célula, no limite do detector. **[SUPOSIÇÃO]**; ensaiar com `scripts/test_aruco_tag.py` na altura de operação antes de voar com RTL a 3,0 m. No totem a distância relevante é a relativa (até 2,0 m).
3. **Guarda de borda em altura maior.** O degrau de sonar previsto `s* = borda + h*tan(a)` cresce com `h` (a 2,0 m relativos e 20 graus: +0,73 m); `PlatformMonitor` usa a altura corrente. **[A CONFIRMAR]** em campo.
4. **Alcance útil do sonar do Bebop 2 acima de 2 m.** Sem dado neste repositório. **[A CONFIRMAR]**.
5. **Voz offline (D5).**
6. **Outras sessões** editam `parameterSchema.ts`, `copilotPhrases.ts`, `main.cjs`, `parameters.py` e `takeoff.py`: reconfira `git status` antes de cada tarefa que toque nesses arquivos.

**Relatório final do executor:** arquivos alterados e criados; linha de base vs resultado; o que foi e o que não foi rodado e por quê; o valor de `venue_ceiling_agl_m` gravado (3,2); as divergências de auditoria esperadas (D4); e a confirmação de que o backend de contagem e `mission_config.json` (`countdown_sec = 10.0`) não mudaram.

## 9. Commits propostos (atômicos; só com autorização)

1. `feat(envelope): raise the altitude ceiling of the launch envelope to 3.0 m` (A1, B1, C1)
2. `feat(ceiling): apply the operator margin on the pad top above 2.0 m` (A2, B2, C2)
3. `feat(takeoff): cap the command plateau and size the climb deadline to the gap` (A3)
4. `chore(config): venue ceiling of 3.2 m` (A4)
5. `feat(bmg): fix the launch countdown at 10 s and retire its parameter from the sheet` (B3)
6. `docs: flight checklist with the fixed 10 s countdown` (5)

Rodapé de commit conforme a sessão. **Não commitar nem enviar sem pedido do usuário.**
