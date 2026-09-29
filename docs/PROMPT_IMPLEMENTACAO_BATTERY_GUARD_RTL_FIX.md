# Prompt de Implementação — Battery Guard, Fix RTL, Housekeeping

> Gerado a partir de auditoria arquitetural da lógica de missão (2026-09-28).
> Escopo aprovado pelo usuário. Não implementar nada fora deste documento.

Você vai trabalhar no repositório MVP-Bebop, localizado em `/home/joaomoreira/ros2_ws/src/mvp_mission_bebop/`
(NÃO na raiz do workspace — o `CLAUDE.md` da raiz e o de `src/mvp_mission_bebop/CLAUDE.md` descrevem essa
subpasta como se fosse a raiz do monorepo; siga ambos os arquivos à risca, incluindo estilo de commit,
tipagem estrita e tolerância zero a características de IA/LLM em código e mensagens).

ANTES DE QUALQUER IMPLEMENTAÇÃO: inspecione o repositório nectar-sdk em
`/home/joaomoreira/ros2_ws/src/nectar-sdk` (não use o path `/home/jv/...` citado no CLAUDE.md, está
desatualizado para esta máquina). Verifique especificamente se o SDK já expõe uma interface de leitura de
estado de bateria do Bebop 2 (ex. via ARDrone3 CommonState/BatteryStateChanged ou equivalente). Se existir,
reutilize-a diretamente. Replique o estilo de engenharia da Black Bee Drones (docstrings NumPy/reST,
type hints estritos, validação defensiva de entradas) em todo código novo. Antes de mexer no wiring de
parâmetros, leia `bebop_mission_control/src/lib/parameterSchema.ts` (a superfície pequena e curada de
seis figuras editáveis pelo operador, cada `path` mapeando para um campo real de `MissionParameters`),
`bebop_mission_control/src/hooks/useMissionParameters.ts` (como o documento inteiro de
`mission_config.json` round-trips, inclusive campos fora do schema) e
`bebop_mission_control/src/lib/batteryFailsafe.ts` + o watcher em `App.tsx` (o failsafe de bateria já
existente, que decide RTL-vs-land do lado da GUI) antes de tocar no item 2/3 abaixo. Não existe
`MissionLaunchScreen.tsx` neste repositório — não crie ou referencie esse arquivo.

Execute as seguintes tarefas, em commits atômicos separados (Conventional Commits, inglês técnico):

## 1. FIX CRÍTICO — RTLGuidanceController.compute() descarta a lei de controle real

Arquivo: `mvp_mission_bebop/controllers/rtl_guidance.py`, linhas ~377-382.

O método chama `_compute_longitudinal(ey, ...)` e `_compute_lateral(ex, ...)` com os erros trocados, e
depois ignora os retornos (`longitudinal_mps`, `lateral_mps`), publicando em vez disso
`vx = -ey * 0.0469` e `vy = ex * 0.0469` (ganho P puro sem shaper). Corrija para:

- Chamar `_compute_longitudinal` com o erro along-track correto e `_compute_lateral` com o erro
  cross-track correto (confirme a convenção de sinais lendo as docstrings dos dois métodos e os
  testes de projeção geométrica em `test/test_aruco_rtl.py` antes de decidir qual argumento é qual).
- Usar os valores retornados (`longitudinal_mps`, `lateral_mps`) como `vx`/`vy`, não uma fórmula paralela.
- Chamar `self._shaper_x.shape(...)` e `self._shaper_y.shape(...)` sobre esses valores antes de publicar,
  seguindo exatamente o padrão já usado por `ArucoCenteringController` nas linhas ~1064-1065 e
  ~1152-1153 do mesmo arquivo.
- Adicione um teste novo (`test/test_rtl_guidance_bugfix.py` ou extensão de `test_aruco_rtl.py`) que
  exercite `RTLGuidanceController.compute()` fim-a-fim e verifique que `vx`/`vy` publicados correspondem
  aos valores computados por `_compute_longitudinal`/`_compute_lateral` após shaping.

## 2. Battery guard — battery-idle-protection não existe hoje no pacote de missão

