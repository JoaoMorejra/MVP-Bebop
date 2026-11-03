# Plano de implementação: mostrador, vídeo ao vivo, painel de evidência e cabine

Documento para a sessão que for implementar. Só arquitetura e passo a passo: nenhum código foi alterado ao escrevê-lo.

Escopo: `bebop_mission_control/` (frontend e ponte de vídeo) e os pontos da missão que desenham no vídeo.
Regras que valem para toda a implementação:

- Não commitar nem dar push sem autorização explícita do usuário (regra de memória do projeto, acima do check-out do CLAUDE.md).
- Sem emojis em código, comentários, logs ou mensagens de commit. Comentários só quando justificam uma decisão técnica.
- Rodar a suíte do frontend com `npm test` (um arquivo por vez, `--no-file-parallelism`). Os testes `launchArguments`, `singleVoice` e `launchReadinessMain` falham com o BMG aberto ou com um `mission.py` em espera: feche o app antes de concluir que há regressão.
- Python sempre depois de `source /home/jv/ros2_ws/bin/nectar-activate`.
- Mexer só nos arquivos listados em cada parte. Outras sessões editam a mesma árvore: rodar `git status` antes de começar cada parte e não reverter o que não for desta tarefa.
- Verificação visual sempre em página de simulação temporária (como nas rodadas anteriores), removida ao fim. Nunca deixar `*-sim.html` ou `src/__*__.tsx` na árvore.

---

## 0. Estado verificado em 02/11/2026 (leia antes de implementar)

A árvore já está à frente do que o pedido supõe. Parte do trabalho existe; o plano cobre só o que falta.

| Ponto do pedido | Estado hoje | O que falta |
|---|---|---|
| A. Mostrador sempre com os detalhes do "iniciar", bloqueado com o motivo em branco, animação própria para "bloqueado" e para "pronto" | `LaunchDial.tsx` já tem anel, núcleo, aura, dois feixes que convergem (bloqueado) e se fundem (pronto), halo de pronto e texto do motivo (`preflightBlockedShort`). Os detalhes mais ricos (arco de progresso, faixa branca, aura crescente, texto grande) só existem em `running`. | Redesenho: anatomia única para todos os estados, linguagem de animação distinta e didática para bloqueado e para pronto, motivos mais claros, vários bloqueios, transição de destravar. |
| B. Remover a caixa preta com letras verdes do vídeo ao vivo | O desenho do banner já não existe em `streamer/mjpeg_server.py` (`draw_detection_summary` só desenha caixas) nem em `context.py` (`publish_annotated_stream`). Foi removido no commit `13bd34c`. | Descobrir por que o operador ainda vê o texto (processo antigo? outra fonte?) e travar com teste e limpeza de resíduos. |
| C. Grid reveal desde o início do voo, 4x, revelação imediata na captura, scan na imagem | `EvidenceGrid.tsx`: `GRID_TEMPO = 12` (4x de 3), crescimento desde a decolagem (`FLIGHT_GROWTH_TAU_MS`), revelação imediata na captura. `ForensicPanel.tsx`: `scanningInFlight` mostra `.evgrid-scan` em voo. | O scan está errado: cobre o painel inteiro (inclui as faixas pretas fora da foto), é vertical, repete sem fim e some de repente no pouso. A revelação, com `CLOSE_TAU_MS = 35 ms`, acontece quase em um quadro. |
| D. Cabine sem missão: só vídeo e mapa | Já feito: `CockpitScreen.tsx` devolve `data-cockpit-layout="standby"` (vídeo 1,5fr e mapa 1fr, sem faixa de etapas, sem abortar, sem painel forense) quando `missionState === 'idle'` e não há `benchStage`. Há teste em `CockpitScreen.test.tsx:390`. | Dois layouts são duas árvores: na troca para missão o vídeo e o mapa são desmontados e remontados. Falta uma transição suave e decidir o que mostrar no vídeo sem sinal. |

---

## 1. Decisões a confirmar com o usuário (com o padrão que o plano adota)

