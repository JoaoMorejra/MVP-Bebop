# Checklist de Voo Real e Matriz de Riscos — MVP Bebop

Este documento consolida o checklist operacional de pré-voo, as suposições críticas a validar no primeiro voo controlado e a matriz de riscos técnicos aceitos, conforme as Seções 8 e 9 de `docs/PROMPT_VOO_REAL_ESCOPO_ATUAL.md`.

---

## 1. O Que Nenhum Teste Sem a Aeronave Prova (Suposições de Hardware / Firmware)

Nenhum simulador ou emulador substitui a resposta real do firmware ARSDK e da física aerodinâmica. Os itens a seguir constituem **suposições** que devem ser validadas e medidas no primeiro voo curto controlado:

| # | Suposição | Impacto se Divergente | Método de Validação em Voo Controlado |
|---|---|---|---|
| **S1** | O firmware ARSDK ignora comandos PCMD (`cmd_vel`) enquanto a aeronave está em solo (`flying_state == 0`) e durante a descida final (`landing`). | Movimentação espúria dos motores antes da decolagem ou durante o toque. | Observação visual e telemetria: conferir se `cmd_vel` publicado em solo não induz rotação dos motores. |
| **S2** | O comando `land` emitido durante `motor_ramping` (7) ou `takingoff` (1) é respeitado pelo firmware. | Atraso no aborto durante a subida inicial. | Teste de aborto voluntário aos 0.5s após `takeoff` em bancada com hélice removida ou voo raso. |
| **S3** | Fidelidade da dinâmica do modelo emulado (constantes de tempo e inércia do Bebop 2). | PIDs podem apresentar sobre-sinal ou oscilação maior que o simulado. | Medição da curva real de subida e resposta aos degraus de velocidade. |
| **S4** | Deriva do odômetro por fluxo óptico no piso real e escala `normalized_to_mps`. | Erro acumulado na navegação relativa e centralização de pouso. | Comparação entre deslocamento estimado por odometria e deslocamento físico marcado no solo. |
| **S5** | Calibração do magnetômetro e enquadramento real da câmera frontal/nadir. | Erro de mira ArUco e IBVS no Stage 3 e Stage 5. | Validação visual da detecção de marcadores em bancada antes do voo. |
| **S6** | Latência real da síntese de voz Gemini Live com o uplink do tethering 4G/5G. | Atraso nos anúncios se a rede móvel oscilar. | Medição p95 dos eventos `[SPEECH_DONE]` em campo antes da decolagem. |
| **S7** | Taxa real de entrega de tópicos `cmd_vel` via Wi-Fi do Bebop 2. | Controle com jerk se houver perda de pacotes UDP. | Monitoramento via `ros2 topic hz /bebop/cmd_vel` durante voo curto. |
| **S8** | Tempo real entre o comando `takeoff` e os estados 1/2 (`takingoff`/`hovering`). | Se exceder 6.0s, o failsafe de decolagem abortará erroneamente. | Medição com cronômetro e telemetria no primeiro salto curto (calibra `takeoff_confirm_timeout_sec`). |

---

## 2. Riscos Aceitos para o Primeiro Voo (Seção 9)

Por decisão de escopo, os itens a seguir foram **adiados** e permanecem como riscos conhecidos e aceitos pelo operador:

