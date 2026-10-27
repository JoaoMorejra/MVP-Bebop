# Tabela de falas do copiloto

Gerada por `python3 bebop_mission_control/streamer/precache_speech.py --table bebop_mission_control/docs/TABELA_DE_FALAS_COPILOTO.md`; não editar à mão. A fonte é o manifesto da estação (`bebop_mission_control/scripts/exportSpeechManifest.ts`, `bmg.speech_manifest.v1`) mesclado aos alertas que a missão compõe em Python (`mvp_mission_bebop/telemetry/spoken_alerts.py`).

Cada texto é lido literalmente pela voz do copiloto (Gemini Live) e tocado do cache de frases (`PhraseCache`: `~/.cache/bmg/speech` ou `BMG_SPEECH_CACHE_DIR`), cuja chave inclui o texto, o modo, a voz (`SPEECH_VOICE`) e o modelo (`SPEECH_MODEL`). Sem rede, uma fala ausente do cache não é dita.

Fontes iniciadas por `src/` são relativas a `bebop_mission_control/`; as demais, à raiz do repositório.

Conjuntos de parâmetros falados: `cruise_mps=0.15, target_altitude_m=2`; `cruise_mps=0.2, target_altitude_m=1`. O `mission_config.json` da estação não entra nesta tabela; o pré-cache o inclui.

Total: 783 falas.

## 1. Voo normal

124 falas.

### 1.1 Contagem e plano de voo

