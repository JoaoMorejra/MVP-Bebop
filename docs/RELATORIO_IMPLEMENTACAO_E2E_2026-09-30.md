# Relatório de Implementação E2E — Vídeo, Finalizar e Copiloto

Plano: `docs/PROMPT_IMPLEMENTACAO_E2E_VIDEO_FINALIZAR_COPILOTO.md`.
Diagnóstico de referência ("antes"): `docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md`.
Registro item a item: `docs/IMPLEMENTACAO_PROGRESSO.md`.

Estado: Fases 0 a 8 concluídas. Validação com o Bebop 2 conectado em 2026-09-30/10-01 (Bebop2-Tech4Humans,
missão sempre `--no-fly` em domínio isolado alimentado pelo relay só de sensores; `Publisher count: 0` em
`/bebop/{takeoff,cmd_vel,land}` no domínio do driver).

## 1. Antes e depois

| Métrica | Antes (diagnóstico) | Depois | Condição da medida |
|---|---|---|---|
| spawn → `[STEP 1` | 11,5 s (relatório); 11,8 s (`bench_startup.py`, 3 rodadas) | **6,2 s** na bancada (mediana de 5); ao vivo via relay 9,0–9,7 s com a iGPU escolhida; **6,4 s** com a MX110 preferida (decisão do usuário) | `bench_startup.py`, `BMG_GCS_SESSION=1`, cache de device quente |
| Abortar → land, ponte residente | 0,7–2,9 ms; 1.301 ms com a ponte antes do `ready` | **0,55 ms** p50; primeiro comando 42 ms; 30/30 entregues | `bench_abort_latency.py`, domínio isolado |
| Abortar → land, fallback CLI | ~1.850 ms, **1 de 3 perdido** | 2.064 ms p50, **30/30** entregues; stop→land 5.075 ms p50, 30/30 | idem |
| Fala, sessão fria | ~11 s até o início | **1.243–1.298 ms** | `bench_speech_latency.py`, API real, dispositivo silencioso |
| Fala, sessão quente | ~2 s | **709–856 ms**; do cache em disco **0,3 ms** | idem |
| Alerta URGENT após cancel | ~25 s (a linha cancelada tocava inteira) | **143–147 ms**; 0 buffers da linha cancelada após o cancel | idem |
| Sobreposição de falas | linha cancelada tocava sobre o alerta | **0 sobreposições** em 8 falas reais | `bench_rehearsal.py`, cenário completo |
| FPS no cockpit (bancada) | — | **30,0** em Stage 1, busca sem alvo e alvo visível (`/status` e cliente) | `bench_fps.py`, câmera sintética a 30 Hz, 856x480 |
| FPS no cockpit por estágio (aeronave) | pré 15,8 · S1 5,2 · S2 2,4 · S4 22,4 · S5 4,0 · pós 25,3 | **pré 30,0 · S1 29,9 · S2 29,9 · S4 30,0 · S5 30,0 · pós 26,6** | câmera real pelo relay, `bench_stage_fps.py` |
| CPU total no Stage 2 | — | 41,8% (VAAPI desnecessário, limite 70%) | idem |
| Device de inferência e p95 | CPU (sem GPU no venv) | AUTO: CUDA FP16 na missão; bancada idle a 480 px: CUDA FP16 45,0/46,4 ms, iGPU 26,1/30,4, CPU 38,5/46,5 (p50/p95) | `bench_inference_devices.py`, `docs/BENCH_INFERENCIA.md` |
| Exit codes | aborto e touchdown não confirmado saíam com 0 | completa **0**, aborto **3**, touchdown não confirmado **4** | `bench_rehearsal.py` |
| Testes | pytest 869; vitest 27 arquivos / 219; tsc 0 | pytest **1177**; vitest **53 / 443**; tsc 0; `npm run build` OK; `colcon build` missão e driver OK (0 warnings no driver) | |

## 2. Ensaio completo em bancada (8.3)

`scripts/bench_rehearsal.py`, domínio 91, `--no-fly`, quadro de bancada com alvo:

