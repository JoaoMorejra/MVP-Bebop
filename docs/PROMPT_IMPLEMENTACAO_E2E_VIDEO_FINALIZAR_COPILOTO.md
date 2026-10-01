# Prompt de Implementação — Segurança do Abort, Trava do Finalizar, Copiloto Verídico, Vídeo 30 FPS

> Gerado a partir de `docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md` (HEAD auditado `00e2292`).
> Leia o relatório inteiro antes de começar. Toda citação `arquivo:linha` refere-se a esse HEAD;
> confirme a linha antes de editar, porque ela pode ter se movido.
> O relatório já contém a verificação ao vivo com o drone conectado (seções 0 a 6). Use seus números
> como a tabela "antes".

## Decisões de produto (vinculantes)

- **D1. Laudo pericial hardcoded.**
  - As falas da perícia continuam sorteadas dentre os achados configurados em
    `bebop_mission_control/src/lib/forensics.ts` (police, samu, victim, vehicle, com aberturas, ligações
    e fechos), sem análise de imagem real.
  - Não substitua esse mecanismo, não o condicione a detecções e não o rotule como simulado.
- **D2. Números falados vêm dos parâmetros da missão corrente.**
  - Todo valor numérico falado que corresponda a um parâmetro vem do **valor configurado** no documento
    de parâmetros com que a missão corrente foi lançada, nunca da telemetria em tempo real. Isso vale para
    altura, velocidade de cruzeiro, velocidade de RTL, raio de chegada, tempo de busca, tempo de hover,
    confiança e limiar de bateria.
  - A fonte é o payload emitido pelo `mission.py`, que já contém o documento mesclado arquivo →
    `--params-json`. Nunca use defaults ou fallbacks do frontend.
  - Valores sem parâmetro correspondente, como a distância estimada até o sinistro, mantêm a estimativa
    atual.
  - D2 muda só a fonte do número. A **condição** de disparo de cada fala continua precisando refletir o
    evento real daquele voo.
- **D3. CPU e GPU liberadas; MX110 como alvo preferencial se o benchmark confirmar.**
  - Habilite os três devices da estação: NVIDIA MX110 (CUDA), Intel HD 620 (OpenVINO GPU) e CPU.
  - A inferência YOLO vai para a MX110 quando o benchmark da Fase 4.5 provar que ela é a melhor opção.
    Senão vai para a iGPU e, por último, para a CPU. Sempre há fallback automático em tempo de execução.
  - Expectativa, não medida: a MX110 (GM108, ~0,8 TFLOPS FP32, 2 GB dedicados) tem cerca de 2x o FP32 da
    HD 620. Também não disputa banda de memória nem o display (Xorg e Chromium) com a CPU. A decisão final
    é do benchmark.
- **D4. Instalações feitas pelo Claude Code.**
  - Todas as dependências de sistema e de Python necessárias são instaladas por você, seguindo a Fase 0B:
    driver NVIDIA legado, CUDA/torch, OpenCL Intel, plugins ROS, sysctl e ferramentas.
  - Trechos que exigem root seguem a regra 6.

Você vai trabalhar no monorepo MVP-Bebop, em `<ws>/src/mvp_mission_bebop/`, onde `<ws>` é o workspace
ROS 2 desta máquina (`/home/jv/ros2_ws` ou `/home/joaomoreira/ros2_ws`; confirme com `ls`). O driver fica em
`<ws>/src/ros2_bebop_driver` (repositório separado) e o SDK Black Bee em `<ws>/src/nectar-sdk`.
Siga o `CLAUDE.md` do monorepo à risca: tipagem estrita, docstrings NumPy/reST, estilo Black Bee, zero
emoji, zero comentário óbvio, Conventional Commits em inglês técnico.

## Regras invioláveis

1. **PROIBIDO ARMAR MOTORES.**
   - Nunca execute, publique nem dispare `takeoff`, `/bebop/takeoff`, PCMD, `/bebop/cmd_vel` com valor
     não nulo, `mission.py --fly`, nem o botão "Iniciar Missão" fora do modo bancada.
   - Nunca rode o fluxo com o driver real conectado a um drone, exceto os comandos somente leitura da
     Fase 0.
   - Toda validação de fluxo roda com `--no-fly` e **sem** o driver conectado ao drone, ou com um nó probe
     que só assina tópicos.
   - Se uma tarefa parecer exigir voo, pare e peça ao usuário.
2. **nectar-sdk é fonte da verdade e não é editado.**
   - Antes de cada fase, inspecione o SDK em busca do que já existe.
   - Correções de comportamento do SDK (por exemplo, `BebopDrone.takeoff` sempre retorna True após
     `sleep(3)`) são contornadas no pacote da missão, no proxy (`actuators/proxy.py`), nunca no SDK.
   - O SDK em uso é `nectar-sdk/nectar/nectar` (espelhado em `build/nectar/python_stage`). A cópia
     `nectar-sdk/nectar-sdk/...` não é a carregada.
3. **Driver com modificações locais.**
   - `ros2_bebop_driver` tem 10 arquivos modificados sem commit. Rode `git -C <ws>/src/ros2_bebop_driver
     diff --stat` antes de tocar.
   - Construa sobre essa working tree. Nunca rode `git checkout`, `git stash` ou `git reset` nesse
     repositório.
4. **Commits.** Não faça commit nem push sem autorização explícita do usuário na sessão. Prepare as
   mudanças já particionadas nos commits atômicos listados no fim deste documento e peça autorização.
5. **Contratos do GCS.**
   - Preserve o esquema de `mission_config.json`.
   - Mudanças de contrato stdout/IPC (marcadores, milestones, exit codes) são aditivas e cobertas em
     `test/test_contracts.py` e nos testes vitest correspondentes.
6. **Root, senhas e reboot.**
   - Teste `sudo -n true`. Se funcionar, execute você mesmo os passos de root da Fase 0B.
   - Se pedir senha, nunca solicite, digite nem armazene a senha. Gere `scripts/setup_station.sh`
     (idempotente, `set -euo pipefail`, log em `~/.cache/bmg/setup_station.log`, sem reboot interno) e
     peça ao usuário para rodar no terminal dele:
     ```bash
     sudo bash scripts/setup_station.sh
     ```
     Depois valide o resultado você mesmo.
   - Nunca reinicie a máquina. Quando um reboot for necessário, atualize `docs/IMPLEMENTACAO_PROGRESSO.md`
     com o ponto exato de retomada e peça ao usuário para reiniciar e colar o prompt de retomada de
     `docs/PROMPT_INICIO_CLAUDE_CODE.md`.