Categoria `voo.contagem`, 21 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Clique em iniciar missão (mission.start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.start']` | Missão iniciada. Carregando parâmetros de voo. |
| Clique em iniciar missão (mission.start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.start']` | Sequência de missão iniciada. Parâmetros de voo em carregamento. |
| Clique em iniciar missão (mission.start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.start']` | Início de missão confirmado. Carregando configuração de voo. |
| Clique em iniciar missão (mission.start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.start']` | Missão autônoma iniciada. Lendo parâmetros da estação. |
| Clique em iniciar missão (mission.start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.start']` | Iniciando missão. Parâmetros de voo sendo carregados. |
| Clique em iniciar missão (mission.start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.start']` | Missão em preparação. Carregando perfil de voo. |
| Fim da contagem regressiva (mission.countdown_3) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.countdown_3']` | Parâmetros carregados. Decolagem em instantes. |
| Fim da contagem regressiva (mission.countdown_3) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.countdown_3']` | Decolagem iminente. Afastem-se da aeronave. |
| Fim da contagem regressiva (mission.countdown_3) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.countdown_3']` | Contagem final. Mantenham distância da aeronave. |
| Fim da contagem regressiva (mission.countdown_3) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.countdown_3']` | Perfil de voo carregado. Decolagem iminente. |
| Fim da contagem regressiva (mission.countdown_3) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.countdown_3']` | Área de decolagem deve estar livre. Decolagem em instantes. |
| Fim da contagem regressiva (mission.countdown_3) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.countdown_3']` | Parâmetros aplicados. Afastem-se da aeronave. |
| Início da contagem, plano com retorno à base | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.rtl']` | Plano de voo: inspeção com retorno à base e pouso de precisão. |
| Início da contagem, plano com retorno à base | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.rtl']` | Plano de voo: inspeção, retorno à base e pouso sobre o marcador. |
| Início da contagem, plano com retorno à base | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.rtl']` | Plano de voo: após a inspeção, retorno à base com pouso de precisão. |
| Início da contagem, modo avançado sem retorno | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.land_in_place']` | Plano de voo: inspeção com pouso no local após a foto. |
| Início da contagem, modo avançado sem retorno | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.land_in_place']` | Plano de voo: inspeção e pouso no ponto da foto. |
| Início da contagem, modo avançado sem retorno | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.land_in_place']` | Plano de voo: após a foto, pouso no local da inspeção. |
| Início da contagem, modo simples sem retorno | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.land_in_place_simple']` | Plano de voo: inspeção com pouso no local após a foto; o rumo volta ao da decolagem antes do pouso. |
| Início da contagem, modo simples sem retorno | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.land_in_place_simple']` | Plano de voo: inspeção e pouso no local; antes do pouso, o rumo volta ao da decolagem. |
| Início da contagem, modo simples sem retorno | `src/lib/copilotPhrases.ts:PLAN_PHRASES['plan.land_in_place_simple']` | Plano de voo: após a foto, o rumo volta ao da decolagem e a aeronave pousa no local. |

### 1.2 Decolagem

Categoria `voo.decolagem`, 20 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem autorizada. Subindo para 2 metros. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem em curso. Altitude alvo de 2 metros. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Aeronave no ar. Estabilizando a 2 metros de altura. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem confirmada. Altitude alvo de 2 metros. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Voo autônomo iniciado. Subida para 2 metros. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem executada. Altitude de operação de 2 metros. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem confirmada. Subindo para a altitude de operação. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Aeronave no ar. Subida para a altitude configurada. |
| Decolagem confirmada, modo simples (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff_native']` | Decolagem pela rotina nativa do Bebop. Estabilizando na altura padrão. |
| Decolagem confirmada, modo simples (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff_native']` | Decolagem nativa em curso. Aguardando a estabilização. |
| Decolagem confirmada, modo simples (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff_native']` | Aeronave no ar pela decolagem nativa. Estabilizando. |
| Decolagem confirmada, modo simples (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff_native']` | Decolagem confirmada. Estabilizando na altura nativa da aeronave. |
| Decolagem confirmada, modo simples (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff_native']` | Voo autônomo iniciado. Decolagem nativa concluída, estabilizando. |
| Decolagem confirmada, modo simples (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff_native']` | Decolagem executada pelo firmware. Estabilizando a aeronave. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem autorizada. Subindo para 1 metro. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem em curso. Altitude alvo de 1 metro. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Aeronave no ar. Estabilizando a 1 metro de altura. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem confirmada. Altitude alvo de 1 metro. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Voo autônomo iniciado. Subida para 1 metro. |
| Decolagem confirmada, modo avançado (mission.takeoff) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.takeoff']` | Decolagem executada. Altitude de operação de 1 metro. |

### 1.3 Varredura e detecção

Categoria `voo.varredura`, 22 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Varredura linear iniciada. Buscando ocorrências na pista. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Iniciando varredura da pista. Visão computacional ativa. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Deslocamento de busca iniciado. Monitorando a área. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Varredura em andamento. Procurando ocorrência ao longo da pista. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Busca iniciada. Detector de objetos em operação. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Iniciando busca retilínea. Câmera apontada para a pista. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Varredura iniciada a 0,15 metro por segundo. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Busca em curso a 0,15 metro por segundo. |
| Alvo confirmado pelo detector (mission.target_found) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.target_found']` | Ocorrência identificada na pista. |
| Alvo confirmado pelo detector (mission.target_found) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.target_found']` | Alvo identificado na pista. |
| Alvo confirmado pelo detector (mission.target_found) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.target_found']` | Ocorrência localizada na pista. |
| Alvo confirmado pelo detector (mission.target_found) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.target_found']` | Detecção confirmada na pista. |
| Alvo confirmado pelo detector (mission.target_found) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.target_found']` | Acidente avistado na pista. |
| Alvo confirmado pelo detector (mission.target_found) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.target_found']` | Alvo avistado na pista. |
| Início da varredura por rotação, modo simples (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start_yaw']` | Varredura por rotação iniciada. Buscando ocorrências ao redor da aeronave. |
| Início da varredura por rotação, modo simples (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start_yaw']` | Girando sobre o próprio eixo. Visão computacional ativa. |
| Início da varredura por rotação, modo simples (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start_yaw']` | Busca angular iniciada. Detector de objetos em operação. |
| Início da varredura por rotação, modo simples (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start_yaw']` | Varredura em andamento. Procurando ocorrência em torno da aeronave. |
| Início da varredura por rotação, modo simples (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start_yaw']` | Busca por giro iniciada. Câmera ativa. |
| Início da varredura por rotação, modo simples (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start_yaw']` | Rotação de busca iniciada. Monitorando a área. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Varredura iniciada a 0,2 metro por segundo. |
| Início da varredura lateral (mission.scan_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.scan_start']` | Busca em curso a 0,2 metro por segundo. |

### 1.4 Aproximação

Categoria `voo.aproximacao`, 18 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Aproximação com guiagem visual (mission.approaching, ibvs true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching']` | Iniciando aproximação controlada ao alvo. |
| Aproximação com guiagem visual (mission.approaching, ibvs true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching']` | Aproximação em curso. Guiagem visual engajada. |
| Aproximação com guiagem visual (mission.approaching, ibvs true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching']` | Deslocando até a ocorrência sob controle visual. |
| Aproximação com guiagem visual (mission.approaching, ibvs true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching']` | Aproximação iniciada. Centralizando o alvo na câmera. |
| Aproximação com guiagem visual (mission.approaching, ibvs true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching']` | Guiagem visual ativa. Aproximando do local da ocorrência. |
| Aproximação com guiagem visual (mission.approaching, ibvs true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching']` | Em aproximação ao alvo. Velocidade reduzida. |
| Aproximação sem guiagem visual (mission.approaching, ibvs false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_direct']` | Aproximação controlada ao alvo iniciada. |
| Aproximação sem guiagem visual (mission.approaching, ibvs false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_direct']` | Aproximando do alvo em velocidade reduzida. |
| Aproximação sem guiagem visual (mission.approaching, ibvs false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_direct']` | Aproximação ao alvo em curso. |
| Aproximação sem guiagem visual (mission.approaching, ibvs false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_direct']` | Deslocando até a ocorrência. |
| Aproximação sem guiagem visual (mission.approaching, ibvs false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_direct']` | Aproximação iniciada. Aeronave a caminho do alvo. |
| Aproximação sem guiagem visual (mission.approaching, ibvs false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_direct']` | Aproximando do local da ocorrência. |
| Centralização angular, modo simples (mission.approaching) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_yaw']` | Alvo na imagem. Centralizando por rotação. |
| Centralização angular, modo simples (mission.approaching) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_yaw']` | Centralização angular do alvo iniciada. |
| Centralização angular, modo simples (mission.approaching) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_yaw']` | Alinhando a aeronave à ocorrência por giro. |
| Centralização angular, modo simples (mission.approaching) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_yaw']` | Guiagem visual ativa. Alinhando o alvo ao centro da câmera. |
| Centralização angular, modo simples (mission.approaching) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_yaw']` | Centralizando a ocorrência no quadro apenas por giro. |
| Centralização angular, modo simples (mission.approaching) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.approaching_yaw']` | Ajuste de rotação em curso. Alvo sendo centralizado. |

### 1.5 Captura

Categoria `voo.captura`, 6 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Registro com alvo confirmado (mission.capture_done, target_confirmed true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_done']` | Registro fotográfico concluído. Imagem enviada para inspeção. |
| Registro com alvo confirmado (mission.capture_done, target_confirmed true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_done']` | Evidência capturada. Foto encaminhada para análise. |
| Registro com alvo confirmado (mission.capture_done, target_confirmed true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_done']` | Captura concluída. Imagem disponível para inspeção. |
| Registro com alvo confirmado (mission.capture_done, target_confirmed true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_done']` | Registro fotográfico da ocorrência concluído. Processando evidência. |
| Registro com alvo confirmado (mission.capture_done, target_confirmed true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_done']` | Registro pericial concluído. Imagem enviada à estação. |
| Registro com alvo confirmado (mission.capture_done, target_confirmed true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_done']` | Evidência fotográfica armazenada. Pronta para inspeção. |

### 1.6 Retorno

Categoria `voo.retorno`, 12 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Retorno comandado pela estação (mission.rtl_start, via_jump true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_jump']` | Retorno à base comandado pela estação. |
| Retorno comandado pela estação (mission.rtl_start, via_jump true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_jump']` | Retorno comandado. Navegando para o ponto de lançamento. |
| Retorno comandado pela estação (mission.rtl_start, via_jump true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_jump']` | Retorno à base comandado. Rota de volta em curso. |
| Retorno comandado pela estação (mission.rtl_start, via_jump true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_jump']` | Retorno comandado. Voltando à base. |
| Retorno comandado pela estação (mission.rtl_start, via_jump true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_jump']` | Retorno comandado pelo operador. Regressando à base. |
| Retorno comandado pela estação (mission.rtl_start, via_jump true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_jump']` | Retorno à base comandado. Buscando marcador de pouso. |
| Retorno após a inspeção, modo avançado com retorno (mission.rtl_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start']` | Iniciando retorno à base. |
| Retorno após a inspeção, modo avançado com retorno (mission.rtl_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start']` | Retorno à base iniciado. Navegando para o ponto de lançamento. |
| Retorno após a inspeção, modo avançado com retorno (mission.rtl_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start']` | Inspeção concluída. Regressando à base. |
| Retorno após a inspeção, modo avançado com retorno (mission.rtl_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start']` | Retornando ao ponto de decolagem. Buscando marcador de pouso. |
| Retorno após a inspeção, modo avançado com retorno (mission.rtl_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start']` | Retorno autônomo engajado. Rota de volta à base. |
| Retorno após a inspeção, modo avançado com retorno (mission.rtl_start) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start']` | Deixando o local da ocorrência. Retorno à base em curso. |

### 1.7 Pouso

Categoria `voo.pouso`, 25 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Pouso fora da base ou sem retorno (mission.landing) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_in_place']` | Iniciando pouso no local. Mantenham a área livre. |
| Pouso fora da base ou sem retorno (mission.landing) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_in_place']` | Pouso no ponto atual. Área deve estar desobstruída. |
| Pouso fora da base ou sem retorno (mission.landing) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_in_place']` | Descida final para pouso no ponto atual. |
| Pouso fora da base ou sem retorno (mission.landing) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_in_place']` | Pouso no local iniciado. Descendo. |
| Pouso fora da base ou sem retorno (mission.landing) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_in_place']` | Preparando pouso no local. Atenção à área. |
| Pouso fora da base ou sem retorno (mission.landing) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_in_place']` | Pouso fora do ponto de lançamento iniciado. |
| Pouso na base (mission.landing, at_base true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing']` | Iniciando pouso. Mantenham a área livre. |
| Pouso na base (mission.landing, at_base true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing']` | Pouso iminente na base. Área de pouso deve estar desobstruída. |
| Pouso na base (mission.landing, at_base true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing']` | Aeronave em descida final para pouso. |
| Pouso na base (mission.landing, at_base true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing']` | Preparando pouso. Atenção na área da base. |
| Pouso na base (mission.landing, at_base true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing']` | Descida para pouso iniciada. |
| Pouso na base (mission.landing, at_base true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing']` | Aproximação final concluída. Pousando. |
| Toque confirmado na base (mission.touchdown, confirmed e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown']` | Pouso seguro concluído com sucesso na base. |
| Toque confirmado na base (mission.touchdown, confirmed e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown']` | Pouso confirmado na base. |
| Toque confirmado na base (mission.touchdown, confirmed e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown']` | Aeronave pousada na base de lançamento. |
| Toque confirmado na base (mission.touchdown, confirmed e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown']` | Toque no solo confirmado na base. |
| Toque confirmado na base (mission.touchdown, confirmed e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown']` | Pouso concluído na base de lançamento. |
| Toque confirmado na base (mission.touchdown, confirmed e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown']` | Aeronave em solo na base. Pouso confirmado. |
| Toque confirmado fora da base (mission.touchdown, confirmed) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_in_place']` | Pouso confirmado no local. |
| Toque confirmado fora da base (mission.touchdown, confirmed) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_in_place']` | Aeronave pousada no ponto atual. Pouso confirmado. |
| Toque confirmado fora da base (mission.touchdown, confirmed) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_in_place']` | Pouso confirmado com toque no solo, no local. |
| Toque confirmado fora da base (mission.touchdown, confirmed) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_in_place']` | Pouso concluído no ponto atual. |
| Toque confirmado fora da base (mission.touchdown, confirmed) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_in_place']` | Aeronave em solo no local do pouso. |
| Toque confirmado fora da base (mission.touchdown, confirmed) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_in_place']` | Pouso confirmado. Aeronave em solo. |
| Pouso sem confirmação odométrica do toque (mission.touchdown) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_unconfirmed']` | Pouso comandado. Confirmação de toque indisponível. Verifique visualmente. |

## 2. Laudo pericial

544 falas.

### 2.1 Aberturas

Categoria `laudo.abertura`, 6 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Aeronave em solo, início do laudo (inspection.intro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.intro']` | Inspeção da ocorrência concluída. Apresentando laudo preliminar. |
| Aeronave em solo, início do laudo (inspection.intro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.intro']` | Aeronave em solo. Apresentando os achados da inspeção. |
| Aeronave em solo, início do laudo (inspection.intro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.intro']` | Análise da ocorrência concluída. Seguem os pontos do laudo. |
| Aeronave em solo, início do laudo (inspection.intro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.intro']` | Laudo preliminar disponível. Apresentando os resultados. |
| Aeronave em solo, início do laudo (inspection.intro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.intro']` | Inspeção finalizada. Relatando os pontos avaliados. |
| Aeronave em solo, início do laudo (inspection.intro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.intro']` | Avaliação do local concluída. Apresentando o parecer preliminar. |

### 2.2 Veículo com guincho

Categoria `laudo.veiculo`, 95 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para começar, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De início, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Logo de cara, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Começando pelo essencial, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De saída, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Abrindo a análise, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Além disso, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Na sequência, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Somado a isso, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Em seguida, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Outro ponto: veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Também observamos: veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E mais: veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por fim, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para fechar, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para arrematar, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E, para concluir, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Encerrando, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por último, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para começar, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De início, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Logo de cara, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Começando pelo essencial, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De saída, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Abrindo a análise, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Além disso, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Na sequência, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Somado a isso, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Em seguida, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Outro ponto: veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Também observamos: veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E mais: veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por fim, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para fechar, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para arrematar, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E, para concluir, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Encerrando, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por último, veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para começar, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De início, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Logo de cara, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Começando pelo essencial, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De saída, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Abrindo a análise, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Além disso, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Na sequência, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Somado a isso, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Em seguida, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Outro ponto: sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Também observamos: sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E mais: sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por fim, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para fechar, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para arrematar, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E, para concluir, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Encerrando, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por último, sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para começar, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De início, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Logo de cara, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Começando pelo essencial, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De saída, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Abrindo a análise, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Além disso, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Na sequência, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Somado a isso, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Em seguida, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Outro ponto: integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Também observamos: integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E mais: integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por fim, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para fechar, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para arrematar, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E, para concluir, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Encerrando, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por último, integridade do automóvel preservada. Guincho acionado para desobstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para começar, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De início, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Logo de cara, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Começando pelo essencial, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | De saída, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Abrindo a análise, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Além disso, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Na sequência, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Somado a isso, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Em seguida, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Outro ponto: veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Também observamos: veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E mais: veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por fim, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para fechar, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Para arrematar, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | E, para concluir, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Encerrando, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |
| Card do veículo, com guincho acionado | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.vehicle)` | Por último, veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via. |

### 2.3 Vítima detectada, não detectada e desconhecida

Categoria `laudo.vitima`, 285 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para começar, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De início, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Logo de cara, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Começando pelo essencial, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De saída, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Abrindo a análise, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Além disso, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Na sequência, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Somado a isso, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Em seguida, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Outro ponto: vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Também observamos: vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E mais: vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por fim, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para fechar, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para arrematar, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E, para concluir, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Encerrando, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por último, vítima consciente e sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para começar, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De início, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Logo de cara, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Começando pelo essencial, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De saída, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Abrindo a análise, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Além disso, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Na sequência, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Somado a isso, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Em seguida, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Outro ponto: acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Também observamos: acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E mais: acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por fim, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para fechar, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para arrematar, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E, para concluir, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Encerrando, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por último, acidentado consciente, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para começar, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De início, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Logo de cara, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Começando pelo essencial, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De saída, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Abrindo a análise, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Além disso, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Na sequência, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Somado a isso, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Em seguida, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Outro ponto: vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Também observamos: vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E mais: vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por fim, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para fechar, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para arrematar, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E, para concluir, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Encerrando, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por último, vítima identificada, consciente e sem lesões aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para começar, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De início, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Logo de cara, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Começando pelo essencial, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De saída, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Abrindo a análise, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Além disso, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Na sequência, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Somado a isso, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Em seguida, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Outro ponto: estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Também observamos: estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E mais: estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por fim, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para fechar, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para arrematar, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E, para concluir, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Encerrando, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por último, estado do acidentado não é grave, sem ferimentos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para começar, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De início, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Logo de cara, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Começando pelo essencial, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | De saída, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Abrindo a análise, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Além disso, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Na sequência, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Somado a isso, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Em seguida, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Outro ponto: acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Também observamos: acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E mais: acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por fim, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para fechar, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Para arrematar, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | E, para concluir, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Encerrando, acidentado consciente e sem ferimentos críticos aparentes. |
| Vítima detectada na imagem da inspeção (victim_detected true) | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.detected)` | Por último, acidentado consciente e sem ferimentos críticos aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para começar, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De início, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Logo de cara, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Começando pelo essencial, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De saída, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Abrindo a análise, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Além disso, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Na sequência, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Somado a isso, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Em seguida, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Outro ponto: nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Também observamos: nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E mais: nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por fim, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para fechar, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para arrematar, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E, para concluir, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Encerrando, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por último, nenhuma vítima visível na cena. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para começar, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De início, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Logo de cara, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Começando pelo essencial, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De saída, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Abrindo a análise, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Além disso, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Na sequência, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Somado a isso, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Em seguida, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Outro ponto: sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Também observamos: sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E mais: sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por fim, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para fechar, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para arrematar, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E, para concluir, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Encerrando, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por último, sem vítimas visíveis no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para começar, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De início, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Logo de cara, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Começando pelo essencial, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De saída, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Abrindo a análise, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Além disso, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Na sequência, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Somado a isso, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Em seguida, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Outro ponto: nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Também observamos: nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E mais: nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por fim, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para fechar, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para arrematar, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E, para concluir, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Encerrando, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por último, nenhum acidentado identificado na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para começar, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De início, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Logo de cara, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Começando pelo essencial, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De saída, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Abrindo a análise, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Além disso, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Na sequência, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Somado a isso, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Em seguida, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Outro ponto: cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Também observamos: cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E mais: cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por fim, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para fechar, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para arrematar, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E, para concluir, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Encerrando, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por último, cena sem vítimas aparentes. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para começar, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De início, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Logo de cara, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Começando pelo essencial, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De saída, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Abrindo a análise, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Além disso, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Na sequência, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Somado a isso, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Em seguida, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Outro ponto: ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Também observamos: ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E mais: ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por fim, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para fechar, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para arrematar, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E, para concluir, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Encerrando, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por último, ausência de vítimas visíveis na imagem. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para começar, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De início, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Logo de cara, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Começando pelo essencial, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | De saída, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Abrindo a análise, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Além disso, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Na sequência, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Somado a isso, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Em seguida, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Outro ponto: nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Também observamos: nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E mais: nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por fim, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para fechar, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Para arrematar, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | E, para concluir, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Encerrando, nenhuma vítima visível no local. |
| Nenhuma vítima detectada na imagem da inspeção | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.not_detected)` | Por último, nenhuma vítima visível no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para começar, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De início, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Logo de cara, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Começando pelo essencial, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De saída, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Abrindo a análise, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Além disso, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Na sequência, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Somado a isso, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Em seguida, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Outro ponto: avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Também observamos: avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E mais: avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por fim, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para fechar, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para arrematar, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E, para concluir, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Encerrando, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por último, avaliação da vítima não incluída neste laudo preliminar. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para começar, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De início, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Logo de cara, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Começando pelo essencial, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De saída, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Abrindo a análise, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Além disso, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Na sequência, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Somado a isso, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Em seguida, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Outro ponto: condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Também observamos: condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E mais: condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por fim, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para fechar, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para arrematar, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E, para concluir, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Encerrando, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por último, condição da vítima a confirmar em verificação presencial. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para começar, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De início, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Logo de cara, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Começando pelo essencial, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De saída, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Abrindo a análise, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Além disso, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Na sequência, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Somado a isso, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Em seguida, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Outro ponto: sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Também observamos: sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E mais: sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por fim, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para fechar, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para arrematar, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E, para concluir, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Encerrando, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por último, sem dados para avaliar a vítima neste registro. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para começar, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De início, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Logo de cara, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Começando pelo essencial, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | De saída, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Abrindo a análise, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Além disso, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Na sequência, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Somado a isso, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Em seguida, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Outro ponto: avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Também observamos: avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E mais: avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por fim, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para fechar, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Para arrematar, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | E, para concluir, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Encerrando, avaliação da vítima pendente de conferência no local. |
| Inferência de vítima não concluída (unknown): linha neutra | `src/lib/forensics.ts:allFindingSentences (VICTIM_WORDINGS.unknown)` | Por último, avaliação da vítima pendente de conferência no local. |

### 2.4 SAMU

Categoria `laudo.samu`, 76 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para começar, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De início, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Logo de cara, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Começando pelo essencial, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De saída, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Abrindo a análise, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Além disso, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Na sequência, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Somado a isso, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Em seguida, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Outro ponto: sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Também observamos: sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E mais: sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por fim, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para fechar, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para arrematar, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E, para concluir, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Encerrando, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por último, sem necessidade de Samu. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para começar, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De início, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Logo de cara, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Começando pelo essencial, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De saída, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Abrindo a análise, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Além disso, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Na sequência, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Somado a isso, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Em seguida, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Outro ponto: socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Também observamos: socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E mais: socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por fim, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para fechar, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para arrematar, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E, para concluir, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Encerrando, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por último, socorro médico dispensado. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para começar, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De início, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Logo de cara, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Começando pelo essencial, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De saída, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Abrindo a análise, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Além disso, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Na sequência, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Somado a isso, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Em seguida, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Outro ponto: atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Também observamos: atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E mais: atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por fim, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para fechar, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para arrematar, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E, para concluir, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Encerrando, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por último, atendimento emergencial não necessário. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para começar, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De início, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Logo de cara, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Começando pelo essencial, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | De saída, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Abrindo a análise, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Além disso, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Na sequência, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Somado a isso, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Em seguida, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Outro ponto: SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Também observamos: SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E mais: SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por fim, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para fechar, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Para arrematar, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | E, para concluir, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Encerrando, SAMU dispensado para a ocorrência. |
| Card de socorro médico do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.samu)` | Por último, SAMU dispensado para a ocorrência. |

### 2.5 Polícia

Categoria `laudo.policia`, 76 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para começar, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De início, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Logo de cara, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Começando pelo essencial, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De saída, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Abrindo a análise, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Além disso, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Na sequência, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Somado a isso, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Em seguida, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Outro ponto: sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Também observamos: sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E mais: sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por fim, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para fechar, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para arrematar, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E, para concluir, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Encerrando, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por último, sem necessidade de polícia. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para começar, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De início, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Logo de cara, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Começando pelo essencial, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De saída, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Abrindo a análise, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Além disso, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Na sequência, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Somado a isso, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Em seguida, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Outro ponto: acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Também observamos: acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E mais: acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por fim, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para fechar, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para arrematar, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E, para concluir, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Encerrando, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por último, acionamento policial dispensado. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para começar, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De início, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Logo de cara, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Começando pelo essencial, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De saída, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Abrindo a análise, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Além disso, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Na sequência, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Somado a isso, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Em seguida, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Outro ponto: sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Também observamos: sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E mais: sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por fim, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para fechar, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para arrematar, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E, para concluir, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Encerrando, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por último, sem demanda para autoridade policial. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para começar, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De início, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Logo de cara, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Começando pelo essencial, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | De saída, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Abrindo a análise, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Além disso, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Na sequência, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Somado a isso, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Em seguida, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Outro ponto: segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Também observamos: segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E mais: segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por fim, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para fechar, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Para arrematar, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | E, para concluir, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Encerrando, segurança pública não requisitada. |
| Card de autoridade policial do laudo | `src/lib/forensics.ts:allFindingSentences (FIXED_WORDINGS.police)` | Por último, segurança pública não requisitada. |

### 2.6 Encerramento

Categoria `laudo.encerramento`, 6 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Último card do laudo exibido (inspection.outro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.outro']` | Relatório pericial emitido e pronto para exportação. |
| Último card do laudo exibido (inspection.outro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.outro']` | Laudo concluído. Relatório disponível para exportação. |
| Último card do laudo exibido (inspection.outro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.outro']` | Fim do laudo preliminar. Documento pronto para envio. |
| Último card do laudo exibido (inspection.outro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.outro']` | Relatório finalizado e disponível na estação. |
| Último card do laudo exibido (inspection.outro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.outro']` | Parecer emitido. Missão pronta para encerramento. |
| Último card do laudo exibido (inspection.outro) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['inspection.outro']` | Laudo pericial completo. Relatório pronto para exportação. |

### 2.7 Regra de ordem e observações

- Cenário A (vítima não detectada ou inferência não concluída): Veículo, Vítima, depois SAMU e Polícia.
- Cenário B (vítima detectada): Vítima, Veículo, depois SAMU e Polícia.
- SAMU e Polícia alternam a ordem entre si de um voo para o outro (finding.dispatch_order).
- Cada card é lido após um conector: 6 aberturas no primeiro, 7 ligações nos do meio e 6 fechamentos no último.

## 3. Falhas

115 falas.

### 3.1 Aborto manual e falhas de etapa

Categoria `falha.aborto`, 27 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Aborto manual com a aeronave em voo ou sem solo confirmado | `electron/main.cjs:bmg:abort-mission` | Missão abortada. Pouso imediato comandado. |
| Aborto manual com a aeronave comprovadamente em solo (flying_state 0) | `electron/main.cjs:bmg:abort-mission` | Missão abortada. |
| Mantida do cache anterior; nenhuma composição atual a produz | `src/hooks/useCopilot.ts (lista histórica do cache)` | Falha de segurança. |
| Falha de etapa relatada sem nome de etapa | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na etapa. |
| Alerta da missão recebido sem frase própria | `src/lib/copilotPhrases.ts:ALERT_FALLBACK` | Alerta de voo recebido. Verifique a aeronave. |
| Failsafe da missão: teto de altitude excedido, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: teto de altitude excedido. Executando pouso seguro imediatamente. |
| Failsafe da missão: teto de altitude excedido, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: teto de altitude excedido. |
| Failsafe da missão: referência de solo perdida na subida, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: referência de solo perdida na subida. Executando pouso seguro imediatamente. |
| Failsafe da missão: referência de solo perdida na subida, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: referência de solo perdida na subida. |
| Failsafe da missão: failsafe já ativo, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: failsafe já ativo. Executando pouso seguro imediatamente. |
| Failsafe da missão: failsafe já ativo, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: failsafe já ativo. |
| Failsafe da missão: falha interna da missão, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: falha interna da missão. Executando pouso seguro imediatamente. |
| Failsafe da missão: falha interna da missão, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: falha interna da missão. |
| Etapa interrompida por aborto (StepStatus.ABORTED); pouso comandado pela missão | `mvp_mission_bebop/engine/runner.py:run` | Missão abortada, pousando drone. |
| Etapa Decolagem falhou sem alerta próprio, com a aeronave em voo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Decolagem. Executando pouso seguro imediatamente. |
| Etapa Decolagem falhou sem alerta próprio, com a aeronave em solo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Decolagem. |
| Etapa Varredura falhou sem alerta próprio, com a aeronave em voo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Varredura. Executando pouso seguro imediatamente. |
| Etapa Varredura falhou sem alerta próprio, com a aeronave em solo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Varredura. |
| Etapa Aproximação falhou sem alerta próprio, com a aeronave em voo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Aproximação. Executando pouso seguro imediatamente. |
| Etapa Aproximação falhou sem alerta próprio, com a aeronave em solo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Aproximação. |
| Etapa Inspeção falhou sem alerta próprio, com a aeronave em voo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Inspeção. Executando pouso seguro imediatamente. |
| Etapa Inspeção falhou sem alerta próprio, com a aeronave em solo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Inspeção. |
| Etapa Retorno à base falhou sem alerta próprio, com a aeronave em voo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Retorno à base. Executando pouso seguro imediatamente. |
| Etapa Retorno à base falhou sem alerta próprio, com a aeronave em solo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Retorno à base. |
| Etapa Pouso no local falhou sem alerta próprio, com a aeronave em voo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Pouso no local. Executando pouso seguro imediatamente. |
| Etapa Pouso no local falhou sem alerta próprio, com a aeronave em solo | `mvp_mission_bebop/engine/runner.py:_announce_failure` | Falha na etapa: Pouso no local. |
| Forma do formatador sem motivo informado, com a aeronave em voo | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na etapa. Executando pouso seguro imediatamente. |

### 3.2 Perda de enlace e watchdog

Categoria `falha.link`, 12 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Telemetria da aeronave interrompida durante um voo real (useLinkLossNarration), uma vez por perda | `src/lib/copilotPhrases.ts:LINK_LOSS_SENTENCES` | Enlace com a aeronave perdido. Verifique a aeronave visualmente. |
| Telemetria da aeronave interrompida durante um voo real (useLinkLossNarration), uma vez por perda | `src/lib/copilotPhrases.ts:LINK_LOSS_SENTENCES` | Telemetria interrompida. Confirme a posição da aeronave a olho. |
| Telemetria da aeronave interrompida durante um voo real (useLinkLossNarration), uma vez por perda | `src/lib/copilotPhrases.ts:LINK_LOSS_SENTENCES` | Sem dados da aeronave. Mantenha contato visual e prepare o pouso manual. |
| Driver do drone inacessível no endereço padrão do Bebop 2; missão encerrada antes do voo | `mvp_mission_bebop/mission.py:main` | Falha na inicialização: Falha de conexão com driver do drone no IP 192.168.42.1. |
| Failsafe da missão: odometria nunca recebida, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: odometria nunca recebida. Executando pouso seguro imediatamente. |
| Failsafe da missão: odometria nunca recebida, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: odometria nunca recebida. |
| Failsafe da missão: perda da telemetria de odometria, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: perda da telemetria de odometria. Executando pouso seguro imediatamente. |
| Failsafe da missão: perda da telemetria de odometria, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: perda da telemetria de odometria. |
| Failsafe da missão: perda do vídeo da câmera, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: perda do vídeo da câmera. Executando pouso seguro imediatamente. |
| Failsafe da missão: perda do vídeo da câmera, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: perda do vídeo da câmera. |
| Forma do formatador sem motivo informado | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na inicialização. |
| Forma do formatador sem motivo informado, com a aeronave em voo | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na inicialização. Executando pouso seguro imediatamente. |

### 3.3 Bateria crítica e auto-land

Categoria `falha.bateria`, 53 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Aviso de bateria baixa. Missão segue. |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Atenção à carga da bateria. |
| Bateria no limiar crítico, retorno forçado, modo avançado (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return']` | Bateria crítica. Missão interrompida. Retornando à base para pouso. |
| Bateria no limiar crítico, retorno forçado, modo avançado (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return']` | Limite de bateria atingido. Voltando à base. |
| Bateria no limiar crítico, retorno forçado, modo avançado (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return']` | Bateria crítica. Interrompendo a operação. Retorno à base engajado. |
| Bateria no limiar crítico, retorno forçado, modo avançado (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return']` | Carga no limite de segurança. Retornando à base para pousar. |
| Bateria no limiar crítico, retorno forçado, modo avançado (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return']` | Bateria crítica. Etapa abortada. Voltando à base. |
| Bateria no limiar crítico, retorno forçado, modo avançado (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return']` | Bateria no limite. Missão interrompida, retornando à base. |
| Início da busca do marcador no retorno por bateria (mission.rtl_start, battery_return true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_battery']` | Retorno por bateria engajado. Buscando o marcador de pouso. |
| Início da busca do marcador no retorno por bateria (mission.rtl_start, battery_return true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_battery']` | Rota de volta à base por limite de bateria. |
| Início da busca do marcador no retorno por bateria (mission.rtl_start, battery_return true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_battery']` | Regressando ao ponto de lançamento. Bateria no limite. |
| Início da busca do marcador no retorno por bateria (mission.rtl_start, battery_return true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_battery']` | Retorno à base em curso por limite de bateria. |
| Início da busca do marcador no retorno por bateria (mission.rtl_start, battery_return true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_battery']` | Voltando à base para pouso. Procurando o marcador. |
| Início da busca do marcador no retorno por bateria (mission.rtl_start, battery_return true) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_battery']` | A caminho da base. Buscando o marcador de pouso. |
| Pouso na base no retorno por bateria (mission.landing, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery']` | Pouso por bateria iniciado na base. Mantenham a área livre. |
| Pouso na base no retorno por bateria (mission.landing, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery']` | Bateria no limite. Iniciando descida na base. |
| Pouso na base no retorno por bateria (mission.landing, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery']` | Descida final na base por limite de bateria. |
| Pouso na base no retorno por bateria (mission.landing, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery']` | Pousando sobre a base. Carga no limite de segurança. |
| Pouso na base no retorno por bateria (mission.landing, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery']` | Pouso de segurança iniciado na base. |
| Pouso na base no retorno por bateria (mission.landing, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery']` | Descendo para pouso na base. Área livre, por favor. |
| Pouso fora da base no retorno por bateria (mission.landing, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery_in_place']` | Marcador da base não localizado. Pouso no local. |
| Pouso fora da base no retorno por bateria (mission.landing, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery_in_place']` | Base não localizada. Pousando no local por bateria. |
| Pouso fora da base no retorno por bateria (mission.landing, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery_in_place']` | Pouso de segurança no ponto atual. Mantenham a área livre. |
| Pouso fora da base no retorno por bateria (mission.landing, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery_in_place']` | Pouso no local por limite de bateria. Atenção à área. |
| Pouso fora da base no retorno por bateria (mission.landing, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery_in_place']` | Sem marcador à vista. Pouso de segurança no local. |
| Pouso fora da base no retorno por bateria (mission.landing, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landing_battery_in_place']` | Carga no limite. Iniciando pouso no ponto atual. |
| Toque confirmado na base no retorno por bateria (mission.touchdown, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery']` | Pouso confirmado na base. Missão encerrada por bateria. |
| Toque confirmado na base no retorno por bateria (mission.touchdown, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery']` | Aeronave em solo na base. Pouso por bateria concluído. |
| Toque confirmado na base no retorno por bateria (mission.touchdown, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery']` | Toque no solo confirmado na base, por limite de bateria. |
| Toque confirmado na base no retorno por bateria (mission.touchdown, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery']` | Pouso seguro na base concluído. Carga no limite. |
| Toque confirmado na base no retorno por bateria (mission.touchdown, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery']` | Aeronave pousada na base. Missão encerrada por bateria. |
| Toque confirmado na base no retorno por bateria (mission.touchdown, battery_return e at_base) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery']` | Toque confirmado na base. Pouso de segurança concluído. |
| Toque confirmado fora da base no retorno por bateria (mission.touchdown, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery_in_place']` | Pouso confirmado no local. Missão encerrada por bateria. |
| Toque confirmado fora da base no retorno por bateria (mission.touchdown, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery_in_place']` | Aeronave em solo no local. Pouso por bateria concluído. |
| Toque confirmado fora da base no retorno por bateria (mission.touchdown, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery_in_place']` | Toque no solo confirmado no local, por limite de bateria. |
| Toque confirmado fora da base no retorno por bateria (mission.touchdown, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery_in_place']` | Pouso seguro no local concluído. Carga no limite. |
| Toque confirmado fora da base no retorno por bateria (mission.touchdown, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery_in_place']` | Aeronave pousada no ponto atual. Missão encerrada por bateria. |
| Toque confirmado fora da base no retorno por bateria (mission.touchdown, battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.touchdown_battery_in_place']` | Toque confirmado no local. Pouso de segurança concluído. |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Bateria abaixo de 10 por cento. |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Atenção: bateria abaixo de 10 por cento. |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Carga da bateria abaixo de 10 por cento. Missão segue. |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Aviso de bateria: abaixo de 10 por cento. |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Bateria em nível de alerta, abaixo de 10 por cento. |
| Bateria no aviso fixo de 10 por cento (mission.battery_warning) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_warning']` | Bateria abaixo de 10 por cento. Monitorando a carga. |
| Bateria no limiar crítico, pouso forçado sobre a base, modo simples (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return_simple']` | Bateria crítica. Missão interrompida. Pouso na base iniciado. |
| Bateria no limiar crítico, pouso forçado sobre a base, modo simples (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return_simple']` | Limite de bateria atingido. Pousando sobre a base. |
| Bateria no limiar crítico, pouso forçado sobre a base, modo simples (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return_simple']` | Bateria crítica. Preparando o pouso sobre a base. |
| Bateria no limiar crítico, pouso forçado sobre a base, modo simples (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return_simple']` | Carga no limite de segurança. Pousando sobre a base. |
| Bateria no limiar crítico, pouso forçado sobre a base, modo simples (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return_simple']` | Bateria crítica. Etapa abortada. Pouso sobre a base. |
| Bateria no limiar crítico, pouso forçado sobre a base, modo simples (mission.battery_return) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.battery_return_simple']` | Bateria no limite. Missão interrompida, pouso na base. |
| Bateria no limiar crítico; sem missão em execução, salto recusado ou sem confirmação do retorno em duas janelas, pouso no local | `src/lib/batteryReturn.ts:BATTERY_LANDING_CALL` | Bateria crítica. Pouso de emergência iniciado. |
| Failsafe da missão: bateria no limiar de pouso, com a aeronave em voo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de voo: bateria no limiar de pouso. Executando pouso seguro imediatamente. |
| Failsafe da missão: bateria no limiar de pouso, com a aeronave em solo | `mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land` | Alerta de segurança: bateria no limiar de pouso. |

### 3.4 Timeout de varredura sem alvo

Categoria `falha.timeout_varredura`, 12 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Registro sem alvo confirmado, após a janela de varredura (mission.capture_done) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_reference']` | Captura de referência registrada, sem alvo confirmado. |
| Registro sem alvo confirmado, após a janela de varredura (mission.capture_done) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_reference']` | Imagem de referência salva, sem alvo confirmado. |
| Registro sem alvo confirmado, após a janela de varredura (mission.capture_done) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_reference']` | Registro de referência concluído, sem alvo confirmado. |
| Registro sem alvo confirmado, após a janela de varredura (mission.capture_done) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_reference']` | Foto de referência capturada, sem alvo confirmado. |
| Registro sem alvo confirmado, após a janela de varredura (mission.capture_done) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_reference']` | Captura registrada sem alvo confirmado. |
| Registro sem alvo confirmado, após a janela de varredura (mission.capture_done) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.capture_reference']` | Referência visual salva, sem alvo confirmado. |
| Retorno sem alvo inspecionado (mission.rtl_start, inspected false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_no_target']` | Retorno à base em andamento. |
| Retorno sem alvo inspecionado (mission.rtl_start, inspected false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_no_target']` | Navegando de volta ao ponto de lançamento. |
| Retorno sem alvo inspecionado (mission.rtl_start, inspected false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_no_target']` | Voltando ao ponto de decolagem. Procurando o marcador de pouso. |
| Retorno sem alvo inspecionado (mission.rtl_start, inspected false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_no_target']` | Retorno autônomo ativo. Voltando para a base. |
| Retorno sem alvo inspecionado (mission.rtl_start, inspected false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_no_target']` | Busca encerrada. Retornando à base. |
| Retorno sem alvo inspecionado (mission.rtl_start, inspected false) | `src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.rtl_start_no_target']` | Regressando ao ponto de lançamento. |

### 3.5 Falha de calibração

Categoria `falha.calibracao`, 6 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Nivelamento (flat trim) não confirmado pela aeronave; decolagem cancelada | `mvp_mission_bebop/steps/takeoff.py:_flat_trim` | Nivelamento sem confirmação: a aeronave não confirmou o nivelamento, decolagem cancelada. |
| Referência de altitude de solo recusada; decolagem cancelada | `mvp_mission_bebop/steps/takeoff.py:_calibrate` | Falha na calibração: falha na calibração de referência de solo, decolagem cancelada. |
| Forma do formatador sem motivo informado | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na calibração. |
| Forma do formatador sem motivo informado, com a aeronave em voo | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na calibração. Executando pouso seguro imediatamente. |
| Forma do formatador sem motivo informado | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Nivelamento sem confirmação. |
| Forma do formatador sem motivo informado, com a aeronave em voo | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Nivelamento sem confirmação. Executando pouso seguro imediatamente. |

### 3.6 Recusa e falha de decolagem

Categoria `falha.decolagem`, 4 falas.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| A aeronave não confirmou a decolagem dentro do prazo (R1) | `mvp_mission_bebop/steps/takeoff.py:_confirm_takeoff (alerta mission.takeoff_failed)` | Falha na decolagem: decolagem não confirmada pela aeronave. |
| Falha na decolagem relatada sem motivo | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na decolagem. |
| Comando de decolagem rejeitado pela controladora de voo | `mvp_mission_bebop/steps/takeoff.py:_command_takeoff` | Falha na decolagem: decolagem rejeitada pela controladora de voo. |
| Forma do formatador sem motivo informado, com a aeronave em voo | `mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement` | Falha na decolagem. Executando pouso seguro imediatamente. |

### 3.7 Recusa de lançamento

Categoria `falha.recusa_lancamento`, 1 fala.

| Condição de disparo | Fonte | Texto |
| --- | --- | --- |
| Documento de lançamento recusado sem motivo informado | `src/lib/copilotPhrases.ts:alertSentence (mission.launch_refused)` | Lançamento recusado: documento inválido. |

## Operação do cache

Comandos (dentro do `nectar-activate`, na raiz do repositório): `python3 bebop_mission_control/streamer/precache_speech.py <opção>`.

| Opção | Efeito | Saída |
| --- | --- | --- |
| (nenhuma) | Sintetiza o que falta no cache e substitui entradas curtas; exige internet e `SPEECH_API_KEY` | 0 completo, 1 falhas, 2 sem chave ou sem rede |
| `--dry-run` | Contagem por categoria, sem rede e sem gravar | 0 |
| `--check` | Lista as falas ausentes e as curtas (menos de 0,25 s por palavra) por categoria | 0 completo, 1 faltam |
| `--prune` | Lista toda entrada que o manifesto atual não referencia, de qualquer voz ou modelo; só remove com `--yes`; recusa se menos da metade do manifesto existe na voz e modelo atuais | 0, 2 recusado |
| `--yes` | Confirma a remoção do `--prune` | |
| `--pack FILE` | Empacota o cache do manifesto em `tar.gz` com voz, modelo e SHA-1; recusa entradas curtas | 0 completo, 1 faltam ou curtas |
| `--unpack FILE` | Instala um pacote; recusa voz ou modelo diferentes | 0, 2 pacote inválido |
| `--table PATH` | Gera esta tabela | 0 |
| `--manifest FILE` | Usa um manifesto exportado em vez de rodar o exportador | |
| `--parameters FILE` | Repassado ao exportador (lista JSON de `mission.parameters`) | |
