# Prompt de Implementacao: parametros de voo, velocidade acoplada, countdown sem espera, mapa fixo, configuracoes avancadas e limpeza do diagnostico

Destinatario: Claude Code, na raiz `/home/jv/ros2_ws/src/mvp_mission_bebop`.
Origem: auditoria de leitura feita em 2026-10-07 sobre o working tree atual. Todas as referencias de arquivo e linha abaixo foram lidas no codigo, nao supostas.

---

## 0. Regras de operacao desta sessao (ler antes de qualquer edicao)

1. **O working tree esta sujo: 93 arquivos modificados ou novos e nao commitados** (`git status --short`). Inclui `parameterSchema.ts`, `launchDocument.ts`, `App.tsx`, `main.cjs`, `preload.cjs`, `steps/takeoff.py`, `mission.py`, `parameters.py` e testes. Trabalho em andamento do operador. **Nao reverta, nao faca `git checkout`/`stash`/`reset`, nao reformate arquivos inteiros.** Construa por cima do estado atual.
   - Exemplo: `parameterSchema.ts` ja tem `altitude_ceiling_margin_m` (0.3 a 1.5) e `nadir_tilt_deg` em -80 a -20 como mudancas nao commitadas. Parta delas.
2. **Nao faca commit nem push.** A memoria do operador proibe commit/push em MVP-Bebop e nectar-sdk sem autorizacao explicita, mesmo com o check-out do `CLAUDE.md`. Ao final, deixe as mudancas no working tree, rode `git status` e proponha a divisao em commits atomicos (Conventional Commits, ingles tecnico) e espere o aval.
3. Siga `CLAUDE.md`: sem emojis em codigo, comentarios, logs, docs ou commits; comentarios apenas para fisica de controle, restricao do Bebop 2 ou sincronismo IPC; tipagem estrita; docstrings NumPy; contrato `[STEP N: ...]` em stdout e schema de `mission_config.json` preservados.
4. **Antes de implementar, inspecione `/home/jv/ros2_ws/src/nectar-sdk`** (regra de ouro do `CLAUDE.md`). Ja verificado: `nectar/nectar/control/bebop/drone.py` (`takeoff` publica `Empty` e depois `self.delay(3.0)`; `flat_trim` so publica) e `nectar/nectar/vision/algorithms/markers/aruco.py` (`resolve_aruco_dict`). Use-os como fonte da verdade e nao reimplemente.
5. **Baseline primeiro:** rode `python3 -m pytest test/` e, em `bebop_mission_control/`, `npm run typecheck` e `npm test`. Registre o resultado. O `docs/IMPLEMENTACAO_PROGRESSO.md` cita pytest 1226 e vitest 480 no ultimo fechamento; qualquer falha pre-existente deve ser relatada, nao escondida.
6. Ambiente: `source /home/jv/ros2_ws/bin/nectar-activate`. Build ROS: `colcon build --symlink-install --packages-select mvp_mission_bebop`.
7. TDD: para cada item, escreva o teste que falha, depois o codigo.

---

## 1. Achados da auditoria que mudam o plano (ler com atencao)

Estes pontos contradizem ou complicam o enunciado original. Cada um tem uma decisao ja tomada neste documento e marcada com **DECISAO**; reporte ao operador no fechamento.

### 1.1 Velocidade ate 0.60 e inutil ao drone com o teto atual (`max_horizontal_speed = 0.30`)
- `telemetry/failsafe.py:309` satura todo `vx/vy` em `abs(kinematics.max_horizontal_speed)`. Default em `parameters.py:562`: **0.30**.
- Se o slider chegar a 0.60 e o teto ficar em 0.30, a aeronave voa a 0.30 e a ficha mente. Alem disso `config_audit.py:148-159` registra divergencia (`ceiling < required`) para `kinematics.max_horizontal_speed`.
- A envelope documentada exige: teto >= toda demanda legitima e < 1.0.
- **DECISAO:** o teto sobe junto, **so para cima** ("lift-only"): ao reconciliar o documento na estacao, `max_horizontal_speed = max(valor_atual, ceil_0.05(forward + 0.05))`, limitado a 0.95. Nunca reduz um valor que o operador ja tenha. Persiste no `mission_config.json`. Nao altere o default Python (0.30) nem a protecao para quem voa em 0.20. Documente em comentario tecnico que o teto e a guarda contra defeito de lei de guiagem, nao um limite de missao.
- Caveat a registrar no relatorio, nao a corrigir: `normalized_to_mps` esta em 1.0 **sem calibracao** (`parameters.py`, comentario de `FlightKinematicsConfig`). A UI rotula "m/s", mas o numero enviado ao driver e velocidade normalizada. 0.60 normalizado no Bebop 2 e uma fracao grande da inclinacao maxima. Isto e risco real de voo; avise o operador para validar em bancada e depois em voo curto.

### 1.2 Acoplar forward e reverse muda o comportamento padrao do RTL
- `rtl.reverse_cruise_velocity` default **-0.10** (`parameters.py:769`), com a justificativa documentada de ser mais lento que a varredura (probabilidade de deteccao por metro). Com o acoplamento, forward default 0.20 passa reverse a -0.20: **dobra a velocidade da busca do marcador**.
- **DECISAO:** implemente o acoplamento exatamente como pedido. Nao mude o default Python (evita mexer em `test_aruco_rtl.py` e `test_parameters.py`). Registre no relatorio que o RTL padrao ficou mais rapido por decisao do operador.