7. **Progresso persistente.**
   - Mantenha `docs/IMPLEMENTACAO_PROGRESSO.md` atualizado ao fim de cada item: fase, item, estado
     (feito, em andamento, bloqueado), testes rodados, pendências para o usuário e próximo passo.
   - É a fonte de verdade para retomar depois de reboot ou de uma nova sessão.
   - Leia esse arquivo antes de qualquer outra coisa ao iniciar.

---

## Fase 0 — Check-in e diagnóstico

1. Check-in:
   ```bash
   cd <ws>/src/mvp_mission_bebop && git status && git pull origin main
   ```
   ```bash
   source <ws>/bin/nectar-activate && python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py -q
   ```
   ```bash
   cd bebop_mission_control && npx vitest run && npx tsc --noEmit
   ```
   Linha de base conhecida: 869 pytest, 219 vitest, tsc limpo.

2. Diagnóstico ao vivo, somente leitura. Serve para revalidar depois das mudanças. A linha de base já
   está no relatório. Rode só se o usuário confirmar que o Bebop está ligado, pousado e com a rede dele
   conectada. Nenhum destes comandos publica.
   - Antes de tudo, reinicie o daemon do `ros2`. Um daemon obsoleto devolve `ros2 node list` vazio com o
     driver vivo:
     ```bash
     ros2 daemon stop && ros2 daemon start
     ```
   - Ensaio da missão com o drone conectado: nunca no domínio do driver.
     - Rode `mission.py --no-fly` em `ROS_DOMAIN_ID` isolado (por exemplo 77), alimentado por um relay
       unidirecional só de sensores (driver → missão).
     - Confirme `Publisher count: 0` em `/bebop/{takeoff,cmd_vel,land}` no domínio do driver durante o
       ensaio.
     - O relay e o método estão descritos na seção 0 do relatório. Transforme o relay em
       `scripts/bench_relay.py`, rastreado no Git, com docstring e tipagem.
   ```bash
   ping -c 3 192.168.42.1
   ```
   ```bash
   make -C <ws>/src/nectar-sdk driver-bebop
   ```
   ```bash
   ros2 node list && ros2 topic list -t && ros2 service list && ros2 action list
   ```
   ```bash
   ros2 topic hz /bebop/camera/image_raw --qos-reliability best_effort --window 60
   ```
   ```bash
   ros2 topic hz /bebop/odom
   ```
   ```bash
   ros2 topic echo --once /bebop/states/flying_state
   ```
   ```bash
   ros2 topic echo --once /bebop/states/battery
   ```
   ```bash
   ros2 topic echo --once /bebop/states/gps
   ```
   ```bash
   ros2 topic echo --once --field width /bebop/camera/image_raw
   ```
   ```bash
   nstat -az UdpRcvbufErrors
   ```
   Registre cada saída numa tabela "antes" (resolução real do stream, FPS, delta de `UdpRcvbufErrors`
   em 60 s). Depois, `make -C <ws>/src/nectar-sdk driver-stop`.

   Se o drone não estiver disponível agora, registre "aguardando drone" e siga para a Fase 0B. Em todo
   item posterior que exija o drone (Fases 4, 6 e 8), pare, peça ao usuário para conectar (mantendo o
   tethering USB para internet), valide o link depois da confirmação e retome do mesmo item.

---

## Fase 0B — Instalação das dependências da estação (D3, D4)

Estado medido em 2026-09-30:

| Item | Estado |
|---|---|
| Sistema | Ubuntu 24.04, kernel 6.8.0-139, Secure Boot desligado (DKMS carrega sem assinar) |
| GPU NVIDIA | MX110 com `nvidia-driver 610.57.04`, vindo do repositório CUDA `developer.download.nvidia.com` (prioridade 600). O kernel ignora a GPU: "supported through the NVIDIA 580.xx Legacy drivers". Candidato disponível: `nvidia-driver-580` 580.178.04 no repositório CUDA e no Ubuntu restricted. |
| Python | venv `/home/jv/ros2_ws/.venv` com torch `2.9.1+cpu`, ultralytics `8.4.137` e OpenVINO `2026.4` (só `CPU`) |
| Disco | `/` com 12 GB livres (86% usado) |

Antes de instalar, grave um snapshot de rollback em `~/.cache/bmg/rollback/`:
- `dpkg -l > dpkg_before.txt`;
- `pip freeze > pip_before.txt` (no venv);
- `apt-mark showhold > holds_before.txt`;
- cópia de `/etc/apt/preferences.d/`.

Execute na ordem. Cada passo é idempotente e validado antes do próximo.

1. **Pacotes ROS e ferramentas (root):**
   ```bash
   apt-get update
   ```
   ```bash
   apt-get install -y ros-jazzy-image-transport-plugins clinfo vainfo intel-opencl-icd
   ```
   - Confirme que o `fastdds` CLI existe (`command -v fastdds` com o ROS carregado). Se não existir,
     localize o pacote que o provê com `apt-file` ou `dpkg -S` e instale.
2. **Grupos de GPU (root):** `usermod -aG render,video <usuário>`. Só vale depois de novo login ou do
   reboot do passo 5.
3. **sysctl do DDS (root):**
   - crie `/etc/sysctl.d/60-bmg-dds.conf` com `net.core.rmem_max=16777216`,
     `net.core.wmem_max=16777216`, `net.core.rmem_default=4194304` e `net.core.wmem_default=4194304`;
   - aplique com `sysctl --system`.
4. **Driver NVIDIA legado 580 (root).** A GPU é usada só para computação; o display continua na Intel.
   - Liste com `dpkg -l | grep -i nvidia` e grave no snapshot.
   - Remova o stack 610 do repositório CUDA:
     ```bash
     apt-get purge -y nvidia-driver nvidia-kernel-common nvidia-kernel-source libnvidia-compute
     ```
     Inclua também qualquer outro pacote `nvidia-*`/`libnvidia-*` 610 que apareça na lista, e depois
     `apt-get autoremove -y --purge`.
   - Crie `/etc/apt/preferences.d/bmg-nvidia-legacy` para impedir que o repositório CUDA reinstale a
     série 581+:
     ```
     Package: nvidia-driver cuda-drivers nvidia-open cuda-drivers-*
     Pin: release *
     Pin-Priority: -1
     ```
   - Instale o stack de computação, sem servidor X:
     ```bash
     apt-get install -y nvidia-headless-580 nvidia-utils-580
     ```
     Se o repositório CUDA oferecer só `nvidia-driver-580`, use esse pacote e confirme que a Intel
     continua dona do display com `prime-select query`; use `prime-select on-demand` se necessário.
   - Rode `apt-mark hold` em todos os pacotes `*-580` instalados.
   - `dkms status` deve mostrar o módulo `nvidia/580.*` construído para `$(uname -r)`.