| # | Dúvida | Padrão adotado |
|---|---|---|
| D1 | O que "sem o sinal" quer dizer na cabine em espera: esconder o aviso "Sem sinal óptico" ou mostrar o vídeo mesmo sem sinal? | Sem sinal, mostrar um quadro escuro limpo no tamanho do vídeo, sem ícone nem texto de erro; o chip de estado ("sem vídeo") continua no canto. Com sinal, o vídeo toma o espaço inteiro. |
| D2 | O rótulo da caixa da YOLO ("Acidente 0.85", "Vitima 0.66") fica? | Fica. É parte da bounding box, como no desenho aprovado. Some só o resto (faixa de status, cruz, texto solto). |
| D3 | O scan em voo é uma passagem só, que acompanha o voo até o pouso, ou varreduras repetidas? | Uma passagem única, da esquerda para a direita, com a posição ligada ao progresso do voo; chega à borda direita exatamente no pouso. |
| D4 | Depois de uma falha ao iniciar ("Falha ao iniciar"), o mostrador pode ser clicado de novo? | Pode. O estado de falha mostra o texto em branco e aceita novo clique; só os bloqueios do gate impedem o clique. |
| D5 | Vários bloqueios ao mesmo tempo: mostrar só o primeiro ou alternar? | Mostrar o primeiro e, havendo mais, alternar a cada 2,6 s com contagem em pontos no anel (item A.4). |

---

## Parte A. Mostrador "Iniciar" (LaunchDial)

Arquivos: `src/components/preflight/LaunchDial.tsx`, `LaunchDial.test.tsx`, `src/lib/preflightGate.ts`, `src/components/preflight/PreflightScreen.tsx`, `src/styles/globals.css` (bloco "LaunchDial", linhas 330 a 470, mais o bloco `prefers-reduced-motion` perto da linha 2190).

### A.1 Referência de aparência

O estado `running` (contagem) é o que o usuário quer ver o tempo todo. Anatomia atual dele, em ordem de camadas:

1. aura radial atrás (`data-dial-aura`), cuja intensidade sobe com o avanço;
2. anel externo e anel interno (`data-dial-outer-ring`, `data-dial-inner-ring`);
3. núcleo escuro com sombra interna na cor do tema (`data-dial-core`);
4. arco de progresso em traço de 3,5 px (`data-progress-arc`);
5. faixa de luz ao redor, em cônica, com a ponta branca (`data-dial-perimeter-fill`) e sombra projetada;
6. texto grande no centro.

Hoje as camadas 4 e 5 só aparecem em `running` (`opacity: 0` fora dele) e o centro troca de estrutura. A mudança é fazer as seis camadas existirem em todos os estados e variar só a energia e o movimento.

### A.2 Modelo de estados

Hoje: `ready | no-driver | blocked | running`. Passa a:

| Estado | Quando | Clicável |
|---|---|---|
| `blocked` | há um ou mais bloqueios do gate (inclui `no-driver`, que vira só um caso de `blocked` com mais bloqueios) | Não |
| `unlocking` | transição de `blocked` para `ready`, dura 900 ms, só ocorre uma vez por destravamento | Não |
| `ready` | gate livre | Sim |
| `failed` | `errorShort` presente com o gate livre | Sim (D4) |
| `running` | sequência de lançamento ativa | Não |

`data-dial-state` continua no botão. `no-driver` deixa de ser estado visual separado: o número de bloqueios restantes já diz o quão longe está.

Cor: o tema continua só `flight` (mint) ou `bench` (ciano). Bloqueado não usa vermelho nem âmbar: é o mesmo matiz em energia baixa (pedido: "sempre nesse tom de cor"). O texto central é sempre branco, `#FFFFFF`, não o `frost` atual.

### A.3 Linguagem de animação (a diferença que o usuário pediu)

Princípio didático: bloqueado é uma luz que procura e ainda não fechou; pronto é uma luz fechada que pulsa.

**Bloqueado (`blocked`)**

- Anel: contorno completo a 25% de opacidade, tracejado em marcas finas (72 marcas), parado. Não há arco fechado.
- Feixes: os dois feixes atuais orbitando, defasados em `gap = 180° * restantes / total`, com `total` = número de gates ativos naquele modo (7 em voo real, 2 em Teste). Cada gate que passa aproxima os feixes de forma contínua, com a transição de `transform` de 950 ms que já existe. Substitui a tabela fixa `resolveBeamGapDeg` (135, 105, 70, 48).
- Aura: respiração lenta, opacidade de 0,35 a 0,6, período de 3,6 s.
- Núcleo: um pouco mais escuro (menos brilho na sombra interna). Cursor `not-allowed`, sem `hover:scale`.
- Pontos de contagem (só se houver 2 ou mais bloqueios): até 4 pontos pequenos no arco inferior do anel, um por bloqueio restante, o ativo em branco; ver A.4.

