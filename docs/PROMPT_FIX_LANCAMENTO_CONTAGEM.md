# Investigação e arquitetura — contagem regressiva pulada a partir do 2º lançamento

> Data: 2026-10-01. HEAD auditado: working tree atual (Fases 0-8 do plano anterior parcialmente
> commitadas até `7c4479d`/`00e2292`; Fases 5-8 prontas no working tree, sem commit; driver com
> alterações locais sem commit). Este documento é fruto de leitura de código, não de execução ao vivo —
> nenhum processo foi rodado, nenhum comando publicado.
>
> Isto não é um novo plano do zero: é uma investigação cirúrgica de um defeito específico dentro do
> sistema que as Fases 1-7 já construíram (a missão em espera / `missionStandby`, o `CountdownTicker`, o
> `CountdownOverlay`). Trate como adenda ao `docs/IMPLEMENTACAO_PROGRESSO.md` existente, não como
> substituto dele.

## 0. O que o operador relatou

1. No primeiro clique em "Iniciar" depois de abrir o BMG, a contagem de 10 s aparece e completa
   normalmente.
2. Do segundo clique em diante (mesma sessão do app), a contagem é pulada: a tela salta direto para o
   cockpit, sem o overlay contar 10→0.
3. Pedido: a contagem de `kinematics.countdown_sec` deve ser visível e audível por completo em **toda**
   execução, em qualquer modo (voo real, bancada, ou uma rotina individual de estágio).
4. Pedido: pré-carregar as dependências pesadas no boot do BMG, para o clique disparar a contagem na
   hora.
5. Pedido: "Finalizar Missão" deve limpar o estado e deixar a estação pronta para um novo "Iniciar" sem
   engasgos.

Os itens 4 e 5 **já existem**, implementados por uma sessão anterior (ver seção 3). O item 1-3 é um
defeito real, **encontrado e confirmado por leitura de código linha a linha** nesta sessão (seção 1). O
texto abaixo substitui as hipóteses do prompt original do usuário, que citava variáveis e arquivos de uma
arquitetura anterior (`isMissionOver`, `useMissionRuntime.reset()` existem, mas o mecanismo de contagem
mudou por completo desde então: não há mais um timer do lado da estação dirigindo o cockpit).

---

## 1. Defeito confirmado: o piso de 10 s pode ser anulado por completo

### 1.1 A arquitetura de contagem hoje

Para um lançamento completo (botão "Iniciar", qualquer modo de armamento), a cadeia é:

1. `App.tsx:launch()` ([App.tsx:229-282](../bebop_mission_control/src/App.tsx:229)) grava `clickedAt =
   Date.now()` e chama `mission.launch({ launchAtMs: clickedAt, ... })`.
2. `main.cjs:startMissionProcess` ([main.cjs:2419-2484](../bebop_mission_control/electron/main.cjs:2419))
   tenta primeiro `missionStandby.take(launchDoc, driverUp(), launchAtMs)` — a missão `mission.py
   --standby` que a estação mantém pré-aquecida (SDK, driver, câmera, detector já prontos).
   - **Se há uma espera pronta e com a mesma chave** (`missionStandby.cjs:143-153`): ela recebe
     `{op:"go", params, launch_at_ms}` pelo stdin e o processo é esse.
   - **Se não há** (nenhuma espera, ou a chave não bate): `missionStandby.pause()` e um `mission.py`
     **novo e frio** é criado, com `--launch-at-ms <launchAtMs>` ([main.cjs:2481-2489](../bebop_mission_control/electron/main.cjs:2481)).
3. Em ambos os casos, o mesmo `launch_at_ms` chega a `mission.py`. Em
   [mission.py:932-940](../mvp_mission_bebop/mission.py:932):
   ```python
   if launch_at_ms is not None:
       ctx.launch_deadline = launch_deadline(
           launch_at_ms, params.kinematics.countdown_sec, time.time(), time.monotonic()
       )
       ctx.countdown_ticker = CountdownTicker(ctx.launch_deadline, ...)
       ctx.countdown_ticker.start()
   ```
   `launch_deadline()` ([engine/launch.py:96-121](../mvp_mission_bebop/engine/launch.py:96)) calcula:
   `elapsed = max(0, now_wall - launch_at_ms/1000)`, `deadline = now_mono + max(0, countdown_sec -
   elapsed)`. **O modelo inteiro assume que o tempo decorrido entre o clique e este ponto do código é
   desprezível.** Isso só é verdade quando a espera (`--standby`) absorveu o custo de inicialização
   ANTES do clique.
