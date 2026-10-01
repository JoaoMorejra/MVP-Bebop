# Prompts para o Claude Code (CLI)

Abra o Claude Code na raiz do monorepo (`cd <ws>/src/mvp_mission_bebop && claude`) e cole o bloco
"Início". Depois de qualquer reboot pedido pela sessão, abra uma nova sessão no mesmo diretório e cole o
bloco "Retomada".

---

## Início

```text
Você vai implementar, do começo ao fim, o plano em docs/PROMPT_IMPLEMENTACAO_E2E_VIDEO_FINALIZAR_COPILOTO.md,
com base no diagnóstico ao vivo em docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md.

Antes de qualquer ação:
1. Leia por inteiro, nesta ordem: CLAUDE.md, docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md,
   docs/PROMPT_IMPLEMENTACAO_E2E_VIDEO_FINALIZAR_COPILOTO.md. Se docs/IMPLEMENTACAO_PROGRESSO.md existir,
   leia também e retome do ponto registrado.
2. Crie docs/IMPLEMENTACAO_PROGRESSO.md (se não existir) com uma linha por item de cada fase (0, 0B, 1 a 8)
   e o estado "pendente". Atualize ao concluir cada item.
3. Monte a lista de tarefas da sessão com todas as fases e itens do plano.

Regras que valem acima de qualquer outra instrução:
- PROIBIDO ARMAR MOTORES. Nunca publique nem dispare takeoff, /bebop/takeoff, PCMD, /bebop/cmd_vel não
  nulo, mission.py --fly, nem "Iniciar Missão" fora da bancada. Ensaios com o drone conectado só com
  mission.py --no-fly num ROS_DOMAIN_ID isolado, alimentado por relay unidirecional só de sensores, com
  "Publisher count: 0" conferido em /bebop/{takeoff,cmd_vel,land} no domínio do driver.
- nectar-sdk é fonte da verdade e não é editado. Inspecione o SDK antes de cada fase e reutilize o que
  existir. Contorne comportamentos do SDK no pacote da missão.
- ros2_bebop_driver tem 10 arquivos modificados sem commit: construa sobre eles. Nunca rode checkout,
  stash ou reset nesse repositório.
- Decisões de produto D1 (laudo pericial hardcoded e sorteado), D2 (números falados = parâmetros
  configurados da missão corrente, com a condição de disparo real) e D3/D4 (use MX110, iGPU e CPU; a
  MX110 é o alvo preferencial se o benchmark confirmar; você instala todas as dependências) são
  vinculantes.
- Root: teste `sudo -n true`. Se pedir senha, nunca solicite nem digite senha. Gere
  scripts/setup_station.sh (idempotente) e peça para eu rodar `sudo bash scripts/setup_station.sh` no meu
  terminal; depois valide você mesmo.
- Nunca reinicie a máquina. Quando precisar de reboot, registre o ponto de retomada em
  docs/IMPLEMENTACAO_PROGRESSO.md e me peça para reiniciar e colar o prompt "Retomada" de
  docs/PROMPT_INICIO_CLAUDE_CODE.md.
- Não faça commit nem push sem minha autorização explícita. Deixe as mudanças prontas para os commits
  atômicos listados no fim do plano e me peça autorização ao terminar cada bloco de fases.
- Estilo do CLAUDE.md: tipagem estrita, docstrings NumPy/reST, estilo Black Bee, zero emoji, zero
  comentário óbvio, Conventional Commits em inglês técnico.

Ordem de execução:
1. Fase 0 (check-in: git status, git pull origin main, linha de base de testes: 869 pytest, 219 vitest,
   tsc limpo).
2. Fase 0B.1 a 0B.4 (dependências de sistema, driver NVIDIA 580 com pin, OpenCL Intel, plugins ROS,
   sysctl). Pare no 0B.5 e me peça o reboot.
3. Depois do reboot (prompt "Retomada"): 0B.6 a 0B.9 (validação, torch CUDA, builds).
4. Fases 1 (segurança do abort, P0), 2 (trava do Finalizar), 3 (copiloto), 4 (vídeo 30 FPS e
   inferência em GPU), 5 (parâmetros), 6 (telemetria e calibrações com ACK), 7 (arranque), 8 (validação
   final).

Em cada item:
- Escreva o teste que falha antes de implementar, quando o item for testável.
- Rode os testes afetados ao concluir o item e a suíte completa (pytest test/, vitest, tsc) ao concluir
  cada fase.
- Registre no progresso: o que mudou (arquivos), testes rodados com resultado e pendências.
- Drone conectado: quando um item exigir o Bebop ligado e em rede (Fase 4: configuração de vídeo no
  driver, tópico comprimido, FPS real, foto nativa e FTP, calibração x resolução; Fase 6: ACKs, timestamps
  e invalidação no disconnect; Fase 8: validação final), PARE o desenvolvimento nesse ponto. Registre
  "aguardando drone" em docs/IMPLEMENTACAO_PROGRESSO.md e me peça para conectar, lembrando de manter o
  tethering USB para internet. Espere minha confirmação. Depois valide o link você mesmo
  (`ping -c 3 192.168.42.1`, SSID Bebop2-*, `ros2 daemon stop && ros2 daemon start`, driver publicando
  /bebop/camera/image_raw) e retome exatamente do item em que parou. Se o link cair durante o item, pare
  de novo e me peça para reconectar.
- Outros itens que dependam de mim (root sem sudo, reboot, decisão): pare, me peça o que for preciso,
  registre no progresso e retome após minha confirmação.

Ao fim de cada fase, me dê um resumo curto: itens concluídos, testes (números), bloqueios e o que
preciso fazer. Ao fim da Fase 8, entregue o relatório final previsto no plano, com a tabela antes/depois
(FPS por estágio, latências de arranque, abort e fala, device de inferência escolhido e p95), e peça
autorização para os commits.

Comece agora pela Fase 0.
```

---

## Retomada (após reboot ou nova sessão)

```text
Retome a implementação do plano docs/PROMPT_IMPLEMENTACAO_E2E_VIDEO_FINALIZAR_COPILOTO.md.

1. Leia CLAUDE.md, docs/IMPLEMENTACAO_PROGRESSO.md (fonte de verdade do ponto de retomada),
   docs/PROMPT_IMPLEMENTACAO_E2E_VIDEO_FINALIZAR_COPILOTO.md e docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md.
2. Rode git status no monorepo e em ../ros2_bebop_driver e confira que o estado bate com o progresso
   registrado.
3. Se a retomada for depois do reboot da Fase 0B, execute primeiro 0B.6 (validação: nvidia-smi com a
   MX110, NVRM sem mensagem de legado, clinfo, OpenVINO com GPU, grupos render/video, sysctl). Se o
   driver 580 falhou, siga o rollback da 0B.6 e continue só com iGPU e CPU.
4. Continue do próximo item pendente, com as mesmas regras da sessão de início: proibido armar
   motores, SDK não editado, driver sobre a working tree local, D1 a D4 vinculantes, root via
   scripts/setup_station.sh quando `sudo -n` falhar, nunca reiniciar sozinho, nenhum commit ou push sem
   minha autorização, progresso atualizado a cada item. Quando um item exigir o drone conectado, pare, me
   peça para conectar (mantendo o tethering USB para internet), valide o link depois da minha confirmação
   e retome do mesmo item.

Diga em uma linha em que fase e item está retomando e continue.
```
