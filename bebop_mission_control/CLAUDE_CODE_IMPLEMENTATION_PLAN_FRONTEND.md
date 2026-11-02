# BMG Frontend: Atualização Visual e Funcional Cirúrgica — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aplicar ao BMG (`bebop_mission_control/`) a barra superior global inspirada no Ubuntu 24.04, remover o Terminal, reduzir `INICIO` a três elementos, substituir a tela de contagem por uma sequência de carregamento dentro de `INICIO`, limpar o modal de Parâmetros e corrigir a `CABINE`, sem alterar nenhum contrato com o backend.

**Architecture:** Cada requisito vira uma intervenção localizada. A lógica nova entra em módulos puros pequenos em `src/lib/` (modo de voo, sequência de lançamento, escala do mapa, estado do tray), testados isoladamente, e em componentes finos que só consomem esses módulos. `App.tsx` continua sendo o único dono do estado de missão; as chamadas de API, hooks de telemetria, IPC e o schema de `mission_config.json` não mudam.

**Tech Stack:** Electron 28 + React 18 + TypeScript strict + Tailwind 3 + Vite 5 + Vitest 1.6 (jsdom) + lucide-react.

**Spec:** o texto da solicitação do usuário (sessão de 2026-10-27, "ARQUITETO FRONTEND SÊNIOR — MODIFICAÇÃO CIRÚRGICA") e a imagem da barra superior do Ubuntu 24.04 anexada a ela. Reproduzidos nas seções 0.1 e Anexo C.

---

## 0. Leia antes de tocar em qualquer arquivo

### 0.1 Regra de ouro (herdada da solicitação)

Isto **não** é refatoração. Tudo que não está citado neste plano permanece intacto: chamadas de `window.bmgAPI`, hooks de telemetria, `useMissionRuntime`, `useMissionParameters` (exceto as adições da Task 3), `launchDocument`, `parameterConflicts`, `batteryFailsafe`/`batteryReturn`, `finishLock`, narração, rotas IPC do Electron que não sejam do Terminal, formato de `mission_config.json`, marcadores `[STEP N: ...]` e `[MILESTONE ...]`. Proibido: renomear arquivos fora da lista da seção 1, reorganizar pastas, alterar tipos globais em `src/types/` além do que a Task 1 remove.

### 0.2 Protocolo de sessão (CLAUDE.md do repositório + memória do usuário)

1. Antes de começar: `git status -- bebop_mission_control` e `git log --oneline -3`. Na data deste plano a árvore do frontend estava limpa em `9321b63`. Outras sessões editam o monorepo em paralelo (arquivos Python e de teste já aparecem como modificados fora de `bebop_mission_control/`). Se algum arquivo listado na seção 1 aparecer modificado por terceiros, pare e reporte; não sobrescreva.
2. **Não executar `git commit` nem `git push` sem autorização explícita do usuário.** Os passos "Checkpoint" abaixo terminam em `git diff --stat` e numa mensagem de commit sugerida (Conventional Commits, inglês técnico, sem emojis); a execução do commit só acontece se o usuário pedir. Isso prevalece sobre o "Check-out Obrigatório" do CLAUDE.md.
3. Ambiente: `source /home/jv/ros2_ws/bin/nectar-activate` antes de qualquer comando que toque o backend. Comandos do frontend rodam em `bebop_mission_control/`.
4. Regra Black Bee (CLAUDE.md seção 2): este plano não toca controle, visão, cinemática, filtros nem interface de sensores. Nenhuma análise do `nectar-sdk` é necessária. Se um passo exigir tocar nesses domínios, pare e reporte.
5. Nenhum emoji em código, comentários, strings de UI, logs ou commits (CLAUDE.md seção 5). Onde a solicitação diz "ícone/emoji", use ícone `lucide-react`.
6. Comentários só para justificar contrato IPC, temporização ou restrição de hardware. Docstrings no estilo do arquivo vizinho.

### 0.3 Decisões de arquitetura (D1 a D13)

| # | Decisão | Motivo |
|---|---|---|
| D1 | `FlightMode = 'simple' \| 'advanced' \| 'test'` é derivado: `no_fly === true` => `test`; senão `mission_mode`. Selecionar `Teste` só faz `edit('no_fly', true)` e **não** muda `mission_mode`. Selecionar `Simples`/`Avançado` faz `await switchMode(next)` **antes** de `edit('no_fly', false)`. | `switchMode` fecha sobre `working`; inverter a ordem persiste `no_fly` com valor velho (ver `useMissionParameters.ts` `performSwitch`). `no_fly` já é o interruptor de "Bancada" hoje; nenhum campo novo no documento. |
| D2 | A barra radial de 0 a 90% soma pesos de seis checkpoints reais, os mesmos sinais que o `CountdownOverlay` usa hoje: parâmetros, enlace, gimbal, IMU, solo, visão. Pesos 10/10/15/20/15/20. Dentro do checkpoint ativo há avanço parcial limitado a 90% do peso. O percentual nunca regride. | "Acompanha a inicialização real do backend". Os sinais já existem (`mission.stage`, `mission.flat_trim`, `mission.countdown`, `mission.countdown_3`). |
| D3 | O 90% é o marco `mission.countdown_3` com todos os checkpoints concluídos. A fala é a mesma que hoje: a linha reservada de `mission.countdown_3` do `NarrationQueue`. O texto em tela "Iniciando missão" é só rótulo. 90% a 100% interpola sobre a duração medida da fala; 100% quando a fala termina **e** a contagem do backend chega a zero. | Não muda o que o copiloto fala nem quando. Só passa a observar início e fim da fala. |
| D4 | **Dependência de contrato do takeoff.** O backend decola em `launch_at_ms + kinematics.countdown_sec` (`engine/launch.py:launch_deadline`), com a chamada de liberação 3,0 s antes (`steps/takeoff.py:_CLEARANCE_ANNOUNCE_SEC`). O frontend não segura o takeoff. O modo padrão deste plano ("deadline") alinha o fim da fala ao zero da contagem dentro da tolerância da duração da fala (a frase típica dura cerca de 3 s). A sincronia **exata** exigiria o gate do Anexo B (mudança aditiva em Python). **DECIDIDO pelo usuário (2026-10-27): modo deadline, sem backend. O Anexo B fica fora de escopo e só vira plano separado se o usuário pedir.** | Honestidade de contrato: o takeoff exato no "fim da fala" não é cumprível só no renderer; o usuário aceitou a tolerância. |
| D5 | `ESC` e `Ctrl+C` cancelam o preparo em `INICIO` reutilizando o canal que já existe para a `CABINE`: `bridge.setCockpitAbortArmed` + `bridge.onAbortShortcut`, com fallback `keydown` quando não há bridge. O hook publica `false` ao desarmar **exceto** quando a próxima dona é a cabine. | Evita novo IPC. A corrida de efeitos pai/filho na transição é o risco real (Review Focus 1). |
| D6 | Durante a sequência ficam desabilitados: botão de modo, Parâmetros, pílula `INICIO/CABINE`, Wi-Fi (conectar), ROS2 (Iniciar/Parar) e bateria (toggle e slider). Volume continua ativo. | A solicitação pede "demais interações desabilitadas". O limiar de bateria já foi enviado no clique; editá-lo depois mentiria. Volume não altera a missão. **DECIDIDO pelo usuário: só o volume fica ativo.** |
| D7 | `CountdownOverlay` sai. Rotina de bancada disparada **na Cabine** (`runBenchStage`) mantém `countdown` enviado ao host (contrato de `bmg:start-bench-stage` intacto) e mostra os segundos restantes dentro da pílula da etapa ativa (prop nova `benchCountdown?: number \| null` em `StageBar`). | Sem overlay, a cabine ficaria muda por até 10 s. Compensação mínima. **DECIDIDO pelo usuário: segundos na pílula.** |
| D8 | Favorito único por modo: o hook ganha `favorite`, `saveFavorite`, `restoreFavorite`. Estrela preenchida quando o documento de trabalho é igual ao favorito (`activePresetId === favorite.id`). "Restaurar Padrões" reaplica o favorito (só edita `working`, não persiste); sem favorito, mantém o comportamento atual (padrões de fábrica). Lista legada com vários favoritos: o favorito é o de `savedAt` mais recente; o primeiro `saveFavorite` reduz a lista a um item. | Mantém a chave `bmg.operator-presets.v2.<mode>`; nenhum dado é apagado até o operador salvar. **DECIDIDO pelo usuário: estrela preenchida só quando os valores atuais são iguais ao favorito.** |
| D9 | `StatusBar.tsx` se dissolve em componentes de tray sob `src/components/shell/tray/`. `ConnectionSheet` deixa de ser aberto (o link "Detalhes do enlace" some) e é removido junto com seu teste. | Sem ponto de entrada, vira código morto. **DECIDIDO pelo usuário: remover tudo.** Glifo do ROS 2: SVG próprio em grade de pontos (**DECIDIDO**). |
| D10 | `OpticalFeed`: remove do canto superior direito as barras de sinal (RF) **e o chip de bateria** (a bateria está na barra superior global, também na Cabine; **DECIDIDO pelo usuário**). Estado offline mostra só o glifo e "Sem sinal óptico"; as mensagens por estado migram para `title`. | A mensagem `no-bridge` cita "Diagnóstico", que deixa de existir; reescrever sem essa referência. |
| D11 | Escala do mapa: barra de comprimento-alvo fixo (96 px) e rótulo calculado a 2 algarismos significativos, em vez de passos 1-2-5. Cada passo de zoom (0,5 nível, razão 1,41) produz rótulo distinto. | `niceGridStep` repete o mesmo valor em níveis consecutivos; é a causa do defeito. |
| D12 | O diálogo de descarte existente (`DiscardChangesDialog`) é reaproveitado. `ESC` no modal chama `closeParams()` já existente; `ESC` com subdiálogo aberto fecha só o subdiálogo (marcador `data-nested-dialog`). | Reuso. Sem alterar a regra de "sujo". |
| D13 | Terminal sai por inteiro: componentes, libs, IPC, preload, tipos, dependências (`node-pty`, `@xterm/*`) e o script de `postinstall`. `getDiagnostics` e `getLogHistory` ficam (não são Terminal). | Pedido explícito de remoção completa. |

### 0.4 Fontes consultadas e lacunas

- Código lido: `src/App.tsx`, `components/{shell,preflight,cockpit,diagnostics}/*`, `hooks/{useLink,useMissionParameters,useMissionCountdown,useVoiceLevel,useCopilot}.ts`, `lib/{missionMode,preflightGate,operatorPresets,mapView,narrationQueue,copilotPhrases}.ts`, `electron/{preload,missionStandby,milestones,abortShortcut}.cjs`, `mvp_mission_bebop/{engine/launch.py,steps/takeoff.py}`.
- T4H - Knowledge: a busca por "BMG Bebop Mission GUI frontend" não retornou documento do BMG (só documentação de WebApp e Force). Nada interno contradiz este plano; a lacuna fica registrada.
- Não verificado: estado verde/vermelho da suíte antes das mudanças. A Task 0 mede.
- Os 8 tópicos do ROS2 são `REQUIRED_TOPICS` em `electron/main.cjs` (`/bebop/odom`, `/bebop/camera/image_raw`, `/bebop/camera/camera_info`, `/bebop/states/battery`, `/bebop/cmd_vel`, `/bebop/takeoff`, `/bebop/land`, `/bebop/move_camera`) e chegam ao renderer em `LinkReadiness.topics`.

## Global Constraints

- TypeScript `strict: true`; sem `any` novo. `npm run typecheck` verde ao fim de cada task.
- Testes novos em Vitest, com o padrão dos vizinhos: `// @vitest-environment jsdom`, `createRoot` + `act`, `IS_REACT_ACT_ENVIRONMENT = true`. Não adicionar `@testing-library`.
- Idioma de UI: pt-BR, com acentuação, como o restante do app. Nomes de campo, status e termos técnicos em inglês permanecem em inglês.
- Tokens de cor existentes em `tailwind.config.js` (`abyss`, `hull`, `frost`, `haze`, `mint`, `cyan`, `ember`, `amber`, `strut`). Nenhuma cor nova, nenhum neon novo. Cyan continua reservado ao modo Teste.
- Animações respeitam `prefers-reduced-motion` (classe utilitária já usada em `globals.css`); duração entre 180 ms e 450 ms, easing `settle` ou `instrument`.
- Nenhum `.md`, comentário ou string com emoji.
- Contratos pinados que não podem quebrar: `test/test_contracts.py` (marcadores), `src/lib/bannedTerms.guard.test.ts` (palavra proibida em qualquer fonte), `modeIsolation.guard.test.ts`, `reportIpcRemoved.guard.test.ts`.
- Nenhum commit sem autorização (0.2).

