Implemente integralmente o plano em `/home/jv/ros2_ws/src/mvp_mission_bebop/CLAUDE_CODE_IMPLEMENTATION_PLAN.md`, tarefas T1 a T13, na ordem do arquivo. Leia o plano inteiro uma vez e o `CLAUDE.md` do repo antes de comecar. O plano e a fonte da verdade; nao amplie escopo.

PROIBIDO: `git commit`, `git push`, `git add`, `git stash`, `git reset`, `git checkout -- <arquivo>`, `git rebase`, `git merge`. Deixe tudo na working tree. Nunca reverta, formate ou apague alteracoes que nao sejam suas (a arvore tem dezenas de arquivos sujos de outras sessoes). Nao edite `mission_config.json`.

Modo de trabalho (prioridade: fluidez e economia de tokens):
- Execute tudo de forma autonoma e continua, sem pedir confirmacao entre tarefas.
- Nao emita mensagens de progresso, narracao, resumo de etapa nem "agora vou fazer X". Trabalhe em silencio ate o fim.
- So escreva ao usuario se houver uma decisao que ele precise tomar e que o plano nao resolva. Nesse caso, uma pergunta curta com as opcoes e o default recomendado. Caso contrario, decida pelo default do plano (secao 3) e siga.
- Hunks alheios no mesmo arquivo: edite o minimo necessario por cima, sem tocar nos hunks dos outros. So pare e pergunte se a mesma linha/funcao que voce precisa alterar estiver sendo modificada por outra sessao de forma incompativel.
- Economia: leia so o trecho necessario (offset/limit), localize por simbolo (grep) em vez de reler arquivos, nao releia o que acabou de editar, rode apenas os testes da tarefa durante o desenvolvimento e a suite completa so na T13. Prefira executar voce mesmo; use subagentes apenas para trabalho independente e paralelo de grande volume, com prompt curto e pedindo resposta de uma linha.
- TDD conforme o plano: teste que falha, implementacao minima, teste que passa.

Regras tecnicas obrigatorias (detalhes no plano, secoes 0 e Global Constraints):
- Todo comando Python/pytest prefixado por `source /home/jv/ros2_ws/bin/nectar-activate &&`.
- Antes de qualquer teste emulado: `ps -eo args | grep -E "fake_bebop|mission.py" | grep -v grep` deve estar vazio; um lote emulado por vez. Falha de timing isolada: rode sozinha antes de chamar de regressao.
- Linha de base: copie a arvore para `.superpowers/baseline-refino-voo/` (com symlink de `yolov8n.pt` e `yolov8n_openvino_model/`) e registre os resultados de `python3 -m pytest test/ -q` e `npx vitest run && npm run typecheck` antes de alterar codigo.
- Sem emojis, sem comentarios obvios, docstrings NumPy, tipagem estrita, estilo do codigo vizinho. `nectar-sdk` somente leitura. Nao altere `MILESTONE_KEYS`, marcadores `[STEP N: ...]` nem remova chaves de configuracao.
- Respostas do usuario ja incorporadas ao plano: proa do nudge 45 graus; aviso informativo dos 10% mantido com failsafe desligado; nudge ativo no Stage 4 e suspenso no pouso de precisao; `rtl.camera_offset_forward_m = 0.115`, `camera_offset_left_m = 0.0`.

Ao terminar T13 (e somente entao), envie UMA mensagem final curta, no formato do relatorio da secao 8 do plano, limitada a: arquivos criados/alterados (lista compacta), resultado dos testes antes vs depois (numeros), o que nao foi rodado e por que, e pendencias que dependam do usuario (checagens de campo da secao 7, aprovacao do estimador de altitude conforme T5.6). Informe que nada foi commitado e liste a divisao de commits proposta apenas por numero e titulo.