**Destravando (`unlocking`, 900 ms, uma vez)**

1. 0 a 500 ms: os feixes completam a convergência e o arco de progresso cresce de 0% a 100% (curva ease-out), com a ponta branca e a sombra projetada, como na contagem.
2. 500 a 700 ms: clarão no anel (brilho +40% e volta) e um anel de onda sai do contorno, cresce 14% e some.
3. 700 a 900 ms: texto troca de motivo para "Iniciar" com o `text-states-swap` já existente.

**Pronto (`ready`)**

- Anel completo em cor viva, contorno externo a 55% (o mesmo do `running`), feixe único fundido com sombra projetada.
- Batimento a cada 2,4 s: a aura sobe até 1,0 e escala 1,03, e um anel de onda sai do contorno (cresce 14%, opacidade de 0,5 a 0), como um radar. O período de 2,4 s reaproveita `dial-aura-pulse` e `dial-ready-halo`.
- Texto "Iniciar" com espaçamento de letras de 0,14 em, e uma passada de brilho (faixa clara a 12%) a cada 4,8 s.
- Hover: `scale(1.02)` e anel a 70%.

**Falha (`failed`)**: igual a `ready`, com o texto "Falha ao iniciar" e o batimento mais lento (3,6 s), para o operador entender que pode tentar de novo.

**`running`**: não muda, é a referência.

Reduced motion: sem tracejado girando, sem batimento e sem onda; mantêm-se as diferenças estáticas (anel tracejado e baixo no bloqueado, anel completo e vivo no pronto). Acrescentar as novas classes ao bloco `@media (prefers-reduced-motion: reduce)`.

### A.4 Texto do centro

Regras: branco, no máximo 2 linhas de 16 caracteres, substantivo que diz o que falta, sem verbo. Reaproveitar `SHORT_REASON` em `preflightGate.ts`, com estes ajustes:

| Código | Texto atual | Texto novo | Linha secundária (opcional, menor) |
|---|---|---|---|
| `params` | Parâmetros | Parâmetros | "carregando" / "gravando" / "indisponíveis" |
| `driver` | Sem driver | Sem driver | |
| `link` | Sem enlace | Sem enlace | |
| `topics` | Sem tópicos | Sem tópicos | primeiro tópico que falta |
| `odometry` | Sem odometria | Sem odometria | |
| `airborne` | Fora do solo | Fora do solo | |
| `battery-unknown` | Bateria? | Bateria? | |
| `battery-low` | Bateria baixa | Bateria baixa | `18% de 20%` |
| `bridge` | Sem ponte | Sem ponte | |

A linha secundária é opcional: se encarecer o desenho, fica só a primeira linha.

Vários bloqueios (D5): em `preflightGate.ts`, extrair de `firstBlock` uma função `collectBlocks(input)` que devolve todos os bloqueios na ordem das checagens, e fazer `firstBlock` ser `collectBlocks(input)[0]`. A API atual (`preflightBlockedCode`, `preflightBlockedReason`, `preflightBlockedShort`) não muda. Nova exportação: `preflightBlocks(input)` com `{ code, short, secondary?, reason }[]`. No dial, havendo mais de um, alternar a cada 2,6 s com o `text-states-swap` existente e acender o ponto correspondente.

Estado `failed` usa `errorShort`; a frase longa vai para `title` e `aria-label`.

### A.5 Acessibilidade e interação

- Trocar `disabled` por `aria-disabled="true"` quando bloqueado, com `tabIndex` mantido. `disabled` remove o botão do foco e da leitura de tela, e o motivo é a informação que o operador precisa ouvir. O `onClick` ignora o clique se bloqueado.
- `aria-label` continua com o motivo completo; `aria-live="polite"` já existe no `sr-only` para `running`, estender para anunciar a mudança de bloqueado para pronto ("Pronto para iniciar").
- Foco visível: anel de 2 px branco a 70%.

### A.6 Passos de implementação

