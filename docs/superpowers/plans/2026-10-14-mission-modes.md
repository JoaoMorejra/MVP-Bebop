# Modos de Missão (Simples, Intermediária, Avançada) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o operador escolha, no pré-voo do BMG, entre três perfis de missão (Simples, Intermediária, Avançada), com o backend executando cada perfil sobre a mesma máquina de 5 estágios e o contrato `[STEP N: ...]` intacto.

**Architecture:** Um valor `mission_mode` (`simple` | `intermediate` | `advanced`, default `advanced`) viaja no documento de parâmetros (`--params-json`, `go` do standby e `mission_config.json`) e é resolvido uma única vez em `mission.py`, depois do `go`. O modo é injetado por construtor nos Steps 2 a 5 (`mode=`), nunca lido ad hoc em vários pontos. O modo Intermediário reduz apenas o Step 5 (pouso no local). O modo Simples é o único que exige rotação: ela é liberada por uma janela de autoridade de yaw (`YawAuthority`), fechada por padrão no proxy e no failsafe, aberta somente pelos Steps 2, 3 e 4 do modo Simples.

**Tech Stack:** Python 3 / ROS 2 Jazzy / Nectar SDK / pytest (backend); Electron + React + TypeScript + Tailwind + Vitest (BMG).

**Spec:** Seção "Especificação dos 3 Modos de Voo" do pedido original, resumida na seção "Especificação de referência" abaixo. Este arquivo é a única fonte que o executor precisa.

---

## Global Constraints

Regras do `/home/jv/ros2_ws/CLAUDE.md` e do repositório, valem para toda tarefa:

- Sem emojis em código, comentários, logs, docs ou mensagens de commit.
- Sem comentários óbvios. Comentário só para física de controle, cinemática, restrição do Bebop 2 ou acordo de sincronismo IPC.
- Docstrings NumPy/reST (`Parameters`, `Returns`, `Raises`), validação defensiva de entrada, `typing` estrito (`Optional`, `Union`, `Final`), TypeScript `strict` sem `any` novo.
- `controllers/` é matemática pura: sem relógio de parede, sem nós ROS, sem I/O. Quem avança o tempo é o Step (`dt` vem de `LoopRate.tick`).
- Contrato GCS: toda execução emite `--- [STEP N: ...] ---` (N de 1 a 5, nessa ordem, prefixo literal `STEP N:`) no stdout. `electron/main.cjs` (`createStepMarkerParser`) depende disso.
- `mission_config.json` mantém esquema retrocompatível: campos novos têm default e `update_from_dict` ignora chaves desconhecidas.
- O padrão do modo Avançado é bit a bit o comportamento atual. Nenhum teste existente pode precisar de alteração de asserção para continuar verde (exceções listadas na Task 1: o fixture de fábrica).
- Nenhum `git commit` / `git push` sem autorização explícita do usuário (memória `no-commit-without-authorization`). Os passos "Checkpoint" abaixo propõem a mensagem de commit; não executam.
- Ambiente: todo comando pytest/mission usa `source /home/jv/ros2_ws/bin/nectar-activate &&` antes. Nunca rodar dois lotes de voo emulado ao mesmo tempo (domínios ROS 80-99 colidem). Subagentes de revisão não devem lançar voo emulado.
- Reuso do `nectar-sdk` (`/home/jv/ros2_ws/src/nectar-sdk`) tem prioridade. Nada neste plano reimplementa algo que o SDK já ofereça: `FilteredPID`, `QuantizedCommandShaper`, `LoopRate`, `Deadline`, `ServoCommand` e o pipeline de percepção são reaproveitados. O SDK não tem lei de yaw por imagem nem janela de autoridade de yaw (verificado), por isso os módulos novos abaixo.
- Convenção de yaw: `vyaw > 0` é anti-horário visto de cima, isto é, giro para a ESQUERDA (REP-103, `geometry_msgs/Twist.angular.z`; o simulador integra `yaw += vyaw * YAW_RATE_PER_UNIT * dt`). `snapshot.yaw` está em radianos, mesma convenção. O sinal no hardware real não foi verificado nesta fase (ver Review Focus).

## Especificação de referência

| Estágio | Avançado | Intermediário | Simples |
|---|---|---|---|
| 1 Decolagem | atual | idêntico | idêntico (grava `initial_yaw_rad`) |
| 2 Varredura | translação lateral esquerda | idêntico | `vx = vy = 0`, giro à esquerda `+vyaw` até a YOLO confirmar |
| 3 Rastreio | PID lateral `vy` | idêntico | PID de yaw `vyaw`, `vx = vy = 0` |
| 4 Inspeção | gimbal ao nadir, foto, hover | idêntico | idêntico, depois yaw volta a `initial_yaw_rad` e gimbal vai a `rtl.camera_tilt_deg` (-80) |
| 5 Pouso | RTL reverso, ArUco, centragem, pouso na base | pouso vertical no local (`_touchdown(at_base=False)`) | só centragem ArUco + pouso (`_touchdown(at_base=True)`) |

Frontend: seletor segmentado acima do `LaunchDial`; folha de parâmetros filtrada por modo; payload de lançamento carrega `mission_mode`.

## Decisões tomadas pelo arquiteto (fora do texto literal do pedido)

Revisar antes de executar; cada uma está refletida nas tarefas.

1. **Yaw é uma exceção escopada, não a remoção do invariante.** Hoje `vyaw` é zerado em três camadas: `BenchtopDroneProxy.move_velocity`, `FailsafeSupervisor.clamp_kinematics` e a convenção dos Steps (a razão é fidelidade da odometria por fluxo óptico). O modo Simples exige rotação, então a Task 2 introduz `YawAuthority` (janela com teto, fecha sozinha, fail-closed). Fora da janela nada muda e os testes existentes (`test_actuator_proxy.py`, `test_safety_invariants.py`) continuam valendo sem edição.
2. **Modo Simples e odometria horizontal.** Durante a rotação a posição por fluxo óptico não é confiável. O modo Simples não usa posição horizontal em nenhum estágio (sem RTL odométrico), por isso o Step 5 do Simples nunca cai em `_legacy_return`: sem sensor ArUco ele pousa no lugar com `at_base=True` (o drone nunca saiu da base).
3. **Narração (copiloto).** Nos modos Simples e Intermediário o marco `mission.rtl_start` NÃO é emitido (diria "iniciando retorno à base"). A fase 5 é narrada por `mission.landing` (`at_base: false` no Intermediário, `true` no Simples) e `mission.touchdown`, que já têm piscinas de frases corretas em `copilotPhrases.ts`. Nenhuma frase nova.
4. **Nomes dos Steps** mudam por modo mantendo o prefixo `STEP N:`. O nome do Avançado permanece idêntico ao atual (testes existentes o comparam).
5. **Retorno do yaw no Step 4 do Simples** ocorre depois do hover de evidência e do ACK nativo da foto, sem avaliação de saúde do sistema (precedente do `_hover`: o heartbeat de frame está suspenso e o Step 5 o rearma na entrada).
6. **Parâmetros ocultos** (modos reduzidos) continuam no documento com o valor guardado; apenas a UI e os bloqueios de lançamento deixam de considerá-los.
7. **Parâmetros de yaw** vivem em `MissionParameters.yaw_pattern` (valores iniciais conservadores, a calibrar em bancada). Só `yaw_pattern.scan_vyaw` aparece na folha de parâmetros, como o slider "Velocidade de Giro", e somente no modo Simples. Os demais ficam só no `mission_config.json`.
8. **Modo Simples troca a "Velocidade de Deslocamento" (`kinematics.forward_cruise_velocity`, sem uso, pois não há translação) pelo slider "Velocidade de Giro"** no cartão Envelope de Voo.
9. **Modo Intermediária remove o botão "Configurações Avançadas"** (os campos de marcador não têm uso). Nos modos Simples e Avançada o botão permanece.
10. **O seletor de modo trava assim que o operador clica em INICIAR** (durante o commit do documento, a contagem e o voo). O documento enviado ao backend é um instantâneo tomado nesse clique, então edições posteriores não o alteram.
11. **Alvo nunca detectado no Simples:** a cadeia continua até o fim, como no Avançado (Step 3 pulado, Step 4 captura referência degradada, yaw restaurado, Step 5 pousa). Sem marcador na centragem, o drone segura posição até `rtl.centering_timeout_sec` e pousa no lugar tratando-o como base.

## Review Focus

Entradas e condições que a especificação implica mas nenhuma tarefa-feature exercita sozinha; cada linha tem teste dono indicado.

1. `mission_mode` ausente, vazio, com caixa diferente (`"Simple"`) ou inválido (`"expert"`) em `mission_config.json`, `--params-json` ou `go`: ausente/vazio vira `advanced`; inválido aborta com `SystemExit` antes de qualquer comando ao drone, nunca voa um modo diferente do pedido. Teste: Task 1.
2. Yaw vazando para fora do modo Simples (um Step de outro modo, ou o Step 5 do Simples, transmitindo `vyaw != 0`) e o último Twist de cada estágio Simples terminando com `vyaw == 0.0` (o Bebop trava o último comando). Teste: Tasks 2, 5, 6, 7, 9.
3. Alvo nunca detectado no Simples (varredura esgota `search_timeout_sec`, Step 3 pulado, Step 4 captura referência degradada): a cadeia 2 a 5 ainda termina com yaw restaurado e pouso. Teste: Task 7 e Task 9.
4. Heading de decolagem: wrap-around (`+170 deg` referência, `-170 deg` atual deve girar -20 deg, não +340 deg), referência ausente (partial run `--stages 4`), e leitura de yaw `NaN`. Teste: Tasks 3 e 7.
5. Marcador ArUco ausente no Step 5 do Simples e `sensor is None`: pouso no lugar, sem `_legacy_return`, sem travar; e Intermediário com ArUco mal configurado (ID inválido) não bloqueia nem falha, pois o marcador não é usado. Teste: Tasks 8 e 10.

Risco aberto, não testável sem hardware: sinal real do yaw do Bebop e latência de rotação com o gimbal em -20 deg. Ver Task 11, passo "Checklist de voo real".

## File Structure

Backend (`/home/jv/ros2_ws/src/mvp_mission_bebop`):

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `mvp_mission_bebop/mission_mode.py` | Create | `MissionMode`, `parse_mission_mode`, `apply_mission_mode`. Sem dependências pesadas. |
| `mvp_mission_bebop/actuators/yaw_authority.py` | Create | `YawAuthority`: janela de autoridade de yaw, fail-closed. |
| `mvp_mission_bebop/controllers/yaw_pattern.py` | Create | `wrap_angle`, `YawRateLimiter`, `YawServoingController`, `YawHeadingController`, `validate_yaw_pattern`. Matemática pura. |
| `mvp_mission_bebop/steps/pipeline.py` | Create | `build_steps(mode)`: fábrica dos 5 steps por modo. |
| `mvp_mission_bebop/parameters.py` | Modify | `YawPatternConfig`, campos `mission_mode` e `yaw_pattern` em `MissionParameters`. |
| `mvp_mission_bebop/blackboard.py` | Modify | `initial_yaw_rad`. |
| `mvp_mission_bebop/actuators/proxy.py` | Modify | proxy consulta `YawAuthority`. |
| `mvp_mission_bebop/telemetry/failsafe.py` | Modify | `yaw_window`, `clamp_kinematics` consulta `YawAuthority`. |
| `mvp_mission_bebop/controllers/visual_servoing.py` | Modify | `ServoCommand.vyaw` (default 0.0). |
| `mvp_mission_bebop/steps/takeoff.py` | Modify | grava `initial_yaw_rad`. |
| `mvp_mission_bebop/steps/search.py` | Modify | Step 2 por modo. |
| `mvp_mission_bebop/steps/tracking.py` | Modify | Step 3 por modo. |
| `mvp_mission_bebop/steps/inspection.py` | Modify | Step 4 por modo. |
| `mvp_mission_bebop/steps/rtl.py` | Modify | Step 5 por modo. |
| `mvp_mission_bebop/context.py` | Modify | anotação de tipo de `visual_controller`. |
| `mvp_mission_bebop/mission.py` | Modify | `--mode`, resolução do modo, `YawAuthority`, controlador e steps por modo. |
| `test/fixtures/mission_factory_document.json` | Modify | acrescenta `mission_mode` e `yaw_pattern`. |
| `test/test_mission_modes.py` | Create | roteamento e contrato dos 3 modos. |
| `test/test_yaw_authority.py` | Create | janela de yaw. |
| `test/test_yaw_pattern.py` | Create | controladores de yaw. |
| `test/test_simple_mode_steps.py` | Create | Steps 2 a 5 do Simples e Step 5 do Intermediário. |
| `test/test_mission_modes_emulated.py` | Create | voo emulado dos 3 modos (`--fly` no emulador e `--no-fly`). |

Frontend (`bebop_mission_control/`):

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `src/lib/missionMode.ts` | Create | tipos, descritores, `readMissionMode`, rótulos de estágio, `isPathActive`. |
| `src/lib/missionMode.test.ts` | Create | testes da lib. |
| `src/lib/parameterSchema.ts` | Modify | metadado `modes` por parâmetro, `groupsForMode`, `mission_mode` em `TRACKED_PATHS`. |
| `src/lib/launchDocument.ts` | Modify | `launchBlockers(doc)` considera só caminhos ativos no modo. |
| `src/lib/parameterConflicts.ts` | Modify | `activeConflicts(doc, mode)` filtra conflitos cujos caminhos estão todos inativos. |
| `src/components/preflight/MissionModeSelector.tsx` | Create | controle segmentado (radiogroup). |
| `src/components/preflight/MissionModeSelector.test.tsx` | Create | teste do seletor. |
| `src/components/preflight/LaunchDial.tsx` | Modify | slot `above` renderizado acima do botão. |
| `src/components/preflight/PreflightScreen.tsx` | Modify | injeta o seletor no slot. |
| `src/components/preflight/ParameterSheet.tsx` | Modify | filtra grupos e itens por modo. |
| `src/hooks/useMissionParameters.ts` | Modify | completa `yaw_pattern.scan_vyaw` ausente a partir dos padrões de fábrica; conflitos filtrados por modo. |
| `src/components/cockpit/StageBar.tsx`, `src/types/mission.ts`, `electron/main.cjs` | Modify | rótulo do estágio 5 por modo. |
| `CLAUDE.md` | Modify | registra os três modos e a exceção escopada de yaw (Task 14, Step 7b). |
| `src/App.tsx` | Modify | passa o modo ao cockpit, trava o seletor a partir do clique em INICIAR e usa `activeConflicts`/`launchBlockers` com modo. |
| testes `LaunchDial.test.tsx`, `ParameterSheet.test.tsx`, `launchDocument.test.ts` | Modify | novos casos. |

---

## Task 0: Linha de base e coordenação

**Files:** nenhum arquivo de produto.

A árvore está suja com trabalho de outras sessões (`git status` mostra `ParameterSheet.tsx`, `PreflightScreen.tsx`, `parameters.py`, `mission.py`, `App.tsx` e outros modificados). Memória do projeto: sessões concorrentes editam os mesmos arquivos.

- [ ] **Step 1: Registrar o estado e congelar a linha de base**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
git status --short > /tmp/claude-1000/modes_baseline_status.txt
git stash list | head -3
mkdir -p .superpowers && rsync -a --delete --exclude node_modules --exclude dist --exclude '*.jpg' --exclude '*.png' --exclude '__pycache__' ./ .superpowers/baseline-modes/
ln -sf "$PWD/mvp_mission_bebop/yolov8n.pt" .superpowers/baseline-modes/mvp_mission_bebop/yolov8n.pt
ln -sfn "$PWD/mvp_mission_bebop/yolov8n_openvino_model" .superpowers/baseline-modes/mvp_mission_bebop/yolov8n_openvino_model
```

Expected: cópia congelada em `.superpowers/baseline-modes/` (diretório git-ignorado). Se `.superpowers/` não estiver no `.gitignore`, avisar o usuário em vez de editá-lo.

- [ ] **Step 2: Rodar a suíte-base e anotar o que já falha**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/ -x -q --ignore=test/test_fly_path_emulated.py --ignore=test/test_fly_path_totem.py 2>&1 | tail -15
```

Expected: anotar o resultado. Falhas pré-existentes (ex.: os testes sensíveis a carga listados na memória `emulator-runs-and-subagent-env`: `test_inspection_hover`, `test_recovery_and_touchdown`, `test_takeoff`, `test_fake_bebop_interlocks`) são re-executadas isoladas antes de serem classificadas como pré-existentes.

- [ ] **Step 3: Rodar a suíte frontend-base**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control && npm test 2>&1 | tail -15 && npm run typecheck 2>&1 | tail -10
```

Expected: anotar falhas pré-existentes. Se a árvore suja já quebra algo que este plano toca (`ParameterSheet.test.tsx`, `App.launchParams.test.tsx`), parar e perguntar ao usuário antes de prosseguir.

- [ ] **Step 4: Antes de editar qualquer arquivo compartilhado**, reexecutar `git status --short` e reler o arquivo (regra `concurrent-sessions-share-tree`). Se outro agente tiver mudado um arquivo da tabela acima desde a leitura, reaplicar as edições sobre o conteúdo atual, nunca sobre o trecho copiado deste plano.

---

## Task 1: `MissionMode`, parâmetros e roteamento por CLI/documento

**Files:**
- Create: `mvp_mission_bebop/mission_mode.py`
- Modify: `mvp_mission_bebop/parameters.py` (classe `MissionParameters` e nova `YawPatternConfig`), `mvp_mission_bebop/mission.py` (`parse_arguments`, `resolve_parameters`, bloco pós-`_await_go`)
- Modify: `test/fixtures/mission_factory_document.json`
- Test: `test/test_mission_modes.py`

**Interfaces:**
- Produces: `MissionMode` (str Enum: `SIMPLE="simple"`, `INTERMEDIATE="intermediate"`, `ADVANCED="advanced"`); `DEFAULT_MODE: Final[MissionMode]`; `parse_mission_mode(value, default=DEFAULT_MODE) -> MissionMode` (lança `ValueError` para string inválida, `TypeError` para tipo não-string); `apply_mission_mode(params, cli_mode: Optional[str]) -> MissionMode` (normaliza `params.mission_mode` para o valor canônico minúsculo). `MissionParameters.mission_mode: str = "advanced"`. `MissionParameters.yaw_pattern: YawPatternConfig`.

- [ ] **Step 1: Escrever os testes que falham**

`test/test_mission_modes.py`:

```python
"""Mission profile selection: parsing, precedence and the factory document."""

from __future__ import annotations

import json
import os

import pytest

from mvp_mission_bebop.mission_mode import (
    DEFAULT_MODE,
    MissionMode,
    apply_mission_mode,
    parse_mission_mode,
)
from mvp_mission_bebop.parameters import MissionParameters

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mission_factory_document.json")


def test_the_default_profile_is_the_full_mission():
    assert DEFAULT_MODE is MissionMode.ADVANCED
    assert MissionParameters().mission_mode == "advanced"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_absent_or_blank_value_is_the_default(raw):
    assert parse_mission_mode(raw) is MissionMode.ADVANCED


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("simple", MissionMode.SIMPLE),
        ("Simple", MissionMode.SIMPLE),
        (" INTERMEDIATE ", MissionMode.INTERMEDIATE),
        ("advanced", MissionMode.ADVANCED),
        (MissionMode.SIMPLE, MissionMode.SIMPLE),
    ],
)
def test_parsing_is_case_and_whitespace_insensitive(raw, expected):
    assert parse_mission_mode(raw) is expected


def test_an_unknown_name_is_refused_not_defaulted():
    with pytest.raises(ValueError, match="expert"):
        parse_mission_mode("expert")


@pytest.mark.parametrize("raw", [3, 1.5, ["simple"], True])
def test_a_non_string_is_a_type_error(raw):
    with pytest.raises(TypeError):
        parse_mission_mode(raw)


def test_the_cli_flag_wins_over_the_stored_value():
    params = MissionParameters()
    params.mission_mode = "advanced"
    assert apply_mission_mode(params, "simple") is MissionMode.SIMPLE
    assert params.mission_mode == "simple"


