# Prompt de Implementacao - Modos Simples/Avancado, RTL configuravel, decolagem totem/solo e trava de altitude no degrau

> Data: 2026-10-20. HEAD auditado: `0144b5b` (`origin/main`), com o trabalho forense nao commitado de outra
> sessao reaplicado na arvore (`electron/main.cjs`, `electron/preload.cjs`, `ForensicPanel*`, `types/bmg.ts`,
> `blackboard.py`, `context.py`, `test/test_forensic_telemetry.py`). O stash `stash@{0}` ("pre-pull foreign
> forensic WIP 2026-10-20") guarda a copia original desse trabalho. Nenhum arquivo de codigo foi alterado nesta
> auditoria e nenhum voo emulado foi rodado. Este documento e a unica saida.
>
> Ele substitui as hipoteses do pedido original quando o codigo as contradiz. A secao 1.4 lista cada uma dessas
> correcoes.

Voce vai trabalhar no repositorio MVP-Bebop em `/home/jv/ros2_ws/src/mvp_mission_bebop/`, a raiz do pacote ROS 2.
Valem integralmente os dois `CLAUDE.md`: tipagem estrita, docstrings NumPy/reST, tolerancia zero a emojis e a
comentarios obvios, Conventional Commits em ingles tecnico. O nectar-sdk (`/home/jv/ros2_ws/src/nectar-sdk`) e o
driver (`/home/jv/ros2_ws/src/ros2_bebop_driver`, que tem alteracoes locais grandes nao commitadas) sao **somente
leitura**. O comportamento do firmware e contornado dentro de `mvp_mission_bebop`.

Prefixos: `BE` = `mvp_mission_bebop/`, `EL` = `bebop_mission_control/electron/`, `FE` = `bebop_mission_control/src/`,
`T` = `test/`. Os numeros de linha sao de `0144b5b` e servem de guia, nao de ancora. Localize pelo simbolo.

Convencao de evidencia: **[FATO]** foi lido na fonte citada. **[SUPOSICAO]** e inferido do comportamento do
firmware e nao foi observado neste repositorio. **[A CONFIRMAR]** exige ensaio de campo.

---

## 0. Regras de execucao

1. **Check-in.**
   - Rode `git status` e `ListAgents`. Outras sessoes editam este tree ao mesmo tempo.
   - Nao toque no trabalho forense listado no cabecalho.
   - So rode `git pull --ff-only origin main` se nao houver conflito com o trabalho local. Se houver, pare e avise.
   - Registre a linha de base:
     `source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py`
     e, em `bebop_mission_control/`, `npx vitest run` e `npm run typecheck`.
2. **Sem commit e sem push** sem autorizacao explicita do usuario neste pedido. Essa regra de memoria do usuario
   prevalece sobre o check-out do CLAUDE.md. Deixe tudo no tree, liste os arquivos e proponha os commits da secao 11.
3. **Voos emulados sao serializados.** Nunca rode dois lotes de `test_fly_path_*.py` ou `test_mission_modes_emulated.py`
   ao mesmo tempo, porque os `ROS_DOMAIN_ID` 80-99 se cruzam. Todo comando pytest, inclusive o de subagentes, comeca
   com `source /home/jv/ros2_ws/bin/nectar-activate &&`.
   - Flakes conhecidos sob carga: `test_inspection_hover`, `test_recovery_and_touchdown`, `test_takeoff`,
     `test_fake_bebop_interlocks` e a seed 3 emulada. Antes de chamar uma falha de regressao, rode o teste sozinho.
4. **Releia o nectar-sdk antes de cada modulo.** Os modulos novos nao tem equivalente no SDK. O padrao interno de
   referencia e `BE/controllers/altitude_hold.py`, `BE/estimation/vertical_estimator.py` e
   `BE/estimation/platform_monitor.py`.
5. **Contrato com o GCS.**
   - Os marcadores `[STEP N: ...]` ficam inalterados. O regex `[STEP ([1-5]):` (`EL/milestones.cjs`) nao muda.
   - `MILESTONE_KEYS` e `ALERT_KEYS` (`BE/telemetry/milestones.py`) sao registros fechados. Esta entrega cria
     **exatamente uma** chave nova, o alerta `mission.launch_refused` (secao 3.4). Ela e registrada nos quatro
     pontos:
     - `BE/telemetry/milestones.py`;
     - `EL/milestones.cjs`;
     - `FE/lib/copilotPhrases.ts`;
     - `SPEC_MILESTONE_KEYS` / chaves de alerta em `T/test_contracts.py`.
   - Fora isso, so se estende payload existente.
   - O `mission_config.json` ganha campos novos sempre com default. Nenhum campo existente muda de nome ou de tipo.
     A unica migracao de valor e `mission_mode: "intermediate"` (secao 3.1).
6. **Invariantes de codigo novo.**
   - Nada de relogio de parede em `controllers/` e `estimation/`. O tempo entra por argumento, vindo do
     `header.stamp`.
   - Nada de acesso a no ROS fora de `telemetry/` e `mission.py`.
   - `vyaw == 0.0` fora dos estagios de yaw do Simples.
   - `vx <= 0` na busca reversa, preservado.

---

## 1. Diagnostico

### 1.1 BUG 1.1: a missao Simples nao decola [FATO, confianca alta]

**Causa raiz.** A estacao e a missao discordam sobre o que `kinematics.target_altitude_m` significa no modo Simples.
O resultado e que todo documento Simples que a GCS produz com o totem configurado e recusado pelo `mission.py`
antes do Estagio 1.

| Lado | Faixa de altitude do Simples | Fonte |
|---|---|---|
| Frontend | relativa ao totem: `pad + [0.7, 1.0]`, ou seja 1,70 a 2,00 m com `platform.height_m = 1` | `FE/lib/missionMode.ts:70` (`SIMPLE_ALTITUDE_RANGE_M`), `:81-88` (`simpleAltitudeRange`) |
| Backend | absoluta: `[0.1, 1.0]` m, com o pad explicitamente ignorado | `BE/mission_mode.py:51,61`, `apply_mission_mode` `:137-150` |

A falha percorre esta cadeia:

1. **Troca de modo.** `normalizeForMode` (`FE/lib/launchDocument.ts:144-159`) prende a altitude em `pad + [0.7, 1.0]`.
   Com pad 1, um valor de 0,8 vira 1,7. `switchModeDocument` grava o resultado no `mission_config.json`.
2. **Lancamento.** `inEnvelope` e `launchBlockers` (`launchDocument.ts:107-118,232-245`) usam a mesma faixa
   relativa e deixam passar. `mission_mode` viaja dentro do `--params-json` (nunca ha `--mode`).
   `missionStandby.take` aceita a espera, porque a chave do standby nao inclui o modo.
3. **`mission.py`.** `apply_mission_mode` lanca `ValueError`, que vira
   `SystemExit("mission_mode: kinematics.target_altitude_m=1.8 is outside the native takeoff range [0.1, 1] m of the simple profile")`.
   - Na partida fria: `mission.py:468-471`.
   - No go do standby: `:1028-1032`. Esse caminho ainda pula `handler.cleanup()` e `nectar.shutdown()`, ao
     contrario dos caminhos vizinhos em `:1022-1027` e `:1054-1059`.
   - O processo sai com codigo 1.
4. **UI.** `bmg:mission-exit {code:1}` faz `missionStateForExit(1)` retornar `'faulted'`
   (`FE/lib/missionOutcome.ts:20-30`). Em `App.tsx:415-417`, `counting && isMissionOver` chama
   `finishCountdown()`. O overlay fecha e a cabine aparece parada.
   - O motivo nunca chega a `launchError`. Ele so aparece no terminal de diagnostico.
5. **Laco do standby.** `refreshStandby()` respawna a espera a partir do `mission_config.json`, que agora contem
   Simples a 1,8 m. A espera morre no `resolve_parameters` e `EL/missionStandby.cjs:121-130` tenta de novo a cada 5 s.

**Evidencia na maquina.**
- O `BE/mission_config.json` ativo tem `platform.height_m = 1` e `target_altitude_m = 1.8`.
- No `localStorage` do Electron, `bmg.working-parameters.simple` guarda `pad=1, alt=1.8` e `alt=1.7`.

**Por que os testes nao pegaram.** Os testes de cada lado travam contratos opostos:
- frontend: `missionMode.test.ts:95-111` e `launchDocument.test.ts:447-456`;
- backend: `test_mission_mode_native_takeoff.py:62-75`.

Nenhum teste passa a saida da estacao pelo `apply_mission_mode` com pad maior que zero.

**[A CONFIRMAR].** Nenhum log de missao e persistido (`recordLog` em `EL/main.cjs:179-194` so guarda em memoria),
entao a mensagem exata da recusa nunca foi lida em disco. Para reproduzir:
`python3 -m mvp_mission_bebop.mission --no-fly --params-json "$(cat <doc simples com pad 1 e alt 1.8>)"`.

**Hipoteses descartadas.**
- `useModeEcho`: o milestone `mission.mode` so e emitido depois do `apply_mission_mode`.
- `validate_yaw_pattern`: os valores ativos passam.
- Contagem regressiva, readiness e conflitos.

### 1.2 BUG 1.2 sintoma A: falso teto na decolagem do totem [FATO para o mecanismo; gatilho exato A CONFIRMAR]

**Sistema de referencia.** O z0 e calibrado sobre o topo do totem (`BE/steps/takeoff.py:327-351`). Todo o controle
vertical roda acima do topo do totem: `relative_altitude = z - z0`.

Com o store ativo (alvo 1,8 m sobre o piso, pad 1,0, margem 0,4, teto do local `venue_ceiling_agl_m` 2,5):

```
alvo no frame de controle   z* = 1.8 - 1.0              = 0.80 m
teto nominal                c  = z* + margem = 0.8+0.4  = 1.20 m   (BE/telemetry/odometry.py:614-644)
fim do perfil nativo        H_fw ~ 1.0 m acima da superficie de decolagem (FIRMWARE_HOVER_HEIGHT_M, estimado)
folga real apos a decolagem c - H_fw                    = 0.20 m
```

1. **O teto nao cobre o fim da decolagem nativa.** O firmware termina a decolagem cerca de 1,0 m acima do topo do
   totem e ignora o alvo. `_ascend` (`takeoff.py:720-750`) **so sobe**. Com `z* = 0.8 < H_fw`, ele retorna "No ascent
   required" e a aeronave continua em cerca de 1,0 m. O teto de 1,20 m deixa 0,20 m de folga, menor que o
   overshoot tipico da decolagem e que o vies de integracao do speedZ.
2. **A primeira verificacao chega tarde.** `_stabilize` nao verifica o teto. A primeira verificacao e
   `_finalize` (`evaluate_system_health`), depois cada ciclo do assentamento de partida do Estagio 2
   (`search.py:200-273`). Esse assentamento desce no maximo -0,08 normalizado, cerca de 0,2 m/s.
3. **O que dispara.** Tres amostras seguidas acima de 1,20 m (cerca de 0,6 s, porque a odometria chega a 5 Hz e
   nao a 15 Hz como diz `parameters.py:481-483`) produzem `Altitude ceiling breached` e depois `trigger_emergency_land`.
4. **Nenhum aviso ao operador.** As regras E03/W01 (`FE/lib/parameterConflicts.ts:193-206,312-324` e
   `BE/parameter_conflicts.py:227-237,313-323`) julgam `alvo + margem` no frame do piso e nunca subtraem o pad.
   Para elas 2,2 >= 1,21, entao nenhum aviso aparece.
5. **A guarda de borda nao ajuda aqui.** A permissao extra de teto (`ceiling_allowance_m`) nao vale na decolagem,
   porque o `PlatformMonitor` fica `UNKNOWN` ate o `_finalize` congelar a origem.
6. **Os testes de totem nao reproduzem.** Eles usam margem 0,6 (teto de 1,4 m) e uma decolagem perfeita para
   exatamente 1,0 m, sem overshoot (`T/support/fake_bebop/plant.py:650-653`).

**[A CONFIRMAR]** se o gatilho de campo e overshoot, efeito solo sobre o topo de 60x60 cm ou vies de integracao.
Os logs que discriminam sao `[TIMING] t_takeoff_confirmed alt=`, `Liftoff transient` e `[ALT_PROBE z_rel fw_alt]`.

### 1.3 BUG 1.2 sintoma B: queda ao sair do totem e subida ao reentrar

**O que a missao mede [FATO].**
- **Nao existe topico de sonar.** O ARSDK deste driver nao tem `AltitudeAboveGroundChanged`. O ultrassom so aparece
  como flag OK/NOK em `SensorsStatesListChanged`.
- A unica realimentacao vertical e `/bebop/odom pose.z`, que e a **integral do `SpeedChanged.speedZ`** feita pelo
  driver (`ros2_bebop_driver/src/telemetry_state.cpp:37-51`, com dt limitado a 0,5 s, a cerca de 5 Hz).
- `/bebop/states/altitude` e o `AltitudeChanged`, "acima do ponto de decolagem", com fonte de sensor nao documentada.
  Hoje ele so vai para o log (`calibration.altitude_anchor_enabled = false`).
- O estimador ativo e `calibration.altitude_estimator = "legacy"`, que e o portao de plausibilidade de posicao
  (`BE/estimation/altitude_plausibility.py:82-122`). Ele **adota um degrau depois de 3 amostras**. Ou seja, se o
  firmware registrar o degrau como pico de velocidade, o `legacy` transforma esse pico num offset permanente de altitude.

**O que o firmware faz [SUPOSICAO; nao ha log de campo].** O Bebop 2 funde ultrassom, barometro e acelerometro no
seu proprio estimador vertical. Em baixa altura, esse estimador acompanha o terreno. Ao cruzar a borda do totem, a
leitura do ultrassom salta em `h_p = 1,0 m`. Ha tres desfechos possiveis
(`docs/PROMPT_IMPLEMENTACAO_TOTEM_VOO_ALTITUDE.md` secao 1.5):

| Modelo | O que acontece | Efeito observado |
|---|---|---|
| **M1** | O laco do firmware move a aeronave para restaurar a altura sobre o novo terreno | Afunda cerca de 1 m ao sair e sobe cerca de 1 m ao reentrar. O speedZ mostra o movimento real. |
| **M2** | O estimador do firmware registra o degrau como velocidade | Pico no speedZ, que vira degrau no `odom.z`. O governador "corrige" um erro que nao existe e afunda ou sobe de verdade. |
| **M3** | Nao ha reacao | Nada. |

A guarda de borda atual (L3: limites, ganho, shaper sigma-delta que nunca envia gaz exatamente 0, parada de
translacao, descida forcada na reentrada) parte de duas premissas nao validadas:
- a retencao por sonar do firmware so atua com gaz == 0;
- o along-track da odometria e correto.

O emulador codifica exatamente essas premissas (`plant.py:682-689`), entao os testes verdes do totem **nao validam
o firmware**. Os ensaios de campo F-0 a F-4 (`docs/CHECKLIST_VOO_REAL.md:108-127`) continuam todos desmarcados.

### 1.4 Correcoes ao enunciado original (prevalecem sobre ele)

| Enunciado | Realidade no codigo | Consequencia |
|---|---|---|
| "guarda estrita de teto **por ultrassom**" | Nao ha ultrassom na missao. O teto compara a integral do speedZ com `z* + margem` (secao 1.2). | O ajuste e no modelo de teto (secao 3.5), nao em um sensor. |
| Totem: "para 1,8 m, a **subida** assistida acima do totem e de apenas 0,8 m" | A decolagem nativa ja termina em cerca de 1,0 m acima do totem. Com alvo 1,8 sobre o piso, `z* = 0,8`. | A correcao pos-decolagem e uma **descida** de `H_fw - z* = 0,2 m`. No totem, `z*` fica sempre em `[0.3, 1.0]`, entao a correcao nunca e uma subida. |
| Atalho Ctrl+C "a implementar" | Ja existe: `FE/lib/abortShortcut.ts:11-20` (Escape e Ctrl/Cmd+C) e `CockpitScreen.tsx:137-152`, com testes em `CockpitScreen.test.tsx:185-246`. | A falha real e outra: `isTextInputOrTerminal` (`CockpitScreen.tsx:21-28`) trata **qualquer** `INPUT`, inclusive `type=range`, como campo de texto. Depois de mexer no slider de tilt (`OpticalFeed.tsx:362`) ou de volume (`shell/StatusBar.tsx:250`), o foco fica no range e o Ctrl+C e ignorado sem aviso. Alem disso, um `iframe` com foco nao repassa `keydown` para a janela. |
| Parametros independentes por modo "a implementar" | Ja existe em parte: `bmg.working-parameters.<mode>` em `localStorage` (`FE/lib/parameterModes.ts:18-26`) e restauracao na troca de modo (`useMissionParameters.ts:246-281`). | Falta a **lista** de favoritos (hoje ha um unico "Meu ajuste" por modo) e a inclusao dos booleanos novos. |
| Altitude de 0,1 m "no totem" | Sobre o totem, uma cota abaixo de `1,0 + 0,30 m` poe a aeronave dentro do proprio totem ou abaixo do piso de "airborne" (`relative_target_altitude_m`, `parameters.py:1359-1393`, lanca `ValueError`, inclusive **em voo**). | Decisao D1 (secao 2). |

---

## 2. Decisoes de arquitetura

Estas decisoes estao fechadas para a implementacao. Se o usuario divergir de D1 ou D2, ajuste so as secoes citadas.

- **D1. Envelope de altitude por superficie.**
  - O operador sempre ve e edita a cota **sobre o piso**, com slider de 0,10 a 2,00 m e passo de 0,05 m.
  - Totem: o minimo efetivo e `h_p + c_p`, onde `c_p = max(PAD_CLEARANCE_M = 0.30, kinematics.takeoff_settle_min_altitude_m)`.
    Com os defaults isso da **1,30 m**. O minimo do slider sobe para esse valor, e um valor abaixo e preso nele com
    aviso visivel.
  - Solo: minimo de 0,10 m.
  - Maximo de 2,00 m nas duas superficies, e nunca acima de `venue_ceiling_agl_m - 0,10`.
  - Alternativa rejeitada: permitir cota abaixo do topo do totem no Avancado com descida depois de sair da projecao.
    Ela exige um terceiro alvo vertical dependente do `PlatformMonitor` e contradiz a "linha de voo plana".
- **D2. No totem o teto nao e desligado: ele e fixo e ancorado no firmware.**
  - O campo de margem fica desabilitado na UI e o `kinematics.altitude_ceiling_margin_m` gravado e ignorado.
  - O teto passa a ser `min(V - h_p, max(z*, H_fw) + TOTEM_CEILING_HEADROOM_M)`, com
    `TOTEM_CEILING_HEADROOM_M: Final = 0.60`. Com os defaults isso da 1,50 m acima do totem (2,50 m sobre o piso),
    ou seja, o teto do local.
  - Assim, nenhum falso alarme e possivel no fim do perfil nativo, e continua havendo envelope absoluto contra
    subida descontrolada na reentrada (M1).
- **D3. O teto vira uma catraca durante a aquisicao de altitude (secao 3.5), nas duas superficies.** Essa e a
  correcao estrutural do sintoma A. Ela torna a regra E03 obsoleta.
- **D4. A aquisicao de altitude pos-decolagem e bidirecional e vale para os dois modos.** O Simples deixa de voar na
  altura nativa e passa a voar no alvo, como pede o item 2.1.
- **D5. O Intermediario e removido. Ele e exatamente "Avancado com `rtl.enabled = false`".**
  - Documentos legados com `mission_mode: "intermediate"` migram para `advanced` com `rtl.enabled = false`, no backend
    e no frontend, com a mesma regra e com teste.
  - Fora da migracao, `--mode intermediate` e recusado.
- **D6. `platform.height_m` continua sendo a altura fisica do totem (1,0, fora da UI).** O checkbox
  `platform.takeoff_from_ground` so escolhe a superficie de decolagem. A altura efetiva do pad e resolvida em memoria
  depois da persistencia, no mesmo idioma de `neutralize_launch_pad`.
- **D7. Estimador vertical sensivel ao terreno (secao 3.8).**
  - Vira default (`calibration.altitude_estimator = "terrain_kf"`) somente quando os criterios emulados M1, M2 e M3
    da secao 10 passarem. `legacy` e `vz_gated` continuam selecionaveis.
  - O uso do `AltitudeChanged` como medida fica atras de `calibration.firmware_altitude_source`, cujo default
    `"unknown"` significa que ele so vai para o log ate o ensaio F-ID.
- **D8. O Ctrl+C e interceptado no processo principal** (`before-input-event`) enquanto a cabine declara o abort
  disponivel, e entregue ao **mesmo** handler do botao. O listener do renderer fica como fallback sem ponte.
- **D9. Os favoritos ficam no `localStorage`, por maquina e por modo**, em lista com nome. Nao ha sincronizacao
  entre maquinas, porque o `mission_config.json` tambem esta no gitignore.

---

## 3. Especificacao: backend (`mvp_mission_bebop`)

### 3.1 Parametros novos e migracao (`BE/parameters.py`)

1. `ReturnToLaunchConfig.enabled: bool = True`.
   - Docstring: com `True`, o fim de voo e um RTL completo com busca de marcador, centralizacao e pouso de precisao.
     Com `False`, a aeronave pousa no local logo depois da evidencia.
   - Envelope: bool estrito.
2. `PlatformConfig.takeoff_from_ground: bool = False`.
   - Docstring: com `False`, decola do topo do totem de `height_m`. Com `True`, decola do piso e `height_m` nao
     participa do voo.
3. **Coercao estrita.** `update_from_dict` e `load_from_file` aceitam somente `bool` para esses dois campos.
   - Inteiro 0/1, string ou `None` geram `TypeError` com o caminho do campo. A string `"false"` e truthy, e esse e o
     motivo da regra.
   - Siga o padrao de validacao defensiva existente no arquivo.
4. **Migracao de modo legado.** Crie `migrate_legacy_mission_mode(raw: Dict[str, Any]) -> Dict[str, Any]`, uma funcao
   pura e idempotente.
   - Se `raw.get("mission_mode")`, apos `strip().lower()`, for `"intermediate"`, ela grava `mission_mode = "advanced"`
     e `rtl.enabled = False`, sem sobrescrever um `rtl.enabled` explicito, e registra um `logger.warning` uma unica vez.
   - Chame a funcao no inicio de `update_from_dict`, que tambem atende o go do standby, e de `load_from_file`.
5. `relative_target_altitude_m` (`:1359-1393`) passa a usar a **altura efetiva** do pad (secao 3.3).
   - O `ValueError` dele so pode ocorrer em resolucao, nunca em voo. A secao 3.4 garante isso validando antes.
6. Atualize:
   - `_FROZEN_CONFIGURATION_KEYS` em `T/test_contracts.py:~303-332`;
   - `T/fixtures/mission_factory_document.json`, regenerado de `MissionParameters().to_dict()`;
   - `FE/lib/__fixtures__/missionDocument.ts`, se ele espelhar o fixture.
   - O teste `test_the_lateral_refactor_adds_and_removes_no_configuration_field` (`:456-465`) tambem compara com o
     `mission_config.json` local, que esta no gitignore. Nao edite esse arquivo a mao. Faca o teste tolerar chaves
     ausentes no arquivo local que tenham default, e documente isso na docstring.

### 3.2 `BE/mission_mode.py`

1. `MissionMode` passa a ter somente `SIMPLE` e `ADVANCED`. Remova `INTERMEDIATE` e `returns_to_base`, que nao tem uso.
2. `parse_mission_mode("intermediate")` lanca `ValueError` com a mensagem
   `"mission mode 'intermediate' was removed; stored documents migrate to 'advanced' with rtl.enabled=false"`.
   A migracao da secao 3.1 roda antes, entao documentos persistidos nunca chegam aqui.
3. `apply_mission_mode`:
   - Remova a verificacao de altitude absoluta `[0.1, 1.0]` e a fixacao da margem no Simples. As duas sao a causa
     do BUG 1.1.
   - Ela so resolve e canoniza o modo. A validacao de envelope passa para `launch_envelope.py` (secao 3.4).
4. Remova `MIN_SIMPLE_ALTITUDE_M` e `SIMPLE_CEILING_MARGIN_M`. Mantenha `NATIVE_HOVER_HEIGHT_M`; o teste que o mantem
   igual a `parameter_conflicts.FIRMWARE_HOVER_HEIGHT_M` e ao simulador continua valendo.
5. Substitua `neutralize_launch_pad` por `apply_launch_surface(params) -> None` (secao 3.3).
6. Atualize `BE/mission.py:242-252`: `--mode choices=("simple", "advanced")` e o texto de ajuda.

### 3.3 Superficie de decolagem (`BE/mission_mode.py:apply_launch_surface`)

Funcao pura sobre `params`, chamada em `mission.py` no lugar de `neutralize_launch_pad` (`:1067-1073`), **depois** da
persistencia e do milestone `mission.mode`. Assim o documento gravado mantem o pad configurado.

```
se platform.takeoff_from_ground:
    params.launch_pad_height_m_original = platform.height_m   (atributo simples, nao dataclass)
    platform.height_m = 0.0
    platform.edge_ahead_m = None          (sem totem, nao ha degrau a guardar)
senao, se modo == SIMPLE:
    platform.edge_ahead_m = None          (sem translacao, a guarda de borda nao se aplica;
                                           height_m e MANTIDO: a aeronave paira sobre o totem e
                                           height_above_floor_m precisa refletir isso)
```

- Nao mexa mais em `altitude_ceiling_margin_m` aqui. O teto passa a ser responsabilidade da secao 3.5.
- Efeito colateral corrigido: hoje o Simples zera o pad, e `height_above_floor_m` (projecao no solo) sai errado em
  1,0 m sobre o totem.
- Registre no log uma unica linha estruturada:
  `[SURFACE] takeoff=platform|ground pad=%.2f target_floor=%.2f target_rel=%.2f`.

### 3.4 Envelope de lancamento unico e recusa estruturada (correcao definitiva do BUG 1.1)

1. **Modulo novo `BE/launch_envelope.py`**, puro, com constantes `Final` e docstrings NumPy:
   ```python
   ALTITUDE_MIN_M: Final[float] = 0.10
   ALTITUDE_MAX_M: Final[float] = 2.00
   ALTITUDE_STEP_M: Final[float] = 0.05
   PAD_CLEARANCE_M: Final[float] = 0.30
   VENUE_HEADROOM_M: Final[float] = 0.10

   def altitude_envelope(params) -> Tuple[float, float]
       # (min, max) over the floor for the configured surface, per D1
   def validate_launch_envelope(params) -> None
       # raises LaunchEnvelopeError(field: str, reason: str), a ValueError subclass
   ```
   - Regras de `validate_launch_envelope`:
     - `target_altitude_m` deve ser finito e estar em `altitude_envelope`;
     - se `takeoff_from_ground` for falso e `platform.height_m` for menor ou igual a 0, recusa com `"platform.height_m"`;
     - `rtl.enabled` e `takeoff_from_ground` devem ser bool.
   - Use os valores **antes** de `apply_launch_surface`, ou seja, com o pad configurado.
2. **Chamada em `mission.py`.** Chame `validate_launch_envelope` logo depois de `apply_mission_mode` nos dois caminhos:
   - partida fria: `resolve_parameters`, `:468-471`;
   - standby: `:1028-1032`.
3. **Recusa estruturada**, nos dois caminhos, para `ValueError`/`TypeError` de `apply_mission_mode`, de
   `validate_launch_envelope` e da migracao:
   - `emit_alert("mission.launch_refused", {"field": <caminho ou "mission_mode">, "reason": <mensagem curta em pt-BR>})`.
     Esta e a chave nova unica (regra 0.5).
   - Imprima `print(f"[LAUNCH_REFUSED] {field}: {reason}", flush=True)`.
   - No caminho standby, rode `handler.cleanup()` e `nectar.shutdown()`, como os caminhos vizinhos.
   - Saia com `sys.exit(EXIT_LAUNCH_REFUSED)`. Crie `EXIT_LAUNCH_REFUSED: Final[int] = 78` (EX_CONFIG) em
     `BE/engine/exit_codes.py` e espelhe em `FE/lib/missionOutcome.ts`.
   - Confirme que `emit_alert` nao depende de no ROS ativo. Pela leitura, ele so loga no stdout.
4. Teste de contrato cruzado (secao 7, `test_launch_envelope_parity`).

### 3.5 Teto: modelo de catraca (correcao estrutural do sintoma A)

**Modulo novo `BE/estimation/ceiling_schedule.py`**, puro, sem lock e sem relogio: `class CeilingSchedule`.

Notacao, toda no frame de controle (acima da superficie de decolagem):

| Simbolo | Significado | Origem |
|---|---|---|
| `h_p` | altura efetiva do pad, ja resolvida na secao 3.3 | `platform.height_m` |
| `z*` | alvo no frame de controle | `relative_target_altitude_m` |
| `m` | margem do operador | `kinematics.altitude_ceiling_margin_m`, so no solo |
| `m_T` | folga fixa do totem | `TOTEM_CEILING_HEADROOM_M = 0.60` |
| `H_fw` | altura nativa | `NATIVE_HOVER_HEIGHT_M` |
| `V` | teto do local | `platform.venue_ceiling_agl_m`, se finito |
| `a` | permissao da guarda | `platform.ceiling_allowance_m` |

**Teto nominal:**

```
solo  : c_n = z* + m
totem : c_n = max(z*, H_fw) + m_T                       (D2; m ignorada)
guarda: c_n += a   enquanto guard_active                (como hoje)
local : c   = min(c_n, V - h_p)                          (sempre; inclusive em c_t)
```

**Teto transitorio (catraca)** entre o comando de takeoff e a convergencia da aquisicao (secao 3.6). Seja `m_e = m`
no solo e `m_e = m_T` no totem:

```
arm():          c_t <- max(c_n, H_fw + m_e)
update(z_k):    c_t <- min(c_t, max(c_n, z_k + m_e))     (monotono nao crescente)
release():      transitorio encerrado; value() = c
value():        min(c_t se armado senao c_n, V - h_p)
```

**Propriedades a testar.**
- `value()` nunca cresce enquanto a catraca esta armada.
- `value()` nunca fica abaixo de `z_k + m_e` para nenhum `z_k` ja observado depois de `arm()`, exceto quando limitado
  pelo teto do local.
- Depois de `release()` com `|z - z*| <= deadband`, `value()` e igual a `c`, porque `max(c_n, z* + m_e) = c_n` por
  construcao.

**Integracao.**
1. `OdometrySupervisor.altitude_ceiling` (`BE/telemetry/odometry.py:614-644`) passa a delegar a `CeilingSchedule`.
   - `_store_sample` chama `schedule.update(rel_alt)` **antes** de comparar com o teto, dentro do lock ja tomado.
     A propriedade continua sem tomar lock, como documentado.
   - Exponha `arm_ceiling_transient()` e `release_ceiling_transient()`.
2. **Durante o perfil nativo** (`flying_state` em `takingoff`), a verificacao de teto usa **somente** `V - h_p`.
   - Justificativa (comentario tecnico permitido): o firmware controla a altitude nesse intervalo. Um pouso de
     emergencia comandado durante o perfil e pior que deixa-lo terminar, salvo acima do teto do local.
3. Adicione a verificacao do teto do local em `_stabilize`, que hoje nao tem nenhuma.
4. Corrija a docstring de `kinematics.ceiling_breach_streak` (`parameters.py:481-483`): sao cerca de 5 Hz, ou seja,
   cerca de 0,6 s com streak 3. **Nao** crie campo novo.
5. Atualize os testes que fixam o teto de 1,4 m com a margem default (`T/test_floor_reference.py:142-176`,
   `T/test_platform_config.py:83-146`) para a nova formula, mantendo os casos de solo identicos.

### 3.6 Estagio 1: aquisicao de altitude bidirecional (`BE/steps/takeoff.py`)

1. Remova o desvio por modo em `:225-233`. Os dois modos executam `_acquire_altitude`.
2. **Renomeie `_ascend` para `_acquire_altitude`** e mantenha o ramo de subida intacto: perfil jerk-limited, envelope
   de frenagem, janela de subida do supervisor e verificacao de teto a cada ciclo. Acrescente o ramo de **descida**
   para `z > z* + governor.deadband_m`:
   - comando por `AltitudeHoldGovernor.compute_vz(relative_altitude, dt, vx_commanded=0.0)` com translacao zero,
     no mesmo padrao de `_settle_before_departure` (`search.py:200-273`);
   - convergencia quando `|z - z*| <= deadband` por 1,0 s, usando o `SettlementDetector` ja usado em `_stabilize`;
   - timeout igual ao do ramo de subida, **sem campo novo**. Ao expirar, siga com aviso, como o assentamento de
     partida faz hoje;
   - `evaluate_system_health()` a cada ciclo, com o teto da catraca (secao 3.5).
   - Anuncio: `self._announce("Descendo", "ajustando altitude de operacao")`, simetrico ao de subida.
3. `arm_ceiling_transient()` antes de `_command_takeoff`. `release_ceiling_transient()` no `_finalize`, depois da
   convergencia e antes de congelar a origem.
4. Milestone de decolagem (`:123-124`): **remova** o `native: True` especifico do Simples e acrescente
   `takeoff_surface` (`"platform"` ou `"ground"`) e `target_altitude_m` (sobre o piso) ao payload existente.
5. No totem, `z*` fica sempre em `[0.30, 1.00]` (D1), entao o ramo de subida so roda no solo. Registre isso em teste,
   nao em comentario.

### 3.7 Estagios 4 e 5 pelo flag de RTL

1. **`BE/steps/pipeline.py`:** `build_steps(mission_mode, rtl_enabled)`.
   - Os nomes do estagio 5 (`BE/steps/rtl.py:103-107`, `BE/engine/runner.py:28-34`, `EL/main.cjs:2873-2879`,
     `FE/types/mission.ts:23`) passam a ser uma funcao de `(mode, rtl_enabled)`:
     - Avancado com RTL: `"ArUco RTL & Precision Landing"`;
     - Simples com RTL: `"ArUco Centering & Base Landing"`;
     - qualquer modo sem RTL: `"Landing at Inspection Point"`.
   - Remova as tabelas `_STEP_NAMES` do Intermediario em `search.py`, `tracking.py`, `inspection.py` e `rtl.py`.
2. **`BE/steps/inspection.py:174-178`:** no Simples, `_restore_heading` roda **sempre**, com ou sem RTL, como pede o
   item 2.2.
   - Depois, o gimbal vai para `rtl.camera_tilt_deg` com RTL. Sem RTL, ele vai para a constante
     `STOW_GIMBAL_TILT_DEG: Final[float] = -80.0`, porque os campos de marcador ficam ocultos e o valor gravado nao
     pode decidir.
   - Falha de `_restore_heading`: mantenha o retorno atual de status e documente que o estagio 5 ainda pousa.
3. **`BE/steps/rtl.py:execute` (`:486-540`):**
   ```
   if not rtl_cfg.enabled:            -> self._land_in_place(ctx, guidance)   (ambos os modos)
   elif mode is SIMPLE:               -> _land_over_base (como hoje)
   else:                              -> RTL completo (como hoje)
   ```
   - `_land_in_place` (`:651-662`) nao muda de comportamento. O `_halt_translation` continua sensivel a guarda.
   - No Simples sem RTL, a aeronave pousa sobre o totem: estado `ON`, e `_grounded_altitude` mantem o limiar sem
     desconto, o que esta correto. Adicione um teste para esse caso.
   - O failsafe de bateria salta para o estagio 5 e herda o mesmo ramo.
4. **Anuncios em `rtl.py`** (`:548-551`, `:599-602`, `:660`, `:712-715`, `:1824-1838`): os textos "retorno",
   "base" e "marcador" so podem ser emitidos com `rtl_cfg.enabled`. O caminho sem RTL fala somente "pouso no local".

### 3.8 Estimador vertical sensivel ao terreno (item 3.3; o requisito nao negociavel)

**Objetivo.** A cota usada pelo governador e pelo teto deve ser continua e fisicamente correta ao cruzar a borda do
totem nos dois sentidos, nos tres modelos de firmware M1, M2 e M3. Nao e possivel ler o sonar. O que e possivel:

- (i) nao deixar o degrau entrar na estimativa (resolve M2);
- (ii) nao bloquear o movimento real, para o governador poder contrariar a reacao do firmware (resolve M1);
- (iii) congelar a integral do governador quando a estimativa estiver so propagando, sem realimentacao confiavel.

Esse e o mesmo principio do EKF2 do PX4: a altura vem de baro/inercial, e o degrau de terreno e absorvido por um
estado de terreno separado, nunca pela altura do veiculo.

**Modulo novo `BE/estimation/terrain_vertical_filter.py`: `class TerrainAwareVerticalFilter`.** E um filtro de
Kalman linear, puro (sem ROS, sem relogio de parede, `stamp_sec` vindo de `header.stamp`), no estilo de
`vertical_estimator.py`.

**Estado** `x = [z, v, b]^T`: altura no frame de controle, velocidade vertical para cima e vies do `AltitudeChanged`.
O vies so e observavel com ancora; sem ancora, mantenha `P_bb` constante.

**Processo**, com `dt` do stamp limitado a `MAX_STEP_SEC`:

```
u      = ultimo gaz normalizado efetivamente enviado (apos clamp do failsafe)
K_u    = calibration.vertical_normalized_to_mps            (2.5 no store ativo)
alpha  = 1 - exp(-dt / tau_v),  tau_v = calibration.vertical_lag_tau_sec
z+ = z + v*dt
v+ = v + alpha*(K_u*u - v)
b+ = b
Q  = sigma_a^2 * [[dt^4/4, dt^3/2, 0],[dt^3/2, dt^2, 0],[0, 0, q_b*dt/sigma_a^2]]
```

O comando entra pelo `OdometrySupervisor.note_commanded_vz(u, stamp_sec)`, chamado pelo proxy de comando em
`actuators/` no momento da publicacao. **Nao** leia o valor do governador, porque o clamp do failsafe pode te-lo
alterado.

**Medida 1, `speedZ`** (`twist.linear.z`, ENU para cima), com `H_v = [0, 1, 0]` e `R_v = sigma_v^2`:
- Teste de inovacao: `d2 = nu^2 / S`, aceita se `d2 <= 9.0` (cerca de 3 sigma, 1 grau de liberdade) **e**
  `|y_v - v_trusted| / dt <= calibration.vz_max_accel_mps2`. A plausibilidade de aceleracao separa pico (M2) de
  movimento real (M1).
- Dentro da janela da guarda (`PlatformMonitor` `guard_active`, ou estado `LEAVING`/`ENTERING`):
  `R_v *= calibration.terrain_step_noise_scale`, e a readquisicao por streak (`vz_reject_streak`) fica **suspensa**
  ate a janela fechar. E isso que impede o filtro de adotar o pico, como o `legacy` faz hoje.
- Fora da janela, readquira depois de `vz_reject_streak` rejeicoes seguidas, como no `vz_gated`.

**Medida 2, `AltitudeChanged`** (opcional, `calibration.firmware_altitude_source`):
- `"unknown"` (default): so log, nenhuma atualizacao.
- `"baro"`: `y_a = z + b`, com `R_a`.
- `"sonar_fused"`: `y_a = z + b + T`, com `T` em `{0, +h_p}`. `T = 0` sobre o totem; fora dele, a distancia ao
  terreno cresce `h_p`.
  - `T` e uma hipotese discreta: calcule a inovacao para os dois valores e escolha o de menor NIS. Use histerese de
    `NIS_HYSTERESIS = 4.0` e prior do `PlatformMonitor` (estado `OFF` favorece `+h_p`).
  - **A troca de hipotese nunca altera o estado `x`.** Ela so altera o modelo de medida, e e isso que torna a cota
    continua no degrau.
- Em todas as fontes, uma inovacao fora do gate e rejeitada. A persistencia da rejeicao por 3 s ou mais liga o flag
  `datum_disagreement` existente.

**Saidas:** `z_est`, `v_est`, `coasting: bool` (sem `speedZ` aceito nos ultimos `COAST_SAMPLES = 2` ciclos),
`terrain_hypothesis: Optional[int]`, `last_nis`.

**Acoplamento com o controle** (`BE/controllers/altitude_hold.py`):
- `compute_vz(..., hold_integrator: bool = False)`. Com `True`, o termo integral nao acumula (anti-windup por
  ausencia de medida).
- `OdometrySupervisor` expoe `vertical_coasting()`. Os call sites (`search.py`, `tracking.py`, `inspection.py`,
  `rtl.py`) passam esse valor sem alterar mais nada.
- Os feedforwards inertes da L3 continuam inertes. Eles so serao calibrados depois do F-ID (secao 9).

**Parametros novos** (todos em `CalibrationConfig`, com default e envelope documentado, todos [A CONFIRMAR] e
ajustados no emulador):

| Campo | Default | Envelope |
|---|---|---|
| `altitude_estimator` | acrescente `"terrain_kf"` a `normalize_altitude_estimator`; default `"legacy"` ate o gate D7, depois `"terrain_kf"` | uma das strings aceitas |
| `firmware_altitude_source` | `"unknown"` | `"unknown"`, `"baro"` ou `"sonar_fused"` |
| `vertical_lag_tau_sec` | 0.35 | (0.05, 2.0] |
| `vertical_accel_noise_mps2` | 1.5 | (0, 10] |
| `vz_measurement_noise_mps` | 0.08 | (0, 1] |
| `altitude_measurement_noise_m` | 0.10 | (0, 1] |
| `terrain_step_noise_scale` | 16.0 | [1, 1000] |

**Integracao em `BE/telemetry/odometry.py:odometry_callback` (`:295-404`):**
- Novo ramo `"terrain_kf"`, ao lado de `legacy` e `vz_gated`, semeado com o primeiro `pose.z`, como o `vz_gated`.
- O ramo `legacy` fica **identico**; os testes existentes provam isso.
- Linha de log a cada amostra rejeitada e a cada troca de hipotese:
  `[VERT_KF] nis=%.2f accepted=%d coasting=%d T=%s z=%.3f v=%.3f`.

### 3.9 Milestones e anuncios

1. O payload de `mission.mode` (`mission.py:1064`) passa a ser
   `{"mode", "rtl_enabled", "takeoff_surface", "target_altitude_m"}`. A chave e a mesma, so o payload e estendido.
   Atualize `T/test_contracts.py:679-687`.
2. O anuncio de inicio de voo do backend inclui o plano:
   - com RTL: `"retorno a base com pouso de precisao ao final"`;
   - sem RTL: `"pouso no local apos a foto"`.
3. Atualize `BE/telemetry/announcer.py:558-561` e `runner.py:33` ("Retorno a base") pela funcao da secao 3.7.

### 3.10 Auditoria e conflitos (`BE/config_audit.py`, `BE/parameter_conflicts.py`)

Estas regras espelham a secao 5.4 e usam o mesmo fixture `T/fixtures/parameter_conflict_cases.json`.

- **Aposente E03 e W01.** O codigo fica reservado, e a docstring aponta para a catraca da secao 3.5.
- Regras novas:
  - **E10.** Cota fora de `altitude_envelope` para a superficie. A mensagem cita o minimo do totem.
  - **E11.** `target_altitude_m >= V - VENUE_HEADROOM_M`.
  - **W10.** Somente no solo: `m < climb_deadband_m + 2*sigma`, ou seja menor que 0,21 com os defaults.
  - **W11.** Cota sobre o piso abaixo de `vision.min_altitude_for_ibvs_m` (0,35): IBVS degradado e efeito solo.
- `config_audit.py:137-167,272-309`: use o frame correto, com o pad efetivo.

---

## 4. Especificacao: Electron (`EL/`)

1. **`EL/missionMode.cjs` e `.d.cts`:** `MISSION_MODES = ['simple', 'advanced']`.
   - `normalizeLaunchMode('intermediate')` recusa com a mensagem `"Modo Intermediario removido: use Avancado sem RTL."`.
     O frontend migra antes, entao isso so pega documento externo.
2. **Saida 78.** Em `missionProcess.on('close')` (`main.cjs:~2680-2689`), com `code === 78`, envie
   `bmg:launch-refused {field, reason}`. Os dados vem do ultimo alerta `mission.launch_refused` lido no stdout ou, como
   fallback, da ultima linha `[LAUNCH_REFUSED]`. O `bmg:mission-exit` continua sendo enviado.
3. **`EL/missionStandby.cjs`.** Uma espera que sai com 78 vai para o estado `refused` e **nao** tenta de novo a cada
   5 s. So `refreshStandby()`, chamado depois de `bmg:save-parameters`, reabilita a espera.
   - Inclua `mission_mode`, `rtl.enabled` e `platform.takeoff_from_ground` nos `CRITICAL_PATHS`, tanto em
     `missionStandby.cjs:22-33` quanto em `BE/engine/launch.py:52-63`. Assim uma espera preparada com outra
     superficie ou outro plano nunca recebe o go.
4. **Log de missao persistente**, pre-requisito do F-ID (secao 9):
   - `recordLog` (`main.cjs:179-194`) passa a duplicar stdout e stderr de cada `mission.py` em
     `app.getPath('userData')/logs/mission_<YYYYMMDD_HHMMSS>.log`;
   - escrita em append, com rotacao que mantem os 30 mais recentes;
   - sem bloquear o loop: use `fs.createWriteStream`.
5. **Ctrl+C no processo principal (D8):**
   - IPC `bmg:cockpit-abort-armed {armed: boolean}`, renderer para main.
   - Em `mainWindow.webContents.on('before-input-event', (event, input) => ...)`, com `armed` verdadeiro,
     `input.type === 'keyDown'` e `!input.isAutoRepeat`: se for `(input.control || input.meta) && input.code === 'KeyC' && !input.shift && !input.alt`,
     ou `input.key === 'Escape'`, faca `event.preventDefault()` e `webContents.send('bmg:abort-shortcut')`.
     Isso cobre foco em `iframe` e em `input[type=range]`, e bloqueia o Copy do menu oculto.
   - `EL/preload.cjs`: `setCockpitAbortArmed(armed)` e `onAbortShortcut(cb) -> unsubscribe`. Atualize `FE/types/bmg.ts`.
     Os dois arquivos tem alteracoes forenses de outra sessao: edite por adicao, sem reordenar nada.

---

## 5. Especificacao: frontend (`FE/`)

### 5.1 Remocao do Intermediario e migracao

1. **`FE/lib/missionMode.ts`.**
   - `type MissionMode = 'simple' | 'advanced'`, com `MISSION_MODES` de dois itens. Rotulos e resumos:
     - Simples: "Giro no lugar, foto e retorno do rumo".
     - Avancado: "Varredura lateral, foto e retorno lateral".
   - Remova `SIMPLE_ALTITUDE_RANGE_M`, `simpleAltitudeRange` e `PLATFORM_HEIGHT_PATH` (passa a morar em
     `altitudeEnvelope.ts`).
   - Troque `INACTIVE_PATHS` por `inactivePaths(mode, doc): ReadonlySet<string>`:
     - Simples: `rtl.timeout_sec`, `rtl.reverse_cruise_velocity`, `kinematics.forward_cruise_velocity`.
     - Avancado: `yaw_pattern.scan_vyaw`.
     - Com `rtl.enabled === false`: mais `rtl.timeout_sec`, `rtl.reverse_cruise_velocity`, `rtl.target_aruco_id`,
       `rtl.marker_dict`, `rtl.tag_size`, `rtl.centering_timeout_sec`, `rtl.camera_tilt_deg`.
     - Com `platform.takeoff_from_ground !== true`: mais `kinematics.altitude_ceiling_margin_m`.
   - `isPathActive(mode, path, doc)` delega para `inactivePaths`. Atualize todos os chamadores.
   - `stageLabel(mode, step, fallback, rtlEnabled)`:
     - com RTL: Simples "Pouso na base", Avancado "Retorno a base";
     - sem RTL: "Pouso no local".
2. **`FE/lib/parameterModes.ts`, migracao na carga**, idempotente e testada:
   - `bmg.active-mode === 'intermediate'` vira `'advanced'`, e o documento de trabalho do Avancado recebe
     `rtl.enabled = false`;
   - apague `bmg.working-parameters.intermediate` e `bmg.operator-preset.intermediate`;
   - um documento vindo da ponte com `mission_mode: 'intermediate'` recebe a mesma regra da secao 3.1, funcao
     `migrateLegacyMode` em `launchDocument.ts`.
3. **Remova os ramos do Intermediario** em:
   - `FE/lib/parameterSchema.ts`
   - `FE/lib/copilotPhrases.ts`
   - `FE/lib/batteryReturn.ts`
   - `FE/hooks/useCopilot.ts`
   - `FE/components/preflight/ParameterSheet.tsx:350-352,439,469`
   - `MissionModeSelector.tsx` (a docstring "tres pilulas" vira duas)
4. **`FE/lib/modeIsolation.guard.test.ts`:** mantenha a lista fechada. Arquivos novos que leiam o modo entram nela
   explicitamente.

### 5.2 Booleanos no schema e checkboxes

1. **`FE/lib/parameterSchema.ts`:**
   ```ts
   export interface BooleanParameter extends Common { kind: 'boolean'; }
   export type ParameterSpec = NumberParameter | BooleanParameter;
   ```
   Campos novos:
   - `platform.takeoff_from_ground`
     - Rotulo: "Decolagem do Solo".
     - Ajuda: "Desmarcada: decola do topo do totem de 1,0 m. Marcada: decola do piso e libera a margem de teto."
     - Grupo e ordem: imediatamente depois de `kinematics.target_altitude_m`.
   - `rtl.enabled`
     - Rotulo: "Retorno a base (RTL)".
     - Ajuda: "Marcada: retorna, centraliza no ArUco e pousa na base. Desmarcada: pousa no local logo apos a foto."
     - No Simples, a ajuda acrescenta: "O rumo volta ao da decolagem antes do pouso."
     - Grupo: o primeiro do bloco de retorno, nos dois modos.
2. **`kinematics.target_altitude_m`:**
   - `min: 0.1`, `max: 2.0`, `step: 0.05`, `precision: 2`;
   - remova `rangeByMode`;
   - acrescente `rangeFor?: (doc) => {min, max}`, que delega para `altitudeEnvelope(doc)`;
   - a dica mostra "Cota sobre o piso".
3. **`kinematics.altitude_ceiling_margin_m`:** visivel nos dois modos, porem **desabilitado** quando
   `takeoff_from_ground !== true`, com a legenda `"Teto fixo do perfil de totem: <V> m sobre o piso"`. O valor vem
   de `ceilingFloorFrame(doc)` em `altitudeEnvelope.ts`, que espelha a formula do totem da secao 3.5.
4. **`FE/components/preflight/ParameterSheet.tsx`:**
   - `ParameterControl` despacha por `kind`. O checkbox e acessivel (`<input type="checkbox">` com `<label>`,
     foco visivel e tokens do tema existente).
   - `restoreDefaults` (`:370-378`) e a deteccao de alteracao tratam booleanos.
   - O cabecalho mostra dois chips de plano: `"Final: RTL + pouso de precisao"` ou `"Final: pouso no local"`, e
     `"Decolagem: totem 1,00 m"` ou `"Decolagem: solo"`.
5. **`FE/lib/launchDocument.ts`:**
   - `REQUIRED_LAUNCH_NUMBERS` filtra `kind === 'number'`. Hoje um booleano no schema seria tratado como ausente e
     **bloquearia o lancamento** (`:64-81`).
   - Crie `REQUIRED_LAUNCH_BOOLEANS`, com backfill: `rtl.enabled = true`, `platform.takeoff_from_ground = false`.
   - `normalizeForMode` **nao** prende mais a altitude por modo e nao fixa a margem. Ela so prende no
     `altitudeEnvelope(doc)`, com aviso visivel quando o valor muda.
   - `inEnvelope` usa `altitudeEnvelope`.
   - Alternar `takeoff_from_ground` reaplica o envelope, por exemplo 0,5 no solo vira 1,30 no totem, com aviso
     `"Cota ajustada para 1,30 m: minimo sobre o totem."`.

### 5.3 Envelope compartilhado (`FE/lib/altitudeEnvelope.ts`, novo e puro)

Espelho exato de `BE/launch_envelope.py`: as mesmas constantes, `altitudeEnvelope(doc)` e `ceilingFloorFrame(doc)`.
Os dois lados sao validados pelo fixture `T/fixtures/altitude_envelope_cases.json` (secao 7).

### 5.4 Conflitos (`FE/lib/parameterConflicts.ts`)

- Espelhe a secao 3.10: E03 e W01 aposentados, e E10, E11, W10 e W11 novos.
- `activeConflicts` (`:564-569`) usa `inactivePaths(mode, doc)`.
- Atualize `T/fixtures/parameter_conflict_cases.json`, consumido por `parameterConflicts.test.ts` e por
  `T/test_parameter_conflicts.py`.

### 5.5 Parametros e favoritos por modo (`parameterModes.ts`, `useMissionParameters.ts`, `ParameterSheet.tsx`)

1. `MODE_OWNED_PATHS` inclui os dois booleanos. A troca de modo restaura os ultimos valores daquele modo, como hoje.
   Adicione um teste que alterna Simples, Avancado e Simples e confere os booleanos e a altitude.
2. **Lista de favoritos por modo:**
   - Chave `bmg.operator-presets.v2.<mode>`, com um array de no maximo 12 itens:
     `{ id: string (crypto.randomUUID), name: string (1..40, trim, unico por modo sem diferenciar caixa), savedAt: string ISO, values: Record<path, number | boolean> }`.
   - `values` guarda somente `MODE_OWNED_PATHS`.
   - Migracao: o `bmg.operator-preset.<mode>` antigo vira o item `"Meu ajuste"` e a chave antiga e apagada.
     A migracao e idempotente.
   - Toda leitura e escrita fica em `try/catch`. Com JSON corrompido, a lista fica vazia, com `console.warn`, sem
     derrubar a UI.
3. **API do hook:** `presets`, `savePreset(name)`, `applyPreset(id)`, `renamePreset(id, name)`, `deletePreset(id)`,
   `activePresetId`.
   - `activePresetId` compara somente os caminhos **ativos** para `(mode, doc)`. Hoje `matchesPreset` compara tambem
     os ocultos (`:35-41`).
   - Aplicar um favorito passa pelo `normalizeForMode` (envelope) e grava o `mission_config.json`, como a troca de modo.
4. **UI:** substitua "Meu ajuste" e "Salvar como meu ajuste" (`ParameterSheet.tsx:415-438`) por um menu
   "Favoritos (N)":
   - cada item tem nome, data relativa, aplicar, renomear e excluir com confirmacao, e o ativo fica marcado;
   - "Salvar configuracao atual" abre um campo de nome inline;
   - navegacao por teclado e `aria-*` corretos.
   - Os favoritos de um modo nunca aparecem no outro.

### 5.6 Ctrl+C na cabine (`CockpitScreen.tsx`, `abortShortcut.ts`)

1. `isTextInputOrTerminal` so trata como texto `TEXTAREA`, `contenteditable`, `.xterm`/`[data-terminal]` e `INPUT`
   cujo `type` esteja em `{text, search, email, password, url, tel, number}`.
   - Range, checkbox, radio, button e `SELECT` **nao** bloqueiam o abort.
   - O seletor de `closest` deixa de incluir `input` e `select` genericos.
2. Publique `armed = abortEnabled(missionState, telemetry.flying_state, landProgress)` via
   `bridge.setCockpitAbortArmed`:
   - em cada mudanca;
   - `false` no unmount.
3. `bridge.onAbortShortcut(() => { if (abortEnabled(...)) onAbort(); })` chama **o mesmo** `onAbort` do
   `AbortControl` (`App.tsx:657`, `land()` e depois `mission.abort()` ou `bridge.abortMission()`), com o mesmo
   `singleFlight` no main.
4. O listener de `window` fica so quando `bridge` nao existe. Com ponte, o main faz `preventDefault` e o DOM nunca ve
   o evento, entao nao ha disparo duplo.

### 5.7 Voz do copiloto e HUD

1. **`FE/lib/copilotPhrases.ts`.** Os filtros por modo de RTL viram filtros por plano, `poolsForPlan({ mode, rtlEnabled })`:
   - **sem RTL:** proibidos `RETURN_POOLS` (`rtl_start*`), `BASE_LANDING_POOLS` e qualquer frase com "base",
     "retorno" ou "marcador". Permitidos `mission.landing_in_place` e `mission.touchdown_in_place`.
   - **com RTL:** tudo permitido. O pouso no local continua como fallback de marcador nao encontrado
     (`rtl_start_no_target`).
   - `isMilestoneAllowed` e `poolForMilestone` (`:394-438`) recebem o plano. O veto de "marcador" (`:444-447`) vale
     sempre que `rtlEnabled` for falso.
2. **Pools novos de plano**, falados no inicio da contagem a partir do documento de lancamento, tres variantes cada,
   sem emojis, por exemplo:
   - `plan.rtl`: "Plano de voo: inspecao com retorno a base e pouso de precisao."
   - `plan.land_in_place`: "Plano de voo: inspecao com pouso no local apos a foto."
   - Variante extra no Simples sem RTL: "...o rumo volta ao da decolagem antes do pouso."
   - Esses pools sao frases de UI, nao chaves de milestone. Registre-os em `allMissionPhrases` (`:739-743`) e no
     `speechCache`.
3. **`FE/hooks/useModeEcho.ts`:** compara `mode`, `rtl_enabled` e `takeoff_surface` do milestone `mission.mode` com o
   documento lancado. Uma divergencia segue o tratamento atual de divergencia de modo.
4. **`FE/lib/batteryReturn.ts` e `FE/components/shell/StatusBar.tsx:422-445`:** frases e textos por plano.
   - Sem RTL: "Bateria critica: pousando no local."
   - Com RTL: o texto atual.
   - `modeParity.test.ts` continua garantindo que abort, trava, finish e failsafe nao recebem modo. O plano vem do
     documento lancado.
5. **`FE/components/cockpit/StageBar.tsx:45-51,139,234`:** use `stageLabel(..., rtlEnabled)`. O HUD da cabine mostra
   o chip de plano da secao 5.2.

### 5.8 Recusa de lancamento visivel (complemento do BUG 1.1)

1. `useMissionRuntime` assina `bmg:launch-refused`.
2. Em `App.tsx` (`:415-417`), se a recusa chegar com `counting`, **cancele** a contagem sem ir para a cabine. A
   estacao fica no preflight com `launchError` vindo do `reason`, e o campo `field` fica destacado na folha.
3. O copiloto fala "Lancamento recusado: <reason>".
4. A UI nunca mais deve fechar a contagem numa cabine parada.

---

## 6. Emulador (`T/support/fake_bebop/`)

1. **`plant.py`:** novas opcoes.
   - `--firmware-step-model {m1,m2,m3}`, com default `m1` para manter a paridade com os testes atuais:
     - `m1`: o comportamento atual, com retencao por sonar em `gaz == 0` e ganho `--sonar-hold-gain`. Acrescente
       `--sonar-hold-any-gaz` para o caso pior, em que o firmware acompanha o sonar com qualquer gaz.
     - `m2`: a posicao real nao reage. O `speedZ` publicado recebe um impulso de area `+-h_p`, positivo ao sair e
       negativo ao entrar, distribuido em 1 ou 2 amostras.
     - `m3`: nada.
   - `--altitude-source {baro,sonar_fused}` para o `AltitudeChanged`. `sonar_fused` publica `z - terreno` relativo
     ao ponto de decolagem.
   - `--takeoff-overshoot-m` (default 0.0) com retorno amortecido ao `takeoff_altitude_m`, para reproduzir o sintoma A.
2. **`node.py:435-451`:** no modo `m2`, a `odom.z` passa a ser a integral do `speedZ` publicado, como o driver real
   faz, e nao a posicao do plant.

---

## 7. Testes obrigatorios (nomes sugeridos; o comportamento e exigido)

### Python (`T/`)

**Envelope, modos e migracao**
- `test_launch_envelope.py`: envelope por superficie (D1); tipos estritos dos booleanos; `LaunchEnvelopeError.field`.
- `test_launch_envelope_parity.py`: le `T/fixtures/altitude_envelope_cases.json` (o mesmo arquivo do vitest) e aplica
  `validate_launch_envelope` mais `relative_target_altitude_m`.
  - Grade: modo x superficie x RTL x altitude em {0.05, 0.1, 0.35, 1.0, 1.25, 1.3, 1.8, 2.0, 2.05}.
- `test_launch_refusal.py`: partida fria e go do standby com documento invalido produzem o alerta
  `mission.launch_refused` e exit 78, e o standby faz cleanup. Use `--no-fly` e o harness existente.
- `test_mission_modes.py`, `test_mode_parity.py`, `test_simple_mode_steps.py`: so dois modos, com a migracao
  `intermediate` para `advanced` e `rtl.enabled=false` nos tres caminhos (arquivo, `update_from_dict` e go).
  - Remova `test_mission_mode_native_takeoff.py` ou reescreva-o para o novo contrato. Ele trava o contrato que causou
    o bug.

**Teto e decolagem**
- `test_ceiling_schedule.py`: as tres propriedades da secao 3.5; solo e totem; limite do teto do local; permissao da
  guarda.
- `test_takeoff_acquisition.py`:
  - descida de 1,0 para 0,8 acima do totem sem breach, com overshoot de ate 0,25 m;
  - subida no solo de 1,0 para 1,8;
  - Simples tambem adquire a altitude;
  - so o teto do local e verificado durante `takingoff`.

**Plano de voo e RTL**
- `test_rtl_flag.py`, nos quatro casos modo x RTL:
  - nome do estagio 5;
  - ramo de `execute`;
  - anuncios sem "base" ou "retorno" quando sem RTL;
  - Simples sem RTL: `_restore_heading`, depois gimbal a -80 e depois `_land_in_place` com limiar sem desconto.

**Estimador vertical**
- `test_terrain_vertical_filter.py`:
  - pico M2 rejeitado dentro da janela, sem readquirir;
  - movimento real M1 aceito, porque a aceleracao e plausivel;
  - troca de hipotese `T` sem salto de estado;
  - `coasting` e `hold_integrator`;
  - ramo `legacy` intacto.

**Contratos**
- `test_contracts.py`:
  - chaves congeladas novas;
  - payload de `mission.mode`;
  - chave de alerta nova;
  - nomes do estagio 5.

**Emulados** (serializados, regra 0.3)
- `test_fly_path_totem.py` parametrizado por `m1`, `m2`, `m3` e `--takeoff-overshoot-m 0.25`, com margem oculta
  (totem) e alvo de 1,8 sobre o piso.
- `test_mission_modes_emulated.py`: Simples e Avancado, com e sem RTL, no totem e no solo.

### Frontend (vitest)

- `altitudeEnvelope.test.ts`: le o mesmo fixture da paridade Python.
- `launchDocument.test.ts`, `missionMode.test.ts`: reescreva os casos que travavam `pad + [0.7, 1.0]`. Inclua os
  booleanos: backfill, bloqueio que nao deve ocorrer e reenvelope ao alternar a superficie.
- `parameterModes.test.ts`, `useMissionParameters.test.tsx`, `ParameterPreset.test.tsx`:
  - migracoes do Intermediario e do preset unico;
  - lista com CRUD;
  - unicidade de nome;
  - limite de 12;
  - JSON corrompido;
  - isolamento por modo.
- `ParameterSheet.test.tsx`: checkboxes; margem desabilitada no totem com legenda; chips de plano.
- `CockpitScreen.test.tsx`:
  - Ctrl+C com foco em `input[type=range]` aborta;
  - com ponte, o `onAbortShortcut` aborta uma unica vez;
  - `armed` e publicado e zerado no unmount.
- `copilotPhrases.test.ts`, `useFlightNarration.parameters.test.tsx`, `batteryReturn.test.ts`, `StageBar.mode.test.tsx`:
  pools e rotulos pelo plano.
- `App.launchParams.test.tsx`: uma recusa durante a contagem mantem o preflight, mostra o motivo e nao abre a cabine.
- `missionModeMain.test.ts`, `mainHarness.test.ts`:
  - `normalizeLaunchMode` com dois modos;
  - exit 78 gera `bmg:launch-refused`;
  - o standby nao entra em laco;
  - `before-input-event`.

---

## 8. Verificacao (executar e relatar a saida)

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws && colcon build --symlink-install --packages-select mvp_mission_bebop
cd /home/jv/ros2_ws/src/mvp_mission_bebop && python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py
python3 -m pytest test/ -q
cd bebop_mission_control && npm run typecheck && npx vitest run && npm run build
```

- Nao ha script `lint` no `package.json`. Use `npm run typecheck` e nao crie um script de lint.
- Rode as suites emuladas sozinhas, uma de cada vez, ao final.
- **Bancada (`--no-fly`).** Rode o Simples com pad 1 e alvo 1,8 e confirme `[SURFACE] takeoff=platform pad=1.00 target_floor=1.80 target_rel=0.80`.
  Rode o Simples com alvo 0,5 no totem e confirme o alerta de recusa e o exit 78.

---

## 9. Procedimento de campo (nova secao em `docs/CHECKLIST_VOO_REAL.md`)

Esta secao e entregue como texto no checklist. Nao e executada pelo Claude Code.

- **F-ID-1. Identificacao sem voo, aeronave na mao e motores desligados.**
  - Leve o Bebop ligado do topo do totem ate fora dele, a altura constante, e volte.
  - Registre `[ALT_PROBE]` e `/bebop/odom`.
  - Se o `AltitudeChanged` saltar cerca de 1,0 m: `firmware_altitude_source = "sonar_fused"`.
  - Se `speedZ` mostrar pico: o firmware esta em M2.
  - [A CONFIRMAR] se o firmware publica altitude com os motores desligados. Se nao publicar, passe para o F-ID-2.
- **F-ID-2. Hover baixo e travessia lenta.**
  - Travessia a 0,05 m/s com o operador pronto no Ctrl+C.
  - Rode uma vez com o estimador `legacy` e outra com `terrain_kf`, comparando os logs persistidos (secao 4.4).
  - O desfecho classifica M1, M2 ou M3.
- **F-0 (revisto).** Mecam a altura real e o overshoot do fim da decolagem nativa sobre o totem e sobre o solo.
  Atualize `NATIVE_HOVER_HEIGHT_M` somente se divergir mais de 0,10 m e mantenha a paridade por teste.
- **Regra de decisao:**
  - M2 confirmado: `terrain_kf` fica default.
  - M1 confirmado: `terrain_kf` mais calibracao dos feedforwards da L3.
  - `AltitudeChanged` imune ao sonar: `firmware_altitude_source = "baro"` com ancora.

---

## 10. Criterios de aceite

1. **BUG 1.1.**
   - Simples no totem a 1,8 m sobre o piso decola, gira, captura e pousa no emulador.
   - Nenhum documento produzido pela estacao e recusado pelo backend; a paridade por fixture prova isso.
   - Uma recusa, quando ocorre, aparece na UI com o motivo e nao gera laco de standby.
2. **Sintoma A.** Totem com alvo 1,8 e overshoot emulado de 0,25 m: zero `ceiling breached` e zero pouso de
   emergencia, nas seeds 0 a 4.
3. **Sintoma B.** Totem com alvo 1,8 sobre o piso, saida e reentrada, seeds 0 a 4, desvio maximo `|z_piso - 1.8|`:
   - **M3** <= 0,05 m;
   - **M2** <= 0,08 m;
   - **M1** <= 0,12 m.
   - Em nenhum caso a aeronave passa do teto do local nem dispara pouso de emergencia.
   - Com o mesmo emulador, o `legacy` em M2 deve **falhar** esse criterio. Isso prova que o teste discrimina.
4. **Modos.** Apenas Simples e Avancado existem, e o Intermediario legado migra sem perda.
5. **RTL.** Os quatro casos modo x RTL seguem a secao 3.7. As falas e o HUD nunca citam base nem retorno sem RTL.
6. **Parametros.** Os valores e os favoritos sao independentes por modo e sao restaurados na troca. Os booleanos
   persistem.
7. **Ctrl+C.** Aborta com foco em qualquer controle da cabine, inclusive range e iframe, exatamente uma vez, pelo
   mesmo caminho do botao.
8. **Altitude.** O slider vai de 0,10 a 2,00 m em passos de 0,05. No totem, o minimo de 1,30 m e aplicado com aviso.
   A margem fica desabilitada no totem e habilitada no solo.
9. **Verificacao.** A secao 8 inteira fica verde, e a linha de base da regra 0.1 nao regride.

---

## 11. Commits propostos

Os commits sao atomicos, e cada um tem os seus testes verdes. **Nao commitar sem autorizacao.**

1. `fix(launch): share the altitude envelope between station and mission`. Cobre 3.4 e 5.3, mais o fixture de paridade.
2. `feat(launch): refuse invalid documents with a structured alert and exit 78`. Cobre 3.4.3, 4.2, 4.3 e 5.8.
3. `refactor(modes): remove the intermediate profile and migrate stored documents`. Cobre 3.1.4, 3.2 e 5.1.
4. `feat(rtl): make the return to launch a per-flight parameter`. Cobre 3.1.1, 3.7 e 3.9.
5. `feat(takeoff): select the launch surface and acquire the target altitude in both directions`. Cobre 3.1.2, 3.3
   e 3.6.
6. `feat(estimation): ratchet the altitude ceiling through the takeoff transient`. Cobre 3.5 e 3.10.
7. `feat(bmg-ui): boolean parameters, launch surface and RTL checkboxes`. Cobre 5.2, 5.4 e 5.7.
8. `feat(bmg-ui): per-mode favorites list`. Cobre 5.5.
9. `fix(bmg-ui): route the cockpit abort shortcut through the main process`. Cobre 4.5 e 5.6.
10. `feat(emulator): firmware terrain-step models, altitude source and takeoff overshoot`. Cobre 6.
11. `feat(estimation): terrain-aware vertical Kalman filter behind calibration.altitude_estimator`. Cobre 3.8.
12. `chore(bmg-core): persist mission logs for field identification`. Cobre 4.4.
13. `docs: field identification of the totem step`. Cobre 9.

---

## 12. Fora de escopo (nao implementar)

- Alteracoes no driver (`ros2_bebop_driver`), no nectar-sdk ou no ARSDK. Isso inclui o `PilotingSettings.MaxAltitude`
  nativo, que o driver nao expoe e cuja semantica depende do mesmo estimador do firmware.
- Cota abaixo do topo do totem no Avancado (alternativa rejeitada em D1).
- Sincronizacao de favoritos entre maquinas.
- Edicao de `platform.height_m` e de `venue_ceiling_agl_m` na UI.
- Qualquer mudanca no trabalho forense de outra sessao.