1. `preflightGate.ts`: criar `collectBlocks`, os textos secundários e `preflightBlocks`; manter as funções antigas. Atualizar os testes do gate (procurar por `preflightGate` nos `*.test.ts`).
2. `PreflightScreen.tsx` (linhas 176 a 202): passar `blocks` (lista) e `gateTotal` ao `LaunchDial`; manter `blockedCode`, `blockedShort`, `blockedReason` por compatibilidade.
3. `LaunchDial.tsx`: introduzir o modelo de estados de A.2 e um `useRef` para detectar a transição `blocked -> ready` e disparar `unlocking` uma vez (nunca na montagem inicial com o gate já livre).
4. `LaunchDial.tsx`: tornar as camadas de arco e faixa permanentes, controladas por variáveis CSS (`--dial-arc`, `--dial-energy`, `--dial-ring-opacity`) definidas por `data-dial-state`.
5. `globals.css`: novas animações (`dial-ring-ticks`, `dial-unlock-arc`, `dial-unlock-flash`, `dial-ready-ripple`, `dial-label-sheen`) e variáveis; remover `dial-aura-nodriver`/`dial-aura-blocked` se ficarem sem uso; atualizar o bloco de reduced motion.
6. Texto central: `color: #fff`, `title-pixel`, `line-clamp-2`; troca de texto com o `text-states-swap`.
7. Testes de `LaunchDial.test.tsx`: estado por entrada (bloqueado, pronto, falha, rodando); `aria-disabled` e clique ignorado; `gap` proporcional (1 de 7, 4 de 7, 0); `unlocking` ocorre uma vez e não na montagem; alternância entre vários bloqueios; texto branco; reduced motion.
8. Verificação visual numa página de simulação que alterna os estados com botões. Capturar os 5 estados a 1x e a 2x de zoom no círculo.

Critérios de aceite:

- Nos 5 estados as seis camadas do A.1 estão presentes e só variam em energia e movimento.
- Bloqueado e pronto são distinguíveis em um quadro parado (anel tracejado e baixo contra anel completo e vivo) e em movimento (busca contra batimento).
- O texto é sempre branco e nunca passa de 2 linhas de 16 caracteres.
- Com `blocked`, clique e `Enter` não chamam `onLaunch`.

---

## Parte B. Texto sobre o vídeo ao vivo

Arquivos: `streamer/mjpeg_server.py`, `mvp_mission_bebop/context.py`, `mvp_mission_bebop/perception/worker.py`, `mvp_mission_bebop/steps/rtl.py` (somente auditoria), `test/test_detection_overlay.py`.

### B.1 O que a leitura do código mostra

- `draw_detection_summary` (ponte) desenha só: caixas com rótulo das detecções e caixas "Base" dos marcadores. Não existe `cv2.rectangle` de banner, `putText` de status nem `drawMarker` de cruz. Comparado com o `HEAD~`, o banner `(20, 20, 20)` com texto `(0, 255, 0)` e a cruz amarela sumiram no commit `13bd34c`.
- `publish_annotated_stream` (missão) desenha só as detecções do SDK e publica a imagem; a docstring ainda cita "crosshair and status band", que não existem mais.
- `DetectionSummary.status` ainda é lido do JSON (contrato com a missão) mas não é desenhado.

Conclusão: o texto que o operador vê não vem do código atual. As causas prováveis, a confirmar na etapa B.2:

1. Uma ponte (`mjpeg_server.py`) ou uma missão antiga ainda em execução, iniciada antes do `13bd34c`.
2. Outra fonte desenhando direto no quadro: o detector de ArUco do SDK (`detector.detect(frame, annotate)` desenha contorno e ID) e `cv2.drawFrameAxes` em `rtl.py:546` escrevem dentro do `frame`, e se esse mesmo array chegar a um publicador de imagem, o texto sai no vídeo.
3. O próprio rótulo das caixas da YOLO ("Acidente 0.85") confundido com o texto (D2).

### B.2 Passos