### 1.3 Altitude 0.1 m e margem 0.0 m colidem com constantes fisicas do backend
- `config_audit.py:127-143`: `target_altitude_m <= max(min_altitude_for_ibvs_m=0.35, takeoff_settle_min_altitude_m=0.30)` gera divergencia ("a abordagem usa a rampa aberta e o gate de decolagem nao assenta"). Valores de 0.1 a 0.35 caem nisso.
- `steps/takeoff.py::_stabilize` descarta amostras abaixo de `takeoff_settle_min_altitude_m` (0.30): com alvo < 0.30 o gate de assentamento nunca satisfaz e a fase gasta **todo** `takeoff_stabilize_duration_sec` (ate 15 s pela ficha). Isto e uma espera cega pos-contagem, ligada ao item 3.
- `telemetry/odometry.py:383-386`: `altitude_ceiling = target + margin`; `odometry.py:333-338` conta amostras acima do teto e `is_ceiling_breached` aciona pouso de emergencia (`takeoff.py::_ascend`, `rtl.py:1681`, `failsafe.py:357`). Com margem 0.0 qualquer overshoot de subida ou ruido de sonar/baro (0.05 a 0.08 m, ver `parameters.py`) dispara emergencia.
- O audit **nunca aborta**: so loga `[CONFIG ENVELOPE]` (docstring de `config_audit.py`). Nao existe "divergencia critica". "Aceitar os novos limites" significa que a ficha nao pode bloquear a faixa pedida e o audit deve dizer a verdade.
- **DECISAO:** as faixas da UI seguem exatamente o pedido (0.1 a 4.0 e 0.0 a 2.0). Em compensacao: (a) aviso inline na ficha, sem bloquear, quando altitude <= 0.35 ou margem < 0.21 m; (b) novo check no audit para a margem; (c) a espera de `_stabilize` deixa de ser cega para alvo baixo (ver 4.3.4). Calculo do piso da margem a partir de constantes existentes, sem numero novo: `climb_deadband_m (0.05) + 2 * climb_settle_max_position_sigma_m (0.08) = 0.21 m`.

### 1.4 Faixas de gimbal permitem configuracao impossivel
- Novas faixas: `search_tilt_deg` em [-45, 0] e `nadir_tilt_deg` em [-80, 0]. O audit (`config_audit.py:113-122`) exige `nadir < search` ("acima da busca a aproximacao nunca termina"). Com os dois sliders independentes o operador consegue, por exemplo, search -30 e nadir -10.
- **DECISAO:** regra entre campos em `launchBlockers`: `nadir_tilt_deg < search_tilt_deg` (estrito). Bloqueia o lancamento com mensagem em portugues; a ficha mostra o erro no campo `nadir`. O envelope Python `[-90, search)` continua valendo e cobre -80.

### 1.5 Calibracao do magnetometro e tambem um interlock de lancamento (nao so um painel)
- `PreflightScreen.tsx:127` e `lib/preflightGate.ts:54-56` bloqueiam o voo real com "Calibracao do magnetometro necessaria" quando `telemetry.magneto_calibration.required === 1`.
- `electron/main.cjs:2597` e `:2788` e `electron/launchReadiness.cjs:156-159` leem `latestTelemetry.calibration_required`, campo que **nenhum emissor produz** (a ponte envia `magneto_calibration.required`). Esse caminho e codigo morto hoje.
- O enunciado lista painel, testes, `DiagnosticsScreen`, props de `App.tsx` e handlers IPC. Nao lista o interlock. Remover o painel mantendo o interlock deixa o operador sem como destravar dentro do app.
- **DECISAO (conservadora, reversivel):** remover tudo o que **comanda ou exibe** calibracao (painel, IPC, op `magneto` do `command_bridge.py`, tipos de UI, props, testes). **Manter** o interlock de pre-voo `preflightGate`, trocando o texto para orientar a acao externa: `Calibracao do magnetometro exigida pela aeronave; calibre pelo aplicativo da Parrot`. Manter o encaminhamento `telemetry.magneto_calibration` ate o gate, porque e o unico leitor. Remover o caminho morto `calibration_required` de `main.cjs`/`launchReadiness` **somente se** o operador confirmar a opcao B abaixo.
  - Opcao A (esta): interlock mantido. Seguro contra decolagem com bussola invalida.
  - Opcao B (apenas com aval explicito): remover tambem o interlock e `magneto_calibration` do telemetry bridge; o painel de pre-voo deixa de bloquear.
- Em ambas, o painel de Diagnostico nao pode conter nenhuma mencao a magnetometro.

### 1.6 O mapa recentra por quatro fontes diferentes, nao por uma
Causas de "pulo" confirmadas em `TacticalMap.tsx`:
1. `gpsFix` verdadeiro faz `geo` virar `telemetry.latitude/longitude` (GPS da aeronave).
2. `useOperatorLocation(!gpsFix)` entrega posicao do dispositivo, host por IP ou cache; `watchPosition` pode sobrescrever antes de o host confirmar o `site`.
3. `telemetry.base_*` vem de `gps_home` quando existe (`streamer/telemetry_bridge.py:605-612`), que tambem muda a base.
4. A vista e **centrada na aeronave** (o terreno rola sob o marcador) e o enquadramento **alarga** com o voo (`fitExtent`, piso 300 m). Isso e o oposto de "ancora fixa".

