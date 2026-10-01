# Relatório de Verificação Ponta a Ponta — MVP-Bebop

> Data: 2026-09-30. HEAD auditado: `00e2292` (main, sincronizado com origin, árvore limpa).
> Método: auditoria estática do código, testes automatizados e verificação ao vivo com o Bebop 2
> conectado (SSID `Bebop2-Tech4Humans`, estação em `192.168.42.52`, RTT de 4,7–5,3 ms).
> Nenhum comando de voo foi enviado. Companheiro deste documento:
> `docs/PROMPT_IMPLEMENTACAO_E2E_VIDEO_FINALIZAR_COPILOTO.md`.

## 0. Condição e método da verificação ao vivo

- O driver real foi lançado com `make driver-bebop` (conexão ARSDK em 0,37 s, vídeo na primeira tentativa).
- Todas as medições no domínio do driver (`ROS_DOMAIN_ID=14`) foram feitas com assinantes somente leitura.
- O ensaio da missão rodou com `mission.py --no-fly` num **domínio isolado** (`ROS_DOMAIN_ID=77`),
  alimentado por um relay unidirecional que encaminhava só sensores (imagem, odometria, estados) de 14
  para 77.
  - Resultado: nenhum comando publicado pela missão tinha caminho físico até o driver.
  - Conferido durante a execução: `ros2 topic info` no domínio 14 mostrou **0 publishers** em
    `/bebop/{takeoff,cmd_vel,land,flattrim,move_camera,photo}`.
- Tudo o que a missão em bancada emitiu, o relay capturou e descartou: `move_camera` (gimbal), `flattrim`
  e `photo`. **Zero** `takeoff`, **zero** `cmd_vel` e **zero** `land`. Isso confirma ao vivo que o proxy
  no-fly intercepta toda a atuação.
- Único comando enviado de propósito ao drone real: `/bebop/flattrim`, uma vez. É calibração de solo e
  não aciona motor. O `flying_state` permaneceu 0 (landed) durante toda a sessão.
- O `mission_config.json` foi salvo antes dos ensaios e restaurado depois. O conteúdo não mudou. Os
  ensaios geraram uma evidência de bancada: `accident_*_20260930_122110.*`, coberta pelo `.gitignore`.

Testes automatizados (linha de base):

| Suite | Resultado |
|---|---|
| `python3 -m pytest test/` | 869 passed |
| `npx vitest run` | 219 passed |
| `npx tsc --noEmit` | 0 erros |

`ros2_bebop_driver` tem 10 arquivos modificados sem commit sobre o upstream `d783e92`. Toda mudança no
driver deve partir dessa working tree.

---

## 1. Relatório de saúde ROS 2 (medido ao vivo)

### Nós

| Nó | Status | Evidência |
|---|---|---|
| `/bebop/bebop_driver` | OK | Conectado; 9 subscribers e 8 publishers, conforme o contrato. Callback group único. Uma exceção C++ num callback mata o nó (código). |
| `nectar_bebop_mission_*`, `nectar_image_handler_*`, `bebop_mission_telemetry` | OK | Subiram e encerraram limpos nos 4 ensaios (1 completo e 3 do Stage 2). |
| mjpeg_server | WARNING | Funcional; em repouso entrega 23,8–27,2 FPS. Sob carga de YOLO cai para 4,1 FPS (seção 3). |
| command_bridge | WARNING | Pronta em 0,94 s. Um land enviado antes do `ready` fica em buffer e sai com 1,3 s de atraso. |
| **Daemon `ros2`** | **FALHA** | O daemon obsoleto, iniciado antes da troca de rede, devolvia `ros2 node list` **vazio** com o driver vivo. O `connect()` da missão depende de `ros2 node list` e faria `sys.exit(1)`. Resolvido com `ros2 daemon stop/start`. |

### Tópicos (medidos por 30 s, CPU ociosa)

