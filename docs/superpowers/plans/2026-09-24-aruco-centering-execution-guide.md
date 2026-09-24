# Guia de Execucao: ArUco Centering Overshoot and Recovery

> Protocolo de execucao imperativo para implementacao em ambiente de desenvolvimento. O plano completo com o codigo exato, os testes exatos e a rationale de cada decisao esta em `docs/superpowers/plans/2026-09-24-aruco-centering-overshoot-and-recovery.md` -- este guia e o roteiro de execucao por task, o plano e a fonte da verdade tecnica.

---

Implemente o plano `docs/superpowers/plans/2026-09-24-aruco-centering-overshoot-and-recovery.md`, localizado em `/home/jv/ros2_ws/src/mvp_mission_bebop/docs/superpowers/plans/2026-09-24-aruco-centering-overshoot-and-recovery.md`, tarefa por tarefa, na ordem em que estao escritas (Task 1 a Task 6). Nao pule etapas, nao reordene, nao paralelize tarefas que dependem de campos adicionados por tarefas anteriores (Task 3 e Task 4 dependem de `landing_radius_m` da Task 2).

## Escopo exato dos arquivos

Voce vai modificar apenas estes dois arquivos de producao:

- `mvp_mission_bebop/mvp_mission_bebop/controllers/rtl_guidance.py`
- `mvp_mission_bebop/mvp_mission_bebop/parameters.py`

E vai estender estes tres arquivos de teste:

- `mvp_mission_bebop/test/test_aruco_rtl.py`
- `mvp_mission_bebop/test/test_stage3_integration.py`

Nenhum outro arquivo de producao deve ser tocado. Especificamente:

- `mvp_mission_bebop/mvp_mission_bebop/steps/rtl.py` **nao muda**. O `_touchdown()` e o codigo mais testado do pacote e a decisao de pouso continua vindo de `command.settled` -- so o significado interno de `settled` muda, dentro de `rtl_guidance.py`.
- `mvp_mission_bebop/mvp_mission_bebop/controllers/visual_servoing.py` **nao muda**. O Ponto A do briefing (recuperacao de alvo perdido no Estagio 2/3) ja esta implementado corretamente via `ConstantVelocityTracker`; a Task 5 apenas adiciona um teste de regressao que prova isso, sem tocar em codigo de producao.
- `mvp_mission_bebop/mvp_mission_bebop/steps/tracking.py` **nao muda**, pelo mesmo motivo.

Se durante a implementacao voce achar que um desses arquivos precisa mudar, pare e relate o motivo antes de editar -- isso contradiz a analise que fundamentou o plano e precisa ser revalidado, nao contornado silenciosamente.