| Item Adiado | Risco que Permanece | Mitigação Operacional |
|---|---|---|
| **Perda de Wi-Fi:** assinatura de `states/link`, `flying_state == 255`, banner na UI | O link perdido só é detectado pela interrupção da odometria (após 3 s); a missão não reage instantaneamente na camada de controle. | Operador deve manter linha de visada próxima (< 10 m) e acionar Abortar ou RTH manual no Skycontroller se houver desvio. |
| **Aeronave ociosa ou missão travada:** heartbeat do laço de controle | Se o nó da missão congelar sem emitir exceção, a aeronave permanece em hover até esgotar a bateria ou comando externo. | Failsafe de bateria do firmware da Parrot (RTH automático com bateria baixa). |
| **Missão morta em voo:** pouso automático da estação | Saída inesperada ou crash do processo `mission.py` em voo exige que o operador aperte o botão "ABORTAR" na estação. | Botão "ABORTAR MISSÃO" permanece habilitado mesmo sem processo de missão ativo (envia `land` direto via ponte/CLI). |
| **Supervisor de `flying_state` durante a missão; saúde no Stage 4** | Pouso ou emergência espontânea do firmware durante um step não é tratado como evento proativo da missão. | Operador monitora visualmente e encerra via Abortar ou desligamento de emergência. |
| **Rajada e confirmação de pouso unificadas no failsafe automático** | Failsafe automático de bateria da estação envia par único `cmd_vel` zero + `land`. | Supervisor de pouso da estação (R6) reenvia `land` a cada 500 ms se o estado continuar em voo. |
| **Driver:** watchdog de `cmd_vel`, reconexão ARSDK, `ReturnHomeOnDisconnect` | O último `cmd_vel` publicado pode persistir temporariamente se o driver cair. | Operador preparado para corte de emergência no Skycontroller/FreeFlight. |
| **Voz offline** | Sem internet móvel, alertas dinâmicos não são sintetizados pelo Gemini Live. | A estação exibe todos os alertas textualmente no banner de diagnóstico e cards visuais; fila não trava. |
| **Gravador de voo/telemetria** | Não haverá gravação binária em disco da telemetria completa em voo real; apenas stdout e logs da estação. | Logs de texto gravados em `~/.config/bmg/logs/` e fotos capturadas salvas no workspace. |
| **Novas falas (decolagem concluída, marcador localizado, etc.)** | Anúncios vocais cobrem apenas eventos existentes do MVP e o novo anúncio de aborto. | Feedback visual no Cockpit (StageBar e cards de telemetria) supre os passos intermediários. |

---

## 3. Checklist Pré-Voo do Operador (Passo a Passo)

### 3.1 Inspeção Mecânica e Espaço Físico
- [ ] **Área de voo:** Raio livre mínimo de 5m horizontal e 2.5m vertical, sem obstáculos, pessoas ou animais.
- [ ] **Iluminação:** Ambiente bem iluminado para permitir rastreamento pelo sensor óptico de fluxo e câmera.
- [ ] **Piso:** Superfície com textura visual rica (não usar piso reflexivo ou monótono) para ancoragem do fluxo óptico.
- [ ] **Aeronave:** Hélices fixadas e travadas nas posições corretas (duas horárias, duas anti-horárias); sem trincas.
- [ ] **Lentes:** Lente da câmera frontal e sensor vertical (nadir) limpos e desobstruídos.
- [ ] **Bateria da Aeronave:** Encaixada mecanicamente até o travamento do clipe traseiro.

### 3.2 Estação de Controle e Conectividade
- [ ] **Laptop GCS:** Conectado à internet via tethering USB de celular (para voz e telemetria).
- [ ] **Rede Wi-Fi:** Conectar interface Wi-Fi do laptop à rede `Bebop2-xxxxxx`.
- [ ] **Ping de teste:** `ping 192.168.42.1` respondendo com RTT < 10 ms.
- [ ] **Driver ROS 2:** Iniciado via GCS ou `make driver-bebop`.
- [ ] **Áudio:** Volume do laptop habilitado e desmutado para ouvir as orientações do copiloto.

### 3.3 Verificação no Dial de Pré-Voo (BMG)
- [ ] **Modo de Operação:** Voo Real (mint), NÃO Bancada (cyan).
- [ ] **Texto do Dial:** `"VOO REAL: motores serão armados"`.
- [ ] **Status dos Tópicos:** Todos os tópicos obrigatórios verdes (odometria, câmera, bateria, cmd_vel, land).
- [ ] **Bateria:** Leitura conhecida e >= 35% (piso de segurança).
- [ ] **Magnetômetro:** Calibração não requerida, verificada manualmente fora da estação (aplicativo da Parrot). A estação não lê nem bloqueia o lançamento por este estado.
- [ ] **Ponte de Comando:** Status `ready`.
- [ ] **Copiloto:** Voz pronta (online).

