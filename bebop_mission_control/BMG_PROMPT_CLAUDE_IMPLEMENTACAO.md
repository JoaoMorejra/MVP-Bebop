# PROMPT DE IMPLEMENTAÇÃO PARA O CLAUDE: REFORMULAÇÃO E AJUSTES CRÍTICOS DO BMG (BEBOP MISSION CONTROL)

> **Destinatário:** Claude (AI Coding Agent)  
> **Projeto:** Tech for Humans — Bebop Mission Control (BMG)  
> **Diretório do Projeto Electron/React:** `/home/joaomoreira/ros2_ws/bebop_mission_control`  
> **Diretório dos Pacotes ROS 2/Python:** `/home/joaomoreira/ros2_ws/src/mvp_mission_bebop/mvp_mission_bebop`  
> **Ambiente Operacional:** Linux Ubuntu, ROS 2 Humble, Node.js 20+, Electron, Python 3.12 (Venv Nectar)

---

## 🎯 OBJETIVO PRINCIPAL

Você deve implementar de forma completa, precisa e funcional todas as alterações solicitadas para a estação de controle de solo **BMG (Bebop Mission Control)**. 
Abaixo está a especificação técnica detalhada de cada módulo, contendo as causas dos problemas atuais, os arquivos exatos a serem modificados, a lógica necessária de frontend e backend, e os critérios de aceitação.

---

## 📋 SUMÁRIO DAS ALTERAÇÕES

1. **Navegação Superior:** Renomeação das abas para **"Inicio"** e **"Cabine"**, remoção de emojis.
2. **Parâmetros de Voo (Pré-voo):** Enxugar radicalmente a lista permitida para apenas **6 parâmetros essenciais**, com interface moderna e garantia de coerência com o backend (ângulo Nadir e freeze).
3. **Modo Bancada & Botão Iniciar:** Mudança da cor para a identidade visual da *Tech for Humans*, inclusão de ícone/emoji no botão Bancada, botão Iniciar assumindo a cor da Bancada quando ativa, e inserção do erro impeditivo / "Motores desligados" **dentro do círculo central** embaixo de "INICIAR".
4. **Barra Superior Direita (Bateria, Wi-Fi e Volume):** Desmembrar o retângulo único em 3 botões/widgets independentes; Wi-Fi com popover idêntico a notebook; Bateria com popover permitindo configurar limiar de **Pouso Automático por Bateria Baixa (Auto-Land)** acionado em voo; Volume mantido.
5. **Diagnóstico como Terminal Interativo:** Transformar a tela de Diagnóstico em um **verdadeiro Terminal Linux**, exibindo o streaming de logs em tempo real do `mission.py` e permitindo envio interativo de comandos (ROS 2 e bash).
6. **Correção do Mapa Tático (Cabine):** Obtenção e renderização da localização real (persistência da geolocalização do operador, leitura real de GPS do Bebop 2 e suporte total offline com Grade Tática).
7. **Modos de Voo (Cabine):** Renomeação determinística para: `"Decolagem"`, `"Varredura"`, `"Acidente detectado"`, `"Inspeção"`, `"Retornando base"`, com forte destaque visual para apresentação.
8. **Painel Forense (Cabine):** Redesign tecnológico (estilo HUD militar/aeronáutico) e alteração da mensagem de espera exclusivamente para `"Aguardo foto da evidencia"`.

---

## 🛠️ ESPECIFICAÇÃO DETALHADA POR MÓDULO

---

### MÓDULO 1: NAVEGAÇÃO SUPERIOR (ABAS)

#### Arquivo Alvo
- `src/components/shell/TabSwitch.tsx`
- `src/App.tsx` (se houver menções em títulos/labels)

#### Requisitos
1. **Renomeação dos labels:**
   - Mudar `Pré-voo` para **`Inicio`**.
   - Manter `Cabine` como **`Cabine`**.
