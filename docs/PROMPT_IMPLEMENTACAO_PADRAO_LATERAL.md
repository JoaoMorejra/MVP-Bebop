# Prompt para o Claude Code: implementar o padrao de voo lateral

Abra o Claude Code na raiz do monorepo e cole o bloco "Inicio":

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop && claude
```

---

## Inicio

```text
Voce vai implementar, do comeco ao fim, o plano em
docs/superpowers/plans/2026-10-08-lateral-flight-pattern-refactor.md
(Tasks 0, 1, 2, 3, 4, 5, 6, 7, 7B, 8, 9, 10).

Antes de qualquer acao:
1. Leia por inteiro, nesta ordem: CLAUDE.md, o plano acima (Global Constraints,
   Decisoes de Arquitetura D1 a D7, File Structure, Review Focus e todas as tasks).
2. Crie docs/IMPLEMENTACAO_PROGRESSO_LATERAL.md com uma linha por task e o estado
   "pendente". Atualize ao concluir cada task. Se o arquivo ja existir, retome do
   ponto registrado.
3. Execute a Task 0 (check-in e linha de base) antes de editar qualquer arquivo.

Regras que valem acima de qualquer outra instrucao:
- PROIBIDO ARMAR MOTORES. Nunca publique nem dispare takeoff, /bebop/takeoff, PCMD,
  /bebop/cmd_vel nao nulo, mission.py --fly, nem "Iniciar Missao" fora do emulador.
  Ensaios so com mission.py --no-fly ou com o emulador (test/support/fake_bebop) em
  ROS_DOMAIN_ID isolado, conferindo "Publisher count: 0" em /bebop/{takeoff,cmd_vel,land}
  no dominio do driver.
- nectar-sdk e fonte da verdade e nao e editado. Inspecione-o antes de cada task
  (CLAUDE.md secao 2). Convencao ja verificada: Twist.linear.y positivo = esquerda (FLU).
- NAO FACA COMMIT NEM PUSH sem minha autorizacao explicita. Deixe tudo no working tree.
  Ao fim de cada task, relate os arquivos alterados e a mensagem de commit proposta
  (Conventional Commits em ingles tecnico) e continue; so commite quando eu autorizar,
  e apenas o escopo que eu nomear.
- A arvore esta SUJA com trabalho de outras sessoes (parameters.py, config_audit.py,
  mission.py, steps/*.py, telemetry/odometry.py, controllers/altitude_hold.py, fixtures de
  teste, e arquivos NAO rastreados como estimation/platform_monitor.py e
  parameter_conflicts.py). Construa sobre eles. Nunca rode git checkout, stash, reset,
  clean ou pull que sobrescreva arquivos. Rode `git status` e releia cada arquivo
  imediatamente antes de edita-lo, porque outras sessoes podem mudar a arvore. Em
  bebop_mission_control/ edite somente o que a Task 8 pede, por cima do estado atual.
- Estilo do CLAUDE.md: tipagem estrita, docstrings NumPy/reST estilo Black Bee, zero emoji,
  zero comentario obvio, comentarios so para fisica de controle, cinematica ou limites do
  Bebop 2.

Decisoes de produto vinculantes (nao reabrir):
- Varredura a ESQUERDA (vy > 0, vx = 0, vyaw = 0) na Velocidade de Deslocamento
  (kinematics.forward_cruise_velocity). Retorno a DIREITA (vy < 0, vx = 0) com o mesmo
  modulo. Gimbal na varredura em gimbal.search_tilt_deg.
- Stage 3 so move vy; nao move o gimbal; termina travando (vx = vy = 0) ao centralizar
  lateralmente com velocidade estabilizada.
- Stage 4: aeronave em pairado absoluto; so o gimbal se move (lei de apontamento da D3),
  depois a captura forense.
- O takeoff e sobre o totem de 1 m (60x60 cm, borda a 0,30 m do ponto de decolagem). A
  conservacao de altura e a diferenciacao da altura do solo no meio do voo (guarda de borda
  do altitude hold, reentrada, height_above_floor_m, limiar de pouso fora do totem) devem ter
  o MESMO efeito do codigo atual na saida pela esquerda e no retorno pela direita. A Task 7B
  prova isso com testes de paridade; se um deles falhar, corrija a causa na origem, nunca
  relaxe o teste.
- Nomes de parametros, esquema de mission_config.json e marcadores [STEP N: ...] nao mudam.

Linha de base conhecida (arvore viva em 2026-10-08, sem os voos emulados):
10 falhas pre-existentes, 1592 passando. As falhas sao:
test_announcer_calls::test_standalone_runs_still_speak_through_the_local_announcer,
test_aruco_rtl::test_sighting_the_marker_leads_to_centering_and_a_landing,
test_driver_discovery::test_a_still_failing_sdk_after_the_restart_fails,
test_inspection_hover (3: station_keeping_continues..., inference_stops_at_the_capture,
a_hover_with_nothing_in_view_keeps_watching),
test_recovery_and_touchdown (4: normal_landing, firmware_that_ignores_land,
confirmed_touchdown_reports_its_outcome[True] e [False]).
Confirme isso na Task 0 antes de mexer em codigo; falhas pre-existentes nao sao corrigidas
aqui, mas as de test_inspection_hover e test_aruco_rtl precisam ser reavaliadas quando as
Tasks 6 e 7 reescreverem esses arquivos (registre se continuam falhando pelo mesmo motivo).

Ordem de execucao (cada task: teste que falha primeiro, implementacao minima, rodar os
testes afetados, suite sem voos emulados ao fim da task):
  0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 7B -> 8 -> 9 -> 10
Os voos emulados (test_fly_path_emulated.py, test_fly_path_totem.py) ficam vermelhos
de proposito entre a Task 1 e a Task 7B e so voltam a ser exigidos na 7B e na 9.
Comando da suite por task:
  source /home/jv/ros2_ws/bin/nectar-activate
  python3 -m pytest test/ -q -p no:cacheprovider \
    --ignore=test/test_fly_path_emulated.py --ignore=test/test_fly_path_totem.py
As tests obsoletas sao removidas ou migradas pela task que as quebra (nao adie para a 9).

Recomendacao de execucao: use subagentes (superpowers:subagent-driven-development), um
por task, com revisao de spec e de qualidade entre elas, mantendo a regra de nao commitar.
As Tasks 4, 6 e 7 tem julgamento de projeto e merecem o modelo mais capaz.

Ao concluir tudo, entregue: resumo tecnico por modulo, relatorio da suite (totais, falhas
pre-existentes por nome, voos emulados), o log do ensaio de bancada --no-fly com a
sequencia decolagem -> varredura a esquerda -> rastreio lateral -> foto com gimbal ->
retorno a direita -> pouso ArUco, os numeros de perda de altura na saida e na reentrada do
totem da Task 7B, os riscos abertos do plano e a proposta de commits atomicos. Depois pergunte se autorizo os commits.
```