1. Reproduzir antes de mudar: subir a ponte pelo lançador, abrir o vídeo no BMG e, com o emulador de voo, capturar um quadro do endpoint de vídeo da ponte no estágio 2 e no estágio 5. Conferir a hora de início do processo `mjpeg_server.py` contra a do último commit de `streamer/`.
2. Se a ponte for antiga: nada a codificar além de garantir que o lançador reinicie o streamer quando o código mudar. Verificar `scripts/bmg` e `electron/main.cjs` (função que reapa pontes órfãs, perto da linha 527 de `main.cjs`): ela mata a ponte antiga ao reabrir o app? Se não, corrigir.
3. Se ainda houver texto no quadro: rastrear todos os publicadores de imagem (`image_pub`, `detection_stream_topic`) e confirmar que nenhum recebe um `frame` que tenha passado por `Aruco.detect(..., annotate=True)` ou `drawFrameAxes`. Em `ArucoMarkerSensor`, usar `annotate=False` nos caminhos que alimentam o vídeo (a decisão de contrato está em `rtl.py`, perto da linha 531, `_annotate`).
4. Remover resíduos: a docstring de `publish_annotated_stream` e a de `draw_detection_summary` (falam de cruz e banner) e `_BANNER_BOTTOM` se ficar sem uso.
5. Teste de guarda em `test_detection_overlay.py`: desenhar um resumo com `status` longo e um marcador sobre um quadro cinza uniforme, e afirmar que (a) nenhuma região retangular `(20, 20, 20)` aparece, (b) nenhum pixel `(0, 255, 0)` aparece, (c) a diferença para o quadro original fica restrita às caixas e aos rótulos. Fazer o mesmo para o quadro que sai de `publish_annotated_stream` com um `MissionContext` falso.
6. Registrar na saída final o resultado do passo 1, para o usuário saber qual era a causa real.

Critério de aceite: num voo emulado, nenhum quadro do vídeo traz texto fora dos rótulos das caixas; o teste de guarda passa.

---

## Parte C. Painel de evidência: grid reveal e scan na foto

Arquivos: `src/components/cockpit/EvidenceGrid.tsx`, `ForensicPanel.tsx`, `ForensicPanel.test.tsx`, `src/styles/globals.css` (bloco `.evgrid-*`, linhas 2100 a 2200 e o reduced motion), novo `src/lib/evidenceReveal.ts`, e `CockpitScreen.tsx` só se mudar a passagem de props.

### C.1 Linha do tempo alvo

| Momento | O que o operador vê | Quem comanda |
|---|---|---|
| Início do voo até a captura | Grade que se divide desde a decolagem, 4x mais rápida, mais proporcional a cada etapa | tempo mínimo e progresso das etapas 1 a 4 (o maior dos dois), como hoje |
| Captura | A mira tira a foto (foco, quique, obturador) em 560 ms e sai em 300 ms | tempo |
| Revelação, `REVEAL_MS` (padrão 2200 ms) | As células tomam a cor da foto a partir da mira e se dividem, primeiro onde há mais detalhe, até virar a imagem | tempo, com ease; nunca em um quadro |
| Imagem revelada até o pouso | Scan sutil só sobre a foto, esquerda para direita, uma passagem | progresso do voo |
| Pouso | O scan chega ao fim e some; a foto sobe, aparecem data e hora e segue o procedimento existente (laudo) | toque no solo |

### C.2 Scan: o que muda em relação ao que existe

Hoje (`ForensicPanel.tsx`, `scanningInFlight`; `globals.css`, `.evgrid-scan`): sobre `data-evidence-frame` inteiro, feixe vertical em `translateY`, `infinite` a 2,6 s, desmontado de uma vez em `onGround`.

Novo comportamento:

1. **Retângulo da foto.** A foto é desenhada em `object-contain` dentro do quadro, então o retângulo dela é menor que o quadro quando as proporções diferem. O scan vive só nele. O `EvidenceGrid` já carrega a imagem e conhece o tamanho do quadro; é o dono natural do retângulo. Mover o scan para dentro do `EvidenceGrid` como camada irmã do canvas, posicionada por `photoRect` (esquerda, topo, largura e altura calculados com a mesma conta de `buildSampler`: `k = min(frameW/natW, frameH/natH)`). Recalcular no `ResizeObserver` e no `onload`. A camada tem `overflow: hidden` e o mesmo raio da foto.
2. **Direção.** Horizontal, da borda esquerda da foto à direita: uma linha vertical fina (1,5 px, `#5CF2CE`, brilho de 8 px) com uma faixa de rastro atrás (largura de 56 px, gradiente de mint 0% a 10%). Sem contagem, sem desfoque, sem escurecer a parte ainda não varrida: só linha e rastro, o mínimo.
3. **Duração = resto do voo.** Posição `x = scanProgress * largura`, com `scanProgress = (flightProgress - p0) / (1 - p0)`, `p0` = progresso do voo no instante em que a revelação terminou. Suavizar com constante de tempo (mesmo `followTau` do resto do painel) para o avanço nunca saltar. No pouso, completar até 100% em no máximo 400 ms e então esmaecer a linha em 200 ms. Só depois disso `down` vira verdadeiro e a foto sobe.
4. **Início.** O scan só existe depois da revelação terminar (`onRevealed`). Nunca durante a grade nem durante a mira.
5. **Pouso antes do scan terminar ou da revelação terminar.** A revelação sempre completa. O scan tem passagem mínima de 1,2 s: se o pouso vier antes, ele corre os 1,2 s e `down` espera. Se a missão for abortada (`missionOver`), o scan fecha pela mesma regra, sem passagem mínima.
6. **Persistência.** `p0` e o fato de a revelação ter terminado precisam sobreviver a trocar de aba (o `CockpitScreen` é desmontado ao ir para INICIO). Guardar em `src/lib/evidenceReveal.ts`, um mapa em módulo por `captureKey` com `{ revealedAtProgress }`, limpo no `mission.start` e no reset da missão. Ao voltar à cabine com a foto já revelada e o drone em voo, o scan retoma a partir do progresso atual, sem refazer a grade nem a mira (comportamento que `firstKey` já tem hoje).
7. **Reduced motion.** Sem scan; a foto fica estática e `down` segue o pouso.
8. **Sem canvas (jsdom e hosts sem canvas).** O caminho `noCanvas` já completa a revelação no pouso; mudar para completar logo após a mira, para que o scan também exista nos testes.

### C.3 Grid reveal: melhorias

Problemas verificados em `EvidenceGrid.tsx`:

- `CLOSE_TAU_MS = 420 / GRID_TEMPO` dá 35 ms: a revelação acontece quase em um quadro (`shown += (goal - shown) * (1 - exp(-dt / tau))`). Isso é o "pulo" que o usuário não quer.
- Um único `GRID_TEMPO = 12` escala espera, abertura de célula, brilho e revelação juntos.
- `closing` é recebido mas só serve ao `noCanvas`; `reveal` (progresso do voo) deixou de ser usado.
- Sem limite de divisões por quadro: com 12x e milhares de células, um quadro pode estourar o orçamento.

Mudanças:

1. Separar constantes: `WAIT_TEMPO = 12` (grade de espera, 4x) e `REVEAL_MS = 2200` (revelação, por tempo). `shown` passa a ser `easeInOut((now - revealStart) / REVEAL_MS)`, sem constante de tempo exponencial. A mira continua à parte (560 ms).
2. A revelação tem três fases dentro dos 2200 ms: cor (0 a 45%, frente radial a partir da mira), divisão (30 a 90%, por detalhe), fechamento dos espaços e dos cantos arredondados (65 a 100%). As curvas atuais (`toneMix`, `gapScale`, `Math.pow(shown, 1.7)`) são mantidas como base, só trocando a origem de `shown`.
3. Cruzamento final: a foto nítida entra por cima (`PHOTO_IN_MS = 500`) enquanto as células esmaecem, em vez de trocar de uma vez.
4. Limite de orçamento: no máximo 24 divisões por quadro e, se o quadro anterior passou de 24 ms, metade disso; o alvo continua o mesmo e é alcançado em quadros seguintes.
5. A grade de espera continua mista (tempo mínimo e progresso das etapas) e satura em `WAIT_DOUBLINGS = 6` na captura, deixando folga para a revelação.
6. Remover `reveal` e `closing` da lista de propriedades que o `EvidenceGrid` não usa, ou passá-los de fato (decidir na implementação; o plano recomenda remover `reveal` do grid e manter `closing` só para o fallback).

### C.4 Passos de implementação

