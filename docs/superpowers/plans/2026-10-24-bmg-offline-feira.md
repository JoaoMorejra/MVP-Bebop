# BMG Offline para a Feira: Voz em Cache, Laudo Determinístico, Mapa Fixo e Fim do Laudo HTML

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o BMG (cockpit, copiloto, laudo e mapa) operar sem nenhuma conexão com a internet, com a voz oficial Orbit pt-BR tocada do disco, sem a palavra "sinistro", com o laudo em cards de ordem determinística e com o rodapé do mapa mostrando base, cidade e localização fixas.

**Architecture:** A voz offline não é uma voz nova. O `PhraseCache` do `announcer.py` já guarda PCM sintetizado pelo Gemini Live (Orbit) em `~/.cache/bmg/speech`, chaveado por sentença, modo, voz e modelo, e já toca do disco sem rede. O que falta é (a) garantir que TODA sentença possível esteja no cache antes da feira (manifesto finito + `precache_speech.py` + verificação), (b) tornar o daemon coerente offline (sem esperas de 3 s por tentativa de sessão, sem declarar "sem voz" quando há cache) e (c) tornar finitas as frases hoje dependentes de medição em voo. O restante é edição de copy, regra de ordem do laudo, remoção do modal HTML e rodapé do mapa a partir de constantes.

**Tech Stack:** TypeScript/React/Vitest (BMG), Electron (`electron/main.cjs`, `preload.cjs`), Python 3.12 + pytest (`mvp_mission_bebop`, `streamer/`), `vite-node` (já em `node_modules/.bin`) para exportar o manifesto.

**Spec:** Prompt do usuário de 2026-10-24 ("Operação 100% offline, regras de falas do copiloto, mapa e descontinuação do laudo HTML"), resumido em Global Constraints. Código lido em 2026-10-24 sobre o HEAD `40676ec`.

Todos os caminhos são relativos a `/home/jv/ros2_ws/src/mvp_mission_bebop` (repo `MVP-Bebop`); `bebop_mission_control/` abreviado como `BMG/`.

## Estado encontrado (fatos verificados no código)

- `BMG/src/lib/copilotPhrases.ts` contém "sinistro" nas linhas 173, 180, 184, 190, 199, 215, 231, 238, 240, 311 e no comentário da 725. Também: `BMG/src/types/mission.ts:22`, `BMG/src/lib/parameterSchema.ts:214,216`, `mvp_mission_bebop/telemetry/announcer.py:1428` (`FORENSIC_INTRO`), `BMG/src/lib/speechCache.test.ts:35`.
- `BMG/src/lib/forensics.ts`: a abertura do laudo ALTERNA entre Veículo e Vítima por voo (`finding.lead` no histórico), sem relação com `victim_detected`. Espelho em `mvp_mission_bebop/telemetry/inspection_report.py`; paridade fixada por `test/test_forensic_parity.py`. Os comentários dizem que Polícia, SAMU e Veículo são "ruling do operador, não editar": esta tarefa é uma nova ordem explícita do usuário e supersede isso (atualizar os comentários).
- `allFindingSentences()` gera 494 sentenças (26 wordings x 19 conectores), fixadas em `speechCache.test.ts`.
- Cache de voz: `PhraseCache` (`announcer.py:578`), `warm_cache` (`:887`), op `cache` do daemon (`:1602`), disparado por `useCopilot.ts:267` em `mission.parameters`. Hit toca offline. Miss offline paga `_open_session` (timeout 3 s) duas vezes e a linha sai muda após até 14 s (`SPEECH_CEILING_MS`).
- `serve_stdin` marca `synthesis=False` sem chave de API; `main.cjs:1968` então grava "sem voz" mesmo com cache completo.
- Não entram em `allMissionPhrases` e portanto nunca são pré-sintetizadas: `mission.target_found` (depende de `{location}` medido em voo), textos de alerta compostos pelo Python, `Lançamento recusado: <reason>.`.
- Nenhuma síntese nativa (`espeak`, `spd-say`) existe no repo. `nectar-sdk` não tem código de fala ou TTS: nada a reutilizar (regra de ouro 2.1 do CLAUDE.md verificada; registrar no commit/PR).
- Laudo HTML: `ForensicPanel.tsx` (`ReportModal`, polling `REPORT_POLL_MS`, botão `open-report`), `electron/main.cjs` (`isReportFilename`, `reportOccurrenceId`, watcher ~936-962, IPC `bmg:get-report-url` ~1851), `electron/preload.cjs:80,96`, `BMG/src/types/bmg.ts` (`ReportReadyEvent`, `getReportUrl`, `onReportReady`), `ForensicPanel.test.tsx:219+`.
- Mapa: `TacticalMap.tsx:584-592` mostra "BASE FIXA" com `aircraft.lat/lng` (posição da aeronave, não a base), "Cidade" com `place.city ?? MAP_ANCHOR.name` (mostra o nome do local no campo cidade) e não tem "Localização". `useReverseGeocode` chama Nominatim (internet) e só roda com tiles online. A grade `LocalGrid` já existe e aparece quando `tilesOk !== true`, mas só depois de `TILE_LOAD_TIMEOUT_MS` = 8 s tentando tiles.
- `config/site-anchor.json` já tem as coordenadas pedidas (`-23.6488913`, `-46.7187124`; `toFixed(6)` resulta em `-23.648891, -46.718712`). Falta `city`.
- Árvore com `BMG/BMG_TECH4AI_INTEGRATION_SPEC.md` não rastreado e outras sessões podem editar o repo: não tocar nele.

## Decisões do usuário (resolvidas em 2026-10-24)

- **D1 - Vítima `unknown`** (inferência não concluiu): ordem do Cenário A (Veículo, Vítima neutra, SAMU/PM). Nenhum texto afirma presença ou ausência de vítima.
- **D2 - Fala de detecção sem distância e sem lado**: a fala deixa de citar metros e lado e diz sempre "na pista". O placeholder `{location}` e `describeTargetLocation` deixam de existir (o texto passa a ser literal nas 6 variantes de `mission.target_found`). Com isso toda a fala de detecção é finita (6 sentenças) e entra no manifesto.
- **D3 - Cache fora do Git**: ~2000 áudios x ~150 kB = ~300 MB. Exceção ao "tudo sob Git" do CLAUDE.md: versionar apenas manifesto, tabela e scripts; transportar o cache com `--pack/--unpack` entre máquinas.
- **D4 - Voz e modelo idênticos** entre a máquina de pré-síntese e a da feira: a chave do cache inclui `SPEECH_VOICE` (Orbit) e `SPEECH_MODEL`. Diferença em qualquer um zera o cache.
- **D5 - Frases de perda de enlace aprovadas** como redigidas na Task 3.
- **D6 - Pacote do agente Tech4AI** (`telemetry/inference_package.py`, campo `expected_report`): fica intacto; só some a UI/IPC do HTML.
- **D7 - Parâmetros da feira**: altitude de decolagem 2 m (`target_altitude_m: 2`, falado "2 metros") e cruzeiro 0,15 m/s (`cruise_mps: 0.15`, falado "0,15 metro por segundo"). A pré-síntese cobre esse conjunto, mais os defaults do schema e o `mission_config.json` da máquina. Na feira não se altera altitude, velocidade nem limiares falados sem repetir a pré-síntese.
- **D8 - Execução**: subagentes por tarefa, com revisão antes de seguir (`superpowers:subagent-driven-development`).