## Review Focus

Entradas e condições que a solicitação implica, que nenhuma task cobriria por conta própria, em ordem de probabilidade de dar problema. Cada linha tem o teste que a fixa na task dona.

1. **Corrida de armamento do abort na passagem INICIO para CABINE.** O efeito do pai (`App`) roda depois do efeito do filho (`CockpitScreen`); um `setCockpitAbortArmed(false)` tardio do hook de INICIO desarma o abort do voo. Esperado: após o takeoff a última chamada é `true` (Task 11).
2. **Cancelar durante o commit do documento, antes do spawn.** `ESC` no meio de `commitLaunchDocument` não pode deixar o processo subir depois (Task 11).
3. **Dupla ativação.** Duplo clique em `Iniciar`, ou `Iniciar` enquanto a barra está fundida, deve iniciar um voo só (Task 11).
4. **Teste -> Avançado com `mission_mode` já `advanced`.** Só `no_fly` muda e não há `switchMode` em vão; a ordem de chamadas não pode persistir `no_fly: true` (Task 2).
5. **Fala não ouvida** (copiloto mudo, sem bridge ou sem voz). `onDone(false)` chega na hora; a barra 90 a 100% deve usar a duração estimada em vez de saltar a 100% (Task 10).
6. **Volume em 0.** Ícone mudo, copiloto efetivamente mudo, e subir o slider desmuta (Task 8).
7. **Wi-Fi do Bebop presente mas aeronave sem responder.** O disparo automático dos nós não pode ignorar o `Parar` manual do operador (Task 8).
8. **Zoom contínuo (roda/pinça) do mapa.** O rótulo da escala acompanha sem repetir e sem `NaN` nos extremos do intervalo (Task 5).
9. **Lista de favoritos legada com vários itens.** A estrela reflete o mais recente; nada é apagado antes do primeiro `saveFavorite` (Task 3).
10. **`ESC` com subdiálogo aberto no modal de Parâmetros.** Fecha só o subdiálogo, nunca o modal inteiro (Task 4).

---

## 1. Mapa de arquivos

Todos os caminhos relativos a `bebop_mission_control/`.

**Criar**

| Arquivo | Responsabilidade |
|---|---|
| `src/lib/flightMode.ts` | `FlightMode`, descritores, `readFlightMode`, `parametersEnabled` |
| `src/lib/launchSequence.ts` | Pesos, estados dos checkpoints, `deriveLaunchView` (puro) |
| `src/lib/speechTiming.ts` | `estimateSpeechMs`, leitura e gravação da duração medida da fala |
| `src/lib/trayState.ts` | `volumeLevel`, `ros2Tone`, `topicLabel`, `sortNetworks` (puro) |
| `src/hooks/useLaunchSequence.ts` | Relógio da sequência, regra de "pronto", `LaunchView` |
| `src/hooks/useLaunchAbortShortcut.ts` | ESC/Ctrl+C em INICIO sobre o canal de abort existente |
| `src/components/shell/TopBar.tsx` | Barra global: logo, pílula de telas, tray |
| `src/components/shell/glyphs.tsx` | `Ros2Glyph`, `WifiGlyph`, `BatteryGlyph` (movidos/novos) |
| `src/components/shell/tray/TrayButton.tsx` | Botão do tray + `useFlyout` + contêiner do popover |
| `src/components/shell/tray/Ros2Tray.tsx` | Popover dos 8 tópicos e Iniciar/Parar |
| `src/components/shell/tray/WifiTray.tsx` | Popover de redes |
| `src/components/shell/tray/VolumeTray.tsx` | Popover do slider de volume |
| `src/components/shell/tray/BatteryTray.tsx` | Popover do failsafe de bateria |
| `src/components/preflight/ControlDock.tsx` | Par Parâmetros + Modo que se funde em "Abortar Missão" |

**Modificar (cirurgicamente)**

`src/App.tsx`, `src/components/shell/TabSwitch.tsx`, `src/components/preflight/{PreflightScreen,LaunchDial,MissionModeSelector,ParameterSheet,AdvancedSettingsDialog,DiscardChangesDialog,ParameterConflictDialog}.tsx`, `src/components/cockpit/{OpticalFeed,StageBar,TacticalMap,ForensicPanel,CockpitScreen}.tsx`, `src/hooks/{useMissionParameters,useLink,useCopilot}.ts`, `src/lib/{preflightGate,operatorPresets,mapView,narrationQueue}.ts`, `src/types/bmg.ts`, `src/styles/globals.css`, `electron/{main,preload}.cjs`, `package.json`, `package-lock.json`. Testes correspondentes só onde a asserção descreve comportamento que mudou por pedido.

**Remover**

`src/components/diagnostics/*`, `src/components/shell/StatusBar.tsx`, `src/components/preflight/{ConnectionSheet,CountdownOverlay,FavoritesMenu}.tsx` e seus `.test.tsx`, `src/lib/{terminalInput,terminalTitle}.ts` e testes, `src/lib/terminalHost.test.ts`, `electron/{terminal.cjs,terminal.d.cts,terminal-rc.bash}`.

**Intocado (conferir ao final, Anexo A):** `electron/{missionStandby,missionLifecycle,milestones,launchReadiness,launchRefusal,abortShortcut,benchCountdown}.cjs`, `src/lib/{launchDocument,parameterConflicts,parameterSchema,batteryFailsafe,batteryReturn,finishLock,copilotPhrases,spokenCatalog,forensics}.ts`, `src/hooks/{useMissionRuntime,useTelemetry,useMissionCountdown,useVoiceLevel,useCameraTilt,useStreamHealth}.ts`, `mvp_mission_bebop/**`.

---

## 2. Ordem de execução

`T0 -> T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10 -> T11 -> T12 -> T13`

Cada task deixa `npm run typecheck` e a suíte do que tocou verdes. T1 vem cedo porque remove superfície (e o botão de Diagnóstico do cabeçalho que a T8 apaga de vez). T9 a T11 formam o bloco de maior risco e entram depois da barra global.

---

### Task 0: Linha de base

**Files:** nenhum (só leitura).

- [ ] **Step 1: Confirmar árvore** — `git status -- bebop_mission_control` deve sair vazio; registrar `git log --oneline -1`.
- [ ] **Step 2: Typecheck** — Run: `npm run typecheck` (em `bebop_mission_control/`). Expected: sem erros. Se houver, anotar e não corrigir (fora de escopo).
- [ ] **Step 3: Suíte completa** — Run: `npx vitest run --no-file-parallelism 2>&1 | tail -40`. Registrar a contagem de falhas e quais arquivos. Qualquer falha anterior é "pré-existente" e não bloqueia, mas não pode aumentar.
- [ ] **Step 4: Build** — Run: `npm run build`. Expected: conclui.
- [ ] **Step 5: Checkpoint** — Sem alterações. Reportar o baseline ao usuário em uma linha.

---

### Task 1: Remover o Terminal por completo

**Files:**
- Remove: `src/components/diagnostics/DiagnosticsScreen.tsx`, `DiagnosticsScreen.test.tsx`, `DiagnosticsOverlayHeader.tsx`, `DiagnosticsOverlayHeader.test.tsx`, `src/lib/terminalInput.ts`, `terminalInput.test.ts`, `src/lib/terminalTitle.ts`, `terminalTitle.test.ts`, `src/lib/terminalHost.test.ts`, `electron/terminal.cjs`, `electron/terminal.d.cts`, `electron/terminal-rc.bash`
- Modify: `src/App.tsx`, `src/components/preflight/PreflightScreen.tsx`, `src/components/cockpit/CockpitScreen.tsx`, `src/types/bmg.ts`, `electron/main.cjs`, `electron/preload.cjs`, `src/styles/globals.css`, `package.json`, `package-lock.json`
- Test: `src/components/cockpit/CockpitScreen.test.tsx`, `src/components/cockpit/AbortControl.test.tsx`, `src/components/preflight/PreflightScreen.test.tsx`

**Interfaces:**
- Consumes: nada.
- Produces: `Overlay` deixa de existir em `App.tsx`; `PreflightScreen` perde a prop `onOpenDiagnostics`; `BmgBridge` perde `terminalSpawn/Write/Resize/Kill` e `onTerminalData/Exit`; `isTextInputOrTerminal` em `CockpitScreen.tsx` passa a se chamar `isTextInput` (mesma assinatura, sem o ramo `.xterm`).

- [ ] **Step 1: Mapear referências** — Run: `grep -rniE "terminal|xterm|node-pty|Diagnostics|onOpenDiagnostics|createLandInterceptor" src electron index.html package.json vite.config.ts`. Conferir que o resultado coincide com a lista de arquivos acima; qualquer arquivo extra entra neste task.
- [ ] **Step 2: Ajustar os testes que descrevem o Terminal** — Em `CockpitScreen.test.tsx` e `AbortControl.test.tsx`, trocar as asserções sobre o foco no terminal por: "Ctrl+C dentro de um `<input type=\"text\">` não aborta; dentro de um `<input type=\"range\">` aborta" (comportamento que continua valendo). Test names: `isTextInput ignores text fields`, `isTextInput lets range sliders abort`. Em `PreflightScreen.test.tsx`, apagar a asserção do botão "Abrir diagnóstico". Run: `npx vitest run src/components/cockpit src/components/preflight/PreflightScreen.test.tsx`. Expected: falha só onde o código ainda referencia o Terminal.
- [ ] **Step 3: Remover os arquivos da lista "Remove"** — `git rm` dos doze arquivos.
- [ ] **Step 4: Editar `App.tsx`** — Remover os imports de `DiagnosticsScreen` e `DiagnosticsOverlayHeader`, o tipo `Overlay`, o estado `overlay`/`setOverlay`, o `setOverlay('none')` de `endCycle`, a prop `onOpenDiagnostics` e o bloco JSX `overlay !== 'none'`.
- [ ] **Step 5: Editar `PreflightScreen.tsx`** — Remover o import de `TerminalSquare`, a prop `onOpenDiagnostics` e o botão "Abrir diagnóstico" do `<header>`. (O cabeçalho inteiro é reescrito na Task 8; aqui só some o botão.)
- [ ] **Step 6: Editar `CockpitScreen.tsx`** — Renomear `isTextInputOrTerminal` para `isTextInput`, remover a linha `target.closest('.xterm, [data-terminal], [data-terminal-title], textarea')` mantendo `textarea` via `tag === 'TEXTAREA'` (já tratado antes). Atualizar o docblock (sem menção a terminal) e o comentário do efeito de fallback. Exportação e uso interno atualizados.
- [ ] **Step 7: Editar `electron/preload.cjs`** — Remover o bloco "Diagnostics terminal" (`terminalSpawn`, `terminalWrite`, `terminalResize`, `terminalKill`) e `onTerminalData`, `onTerminalExit`.
- [ ] **Step 8: Editar `electron/main.cjs`** — Remover `require('./terminal.cjs')`, `TERMINAL_HOME`, o bloco "IPC: diagnostics terminal" (`createTerminalHost`, os quatro `ipcMain.handle('bmg:terminal-*')`) e `terminalHost.killAll()` do teardown em torno de L3279. **Não** remover `bmg:get-diagnostics` nem `bmg:get-log-history`. Ajustar o comentário de L199 a L204 (cita "Diagnostics") para falar só do espelhamento no terminal que lançou o app; o comentário de L891 que cita "terminal included" perde essa menção.
- [ ] **Step 9: Editar `src/types/bmg.ts`** — Remover `TerminalSpawnResult`, `TerminalIdentity`, `TerminalDataEvent`, `TerminalExitEvent` e os seis membros de `BmgBridge`. Manter `DiagnosticsReport`, `getDiagnostics`, `getLogHistory`, `onMissionLog`, `onDriverLog`.
- [ ] **Step 10: Editar `globals.css`** — Remover o bloco do cursor do terminal (L190) e qualquer regra `.xterm` ou import de `@xterm/xterm/css`.
- [ ] **Step 11: Dependências** — Run: `npm uninstall node-pty @xterm/xterm @xterm/addon-fit @electron/rebuild`. Em `package.json` remover os scripts `rebuild:native` e `postinstall`. Conferir que `@electron/rebuild` não é referenciado em outro lugar (`grep -rn "electron-rebuild" . --include=*.json --include=*.cjs --include=*.sh -l` fora de `node_modules`).
- [ ] **Step 12: Verificar** — Run: `grep -rniE "terminal|xterm|node-pty" src electron package.json` (os únicos hits aceitáveis são comentários que descrevem o "terminal que lançou o app" em `main.cjs`/`missionLogFile.cjs`). Run: `npm run typecheck && npx vitest run`. Expected: verde, com contagem de testes menor pelos arquivos removidos.
- [ ] **Step 13: Checkpoint** — `git diff --stat`. Mensagem sugerida: `refactor(bmg-ui): remove diagnostics terminal and pty dependencies`.