5. **Reboot (usuário).**
   - Atualize `docs/IMPLEMENTACAO_PROGRESSO.md` com "retomar em Fase 0B.6" e peça ao usuário para
     reiniciar e colar o prompt de retomada.
6. **Validação após o reboot:**
   - `nvidia-smi` lista a `GeForce MX110`;
   - `journalctl -k -b | grep NVRM` sem a mensagem de legado;
   - `clinfo -l` lista a Intel;
   - `python3 -c "import openvino as ov; print(ov.Core().available_devices)"` contém `GPU`;
   - `id -nG` contém `render` e `video`;
   - `sysctl net.core.rmem_max` retorna 16777216.

   Se o driver 580 falhar, restaure o 610 a partir do snapshot, registre o motivo no progresso e siga só
   com iGPU e CPU. Não bloqueie as outras fases.
7. **torch CUDA no venv (sem root):**
   - Confira espaço: pelo menos 8 GB livres. Rode `pip cache purge` antes, se necessário.
   - Instale as mesmas versões da linha de base, em build CUDA 12.6:
     ```bash
     pip install --index-url https://download.pytorch.org/whl/cu126 torch==2.9.1 torchvision==0.24.1
     ```
   - O venv tem `torchaudio 2.11.0`, desalinhado com o torch 2.9.1.
     - Rode `grep -rn torchaudio` no monorepo e no nectar-sdk.
     - Se não houver uso, desinstale.
     - Se houver, instale `torchaudio==2.9.1` do mesmo índice.
   - Valide `torch.cuda.is_available()`, `torch.cuda.get_device_name(0)` e que `torch.cuda.get_arch_list()`
     contém `sm_50`.
   - Se não contiver, desinstale e use a última versão cu118 que suporte sm_50 (por exemplo
     `torch==2.7.1+cu118` com o torchvision pareado).
   - Confirme que ultralytics e nectar-sdk continuam importando e que as dependências do SDK aceitam a
     versão escolhida.
   - Rode a suíte completa: `pytest test/`, com 869 ou mais testes verdes.
8. **Build:**
   - `colcon build --symlink-install --packages-select ros2_bebop_driver mvp_mission_bebop`;
   - `npm run build` em `bebop_mission_control`.
9. Registre no progresso as versões finais instaladas e o resultado de cada validação.

---

## Fase 1 — Segurança do abort e do ciclo de vida (P0)

1. **Segundo SIGINT não pode cortar o pouso.**
   - `bebop_mission_control/electron/main.cjs:stopMissionProcess` passa a marcar o filho como
     sinalizado e nunca reenvia SIGINT. Chamadas repetidas retornam a mesma promise (single-flight).
   - Em `mvp_mission_bebop/engine/signals.py:_on_signal`, o segundo sinal só força `os._exit` depois que o
     burst de land do primeiro já foi transmitido. Proteja com um `threading.Event` setado ao fim de
     `_transmit_emergency_landing`.
   - Teste em `test/test_signals.py`: dois SIGINT em sequência produzem o burst completo.
2. **Handlers IPC single-flight.** `bmg:abort-mission` e `bmg:end-mission` guardam a promise pendente e
   a devolvem a chamadas concorrentes.
3. **Backup de land confiável.**
   - Remova `-w 0` de `LAND_PUB` e `STOP_PUB` (`main.cjs:2349-2350`).
   - Medido ao vivo: `--once` perdeu 1 de 3 tanto com `-w 0` quanto com `-w 1`. Use `-w 1 -t 10 -r 20`
     (10 publicações em 0,5 s).
   - Com o fallback CLI aplicado, meça a entrega em domínio isolado (script `scripts/bench_abort_latency.py`,
     rastreado): 30 tentativas, 0 perdas exigidas.
   - Execute STOP antes de LAND, em sequência, nunca concorrentes. O land é sempre o último comando
     publicado.
   - Em `sendCommand` (`main.cjs:1778-1788`), respeite o evento `ready` da command_bridge: se a ponte não
     estiver pronta, retorne `false` e registre isso no log. O caminho C passa a ser obrigatório nesse caso.
   - Em `engine/runner.py:_transmit_emergency_landing`, publique o Twist zero antes do land em cada
     iteração, terminando sempre em land (hoje o Twist é intercalado depois do land).
4. **Exit codes determinísticos.**
   - Defina em `engine/signals.py` (ou num módulo `engine/exit_codes.py` com `Final[int]`):
     `EXIT_COMPLETE = 0`, `EXIT_FAILURE = 1`, `EXIT_ABORTED_LANDED = 3`, `EXIT_TOUCHDOWN_UNCONFIRMED = 4`,
     e o `FORCED_EXIT_CODE = 130` existente.
   - `EmergencyHandler` usa `EXIT_ABORTED_LANDED`.
   - `steps/rtl.py:_touchdown`, no ramo sem confirmação odométrica (`rtl.py:1570-1579`), sinaliza
     `EXIT_TOUCHDOWN_UNCONFIRMED` através do contexto. O runner o propaga em `finalize`.
   - Emita o milestone `mission.touchdown {confirmed: bool, at_base: bool, method: str}` antes de sair.
     Adicione a chave em `telemetry/milestones.py:MILESTONE_KEYS`.
   - `useMissionRuntime.ts` mapeia 0 para `finished`, 3 para `aborted`, 4 para `finished_unconfirmed` e o
     resto para `faulted`. Estenda `types/bmg.ts`.
   - Atualize `test/test_contracts.py`.
5. **Parser `[STEP N:` com buffer de linha.**
   - Em `main.cjs:2238-2245`, reutilize o buffer de linha de `electron/milestones.cjs` e processe todos
     os marcadores do chunk, emitindo `bmg:step-change` para cada um, em ordem.
   - Teste vitest cobrindo um marcador partido entre chunks e dois marcadores no mesmo chunk.