### 3.4 Procedimento de Lançamento
1. Conferir que todos ao redor estão cientes da decolagem iminente.
2. Posicionar o cursor sobre o dial central.
3. **Pressionar e segurar** o dial por **1,2 segundos** até o anel de armamento completar 100% e o rótulo mudar de `"VOO REAL: motores serão armados"` para `"ARMANDO"`.
4. Soltar o botão: a missão entra na contagem regressiva sonora, fixa em 10 segundos (não é parâmetro da estação), durante a qual executa a sequência de solo (flat trim e referência de solo). O aviso falado de afastamento soa 3,2 s antes do zero. É possível abortar a qualquer momento da contagem (3.5).
5. Manter as mãos prontas sobre a tecla **Escape** ou o botão vermelho **ABORTAR MISSÃO**.

### 3.5 Procedimento de Aborto de Emergência
- A qualquer momento durante a contagem, subida, voo ou aproximação:
  - Pressionar a tecla **Escape** no teclado OU
  - Clicar imediatamente no botão **ABORTAR MISSÃO** no topo do Cockpit.
- A estação cortará a velocidade instantaneamente, emitirá rajada de comandos `land` e acompanhará o pouso até a confirmação em solo (`flying_state == 0`).

## 4. Procedimento de Campo: Decolagem e Pouso no Totem de 1,0 m

Referência: `docs/PROMPT_IMPLEMENTACAO_TOTEM_VOO_ALTITUDE.md`, seções 1.5 e 6. Requisito: voar a 1,8 m
sobre o piso (0,8 m sobre o topo do totem). Os itens abaixo decidem os pontos **[A CONFIRMAR]** do
modelo de sonar; nenhum teste sem a aeronave os prova.

### 4.1 Pré-requisitos
- [ ] Totem de 1,0 m estável; marcador ID 8 no topo, no ponto de decolagem.
- [ ] Medir com trena `platform.edge_ahead_m`: ponto de decolagem -> borda do totem **à ESQUERDA** (eixo lateral
  da trilha de saída; valor esperado 0,30 m para o totem de 60 x 60 cm com decolagem no centro).
- [ ] **Posicionar a trilha de varredura na faixa de visada (D7).** O padrão de voo é estritamente lateral: a
  aeronave não avança nem recua, de modo que o alvo deve estar à frente da câmera na faixa de detecção
  `[h / tan(|tilt| + VFOV/2), h / tan(|tilt| - VFOV/2)]`, com `h` a altura sobre o alvo e `tilt` =
  `gimbal.search_tilt_deg`. Medir a distância da trilha ao alvo e conferir que cai dentro da faixa; fora dela
  o alvo não entra no quadro durante a varredura.
- [ ] **Alcance do retorno (Estágio 3 + RTL).** O Estágio 3 voa lateralmente até ~0,7 x R (R = distância ao
  alvo) ao longo do eixo de retorno, e o W08 (`needed = forward_cruise_velocity * search_timeout_sec /
  |reverse_cruise_velocity|`) não conta esse trecho: `rtl.timeout_sec` deve cobrir o Estágio 2 mais o
  deslocamento lateral do Estágio 3. Com a configuração padrão (0,15 normalizado, 30 s) o retorno cobre
  ~4,5 m; alvos além de ~4-5 m de distância exigem `rtl.timeout_sec` maior.
- [ ] Medir o teto do local sobre o piso e registrar em `platform.venue_ceiling_agl_m`.
- [ ] `mission_config.json`: `platform.height_m = 1.0`, `kinematics.target_altitude_m = 1.8`.
  O arquivo é o store local da estação e não é versionado: conferir os valores nesta máquina.
- [ ] Sonda ativa (`calibration.altitude_probe_enabled = true`): linhas `[ALT_PROBE ...]` a 1 Hz
  e `[PLATFORM ...]` a cada transição no log da missão.

### 4.2 Ensaios
- [ ] **F-0 Decolagem:** registrar a altura em que o firmware encerra o launch profile (esperado
  ~1,0 m sobre o totem) e o tempo até o governor assentar em 0,8 m relativos
  (`[PLATFORM departure_settle err=... elapsed=...]`). Confirmar que não passa de `venue_ceiling_agl_m`.
- [ ] **F-1 Linha de base:** pairar 60 s a 0,8 m sobre o totem; `z_rel` e `fw_alt` com divergência
  estável < 0,10 m; registrar o ruído de ambos.