## Global Constraints

- Sem emojis em código, comentários, logs, documentação e commits. Sem comentários redundantes; comentário só para física de controle, hardware Bebop 2 ou acordo de sincronismo IPC.
- Python: type hints estritos, docstrings NumPy (`Parameters`, `Returns`, `Raises`), validação defensiva com `TypeError`/`ValueError`. TypeScript `strict`, sem `any`.
- Arquitetura: `telemetry/` para fala/IPC; nada de matemática de controle aqui. Não editar `nectar-sdk`.
- Contrato com o GCS preservado: marcadores `[STEP N: ...]` e esquema de `mission_config.json` intocados.
- Commits Conventional Commits em inglês técnico, atômicos. **Nenhum commit nem push sem autorização explícita do usuário nesta sessão** (regra persistida na memória do projeto, prevalece sobre o check-out do CLAUDE.md). Onde houver "Commit" abaixo, executar somente com essa autorização, com o trailer `Co-Authored-By` definido pela sessão.
- Palavra proibida em UI, fala, tabela e código fonte: "sinistro" (qualquer caixa). Substitutos: "ocorrência", "acidente", "alvo", "veículo na via".
- Veículo no laudo: todo texto contém o acionamento do guincho por obstrução da via. Texto do card pedido: "Veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via." Fala pedida: "Veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via."
- Ordem do laudo: `victim_detected === true` -> Vítima, Veículo, depois SAMU/PM; caso contrário -> Veículo, Vítima, depois SAMU/PM. SAMU/PM alternam entre si de um voo para o outro (comportamento atual de `finding.dispatch_order`, mantido).
- Mapa: Latitude `-23.648891`, Longitude `-46.718712`, Cidade `São Paulo`, Localização `Transamerica Expo Center`; grade `LocalGrid` sem mensagem de erro de rede nem tela preta.
- Ambiente: `source /home/jv/ros2_ws/bin/nectar-activate` antes de qualquer Python/pytest. Nunca rodar dois lotes de voo emulado ao mesmo tempo. Subagentes precisam do mesmo `source`.

## Review Focus

1. Cache miss offline (parâmetro alterado na feira, voz/modelo diferentes): a linha deve ser pulada em milissegundos com `[SPEECH_MISS]` no log, e a narração/cards seguem na cadência própria (`fallbackMs`), sem esperar 14 s. Teste na Task 4.
2. Vítima `unknown` (timeout de inferência): laudo abre com Veículo, linha de vítima neutra, nunca afirma presença/ausência. Teste na Task 2.
3. Falas com número vindo do payload do milestone e não dos parâmetros (limiar de bateria `{threshold}`, `altitude_m` do payload de decolagem): se o valor em voo diferir do pré-sintetizado, a linha sai muda offline. Teste na Task 3 (manifesto inclui o limiar configurado) e conferência `source=cache` na Task 8.
4. Histórico de variantes no `localStorage` com a chave removida `finding.lead` e índices antigos de pools que mudaram de tamanho: não pode lançar exceção nem repetir texto idêntico ao voo anterior de forma sistemática (`pickVariant` já filtra índices fora do conjunto elegível; provar com teste). Task 2.
5. Cache parcial por interrupção da pré-síntese (queda de rede, Ctrl-C): arquivo `.part` nunca vira entrada válida, áudio truncado é rejeitado, segunda execução completa só o que falta. Task 5.

---

### Task 0: Check-in e baseline

**Files:** nenhum.

- [ ] **Step 1: Estado do repo e baseline**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop && git status --short && git log --oneline -3
```
Expected: só `?? bebop_mission_control/BMG_TECH4AI_INTEGRATION_SPEC.md` (e este plano). Se houver outras alterações, outra sessão está editando: reler antes de cada tarefa. `git pull origin main` só se o `git status` estiver limpo de alterações suas; divergência não-ff deve ser reportada ao usuário, não resolvida sozinho.

- [ ] **Step 2: Baseline verde antes de alterar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && cd /home/jv/ros2_ws/src/mvp_mission_bebop && python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py test/test_forensic_parity.py test/test_inspection_report.py test/test_announcer_calls.py -q
cd bebop_mission_control && npm test 2>&1 | tail -15
```
Expected: tudo passa. Registrar falhas pré-existentes e não as atribuir às tarefas.

---

### Task 1: Banir "sinistro" e travar com guard test

**Files:**
- Modify: `BMG/src/lib/copilotPhrases.ts` (linhas 173, 180, 184, 190, 199, 215, 231, 238, 240, 311, comentário 725), `BMG/src/types/mission.ts:22`, `BMG/src/lib/parameterSchema.ts:214,216`, `mvp_mission_bebop/telemetry/announcer.py:1428`
- Modify (tests): `BMG/src/lib/speechCache.test.ts:35`, `BMG/src/lib/copilotPhrases.test.ts`
- Create: `BMG/src/lib/bannedTerms.guard.test.ts`, `test/test_banned_terms.py`

**Interfaces:** Produces as constantes de texto abaixo, consumidas pelas Tasks 3 e 5. Nenhuma assinatura muda.

Substituições exatas (todas mantêm unicidade dentro do pool, exigida por `speechCache.test.ts`):

| Pool / local | Novo texto |
|---|---|
| `mission.scan_start` (173) | `Varredura em andamento. Procurando ocorrência ao longo da pista.` |
| `mission.target_found` (180) | `Ocorrência identificada na pista.` (D2: sem `{location}`) |
| `mission.target_found` (184) | `Acidente avistado na pista.` (D2: sem `{location}`) |
| `mission.approaching` (190) | `Deslocando até a ocorrência sob controle visual.` |
| `mission.capture_done` (199) | `Registro fotográfico da ocorrência concluído. Processando evidência.` |
| `mission.approaching_direct` (215) | `Deslocando até a ocorrência.` |
| `mission.scan_start_yaw` (231) | `Varredura em andamento. Procurando ocorrência em torno da aeronave.` |
| `mission.approaching_yaw` (238) | `Alinhando a aeronave à ocorrência por giro.` |
| `mission.approaching_yaw` (240) | `Centralizando a ocorrência no quadro apenas por giro.` |
| `inspection.intro` (311) e `FORENSIC_INTRO` | `Inspeção da ocorrência concluída. Apresentando laudo preliminar.` |
| `mission.ts:22` detail | `Pairado imóvel sobre a ocorrência, captura em dupla fidelidade` |
| `parameterSchema.ts:214` hint | `Sem ocorrência confirmada neste tempo, a varredura encerra e a aeronave retorna.` |
| `parameterSchema.ts:216` simple | `Tempo máximo girando em busca da ocorrência. Sem alvo confirmado neste tempo, a aeronave fotografa a referência e pousa.` |