---

### Task 2: Modo de voo em três opções (INICIO, itens 3 e parte do 4)

**Files:**
- Create: `src/lib/flightMode.ts`, `src/lib/flightMode.test.ts`
- Modify: `src/components/preflight/MissionModeSelector.tsx` (reescrita interna, mesmo arquivo e mesmo nome de export), `src/components/preflight/MissionModeSelector.test.tsx`, `src/components/preflight/PreflightScreen.tsx`, `src/App.tsx`
- Test: `src/App.launchParams.test.tsx` (acrescentar casos de seleção de modo)

**Interfaces:**
- Consumes: `MissionMode`, `readMissionMode` de `src/lib/missionMode.ts`; `getPath` de `src/lib/paths.ts`.
- Produces:
  - `export type FlightMode = MissionMode | 'test'`
  - `export const NO_FLY_PATH = 'no_fly'`
  - `export const FLIGHT_MODES: ReadonlyArray<{ id: FlightMode; label: string }>` na ordem `simple`, `advanced`, `test` com rótulos `Simples`, `Avançado`, `Teste`
  - `export function readFlightMode(doc: unknown): FlightMode`
  - `export function parametersEnabled(mode: FlightMode): boolean` (falso só para `test`)
  - `MissionModeSelector` props: `{ value: FlightMode; onChange: (mode: FlightMode) => void; disabled?: boolean }`
  - `App`: `selectFlightMode(next: FlightMode): Promise<void>`

- [ ] **Step 1: Write the failing test `src/lib/flightMode.test.ts`**
  - `readFlightMode returns test when no_fly is true regardless of mission_mode` (`{ no_fly: true, mission_mode: 'simple' }` => `'test'`)
  - `readFlightMode falls back to mission_mode when no_fly is false or absent` (`'simple'`, `'advanced'`; documento vazio => `'advanced'`, o default de `DEFAULT_MISSION_MODE`)
  - `readFlightMode treats a non-boolean no_fly as not test` (`no_fly: 'true'` => modo do `mission_mode`)
  - `FLIGHT_MODES labels are Simples, Avançado, Teste in that order`
  - `parametersEnabled is false only for test`
- [ ] **Step 2: Run** — `npx vitest run src/lib/flightMode.test.ts`. Expected: FAIL, módulo inexistente.
- [ ] **Step 3: Implement `src/lib/flightMode.ts`** conforme as assinaturas acima, no estilo de `missionMode.ts` (docblock curto, sem emojis). `readFlightMode` usa `getPath(doc, NO_FLY_PATH) === true`.
- [ ] **Step 4: Run** — mesmo comando. Expected: PASS.
- [ ] **Step 5: Reescrever o teste do seletor** (`MissionModeSelector.test.tsx`), mantendo o padrão do arquivo. Test names e asserções:
  - `shows the selected mode label on the trigger` (valor `advanced` => o botão com `aria-haspopup="listbox"` tem texto `Avançado`)
  - `lists Simples, Avançado and Teste when opened` (após clique, `[role="option"]` textos nessa ordem)
  - `marks the selected option with aria-selected`
  - `reports the picked mode and closes` (`onChange` com `'test'`, listbox some)
  - `does not report the already selected mode`
  - `closes on Escape and returns focus to the trigger`
  - `is inert when disabled` (clicar não abre; `aria-disabled="true"`)
  - `moves selection with ArrowDown/ArrowUp and wraps`
- [ ] **Step 6: Run** — Expected: FAIL.
- [ ] **Step 7: Implement `MissionModeSelector`** — botão-gatilho mostrando o rótulo do modo atual com um chevron (`ChevronDown` do lucide), lista `role="listbox"` abaixo (abre para cima se não couber), fecha em clique fora, `Escape` e seleção. Sem linha de resumo (`summary`) sob o botão. O botão tem a mesma altura e o mesmo estilo de pílula do botão Parâmetros (`rounded-full border px-5 py-2 font-cond text-sm`), para que o `ControlDock` (Task 11) os junte sem diferença visual.
- [ ] **Step 8: Run** — Expected: PASS.
- [ ] **Step 9: Test de integração em `App.launchParams.test.tsx`** — usando o harness do arquivo, três casos:
  - `selecting Teste sets no_fly true and keeps mission_mode` (mission_mode `advanced` => documento de trabalho `no_fly: true`, `mission_mode: 'advanced'`, nenhuma chamada a `saveParameters` com `no_fly` ainda `true` e `mission_mode` trocado)
  - `selecting Simples from Teste switches mode before clearing no_fly` (a chamada de persistência de `switchMode` ocorre antes da edição; ao final `working.no_fly === false`, `mission_mode === 'simple'`)
  - `selecting Avançado from Teste when mission_mode is already advanced only clears no_fly` (zero chamadas de `switchMode`)
- [ ] **Step 10: Run** — Expected: FAIL.
- [ ] **Step 11: Implement em `App.tsx`** — `flightMode = readFlightMode(params.working ?? params.committed)`. `selectFlightMode(next)`: se `next === 'test'`, `params.edit(NO_FLY_PATH, true)`; senão, se `readMissionMode(doc) !== next` fazer `await params.switchMode(next)`, e em seguida, se o documento de trabalho ainda estiver com `no_fly` verdadeiro, `params.edit(NO_FLY_PATH, false)`. Ignorar chamada com `modeLocked`. Passar `flightMode` e `onFlightModeChange={selectFlightMode}` ao `PreflightScreen`. O `onModeChange` antigo some da interface do `PreflightScreen`.
- [ ] **Step 12: Implement em `PreflightScreen.tsx`** — Remover o interruptor "Bancada" (o `<button role="switch">`, o ícone `Wrench` e o título longo). O botão de parâmetros passa a ter o texto `Parâmetros`, `disabled={!parametersEnabled(flightMode)}` e `title` somente quando desabilitado (`Sem parâmetros no modo Teste`). `MissionModeSelector` passa a receber `value={flightMode}`. O slot `above` do `LaunchDial` deixa de ser usado; o seletor entra no slot `beneath` ao lado do botão Parâmetros (o dock formal vem na Task 11, aqui basta uma linha com os dois). Se `sheet === 'params'` e o modo virar `test`, fechar o modal.
- [ ] **Step 13: Run** — `npx vitest run src/lib src/components/preflight src/App.launchParams.test.tsx && npm run typecheck`. Expected: PASS. Atualizar em `PreflightScreen.test.tsx` as asserções do interruptor "Bancada" para o novo comportamento (`Parâmetros` desabilitado em `Teste`).
- [ ] **Step 14: Checkpoint** — `git diff --stat`. Sugerido: `feat(bmg-ui): three-option flight mode selector with test mode`.

---

### Task 3: Favorito único por modo (base do item 5)

**Files:**
- Modify: `src/lib/operatorPresets.ts`, `src/hooks/useMissionParameters.ts`
- Test: `src/lib/operatorPresets.test.ts` (criar; hoje as asserções de presets estão em `src/components/preflight/ParameterPreset.test.tsx`), `src/hooks/useMissionParameters.test.tsx`

**Interfaces:**
- Consumes: `OperatorPreset`, `addPreset`, `applyPresetValues`, `readPresets`, `writePresets`, `presetValues`.
- Produces em `operatorPresets.ts`:
  - `export const FAVORITE_NAME = 'Favorito'`
  - `export function latestPreset(presets: readonly OperatorPreset[]): OperatorPreset | null` (maior `savedAt`; empate pela ordem da lista)
  - `export function singleFavorite(doc: unknown, id: string, now?: Date): OperatorPreset[]` (lista de um item, nome `FAVORITE_NAME`, valores de `presetValues(doc)`)
- Produz em `useMissionParameters()`: `favorite: OperatorPreset | null`, `saveFavorite: () => boolean`, `restoreFavorite: () => boolean`. As funções existentes (`savePreset`, `applyPreset`, `renamePreset`, `deletePreset`, `applyFactory`, `presets`, `activePresetId`) permanecem.

- [ ] **Step 1: Write the failing tests**
  - `operatorPresets.test.ts`: `latestPreset returns null for an empty list`; `latestPreset picks the most recent savedAt among several`; `singleFavorite returns exactly one item named Favorito with the mode-owned values of the doc`.
  - `useMissionParameters.test.tsx`: `favorite is null until saved`; `saveFavorite replaces a legacy list with a single item`; `favorite is the most recent legacy item before any save and nothing is deleted`; `restoreFavorite edits working values without persisting`; `restoreFavorite returns false without a favorite`; `favorite is per mode` (trocar de modo mostra o favorito do outro).
