# MEGA-PROMPT — BMG Mission Experience Upgrade — Implementação Completa

> Cole este prompt inteiro no terminal do Claude Code, dentro do repositório
> `mvp_mission_bebop`. Ele assume que você seguirá o `CLAUDE.md` do
> workspace e a spec de arquitetura referenciada abaixo como fontes de
> verdade.

## 0. Contexto obrigatório — leia antes de tocar em qualquer arquivo

1. Leia `CLAUDE.md` (na raiz do workspace) por completo — ele define regras
   mandatórias de engenharia, estilo, e o protocolo de handover. Siga-o à
   risca (tolerância zero a emojis/comentários redundantes, type hints
   estritos, Conventional Commits, `--symlink-install`, etc.).
2. Leia a spec de arquitetura por completo antes de escrever qualquer
   código: `docs/superpowers/specs/2026-09-24-bmg-mission-experience-design.md`.
   Ela é a fonte de verdade para TODAS as decisões de design deste
   prompt — se algo aqui parecer ambíguo, a spec resolve a ambiguidade.
3. Regra de ouro do `CLAUDE.md`: antes de qualquer implementação de
   controle/visão, inspecione `nectar-sdk` (localizado em
   `/home/joaomoreira/ros2_ws/src/nectar-sdk` nesta máquina) para reutilizar
   classes/abstrações existentes em vez de reinventar.
4. Check-in obrigatório: `git status`, depois rode a suite de testes atual
   para confirmar baseline verde antes de começar:
   ```bash
   python3 -m pytest test/
   ```
5. Trabalhe em um branch de feature dedicado (não em `main` diretamente):
   ```bash
   git checkout -b feat/mission-experience-upgrade
   ```

## Regras gerais (aplicam-se a TODAS as tasks abaixo)

- Sem emojis em código, comentários, logs, commits ou docs.
- Comentários só para justificar física de controle, equações cinemáticas,
  restrições de hardware do Bebop 2, ou acordos de sincronismo IPC — nunca
  comentários óbvios ("cria variável", "faz loop").
- Python: type hints estritos (`Optional`, `Union`, `Tuple`, `Dict`,
  `Final`), docstrings estilo NumPy/reST onde o `nectar-sdk` já usa esse
  padrão.
- TypeScript: `strict: true`, sem `any` não justificado.
- Cada task abaixo termina em um commit atômico próprio, Conventional
  Commits, em inglês técnico (`feat(...)`, `fix(...)`, `refactor(...)`,
  `test(...)`).
- Rode o comando de teste indicado em cada task antes de considerá-la
  concluída. Se um teste não existir ainda para a mudança, escreva-o
  primeiro (TDD onde fizer sentido) e então implemente.
- Não implemente nada listado em "Fora de escopo" na spec (§6): algoritmos
  de controle de voo, filtros de pose, detecção ArUco/AprilTag em si, ou
  troca de provedor de TTS.

---

## FASE 1 — BACKEND (Python / ROS 2)

### Task 1.1 — Flag de reveal de bounding box

**Arquivos**: `mvp_mission_bebop/mvp_mission_bebop/context.py`,
`mvp_mission_bebop/mvp_mission_bebop/steps/search.py`.

**O que fazer**:
- Adicione `detection_reveal_enabled: bool = False` a `MissionContext`
  (provavelmente um dataclass ou classe simples — siga o padrão dos campos
  já existentes como `rtl_completed`).
- Em `publish_annotated_stream()` (`context.py`, linha ~209), antes de
  chamar `self.detector.draw_detections(...)`, verifique a flag: se
  `False`, publique o frame cru (`image`) sem anotação no mesmo tópico de
  detecções (`params.network.detection_stream_topic`), preservando o
  overlay de crosshair/status-bar existente se ele não depender das caixas.
- Em `ForwardSearchStep.execute()` (`steps/search.py`), no ponto em que a
  varredura efetivamente começa (marco "varredura iniciada" — ver Task 1.2
  para o log correspondente), sete `ctx.detection_reveal_enabled = True`
  **apenas no marco de sinistro detectado**, não no início da varredura —
  releia a tabela de sincronismo (spec §4, linha 5): a caixa só aparece
  quando a primeira detecção válida é enfileirada, não quando a varredura
  simplesmente começa. Ajuste a flag no ponto exato em que a primeira
  detecção com confiança acima do threshold ocorre.