- [ ] **Step 1: Write the failing guard tests**

`BMG/src/lib/bannedTerms.guard.test.ts`, `describe('banned terms')`, `it('no source file spells the banned word')`: percorre `src/`, `electron/`, `streamer/`, `config/` e `../mvp_mission_bebop/` (extensões `.ts .tsx .cjs .py .json`, excluindo `node_modules`, `dist`, `__pycache__` e o próprio guard), mais o arquivo `docs/TABELA_DE_FALAS_COPILOTO.md` quando existir. Docs históricos (`docs/*.md`, `BMG_*.md`) ficam fora do escopo. O padrão é montado em tempo de execução, `new RegExp(['sini', 'stro'].join(''), 'i')`, para o guard não se autodetectar. Assert: lista de arquivos ofensores `toEqual([])`.
`test/test_banned_terms.py::test_no_banned_term_in_package_and_tests`: mesma varredura sobre `mvp_mission_bebop/`, `test/`, `scripts/`, `bebop_mission_control/{src,electron,streamer,docs}`.

- [ ] **Step 2: Run to verify they fail**

```bash
cd bebop_mission_control && npx vitest run src/lib/bannedTerms.guard.test.ts
cd .. && python3 -m pytest test/test_banned_terms.py -q
```
Expected: FAIL listando os arquivos da seção "Estado encontrado".

- [ ] **Step 3: Aplicar as substituições da tabela** nos arquivos acima, incluindo o comentário de `describeTargetLocation` (725), que a Task 3 remove junto com a função (nesta tarefa basta não conter o termo proibido). Ajustar `speechCache.test.ts:35` para `/Ocorrência identificada|Acidente avistado|Alvo identificado/` e conferir que `copilotPhrases.test.ts:401` (`not.toMatch(/sinistro|pericial/i)`) segue válido, escrevendo o padrão sem a palavra literal para passar no guard. Docs históricos que citam o termo não são alterados.

- [ ] **Step 4: Run tests**

```bash
cd bebop_mission_control && npx vitest run src/lib/bannedTerms.guard.test.ts src/lib/copilotPhrases.test.ts src/lib/speechCache.test.ts && cd .. && python3 -m pytest test/test_banned_terms.py test/test_announcer_calls.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit** (se autorizado): `fix(copilot): replace banned term with operational wording and add guard tests`

---

### Task 2: Laudo determinístico com guincho (TS + espelho Python)

**Files:**
- Modify: `BMG/src/lib/forensics.ts`, `BMG/src/lib/copilotPhrases.ts` (tipo `FindingHistoryKey`: remover `finding.lead`), `mvp_mission_bebop/telemetry/inspection_report.py`
- Modify (tests): `BMG/src/lib/forensics.test.ts`, `BMG/src/lib/speechCache.test.ts` (494 passa a `28 * 19`), `BMG/src/hooks/useForensicNarration.test.tsx:18` (fixture do veículo), `test/test_inspection_report.py`, `test/test_forensic_parity.py`

**Interfaces:**
- Produces TS: `export function leadTopicsFor(status: VictimStatus): readonly ['vehicle', 'victim'] | readonly ['victim', 'vehicle']` (`detected` -> victim primeiro; `not_detected` e `unknown` -> vehicle primeiro). `LEAD_TOPICS` permanece apenas como o conjunto de aberturas válidas usado por `checkTopicOrder`.
- Produces Python: `lead_topics_for(status: VictimStatus) -> Tuple[str, str]` com a mesma regra.
- `buildForensicReport` e `build_inspection_report` deixam de sortear `finding.lead` e montam `topics = [*leadTopicsFor(status), *DISPATCH_ORDERS[dispatch.index]]`. Assinaturas públicas inalteradas.

Pools novos (cards sem ponto final, convenção atual; a fala continua `conector + decapitalise(card) + "."`, preservando a invariante "linha no ouvido == linha na tela"):

`FIXED_WORDINGS.vehicle` (5):
- `Veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via`
- `Veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via`
- `Sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via`
- `Integridade do automóvel preservada. Guincho acionado para desobstrução da via`
- `Veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via`

`VICTIM_WORDINGS.not_detected` ganha `Nenhuma vítima visível no local` (6 no total). `detected` já contém `Vítima consciente e sem ferimentos aparentes`. Atualizar os docstrings que afirmam "alterna" e "Not to be edited" nos dois lados.

- [ ] **Step 1: Write the failing tests** em `forensics.test.ts` (e equivalentes em `test_inspection_report.py`):
  - `it('opens with the victim when one was detected')`: 50 sorteios (`random` variando, `storage: null`) com `assessment` detectada -> `report.map(f => f.topic).slice(0, 2)` `toEqual(['victim', 'vehicle'])`.
  - `it('opens with the vehicle when none was detected or the inference is unknown')`: `victim_count: 0` e `assessment: null` -> `['vehicle', 'victim']`.
  - `it('closes with SAMU and police, alternating flight to flight')`: dois voos com `storage` compartilhado; `topics.slice(2)` são `{samu, police}` e a ordem difere entre os voos.
  - `it('every vehicle card names the tow truck')`: `/guincho/i` e `/(danos|avarias) (estruturais )?aparentes|Integridade/` em todos os cards do veículo; `speech` sem `/\.\./`.
  - `it('tolerates a stale history document')`: `storage` com `{ 'finding.lead': [0, 1], 'finding.vehicle': [9] }` não lança e devolve 4 findings.
  - `it('enumerates every sentence')`: `allFindingSentences().length === (4 + 4 + 5 + 5 + 6 + 4) * 19` (= 532) e contém a `speech` de cada finding sorteado.
  - Paridade: `test_forensic_parity.py` compara `FIXED_WORDINGS.vehicle`, `VICTIM_WORDINGS.not_detected` e a regra `leadTopicsFor` com o Python, lendo o TS como já faz hoje.

- [ ] **Step 2: Run to verify they fail**

```bash
cd bebop_mission_control && npx vitest run src/lib/forensics.test.ts src/lib/speechCache.test.ts
cd .. && python3 -m pytest test/test_inspection_report.py test/test_forensic_parity.py -q
```
Expected: FAIL (ordem alternada, pool sem guincho, 494).

- [ ] **Step 3: Implement** `leadTopicsFor`/`lead_topics_for`, remover o sorteio `finding.lead` em ambos os lados (manter a ordem de consumo do `rng` coerente entre TS e Python onde a paridade de sementes é testada), trocar os pools. Nenhuma outra mudança de contrato.

- [ ] **Step 4: Run tests**

```bash
cd bebop_mission_control && npx vitest run src/lib src/hooks/useForensicNarration.test.tsx src/components/cockpit/ForensicPanel.test.tsx
cd .. && python3 -m pytest test/test_inspection_report.py test/test_forensic_parity.py test/test_forensic_telemetry.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit** (se autorizado): `feat(forensics): open the report by victim status and add tow truck dispatch to vehicle finding`