6. **`goto_stage` seguro** (`mission.py:660-684`):
   - recusar 1 sempre que a missão já passou do Stage 1 ou o countdown terminou, independentemente da
     altitude;
   - recusar 5 quando o estágio atual já é 5;
   - recusar 2 enquanto o Stage 1 não concluiu o takeoff;
   - ler a altitude do `odom_supervisor` continua sendo condição adicional, não suficiente.
   - Testes em `test/test_safety_invariants.py`.
7. **Encerramento do app em voo.**
   - `cleanupAllProcesses` (`main.cjs:2528-2566`) aguarda o `close` da missão (grace de 1500 ms,
     escalando para SIGKILL) **antes** de `make driver-stop`.
   - Se `latestTelemetry.flying_state` for airborne, publique land pela command_bridge e aguarde
     `flying_state` landed ou 10 s antes de derrubar o driver.
   - `window-all-closed` e `before-quit` pedem confirmação (`dialog.showMessageBox`) quando há missão
     ativa ou aeronave airborne.
   - Adicione o `mission.py` a `reapOrphanedServices` por padrão de cmdline e recuse o lançamento se
     encontrar uma missão órfã viva (`main.cjs:2152`).
8. **Intertravamento estrutural da bancada.**
   - `actuators/proxy.py:BenchtopDroneProxy` em `no_fly` levanta `RuntimeError` em qualquer caminho que
     alcance `takeoff`/`move_velocity` do drone cru.
   - Adicione um teste de integração que roda `mission.py --no-fly --stages 1,2,3,4,5` com um nó probe
     assinando `/bebop/takeoff` e `/bebop/cmd_vel`, e falha se receber qualquer mensagem de takeoff ou
     qualquer Twist não nulo. O Twist zero do burst de emergência é permitido.
   - Documente no docstring do proxy que gimbal, flat trim e photo continuam físicos em bancada.
9. **Abortar sempre disponível com aeronave no ar.** `CockpitScreen.tsx:157-163`: `abortEnabled = running
   || isAirborne(flying_state)`. Com o processo morto, o abort publica land direto pela command_bridge.

---

## Fase 2 — Trava lógica do botão "Finalizar Missão"

Implemente exatamente a máquina de estados da seção 7 do relatório.

1. Crie `bebop_mission_control/src/lib/finishLock.ts` com uma função pura:
   ```ts
   export type FinishLockState =
     | 'no_mission' | 'in_flight' | 'aborting' | 'await_exit'
     | 'await_ground' | 'grounded' | 'link_lost' | 'finishing';
   export function finishLockState(input: FinishLockInput, now: number): {
     state: FinishLockState; enabled: boolean; reason: string; requiresConfirm: boolean;
   }
   ```
   `FinishLockInput` recebe:
   - `missionState`, `processUp`, `exitCode`, `benchMode`;
   - `flyingState: number | null`, `flyingStateSince: number` (instante do primeiro `landed` contínuo);
   - `navFresh`, `navSource`, `telemetryAgeSec`, `finishing`.
2. Regras:
   - habilitado somente em `grounded`, com `flying_state === 0` contínuo por pelo menos 2 s, telemetria
     fresca, fonte `aircraft` e processo encerrado;
   - em bancada, exit mais `bench_flying_state === 0`;
   - `flying_state === 5` leva a `grounded` com aviso;
   - `link_lost` (null por mais de 10 s após o exit) só habilita por hold-to-confirm de 1,5 s;
   - qualquer valor em {1, 2, 3, 4, 7, 8} volta para `await_ground`.
3. Unifique os conjuntos airborne. Crie `src/lib/flightState.ts:AIRBORNE_STATES = [1,2,3,4,7,8]` e
   espelhe o mesmo conjunto em `main.cjs:2411` e `streamer/telemetry_bridge.py:102`. O estado 6
   (usertakeoff, espera em solo) sai do conjunto.
   - Teste de contrato em `test/test_contracts.py` que lê os três arquivos e compara os conjuntos.
4. `ForensicPanel.tsx:246-262`:
   - `disabled={!lock.enabled}`, `aria-disabled` e cor inativa;
   - tooltip com `lock.reason` em todos os estados bloqueados: "Em voo: use Abortar Missão",
     "Aguardando confirmação de pouso (flying_state)", "Pousando...", "Link perdido: confirme
     visualmente e segure para finalizar";
   - guard por `useRef` em `App.finishMission` (`App.tsx:520-542`), com o estado `finishing` até
     `endMission` resolver.
5. `StatusBar.tsx:311-315`: remova o fallback "em solo" para estado desconhecido e exiba "estado
   desconhecido".
6. Testes vitest em `finishLock.test.ts` cobrindo toda a tabela de transições, clique duplo e
   pointer-down repetido em 50 ms. Adicione também um teste de integração em
   `CockpitScreen`/`ForensicPanel`.

---

## Fase 3 — Copiloto de voz verídico, sem sobreposição e sem atraso

Regras da fase:

- Cada fala só dispara quando o evento que ela descreve realmente ocorreu no voo corrente.
- Os números seguem D2: vêm do parâmetro configurado da missão.
- O laudo segue D1.
- Refaça a tabela da seção 5 do relatório como teste: cada entrada vira um caso em
  `copilotPhrases.test.ts` ou `test/test_announcer_calls.py`.

0. **Fonte única dos números falados (D2).**
   - O `mission.py` passa a emitir, uma vez no início, o milestone `mission.parameters` com os valores
     efetivos da missão: `target_altitude_m`, `cruise_mps`, `rtl_mps`, `search_timeout_sec`,
     `hover_duration_sec`, `confidence`, `arrival_radius_m`, `battery_land_pct` e `countdown_sec`.
     São os mesmos valores já usados pelos steps, depois do merge arquivo → `--params-json`.
   - Os milestones seguintes que falam um desses números o repetem no payload, lido de `ctx.params`,
     nunca de `snapshot()`.
   - `copilotPhrases.ts` formata apenas o valor do payload, e o fallback atual para o documento do
     frontend (`copilotPhrases.ts:388-390`, `App.tsx:171`) é removido.
     - Sem valor no payload, a variante com número não é elegível e cai numa variante sem número.
     - O frontend nunca inventa default.
   - Teste: lançar com altitude 2,3 m e velocidade 0,35 m/s no `--params-json` e verificar que as falas
     dizem "dois vírgula três metros" e "zero vírgula trinta e cinco metros por segundo", mesmo com a
     telemetria em outro valor.