1. Criar `src/lib/evidenceReveal.ts` (mapa em módulo, `recordRevealed(key, progress)`, `revealedAt(key)`, `clear()`), ligar o `clear()` ao `mission.start` onde `useStageProgress` já escuta.
2. `EvidenceGrid.tsx`: calcular `photoRect`; nova prop `scan: { active: boolean; progress: number; closing: boolean; onDone: () => void }`; renderizar a camada de scan dentro do host, posicionada pelo retângulo; revelação por tempo; limite de divisões por quadro; chamar `onRevealed` com o progresso do voo naquele instante.
3. `ForensicPanel.tsx`: remover o `<div data-scan-sweep className="evgrid-scan">` externo; guardar `p0` via `evidenceReveal`; calcular `scanProgress`; passar as props ao grid; `down` passa a depender de `scanDone` (scan terminado) e de `onGround`.
4. `globals.css`: substituir `.evgrid-scan`, `.evgrid-scan-beam` e `evgrid-scan-sweep` por `.evgrid-scan` (camada), `.evgrid-scan-line` (linha com brilho) e `.evgrid-scan-trail` (rastro); posição por `transform: translateX(var(--scan-x))` com `--scan-x` em px; incluir as novas classes no reduced motion.
5. Testes:
   - `ForensicPanel.test.tsx`: o scan não existe antes da revelação; existe depois e só dentro de `[data-evidence-frame]`; a posição cresce monotonicamente com `flightProgress` e chega a 100% no pouso; passagem mínima de 1,2 s; `down` espera o fim do scan; reiniciar com nova captura limpa tudo; voltar à aba com foto revelada retoma o scan sem refazer a mira.
   - Testes de função pura para o cálculo do retângulo da foto: quadro 16:9 com foto 16:9 (cobre tudo), quadro quadrado com foto 16:9 (faixas em cima e embaixo), quadro largo com foto vertical (faixas laterais).
   - Teste de que a revelação nunca avança mais de uma fração `1/REVEAL_FRAMES` por quadro simulado (sem salto).
6. Verificação visual: página de simulação temporária que usa o `ForensicPanel` real com o modelo de progresso de `stageProgress` (como na rodada anterior), a 1x e a 4x, com 3 proporções de foto. Capturar: grade a 25% e 80% do voo, instante do obturador, revelação a 30%, 60%, 90%, foto inteira, scan a 10%, 50%, 90%, e o quadro do pouso.

Critérios de aceite:

- O scan nunca aparece fora do retângulo da foto, em nenhuma proporção.
- A linha anda da esquerda para a direita, uma vez, e chega à borda direita no pouso.
- A revelação dura `REVEAL_MS`, sem saltos visíveis.
- Pousar antes da revelação ou do scan terminarem não corta a animação.

---

## Parte D. Cabine: só vídeo e mapa sem missão

Arquivos: `src/components/cockpit/CockpitScreen.tsx`, `CockpitScreen.test.tsx`, `OpticalFeed.tsx`, `src/styles/globals.css`.

### D.1 O que já está certo (não refazer)

`missionStarted = missionState !== 'idle' || benchStage !== null`. Sem missão: só `OpticalFeed` (1,5fr) e `TacticalMap` (1fr) em coluna única, sem `StageBar`, sem `AbortControl`, sem `ForensicPanel`. Com missão: layout completo. Os atalhos de abortar não disparam em `idle`, porque `abortEnabled` é falso e o `setCockpitAbortArmed` leva esse valor.

### D.2 Lacunas

1. **Remontagem.** São dois `return` com árvores diferentes. Quando a missão começa estando na cabine (por exemplo, iniciada pelo teclado ou ao voltar da aba), `OpticalFeed` e `TacticalMap` são desmontados e remontados: o `<img>` do MJPEG reconecta, o mapa recarrega tiles e o drone da animação Lottie reinicia. Isso aparece como piscada no instante em que o operador mais precisa do vídeo.
2. **Sem transição.** A troca é um corte seco.
3. **Vídeo sem sinal (D1).**

### D.3 Passos

1. Unificar em uma única árvore. Um contêiner de grade com `data-cockpit-layout="standby" | "mission"` e colunas e linhas definidas por CSS conforme esse atributo:
   - `standby`: `grid-template-columns: 1fr`, `grid-template-rows: minmax(0, 1.5fr) minmax(0, 1fr)`, vídeo e mapa nas duas linhas.
   - `mission`: `grid-template-rows: auto minmax(0, 1fr)` com a faixa de etapas na primeira linha; abaixo, duas colunas (`1.1fr 1fr`), vídeo e mapa na primeira coluna (linhas 1,5fr e 1fr) e painel forense na segunda.
   Vídeo e mapa têm sempre a mesma posição de árvore (mesmas chaves React), então não desmontam.