**Critério de aceite**: com `detection_reveal_enabled=False`, o tópico de
detecções publica frames idênticos aos frames crus (sem caixas desenhadas);
assim que a flag vira `True`, caixas aparecem nos frames seguintes sem
troca de tópico/resolução.

**Teste**:
```bash
python3 -m pytest test/test_aruco_rtl.py test/test_contracts.py
```
Adicione um teste unitário novo em `test/` que instancia `MissionContext`
com a flag `False`, chama `publish_annotated_stream()` com um resultado de
detecção não-vazio, e assere que a imagem publicada é bit-idêntica (ou
hash-idêntica) ao frame de entrada; repita com `True` e assere que difere.

---

### Task 1.2 — Marcos internos de estágio (roteiro de 9+ pontos)

**Arquivos**: `mvp_mission_bebop/mvp_mission_bebop/steps/*.py`,
`mvp_mission_bebop/mvp_mission_bebop/engine/runner.py`.

**O que fazer**: hoje só existe o marcador grosso `[STEP N: NOME]` por
estágio (5 estágios). Adicione logs de marco fino DENTRO de cada step, no
formato `[MILESTONE mission.<chave>]`, usando as chaves exatas da coluna
"Fala do copiloto (pool key)" da tabela de sincronismo (spec §4):
`mission.start`, `mission.countdown_3`, `mission.takeoff`,
`mission.scan_start`, `mission.target_found`, `mission.approaching`,
`mission.capture_done`, `mission.rtl_start`, `mission.landing`. Os marcos
`mission.start`/`mission.countdown_3` são emitidos pelo processo Electron
antes mesmo do processo Python começar (ver Fase 3) — não implemente esses
dois no Python.
- `mission.takeoff`: emita citando a altitude configurada
  (`params.kinematics.target_altitude` ou equivalente já usado em
  `takeoff.py`) como parte da mensagem de log, para o frontend poder
  extrair o valor e compor a fala.
- `mission.target_found`: emita no exato ponto em que a primeira detecção
  válida é confirmada em `search.py` (mesmo ponto da Task 1.1), incluindo
  a posição/bearing relativa do alvo no payload de log se disponível.
- Mantenha os marcadores `[STEP N: ...]` existentes intactos — são um
  contrato com `main.cjs` (`context.py:354-384` menciona esse contrato;
  não quebre).

**Critério de aceite**: rodar `mission.py --no-fly --stages 1,2,3,4,5`
produz, na ordem correta, todos os marcos `[MILESTONE mission.*]`
intercalados com os `[STEP N:...]` existentes.

**Teste**:
```bash
python3 -m pytest test/test_contracts.py
```
Estenda `test_contracts.py` com uma asserção de que cada milestone-key
esperado aparece pelo menos uma vez no stdout capturado de uma run
`--no-fly --stages 1,2,3,4,5`.

---

### Task 1.3 — Auditoria de persistência de parâmetros

**Arquivos**: `mvp_mission_bebop/mvp_mission_bebop/mission.py`,
`bebop_mission_control/electron/main.cjs` (handlers `bmg:get-parameters`,
`bmg:save-parameters`), `bebop_mission_control/src/hooks/useMissionParameters.ts`.

**O que fazer**: NÃO reescreva o mecanismo — ele já parece correto (round-
trip atômico, documento completo, sem latch de `no_fly`). Faça:
1. Um teste manual controlado: editar um parâmetro exposto na UI (ex.:
   altitude), salvar, fechar e reabrir o app (ou recarregar via
   `bmg:get-parameters`), confirmar que o valor persiste.
2. Um segundo teste: editar um parâmetro, iniciar uma missão SEM salvar
   antes, e confirmar (via `--params-json` recebido pelo processo Python,
   logue-o em modo debug temporariamente se necessário) que o valor
   working (não commitado) é o que efetivamente chega ao controlador —
   isso valida o comentário em `App.tsx:launch()` sobre commitar edições
   pendentes antes de lançar.
3. Se qualquer um dos dois testes falhar, corrija apenas o ponto exato da
   falha — não refatore o resto do mecanismo.