### 1.7 Itens do countdown ja estao parcialmente resolvidos; restam atrasos reais
- A arquitetura de standby ja ancora a decolagem em `launch_at_ms + countdown_sec` e mediu 10.08 s para contagem de 10 s (`docs/IMPLEMENTACAO_PROGRESSO.md`, secao "Fix contagem pulada"). Nao refaca isso e **preserve os invariantes** de `docs/PROMPT_FIX_LANCAMENTO_CONTAGEM.md` (contagem nunca encurtada; teste `test_a_deadline_nobody_saw_counted_runs_the_whole_countdown`).
- Atrasos residuais encontrados em `steps/takeoff.py`:
  a. `_FLAT_TRIM_SETTLE_SEC = 2.0` bloqueante via `ctx.drone.delay` apos o ack do flat trim (em `_flat_trim`).
  b. A sessao de warmup de percepcao (`ctx.perception.session`) so abre dentro de `_countdown`, **depois** de flat trim e calibracao. O aquecimento do YOLO nao ocorre em paralelo com eles.
  c. No caminho frio (sem `launch_deadline`), o prazo e `countdown_sec` contado **apos** a sequencia de solo inteira: espera = solo + contagem, serial.
  d. Ao fim do laco, o `with ... session()` chama `disengage()` (`perception/worker.py:598`), que espera ate `DEFAULT_DISENGAGE_TIMEOUT_SEC = 2.0 s` por um ciclo de inferencia em voo **antes** de o takeoff ser comandado. Um detector pendurado adiciona 2 s entre o zero e o `t_takeoff_cmd`.
  e. Nao e atraso a remover: o `delay(3.0)` interno do `BebopDrone.takeoff` ocorre **depois** da publicacao do comando (`t_takeoff_cmd` e carimbado antes). Nao toque.
- Se a sequencia de solo for mais longa que a contagem (ex.: contagem 3 s, flat trim + calibracao 5 s), a decolagem **espera** o termino: nunca pule flat trim nem calibracao de solo (sao interlocks). Logue o excesso.

---

## 2. Item 1 e 2: faixas e velocidade acoplada (frontend)

### 2.1 `src/lib/parameterSchema.ts`
Valores finais (min, max, step, precision):

| path | min | max | step | precision |
|---|---|---|---|---|
| `kinematics.target_altitude_m` | 0.1 | 4.0 | 0.1 | 1 |
| `kinematics.altitude_ceiling_margin_m` | 0.0 | 2.0 | 0.05 | 2 |
| `kinematics.forward_cruise_velocity` | 0.01 | 0.60 | 0.01 | 2 |
| `gimbal.search_tilt_deg` | -45 | 0 | 1 | 0 |
| `gimbal.nadir_tilt_deg` | -80 | 0 | 1 | 0 |

Observacoes:
- Hoje o arquivo tem altitude 0.5 a 4, margem 0.3 a 1.5, velocidade 0.01 a 0.6 (ja alterada), nadir -80 a -20. Ajuste os que diferem.
- Atualize o comentario de cabecalho do arquivo (menciona "dois" limites e faixas antigas) e os `hint` quando necessario. A velocidade passa a descrever "avanco na varredura e retorno do RTL".
- `kinematics.takeoff_stabilize_duration_sec`, `timeouts.*` e `countdown_sec` nao mudam.

### 2.2 Acoplamento forward/reverse (estrutura)
Adicione ao `NumberParameter` um campo opcional:
```ts
/** Paths written together with `path`, each as `sign * value`. */
mirrors?: ReadonlyArray<{ path: string; sign: 1 | -1 }>;
```
No spec de `kinematics.forward_cruise_velocity`: `mirrors: [{ path: 'rtl.reverse_cruise_velocity', sign: -1 }]`.

Crie em `src/lib/launchDocument.ts` (ou `paths.ts`) duas funcoes puras e exportadas:
- `setLinkedPath(doc, spec, value)`: aplica `value` em `spec.path` e `sign * value` em cada mirror, **sem produzir `-0`** (`v === 0 ? 0 : sign * v`; o minimo 0.01 ja evita zero, mas proteja). Arredonde a `precision` do spec antes de espelhar para os dois lados serem exatamente simetricos.
- `reconcileCoupledPaths(doc)`: se `forward` e finito, forca `reverse = -forward` e aplica o lift-only de `kinematics.max_horizontal_speed` (secao 1.1). Idempotente. Se `forward` ausente ou nao finito, nao toca (o `launchBlockers` ja reprova).

Pontos de aplicacao (todos devem passar pelo mesmo helper):
1. `useMissionParameters.edit`: usar `setLinkedPath` quando o path for de um spec com `mirrors`. Hoje `edit` faz `setPath` cru (`useMissionParameters.ts`).
2. `applySchemaFields` (preset e padroes de fabrica): para cada spec, usar `setLinkedPath`, de modo que `applyPreset`/`applyFactory` acoplem. Consequencia: restaurar padroes de fabrica resulta em reverse = -0.20, nao -0.10 (secao 1.2).
3. `ParameterSheet.restoreDefaults` ja chama `onEdit` por spec: herda o acoplamento via (1).
4. **Carga do documento:** apos `getParameters`, aplicar `reconcileCoupledPaths` em `working` (nao em `committed`). Isso mostra a diferenca como alteracao pendente. O arquivo em disco hoje tem forward 0.20 e reverse -0.10 (desacoplados); sem esta reconciliacao o operador lancaria valores desacoplados sem perceber.
5. `App.tsx::commitLaunchDocument`: antes de `launchBlockers`, garantir reconciliacao e salvar se mudou (o fluxo ja faz `params.dirty && save()`; reconcile antes).

Deteccao de alteracao (`changedPaths`, `dirty`): um spec conta como alterado se `path` **ou** qualquer mirror diferir de `committed`. Adicione somente `spec.path` ao `Set` (a contagem exibida continua "1 alteracao" por slider). Os mirrors **nao** entram em `TRACKED_PATHS` como itens separados. Para `matchesPreset`, a comparacao por `ALL_PARAMETERS` continua suficiente porque o preset e reaplicado via `setLinkedPath`.