1. **Laudo pericial (D1: manter hardcoded e sorteado).**
   - Preserve `buildForensicReport`, os pools de `WORDINGS`, `OPENERS`, `LINKERS`, `CLOSERS` e o
     histórico anti-repetição em localStorage.
   - Não vincule os achados a detecção nem a imagem.
   - Corrija só o encadeamento:
     - `inspection.intro` ("Aeronave em solo") sai apenas com `finishLock.state === 'grounded'`
       (Fase 2);
     - as linhas do laudo entram na `NarrationQueue` como grupo cancelável (`resetGroup`), para que
       "Finalizar" ou um alerta as descartem de fato (item 8);
     - `findingGapMs` só é usado quando o copiloto não responde.
2. **Touchdown** (`useCopilot.ts:137, 240-244`, `App.tsx:164`):
   - dispare apenas no milestone `mission.touchdown` com `confirmed === true`;
   - o texto inclui "na base" somente com `at_base === true`;
   - com `confirmed === false`: "Pouso comandado. Confirmação de toque indisponível. Verifique
     visualmente.";
   - não diga nada sobre pouso quando não houver Stage 5 na execução.
3. **Decolagem** (`steps/takeoff.py:_launch`, `copilotPhrases.ts:119-126`):
   - `mission.takeoff` é emitido só quando o `flying_state` real passa para 1/2 (voo) ou quando a
     odometria confirma `relative_altitude > takeoff_settle_min_altitude_m`. Em bancada, com o
     simulador.
   - O payload mantém `altitude_m = params.kinematics.target_altitude_m` (D2: valor configurado, não
     medido). Só o **momento** do disparo muda.
   - Remova "Teto operacional", porque o teto é `altitude_ceiling`, outra grandeza. Se quiser falar o
     teto, use o valor configurado do teto.
   - Assine `/bebop/states/flying_state` no nó de telemetria da missão (hoje ausente) e exponha o valor em
     `ctx`.
   - `copilotPhrases.test.ts:79` ("cite the configured altitude") continua válido sob D2. Adicione o
     caso de que a fala não sai antes da confirmação de voo.
4. **Countdown:**
   - remova o timer de `electron/milestones.cjs:scheduleScriptMilestones` e o overlay baseado no spawn;
   - o `mission.py` emite `mission.countdown {remaining_sec}` a cada segundo inteiro de
     `takeoff.py:_countdown` e `mission.countdown_3` com `remaining <= _CLEARANCE_ANNOUNCE_SEC`;
   - `CountdownOverlay.tsx` passa a ser dirigido por esse milestone;
   - remova "checklist/validado/validação" das variantes;
   - o passo "Nivelamento da IMU" só fica concluído com o ACK de flat trim da Fase 6.
5. **Frases condicionais.** Passe flags no payload e filtre os pools em `phraseForMilestone`:
   - `capture_done {target_confirmed, settled}`: sem alvo, "Captura de referência registrada, sem alvo
     confirmado"; remova "alta fidelidade", a menos que exista foto nativa;
   - `rtl_start {via_jump, inspected, marker_guided}`;
   - `landing {at_base}`;
   - `approaching {ibvs}`.
6. **Alertas no solo** (`telemetry/announcer.py:_format_telemetry_statement`):
   - `takeoff_failed`, `calibration_failed`, `init_failed` e `step_failed` antes do takeoff não anexam
     "Executando pouso seguro". Use o motivo real (`details['etapa']`/`erro`, com a porcentagem de
     bateria);
   - `failsafe.py:trigger_emergency_land`: `reason` mapeado para pt-BR por dicionário `Final`, sem ponto
     duplo e sem `str(exc)` cru;
   - `runner.py:_announce_failure`: suprimir `step_failed` quando o failsafe ou a própria etapa já
     alertou.
7. **`mission.battery_warning`:**
   - adicione o pool em `PHRASE_POOLS`;
   - o disparo segue a leitura real da bateria, porque é o evento;
   - o número falado é o limiar configurado (`battery.warning_pct` / `land_pct`, D2), por exemplo
     "Bateria abaixo de dez por cento".
8. **Fila única e mutex real:**
   - todos os bypasses passam por `NarrationQueue.preempt`: `App.tsx:returnOnCriticalBattery`,
     `runBenchStage`, `finishMission` e `main.cjs:bmg:abort-mission`. No abort, o main emite
     `bmg:milestone {kind:'alert', key:'mission.abort'}` em vez de escrever no stdin do daemon;
   - a frase de RTL por bateria só é falada depois do ACK de `gotoStage`;
   - `announcer.py`: contador de geração incrementado em `cancel_pending`. O `_consumer_worker` descarta o
     áudio se a geração mudou antes de `play_audio` e cancela a task de síntese em curso. `serve_stdin`
     processa `cancel` fora do worker serial;
   - alerta não corta alerta, tanto no daemon quanto na fila;
   - `electron/terminal.cjs:85-90` exporta `BMG_GCS_SESSION=1` no PTY. Além disso, `station_narrates()`
     detecta um lockfile do daemon (`$XDG_RUNTIME_DIR/bmg-announcer.lock`) para impedir um segundo
     player.
9. **Latência.** Medições ao vivo desta máquina, a serem batidas:

   | Caso | Hoje | Meta |
   |---|---|---|
   | Sessão fria até o início da fala | ~11 s | < 1,5 s |
   | Sessão quente | ~2 s | < 0,8 s |
   | Alerta URGENT após cancel | ~25 s | < 1,0 s |
   | Linha cancelada | tocou inteira | silenciada em < 100 ms |

   - aquecimento da sessão Gemini Live no boot do daemon, com keepalive a cada 60 s, para que a primeira
     fala da missão nunca pague o connect frio;
   - streaming de playback a partir do primeiro chunk em `_synthesize_once`/`AudioPlaybackDevice`, com
     `sounddevice.OutputStream`;
   - cache em disco (`~/.cache/bmg/speech/<sha1(texto+voz+modelo)>.pcm`) de todas as frases de texto
     fixo, incluindo os pools de milestone com o número já resolvido pelos parâmetros da missão (D2) e
     todas as combinações do laudo (D1).
     - O cache é gerado no lançamento, em segundo plano, e na hora do evento toca do disco com
       latência abaixo de 100 ms.
     - Como os números vêm dos parâmetros, todas as frases de uma missão são conhecidas antes do voo.
   - Alertas URGENT tocam do cache e interrompem o `OutputStream` em curso: `stop` com o contador de
     geração do item 8.
   - prefetch no lançamento das frases de texto fixo (alertas, touchdown, abort) com o mecanismo de
     `reserveLaunchPhrase`;
   - alinhe os tetos: `SPEECH_CEILING_MS` (`useCopilot.ts:21`) igual ao `timeout` do daemon
     (`main.cjs:1700`) e ao `announce(wait)`. Ao estourar o teto, envie `cancel`;
   - meça evento → início de áudio com timestamps monotônicos no log (`[SPEECH] key latency_ms=`) e
     cubra com teste.