**Critério de aceite**: ambos os testes manuais acima passam; nenhum valor
antigo/cacheado sobrevive a um ciclo editar→salvar→relançar.

**Teste**: não há comando único — documente o resultado da auditoria como
comentário no PR/commit (`test(params): document persistence audit
findings` ou similar), incluindo qualquer fix como commit separado
`fix(params): ...` se necessário.

---

## FASE 2 — PIPELINE DE VISÃO

### Task 2.1 — Confirmar YOLO ativo desde a decolagem sem regressão de FPS

**Arquivos**: `mvp_mission_bebop/mvp_mission_bebop/mission.py`,
`bebop_mission_control/streamer/mjpeg_server.py`.

**O que fazer**: com a Task 1.1 aplicada, o detector já roda continuamente
e só o desenho é suprimido — confirme que isso não introduz overhead
adicional (branch condicional simples, sem custo de inferência extra).
Meça FPS do stream MJPEG em bancada antes e depois da mudança usando o
indicador já existente em `OpticalFeed.tsx` (chip `YOLO`/`sem YOLO` + FPS
em âmbar se `< 8`).

**Critério de aceite**: FPS estável `>= 30` durante todo o voo (decolagem
→ pouso) tanto com a caixa oculta quanto revelada — sem degradação visível
na transição.

**Teste**: rodar bancada com `--stages 1,2,3` e observar o painel de FPS
manualmente (não há teste automatizado de FPS — registre a observação no
commit).

---

## FASE 3 — FRONTEND / UI

### Task 3.1 — `copilotPhrases.ts` + histórico anti-repetição

**Arquivos novos**: `bebop_mission_control/src/lib/copilotPhrases.ts`.
**Arquivos existentes a modificar**: `bebop_mission_control/src/hooks/useCopilot.ts`
(especificamente onde `STAGE_CALLS` é usado hoje).

**O que fazer**:
- Crie `copilotPhrases.ts` com um objeto `PHRASE_POOLS: Record<MilestoneKey, string[]>`
  cobrindo as chaves da tabela de sincronismo (spec §4): `mission.start`,
  `mission.countdown_3`, `mission.takeoff`, `mission.scan_start`,
  `mission.target_found`, `mission.approaching`, `mission.capture_done`,
  `mission.rtl_start`, `mission.landing`, `inspection.intro`,
  `inspection.outro`. 3 a 5 variantes por chave, em português, tom técnico
  aeronáutico consistente com o `_format_telemetry_statement` existente em
  `announcer.py` (releia-o para manter o registro de linguagem). Para
  `mission.takeoff`, cada variante deve ter um placeholder de altitude
  (ex.: template string com `{altitude}`) já que a fala deve citar o valor
  configurado.
- Implemente `pickVariant(key: MilestoneKey, pool: string[], history: HistoryStore): { text: string; index: number }`
  que exclui índices usados nos últimos 5 voos para aquela chave
  (lidos de `history`), com fallback para o pool completo se
  `pool.length <= 5`.
- Implemente leitura/escrita de `localStorage["bmg.copilot-history.v1"]`
  (schema: `Record<MilestoneKey, number[]>`, cada array com no máximo 5
  entradas, FIFO — descarta a mais antiga ao adicionar a 6ª). Envolva
  leitura/escrita em `try/catch` (localStorage pode falhar em contexto sem
  Electron bridge — siga o padrão já usado em `useMissionParameters.ts`
  para o fallback local).
- Substitua o uso de `STAGE_CALLS` fixo em `useCopilot.ts`/`useFlightNarration`
  por chamadas a `pickVariant`.

**Critério de aceite**: rodar 6 voos consecutivos em bancada não repete a
mesma variante de `mission.takeoff` (ou qualquer chave) dentro da janela
das últimas 5 execuções, exceto quando o pool tiver 5 ou menos variantes
(nesse caso, documente o pool com pelo menos 6 variantes para as chaves
mais frequentes se possível).

**Teste**:
```bash
npm test -- copilotPhrases
```
Escreva um teste unitário (Vitest/Jest, o que já estiver configurado no
projeto — verifique `package.json`) que simula 10 chamadas sucessivas de
`pickVariant` para a mesma chave e assere que nenhuma variante se repete
dentro de qualquer janela deslizante de 5.

---

### Task 3.2 — Fila FIFO de narração (regra anti-atropelo)