def test_the_stored_value_is_normalised_in_place():
    params = MissionParameters()
    params.mission_mode = " Intermediate "
    assert apply_mission_mode(params, None) is MissionMode.INTERMEDIATE
    assert params.mission_mode == "intermediate"


def test_an_invalid_stored_value_raises_instead_of_flying_the_default():
    params = MissionParameters()
    params.mission_mode = "expert"
    with pytest.raises(ValueError):
        apply_mission_mode(params, None)


def test_the_launch_document_carries_the_mode_through_update_from_dict():
    params = MissionParameters()
    params.update_from_dict({"mission_mode": "simple", "yaw_pattern": {"scan_vyaw": 0.2}})
    assert params.mission_mode == "simple"
    assert params.yaw_pattern.scan_vyaw == 0.2


def test_the_factory_document_fixture_lists_the_new_fields():
    with open(_FIXTURE, encoding="utf-8") as handle:
        document = json.load(handle)
    assert document["mission_mode"] == "advanced"
    assert "yaw_pattern" in document
    assert document == MissionParameters.factory().to_dict()
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_modes.py -v 2>&1 | tail -15
```

Expected: FAIL com `ModuleNotFoundError: No module named 'mvp_mission_bebop.mission_mode'`.

- [ ] **Step 3: Criar `mvp_mission_bebop/mission_mode.py`**

```python
"""Mission profiles.

All three profiles fly the same five deterministic stages and emit the same
``[STEP N: ...]`` markers; they differ in what stages 2 to 5 do:

* ``advanced``: the complete mission, ending with the ArUco return and landing
  on the launch pad.
* ``intermediate``: stages 1 to 4 as in ``advanced``; stage 5 lands vertically
  where the inspection ended.
* ``simple``: the airframe never translates. Stage 2 and 3 rotate about the
  vertical axis, stage 4 restores the takeoff heading after the capture and
  stage 5 centres on the pad marker and lands.

This module depends on nothing but the standard library so that the argument
parser, the parameter store and the tests can use it without importing the
flight stack.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Final, Optional, Union


class MissionMode(str, Enum):
    """The three selectable mission profiles."""

    SIMPLE = "simple"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"

    @property
    def translates(self) -> bool:
        """Whether the airframe moves horizontally in stages 2 and 3."""
        return self is not MissionMode.SIMPLE

    @property
    def returns_to_base(self) -> bool:
        """Whether stage 5 flies the reverse leg to the launch pad."""
        return self is MissionMode.ADVANCED


#: Profile flown when nothing selects one. The complete mission.
DEFAULT_MODE: Final[MissionMode] = MissionMode.ADVANCED


def parse_mission_mode(
    value: Union[str, MissionMode, None], default: MissionMode = DEFAULT_MODE
) -> MissionMode:
    """Read a mission profile from a configuration value.

    Parameters
    ----------
    value : str, MissionMode or None
        Raw value. ``None`` and a blank string select ``default``. A string is
        matched case-insensitively after trimming.
    default : MissionMode
        Profile returned when ``value`` is absent.

    Returns
    -------
    MissionMode
        The selected profile.

    Raises
    ------
    TypeError
        If ``value`` is neither a string nor a :class:`MissionMode`.
    ValueError
        If a non-blank string names no profile. An unknown name is never
        replaced by the default: flying a profile the operator did not ask for
        is worse than refusing to start.
    """
    if value is None:
        return default
    if isinstance(value, MissionMode):
        return value
    if not isinstance(value, str):
        raise TypeError(f"mission mode must be a string, got {type(value).__name__}")
    token = value.strip().lower()
    if not token:
        return default
    try:
        return MissionMode(token)
    except ValueError as error:
        names = ", ".join(mode.value for mode in MissionMode)
        raise ValueError(f"mission mode '{value}' is not one of: {names}") from error


def apply_mission_mode(params: Any, cli_mode: Optional[str]) -> MissionMode:
    """Resolve the profile of a run and write its canonical name back.

    Precedence: the ``--mode`` flag, then ``params.mission_mode`` (which already
    holds the parameter store merged with ``--params-json`` or the standby
    ``go`` document).

    Parameters
    ----------
    params : MissionParameters
        Mutated: ``params.mission_mode`` becomes the lower-case canonical name,
        which is what gets persisted to ``mission_config.json``.
    cli_mode : str or None
        Value of ``--mode``.

    Returns
    -------
    MissionMode

    Raises
    ------
    TypeError, ValueError
        As :func:`parse_mission_mode`.
    """
    mode = parse_mission_mode(cli_mode if cli_mode is not None else getattr(params, "mission_mode", None))
    params.mission_mode = mode.value
    return mode
```

- [ ] **Step 4: Acrescentar `YawPatternConfig` e os campos em `parameters.py`**

Inserir a nova dataclass antes de `class MissionParameters` (localizar por símbolo; os números de linha mudam com sessões concorrentes):

```python
@dataclass
class YawPatternConfig:
    """Yaw laws of the Simple mission profile (``mission_mode == "simple"``).

    Yaw rate is a normalized command in [-1, 1]; a positive value turns left
    (counter-clockwise seen from above). One unit is about
    ``DeadReckoningConfig.yaw_rate_per_unit_rad_s`` rad/s on this airframe.
    The values below are conservative starting points to be tuned on the bench;
    none of them is an operator parameter.
    """

    #: Yaw command of the Stage 2 scan, normalized. 0.15 is roughly 15 deg/s:
    #: slow enough that the detector, which runs near 10 Hz, sees every
    #: bearing for several frames, and one full turn takes about 24 s, inside
    #: the default ``timeouts.search_timeout_sec`` of 30 s.
    scan_vyaw: float = 0.15
    #: Slew limit of the scan command, normalized units per second. Bounds the
    #: angular acceleration so the camera does not smear on the start and stop.
    scan_ramp_per_sec: float = 0.30
    #: Ceiling of the yaw authority window, normalized. Must be at least every
    #: other yaw magnitude in this section.
    max_vyaw: float = 0.30
    #: Stage 3 yaw PID on the normalized horizontal pixel error.
    kp: float = 0.45
    ki: float = 0.0
    kd: float = 0.03
    #: Output saturation of the Stage 3 PID, normalized.
    pid_output_limit: float = 0.25
    #: Output deadband of the Stage 3 PID, normalized.
    deadband: float = 0.01
    #: Smallest yaw command the airframe reproduces; sub-floor demands are
    #: duty-cycled by ``QuantizedCommandShaper`` rather than raised to the floor.
    min_effective_vyaw: float = 0.05
    #: Driver quantization step (``int8(v * 100)``).
    quantization_step: float = 0.01
    #: Rate at which Stage 3 searches toward the side the target left from.
    reacquire_vyaw: float = 0.08
    #: Heading restore (end of Stage 4): proportional and derivative gains on
    #: the wrapped heading error in radians.
    return_kp: float = 0.50
    return_kd: float = 0.02
    #: Output saturation of the heading restore, normalized.
    return_max_vyaw: float = 0.25
    #: Heading error inside which the restore counts as settled, degrees.
    return_tolerance_deg: float = 4.0
    #: Consecutive in-tolerance cycles required to end the restore.
    return_settle_cycles: int = 4
    #: Window of the heading restore, seconds. On expiry the mission continues
    #: with the heading reached.
    return_timeout_sec: float = 12.0
```

Em `MissionParameters`, adicionar (junto de `no_fly`):

```python
    mission_mode: str = "advanced"
    yaw_pattern: YawPatternConfig = field(default_factory=YawPatternConfig)
```

Documentar `mission_mode` no docstring da classe: "Mission profile: ``simple``, ``intermediate`` or ``advanced``. Resolved once by ``mission_mode.apply_mission_mode``."

- [ ] **Step 5: Adicionar `--mode` e a resolução em `mission.py`**

Imports: `from mvp_mission_bebop.mission_mode import MissionMode, apply_mission_mode`.

Em `parse_arguments`, junto de `--stages`:

```python
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in MissionMode],
        default=None,
        help=(
            "Mission profile: simple (yaw-only search, no translation), intermediate "
            "(full mission, lands where the inspection ends) or advanced (full mission "
            "with the ArUco return). Overrides mission_mode in the parameter store and "
            "in --params-json (default: advanced)."
        ),
    )
```

Em `resolve_parameters`, imediatamente antes de `_validate_countdown(params)`:

```python
    try:
        apply_mission_mode(params, getattr(args, "mode", None))
    except (TypeError, ValueError) as error:
        raise SystemExit(f"mission_mode: {error}") from error
```

No bloco pós-`_await_go` (depois de `params.update_from_dict(command["params"])` e antes de `_validate_countdown(params)`), a mesma chamada, com a mesma conversão para `SystemExit` (o `go` pode trazer um modo diferente do documento de standby; o modo não está em `CRITICAL_PATHS` de `engine/launch.py` de propósito, porque nada preparado antes do `go` depende dele).

- [ ] **Step 6: Regenerar o fixture de fábrica e conferir o diff**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
source /home/jv/ros2_ws/bin/nectar-activate && python3 - <<'EOF'
import json
from mvp_mission_bebop.parameters import MissionParameters
path = "test/fixtures/mission_factory_document.json"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(MissionParameters.factory().to_dict(), handle, indent=2)
    handle.write("\n")
EOF
git diff --stat test/fixtures/mission_factory_document.json
git diff test/fixtures/mission_factory_document.json | grep '^[-+]' | grep -v '^[-+][-+]' | head -40
```

Expected: o diff só ADICIONA `mission_mode` e o objeto `yaw_pattern`. Se aparecerem outras mudanças (formatação, chaves de outras sessões), restaurar o fixture com `git checkout -- test/fixtures/mission_factory_document.json`, inserir manualmente apenas as duas chaves novas na posição correta e repetir.

- [ ] **Step 7: Rodar os testes**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_modes.py test/test_shared_fixtures.py test/test_parameter_conflicts.py test/test_config_audit.py test/test_mission_parameters.py test/test_contracts.py -q 2>&1 | tail -15
```

Expected: PASS. Se `test_config_audit.py` ou `test_contracts.py` enumerar campos e reclamar dos novos, estender a enumeração para incluir `mission_mode` e `yaw_pattern.*` (não relaxar a asserção).

- [ ] **Step 8: Checkpoint (sem executar)** Mensagem proposta: `feat(mission): add mission_mode selection and yaw pattern parameters`. Aguardar autorização do usuário.

---

## Task 2: Autoridade de yaw (`YawAuthority`) no proxy e no failsafe

**Files:**
- Create: `mvp_mission_bebop/actuators/yaw_authority.py`
- Modify: `mvp_mission_bebop/actuators/proxy.py` (`BenchtopDroneProxy.__init__`, `move_velocity`), `mvp_mission_bebop/telemetry/failsafe.py` (`FailsafeSupervisor.__init__`, `clamp_kinematics`, nova `yaw_window`)
- Test: `test/test_yaw_authority.py`

**Interfaces:**
- Produces: `YawAuthority()` com `ceiling -> float`, `is_open -> bool`, `window(max_vyaw: float) -> ContextManager[None]` (aninhável, restaura o teto anterior, `ValueError` para valor negativo ou não finito), `clamp(vyaw: float) -> float` (0.0 fechada, saturação em `±ceiling` aberta, `NaN` vira 0.0). `FailsafeSupervisor(..., yaw_authority: Optional[YawAuthority] = None)` com atributo `yaw_authority` e método `yaw_window(max_vyaw)`. `BenchtopDroneProxy(..., yaw_authority: Optional[YawAuthority] = None)` com atributo `yaw_authority`.

- [ ] **Step 1: Escrever os testes que falham**

`test/test_yaw_authority.py`:

```python
"""Yaw is forbidden everywhere except inside an explicit, bounded window."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.actuators.yaw_authority import YawAuthority
from mvp_mission_bebop.parameters import FlightKinematicsConfig, TimeoutsConfig
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor


class RawDrone:
    def __init__(self) -> None:
        self.calls = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.calls.append((vx, vy, vz, vyaw))


class Odometry:
    def snapshot(self):
        return self


def failsafe(authority=None) -> FailsafeSupervisor:
    return FailsafeSupervisor(
        drone_actuator=None,
        odom_supervisor=Odometry(),
        timeouts_cfg=TimeoutsConfig(),
        kinematics_cfg=FlightKinematicsConfig(),
        yaw_authority=authority,
    )


def test_a_closed_authority_forces_zero():
    authority = YawAuthority()
    assert not authority.is_open
    assert authority.clamp(0.5) == 0.0
    assert authority.clamp(-0.5) == 0.0


def test_an_open_window_passes_up_to_its_ceiling_and_saturates_beyond():
    authority = YawAuthority()
    with authority.window(0.30):
        assert authority.is_open
        assert authority.clamp(0.20) == 0.20
        assert authority.clamp(-0.20) == -0.20
        assert authority.clamp(0.90) == 0.30
        assert authority.clamp(-0.90) == -0.30


def test_the_window_closes_on_exit_even_after_an_exception():
    authority = YawAuthority()
    with pytest.raises(RuntimeError):
        with authority.window(0.30):
            raise RuntimeError("step fault")
    assert not authority.is_open
    assert authority.clamp(0.2) == 0.0


def test_windows_nest_and_restore_the_previous_ceiling():
    authority = YawAuthority()
    with authority.window(0.30):
        with authority.window(0.10):
            assert authority.ceiling == 0.10
        assert authority.ceiling == 0.30
    assert authority.ceiling == 0.0


@pytest.mark.parametrize("ceiling", [-0.1, math.nan, math.inf])
def test_an_invalid_ceiling_is_refused(ceiling):
    with pytest.raises(ValueError):
        with YawAuthority().window(ceiling):
            pass


def test_a_non_finite_command_is_suppressed_inside_a_window():
    authority = YawAuthority()
    with authority.window(0.30):
        assert authority.clamp(math.nan) == 0.0
        assert authority.clamp(math.inf) == 0.0


def test_the_failsafe_suppresses_yaw_unless_its_authority_is_open():
    supervisor = failsafe()
    assert supervisor.clamp_kinematics(0.0, 0.2) == (0.0, 0.0)
    with supervisor.yaw_window(0.30):
        assert supervisor.clamp_kinematics(0.0, 0.2) == (0.0, 0.2)
    assert supervisor.clamp_kinematics(0.0, 0.2) == (0.0, 0.0)


def test_the_proxy_suppresses_yaw_unless_its_authority_is_open():
    authority = YawAuthority()
    raw = RawDrone()
    proxy = BenchtopDroneProxy(raw, no_fly=False, yaw_authority=authority)
    proxy.move_velocity(vyaw=0.2)
    with authority.window(0.30):
        proxy.move_velocity(vyaw=0.2)
    proxy.move_velocity(vyaw=0.2)
    assert [call[3] for call in raw.calls] == [0.0, 0.2, 0.0]


def test_a_mismatched_pair_fails_closed():
    # The failsafe opens its window, the proxy holds a different, closed one:
    # the airframe must still receive zero.
    supervisor = failsafe(YawAuthority())
    raw = RawDrone()
    proxy = BenchtopDroneProxy(raw, no_fly=False, yaw_authority=YawAuthority())
    with supervisor.yaw_window(0.30):
        _vz, vyaw = supervisor.clamp_kinematics(0.0, 0.2)
        proxy.move_velocity(vyaw=vyaw)
    assert raw.calls[-1][3] == 0.0
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_yaw_authority.py -v 2>&1 | tail -12
```

Expected: FAIL (`ModuleNotFoundError: mvp_mission_bebop.actuators.yaw_authority`).

- [ ] **Step 3: Criar `actuators/yaw_authority.py`**

```python
"""Authority to command yaw, granted by window and closed by default.

The mission holds ``vyaw == 0.0`` as a hard invariant: the Bebop derives its
horizontal odometry from optical flow and a yaw rate corrupts it. The invariant
is enforced at two independent points, the actuator proxy and the failsafe
supervisor. The Simple mission profile is the one flight that must rotate, and
it must do so without weakening either point for any other stage.

A window is the same device as ``FailsafeSupervisor.altitude_hold_window``:
authority exists only inside a ``with`` block, nests, restores the previous
ceiling on exit and cannot outlive an exception. The proxy and the supervisor
are given the *same* instance by ``mission.py``; if they are ever given two
different ones, the window opened on one is closed on the other and the
airframe receives zero, which is the safe direction.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from typing import Iterator


class YawAuthority:
    """Bounded, scoped permission to command a yaw rate."""

    def __init__(self) -> None:
        self._ceiling: float = 0.0
        self._lock = threading.Lock()

    @property
    def ceiling(self) -> float:
        """Largest yaw magnitude currently authorized, normalized. Zero when closed."""
        with self._lock:
            return self._ceiling

    @property
    def is_open(self) -> bool:
        """Whether any yaw is currently authorized."""
        return self.ceiling > 0.0

    @contextmanager
    def window(self, max_vyaw: float) -> Iterator[None]:
        """Authorize yaw up to ``max_vyaw`` for the duration of the block.

        Parameters
        ----------
        max_vyaw : float
            Ceiling on the yaw magnitude, normalized. Zero authorizes nothing.

        Raises
        ------
        ValueError
            If ``max_vyaw`` is negative or not finite.
        """
        if not math.isfinite(max_vyaw) or max_vyaw < 0.0:
            raise ValueError(f"max_vyaw must be a finite non-negative number, got {max_vyaw!r}")
        with self._lock:
            previous = self._ceiling
            self._ceiling = float(max_vyaw)
        try:
            yield
        finally:
            with self._lock:
                self._ceiling = previous

    def clamp(self, vyaw: float) -> float:
        """Saturate a yaw command onto the authorized ceiling.

        A non-finite value is rejected to zero rather than saturated: ``NaN``
        compares False against every bound and would otherwise resolve to
        whichever bound ``min``/``max`` happens to evaluate first.
        """
        ceiling = self.ceiling
        if ceiling <= 0.0 or not math.isfinite(vyaw):
            return 0.0
        return max(-ceiling, min(ceiling, float(vyaw)))
```

- [ ] **Step 4: Ligar o proxy**

Em `actuators/proxy.py`: importar `YawAuthority`; adicionar ao `__init__` de `BenchtopDroneProxy` o parâmetro `yaw_authority: Optional[YawAuthority] = None` (último, com default, para não quebrar chamadas existentes) e `self.yaw_authority = yaw_authority if yaw_authority is not None else YawAuthority()`. Em `move_velocity`, substituir o bloco "if vyaw != 0.0 ... vyaw = 0.0" por:

```python
        allowed = self.yaw_authority.clamp(vyaw)
        if allowed != vyaw:
            logger.error(
                "Rejected vyaw=%.4f from a velocity command; the yaw authority allows %.4f "
                "(rotation is authorized only inside a yaw window).",
                vyaw,
                allowed,
            )
            vyaw = allowed
```

Atualizar o docstring: o texto "forced to zero here unconditionally" passa a "forced to zero unless a yaw window is open". Manter a palavra `vyaw` na mensagem (o teste `test_a_nonzero_yaw_command_is_logged_at_error` a procura).

- [ ] **Step 5: Ligar o failsafe**

Em `telemetry/failsafe.py`: importar `YawAuthority`; em `FailsafeSupervisor.__init__` adicionar `yaw_authority: Optional[YawAuthority] = None` (último) e `self.yaw_authority = yaw_authority if yaw_authority is not None else YawAuthority()`; adicionar:

```python
    def yaw_window(self, max_vyaw: float) -> ContextManager[None]:
        """Authorize yaw up to ``max_vyaw`` for the duration of the block.

        Opened only by the Simple mission profile (stages 2 to 4). See
        :class:`~mvp_mission_bebop.actuators.yaw_authority.YawAuthority`.
        """
        return self.yaw_authority.window(max_vyaw)
```

Em `clamp_kinematics`, trocar o final por:

```python
        safe_vyaw = self.yaw_authority.clamp(vyaw)
        if abs(vyaw - safe_vyaw) > KINEMATIC_TOLERANCE:
            logger.warning(
                "Yaw command vyaw=%.4f limited to %.4f; rotation is authorized only inside "
                "a yaw window.",
                vyaw,
                safe_vyaw,
            )
        return safe_vz, safe_vyaw
```

e ajustar o docstring ("``vyaw`` is forced to zero unconditionally" vira "forced to zero unless the yaw authority is open"; o retorno documentado deixa de ser "always 0.0"). Importar `ContextManager` de `typing`.

- [ ] **Step 6: Rodar os testes novos e os de invariantes**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_yaw_authority.py test/test_actuator_proxy.py test/test_safety_invariants.py test/test_failsafe.py -q 2>&1 | tail -12
```

Expected: PASS, sem editar `test_actuator_proxy.py` nem `test_safety_invariants.py` (autoridade fechada por padrão preserva o comportamento).

- [ ] **Step 7: Checkpoint (sem executar)** Mensagem proposta: `feat(safety): add scoped yaw authority window to the actuator proxy and failsafe`.

---

## Task 3: Controladores de yaw (matemática pura) e `ServoCommand.vyaw`

**Files:**
- Create: `mvp_mission_bebop/controllers/yaw_pattern.py`
- Modify: `mvp_mission_bebop/controllers/visual_servoing.py` (`ServoCommand`)
- Test: `test/test_yaw_pattern.py`

**Interfaces:**
- Consumes: `FilteredPID`, `PIDGains` (`controllers/pid.py`); `QuantizedCommandShaper` (`controllers/quantization.py`); `ServoCommand`, `TrackingPhase` (`controllers/visual_servoing.py`); `VisionConfig`, `YawPatternConfig` (`parameters.py`).
- Produces:
  - `ServoCommand.vyaw: float = 0.0` (campo novo, último, com default).
  - `wrap_angle(angle_rad: float) -> float` em `(-pi, pi]`, `ValueError` se não finito.
  - `validate_yaw_pattern(cfg: YawPatternConfig) -> None`, `ValueError` em configuração inexecutável.
  - `YawRateLimiter(max_rate_per_sec: float)` com `value: float`, `step(target: float, dt: float) -> float`, `reset() -> None`.
  - `YawServoingController(yaw_cfg: YawPatternConfig, vision_cfg: VisionConfig)` com a mesma interface duck-typed de `VisualServoingController` usada pelo Step 3: `reset()`, `compute(target_center, frame_dimensions, current_tilt_deg, relative_altitude_m, dt, *, speed_scale=1.0, measured=True, airframe_at_rest=True) -> ServoCommand`, `note_target_lost(current_tilt_deg, dt) -> ServoCommand`, propriedades `phase` e `locked`.
  - `HeadingCommand(vyaw: float, error_rad: float, settled: bool)` e `YawHeadingController(yaw_cfg)` com `reset()` e `update(yaw_rad, reference_rad, dt) -> HeadingCommand`.

- [ ] **Step 1: Escrever os testes que falham**

`test/test_yaw_pattern.py`:

```python
"""Pure yaw laws: scan ramp, pixel-error servo, heading restore."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.controllers.visual_servoing import TrackingPhase
from mvp_mission_bebop.controllers.yaw_pattern import (
    YawHeadingController,
    YawRateLimiter,
    YawServoingController,
    validate_yaw_pattern,
    wrap_angle,
)
from mvp_mission_bebop.parameters import VisionConfig, YawPatternConfig