---

### Task 3: Catálogo de falas, manifesto finito e `TABELA_DE_FALAS_COPILOTO.md`

**Files:**
- Create: `BMG/src/lib/spokenCatalog.ts`, `BMG/scripts/exportSpeechManifest.ts`, `mvp_mission_bebop/telemetry/spoken_alerts.py`, `BMG/docs/TABELA_DE_FALAS_COPILOTO.md` (gerado, versionado)
- Modify: `BMG/src/lib/copilotPhrases.ts` (fala de detecção sem local, pool de perda de enlace), `BMG/src/hooks/useCopilot.ts` (usa o catálogo; hoje `FIXED_ALERT_SENTENCES` é local), `BMG/package.json` (script `speech:manifest`: `vite-node scripts/exportSpeechManifest.ts`)
- Test: `BMG/src/lib/spokenCatalog.test.ts`, `test/test_spoken_alerts.py`

**Interfaces:**
- TS (`spokenCatalog.ts`):
  ```ts
  export type SpeechCategory =
    | 'voo.contagem' | 'voo.decolagem' | 'voo.varredura' | 'voo.aproximacao' | 'voo.captura' | 'voo.retorno' | 'voo.pouso'
    | 'laudo.abertura' | 'laudo.veiculo' | 'laudo.vitima' | 'laudo.samu' | 'laudo.policia' | 'laudo.encerramento'
    | 'falha.aborto' | 'falha.link' | 'falha.bateria' | 'falha.timeout_varredura' | 'falha.calibracao' | 'falha.decolagem' | 'falha.recusa_lancamento';
  export interface SpeechEntry { text: string; category: SpeechCategory; source: string; condition: string }
  export interface SpeechParameterSet { parameters: MissionParametersPayload; plan: PlanLike }
  export const FIXED_ALERT_SENTENCES: readonly SpeechEntry[];
  export function buildSpeechManifest(sets: readonly SpeechParameterSet[]): SpeechEntry[];
  ```
  `buildSpeechManifest` une, deduplicando por `text` (primeira categoria vence) e em ordem determinística: `allMissionPhrases` por conjunto de parâmetros e plano, `allFindingSentences`, intro/outro do laudo, `FIXED_ALERT_SENTENCES`, as 6 variantes de `mission.target_found`, pool de perda de enlace e os motivos finitos de recusa de lançamento.
- Conjuntos de parâmetros do manifesto (D7): `{ target_altitude_m: 2, cruise_mps: 0.15 }` mais os defaults do schema e o `mission_config.json` da máquina, cada um nos 4 planos. Incluir também o valor falado de `{threshold}` do aviso de bateria: hoje `phraseValues` só o lê do payload do milestone, então `allMissionPhrases` omite essas variantes. Ler o limiar configurado (`parameters.py`/`mission_config.json`, achar a chave) e passá-lo ao renderizar `mission.battery_warning`; confirmar no Task 8 que a linha real em voo bate com a pré-sintetizada.
- TS (`copilotPhrases.ts`) (D2): remover `describeTargetLocation`, `TargetFix`, `AHEAD_DEG`, `ASIDE_DEG` e a chave `location` de `phraseValues`; `mission.target_found` deixa de ter placeholder e sai de `FLIGHT_ESTIMATE_POOLS` (o conjunto fica vazio: remover a constante e o `continue` em `allMissionPhrases`). Grep por todos os usos antes de remover e ajustar os testes que os citam.
- TS (`copilotPhrases.ts`): `export const LINK_LOSS_SENTENCES: readonly string[]`, 3 variantes: `Enlace com a aeronave perdido. Verifique a aeronave visualmente.`, `Telemetria interrompida. Confirme a posição da aeronave a olho.`, `Sem dados da aeronave. Mantenha contato visual e prepare o pouso manual.` Disparo: nova função em `useFlightNarration` (ou hook dedicado), `URGENT`, por borda (uma vez por perda, rearmada na recuperação), ligada ao mesmo sinal de `linkLost` do `useFinishLock`/`useTelemetry` (ler os dois antes de escolher a fonte).
- Python (`spoken_alerts.py`): `ALERT_SENTENCES: Final[Tuple[AlertSpec, ...]]` com `AlertSpec(category: str, condition: str, text: str)` e `def all_alert_sentences() -> Tuple[str, ...]`. Enumera as falhas que a missão pode levantar com frase fixa (`_GROUND_FAULT_ACTIONS` com e sem `_LANDING_CLAUSE`, abortos, watchdog de vídeo/odometria). Listar todos os call sites de `announce_alert`/`emit_alert` em `mission.py` e `steps/`; qualquer um com texto não enumerável deve ser registrado ao fim deste plano em "Pendências", não inventado.

Tabela gerada: `precache_speech.py --table` (Task 5) escreve `BMG/docs/TABELA_DE_FALAS_COPILOTO.md` a partir do JSON do exportador e de `all_alert_sentences()`, com as seções: 1) Voo normal (contagem, decolagem, varredura, aproximação, captura, retorno, pouso), 2) Laudo pericial (aberturas, Veículo com guincho, Vítima detectada/não detectada/desconhecida, SAMU, Polícia, encerramento, regra de ordem dos Cenários A e B), 3) Falhas (aborto manual, perda de enlace/watchdog, bateria crítica e auto-land, timeout de varredura sem alvo, falha de calibração/recusa de decolagem). Cada linha traz condição de disparo, fonte (arquivo e pool) e texto.

- [ ] **Step 1: Write the failing tests** (`spokenCatalog.test.ts`):
  - `it('the detection call never carries a distance or a side')`: para `payload` com `forward_m: 3.3, bearing_deg: 12`, com `NaN` e vazio, `phraseForMilestone('mission.target_found', payload, {}, { storage: null, random })` (12 valores de `random`) casa `/na pista\.$/`, não casa `/metro|esquerda|direita|\{/` e pertence ao manifesto.
  - `it('speaks the fair parameters')`: o manifesto contém `Decolagem autorizada. Subindo para 2 metros.` (ou a variante equivalente do pool `mission.takeoff` com `{altitude}` = "2 metros") e `Varredura iniciada a 0,15 metro por segundo.`
  - `it('has the configured battery threshold line')`: com limiar `L` lido do config, o manifesto contém `Aviso de bateria baixa` renderizado com `spokenPercent(L)` quando a variante usa `{threshold}`.
  - `it('manifest has no unresolved placeholder and no banned word')`: nenhum texto casa `/\{[a-z_]+\}/`; sem duplicatas; todo `category` pertence a `SpeechCategory`.
  - `it('covers every sentence the station can compose')`: para os 4 planos (advanced/simple x rtl on/off) e os parâmetros padrão, todo `phraseForMilestone(key, payload, params, { storage: null }, plan)` com `random` variando e todo `finding.speech` sorteado estão no manifesto.
  - `it('lists the three link-loss variants under falha.link')`.
  - `test_spoken_alerts.py`: sem duplicatas; toda frase termina em ponto; `_format_telemetry_statement` das ações de `_GROUND_FAULT_ACTIONS` (com e sem `em_voo`) está em `all_alert_sentences()`.