**Arquivos**: `bebop_mission_control/src/hooks/useCopilot.ts` (ou onde
`useFlightNarration` estiver definido).

**O que fazer**: hoje os marcos de estágio disparam `bridge.announce()`
diretamente ao chegar o evento IPC. Substitua por uma fila FIFO interna ao
hook:
- Cada evento de milestone (`bmg:step-change` estendido com o novo payload
  de milestone da Task 1.2, ou um novo evento IPC dedicado se for mais
  limpo — decida e documente a escolha no commit) é enfileirado, nunca
  disparado diretamente.
- Um efeito consome a fila: pega o próximo item, chama
  `bridge.announce(text)`, aguarda `bmg:announce-done` (via o mecanismo já
  existente em `useCopilot.ts`, incluindo o teto `SPEECH_CEILING_MS`), só
  então processa o próximo item da fila.
- Isso garante que, mesmo que `mission.target_found` chegue antes de
  `mission.scan_start` terminar de narrar (drone detectou cedo), a ordem de
  reprodução respeita a ordem de enfileiramento, nunca pulando um marco.

**Critério de aceite**: forçar (em bancada, manipulando o `--stages` ou
timing) uma detecção muito precoce do alvo e confirmar que todas as falas
anteriores da fila (`mission.start`, `mission.countdown_3`,
`mission.takeoff`, `mission.scan_start`) tocam por completo, em ordem,
antes de `mission.target_found`, mesmo que o voo real já tenha avançado
para a aproximação nesse meio tempo.

**Teste**:
```bash
npm test -- useFlightNarration
```
Teste a fila isoladamente (mock de `bridge.announce`/`onAnnounceDone`):
enfileire eventos fora de ordem de timing (mas a função só deve processar
na ordem de chegada/enfileiramento) e assere que `announce` é chamado na
ordem correta, um de cada vez.

---

### Task 3.3 — Overlay de warm-up de bancada (10s)

**Arquivos**: `bebop_mission_control/src/App.tsx`.

**O que fazer**: no branch `if (benchMode) { setScreen('cockpit'); return; }`
(linha ~258-261), antes de trocar de tela, insira um overlay de 10s com o
texto "Carregando pipeline de vídeo e pesos YOLO..." (ou variação — não
precisa de pool de falas aqui, é só texto de UI, não fala do copiloto).
Mecanismo separado e independente do countdown de voo real — não reutilize
o componente de countdown existente, crie um componente simples dedicado
(`BenchWarmupOverlay.tsx` ou inline) para não acoplar os dois conceitos.

**Critério de aceite**: ao iniciar missão em modo bancada, o operador vê 10s
de overlay de carregamento antes de poder interagir com a tela de cabine;
voo real continua sem esse overlay (comportamento de countdown inalterado).

**Teste**: manual — iniciar missão em bancada, cronometrar o overlay.
Adicione um teste de componente (`npm test -- BenchWarmup` ou equivalente)
que confirma o overlay desmonta após 10s (fake timers).

---

### Task 3.4 — Verificar/corrigir timing do bloqueio de navegação

**Arquivos**: `bebop_mission_control/src/App.tsx` (variável `airborne`/`locked`,
linha ~134), `TabSwitch.tsx`.

**O que fazer**: primeiro, teste manualmente: clique em "Iniciar Missão"
para um voo real (ou simule via as URL query params `?screen=cockpit` /
estado interno, se não houver hardware disponível) e observe exatamente em
que ponto a aba "Início" fica desabilitada — deve ser no início do
countdown, não apenas quando o drone de fato decola. Se `running` só vira
`true` após a decolagem efetiva, ajuste a condição para também cobrir o
período de countdown (ex.: um novo estado `counting: boolean` incluído na
expressão `airborne`).

**Critério de aceite**: a aba "Início" fica bloqueada a partir do clique em
"Iniciar Missão" (início do countdown), permanece bloqueada durante todo o
voo real, e volta a ficar disponível só ao fim do voo (sucesso ou aborto
seguro) — sem regressão no comportamento de bancada (que continua sem
bloqueio, por design).

**Teste**: teste de componente/integração cobrindo os estados
`countdown → airborne → landed` e a disponibilidade da aba em cada um.

---

### Task 3.5 — Destaque visual dos 4 pontos de inspeção