- [ ] **F-2 Saída do totem (critério de aceite):** cruzeiro lateral de 2 m para a esquerda (`vy > 0`, `vx = 0`,
  sem guinada) e retorno parando antes do totem. 3 repetições com a guarda **desligada** (`edge_ahead_m = null`), 3 com a guarda ligada,
  3 com `guard_vertical_shaper = false`. Registrar a perda máxima transiente de altura sobre o piso,
  o tempo até +/-0,10 m, o `vz` mínimo, o `s` em que o degrau de `fw_alt` ocorre contra o `s_star`
  previsto (valida `sonar_half_angle_deg`) e a velocidade horizontal real na borda. Repetir sobre
  uma bancada. **Meta: perda transiente <= 0,25 m com a guarda ligada.** Se o firmware afundar mais
  rápido que a autoridade de subida da guarda (0,2 normalizado), a meta não é atingível por software
  e o relatório deve dizê-lo.
- [ ] **F-3 Reentrada:** retorno lateral para a direita (`vy < 0`) cruzando a borda com o RTL atual; registrar o pico de `z_rel` (falso
  climb), se `[PLATFORM state=ENTERING ...]` ocorreu antes do degrau, o erro lateral acumulado e o
  pico contra o teto efetivo. Dimensiona `ceiling_allowance_m` e `rise_feedforward_mps`.
  Em `ENTERING`, um estouro de teto **não** pousa a aeronave por até `reentry_breach_grace_sec` (3 s):
  a guarda comanda a descida máxima (`[PLATFORM reentry_descent ...]`). Passado esse tempo, ou fora de
  `ENTERING` ainda acima do teto, o pouso de emergência volta. Manter a mão no ABORTAR durante a reentrada.
- [ ] **F-4 Feedforwards:** a partir de F-2/F-3, ajustar `sag_feedforward_mps` e
  `rise_feedforward_mps`; repetir 3 vezes; manter só se reduzirem a perda.

### 4.3 Decisões após os ensaios
- Divergência `z_rel` x `fw_alt` estável e sem degrau de `vz`: manter `altitude_estimator = "legacy"`.
- Degrau ou spike de `vz` na borda: promover `"vz_gated"`.
- `fw_alt` imune ao sonar e `z_rel` com deriva: habilitar `altitude_anchor_enabled`.

### 4.3.1 Identificação do degrau do totem (F-ID)

Pré-requisito: o log persistente de missão da estação, em
`<userData>/logs/mission_<AAAAMMDD_HHMMSS>.log` (os 30 mais recentes ficam guardados). Cada
ensaio abaixo é um voo; anotar o nome do arquivo de log ao lado da caixa.

- [ ] **F-ID-1 Identificação sem voo (aeronave na mão, motores desligados).** Levar o Bebop
  ligado do topo do totem até fora dele, à altura constante, e voltar. Registrar `[ALT_PROBE]` e
  `/bebop/odom`.
  - Se o `AltitudeChanged` (`fw_alt`) saltar cerca de 1,0 m: `calibration.firmware_altitude_source = "sonar_fused"`.
  - Se o `speedZ` mostrar um pico na borda: o firmware está em M2.
  - [A CONFIRMAR] se o firmware publica altitude com os motores desligados. Se não publicar, passar ao F-ID-2.
- [ ] **F-ID-2 Pairado baixo e travessia lenta.** Travessia a 0,05 m/s com o operador pronto no
  Ctrl+C (aborta com o foco em qualquer controle da cabine). Voar uma vez com
  `calibration.altitude_estimator = "legacy"` e outra com `"terrain_kf"`, e comparar os dois logs
  persistidos (`[ALT_PROBE z_rel fw_alt]`, `[VERT_KF nis=... accepted=... T=...]`, `[PLATFORM ...]`).
  O desfecho classifica o firmware:
  - **M1:** a aeronave afunda ~1 m ao sair e sobe ~1 m ao reentrar, e o `speedZ` mostra o movimento.
  - **M2:** o `speedZ` mostra um pico sem movimento real; com `legacy` o `z_rel` fica com degrau permanente.
  - **M3:** nada acontece.