- [ ] **Step 2: Run to verify they fail**

```bash
cd bebop_mission_control && npx vitest run src/lib/spokenCatalog.test.ts
cd .. && python3 -m pytest test/test_spoken_alerts.py -q
```
Expected: FAIL (módulos inexistentes).

- [ ] **Step 3: Implement** `spokenCatalog.ts`, `exportSpeechManifest.ts` (imprime em stdout `{ schema: 'bmg.speech_manifest.v1', voice: null, entries, table }`; a voz vem do Python), `spoken_alerts.py`, a remoção do local do alvo e o pool de enlace. Conjuntos de parâmetros padrão do exportador (D7): `{ target_altitude_m: 2, cruise_mps: 0.15 }`, defaults de `parameterSchema`/`mission_parameters.py` e o `mission_config.json` atual da estação, cada um nos 4 planos; `--parameters FILE` (JSON, lista de conjuntos) acrescenta outros. Verificar que `tsc` (`npm run build`) não passa a incluir `scripts/` por engano.

- [ ] **Step 4: Run tests**

```bash
cd bebop_mission_control && npx vitest run src/lib src/hooks && npx vite-node scripts/exportSpeechManifest.ts | head -c 600
cd .. && python3 -m pytest test/test_spoken_alerts.py test/test_banned_terms.py -q
```
Expected: PASS; manifesto JSON válido. Contagem esperada na ordem de 700 a 1000 entradas (laudo 532 mais pools de voo e alertas), informar o número real.

- [ ] **Step 5: Commit** (se autorizado): `feat(copilot): add finite spoken catalog, link-loss call and location-free detection call`

---

### Task 4: Daemon de voz coerente offline

**Files:**
- Modify: `mvp_mission_bebop/telemetry/announcer.py`, `BMG/electron/main.cjs` (handler `ready` ~1968 e novo campo no evento `cache`)
- Test: `test/test_speech_offline.py`, `test/test_no_native_tts.py`; ajustar `BMG/src/lib/preflightGate.test.ts` ou o teste que fixa `latestSpeechState` se existir

**Interfaces:**
- `SPEECH_OFFLINE_ENV: Final[str] = "BMG_SPEECH_OFFLINE"` (`"1"` força offline, `"0"` nunca, ausente ou `"auto"` sonda).
- `def synthesis_reachable(host: str = "generativelanguage.googleapis.com", port: int = 443, timeout_sec: float = 1.0, ttl_sec: float = 30.0) -> bool` (TCP connect com resultado em cache por `ttl_sec`; honra `SPEECH_OFFLINE_ENV`).
- `PhraseCache.missing(self, statements: Iterable[str], verbatim: bool = True) -> List[str]`.
- `def voice_readiness(*, genai_available: bool, token: bool, playback: bool, cache_entries: int) -> Tuple[bool, str]`: pronto se `playback` e (`genai_available and token` ou `cache_entries > 0`); detalhe `"pronto (voz em cache, sem rede)"` no segundo caso.
- Evento `ready` ganha `voice_ready: bool`, `cache_entries: int`, `offline: bool`; evento `cache` ganha `missing: int` e `offline: bool`. `main.cjs` usa `event.voice_ready ?? Boolean(event.synthesis && event.playback)`.

Comportamento: com `synthesis_reachable() == False`, `_ensure_warm_session`/`_keep_warm` não tentam conectar (sem warning a cada 10 s), `_synthesize_once` devolve `(b"", "offline")` sem abrir sessão e a linha seguinte do consumidor registra `logger.warning("[SPEECH_MISS] '%s'", statement[:48])` e resolve `done ok=false` imediatamente; `warm_cache` offline retorna 0 mas o evento `cache` informa `missing` com `PhraseCache.missing`.

- [ ] **Step 1: Write the failing tests** (`test_speech_offline.py`, sem rede, `BMG_SPEECH_CACHE_DIR` em `tmp_path`, `synthesis_reachable` monkeypatched para `False`, `_synthesize_once` monkeypatched para levantar `AssertionError`, dispositivo de áudio substituído por um coletor de `play_audio`):
  - `test_cached_line_plays_without_network`: `PhraseCache().put(s, True, pcm)`; `announce(s, wait=True, verbatim=True)` retorna `True` e o coletor recebeu exatamente `pcm`.
  - `test_cache_miss_offline_returns_fast`: `announce(outra, wait=True, verbatim=True)` retorna `False` em menos de 0,5 s e nenhum `_open_session` foi chamado; o log contém `[SPEECH_MISS]`.
  - `test_voice_readiness_cache_only`: `voice_readiness(genai_available=True, token=False, playback=True, cache_entries=10)` `== (True, "pronto (voz em cache, sem rede)")`; com `cache_entries=0` e sem token, `False`.
  - `test_cache_missing_lists_only_absent`.
  - `test_no_native_tts.py`: varredura de `mvp_mission_bebop/`, `streamer/`, `BMG/electron/`, `BMG/src/` sem as strings `espeak`, `spd-say`, `pyttsx3`, `festival`, `flite` (montadas em tempo de execução).

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest test/test_speech_offline.py test/test_no_native_tts.py -q
```
Expected: FAIL (símbolos inexistentes).

- [ ] **Step 3: Implement** os símbolos acima em `announcer.py` e o consumo em `main.cjs`. Conferir em `electron/main.cjs` e `src/lib/preflightGate.ts` quem lê `latestSpeechState.ready` para que "voz em cache" não bloqueie o lançamento nem mostre "sem voz".

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest test/test_speech_offline.py test/test_no_native_tts.py test/test_announcer_calls.py test/test_contracts.py -q
cd bebop_mission_control && npx vitest run src/lib/preflightGate.test.ts src/lib/mainHarness.test.ts
```
Expected: PASS. `test_contracts.py` ainda fixa o teto de 14 s em `SPEECH_CEILING_SEC`: não alterar.

- [ ] **Step 5: Commit** (se autorizado): `feat(announcer): fail fast on cache miss offline and report cache-backed voice readiness`

---

### Task 5: `precache_speech.py`, verificação e transporte do cache

**Files:**
- Create: `BMG/streamer/precache_speech.py`, `test/test_precache_speech.py`
- Modify: `mvp_mission_bebop/telemetry/announcer.py`