| Tópico | Taxa | Conteúdo real | Status |
|---|---|---|---|
| `/bebop/camera/image_raw` | **27,2 Hz**; dt mediano 30 ms, p95 63 ms, máx 172 ms | **1280x720 bgr8, 2.764.800 B por quadro** | WARNING: 720p cru |
| `/bebop/camera/camera_info` | 29,9 Hz | **856x480**, K=[537,3 0 427,3; 0 527,0 240,2] | **FALHA**: não corresponde à imagem de 1280x720 |
| `/bebop/odom` | 15,1 Hz | z=0, v=0 (em solo), quaternion válido | OK |
| `/bebop/states/battery` | 2,0 Hz | 81%, `present=true`, voltage NaN | OK |
| `/bebop/states/flying_state` | 2,0 Hz | 0 (landed) | OK |
| `/bebop/states/gps` | 2,0 Hz | 0/0, status −1 (sem fix; ambiente interno) | OK (sentinela tratada) |
| `/bebop/states/wifi_rssi` | 2,0 Hz | −51 dBm | OK |
| `/bebop/states/altitude` | 2,0 Hz | 0,0 | OK |
| Stamps de todos os tópicos | idade ~0,5–2,5 ms | Carimbados na **publicação**, não na origem ARSDK | **FALHA**: impossível detectar valor velho |
| `UdpRcvbufErrors` | 7129 antes e depois de todos os ensaios | Sem descarte de socket | OK. O contador é histórico, não deste pipeline. |
| `/dev/shm/fastrtps_*` | 83 segmentos, 61 após o encerramento | Segmentos órfãos de processos mortos | WARNING: vazamento de SHM |
| `/bebop/autoflight/navigate_home` | — | O driver assina `Bool`; o SDK publica `Empty` | FALHA de contrato (sem uso hoje) |
| `/bebop/camera/camera_info`, `/tf` | — | Nenhum assinante | WARNING (órfãos) |

### Serviços e actions

`ros2 service list` mostra apenas os serviços de parâmetro do driver e `set_camera_info`. `ros2 action
list` está vazio. Não existe serviço de abort, calibração ou configuração de vídeo: todo o controle é
por tópico, e o abort chega à missão por sinal POSIX.

---

## 2. Diagnóstico do fluxo da missão (ensaio ao vivo em bancada, câmera real)

| Transição | Tempo medido | Resultado |
|---|---|---|
| spawn → `[STEP 1` | **11,5 s** | OK. Import, YOLO e warmup (2,0 s). |
| Stage 1 (flat trim, countdown de 10 s, takeoff simulado, subida de 1,00 para 1,80 m) | 16,7 s | OK. `mission.takeoff {altitude_m:1.8}` emitido no instante do takeoff simulado, sem confirmação. Warning: "Liftoff transient did not settle within the 2.0 s ceiling". |
| Stage 2 (varredura) | 31,0 s | Timeout de 30 s, sem alvo |
| Stage 3 | 0,0 s | Pulado sem alvo. `[STEP 3` e `[STEP 4` saíram no **mesmo milissegundo**, cenário que quebra o parser de um match por chunk em `main.cjs:2241`. |
| Stage 4 (captura e hover) | 8,3 s | Captura em modo degradado (`settled:false`), sem alvo |
| Stage 5 (busca reversa de 30 s, pouso) | 35,5 s | "Touchdown confirmed: altitude 0.07 m" (simulador) |
| Cleanup | 0,2 s | OK |
| **Total** | **~103 s**, sem exceções nem travamentos | Fluxo completo OK em bancada |

Defeitos de transição (código, confirmados):

1. O parser `[STEP N:` não tem buffer de linha e casa só um marcador por chunk.
2. `goto_stage 1` só é recusado com altitude relativa acima de 0,25 m: no solo, depois do touchdown, é
   aceito e re-decola.
3. O aborto sai com código 0 (`signals.py:98`). O touchdown sem confirmação também dá SUCCESS e código 0.
4. `BebopDrone.takeoff` sempre retorna True após `sleep(3)` (`nectar/control/bebop/drone.py:146-163`).
5. A contagem da UI começa no spawn, enquanto a real começa depois de medidos 11,5 s. O "3 s para
   decolar" da estação sai cerca de 14 s antes do takeoff.

---

## 3. Diagnóstico de latência e gargalos (medido)