- [ ] **F-0 (revisto).** Medir a altura real e o overshoot do fim da decolagem nativa sobre o
  totem e sobre o solo (`[TIMING] t_takeoff_confirmed alt=`, `Liftoff transient`). Atualizar
  `NATIVE_HOVER_HEIGHT_M` (`mission_mode.py`) somente se divergir mais de 0,10 m, mantendo a
  paridade com `parameter_conflicts.FIRMWARE_HOVER_HEIGHT_M` e o simulador (há teste).

**Regra de decisão:**
- M2 confirmado: `altitude_estimator = "terrain_kf"` vira o padrão.
- M1 confirmado: `"terrain_kf"` mais a calibração dos feedforwards da guarda (`sag_feedforward_mps`,
  `rise_feedforward_mps`, F-4).
- `AltitudeChanged` imune ao sonar: `firmware_altitude_source = "baro"`, com âncora.

### 4.4 Limite da bancada `--no-fly`
Ensaiar o padrão lateral antes do voo com `mission.py --no-fly` (nunca armar motores nesse ensaio): conferir
no log a sequência `[STEP 2: Lateral Left Search]` com `vy > 0` e `vx = 0`, o freeze do Stage 3, o
enquadramento por gimbal no Stage 4 e a busca do marcador com `vy < 0` no Stage 5.
O `KinematicSimulator` não tem sonar, solo nem totem: o `PlatformMonitor` roda sobre a odometria
sintética, mas não há degrau. A bancada valida a máquina de estados e a ausência de regressão, não a
reação do firmware nem a manutenção de altitude na borda.

### 4.5 Expectativas no Eixo Lateral
- [ ] **Stage 2:** deslocamento somente para a esquerda a `kinematics.forward_cruise_velocity`; `vx` e `vyaw`
  nulos; câmera em `gimbal.search_tilt_deg`. Qualquer avanço ou guinada visível é falha.
- [ ] **Stage 3:** apenas correções laterais (`vy`); sem avanço e sem movimento do gimbal; a aeronave congela
  (`vx = vy = 0`) ao centralizar lateralmente com velocidade abaixo de `inspection.settle_max_speed_mps`.
- [ ] **Stage 4:** aeronave em pairado absoluto; somente o gimbal se move (lei de apontamento limitada por
  `max_slew_limit_deg`, entre `nadir_tilt_deg` e `search_tilt_deg`); captura e JSON pericial marcados
  `degraded`/`settled = false` se o alvo não for enquadrado.
- [ ] **Stage 5:** busca do marcador voando para a **direita** (`vy < 0`, `|vy| = |rtl.reverse_cruise_velocity|`,
  regra E05); se o marcador for ultrapassado, o reenquadramento é um creep para a esquerda.

---

## 5. Feira Offline (Voz Pré-Sintetizada, Sem Rede na Estação)

Na feira a estação não tem internet: o Wi-Fi do Bebop não tem rota. A voz oficial (Gemini Live, voz Orbit) só toca para frases cujo PCM já está no cache em disco (`~/.cache/bmg/speech`, ou `BMG_SPEECH_CACHE_DIR`). O cache **não está no Git**; é gerado com internet e transportado em `tar.gz`. Referência das frases e da operação do cache: `bebop_mission_control/docs/TABELA_DE_FALAS_COPILOTO.md`, seção "Operação do cache".

### 5.1 Pré-síntese (máquina com internet, `SPEECH_API_KEY` exportada)
Na raiz do repositório, após `source ~/ros2_ws/bin/nectar-activate`:
- [ ] `python3 bebop_mission_control/streamer/precache_speech.py --dry-run`: contagem por categoria, sem rede e sem gravar. Na máquina de desenvolvimento hoje faltam 482 de 739 frases.
- [ ] `python3 bebop_mission_control/streamer/precache_speech.py`: sintetiza o que falta (~2 s por frase; 482 frases ~ 16 min). Entradas curtas (< 0,25 s por palavra, turno cortado) são sobrescritas. Saída 2 sem chave ou sem rede.
- [ ] `python3 bebop_mission_control/streamer/precache_speech.py --check`: **saída 0 obrigatória**. Saída 1 lista as faltantes e as curtas; repetir o passo anterior.
- [ ] `python3 bebop_mission_control/streamer/precache_speech.py --pack ~/bmg-speech-feira.tar.gz`: recusa (saída 1) se houver entrada ausente ou curta.
- Outro conjunto de parâmetros: `--parameters FILE` (lista JSON de `mission.parameters`) ou `BMG_MISSION_CONFIG` apontando para o `mission_config.json` desejado. A pré-síntese deve usar os mesmos parâmetros que serão voados.