**Arquivos**: `bebop_mission_control/src/components/cockpit/ForensicPanel.tsx`.

**O que fazer**: sem mover o componente (permanece na coluna atual). Reforce
apenas estilo: maior contraste no card ativo (`shown === true`), hierarquia
tipográfica mais clara entre `TOPIC_LABEL` e o texto do card, borda
ativa/glow sutil na transição de reveal (a transição `translate-y` +
opacity já existe — adicione um estado de destaque momentâneo ao entrar,
ex.: box-shadow ou borda mint mais intensa que decai após ~1-2s).

**Critério de aceite**: revisão visual manual — os 4 pontos, ao aparecerem
um a um, chamam atenção claramente sem cobrir ou deslocar outros elementos
da tela de cabine.

**Teste**: nenhum teste automatizado necessário (mudança puramente visual);
capture screenshot manual antes/depois para o PR.

---

### Task 3.6 — Filtro rígido de Wi-Fi

**Arquivos**: `bebop_mission_control/electron/main.cjs` (handler
`bmg:scan-wifi`, linha ~852), `bebop_mission_control/src/components/preflight/ConnectionSheet.tsx`.

**O que fazer**: reutilize o regex já existente `BEBOP_SSID_RE = /^Bebop2?[-_]/i`
(`main.cjs:19`). No handler `bmg:scan-wifi`, filtre (não apenas marque) a
lista retornada para conter só redes onde `BEBOP_SSID_RE.test(ssid)` é
verdadeiro, antes de enviar ao frontend. Ajuste `ConnectionSheet.tsx` para
remover qualquer lógica de exibição de redes não-Bebop (se houver alguma
renderização condicional que ainda as mostre). Atualize o texto de
placeholder (`ConnectionSheet.tsx:165`) se necessário para refletir que a
lista já vem pré-filtrada.

**Critério de aceite**: a lista de redes exibida nunca contém SSIDs fora do
padrão `Bebop-*`/`Bebop2-*`/`Bebop_*`/`Bebop2_*`, mesmo em um ambiente com
outras redes Wi-Fi por perto.

**Teste**:
```bash
npm test -- ConnectionSheet
```
Teste unitário do handler/filtro com uma lista mista de SSIDs simulada,
assertando que só os que casam o regex sobrevivem.

---

### Task 3.7 — Mapa tático: escala visual + investigação de coordenadas

**Arquivos**: `bebop_mission_control/src/components/cockpit/TacticalMap.tsx`,
`bebop_mission_control/src/lib/geo.ts`.

**O que fazer — parte (a), escala visual**:
- Aumente espessura de linha das rotas (trail path), tamanho do marcador
  `Quadcopter`, tamanho do marcador de base, e escala de fonte dos labels
  (compass, scale bar, "BASE · DECOLAGEM").
- Reexamine o sharding de subdomínio de tiles (comentário em
  `TacticalMap.tsx:174-181`) para confirmar que não há regressão de tiles
  "meio desenhados" nesse processo.

**O que fazer — parte (b), precisão de coordenadas** (requer investigação
em runtime, não só leitura estática — sinalizado na spec §5 como risco):
1. Instrumente temporariamente (log de debug) cada elo da cadeia `geo`
   (`TacticalMap.tsx`, memo `geo`, linhas ~230-244): valor de `gpsFix`, de
   `useOperatorLocation()`, de `droneEast`/`droneNorth` (último ponto de
   `track`), e o resultado de `fromEnu()`.
2. Rode em bancada com o simulador cinemático (`KinematicSimulator`)
   executando um padrão de movimento conhecido (ex.: reto por N metros) e
   compare a posição exibida no mapa com a posição esperada calculada
   manualmente a partir dos comandos de velocidade enviados.
3. Isole se o erro está na conversão ENU→lat/lon (`lib/geo.ts:fromEnu`) ou
   em `baseLatitude`/`baseLongitude` sendo stale/incorreto no momento em
   que é passado como prop.
4. Corrija a causa raiz identificada. Remova a instrumentação de debug
   antes do commit final (ou reduza a `console.debug` condicionalmente).

