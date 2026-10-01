# Relatório de Implementação E2E — Vídeo, Finalizar e Copiloto (rascunho, pendente drone)

Plano: `docs/PROMPT_IMPLEMENTACAO_E2E_VIDEO_FINALIZAR_COPILOTO.md`.
Diagnóstico de referência ("antes"): `docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md`.
Registro item a item: `docs/IMPLEMENTACAO_PROGRESSO.md`.

Estado: Fases 0 a 7 concluídas em código; Fase 8 concluída na parte de bancada. As medições com o Bebop
conectado (vídeo real, ACKs, FTP, link) estão marcadas como **pendente drone** e são preenchidas na retomada.

## 1. Antes e depois

| Métrica | Antes (diagnóstico) | Depois | Condição da medida |
|---|---|---|---|
| spawn → `[STEP 1` | 11,5 s (relatório); 11,8 s (`bench_startup.py`, 3 rodadas) | **6,2 s** (mediana de 5: 6,1–7,2 s) | Bancada sem driver, `BMG_GCS_SESSION=1`, cache de device quente |
| Abortar → land, ponte residente | 0,7–2,9 ms; 1.301 ms com a ponte antes do `ready` | **0,55 ms** p50; primeiro comando 42 ms; 30/30 entregues | `bench_abort_latency.py`, domínio isolado |
| Abortar → land, fallback CLI | ~1.850 ms, **1 de 3 perdido** | 2.064 ms p50, **30/30** entregues; stop→land 5.075 ms p50, 30/30 | idem |
| Fala, sessão fria | ~11 s até o início | **1.243–1.298 ms** | `bench_speech_latency.py`, API real, dispositivo silencioso |
| Fala, sessão quente | ~2 s | **709–856 ms**; do cache em disco **0,3 ms** | idem |
| Alerta URGENT após cancel | ~25 s (a linha cancelada tocava inteira) | **143–147 ms**; 0 buffers da linha cancelada após o cancel | idem |
| Sobreposição de falas | linha cancelada tocava sobre o alerta | **0 sobreposições** em 8 falas reais | `bench_rehearsal.py`, cenário completo |
| FPS no cockpit (bancada) | — | **30,0** em Stage 1, busca sem alvo e alvo visível (`/status` e cliente) | `bench_fps.py`, câmera sintética a 30 Hz, 856x480 |
| FPS no cockpit por estágio (aeronave) | pré 15,8 · S1 5,2 · S2 2,4 · S4 22,4 · S5 4,0 · pós 25,3 | **pendente drone** (4.8) | câmera real |
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

## 3. Pendências que exigem o drone

| Item | O que validar |
|---|---|
| 4.1 | Configuração de vídeo aplicada (logs `... applied`), taxa de `image_raw` |
| 4.2 / 4.3 | `camera/image_raw/compressed` no bridge; perfil Fast DDS com o driver no ar |
| 4.5f | CPU total no Stage 2 com vídeo real (VAAPI só acima de 70%) |
| 4.6 | `camera_info` contra o tamanho do quadro real |
| 4.7 | `states/picture_event` (foto pedida uma vez no domínio do driver, autorizado) e download FTP |
| 4.8 | FPS por estágio com câmera real |
| 6.1–6.4 | Stamps ARSDK, invalidação ao derrubar o link, ACK de flat trim, eventos de calibração magnética |
| 7.2 | Reprodução do daemon obsoleto com o driver vivo |
| 7.4 | spawn → `[STEP 1` com driver e câmera reais (a bancada mede 6,2 s; o caminho crítico é o setup de inferência) |

## 4. Desvios registrados

- spawn → `[STEP 1` ficou 0,2 s acima da meta de 6 s na bancada; detalhes e o que resta no caminho crítico no
  registro (7.4).
- Defaults da ficha de parâmetros passam a ser os do Python (5.2); countdown com piso 0 e default 10 s (5.7).
- Demais rulings: seção "Decisões e desvios" de `docs/IMPLEMENTACAO_PROGRESSO.md`.