10. **Standalone:** verbatim também fora da GCS (`announcer.py`, `verbatim=True`), para o texto logado ser
    o texto falado.

---

## Fase 4 — Vídeo ao vivo a 30 FPS com evidência nativa

1. **Driver** (`ros2_bebop_driver`, sobre a working tree local):
   - `Bebop::configureVideo(resolution, framerate, stream_mode, stabilization)` em `bebop.hpp`/
     `bebop.cpp`, chamado em `connect()` antes de `sendMediaStreamingVideoEnable(1)`:
     `sendPictureSettingsVideoResolutions(REC1080_STREAM480)`, `sendPictureSettingsVideoFramerate(30_FPS)`,
     `sendMediaStreamingVideoStreamMode(LOW_LATENCY)`;
   - parâmetros ROS `video_resolution`, `video_framerate`, `video_stream_mode` e `video_stabilization`,
     declarados junto de `image_qos` e expostos em `launch/bebop_node_launch.xml`;
   - trate `VIDEORESOLUTIONSCHANGED`, `VIDEOFRAMERATECHANGED` e `VIDEOSTREAMMODECHANGED` em
     `commandReceivedCallback` e logue o valor aplicado;
   - thread de publicação dedicada, esperando a condition variable e publicando com `unique_ptr`, no lugar
     do timer de 30 ms com espera de 10 ms. Câmera, odometria e `cmd_vel` em callback groups separados;
   - `try/catch` em todos os callbacks de subscription que chamam o ARSDK (`photo`, `flattrim`, etc.),
     logando em vez de derrubar o nó.
2. **Transporte comprimido para o GCS:**
   - `ros-jazzy-image-transport-plugins` é instalado na Fase 0B. Documente essa dependência em
     `CLAUDE.md` e no README do driver, e confirme que `/bebop/camera/image_raw/compressed` aparece com o
     driver rodando;
   - `streamer/mjpeg_server.py`: assine `/bebop/camera/image_raw/compressed` (`CompressedImage`, BE
     depth 1). Sem overlay, repasse os bytes JPEG direto a `BUFFER.publish`. Com overlay, `cv2.imdecode`,
     desenhar e recodificar em q75;
   - fallback para raw se o tópico compressed não existir;
   - um único `wfile.write` por quadro e `TCP_NODELAY` em `_serve_stream`.
3. **Fast DDS:**
   - `bebop_mission_control/config/fastdds_video.xml` com SHM `segment_size` de 16 MB e buffers UDP de
     8 MB;
   - exporte `FASTRTPS_DEFAULT_PROFILES_FILE` em `bin/nectar-activate` e em `getNectarEnv()`;
   - o sysctl de buffers é aplicado na Fase 0B.3. Documente em `CLAUDE.md` o arquivo
     `/etc/sysctl.d/60-bmg-dds.conf` e o `scripts/setup_station.sh` como parte do setup de uma estação
     nova.
4. **Fim do bloqueio por quadro anotado:**
   - countdown (`steps/takeoff.py:170`): `publish_detection_summary` no lugar de
     `publish_annotated_stream`;
   - RTL (`steps/rtl.py` `_publish_telemetry`/`_publish_marker_stream`): overlay JSON com schema
     `bmg.detections.v2` opcional, contendo cantos do marcador e eixos de pose, em
     `perception/summary.py` e `mjpeg_server.py:parse_detection_summary`/`draw_detection_summary`;
   - `DETECTION_PRIORITY_SEC` (`mjpeg_server.py:65`) passa para 0,25 s, com o quadro anotado usado só
     na captura do Stage 4.
5. **Orçamento de CPU e GPU (D3). Causa dominante medida ao vivo:** com o YOLO `.pt` a 640 sem limite
   de período, o `mission.py` ocupa 187% de CPU. O vídeo no mjpeg cai de 23,8 para 4,1 FPS, e o próprio
   driver → assinante cai de 27 para 11 Hz.

   Hardware medido:
   - Intel HD 620 (Gen9): OpenVINO 2026.4 instalado, mas só com o device `CPU`, porque falta o
     `intel-opencl-icd` (candidato 23.43 no apt);
   - NVIDIA MX110 (GM108, Maxwell): o kernel registra que só o driver legado 580.xx a suporta; o
     610.57.04 instalado a ignora. torch é `2.9.1+cpu`.

   Os três devices ficam disponíveis depois da Fase 0B.

   a. **Seleção de device.**
      - Adicione `vision.inference_device: str` em `parameters.py`, com os valores `"AUTO"`, `"CUDA"`
        (MX110), `"IGPU"` (HD 620 via OpenVINO) e `"CPU"` (OpenVINO CPU), default `"AUTO"`. Valide com
        `ValueError` fora do conjunto e inclua no `mission_config.json` sem quebrar o esquema.
      - Antes de criar qualquer abstração, verifique se o `Detector` do nectar-sdk já aceita `device` ou
        backend. Se aceitar, use o parâmetro dele. Se não aceitar, faça o roteamento numa camada da
        missão (`perception/`), sem editar o SDK:
        - CUDA: ultralytics `YOLO("yolov8n.pt")` com `device="cuda:0"`;
        - IGPU: `YOLO("yolov8n_openvino_model")` com `device="intel:gpu"`;
        - CPU: o mesmo modelo com `device="intel:cpu"`.
      - Nenhum caminho quebra os contratos de detecção existentes (classes, `conf`, caixas em pixels do
        frame original).
   b. **MX110 (CUDA, alvo preferencial).**
      - Rode FP32 e FP16 (`half=True`) e fique com o mais rápido. Maxwell sm_50 não tem FP16 acelerado,
        então é provável que FP32 vença.
      - Mantenha o modelo residente na GPU, com um warmup de 10 inferências.
      - Limite a memória com `torch.cuda.set_per_process_memory_fraction(0.8)`, porque são só 2 GB.
      - Se o CUDA falhar em tempo de execução (OOM, `CUDA error`, GPU ausente), faça fallback para IGPU
        e depois CPU sem derrubar o worker, logando `WARNING` com o motivo.
   c. **iGPU (OpenVINO GPU).**
      - FP16, com cache de compilação (`CACHE_DIR` do OpenVINO em `~/.cache/bmg/ov`), para o warmup não
        recompilar a cada missão.
   d. **Regra de decisão em `AUTO`, no warmup (`mission.py:503-509`):**
      - 30 inferências por device disponível, sobre o primeiro quadro real e com o `imgsz` configurado;
      - escolha o de menor p95;
      - em empate técnico (diferença de p95 abaixo de 10%), prefira CUDA, depois IGPU, depois CPU. CUDA
        deixa a iGPU livre para o display e o compositor do Chromium;
      - logue `[TIMING] inference_device=<d> p50_ms= p95_ms= candidates=<json>`;
      - o benchmark do warmup não pode passar de 6 s no total. Grave em `~/.cache/bmg/inference_device.json`
        a escolha por hash do modelo e `imgsz`, e reutilize nas missões seguintes, rodando só uma
        validação de 5 inferências.
   e. **Independentemente do device:**
      - valide a detecção do `yolov8n_openvino_model` com `imgsz 480` contra o `.pt` a 640, usando as
        evidências em `mvp_mission_bebop/accident_raw_*.png` (mesmas classes e confiança acima do
        limiar);
      - só então troque `vision.model_path` e `inference_imgsz` no default de `parameters.py`;
      - `PerceptionWorker._cycle` com período mínimo de 100 ms, parametrizado em
        `vision.inference_min_period_sec`;
      - o YOLO do countdown (`takeoff.py:159-171`) passa pelo mesmo worker, e não na thread principal.
   f. **Decode H.264:** fica em CPU (2,5 ms por quadro em 480p). VAAPI no driver
      (`AV_HWDEVICE_TYPE_VAAPI` em `video_decoder.cpp`) só entra se, com a inferência já fora da CPU, a
      CPU total no Stage 2 passar de 70%.
   g. **Script rastreado `scripts/bench_inference_devices.py`:**
      - mede p50/p95 por device (CUDA FP32/FP16, IGPU FP16, CPU) e por `imgsz` (320/480/640);
      - mede a CPU total durante a inferência contínua e o FPS do mjpeg em paralelo;
      - grava uma tabela em `docs/`.
      - A escolha do default em `parameters.py` sai dessa tabela.