- [ ] **Step 2: Run** — `npx vitest run src/lib/operatorPresets.test.ts src/hooks/useMissionParameters.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** os dois helpers puros. No hook: `favorite = latestPreset(presets)` (memo). `saveFavorite()` monta `singleFavorite(working, newPresetId())`, grava com `writePresets(storage, missionMode, list)`, atualiza `presets`; retorna `false` se `working` for nulo ou a escrita falhar. `restoreFavorite()` faz `setWorking(prev => applyPresetValues(prev, favorite))` e retorna `true`; não chama `persistDocument` (a edição fica "suja" e o operador salva no modal, como hoje com "Restaurar Padrões").
- [ ] **Step 4: Run** — Expected: PASS. Rodar também `src/components/preflight/ParameterPreset.test.tsx`; ele ainda cobre o `FavoritesMenu` até a Task 4 removê-lo.
- [ ] **Step 5: Checkpoint** — Sugerido: `feat(bmg-ui): single favorite per mission mode`.

---

### Task 4: Modal de Parâmetros limpo (item 5)

**Files:**
- Modify: `src/components/preflight/ParameterSheet.tsx`, `src/components/preflight/PreflightScreen.tsx`, `src/components/preflight/AdvancedSettingsDialog.tsx`, `src/components/preflight/DiscardChangesDialog.tsx`, `src/components/preflight/ParameterConflictDialog.tsx`
- Remove: `src/components/preflight/FavoritesMenu.tsx`
- Test: `src/components/preflight/ParameterSheet.test.tsx`, `src/components/preflight/ParameterPreset.test.tsx` (reescrever para estrela e restauração), `src/components/preflight/PreflightScreen.test.tsx`

**Interfaces:**
- Consumes: `favorite`, `saveFavorite`, `restoreFavorite`, `activePresetId` (Task 3).
- Produces: `ParameterSheetProps` troca `presets`, `onSavePreset`, `onApplyPreset`, `onRenamePreset`, `onDeletePreset` por `favoriteActive: boolean`, `hasFavorite: boolean`, `onSaveFavorite: () => void`, `onRestoreFavorite: () => void`. Atributo `data-nested-dialog` nas raízes de `AdvancedSettingsDialog`, `DiscardChangesDialog` e `ParameterConflictDialog`.

- [ ] **Step 1: Write the failing tests** (nomes e asserções)
  - `header shows only the mode name` (texto do `<h2>` é `Avançado` ou `Simples`; não existe o texto `Os ajustes que definem` nem `Modo:`; não existe `[data-plan-chips]`)
  - `shows no help text` (nenhum `p` com a classe de hint por parâmetro; nenhum `summary` de grupo; a explicação do arco do gimbal ausente; o `GimbalArc` continua desenhado)
  - `each number parameter is label, slider and numeric input` e `each boolean parameter is label and checkbox`
  - `the star is outlined without a favorite and filled when the working values match it`
  - `clicking the star calls onSaveFavorite`
  - `Restaurar Padrões calls onRestoreFavorite when a favorite exists, and falls back to factory defaults otherwise`
  - `Aruco button sits in the header, top right, labeled Aruco` (texto `Aruco`; some se `rtl.enabled` for falso; não existe o texto `Configurações Avançadas`)
  - `Escape closes the sheet directly when nothing changed` (em `PreflightScreen`)
  - `Escape asks for confirmation when there are unsaved edits` (aparece o `DiscardChangesDialog`)
  - `Escape with the Aruco dialog open closes only that dialog`
  - `Escape on the confirmation dialog keeps editing` (comportamento já existente, regressão)
- [ ] **Step 2: Run** — `npx vitest run src/components/preflight`. Expected: FAIL.
- [ ] **Step 3: Cabeçalho** — `ParameterSheet.tsx`: o cabeçalho passa a ter à esquerda o `<h2>` com `modeLabel` e, imediatamente depois, o botão-estrela (`Star` do lucide; preenchida e em `mint` quando `favoriteActive`, contorno em `haze` caso contrário; `aria-pressed={favoriteActive}`; `title` curto `Salvar como favorito`); à direita o botão `Aruco` (ícone `QrCode`, variante `ghost`, mesmo `returns` de hoje). Remover `PlanChips` do cabeçalho (manter o export no arquivo; remover só o uso; `grep -rn PlanChips src` para confirmar que ninguém mais o usa e, se ninguém usar, remover o componente e o import de `launchesFromGround`/`padHeightM`).
- [ ] **Step 4: Cartões** — Em `BooleanControl` e `NumberControl` deixar de renderizar `spec.hint` e `aria-describedby` do hint. Em `GroupCard` remover o `<p>` do `group.summary` e o bloco explicativo do gimbal (manter `GimbalArc`). `MissingValue` fica com uma linha curta: `Valor ausente`. **Não** alterar `src/lib/parameterSchema.ts`: `hint` e `summary` continuam no schema para outros consumidores. Manter `ConflictNote` (título curto de conflito é alerta funcional) e o aviso `altitudeNotice`.
- [ ] **Step 5: Rodapé** — Remover o `FavoritesMenu` e o botão `Configurações Avançadas` do rodapé. "Restaurar Padrões" chama `onRestoreFavorite` se `hasFavorite`; senão executa o `restoreDefaults` atual. Remover `src/components/preflight/FavoritesMenu.tsx`.
- [ ] **Step 6: Dialog Aruco** — `AdvancedSettingsDialog`: título do diálogo passa a `Aruco`; `data-nested-dialog` no contêiner raiz. Textos de ajuda longos por campo (prop `hint` de `Field`) saem; mantém rótulo, controle e erro.
- [ ] **Step 7: ESC no modal** — `PreflightScreen.tsx`: enquanto `sheet === 'params'` e `!confirmingDiscard`, listener `keydown` em `window` (registrado no `useEffect`): `Escape` => se `document.querySelector('[data-nested-dialog]')` existir, não faz nada (o subdiálogo trata); senão `closeParams()`. `DiscardChangesDialog` e `ParameterConflictDialog` ganham `data-nested-dialog`; `DiscardChangesDialog` mantém o `Escape` => "Continuar editando".
- [ ] **Step 8: Passar as props novas** — `PreflightScreen` recebe `favoriteActive`, `hasFavorite`, `onSaveFavorite`, `onRestoreFavorite` e as repassa; `App.tsx` liga a `params.favorite`/`params.activePresetId`/`params.saveFavorite`/`params.restoreFavorite`.
- [ ] **Step 9: Run** — `npx vitest run src/components/preflight && npm run typecheck`. Expected: PASS. `ParameterPreset.test.tsx` reescrito cobre a estrela e a restauração; os testes de renomear/excluir favorito saem junto com o `FavoritesMenu`.
- [ ] **Step 10: Checkpoint** — Sugerido: `feat(bmg-ui): minimal parameters modal with single favorite and aruco tab`.

---

### Task 5: Escala dinâmica do mapa tático (item 6, Mapa Tático)

**Files:**
- Modify: `src/lib/mapView.ts`, `src/components/cockpit/TacticalMap.tsx`
- Test: `src/lib/mapView.test.ts`, `src/components/cockpit/TacticalMap.test.tsx`

**Interfaces:**
- Consumes: `pixelsPerMetre(zoom, latitude)`, `MIN_DISPLAY_ZOOM`, `MAX_DISPLAY_ZOOM`, `ZOOM_STEP`, `MAP_ANCHOR`.
- Produces: `export const SCALE_BAR_TARGET_PX = 96` e `export function scaleBar(pxPerMetre: number, targetPx?: number): { metres: number; px: number; label: string }`.

- [ ] **Step 1: Write the failing tests em `mapView.test.ts`** (`describe('scaleBar')`)
  - `labels are unique for every 0.5 zoom step from MIN to MAX at the site latitude` (laço `z = 16, 16.5, ... 22` com `LAT = MAP_ANCHOR.latitude`; `new Set(labels).size === labels.length`)
  - `labels are unique across quarter-step zoom values too`
  - `metres strictly decrease as the zoom increases`
  - `bar length stays within 90 to 110 percent of the target`
  - `label uses cm below one metre, m up to 999 m and km from 1000 m` (casos pontuais: pxPerMetre 200 => cm; 0,4 => m; 0,05 => km com vírgula decimal pt-BR)
  - `returns finite values for a non positive or non finite scale` (entrada `0`, `-1`, `NaN` => `metres: 0`, `px: 0`, `label: '—'`, sem exceção)
  - `matches the pinned values at the default framing` (fixar o rótulo para `zoom = displayZoom(OVERVIEW_EXTENT_M, {width: 880, height: 360}, LAT)` depois de calculado uma vez; serve de regressão)
- [ ] **Step 2: Run** — `npx vitest run src/lib/mapView.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement `scaleBar`** — `metres = targetPx / pxPerMetre` arredondado a **2 algarismos significativos** (ordem de grandeza por `Math.floor(Math.log10(x))`); `px = metres * pxPerMetre`; `label` via `Intl.NumberFormat('pt-BR')`: `< 1 m` em cm inteiros, `< 1000 m` em m inteiros, `>= 1000 m` em km com até 2 casas. Documentar no docblock por que não usa passos 1-2-5 (repetem valor em níveis consecutivos). Não remover `niceGridStep` (a grade o usa).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Test de componente em `TacticalMap.test.tsx`** — `scale legend updates when the zoom buttons are pressed` (clicar em zoom-in N vezes e coletar o texto da legenda; todos distintos) e `scale legend never repeats between two adjacent wheel steps`. Run: FAIL.
- [ ] **Step 6: Wire em `TacticalMap.tsx`** — substituir as três linhas `scaleMetres`, `scaleBarPx`, `scaleLabel` (L273 a L275) por `const scale = scaleBar(view.pxPerMetre)` e usar `scale.px` e `scale.label` na barra (L439 a L449), preservando o desenho. Remover `scaleMetres`. Se `niceGridStep` passar a ter um único consumidor, manter.
- [ ] **Step 7: Run** — `npx vitest run src/components/cockpit/TacticalMap.test.tsx src/lib/mapView.test.ts && npm run typecheck`. Expected: PASS.
- [ ] **Step 8: Checkpoint** — Sugerido: `fix(bmg-ui): unique tactical map scale label per zoom level`.

---

### Task 6: Cabine sem redundâncias e câmera offline (item 6)

**Files:**
- Modify: `src/components/cockpit/OpticalFeed.tsx`, `src/components/cockpit/StageBar.tsx`, `src/components/cockpit/CockpitScreen.tsx`
- Test: `src/components/cockpit/OpticalFeed.render.test.tsx`, `src/components/cockpit/OpticalFeed.test.ts`, `src/components/cockpit/StageBar.mode.test.tsx`, `src/components/cockpit/CockpitScreen.test.tsx`

**Interfaces:**
- Consumes: `feedState` e `FeedState` (inalterados).
- Produces: `StageBar` perde as props `voice`, `onVolume`, `onToggleMute`; `CockpitScreen` perde as mesmas props; `StageBar` ganha `benchCountdown?: number | null` (usada na Task 12).

- [ ] **Step 1: Write the failing tests**
  - `offline feed shows only the glyph and "Sem sinal óptico"` (para os estados `no-link`, `no-bridge` e `stalled`: o texto visível do painel é exatamente `Sem sinal óptico`; o texto `Sem enlace com a aeronave` não existe)
  - `offline feed keeps data-feed-state and exposes the per-state reason as a title` (atributo `data-feed-state` presente no elemento; `title` não vazio)
  - `no-bridge reason does not mention Diagnóstico`
  - `top right of the feed has no signal bars and no battery chip` (não existe elemento com `sr-only` "Sinal:", nem os quatro traços de barra, nem o chip de bateria; os chips do canto superior esquerdo, tempo, formato/FPS e YOLO, permanecem)
  - `stage bar has no voice control` (não existe `[aria-label="Voz do copiloto"]`)
  - `feedState` continua com os mesmos testes existentes (regressão)
- [ ] **Step 2: Run** — `npx vitest run src/components/cockpit`. Expected: FAIL.
- [ ] **Step 3: `OpticalFeed.tsx`** — Bloco offline: manter `VideoOff` e `Sem sinal óptico`; o `<p data-feed-state>` com a mensagem longa vira um contêiner sem texto, com `title={FEED_MESSAGE[state]}` e `aria-label` igual. Reescrever `FEED_MESSAGE['no-link']` para `Sem enlace com a aeronave` (curto, só tooltip) e `no-bridge` para `A ponte MJPEG na porta 9090 não respondeu.` (sem a referência a Diagnóstico). Remover o contêiner inteiro do canto superior direito (barras de sinal e `HudChip` da bateria) e os cálculos `bars`, `known`, `charge` e os imports `Battery` e `rfBars` que ficarem sem uso. `connected` continua sendo usado por `feedState` e `navFresh`.
- [ ] **Step 4: `StageBar.tsx`** — Remover o import de `VoiceControl`, as três props de voz e a `<div className="flex items-center justify-end">` que a envolve (L174 a L176); conferir que o layout da faixa continua alinhado (a coluna da direita some ou recebe o contador `benchCountdown` na Task 12). Atualizar o docblock da prop removida.
- [ ] **Step 5: `CockpitScreen.tsx`** — Remover as props `voice`, `onVolume`, `onToggleMute` e o repasse ao `StageBar`. `App.tsx`: deixar de passá-las ao `CockpitScreen` (o `voice` continua vindo de `useVoiceLevel` para o `TopBar`, Task 8).
- [ ] **Step 6: Run** — `npx vitest run src/components/cockpit && npm run typecheck`. Expected: PASS.
- [ ] **Step 7: Checkpoint** — Sugerido: `refactor(bmg-ui): drop cockpit signal, battery chip and voice control now in the top bar`.

---

### Task 7: Painel Forense polido (item 6)

Somente apresentação. Nenhuma prop, nenhum dado, nenhum texto de dado muda.

**Files:**
- Modify: `src/components/cockpit/ForensicPanel.tsx`, `src/styles/globals.css` (somente keyframes/utilitários novos, se necessários)
- Test: `src/components/cockpit/ForensicPanel.test.tsx` (só ajustar asserções que dependem de classes CSS)

**Interfaces:** Consumes e produz as mesmas props. Atributos preservados: `data-focus-state`, `data-finding-state`, `data-finish-state`, `aria-live`, `aria-busy`, `title` do botão de finalizar.

