# Especificação de Arquitetura — BMG Mission Experience Upgrade

Data: 2026-09-24
Autor (sessão): Claude (arquiteto), revisado por joao.moreira@tech4h.com.br

## 1. Escopo

Upgrade transversal da experiência de missão do BMG (Bebop Mission GUI), cobrindo:
anti-repetição de falas do copiloto, sincronismo estrito entre voo/visão/áudio/UI,
reveal controlado de bounding box, warm-up de bancada, bloqueio de navegação,
destaque visual dos 4 pontos de inspeção, auditoria de persistência de parâmetros,
terminal de diagnóstico real (PTY), filtro rígido de Wi-Fi e correção do mapa
tático. Não inclui: mudanças no algoritmo de controle de voo (IBVS, RTL), novo
hardware, ou mudança de modelo YOLO.

## 2. Estado atual relevante (grounding)

- Stage narration hoje é decidida no **frontend** (`useFlightNarration`,
  `STAGE_CALLS` fixo, 1 frase por estágio) — `bebop_mission_control/src/hooks/useCopilot.ts`.
- Forensic report (4 pontos) já tem pool de variações por voo
  (`FORENSIC_FINDINGS` / `build_forensic_report`, espelhado em
  `src/lib/forensics.ts`), mas **sem memória entre voos** — apenas shuffle
  por seed do voo atual.
- `announcer.py` é o player TTS (fila de prioridade, Gemini Live API); não
  decide o conteúdo narrado pelo roteiro de voo, só o reproduz.
- Bounding box é desenhada incondicionalmente no servidor
  (`context.py:publish_annotated_stream`); não há flag de visibilidade.
- Bancada (`--no-fly`) pula o countdown inteiro hoje (`App.tsx:launch`).
- Bloqueio de navegação já existe (`TabSwitch.tsx`), condicionado a
  `running && !benchMode && benchStage === null`.
- 4 pontos já revelam progressivamente (`ForensicPanel.tsx`,
  `reportRevealed` pareado a `useForensicNarration`).
- Persistência de parâmetros já faz round-trip atômico do documento completo
  (`main.cjs: bmg:save-parameters`) e já corrigiu o bug histórico do latch de
  `no_fly` (`mission.py:274-300`).
- Terminal de diagnóstico é emulador hand-rolled (`DiagnosticsScreen.tsx`),
  sem PTY real, sem Tab-completion nativo.
- Filtro de Wi-Fi hoje é soft (`BEBOP_SSID_RE = /^Bebop2?[-_]/i`, apenas
  prioriza/tag, não esconde redes).
- Mapa tático (`TacticalMap.tsx`) é hand-rolled sem lib de mapas; cadeia de
  fontes de coordenada: GPS fix → geolocalização do operador + offset ENU →
  base lat/lon em cache → `{0,0}`.

## 3. Decisões de arquitetura por subsistema

### 3.1 Anti-repetição de falas (TTS)

- Fonte de verdade da seleção de fala permanece no **frontend** (consistente
  com o padrão já usado por `STAGE_CALLS`/`forensics.ts`); `announcer.py`
  continua sendo apenas o player.
- Novo módulo `src/lib/copilotPhrases.ts`: pools de 3–5 variantes por chave
  de marco do roteiro (ver tabela §4) + as 4 chaves de achado forense
  existentes.
- Novo histórico persistido `localStorage["bmg.copilot-history.v1"]`:
  últimos **5 voos**, por chave de marco/achado, guardando o índice de
  variante usado. `pickVariant(key, pool, history)` exclui variantes usadas
  nesses 5 voos; se `pool.length <= history window`, cai de volta no pool
  completo (degrada sem erro).

### 3.2 Máquina de estados & sincronismo

- Transporte inalterado: stdout `[STEP N: ...]` → regex em `main.cjs` →
  IPC `bmg:step-change` → hooks React.