### 2.3 Validacao de lancamento (`launchDocument.ts`)
- `REQUIRED_LAUNCH_NUMBERS`: incluir `rtl.reverse_cruise_velocity`.
- `inEnvelope`: para `rtl.reverse_cruise_velocity`, intervalo `[-0.60, -0.01]` (derivado da faixa do forward). Hoje `EXTRA_BOUNDS` so modela `(low, high]` positivo; adicione suporte a limites negativos ou trate esse path num caso proprio.
- Nova regra de coerencia: `|forward + reverse| <= 1e-9` ou o path `rtl.reverse_cruise_velocity` entra na lista de bloqueios.
- Nova regra entre campos (1.4): `nadir_tilt_deg < search_tilt_deg`. Retorne o path `gimbal.nadir_tilt_deg` e uma mensagem. Estenda `launchBlockers` sem quebrar a assinatura atual (`string[]`); se precisar de mensagem, crie `launchBlockerMessages(doc)` e mantenha `launchBlockers` como esta para os testes existentes.
- `max_horizontal_speed`: exigir finito, `>= forward` e `< 1.0` (espelha o audit).
- Atualize `launchDocument.test.ts`: o teste "names a sheet value outside its slider range" usa nadir 5 e forward 2, que continuam fora; adicione casos de limite inclusivo (0.1, 4.0, 0.0, 2.0, 0.01, 0.60, -45, 0, -80) e fora (0.09, 4.01, -0.01, 2.01, 0.0, 0.61, -46, 1, -81).

### 2.4 Avisos inline na ficha (`ParameterSheet.tsx`)
Sem bloquear, texto em `amber`, abaixo do slider, em portugues, sem emoji:
- Altitude <= 0.35 m: "Abaixo do piso da guiagem visual (0.35 m): a aproximacao usa rampa aberta e a estabilizacao pos-decolagem nao assenta."
- Margem < 0.21 m: "Margem abaixo do ruido de sonar e barometro: risco de pouso de emergencia por teto."
- Velocidade > 0.30: "Acima de 0.30 o teto de velocidade horizontal sera elevado junto."
- Nadir >= search: erro vermelho/`rust`, texto "A camera precisa terminar mais baixa que o angulo de varredura."
Obtenha os limiares de constantes exportadas num unico lugar (`parameterSchema.ts`: `LOW_ALTITUDE_ADVISORY_M = 0.35`, `LOW_CEILING_MARGIN_ADVISORY_M = 0.21`), com comentario citando o parametro Python de origem.

### 2.5 Backend (defesa em profundidade e honestidade do audit)
Arquivo `mvp_mission_bebop/config_audit.py` e `parameters.py`:
1. Nada no audit precisa **relaxar** para altitude 0.1: ele ja reporta a verdade como aviso. Mantenha a regra. Atualize `test/test_config_audit.py` somente se a assinatura mudar.
2. Adicionar check `kinematics.altitude_ceiling_margin_m`: finito e `>= climb_deadband_m + 2 * climb_settle_max_position_sigma_m`. Primeiro adicione a nota "Safe envelope" correspondente no comentario do campo em `parameters.py` (a docstring do audit exige que todo check restate uma nota de envelope). Consequencia: o default 0.60 passa; teste `test_the_shipped_defaults_are_inside_every_envelope` deve continuar verde.
3. Nenhum check novo para `reverse_cruise_velocity`: o existente (`< 0`) cobre.
4. Confirme que nadir -80 e search ate 0 nao geram divergencia fora da regra `nadir >= search` (ja coberta).
5. Teste novo: `max_horizontal_speed` abaixo de `forward` ainda e reportado; `max_horizontal_speed = 0.65` com forward 0.60 e reverse -0.60 nao e.

---

## 3. Item 5: Configuracoes Avancadas (frontend + persistencia + Stage 5)

### 3.1 Estado e schema
Novo grupo no schema, **fora** de `PARAMETER_GROUPS` (os tres cards nao mudam): exporte de `parameterSchema.ts`:
```ts
export const ADVANCED_PATHS = ['rtl.target_aruco_id', 'rtl.marker_dict', 'rtl.tag_size'] as const;
export const MARKER_DICTIONARIES: ReadonlyArray<{ name: string; markers: number }> = [
  { name: 'DICT_4X4_50', markers: 50 },   { name: 'DICT_4X4_100', markers: 100 },
  { name: 'DICT_4X4_250', markers: 250 }, { name: 'DICT_4X4_1000', markers: 1000 },
  { name: 'DICT_5X5_50', markers: 50 },   { name: 'DICT_5X5_100', markers: 100 },
  { name: 'DICT_5X5_250', markers: 250 }, { name: 'DICT_5X5_1000', markers: 1000 },
  { name: 'DICT_6X6_50', markers: 50 },   { name: 'DICT_6X6_100', markers: 100 },
  { name: 'DICT_6X6_250', markers: 250 }, { name: 'DICT_6X6_1000', markers: 1000 },
  { name: 'DICT_7X7_50', markers: 50 },   { name: 'DICT_7X7_1000', markers: 1000 },
  { name: 'DICT_ARUCO_ORIGINAL', markers: 1024 },
  { name: 'DICT_APRILTAG_16h5', markers: 30 },  { name: 'DICT_APRILTAG_25h9', markers: 35 },
  { name: 'DICT_APRILTAG_36h10', markers: 2320 }, { name: 'DICT_APRILTAG_36h11', markers: 587 },
];
```
Os tamanhos foram medidos com `cv2 4.10.0` (`getPredefinedDictionary(...).bytesList.shape[0]`) no ambiente da estacao; todos os nomes sao atributos de `cv2.aruco`, portanto resolvem por `resolve_aruco_dict` (caminho "exact attribute name"). Inclua um teste Python que itere sobre `MARKER_DICTIONARIES` lendo o JSON exportado, ou mantenha a lista num fixture JSON compartilhado por `test/` e pelo vitest, para detectar divergencia de nome ou tamanho.