**Critério de aceite**: em bancada com simulador, a posição exibida no
mapa bate com a posição calculada a partir dos comandos de movimento
dentro de uma tolerância razoável (documente a tolerância obtida); mapa
visualmente legível (marcadores/rotas/labels claramente visíveis) em
resolução de tela típica de operação.

**Teste**:
```bash
npm test -- geo
```
Teste unitário de `fromEnu()` com casos conhecidos (offsets conhecidos →
lat/lon esperado, calculado independentemente, ex. via fórmula de
destino geodésico) para pegar qualquer erro de sinal/eixo/escala.

---

## FASE 4 — TTS (integração final)

> A maior parte da lógica de seleção de fala já foi implementada na Fase 3
> (Tasks 3.1, 3.2). Esta fase cobre o que resta no lado de reprodução.

### Task 4.1 — Confirmar que `announcer.py` não precisa de mudança de conteúdo

**Arquivos**: `mvp_mission_bebop/mvp_mission_bebop/telemetry/announcer.py`.

**O que fazer**: por decisão de arquitetura (spec §3.1), a seleção de fala
do roteiro de voo permanece no frontend; `announcer.py` só reproduz o texto
recebido via `serve_stdin()`. Confirme que nenhuma chamada
`announce_sync(...)` feita diretamente pelo Python (startup, falha —
`mission.py`, `engine/runner.py:_announce_failure`) foi afetada pelas
mudanças das Fases 1-3. Essas chamadas diretas podem opcionalmente também
ganhar variação simples (2-3 variantes fixas no próprio Python, sem
necessidade de histórico entre voos, já que são mensagens de erro/status
menos frequentes) — trate como melhoria opcional, não bloqueante.

**Critério de aceite**: nenhuma regressão nas mensagens de startup/falha
existentes.

**Teste**:
```bash
python3 -m pytest test/
```

### Task 4.2 — Sincronizar reveal dos 4 pontos com a fila de narração

**Arquivos**: `bebop_mission_control/src/hooks/useCopilot.ts`
(`useForensicNarration`), `ForensicPanel.tsx`.

**O que fazer**: confirme que `useForensicNarration` agora consome as
falas `inspection.point_1..4` (via `copilotPhrases.ts`, Task 3.1) através
da mesma fila FIFO da Task 3.2 (não um mecanismo paralelo), e que
`reportRevealed` incrementa exatamente ao final de cada fala de ponto —
mantendo o comportamento "later of speech-done or ~2s beat" já existente
como piso mínimo de ritmo (não remover esse beat mínimo, só garantir que
está sobre a fila unificada).

**Critério de aceite**: os 4 pontos revelam um a um, cada um só após o
áudio daquele ponto terminar (ou o beat mínimo, o que for maior), sem
depender de um mecanismo de timing separado da fila do roteiro principal.

**Teste**:
```bash
npm test -- useForensicNarration
```

---

## FASE 5 — DIAGNÓSTICO / TERMINAL

### Task 5.1 — Adicionar `node-pty` e `xterm.js`

**Arquivos**: `bebop_mission_control/package.json`,
`bebop_mission_control/electron/main.cjs`.

**O que fazer**:
```bash
cd bebop_mission_control
npm install node-pty xterm xterm-addon-fit
```
Valide que `node-pty` compila/rebuilda corretamente para a versão do
Electron em uso (pode exigir `electron-rebuild` — verifique
`package.json` scripts existentes e siga o padrão do projeto; se não
houver script de rebuild, adicione um). Documente no commit se houve
qualquer ajuste de configuração de build necessário.

**Critério de aceite**: `npm run build` (ou equivalente) conclui sem erros
com as novas dependências nativas.

**Teste**: build local + `npm run build` sem erros.

---

### Task 5.2 — Sessão PTY persistente + novo contrato IPC

**Arquivos**: `bebop_mission_control/electron/main.cjs`,
`bebop_mission_control/electron/preload.cjs` (ou onde a bridge for
exposta), `bebop_mission_control/src/components/diagnostics/DiagnosticsScreen.tsx`.

**O que fazer**:
- No processo principal, implemente `ipcMain.handle('bmg:terminal-spawn', ...)`
  que cria um processo PTY (`node-pty.spawn`) no mesmo ambiente do
  `nectar-activate` já usado para `terminalExec` hoje (reaproveite a lógica
  de ambiente existente, comentário em `DiagnosticsScreen.tsx:149-160`
  documenta o setup atual).