## Ambiente

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
```

Todo comando `pytest` abaixo roda a partir deste diretorio, dentro deste ambiente.

## Protocolo de execucao por task (TDD estrito, sem excecao)

Para cada uma das seis tasks do plano:

1. Escreva exatamente os testes que a task especifica no "Step 1" (o codigo dos testes ja esta pronto no plano -- copie fielmente, nao parafraseie).
2. Rode o comando de verificacao de falha indicado no "Step 2" da task e confirme que o resultado bate com o esperado descrito no plano (falha por `AttributeError`, falha por assercao, ou passe coincidente -- o plano diz qual em cada caso).
3. Aplique a mudanca de producao exata descrita nos steps seguintes (codigo literal do plano, sem reformular).
4. Rode o comando de verificacao de sucesso da task e confirme 100% de aprovacao.
5. Rode o comando de regressao da suite ampliada indicado ao final da task (ex.: `test_aruco_rtl.py test_rtl_guidance.py test_safety_invariants.py test_recovery_and_touchdown.py`) e confirme 100% de aprovacao antes de avancar para a proxima task.

Nao avance para a proxima task com testes vermelhos. Se uma regressao aparecer, ela deve ser diagnosticada e corrigida dentro da task atual -- nao deixada para a Task 6 consolidar.

## Ordem e comandos de verificacao

**Task 1 -- Teto de frenagem no canal longitudinal (elimina o overshoot frontal).**
Arquivo: `controllers/rtl_guidance.py`, bloco `else` de `ArucoCenteringController.update()` (a branch de correcao ativa). Aplica `braking_velocity()` -- ja usado por `RTLGuidanceController._compute_longitudinal` -- como teto de velocidade no canal longitudinal (body-x) apenas, porque e o eixo que perde o marcador sob o FOV traseiro estreito do `camera_tilt_deg=-80`. O canal lateral fica com autoridade plena.
```bash
python3 -m pytest test/test_aruco_rtl.py -k "never_commands_more_speed or high_kp_configuration_still_cannot" -v
python3 -m pytest test/test_aruco_rtl.py test/test_rtl_guidance.py test/test_safety_invariants.py test/test_recovery_and_touchdown.py -v
```

**Task 2 -- Desacopla o gate de pouso da tolerancia de convergencia (elimina o desperdicio de bateria em ajuste milimetrico).**
Arquivos: `parameters.py` (novo campo `landing_radius_m: float = 0.13`, `centering_settle_cycles` default `4` -> `2`) e `rtl_guidance.py` (`__init__`, nova property `landing_radius_m`, `update()` -- o contador de settle passa a ser julgado contra `landing_radius_m`, nao contra `tolerance_m`). Reescreve tres testes de convergencia existentes que assumiam `settled` implicava residual dentro de `tolerance_m` -- essa e uma mudanca de comportamento intencional, documentada no plano, nao uma fraqueza de teste.
```bash
python3 -m pytest test/test_aruco_rtl.py -k "landing_gate_is_looser or settling_is_authorized_before or converges_from_a_combined_offset or converges_from_every_quadrant or converges_under_pose_noise" -v
python3 -m pytest test/test_aruco_rtl.py -v
python3 -m pytest test/test_aruco_rtl.py test/test_rtl_guidance.py test/test_safety_invariants.py test/test_recovery_and_touchdown.py -v
```
Atencao redobrada em `test_the_gate_requires_consecutive_cycles`, `test_crossing_the_centre_at_speed_does_not_authorize_landing` e `test_leaving_the_tolerance_clears_an_almost_complete_settlement` -- sao os que mais exercitam a contabilidade de settle-cycles que esta task toca.

**Task 3 -- Creep traseiro limitado na perda do marcador (substitui o hover as cegas ate os 25s de timeout).**
Arquivos: `parameters.py` (`reacquire_creep_speed: float = 0.03`, `reacquire_creep_sec: float = 1.0`, `reacquire_creep_range_m: float = 0.30`) e `rtl_guidance.py` (`__init__` rastreia `_last_radial_m`/`_last_speed_mps`/`_lost_elapsed_sec`, `reset()` zera esse estado, `update()` grava o estado a cada ciclo valido, `_coast()` reescrito para condicionar o creep a: perda tolerada, ultima leitura dentro de `reacquire_creep_range_m`, ultima velocidade comandada acima de `settle_max_speed_mps` -- ou seja, so faz creep se a aeronave ainda estava em aproximacao ativa, nunca se ja estava parada e centralizada -- e tempo de perda dentro de `reacquire_creep_sec`).
```bash
python3 -m pytest test/test_aruco_rtl.py -k "creep" -v
python3 -m pytest test/test_aruco_rtl.py test/test_rtl_guidance.py test/test_safety_invariants.py test/test_recovery_and_touchdown.py -v
```
`test_a_dropped_frame_inside_the_tolerance_does_not_restart_the_settlement` e `test_a_sustained_loss_clears_the_settlement_and_holds_station` sao os testes mais sensiveis a essa mudanca -- ambos perdem o marcador a partir de um estado ja parado (`sighting(0.0, 0.0)`), que e exatamente o caso que o gate de velocidade deve impedir de gerar creep.

**Task 4 -- Cobertura de round-trip de configuracao para os campos novos.**
Arquivo: `test/test_aruco_rtl.py`, extensao de `test_the_aruco_block_round_trips_through_mission_config` e `test_a_config_written_before_the_aruco_fields_existed_still_loads`.
```bash
python3 -m pytest test/test_aruco_rtl.py -k "round_trips_through_mission_config or written_before_the_aruco_fields" -v
```

**Task 5 -- Trava de regressao para a recuperacao de oclusao breve do Estagio 2/3 (fixa o Ponto A).**
Arquivo: `test/test_stage3_integration.py`, novo teste `test_a_brief_occlusion_does_not_trigger_reacquisition_or_lose_progress`. Nenhum codigo de producao muda aqui.
```bash
python3 -m pytest test/test_stage3_integration.py -k "brief_occlusion" -v
python3 -m pytest test/test_stage3_integration.py test/test_centering_recovery.py test/test_stage3_parameter_authority.py -v
```
Se esse teste falhar contra o codigo atual sem modificacoes, isso e um defeito real e distinto dos tres pontos deste plano -- pare e investigue, nao enfraqueca a assercao para forcar passagem.

**Task 6 -- Verificacao final da suite completa.**
```bash
python3 -m pytest test/ -v
```
Espera-se 100% de aprovacao, sem novas falhas ou skips alem dos ja existentes e condicionados a hardware/SDK real na secao "against the real SDK and the real OpenCV" de `test_aruco_rtl.py`.

```bash
cd /home/jv/ros2_ws
colcon build --symlink-install --packages-select mvp_mission_bebop
```
Build deve suceder sem novos warnings vindos de `mvp_mission_bebop`.

Por fim, confira por inspecao (sem alterar nada) que:
- `landing_radius_m = 0.13` esta dentro da faixa 0.12-0.15 m pedida no briefing.
- `centering_settle_cycles = 2` ainda exige pelo menos duas amostras consecutivas qualificadas.
- `reacquire_creep_speed = 0.03` esta bem abaixo de `reverse_cruise_velocity = -0.10` e de `max_centering_speed = 0.08`.
- `max_accel_mps2 = 0.25` (inalterado) e o que os testes da Task 1 usam para provar o teto de frenagem em qualquer distancia radial.

## Restricoes inegociaveis (repetidas do plano, porque violar qualquer uma invalida a entrega)

- **Nenhum commit ou push automatico.** Nao rode `git commit` nem `git push` em nenhum momento. Deixe a arvore de trabalho para revisao manual do usuario.
- `vyaw` continua ausente/zero em todo o Estagio 5 e no Estagio 2/3. Nunca adicione saida de yaw.
- `steps/rtl.py::ClosedLoopRTLStep._touchdown` nao muda.
- Toda saturacao de velocidade e feita sobre o vetor resultante, nunca por eixo -- `_saturate_pair` escala, nunca recorta por componente.
- Pouso nunca e autorizado em um ciclo em que o marcador nao foi visto nesse ciclo -- essa invariante (`test_a_landing_is_never_authorized_on_a_cycle_the_marker_was_not_seen`) tem que continuar valendo depois do gate desacoplado.
- Todo teste hoje existente nas seis suites (`test_aruco_rtl.py`, `test_recovery_and_touchdown.py`, `test_safety_invariants.py`, `test_centering_recovery.py`, `test_stage3_integration.py`, `test_rtl_guidance.py`) tem que passar ao final -- qualquer assercao intencionalmente alterada precisa estar explicitamente descrita na task correspondente do plano (ja esta, na Task 2), nunca enfraquecida em silencio.
- Docstrings em estilo NumPy/SciPy, tipagem estrita, zero emojis, zero comentarios redundantes -- o codigo novo tem que ler como se tivesse sido escrito pelo mesmo autor do restante de `rtl_guidance.py`.

## Ao terminar

Rode `git status` e `git diff --stat` para eu revisar o escopo do diff antes de qualquer commit. Nao proponha mensagem de commit ainda -- apenas confirme que a suite completa esta verde e liste os arquivos alterados.