2. **Remoção de emojis/ícones decorativos:**
   - No array `TABS` de `TabSwitch.tsx`, remover a renderização dos ícones (como `Rocket` e `Gauge`) ou substituir por uma apresentação tipográfica limpa e minimalista.
   - Manter estritamente o ícone de bloqueio (`Lock`) quando a aeronave estiver em voo real (`airborne`) e o ponto luminoso pulsante (`anim-breathe bg-mint`) na aba `Cabine` indicando missão ativa.

---

### MÓDULO 2: PARÂMETROS DE VOO (ENXUGAMENTO E COERÊNCIA COM BACKEND)

#### Arquivos Alvo
- `src/lib/parameterSchema.ts`
- `src/components/preflight/ParameterSheet.tsx`
- `src/components/preflight/QuickParams.tsx`
- `src/mvp_mission_bebop/mvp_mission_bebop/mission_config.json`
- `src/mvp_mission_bebop/mvp_mission_bebop/controllers/visual_servoing.py` (verificação de coerência)

#### Contexto e Diagnóstico
Atualmente, o modal de parâmetros expõe dezenas de campos secundários (ganhos PID, limites de jerk da cinemática, dead reckoning, limiares avançados de falso positivo). O operador necessita alterar **apenas os 6 parâmetros que definem o comportamento da missão**, eliminando toda a poluição visual.

Além disso, há uma exigência crucial de **coerência com o backend**:
No backend Python (`controllers/visual_servoing.py` e `steps/tracking.py`), durante a aproximação da etapa 3, o controle IBVS faz a câmera descer gradualmente em direção ao solo até atingir o ângulo nadir configurado (`gimbal.nadir_tilt_deg`, que no projeto foi calibrado para `-69°`). 
Quando a câmera atinge esse ângulo (dentro da tolerância `nadir_tilt_tolerance_deg`), o controller emite `command.nadir_frozen = True`. Isso força a aeronave a travar completamente a velocidade horizontal (`vx = 0, vy = 0`) no standoff geométrico ideal, encerrando o rastreamento e iniciando o pairado estático para a foto pericial da etapa 4. 

#### Requisitos de Implementação
1. **Filtrar os Parâmetros:** A interface do modal deve permitir visualizar e editar **somente** estas 6 opções:
   - **1. Altitude Operacional:**
     - Path: `kinematics.target_altitude_m`
     - Label: "Altitude de Voo"
     - Range: `0.5` a `4.0` m (step: `0.1` m, default: `1.8` m).
     - Hint: "Altitude de cruzeiro e teto de segurança estabilizado."
   - **2. Velocidade de Varredura:**
     - Path: `kinematics.forward_cruise_velocity`
     - Label: "Velocidade de Cruzeiro"
     - Range: `0.05` a `0.60` m/s (step: `0.01` m/s, default: `0.20` m/s).
     - Hint: "Velocidade retilínea de busca na etapa de varredura."
   - **3. Estabilização Pós-Decolagem:**
     - Path: `kinematics.takeoff_stabilize_duration_sec`
     - Label: "Estabilização Pós-Decolagem"
     - Range: `1.0` a `15.0` s (step: `0.5` s, default: `2.0` s).
     - Hint: "Tempo de pairado no ponto de decolagem antes de iniciar o avanço."
   - **4. Ângulo da Inclinação Nadir (Freeze da Câmera):**
     - Path: `gimbal.nadir_tilt_deg`
     - Label: "Ângulo da Inclinação Nadir"
     - Range: `-90` a `-50` ° (step: `1` °, default: `-69` °).
     - Hint: "Inclinação final da câmera sobre o alvo. Ao atingir este ângulo, o drone congela o movimento horizontal para captura."
     - *Coerência Backend:* Assegurar que o valor editado seja enviado em `paramsJson` e atualize `params.gimbal.nadir_tilt_deg`. O backend deve utilizar esse valor exato como ponto de término do tracking e disparo de `nadir_frozen`.
   - **5. Início de Inclinação de Varredura:**
     - Path: `gimbal.search_tilt_deg`
     - Label: "Inclinação Inicial de Varredura"
     - Range: `-45` a `0` ° (step: `1` °, default: `-20` °).
     - Hint: "Ângulo inicial da câmera durante a varredura para busca de alvos."
   - **6. Timeouts de Operação e Segurança:**
     - Agrupar os timeouts essenciais:
       - *Timeout de Procura/Varredura:* `timeouts.search_timeout_sec` (Range: `5` a `180` s, default: `30` s).
       - *Timeout de Retorno e Pouso ArUco:* `rtl.timeout_sec` (Range: `10` a `180` s, default: `60` s).