| Ponto crítico | Medido | Causa-raiz |
|---|---|---|
| spawn → primeiro comando (`move_camera`) | 11,5 s | Imports (~5,1 s, dos quais `google.genai` ~2,1 s sem uso sob a GCS), YOLO e warmup (2,0 s) |
| spawn → takeoff | ~22 s | Os itens acima mais o countdown de 10 s |
| Comando → ACK do drone | **Não observável** | Flat trim publicado e entregue, mas o driver não loga nem publica resposta. Não despacha FlatTrimChanged, LandingStateChanged nem PictureStateChangedV2. Link: RTT de 5 ms. |
| **Abortar → land, via command_bridge residente** | **0,7–2,9 ms** (5 amostras) | Instantâneo |
| Abortar → land, com a ponte recém-reiniciada (antes do `ready`) | 1.301 ms | O stdin fica em buffer até o nó subir; `sendCommand` ignora o `ready` |
| **Abortar → land, via fallback `ros2 topic pub --once -w 0`** | ~1.850 ms e **1 de 3 PERDIDO** | O processo frio publica e sai antes do pareamento |
| Fallback com `-w 1` | ~1.890 ms e **1 de 3 PERDIDO** | Espera um assinante, não todos. `--once` sem repetição não é confiável. |
| Clique duplo em Abortar/Finalizar | corta o pouso (código) | O segundo SIGINT executa `os._exit(130)` |
| **Vídeo ao vivo por estágio** | pré-missão 15,8 · **S1 5,2 · S2 2,4 · S4 22,4 · S5 4,0** · pós 25,3 FPS (mediana) | Seção 6 |
| **Fala: sessão fria** | **~11 s** até o início (14,9 s até o fim de uma frase de 8 palavras) | Connect da sessão Gemini Live mais turno inteiro antes de tocar |
| Fala: sessão quente | ~2 s até o início | Síntese do turno completo |
| **Alerta URGENT após cancel** | **~25 s** até o início (28,1 s até o fim) | O cancel não descarta a linha em síntese: ela tocou inteira (12 s após o cancel) antes do alerta |

CPU durante o Stage 2 (i5-7200U, 2C/4T, carga média de 3,7):

| Processo | CPU |
|---|---|
| mission.py (YOLO `.pt` a 640, ~7 inferências/s) | **187%** |
| driver | 29% |
| gnome-shell | 19% |
| relay de teste | 11% (não existe em produção) |
| mjpeg_server | 9% (ocioso, faminto de quadros) |

---

## 4. Persistência de parâmetros e telemetria

### 4.1 Parâmetros

- Ao vivo: a missão aplicou os valores do arquivo (altitude 1,80 m, countdown 10 s, `land_pct` 20%) e os
  regravou sem alteração.
- Uma segunda missão na mesma sessão usa os valores mais recentes do React.
- Há 13 pontos em que um default pode sobrescrever o valor do usuário sem aviso. Os críticos:
  1. **Fallbacks hardcoded em `App.tsx:246-257`:** com campo não finito vão `height 1`, `velocity 0.2`,
     etc., como flags da CLI, e **vencem** o `--params-json`.
  2. **Tela diferente do voo:** a tela mostra o default TS (1,8 m) enquanto o voo usa `--height 1`.
  3. **Defaults divergentes:** altitude TS 1,8 vs Python 1,00; estabilização 2 s vs 4 s.
  4. **JSON corrompido** devolve os defaults com `success:true`.
  5. **Lançamento permitido com parâmetros em `loading`.**
  6. **Falha de `params.save()`** ignorada no lançamento.
  7. **Preset** substitui o documento inteiro, incluindo PID e `no_fly`.
  8. `countdown_sec` 0 vira 10. O slider de bateria reescreve `land_pct` no disco.
  9. `mission_config.json` está no `.gitignore` e não viaja no handover. Hoje tem `"no_fly": true`.

### 4.2 Telemetria

- Valores reais lidos do drone: bateria 81%, RSSI −51 dBm, `flying_state` 0, GPS sem fix, odometria em
  repouso.