- `bmg:terminal-write(id, data)`: encaminha bytes crus (incluindo Tab,
  setas, Ctrl+C) ao PTY via `pty.write(data)` — **exceto** quando os bytes
  correspondem ao comando de emergência `land` completo seguido de Enter,
  que deve ser interceptado na camada de input (antes de chegar ao PTY) e
  disparar o mesmo fluxo de pouso de emergência que hoje existe como
  built-in do emulador antigo.
- `bmg:terminal-resize(id, cols, rows)`: `pty.resize(cols, rows)`.
- Evento `bmg:terminal-data(id, chunk)`: emitido a cada `pty.onData(...)`,
  consumido pelo componente React que renderiza via `xterm.js`
  (`terminal.write(chunk)`).
- `bmg:terminal-kill(id)`: `pty.kill()`.
- Reescreva `DiagnosticsScreen.tsx` para instanciar um `Terminal` do
  `xterm.js` com `xterm-addon-fit`, conectado a esse novo contrato IPC no
  lugar do emulador hand-rolled. Mantenha os painéis de log
  (mission/driver, toggle de `source`) como estavam — eles não fazem parte
  do PTY, continuam como uma view separada dentro do mesmo componente.
  Mantenha também o histórico persistido (`bmg.terminal-history.v1`) se
  fizer sentido para os built-ins que ainda existirem fora do shell real
  (ex.: `clear`/`exit` como atalhos de UI, não como comandos que passam
  pelo shell).

**Critério de aceite**: dentro do terminal de diagnóstico, digitar um
comando parcial e pressionar Tab produz autocompletar nativo do shell
(bash), idêntico ao comportamento de um terminal real; `ros2 topic list`,
histórico de comandos (setas), `Ctrl+C`, e o atalho de emergência `land`
continuam funcionando.

**Teste**: manual (terminais reais são difíceis de testar
automaticamente) — documente um roteiro de teste manual no commit:
1. Abrir diagnóstico, digitar `ros2 to` + Tab → completa para `ros2 topic`.
2. Digitar `land` + Enter → aciona pouso de emergência.
3. Redimensionar a janela → o terminal se ajusta sem quebrar o layout.
4. Rodar um comando de longa duração e matar com Ctrl+C → processo morre.

---

### Task 5.3 — Botão de fechar: círculo vermelho, canto superior esquerdo

**Arquivos**: `bebop_mission_control/src/App.tsx` (região do overlay de
diagnóstico, linhas ~621-635), `DiagnosticsScreen.tsx`.

**O que fazer**: remova o botão `X` atual no canto superior direito.
Adicione um botão circular vermelho no canto superior **esquerdo** do
modal/overlay de diagnóstico, único ponto de fechamento via UI (o
fechamento via digitar `exit` no terminal pode continuar existindo como
atalho adicional, já que a spec só exige que o fechamento via clique seja
por esse botão específico).

**Critério de aceite**: o único controle de fechar visível no header do
modal de diagnóstico é o círculo vermelho, posicionado no canto superior
esquerdo; clicá-lo fecha o overlay (`setOverlay('none')`).

**Teste**: teste de componente confirmando que o clique no botão dispara
`onClose`/`setOverlay('none')`, e snapshot/screenshot manual de
posicionamento.

---

## Validação final (end-to-end)

Depois de todas as tasks das 5 fases:

1. Suite completa:
   ```bash
   python3 -m pytest test/
   cd bebop_mission_control && npm test
   ```
2. Build de produção do frontend:
   ```bash
   cd bebop_mission_control && npm run build
   ```
3. Build ROS 2:
   ```bash
   colcon build --symlink-install --packages-select mvp_mission_bebop
   ```
4. Roteiro manual completo em bancada (`--no-fly`, todos os 5 stages),
   conferindo item a item a tabela de sincronismo da spec (§4): cada marco
   dispara a fala certa, na ordem certa, sem pular nenhum, e a caixa
   delimitadora só aparece a partir da detecção do sinistro.
5. `git status` limpo, todos os commits atômicos com Conventional Commits,
   branch pronto para revisão (`feat/mission-experience-upgrade`).

Não faça `git push` nem abra PR sem confirmação explícita do operador —
isso está fora do escopo deste prompt.