2. **Remoção dos Demais Parâmetros:**
   - Todos os demais grupos e parâmetros em `PARAMETER_GROUPS` (`parameterSchema.ts`) devem ser removidos da interface ou ocultados. Os valores originais em `mission_config.json` devem ser preservados nos saves.
3. **Novo Design Estruturado:**
   - Reformular o layout de `ParameterSheet.tsx` para apresentar um design moderno em cards agrupados com divisões claras:
     - Card 1: **Envelope de Voo** (Altitude, Velocidade, Estabilização).
     - Card 2: **Gimbal & Câmera** (Inclinação de Varredura e Inclinação Nadir Freeze).
     - Card 3: **Limites de Tempo (Timeouts)** (Busca de Sinistro e Pouso ArUco).
   - Sliders responsivos com indicação numérica precisa e botões claros de "Salvar" e "Restaurar Padrões".

---

### MÓDULO 3: MODO BANCADA E BOTÃO INICIAR

#### Arquivos Alvo
- `src/components/preflight/LaunchDial.tsx`
- `src/components/preflight/PreflightScreen.tsx`
- `tailwind.config.js`

#### Contexto e Diagnóstico
Atualmente, o botão do modo bancada utiliza a cor âmbar (`amber`), destoando da identidade visual futurista da Tech for Humans (`mint` `#01D5A3`, `cyan` `#00E5FF`, `kelp` `#12695E`). Além disso, o botão "Bancada" não possui ícone e, quando ativado, o botão central "Iniciar" continua verde ou inalterado. 
As mensagens de erro que impedem o clique em "Iniciar" ficavam soltas abaixo do conjunto, perdendo o foco pedagógico.

#### Requisitos de Implementação
1. **Identidade Visual Tech for Humans no Modo Bancada:**
   - Substituir o estilo amarelo/âmbar do modo bancada por tons da paleta *Tech for Humans*: Ciano elétrico / Mint tecnológico (`text-mint`, `border-mint/60`, `bg-mint/10` ou `cyan-400`).
   - Adicionar um emoji ou ícone no botão "Bancada" (ex: 🔬 ou 🛠️ / Lucide `Wrench` ou `Cpu`) para harmonizar com o botão "Parâmetros de voo".
2. **Botão Iniciar na Cor da Bancada:**
   - Em `LaunchDial.tsx`, quando `benchMode === true`, todos os elementos de iluminação do dial (o halo radial externo, os anéis concêntricos, o gradiente de varredura `conic-gradient` e o ícone de Play) devem adotar a cor temática da bancada (Ciano/Mint brilhante), diferenciando visualmente um teste de bancada de um voo real.
3. **Feedback de Erro e Status Dentro do Círculo do Iniciar:**
   - Mover o aviso de bloqueio (`blockedReason` ou `launchError`, ex: *"Driver ROS 2 fora do ar"*, *"Conecte-se à rede da aeronave"*, etc.) para **DENTRO do círculo central do LaunchDial**, posicionado logo abaixo da palavra `INICIAR`.
   - Quando o modo bancada estiver ativado, exibir no mesmo local interno do círculo a frase explicativa: **"Motores desligados"**.
   - O texto dentro do círculo deve ter tipografia mono/condensada, legível, com tamanho adequado (`text-3xs` a `text-2xs`), garantindo que o operador entenda instantaneamente o estado sem desviar o olhar do botão central.

---

### MÓDULO 4: BARRA SUPERIOR DIREITA (BATERIA, WI-FI E VOLUME INDEPENDENTES)

#### Arquivos Alvo
- `src/components/shell/StatusBar.tsx`
- `src/components/preflight/ConnectionSheet.tsx`
- `src/App.tsx`
- `src/hooks/useTelemetry.ts`
- `src/types/bmg.ts`