- Falhas estruturais:
  - todos os stamps são carimbados na publicação e o driver republica o último valor a 2 Hz. Com o link
    ARSDK caído e o driver vivo, a UI mostra valores congelados como frescos;
  - `camera_info` a 856x480 contra imagem a 1280x720: a pose do ArUco no RTL usa o ponto principal errado;
  - roll/pitch não chegam à UI; a altitude da UI não subtrai o z0 da missão;
  - `StatusBar` mostra "em solo" quando o estado é desconhecido;
  - três conjuntos "airborne" divergentes.

### 4.3 Subsistemas

| Subsistema | Status ao vivo |
|---|---|
| YOLO | Carregou em 0,06 s, warmup de 2,0 s, 191 ciclos no ensaio completo. Sem erro. Ocupa 187% de CPU. |
| PID/controladores | Em bancada consomem o `KinematicSimulator` (subida 1,00 → 1,80 m, hold, pouso a 0,07 m). Em voo, `/bebop/odom` real a 15 Hz (confirmado no tópico). O chaveamento está em `mission.py:417-423, 516-522`. |
| Flat trim | Entregue ao driver; **sem ACK nem log**. A missão espera 2 s fixos. |
| Calibração magnética | Não implementada em nenhuma camada |
| Câmera | Ativa, 27 Hz, consumida pela missão e pelo mjpeg. **720p, fora do padrão da calibração.** |

### 4.4 Hardware da estação

| Recurso | Estado | Evidência |
|---|---|---|
| CPU | i5-7200U, 2 núcleos e 4 threads | Saturada pelo YOLO |
| iGPU Intel HD 620 (Gen9) | Utilizável para inferência com o OpenVINO GPU plugin, mas **`intel-opencl-icd` não está instalado** (candidato 23.43 no apt). O OpenVINO 2026.4 só enxerga `['CPU']`. | `ov.Core().available_devices` |
| dGPU NVIDIA MX110 (GM108, Maxwell, 2 GB) | **Inutilizada**. O kernel registra: "supported through the NVIDIA 580.xx Legacy drivers ... The 610.57.04 NVIDIA driver will ignore this GPU". torch é `2.9.1+cpu`. | `journalctl -k` |
| VAAPI | Driver iHD presente, `/dev/dri/renderD128` | Decode H.264 em software custa só 2,5–4 ms por quadro |

---

## 5. Auditoria do copiloto de voz

Decisões de produto registradas em 2026-09-30:

- **(D1)** O laudo pericial é intencionalmente hardcoded e sorteado dentre os achados configurados, sem
  análise de imagem.
- **(D2)** Todo valor numérico falado que corresponda a um parâmetro de missão (altura, velocidades,
  raio, tempos, confiança) vem do **parâmetro configurado da missão corrente**, não da telemetria em
  tempo real.

A condição de disparo de cada fala continua precisando refletir o evento real.