**Interfaces:**
- `announcer.py`: `MissionAudioAnnouncer.synthesize_blocking(self, statement: str, verbatim: bool = True, timeout: float = 30.0) -> bytes` (executa `_synthesize` no loop do announcer; `TypeError` se `statement` não for `str`; `RuntimeError` sem sessão) e
  ```python
  @dataclass(frozen=True)
  class PrecacheReport:
      synthesized: int
      skipped: int
      failed: Tuple[str, ...]

  def precache_phrases(statements: Sequence[str], *, cache: Optional[PhraseCache] = None, attempts: int = 3,
                       on_progress: Optional[Callable[[int, int, str], None]] = None) -> PrecacheReport
  ```
  Pula o que já está no cache; tenta até `attempts` vezes com recuo; rejeita áudio com menos de `0,25 s` por palavra (truncado) ou abaixo de `_MIN_AUDIO_BYTES`; grava via `PhraseCache.put` (atômico, `.part`). `ValueError` sem chave de API.
- CLI `streamer/precache_speech.py` (código de saída 0 ok, 1 faltam entradas em `--check`, 2 uso/rede/chave):
  `--manifest FILE` (padrão: executa `npx vite-node scripts/exportSpeechManifest.ts` em `BMG/`) | `--check` (não sintetiza; lista faltantes por categoria) | `--dry-run` | `--prune` (remove entradas do cache fora do manifesto, só do voice/model atuais) | `--pack FILE` / `--unpack FILE` (`tar.gz` stdlib com `manifest.json` contendo voz, modelo e SHA-1 dos arquivos; `--unpack` com voz/modelo diferentes do ambiente atual falha com `ValueError`) | `--table PATH` (gera `TABELA_DE_FALAS_COPILOTO.md`) | `--parameters FILE` (repassado ao exportador). Mescla `spoken_alerts.all_alert_sentences()` ao manifesto do TS. Imprime voz, modelo, total, já em cache, sintetizadas e falhas, de forma seca.
  Registrar `streamer/precache_speech.py` na documentação e, se o `setup.py` listar scripts, seguir o padrão existente.

- [ ] **Step 1: Write the failing tests** (`test_precache_speech.py`, `synthesize_blocking` substituído por gerador determinístico de PCM, cache em `tmp_path`):
  - `test_only_missing_are_synthesized_and_second_run_is_noop`: primeira execução sintetiza N; segunda, 0 e `skipped == N`.
  - `test_check_exit_codes`: cache vazio -> 1 com faltantes listados; cache completo -> 0.
  - `test_truncated_audio_is_rejected`: devolução curta demais para o número de palavras não é gravada e entra em `failed`; nenhum `.part` sobra.
  - `test_interrupted_run_leaves_no_partial_entry`: exceção no meio do lote; todas as entradas existentes são válidas (`PhraseCache.get` não `None`).
  - `test_prune_keeps_manifest_entries_only`.
  - `test_pack_unpack_roundtrip`: conjunto de arquivos e bytes idênticos; `--unpack` com `SPEECH_VOICE` diferente levanta `ValueError`.
  - `test_table_is_current`: executa a geração de tabela em arquivo temporário e compara com `BMG/docs/TABELA_DE_FALAS_COPILOTO.md` versionado (pula se `npx` indisponível).

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest test/test_precache_speech.py -q
```
Expected: FAIL.

- [ ] **Step 3: Implement** `synthesize_blocking`, `precache_phrases` e o CLI. Reutilizar `PhraseCache`, `SYNTHESIZER_VOICE`, `SYNTHESIZER_MODEL`, `_resolve_auth_token`; não instanciar nenhum sintetizador novo nem voz alternativa.

- [ ] **Step 4: Run tests e gerar a tabela**

```bash
python3 -m pytest test/test_precache_speech.py test/test_speech_offline.py -q
python3 bebop_mission_control/streamer/precache_speech.py --dry-run
python3 bebop_mission_control/streamer/precache_speech.py --table bebop_mission_control/docs/TABELA_DE_FALAS_COPILOTO.md
python3 -m pytest test/test_banned_terms.py -q
```
Expected: PASS; `--dry-run` lista contagem por categoria sem rede; a tabela nova não contém o termo proibido. A síntese real (com internet e `SPEECH_API_KEY`) é a etapa do usuário na Task 8.

- [ ] **Step 5: Commit** (se autorizado): `feat(streamer): add speech pre-cache, verification and pack tooling for offline operation`

---

### Task 6: Remover o laudo HTML externo

**Files:**
- Modify: `BMG/src/components/cockpit/ForensicPanel.tsx`, `BMG/electron/main.cjs`, `BMG/electron/preload.cjs`, `BMG/src/types/bmg.ts`
- Modify (tests): `BMG/src/components/cockpit/ForensicPanel.test.tsx` (bloco `describe('Visualizar Laudo Pericial')` a partir da linha 219), qualquer teste/fixture que cite os símbolos removidos
- Não modificar: `mvp_mission_bebop/telemetry/inference_package.py`, `blackboard.py` (decisão D6)

**Interfaces:** `ForensicPanel` mantém as props atuais. Removidos: `ReportModal`, `REPORT_POLL_MS`, `REPORT_WITHOUT_IDENTIFIER`, `expectedReportUrl`, estados `reportUrl`/`isModalOpen`, `resolveReport`, o botão `data-testid="open-report"`, `allTopicsSpoken`, imports sem uso (`FileText`, `Maximize2`, `Minimize2`, `X`, `useBridge`, `RawEvidence` se ficar sem uso). `main.cjs`: remover `isReportFilename`, `reportOccurrenceId`, `REPORT_WITHOUT_IDENTIFIER`, o ramo de `bmg:report-ready` no watcher (~936-962) e o handler `bmg:get-report-url` (~1851); manter o servidor `/evidence/` para as imagens. `preload.cjs`: remover `getReportUrl` e `onReportReady`. `bmg.ts`: remover `ReportReadyEvent` e os dois membros de `BmgAPI`.

- [ ] **Step 1: Write the failing tests** (`ForensicPanel.test.tsx`, substituindo o bloco antigo):
  - `it('offers no HTML report entry point after the last finding')`: com `report` completo e `reportRevealed === report.length`, `queryByTestId('open-report')` é `null` e `queryByText(/Visualizar Laudo Pericial/)` é `null`.
  - `it('never asks the host for an HTML report')`: bridge mock sem `getReportUrl`/`onReportReady` (ou espiões) e `render` completo não os chama.
  - `it('shows the four cards in the drawn order')` com `report` do cenário A e B (reaproveita `buildForensicReport`).
  - Guard (em `bannedTerms.guard.test.ts` ou teste próprio): `electron/`, `src/` e `streamer/` não contêm `relatorio_` fora de `inference_package` (que não é varrido) nem `bmg:get-report-url`/`bmg:report-ready`.

- [ ] **Step 2: Run to verify they fail**

```bash
cd bebop_mission_control && npx vitest run src/components/cockpit/ForensicPanel.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement** as remoções. Antes de editar, `grep -rn "getReportUrl\|onReportReady\|bmg:report-ready\|bmg:get-report-url\|expectedReportUrl\|REPORT_POLL_MS" BMG/src BMG/electron ../test` e tratar cada ocorrência, inclusive `test/test_contracts.py` se fixar esses canais IPC.

- [ ] **Step 4: Run tests e build**