### 5.2 Instalação na máquina da feira
- [ ] Copiar `~/bmg-speech-feira.tar.gz` para a máquina da feira.
- [ ] `python3 bebop_mission_control/streamer/precache_speech.py --unpack ~/bmg-speech-feira.tar.gz`: recusa (saída 2) pacote de outra voz ou modelo.
- [ ] `python3 bebop_mission_control/streamer/precache_speech.py --check` com os **mesmos** `SPEECH_VOICE` (Orbit) e `SPEECH_MODEL` da síntese: saída 0. A chave do cache contém frase, modo de prompt, voz e modelo; qualquer diferença invalida o cache inteiro.
- [ ] `--prune` apenas lista entradas fora do manifesto atual (qualquer voz); remove só com `--yes` e recusa abaixo de metade de cobertura. Não usar na feira.

### 5.3 Regra: parâmetros falados
- [ ] **Não alterar altitude, velocidade nem limiares falados na feira sem repetir a pré-síntese (5.1 e 5.2).** Parâmetro alterado muda o texto da frase, que vira cache miss: a linha é descartada em milissegundos com `[SPEECH_MISS]` no log, sem síntese e sem travar a fila; os cards seguem na cadência própria e a voz fica ausente naquela frase.

### 5.4 Opcional: evitar a sonda de alcançabilidade
- [ ] Exportar `BMG_SPEECH_OFFLINE=1` no ambiente da estação: o daemon não sonda a rede (a primeira sonda custa até 1 s). `"0"` nunca assume offline; ausente ou `auto` sonda.
- [ ] Medir na estação conectada ao Wi-Fi do Bebop: `time getent hosts generativelanguage.googleapis.com` (falha esperada, anotar o tempo).

### 5.5 Ensaio offline em bancada (`--no-fly`)
Desligar a rede com internet (`nmcli networking off` ou desconectar cabo/Wi-Fi); conectar apenas ao Wi-Fi do Bebop, se aplicável. No BMG marcar "bancada" (o `main.cjs` passa `--no-fly`; nunca armar motores). Critérios, lidos no log da missão (`<userData>/logs/mission_<AAAAMMDD_HHMMSS>.log`) e na tela:
- [ ] Toda linha `[SPEECH]` com `source=cache`; nenhuma com `source=synth`, `stream` ou `prefetch`.
- [ ] Zero `[SPEECH_MISS]` com os parâmetros pré-sintetizados.
- [ ] Contagem da palavra banida igual a 0 no log e na tela: `grep -icE 'sini[s]tro' <log>`.
- [ ] Laudo abre com **Veículo** (com guincho) quando não há vítima, e com **Vítima** primeiro quando detectada. Forçar os dois cenários com as imagens de `test/fixtures` quando existirem; sem forma de forçar `victim_detected`, cobrir pelo teste de componente e registrar a lacuna.
- [ ] Sem o botão "Visualizar Laudo Pericial".
- [ ] Rodapé do mapa com valores exatos: BASE FIXA `-23.648891, -46.718712`; Cidade `São Paulo`; Localização `Transamerica Expo Center`. Grade visível desde o primeiro quadro, sem tiles de rede.
- [ ] Nenhuma tentativa de rede nos logs do daemon (sem `warm-up failed`).
- [ ] **Comportamento de miss:** repetir com um parâmetro alterado (altitude ou velocidade) e observar `[SPEECH_MISS]` na frase afetada, linha pulada em milissegundos e cards na cadência própria. Restaurar o parâmetro e confirmar zero miss.

---

## 6. Mapa Offline (Tiles de Satélite Locais, Sem Rede na Estação)