WIDTH, HEIGHT = 856, 480
CENTER = (WIDTH / 2.0, HEIGHT / 2.0)
DT = 1.0 / 15.0
YAW_RATE_PER_UNIT = 1.75


def servo(**overrides) -> YawServoingController:
    cfg = YawPatternConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return YawServoingController(cfg, VisionConfig())


def compute(controller, px, *, measured=True, at_rest=True):
    return controller.compute(
        target_center=(px, HEIGHT / 2.0),
        frame_dimensions=(WIDTH, HEIGHT),
        current_tilt_deg=-20.0,
        relative_altitude_m=1.0,
        dt=DT,
        measured=measured,
        airframe_at_rest=at_rest,
    )


# ---------------------------------------------------------------- wrap_angle


@pytest.mark.parametrize(
    "angle, expected",
    [
        (0.0, 0.0),
        (math.pi, math.pi),
        (-math.pi, math.pi),
        (3 * math.pi / 2, -math.pi / 2),
        (-3 * math.pi / 2, math.pi / 2),
        (4 * math.pi + 0.3, 0.3),
    ],
)
def test_wrap_angle_lands_in_the_half_open_interval(angle, expected):
    assert wrap_angle(angle) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("angle", [math.nan, math.inf])
def test_wrap_angle_refuses_a_non_finite_angle(angle):
    with pytest.raises(ValueError):
        wrap_angle(angle)


# ------------------------------------------------------------- rate limiter


def test_the_ramp_never_changes_faster_than_its_slew_limit():
    limiter = YawRateLimiter(max_rate_per_sec=0.30)
    previous = 0.0
    for _ in range(30):
        value = limiter.step(0.15, DT)
        assert abs(value - previous) <= 0.30 * DT + 1e-12
        previous = value
    assert previous == pytest.approx(0.15)


def test_the_ramp_decelerates_to_exactly_zero():
    limiter = YawRateLimiter(0.30)
    for _ in range(30):
        limiter.step(0.15, DT)
    for _ in range(30):
        value = limiter.step(0.0, DT)
    assert value == 0.0


def test_the_ramp_refuses_a_non_positive_slew_limit():
    with pytest.raises(ValueError):
        YawRateLimiter(0.0)


# --------------------------------------------------------------- validation


def test_the_factory_configuration_is_executable():
    validate_yaw_pattern(YawPatternConfig())


@pytest.mark.parametrize(
    "field, value",
    [
        ("scan_vyaw", 0.0),
        ("scan_vyaw", 0.9),
        ("max_vyaw", 0.05),
        ("min_effective_vyaw", 0.001),
        ("return_tolerance_deg", 0.0),
        ("return_timeout_sec", 0.0),
        ("return_settle_cycles", 0),
        ("scan_ramp_per_sec", -1.0),
    ],
)
def test_an_inexecutable_configuration_is_refused(field, value):
    cfg = YawPatternConfig()
    setattr(cfg, field, value)
    with pytest.raises(ValueError):
        validate_yaw_pattern(cfg)


# ----------------------------------------------------------- servoing law


def test_a_target_right_of_centre_turns_the_airframe_right():
    command = compute(servo(), CENTER[0] + 250.0)
    assert command.vyaw < 0.0
    assert command.vx == 0.0 and command.vy == 0.0


def test_a_target_left_of_centre_turns_the_airframe_left():
    command = compute(servo(), CENTER[0] - 250.0)
    assert command.vyaw > 0.0
    assert command.vx == 0.0 and command.vy == 0.0


def test_the_command_never_exceeds_the_pid_saturation():
    cfg = YawPatternConfig()
    controller = YawServoingController(cfg, VisionConfig())
    for px in (0.0, WIDTH):
        command = compute(controller, px)
        assert abs(command.vyaw) <= cfg.pid_output_limit + 1e-6


def test_the_lock_latches_after_the_dwell_and_stays_latched():
    controller = servo()
    dwell = VisionConfig().alignment_dwell_cycles
    commands = [compute(controller, CENTER[0] + 3.0) for _ in range(dwell + 2)]
    locked = [c for c in commands if c.locked]
    assert locked, "the freeze never latched"
    assert all(c.vyaw == 0.0 for c in locked)
    assert commands[-1].locked and commands[-1].phase is TrackingPhase.LOCKED
    assert compute(controller, CENTER[0] + 200.0).locked


def test_the_lock_waits_for_the_airframe_to_be_at_rest():
    controller = servo()
    for _ in range(20):
        assert not compute(controller, CENTER[0], at_rest=False).locked
    assert compute(controller, CENTER[0], at_rest=True).locked


def test_predicted_frames_earn_no_centring_credit():
    controller = servo()
    for _ in range(20):
        assert not compute(controller, CENTER[0], measured=False).locked


def test_losing_the_target_searches_toward_the_side_it_left_from():
    controller = servo()
    compute(controller, CENTER[0] + 300.0)
    command = controller.note_target_lost(-20.0, DT)
    assert command.phase is TrackingPhase.REACQUIRING
    assert command.vyaw < 0.0
    assert command.vx == 0.0 and command.vy == 0.0

    controller = servo()
    compute(controller, CENTER[0] - 300.0)
    assert controller.note_target_lost(-20.0, DT).vyaw > 0.0


def test_reacquisition_is_bounded_and_reports_exhaustion():
    controller = servo()
    compute(controller, CENTER[0] + 300.0)
    window = VisionConfig().reacquire_timeout_sec
    exhausted = False
    for _ in range(int(window / DT) + 5):
        command = controller.note_target_lost(-20.0, DT)
        exhausted = exhausted or command.recovery_exhausted
    assert exhausted
    assert command.vyaw == 0.0


def test_regaining_the_target_resumes_centring():
    controller = servo()
    compute(controller, CENTER[0] + 300.0)
    for _ in range(3):
        controller.note_target_lost(-20.0, DT)
    confirm = VisionConfig().reacquire_confirm_frames
    for _ in range(confirm):
        command = compute(controller, CENTER[0] + 100.0)
    assert command.phase is TrackingPhase.CENTERING


def test_reset_returns_to_centring_and_clears_the_lock():
    controller = servo()
    for _ in range(10):
        compute(controller, CENTER[0])
    assert controller.locked
    controller.reset()
    assert not controller.locked and controller.phase is TrackingPhase.CENTERING


def test_a_non_positive_frame_is_refused():
    with pytest.raises(ValueError):
        servo().compute((0.0, 0.0), (0, 480), -20.0, 1.0, DT)


# ------------------------------------------------------------ heading law


def settle(controller, yaw0, reference, cycles=600):
    yaw = yaw0
    for index in range(cycles):
        command = controller.update(yaw, reference, DT)
        if command.settled:
            return yaw, index, True
        yaw += command.vyaw * YAW_RATE_PER_UNIT * DT
    return yaw, cycles, False


@pytest.mark.parametrize("offset_deg", [90.0, -90.0, 25.0, -170.0, 179.0])
def test_the_heading_restore_converges_from_any_offset(offset_deg):
    cfg = YawPatternConfig()
    controller = YawHeadingController(cfg)
    reference = math.radians(30.0)
    yaw, _cycles, settled = settle(controller, reference + math.radians(offset_deg), reference)
    assert settled, "the heading restore did not settle in 40 s of simulated time"
    assert abs(wrap_angle(reference - yaw)) <= math.radians(cfg.return_tolerance_deg) + 1e-9


def test_the_restore_takes_the_short_way_round():
    controller = YawHeadingController(YawPatternConfig())
    # Reference +170 deg, airframe at -170 deg: the short way is -20 deg (clockwise).
    command = controller.update(math.radians(-170.0), math.radians(170.0), DT)
    assert command.error_rad == pytest.approx(math.radians(-20.0), abs=1e-9)
    assert command.vyaw <= 0.0


def test_the_restore_never_exceeds_its_saturation():
    cfg = YawPatternConfig()
    controller = YawHeadingController(cfg)
    for _ in range(40):
        command = controller.update(math.radians(179.0), 0.0, DT)
        assert abs(command.vyaw) <= cfg.return_max_vyaw + 1e-6


def test_the_restore_commands_zero_inside_the_tolerance():
    controller = YawHeadingController(YawPatternConfig())
    command = controller.update(math.radians(1.0), 0.0, DT)
    assert command.vyaw == 0.0


def test_a_non_finite_heading_is_refused():
    controller = YawHeadingController(YawPatternConfig())
    with pytest.raises(ValueError):
        controller.update(math.nan, 0.0, DT)
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_yaw_pattern.py -q 2>&1 | tail -8
```

Expected: FAIL com `ModuleNotFoundError: ...controllers.yaw_pattern`.

- [ ] **Step 3: Acrescentar `vyaw` ao `ServoCommand`**

Em `controllers/visual_servoing.py`, como ÚLTIMO campo da dataclass `ServoCommand` (depois de `recovery_exhausted`):

```python
    #: Normalized yaw-rate command, positive to the left. Always ``0.0`` from the
    #: lateral law; nonzero only from ``YawServoingController`` (Simple mission
    #: profile), whose consumer must hold a yaw window to transmit it.
    vyaw: float = 0.0