4. Quando o processo é **frio** (sem espera pronta), tudo o que vem antes da linha 932 — init do SDK,
   `connect()` do driver, carga e warmup do YOLO, abertura de câmera, criação dos nós de telemetria —
   roda **depois do clique**. Pela medição já registrada no próprio
   `docs/IMPLEMENTACAO_PROGRESSO.md` (Fase 7.5), esse arranque fica em torno de 6-12 s na bancada, e mais
   com o driver/detector reais. Se `countdown_sec` configurado é 10 s (o default), `elapsed` já pode
   **igualar ou superar** esses 10 s antes da linha 932 rodar. `deadline` vira `now_mono + 0`, ou seja,
   **o prazo já nasce vencido.**
5. `CountdownTicker._run()` ([engine/launch.py:186-196](../mvp_mission_bebop/engine/launch.py:186)):
   ```python
   remaining = self._deadline - time.monotonic()
   whole = int(math.ceil(remaining))
   if whole <= 0:
       return
   ```
   Com o prazo já vencido, a primeira iteração já encontra `whole <= 0` e o método **retorna sem emitir
   nenhum tick.** Nenhum `mission.countdown` é publicado pelo ticker.
6. `TakeoffStep._countdown()` ([steps/takeoff.py:247-266](../mvp_mission_bebop/steps/takeoff.py:247)):
   ```python
   duration = ctx.params.kinematics.countdown_sec
   if launch_deadline is not None:
       duration = max(0.0, launch_deadline - time.monotonic())
   if duration <= 0.0:
       self._stop_ticker(ctx)
       emit_milestone("mission.countdown_3", {"remaining_sec": 0})
       emit_milestone("mission.countdown", {"remaining_sec": 0})
       return StepStatus.SUCCESS
   ```
   Com `duration <= 0`, **o laço inteiro do countdown (linhas 268-308), que faz o warmup do YOLO e os
   ticks locais, nunca roda.** O método emite os dois milestones já com `remaining_sec: 0` e retorna
   `SUCCESS` direto para `_flight_sequence()` → `_launch()` — **o Estágio 1 já declara decolagem
   autorizada e segue para a decolagem (real ou simulada) sem que o piso de segurança de 10 s tenha
   decorrido de fato.**
7. No frontend, `useMissionCountdown` ([useMissionCountdown.ts:36-39](../bebop_mission_control/src/hooks/useMissionCountdown.ts:36))
   recebe esse `mission.countdown {remaining_sec: 0}` e grava `remaining: 0`.
8. `CountdownOverlay` ([CountdownOverlay.tsx:104-109](../bebop_mission_control/src/components/preflight/CountdownOverlay.tsx:104)):
   ```jsx
   useEffect(() => {
     if (reported === 0 && !done.current) {
       done.current = true;
       onDone();
     }
   }, [reported, onDone]);
   ```
   dispara `onDone()` **na hora em que `reported` chega a 0** — não importa se o relógio próprio do
   overlay (`clockRemaining`, calculado de `launchAt`) ainda estava em fase "preparing"/"waiting" há
   poucos segundos. `onDone` é `App.tsx:finishCountdown()` ([App.tsx:373-376](../bebop_mission_control/src/App.tsx:373)),
   que fecha o overlay e troca a tela para o cockpit **imediatamente.**

Resultado: o operador vê o overlay em "Preparando a aeronave"/"Aguardando a aeronave" por alguns
segundos e então ele **desaparece sem nunca mostrar 10, 9, 8…**, direto para o cockpit — e, mais grave,
**o Estágio 1 já retornou `SUCCESS` e a decolagem já está em curso nesse instante.** Não é só uma falha
visual: é o piso de segurança ("a janela em que afastar-se não custa nada", como o próprio comentário do
código descreve) sendo eliminado.

### 1.2 Por que funciona na primeira vez e falha depois

- No boot do app, `startBackgroundServices()` chama `refreshStandby()`
  ([main.cjs:2984-2985](../bebop_mission_control/electron/main.cjs:2984)), que começa a preparar a
  primeira espera. Entre abrir o app, navegar a tela de pré-voo e ajustar parâmetros, normalmente já
  passou tempo suficiente para essa primeira espera ficar pronta — **o primeiro clique usa a espera, cai
  no caminho correto, e por isso funciona.**