- [ ] **Step 1: Rodar o teste atual como contrato** — `npx vitest run src/components/cockpit/ForensicPanel.test.tsx`. Expected: PASS antes de mexer.
- [ ] **Step 2: Aplicar o brief visual** (um commit lógico; sem lógica nova):
  - Painel: um único contêiner `rounded-panel` com `border-strut-soft`, sem `bg-hull-deep/80` empilhado em duas camadas; cabeçalho de 40 px com título em `font-cond`, contador à direita em `text-haze-deep`, divisor de 1 px.
  - Estado de espera (`EvidenceStandby`): manter o quadro de autofoco e as animações `anim-focus-hunt`/`anim-focus-lock`; remover o `bg-abyss/70` pesado e a borda dupla; texto único `Aguardando foto da evidência` em `haze` (mint ao inspecionar).
  - Imagem: moldura de 1 px sem sombra, cantos em marcas finas (quatro `L` de 10 px em `mint/60`), legenda `capturada em` com gradiente mais curto; transição de altura `duration-[600ms] ease-settle` (hoje 900 ms) e entrada `anim-land` preservada.
  - Constatações: lista como linha do tempo (trilho vertical de 1 px em `strut`, nó circular de 8 px que acende em `mint` ao revelar; sem caixa preenchida nem `shadow-[inset_3px_0_0_#01D5A3]`); número tabular em `font-mono` `text-3xs`, rótulo do tópico em `text-mint/80`, texto do cartão `text-[15px]` `text-frost`; revelação com `translate-y-1` para `0` e opacidade em 400 ms.
  - Botão "Finalizar missão": manter os três estados e a lógica de segurar para finalizar; trocar o preenchimento sólido por contorno com hover preenchido; sem alterar `data-finish-state`.
  - Nenhuma cor nova; `prefers-reduced-motion` desliga o piscar do autofoco e a revelação deslizante.
- [ ] **Step 3: Run** — `npx vitest run src/components/cockpit/ForensicPanel.test.tsx && npm run typecheck`. Ajustar só asserções de classe CSS; nenhuma asserção de texto ou atributo pode ser enfraquecida.
- [ ] **Step 4: Verificação visual** — Abrir `npm run dev` e `http://localhost:5173/?screen=cockpit` no browser integrado; capturar três estados (espera, captura chegando, laudo revelando) com os fixtures do teste; anexar as capturas à resposta final.
- [ ] **Step 5: Checkpoint** — Sugerido: `refactor(bmg-ui): minimal forensic panel presentation`.

---

### Task 8: Barra superior global e tray (item 1)

**Files:**
- Create: `src/lib/trayState.ts`, `src/lib/trayState.test.ts`, `src/components/shell/TopBar.tsx`, `src/components/shell/glyphs.tsx`, `src/components/shell/tray/{TrayButton,Ros2Tray,WifiTray,VolumeTray,BatteryTray}.tsx` e um teste por tray (`*.test.tsx`)
- Modify: `src/components/shell/TabSwitch.tsx`, `src/components/preflight/PreflightScreen.tsx`, `src/App.tsx`, `src/hooks/useLink.ts`, `src/hooks/useLink.test.tsx` (criar se não existir), `src/App.battery.test.tsx`, `src/components/preflight/PreflightScreen.test.tsx`
- Remove: `src/components/shell/StatusBar.tsx`, `src/components/preflight/ConnectionSheet.tsx`, `src/components/preflight/ConnectionSheet.test.tsx`

**Interfaces:**
- Consumes: `TelemetryView`, `LinkReadiness`, `TopicReadiness`, `WifiNetwork`, `BatteryFailsafe`, `VoiceLevel`, `LinkPhase`, `HybridSlider`, `FAILSAFE_MIN_PCT`, `FAILSAFE_MAX_PCT`, `isBebopSsid`, `Wordmark`.
- Produces em `trayState.ts`:
  - `export type Ros2Tone = 'off' \| 'busy' \| 'ready'`
  - `export function ros2Tone(input: { driverRunning: boolean; busy: boolean; ready: boolean }): Ros2Tone`
  - `export function topicLabel(topic: string): string` (remove o prefixo `/bebop/`)
  - `export function volumeLevel(level: VoiceLevel): 0 \| 1 \| 2 \| 3` (0 quando `muted` ou `volume === 0`; 1 até 0,33; 2 até 0,66; 3 acima)
  - `export function sortNetworks(networks: readonly WifiNetwork[]): WifiNetwork[]` (rede Bebop primeiro; mantém a ordem relativa do resto)
- `TopBarProps`:
  ```ts
  interface TopBarProps {
    screen: Screen; onNavigate: (s: Screen) => void; live: boolean; locked: boolean;
    frozen: boolean;               // sequência de lançamento ativa (D6)
    telemetry: TelemetryView;
    networks: WifiNetwork[]; currentSsid: string; scanning: boolean;
    linkPhase: LinkPhase; readiness: LinkReadiness | null;
    flightReady: boolean; driverRunning: boolean;
    onScan: () => void; onConnect: (ssid: string) => void;
    onStartDriver: () => void; onStopDriver: () => void;
    failsafe: BatteryFailsafe; onFailsafeChange: (n: BatteryFailsafe) => void; failsafeTriggered: boolean;
    voice: VoiceLevel; onVolume: (v: number) => void; onToggleMute: () => void;
  }
  ```

- [ ] **Step 1: Write the failing tests de `trayState.test.ts`**
  - `ros2Tone is off when the driver is down, busy while starting, ready when topics flow`
  - `topicLabel strips the /bebop/ prefix and keeps nested paths` (`/bebop/camera/image_raw` => `camera/image_raw`)
  - `volumeLevel is 0 when muted even with a positive volume`
  - `volumeLevel boundaries` (0 => 0; 0,01 => 1; 0,33 => 1; 0,34 => 2; 0,66 => 2; 0,67 => 3; 1 => 3)
  - `sortNetworks puts Bebop networks first and keeps the rest stable`
- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement** `trayState.ts`. **Step 4: Run** — PASS.
- [ ] **Step 5: Mover glifos** — Criar `glyphs.tsx` com `BatteryGlyph` e `WifiGlyph` (copiados de `StatusBar.tsx` sem mudança de desenho) e o novo `Ros2Glyph`: grade 3x3 de pontos de 2 px em SVG de 16x16 que ecoa a grade de pontos da marca ROS sem copiá-la; props `{ tone: Ros2Tone; size?: number }`; `off` em `haze-deep`, `busy` em `amber` com `anim-breathe`, `ready` em `mint`.
- [ ] **Step 6: `TrayButton.tsx`** — Exporta `useFlyout()` (mesmo contrato de `StatusBar.tsx`: fecha em clique fora e `Escape`) e `TrayButton` (botão sem borda, `rounded-full px-2 py-1`, hover `bg-frost/[0.08]`, foco visível, `aria-expanded`, `disabled`), mais o contêiner `TrayPopover` (`absolute right-0 top-[calc(100%+6px)] z-50 rounded-panel border border-frost/12 bg-abyss/95 backdrop-blur-xl shadow-lg anim-rise`).
- [ ] **Step 7: Testes dos trays** (um arquivo por tray, padrão `createRoot`):
  - `Ros2Tray.test.tsx`: `lists the eight topics with no explanatory text` (8 linhas; cada uma com ponto `mint` se `receiving`, `amber` se `present && !receiving`, `haze-deep` se ausente); `shows Iniciar when the driver is down and Parar when it is up`; `Iniciar is disabled without a connected aircraft or while busy` (mesma regra de hoje: `!connected || busy`); `buttons are disabled when frozen`; `has no heading text beyond the ROS 2 glyph label`.
  - `WifiTray.test.tsx`: `opening the popover requests a scan`; `lists networks with the Bebop network first and a subtle highlight` (atributo `data-bebop="true"` na linha, sem o texto `DRONE`); `keeps the refresh button and disables it while scanning`; `connecting is disabled when frozen`; `has no driver section, no status message and no "Detalhes do enlace"`.
  - `VolumeTray.test.tsx`: `renders a single slider`; `icon follows volumeLevel` (mudo/baixo/médio/alto por `data-level`); `moving the slider to 0 mutes` (chama `onToggleMute` se `!muted`); `moving the slider above 0 unmutes`; `stays enabled when frozen`.
  - `BatteryTray.test.tsx`: `shows the percentage next to the glyph` (`78%`); `popover has only the Failsafe de bateria switch and the threshold slider` (sem `dl`, sem parágrafo explicativo, sem chip de nível); `switch toggles failsafe.enabled`; `slider changes thresholdPct and is disabled when failsafe is off`; `shows an ember dot instead of text after the failsafe fired`; `controls are disabled when frozen`.
- [ ] **Step 8: Run** — Expected: FAIL (componentes inexistentes).
- [ ] **Step 9: Implement os quatro trays** conforme os testes. Regras:
  - `Ros2Tray`: botão com `Ros2Glyph` e `aria-label="ROS 2"`; popover com cabeçalho de uma linha (`ROS 2` em `font-cond` à esquerda, botão `Iniciar`/`Parar` à direita, mesmas classes de botão do `WifiWidget` atual) e a lista de `readiness.topics` (nome via `topicLabel`, ponto de estado). `readiness` nulo => os 8 nomes fixos de `REQUIRED_TOPICS` não estão disponíveis no renderer; mostrar linhas vazias não é aceitável, então nesse caso a lista fica oculta e só o cabeçalho aparece.
  - `WifiTray`: botão com `WifiGlyph` apenas (sem SSID), `aria-label="Redes Wi-Fi"`, `title` com SSID e dBm como hoje; popover = cabeçalho só com o botão de atualizar (`RefreshCw`, `aria-label="Atualizar redes"`) + lista `sortNetworks`. Linha Bebop: `WifiGlyph` em `mint`, nome em `frost`, ícone `Plane` de 10 px e barra lateral de 2 px `mint/60` (destaque sutil). `onScan` ao abrir (comportamento de hoje).
  - `VolumeTray`: botão com o ícone do nível (`VolumeX` para 0; `Volume`, `Volume1`, `Volume2` para 1, 2, 3), `aria-label="Voz do copiloto"`; popover com um `<input type="range" min=0 max=100>` estilizado como o atual do `VoiceControl`. Sem botão de silenciar, sem rótulo, sem percentual.
  - `BatteryTray`: botão com `BatteryGlyph` e `{charge}%` (traço `—` se desconhecida); `aria-label="Bateria da aeronave"`; popover com a linha `Failsafe de bateria` + switch (mesmo desenho do atual) e, logo abaixo, `HybridSlider` do limiar com o valor; nada mais. O ponto de falha vira um ponto `ember anim-breathe` no próprio botão quando `failsafeTriggered`.
- [ ] **Step 10: `TabSwitch.tsx`** — Manter props e comportamento. Restyle: contêiner sem borda dupla nem sombra, `rounded-full bg-frost/[0.06] p-0.5`; rótulos `INICIO` e `CABINE` via `uppercase tracking-[0.12em]` (o texto do DOM continua `Inicio`/`Cabine`); aba ativa `bg-frost/[0.12] text-frost`; remove a posição `fixed` do chamador. Nova prop `frozen?: boolean` desabilita ambas as abas.
- [ ] **Step 11: `TopBar.tsx`** — `<header>` de largura total, altura 36 px, `bg-abyss` quase sólido (`bg-abyss/95`), sem borda superior, divisor inferior de 1 px `border-frost/[0.06]`, grade `grid-cols-[1fr_auto_1fr] items-center px-3`. Esquerda: `<Wordmark height={16} />`. Centro: `<TabSwitch ... />`. Direita: `flex justify-end gap-0.5` com `Ros2Tray`, `WifiTray`, `VolumeTray`, `BatteryTray` **nesta ordem**. Sem textos auxiliares.
- [ ] **Step 12: Layout em `App.tsx`** — Raiz passa a `flex h-full w-full flex-col bg-abyss`; `<TopBar>` primeiro, depois `<div className="relative min-h-0 flex-1">{body}</div>`. Remover a montagem `fixed` do `TabSwitch`. Construir as props do `TopBar` a partir de `telemetry`, `link`, `failsafe`, `voice`, `airborne`, `running`. `frozen` recebe `counting` (Task 11 refina).
- [ ] **Step 13: `PreflightScreen.tsx`** — Remover o `<header>` inteiro (StatusBar e placeholder), o estado `'link'` de `sheet`, a montagem de `ConnectionSheet` e as props que só serviam a eles: `networks`, `currentSsid`, `scanning`, `linkPhase`, `linkMessage`, `linkProgress`, `onScan`, `onConnect`, `onStartDriver`, `onStopDriver`, `failsafe`, `onFailsafeChange`, `failsafeTriggered`, `voice`, `onVolume`, `onToggleMute`. **Manter** `readiness`, `flightReady`, `driverRunning`, `telemetry`, `stale` e `failsafe?.thresholdPct` (alimentam `preflightBlockedReason`); como `failsafe` sai, receber `batteryThresholdPct: number` no lugar. `App.tsx` ajusta o repasse. O `<main>` mantém o centro; o `VideoBackdrop` continua cobrindo a tela.
- [ ] **Step 14: Disparo automático dos nós em `useLink.ts`** — Estender a condição do efeito de bring-up automático: dispara quando `opts.connected || isBebopSsid(currentSsid)`, mantendo as guardas existentes (`busyRef`, `manualStopRef`, `readiness?.ready`, `AUTO_RETRY_MS`). Test `useLink.test.tsx`: `starts the driver automatically when joined to a Bebop SSID even before the aircraft answers`; `does not auto start after a manual stop until a new connect`; `does not auto start on a non Bebop SSID`; `respects the retry cooldown`. Fixar o contrato: `connect()` de uma rede Bebop zera `manualStopRef` (já faz em `runEnsureLink`).
- [ ] **Step 15: Atualizar testes dependentes** — `App.battery.test.tsx`: os seletores do flyout migram para `aria-label="Bateria da aeronave"` (igual), o switch passa a ser localizado por `Failsafe de bateria`, e as asserções sobre o texto explicativo e o chip de nível saem. `PreflightScreen.test.tsx`: remover asserções de `StatusBar`/`ConnectionSheet`.
- [ ] **Step 16: Remover** `StatusBar.tsx`, `ConnectionSheet.tsx` e `ConnectionSheet.test.tsx`; `grep -rn "StatusBar\|ConnectionSheet\|VoiceControl" src` deve voltar vazio.
- [ ] **Step 17: Run** — `npx vitest run src && npm run typecheck`. Expected: PASS.
- [ ] **Step 18: Verificação visual** — `npm run dev`; comparar com a imagem do Anexo C em 1366 px de largura: altura da barra, logo à esquerda, pílula ao centro, ordem dos ícones à direita, `78%` ao lado da bateria; capturar `INICIO` e `CABINE` com a barra.
- [ ] **Step 19: Checkpoint** — Sugerido: `feat(bmg-ui): global top bar with ros2 wifi volume and battery tray`.