```

Atualizar o docstring da classe se ele disser que o comando é só translacional. Nenhuma outra edição neste arquivo.

- [ ] **Step 4: Criar `controllers/yaw_pattern.py`**

```python
"""Yaw laws of the Simple mission profile: scan ramp, pixel servo, heading restore.

Pure mathematics. Nothing here reads a wall clock, touches a ROS node or sends
a command; the steps advance time (``dt`` from ``LoopRate.tick``) and transmit
what these laws return through the failsafe and the proxy, both of which refuse
yaw outside a ``YawAuthority`` window.

Sign convention
---------------
``vyaw > 0`` turns left (counter-clockwise seen from above, REP-103). A target
right of the image centre has a positive normalized pixel error and must be
answered with a negative ``vyaw``. As in ``VisualServoingController``, feeding
the error to :class:`~mvp_mission_bebop.controllers.pid.FilteredPID` as the
*measurement* against a zero setpoint produces exactly that, because the PID's
error term is then ``-error``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional, Tuple

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.controllers.visual_servoing import ServoCommand, TrackingPhase
from mvp_mission_bebop.parameters import VisionConfig, YawPatternConfig

#: Normalized yaw magnitude above which a configuration is not a plausible
#: scan or restore on this airframe (1.0 is full authority).
_MAX_PLAUSIBLE_VYAW: Final[float] = 0.5


def wrap_angle(angle_rad: float) -> float:
    """Wrap an angle to the half-open interval ``(-pi, pi]``.

    Parameters
    ----------
    angle_rad : float
        Angle in radians.

    Returns
    -------
    float

    Raises
    ------
    ValueError
        If ``angle_rad`` is not finite.
    """
    if not math.isfinite(angle_rad):
        raise ValueError(f"angle must be finite, got {angle_rad!r}")
    wrapped = math.fmod(angle_rad + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


def validate_yaw_pattern(cfg: YawPatternConfig) -> None:
    """Refuse a yaw configuration that cannot be flown.

    Called from ``mission.py`` before the first command of a Simple run, so a
    bad value is discovered on the ground and not halfway through Stage 4.

    Raises
    ------
    ValueError
        On a non-positive rate, a magnitude above :data:`_MAX_PLAUSIBLE_VYAW`,
        a yaw window ceiling below any commanded magnitude, a quantization floor
        below one driver step, or a non-positive window or tolerance.
    """
    magnitudes = {
        "scan_vyaw": cfg.scan_vyaw,
        "pid_output_limit": cfg.pid_output_limit,
        "reacquire_vyaw": cfg.reacquire_vyaw,
        "return_max_vyaw": cfg.return_max_vyaw,
    }
    for name, value in magnitudes.items():
        if not math.isfinite(value) or not 0.0 < value <= _MAX_PLAUSIBLE_VYAW:
            raise ValueError(f"yaw_pattern.{name} must be in (0, {_MAX_PLAUSIBLE_VYAW}], got {value!r}")
    if not math.isfinite(cfg.max_vyaw) or cfg.max_vyaw < max(magnitudes.values()):
        raise ValueError(
            f"yaw_pattern.max_vyaw ({cfg.max_vyaw!r}) must be at least the largest commanded "
            f"yaw magnitude ({max(magnitudes.values())!r})"
        )
    positives = {
        "scan_ramp_per_sec": cfg.scan_ramp_per_sec,
        "return_tolerance_deg": cfg.return_tolerance_deg,
        "return_timeout_sec": cfg.return_timeout_sec,
        "quantization_step": cfg.quantization_step,
        "kp": cfg.kp,
        "return_kp": cfg.return_kp,
    }
    for name, value in positives.items():
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"yaw_pattern.{name} must be positive, got {value!r}")
    if cfg.min_effective_vyaw < cfg.quantization_step:
        raise ValueError(
            f"yaw_pattern.min_effective_vyaw ({cfg.min_effective_vyaw!r}) must be at least one "
            f"quantization step ({cfg.quantization_step!r})"
        )
    if int(cfg.return_settle_cycles) < 1:
        raise ValueError(f"yaw_pattern.return_settle_cycles must be >= 1, got {cfg.return_settle_cycles!r}")


class YawRateLimiter:
    """Slew limiter for a normalized yaw-rate command.

    Parameters
    ----------
    max_rate_per_sec : float
        Largest change of the command per second, normalized units.

    Raises
    ------
    ValueError
        If ``max_rate_per_sec`` is not positive and finite.
    """

    def __init__(self, max_rate_per_sec: float) -> None:
        if not math.isfinite(max_rate_per_sec) or max_rate_per_sec <= 0.0:
            raise ValueError(f"max_rate_per_sec must be positive and finite, got {max_rate_per_sec!r}")
        self._max_rate = float(max_rate_per_sec)
        self._value = 0.0

    @property
    def value(self) -> float:
        """The command last returned by :meth:`step`."""
        return self._value

    def reset(self) -> None:
        """Return the command to zero without ramping."""
        self._value = 0.0

    def step(self, target: float, dt: float) -> float:
        """Move the command toward ``target`` by at most ``max_rate * dt``."""
        if dt <= 0.0 or not math.isfinite(target):
            return self._value
        limit = self._max_rate * dt
        delta = max(-limit, min(limit, target - self._value))
        self._value += delta
        if abs(target - self._value) <= 1e-12:
            self._value = float(target)
        return self._value


class YawServoingController:
    """Nulls the horizontal pixel error of the target with yaw alone.

    Duck-type compatible with ``VisualServoingController`` for ``VisualServoingStep``:
    the returned :class:`ServoCommand` carries ``vx == vy == 0.0`` and the yaw
    demand in ``vyaw``. The freeze latches on the same conditions as the lateral
    law: the target measured (not predicted) inside the centring gate for
    ``vision.alignment_dwell_cycles`` frames with the airframe at rest.

    Parameters
    ----------
    yaw_cfg : YawPatternConfig
    vision_cfg : VisionConfig
        Optical tolerance, dwell and reacquisition windows, shared with the
        lateral law so a lock means the same thing in every profile.

    Raises
    ------
    ValueError
        From :func:`validate_yaw_pattern`.
    """

    def __init__(self, yaw_cfg: YawPatternConfig, vision_cfg: VisionConfig) -> None:
        validate_yaw_pattern(yaw_cfg)
        self.yaw_cfg = yaw_cfg
        self.vision_cfg = vision_cfg
        self._pid = FilteredPID(
            PIDGains(
                kp=yaw_cfg.kp,
                ki=yaw_cfg.ki,
                kd=yaw_cfg.kd,
                output_limits=(-yaw_cfg.pid_output_limit, yaw_cfg.pid_output_limit),
                output_deadband=yaw_cfg.deadband,
            )
        )
        self._shaper = QuantizedCommandShaper(yaw_cfg.min_effective_vyaw, yaw_cfg.quantization_step)
        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._locked = False
        self._last_err_x: Optional[float] = None
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        self._recovery_exhausted = False
        self._tolerance_px = vision_cfg.optical_center_tolerance_px

    @property
    def phase(self) -> TrackingPhase:
        return self._phase

    @property
    def locked(self) -> bool:
        return self._locked

    def reset(self) -> None:
        """Clear all state and return to centring."""
        self._pid.reset()
        self._shaper.reset()
        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._locked = False
        self._last_err_x = None
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        self._recovery_exhausted = False

    def compute(
        self,
        target_center: Tuple[float, float],
        frame_dimensions: Tuple[int, int],
        current_tilt_deg: float,
        relative_altitude_m: float,
        dt: float,
        *,
        speed_scale: float = 1.0,
        measured: bool = True,
        airframe_at_rest: bool = True,
    ) -> ServoCommand:
        """One servoing cycle from a resolved observation.

        ``relative_altitude_m`` and ``speed_scale`` are accepted for call
        compatibility with the lateral law and ignored: yaw servoing needs no
        ground geometry.

        Raises
        ------
        ValueError
            If a frame dimension is not positive.
        """
        width, height = frame_dimensions
        if width <= 0 or height <= 0:
            raise ValueError(f"frame dimensions must be positive, got {frame_dimensions!r}")
        half_width = width / 2.0
        err_x = float(target_center[0]) - half_width
        err_y = float(target_center[1]) - height / 2.0
        pixel_error = math.hypot(err_x, err_y)
        self._tolerance_px = max(
            self.vision_cfg.optical_center_tolerance_px,
            self.vision_cfg.optical_center_tolerance_ratio * width,
        )
        in_gate = abs(err_x) <= self._tolerance_px

        if self._phase is TrackingPhase.LOCKED:
            return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "locked (yaw freeze)")

        self._last_err_x = err_x

        if self._phase is TrackingPhase.REACQUIRING:
            if not measured:
                return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "reacquiring (predicted)")
            self._reacquire_hits += 1
            if self._reacquire_hits < max(1, int(self.vision_cfg.reacquire_confirm_frames)):
                return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "reacquiring (confirming)")
            self._phase = TrackingPhase.CENTERING
            self._reacquire_hits = 0
            self._reacquire_elapsed_sec = 0.0
            self._pid.reset()
            self._shaper.reset()

        demand = self._pid.update(err_x / half_width, dt)
        vyaw = self._shaper.shape(demand, dt)

        if measured and in_gate:
            self._centered_frames += 1
        elif not in_gate:
            self._centered_frames = max(0, self._centered_frames - 1)

        if self._centered_frames >= max(1, int(self.vision_cfg.alignment_dwell_cycles)) and airframe_at_rest:
            self._locked = True
            self._phase = TrackingPhase.LOCKED
            return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "yaw freeze latched")

        return self._command(vyaw, err_x, pixel_error, in_gate, current_tilt_deg, "centring on yaw")

    def note_target_lost(self, current_tilt_deg: float, dt: float) -> ServoCommand:
        """One cycle without a usable target: search toward the side it left from.

        Rotation direction follows the last measured error (target last seen to
        the right means turn right). With no sighting at all, turn left, the
        direction of the Stage 2 scan. The window is
        ``vision.reacquire_timeout_sec``; on expiry the command is zero and
        ``recovery_exhausted`` is set so the step can end.
        """
        self._phase = TrackingPhase.REACQUIRING
        self._reacquire_hits = 0
        self._reacquire_elapsed_sec += max(0.0, dt)
        if self._reacquire_elapsed_sec >= self.vision_cfg.reacquire_timeout_sec:
            self._recovery_exhausted = True
            return self._command(
                0.0, self._last_err_x or 0.0, 0.0, False, current_tilt_deg, "reacquisition exhausted"
            )
        rate = self.yaw_cfg.reacquire_vyaw
        vyaw = rate if self._last_err_x is None else -math.copysign(rate, self._last_err_x)
        return self._command(vyaw, self._last_err_x or 0.0, 0.0, False, current_tilt_deg, "reacquiring on yaw")

    def _command(
        self,
        vyaw: float,
        err_x: float,
        pixel_error: float,
        in_gate: bool,
        tilt_deg: float,
        note: str,
    ) -> ServoCommand:
        half_width = max(self._tolerance_px, 1.0)
        alignment = 1.0 if in_gate else max(0.0, 1.0 - abs(err_x) / max(half_width * 10.0, 1.0))
        return ServoCommand(
            vx=0.0,
            vy=0.0,
            tilt_deg=tilt_deg,
            pixel_error=pixel_error,
            ground_range_m=None,
            depression_deg=None,
            phase=self._phase,
            laterally_centered=in_gate,
            locked=self._locked,
            note=note,
            tolerance_px=self._tolerance_px,
            alignment=alignment,
            recovery_exhausted=self._recovery_exhausted,
            vyaw=vyaw,
        )


@dataclass(frozen=True)
class HeadingCommand:
    """One cycle of the heading restore."""

    #: Normalized yaw command, positive to the left.
    vyaw: float
    #: Wrapped heading error ``reference - yaw``, radians, in ``(-pi, pi]``.
    error_rad: float
    #: True once the error has stayed inside the tolerance for the settle cycles.
    settled: bool


class YawHeadingController:
    """Returns the airframe to a reference heading by the short way round.

    Parameters
    ----------
    yaw_cfg : YawPatternConfig

    Raises
    ------
    ValueError
        From :func:`validate_yaw_pattern`.
    """

    def __init__(self, yaw_cfg: YawPatternConfig) -> None:
        validate_yaw_pattern(yaw_cfg)
        self._pid = FilteredPID(
            PIDGains(
                kp=yaw_cfg.return_kp,
                ki=0.0,
                kd=yaw_cfg.return_kd,
                output_limits=(-yaw_cfg.return_max_vyaw, yaw_cfg.return_max_vyaw),
            )
        )
        self._shaper = QuantizedCommandShaper(yaw_cfg.min_effective_vyaw, yaw_cfg.quantization_step)
        self._tolerance_rad = math.radians(yaw_cfg.return_tolerance_deg)
        self._settle_cycles = max(1, int(yaw_cfg.return_settle_cycles))
        self._in_tolerance = 0

    def reset(self) -> None:
        self._pid.reset()
        self._shaper.reset()
        self._in_tolerance = 0

    def update(self, yaw_rad: float, reference_rad: float, dt: float) -> HeadingCommand:
        """Command for one cycle.

        Raises
        ------
        ValueError
            If either heading is not finite.
        """
        error = wrap_angle(reference_rad - yaw_rad)
        if abs(error) <= self._tolerance_rad:
            self._in_tolerance += 1
            return HeadingCommand(0.0, error, self._in_tolerance >= self._settle_cycles)
        self._in_tolerance = 0
        demand = self._pid.update(-error, dt)
        return HeadingCommand(self._shaper.shape(demand, dt), error, False)
```

- [ ] **Step 5: Rodar os testes**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_yaw_pattern.py test/test_lateral_servoing.py test/test_controllers.py -q 2>&1 | tail -15
```

Expected: PASS. Se um teste de convergência (`test_the_heading_restore_converges_from_any_offset`, `test_the_lock_latches_after_the_dwell_and_stays_latched`) falhar, ajustar os DEFAULTS de `YawPatternConfig` (ganhos, piso `min_effective_vyaw`) e nunca relaxar a asserção. Se `test_the_lock_latches...` falhar por o PID produzir saída diferente de zero perto do centro, conferir que o lock retorna `vyaw == 0.0` (asserção já exige).

- [ ] **Step 6: Checkpoint (sem executar)** Mensagem proposta: `feat(controllers): add yaw scan limiter, yaw servoing and heading restore laws`.

---

## Task 4: Blackboard, Step 1, fábrica de steps e fiação em `mission.py`

**Files:**
- Modify: `mvp_mission_bebop/blackboard.py`, `mvp_mission_bebop/steps/takeoff.py` (`TakeoffStep._finalize`), `mvp_mission_bebop/context.py` (anotação), `mvp_mission_bebop/mission.py`
- Create: `mvp_mission_bebop/steps/pipeline.py`
- Modify: `mvp_mission_bebop/steps/search.py`, `tracking.py`, `inspection.py`, `rtl.py` (somente o construtor, a propriedade `mode` e o nome; a lógica vem nas Tasks 5 a 8)
- Test: `test/test_mission_modes.py` (acrescentar)

**Interfaces:**
- Consumes: `MissionMode` (Task 1), `YawAuthority` (Task 2), `YawServoingController`, `validate_yaw_pattern` (Task 3).
- Produces: `MissionBlackboard.initial_yaw_rad: Optional[float] = None`. `LateralSearchStep(*, mode=MissionMode.ADVANCED)`, `VisualServoingStep(*, mode=...)`, `NadirInspectionStep(*, mode=...)`, `ClosedLoopRTLStep(guidance=None, centering=None, sensor=None, *, mode=...)`, cada um com a propriedade `mode -> MissionMode`. `build_steps(mode: MissionMode) -> Dict[int, Callable[[], BaseStep]]` em `steps/pipeline.py`.

Nomes dos steps por modo (prefixo `STEP N:` obrigatório; o nome Avançado é o atual):

| Step | Avançado | Intermediário | Simples |
|---|---|---|---|
| 2 | `STEP 2: Lateral Left Search` | igual | `STEP 2: Yaw Scan Search` |
| 3 | `STEP 3: Visual Servoing & Coupled Guidance` | igual | `STEP 3: Yaw Servoing & Coupled Guidance` |
| 4 | `STEP 4: Motionless Nadir Hover & Evidence Capture` | igual | igual |
| 5 | `STEP 5: ArUco RTL & Precision Landing` | `STEP 5: Landing at Inspection Point` | `STEP 5: ArUco Centering & Base Landing` |

- [ ] **Step 1: Escrever os testes que falham** (acrescentar a `test/test_mission_modes.py`)

```python
import re

from mvp_mission_bebop.steps.pipeline import build_steps


@pytest.mark.parametrize("mode", list(MissionMode))
def test_every_profile_builds_five_steps_with_the_stage_marker_prefix(mode):
    factories = build_steps(mode)
    assert list(factories) == [1, 2, 3, 4, 5]
    for number, factory in factories.items():
        step = factory()
        assert re.match(rf"STEP {number}: ", step.name), step.name


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_steps_that_differ_by_profile_carry_it(mode):
    steps = {number: factory() for number, factory in build_steps(mode).items()}
    for number in (2, 3, 4, 5):
        assert steps[number].mode is mode


def test_the_advanced_names_are_the_historical_ones():
    steps = {number: factory() for number, factory in build_steps(MissionMode.ADVANCED).items()}
    assert steps[2].name == "STEP 2: Lateral Left Search"
    assert steps[3].name == "STEP 3: Visual Servoing & Coupled Guidance"
    assert steps[4].name == "STEP 4: Motionless Nadir Hover & Evidence Capture"
    assert steps[5].name == "STEP 5: ArUco RTL & Precision Landing"


def test_a_step_built_without_a_mode_is_the_advanced_one():
    from mvp_mission_bebop.steps import ClosedLoopRTLStep, LateralSearchStep, NadirInspectionStep, VisualServoingStep

    for cls in (LateralSearchStep, VisualServoingStep, NadirInspectionStep, ClosedLoopRTLStep):
        assert cls().mode is MissionMode.ADVANCED


def test_the_blackboard_starts_without_a_takeoff_heading():
    from mvp_mission_bebop.blackboard import MissionBlackboard

    assert MissionBlackboard().initial_yaw_rad is None
```

- [ ] **Step 2: Rodar e ver falhar** (`ModuleNotFoundError: ...steps.pipeline`).

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_modes.py -q 2>&1 | tail -8
```

- [ ] **Step 3: Blackboard e Step 1**

`blackboard.py`, dentro de `MissionBlackboard`:

```python
    #: Heading at the end of Stage 1, radians (``OdometrySnapshot.yaw``). The
    #: Simple profile restores it at the end of Stage 4. ``None`` until Stage 1
    #: completes (a partial run that skips Stage 1 records it on first use).
    initial_yaw_rad: Optional[float] = None
```

`steps/takeoff.py`, em `TakeoffStep._finalize`, logo depois de `ctx.odom_supervisor.freeze_hover_takeoff_origin()` e do `snapshot = ctx.odom_supervisor.snapshot()` seguinte:

```python
        bb = getattr(ctx, "blackboard", None)
        if bb is not None:
            bb.initial_yaw_rad = float(snapshot.yaw)
```

(Há um bloco `bb = getattr(ctx, "blackboard", None)` logo abaixo para `t_hover`; reaproveitar a variável em vez de duplicar, mantendo a ordem: origem congelada, yaw gravado, `t_hover`.) Registra para todos os modos; é inofensivo.

- [ ] **Step 4: Construtores e `mode` nos quatro steps**

Padrão (exemplo para `search.py`; repetir nos outros com seus nomes):

```python
from mvp_mission_bebop.mission_mode import MissionMode

_STEP_NAMES = {
    MissionMode.ADVANCED: "STEP 2: Lateral Left Search",
    MissionMode.INTERMEDIATE: "STEP 2: Lateral Left Search",
    MissionMode.SIMPLE: "STEP 2: Yaw Scan Search",
}

class LateralSearchStep(BaseStep):
    def __init__(self, *, mode: MissionMode = MissionMode.ADVANCED) -> None:
        self._mode = mode
        super().__init__(_STEP_NAMES[mode])

    @property
    def mode(self) -> MissionMode:
        return self._mode
```

`inspection.py`: o `__init__` existente já cria `self._pending_ack`; acrescentar `mode` mantendo-o. `rtl.py`: `ClosedLoopRTLStep.__init__(self, guidance=None, centering=None, sensor=None, *, mode=MissionMode.ADVANCED)`, nome por tabela acima, `self._mode = mode`, propriedade `mode`. `ForwardSearchStep = LateralSearchStep` permanece.

- [ ] **Step 5: Criar `steps/pipeline.py`**

```python
"""Step factories by mission profile."""

from __future__ import annotations

import functools
from typing import Callable, Dict

from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps.base import BaseStep
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from mvp_mission_bebop.steps.search import LateralSearchStep
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.steps.tracking import VisualServoingStep


def build_steps(mode: MissionMode) -> Dict[int, Callable[[], BaseStep]]:
    """Factories for the five stages of ``mode``, keyed by stage number.

    Stage 1 is identical in every profile. Stages 2 to 5 receive the profile at
    construction; it is never read from the parameter document by the steps.
    """
    return {
        1: TakeoffStep,
        2: functools.partial(LateralSearchStep, mode=mode),
        3: functools.partial(VisualServoingStep, mode=mode),
        4: functools.partial(NadirInspectionStep, mode=mode),
        5: functools.partial(ClosedLoopRTLStep, mode=mode),
    }
```

- [ ] **Step 6: Fiação em `mission.py`**

Imports: `from mvp_mission_bebop.actuators.yaw_authority import YawAuthority`, `from mvp_mission_bebop.controllers.yaw_pattern import YawServoingController, validate_yaw_pattern`, `from mvp_mission_bebop.steps.pipeline import build_steps` (remover `ClosedLoopRTLStep, LateralSearchStep, ...` do import local somente se ficarem sem uso).

1. Antes da construção de `BenchtopDroneProxy(...)` (localizar por símbolo): `yaw_authority = YawAuthority()` e passar `yaw_authority=yaw_authority` ao proxy. O proxy nasce antes do `go`; isso é seguro porque a autoridade não depende do modo (fechada até um step abri-la).
2. Em `FailsafeSupervisor(...)`: `yaw_authority=yaw_authority`.
3. Logo antes de construir o `visual_controller` (bloco pós-`go`):

```python
    mission_mode = MissionMode(params.mission_mode)
    logger.info("Mission mode: %s.", mission_mode.value)
    if mission_mode is MissionMode.SIMPLE:
        try:
            validate_yaw_pattern(params.yaw_pattern)
        except ValueError as error:
            raise SystemExit(f"yaw_pattern: {error}") from error
        visual_controller = YawServoingController(params.yaw_pattern, params.vision)
    else:
        visual_controller = VisualServoingController(
            gimbal_config=params.gimbal,
            gimbal_pid_cfg=params.gimbal_pid,
            lateral_pid_cfg=params.lateral_pid,
            vision_cfg=params.vision,
            kinematics_cfg=params.kinematics,
            calibration=calibration,
        )
```

(`params.mission_mode` já foi normalizado por `apply_mission_mode`; `MissionMode(...)` não pode falhar aqui.) Substituir o `visual_controller = VisualServoingController(...)` existente por este bloco.

4. Trocar o dicionário `all_steps = {1: TakeoffStep, ...}` por `all_steps = build_steps(mission_mode)`.

`context.py`: a anotação `visual_controller: VisualServoingController` passa a `Union[VisualServoingController, "YawServoingController"]` importando `YawServoingController` sob `TYPE_CHECKING` para evitar ciclo.

- [ ] **Step 7: Rodar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_modes.py test/test_lateral_search.py test/test_lateral_servoing.py test/test_inspection_hover.py test/test_lateral_rtl.py test/test_aruco_rtl.py test/test_takeoff.py test/test_stage_altitude_wiring.py -q 2>&1 | tail -15
```

Expected: PASS (o modo Avançado é o default e nenhum teste existente passa `mode`).

- [ ] **Step 8: Checkpoint (sem executar)** Mensagem proposta: `refactor(mission): inject the mission profile into the stage steps and wire the yaw authority`.

---

## Task 5: Step 2 do modo Simples (varredura por yaw)

**Files:**
- Modify: `mvp_mission_bebop/steps/search.py`
- Test: `test/test_simple_mode_steps.py` (criar; as Tasks 6, 7 e 8 acrescentam a este arquivo)

**Interfaces:**
- Consumes: `MissionMode`, `YawRateLimiter`, `ctx.failsafe.yaw_window`, `ctx.params.yaw_pattern`, `blackboard.initial_yaw_rad`.
- Produces: com `mode=SIMPLE`, `LateralSearchStep.execute` retorna `StepStatus.SUCCESS` após confirmação ou fim de janela e transmite apenas Twists com `vx == vy == 0.0` e `vyaw >= 0.0`; o último Twist tem `vyaw == 0.0`.

- [ ] **Step 1: Escrever os testes que falham**

Cabeçalho do `test/test_simple_mode_steps.py` (reaproveita o `Ctx` compartilhado e o relógio simulado de `test_lateral_search.py`):

```python
"""Simple mission profile: Stages 2 to 5 against the shared synchronous-perception context."""

from __future__ import annotations

import functools
import math
from typing import List, Tuple

import pytest

from mvp_mission_bebop.actuators.yaw_authority import YawAuthority
from mvp_mission_bebop.controllers.yaw_pattern import YawServoingController
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps import inspection as inspection_module
from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps import search as search_module
from mvp_mission_bebop.steps import tracking as tracking_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from mvp_mission_bebop.steps.search import LateralSearchStep
from mvp_mission_bebop.steps.tracking import VisualServoingStep
from test_stage_altitude_wiring import Ctx

YAW_RATE_PER_UNIT = 1.75


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture(autouse=True)
def simulated_time(monkeypatch):
    fake = FakeTime()
    for module in (search_module, tracking_module, inspection_module, rtl_module):
        monkeypatch.setattr(module, "LoopRate", functools.partial(LoopRate, clock=fake.clock, sleeper=fake.sleep))
        monkeypatch.setattr(module, "Deadline", functools.partial(Deadline, clock=fake.clock))
    return fake


def simple_ctx(*, detections: bool, window: float = 2.0) -> Ctx:
    """A context whose failsafe and drone share one YawAuthority, as in mission.py."""
    ctx = Ctx(detections=detections)
    authority = YawAuthority()
    ctx.failsafe.yaw_authority = authority
    ctx.params.mission_mode = "simple"
    ctx.params.timeouts.search_timeout_sec = window
    ctx.params.timeouts.tracking_timeout_sec = window
    ctx.params.platform.edge_ahead_m = None
    ctx.odom_supervisor.yaw = 0.0
    ctx.blackboard.initial_yaw_rad = None
    ctx.visual_controller = YawServoingController(ctx.params.yaw_pattern, ctx.params.vision)
    return ctx


def twists(ctx: Ctx) -> List[Tuple[float, float, float, float]]:
    return list(ctx.drone.commands)


# ------------------------------------------------------------------ Stage 2


def test_the_yaw_scan_never_translates_and_only_turns_left():
    ctx = simple_ctx(detections=False)

    status = LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    commands = twists(ctx)
    assert commands
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in commands)
    assert all(vyaw >= 0.0 for _vx, _vy, _vz, vyaw in commands)
    assert any(vyaw > 0.0 for _vx, _vy, _vz, vyaw in commands)


def test_the_scan_reaches_the_configured_rate_and_ends_at_exactly_zero():
    ctx = simple_ctx(detections=False)
    scan = ctx.params.yaw_pattern.scan_vyaw

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    commands = twists(ctx)
    assert max(vyaw for *_rest, vyaw in commands) == pytest.approx(scan, abs=1e-9)
    assert commands[-1][3] == 0.0, "the Bebop latches the last Twist; the stage must leave it at zero"


def test_a_confirmed_target_stops_the_rotation_and_records_it():
    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = False

    status = LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.blackboard.target_confirmed is True
    assert twists(ctx)[-1][3] == 0.0


def test_the_stage_records_the_heading_when_stage_one_did_not():
    ctx = simple_ctx(detections=False)
    ctx.odom_supervisor.yaw = 0.7

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.blackboard.initial_yaw_rad == pytest.approx(0.7)


def test_the_stage_keeps_the_heading_stage_one_recorded():
    ctx = simple_ctx(detections=False)
    ctx.blackboard.initial_yaw_rad = 0.2
    ctx.odom_supervisor.yaw = 0.7

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.blackboard.initial_yaw_rad == pytest.approx(0.2)


def test_the_yaw_window_is_closed_again_when_the_stage_returns():
    ctx = simple_ctx(detections=False)

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert not ctx.failsafe.yaw_authority.is_open


def test_the_advanced_search_still_pins_yaw_to_zero():
    ctx = simple_ctx(detections=False)
    ctx.params.timeouts.search_timeout_sec = 1.0

    LateralSearchStep().execute(ctx)

    assert all(vyaw == 0.0 for *_rest, vyaw in twists(ctx))
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_simple_mode_steps.py -q 2>&1 | tail -12
```

Expected: FAIL (o Step 2 ainda translada no modo Simples).

- [ ] **Step 3: Implementar em `steps/search.py`**

Imports: `import contextlib`, `from mvp_mission_bebop.controllers.yaw_pattern import YawRateLimiter`. No `__init__`: `self._yaw_ramp: Optional[YawRateLimiter] = None` (antes do `super().__init__`).

Em `execute`, depois de `self._assert_search_tilt(ctx)` e antes de criar `deadline`:

```python
        simple = self._mode is MissionMode.SIMPLE
        if simple:
            self._begin_yaw_scan(ctx)
```

Substituir o bloco `with ctx.failsafe.altitude_hold_window(...), ctx.perception.session(...)` por:

```python
        yaw_scope = (
            ctx.failsafe.yaw_window(ctx.params.yaw_pattern.max_vyaw)
            if simple
            else contextlib.nullcontext()
        )
        with ctx.failsafe.altitude_hold_window(
            ctx.params.governor.climb_authority
        ), yaw_scope, ctx.perception.session(
            conf=vision_cfg.confidence_threshold, imgsz=vision_cfg.inference_imgsz
        ):
```

e proteger o ramo de assentamento de partida: `if not simple and getattr(ctx.params.platform, "edge_ahead_m", None) is not None:` (o drone não translada, a guarda de borda de plataforma e a espera de partida não se aplicam).

Os logs de abertura e de invariantes passam a depender do modo: no Simples,

```python
            logger.info(
                "Yaw scan engaged: window %.1f s, rate %.3f normalized (about %.1f deg/s), "
                "slew %.2f /s. Translation is disabled.",
                timeout,
                yaw_cfg.scan_vyaw,
                math.degrees(yaw_cfg.scan_vyaw * ctx.params.dead_reckoning.yaw_rate_per_unit_rad_s),
                yaw_cfg.scan_ramp_per_sec,
            )
            logger.info(
                "Invariants: vx == 0.0, vy == 0.0, vyaw >= 0.0 (left) bounded at %.3f by the yaw "
                "window, vz bounded by altitude hold at %.2f m.",
                yaw_cfg.max_vyaw,
                ctx.params.relative_target_altitude_m,
            )
```

(`yaw_cfg = ctx.params.yaw_pattern`; os dois logs originais ficam no ramo `else`.)

Novos métodos:

```python
    def _begin_yaw_scan(self, ctx: MissionContext) -> None:
        """Arm the scan ramp and record the heading Stage 4 will restore.

        Stage 1 records the heading at the end of the climb. A run that starts
        at this stage (``--stages 2,...``) has none, so it is taken here, before
        the first rotation.
        """
        self._yaw_ramp = YawRateLimiter(ctx.params.yaw_pattern.scan_ramp_per_sec)
        blackboard = getattr(ctx, "blackboard", None)
        if blackboard is not None and getattr(blackboard, "initial_yaw_rad", None) is None:
            blackboard.initial_yaw_rad = float(ctx.odom_supervisor.snapshot().yaw)

    def _yaw_command(self, ctx: MissionContext, dt: float, target_vyaw: float) -> None:
        """Ramp toward ``target_vyaw`` and transmit it with every translation channel at zero.

        The governor still runs, so altitude hold is unchanged; its horizontal
        feedforward is zero because the airframe does not translate.
        """
        vyaw = self._yaw_ramp.step(target_vyaw, dt)
        vz = ctx.governor.compute_vz(
            ctx.odom_supervisor.snapshot().relative_altitude, dt, vx_commanded=0.0
        )
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, vyaw)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(0.0, 0.0)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)

    def _yaw_halt(self, ctx: MissionContext, rate: Optional[LoopRate], dt: float) -> None:
        """Decelerate the rotation to exactly zero, paced like the translational halt."""
        yaw_cfg = ctx.params.yaw_pattern
        cycles = int(math.ceil(yaw_cfg.scan_vyaw / max(yaw_cfg.scan_ramp_per_sec * max(dt, 1e-3), 1e-6))) + 2
        for _ in range(cycles):
            self._yaw_command(ctx, dt, 0.0)
            if abs(self._yaw_ramp.value) <= 1e-9:
                break
            if rate is not None:
                rate.tick()
```

Ramificação nos três métodos de movimento existentes (início de cada um):

- `_cruise`: `if self._mode is MissionMode.SIMPLE: self._yaw_command(ctx, dt, ctx.params.yaw_pattern.scan_vyaw); return`
- `_command`: `if self._mode is MissionMode.SIMPLE: self._yaw_command(ctx, dt, 0.0); return` (só chega aqui com alvo candidato, demanda zero)
- `_halt`: `if self._mode is MissionMode.SIMPLE: self._yaw_halt(ctx, rate, dt); return`

`_cruise_until_confirmed` e `_act` ficam inalterados.

- [ ] **Step 4: Rodar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_simple_mode_steps.py test/test_lateral_search.py test/test_departure_settle.py test/test_lateral_altitude_parity.py -q 2>&1 | tail -12
```

Expected: PASS.

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `feat(search): add the yaw-only scan to the Simple mission profile`.

---

## Task 6: Step 3 do modo Simples (rastreio por yaw)

**Files:**
- Modify: `mvp_mission_bebop/steps/tracking.py`
- Test: `test/test_simple_mode_steps.py`

**Interfaces:**
- Consumes: `ServoCommand.vyaw`, `YawServoingController` (injetado em `ctx.visual_controller` pelo `mission.py`), `ctx.failsafe.yaw_window`.
- Produces: com `mode=SIMPLE`, o step transmite `vyaw = command.vyaw` (limitado pela autoridade) com `vx == vy == 0.0`; o Twist de lock tem `vyaw == 0.0`; a janela de yaw fecha ao sair.

- [ ] **Step 1: Escrever os testes que falham** (acrescentar)

```python
# ------------------------------------------------------------------ Stage 3


class YawDrone:
    """Records Twists and turns the shared odometry heading by the commanded rate."""

    def __init__(self, ctx: Ctx, period: float) -> None:
        self.commands: List[Tuple[float, float, float, float]] = []
        self._ctx = ctx
        self._period = period
        self.no_fly = False

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.commands.append((vx, vy, vz, vyaw))
        self._ctx.odom_supervisor.yaw += vyaw * YAW_RATE_PER_UNIT * self._period

    def camera_control(self, tilt, pan=0.0):
        return None

    def land(self):
        return True


def test_the_yaw_servo_never_translates_and_stays_inside_the_window():
    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = True

    VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    commands = twists(ctx)
    assert commands
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in commands)
    assert all(abs(vyaw) <= ctx.params.yaw_pattern.max_vyaw + 1e-9 for *_rest, vyaw in commands)
    assert not ctx.failsafe.yaw_authority.is_open


def test_the_fixed_scene_target_right_of_centre_drives_a_negative_yaw():
    # The shared fake detects the target at x=428 of 856 (the frame centre); move it right.
    from test_stage_altitude_wiring import Detection, DetectionResult

    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = True
    ctx.detect = lambda _frame, conf=0.5, imgsz=None: DetectionResult([Detection(center=(700.0, 240.0))])

    VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert any(vyaw < 0.0 for *_rest, vyaw in twists(ctx))
    assert all(vyaw <= 0.0 for *_rest, vyaw in twists(ctx))


def test_the_lock_cycle_leaves_the_last_twist_at_zero_yaw():
    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = True

    VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.blackboard.approach_finished is True
    assert twists(ctx)[-1][3] == 0.0


def test_without_a_confirmed_target_the_stage_is_skipped_without_commands():
    ctx = simple_ctx(detections=False)
    ctx.blackboard.target_confirmed = False

    assert VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert ctx.blackboard.approach_finished is False
    assert twists(ctx) == []


def test_the_lateral_servo_of_the_other_profiles_still_transmits_zero_yaw():
    ctx = Ctx(detections=True)
    ctx.blackboard.target_confirmed = True

    VisualServoingStep().execute(ctx)

    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)
```

(`Ctx` usa `self.detector = self`, então sobrescrever `ctx.detect` na instância posiciona o alvo à direita do centro (x=700 em 856 px) sem tocar no fake compartilhado. A classe `YawDrone` fica disponível para a Task 7, que a instala em `ctx.drone`.)

- [ ] **Step 2: Rodar e ver falhar** (`vyaw` ainda é forçado a zero pelo step).

- [ ] **Step 3: Implementar em `steps/tracking.py`**

Imports: `import contextlib`, `from mvp_mission_bebop.mission_mode import MissionMode`. Em `execute`, trocar o `with` por:

```python
            yaw_scope = (
                ctx.failsafe.yaw_window(ctx.params.yaw_pattern.max_vyaw)
                if self._mode is MissionMode.SIMPLE
                else contextlib.nullcontext()
            )
            with ctx.failsafe.altitude_hold_window(
                ctx.params.governor.climb_authority
            ), yaw_scope, ctx.perception.session(
                conf=vision_cfg.confidence_threshold, imgsz=vision_cfg.inference_imgsz
            ):
                approach_finished = self._servo(ctx, deadline, rate, tracker)
```

Em `_apply`, trocar `clamp_kinematics(vz, 0.0)` por `clamp_kinematics(vz, command.vyaw)` e atualizar o docstring: "A lei lateral emite `vyaw == 0.0`, então o canal permanece em zero; a lei de yaw do perfil Simples emite o giro aqui, e a autoridade de yaw (`failsafe.yaw_window`) o limita. `vx` continua literal 0.0." O `clamp_translation(0.0, command.vy)` não muda (`vy == 0.0` na lei de yaw). `_hold` não muda: ele envia `vyaw` zero, que é exatamente o congelamento. O docstring do módulo ganha um parágrafo sobre o perfil Simples (a lei de erro de pixel em yaw, `ctx.visual_controller` injetado pelo `mission.py`).

- [ ] **Step 4: Rodar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_simple_mode_steps.py test/test_lateral_servoing.py test/test_stage3_integration.py test/test_stage3_parameter_authority.py test/test_centering_recovery.py -q 2>&1 | tail -12
```

Expected: PASS.

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `feat(tracking): add yaw servoing to the Simple mission profile`.

---

## Task 7: Step 4 do modo Simples (retorno do yaw e gimbal a -80)

**Files:**
- Modify: `mvp_mission_bebop/steps/inspection.py`
- Test: `test/test_simple_mode_steps.py`

**Interfaces:**
- Consumes: `YawHeadingController`, `ctx.blackboard.initial_yaw_rad`, `ctx.odom_supervisor.snapshot().yaw`, `ctx.params.rtl.camera_tilt_deg`.
- Produces: no modo Simples, depois do hover e do ACK nativo, o step restaura o heading e aponta o gimbal para `rtl.camera_tilt_deg`; retorna `SUCCESS`, `ABORTED` (interrupção) ou `SUCCESS` com aviso (referência ausente ou janela esgotada). Não avalia saúde do sistema durante o retorno.

- [ ] **Step 1: Escrever os testes que falham** (acrescentar)

```python
# ------------------------------------------------------------------ Stage 4


def inspection_ctx(*, offset_deg: float) -> Ctx:
    ctx = simple_ctx(detections=True)
    ctx.params.kinematics.hover_duration_sec = 0.2
    ctx.drone = YawDrone(ctx, period=1.0 / ctx.params.kinematics.control_loop_hz)
    ctx.drone.camera_calls = []
    ctx.drone.camera_control = lambda tilt, pan=0.0: ctx.drone.camera_calls.append(tilt)
    ctx.blackboard.initial_yaw_rad = 0.5
    ctx.odom_supervisor.yaw = 0.5 + math.radians(offset_deg)
    return ctx


@pytest.mark.parametrize("offset_deg", [60.0, -60.0, 170.0])
def test_the_heading_is_restored_by_the_short_way_after_the_capture(offset_deg):
    ctx = inspection_ctx(offset_deg=offset_deg)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0

    status = NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    error = abs((ctx.odom_supervisor.yaw - 0.5 + math.pi) % (2 * math.pi) - math.pi)
    assert error <= math.radians(ctx.params.yaw_pattern.return_tolerance_deg) + 1e-6
    assert ctx.drone.commands[-1][3] == 0.0
    assert not ctx.failsafe.yaw_authority.is_open


def test_the_gimbal_goes_to_the_return_tilt_at_the_end_of_the_stage():
    ctx = inspection_ctx(offset_deg=20.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0

    NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.drone.camera_calls[-1] == ctx.params.rtl.camera_tilt_deg
    assert ctx.current_tilt_deg == ctx.params.rtl.camera_tilt_deg


def test_no_yaw_is_commanded_before_the_capture_and_hover_are_done():
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0

    NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    first_turn = next(i for i, (*_rest, vyaw) in enumerate(ctx.drone.commands) if vyaw != 0.0)
    assert first_turn > 0
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands[:first_turn])


def test_a_missing_reference_heading_skips_the_restore_but_still_lowers_the_gimbal():
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.blackboard.initial_yaw_rad = None

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)
    assert ctx.drone.camera_calls[-1] == ctx.params.rtl.camera_tilt_deg


def test_a_restore_that_runs_out_of_window_does_not_fail_the_mission():
    ctx = inspection_ctx(offset_deg=170.0)
    ctx.params.yaw_pattern.return_timeout_sec = 0.05

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert ctx.drone.commands[-1][3] == 0.0


def test_an_abort_during_the_restore_stops_the_rotation():
    ctx = inspection_ctx(offset_deg=170.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    original = ctx.drone.move_velocity

    def abort_after_the_first_turn(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        original(vx, vy, vz, vyaw, duration)
        if vyaw != 0.0:
            ctx.emergency_event.set()

    ctx.drone.move_velocity = abort_after_the_first_turn

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.ABORTED
    assert ctx.drone.commands[-1][3] == 0.0


def test_the_advanced_inspection_never_yaws_and_ends_at_the_nadir_tilt():
    ctx = Ctx(detections=True)
    ctx.params.kinematics.hover_duration_sec = 0.2

    NadirInspectionStep().execute(ctx)

    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)
```

- [ ] **Step 2: Rodar e ver falhar.**

- [ ] **Step 3: Implementar em `steps/inspection.py`**

Imports: `from mvp_mission_bebop.controllers.yaw_pattern import YawHeadingController`, `from mvp_mission_bebop.mission_mode import MissionMode`. Em `_inspect`, no final, substituir o `return StepStatus.SUCCESS` por:

```python
        if self._mode is MissionMode.SIMPLE:
            status = self._restore_heading(ctx)
            if status is not StepStatus.SUCCESS:
                return status
            self._point_gimbal(ctx, float(ctx.params.rtl.camera_tilt_deg))

        return StepStatus.SUCCESS
```

Novo método (junto de `_hover`):

```python
    def _restore_heading(self, ctx: MissionContext) -> StepStatus:
        """Turn back to the heading recorded at the end of Stage 1 (Simple profile).

        Runs after the evidence is recorded, so a heading fault can never cost
        the photograph. The yaw window is open only for this loop and the last
        command sent is an explicit zero, because the Bebop latches its last
        Twist.

        System health is deliberately not evaluated here. Vision is suspended
        after the capture, so the frame heartbeat is stale by construction; the
        hover loop above has the same property and Stage 5 re-arms the
        heartbeat on entry. The loop is bounded by ``yaw_pattern.return_timeout_sec``
        and by the abort check instead.

        Returns
        -------
        StepStatus
            ``ABORTED`` on an interrupt; ``SUCCESS`` otherwise, including when
            no reference heading was recorded or the window expired. Stage 5
            centres on the marker from whatever heading was reached.
        """
        reference = getattr(ctx.blackboard, "initial_yaw_rad", None)
        if reference is None:
            logger.warning("No takeoff heading was recorded; the heading restore is skipped.")
            return StepStatus.SUCCESS

        yaw_cfg = ctx.params.yaw_pattern
        controller = YawHeadingController(yaw_cfg)
        controller.reset()
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(yaw_cfg.return_timeout_sec)
        logger.info(
            "Restoring the takeoff heading %.1f deg: tolerance %.1f deg, window %.1f s.",
            math.degrees(float(reference)),
            yaw_cfg.return_tolerance_deg,
            yaw_cfg.return_timeout_sec,
        )
        try:
            with ctx.failsafe.yaw_window(yaw_cfg.max_vyaw):
                while deadline.active:
                    if ctx.interrupted():
                        return StepStatus.ABORTED
                    dt = min(rate.tick(), MAX_INTERVAL_SEC)
                    snapshot = ctx.odom_supervisor.snapshot()
                    command = controller.update(float(snapshot.yaw), float(reference), dt)
                    self._transmit_yaw(ctx, command.vyaw, dt)
                    if command.settled:
                        logger.info(
                            "Heading restored after %.1f s (residual %.1f deg).",
                            deadline.elapsed_sec,
                            math.degrees(command.error_rad),
                        )
                        return StepStatus.SUCCESS
            logger.warning(
                "Heading not restored within %.1f s; continuing from the heading reached.",
                yaw_cfg.return_timeout_sec,
            )
            return StepStatus.SUCCESS
        finally:
            self._hold(ctx)
            logger.info("Heading loop cadence: %s.", rate.cadence_report())

    @staticmethod
    def _transmit_yaw(ctx: MissionContext, vyaw: float, dt: float) -> None:
        """Send a yaw command through the failsafe with every translation channel at zero."""
        vz = ctx.governor.compute_vz(
            ctx.odom_supervisor.snapshot().relative_altitude, dt, vx_commanded=0.0
        )
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, vyaw)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)
```

Conferir que `math`, `MAX_INTERVAL_SEC`, `Deadline` e `LoopRate` já são importados no módulo (são, pelo `_frame_with_gimbal`/`_hover`); importar `math` se faltar. O `finally: self._hold(ctx)` executa depois que a janela de yaw já fechou: o `vyaw` dele é zero de qualquer forma. A janela de altitude (`altitude_hold_window`) aberta pelo `execute` cobre o `vz` do governador.

- [ ] **Step 4: Rodar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_simple_mode_steps.py test/test_inspection_hover.py test/test_gimbal_framing.py test/test_nadir_freeze.py test/test_picture_ack.py -q 2>&1 | tail -12
```

Expected: PASS (testes de hover sensíveis a carga: reexecutar isolados antes de classificar falha).

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `feat(inspection): restore the takeoff heading and lower the gimbal at the end of the Simple profile`.

---

## Task 8: Step 5 dos modos Intermediário e Simples

**Files:**
- Modify: `mvp_mission_bebop/steps/rtl.py` (`ClosedLoopRTLStep.execute` e novos métodos)
- Test: `test/test_simple_mode_steps.py`

**Interfaces:**
- Consumes: `_touchdown`, `_halt_translation`, `_center_over_marker`, `_engage_return_attitude`, `_build_centering`, `_build_sensor`, `_PhaseOutcome` (todos existentes).
- Produces: `execute` roteia por modo antes de qualquer fase de retorno. Intermediário: `_land_in_place`. Simples: `_land_over_base`. O Avançado permanece sem alteração.

- [ ] **Step 1: Escrever os testes que falham** (acrescentar; reaproveitar o `LandingDrone`/`landing_ctx` de `test/test_recovery_and_touchdown.py`)

```python
# ------------------------------------------------------------------ Stage 5

from test_recovery_and_touchdown import LandingDrone, landing_ctx


def land_ctx(mode_name: str, **drone_kwargs):
    drone = LandingDrone(**drone_kwargs)
    ctx = landing_ctx(drone)
    ctx.params.mission_mode = mode_name
    return drone, ctx


def test_the_intermediate_profile_lands_in_place_without_building_a_marker_sensor():
    drone, ctx = land_ctx("intermediate")
    ctx.params.rtl.target_aruco_id = 10_000  # not a valid marker: must be irrelevant here

    class ExplodingSensor:
        def __getattr__(self, name):
            raise AssertionError("the intermediate profile must not touch the marker sensor")

    step = ClosedLoopRTLStep(sensor=ExplodingSensor(), mode=MissionMode.INTERMEDIATE)
    status = step.execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.blackboard.touchdown_confirmed is True
    assert drone.land_calls >= 1
    assert all(vyaw == 0.0 for *_rest, vyaw in drone.velocity_calls)


def test_the_intermediate_profile_does_not_announce_a_return():
    drone, ctx = land_ctx("intermediate")
    milestones = []
    original = rtl_module.emit_milestone
    rtl_module.emit_milestone = lambda key, payload=None: milestones.append((key, payload))
    try:
        ClosedLoopRTLStep(mode=MissionMode.INTERMEDIATE).execute(ctx)
    finally:
        rtl_module.emit_milestone = original

    keys = [key for key, _payload in milestones]
    assert "mission.rtl_start" not in keys
    landing = [payload for key, payload in milestones if key == "mission.landing"]
    assert landing and landing[0]["at_base"] is False
    touchdown = [payload for key, payload in milestones if key == "mission.touchdown"]
    assert touchdown and touchdown[0]["at_base"] is False


def test_the_simple_profile_without_a_marker_sensor_lands_in_place_flagged_as_base():
    drone, ctx = land_ctx("simple")
    milestones = []
    original = rtl_module.emit_milestone
    rtl_module.emit_milestone = lambda key, payload=None: milestones.append((key, payload))
    try:
        step = ClosedLoopRTLStep(mode=MissionMode.SIMPLE)
        step._build_sensor = lambda _ctx: None
        status = step.execute(ctx)
    finally:
        rtl_module.emit_milestone = original

    assert status is StepStatus.SUCCESS
    keys = [key for key, _payload in milestones]
    assert "mission.rtl_start" not in keys
    assert not any(vx != 0.0 or vy != 0.0 for vx, vy, *_rest in drone.velocity_calls)
    touchdown = [payload for key, payload in milestones if key == "mission.touchdown"]
    assert touchdown and touchdown[0]["at_base"] is True


class StubSensor:
    target_id = 8
    backend = "stub"
    failures = 0

    def observe(self, _frame):
        return None

    def overlay_geometry(self, _frame):
        return []


def test_the_simple_profile_never_flies_the_reverse_search():
    drone, ctx = land_ctx("simple")
    ctx.params.rtl.centering_timeout_sec = 0.1
    step = ClosedLoopRTLStep(sensor=StubSensor(), mode=MissionMode.SIMPLE)
    step._reverse_search = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("the simple profile has no reverse search")
    )

    assert step.execute(ctx) is StepStatus.SUCCESS
    assert all(vy == 0.0 for _vx, vy, *_rest in drone.velocity_calls if True)
    assert all(vyaw == 0.0 for *_rest, vyaw in drone.velocity_calls)


def test_the_advanced_profile_is_unchanged():
    # The historical Stage 5 tests (test_aruco_rtl.py, test_lateral_rtl.py) cover the
    # advanced flow end to end; this pins the default constructor to it.
    assert ClosedLoopRTLStep().mode is MissionMode.ADVANCED
```

(`LandingDrone` em `test_recovery_and_touchdown.py:286-310` define `land()` e `move_velocity(...)`. Os testes acima assumem que ele expõe `land_calls` (contador de `land()`) e `velocity_calls` (lista de tuplas `(vx, vy, vz, vyaw)`); conferir os nomes reais no fake e usar os dele. Se ele não registrar essas chamadas, estender o fake de forma aditiva (acrescentar as duas listas sem alterar o comportamento existente) e rodar `test_recovery_and_touchdown.py` para confirmar que nada mudou.)

- [ ] **Step 2: Rodar e ver falhar.**

- [ ] **Step 3: Implementar em `steps/rtl.py`**

Em `execute`, imediatamente depois de `guidance.reset()` e do `warn_if_uncalibrated`, e ANTES de `centering = self._build_centering(ctx)`:

```python
        if self._mode is MissionMode.INTERMEDIATE:
            return self._land_in_place(ctx, guidance)
```

e logo depois de `self._active_sensor = sensor` (a linha `sensor = None if centering is None else (...)` já existe):

```python
        if self._mode is MissionMode.SIMPLE:
            return self._land_over_base(ctx, guidance, centering, sensor)
```

Os dois métodos novos, depois de `_engage_return_attitude`:

```python
    def _land_in_place(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Intermediate profile: land vertically where the inspection ended.

        No return leg, no marker sensor and no ``mission.rtl_start`` milestone:
        that milestone is narrated as "iniciando retorno à base" and nothing here
        returns. ``mission.landing`` and ``mission.touchdown`` carry
        ``at_base: False``, which selects the in-place phrasing.
        """
        logger.info("Intermediate profile: no return leg; landing at the inspection point.")
        self._announce("Pouso no local", "inspeção concluída, executando pouso no local da inspeção")
        self._halt_translation(ctx)
        return self._touchdown(ctx, guidance, at_base=False)

    def _land_over_base(
        self,
        ctx: MissionContext,
        guidance: RTLGuidanceController,
        centering: Optional[ArucoCenteringController],
        sensor: Optional[ArucoMarkerSensor],
    ) -> StepStatus:
        """Simple profile: centre on the pad marker and land; the airframe never left it.

        The reverse search and the odometric return are skipped. Position by
        optical flow is not trusted after the yaw manoeuvres of Stages 2 to 4,
        and it is not needed: the airframe has not translated. Without a marker
        sensor the aircraft lands where it hovers, flagged ``at_base`` for the
        same reason. A marker that never appears is handled by the centering
        window of ``rtl.centering_timeout_sec``, after which it lands as well.

        Returns
        -------
        StepStatus
            ``ABORTED`` / ``FAILURE`` on an interrupt or a health fault, else the
            result of the verified touchdown.
        """
        rtl_cfg = ctx.params.rtl
        self._engage_return_attitude(ctx)

        if sensor is None or centering is None:
            logger.warning(
                "Marker-guided centering unavailable; landing in place (the airframe has not "
                "left the launch pad)."
            )
            self._halt_translation(ctx)
            return self._touchdown(ctx, guidance, at_base=True)

        logger.info(
            "Simple profile: centring on marker %d (dictionary %s, tag %.3f m, gimbal %.1f deg), "
            "window %.1f s; no reverse search.",
            rtl_cfg.target_aruco_id,
            rtl_cfg.marker_dict,
            rtl_cfg.tag_size,
            rtl_cfg.camera_tilt_deg,
            rtl_cfg.centering_timeout_sec,
        )
        self._announce(
            "Centralizando sobre a base",
            "centralizando sobre o marcador da base para o pouso de precisão",
        )
        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            outcome = self._center_over_marker(ctx, centering, sensor)

        if outcome is _PhaseOutcome.ABORTED:
            return StepStatus.ABORTED
        if outcome is _PhaseOutcome.FAILED:
            return StepStatus.FAILURE
        if outcome is _PhaseOutcome.EXHAUSTED:
            logger.warning(
                "Centering window of %.1f s expired; landing from the position held.",
                rtl_cfg.centering_timeout_sec,
            )
        else:
            self._announce(
                "Centralizado sobre a base",
                "aeronave centralizada sobre o marcador, iniciando pouso de precisão",
            )
        return self._touchdown(ctx, guidance, at_base=True)