6. **Calibração por resolução:**
   - `perception/intrinsics.py:apply_driver_calibration` valida `width`/`height` do `camera_info` contra
     a imagem recebida;
   - em divergência, escala as intrínsecas e loga em WARNING, ou recusa a pose se a razão de aspecto não
     bater.
7. **Evidência em resolução nativa:**
   - driver: `sendMediaRecordPictureV2` no lugar de `sendMediaRecordPicture` (`bebop.cpp:228-233`) e
     `sendPictureSettingsPictureFormatSelection(JPEG)` na conexão;
   - trate `PICTURESTATECHANGEDV2` e `PICTUREEVENTCHANGED`, publicando em `states/picture_event`;
   - `steps/inspection.py:_capture`: dispare `ctx.drone.snapshot()` **antes** do YOLO e registre no
     sidecar o ACK (ou a ausência dele);
   - no GCS, após `mission.touchdown`, baixe a foto por FTP (`192.168.42.1:21`, pasta de mídia do Bebop,
     com `ftplib` num script `streamer/media_fetch.py`) e anexe o caminho ao sidecar JSON;
   - o quadro do stream continua sendo a evidência imediata.
8. **Meta.** Linha de base medida ao vivo, com a câmera real e a mediana por estágio no `/status`:
   - hoje: pré-missão 15,8 · S1 5,2 · S2 2,4 · S4 22,4 · S5 4,0 · pós-missão 25,3 FPS;
   - com mjpeg direto no driver e YOLO ativo: 4,1 FPS;
   - meta: 30 FPS (mínimo de 27) em todos os estágios, com 856x480;
   - `camera_info` e imagem com a mesma resolução;
   - CPU total abaixo de 70% durante o Stage 2.

   Meça com o drone conectado e a missão em domínio isolado (Fase 0), e registre a tabela "depois".

---

## Fase 5 — Persistência de parâmetros

1. `App.tsx:246-257`:
   - remova todos os fallbacks hardcoded;
   - as flags individuais saem do `main.cjs:startMissionProcess`, e o `--params-json` passa a ser a
     única fonte;
   - se algum campo obrigatório não for número finito, o lançamento é bloqueado com mensagem.
2. Fonte única de defaults:
   - o `main.cjs` expõe `bmg:get-parameter-defaults`, que devolve `MissionParameters.factory()` do Python
     por um subcomando `mission.py --dump-defaults`;
   - `parameterSchema.ts` deixa de ter `defaultValue` próprio, e "Restaurar Padrões" usa esses defaults;
   - `ParameterSheet.readNumber` nunca mascara um valor ausente com default.
3. `bmg:get-parameters` com JSON inválido retorna `success:false`, com erro, e a UI bloqueia o lançamento.
4. `PreflightScreen.tsx:117-128` bloqueia o lançamento com `status !== 'ready'`.
5. `App.tsx:226`: falha de `params.save()` aborta o lançamento.
6. Escrita do Electron com temporário único, `fsyncSync` e `renameSync`.
7. `countdown_sec` 0 é respeitado como 0, com piso mínimo documentado em `parameters.py`.
8. `applyPreset` aplica apenas os campos do schema e preserva PID, calibração e `no_fly`.
9. A bancada também salva o `dirty` antes do spawn.
10. Teste vitest de duas missões na mesma sessão com edição entre elas, e teste pytest do merge
    arquivo → `--params-json` sem flags.

---

## Fase 6 — Telemetria verdadeira e calibrações com ACK

1. **Driver:**
   - propague o timestamp ARSDK (`when`) para `header.stamp` ou para um campo `age` nos estados;
   - invalide os estados no disconnect: bateria NaN, `flying_state` 255, GPS `NO_FIX`;
   - pare de integrar a pose sem odometria nova.
2. **`telemetry_bridge.py`:**
   - frescor por `header.stamp` com limite de 1,5 s;
   - lat/lon com o mesmo gating de `gps_fix`;
   - `gps_home` reseta a cada missão;
   - altitude relativa ao z0 da missão, publicada pela missão em `/bebop/mission/ground_reference`;
   - exponha roll/pitch do quaternion e o `sonar_altitude`;
   - `battery_age_sec` pelo stamp.