- Cada um dos 5 `StepStatus` estágios do backend passa a carregar **marcos
  internos** mais finos (o roteiro de 9 pontos do usuário é mais granular
  que os 5 estágios), tabela completa em §4.
- Regra anti-atropelo (decidida: **voo não espera a narração**): a máquina
  de voo (Python) continua avançando em seu próprio ritmo. O frontend
  implementa uma **fila FIFO** de marcos pendentes em `useFlightNarration`,
  consumida um de cada vez, gated em `announceDone` — nunca pula um marco,
  mesmo que o evento de estágio seguinte já tenha chegado.

### 3.3 Reveal de bounding box

- Gate **server-side**: novo campo `detection_reveal_enabled: bool` em
  `MissionContext` (`context.py`), `False` até o marco "varredura iniciada",
  `True` daí em diante até o fim do voo.
- `publish_annotated_stream()` pula `draw_detections(...)` quando a flag é
  `False`, republicando o frame cru no mesmo tópico de detecções — evita
  troca de tópico no lado do bridge/MJPEG (`OpticalFeed.tsx`), que já teve
  um bug de instabilidade documentado (`useStreamHealth`).
- YOLO continua rodando desde a decolagem — só o desenho da caixa é
  suprimido, não a inferência.

### 3.4 Bancada — warm-up

- Novo overlay de 10s exclusivo de bancada ("Carregando pipeline de vídeo e
  pesos YOLO..."), mecanismo separado do countdown de voo real (que
  continua pulado em bancada, sem mudança). Inserido no caminho
  `App.tsx: launch()` quando `benchMode === true`.
- Coexistência ArUco + sinistro em bancada: já funciona, sem mudança.

### 3.5 Bloqueio de navegação

- Mecanismo já existe e cobre voo real. Item de verificação (não redesign):
  confirmar em runtime que `running` fica `true` já no início do countdown
  (clique em "Iniciar Missão"), não só após decolagem de fato — corrigir se
  o teste mostrar que não é o caso.

### 3.6 UI dos 4 pontos

- Posição inalterada (coluna já existente). Só reforço visual: contraste,
  hierarquia tipográfica, borda ativa/glow em `shown = index < reportRevealed`
  (mecanismo de reveal já existe, sem mudança de dados).

### 3.7 Persistência de parâmetros de voo

- Auditoria + teste manual (editar → salvar → relançar) em vez de reescrita;
  código já implementa round-trip atômico e envio do documento completo via
  `--params-json` no launch. Corrigir apenas o que o teste evidenciar.

### 3.8 Terminal de diagnóstico

- Substituir emulador hand-rolled por **`node-pty`** (processo principal
  Electron) + **`xterm.js`** (React), sessão persistente por PTY real
  (Tab-completion nativo do shell).
- Novo contrato IPC: `bmg:terminal-spawn`, `bmg:terminal-write` (bytes crus,
  incluindo Tab/setas), `bmg:terminal-resize`, `bmg:terminal-data`
  (stream de saída), `bmg:terminal-kill`. Substitui `terminalExec` por
  comando único.
- Atalho de emergência `land` interceptado na camada de input **antes** de
  ser encaminhado ao PTY, para manter resposta instantânea.
- Visualização de logs (mission/driver) permanece separada, fora do PTY.
- Botão de fechar: círculo vermelho, canto superior esquerdo do modal,
  substituindo o X atual no canto superior direito.

### 3.9 Filtro de Wi-Fi

- Trocar de soft-priorização para filtro rígido, reaproveitando o regex já
  correto `BEBOP_SSID_RE = /^Bebop2?[-_]/i` (não `BebopDrone-*`, que não
  corresponde à realidade do driver). Aplicar em `main.cjs` (scan handler) e
  em `ConnectionSheet.tsx` (lista exibida) — redes fora do padrão somem da
  lista em vez de só perderem prioridade.

### 3.10 Mapa tático

Dois problemas independentes reportados pelo usuário:

- **(a) Escala visual**: marcadores, rotas e tiles pequenos demais, difícil
  de enxergar. Aumentar espessura de traço, tamanho de marcador, escala de
  fonte dos labels; reexaminar o sharding de subdomínio de tiles (comentário
  no código já menciona uma classe de bug de "mapa meio desenhado").
- **(b) Precisão de coordenadas**: posição exibida não bate com a posição
  real; "imprecisão em todos os dados". Requer investigação em runtime
  (não só leitura estática) na cadeia `geo` (`TacticalMap.tsx`): GPS fix →
  geolocalização do operador + `fromEnu()` → base lat/lon em cache →
  fallback `{0,0}`. Suspeitos prioritários: conversão ENU↔lat/lon
  (`lib/geo.ts:fromEnu`) e possível `baseLatitude`/`baseLongitude` stale ou
  incorreto sendo repassado.

## 4. Tabela de Sincronismo (Máquina de Estados do Roteiro)

| # | Estado / Marco | Gatilho (HW/Tempo) | Fala do copiloto (pool key) | Ação UI |
|---|---|---|---|---|
| 1 | Início da missão | Clique operador em "Iniciar Missão" | `mission.start` (carregando parâmetros) | Countdown visual inicia; nav lock engaja |
| 2 | Countdown = 3s | Timer local | `mission.countdown_3` (parâmetros carregados, iniciando) | Countdown continua visível |
| 3 | Decolagem | `[STEP 1: TAKEOFF]` + evento de liftoff | `mission.takeoff` (cita altitude configurada) | Estado "decolando"; YOLO já ativo, bbox oculta |
| 4 | Varredura iniciada | `[STEP 2: SEARCH]` | `mission.scan_start` | `detection_reveal_enabled=False` mantido; feed sem caixa |
| 5 | Sinistro detectado | 1ª detecção válida (mesmo que precoce) enfileirada na fila FIFO | `mission.target_found` (localização) | `detection_reveal_enabled=True`; bbox aparece |
| 6 | Aproximação | `[STEP 3: TRACKING]` | `mission.approaching` | Indicador de deslocamento até o alvo |
| 7 | Captura fotográfica | `record_photographic_evidence()` concluído | `mission.capture_done` (foto enviada p/ inspeção) | Flash/indicador de captura |
| 8 | RTH início | `[STEP 5: RTL]` | `mission.rtl_start` | Estado "retornando" |
| 9 | Pouso | Confirmação de touchdown (antes do toque) | `mission.landing` (aviso prévio) | Estado "pousando" |
| 10 | Inspeção — intro | `land` confirmado | `inspection.intro` | — |
| 11–14 | 4 pontos do sinistro | Cada fala de ponto concluída (`announceDone`) | `inspection.point_1..4` (pool, sem repetir topic order do voo anterior) | Cada card revela ao final da fala correspondente (`reportRevealed++`) |
| 15 | Encerramento | 4º ponto revelado | `inspection.outro` | Botão "Finalizar missão" habilitado |

Todos os itens 1–9 e 15 usam a fila FIFO decoupled (§3.2): mesmo que o marco
5 dispare cedo, os marcos 1–4 já devem ter tocado antes dele ser consumido.

## 5. Riscos / itens que exigem validação em runtime

- Offset de coordenadas do mapa: causa raiz não confirmável por leitura
  estática; precisa de telemetria real ou simulada em bancada para
  comparar `geo` calculado vs. posição conhecida.
- Timing exato de `running` em `App.tsx` vs. início do countdown (nav lock).
- Se `node-pty` está disponível/compilável no ambiente Electron alvo
  (requer rebuild nativo por versão do Electron) — validar antes de
  remover o emulador antigo.

## 6. Fora de escopo

- Mudanças em algoritmos de controle de voo, filtros de pose, ou detecção
  ArUco/AprilTag em si.
- Troca do provedor de TTS (Gemini Live API mantido).
- Novo hardware ou driver.