---

### Task 9: Núcleo puro da sequência de lançamento (item 4, parte 1)

**Files:**
- Create: `src/lib/launchSequence.ts`, `src/lib/launchSequence.test.ts`, `src/lib/speechTiming.ts`, `src/lib/speechTiming.test.ts`
- Modify: `src/lib/preflightGate.ts`, `src/lib/preflightGate.test.ts`

**Interfaces:**
- Consumes: `PreflightGateInput` e `preflightBlockedReason` de `preflightGate.ts`; `LAUNCH_COUNTDOWN_SEC` de `launchDocument.ts`.
- Produces em `launchSequence.ts`:
  ```ts
  export type LaunchStepId = 'params' | 'link' | 'gimbal' | 'imu' | 'ground' | 'vision';
  export interface LaunchStep { id: LaunchStepId; label: string; weight: number; expectedMs: number }
  export const LAUNCH_STEPS: ReadonlyArray<LaunchStep>; // pesos 10,10,15,20,15,20; rótulos Parâmetros, Enlace, Gimbal, IMU, Solo, Visão
  export const LOAD_CEILING_PCT = 90;
  export type StepState = 'done' | 'active' | 'pending' | 'skipped';
  export interface LaunchSignals {
    now: number; clickedAt: number;
    paramsCommitted: boolean; linkReady: boolean;
    stageReached: boolean; countdownStarted: boolean;
    trim: 'acked' | 'skipped' | null; clearance: boolean;
    countdownZero: boolean;                 // backend reportou 0 e o relógio do clique também
    speech: { startedAt: number | null; endedAt: number | null; heard: boolean; expectedMs: number };
  }
  export type LaunchPhase = 'loading' | 'announcing' | 'releasing' | 'done';
  export interface LaunchView {
    phase: LaunchPhase; percent: number;     // inteiro 0..100
    stepId: LaunchStepId | null; label: string | null;
    message: 'Iniciando missão' | null;
  }
  export function stepStates(s: LaunchSignals): Record<LaunchStepId, StepState>;
  export function deriveLaunchView(s: LaunchSignals, floorPct?: number): LaunchView;
  ```
- Produces em `speechTiming.ts`:
  - `export const SPEECH_MS_KEY = 'bmg.speech-ms.v1'`
  - `export function estimateSpeechMs(text: string): number` (68 ms por caractere, limitado a `[1500, 14000]`; texto vazio => 1500)
  - `export function readSpeechMs(storage: StorageLike | null, text: string): number | null`
  - `export function recordSpeechMs(storage: StorageLike | null, text: string, ms: number): void` (guarda até 32 textos; ignora `ms` fora de `[300, 14000]`; nunca lança)
- Produz em `preflightGate.ts`: `export function preflightBlockedShort(input: PreflightGateInput): string | null`.

- [ ] **Step 1: Write the failing tests de `launchSequence.test.ts`**
  - `weights of the six steps add up to LOAD_CEILING_PCT`
  - `percent is 0 right after the click and the params step is active`
  - `step order of activity: params, link, then gimbal and ground after stageReached`
  - `a skipped trim counts the full weight of the imu step` (`trim: 'skipped'` => `imu` `skipped` e o peso contado)
  - `percent never exceeds 90 while the speech has not started` (todos os checkpoints concluídos, `clearance: false`)
  - `partial credit inside the active step is capped below its weight` (peso 20 com `expectedMs` estourado dá no máximo 90% do peso)
  - `percent never decreases when floorPct is passed back` (sinal regredindo, `floorPct = 55` => `>= 55`)
  - `clearance with every checkpoint done enters announcing at 90 with message "Iniciando missão"`
  - `announcing interpolates 90..99 over expectedMs of the speech`
  - `an unheard speech uses expectedMs, not an instant jump` (`heard: false`, `endedAt` imediato, `startedAt` = agora => ainda `< 100`)
  - `100 only when speech ended and countdownZero`; `speech ended without countdownZero holds at 99 in releasing`; `countdownZero without speech end holds at 99`
  - `done phase at 100 has no label and no message`
  - `label is the first step not done, in LAUNCH_STEPS order`
- [ ] **Step 2: Tests de `speechTiming.test.ts`** — `estimate is clamped at both ends`; `estimate grows with text length`; `record then read returns the measured ms`; `record ignores implausible values`; `record caps the store at 32 texts`; `corrupt storage reads as null and never throws`; `a storage that throws on write is tolerated`.
- [ ] **Step 3: Tests em `preflightGate.test.ts`** — `preflightBlockedShort is null exactly when preflightBlockedReason is null` (varrer os mesmos casos da tabela de testes existente); e rótulos curtos, um por motivo, todos com no máximo 18 caracteres: `Parâmetros`, `Sem driver`, `Sem enlace`, `Sem tópicos`, `Sem odometria`, `Fora do solo`, `Bateria?`, `Bateria baixa`, `Sem ponte`.
- [ ] **Step 4: Run** — `npx vitest run src/lib/launchSequence.test.ts src/lib/speechTiming.test.ts src/lib/preflightGate.test.ts`. Expected: FAIL.
- [ ] **Step 5: Implement `launchSequence.ts`** — estados de checkpoint copiados da lógica de `CountdownOverlay.tsx` (que sai na Task 12): `link` conclui por `linkReady`; `params` por `paramsCommitted`; `gimbal` e `ground` por `countdownStarted`, ativos entre `stageReached` e `countdownStarted`; `imu` por `trim`; `vision` conclui por `clearance`, ativo após `countdownStarted`. Avanço parcial do ativo: `min(0.9, (now - activeSince) / expectedMs) * weight`, onde `activeSince` é o `clickedAt` para `params`/`link` e o instante do último checkpoint concluído para os demais (derivar do máximo de `clickedAt`; como a função é pura, aproximar `activeSince = clickedAt` e documentar que o parcial é monotonizado por `floorPct`). Fase `announcing` quando todos os checkpoints estão concluídos e `clearance`; progresso `90 + 9 * min(1, (now - speech.startedAt) / speech.expectedMs)` (com `startedAt` nulo, 90). `releasing` quando a fala terminou (usando `expectedMs` se `!heard`) mas falta `countdownZero`, ou vice-versa, fixa em 99. `done` e 100 quando ambos.
- [ ] **Step 6: Implement `speechTiming.ts` e `preflightBlockedShort`** — `preflightBlockedShort` repete a ordem exata de decisão de `preflightBlockedReason`; mover a lógica comum para uma função interna `firstBlock(input): { code; reason } | null` e fazer as duas públicas projetarem o campo. `preflightBlockedReason` deve devolver exatamente as mesmas strings de hoje (os testes `preflightGate.test.ts` e `batteryGateParity.test.ts` seguem verdes).
- [ ] **Step 7: Run** — Expected: PASS, incluindo `batteryGateParity.test.ts`.
- [ ] **Step 8: Checkpoint** — Sugerido: `feat(bmg-ui): launch sequence model and speech timing helpers`.

---

### Task 10: Observar a fala de liberação (item 4, parte 2)

**Files:**
- Modify: `src/lib/narrationQueue.ts`, `src/hooks/useCopilot.ts`
- Test: `src/lib/narrationQueue.test.ts`, `src/hooks/useFlightNarration.test.ts`

**Interfaces:**
- Consumes: `takeLaunchPhrase`, `NarrationQueue.enqueue`.
- Produces:
  - `EnqueueOptions.onStart?: () => void` (chamado uma vez, imediatamente antes de pedir a fala ao copiloto; não chamado para item cancelado)
  - `export interface LaunchSpeechEvent { phase: 'start' | 'end'; text: string; at: number; heard?: boolean }` em `useCopilot.ts`
  - `useFlightNarration(queue, enabled, flightKey, observe?: (event: LaunchSpeechEvent) => void): void`; o observador só recebe eventos da chave `mission.countdown_3`.

- [ ] **Step 1: Write the failing tests**
  - `narrationQueue.test.ts`: `onStart runs before speak is called and once per item`; `onStart is not called for an item cancelled before its turn`; `items without onStart behave as before`.
  - `useFlightNarration.test.ts`: `emits start and end for mission.countdown_3 only`; `end carries heard false when the voice did not answer`; `the spoken text is the reserved launch line`; `no observer given changes nothing in the existing assertions` (regressão); `a new mission.start discards a pending observer state`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** — em `drain()` chamar `item.onStart?.()` dentro de `try/catch` logo após `this.current = item` e antes do `say`. Em `useFlightNarration`, no `enqueue` de `mission.countdown_3` (e somente nele) passar `onStart` e `onDone` que chamam `observe` com `{ phase, text, at: Date.now(), heard }`. A fala, a ordem, os `minMs`/`fallbackMs` e o `stage` ficam idênticos. Gravar a duração medida com `recordSpeechMs` (importado de `speechTiming`) ao receber `end` com `heard === true`, usando `localStorage` guardado por try/catch.
- [ ] **Step 4: Run** — `npx vitest run src/lib/narrationQueue.test.ts src/hooks && npm run typecheck`. Expected: PASS (todas as suítes de narração existentes seguem verdes).
- [ ] **Step 5: Checkpoint** — Sugerido: `feat(bmg-ui): observe launch clearance speech start and end`.

---

### Task 11: Sequência em INICIO, botão circular e dock fundido (item 4, parte 3)

O maior bloco. `App.tsx` continua dono do estado de lançamento; o relógio e a regra de "pronto" saem do `CountdownOverlay` para um hook.

**Files:**
- Create: `src/hooks/useLaunchSequence.ts`, `src/hooks/useLaunchSequence.test.tsx`, `src/hooks/useLaunchAbortShortcut.ts`, `src/hooks/useLaunchAbortShortcut.test.tsx`, `src/components/preflight/ControlDock.tsx`, `src/components/preflight/ControlDock.test.tsx`
- Modify: `src/components/preflight/LaunchDial.tsx`, `src/components/preflight/LaunchDial.test.tsx`, `src/components/preflight/PreflightScreen.tsx`, `src/App.tsx`, `src/styles/globals.css`, `src/App.launchParams.test.tsx`, `src/App.battery.test.tsx`