Em `useMissionParameters`:
- `edit` ja aceita qualquer path; os tres paths editam por la. Inclua `ADVANCED_PATHS` em `TRACKED_PATHS` (dirty, `changedPaths`, descartar).
- `applySchemaFields`: **nao** aplicar `ADVANCED_PATHS` em "Meu ajuste" nem "Restaurar Padroes" da ficha principal (sao identidade fisica do pad, nao ajuste de missao). Dentro do modal, botao "Restaurar padroes avancados" usando `factory`.
- `matchesPreset` continua so sobre `ALL_PARAMETERS`.

### 3.2 Validacao (`launchDocument.ts`)
- `rtl.target_aruco_id`: inteiro (`Number.isInteger`), `>= 0` e `< markers` do dicionario escolhido.
- `rtl.marker_dict`: string presente em `MARKER_DICTIONARIES` (`name`). Se o documento em disco trouxer inteiro legado (4 a 7) ou enum numerico, a ficha mostra o valor e marca "nao reconhecido pela ficha"; o lancamento so bloqueia se o dicionario for realmente irresoluvel, o que a ficha nao consegue provar. Regra: aceitar string da lista ou numero inteiro nao negativo; bloquear qualquer outro tipo.
- `rtl.tag_size`: finito, `> 0`, `<= 1.0` m. Controle: campo numerico, step 0.005, precisao 3, chips de atalho 0.16 e 0.20. Mensagem de ajuda citando que o valor e a aresta externa da borda preta e que erro de tamanho escala toda distancia estimada (texto derivado do comentario de `parameters.py`).
- Inclua os tres paths em `REQUIRED_LAUNCH_NUMBERS` apenas para `target_aruco_id` e `tag_size` (numericos). `marker_dict` entra numa checagem propria de tipo.

### 3.3 UI
- `ParameterSheet.tsx`: no rodape (linha do `Button` "Salvar como meu ajuste"), adicionar logo ao lado o botao `Configuracoes Avancadas` (variant `ghost`, icone `Settings2` de `lucide-react`).
- Crie `src/components/preflight/AdvancedSettingsDialog.tsx`. Siga o padrao de `DiscardChangesDialog.tsx` (modal existente) para foco, Escape e backdrop. Conteudo: tres campos (ID numerico, `select` de dicionario, tamanho), indicacao do tamanho do dicionario ("IDs validos: 0 a 586"), erro inline por campo, botoes Fechar e Restaurar. As edicoes vao direto ao estado de trabalho via `onEdit`; "Salvar" continua sendo o do rodape da ficha (mesmo ciclo de `dirty`).
- Mudar o dicionario com um ID fora do novo intervalo: nao corrigir sozinho; mostrar o erro e bloquear o lancamento pela validacao.
- Props novas em `ParameterSheetProps` ja existem para `onEdit` e `working`; adicione apenas estado local `advancedOpen`.
- `PreflightScreen.tsx` e `App.tsx` nao exigem props novas.

### 3.4 Envio e leitura real pelo Stage 5
Verificado: nao e preciso mudar o contrato.
- O launch envia o documento inteiro por `--params-json` (`App.tsx::commitLaunchDocument` -> `launchParamsJson`, `main.cjs:2535-2542`), e `MissionParameters.update_from_dict` aplica `rtl.*` (`parameters.py:1087`).
- `steps/rtl.py` le `rtl_cfg.target_aruco_id`, `marker_dict`, `tag_size` em `rtl.py:507,536-538,700-721,762-768` e constroi `Aruco(marker_dict=..., tag_size=...)`.
- `rtl.*` nao esta em `CRITICAL_PATHS` (`engine/launch.py:44-55`, `electron/missionStandby.cjs:22-33`): editar nao recicla a espera e o valor vale no go. **Nao** inclua esses paths em `CRITICAL_PATHS`.
- Testes novos:
  - vitest: `launchParamsJson(doc, pct)` carrega os tres campos tal como editados (extensao de `App.launchParams.test.tsx`).
  - pytest: `update_from_dict({"rtl": {"target_aruco_id": 12, "marker_dict": "DICT_4X4_50", "tag_size": 0.16}})` seguido de `audit_safe_envelopes` com resolver real do SDK devolve `[]`; e com `target_aruco_id=50` em `DICT_4X4_50` reporta `rtl.target_aruco_id`.
  - pytest de contrato (`test_contracts.py`): acrescentar `("rtl", "target_aruco_id")`, `("rtl", "marker_dict")`, `("rtl", "tag_size")` e `("rtl", "reverse_cruise_velocity")`, `("kinematics", "altitude_ceiling_margin_m")` e `("kinematics","max_horizontal_speed")` a lista `test_configuration_keys_the_renderer_reads_still_exist`.

---

## 4. Item 3: contagem como janela de warmup, decolagem instantanea no zero

Escopo: `mvp_mission_bebop/steps/takeoff.py`, `mvp_mission_bebop/mission.py` (so se necessario), `engine/launch.py` (sem mudar `launch_deadline`/`CountdownTicker`). Mudancas minimas e cirurgicas.