2. Transição de layout: as animações de `grid-template-columns` e `grid-template-rows` funcionam no Chromium do Electron atual; usar `transition: grid-template-columns 480ms, grid-template-rows 480ms` com `cubic-bezier(0.22, 1, 0.36, 1)`. Verificar a versão do Electron em `package.json`; se não animar, usar o mesmo efeito por `transform` nos filhos.
3. Entrada dos extras: faixa de etapas, botão de abortar e painel forense entram com `opacity` e `translateY(8px)` em 320 ms, com atraso de 120 ms depois do início do redimensionamento; saem na ordem inversa quando a missão é encerrada (`Finalizar` leva o estado de volta a `idle`).
4. Botão de abortar: continua ancorado na junção entre vídeo e mapa, só em `mission`.
5. Vídeo sem sinal na espera (D1): em `OpticalFeed`, quando o estado não é `image` e o layout é `standby`, desenhar o quadro escuro sem ícone nem mensagem. As mensagens de `FEED_MESSAGE` seguem existindo na missão (onde o operador precisa do diagnóstico). Passar uma propriedade `quiet` do `CockpitScreen`.
6. Testes: `CockpitScreen.test.tsx` já cobre os dois layouts; acrescentar (a) a mesma instância de `OpticalFeed` e `TacticalMap` persiste na troca de `idle` para `arming` (marcar os elementos e comparar a identidade do nó), (b) em `standby` não há `AbortControl`, `StageBar` nem `ForensicPanel`, (c) em `standby` sem sinal não há texto de erro, (d) o botão de abortar aparece com missão e só então, (e) `prefers-reduced-motion` troca sem animar.
7. Verificação visual: página de simulação que alterna o estado da missão, capturando o quadro 0, 160 ms, 320 ms e 480 ms da transição, a 1600 por 900 e a 1280 por 720.

Critérios de aceite:

- Sem missão: só vídeo e mapa, sem nenhum controle de voo.
- Ao iniciar a missão estando na cabine, o vídeo não pisca nem reconecta.
- A transição dura cerca de 500 ms e não deixa elementos sobrepostos no meio.

---

## 2. Ordem de execução

Cada passo termina com `tsc`, os testes do que foi tocado e a verificação visual quando houver.

1. **B** primeiro: é investigação curta e define se há algo a reiniciar. Se a causa for um processo antigo, avisar o usuário logo.
2. **C**: scan na foto e revelação por tempo. É o que o usuário pediu por último, com mais risco visual.
3. **D**: unificar a árvore da cabine. Não depende de C, mas toca `CockpitScreen.tsx` como C (a passagem de props); fazer depois para evitar conflito.
4. **A**: o mostrador, maior em design e independente das outras partes.
5. Rodada final: `npx tsc --noEmit`, `npm test`, `npm run build`, `python3 -m pytest test/test_detection_overlay.py test/test_contracts.py test/test_aruco_rtl.py`.

Partição de commits sugerida (só com autorização):

1. `fix(streamer): lock the live video to detection boxes only`
2. `feat(bmg-ui): scan the captured frame left to right until touchdown`
3. `refactor(bmg-ui): reveal the evidence grid over time without snapping`
4. `refactor(bmg-ui): keep feed and map mounted across the cockpit layouts`
5. `feat(bmg-ui): one anatomy for every state of the launch dial`

## 3. Riscos

| Risco | Mitigação |
|---|---|
| Animar `grid-template-*` não funcionar na versão do Electron | Conferir a versão antes; alternativa por `transform` nos filhos (D.3, passo 2). |
| O scan acompanhar `flightProgress` e este saltar (medição por etapas) | Suavização por constante de tempo e teste de passo máximo por quadro. |
| Estourar o orçamento de quadro com muitas células | Limite de divisões por quadro e adaptação quando o quadro passa de 24 ms. |
| O texto de status continuar aparecendo por processo antigo | Passo B.2: conferir hora do processo e a limpeza de pontes órfãs no lançador. |
| `aria-disabled` deixar o botão focável e clicável por engano | `onClick` e `onKeyDown` retornam cedo quando bloqueado; teste dedicado. |
| Testes de `launchArguments` e `launchReadinessMain` falharem por ambiente | Fechar o BMG e rodar `npm test`; ver a memória `main-harness-tests-need-app-closed`. |

## 4. Fora do escopo

- Qualquer mudança nos dados da missão, nos gates em si (só a forma de exibi-los) ou na detecção de ArUco.
- O vídeo de fundo da aba INICIO e o projeto HyperFrames.
- Commit, push e qualquer alteração em `nectar-sdk`.