**Interfaces:**
- Consumes: `deriveLaunchView`, `LaunchView`, `LAUNCH_STEPS` (T9); `LaunchSpeechEvent` e a variante de `useFlightNarration` (T10); `estimateSpeechMs`/`readSpeechMs` (T9); `preflightBlockedShort` (T9); `useMissionCountdown`, `isAbortShortcut`.
- Produces:
  ```ts
  // useLaunchSequence.ts
  export interface LaunchSequenceInput {
    active: boolean; clickedAt: number | null; seconds: number;
    paramsCommitted: boolean; linkReady: boolean; stageReached: boolean;
    countdown: { remaining: number | null; clearance: boolean; trim: 'acked' | 'skipped' | null };
    speech: { startedAt: number | null; endedAt: number | null; heard: boolean };
    speechText: string | null;
    onDone: () => void;
  }
  export function useLaunchSequence(input: LaunchSequenceInput): LaunchView | null; // null quando !active
  // useLaunchAbortShortcut.ts
  export function useLaunchAbortShortcut(opts: { armed: boolean; handoff: boolean; onAbort: () => void }): void;
  // ControlDock.tsx
  interface ControlDockProps { fused: boolean; onAbort: () => void; parameters: React.ReactNode; mode: React.ReactNode }
  // LaunchDial.tsx
  interface LaunchDialProps {
    view: LaunchView | null;           // sequência ativa, ou null
    ready: boolean; blockedShort: string | null; blockedReason: string | null;
    errorShort: string | null;         // 'Falha ao iniciar' quando launchError
    benchMode: boolean; onLaunch: () => void; beneath?: React.ReactNode;
  }
  ```

**Regras do hook `useLaunchSequence`** (portadas de `CountdownOverlay.tsx`, sem mudar a semântica):
- Relógio do clique: `clockRemaining = max(0, ceil(seconds - (now - clickedAt)/1000))`, atualizado a cada 200 ms enquanto `active`.
- `countdownZero = reported === 0 && clockRemaining === 0` (mesma regra de `onDone` do overlay: um zero reportado antes do relógio do clique chegar a zero não vale).
- `countdownStarted = countdown.remaining !== null`.
- Chama `onDone` uma única vez quando a vista chega a `done`.
- `speech.expectedMs = readSpeechMs(storage, speechText) ?? estimateSpeechMs(speechText ?? '')`.
- Mantém o piso monotônico do percentual com um `useRef`.

- [ ] **Step 1: Write the failing tests do hook** (`useLaunchSequence.test.tsx`, com relógio falso `vi.useFakeTimers`)
  - `returns null while inactive`
  - `starts at 0 with the params step active right after the click`
  - `advances through gimbal, imu, ground and vision on the real signals` (alimentando `stageReached`, `remaining: 7`, `trim: 'acked'`, `clearance: true`)
  - `reaches 90 and shows "Iniciando missão" at clearance with all steps done`
  - `reaches 100 only when the speech ended and the backend countdown is zero` (fala termina antes do zero => 99; zero antes do fim da fala => 99; ambos => 100)
  - `calls onDone once at 100`
  - `a zero reported before the click clock expired is ignored`
  - `an unheard speech progresses over the estimated duration`
  - `percent never decreases if a signal flickers`
- [ ] **Step 2: Tests do atalho** (`useLaunchAbortShortcut.test.tsx`) com um bridge falso
  - `arming publishes true and onAbortShortcut triggers onAbort`
  - `disarming publishes false`
  - `disarming with handoff true does not publish false` (a cabine é a nova dona)
  - `unmounting does not publish false when handoff is true`
  - `without a bridge Escape and Ctrl+C trigger onAbort through the window listener while armed`
  - `ignores key repeat` e `ignores Ctrl+C inside a text input`
  - `does nothing while not armed` (Ctrl+C continua copiando)
- [ ] **Step 3: Tests do `ControlDock`** — `renders the parameters and mode slots when split`; `renders a single Abortar Missão button when fused` (texto exato `Abortar Missão`; os dois slots ficam inertes: `inert` ou `aria-hidden` e `tabIndex=-1`); `fused container width equals the sum of the two slots` (largura do contêiner constante entre os estados: `data-fused` muda, o `width` do host não); `pressing Abortar Missão calls onAbort`; `has a transition and respects reduced motion` (classe `dock-motion` presente; regra `prefers-reduced-motion` em `globals.css`).
- [ ] **Step 4: Tests do `LaunchDial`**
  - `ready and idle shows only "Iniciar" in both flight and Teste modes` (mesmo texto nos dois; sem `VOO REAL`, sem `Motores desligados`, sem ícone de play; só a classe de tema do anel muda: mint em voo, cyan em Teste)
  - `blocked shows the short reason inside the dial and the long reason in the title`
  - `launchError shows "Falha ao iniciar" in the dial and the long text only as title`
  - `with a view it shows the percent as an integer and the active step label`
  - `at announcing it shows "Iniciando missão" and 90 or more`
  - `the ring offset follows the percent` (atributo `data-progress` com o valor)
  - `step label changes with a keyed enter and leave animation` (o rótulo anterior recebe `data-leaving`)
  - `is not clickable while a view is active` e `a double activation within 1 s launches once`
  - `bench theme keeps the cyan ring` (`benchMode`)
- [ ] **Step 5: Tests de integração em `App.launchParams.test.tsx`**
  - `clicking Iniciar fuses the dock into Abortar Missão right away`
  - `Abortar Missão, Escape and Ctrl+C each cancel the preparation and split the dock back` (chama `mission.abort`)
  - `cancelling during the document commit prevents the spawn` (Review Focus 2: resolver o `saveParameters` **depois** do `ESC`; `startMission` nunca é chamado)
  - `a double click launches one mission` (Review Focus 3)
  - `after takeoff the last setCockpitAbortArmed call is true` (Review Focus 1: simular a transição INICIO para CABINE com `CockpitScreen` armando)
  - `while the sequence is active the mode, parameters, tab switch, wifi, ros2 and battery controls are disabled and volume is not`
  - `a launch refused by the document shows the error in the dial and splits the dock`
  - `the screen hands over to the cockpit when the sequence is done` (`screen === 'cockpit'`)
  - `the 'Teste' mode runs the same sequence with the cyan dial`
- [ ] **Step 6: Run** — `npx vitest run src/hooks src/components/preflight src/App.launchParams.test.tsx`. Expected: FAIL.
- [ ] **Step 7: Implement `useLaunchSequence`** conforme as regras acima, delegando o cálculo a `deriveLaunchView`.
- [ ] **Step 8: Implement `useLaunchAbortShortcut`** — com bridge: efeito que chama `bridge.setCockpitAbortArmed(armed)` quando `armed` muda, e `bridge.onAbortShortcut(() => armedRef.current && onAbortRef.current())`. Ao desarmar (`armed` vai a `false`) ou desmontar: publicar `false` **apenas** se `!handoff`. Sem bridge: `keydown` em `window` com `isAbortShortcut`, ignorando `isTextInput` (importado de `CockpitScreen`) e chamando `preventDefault` só quando armado.
- [ ] **Step 9: Implement `ControlDock`** — contêiner `relative flex items-center justify-center` de largura fixa igual à soma natural dos dois slots (medir com `ref` na montagem e fixar via `style.width` para a animação), com os dois slots lado a lado e um botão "Abortar Missão" sobreposto. Transição: `gap` e `opacity`/`scale` dos slots, e `opacity` do botão de abortar, em 360 ms `ease-settle` (classe `dock-motion` em `globals.css`); no estado fundido os slots ganham `pointer-events-none`, `aria-hidden` e `tabIndex={-1}` nos filhos focáveis (usar o atributo `inert` quando disponível). O botão "Abortar Missão" usa o estilo `ember` do `AbortControl` da cabine (contorno `ember/70`, texto `ember`, hover preenchido) com altura igual à das pílulas.
- [ ] **Step 10: Implement `LaunchDial`** — manter o anel duplo, o halo e o tema (`mint` voo, `cyan` Teste) já existentes; remover `Lock`/`Play`, o texto `VOO REAL: motores serão armados` e `Motores desligados`; adicionar o anel radial de progresso (SVG, círculo de raio `r = 124` por `stroke-dasharray`/`stroke-dashoffset`, `strokeLinecap="round"`, `transition: stroke-dashoffset 200ms linear`) por cima do anel externo. Conteúdo central: ocioso e pronto => `Iniciar`; bloqueado => `blockedShort` em `font-mono text-3xs text-amber/90` (limite de 2 linhas); em sequência => percentual em `font-mono text-4xl` e, abaixo, o rótulo do checkpoint com `key={stepId}` e animação `anim-step-in` (entra por baixo, 220 ms) / `anim-step-out` na saída (classe `data-leaving`, 180 ms); em `announcing` o rótulo é `Iniciando missão`. O `title` do botão e o `aria-label` levam o motivo longo. `aria-live="polite"` num `<span class="sr-only">` anuncia a etapa, nunca o percentual a cada tick.
- [ ] **Step 11: `PreflightScreen.tsx`** — Receber `sequence: LaunchView | null`, `onAbortLaunch: () => void`, `launchErrorShort`. Montar `<ControlDock fused={sequence !== null} parameters={botão Parâmetros} mode={MissionModeSelector} onAbort={onAbortLaunch} />` no slot `beneath` do `LaunchDial`. Passar `blockedShort` calculado por `preflightBlockedShort(...)` (mesma entrada que hoje alimenta `preflightBlockedReason`). Enquanto `sequence` está ativa, o modal de Parâmetros não abre.
- [ ] **Step 12: `App.tsx`** — (a) `launch()`: chamar `setCounting(true)` **logo após** a guarda `launching.current`, e `setLaunchAt(clickedAt)` imediatamente, antes de `commitLaunchDocument`; em qualquer retorno antecipado (documento recusado, falha de spawn) `setCounting(false)`. (b) Adicionar `paramsCommitted` (estado) que vira `true` após `commitLaunchDocument()` devolver o documento e `false` em `endCycle`/cancelamento. (c) `launchCancelledRef`: `cancelCountdown()` o seta; `launch()` o checa depois de cada `await` (commit e antes de `mission.launch`) e retorna sem lançar; `launch()` o limpa ao iniciar. (d) Estado da fala: `speech` (`startedAt`, `endedAt`, `heard`, `text`) alimentado pelo observador de `useFlightNarration(narration, true, mission.startedAt, observe)` e zerado em cada `launch()`. (e) `const sequence = useLaunchSequence({ active: counting, clickedAt: launchAt, seconds: countdownSeconds || LAUNCH_COUNTDOWN_SEC, paramsCommitted, linkReady: benchMode || benchStage !== null ? link.driverRunning : link.flightReady, stageReached: mission.stage >= 1, countdown: missionCountdown, speech, speechText: speech.text, onDone: finishCountdown })`. (f) `useLaunchAbortShortcut({ armed: counting && screen === 'preflight', handoff: screen === 'cockpit', onAbort: () => void cancelCountdown() })`. (g) `frozen={counting}` no `TopBar`; `modeLocked` inalterado. (h) Remover `<CountdownOverlay>` do JSX (o arquivo some na Task 12). (i) Manter `finishCountdown`, o efeito de "missão terminou durante a contagem", o efeito de `mission.refusal` e `useModeEcho` exatamente como estão.
- [ ] **Step 13: Estilos** — `globals.css`: `dock-motion`, `anim-step-in`, `anim-step-out`, `dial-progress` (apenas `transition`) e a regra `@media (prefers-reduced-motion: reduce)` para eles.
- [ ] **Step 14: Run** — `npx vitest run src && npm run typecheck`. Expected: PASS. `App.battery.test.tsx` e `App.launchParams.test.tsx` que simulavam `CountdownOverlay` migram para as asserções sobre o `LaunchDial` (selecionar por `data-progress` e pelo texto do rótulo).
- [ ] **Step 15: Verificação visual** — `npm run dev` + `?countdown=10` (pré-visualização existente em `previewCountdown()`): ajustar `previewCountdown` para ativar `counting` sem comandar missão e conferir no browser integrado: fusão do dock, anel de 0 a 90, "Iniciando missão", 90 a 100 e divisão ao cancelar. Capturar quatro quadros.
- [ ] **Step 16: Checkpoint** — Sugerido: `feat(bmg-ui): in-place launch sequence with fused abort dock`.