### 4.1 Medicao primeiro (obrigatorio)
Antes de editar, instrumente e registre num teste/bench (`--no-fly`, `test/test_takeoff.py` fixtures): intervalos `t_click -> inicio da sequencia de solo`, `flat trim ack`, `settle`, `calibracao`, `zero da contagem -> t_takeoff_cmd`. Ja existem `bb.t_click` e `bb.t_takeoff_cmd` (monotonic). Adicione `bb.t_countdown_zero` em `_countdown`. Meta numerica de aceite: `t_takeoff_cmd - t_countdown_zero <= 0.100 s` em simulacao e `[TIMING] ground_sequence_sec` logado.

### 4.2 Reestruturar a sequencia de solo (item 1.7 a, b, c)
1. **Abrir a sessao de percepcao no inicio da sequencia de solo**, nao em `_countdown`. Em `execute`, envolver `_ground_sequence` + `_countdown` num unico `with ctx.perception.session(imgsz=...)`. O warmup do YOLO passa a ocorrer desde o go, em paralelo com flat trim e calibracao. `_countdown` deixa de abrir a sessao e so alimenta `get_latest`/`notify_frame_received`.
2. **Eliminar o `delay(2.0)` bloqueante** de `_flat_trim`. Substituir por `self._imu_settled_at = time.monotonic() + _FLAT_TRIM_SETTLE_SEC` registrado no ack. A calibracao do solo e o warmup continuam durante a janela; antes de `_launch`, `_countdown` espera apenas `max(0, imu_settled_at - now)` se ainda restar. Em condicoes normais com contagem >= 5 s o assentamento termina dentro da janela e custa 0 s.
   - Verifique antes se `calibrate_ground_reference` depende de a IMU ter assentado (le `_buffer_z`, ver `odometry.py:388`). A leitura do codigo indica que usa buffer de odometria ja acumulado, nao a IMU. Se a calibracao puder correr em paralelo com o assentamento, ela corre; se o teste existente provar dependencia, mantenha a ordem flat trim -> calibrar mas ainda sem `delay` bloqueante, usando polling com `ctx.interrupted()`.
3. **Caminho frio** (`launch_deadline is None`): ancore o prazo no **inicio** do Stage 1 (`t0 = time.monotonic()` no topo de `execute`; prazo `t0 + countdown_sec`), nao apos a sequencia de solo. Os ticks `mission.countdown` passam a sair desde `t0`. Preserve: a contagem nunca encurta (cheia a partir de `t0`), `countdown_sec = 0` continua sem contagem e o regresso `test_a_deadline_nobody_saw_counted_runs_the_whole_countdown` continua verde.
4. Sequencia de solo mais longa que a contagem: a decolagem espera o termino, loga `[TIMING] ground_sequence_overrun_sec=<x>` e anuncia no log que a contagem foi excedida por interlock de solo. Nunca pular flat trim, calibracao ou checagem de bateria.

### 4.3 Atraso entre zero e comando de decolagem (item 1.7 d)
- Nao esperar o `disengage` de um ciclo em voo antes do takeoff. Ao fim da contagem: `ctx.perception._engaged_event.clear()` equivalente publico (adicionar `PerceptionWorker.release_nowait()` que apenas limpa o evento de engajamento, sem adquirir `_cycle_lock`), emitir `ctx.drone.takeoff(...)`, e so entao `disengage()` com espera limitada, **antes** do primeiro `ctx.grab_frame` de `_wait_for_takeoff_confirmation`. Racional: o comando de decolagem nao depende da camera; o `grab_frame` depende do detector solto.
- Mantenha a semantica de geracao de engajamento (`_engagement_generation`) para descartar resultados tardios.
- Teste (extensao de `test_takeoff.py`): detector falso que bloqueia 2 s no `detect`; assertar `t_takeoff_cmd - t_countdown_zero < 0.100`.

### 4.4 Estabilizacao pos-decolagem com alvo baixo (item 1.3)
- Em `_stabilize`, quando `target_altitude_m <= takeoff_settle_min_altitude_m + 0.05`, o gate nunca assenta. Use como piso efetivo `min(takeoff_settle_min_altitude_m, 0.5 * target_altitude_m)` e logue o piso usado. Assim a espera deixa de gastar todo o teto de `takeoff_stabilize_duration_sec` para alvos baixos. Documente no comentario o fundamento fisico (amostras abaixo do piso so provam "no chao").
- Nao altere o default `takeoff_settle_min_altitude_m = 0.30`.

### 4.5 O que NAO fazer
- Nao remova `_wait_for_takeoff_confirmation` (R1) nem `takeoff_confirm_timeout_sec`.
- Nao remova o `delay(3.0)` do SDK nem tente contorna-lo.
- Nao altere `launch_deadline`, `MAX_GO_DELAY_SEC`, `CountdownTicker` ou o protocolo `{op: go}`.
- Nao mexa em `CRITICAL_PATHS`.

### 4.6 Frontend
Nenhuma mudanca de logica. Revise `CountdownOverlay.tsx` apenas para confirmar que o fechamento depende do relogio proprio e do `reported === 0` (ja feito) e que nao ha `setTimeout` residual apos zero. Se houver, remova.

---

## 5. Item 4: mapa tatico com ancora fixa

### 5.1 Fonte unica da ancora
- Crie `src/lib/siteAnchor.ts`:
```ts
export const MAP_ANCHOR = { latitude: -23.6488913, longitude: -46.7187124, name: 'Transamerica Expo Center' } as const;
```
  Valores identicos a `config/site-anchor.json` (o enunciado cita -23.648891 e -46.718712, que arredondam para os mesmos). Adicione teste em `siteAnchor.test.ts` (ja le o JSON) assertando que `MAP_ANCHOR` coincide com o arquivo, para o JSON continuar sendo a fonte de verdade sem import entre diretorios (nao depender de `resolveJsonModule` cruzando a raiz do Vite).