#### Contexto e Diagnóstico
Atualmente, a bateria e o Wi-Fi estão fundidos dentro de um mesmo botão retangular em `StatusBar.tsx`. Clicar em qualquer parte abre o painel lateral completo `ConnectionSheet`. O usuário deseja que os três elementos sejam botões/pills independentes.

#### Requisitos de Implementação
1. **Três Elementos Independentes:**
   - Desagrupar o container único. A barra superior direita deve conter:
     - `[Widget de Bateria]` (independente)
     - `[Widget de Wi-Fi]` (independente)
     - `[Widget de Volume]` (independente - VoiceControl já existente)
2. **Widget Wi-Fi com Flyout Estilo Notebook:**
   - Exibir o ícone com barras de sinal e o SSID conectado (ou "Desconectado").
   - Ao clicar, abrir um popover suspenso flutuante com visual moderno estilo sistema operacional de notebook (Windows 11 / macOS / Ubuntu Quick Settings):
     - Cabeçalho com título "Redes Wi-Fi", status de scan e botão de atualizar redes.
     - Lista rolante de redes Wi-Fi disponíveis.
     - Manter o filtro/destaque inteligente para redes do drone (`Bebop2-xxxxxx`), colocando-as no topo com badge especial.
     - Ao clicar em uma rede, disparar a conexão via `onConnect(ssid)`.
     - Exibir se o driver ROS 2 está ativo e botão rápido para Iniciar/Parar driver.
3. **Widget Bateria com Pop-up de Pouso Automático (Auto-Land Failsafe):**
   - Exibir o glifo de bateria preenchido proporcionalmente e a porcentagem atual (ou `—` se desconhecido).
   - Ao clicar, abrir um pop-up flutuante dedicado com:
     - Carga atual (%), status de carregamento e tensão/origem se disponível.
     - **Controle de Failsafe de Bateria (Pouso Automático):**
       - Toggle switch: *"Pouso automático por bateria baixa"*.
       - Input numérico / Slider: *"Nível crítico para pouso imediato (%)"* (ex: configurável entre `10%` e `40%`, default `20%`).
       - Texto explicativo didático: *"Quando ativado, caso a bateria atinja ou caia abaixo deste nível durante o voo, a estação emitirá o comando imediato de pouso (Land) para segurança da aeronave."*
     - **Lógica de Execução em Voo:**
       - No hook ou no loop de monitoramento da missão (`App.tsx` / `useTelemetry`), verificar se a aeronave está em voo (`running` e `telemetry.connected`).
       - Se a opção de pouso automático estiver ativada e `telemetry.battery_known && telemetry.battery_pct <= threshold`:
         - Disparar automaticamente `mission.abort()` ou comando ROS 2 `/bebop/land`.
         - Emitir aviso sonoro via copiloto: *"Atenção: Nível crítico de bateria atingido. Pouso de emergência iniciado."*

---

### MÓDULO 5: DIAGNÓSTICO COMO TERMINAL INTERATIVO REAL

#### Arquivos Alvo
- `src/components/diagnostics/DiagnosticsScreen.tsx`
- `electron/main.cjs`
- `electron/preload.cjs`
- `src/App.tsx`
- `src/types/bmg.ts`

#### Contexto e Diagnóstico
A tela de diagnóstico atual divide espaço entre uma lista passiva de logs e painéis de ambiente/processos. O operador solicitou uma mudança completa: ao clicar em "Diagnóstico", deve abrir um **verdadeiro Terminal**, com estética autêntica de terminal Linux, exibindo os logs ao vivo de `mission.py` e com um prompt de comando interativo para executar comandos avançados (como `ros2 topic echo`, `ros2 topic pub`, `ping`, scripts ROS 2, etc.).

#### Requisitos de Implementação
1. **Design Literal de Terminal:**
   - Fundo escuro total (`#03090e` ou `#000a12`), tipografia monospaçada (`Azeret Mono`, `Menlo`, `Consolas`), barra de título estilo janela de terminal com indicador de status (`mission.py: online/offline`).
   - Cores de sintaxe autênticas: verde mint para timestamps e prompts, azul ciano para tópicos ROS, amarelo para avisos e vermelho para erros.