| Cenário | Esperado | Obtido |
|---|---|---|
| Completo (5 estágios) | exit 0, `[STEP 1..5]` em ordem, milestones em ordem, sem fala sobreposta | exit 0; 1,2,3,4,5; milestones em ordem; 8 falas, 0 sobreposições |
| Abort (SIGINT no Stage 2) | exit 3 | exit 3 (estágios 1, 2) |
| Touchdown sem confirmação (`rtl.touchdown_timeout_sec` 0,05) | exit 4 | exit 4; `mission.touchdown {confirmed: false, method: unconfirmed}` |
| Botão Finalizar ao longo do run | travado em todos os estágios, liberado após o exit com a aeronave no solo | confirmado: `finishLock.rehearsal.test.ts` sobre o traço gravado |
| Probe no lugar do driver (1.8) | nenhum takeoff, nenhum Twist não nulo | `test_no_fly_interlock.py` verde |

Achado do ensaio, corrigido: o touchdown é confirmado pela odometria a `rtl.touchdown_altitude_m` e a missão
saía com o simulador ainda descendo, de modo que o último estado de bancada era LANDING (4) e o Finalizar
ficava travado depois de uma missão de bancada bem-sucedida. `KinematicSimulator.complete_landing()` termina o
pouso comandado no encerramento, como o firmware faz, e publica LANDED.

## 3. Validação com a aeronave

| Item | Resultado |
|---|---|
| 4.1 | Aeronave confirmou `REC1080_STREAM480`, 30 FPS, `LOW_LATENCY`, foto JPEG; `image_raw` 856x480 a 29,98 Hz |
| 4.2 / 4.3 | `compressed` a 29,97 Hz; bridge MJPEG com fonte comprimida a 29,8–29,9 FPS; perfil DDS entregando 1,23 MB a 30 Hz |
| 4.5f | CPU no Stage 2 41,8%: VAAPI não implementado (regra do plano) |
| 4.6 | `camera_info` 856x480 igual ao quadro |
| 4.7 | Dois defeitos achados e corrigidos ao vivo: o driver coalescia os eventos de foto e perdia o TAKEN (fila de eventos drenada a 50 ms); o TAKEN chega 2,5 s após o pedido (ACK assíncrono, janela de 4 s). Foto nativa 4096x3072 baixada por FTP em 0,8 s |
| 4.8 | 30 FPS em todos os estágios medidos |
| 6.1 | Wi-Fi derrubado: link false, bateria NaN, `flying_state` 255, GPS `NO_FIX` e odometria parada em ~1 s. O driver não reconecta a sessão ARSDK sozinho (limitação do upstream) |
| 6.3 | ACK de flat trim em < 0,5 s |
| 6.4 | Dispensada pelo usuário (calibração pelo app); `required: 0` reportado |
| 7.2 | Daemon não estava obsoleto nesta sessão; descoberta no processo em 452 ms |
| 7.4 | Ver seção 5 |

## 5. Device de inferência e arranque (decidido)

O AUTO escolhia pelo menor p95 e gravou a iGPU (p95 31,6 ms contra 53 ms da CUDA, sob a carga do relay), mas
carregar a iGPU custa ~5,4 s no arranque contra ~2,5 s da CUDA: spawn → `[STEP 1` de 9–10 s. Decisão do
usuário (2026-10-01): **MX110 preferida**. O AUTO usa a CUDA sempre que ela carrega e passa a validação; o
benchmark só decide entre iGPU e CPU sem ela. Arranque na bancada depois da mudança: 6,4 s.

## 6. Desvios registrados

- spawn → `[STEP 1` ficou 0,2 s acima da meta de 6 s na bancada; detalhes e o que resta no caminho crítico no
  registro (7.4).
- Defaults da ficha de parâmetros passam a ser os do Python (5.2); countdown com piso 0 e default 10 s (5.7).
- Demais rulings: seção "Decisões e desvios" de `docs/IMPLEMENTACAO_PROGRESSO.md`.