- O backend (`main.cjs::siteAnchorEnv`, `BMG_BASE_LAT/LNG`) nao muda: continua servindo a ponte de telemetria.

### 5.2 `TacticalMap.tsx`
1. Remover do calculo de `geo` as fontes `gps`, `operator.location` e `baseLatitude/baseLongitude`. `geo` passa a ser constante: `{ lat: MAP_ANCHOR.latitude, lng: MAP_ANCHOR.longitude, source: 'site' }`.
2. Remover `useOperatorLocation` do componente. O hook continua existindo (`bmg:save-operator-location` e o cache do host alimentam a ponte); nao o apague, apenas pare de consumi-lo aqui. As props `latitude`, `longitude`, `gpsFix`, `baseLatitude`, `baseLongitude`, `baseSource` ficam obsoletas: remova-as do componente e de `CockpitScreen.tsx:203-209`, ou mantenha como opcionais ignoradas se o numero de testes tornar a remocao ruidosa. Preferir remover.
3. **Centro da vista = base (0,0)**, nao a aeronave. Em `view`: `sx = cx + east * pxPerMetre`, `sy = cy - north * pxPerMetre`. O marcador da aeronave passa de `translate(cx, cy)` para `translate(sx(droneEast), sy(droneNorth))`. O halo, o rotulo `E x.x - N y.y m` e o rotulo da base acompanham os dois pontos. A projecao dos tiles fica constante (`project(anchor)`), logo o basemap nao rola.
4. **Escala fixa:** remover `fitExtent`, `restExtent` e `overview` do calculo. Zoom padrao = `displayZoom(OVERVIEW_EXTENT_M, size, anchor.lat)` com `OVERVIEW_EXTENT_M = 300` (enquadramento que produz a barra de escala de ~200 m do mapa de referencia). Os botoes de zoom e roda continuam como acoes **manuais explicitas**; o botao "Voltar ao enquadramento" restaura o zoom padrao. Sem auto-ajuste que alargue com a trilha. Nao e necessario manter `fitExtent`; atualizar `TacticalMap.test.ts` (hoje so testa `fitExtent`) para testar a nova funcao pura de vista (extraia `fixedView(size, anchor, manualZoom)` para `lib/mapView.ts` e teste: centro, `pxPerMetre`, e que mudar `droneEast/droneNorth` nao altera `centre` nem `zoom`).
5. Rotulos: `BASE · DECOLAGEM (0, 0)` permanece. O rotulo do drone deve ler exatamente `E 0.0 · N 0.0 m` em repouso. O enunciado escreve `E 0.0 - N 0.0 m` com hifen; o componente atual usa o ponto medio. **Mantenha o ponto medio ja existente** (uniformidade com o visual atual) e relate a diferenca de glifo. Se o operador exigir o hifen, trocar uma string.
6. Rodape de coordenadas: exibir sempre a coordenada da ancora projetada pela odometria; rotular a fonte como `BASE FIXA` (substitui `GPS`/`ODOM`). `GEO_LABEL` perde `gps`, `device`, `host`, `cache`, `bridge`; mantenha `site`. Remover o aviso `baseCoarse` ("posicao aproximada"), pois a base e conhecida.
7. `useReverseGeocode(anchor.lat + dN, anchor.lng + dE, ...)`: manter, apontando para a posicao da aeronave projetada sobre a ancora, throttled como hoje. Cidade vem do reverse geocode ou, na falta, de `MAP_ANCHOR.name`.
8. Comportamento offline: sem tiles, a grade local continua registrada na base, agora fixa. Nenhuma outra alteracao.

### 5.3 O que verificar no restante
- `streamer/telemetry_bridge.py` continua publicando `latitude/longitude/base_*` (outros consumidores, p.ex. relatorio forense). Nao altere o payload.
- Procure outros leitores de `telemetry.latitude/longitude` (`grep`): hoje apenas `CockpitScreen.tsx:203-204`.

### 5.4 Testes
- vitest da vista fixa (acima).
- Teste de renderizacao simples de `TacticalMap` com `track` crescente e `gpsFix` simulado mudando: o `centre` do tile e a posicao do rotulo da base nao se movem entre renders. `CockpitScreen.test.tsx` mocka o `TacticalMap`; ajuste apenas props removidas.

---

## 6. Item 6: remocao da calibracao do magnetometro (conforme decisao 1.5, opcao A)