```bash
cd bebop_mission_control && npx vitest run && npm run build
cd .. && python3 -m pytest test/test_contracts.py -q
```
Expected: PASS; `npm run build` com código de saída 0 e sem erro de TypeScript.

- [ ] **Step 5: Commit** (se autorizado): `refactor(bmg): drop external HTML report modal, polling and IPC`

---

### Task 7: Rodapé do mapa fixo e grade offline

> **Parcialmente substituída em 2026-10-24.** O modo `basemap: "grid"` descrito aqui removeu a imagem de satélite por engano. Vale o rodapé (e a remoção de `useReverseGeocode`); o basemap segue `docs/PLANO_MAPA_OFFLINE_EXPOCENTER.md`: satélite local de `public/tiles`, grade só como fallback.

**Files:**
- Modify: `BMG/config/site-anchor.json`, `BMG/electron/siteAnchor.cjs`, `BMG/src/lib/siteAnchor.ts`, `BMG/src/components/cockpit/TacticalMap.tsx`
- Delete se não restar usuário: `BMG/src/hooks/useReverseGeocode.ts` (único consumidor é `TacticalMap.tsx`)
- Modify (tests): `BMG/src/lib/siteAnchor.test.ts`, `BMG/src/components/cockpit/TacticalMap.test.tsx`

**Interfaces:**
- `site-anchor.json` ganha `"city": "São Paulo"` e `"basemap": "grid"` (`"grid"` nunca solicita tiles; `"auto"` mantém o comportamento atual). Coordenadas e `name` inalterados.
- `readSiteAnchorFile` passa a devolver `city: string | null` e `basemap: 'grid' | 'auto'` (padrões `null` e `'auto'`).
- `MAP_ANCHOR` ganha `city: 'São Paulo'` e `basemap: 'grid'`; o teste "is the shipped site file, to the digit" continua exigindo igualdade com o JSON.
- Rodapé de `TacticalMap` (texto exato, lido por `textContent` normalizado): `BASE FIXA: -23.648891, -46.718712`, `Cidade: São Paulo`, `Localização: Transamerica Expo Center`, com ícones `MapPin` e `Building2` (já importados). Os valores vêm só de `MAP_ANCHOR` (a base é fixa; hoje usa `aircraft.lat/lng`). `GeoField` ganha o separador `:` entre rótulo e valor; as três células usam `truncate`/`min-w-0` e a barra aceita quebrar em duas linhas em largura estreita sem sobrepor.
- Com `MAP_ANCHOR.basemap === 'grid'`: `tilesOk` inicia `false`, nenhum `<img>` de tile é montado, a sonda de 45 s não roda, `useReverseGeocode` não é chamado e não há `fetch` externo.

- [ ] **Step 1: Write the failing tests**
  - `siteAnchor.test.ts`: o arquivo entregue tem `city === 'São Paulo'` e `basemap === 'grid'`; `readSiteAnchorFile` sem os campos devolve `city: null`, `basemap: 'auto'` (ajustar o `toEqual` de "defaults the name and accuracy"); `MAP_ANCHOR` igual ao JSON em `city` e `basemap`.
  - `TacticalMap.test.tsx`: `it('footer shows the fixed base, city and location')` verifica os três textos exatos acima; `it('does not move the base coordinates with the aircraft')` renderiza com a aeronave deslocada e o texto de coordenadas não muda; `it('renders the grid with no tile request and no network call')`: `container.querySelectorAll('img[src*="http"]')` vazio, `fetch` espiado e nunca chamado, `queryByText(/GRADE TÁTICA LOCAL/)` presente, sem `console.error`.

- [ ] **Step 2: Run to verify they fail**

```bash
cd bebop_mission_control && npx vitest run src/lib/siteAnchor.test.ts src/components/cockpit/TacticalMap.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement** as alterações. Ajustar a mensagem `title` do rótulo de fonte (`GEO_LABEL`) para nunca citar "mapa online" em modo `grid`; `basemapState` mostra `grade offline`.

- [ ] **Step 4: Run tests, build e verificação visual**

```bash
cd bebop_mission_control && npx vitest run src/components/cockpit src/lib/siteAnchor.test.ts && npm run build
```
Depois, `npm run dev` e, no navegador embutido (sem rede), abrir o cockpit em larguras de painel estreita e larga; conferir rodapé legível sem sobreposição, grade desenhada, console sem erros de rede. Anexar captura ao relatório final.

- [ ] **Step 5: Commit** (se autorizado): `feat(bmg-map): fixed base footer with city and location, offline grid basemap`

---

### Task 8: Verificação ponta a ponta offline e entrega

**Files:**
- Modify: `docs/CHECKLIST_VOO_REAL.md` (nova seção "Feira offline")

- [ ] **Step 1: Suites completas**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/ -q
cd bebop_mission_control && npm test && npm run build; echo "build exit=$?"
```
Expected: tudo verde, `build exit=0`. Qualquer falha pré-existente registrada na Task 0 é reportada separadamente.

- [ ] **Step 2: Pré-síntese com internet (ação do usuário, exige `SPEECH_API_KEY` e rede)**

```bash
python3 bebop_mission_control/streamer/precache_speech.py
python3 bebop_mission_control/streamer/precache_speech.py --check
python3 bebop_mission_control/streamer/precache_speech.py --pack ~/bmg-speech-feira.tar.gz
```
Expected: `--check` com código de saída 0. Na máquina da feira: `--unpack ~/bmg-speech-feira.tar.gz`, depois `--check` com os mesmos `SPEECH_VOICE`/`SPEECH_MODEL`. Estimar e informar o tempo real da síntese (~2 s por frase).

- [ ] **Step 3: Rehearsal offline em bancada (`--no-fly`)** — o usuário desliga a rede (`nmcli networking off` ou desconectar o cabo/Wi-Fi com internet; o Wi-Fi do Bebop não tem rota, o que é o cenário real). Rodar a missão em modo bancada pelo BMG (`main.cjs` passa `--no-fly` quando "bancada" está marcado). Forçar os dois cenários do laudo com as imagens de `test/fixtures` quando existirem (vítima presente e ausente); se não houver forma de forçar `victim_detected`, cobrir pelo teste de componente e declarar a lacuna. Critérios, lidos no log da missão:
  - toda linha `[SPEECH]` com `source=cache`; zero `source=synth|stream|prefetch`; zero `[SPEECH_MISS]` com os parâmetros pré-sintetizados;
  - `grep -ic 'sinistro'` no log da missão e na tela: 0;
  - laudo abre com Veículo (com guincho) sem vítima, e com Vítima primeiro quando detectada; sem botão "Visualizar Laudo Pericial";
  - rodapé do mapa com latitude, longitude, cidade e localização exatas, grade visível desde o primeiro quadro;
  - nenhuma tentativa de rede nos logs do daemon (sem `warm-up failed`).
  Repetir com um parâmetro alterado (altitude ou velocidade) para observar o comportamento de miss: linha pulada em milissegundos com `[SPEECH_MISS]`, cards seguindo na cadência própria.