```

Importar `MissionMode`. O Step 5 não abre janela de yaw: o `_transmit_centering` continua passando `0.0` e a autoridade fechada garante zero.

- [ ] **Step 4: Rodar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_simple_mode_steps.py test/test_aruco_rtl.py test/test_lateral_rtl.py test/test_recovery_and_touchdown.py test/test_rtl_guidance.py test/test_rtl_guidance_bugfix.py test/test_battery_rtl_jump.py -q 2>&1 | tail -15
```

Expected: PASS. `test_battery_rtl_jump.py` confirma que o salto de bateria ao Step 5 continua funcionando (nos modos reduzidos o "retorno" por bateria é pouso, como descrito nas decisões).

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `feat(rtl): land in place for the intermediate profile and centre-and-land for the simple profile`.

---

## Task 9: Integração emulada dos três modos (`--fly` no emulador e `--no-fly`)

**Files:**
- Create: `test/test_mission_modes_emulated.py`

**Interfaces:**
- Consumes: `EmulatedFlight`, `SHORT_FLIGHT` (`test/support/fake_bebop/harness.py`); `FlightResult.steps()`, `.commands()`, `.nonzero_twists()`, `.lines`, `.emulator`.

Regras de execução: um lote por vez (domínios 80-99), com a ativação do ambiente. O emulador renderiza o alvo de uma imagem fixa, então o Step 3 não converge (termina degradado): estes testes verificam sequência, sinais e segurança, não convergência.