Remover:
1. `src/components/diagnostics/MagnetoCalibrationPanel.tsx` e `MagnetoCalibrationPanel.test.tsx`.
2. `src/lib/magnetoCalibration.ts`, `magnetoCalibration.test.ts`, `magnetoCommand.test.ts`.
3. `DiagnosticsScreen.tsx`: import do painel, prop `magneto` (e do tipo `MagnetoCalibration`), `calibrateMagneto` (`useCallback`) e o `<aside className="w-[260px] ...">` com o painel. Ajustar o layout: o terminal ocupa a largura toda; confira o `flex` pai (`DiagnosticsScreen.tsx` ~linhas 380-400) e `DiagnosticsScreen.test.tsx`.
4. `App.tsx:718`: remover `magneto={telemetry.magneto_calibration ?? null}`.
5. `electron/preload.cjs:71` (`calibrateMagneto`) e `electron/main.cjs:2076-2094` (`bmg:magneto-calibration`, com seu comentario).
6. `src/types/bmg.ts`: remover `calibrateMagneto` da interface de ponte (linhas 434-435). **Manter** `MagnetoCalibration` e `magneto_calibration` no `BmgTelemetry` enquanto o interlock (1.5, opcao A) existir; remover `magneto?: MagnetoCalibration | null` solto se nao for lido por ninguem (so o `DiagnosticsScreen` o consumia pela prop).
7. `streamer/command_bridge.py`: remover op `magneto`, `magneto_request`, `TOPIC_CALIBRATE_MAGNETO`, o publisher `calibrate_magneto`, a entrada no mapa de topicos do `hello`/status e a doc do protocolo (linhas ~24, 52-53, 90-100, 114, 124, 176-183). Remover `test/test_command_bridge_magneto.py`. Racional: comando de hardware sem nenhuma UI e superficie morta. Manter `support/fake_bebop/node.py` e `scripts/bench_relay.py` (contratos do driver, nao do frontend).
8. Interlock (opcao A): em `preflightGate.ts` trocar o texto por `Calibracao do magnetometro exigida pela aeronave; calibre pelo aplicativo da Parrot` e atualizar `preflightGate.test.ts:58`. Os testes `launchReadiness.test.ts:157` e `launchReadinessMain.test.ts:148` permanecem. **Se o operador escolher a opcao B**, remover tambem `magnetoRequired` de `preflightGate.ts`, `PreflightScreen.tsx:127,138`, `electron/launchReadiness.cjs:156-159` e `.d.cts:16`, `main.cjs:2597,2788` e os testes, alem de `set_magneto_calibration`/`TOPIC_MAGNETO`/`_on_magneto` em `streamer/telemetry_bridge.py` e `test_telemetry_truth.py:154-163`.
9. `docs/CHECKLIST_VOO_REAL.md:17,64` citam o magnetometro como item de checklist de voo real (nao e UI). Mantenha, mas se o interlock cair (opcao B) ajuste a linha 64.
10. Verificacao final: `grep -rniE "magneto|magnet" bebop_mission_control/src bebop_mission_control/electron bebop_mission_control/streamer` deve retornar **somente** o interlock e o tipo de telemetria da opcao A (ou nada, na opcao B). Nenhuma ocorrencia em `components/diagnostics/`.

---

## 7. Ordem de execucao sugerida

1. Baseline de testes (secao 0.5).
2. Item 6 (remocao): menor risco, reduz ruido nos arquivos que os demais itens tocam (`DiagnosticsScreen`, `App.tsx`, `main.cjs`, `preload.cjs`).
3. Itens 1 e 2: schema, helpers puros (`setLinkedPath`, `reconcileCoupledPaths`), hook, validacao, avisos, audit.
4. Item 5: modal avancado e validacao.
5. Item 4: mapa.
6. Item 3: backend de decolagem, com medicao antes e depois.
7. Suites completas, build, relatorio.

Faca testes e codigo de cada item no mesmo passo; nao acumule todos para o fim.

---

## 8. Criterios de aceite e verificacao

Comandos (todos verdes, saida resumida no relatorio):
```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py
python3 -m pytest test/
cd bebop_mission_control
npm run typecheck
npm test
npm run build
```

Verificacoes funcionais (cada uma com teste automatizado ou evidencia):
1. Os sliders alcancam as extensoes exatas da tabela 2.1 (teste de limites inclusivos em `launchBlockers`; teste de render do `HybridSlider` com `min`/`max` novos).
2. Mover o slider de velocidade para 0.35 grava `forward = 0.35`, `reverse = -0.35` e `max_horizontal_speed >= 0.40` no `working`, no `mission_config.json` apos salvar e no `--params-json` do lancamento. Carregar um documento com `forward 0.20 / reverse -0.10` o reconcilia para -0.20 e o mostra como alteracao pendente.
3. `Configuracoes Avancadas` abre o modal; editar `target_aruco_id`, `marker_dict` e `tag_size` marca a ficha como alterada, persiste ao salvar, vai no `--params-json` e e lido por `steps/rtl.py` (teste Python com `update_from_dict`).
4. Mapa: com `gpsFix` alternando e `latitude/longitude` variando, centro, zoom e rotulo da base nao se movem. Base em -23.6488913, -46.7187124, drone em (0,0) no repouso, barra de escala coerente com ~200 m no tamanho de painel de referencia.
5. Diagnostico sem nenhuma mencao a magnetometro; `grep` da secao 6.10.
6. Backend: teste de temporizacao `t_takeoff_cmd - t_countdown_zero < 0.100 s` com detector bloqueante; teste de caminho frio com contagem total = N s (nao N + solo); teste de excesso da sequencia de solo (decolagem espera, nunca pula).
7. Audit: defaults dentro de todos os envelopes; margem 0.0 reportada; altitude 0.1 reportada como divergencia consultiva; nadir >= search reportado.

---

## 9. Relatorio final obrigatorio (curto, tecnico, sem floreio)

Liste: resultado de cada suite (contagens antes e depois), arquivos tocados por item, e **explicitamente** as decisoes e riscos abaixo para o operador confirmar:
1. Teto de velocidade horizontal elevado junto com a velocidade (1.1) e o fato de `normalized_to_mps = 1.0` nao estar calibrado.
2. RTL padrao ficou o dobro da velocidade anterior (1.2).
3. Altitude 0.1 e margem 0.0 aceitas pela ficha, com aviso e audit consultivo; risco de pouso de emergencia por teto e de estabilizacao sem assentar (1.3).
4. Regra `nadir < search` bloqueando o lancamento (1.4).
5. Interlock de magnetometro mantido (opcao A) e o codigo morto `calibration_required` (1.5); pedir escolha A ou B.
6. Glifo do rotulo do drone (`·` vs `-`) no mapa.
7. Estado do working tree: nada commitado. Proposta de commits atomicos (um por item) aguardando autorizacao.

Se qualquer premissa deste documento nao se confirmar no codigo, **pare, relate o fato e a evidencia, e nao improvise**.