2. **Visualização Contínua dos Logs de `mission.py`:**
   - Incorporar os fluxos `stdout` e `stderr` do processo `mission.py` em tempo real.
   - Suporte a rolagem automática com botão de pausar/travar scroll (*Auto-scroll*), busca rápida/filtro e botão de limpar tela (`clear`).
3. **Prompt e Entrada de Comandos Interativa:**
   - Linha de comando no rodapé do terminal:
     - Prompt visível: `operator@bmg-ground-station:~/ros2_ws$ ` com cursor pulsante.
     - Input de texto onde o usuário pode digitar qualquer comando.
     - Suporte a histórico de comandos (navegação com setas para Cima/Baixo).
     - Execução ao pressionar Enter.
4. **Backend Electron IPC (`bmg:terminal-exec`):**
   - Criar handler no `main.cjs` e expor no `preload.cjs`:
     ```javascript
     ipcMain.handle('bmg:terminal-exec', async (_event, command) => {
       // Executa com o ambiente carregado do ROS 2 / Nectar venv
       // Retorna stdout, stderr e exitCode
     });
     ```
   - Permitir também comandos especiais embutidos no frontend:
     - `clear`: limpa os logs da tela.
     - `help`: exibe comandos úteis de inspeção ROS 2.
     - `land`: dispara comando de pouso emergencial.
     - `topics`: atalho para listar tópicos ativos.
   - O output do comando deve ser impresso no corpo do próprio terminal com formatação limpa.

---

### MÓDULO 6: CORREÇÃO E FUNCIONAMENTO REAL DO MAPA TÁTICO

#### Arquivos Alvo
- `src/components/cockpit/TacticalMap.tsx`
- `streamer/telemetry_bridge.py`
- `electron/main.cjs`
- `src/hooks/useOperatorLocation.ts`

#### Contexto e Diagnóstico
O mapa tático frequentemente falha em exibir a localização real por dois motivos:
1. **Coordenadas Fixas de Belo Horizonte:** Em `telemetry_bridge.py` e `TacticalMap.tsx`, se o drone não tiver fix de GPS (típico em testes internos ou bancada), o sistema recorria às coordenadas fixas `-19.8703, -43.9678` (Pampulha, BH).
2. **Queda de Internet no Wi-Fi do Bebop 2:** O Bebop 2 cria uma rede Wi-Fi local sem saída para a internet (`192.168.42.1`). Quando o computador se conecta a ela, as requisições para `ipwho.is` ou tiles do OpenStreetMap/Mapbox/MapTiler falham por falta de rota/DNS, deixando o mapa preto ou desorientado.

#### Requisitos de Implementação
1. **Persistência / Cache de Coordenadas Reais do Operador:**
   - No `main.cjs`, antes de conectar ao Bebop ou logo na inicialização (enquanto há internet ativa), consultar a geolocalização do operador via IP e persistir em cache de disco (`appData` ou arquivo de configuração local).
   - Quando o computador conectar ao Wi-Fi do Bebop (ficando sem internet), utilizar as coordenadas cacheadas como ponto de referência real da base (`base_lat`, `base_lng`), garantindo que o mapa centre na cidade/local exato do operador e não em Belo Horizonte.
2. **Prioridade Total ao GPS Real da Aeronave:**
   - Em `streamer/telemetry_bridge.py`, validar o tópico `/bebop/states/gps` (`NavSatFix`). Quando `msg.status.status >= 0` e as coordenadas forem válidas (não zeradas), propagar imediatamente como coordenadas primárias da aeronave com `gps_fix = True`.
3. **Resiliência da Grade Tática Local (Modo Offline):**
   - Em `TacticalMap.tsx`, se os tiles online não carregarem devido à falta de internet no Wi-Fi do Bebop, o componente **não deve travar nem ficar preto**.
   - Ele deve exibir com riqueza de detalhes a **Grade Tática Vetorial Local**:
     - Ponto de decolagem (origem `0, 0` com latitude/longitude real).
     - Trajetória percorrida pelo drone desenhada em tempo real com base na odometria (`/bebop/odom`).
     - Ícone do drone orientado pela bússola/heading real (`telemetry.heading`).
     - Círculo de tolerância do RTL (`arrivalRadius`).
     - Escala métrica em metros e bússola tática.
     - Rodapé exibindo as coordenadas reais (GPS ou Odometria com base real).