O mapa tático lê tiles de `bebop_mission_control/public/tiles/{z}/{x}/{y}.jpg` (rota `tileRoute.cjs`; tile ausente responde 404 sem corpo). Referência: `docs/PLANO_MAPA_OFFLINE_EXPOCENTER.md`, Task 6. Pipeline: `scripts/offline_tiles.py` (subcomandos `plan`, `raster`, `xyz`, `verify`; saída 0 sucesso, 1 cobertura incompleta, 2 erro de uso ou de rede).

### 6.1 Estado
- **A imagem ainda NÃO foi obtida.** Sem tiles em `public/tiles`, o mapa cai na grade local.
- Origem escolhida: MapTiler `satellite-v2` (fonte usada antes), **condicionada a autorização escrita do MapTiler** para exportar e cachear tiles para uso offline. Sem a autorização, não baixar (termos do MapTiler Cloud).
- Alternativas: ortofoto GeoSampa (comando `raster`; exige `sudo apt install gdal-bin python3-gdal`) ou ortomosaico próprio do Bebop.
- A chave antiga foi removida do código e **precisa ser rotacionada no painel do MapTiler**. Usar chave nova somente via `BMG_TILE_KEY`; nunca versionar.

### 6.2 Cobertura
- Tiles z17 a z20 (padrão `--zooms 17-20`). A exibição vai de 17 a 22; z21 e z22 são sobrezoom do z20.
- `--viewport-px` padrão 1280 (256 tiles). Para painel até 1920 px, usar `--viewport-px 1920` (700 tiles para z15-20 é só referência; para z17-20, rodar `plan` e registrar o número).
- Limite: painel maior que `viewportPx` em qualquer eixo fica com bordas sem imagem.
- [ ] `python3 scripts/offline_tiles.py plan [--viewport-px 1920]`: registrar a contagem por nível.

### 6.3 Obtenção (máquina com internet, somente com a autorização de 6.1)
- [ ] `export BMG_TILE_KEY=<chave nova>` (somente no ambiente; o `xyz` recusa chave fora desta variável).
- [ ] Baixar (template https obrigatório, com `{z}/{x}/{y}` e `{key}`):
  `python3 scripts/offline_tiles.py xyz --url-template 'https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key={key}' --source 'MapTiler satellite-v2' --license '<autorização escrita, data>' --attribution '© MapTiler © OpenStreetMap contributors' --rate 4 [--viewport-px 1920]`
- [ ] `python3 scripts/offline_tiles.py verify`: **saída 0 obrigatória**.
- [ ] `du -sh bebop_mission_control/public/tiles`: registrar o tamanho.
- [ ] Abrir 3 tiles (um em z17, um em z19, um em z20) e confirmar que mostram o Expo Center, não placeholder.
- Os tiles são versionados no Git (`public/tiles`, decisão D-B).
- Via raster: `python3 scripts/offline_tiles.py raster --src <arquivo> [--srs <EPSG>] --source ... --license ... --attribution ...`.

### 6.4 Verificação sem rede (ação do operador)
Desligar a rota para a internet (`nmcli networking off` ou desconectar cabo/Wi-Fi). Executar `npm run dev` em `bebop_mission_control/` e repetir no app Electron empacotado.
- [ ] Mapa de satélite visível desde o primeiro quadro.
- [ ] Zoom por botões, roda e pinch de 17 a 22, sem quadrados pretos nem costuras.
- [ ] Recentrar funciona.
- [ ] Rodapé com valores exatos: BASE FIXA `-23.648891, -46.718712`; Cidade `São Paulo`; Localização `Transamerica Expo Center`.
- [ ] Atribuição `© MapTiler © OpenStreetMap contributors` visível.
- [ ] Painel estreito e painel largo sem borda vazia (até o `--viewport-px` usado).
- [ ] Nenhuma requisição fora de `localhost` (aba Network).
- [ ] Nenhum 404 de tile nas faixas permitidas (z17-z20 na cobertura).
- [ ] Console sem erros.
- [ ] **Cenário de falha:** renomear temporariamente `dist/tiles`; confirmar que a grade local aparece sem tela preta (o fallback dispara quando todos os tiles da tela falham, ou após 2 s sem nenhum carregado, e é terminal até remontar o componente); restaurar o diretório e recarregar.