- [ ] **Step 1: Escrever os testes**

```python
"""Integration: the three mission profiles against the driver emulator.

One run per profile, shared by the assertions on it. Checks sequence, command
signs and safety; the emulator's fixed scene does not let Stage 3 converge.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Tuple

import pytest

from support.fake_bebop.harness import EmulatedFlight, FlightResult, SHORT_FLIGHT

PROFILES: List[Tuple[str, int]] = [("advanced", 94), ("intermediate", 95), ("simple", 96)]


def document(mode: str) -> Dict:
    doc = copy.deepcopy(SHORT_FLIGHT)
    doc["mission_mode"] = mode
    return doc


@pytest.fixture(scope="module", params=PROFILES, ids=[mode for mode, _ in PROFILES])
def run(request, tmp_path_factory):
    mode, domain = request.param
    flight = EmulatedFlight(str(tmp_path_factory.mktemp(f"mode_{mode}")), domain=domain, seed=1)
    with flight:
        return mode, flight.fly(document(mode), fly=True, timeout=240.0)


def test_every_profile_runs_the_five_stage_markers_in_order(run):
    mode, result = run
    assert result.exit_code == 0, f"{mode} exited {result.exit_code}:\n{result.tail()}"
    assert result.steps() == [1, 2, 3, 4, 5]
    assert result.watchdog.get("violations", 0) == 0


def test_every_profile_takes_off_once_and_ends_on_the_ground(run):
    mode, result = run
    assert len(result.commands("takeoff")) == 1, mode
    assert result.commands("land"), f"{mode} never requested a landing"


def test_the_intermediate_profile_never_flies_the_return_leg(run):
    mode, result = run
    if mode != "intermediate":
        pytest.skip("intermediate only")
    text = "\n".join(line.text for line in result.lines)
    assert "Executing terminal landing" in text
    assert "Centering on marker" not in text
    assert "rtl_start" not in text


def test_the_simple_profile_never_translates_after_takeoff(run):
    mode, result = run
    if mode != "simple":
        pytest.skip("simple only")
    starts = {step: next(l.mono for l in result.lines if f"[STEP {step}:" in l.text) for step in range(2, 6)}
    flown = [
        record for record in result.nonzero_twists()
        if starts[2] <= record["mono"]
    ]
    assert flown, "the simple profile never rotated"
    assert all(abs(r["linear"][0]) == 0.0 and abs(r["linear"][1]) == 0.0 for r in flown)


def test_yaw_appears_only_in_the_simple_profile(run):
    mode, result = run
    yawed = [r for r in result.nonzero_twists() if r["angular"][2] != 0.0]
    if mode == "simple":
        assert yawed
    else:
        assert not yawed, f"{mode} transmitted a yaw command"
```

(Os nomes dos campos de `nonzero_twists()` registros (`mono`, `linear`, `angular`) e de `commands(...)` devem ser conferidos em `FlightResult` (`support/fake_bebop/harness.py`, `nonzero_twists`, `commands`) e adaptados exatamente; `test_fly_path_emulated.py` mostra o formato `(linear.x, linear.y, linear.z, angular.z)` usado por `_admissible`. Se `nonzero_twists()` devolver tuplas, usar os índices 0, 1, 3 em vez de chaves.)

- [ ] **Step 2: Rodar UM lote, sozinho**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
pgrep -fa "fake_bebop|test_fly_path" || echo "no emulated batch running"
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_modes_emulated.py -v 2>&1 | tail -25
```

Expected: PASS. Falhas de timing em carga: reexecutar a variação isolada com `-k simple` antes de classificar.

- [ ] **Step 3: Regressão do Avançado no emulador (um lote, depois do anterior terminar)**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_fly_path_emulated.py test/test_fly_path_totem.py -q 2>&1 | tail -15
```

Expected: PASS sem alteração dos testes (o Avançado é o default).

- [ ] **Step 4: Checkpoint (sem executar)** Mensagem proposta: `test(mission): emulated flights for the three mission profiles`.

---

## Task 10: Frontend, biblioteca de modos, esquema e bloqueios de lançamento

**Files:**
- Create: `bebop_mission_control/src/lib/missionMode.ts`, `bebop_mission_control/src/lib/missionMode.test.ts`
- Modify: `src/lib/parameterSchema.ts`, `src/lib/launchDocument.ts`, `src/lib/parameterConflicts.ts`
- Test: `src/lib/missionMode.test.ts`, `src/lib/launchDocument.test.ts` (acrescentar casos)

**Interfaces:**
- Produces (`missionMode.ts`):
  - `type MissionMode = 'simple' | 'intermediate' | 'advanced'`
  - `MISSION_MODES: ReadonlyArray<{ id: MissionMode; label: string; summary: string }>` na ordem Simples, Intermediária, Avançada
  - `DEFAULT_MISSION_MODE: MissionMode = 'advanced'`; `MISSION_MODE_PATH = 'mission_mode'`
  - `readMissionMode(doc: unknown): MissionMode` (ausente, vazio ou inválido vira `'advanced'`)
  - `isPathActive(mode: MissionMode, path: string): boolean`
  - `stageLabel(mode: MissionMode, step: number, fallback: string): string`
- Produces (`parameterSchema.ts`): `Common.modes?: ReadonlyArray<MissionMode>`; `Common.hints?` e `ParameterGroup.summaries?: Partial<Record<MissionMode, string>>`; `groupsForMode(mode): ParameterGroup[]` (itens filtrados, grupos vazios removidos, textos por modo resolvidos); `'mission_mode'` em `TRACKED_PATHS`.
- Produces: `launchBlockers(doc: unknown): string[]` passa a ler o modo do próprio `doc` e ignora caminhos inativos; `activeConflicts(doc: unknown): ParameterConflict[]` em `parameterConflicts.ts`. `fillMissing(doc, factory, paths?)` e `BACKFILL_PATHS` em `launchDocument.ts`. `yaw_pattern.scan_vyaw` entra em `PARAMETER_GROUPS` (cartão `envelope`, `modes: ['simple']`).

Caminhos inativos por modo (`isPathActive` retorna `false`):

- `intermediate`: `rtl.timeout_sec`, `rtl.reverse_cruise_velocity`, `rtl.target_aruco_id`, `rtl.marker_dict`, `rtl.tag_size`, `rtl.centering_timeout_sec`, `rtl.camera_tilt_deg`.
- `simple`: `rtl.timeout_sec`, `rtl.reverse_cruise_velocity`, `kinematics.forward_cruise_velocity`.
- `advanced`: `yaw_pattern.scan_vyaw`.

Acrescentar `yaw_pattern.scan_vyaw` também à lista do `intermediate`. Esse caminho é um parâmetro obrigatório de lançamento (todo item de `ALL_PARAMETERS` entra em `REQUIRED_LAUNCH_NUMBERS`), então só pode bloquear um voo no modo Simples.

- [ ] **Step 1: Escrever os testes que falham**

`src/lib/missionMode.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { PARAMETER_GROUPS, groupsForMode, TRACKED_PATHS } from './parameterSchema';
import { DEFAULT_MISSION_MODE, MISSION_MODES, isPathActive, readMissionMode, stageLabel } from './missionMode';

describe('readMissionMode', () => {
  it('defaults to the full mission when the document says nothing', () => {
    expect(DEFAULT_MISSION_MODE).toBe('advanced');
    expect(readMissionMode({})).toBe('advanced');
    expect(readMissionMode(null)).toBe('advanced');
    expect(readMissionMode({ mission_mode: '' })).toBe('advanced');
  });

  it('reads a valid mode and refuses to guess for an invalid one', () => {
    expect(readMissionMode({ mission_mode: 'simple' })).toBe('simple');
    expect(readMissionMode({ mission_mode: 'intermediate' })).toBe('intermediate');
    expect(readMissionMode({ mission_mode: 'expert' })).toBe('advanced');
    expect(readMissionMode({ mission_mode: 3 })).toBe('advanced');
  });
});

describe('MISSION_MODES', () => {
  it('lists Simples, Intermediária, Avançada in that order', () => {
    expect(MISSION_MODES.map((m) => m.id)).toEqual(['simple', 'intermediate', 'advanced']);
    expect(MISSION_MODES.map((m) => m.label)).toEqual(['Simples', 'Intermediária', 'Avançada']);
  });
});

describe('parameter filtering by mode', () => {
  const paths = (mode: Parameters<typeof groupsForMode>[0]) =>
    groupsForMode(mode).flatMap((g) => g.items.map((i) => i.path));

  const everyField = PARAMETER_GROUPS.flatMap((g) => g.items.map((i) => i.path));

  it('shows every group and field of the original sheet in the advanced mode', () => {
    expect(groupsForMode('advanced')).toHaveLength(PARAMETER_GROUPS.length);
    expect(paths('advanced')).toEqual(everyField.filter((p) => p !== 'yaw_pattern.scan_vyaw'));
  });

  it('drops the return timeout and the turn rate in the intermediate mode', () => {
    const shown = paths('intermediate');
    expect(shown).not.toContain('rtl.timeout_sec');
    expect(shown).not.toContain('yaw_pattern.scan_vyaw');
    expect(shown).toContain('timeouts.search_timeout_sec');
    expect(shown).toContain('kinematics.countdown_sec');
    expect(shown).toContain('kinematics.forward_cruise_velocity');
    expect(everyField.filter((p) => !shown.includes(p)).sort()).toEqual(
      ['rtl.timeout_sec', 'yaw_pattern.scan_vyaw'].sort()
    );
  });

  it('swaps the cruise velocity for the turn rate in the simple mode', () => {
    const shown = paths('simple');
    expect(shown).not.toContain('rtl.timeout_sec');
    expect(shown).not.toContain('kinematics.forward_cruise_velocity');
    expect(shown).toContain('yaw_pattern.scan_vyaw');
    expect(shown).toContain('gimbal.search_tilt_deg');
    expect(shown).toContain('gimbal.nadir_tilt_deg');
    expect(shown).toContain('timeouts.search_timeout_sec');
    expect(shown).toContain('kinematics.countdown_sec');
  });

  it('keeps the three group cards in every mode', () => {
    for (const mode of ['simple', 'intermediate', 'advanced'] as const) {
      expect(groupsForMode(mode).map((g) => g.id)).toEqual(['envelope', 'gimbal', 'timeouts']);
    }
  });

  it('tracks the mode as a document edit', () => {
    expect(TRACKED_PATHS).toContain('mission_mode');
  });
});

describe('isPathActive', () => {
  it('treats every flight path as active in the advanced mode, the turn rate excepted', () => {
    expect(isPathActive('advanced', 'rtl.timeout_sec')).toBe(true);
    expect(isPathActive('advanced', 'rtl.target_aruco_id')).toBe(true);
    expect(isPathActive('advanced', 'yaw_pattern.scan_vyaw')).toBe(false);
    expect(isPathActive('intermediate', 'yaw_pattern.scan_vyaw')).toBe(false);
    expect(isPathActive('simple', 'yaw_pattern.scan_vyaw')).toBe(true);
  });

  it('deactivates the marker fields only in the intermediate mode', () => {
    expect(isPathActive('intermediate', 'rtl.target_aruco_id')).toBe(false);
    expect(isPathActive('intermediate', 'rtl.tag_size')).toBe(false);
    expect(isPathActive('simple', 'rtl.target_aruco_id')).toBe(true);
    expect(isPathActive('simple', 'rtl.centering_timeout_sec')).toBe(true);
  });
});

describe('stageLabel', () => {
  it('relabels the last stage by mode and leaves the others alone', () => {
    expect(stageLabel('advanced', 5, 'Retornando base')).toBe('Retornando base');
    expect(stageLabel('intermediate', 5, 'Retornando base')).toBe('Pouso no local');
    expect(stageLabel('simple', 5, 'Retornando base')).toBe('Pouso na base');
    expect(stageLabel('simple', 2, 'Varredura')).toBe('Varredura');
  });
});
```