| # | Fala | Trigger | Fonte do dado | Veredito sob D1/D2 |
|---|---|---|---|---|
| A1 | `mission.start` | Spawn | constante | OK em voo; FALSO leve na bancada (emitido antes do spawn) |
| A2 | `mission.countdown_3` ("Checklist completo...") | Timer a partir do spawn | host | **FALSO**: adiantado ~14 s (medido 11,5 s até o STEP 1 e mais 10 s de countdown), e afirma uma validação que não ocorreu |
| A3 | `mission.takeoff` "{altitude}" | `takeoff()` retorna True, sempre | `target_altitude_m` = parâmetro (1,8) | Dado **conforme D2**. Condição **FALSA**: "Aeronave no ar" sai sem confirmação de voo. "Teto operacional {altitude}" é outra grandeza (`altitude_ceiling`). |
| A4 | `mission.scan_start` | Entrada no Stage 2 | constante | OK. O payload `cruise_mps` (parâmetro) está disponível e não é falado. |
| A5 | `mission.target_found` "{location}" | Histerese confirmada | Projeção geométrica da detecção | Sem parâmetro correspondente; mantém a estimativa geométrica (ver D2) |
| A6 | `mission.approaching` | Stage 3 com alvo | constante | "Guiagem visual" é FALSO com `ibvs_enabled=false` |
| A7 | `mission.capture_done` | `record.captured` | payload | FALSO sem alvo (disparou no ensaio com `settled:false` e sem alvo) |
| A8 | `mission.rtl_start` | Entrada no Stage 5 | constante | "Inspeção concluída" / "Deixando o local" são FALSO em salto por bateria ou sem alvo |
| A9 | `mission.landing` | Início do `_touchdown` | constante | "Na base" é FALSO quando pousa no local |
| A10 | "Pouso seguro concluído com sucesso na base." | `exitCode === 0` | exit do processo | **FALSO**: aborto sai com 0, touchdown sem confirmação sai com 0 |
| A11–A13 | Laudo: intro, 4 achados sorteados, outro | Captura com processo encerrado | `Math.random` sobre os pools | **CONFORME D1.** Pendências: "Aeronave em solo" em `intro` só deve sair com o pouso confirmado; cada achado segura a fila por ≥1,8 s. |
| A15/A16/A19/A20 | "Falha ... Executando pouso seguro imediatamente." | Falhas no solo | detalhe descartado | **FALSO**: o drone está no chão. A20 duplica os anteriores. |
| A18 | "Alerta de voo: {reason}" | Failsafe | `reason` em inglês ou `str(exc)` | REAL; idioma errado |
| A21–A25 | Abort e bateria crítica | Eventos reais | bateria real (limiar = parâmetro) | REAL. A22–A25 contornam a fila. A25 é falado antes do ACK do salto. |
| — | `mission.battery_warning` | Backend | — | Nunca falado (chave ausente em `PHRASE_POOLS`) |

Sobreposição e latência (medido): o cancel não interrompe a síntese em curso, e a linha cancelada tocou
inteira. A sessão fria leva ~11 s até começar a falar, a quente ~2 s, e um alerta URGENT após cancel ~25
s. Há 4 caminhos que contornam a `NarrationQueue`. O terminal embutido do BMG abre uma segunda voz.

---

## 6. Pipeline de vídeo: diagnóstico e plano

### 6.1 Causa-raiz de 1–3 FPS, reproduzida ao vivo

| Condição | FPS no cliente MJPEG |
|---|---|
| Driver, 1 assinante, CPU ociosa | 27,2 |
| mjpeg direto no driver, sem missão | 23,8 |
| mjpeg direto no driver, **com YOLO rodando** (Stage 2) | **4,1** |
| Produção por estágio (ensaio, via relay) | S1 5,2 · S2 2,4 · S5 4,0 |

1. **CPU saturada pelo YOLO `.pt` a 640, sem limite de período:** 187% de CPU, 7 inferências/s. Isso
   derruba o próprio caminho driver → assinantes de 27 para 11 Hz. É a causa dominante.
2. **720p cru pelo DDS:** 2,76 MB por quadro para 2 assinantes. Não há descarte de socket
   (`UdpRcvbufErrors` estável); o custo é CPU de cópia e desserialização.
3. **Bloqueio por quadro anotado** (`DETECTION_PRIORITY_SEC=1.5`) nos Stages 1 e 5: fonte `detections`
   nas amostras, limitado a ~5 FPS.
4. **O stream está em 720p fora do código.** O driver nunca configura resolução, framerate ou modo.
   `camera_info` está em 856x480.

### 6.2 Plano comparativo (CPU + GPU liberados)