---

### Task 12: Remover a tela de contagem e fechar as pontas (itens 4 e 6)

**Files:**
- Remove: `src/components/preflight/CountdownOverlay.tsx`, `CountdownOverlay.test.tsx`
- Modify: `src/App.tsx`, `src/components/cockpit/StageBar.tsx`, `src/components/cockpit/CockpitScreen.tsx`, `src/styles/globals.css`
- Test: `src/components/cockpit/StageBar.mode.test.tsx`, `src/hooks/useMissionCountdown.test.tsx` (inalterado, regressão)

**Interfaces:**
- Consumes: `useMissionCountdown().remaining`, `StageBar.benchCountdown` (T6).
- Produces: `CockpitScreen` prop `benchCountdown?: number | null` repassada ao `StageBar`.

- [ ] **Step 1: Write the failing tests** — `stage bar shows the remaining countdown seconds inside the running bench stage pill` (`benchStage = 2`, `benchCountdown = 7` => a pílula da etapa 2 contém `7`); `no countdown text when benchCountdown is null`; `the numeral has tabular figures and is aria-hidden with an sr-only equivalent`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** — `StageBar`: dentro da pílula da etapa de bancada em execução, um `<span class="tnum font-mono text-3xs">` com o número quando `benchCountdown` for não nulo e maior que zero. `App.tsx`: passar `benchCountdown={benchStage !== null && counting ? missionCountdown.remaining : null}` ao `CockpitScreen`.
- [ ] **Step 4: Remover o overlay** — `git rm` de `CountdownOverlay.tsx` e do teste. `grep -rn "CountdownOverlay" src` deve voltar vazio. Remover de `globals.css` as classes que só ele usava (`brand-field` permanece se `VideoBackdrop`/outros usam; conferir por `grep -rn` antes de apagar qualquer uma).
- [ ] **Step 5: Run** — `npx vitest run src && npm run typecheck`. Expected: PASS.
- [ ] **Step 6: Checkpoint** — Sugerido: `refactor(bmg-ui): remove countdown overlay replaced by in-place sequence`.

---

### Task 13: Verificação final

**Files:** nenhum novo.

- [ ] **Step 1: Typecheck e testes completos** — Run: `npm run typecheck && npx vitest run --no-file-parallelism`. Expected: nenhuma falha além das registradas na Task 0; contagem de arquivos de teste menor pelos removidos.
- [ ] **Step 2: Build de produção** — Run: `npm run build`. Expected: conclui sem erro.
- [ ] **Step 3: Contrato com o backend** — Run (com o ambiente do Nectar ativo): `python3 -m pytest test/test_contracts.py` na raiz do monorepo. Expected: igual ao baseline (nada em Python foi alterado por este plano, (o Anexo B está fora de escopo)).
- [ ] **Step 4: Varredura de resíduos** — Run: `grep -rnE "Terminal|xterm|node-pty|CountdownOverlay|StatusBar|ConnectionSheet|FavoritesMenu|Bancada|Configurações Avançadas|Parâmetros de voo" src electron package.json`. Expected: nenhum hit em código de UI (comentários técnicos sobre "terminal que lançou o app" são aceitos).
- [ ] **Step 5: Varredura de emojis e termos banidos** — Run: `grep -rnP "[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]" src electron` e `npx vitest run src/lib/bannedTerms.guard.test.ts`. Expected: vazio / PASS.
- [ ] **Step 6: Smoke manual no app** — `npm run start:app` (ou `npm run dev` no browser integrado para o que não depende de Electron). Roteiro: (1) barra superior em INICIO e CABINE, ordem do tray; (2) popover ROS2 com os 8 tópicos e Iniciar/Parar; (3) Wi-Fi com rede Bebop destacada e atualizar; (4) volume 0 mostra o ícone mudo; (5) bateria: só toggle e slider; (6) INICIO com três elementos; Teste desabilita Parâmetros; (7) Iniciar funde o dock, ESC e Ctrl+C dividem; (8) 0 a 100% com "Iniciando missão" (em bancada, sem motores); (9) modal: cabeçalho só com o nome, estrela, Aruco no canto superior direito, ESC; (10) Cabine sem barras de sinal nem volume, câmera offline só com glifo e "Sem sinal óptico"; (11) escala do mapa distinta a cada zoom; (12) painel forense. Capturar as telas e anexar à resposta final.
- [ ] **Step 7: Anexo A** — Executar a checagem de preservação (abaixo) e colar o resultado.
- [ ] **Step 8: Checkpoint final** — `git status` e `git diff --stat`. Entregar ao usuário o resumo e perguntar se autoriza os commits atômicos sugeridos (um por task). **Não** executar `git push`.

---

## Anexo A: Checagem de preservação

Após a Task 13, nenhum destes deve aparecer no `git diff` (conferir com `git diff --stat -- <caminho>`):

`electron/missionStandby.cjs`, `electron/missionLifecycle.cjs`, `electron/milestones.cjs`, `electron/launchReadiness.cjs`, `electron/launchRefusal.cjs`, `electron/abortShortcut.cjs`, `electron/benchCountdown.cjs`, `src/lib/launchDocument.ts`, `src/lib/parameterConflicts.ts`, `src/lib/parameterSchema.ts`, `src/lib/batteryFailsafe.ts`, `src/lib/batteryReturn.ts`, `src/lib/finishLock.ts`, `src/lib/copilotPhrases.ts`, `src/lib/spokenCatalog.ts`, `src/hooks/useMissionRuntime.ts`, `src/hooks/useTelemetry.ts`, `src/hooks/useMissionCountdown.ts`, `src/hooks/useVoiceLevel.ts`, `mvp_mission_bebop/**`.

Alterações esperadas e limitadas em arquivos centrais, para revisão de diff:

- `electron/main.cjs`: só remoções do Terminal (e dois comentários). Nenhuma linha de lançamento, standby, milestone ou telemetria.
- `electron/preload.cjs`: só as seis linhas do Terminal.
- `src/types/bmg.ts`: só as quatro interfaces e seis membros do Terminal.
- `src/hooks/useMissionParameters.ts`: só `favorite`, `saveFavorite`, `restoreFavorite` e o que eles exigem.
- `src/App.tsx`: remoção de `Overlay`/Diagnóstico/`CountdownOverlay`, novo layout com `TopBar`, `selectFlightMode`, `useLaunchSequence`, `useLaunchAbortShortcut`. Toda a lógica de failsafe de bateria, `returnOnCriticalBattery`, `gotoStage`, `runBenchStage`, `endCycle`, `finishLock`, forense e narração permanece com as mesmas linhas.

---

## Anexo B: Gate de takeoff por liberação (FORA DE ESCOPO: usuário escolheu o modo deadline)

Registrado só como referência para um plano futuro. Nada abaixo é executado por este plano.

**Quando é necessário.** O modo "deadline" (D4) entrega: barra 0 a 100%, "Iniciando missão" no marco `mission.countdown_3`, fim da fala próximo ao zero da contagem. Se a diferença entre o fim da fala e a decolagem (hoje: a chamada de liberação dispara 3,0 s antes do zero e a frase dura cerca de 3 s) precisar ser **zero garantido**, o frontend sozinho não consegue: o instante do takeoff é `launch_at_ms + countdown_sec` e pertence ao processo Python.

**Mudança aditiva proposta** (nenhuma delas é feita por este plano sem aprovação):
1. `mvp_mission_bebop/engine/launch.py:parse_go_command`: aceitar o campo opcional `hold_takeoff: bool` no comando `go` (ausente => comportamento atual) e o novo comando `{"op": "release"}` lido em stdin depois do `go`.
2. `steps/takeoff.py`: em `_await_ground_interlocks`, quando `hold_takeoff` estiver ativo, esperar o evento de liberação (com `ctx.interrupted()` e um teto de segurança configurável) antes de `_launch`; emitir `emit_milestone("mission.takeoff_hold", {...})` ao entrar na espera.
3. `electron/missionStandby.cjs:take` e o handler `bmg:start-mission`: repassar `hold_takeoff`; novo `ipcMain.handle('bmg:release-takeoff')` que escreve `{"op":"release"}` no stdin; preload e tipo `BmgBridge.releaseTakeoff`.
4. Renderer: em `App.tsx`, ao `done` de `useLaunchSequence` com o gate ativo, chamar `bridge.releaseTakeoff()` no fim da fala; a barra chega a 100% no `release`.
5. Testes: `test/test_contracts.py` (novo comando e marco), `test/test_takeoff_*` (espera e liberação, teto de segurança, abort durante a espera), e testes do renderer.

**Riscos.** Mudança em caminho crítico de voo: exige revisão do dono do backend, voo de bancada antes de voo real, e o teto de segurança para que uma liberação perdida nunca deixe a aeronave armada esperando indefinidamente. Se o usuário aprovar, tratar como plano separado, com sua própria sequência de testes.

---

## Anexo C: Especificação visual da barra superior (da imagem anexada)

Leitura da imagem (barra do GNOME no Ubuntu 24.04, 1366 px de largura, aproximadamente 27 px de altura): faixa contínua quase preta, sem bordas nem sombra; à esquerda um indicador em pílula e um ponto; ao centro o relógio ("ter 27 de out 10:00 PM"); à direita, da esquerda para a direita, três glifos de estado (cada um sem rótulo), Wi-Fi, volume e bateria com o percentual `78%` ao lado, em texto claro de corpo pequeno.

Tradução para o BMG:

| Ubuntu | BMG |
|---|---|
| Indicador à esquerda | Logo `techforhumans` (`Wordmark`, 16 px de altura) |
| Relógio centralizado | Pílula `INICIO` \| `CABINE` centralizada |
| Glifos de estado | ROS2 (grade de pontos) |
| Wi-Fi | `WifiGlyph`, só o ícone |
| Volume | Ícone por nível; mudo em 0 |
| Bateria + `78%` | `BatteryGlyph` + percentual |

Faixa de 36 px (um pouco mais alta que os 27 px do original para alvos de clique confortáveis), `bg-abyss/95`, ícones de 16 px, espaçamento de 2 px entre botões do tray, hover como fundo `frost/[0.08]` em pílula. Sem texto auxiliar na barra.

---

## Self-Review

1. **Cobertura da solicitação.** Item 1 (barra, ordem do tray, ROS2 com 8 tópicos e Iniciar/Parar, automação, Wi-Fi com destaque e atualizar, volume com ícone reativo, bateria só com toggle e slider): Task 8. Item 2 (Terminal): Task 1. Item 3 (três elementos, `Parâmetros`, seletor `Simples/Avançado/Teste`, Parâmetros desabilitado em Teste): Task 2 e Task 11. Item 4 (fusão e divisão, abortar por botão/`ESC`/`Ctrl+C`, círculo com motivo, 0 a 100%, subsistema entrando e saindo, 90% com "Iniciando missão" e áudio, 90 a 100% com a fala, takeoff aos 100%): Tasks 9 a 12, com a ressalva de D4/Anexo B. Item 5 (modal): Tasks 3 e 4. Item 6 (Cabine: redundâncias, câmera offline, painel forense, escala do mapa): Tasks 5, 6 e 7.
2. **Decisões do usuário incorporadas (2026-10-27).** D4 modo deadline sem backend (Anexo B fora de escopo); D7 segundos na pílula; D10 chip de bateria também sai do vídeo; círculo mostra só `Iniciar` nos dois modos, distinguidos pela cor do anel e pelo nome no botão de modo; estrela preenchida só quando os valores atuais igualam o favorito; `ConnectionSheet` removido; glifo ROS 2 próprio; só o volume ativo durante a sequência. Não há pendências abertas.
3. **Consistência de tipos.** `FlightMode`, `LaunchView`, `LaunchSignals`, `LaunchSpeechEvent`, `TopBarProps`, `ControlDockProps` e `LaunchDialProps` são definidos uma vez e usados com os mesmos nomes nas tasks consumidoras. `useLaunchSequence` consome `LaunchSignals` indiretamente por `deriveLaunchView`.
4. **Review Focus.** Cada uma das dez linhas tem teste nomeado na task dona (Tasks 2, 3, 4, 5, 8, 10, 11).
5. **Proporção.** Código em corpo aparece só como assinaturas, nomes de teste e regras que a assinatura não determina; nenhum componente foi transcrito.