Acrescentar a `src/lib/launchDocument.test.ts` (usar `completeDocument`/`validDocument` de `src/lib/__fixtures__/missionDocument`, como os demais testes do arquivo):

```ts
describe('launchBlockers by mission mode', () => {
  it('does not block an intermediate launch on a field it never uses', () => {
    const doc = { ...completeDocument(), mission_mode: 'intermediate' } as Record<string, unknown>;
    const broken = setPath(setPath(doc, 'rtl.timeout_sec', 9999), 'rtl.target_aruco_id', -4);
    expect(launchBlockers(broken)).toEqual([]);
  });

  it('still blocks the same document in the advanced mode', () => {
    const doc = { ...completeDocument(), mission_mode: 'advanced' } as Record<string, unknown>;
    const broken = setPath(doc, 'rtl.timeout_sec', 9999);
    expect(launchBlockers(broken)).toContain('rtl.timeout_sec');
  });

  it('keeps blocking a simple launch on a marker the landing needs', () => {
    const doc = { ...completeDocument(), mission_mode: 'simple' } as Record<string, unknown>;
    const broken = setPath(doc, 'rtl.target_aruco_id', -4);
    expect(launchBlockers(broken)).toContain('rtl.target_aruco_id');
  });
});
```

(Importar `setPath` de `./paths` e `launchBlockers` de `./launchDocument` se ainda não importados no arquivo de teste.)

- [ ] **Step 2: Rodar e ver falhar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/missionMode.test.ts src/lib/launchDocument.test.ts 2>&1 | tail -15
```

Expected: FAIL (módulo `missionMode` inexistente).

- [ ] **Step 3: Criar `src/lib/missionMode.ts`**

```ts
/**
 * The three mission profiles the pre-flight screen offers.
 *
 * Mirrors `mvp_mission_bebop/mission_mode.py`: the value travels in the launch
 * document as `mission_mode` and the mission resolves it once, after the go.
 * All three fly the five stages; only what stages 2 to 5 do differs, so the
 * cockpit, the stage bar and the `[STEP N:` markers are shared.
 */
import { getPath } from './paths';

export type MissionMode = 'simple' | 'intermediate' | 'advanced';

export const MISSION_MODE_PATH = 'mission_mode';

export const DEFAULT_MISSION_MODE: MissionMode = 'advanced';

export interface MissionModeDescriptor {
  id: MissionMode;
  label: string;
  /** One line, shown under the selector. */
  summary: string;
}

export const MISSION_MODES: ReadonlyArray<MissionModeDescriptor> = [
  {
    id: 'simple',
    label: 'Simples',
    summary: 'Gira sobre o próprio eixo até achar o sinistro, fotografa e pousa na base. Não se desloca.',
  },
  {
    id: 'intermediate',
    label: 'Intermediária',
    summary: 'Missão completa até a foto; depois pousa onde a inspeção terminou, sem retorno.',
  },
  {
    id: 'advanced',
    label: 'Avançada',
    summary: 'Missão completa: varredura, inspeção, retorno pelo marcador ArUco e pouso na base.',
  },
];

/** The mode a launch document asks for; anything unreadable is the full mission. */
export function readMissionMode(doc: unknown): MissionMode {
  const raw = getPath(doc, MISSION_MODE_PATH);
  return raw === 'simple' || raw === 'intermediate' || raw === 'advanced' ? raw : DEFAULT_MISSION_MODE;
}

const INACTIVE_PATHS: Readonly<Record<MissionMode, ReadonlySet<string>>> = {
  advanced: new Set(['yaw_pattern.scan_vyaw']),
  intermediate: new Set([
    'yaw_pattern.scan_vyaw',
    'rtl.timeout_sec',
    'rtl.reverse_cruise_velocity',
    'rtl.target_aruco_id',
    'rtl.marker_dict',
    'rtl.tag_size',
    'rtl.centering_timeout_sec',
    'rtl.camera_tilt_deg',
  ]),
  simple: new Set(['rtl.timeout_sec', 'rtl.reverse_cruise_velocity', 'kinematics.forward_cruise_velocity']),
};

/**
 * Whether a document path matters to a flight in `mode`.
 *
 * An inactive path stays in the document with its stored value; it is only
 * hidden from the sheet and left out of the launch checks, so an operator is
 * never blocked by a field they cannot see.
 */
export function isPathActive(mode: MissionMode, path: string): boolean {
  return !INACTIVE_PATHS[mode].has(path);
}

const LAST_STAGE_LABEL: Readonly<Partial<Record<MissionMode, string>>> = {
  intermediate: 'Pouso no local',
  simple: 'Pouso na base',
};

/** Name of stage `step` in `mode`; only the last stage changes. */
export function stageLabel(mode: MissionMode, step: number, fallback: string): string {
  return step === 5 ? LAST_STAGE_LABEL[mode] ?? fallback : fallback;
}
```

- [ ] **Step 4: `parameterSchema.ts`**

Acrescentar em `Common`:

```ts
  /** Modes in which the field is shown and checked. Absent means every mode. */
  modes?: ReadonlyArray<MissionMode>;
  /** Replaces `hint` in the modes named. */
  hints?: Partial<Record<MissionMode, string>>;
```

e em `ParameterGroup`: `summaries?: Partial<Record<MissionMode, string>>;`. Importar `type MissionMode` de `./missionMode` (importar só o tipo, sem ciclo: `missionMode.ts` importa apenas `paths`).

Anotar os itens:

- `kinematics.forward_cruise_velocity`: `modes: ['advanced', 'intermediate']`.
- `rtl.timeout_sec`: `modes: ['advanced']`.
- Novo item, no FIM da lista `items` do grupo `envelope` (a ordem dentro do cartão não afeta os outros modos, pois ele só aparece no Simples):

```ts
      {
        kind: 'number',
        path: 'yaw_pattern.scan_vyaw',
        label: 'Velocidade de Giro',
        hint: 'Taxa de giro da varredura, normalizada (0,15 equivale a cerca de 15 graus por segundo). Uma volta completa leva 360 dividido pela taxa em graus por segundo.',
        min: 0.05,
        max: 0.3,
        step: 0.01,
        precision: 2,
        modes: ['simple'],
      },
```

  O teto 0,30 coincide com `yaw_pattern.max_vyaw` (a janela de yaw do backend), e o piso 0,05 com `min_effective_vyaw`: nenhum valor do slider é rejeitado por `validate_yaw_pattern`.
- `timeouts.search_timeout_sec`: `hints: { simple: 'Tempo máximo girando em busca do sinistro. Sem alvo confirmado neste tempo, a aeronave fotografa a referência e pousa.', intermediate: 'Sem sinistro confirmado neste tempo, a varredura encerra, a aeronave fotografa a referência e pousa onde estiver.' }`.
- Grupo `timeouts`: `summaries: { simple: 'Quanto tempo a busca por giro pode durar, e a contagem antes da decolagem', intermediate: 'Quanto tempo a busca pode durar, e a contagem antes da decolagem' }`.
- Grupo `envelope`, `summaries: { simple: 'Altitude e estabilização após a decolagem' }`.

Função nova:

```ts
/** The cards of the sheet for `mode`: items the mode does not use removed, texts resolved, empty cards dropped. */
export function groupsForMode(mode: MissionMode): ParameterGroup[] {
  return PARAMETER_GROUPS.map((group) => ({
    ...group,
    summary: group.summaries?.[mode] ?? group.summary,
    items: group.items
      .filter((item) => !item.modes || item.modes.includes(mode))
      .map((item) => ({ ...item, hint: item.hints?.[mode] ?? item.hint })),
  })).filter((group) => group.items.length > 0);
}
```

`TRACKED_PATHS`: acrescentar `MISSION_MODE_PATH` (importar de `./missionMode`). `ALL_PARAMETERS`, `PARAMETER_GROUPS` e os `mirrors` permanecem como estão (presets, "Restaurar Padrões" e `reconcileCoupledPaths` continuam operando em todos os campos, também nos ocultos).

**Documento antigo sem `yaw_pattern`.** `bmg:get-parameters` devolve o `mission_config.json` como está, e um arquivo gravado antes desta mudança não tem `yaw_pattern.scan_vyaw`; como o caminho é obrigatório no modo Simples, o lançamento ficaria bloqueado por um campo recém-criado. Em `launchDocument.ts`:

```ts
/** Schema paths introduced after a store may have been written; filled from the factory document when absent. */
export const BACKFILL_PATHS: readonly string[] = ['yaw_pattern.scan_vyaw'];

/** `doc` with each of `paths` taken from `factory` when `doc` does not hold a finite number there. */
export function fillMissing<T extends Doc>(doc: T, factory: unknown, paths: readonly string[] = BACKFILL_PATHS): T {
  let next = doc;
  for (const path of paths) {
    if (finite(getPath(next, path))) continue;
    const value = getPath(factory, path);
    if (finite(value)) next = setPath(next, path, value);
  }
  return next;
}
```

Em `useMissionParameters.ts`, no `then` que recebe `defaults.params` (`setFactory(defaults.params)`), aplicar `fillMissing` a `committed` e a `working` com o mesmo resultado (assim a folha não fica suja nem diverge do armazenado): `setCommitted((prev) => (prev ? fillMissing(prev, defaults.params) : prev)); setWorking((prev) => (prev ? fillMissing(prev, defaults.params) : prev));`. Conferir os nomes reais dos setters no hook. Teste (em `launchDocument.test.ts`): `fillMissing({}, factoryDocument())` devolve `yaw_pattern.scan_vyaw === 0.15`; um documento que já tem o valor não é alterado (mesma referência); um valor não finito é substituído.

- [ ] **Step 5: `launchDocument.ts` e `parameterConflicts.ts`**

Em `launchBlockers`, depois de montar `blocked` (e antes do `return`), filtrar:

```ts
  const mode = readMissionMode(doc);
  return blocked.filter((path) => isPathActive(mode, path));
```

e, no laço de `blockingConflicts(doc)`, ignorar conflitos cujos caminhos estão todos inativos: trocar `for (const conflict of blockingConflicts(doc))` por `for (const conflict of activeConflicts(doc))` onde `activeConflicts` vem de `parameterConflicts.ts`. Atualizar também o docstring de `launchBlockers` com um parágrafo sobre o modo.

Em `parameterConflicts.ts`:

```ts
/**
 * The conflicts that matter to the mission mode of `doc`: a conflict is kept
 * unless every parameter it names is inactive in that mode. A conflict that
 * names no path is always kept.
 */
export function activeConflicts(doc: unknown): ParameterConflict[] {
  const mode = readMissionMode(doc);
  return evaluateParameterConflicts(doc).filter(
    (conflict) => conflict.paths.length === 0 || conflict.paths.some((path) => isPathActive(mode, path))
  );
}
```

`blockingConflicts` passa a derivar de `activeConflicts` (mantendo o filtro `severity === 'error'`). Importar `readMissionMode`, `isPathActive`. Verificar que `evaluateParameterConflicts` continua sendo o ponto de teste de `test/fixtures/parameter_conflict_cases.json` (o espelho Python `parameter_conflicts.py` NÃO muda; o filtro por modo é só da estação).

Usos a atualizar: `src/App.tsx` (`commitLaunchDocument` usa `blockingConflicts(doc)`, que agora já é filtrado), `src/hooks/useMissionParameters.ts` (`conflicts = evaluateParameterConflicts(working)` passa a `activeConflicts(working)`) e `ParameterSheet.tsx` (`evaluateParameterConflicts(working)` passa a `activeConflicts(working)`), para que o bloqueio de salvar e a marcação na folha concordem com o que o modo usa.

- [ ] **Step 6: Rodar**

```bash
npx vitest run src/lib 2>&1 | tail -20 && npm run typecheck 2>&1 | tail -10
```

Expected: PASS. Os testes de `parameterConflicts` e `launchDocument` existentes usam documentos sem `mission_mode` (modo avançado): comportamento idêntico.

- [ ] **Step 7: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): add mission mode library, schema filtering and mode-aware launch checks`.

---

## Task 11: Seletor de modo acima do `LaunchDial` e persistência

**Files:**
- Create: `src/components/preflight/MissionModeSelector.tsx`, `src/components/preflight/MissionModeSelector.test.tsx`
- Modify: `src/components/preflight/LaunchDial.tsx`, `src/components/preflight/PreflightScreen.tsx`, `src/components/preflight/LaunchDial.test.tsx`

**Files (acréscimo):** `src/App.tsx` (trava do seletor).

**Interfaces:**
- Consumes: `MISSION_MODES`, `MissionMode` (Task 10); `props.onEdit(path, value)` e o documento de trabalho de `PreflightScreen` (o mesmo mecanismo do interruptor `no_fly`: `props.onEdit('no_fly', ...)`).
- Produces: `MissionModeSelector({ value: MissionMode; onChange: (mode: MissionMode) => void; disabled?: boolean })`; `PreflightScreenProps.modeLocked?: boolean`; `LaunchDialProps.above?: React.ReactNode` renderizado ACIMA do botão "INICIAR".

- [ ] **Step 1: Escrever os testes que falham**

`MissionModeSelector.test.tsx` (mesmo padrão jsdom de `LaunchDial.test.tsx`):

```tsx
// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MissionModeSelector } from './MissionModeSelector';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const radios = () => Array.from(host.querySelectorAll<HTMLElement>('[role="radio"]'));

describe('MissionModeSelector', () => {
  it('offers the three modes in order inside a radiogroup', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    expect(host.querySelector('[role="radiogroup"]')).not.toBeNull();
    expect(radios().map((r) => r.textContent)).toEqual(['Simples', 'Intermediária', 'Avançada']);
  });

  it('marks exactly the active mode', () => {
    act(() => root.render(<MissionModeSelector value="intermediate" onChange={() => undefined} />));
    expect(radios().map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false']);
  });

  it('reports the mode the operator picks', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    act(() => radios()[0].click());
    expect(onChange).toHaveBeenCalledWith('simple');
  });

  it('does not re-report the active mode', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    act(() => radios()[2].click());
    expect(onChange).not.toHaveBeenCalled();
  });

  it('is inert when disabled', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} disabled />));
    act(() => radios()[0].click());
    expect(onChange).not.toHaveBeenCalled();
  });

  it('describes the active mode under the control', () => {
    act(() => root.render(<MissionModeSelector value="simple" onChange={() => undefined} />));
    expect(host.textContent).toContain('Não se desloca');
  });
});
```

Acrescentar a `LaunchDial.test.tsx`:

```tsx
describe('LaunchDial slot above the dial', () => {
  it('renders the slot before the launch button in document order', () => {
    act(() => {
      root.render(
        <LaunchDial
          ready={true}
          blockedReason={null}
          benchMode={false}
          onLaunch={() => undefined}
          above={<div data-testid="mode-slot">modo</div>}
        />
      );
    });
    const slot = host.querySelector('[data-testid="mode-slot"]')!;
    const button = host.querySelector('button')!;
    expect(slot.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
```

- [ ] **Step 2: Rodar e ver falhar** (`npx vitest run src/components/preflight/MissionModeSelector.test.tsx src/components/preflight/LaunchDial.test.tsx`).

- [ ] **Step 3: Criar `MissionModeSelector.tsx`**

Um `div role="radiogroup" aria-label="Modo de missão"` com um botão `role="radio"` por modo, estilo "pílula segmentada" consistente com os botões "Parâmetros de voo"/"Bancada" já existentes (`rounded-full border backdrop-blur-md`, ativo com `border-mint/45 bg-mint/[0.08] text-frost`, inativo com `border-frost/15 bg-abyss/45 text-frost/85`, `font-cond text-sm tracking-wide`). Abaixo, um `<p className="text-2xs text-haze">` com o `summary` do modo ativo. Navegação por teclado: setas esquerda/direita movem a seleção (`onKeyDown`), `tabIndex={active ? 0 : -1}` (padrão roving tabindex). Quando `disabled`, `aria-disabled` e cliques ignorados. Chamar `onChange` só quando o modo clicado difere de `value`. Seguir o guia visual do projeto (`frontend-design` / tokens existentes em `tailwind.config.js`); nenhum emoji, ícones só via `lucide-react` se necessário.

- [ ] **Step 4: Slot no `LaunchDial` e uso no `PreflightScreen`**

`LaunchDial.tsx`: adicionar `above?: React.ReactNode;` em `LaunchDialProps` (com docstring: "Control seated directly above the dial; the mission profile lives here because it is the last thing an operator decides before pressing it."), desestruturar `above` e renderizar `{above}` como primeiro filho do `div className="flex flex-col items-center gap-5"`, antes do `<button>`.

`PreflightScreen.tsx`: importar `readMissionMode`, `MISSION_MODE_PATH`, `MissionModeSelector`; derivar `const missionMode = readMissionMode(working)` (usar a mesma variável do documento de trabalho que o componente já usa para `benchMode`; localizar por `benchMode`) e passar:

```tsx
          <LaunchDial
            ...
            above={
              <MissionModeSelector
                value={missionMode}
                onChange={(mode) => props.onEdit(MISSION_MODE_PATH, mode)}
              />
            }
```

Persistência: `onEdit` escreve no documento de trabalho; `'mission_mode'` está em `TRACKED_PATHS`, logo conta como alteração, `commitLaunchDocument` salva em `mission_config.json` antes do lançamento (mesmo caminho do `no_fly`) e `launchParamsJson(doc, ...)` envia o documento inteiro, incluindo `mission_mode`, tanto no `--params-json` quanto no `go` do standby. Nenhuma mudança em `electron/main.cjs` é necessária para o envio (`parseLaunchDocument` repassa o objeto inteiro).

- [ ] **Step 4b: Travar o seletor a partir do clique em INICIAR**

O operador não pode trocar o modo depois de clicar em INICIAR. Em `PreflightScreen.tsx` acrescentar a prop opcional `modeLocked?: boolean` (docstring: "True from the click on the launch dial until the mission ends; the profile is part of the document already sent") e repassá-la: `<MissionModeSelector ... disabled={props.modeLocked} />`.

Em `src/App.tsx`:

```tsx
  const [launchPending, setLaunchPending] = useState(false);
  const modeLocked = launchPending || counting || mission.state === 'arming' || mission.state === 'running';
```

No começo de `launch` (logo depois do guard `launching.current`, ANTES de `await commitLaunchDocument()`): `setLaunchPending(true);`; no `finally` existente: `setLaunchPending(false);`. Ao passar `modeLocked` para `<PreflightScreen modeLocked={modeLocked} ... />`, o seletor fica inerte durante o commit do documento, durante a contagem (`counting` já é setado antes de `mission.launch`) e enquanto a missão estiver armando ou em voo; destrava quando o lançamento é recusado (erro ou bloqueio) e quando a missão termina. Conferir os nomes reais em `App.tsx` (`counting`, `mission.state`, o `try/finally` de `launch`); o arquivo foi modificado por outra sessão, reler antes de editar. A trava vale também para a bancada (`runBenchStage`), que usa o mesmo documento: aplicar `setLaunchPending(true/false)` do mesmo modo.