3. **Flat trim:**
   - trate `FLATTRIMCHANGED` no driver e publique em `states/flat_trim`;
   - `steps/takeoff.py` espera o ACK com timeout de 3 s. Sem ACK em voo real, FAILURE com alerta
     "Nivelamento sem confirmação", sem frase de pouso.
4. **Calibração magnética:**
   - exponha `sendCalibrationMagnetoCalibration(start)` no driver pelo tópico `calibrate_magneto` (Bool)
     e publique os eventos `MagnetoCalibrationStateChanged`/`RequiredState`/`StartedChanged`/
     `AxisToCalibrateChanged` em `states/magneto_calibration`;
   - no GCS, crie uma entrada em `DiagnosticsScreen` com estado por eixo e resultado confirmado pelo
     evento;
   - não dispare automaticamente: é procedimento manual de rotação do drone, sem motores.

---

## Fase 7 — Latência de arranque

1. `mvp_mission_bebop/telemetry/__init__.py`: importe o `announcer` (e `google.genai`) de forma lazy,
   somente quando `station_narrates()` for falso. Ganho medido de ~2,1 s.
2. **Daemon `ros2` obsoleto, reproduzido ao vivo.** Com o driver vivo, `ros2 node list` pelo daemon
   devolveu vazio até `ros2 daemon stop/start`. O `connect()` do SDK depende disso
   (`nectar/utils/process.py:61`, via `check_driver_status` em `nectar/control/base.py`). O SDK não é
   editado; corrija do lado da missão:
   - `actuators/proxy.py:BenchtopDroneProxy.connect`: antes de chamar o `connect()` do SDK, verifique o
     driver no próprio processo com `node.get_node_names_and_namespaces()` do nó de telemetria, com
     espera ativa de até 2 s;
   - se o grafo interno vê `/bebop/bebop_driver` e o SDK falha, logue `WARNING` "ros2 daemon stale",
     execute `ros2 daemon stop` e `ros2 daemon start` uma vez e repita o `connect()`;
   - `electron/main.cjs`: reinicie o daemon (`ros2 daemon stop; ros2 daemon start`) sempre que a
     estação sobe o driver e quando a interface Wi-Fi muda de rede.
3. **SHM órfão do Fast DDS:** 83 segmentos `fastrtps_*` em `/dev/shm`, 61 depois de encerrar tudo. No
   boot do BMG, sem processo ROS vivo, rode `fastdds shm clean` e logue quantos segmentos foram
   removidos.
4. Mantenha o warmup do YOLO, mas em paralelo ao `take_photo`, numa thread com join antes do Stage 1.
   Linha de base medida: spawn → `[STEP 1` em 11,5 s; meta abaixo de 6 s.
5. Log `[TIMING] phase=<nome> ms=<n>` para cada etapa de arranque, e o teste `test/test_profiling.py`
   estendido.

---

## Fase 8 — Validação final de integridade

1. `python3 -m pytest test/ -q`, `npx vitest run`, `npx tsc --noEmit` e `npm run build`:
   - todos verdes;
   - número de testes igual ou maior que a linha de base, mais os novos.
2. `colcon build --symlink-install --packages-select mvp_mission_bebop`, e o driver com
   `colcon build --symlink-install --packages-select ros2_bebop_driver`.
3. Ensaio completo em bancada, sem drone conectado:
   - `mission.py --no-fly` com o probe da Fase 1.8;
   - verificar a ordem de `[STEP 1..5]`, os milestones, os exit codes (0, 3 com abort no meio, 4 com
     touchdown forçado sem confirmação) e o estado do botão Finalizar em cada fase;
   - verificar a ausência de sobreposição de falas no log `[SPEECH]`;
   - verificar 30 FPS no `/status` do mjpeg com a imagem estática de bancada.
4. Relatório final em `docs/RELATORIO_VERIFICACAO_E2E_<data>.md`, com a tabela antes/depois de FPS,
   latências de arranque, abort e fala, e o que ficou pendente de validação com o drone real.
5. `git status` nos dois repositórios, lista de arquivos alterados e pedido de autorização ao usuário
   para commitar.

---

## Partição de commits (após autorização)

Monorepo MVP-Bebop:
1. `fix(signals): never cut an in-flight landing burst on a repeated interrupt`
2. `fix(gcs): make abort and end-mission single-flight and order stop before land`
3. `feat(engine): distinct exit codes and touchdown milestone for mission outcome`
4. `fix(gcs): line-buffer the stage marker parser`
5. `fix(mission): refuse unsafe stage jumps`
6. `fix(gcs): land and drain the mission before stopping the driver on quit`
7. `test(bench): assert no takeoff or non-zero velocity reaches the driver in no-fly`
8. `feat(gcs): finish-mission lock driven by confirmed ground state`
9. `refactor(gcs): single canonical airborne state set`
10. `fix(copilot): gate the forensic report on confirmed ground state and make it cancellable`
11. `fix(copilot): speak takeoff, countdown and touchdown only on confirmed events`
12. `fix(copilot): route every utterance through the narration queue and drop cancelled synthesis`
13. `perf(copilot): stream playback from the first audio chunk`
14. `perf(streamer): serve compressed frames and stop gating raw video on annotated frames`
15. `perf(perception): benchmarked CUDA, integrated GPU and CPU inference with runtime fallback and a bounded worker period`
16. `fix(perception): validate calibration resolution against the stream`
17. `feat(inspection): native-resolution evidence with acknowledgement and download`
18. `fix(gcs): launch strictly from the saved parameter document`
19. `fix(telemetry): freshness from source timestamps`
20. `perf(mission): lazy announcer import and in-process driver discovery`
21. `chore(dds): Fast DDS profile for large image samples`
22. `feat(copilot): speak configured mission parameters from a single launch milestone`
23. `perf(copilot): warm speech session and on-disk phrase cache`
24. `fix(actuators): recover from a stale ros2 daemon before driver connect`
25. `test(bench): isolated-domain sensor relay and abort latency benches`
26. `chore(station): idempotent setup script for GPU drivers, ROS plugins and DDS sysctl`
27. `chore(docs): implementation progress log and station setup notes`

Driver `ros2_bebop_driver`:
1. `feat(video): configure stream resolution, framerate and mode on connect`
2. `perf(video): dedicated publish thread and callback groups`
3. `feat(state): flat trim, magneto calibration, picture and video settings events`
4. `fix(media): use RecordPictureV2`
5. `fix(state): propagate ARSDK timestamps and invalidate on disconnect`
6. `fix(node): catch ARSDK errors in subscription callbacks`