- [ ] **Step 4: Documentar** o procedimento acima em `docs/CHECKLIST_VOO_REAL.md` (seção "Feira offline": pré-síntese, `--check`, `--pack/--unpack`, mesma voz/modelo, regra de não alterar parâmetros falados sem repetir a pré-síntese).

- [ ] **Step 5: Fechar a sessão** (somente com autorização): commits atômicos por tarefa, `git status` limpo, push conforme autorização do usuário.

---

## Self-review (cobertura do pedido)

| Requisito do prompt | Tarefa |
|---|---|
| 1. Banir "sinistro", substituições, exemplos obrigatórios | 1 |
| 1. Guincho no card e na fala do veículo, com variações | 2 |
| 2. Ordem determinística do laudo (Cenários A e B, SAMU/PM alternados) | 2 |
| 3. `TABELA_DE_FALAS_COPILOTO.md` com voo, laudo e falhas (aborto, link, bateria, timeout, calibração) | 3 (conteúdo e frases novas), 5 (gerador) |
| 4. Voz oficial offline, pré-síntese, `announcer.py` tocando do disco, sem `espeak` | 3 (manifesto finito), 4, 5, 8 |
| 5. Remover laudo HTML, botão, polling, IPC | 6 |
| 6. Rodapé do mapa e grade sem rede | 7 |
| Critérios de validação (`npm test`, `npm run build`, bancada offline) | 8 |

Consistência de nomes entre tarefas: `leadTopicsFor`/`lead_topics_for` (2), `SpeechEntry`/`buildSpeechManifest`/`FIXED_ALERT_SENTENCES` (3, usados em 5), `PhraseCache.missing`/`synthesis_reachable`/`voice_readiness` (4), `precache_phrases`/`synthesize_blocking` (5), `MAP_ANCHOR.city`/`basemap` (7).

## Pendências

- Registrar aqui, ao implementar a Task 3, cada call site de alerta do Python com texto não enumerável, e o resultado de D1 a D8 (todas já decididas; registrar só desvios).

### Call sites de alerta com texto não enumerável (Task 3)

Offline, cada uma destas linhas é um cache miss: pulada em milissegundos com `[SPEECH_MISS]`.

1. `mvp_mission_bebop/mission.py:437`, `emit_alert("mission.launch_refused", {field, reason})`: a estação fala `Lançamento recusado: <reason>.`. Os motivos (`launch_envelope.py:89,179,183,193,195`, `parameters.py:ParameterTypeError`, `mission.py:launch_refusal`) embutem o valor recusado (`{value!r}`, `{altitude:.2f} m`, texto da exceção). Só o fallback `Lançamento recusado: documento inválido.` está no manifesto.
2. `mvp_mission_bebop/steps/takeoff.py:328`, recusa de decolagem por bateria antes de armar: `Falha na decolagem: bateria em {percentage} por cento, no limiar de pouso de {land_pct} por cento, decolagem cancelada.` A carga é uma medição; não enumerada.
3. `mvp_mission_bebop/mission.py:884`, falha de conexão com o driver: cita `network.drone_ip`. Enumerada só para o padrão `192.168.42.1`; outro IP gera frase fora do cache.
4. `mvp_mission_bebop/engine/runner.py:234`, fallback `etapa {stage}` para etapa fora de 1 a 5: inalcançável com o pipeline de cinco etapas; não enumerado.
5. Resolvido nesta execução: o aborto do runner (`engine/runner.py:151`) chegava sem ponto final (`Missão abortada, pousando drone`). O formatador do `announcer.py` passou a fechar a frase (`_sentence`), e ela entrou em `spoken_alerts.py` como `falha.aborto`.

### Desvios e decisões tomadas na execução (2026-10-24)

- D2: o usuário confirmou o texto do plano (fala de detecção sem distância e sem lado, sempre "na pista"). As quatro variantes que o plano não redigiu ficaram `Alvo identificado na pista.`, `Ocorrência localizada na pista.`, `Detecção confirmada na pista.` e `Alvo avistado na pista.`.
- D1, D3, D4, D5, D6, D7: sem desvio. Limiar de bateria falado: é a constante `BATTERY_WARNING_PCT = 10.0` (`telemetry/battery.py`), não um parâmetro de configuração; a linha em voo sempre coincide com a pré-sintetizada.
- D8: as Tasks 1 e 7 rodaram em paralelo, depois 2 e 4, depois 3 e 6, e por fim a 5. As Tasks 4 e 6 não rodaram juntas porque as duas editam `electron/main.cjs`. Nenhum commit sem autorização.
- Task 3: `TABELA_DE_FALAS_COPILOTO.md` é gerada pela Task 5 (`--table`), não escrita à mão. O aviso de perda de enlace usa `useTelemetry().stale` com a missão em execução (fora de bancada), uma vez por perda, e só rearma depois de 3 s de telemetria fresca (`LINK_REARM_DWELL_MS`). A ligação exigiu 3 linhas em `App.tsx` e `App.launchParams.test.tsx`.
- Task 4: `test/conftest.py` (novo) fixa `BMG_SPEECH_OFFLINE=0` em todos os testes, para nenhum teste abrir conexão real. `synthesis_reachable` devolve o último veredito e atualiza em segundo plano; só a primeira sondagem bloqueia, por até 1 s.
- Task 5: com nomes de arquivo em hash, não dá para restringir o `--prune` à voz e ao modelo atuais. Ele remove o que o manifesto não referencia, de qualquer voz, mas recusa com menos de metade do manifesto presente e só apaga com `--yes`. Entradas curtas (menos de 0,25 s por palavra) contam como faltantes em `--check`, são sintetizadas de novo e bloqueiam `--pack`.
- Task 7: o rodapé vem de `MAP_ANCHOR` (`basemap: "grid"`); `useReverseGeocode.ts` foi removido.

### Não verificado nesta execução

- Síntese real, `--check` com código de saída 0, `--pack` e `--unpack` entre máquinas (Task 8 Step 2: exige internet e `SPEECH_API_KEY`, ação do usuário). Estado atual da máquina de desenvolvimento: 739 frases, 257 em cache, 482 faltam.
- Ensaio offline em bancada (Task 8 Step 3), inclusive o cenário de miss com parâmetro alterado.
- Forçar `victim_detected` em bancada: coberto só por testes de componente e de unidade (`forensics.test.ts`, `ForensicPanel.test.tsx`, `test_inspection_report.py`).
- Conferência visual do rodapé e da grade do mapa em painel estreito e largo (Task 7 Step 4): o subagente não tinha tela.
- Latência de DNS na estação da feira conectada ao Wi-Fi do Bebop (`time getent hosts generativelanguage.googleapis.com`). Se passar de cerca de 100 ms, usar `BMG_SPEECH_OFFLINE=1` no launcher.
- `nectar-sdk` não tem código de fala ou TTS: nada a reutilizar (regra 2.1 do CLAUDE.md verificada) e nada foi editado lá.