---

### MÓDULO 7: RENOMEAÇÃO E DESTAQUE DOS MODOS DE VOO NA CABINE

#### Arquivos Alvo
- `src/components/cockpit/StageBar.tsx`
- `electron/main.cjs` (`STEP_NAMES`)
- `src/hooks/useMissionRuntime.ts`
- `src/types/mission.ts`

#### Requisitos de Implementação
1. **Novos Nomes das 5 Etapas:**
   - Atualizar a lista de etapas para exatamente estes nomes:
     - **Etapa 1:** `Decolagem`
     - **Etapa 2:** `Varredura`
     - **Etapa 3:** `Acidente detectado` *(anteriormente "IBVS Tracking")*
     - **Etapa 4:** `Inspeção` *(anteriormente "Inspeção Nadir")*
     - **Etapa 5:** `Retornando base` *(anteriormente "RTL & Pouso")*
   - Sincronizar esses mesmos nomes em `STEP_NAMES` no `electron/main.cjs`:
     ```javascript
     const STEP_NAMES = {
       1: 'Decolagem',
       2: 'Varredura',
       3: 'Acidente detectado',
       4: 'Inspeção',
       5: 'Retornando base',
     };
     ```
2. **Destaque Visual Acentuado (Apresentação):**
   - As etapas serão o ponto central da demonstração para a banca/avaliadores.
   - Tornar os badges (pills) maiores, com tipografia mais visível e marcante (`font-semibold`, texto ligeiramente maior).
   - Incluir o número da etapa em destaque (ex: `1 · Decolagem`, `2 · Varredura`, `3 · Acidente detectado`...).
   - A etapa ativa deve possuir iluminação forte: fundo mint brilhante com sombra viva (`shadow-live`), pulsação sutil de atividade e ícone em alto contraste.
   - Transição suave entre etapas para impressionar visualmente durante a execução do pipeline autônomo.

---

### MÓDULO 8: REDESIGN DO PAINEL FORENSE

#### Arquivo Alvo
- `src/components/cockpit/ForensicPanel.tsx`

#### Requisitos de Implementação
1. **Novo Layout Tecnológico (Estilo HUD Militar/Aeronáutico):**
   - Reformular a área de espera da captura para um visual cyberpunk/tecnológico avançado:
     - Cantoneiras de mira HUD nos 4 cantos da moldura (`border-t-2 border-l-2 border-mint`, etc.).
     - Retículo central de escaneamento ou mira óptica com mira cruzada.
     - Linha de grade técnica ou textura vetorial.
     - Indicador de status de sensor óptico (ex: `SENSOR RAW 14MP // STANDBY`).
2. **Alteração do Texto Central:**
   - Substituir o texto de espera central por estritamente: **`Aguardo foto da evidencia`**.
   - **Remover completamente** a descrição secundária abaixo dele (`<p className="text-2xs leading-relaxed text-haze">...`), deixando o painel limpo, direto e profissional sem parágrafos explicativos redundantes.

---

## 🔍 COMPILAÇÃO E VERIFICAÇÃO FINAL

Após aplicar todas as modificações, execute a verificação estrita:

1. **Compilação do TypeScript e Vite:**
   ```bash
   cd /home/joaomoreira/ros2_ws/bebop_mission_control
   npm run build
   ```
   *Critério de aprovação:* O comando deve encerrar com código de saída 0 e sem nenhum erro de tipo TypeScript ou sintaxe.

2. **Verificação de Inicialização:**
   - Certifique-se de que a aplicação inicia normalmente com `npm run electron:dev` ou `./bin/bmg`.

---

> **Instrução Final para o Claude:**  
> Implemente todos os arquivos necessários com código de alta qualidade, sem placeholders ou `// TODO`, garantindo total integração entre o frontend React e os processos Electron / ROS 2.