| Técnica | Ganho | Custo | Recomendação |
|---|---|---|---|
| Driver: `REC1080_STREAM480` + 30 FPS + `LOW_LATENCY` | 2,25x menos pixels; calibração volta a valer | Baixo | **Fazer** |
| YOLO na **iGPU HD 620 via OpenVINO GPU** (instalar `intel-opencl-icd`) | Tira a inferência dos núcleos da CPU; a CPU fica para decode, DDS e MJPEG | Baixo (pacote apt + parâmetro de device) | **Fazer primeiro**, com benchmark e fallback automático para CPU |
| YOLO na **MX110 via CUDA** (driver legado `nvidia-headless-580` + torch cu126 com sm_50) | GPU dedicada de 2 GB, ~2x o FP32 da HD 620. Não disputa banda de memória nem o display. | Médio: troca do driver 610 por 580 com pin contra o repositório CUDA, e reboot. O display fica na Intel (stack headless). | **Alvo preferencial (decisão do usuário, D3)**, confirmado por benchmark contra a iGPU, com fallback automático |
| OpenVINO CPU a 480 + período mínimo de 100 ms no worker | 75 contra 126 ms por inferência; limita a 10 Hz | Baixo | **Fazer** (fallback do item acima) |
| `image_transport` compressed para o GCS | ~43 KiB por quadro, contra 1,2 MB | Baixo | **Fazer** |
| Overlay JSON no Stage 1 e no RTL (fim do bloqueio anotado) | S1 e S5 de ~5 para 30 FPS | Médio | **Fazer** |
| Perfil Fast DDS e limpeza de SHM órfão | Robustez | Baixo | **Fazer** |
| Decode H.264 via VAAPI | 2,5 ms → ~1 ms por quadro | Médio | Opcional: ganho pequeno |
| GStreamer | Nenhum sobre o libavcodec `LOW_DELAY` | Médio | Não fazer |

**Divisão de carga recomendada:**

- MX110: YOLO, se o benchmark confirmar;
- iGPU: display e compositor; YOLO como segunda opção;
- CPU: decode H.264 (barato), DDS, MJPEG, ArUco; YOLO só como último fallback.

**Meta:** 30 FPS no GCS em todos os estágios, com 856x480.

### 6.3 Evidência pericial em resolução nativa

Hoje a evidência é o quadro do stream (1280x720 no ensaio). A foto de 14 MP usa o comando deprecado
`sendMediaRecordPicture`, sem ACK nem download. O plano: `RecordPictureV2`, `PictureFormatSelection`,
eventos de ACK, disparo antes do YOLO e download por FTP depois do pouso.

---

## 7. Especificação da trava do botão "Finalizar Missão"

Estado atual (**FALHA**, código):
- `canFinish = missionState !== 'idle' || ...` (`CockpitScreen.tsx:110`), o que o deixa clicável em voo;
- sem guard contra clique duplo; um segundo SIGINT executa `os._exit(130)`;
- Abortar fica desabilitado se o processo morrer com o drone no ar.

```text
                    launch ok
   +-----------+  ------------->  +-----------+   abort click   +-----------+
   | NO_MISSION|                  | IN_FLIGHT | --------------> | ABORTING  |
   | disabled  |                  | disabled  |                 | disabled  |
   +-----------+                  +-----------+                 +-----------+
        ^                               |  mission-exit               | mission-exit
        |                               v                             v
        |                         +--------------------------------------+
        |                         | AWAIT_GROUND (disabled)               |
        |                         | real: flying_state==0 (landed)        |
        |                         |   continuous >= 2 s, nav_fresh,       |
        |                         |   source=aircraft, ARSDK age < 1.5 s  |
        |                         | bench: exit + bench_flying_state==0   |
        |                         +--------------------------------------+
        |                            |  grounded             | flying_state null > 10 s
        |                            v                       v
        |                      +-----------+          +------------------+
        |                      | GROUNDED  |          | LINK_LOST        |
        |                      | ENABLED   |          | enabled only via |
        |                      +-----------+          | hold-to-confirm  |
        |                            | click (ref guard)     |
        |                            v                       |
        |                      +-----------+                 |
        +--------------------- | FINISHING | <---------------+
              endMission done  | disabled  |
                               +-----------+

   Any state: flying_state in {1,2,3,4,7,8} -> AWAIT_GROUND (Finalizar disabled, Abortar enabled).
   flying_state 5 (emergency, motors cut) -> GROUNDED with warning.
```

Requisitos:

1. A liberação exige um desfecho determinístico: milestone `mission.touchdown {confirmed, at_base,
   method}` e exit codes distintos (0 = completa, 3 = abortada e pousada, 4 = pouso não confirmado).
2. Guard por `useRef` e single-flight em `bmg:end-mission` / `bmg:abort-mission`. Nunca reenviar SIGINT.
3. Tooltip com o motivo em cada estado bloqueado; `aria-disabled` e cor inativa.
4. Abortar habilitado sempre que `running || isAirborne(flying_state)`.
5. Um único conjunto canônico airborne/grounded nos três arquivos.