- `refreshStandby()` só é chamado de novo: ao fechar o processo da missão
  ([main.cjs:2541-2543](../bebop_mission_control/electron/main.cjs:2541)), ao salvar parâmetros
  ([main.cjs:1646-1648](../bebop_mission_control/electron/main.cjs:1646)), quando o estado do driver muda
  ([main.cjs:430-432](../bebop_mission_control/electron/main.cjs:430)), e no boot. **Não há nenhuma
  garantia de que a nova espera já esteja pronta (imprimiu `[STANDBY] ready`) antes do próximo clique.**
  Se o ciclo do operador (observar o resultado, clicar "Finalizar", clicar "Iniciar" de novo) for mais
  rápido do que o tempo de preparo da espera, o segundo lançamento **sempre** cai no caminho frio —
  reproduzindo o defeito da seção 1.1 de forma determinística, não ocasional.
- **Evidência adicional de uma falha de observabilidade:** o ramo frio em `startMissionProcess`
  ([main.cjs:2487-2489](../bebop_mission_control/electron/main.cjs:2487)) não grava nenhuma linha de log
  quando cai nesse caminho — só o ramo "espera pronta" loga algo
  ([main.cjs:2485](../bebop_mission_control/electron/main.cjs:2485)). Por isso o log da missão não diz,
  hoje, quando um lançamento foi frio. Isso por si só impediu o diagnóstico anterior.

### 1.3 Escopo exato do defeito

- **Afetado:** o botão principal "Iniciar" ([LaunchDial](../bebop_mission_control/src/App.tsx:229)),
  em **qualquer** modo de armamento (`--fly` ou `--no-fly`), porque ambos passam por
  `startMissionProcess` sem `--stages` (`fullLaunch = true`).
- **Não afetado, por construção:** os botões de rotina individual de estágio (`runBenchStage`,
  [App.tsx:289-335](../bebop_mission_control/src/App.tsx:289)). Esses sempre usam `--stages`, nunca
  tomam a espera (`prepared = fullLaunch ? ... : null`), nunca recebem `--launch-at-ms`, e por isso
  `ctx.launch_deadline` nunca é definido nesse caminho: `TakeoffStep._countdown()` cai no ramo
  `launch_deadline is None` e roda o laço local inteiro, sempre pelos `countdown_sec` completos. A
  contagem do operador descrita no item 2 ("qualquer rotina individual de estágio") já está correta hoje;
  **confirme isso com um teste antes de mexer**, para não introduzir uma regressão ali.

### 1.4 Nuance que a correção precisa preservar

`kinematics.countdown_sec = 0` é um valor legítimo e documentado ("0 = sem contagem", Fase 5.7 do
progresso). Hoje a mesma checagem `duration <= 0.0` ([takeoff.py:262](../mvp_mission_bebop/steps/takeoff.py:262))
cobre tanto esse caso intencional quanto o caso acidental descrito acima — **a correção precisa
distinguir "o operador configurou 0 s de propósito" de "o prazo já nasceu vencido por causa do tempo de
arranque"**, sem reintroduzir o defeito para quem usa 0 s de propósito nem quebrar o teste que cobre esse
caso.

### 1.5 Hipótese secundária a verificar (não confirmada, investigar antes de descartar)

Independente do que foi confirmado acima, existe um segundo caminho possível para o mesmo sintoma:
`startMissionProcess` recusa um lançamento com `missionProcess` ainda não nulo
([main.cjs:2419](../bebop_mission_control/electron/main.cjs:2419): `{success:false, message:'Uma missão
já está em andamento.'}`). Se esse retorno chegar enquanto o processo anterior ainda não fechou de fato
(por exemplo, um clique em "Iniciar" disparado antes do travamento de "Finalizar" liberar de verdade, ou
uma condição de corrida entre o fechamento do processo anterior e a tela voltar para o pré-voo),
`useMissionRuntime.launch()` ([useMissionRuntime.ts:144-148](../bebop_mission_control/src/hooks/useMissionRuntime.ts:144))
grava `state = 'faulted'`, que está em `OVER_STATES`
([missionOutcome.ts:33-37](../bebop_mission_control/src/lib/missionOutcome.ts:33)), e o efeito em
`App.tsx:381-383` chama `finishCountdown()` imediatamente pelo mesmo motivo (`isMissionOver`), por um
caminho totalmente diferente do descrito acima. Investigue se isso é alcançável dado o `useFinishLock`
já implementado (Fase 2); se for, é um segundo defeito independente com o mesmo sintoma e precisa de
correção própria.

---

## 2. O que já existe e não deve ser refeito

Confirmado por leitura de código (não apenas pelo progresso registrado):