CONTEXTO DE WIRING (correção sobre a versão anterior deste documento, que citava uma
`MissionLaunchScreen.tsx` inexistente): a estação já tem um failsafe de bateria completo, inteiramente do
lado da GUI — `bebop_mission_control/src/lib/batteryFailsafe.ts` + o watcher em `App.tsx`
(`returnOnCriticalBattery`, `~linha 450-491`). Ele assiste `telemetry.battery_pct` ao vivo, decide entre
pedir RTL (`bridge.gotoStage(RTL_STAGE)`, com watchdog de ack de `RTL_ACK_TIMEOUT_MS=5000`) ou pousar no
lugar (`landRef.current()`), usando um único número, `failsafe.thresholdPct` (default 20, persistido em
`localStorage` sob `FAILSAFE_KEY`, editável no flyout de bateria de `StatusBar.tsx`). Essa lógica de
decisão RTL-vs-land continua sendo dona exclusiva da GUI — NÃO a reimplemente em Python. O que este item
constrói é apenas uma rede de segurança redundante *dentro do processo `mission.py`*, para os casos em
que a GUI/Electron não está no comando (rotina de bancada headless via CLI, crash da GUI, ponte IPC
caída): pouso instantâneo, nunca RTL.

- Não adicione um novo item em `parameterSchema.ts`/`PARAMETER_GROUPS`. Essa superfície é
  deliberadamente pequena (seis figuras editáveis pelo operador, cada uma com seu próprio
  `defaultValue`/reset) e o próprio docstring do arquivo diz que outros campos de `MissionParameters`
  seguem existindo em `mission_config.json` sem passar por ali. O limiar de bateria já tem UI própria
  (o flyout) e persistência própria (`FAILSAFE_KEY`) — não crie um segundo controle para o mesmo número.
- Em vez disso, no ponto em que `bebop_mission_control/src/App.tsx` monta `paramsJson` antes de lançar a
  missão (`paramsJson: JSON.stringify(doc ?? {})`, ocorre tanto no launch real quanto em
  `runBenchStage`), inclua no `doc` clonado o campo `battery.land_pct` (ou o path equivalente que o
  dataclass Python expuser) com o valor atual de `failsafe.thresholdPct` — o mesmo state que o flyout já
  lê/escreve. `doc` já trafega o documento inteiro de `mission_config.json`, incluindo campos fora do
  schema editável, então isso não exige UI nova nem uma segunda chave persistida.
- Crie um módulo `telemetry/battery.py` (nome consistente com `telemetry/odometry.py` e
  `telemetry/failsafe.py`) que:
  - Assine o estado de bateria do driver Bebop 2 (reutilizando a interface do nectar-sdk se existir,
    conforme instrução acima; caso contrário, parsing próprio seguindo o padrão `TelemetryHealth` já
    usado em `telemetry/odometry.py`, incluindo tratamento de telemetria ausente/stale).
  - Exponha `battery_land_pct: float` em `MissionParameters` (`parameters.py`), populado a partir do
    `battery.land_pct` injetado no `--params-json`. Quando ausente (ex. execução de bancada via CLI sem
    a GUI), caia no mesmo default que a GUI usa hoje — `20.0` (`App.tsx:64`,
    `FAILSAFE_DEFAULT.thresholdPct`) — em vez de abortar a inicialização; comente no dataclass que os
    dois defaults (TS e Python) são o mesmo número por convenção e não podem compartilhar a constante
    entre linguagens, então qualquer mudança de um lado deve ser replicada no outro.
  - Threshold de warning é FIXO em 10% (não configurável, não é o mesmo campo de `battery_land_pct`):
    ao cruzar esse valor, apenas logue e emita um milestone novo (`mission.battery_warning`) — a missão
    continua normalmente, sem RTL forçado, sem alterar trajetória ou estágio.
  - Ao atingir `battery_land_pct`, acione `trigger_emergency_land()` (`telemetry/failsafe.py`, já
    existente, já zera velocidade e comanda `land()` direto) — NÃO implemente um RTL antes disso, é
    pouso instantâneo no lugar em que o drone está. Não replique `failsafeAction`/`gotoStage`: essa
    decisão continua sendo tomada apenas pela GUI quando ela está presente.
  - Integre a checagem em `FailsafeSupervisor.evaluate_system_health()` (`telemetry/failsafe.py`, linha
    ~298) como mais uma condição, no mesmo padrão das existentes.
  - Valide a nova chave de milestone em `test/test_contracts.py` (`MILESTONE_KEYS`) e a presença de
    `battery.land_pct` no `--params-json` de exemplo usado pelos testes de contrato.
  - Escreva testes (`test/test_battery_guard.py`) cobrindo: telemetria de bateria ausente/stale, warning
    em exatamente 10% (e não antes), land instantâneo em `battery_land_pct` (valor de teste arbitrário,
    configurado explicitamente no teste), fallback para `20.0` quando o campo está ausente do
    `--params-json`, e que o land reutiliza `trigger_emergency_land` existente em vez de duplicar
    lógica de pouso.