Teste (em `src/App.launchParams.test.tsx`, no padrão do arquivo): clicar no mostrador, e antes de `mission.launch` resolver, verificar que todos os `[role="radio"]` têm `aria-disabled="true"` e que um clique em outro modo não altera `mission_mode` no `paramsJson` enviado. Se o harness do arquivo não permitir pausar `mission.launch`, cobrir apenas a prop: `PreflightScreen` com `modeLocked` renderiza o seletor com `aria-disabled="true"`.

- [ ] **Step 5: Teste de contrato do payload** (acrescentar a `src/App.launchParams.test.tsx`, seguindo o padrão do arquivo)

```tsx
it('sends the mission mode with the launch document', async () => {
  // Arrange the working document with mission_mode = 'simple' through the same
  // helpers the neighbouring tests use, launch, and read the paramsJson the bridge received.
  // Expected: JSON.parse(paramsJson).mission_mode === 'simple'
});
```

Preencher o corpo copiando a estrutura do teste vizinho que já verifica `paramsJson` (o arquivo foi modificado por outra sessão: reler antes de editar). A asserção obrigatória é `expect(JSON.parse(received.paramsJson).mission_mode).toBe('simple')`. Se o arquivo não oferecer helper para fixar o documento de trabalho, verificar o contrato no nível de `launchParamsJson`: `expect(JSON.parse(launchParamsJson({ mission_mode: 'simple' }, 20)).mission_mode).toBe('simple')` em `src/lib/batteryFailsafe.test.ts`.

- [ ] **Step 6: Rodar**

```bash
npx vitest run src/components/preflight src/lib src/App.launchParams.test.tsx 2>&1 | tail -20 && npm run typecheck 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 7: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): add the mission mode selector above the launch dial`.

---

## Task 12: Folha de parâmetros filtrada por modo e botão Avançadas oculto na Intermediária

**Files:**
- Modify: `src/components/preflight/ParameterSheet.tsx`
- Test: `src/components/preflight/ParameterSheet.test.tsx` (acrescentar)

**Interfaces:**
- Consumes: `groupsForMode`, `readMissionMode`, `activeConflicts` (Task 10).
- Produces: a folha renderiza `groupsForMode(readMissionMode(working))`; o botão "Configurações Avançadas" não é renderizado no modo Intermediária (e o diálogo, se estiver aberto no momento da troca, fecha); nos modos Simples e Avançada ele permanece. `AdvancedSettingsDialog.tsx` não muda.

- [ ] **Step 1: Escrever os testes que falham** (acrescentar a `ParameterSheet.test.tsx`, usando o `render` e `button` já definidos no arquivo)

```tsx
describe('ParameterSheet by mission mode', () => {
  const withMode = (mode: string) => ({ ...completeDocument(), mission_mode: mode } as Record<string, unknown>);
  const labels = () => Array.from(container.querySelectorAll('section')).map((s) => s.textContent ?? '');
  const text = () => container.textContent ?? '';

  it('shows every group and field in the advanced mode', () => {
    render(withMode('advanced'), completeDocument());
    expect(text()).toContain('Envelope de Voo');
    expect(text()).toContain('Gimbal & Câmera');
    expect(text()).toContain('Limites de Tempo');
    expect(text()).toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Giro');
  });

  it('hides the return timeout and the turn rate in the intermediate mode', () => {
    render(withMode('intermediate'), completeDocument());
    expect(text()).not.toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Giro');
    expect(text()).toContain('Timeout de Procura/Varredura');
    expect(text()).toContain('Contagem Regressiva');
    expect(text()).toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Gimbal & Câmera');
  });

  it('swaps the cruise velocity for the turn rate in the simple mode', () => {
    render(withMode('simple'), completeDocument());
    expect(text()).not.toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Velocidade de Giro');
    expect(text()).toContain('Inclinação Inicial de Varredura');
    expect(text()).toContain('Ângulo da Câmera Apontada para Baixo');
    expect(text()).toContain('Timeout de Procura/Varredura');
    expect(text()).toContain('Contagem Regressiva');
  });

  it('offers the advanced settings in the simple and advanced modes only', () => {
    const hasButton = () =>
      Array.from(container.querySelectorAll('button')).some((b) => b.textContent?.includes('Configurações Avançadas'));
    render(withMode('advanced'), completeDocument());
    expect(hasButton()).toBe(true);
    render(withMode('simple'), completeDocument());
    expect(hasButton()).toBe(true);
    render(withMode('intermediate'), completeDocument());
    expect(hasButton()).toBe(false);
  });

  it('closes an open advanced dialog when the mode becomes intermediate', () => {
    render(withMode('advanced'), completeDocument());
    act(() => button('Configurações Avançadas').click());
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    render(withMode('intermediate'), completeDocument());
    expect(container.textContent).not.toContain('Janela de centragem');
  });

  it('still restores every field, hidden ones included', () => {
    const defaults = completeDocument();
    const onEdit = render(withMode('simple'), defaults);
    act(() => button('Restaurar Padrões').click());
    for (const spec of ALL_PARAMETERS) expect(onEdit).toHaveBeenCalledWith(spec.path, getPath(defaults, spec.path));
  });
});
```

(A função `labels` é opcional; remover se não usada, para não deixar variável morta.) Os `render` sucessivos dentro de um mesmo teste reutilizam o `root`; o React re-renderiza no mesmo contêiner, o que é suficiente aqui.

- [ ] **Step 2: Rodar e ver falhar.**

- [ ] **Step 3: Implementar em `ParameterSheet.tsx`**

- Importar `groupsForMode` e `readMissionMode`, `activeConflicts` (substituindo `evaluateParameterConflicts` do `useMemo`).
- No componente: `const missionMode = readMissionMode(working); const groups = useMemo(() => groupsForMode(missionMode), [missionMode]);` e iterar `groups` em vez de `PARAMETER_GROUPS` na grade (`{groups.map((group, index) => (<GroupCard ... />))}`).
- Ajustar a grade para não quebrar com menos cartões: manter `lg:grid-cols-3` (os três grupos permanecem em todos os modos; só os itens variam).
- `restoreDefaults` continua percorrendo `PARAMETER_GROUPS` (todos os campos), como o teste exige.
- No cabeçalho, sob o título, acrescentar uma linha `text-2xs text-haze` com o modo ativo: `Modo: {rótulo}` (rótulo vindo de `MISSION_MODES`), para o operador saber por que campos sumiram.

- Botão "Configurações Avançadas": renderizar somente quando `missionMode !== 'intermediate'`. O diálogo é renderizado por `advancedOpen && missionMode !== 'intermediate'`, de modo que uma troca de modo com o diálogo aberto o fecha sem estado residual. Os valores de marcador continuam no documento e `launchBlockers` os ignora no modo Intermediária (Task 10), então nenhum campo oculto bloqueia o voo.
- No teste "closes an open advanced dialog", o texto "Janela de centragem" deve ser o rótulo real do campo `rtl.centering_timeout_sec` em `AdvancedSettingsDialog.tsx`; conferir o rótulo e ajustar a string do teste, e conferir também se o contêiner do diálogo expõe `role="dialog"` (o diálogo da folha em `PreflightScreen` expõe; o avançado pode não expor, caso em que basta a asserção sobre o texto).

- [ ] **Step 4: Rodar**

```bash
npx vitest run src/components/preflight 2>&1 | tail -20 && npm run typecheck 2>&1 | tail -10
```

Expected: PASS, incluindo os testes antigos da folha (documentos sem `mission_mode` = avançado).

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): filter the parameter sheet by mission mode`.

---

## Task 13: Rótulo do estágio 5 por modo (StageBar, cabeçalho e evento do Electron)

**Files:**
- Modify: `src/components/cockpit/StageBar.tsx`, `src/types/mission.ts`, `electron/main.cjs` (`STEP_NAMES`), `src/App.tsx` (ou o ponto que fornece `stageName` ao cockpit), `src/components/cockpit/CockpitScreen.tsx` se ele repassar o nome
- Test: `src/lib/missionMode.test.ts` (já cobre `stageLabel`) e um teste de renderização do `StageBar` por modo

**Interfaces:**
- Consumes: `stageLabel`, `readMissionMode`.
- Produces: `StageBar` aceita `missionMode?: MissionMode` (default `'advanced'`) e usa `stageLabel(missionMode, step, label)` para o rótulo; `STEP_NAMES[5]` do Electron permanece `'Retornando base'` (evento genérico) e o renderer substitui o nome exibido pelo rótulo do modo.

- [ ] **Step 1: Teste que falha** — criar `src/components/cockpit/StageBar.mode.test.tsx` no padrão jsdom dos testes vizinhos (`AbortControl.test.tsx` mostra as props mínimas), renderizando `StageBar` com `stage={5}`, `state="running"`, `missionMode="intermediate"` e esperando o texto `Pouso no local` e a ausência de `Retornando base`; repetir para `simple` (`Pouso na base`) e para o default (`Retornando base`). Fornecer as demais props obrigatórias com valores neutros (`benchMode={false}`, `benchStage={null}`, `pendingStage={null}`, funções `() => undefined`, `voice` conforme o tipo `VoiceLevel`).

- [ ] **Step 2: Rodar e ver falhar.**

- [ ] **Step 3: Implementar**

`StageBar.tsx`: nova prop opcional `missionMode?: MissionMode`; no `map` das pílulas usar `stageLabel(missionMode ?? 'advanced', stage.step, stage.label)`. O `stageName` recebido por prop (cabeçalho) é calculado no chamador: em `App.tsx`, onde `stageName` é derivado do passo corrente (localizar o uso de `MISSION_STAGES` ou `stepName`), aplicar `stageLabel(readMissionMode(params.working ?? params.committed), step, nome)`. O modo a usar no cockpit é o do documento enviado ao lançamento; como a folha não é editável durante o voo, ler do documento de trabalho é suficiente. `src/types/mission.ts`: nada a mudar em `MISSION_STAGES` (continua a lista do modo avançado; o rótulo por modo é resolvido na camada de exibição). `electron/main.cjs`: nenhuma alteração obrigatória; `bmg:step-change` continua enviando `stepNumber`.

- [ ] **Step 4: Rodar**

```bash
npx vitest run src/components/cockpit 2>&1 | tail -15 && npm run typecheck 2>&1 | tail -8
```

Expected: PASS.

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): label the last stage by mission mode`.

---

## Task 14: Validação completa e relatório

**Files:** nenhum arquivo de produto (apenas execução e relatório). Rodar nesta ordem; UM lote de voo emulado por vez.

- [ ] **Step 1: Suíte backend completa**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py -q 2>&1 | tail -6
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/ -q --ignore=test/test_fly_path_emulated.py --ignore=test/test_fly_path_totem.py --ignore=test/test_mission_modes_emulated.py 2>&1 | tail -15
```

Expected: 100% verde (o protocolo do repositório exige `test_contracts.py` e `test_aruco_rtl.py` antes de tudo). Falha de teste sensível a carga: reexecutar isolada; se persistir, comparar com a linha de base congelada (Task 0, `PYTHONPATH=.superpowers/baseline-modes`).

- [ ] **Step 2: Voos emulados (um lote de cada vez, na ordem)**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_modes_emulated.py -q 2>&1 | tail -10
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_fly_path_emulated.py test/test_fly_path_totem.py -q 2>&1 | tail -10
```

- [ ] **Step 3: Frontend**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npm test 2>&1 | tail -15 && npm run typecheck 2>&1 | tail -8 && npm run build 2>&1 | tail -8
```

- [ ] **Step 4: Auditoria de persistência do Electron (manual, sem BMG nem missão em execução)**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop && node test/audit_parameter_persistence.cjs 2>&1 | tail -20
```

Expected: o campo `mission_mode` faz round-trip (edit, save, reopen) e chega ao `--params-json`. Se a auditoria enumerar campos e reclamar do novo, estendê-la para incluí-lo.

- [ ] **Step 5: Ciclo de bancada `--no-fly` de cada modo** (CLI, sem hardware; o simulador cinemático integra yaw e o `[NO-FLY]` mantém os motores inertes). Um modo por vez, aguardando terminar:

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/mvp_mission_bebop
source /home/jv/ros2_ws/bin/nectar-activate
for MODE in advanced intermediate simple; do
  echo "=== $MODE ==="
  python3 -m mvp_mission_bebop.mission --no-fly --mode "$MODE" --countdown 3 \
    --params-json '{"timeouts":{"search_timeout_sec":10,"tracking_timeout_sec":6},"rtl":{"timeout_sec":12,"centering_timeout_sec":6,"touchdown_timeout_sec":8},"kinematics":{"hover_duration_sec":1.0}}' \
    2>&1 | grep -E "STEP [1-5]:|Mission mode|Yaw scan|Restoring the takeoff heading|Heading restored|terminal landing|Touchdown|Centering on marker|Intermediate profile|Simple profile|Traceback|ERROR" | head -40
  echo "exit: ${PIPESTATUS[0]}"
done
```

Expected por modo: cinco marcadores `[STEP N: ...]` em ordem; `Mission mode: <modo>`; Avançado com `Centering on marker`; Intermediário com `Intermediate profile: no return leg` e sem `Centering on marker`; Simples com `Yaw scan engaged`, `Restoring the takeoff heading`, `Simple profile: centring on marker` e sem busca reversa; nenhum `Traceback`. Anexar as linhas ao relatório. Um modo inválido deve abortar na partida:

```bash
python3 -m mvp_mission_bebop.mission --no-fly --params-json '{"mission_mode":"expert"}' 2>&1 | tail -3
```

Expected: `SystemExit` com `mission_mode: mission mode 'expert' is not one of: simple, intermediate, advanced`, sem tocar o drone.

- [ ] **Step 6: Verificação do contrato visual com o BMG** (ferramenta de browser/Electron do projeto, `npm run` do dev server conforme `package.json`): abrir o pré-voo, conferir (a) o seletor acima do círculo "INICIAR" com os três modos e o ativo destacado; (b) a folha de parâmetros nos três modos conforme a tabela da Task 12, incluindo o slider "Velocidade de Giro" só no Simples e a ausência do botão "Configurações Avançadas" na Intermediária; (b2) que o seletor fica inerte a partir do clique em INICIAR e destrava se o lançamento for recusado; (c) iniciar uma missão em bancada em cada modo e observar a barra de estágios percorrendo 1 a 5 com o rótulo do estágio 5 correto. Registrar capturas de tela no relatório.

- [ ] **Step 7: Checklist de voo real (documentar, não executar sem o usuário)**: antes do primeiro voo do modo Simples, com a aeronave presa/baixa altitude, verificar em log: (1) sinal do yaw (comando `+vyaw` deve aumentar `snapshot.yaw` e girar à esquerda), (2) que o gimbal em `search_tilt_deg` deixa o alvo visível durante a rotação, (3) leitura de `horizontal_speed` durante o giro (se espúria, o `airframe_at_rest` do lock e o `_await_stillness` do Step 4 podem atrasar), (4) retorno de yaw dentro de `return_tolerance_deg` e (5) ArUco visível a `rtl.camera_tilt_deg` sobre a base. Ajustar `yaw_pattern.*` conforme os logs.

- [ ] **Step 7b: Registrar a exceção no `CLAUDE.md` do repositório.** A seção "Arquitetura de Voo" afirma que o padrão é estritamente lateral, com `vx == 0` e `vyaw == 0` em todo o voo. Isso continua verdadeiro nos modos Intermediária e Avançada, mas o modo Simples gira por desenho. Sem registro, uma sessão futura pode tratar o yaw do Simples como regressão e removê-lo. Acrescentar ao fim da seção 1, em texto seco e sem emojis, um parágrafo com: (1) os três modos e o que cada um muda nos estágios 2 a 5; (2) que `vyaw` só é autorizado dentro de `FailsafeSupervisor.yaw_window` / `YawAuthority`, aberta somente pelos Steps 2, 3 e 4 do modo Simples, e fechada por padrão no proxy e no failsafe; (3) que o modo Simples não usa odometria horizontal. Reler o arquivo imediatamente antes de editar (outras sessões o alteram) e editar somente esse parágrafo, sem reformatar o resto.

- [ ] **Step 8: Relatório final ao usuário** com: resumo técnico das alterações por arquivo, resultado de cada suíte (contagem de testes e tempo), as linhas de bancada por modo, divergências da linha de base, decisões 1 a 8 desta seção e o risco aberto do sinal de yaw. Perguntar ao usuário se autoriza os commits propostos (lista dos checkpoints, na ordem das tasks) e o `git push origin main`; só executar com autorização explícita.

---

## Self-Review (feita pelo arquiteto)

**1. Cobertura da especificação**

| Requisito | Tarefa |
|---|---|
| Seletor Simples/Intermediária/Avançada acima do círculo, estado persistido, `mission_mode` no payload | 10, 11 |
| Folha de parâmetros: Avançado completo | 10, 12 |
| Intermediário: remove timeout de retorno, mantém procura e contagem; remove o botão Avançadas (decisão do usuário) | 10, 12 |
| Seletor travado a partir do clique em INICIAR (decisão do usuário) | 11 |
| Simples: oculta cruzeiro (troca pelo slider de giro), mantém gimbal, procura, contagem, avançadas | 10, 12 |
| `--mode` / `mission_mode` em `--params-json` / `mission_config.json`, default `advanced` | 1 |
| Intermediário: Step 5 pousa no local com `_touchdown(at_base=False)` | 8 |
| Simples Step 2: `vx = vy = 0`, giro à esquerda até a YOLO | 5 |
| Simples Step 3: PID de yaw com `vx = vy = 0` | 3, 6 |
| Simples Step 4: gimbal ao `nadir_tilt_deg`, captura, retorno do yaw, gimbal a -80 | 7 (o gimbal ao nadir e a captura são o fluxo existente) |
| Simples Step 5: centragem ArUco e pouso | 8 |
| Marcadores `[STEP N: ...]` nos três modos, reuso de máquina de estados/atuadores | 4, 9, 14 |
| Testes Vitest (seletor acima do dial, filtragem por modo), `npm test` e `typecheck` | 10, 11, 12, 14 |
| Testes pytest dos modos Simples e Intermediário, suíte completa verde | 5 a 9, 14 |
| Validação `--no-fly` dos 3 modos | 14 |
| Resumo técnico e relatório de testes | 14, Step 8 |

Lacuna conhecida e declarada: o pedido diz "yaw para a orientação inicial ($yaw \to 0^\circ$)"; o plano usa o heading de decolagem gravado (`initial_yaw_rad`), que é o que "orientação inicial de decolagem" significa e é robusto à referência de yaw da odometria não ser zero na partida.

**2. Placeholders:** três pontos dependem de nomes que só o código vivo confirma (atributos do `LandingDrone` na Task 8, campos de `FlightResult.nonzero_twists()` na Task 9, helper de documento de trabalho em `App.launchParams.test.tsx` na Task 11). Cada um traz a instrução exata de verificação, a alternativa aditiva e a asserção obrigatória. Não há "TBD" nem passo sem conteúdo.

**3. Consistência de tipos:** `MissionMode` (Python) e `MissionMode` (TS) usam os mesmos três valores; `YawAuthority.window/clamp/ceiling/is_open`, `FailsafeSupervisor.yaw_window`, `ServoCommand.vyaw`, `YawServoingController.compute/note_target_lost/reset/phase/locked`, `YawHeadingController.update -> HeadingCommand(vyaw, error_rad, settled)`, `YawRateLimiter.step/value/reset` e `build_steps(mode)` são referidos com a mesma assinatura em todas as tarefas; `blackboard.initial_yaw_rad` é gravado na Task 4 (Step 1) e na Task 5 (partial run) e lido na Task 7.

**4. Review Focus:** itens 1 (Task 1), 2 (Tasks 2, 5, 6, 7, 9), 3 (Tasks 5/7/9: o teste `test_the_yaw_scan_...` roda sem detecções e o emulado termina com a cadeia completa), 4 (Tasks 3 e 7) e 5 (Task 8) têm teste dono.