- **Missão em espera** (`electron/missionStandby.cjs`, `mvp_mission_bebop/engine/launch.py`): pré-aquece
  SDK, driver, câmera, detector e telemetria antes do clique. Reciclada quando os `CRITICAL_PATHS` mudam
  ou o driver sobe/cai. **Implementado, funcional no caminho feliz — não reimplementar.**
- **Travamento do "Finalizar Missão"** (`useFinishLock.ts`, `finishLock.ts`): já existe uma máquina de
  estados completa, com teste de bancada dedicado
  (`finishLock.rehearsal.test.ts`) que prova Finalizar travado em todos os estágios e liberado só após o
  pouso confirmado em solo. Um bug relacionado ("último estado de bancada ficava LANDING e travava o
  Finalizar") já foi corrigido (`KinematicSimulator.complete_landing()`). **Não redesenhar; apenas
  confirmar que esse mecanismo não interage mal com o defeito da seção 1 (ver 1.5).**
- **Exit codes determinísticos e `mission.touchdown`**: já existem (`engine/exit_codes.py`,
  `missionOutcome.ts`). **Não mexer.**
- **Latência de arranque**: já otimizada na Fase 7 (import lazy do announcer, warmup do YOLO em
  paralelo, device de inferência pré-carregado). O arranque frio ficou em ~6-12 s na bancada — rápido o
  bastante para tornar o defeito da seção 1 **mais frequente**, não menos, porque um arranque frio de
  6 s facilmente consome um `countdown_sec` de 10 s quase por inteiro antes mesmo de qualquer margem.

---

## 3. Diretriz arquitetural para a correção (sem prescrever código)

O convite é para consertar um **invariante**, não um sintoma pontual: *toda vez que o operador clicar em
"Iniciar", o piso configurado em `kinematics.countdown_sec` deve decorrer de verdade, visível e audível,
antes de `TakeoffStep` autorizar a decolagem — independentemente de o lançamento ter vindo de uma espera
pronta ou de um processo frio, e independentemente de quanto tempo o arranque consumiu.*

Isso aponta para duas camadas de correção, as duas necessárias (defesa em profundidade: uma por si só
não cobre a garantia de segurança completa):

### 3.1 Camada de backend (a que protege a aeronave)

O modelo de "prazo absoluto = clique + countdown" só é seguro quando o tempo decorrido até ele ser lido é
desprezível. Duas direções possíveis, a escolher pela investigação (não são mutuamente exclusivas):

- **(a) Não propagar um prazo que já nasce estourado.** Em vez de `_countdown()` tratar `duration <= 0`
  como "o prazo já passou, então a decolagem já está autorizada", ele deve tratar isso como "o prazo não
  é mais útil para medir o piso: rode o piso completo a partir de agora." Ou seja, o piso de segurança
  (`countdown_sec`) nunca é encurtado pelo tempo que o arranque consumiu — ele só pode ser **pulado**
  quando configurado como 0 de propósito (distinção da seção 1.4).
- **(b) Não computar um prazo pinado ao clique quando o lançamento é frio.** Um processo frio não tem
  como saber, no momento do clique, quanto tempo seu próprio arranque vai levar; pinar o prazo ao
  instante do clique é uma otimização que só faz sentido quando uma espera absorveu esse custo
  antecipadamente. Investigue se faz mais sentido `main.cjs` só enviar `--launch-at-ms` quando de fato
  entregou uma missão pré-aquecida (`missionStandby.take()` não nulo), deixando o caminho frio sem prazo
  pinado — nesse caso `_countdown()` já cai, hoje, no ramo `launch_deadline is None`, que roda o laço
  local inteiro (comportamento que já se comporta corretamente, conforme a seção 1.3 confirma para
  `runBenchStage`).

Qualquer uma das duas (ou as duas) fecha o defeito. Decida com base no que for mais simples de manter e
testar; documente a escolha em `docs/IMPLEMENTACAO_PROGRESSO.md`, no mesmo formato de `Ruling:` já usado
no arquivo.

### 3.2 Camada de frontend (defesa em profundidade, não substitui a 3.1)

`CountdownOverlay` fechar o overlay só porque `reported === 0` chegou é, hoje, uma confiança cega no
backend. Mesmo depois da correção de 3.1, um relógio de segurança no próprio overlay evita que um futuro
defeito do mesmo tipo volte a produzir o mesmo sintoma sem que ninguém perceba: o overlay não deveria se
fechar antes de o relógio próprio dele (`clockRemaining`, já calculado a partir de `launchAt`) também
chegar a zero — exceto quando o relógio próprio nunca foi armado (`launchAt === null`, caso em que ele já
hoje confia inteiramente no `reported`). Isso é independente de qualquer mudança na semântica de
`mission.countdown`: é só a condição que decide chamar `onDone()`.

Isso **não substitui** a correção de backend: um frontend que não fecha a tela cedo não impede a
aeronave de já ter recebido o comando de decolagem internamente (o `_countdown()` do backend é quem
decide isso). As duas camadas corrigem problemas diferentes: 3.1 protege a aeronave; 3.2 protege a
veracidade do que a interface mostra ao operador.

### 3.3 Observabilidade (para este defeito nunca mais passar despercebido)

O ramo frio de `startMissionProcess` precisa logar explicitamente que caiu nesse caminho e por quê (sem
espera pronta — e, se possível, qual critério de `CRITICAL_PATHS` não bateu, ou se não havia nenhuma
espera em preparo). Hoje só o caminho "espera pronta" loga algo. Essa assimetria é, por si, uma falha de
observabilidade que atrasou o diagnóstico deste defeito.

### 3.4 Hipótese secundária (seção 1.5)

Investigue se o retorno `"Uma missão já está em andamento."` é alcançável por um clique duplo do
operador dado o `useFinishLock` existente. Se for, decida e implemente a correção própria (provavelmente
um retry curto ou uma espera pelo fechamento do processo anterior em vez de recusa imediata com
`faulted`). Se não for alcançável (o lock já impede), documente a conclusão e não mude nada ali.

---

## 4. Critérios de aceite

1. Clicar em "Iniciar" exibe o `CountdownOverlay` regredindo de `countdown_sec` até 0, por completo, em
   **toda** execução consecutiva (1ª, 2ª, 3ª...), em qualquer modo de armamento, com e sem uma espera
   pronta no momento do clique.
2. Em nenhum caso o `Estágio 1` retorna `SUCCESS` do `_countdown()` antes de `countdown_sec` segundos
   reais terem decorrido desde o início efetivo da contagem — exceto quando `countdown_sec` é
   explicitamente 0.
3. `countdown_sec = 0` continua pulando a contagem de propósito, sem regressão.
4. As rotinas de estágio individual (`runBenchStage`) continuam com a contagem completa (comportamento
   já correto hoje — cubra com um teste de regressão explícito, não assuma).
5. O log da missão (`recordLog('mission', ...)`) distingue, de forma auditável, um lançamento que usou
   uma espera pronta de um que caiu no caminho frio.
6. `useFinishLock`/`finishLock` continuam passando no teste de bancada existente
   (`finishLock.rehearsal.test.ts`) sem alteração de comportamento.
7. Suítes completas (`pytest test/`, `npx vitest run`, `npx tsc --noEmit`) permanecem 100% verdes, com
   casos novos cobrindo especificamente: (a) segunda execução sem espera pronta completando a contagem
   inteira; (b) `countdown_sec=0` continuando instantâneo; (c) rotina de estágio individual continuando
   com contagem completa; (d) a hipótese secundária da seção 1.5, confirmada ou refutada com teste.

## 5. Procedimento de validação obrigatório

Tudo em `--no-fly`, sem o driver conectado a um drone real, a menos que o usuário autorize
explicitamente voo real para uma validação final pontual. Nunca comandar motores.

1. Testes automatizados (backend e frontend) verdes, incluindo os casos novos do item 7 acima.
2. Pelo menos 3 ciclos consecutivos em bancada, com um script de bancada dedicado (nos moldes de
   `scripts/bench_rehearsal.py` já existente), medindo o tempo real entre `mission.start` e o primeiro
   `[STEP 2` (ou a decolagem simulada) em cada ciclo, e confirmando que esse intervalo nunca é menor que
   `countdown_sec` configurado:
   - Ciclo 1: Iniciar → confirmar contagem completa → aguardar o fim ou abortar → Finalizar.
   - Ciclo 2: Iniciar imediatamente após o Ciclo 1 (de propósito, sem esperar a espera reaquecer) →
     confirmar que a contagem AINDA É COMPLETA, mesmo indo pelo caminho frio.
   - Ciclo 3: repetir, desta vez esperando a espera reaquecer antes de clicar, confirmando o caminho
     quente também continua correto.
3. Apresentar um resumo claro: causa raiz confirmada, qual das direções da seção 3.1 foi escolhida e por
   quê, o que mudou em cada arquivo, e os resultados do item 2 acima (tabela com os 3 ciclos).