- No frontend, adicione um teste (junto de `ParameterPreset.test.tsx` ou novo arquivo) confirmando que
  `battery.land_pct` sai no `paramsJson` do launch e do bench-stage com o valor corrente de
  `failsafe.thresholdPct`, incluindo o caso em que o operador alterou o slider do flyout antes de lançar.

## 3. Pré-arm battery gate

Antes de `TakeoffStep` executar a decolagem (`steps/takeoff.py`), recuse iniciar a missão se a leitura de
bateria atual já estiver ≤ `battery_land_pct` (o mesmo parâmetro do item 2 — não crie um segundo
limiar). Ao recusar, emita um erro claro no mesmo canal de log estruturado já usado para outras
recusas de decolagem (ex. a recusa existente por falha de calibração de solo em
`steps/takeoff.py:81-90` é o padrão a seguir). Escreva teste cobrindo a recusa e o caminho normal
(bateria acima do limiar).

## 4. Race condition em PerceptionWorker.disengage()

Corrija a race condition documentada (`mvp_mission_bebop/perception/worker.py`, linhas ~556-563): hoje o
timeout de 2.0s loga erro mas retorna controle mesmo assim. Substitua por um contador de geração
(`generation`) incrementado a cada `engage()`, descartando por número de geração qualquer resultado de um
ciclo anterior, em vez de depender de timeout de relógio. Atualize/estenda os testes existentes de
`perception/worker` para cobrir o novo mecanismo de fencing.

## 5. Alinhamento de timeouts vídeo/percepção

Alinhe `video_stream_timeout_sec` (`parameters.py`, atualmente 8.0s) com a ordem de grandeza de
`perception_max_age_sec` (atualmente 0.5s), usado nas leis de controle. Proponha e implemente um valor
entre 1.5 e 2.0s para o failsafe crítico de vídeo, mantendo se necessário um segundo limiar mais
longo apenas para diagnóstico não crítico. Ajuste os testes de `telemetry/failsafe.py` afetados.

## 6. Validação de configuração no boot

Ao carregar `mission_config.json` em `mission.py`, compare os valores persistidos (`nadir_tilt_deg`,
`target_altitude_m`, `marker_dict`, `target_aruco_id`, entre outros com comentários de envelope seguro em
`parameters.py`) contra os limites documentados nos comentários do dataclass `MissionParameters`. Logue
um warning estruturado (não aborte a missão) quando o valor operacional divergir do envelope
documentado como seguro. Adicione teste cobrindo o caso de divergência.

## 7. Housekeeping (commits separados, sem impacto funcional)

- Corrija o path do nectar-sdk no `CLAUDE.md` (raiz do workspace e `src/mvp_mission_bebop/CLAUDE.md`) de
  `/home/jv/ros2_ws/src/nectar-sdk` para `/home/joaomoreira/ros2_ws/src/nectar-sdk`.
- Adicione ao `.gitignore` os arquivos `accident_raw_*.png` / `accident_inspected_*.jpg` /
  `accident_metadata_*.json` soltos na árvore de trabalho (capturas de bancada não versionadas).
- Não mexa na cópia aninhada em `src/nectar-sdk/nectar-sdk/` (tem `COLCON_IGNORE` próprio e `.git` próprio)
  além de confirmar que nenhum grep/build do pacote de missão a referencia acidentalmente.

## Escopo explicitamente descartado

NÃO IMPLEMENTE: `MissionDurationGuard`, force-RTL por bateria, qualquer item de showmanship ou de
eficiência além do pré-arm gate do item 3 — todos descartados explicitamente pelo usuário.

## Validação final (obrigatória antes de considerar qualquer tarefa concluída)

- Rode `python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py` e a suíte completa
  `python3 -m pytest test/` — todos os testes (novos e existentes) devem passar.
- Rode `colcon build --symlink-install --packages-select mvp_mission_bebop` e confirme build limpo.
- `git status` deve estar limpo ao final, com todas as mudanças em commits atômicos conforme o padrão
  Conventional Commits do CLAUDE.md.
