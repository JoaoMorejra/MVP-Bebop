# Diretrizes de Engenharia e Protocolo de Operacao (CLAUDE.md)

Este documento estabelece as regras mandatorias de engenharia de software, sincronizacao de repositorio, compilacao, validacao e qualidade de codigo para qualquer sessao de desenvolvimento.

---

## 1. Visao Geral do Sistema

O projeto consiste no sistema autonomo completo para o drone **Parrot Bebop 2**, desenvolvido sobre **ROS 2 Jazzy** e **Nectar SDK**, integrado a uma estacao de controle de solo desktop (**BMG - Bebop Mission GUI**).

### Arquitetura de Voo (5 Estagios Deterministicos)
1. **Decolagem e Estabilizacao:** calibracao de sensores em solo, subida e verificacao de teto com protecao anti-climb.
2. **Varredura Retilinea:** cruzeiro com controle cinematico jerk-limited, histerese e deteccao via YOLOv8n.
3. **Aproximacao IBVS:** guiagem visual em malha fechada via projecao geometrica pinhole e controle proporcional com frenagem acoplada.
4. **Inspecao Nadir e Registro Pericial:** inclinacao de camera a 90 deg, verificacao estatistica de imobilidade, captura atomica de evidencias em alta fidelidade e emissao de metadados forenses JSON.
5. **RTL em Malha Fechada e Pouso de Precisao:** cruzeiro reverso, busca de marcador visual (ArUco / AprilTag 36h11), centralizacao fina em coordenadas de camera e touchdown com confirmacao odometrica multivariada.

---

## 2. Estrutura do Repositorio e Sincronizacao Multi-Dispositivo

O projeto adota a arquitetura de **Monorepo**:
```text
MVP-Bebop/
├── mvp_mission_bebop/        # Pacote ROS 2 / Python (controladores, steps, runner, telemetria)
├── bebop_mission_control/    # Estacao de solo Desktop (Electron + React + TypeScript + Tailwind)
├── scripts/                  # Scripts executaveis (launcher bmg, utilitario de teste de marcadores)
├── test/                     # Suite de testes unitarios, simulacao cinematica e contratos IPC
├── setup.py                  # Metadados e entrypoints do pacote ROS 2
├── package.xml               # Manifesto do pacote ament_python
└── CLAUDE.md                 # Este manual operacional
```

### Protocolo Obrigatorio de Handover entre Computadores
1. **Ao Iniciar Qualquer Sessao (Check-in Obrigatorio):**
   - Sempre executar `git status` e `git pull origin main` antes de ler, planejar ou alterar codigo.
   - Validar se o ambiente e os testes estao passando (`python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py`).
2. **Durante a Sessao:**
   - Todo arquivo novo ou alterado DEVE pertencer a arvore rastreada pelo Git.
   - Nenhuma biblioteca ou modulo deve ser mantido solto fora do repositorio.
3. **Ao Encerrar a Sessao (Check-out Obrigatorio):**
   - Rodar a suite de testes relevante.
   - Dividir as alteracoes em commits atomicos, limpos e granulares.
   - Enviar todas as alteracoes para o repositorio remoto via `git push origin main`.
   - Garantir que `git status` esteja 100% limpo antes de encerrar.

---

## 3. Compilacao, Ambiente e Execucao

### Ambiente Python e ROS 2
- O ambiente canonico e ativado atraves de:
  ```bash
  source /home/jv/ros2_ws/bin/nectar-activate
  ```
- A compilacao do pacote ROS 2 DEVE utilizar `--symlink-install` para evitar discrepancias entre o codigo fonte e a pasta `install/`:
  ```bash
  colcon build --symlink-install --packages-select mvp_mission_bebop
  ```

### Estacao de Solo (BMG Frontend)
- Local: `bebop_mission_control/`
- Build de producao:
  ```bash
  npm run build
  ```
- O build do ROS 2 ignora o frontend gracas a presenca do arquivo `COLCON_IGNORE`.

### Testes Automatizados
- Testes de Contrato GCS <-> Backend:
  ```bash
  python3 -m pytest test/test_contracts.py
  ```
- Testes do Modulo de RTL e Deteccao de Marcadores:
  ```bash
  python3 -m pytest test/test_aruco_rtl.py
  ```
- Execucao completa da suite de testes:
  ```bash
  python3 -m pytest test/
  ```

---

## 4. Padroes de Codigo e Diretrizes de Engenharia

Toda contribuicao deve atender ao padrao de engenharia de software senior do estado da arte:

### Tolerancia Zero a Caracteristicas de IA / LLM
- **Sem Emojis:** E estritamente proibido o uso de emojis em arquivos de codigo, comentarios, mensagens de log, documentacao ou mensagens de commit.
- **Sem Comentarios Redundantes ou Obvios:** Comentarios explicativos de acoes triviais (ex.: `# importa modulo`, `# cria variavel`, `# faz loop`) nao sao permitidos.
- **Comentarios Tecnicos Justificados:** Comentarios sao reservados exclusivamente para justificar fisica de controle, equacoes cinematicas, restricoes do hardware Bebop 2 ou acordos de sincronismo IPC.
- **Sem Floreios:** Mensagens de commit e saidas de terminal devem ser secas, tecnicas, formais e objetivas.

### Qualidade e Legibilidade de Codigo
- **Tipagem Estrita:** Uso de `typing` (Type Hints) rigoroso em Python (`Optional`, `Union`, `Tuple`, `Dict`, `Final`) e TypeScript estrito no frontend (`strict: true`, sem uso indiscriminado de `any`).
- **Arquitetura Desacoplada:**
  - `controllers/`: matematica pura, funcoes cinematicas puras e leis de controle. Nao acessam clocks de parede nem nos ROS diretamente.
  - `steps/`: sequencia e transicao de estados.
  - `actuators/`: proxies de comando e simuladores cinematicos.
  - `telemetry/`: supervisores de seguranca, medicao de monotonia e fala/IPC.
- **Preservacao do Contrato com o GCS:**
  - O `mission.py` loga eventos estruturados em `stdout` (ex.: marcadores `[STEP N: ...]`), os quais sao interceptados pelo processo principal do Electron (`electron/main.cjs`).
  - O arquivo `mission_config.json` e o store compartilhado de parametros e seu esquema de serializacao deve permanecer estritamente compativel.

### Padrao de Commits
- Adotar o padrao **Conventional Commits** em ingles tecnico:
  - `feat(...)`: nova funcionalidade
  - `fix(...)`: correcao de bug
  - `refactor(...)`: reestruturacao sem mudanca funcional
  - `test(...)`: adicao ou refatoracao de testes
  - `perf(...)`: melhoria de desempenho
  - `chore(...)`: manutencao de build ou tooling
- Commits atomicos e bem particionados: cada commit deve representar uma unidade de trabalho coerente e testavel.
