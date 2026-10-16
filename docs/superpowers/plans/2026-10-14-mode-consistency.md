# Consistência dos Modos de Missão, Parâmetros Isolados, Copiloto e Ctrl+C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provar e garantir que o modo escolhido na interface é o modo voado, tornar a decolagem do modo Simples nativa, falar no copiloto só o que cabe ao modo ativo, isolar parâmetros e favoritos por modo e abortar a missão com Ctrl+C na Cabine.

**Architecture:** O modo continua viajando no documento de parâmetros e resolvido uma vez no backend. Esta fase acrescenta (1) um marco `mission.mode` ecoado pelo backend e conferido pela estação antes da decolagem, (2) o modo injetado também no Step 1 (decolagem nativa no Simples) e normalizado no backend (altura máxima nativa, margem de teto fixa), (3) o copiloto escolhendo pools de frases pelo modo ecoado, (4) um módulo puro de armazenamento por modo (`parameterModes.ts`) usado pelo hook `useMissionParameters`, (5) um atalho global Ctrl/Cmd+C na Cabine com a mesma ação do botão de aborto.

**Tech Stack:** Python 3 / ROS 2 Jazzy / Nectar SDK / pytest (backend); Electron + React + TypeScript + Tailwind + Vitest (BMG).

**Spec:** Seções 1 a 6 do pedido de auditoria "Consistência dos 3 Modos de Missão, Parâmetros Isolados, Copiloto e Atalho Ctrl+C", resumidas em "Resultado da auditoria" abaixo. Este arquivo é a única fonte que o executor precisa. O plano anterior, `docs/superpowers/plans/2026-10-14-mission-modes.md`, já foi implementado; os símbolos que ele criou (`MissionMode`, `build_steps`, `YawAuthority`, `readMissionMode`, `groupsForMode`, `isPathActive`, `MissionModeSelector`) existem no código.

---

## Global Constraints

Regras do `CLAUDE.md` do repositório, válidas para toda tarefa:

- Sem emojis em código, comentários, logs, docs ou mensagens de commit. Comentários só para física de controle, cinemática, restrição do Bebop 2 ou acordo de sincronismo IPC.
- Python: docstrings NumPy/reST (`Parameters`, `Returns`, `Raises`), validação defensiva, `typing` estrito. TypeScript `strict`, sem `any` novo.
- `controllers/` é matemática pura (sem relógio de parede, sem ROS).
- Contrato GCS: toda execução emite `--- [STEP N: ...] ---` (N de 1 a 5, em ordem, prefixo literal `STEP N:`). `mission_config.json` mantém esquema retrocompatível (campo novo com default; chave desconhecida ignorada).
- O modo Avançado permanece bit a bit igual ao comportamento atual. Nenhum teste existente pode precisar de mudança de asserção, exceto onde a tarefa disser explicitamente (as chaves de `localStorage` da Task 5 e as constantes de bateria da Task 6).
- Nenhum `git commit` / `git push` / `git add` sem autorização explícita do usuário (memória `no-commit-without-authorization`). Os passos "Checkpoint" só propõem a mensagem de commit.
- Ambiente: todo comando pytest/mission é precedido de `source /home/jv/ros2_ws/bin/nectar-activate &&`. Nunca rodar dois lotes de voo emulado ao mesmo tempo (domínios ROS 80-99). Subagentes de revisão não lançam voo emulado.
- A árvore está suja com trabalho de outras sessões. Reler cada arquivo imediatamente antes de editar; nunca reverter nem reformatar alterações alheias.
- Reuso do `nectar-sdk` (`/home/jv/ros2_ws/src/nectar-sdk`) tem prioridade; nada aqui o reimplementa.
- Convenção de yaw (herdada): `vyaw > 0` gira à esquerda.

## Resultado da auditoria (estado do código antes deste plano)

Cada linha é um fato verificado no código e a tarefa que o corrige.

| # | Requisito do pedido | Achado | Tarefa |
|---|---|---|---|
| A1 | O modo escolhido chega ao backend sem ambiguidade | O documento inteiro vai em `--params-json` (partida fria) e no `go` do standby, e o backend resolve o modo com `apply_mission_mode` (modo inválido aborta). Faltam: validação e log no Electron, prova em teste de que as duas rotas carregam o modo, e eco do modo realmente executado. | 1 |
| A2 | Sem fallback silencioso para Avançado | `readMissionMode` (TS) devolve `'advanced'` para qualquer valor inválido, e o Electron repassa documento sem `mission_mode` sem registrar nada. Um valor corrompido no `mission_config.json` seria voado como Avançado sem aviso na estação. | 1 |
| A3 | Simples decola pela rotina nativa, sem `_ascend` | `TakeoffStep` não conhece o modo. `_ascend` só é pulado quando a altura atingida já está dentro de `climb_deadband_m` do alvo; com o firmware parando em 0,90 m e alvo 1,00 m ele roda. A margem do teto (`altitude_ceiling_margin_m`) entra no teto de segurança em todos os modos. | 2 |
| A4 | Estágios 1 a 5 e marcos `[STEP N:` nos 3 modos | Já garantido por `build_steps` e testado (`test_mission_modes.py`, `test_mission_modes_emulated.py`). Falta o teste de contrato explícito sobre o marco `mission.mode` e a leitura dos 5 marcadores no emulado dos 3 modos com Step 1 por modo. | 1, 9 |
| A5 | Copiloto nunca fala o que não cabe ao modo | A estação não conhece o modo. Pools incoerentes com o Simples: `mission.takeoff` ("Subindo para {altitude}"), `mission.scan_start` ("Varredura linear", "{speed}"), `mission.approaching` ("Deslocando", "Velocidade reduzida"); `BATTERY_CALLS` diz "Retornando à base" em todos os modos. A supressão do `mission.rtl_start` existe só no backend. `MILESTONE_STAGE` usa a chave `mission.approach`, mas o marco emitido é `mission.approaching`. O hook de narração é `useFlightNarration`, em `src/hooks/useCopilot.ts` (não há `useFlightNarration.ts`). | 6 |
| A6 | Regras de operação idênticas nos 3 modos | `preflightLocked`, `abortEnabled`, `useFinishLock`, `endCycle` e `failsafeAction` dependem só do estado da missão, não do modo: paridade por construção, sem teste que a trave. No backend, o laço novo `_restore_heading` do Simples não avalia a saúde do sistema (bateria, odometria, teto), ao contrário dos laços dos Steps 2, 3 e 5. | 3, 8 |
| A7 | Parâmetros e favorito isolados por modo | Existe uma chave única `bmg.working-parameters.v2` e uma `bmg.operator-preset.v2`. Trocar de modo só edita `mission_mode`; os valores e o favorito são compartilhados pelos três modos. | 5 |
| A8 | Folha do Simples sem "Margem do Teto"; altura nativa | No Simples a folha ainda mostra `kinematics.altitude_ceiling_margin_m` e permite altura de 0,1 a 4 m. | 4 |
| A9 | Ctrl+C na Cabine aborta | Só existe o atalho Escape (R11, `CockpitScreen.tsx`). O Electron usa o menu padrão (`autoHideMenuBar: true`), que registra Ctrl+C como Copiar. | 7 |

## Decisões

Duas vieram do usuário nesta rodada; as demais são do arquiteto e devem ser revisadas.

1. **(Usuário) Altitude de Voo no Simples** permanece na folha, limitada a 1,0 m (altura nativa do firmware, `NATIVE_HOVER_HEIGHT_M`). A decolagem é só a rotina nativa; a subida corretiva (`_ascend`) nunca roda no Simples. A margem do teto sai da folha e é fixada internamente no valor de fábrica (0,60 m) pelo backend e pela estação. O limite é relativo ao ponto de decolagem (topo do totem): a altura sobre o piso vai de `platform.height_m` + 0,7 a `platform.height_m` + 1,0 m.
2. **(Usuário) Primeiro uso de um modo:** a folha começa com uma cópia dos valores atuais, e a partir daí o modo evolui sozinho.
3. **(Usuário) Eco do modo:** o backend emite `mission.mode`; se o modo ecoado diferir do pedido, a estação aborta ainda na contagem (nada voou) e mostra o erro no pré-voo.
4. **Faixa da altura no Simples:** 0,7 a 1,0 m (medido na Task 4: com 0,6 m o aviso W01 dispara, pois 0,6 + 0,6 = 1,20 m fica abaixo de 1,21 m). O piso 0,7 m mantém `altitude + margem fixa >= 1,3 m`, o que impede os conflitos E03 e W01 (que citariam a margem, invisível no Simples). A faixa é relativa ao ponto de decolagem (topo do totem): sobre o piso, a altitude fica em `platform.height_m` + [0,7; 1,0] m (com totem de 1 m, 1,7 a 2,0 m).
5. **Backend recusa, não corrige:** no Simples, altura acima de 1,0 m é recusada com `ValueError` (vira `SystemExit` na partida). A estação já limita a faixa e, ao copiar valores de outro modo, aperta a altura para a faixa do modo; a recusa só pega documentos que não passaram pela estação.
6. **O Electron não passa `--mode`.** O standby é preparado antes do clique com o documento salvo; um `--mode` fixo na partida venceria o modo do `go`. O modo vai só no documento (`--params-json` e `go`), que o Electron valida, normaliza e registra.
7. **Origem do modo na narração:** o marco `mission.mode` do backend (fonte da verdade do que voa), não o estado da interface.
8. **Formato do armazenamento por modo:** documento inteiro (como já era), restaurando só os caminhos que o modo possui (`MODE_OWNED_PATHS`), de modo que PID, calibração e demais campos do arquivo nunca são copiados entre modos. As chaves são exatamente `bmg.working-parameters.<modo>` e `bmg.operator-preset.<modo>`; as chaves `.v2` antigas são migradas, sem apagar, para o modo `advanced`.
9. **Trocar de modo grava o arquivo ativo** (`mission_config.json`) em seguida, para a folha não ficar "suja" só por causa da troca; as edições não salvas do modo que está saindo ficam guardadas no instantâneo dele.
10. **Ctrl+C** segue as mesmas regras do Escape (`abortEnabled`, ignora campo de texto e terminal), ignora `event.repeat`, e não chama `preventDefault` quando o aborto não está disponível (copiar continua funcionando fora de voo). Ctrl+Shift+C não é capturado.
11. **Saúde no retorno do yaw (Simples):** o laço de retorno avalia a saúde sem o item de vídeo (`check_video=False`), pois a visão está suspensa depois da captura; bateria, odometria e teto passam a valer como nos outros laços.
12. **Bateria crítica por modo:** o salto ao estágio 5 não muda; só a fala muda (Intermediária: pouso no local; Simples: pouso na base; Avançada: retorno).

## Review Focus

1. Documento com `mission_mode` ausente, vazio, em caixa diferente (`"Simple"`) ou inválido (`"expert"`): ausente vira Avançada **de forma registrada** (linha de log `[BMG] Modo de missão: ...`); inválido recusa o lançamento sem tocar o drone; caixa diferente é normalizada. Teste: Task 1.
2. Simples com a firmware parando abaixo do alvo (0,90 m com alvo 1,00 m): nenhuma subida corretiva, nenhum erro. E altura pedida acima de 1,0 m: recusada. Teste: Task 2.
3. Sessão com nada salvo para o modo de destino, snapshot corrompido ou de versão antiga (`.v2`), armazenamento indisponível (`localStorage` lançando): a folha abre, sem exceção, com os valores atuais. Teste: Task 5.
4. Marco `mission.mode` ausente (backend antigo ou saída truncada): a estação não pode abortar por ausência, só por divergência; e o copiloto, sem modo conhecido, usa o repertório Avançado, nunca o do Simples. Teste: Tasks 1 e 6.
5. Ctrl+C com o foco em campo de texto, com a missão em solo, mantida pressionada (`repeat`), e em teclado não-QWERTY (`code === 'KeyC'`). Teste: Task 7.

## File Structure

Backend (`/home/jv/ros2_ws/src/mvp_mission_bebop`):

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `mvp_mission_bebop/mission_mode.py` | Modify | `NATIVE_HOVER_HEIGHT_M`, `SIMPLE_CEILING_MARGIN_M`, normalização do modo Simples dentro de `apply_mission_mode`. |
| `mvp_mission_bebop/mission.py` | Modify | emite `mission.mode` antes de `mission.parameters`. |
| `mvp_mission_bebop/steps/pipeline.py` | Modify | Step 1 recebe o modo. |
| `mvp_mission_bebop/steps/takeoff.py` | Modify | `mode=`, pula `_ascend` no Simples, `native` no marco `mission.takeoff`. |
| `mvp_mission_bebop/steps/inspection.py` | Modify | saúde no `_restore_heading`; fala do Step 4 por modo se houver. |
| `mvp_mission_bebop/steps/search.py` | Modify | fala do Step 2 por modo. |
| `mvp_mission_bebop/telemetry/failsafe.py` | Modify | `evaluate_system_health(check_video=True)`. |
| `test/test_mission_mode_native_takeoff.py` | Create | decolagem nativa e normalização do Simples. |
| `test/test_mode_parity.py` | Create | paridade de segurança por modo. |
| `test/test_mission_modes_emulated.py` | Modify | marco `mission.mode`, log de decolagem nativa. |
| `test/test_contracts.py` | Modify | linha do marco `mission.mode`. |

Frontend (`bebop_mission_control/`):

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `electron/missionMode.cjs`, `electron/missionMode.d.cts` | Create | `normalizeLaunchMode(doc)`: valida, normaliza e relata o modo. |
| `electron/main.cjs` | Modify | usa `normalizeLaunchMode` em `startMissionProcess` e no bench. |
| `src/lib/missionMode.ts` | Modify | `parseMissionMode` estrito; Simples sem margem; `MODE_RANGES`. |
| `src/lib/parameterSchema.ts` | Modify | `modes` da margem, `rangeByMode` da altura. |
| `src/lib/launchDocument.ts` | Modify | envelope por modo; `normalizeForMode`. |
| `src/lib/parameterModes.ts` | Create | armazenamento por modo (puro). |
| `src/hooks/useMissionParameters.ts` | Modify | `switchMode`, favorito e snapshot por modo. |
| `src/hooks/useModeEcho.ts` | Create | confere `mission.mode` com o pedido. |
| `src/hooks/useCopilot.ts` | Modify | `useFlightNarration` com o modo. |
| `src/lib/copilotPhrases.ts` | Modify | pools por modo, `isMilestoneAllowed`, `allMissionPhrases(parameters, mode)`. |
| `src/lib/batteryReturn.ts` | Modify | frases de bateria por modo. |
| `src/components/cockpit/CockpitScreen.tsx` | Modify | atalho Ctrl/Cmd+C. |
| `src/components/preflight/PreflightScreen.tsx`, `src/App.tsx` | Modify | `onModeChange`, eco, bateria por modo. |
| testes novos e alterados | | citados em cada tarefa |

---

## Task 0: Linha de base e conferência do estado

**Files:** nenhum.

- [ ] **Step 1: Confirmar que o plano anterior está no código**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
ls mvp_mission_bebop/mission_mode.py mvp_mission_bebop/steps/pipeline.py mvp_mission_bebop/actuators/yaw_authority.py mvp_mission_bebop/controllers/yaw_pattern.py bebop_mission_control/src/lib/missionMode.ts bebop_mission_control/src/components/preflight/MissionModeSelector.tsx
git status --short | wc -l
```

Expected: os seis arquivos existem. Se algum faltar, parar e avisar o usuário: este plano pressupõe o anterior.

- [ ] **Step 2: Linha de base de testes**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/ -q --ignore=test/test_fly_path_emulated.py --ignore=test/test_fly_path_totem.py --ignore=test/test_mission_modes_emulated.py 2>&1 | tail -15
cd bebop_mission_control && npm test 2>&1 | tail -15 && npm run typecheck 2>&1 | tail -8
```

Expected: anotar as falhas pré-existentes (testes de temporização: reexecutar isolados antes de classificar). Parar e perguntar se uma falha pré-existente estiver em arquivo que este plano altera.

- [ ] **Step 3: Congelar a base para comparação**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
mkdir -p .superpowers && rsync -a --delete --exclude node_modules --exclude dist --exclude '*.jpg' --exclude '*.png' --exclude '__pycache__' ./ .superpowers/baseline-consistency/
ln -sf "$PWD/mvp_mission_bebop/yolov8n.pt" .superpowers/baseline-consistency/mvp_mission_bebop/yolov8n.pt
ln -sfn "$PWD/mvp_mission_bebop/yolov8n_openvino_model" .superpowers/baseline-consistency/mvp_mission_bebop/yolov8n_openvino_model
```

---

## Task 1: Roteamento comprovado do modo (Electron, marco `mission.mode`, eco e abort)

**Files:**
- Create: `bebop_mission_control/electron/missionMode.cjs`, `bebop_mission_control/electron/missionMode.d.cts`, `bebop_mission_control/src/lib/missionModeMain.test.ts`, `bebop_mission_control/src/hooks/useModeEcho.ts`, `bebop_mission_control/src/hooks/useModeEcho.test.tsx`
- Modify: `bebop_mission_control/electron/main.cjs`, `bebop_mission_control/src/lib/missionMode.ts`, `bebop_mission_control/src/lib/launchDocument.ts`, `bebop_mission_control/src/App.tsx`, `bebop_mission_control/src/lib/launchArguments.test.ts`, `mvp_mission_bebop/mission.py`, `test/test_contracts.py`, `test/test_mission_modes_emulated.py`
- Test: os citados.

**Interfaces:**
- Produces (`missionMode.cjs`): `MISSION_MODES: readonly ['simple','intermediate','advanced']`; `normalizeLaunchMode(doc) -> { ok: true, doc, mode, defaulted: boolean } | { ok: false, error: string }`. `defaulted` é `true` quando o documento não trazia o modo. O `doc` devolvido é uma cópia com `mission_mode` canônico (minúsculo).
- Produces (`missionMode.ts`): `parseMissionMode(raw: unknown): MissionMode | null` (estrito: `null` para qualquer valor que não seja um dos três, aceitando caixa e espaços); `readMissionMode` passa a usar `parseMissionMode` mas continua devolvendo `'advanced'` só para valor **ausente ou vazio**; para valor presente e inválido devolve `'advanced'` também (compatibilidade da folha), e `launchBlockers` passa a incluir `'mission_mode'` quando o valor é presente e inválido.
- Produces (backend): marco `mission.mode` com payload `{"mode": "<simple|intermediate|advanced>"}`, emitido uma vez, antes de `mission.parameters`.
- Produces (`useModeEcho.ts`): `useModeEcho({ requested: MissionMode | null, onMismatch: (reported: string, requested: MissionMode) => void }): void`.

- [ ] **Step 1: Testes do Electron que falham** — `src/lib/missionModeMain.test.ts` (mesmo padrão de `launchReadinessMain.test.ts`: `createRequire` para carregar o `.cjs`):

```ts
import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { MISSION_MODES, normalizeLaunchMode } = require('../../electron/missionMode.cjs') as {
  MISSION_MODES: readonly string[];
  normalizeLaunchMode: (doc: unknown) =>
    | { ok: true; doc: Record<string, unknown>; mode: string; defaulted: boolean }
    | { ok: false; error: string };
};

describe('normalizeLaunchMode', () => {
  it('lists the three profiles', () => {
    expect(MISSION_MODES).toEqual(['simple', 'intermediate', 'advanced']);
  });

  it.each([
    ['simple', 'simple'],
    ['Intermediate', 'intermediate'],
    [' ADVANCED ', 'advanced'],
  ])('normalises %j to %s and keeps the rest of the document', (raw, expected) => {
    const result = normalizeLaunchMode({ mission_mode: raw, kinematics: { countdown_sec: 3 } });
    expect(result).toMatchObject({ ok: true, mode: expected, defaulted: false });
    if (result.ok) {
      expect(result.doc.mission_mode).toBe(expected);
      expect(result.doc.kinematics).toEqual({ countdown_sec: 3 });
    }
  });

  it('says so when the document carries no mode and runs the full mission', () => {
    for (const doc of [{}, { mission_mode: '' }, { mission_mode: null }]) {
      const result = normalizeLaunchMode(doc);
      expect(result).toMatchObject({ ok: true, mode: 'advanced', defaulted: true });
      if (result.ok) expect(result.doc.mission_mode).toBe('advanced');
    }
  });

  it.each(['expert', 3, ['simple'], true])('refuses %j instead of defaulting', (raw) => {
    const result = normalizeLaunchMode({ mission_mode: raw });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain('mission_mode');
  });

  it('does not mutate its input', () => {
    const doc = { mission_mode: 'Simple' };
    normalizeLaunchMode(doc);
    expect(doc.mission_mode).toBe('Simple');
  });
});
```

Acrescentar a `src/lib/launchArguments.test.ts` (mesmo harness `loadMain`; o `describe('bmg:start-mission')` já existe), três casos:

```ts
  it.each(['simple', 'intermediate', 'advanced'] as const)(
    'carries the %s mission mode in --params-json, canonical and untouched by the CLI flags',
    async (mode) => {
      const doc = completeDocument({ mission_mode: mode === 'simple' ? 'Simple' : mode });
      const result = (await main.handlers['bmg:start-mission']({}, {
        noFly: true,
        countdown: 0,
        paramsJson: JSON.stringify(doc),
      })) as { success: boolean };
      expect(result.success).toBe(true);
      const args = missionSpawn()!.args;
      const payload = JSON.parse(args[args.indexOf('--params-json') + 1]);
      expect(payload.mission_mode).toBe(mode);
      expect(args).not.toContain('--mode');
      await endMission(missionSpawn());
    }
  );

  it('refuses a launch whose document names an unknown mission mode, spawning nothing', async () => {
    const result = (await main.handlers['bmg:start-mission']({}, {
      noFly: true,
      countdown: 0,
      paramsJson: JSON.stringify(completeDocument({ mission_mode: 'expert' })),
    })) as { success: boolean; error?: string };
    expect(result.success).toBe(false);
    expect(result.error).toContain('mission_mode');
    expect(missionSpawn()).toBeUndefined();
  });

  it('makes an absent mode explicit in the document it sends', async () => {
    const doc = completeDocument();
    delete (doc as Record<string, unknown>).mission_mode;
    await main.handlers['bmg:start-mission']({}, { noFly: true, countdown: 0, paramsJson: JSON.stringify(doc) });
    const args = missionSpawn()!.args;
    expect(JSON.parse(args[args.indexOf('--params-json') + 1]).mission_mode).toBe('advanced');
    await endMission(missionSpawn());
  });
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/missionModeMain.test.ts src/lib/launchArguments.test.ts 2>&1 | tail -15
```

Expected: FAIL (`missionMode.cjs` inexistente; documento sem normalização).

- [ ] **Step 3: Criar `electron/missionMode.cjs`**

```js
/**
 * Mission profile of a launch document, as the station sends it to `mission.py`.
 *
 * The profile travels only inside the document (`--params-json` on a cold
 * start, the `go` of a standby): never as `--mode`. A standby is prepared at
 * the saved document before the click, and a `--mode` fixed at that spawn
 * would win over the profile of the document sent with the go
 * (`mission_mode.apply_mission_mode`). The station validates the value here so
 * a corrupt document is refused on the ground instead of being flown as a
 * default, and the mission echoes the profile it resolved (`mission.mode`) so
 * the station can confirm it before takeoff.
 */

const MISSION_MODES = Object.freeze(['simple', 'intermediate', 'advanced']);

/**
 * @param {unknown} doc Launch document (parsed JSON object).
 * @returns {{ok: true, doc: object, mode: string, defaulted: boolean} | {ok: false, error: string}}
 *   `defaulted` is true when the document carried no profile and the full
 *   mission was selected; the caller logs it. An unknown value is refused.
 */
function normalizeLaunchMode(doc) {
  const source = doc && typeof doc === 'object' && !Array.isArray(doc) ? doc : {};
  const raw = source.mission_mode;
  if (raw === undefined || raw === null || (typeof raw === 'string' && raw.trim() === '')) {
    return { ok: true, doc: { ...source, mission_mode: 'advanced' }, mode: 'advanced', defaulted: true };
  }
  const token = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  if (!MISSION_MODES.includes(token)) {
    return {
      ok: false,
      error: `mission_mode inválido (${JSON.stringify(raw)}): use ${MISSION_MODES.join(', ')}. Lançamento bloqueado.`,
    };
  }
  return { ok: true, doc: { ...source, mission_mode: token }, mode: token, defaulted: false };
}

module.exports = { MISSION_MODES, normalizeLaunchMode };
```

`electron/missionMode.d.cts` (padrão dos demais `.d.cts`):

```ts
export declare const MISSION_MODES: readonly ['simple', 'intermediate', 'advanced'];
export declare function normalizeLaunchMode(
  doc: unknown
):
  | { ok: true; doc: Record<string, unknown>; mode: string; defaulted: boolean }
  | { ok: false; error: string };
```

- [ ] **Step 4: Usar em `electron/main.cjs`**

`const { normalizeLaunchMode } = require('./missionMode.cjs');` junto dos demais `require`. Em `startMissionProcess`, no lugar do trecho atual:

```js
    const doc = parseLaunchDocument(options.paramsJson);
    if (!doc) {
      return { success: false, error: 'Documento de parâmetros ausente ou inválido; lançamento bloqueado.' };
    }
    args.push('--params-json', JSON.stringify(doc));
```

escrever:

```js
    const parsedDoc = parseLaunchDocument(options.paramsJson);
    if (!parsedDoc) {
      return { success: false, error: 'Documento de parâmetros ausente ou inválido; lançamento bloqueado.' };
    }
    const resolvedMode = normalizeLaunchMode(parsedDoc);
    if (!resolvedMode.ok) {
      recordLog('mission', { type: 'stderr', text: `[BMG] ${resolvedMode.error}\n` });
      return { success: false, error: resolvedMode.error, message: resolvedMode.error };
    }
    const doc = resolvedMode.doc;
    recordLog('mission', {
      type: 'stdout',
      text: `[BMG] Modo de missão: ${resolvedMode.mode}${resolvedMode.defaulted ? ' (o documento não trazia o modo: missão completa)' : ''}\n`,
    });
    args.push('--params-json', JSON.stringify(doc));
```

`doc` já é a variável usada adiante (`launchDoc = { ...doc, no_fly: ... }`, `missionStandby.take(launchDoc, ...)`, `evaluateLaunchReadiness({ configValid: Boolean(doc) ...})`), então a rota do standby passa a levar o modo normalizado. Conferir que o `go` enviado por `missionStandby.take` usa o `launchDoc` inteiro (`electron/missionStandby.cjs`, função `take`, e `src/lib/missionStandby.test.ts`); se `take` enviar só um subconjunto, incluir `mission_mode` e cobrir em `missionStandby.test.ts` com a asserção `expect(sentGo.params.mission_mode).toBe('simple')`.

Rota de bancada (`bmg:start-bench-stage`): ela termina chamando `startMissionProcess(spawnOptions)` (linhas ~2828 a 2837 de `main.cjs`), então herda a validação. Cobrir com um caso que dispara `bmg:start-bench-stage` com `mission_mode: 'simple'` e espera `--params-json` com o modo.

- [ ] **Step 5: Eliminar o fallback silencioso na folha** — em `src/lib/missionMode.ts`:

```ts
/** The mode named by `raw`, or `null` when it is not one of the three. Case and spaces are ignored. */
export function parseMissionMode(raw: unknown): MissionMode | null {
  if (typeof raw !== 'string') return null;
  const token = raw.trim().toLowerCase();
  return token === 'simple' || token === 'intermediate' || token === 'advanced' ? token : null;
}

/** The mode a launch document asks for. A document that names none is the full mission. */
export function readMissionMode(doc: unknown): MissionMode {
  return parseMissionMode(getPath(doc, MISSION_MODE_PATH)) ?? DEFAULT_MISSION_MODE;
}

/** Whether the document names a mode that is neither absent nor one of the three. */
export function hasInvalidMissionMode(doc: unknown): boolean {
  const raw = getPath(doc, MISSION_MODE_PATH);
  if (raw === undefined || raw === null || (typeof raw === 'string' && raw.trim() === '')) return false;
  return parseMissionMode(raw) === null;
}
```

Em `launchDocument.ts`, no fim de `launchBlockers` (antes do `filter` por modo), acrescentar `if (hasInvalidMissionMode(doc)) blocked.push(MISSION_MODE_PATH);` e importar. Testes em `missionMode.test.ts` e `launchDocument.test.ts`: `parseMissionMode('Simple') === 'simple'`, `parseMissionMode('expert') === null`, `parseMissionMode(3) === null`; `launchBlockers({ ...completeDocument(), mission_mode: 'expert' })` contém `'mission_mode'`; documento sem o campo não o contém.

- [ ] **Step 6: Backend emite `mission.mode`** — em `mvp_mission_bebop/mission.py`, imediatamente antes de `emit_milestone("mission.parameters", spoken_parameters(params, calibration))`:

```python
    emit_milestone("mission.mode", {"mode": MissionMode(params.mission_mode).value})
```

(`params.mission_mode` já foi normalizado por `apply_mission_mode` depois do `go`; o `MissionMode(...)` não pode falhar aqui.) Contrato: em `test/test_contracts.py` há a regex `MILESTONE_LINE`; acrescentar um teste que formata o marco com `encode_milestone("mission.mode", {"mode": "simple"})` e confere que casa a regex e que o JSON decodificado tem `{"mode": "simple"}`, no estilo dos testes de marco vizinhos.

Em `test/test_mission_modes_emulated.py` (já parametrizado pelos 3 modos), acrescentar:

```python
def test_every_profile_echoes_the_mode_it_flies(run):
    mode, result = run
    echoed = [
        line.text for line in result.lines
        if "[MILESTONE mission.mode]" in line.text
    ]
    assert len(echoed) == 1, f"{mode}: expected one mission.mode milestone, got {echoed}"
    assert f'"mode": "{mode}"' in echoed[0].replace('":"', '": "')
    order = [line.text for line in result.lines]
    mode_at = next(i for i, t in enumerate(order) if "[MILESTONE mission.mode]" in t)
    params_at = next(i for i, t in enumerate(order) if "[MILESTONE mission.parameters]" in t)
    assert mode_at < params_at, "the mode must be echoed before the parameters (the cache keys on it)"
```

- [ ] **Step 7: Eco conferido pela estação** — `src/hooks/useModeEcho.ts`:

```ts
import { useEffect, useRef } from 'react';
import type { MissionMode } from '../lib/missionMode';
import { parseMissionMode } from '../lib/missionMode';
import { useBridge } from './useBridge';

export interface ModeEchoOptions {
  /** The profile of the launch in progress; null while no launch has been requested. */
  requested: MissionMode | null;
  /** Called once per mission when the profile the mission reports differs from the one requested. */
  onMismatch: (reported: string, requested: MissionMode) => void;
}

/**
 * Confirm that the mission flies the profile the operator selected.
 *
 * The mission raises `mission.mode` before `mission.parameters` and before
 * Stage 1, so a divergence is always found during the countdown, with nothing
 * in the air. Only a divergence acts: a mission that never reports the milestone
 * (an older backend, a truncated stream) is not an error here.
 */
export function useModeEcho({ requested, onMismatch }: ModeEchoOptions): void {
  const bridge = useBridge();
  const requestedRef = useRef(requested);
  requestedRef.current = requested;
  const handlerRef = useRef(onMismatch);
  handlerRef.current = onMismatch;

  useEffect(() => {
    if (!bridge?.onMilestone) return;
    let reportedFor: MissionMode | null = null;
    return bridge.onMilestone((event) => {
      if (event.kind === 'alert' || event.key !== 'mission.mode') return;
      const wanted = requestedRef.current;
      if (wanted === null || reportedFor === wanted) return;
      const raw = (event.payload ?? {}).mode;
      const reported = parseMissionMode(raw);
      if (reported === wanted) return;
      reportedFor = wanted;
      handlerRef.current(typeof raw === 'string' ? raw : String(raw), wanted);
    });
  }, [bridge]);
}
```

Teste `useModeEcho.test.tsx`, no padrão dos testes de hook com ponte falsa (`useFlightNarration.test.ts` e `useTelemetry.test.tsx` mostram como simular `bridge.onMilestone`): (a) `requested='simple'` e evento `mission.mode` com `simple` não chama `onMismatch`; (b) evento com `advanced` chama uma vez com `('advanced', 'simple')`; (c) dois eventos divergentes seguidos chamam uma vez; (d) `requested=null` nunca chama; (e) sem nenhum evento nunca chama.

Em `src/App.tsx`: guardar o modo pedido no clique (`const [launchedMode, setLaunchedMode] = useState<MissionMode | null>(null)`, atribuído em `launch` com `readMissionMode(doc)` logo depois de `commitLaunchDocument`, e zerado em `endCycle`), e:

```tsx
  useModeEcho({
    requested: launchedMode,
    onMismatch: (reported, requested) => {
      setLaunchError(
        `A missão iniciou no modo "${reported}", mas o modo "${requested}" foi selecionado. Missão abortada antes da decolagem.`
      );
      void cancelCountdown();
    },
  });
```

`cancelCountdown` (já existe em `App.tsx`) encerra a contagem e chama `mission.abort()`. Conferir que ele é declarado antes do hook; reordenar se necessário. O `launchError` aparece no pré-voo (`PreflightScreen` já exibe `launchError`).

- [ ] **Step 8: Rodar tudo desta tarefa**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/missionModeMain.test.ts src/lib/launchArguments.test.ts src/lib/missionMode.test.ts src/lib/launchDocument.test.ts src/hooks/useModeEcho.test.tsx src/lib/missionStandby.test.ts 2>&1 | tail -20 && npm run typecheck 2>&1 | tail -8
cd .. && source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_contracts.py test/test_mission_modes.py -q 2>&1 | tail -10
```

Expected: PASS. O voo emulado (`test_mission_modes_emulated.py`) roda sozinho na Task 9.

- [ ] **Step 9: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): validate and echo the mission mode end to end`.

---

## Task 2: Decolagem nativa do modo Simples e normalização do backend

**Files:**
- Modify: `mvp_mission_bebop/mission_mode.py`, `mvp_mission_bebop/steps/takeoff.py`, `mvp_mission_bebop/steps/pipeline.py`
- Create: `test/test_mission_mode_native_takeoff.py`

**Interfaces:**
- Produces: `NATIVE_HOVER_HEIGHT_M: Final[float] = 1.0`; `SIMPLE_CEILING_MARGIN_M: Final[float] = 0.60`; `apply_mission_mode(params, cli_mode)` passa a normalizar o Simples (recusa altura acima de `NATIVE_HOVER_HEIGHT_M` com `ValueError`; fixa `params.kinematics.altitude_ceiling_margin_m = SIMPLE_CEILING_MARGIN_M`; mantém o retorno `MissionMode`). `TakeoffStep(*, mode: MissionMode = MissionMode.ADVANCED)` com a propriedade `mode`. `build_steps(mode)` passa `mode` ao Step 1. O marco `mission.takeoff` ganha `"native": true` no Simples.

- [ ] **Step 1: Testes que falham** — `test/test_mission_mode_native_takeoff.py`:

```python
"""Simple profile: the firmware's native takeoff, no corrective ascent, fixed ceiling margin."""

from __future__ import annotations

import types

import pytest

from mvp_mission_bebop.actuators.simulator import FIRMWARE_HOVER_ALTITUDE_M
from mvp_mission_bebop.blackboard import MissionBlackboard
from mvp_mission_bebop.mission_mode import (
    NATIVE_HOVER_HEIGHT_M,
    SIMPLE_CEILING_MARGIN_M,
    MissionMode,
    apply_mission_mode,
)
from mvp_mission_bebop.parameter_conflicts import FIRMWARE_HOVER_HEIGHT_M
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps import takeoff as takeoff_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.pipeline import build_steps
from mvp_mission_bebop.steps.takeoff import TakeoffStep


def test_the_native_height_agrees_with_the_simulator_and_the_conflict_rules():
    assert NATIVE_HOVER_HEIGHT_M == FIRMWARE_HOVER_ALTITUDE_M == FIRMWARE_HOVER_HEIGHT_M


def test_the_ceiling_margin_constant_is_the_factory_value():
    assert SIMPLE_CEILING_MARGIN_M == MissionParameters.factory().kinematics.altitude_ceiling_margin_m


# ------------------------------------------------------------- normalisation


def simple_params(**kinematics) -> MissionParameters:
    params = MissionParameters()
    params.mission_mode = "simple"
    for key, value in kinematics.items():
        setattr(params.kinematics, key, value)
    return params


def test_the_simple_profile_pins_the_ceiling_margin():
    params = simple_params(altitude_ceiling_margin_m=0.05, target_altitude_m=0.9)
    assert apply_mission_mode(params, None) is MissionMode.SIMPLE
    assert params.kinematics.altitude_ceiling_margin_m == SIMPLE_CEILING_MARGIN_M
    assert params.kinematics.target_altitude_m == 0.9


@pytest.mark.parametrize("altitude", [0.6, 0.9, NATIVE_HOVER_HEIGHT_M])
def test_the_simple_profile_accepts_up_to_the_native_height(altitude):
    params = simple_params(target_altitude_m=altitude)
    apply_mission_mode(params, None)
    assert params.kinematics.target_altitude_m == altitude


@pytest.mark.parametrize("altitude", [1.05, 1.4, 4.0])
def test_the_simple_profile_refuses_an_altitude_above_the_native_height(altitude):
    params = simple_params(target_altitude_m=altitude)
    with pytest.raises(ValueError, match="target_altitude_m"):
        apply_mission_mode(params, None)


@pytest.mark.parametrize("mode", ["intermediate", "advanced"])
def test_the_other_profiles_keep_their_altitude_and_margin(mode):
    params = MissionParameters()
    params.mission_mode = mode
    params.kinematics.target_altitude_m = 1.8
    params.kinematics.altitude_ceiling_margin_m = 0.25
    apply_mission_mode(params, None)
    assert params.kinematics.target_altitude_m == 1.8
    assert params.kinematics.altitude_ceiling_margin_m == 0.25


def test_the_cli_flag_selects_the_simple_normalisation_too():
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.4
    with pytest.raises(ValueError):
        apply_mission_mode(params, "simple")


# ------------------------------------------------------------ takeoff routing


def flight_sequence(monkeypatch, mode: MissionMode):
    step = TakeoffStep(mode=mode)
    calls = []
    monkeypatch.setattr(step, "_confirm_takeoff", lambda ctx: StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_stabilize", lambda ctx: StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_ascend", lambda ctx: calls.append("ascend") or StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_finalize", lambda ctx: StepStatus.SUCCESS)
    ctx = types.SimpleNamespace(blackboard=MissionBlackboard())
    return step._flight_sequence(ctx), calls, ctx


def test_the_simple_profile_never_runs_the_corrective_ascent(monkeypatch):
    status, calls, ctx = flight_sequence(monkeypatch, MissionMode.SIMPLE)
    assert status is StepStatus.SUCCESS
    assert calls == []
    assert ctx.blackboard.takeoff_complete is True


@pytest.mark.parametrize("mode", [MissionMode.INTERMEDIATE, MissionMode.ADVANCED])
def test_the_other_profiles_still_run_the_ascent_stage(monkeypatch, mode):
    status, calls, _ctx = flight_sequence(monkeypatch, mode)
    assert status is StepStatus.SUCCESS
    assert calls == ["ascend"]


def test_the_takeoff_default_is_the_advanced_profile():
    assert TakeoffStep().mode is MissionMode.ADVANCED


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_pipeline_hands_the_profile_to_stage_one(mode):
    assert build_steps(mode)[1]().mode is mode


# ------------------------------------------------------------------ milestone


@pytest.mark.parametrize(
    "mode, native", [(MissionMode.SIMPLE, True), (MissionMode.INTERMEDIATE, False), (MissionMode.ADVANCED, False)]
)
def test_the_takeoff_milestone_says_whether_the_takeoff_is_native(monkeypatch, mode, native):
    emitted = []
    monkeypatch.setattr(takeoff_module, "emit_milestone", lambda key, payload=None: emitted.append((key, payload)))
    monkeypatch.setattr(takeoff_module, "takeoff_confirmed", lambda *args, **kwargs: True)
    ctx = types.SimpleNamespace(params=MissionParameters(), flying_state=2, blackboard=MissionBlackboard())
    step = TakeoffStep(mode=mode)
    step._takeoff_called = False

    step._call_takeoff_once_confirmed(ctx, 1.0)

    (key, payload), = emitted
    assert key == "mission.takeoff"
    assert payload.get("native", False) is native
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_mode_native_takeoff.py -q 2>&1 | tail -12
```

Expected: FAIL (`NATIVE_HOVER_HEIGHT_M` e `TakeoffStep(mode=...)` inexistentes). Se `parameter_conflicts` não exportar `FIRMWARE_HOVER_HEIGHT_M` com esse nome, usar o nome real do módulo (`grep -n HOVER mvp_mission_bebop/parameter_conflicts.py`) e manter a igualdade dos três valores.

- [ ] **Step 3: Implementar em `mission_mode.py`**

Constantes (junto de `DEFAULT_MODE`):

```python
#: Height at which the Bebop 2 firmware ends its launch profile, metres. The
#: airframe ignores the altitude it is given and stabilizes here; the Simple
#: profile flies at it. Mirrors ``actuators.simulator.FIRMWARE_HOVER_ALTITUDE_M``
#: and ``parameter_conflicts.FIRMWARE_HOVER_HEIGHT_M`` (a test holds the three equal).
NATIVE_HOVER_HEIGHT_M: Final[float] = 1.0

#: Safety headroom above the target altitude in the Simple profile, metres. The
#: factory value of ``kinematics.altitude_ceiling_margin_m``; the profile does
#: not expose the margin, so a stored value cannot weaken the ceiling.
SIMPLE_CEILING_MARGIN_M: Final[float] = 0.60
```

Em `apply_mission_mode`, depois de `params.mission_mode = mode.value` e antes do `return`:

```python
    if mode is MissionMode.SIMPLE:
        kinematics = params.kinematics
        altitude = float(kinematics.target_altitude_m)
        if altitude > NATIVE_HOVER_HEIGHT_M + 1e-9:
            raise ValueError(
                f"kinematics.target_altitude_m={altitude:g} exceeds the native takeoff height "
                f"({NATIVE_HOVER_HEIGHT_M:g} m) the simple profile flies at"
            )
        kinematics.altitude_ceiling_margin_m = SIMPLE_CEILING_MARGIN_M
```

Atualizar o docstring: "In the simple profile the target altitude may not exceed the native takeoff height and the ceiling margin is fixed." (`Raises` ganha o novo caso). O `ValueError` já é convertido em `SystemExit` nos dois pontos de `mission.py` que chamam `apply_mission_mode`.

- [ ] **Step 4: `TakeoffStep` com modo** — em `steps/takeoff.py`: importar `MissionMode`; o `__init__` atual (`def __init__(self) -> None:`) passa a `def __init__(self, *, mode: MissionMode = MissionMode.ADVANCED) -> None:` com `self._mode = mode` antes das demais atribuições, e a propriedade:

```python
    @property
    def mode(self) -> MissionMode:
        return self._mode
```

Em `_flight_sequence`, substituir o bloco de `_ascend`:

```python
        if self._mode is MissionMode.SIMPLE:
            logger.info(
                "Simple profile: native firmware takeoff; the corrective ascent is skipped and "
                "the airframe flies at the height the firmware reached."
            )
        else:
            status = self._ascend(ctx)
            if status is not StepStatus.SUCCESS:
                return status
```

Em `_call_takeoff_once_confirmed`, depois de montar `payload`: `if self._mode is MissionMode.SIMPLE: payload["native"] = True`.

`steps/pipeline.py`: `1: functools.partial(TakeoffStep, mode=mode)`.

- [ ] **Step 5: Rodar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_mode_native_takeoff.py test/test_mission_modes.py test/test_takeoff.py test/test_takeoff_timing.py test/test_altitude_hold.py test/test_stage_altitude_wiring.py test/test_contracts.py -q 2>&1 | tail -15
```

Expected: PASS, sem alterar os testes antigos (o modo Avançado é o default).

- [ ] **Step 6: Checkpoint (sem executar)** Mensagem proposta: `feat(takeoff): native takeoff and fixed ceiling margin for the simple profile`.

---

## Task 3: Paridade de saúde no retorno do yaw e falas dos steps por modo

**Files:**
- Modify: `mvp_mission_bebop/telemetry/failsafe.py`, `mvp_mission_bebop/steps/inspection.py`, `mvp_mission_bebop/steps/search.py`
- Create: `test/test_mode_parity.py`

**Interfaces:**
- Produces: `FailsafeSupervisor.evaluate_system_health(self, *, check_video: bool = True) -> Tuple[bool, str]`. Com `check_video=False` o item "stream de câmera" é ignorado; bateria, odometria e teto continuam.

- [ ] **Step 1: Testes que falham** — `test/test_mode_parity.py`:

```python
"""The safety envelope is the same in the three profiles."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.actuators.yaw_authority import YawAuthority
from mvp_mission_bebop.controllers.yaw_pattern import YawServoingController
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.steps.search import LateralSearchStep
from mvp_mission_bebop.steps.tracking import VisualServoingStep
from test_simple_mode_steps import YawDrone, inspection_ctx, simple_ctx, simulated_time  # noqa: F401
from test_stage_altitude_wiring import Ctx


class UnhealthyOdometry:
    """Delegates to the shared fake and reports a stale telemetry stream."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @staticmethod
    def telemetry_health():
        from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

        return TelemetryHealth.STALE


def make_ctx(mode: MissionMode, *, detections: bool = True) -> Ctx:
    ctx = Ctx(detections=detections)
    if mode is MissionMode.SIMPLE:
        ctx = simple_ctx(detections=detections)
    ctx.params.mission_mode = mode.value
    return ctx


# ------------------------------------------------ the health check has no mode


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_search_stage_lands_on_a_health_fault_in_every_profile(mode):
    ctx = make_ctx(mode, detections=False)
    ctx.odom_supervisor = UnhealthyOdometry(ctx.odom_supervisor)
    ctx.failsafe.odom_supervisor = ctx.odom_supervisor
    ctx.drone.no_fly = False

    assert LateralSearchStep(mode=mode).execute(ctx) is StepStatus.FAILURE
    assert ctx.failsafe.failsafe_active is True


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_tracking_stage_lands_on_a_health_fault_in_every_profile(mode):
    ctx = make_ctx(mode)
    ctx.blackboard.target_confirmed = True
    ctx.odom_supervisor = UnhealthyOdometry(ctx.odom_supervisor)
    ctx.failsafe.odom_supervisor = ctx.odom_supervisor
    ctx.drone.no_fly = False

    assert VisualServoingStep(mode=mode).execute(ctx) is StepStatus.FAILURE
    assert ctx.failsafe.failsafe_active is True


@pytest.mark.parametrize("mode", list(MissionMode))
def test_an_abort_stops_the_search_with_a_zero_last_command_in_every_profile(mode):
    ctx = make_ctx(mode, detections=False)
    ctx.emergency_event.set()

    assert LateralSearchStep(mode=mode).execute(ctx) is StepStatus.ABORTED
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands[-1:])


# ----------------------------------------------- the new yaw loop is guarded too


def test_the_heading_restore_lands_on_a_battery_fault():
    ctx = inspection_ctx(offset_deg=120.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    ctx.drone.no_fly = False

    class LowBattery:
        @staticmethod
        def land_reason():
            return "Battery below the land threshold."

    ctx.failsafe.battery_supervisor = LowBattery()

    status = NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.FAILURE
    assert ctx.failsafe.failsafe_active is True
    assert not ctx.failsafe.yaw_authority.is_open


def test_the_heading_restore_ignores_the_suspended_video_stream():
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    ctx.drone.no_fly = False
    ctx.failsafe.last_valid_frame_timestamp -= 10.0 * ctx.params.timeouts.video_stream_timeout_sec

    status = NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.failsafe.failsafe_active is False


def test_the_health_check_can_skip_only_the_video_item():
    ctx = Ctx(detections=False)
    ctx.failsafe.last_valid_frame_timestamp -= 100.0
    healthy_without_video, _ = ctx.failsafe.evaluate_system_health(check_video=False)
    healthy_with_video, reason = ctx.failsafe.evaluate_system_health()
    assert healthy_without_video is True
    assert healthy_with_video is False and "stream" in reason.lower()
```

(Os helpers `simple_ctx`, `inspection_ctx` e `YawDrone` vêm de `test/test_simple_mode_steps.py`, criado no plano anterior; reutilizá-los em vez de duplicar. Se `Ctx.drone` do harness compartilhado não tiver o atributo `no_fly`, o teste o define como acima; conferir se `Drone` do `test_stage_altitude_wiring.py` já o expõe. O `import ... simulated_time` traz o fixture autouse de relógio simulado do mesmo arquivo.)

- [ ] **Step 2: Rodar e ver falhar** — `test_the_health_check_can_skip_only_the_video_item` e `test_the_heading_restore_*` falham (`check_video` não existe; o laço não avalia saúde).

- [ ] **Step 3: Implementar `check_video`** — em `telemetry/failsafe.py`:

```python
    def evaluate_system_health(self, *, check_video: bool = True) -> Tuple[bool, str]:
        """Verify telemetry health, altitude ceiling, battery charge, and optical stream continuity.

        Parameters
        ----------
        check_video : bool
            Include the camera heartbeat. A phase that suspends vision on purpose
            (the heading restore after the Stage 4 capture) passes False: the
            frame age is stale there by construction, and every other item stays
            enforced.
        """
```

e envolver o bloco `frame_age`/`timeout` com `if check_video:`. Os chamadores existentes não passam o argumento (default `True`), então o comportamento não muda.

- [ ] **Step 4: Saúde no `_restore_heading`** — em `steps/inspection.py`, dentro do laço `while deadline.active:` de `_restore_heading`, logo depois do `if ctx.interrupted(): return StepStatus.ABORTED`:

```python
                    if not ctx.drone.no_fly:
                        healthy, reason = ctx.failsafe.evaluate_system_health(check_video=False)
                        if not healthy:
                            ctx.failsafe.trigger_emergency_land(reason)
                            return StepStatus.FAILURE
```

Reescrever o parágrafo "System health is deliberately not evaluated here" do docstring: "Battery, odometry and the ceiling are evaluated every cycle, as in the other loops of the mission. The camera heartbeat is not: vision is suspended after the capture, so the frame age is stale by construction (the hover loop above has the same property and Stage 5 re-arms the heartbeat on entry)." O `finally` já zera o comando de yaw; conferir que o `FAILURE` por emergência também passa por ele (o `with ctx.failsafe.yaw_window(...)` fecha antes do `finally`).

- [ ] **Step 5: Falas dos steps por modo (CLI sem estação)** — `steps/search.py`, no `_announce("Acidente detectado", "alvo detectado na pista, iniciando aproximação")` do Step 2 (procurar por `Acidente detectado`): no Simples, o detalhe passa a `"alvo detectado, centralizando por rotação"`:

```python
                detail = (
                    "alvo detectado, centralizando por rotação"
                    if self._mode is MissionMode.SIMPLE
                    else "alvo detectado na pista, iniciando aproximação"
                )
                self._announce("Acidente detectado", detail)
```

Auditoria dos demais `_announce`/alertas do backend: rodar

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop && grep -rn "_announce(\|announce_sync(\|emit_alert(" mvp_mission_bebop | grep -i "retorn\|aproxim\|velocidade\|varredura\|cruzeiro" | grep -v "^mvp_mission_bebop/steps/rtl.py:.*ArUco"
```

Cada ocorrência alcançável no Simples ou na Intermediária (ex.: "iniciando varredura" é neutra; "retorno" não pode sair em Intermediária/Simples) vira ramo por modo, ou fica documentada no relatório como alcançável só no Avançado. Esperado: nenhuma além da do Step 2 acima; o Step 5 já separa os textos por modo (plano anterior, Task 8).

- [ ] **Step 6: Rodar**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mode_parity.py test/test_simple_mode_steps.py test/test_failsafe.py test/test_telemetry_freshness.py test/test_inspection_hover.py test/test_battery_guard.py -q 2>&1 | tail -15
```

Expected: PASS.

- [ ] **Step 7: Checkpoint (sem executar)** Mensagem proposta: `fix(inspection): enforce system health during the Simple heading restore`.

---

## Task 4: Folha do Simples: sem margem do teto, altura limitada à altura nativa

**Files:**
- Modify: `bebop_mission_control/src/lib/parameterSchema.ts`, `src/lib/missionMode.ts`, `src/lib/launchDocument.ts`, `src/lib/parameterConflicts.ts`
- Test: `src/lib/missionMode.test.ts`, `src/lib/launchDocument.test.ts`, `src/components/preflight/ParameterSheet.test.tsx`

**Interfaces:**
- Produces: `NumberParameter.rangeByMode?: Partial<Record<MissionMode, { min?: number; max?: number }>>`; `groupsForMode(mode)` devolve cada item com `min`/`max` já resolvidos para o modo; `normalizeForMode(doc: Doc, mode: MissionMode, factory: unknown): Doc` em `launchDocument.ts` (aperta a altura para a faixa do modo e, no Simples, fixa a margem do teto no valor de fábrica); `SIMPLE_ALTITUDE_RANGE_M = { min: 0.6, max: FIRMWARE_HOVER_HEIGHT_M }`.

- [ ] **Step 1: Testes que falham**

Em `missionMode.test.ts`, trocar as expectativas do bloco do Simples e acrescentar:

```ts
  it('drops the ceiling margin from the simple sheet and keeps the flight altitude', () => {
    const shown = paths('simple');
    expect(shown).not.toContain('kinematics.altitude_ceiling_margin_m');
    expect(shown).toContain('kinematics.target_altitude_m');
    expect(shown).toContain('kinematics.takeoff_stabilize_duration_sec');
    expect(paths('intermediate')).toContain('kinematics.altitude_ceiling_margin_m');
    expect(paths('advanced')).toContain('kinematics.altitude_ceiling_margin_m');
  });

  it('limits the simple flight altitude to the native takeoff height', () => {
    const altitude = (mode: Parameters<typeof groupsForMode>[0]) =>
      groupsForMode(mode).flatMap((g) => g.items).find((i) => i.path === 'kinematics.target_altitude_m')!;
    expect(altitude('simple')).toMatchObject({ min: 0.6, max: 1 });
    expect(altitude('advanced').max).toBe(4);
    expect(altitude('intermediate').max).toBe(4);
  });

  it('marks the ceiling margin inactive only in the simple mode', () => {
    expect(isPathActive('simple', 'kinematics.altitude_ceiling_margin_m')).toBe(false);
    expect(isPathActive('intermediate', 'kinematics.altitude_ceiling_margin_m')).toBe(true);
  });
```

(O teste "swaps the cruise velocity..." existente continua válido; o conjunto de caminhos do Simples já exclui a margem agora.)

Em `launchDocument.test.ts`:

```ts
describe('normalizeForMode', () => {
  const factory = { kinematics: { altitude_ceiling_margin_m: 0.6 } };

  it('squeezes the altitude into the simple range and pins the margin', () => {
    const doc = { kinematics: { target_altitude_m: 1.8, altitude_ceiling_margin_m: 0.1 } };
    const next = normalizeForMode(doc, 'simple', factory);
    expect(next.kinematics).toMatchObject({ target_altitude_m: 1, altitude_ceiling_margin_m: 0.6 });
  });

  it('raises an altitude below the simple floor', () => {
    const next = normalizeForMode({ kinematics: { target_altitude_m: 0.3 } }, 'simple', factory);
    expect((next.kinematics as { target_altitude_m: number }).target_altitude_m).toBe(0.6);
  });

  it('leaves the other modes untouched, by reference', () => {
    const doc = { kinematics: { target_altitude_m: 1.8, altitude_ceiling_margin_m: 0.1 } };
    expect(normalizeForMode(doc, 'advanced', factory)).toBe(doc);
    expect(normalizeForMode(doc, 'intermediate', factory)).toBe(doc);
  });

  it('does not pin the margin without a factory value', () => {
    const doc = { kinematics: { target_altitude_m: 0.8, altitude_ceiling_margin_m: 0.1 } };
    expect(normalizeForMode(doc, 'simple', null)).toBe(doc);
  });
});

describe('launch checks in the simple mode', () => {
  it('does not block on a ceiling margin the mode hides', () => {
    const doc = { ...completeDocument(), mission_mode: 'simple' } as Record<string, unknown>;
    const broken = setPath(setPath(doc, 'kinematics.altitude_ceiling_margin_m', -3), 'kinematics.target_altitude_m', 0.8);
    expect(launchBlockers(broken)).not.toContain('kinematics.altitude_ceiling_margin_m');
  });

  it('blocks an altitude above the native height only in the simple mode', () => {
    const base = setPath(completeDocument(), 'kinematics.target_altitude_m', 1.4) as Record<string, unknown>;
    expect(launchBlockers({ ...base, mission_mode: 'simple' })).toContain('kinematics.target_altitude_m');
    expect(launchBlockers({ ...base, mission_mode: 'advanced' })).not.toContain('kinematics.target_altitude_m');
  });

  it('raises no conflict for any altitude the simple slider offers, with the pinned margin', () => {
    const factory = factoryDocument();
    for (let altitude = 0.6; altitude <= 1.0001; altitude += 0.1) {
      const doc = normalizeForMode(
        setPath(setPath(validDocument(), 'mission_mode', 'simple'), 'kinematics.target_altitude_m', Math.round(altitude * 10) / 10),
        'simple',
        factory
      );
      expect(activeConflicts(doc).filter((c) => c.paths.includes('kinematics.altitude_ceiling_margin_m'))).toEqual([]);
    }
  });
});
```

(Importar `normalizeForMode`, `launchBlockers`, `activeConflicts`, `factoryDocument`, `validDocument`, `completeDocument` e `setPath` conforme o arquivo.)

Em `ParameterSheet.test.tsx`: no teste "swaps the cruise velocity for the turn rate in the simple mode", acrescentar `expect(text()).not.toContain('Margem do Teto de Altitude');` e, em novo teste, `expect(text()).toContain('Margem do Teto de Altitude')` para `intermediate` e `advanced`.

- [ ] **Step 2: Rodar e ver falhar** (`npx vitest run src/lib/missionMode.test.ts src/lib/launchDocument.test.ts src/components/preflight/ParameterSheet.test.tsx`).

- [ ] **Step 3: Implementar**

`parameterSchema.ts`: em `NumberParameter`, `rangeByMode?: Partial<Record<MissionMode, { min?: number; max?: number }>>;`. No item `kinematics.target_altitude_m`: `rangeByMode: { simple: { min: SIMPLE_ALTITUDE_RANGE_M.min, max: SIMPLE_ALTITUDE_RANGE_M.max } }`, hint por modo `hints: { simple: 'Altura do voo, até a altura nativa de decolagem do Bebop (1,0 m). A aeronave decola pela rotina nativa e mantém essa altura.' }`. No item `kinematics.altitude_ceiling_margin_m`: `modes: ['advanced', 'intermediate']`. `groupsForMode` resolve o range por modo no `.map` (`{ ...item, hint: ..., min: item.rangeByMode?.[mode]?.min ?? item.min, max: item.rangeByMode?.[mode]?.max ?? item.max }`).

`SIMPLE_ALTITUDE_RANGE_M` em `missionMode.ts` (importando `FIRMWARE_HOVER_HEIGHT_M` de `parameterConflicts.ts` criaria ciclo: `parameterConflicts` já importa `missionMode`). Definir a constante em `missionMode.ts` com o literal `{ min: 0.6, max: 1.0 }`, e acrescentar em `parameterConflicts.test.ts` a asserção `expect(SIMPLE_ALTITUDE_RANGE_M.max).toBe(FIRMWARE_HOVER_HEIGHT_M)`.

`missionMode.ts`: adicionar `'kinematics.altitude_ceiling_margin_m'` ao conjunto inativo do `simple` em `INACTIVE_PATHS`.

`launchDocument.ts`:

- `inEnvelope(path, value)` passa a `inEnvelope(mode, path, value)` e usa o range do modo: `const range = spec.rangeByMode?.[mode]; const min = range?.min ?? spec.min; const max = range?.max ?? spec.max;`. Atualizar `envelopeBlockers(doc)` para ler `readMissionMode(doc)` e repassar; `launchBlockers` já chama `envelopeBlockers(doc)`.
- Nova função:

```ts
/**
 * `doc` made valid for `mode`: in the simple mode the altitude is squeezed into
 * the native-takeoff range and the ceiling margin the sheet hides is pinned to
 * the factory value (the mission pins it as well, `mission_mode.apply_mission_mode`).
 * Other modes return `doc` itself. Without a factory document the margin is left alone.
 */
export function normalizeForMode<T extends Doc>(doc: T, mode: MissionMode, factory: unknown): T {
  if (mode !== 'simple') return doc;
  let next = doc;
  const altitude = getPath(next, 'kinematics.target_altitude_m');
  if (finite(altitude)) {
    const clamped = Math.min(SIMPLE_ALTITUDE_RANGE_M.max, Math.max(SIMPLE_ALTITUDE_RANGE_M.min, altitude));
    if (clamped !== altitude) next = setPath(next, 'kinematics.target_altitude_m', clamped);
  }
  const margin = getPath(factory, 'kinematics.altitude_ceiling_margin_m');
  if (finite(margin) && getPath(next, 'kinematics.altitude_ceiling_margin_m') !== margin) {
    next = setPath(next, 'kinematics.altitude_ceiling_margin_m', margin);
  }
  return next;
}
```

Os usos de `inEnvelope` com o modo (ex.: `advancedFieldErrors`) recebem o modo pelo mesmo caminho; conferir os chamadores com `grep -n "inEnvelope" src/lib/launchDocument.ts`.

`parameterConflicts.ts`: nenhuma regra muda. O teste de varredura de altitude (acima) prova que, com a margem fixada em 0,6 e a altura em 0,6 a 1,0, nenhum conflito cita a margem. Se um conflito aparecer (E03, W01 ou W02), ajustar `SIMPLE_ALTITUDE_RANGE_M.min` para o menor valor que o teste aceite e registrar o novo piso na seção Decisões (item 4).

- [ ] **Step 4: Rodar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib src/components/preflight 2>&1 | tail -20 && npm run typecheck 2>&1 | tail -8
```

Expected: PASS.

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): hide the ceiling margin and cap the altitude in the simple mode`.

---

## Task 5: Parâmetros e favorito isolados por modo

**Files:**
- Create: `bebop_mission_control/src/lib/parameterModes.ts`, `bebop_mission_control/src/lib/parameterModes.test.ts`
- Modify: `bebop_mission_control/src/hooks/useMissionParameters.ts`, `bebop_mission_control/src/hooks/useMissionParameters.test.tsx`, `bebop_mission_control/src/components/preflight/ParameterPreset.test.tsx`, `bebop_mission_control/src/components/preflight/PreflightScreen.tsx`, `bebop_mission_control/src/App.tsx`
- Test: os citados.

**Interfaces:**
- Produces (`parameterModes.ts`):
  - `workingKey(mode: MissionMode): string` = `bmg.working-parameters.${mode}`; `presetKey(mode)` = `bmg.operator-preset.${mode}`; `ACTIVE_MODE_KEY = 'bmg.active-mode'`
  - `MODE_OWNED_PATHS: readonly string[]` (os caminhos de `ALL_PARAMETERS`, seus espelhos e `ADVANCED_PATHS`)
  - `applyOwnedPaths<T extends Doc>(target: T, source: unknown): T` (copia só os caminhos possuídos que o `source` traz; o resto de `target` permanece)
  - `readStoredDoc(storage: StorageLike | null, key: string): Doc | null` e `writeStoredDoc(storage, key, doc): boolean` (nunca lançam)
  - `migrateLegacyKeys(storage: StorageLike | null): void` (copia `.v2` para o modo `advanced` se este ainda não existir; não apaga o `.v2`)
  - `switchModeDocument(input: { working: Doc; from: MissionMode; to: MissionMode; stored: Doc | null; factory: unknown }): { doc: Doc; stash: Doc; usedStored: boolean }` (puro)
- Produces (hook): o retorno de `useMissionParameters()` ganha `missionMode: MissionMode` e `switchMode: (next: MissionMode) => Promise<boolean>`; `preset`, `saveAsPreset`, `applyPreset` e `isPresetActive` passam a ser do modo ativo.

- [ ] **Step 1: Testes puros que falham** — `src/lib/parameterModes.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { completeDocument, factoryDocument } from './__fixtures__/missionDocument';
import {
  ACTIVE_MODE_KEY,
  MODE_OWNED_PATHS,
  applyOwnedPaths,
  migrateLegacyKeys,
  presetKey,
  readStoredDoc,
  switchModeDocument,
  workingKey,
  writeStoredDoc,
} from './parameterModes';
import { getPath, setPath } from './paths';

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}

describe('keys', () => {
  it('namespaces the working document and the favourite by mode', () => {
    expect(workingKey('simple')).toBe('bmg.working-parameters.simple');
    expect(presetKey('intermediate')).toBe('bmg.operator-preset.intermediate');
    expect(ACTIVE_MODE_KEY).toBe('bmg.active-mode');
  });
});

describe('applyOwnedPaths', () => {
  it('moves the sheet and advanced fields and nothing else', () => {
    const target = setPath(completeDocument(), 'lateral_pid.kp', 0.42);
    const source = setPath(setPath(completeDocument(), 'kinematics.target_altitude_m', 0.7), 'lateral_pid.kp', 9);
    const next = applyOwnedPaths(target, source);
    expect(getPath(next, 'kinematics.target_altitude_m')).toBe(0.7);
    expect(getPath(next, 'lateral_pid.kp')).toBe(0.42);
  });

  it('skips a path the source does not hold', () => {
    const next = applyOwnedPaths(completeDocument(), { kinematics: {} });
    expect(getPath(next, 'kinematics.target_altitude_m')).toBe(getPath(completeDocument(), 'kinematics.target_altitude_m'));
  });

  it('owns every sheet field and its mirror', () => {
    expect(MODE_OWNED_PATHS).toContain('kinematics.target_altitude_m');
    expect(MODE_OWNED_PATHS).toContain('rtl.reverse_cruise_velocity');
    expect(MODE_OWNED_PATHS).toContain('rtl.target_aruco_id');
    expect(MODE_OWNED_PATHS).not.toContain('lateral_pid.kp');
    expect(MODE_OWNED_PATHS).not.toContain('no_fly');
    expect(MODE_OWNED_PATHS).not.toContain('mission_mode');
  });
});

describe('storage', () => {
  it('round-trips a document', () => {
    const storage = new MemoryStorage();
    expect(writeStoredDoc(storage, workingKey('simple'), { a: 1 })).toBe(true);
    expect(readStoredDoc(storage, workingKey('simple'))).toEqual({ a: 1 });
  });

  it('reads a missing, corrupt or non-object entry as nothing', () => {
    const storage = new MemoryStorage();
    expect(readStoredDoc(storage, 'k')).toBeNull();
    storage.setItem('k', '{not json');
    expect(readStoredDoc(storage, 'k')).toBeNull();
    storage.setItem('k', '[1,2]');
    expect(readStoredDoc(storage, 'k')).toBeNull();
    expect(readStoredDoc(null, 'k')).toBeNull();
  });

  it('survives a storage that throws', () => {
    const hostile = {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('quota'); },
    };
    expect(readStoredDoc(hostile, 'k')).toBeNull();
    expect(writeStoredDoc(hostile, 'k', {})).toBe(false);
  });
});

describe('migrateLegacyKeys', () => {
  it('moves the single v2 document and favourite to the advanced mode, keeping the originals', () => {
    const storage = new MemoryStorage();
    storage.setItem('bmg.working-parameters.v2', JSON.stringify({ w: 1 }));
    storage.setItem('bmg.operator-preset.v2', JSON.stringify({ p: 1 }));
    migrateLegacyKeys(storage);
    expect(readStoredDoc(storage, workingKey('advanced'))).toEqual({ w: 1 });
    expect(readStoredDoc(storage, presetKey('advanced'))).toEqual({ p: 1 });
    expect(storage.getItem('bmg.working-parameters.v2')).not.toBeNull();
    expect(readStoredDoc(storage, workingKey('simple'))).toBeNull();
  });

  it('never overwrites what the advanced mode already holds', () => {
    const storage = new MemoryStorage();
    storage.setItem('bmg.working-parameters.v2', JSON.stringify({ w: 1 }));
    storage.setItem(workingKey('advanced'), JSON.stringify({ w: 2 }));
    migrateLegacyKeys(storage);
    expect(readStoredDoc(storage, workingKey('advanced'))).toEqual({ w: 2 });
  });
});

describe('switchModeDocument', () => {
  const factory = factoryDocument();
  const advanced = setPath(setPath(completeDocument(), 'mission_mode', 'advanced'), 'kinematics.target_altitude_m', 2.4);

  it('uses a copy of the current values the first time a mode is entered', () => {
    const { doc, stash, usedStored } = switchModeDocument({ working: advanced, from: 'advanced', to: 'intermediate', stored: null, factory });
    expect(usedStored).toBe(false);
    expect(getPath(doc, 'mission_mode')).toBe('intermediate');
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(2.4);
    expect(getPath(stash, 'kinematics.target_altitude_m')).toBe(2.4);
  });

  it('restores what the target mode last used', () => {
    const stored = setPath(completeDocument(), 'kinematics.target_altitude_m', 0.8);
    const { doc, usedStored } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored, factory });
    expect(usedStored).toBe(true);
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(0.8);
    expect(getPath(doc, 'mission_mode')).toBe('simple');
  });

  it('squeezes a copied altitude into the simple range and pins the margin', () => {
    const { doc } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored: null, factory });
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(1);
    expect(getPath(doc, 'kinematics.altitude_ceiling_margin_m')).toBe(getPath(factory, 'kinematics.altitude_ceiling_margin_m'));
  });

  it('keeps the fields the modes do not own', () => {
    const working = setPath(advanced, 'lateral_pid.kp', 0.77);
    const { doc } = switchModeDocument({ working, from: 'advanced', to: 'simple', stored: completeDocument(), factory });
    expect(getPath(doc, 'lateral_pid.kp')).toBe(0.77);
  });

  it('stashes the leaving mode with its unsaved edits', () => {
    const { stash } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored: null, factory });
    expect(getPath(stash, 'kinematics.target_altitude_m')).toBe(2.4);
    expect(getPath(stash, 'mission_mode')).toBe('advanced');
  });

  it('keeps the return cruise coupled to the forward cruise after a restore', () => {
    const stored = setPath(setPath(completeDocument(), 'kinematics.forward_cruise_velocity', 0.3), 'rtl.reverse_cruise_velocity', -0.1);
    const { doc } = switchModeDocument({ working: advanced, from: 'advanced', to: 'intermediate', stored, factory });
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(-0.3);
  });
});
```

- [ ] **Step 2: Rodar e ver falhar** (`npx vitest run src/lib/parameterModes.test.ts`: módulo inexistente).

- [ ] **Step 3: Criar `src/lib/parameterModes.ts`**

```ts
/**
 * Flight parameters and the operator's favourite, kept apart for each mission mode.
 *
 * The station edits one document (`mission_config.json`), but an operator tunes
 * three different missions: the figures that suit the Simple mission are not the
 * ones of the Advanced. Each mode therefore keeps its own last-used document and
 * its own favourite in `localStorage`, under `bmg.working-parameters.<mode>` and
 * `bmg.operator-preset.<mode>`. Whole documents are stored, but only the paths a
 * mode OWNS are ever copied back (`MODE_OWNED_PATHS`): PID gains, calibration
 * statistics and the arming mode are shared and never travel between modes.
 */
import type { MissionMode } from './missionMode';
import { MISSION_MODE_PATH } from './missionMode';
import { normalizeForMode, reconcileCoupledPaths } from './launchDocument';
import { ADVANCED_PATHS, ALL_PARAMETERS } from './parameterSchema';
import { getPath, setPath, type Doc } from './paths';

export const WORKING_KEY_PREFIX = 'bmg.working-parameters.';
export const PRESET_KEY_PREFIX = 'bmg.operator-preset.';
export const ACTIVE_MODE_KEY = 'bmg.active-mode';

const LEGACY_WORKING_KEY = 'bmg.working-parameters.v2';
const LEGACY_PRESET_KEY = 'bmg.operator-preset.v2';

export const workingKey = (mode: MissionMode): string => `${WORKING_KEY_PREFIX}${mode}`;
export const presetKey = (mode: MissionMode): string => `${PRESET_KEY_PREFIX}${mode}`;

/** The part of `Storage` this module touches. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** Paths whose values belong to a mode: the sheet fields, their mirrors and the advanced pad fields. */
export const MODE_OWNED_PATHS: readonly string[] = Array.from(
  new Set([
    ...ALL_PARAMETERS.flatMap((spec) => [spec.path, ...(spec.mirrors?.map((mirror) => mirror.path) ?? [])]),
    ...ADVANCED_PATHS,
  ])
);

/** `target` with the owned paths `source` holds copied in; everything else in `target` is kept. */
export function applyOwnedPaths<T extends Doc>(target: T, source: unknown): T {
  let next = target;
  for (const path of MODE_OWNED_PATHS) {
    const value = getPath(source, path);
    if (value !== undefined) next = setPath(next, path, value);
  }
  return next;
}

/** A stored document, or null when absent, unreadable, or not a JSON object. Never throws. */
export function readStoredDoc(storage: StorageLike | null, key: string): Doc | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Doc) : null;
  } catch {
    return null;
  }
}

/** Persist a document. Returns false when the store refused. Never throws. */
export function writeStoredDoc(storage: StorageLike | null, key: string, doc: Doc): boolean {
  if (!storage) return false;
  try {
    storage.setItem(key, JSON.stringify(doc));
    return true;
  } catch {
    return false;
  }
}

/**
 * Carry the single pre-modes document and favourite over to the Advanced mode,
 * the only mode that existed when they were written. Idempotent and
 * non-destructive: the old keys stay, and nothing already stored is overwritten.
 */
export function migrateLegacyKeys(storage: StorageLike | null): void {
  const pairs: ReadonlyArray<readonly [string, string]> = [
    [LEGACY_WORKING_KEY, workingKey('advanced')],
    [LEGACY_PRESET_KEY, presetKey('advanced')],
  ];
  for (const [legacy, target] of pairs) {
    if (readStoredDoc(storage, target)) continue;
    const old = readStoredDoc(storage, legacy);
    if (old) writeStoredDoc(storage, target, old);
  }
}

export interface ModeSwitchInput {
  /** The document being edited, with any unsaved edits. */
  working: Doc;
  from: MissionMode;
  to: MissionMode;
  /** What `to` last used, or null the first time it is entered. */
  stored: Doc | null;
  /** `MissionParameters.factory()`, for the values a mode pins. */
  factory: unknown;
}

export interface ModeSwitchResult {
  /** The document to edit in `to`. */
  doc: Doc;
  /** The document to store for `from`, so leaving it loses no edit. */
  stash: Doc;
  /** False when `to` had nothing stored and starts from a copy of the current values. */
  usedStored: boolean;
}

/**
 * Switch the editing document from one mode to another.
 *
 * Pure. The target mode's owned values come from `stored` when it has any,
 * otherwise from a copy of `working` (first use). The mode is then normalised
 * (the Simple altitude range, the pinned margin) and the coupled cruise paths
 * are reconciled.
 */
export function switchModeDocument({ working, from, to, stored, factory }: ModeSwitchInput): ModeSwitchResult {
  const stash = setPath(working, MISSION_MODE_PATH, from);
  const base = stored ? applyOwnedPaths(working, stored) : working;
  const moved = setPath(base, MISSION_MODE_PATH, to);
  const doc = reconcileCoupledPaths(normalizeForMode(moved, to, factory));
  return { doc, stash, usedStored: stored !== null };
}
```

Atenção ao ciclo de imports: `parameterModes.ts` importa de `launchDocument.ts`, que importa de `parameterSchema.ts` e `missionMode.ts`; nenhum deles importa `parameterModes`. Conferir com `npm run typecheck`.

- [ ] **Step 4: Rodar** (`npx vitest run src/lib/parameterModes.test.ts`): PASS. Ajustar só a implementação; os testes são o contrato.

- [ ] **Step 5: Testes do hook que falham** — em `src/hooks/useMissionParameters.test.tsx`, seguir o padrão de montagem já usado no arquivo (ponte falsa com `getParameters`, `getParameterDefaults`, `saveParameters`, e `window.localStorage` do jsdom limpo em `beforeEach`). Atualizar as chaves `bmg.operator-preset.v2`/`bmg.working-parameters.v2` dos testes existentes para as por modo (`...advanced`) e acrescentar:

```tsx
describe('per-mode parameters', () => {
  it('keeps the working values of each mode apart', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    await act(() => hook.current.switchMode('simple'));
    expect(hook.current.missionMode).toBe('simple');
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(1); // squeezed copy
    act(() => hook.current.edit('kinematics.target_altitude_m', 0.7));
    await act(() => hook.current.save());

    await act(() => hook.current.switchMode('advanced'));
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(2.4);
    await act(() => hook.current.switchMode('simple'));
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(0.7);
  });

  it('never lets a save in one mode overwrite another mode\'s stored document', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    await act(() => hook.current.switchMode('intermediate'));
    act(() => hook.current.edit('kinematics.target_altitude_m', 3.1));
    await act(() => hook.current.save());
    const advancedStored = JSON.parse(window.localStorage.getItem('bmg.working-parameters.advanced') ?? '{}');
    expect(getPath(advancedStored, 'kinematics.target_altitude_m')).toBe(2.4);
  });

  it('keeps one favourite per mode', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    act(() => hook.current.saveAsPreset());
    await act(() => hook.current.switchMode('intermediate'));
    expect(hook.current.preset).toBeNull();
    act(() => hook.current.edit('kinematics.target_altitude_m', 3.3));
    act(() => hook.current.saveAsPreset());
    await act(() => hook.current.switchMode('advanced'));
    act(() => hook.current.edit('kinematics.target_altitude_m', 1.1));
    act(() => hook.current.applyPreset());
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(2.4);
    expect(window.localStorage.getItem('bmg.operator-preset.intermediate')).toContain('3.3');
  });

  it('reports the favourite as active only in its own mode', async () => {
    const hook = await mountReady({ mission_mode: 'advanced' });
    act(() => hook.current.saveAsPreset());
    expect(hook.current.isPresetActive).toBe(true);
    await act(() => hook.current.switchMode('intermediate'));
    expect(hook.current.isPresetActive).toBe(false);
  });

  it('writes the active document to the store on a switch, leaving the sheet clean', async () => {
    const hook = await mountReady({ mission_mode: 'advanced' });
    await act(() => hook.current.switchMode('intermediate'));
    expect(hook.current.dirty).toBe(false);
    expect(saveParameters).toHaveBeenLastCalledWith(expect.objectContaining({ mission_mode: 'intermediate' }));
  });

  it('stashes unsaved edits of the mode being left', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    act(() => hook.current.edit('kinematics.target_altitude_m', 3.7));
    await act(() => hook.current.switchMode('intermediate'));
    await act(() => hook.current.switchMode('advanced'));
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(3.7);
  });

  it('migrates the pre-modes document and favourite into the advanced mode', async () => {
    window.localStorage.setItem('bmg.operator-preset.v2', JSON.stringify(setPath(completeDocument(), 'kinematics.target_altitude_m', 1.9)));
    const hook = await mountReady({ mission_mode: 'advanced' });
    expect(hook.current.preset).not.toBeNull();
    expect(getPath(hook.current.preset, 'kinematics.target_altitude_m')).toBe(1.9);
  });

  it('opens with the values it has when the storage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    expect(hook.current.status).toBe('ready');
    await act(() => hook.current.switchMode('simple'));
    expect(hook.current.missionMode).toBe('simple');
  });
});
```

(`mountReady(overrides)`, `saveParameters` e o `getPath` seguem o harness do arquivo; se o harness atual tiver outros nomes, usar os dele e manter as asserções. `mountReady` monta o hook com a ponte falsa já resolvida e devolve `{ current }` do `renderHook` ou equivalente usado ali.)

- [ ] **Step 6: Implementar no hook** — em `useMissionParameters.ts`:

1. Remover `LOCAL_PRESET_KEY`/`LOCAL_WORKING_KEY`; importar `parameterModes` e `missionMode`.
2. `const storage = useMemo(() => { try { return window.localStorage; } catch { return null; } }, []);` e `migrateLegacyKeys(storage)` uma vez, no início do efeito de carga.
3. Estado novo: o modo ativo é derivado: `const missionMode = readMissionMode(working);`. O `preset` passa a ser carregado do modo ativo num efeito que depende de `missionMode`: `setPreset(readStoredDoc(storage, presetKey(missionMode)))`.
4. `restoreLocal` (sem ponte): ler `ACTIVE_MODE_KEY` (padrão `'advanced'` se ausente ou inválido via `parseMissionMode`) e `readStoredDoc(storage, workingKey(mode))`.
5. `save`: depois de gravar com sucesso, `writeStoredDoc(storage, workingKey(missionMode), working)` e `storage?.setItem(ACTIVE_MODE_KEY, missionMode)` (dentro de `try`). Substitui o `window.localStorage.setItem(LOCAL_WORKING_KEY, ...)`.
6. `saveAsPreset`: `setPreset(working); writeStoredDoc(storage, presetKey(missionMode), working)`.
7. `applyPreset`: sem mudança (usa o `preset` do modo ativo).
8. `switchMode`:

```ts
  const switchMode = useCallback(
    async (next: MissionMode): Promise<boolean> => {
      if (!working || next === missionMode) return false;
      const { doc, stash } = switchModeDocument({
        working,
        from: missionMode,
        to: next,
        stored: readStoredDoc(storage, workingKey(next)),
        factory,
      });
      writeStoredDoc(storage, workingKey(missionMode), stash);
      setWorking(doc);
      setPreset(readStoredDoc(storage, presetKey(next)));
      try { storage?.setItem(ACTIVE_MODE_KEY, next); } catch { /* the document is the source of truth */ }
      if (activeConflicts(doc).some((conflict) => conflict.severity === 'error')) {
        setSaveAttempted(true);
        return false;
      }
      setStatus('saving');
      if (bridge) {
        const result = await bridge.saveParameters(doc);
        if (!result.success) {
          setStatus('error');
          setError(result.error ?? 'Falha ao gravar mission_config.json');
          return false;
        }
      }
      writeStoredDoc(storage, workingKey(next), doc);
      setCommitted(doc);
      setStatus('ready');
      setError(null);
      setSaveAttempted(false);
      return true;
    },
    [bridge, factory, missionMode, storage, working]
  );
```

   e incluir `missionMode` e `switchMode` no objeto retornado.
9. `matchesPreset`, `isPresetActive`, `applySchemaFields`: sem mudança de formato (o favorito continua sendo documento inteiro).
10. O `edit('mission_mode', ...)` direto deixa de ser o caminho do seletor; o hook continua aceitando-o (testes existentes), mas a UI usa `switchMode`.

- [ ] **Step 7: Ligar o seletor** — `PreflightScreen.tsx`: nova prop `onModeChange: (mode: MissionMode) => void;`; o seletor passa a chamar `props.onModeChange(mode)` no lugar de `props.onEdit(MISSION_MODE_PATH, mode)`. `App.tsx`: `onModeChange={(mode) => void params.switchMode(mode)}` (o estado `modeLocked` do plano anterior continua desabilitando o seletor). Ajustar os testes de `PreflightScreen.test.tsx` que verificam `onEdit('mission_mode', ...)` para `onModeChange`.

- [ ] **Step 8: Rodar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/parameterModes.test.ts src/hooks/useMissionParameters.test.tsx src/components/preflight src/App.launchParams.test.tsx 2>&1 | tail -25 && npm run typecheck 2>&1 | tail -8
cd .. && node test/audit_parameter_persistence.cjs 2>&1 | tail -15
```

Expected: PASS. A auditoria de persistência roda sem BMG nem missão abertos; ela confirma que `mission_mode` e os parâmetros fazem o ciclo editar, salvar e reabrir.

- [ ] **Step 9: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): isolate flight parameters and the operator favourite per mission mode`.

---

## Task 6: Copiloto coerente com o modo ativo

**Files:**
- Modify: `bebop_mission_control/src/lib/copilotPhrases.ts`, `src/hooks/useCopilot.ts`, `src/lib/batteryReturn.ts`, `src/App.tsx`, `mvp_mission_bebop/steps/takeoff.py` (nada além do `native` da Task 2)
- Test: `src/lib/copilotPhrases.test.ts`, `src/hooks/useFlightNarration.test.ts`, `src/hooks/useFlightNarration.parameters.test.tsx`, `src/lib/batteryReturn.test.ts`, `src/lib/milestoneStream.test.ts`

**Interfaces:**
- Consumes: marco `mission.mode` (Task 1) e `payload.native` do `mission.takeoff` (Task 2).
- Produces (`copilotPhrases.ts`):
  - novos `PoolKey`: `'mission.takeoff_native'`, `'mission.scan_start_yaw'`, `'mission.approaching_yaw'`
  - `poolForMilestone(key, payload, mode?: MissionMode)` (default `'advanced'`)
  - `phraseForMilestone(key, payload, parameters, options?, mode?)`: o `mode` é o último argumento, para não quebrar os chamadores existentes
  - `isMilestoneAllowed(key: MilestoneKey, mode: MissionMode): boolean`
  - `poolsForMode(mode: MissionMode): readonly PoolKey[]`
  - `allMissionPhrases(parameters, mode?: MissionMode): string[]` (só os pools do modo)
- Produces (`batteryReturn.ts`): `batteryCalls(mode: MissionMode)` com as chaves `alreadyReturning`, `returning`, `landing`; `runCriticalBatteryReturn(action, hooks, mode?: MissionMode)`; `BATTERY_CALLS` continua exportado e igual a `batteryCalls('advanced')`.

- [ ] **Step 1: Testes que falham** — em `copilotPhrases.test.ts` acrescentar:

```ts
import { HISTORY_WINDOW } from './copilotPhrases'; // conferir o nome real da constante exportada
import {
  PHRASE_POOLS,
  allMissionPhrases,
  isMilestoneAllowed,
  phraseForMilestone,
  poolForMilestone,
  poolsForMode,
  type MilestoneKey,
} from './copilotPhrases';
import type { MissionMode } from './missionMode';

/** Sentences no mode may speak, by what each mode does NOT do. */
const FORBIDDEN: Record<MissionMode, RegExp | null> = {
  simple: /velocidade|cruzeiro|retilíne|linear|por segundo|deslocamento|deslocando|avanço|retorno|retornando|regress/i,
  intermediate: /retorn|regress|rota de volta|marcador|na base|à base|base de lançamento/i,
  advanced: null,
};

describe('copilot repertoire by mission mode', () => {
  it.each(['simple', 'intermediate'] as const)('never says what the %s mode does not do', (mode) => {
    for (const key of poolsForMode(mode)) {
      for (const sentence of PHRASE_POOLS[key]) {
        expect(sentence, `${mode} / ${key}`).not.toMatch(FORBIDDEN[mode]!);
      }
    }
  });

  it('keeps the full repertoire for the advanced mode', () => {
    expect(poolsForMode('advanced')).toEqual(expect.arrayContaining(['mission.rtl_start', 'mission.rtl_start_no_target', 'mission.scan_start', 'mission.approaching']));
  });

  it('never reaches the return pools from the reduced modes', () => {
    for (const mode of ['simple', 'intermediate'] as const) {
      const reachable = poolsForMode(mode);
      for (const key of ['mission.rtl_start', 'mission.rtl_start_jump', 'mission.rtl_start_no_target'] as const) {
        expect(reachable).not.toContain(key);
      }
      expect(isMilestoneAllowed('mission.rtl_start', mode)).toBe(false);
    }
    expect(isMilestoneAllowed('mission.rtl_start', 'advanced')).toBe(true);
  });

  it('allows every other milestone in every mode', () => {
    const keys: MilestoneKey[] = ['mission.takeoff', 'mission.scan_start', 'mission.target_found', 'mission.approaching', 'mission.capture_done', 'mission.landing', 'mission.touchdown'];
    for (const mode of ['simple', 'intermediate', 'advanced'] as const) {
      for (const key of keys) expect(isMilestoneAllowed(key, mode)).toBe(true);
    }
  });

  it('speaks a yaw-scan line for the scan start in the simple mode', () => {
    for (let i = 0; i < 12; i += 1) {
      const text = phraseForMilestone('mission.scan_start', { cruise_mps: 0.2 }, { cruise_mps: 0.2 }, { storage: null, random: () => i / 12 }, 'simple');
      expect(PHRASE_POOLS['mission.scan_start_yaw']).toContain(text);
    }
  });

  it('speaks an angular-centring line for the approach in the simple mode, whatever the payload says', () => {
    for (const payload of [{ ibvs: true }, { ibvs: false }, {}]) {
      expect(poolForMilestone('mission.approaching', payload, 'simple')).toBe('mission.approaching_yaw');
    }
    expect(poolForMilestone('mission.approaching', { ibvs: true }, 'intermediate')).toBe('mission.approaching');
    expect(poolForMilestone('mission.approaching', { ibvs: true })).toBe('mission.approaching');
  });

  it('says the takeoff is the native one only in the simple mode', () => {
    expect(poolForMilestone('mission.takeoff', { native: true }, 'simple')).toBe('mission.takeoff_native');
    expect(poolForMilestone('mission.takeoff', { native: true }, 'advanced')).toBe('mission.takeoff');
    expect(poolForMilestone('mission.takeoff', {}, 'simple')).toBe('mission.takeoff_native');
  });

  it('announces the landing in place, not a return, in the intermediate mode', () => {
    expect(poolForMilestone('mission.landing', { at_base: false }, 'intermediate')).toBe('mission.landing_in_place');
    expect(poolForMilestone('mission.touchdown', { confirmed: true, at_base: false }, 'intermediate')).toBe('mission.touchdown_in_place');
  });

  it('announces the landing on the base in the simple mode', () => {
    expect(poolForMilestone('mission.landing', { at_base: true }, 'simple')).toBe('mission.landing');
    expect(poolForMilestone('mission.touchdown', { confirmed: true, at_base: true }, 'simple')).toBe('mission.touchdown');
  });

  it('keeps every new pool larger than the cross-flight history window', () => {
    for (const key of ['mission.takeoff_native', 'mission.scan_start_yaw', 'mission.approaching_yaw'] as const) {
      expect(PHRASE_POOLS[key].length).toBeGreaterThan(HISTORY_WINDOW);
    }
  });

  it('caches only the sentences the mode can speak', () => {
    const parameters = { target_altitude_m: 1, cruise_mps: 0.2 };
    const simple = allMissionPhrases(parameters, 'simple');
    const intermediate = allMissionPhrases(parameters, 'intermediate');
    const advanced = allMissionPhrases(parameters);
    expect(simple.some((s) => PHRASE_POOLS['mission.rtl_start'].includes(s))).toBe(false);
    expect(intermediate.some((s) => PHRASE_POOLS['mission.rtl_start'].includes(s))).toBe(false);
    expect(advanced.some((s) => PHRASE_POOLS['mission.rtl_start'].includes(s))).toBe(true);
    expect(simple.some((s) => PHRASE_POOLS['mission.scan_start_yaw'].includes(s))).toBe(true);
  });
});
```

(`HISTORY_WINDOW`: usar o nome realmente exportado por `copilotPhrases.ts`; ele é citado no comentário do arquivo como `HISTORY_WINDOW`. Se for `const` não exportada, exportá-la.)

Em `src/lib/batteryReturn.test.ts`:

```ts
import { BATTERY_CALLS, batteryCalls, runCriticalBatteryReturn } from './batteryReturn';

describe('battery calls by mode', () => {
  it('keeps the advanced wording', () => {
    expect(batteryCalls('advanced')).toEqual(BATTERY_CALLS);
    expect(BATTERY_CALLS.returning).toBe('Bateria crítica. Retornando à base para pouso.');
  });

  it('speaks a landing in place for the intermediate mode and never a return', () => {
    const calls = batteryCalls('intermediate');
    for (const text of Object.values(calls)) expect(text).not.toMatch(/retorn|base/i);
    expect(calls.returning).toBe('Bateria crítica. Pouso no local iniciado.');
  });

  it('speaks a landing on the base for the simple mode and never a return', () => {
    const calls = batteryCalls('simple');
    for (const text of Object.values(calls)) expect(text).not.toMatch(/retorn/i);
    expect(calls.returning).toBe('Bateria crítica. Pouso na base iniciado.');
  });

  it('says the call of the mode when the jump to stage 5 is acknowledged', async () => {
    const say = vi.fn();
    await runCriticalBatteryReturn('rtl', { say, gotoStage: async () => ({ success: true }), land: async () => undefined }, 'intermediate');
    expect(say).toHaveBeenCalledWith('battery.critical', 'Bateria crítica. Pouso no local iniciado.');
  });
});
```

Em `useFlightNarration.test.ts` (padrão: ponte falsa que emite marcos; a fila `NarrationQueue` real ou falsa já usada ali), acrescentar casos de comportamento:

```ts
  it('drops mission.rtl_start in a reduced mode even if it arrives', () => {
    const { emit, spoken } = harness();
    emit('mission.mode', { mode: 'intermediate' });
    emit('mission.rtl_start', { inspected: true, marker_guided: true });
    expect(spoken()).toEqual([]);
  });

  it('speaks the yaw-scan line for the scan start in the simple mode', () => {
    const { emit, spoken } = harness();
    emit('mission.mode', { mode: 'simple' });
    emit('mission.takeoff', { altitude_m: 1, native: true });
    emit('mission.scan_start', { window_sec: 30, cruise_mps: 0.2 });
    expect(spoken().some((line) => PHRASE_POOLS['mission.scan_start_yaw'].includes(line))).toBe(true);
    expect(spoken().some((line) => PHRASE_POOLS['mission.scan_start'].includes(line))).toBe(false);
  });

  it('falls back to the full repertoire when no mode was reported', () => {
    const { emit, spoken } = harness();
    emit('mission.takeoff', { altitude_m: 1 });
    emit('mission.scan_start', { window_sec: 30, cruise_mps: 0.2 });
    expect(spoken().some((line) => PHRASE_POOLS['mission.scan_start'].includes(line))).toBe(true);
  });

  it('forgets the mode at the start of the next mission', () => {
    const { emit, spoken } = harness();
    emit('mission.mode', { mode: 'simple' });
    emit('mission.start', {});
    emit('mission.takeoff', { altitude_m: 1 });
    emit('mission.scan_start', { window_sec: 30, cruise_mps: 0.2 });
    expect(spoken().some((line) => PHRASE_POOLS['mission.scan_start'].includes(line))).toBe(true);
  });

  it('assigns the approach milestone to stage 3 so earlier-stage lines are pruned', () => {
    const { emit, stagesOf } = harness();
    emit('mission.approaching', { ibvs: true });
    expect(stagesOf('mission.approaching')).toBe(3);
  });
```

(`harness()`, `emit`, `spoken`, `stagesOf` são ajudantes do arquivo de teste; reaproveitar os existentes ou criá-los no estilo do arquivo, conferindo como `queue.enqueue` recebe `{ stage }`. A última asserção cobre a chave `mission.approach` errada em `MILESTONE_STAGE`.)

- [ ] **Step 2: Rodar e ver falhar.**

- [ ] **Step 3: Implementar `copilotPhrases.ts`**

Novos pools (acrescentar ao tipo `PoolKey` e a `PHRASE_POOLS`, mesmo registro curto e declarativo dos demais, sem números nem a palavra "base" fora do pouso):

```ts
  'mission.takeoff_native': [
    'Decolagem pela rotina nativa do Bebop. Estabilizando na altura padrão.',
    'Decolagem nativa em curso. Aguardando a estabilização.',
    'Aeronave no ar pela decolagem nativa. Estabilizando.',
    'Decolagem confirmada. Estabilizando na altura nativa da aeronave.',
    'Voo autônomo iniciado. Decolagem nativa concluída, estabilizando.',
    'Decolagem executada pelo firmware. Estabilizando a aeronave.',
  ],
  'mission.scan_start_yaw': [
    'Varredura por rotação iniciada. Buscando ocorrências ao redor da aeronave.',
    'Girando sobre o próprio eixo. Visão computacional ativa.',
    'Busca angular iniciada. Detector de objetos em operação.',
    'Varredura em andamento. Procurando sinistro em torno da aeronave.',
    'Busca por giro iniciada. Câmera ativa.',
    'Rotação de busca iniciada. Monitorando a área.',
  ],
  'mission.approaching_yaw': [
    'Alvo na imagem. Centralizando por rotação.',
    'Centralização angular do alvo iniciada.',
    'Alinhando a aeronave ao sinistro por giro.',
    'Guiagem visual ativa. Alinhando o alvo ao centro da câmera.',
    'Centralizando o sinistro no quadro, sem deslocamento.',
    'Ajuste de rotação em curso. Alvo sendo centralizado.',
  ],
```

Funções:

```ts
/** Pools a mission mode can reach. The return pools exist only in the advanced mode. */
const RETURN_POOLS: ReadonlySet<PoolKey> = new Set<PoolKey>(['mission.rtl_start', 'mission.rtl_start_jump', 'mission.rtl_start_no_target']);
/** Pools whose wording assumes translation, a speed or the corrective climb: the simple mode never flies it. */
const TRANSLATION_POOLS: ReadonlySet<PoolKey> = new Set<PoolKey>(['mission.takeoff', 'mission.scan_start', 'mission.approaching', 'mission.approaching_direct']);
/** Pools written for the simple mode alone. */
const SIMPLE_ONLY_POOLS: ReadonlySet<PoolKey> = new Set<PoolKey>(['mission.takeoff_native', 'mission.scan_start_yaw', 'mission.approaching_yaw']);

export function poolsForMode(mode: MissionMode): readonly PoolKey[] {
  return POOL_KEYS.filter((key) => {
    if (mode !== 'advanced' && RETURN_POOLS.has(key)) return false;
    if (mode === 'simple' && TRANSLATION_POOLS.has(key)) return false;
    if (mode !== 'simple' && SIMPLE_ONLY_POOLS.has(key)) return false;
    return true;
  });
}

/** Whether the station may speak `key` in `mode`. The mission already withholds the return milestone outside the advanced mode; this is the station's own guard. */
export function isMilestoneAllowed(key: MilestoneKey, mode: MissionMode): boolean {
  return !(key === 'mission.rtl_start' && mode !== 'advanced');
}
```

`poolForMilestone(key, payload, mode = 'advanced')`: antes do `switch`, no `mission.takeoff` com `mode === 'simple'` retornar `'mission.takeoff_native'` (o `payload.native` é redundante mas aceito; o modo manda); `mission.scan_start` com `simple` retorna `'mission.scan_start_yaw'`; `mission.approaching` com `simple` retorna `'mission.approaching_yaw'` independentemente de `ibvs`. Os demais casos não mudam. Para a Intermediária e a Avançada nada muda no `switch`.

`phraseForMilestone(key, payload, parameters, options = {}, mode: MissionMode = 'advanced')` passa `mode` a `poolForMilestone`. `allMissionPhrases(parameters, mode = 'advanced')` itera `poolsForMode(mode)` em vez de `POOL_KEYS`. Os valores `{speed}` e `{altitude}` não são fornecidos aos pools do Simples (não têm placeholder).

`FALLBACK_VARIANTS` não muda. Atenção a `vetoFor(key, payload)`: sem mudança.

- [ ] **Step 4: Implementar `useFlightNarration`** — em `src/hooks/useCopilot.ts`:

- Corrigir `MILESTONE_STAGE`: acrescentar `'mission.approaching': 3` (manter `'mission.approach': 3` por compatibilidade).
- `const missionMode = useRef<MissionMode | null>(null);`
- No `onMilestone`, antes do `if (!enabledRef.current || !isMilestoneKey(event.key)) return;` e depois do tratamento de `mission.parameters`:

```ts
      if (event.key === 'mission.mode') {
        const reported = parseMissionMode((event.payload ?? {}).mode);
        missionMode.current = reported;
        return;
      }
```

- Em `key === 'mission.start'`: `missionMode.current = null;` junto de `parameters.current = {}`.
- Em `mission.parameters`: `allMissionPhrases(parameters.current, missionMode.current ?? 'advanced')`. O cache usa os `FIXED_ALERT_SENTENCES` ampliados com as frases de bateria dos três modos: `...Object.values(batteryCalls('advanced')), ...Object.values(batteryCalls('intermediate')), ...Object.values(batteryCalls('simple'))` (as de alerta são pré-sintetizadas para qualquer modo, pois o alerta não depende de eco).
- Antes de enfileirar: `const mode = missionMode.current ?? 'advanced'; if (!isMilestoneAllowed(key, mode)) return;` e `phraseForMilestone(key, payload, parameters.current, {}, mode)`.

Documentar no docstring do hook: "The profile comes from the `mission.mode` milestone, never from the interface: it is what the mission resolved. Without one the full repertoire is spoken."

- [ ] **Step 5: Frases de bateria por modo** — `src/lib/batteryReturn.ts`:

```ts
import type { MissionMode } from './missionMode';

const ADVANCED_CALLS = {
  alreadyReturning: 'Bateria crítica. Retorno à base já em curso.',
  returning: 'Bateria crítica. Retornando à base para pouso.',
  landing: 'Bateria crítica. Pouso de emergência iniciado.',
} as const;

const INTERMEDIATE_CALLS = {
  alreadyReturning: 'Bateria crítica. Pouso já em curso.',
  returning: 'Bateria crítica. Pouso no local iniciado.',
  landing: 'Bateria crítica. Pouso de emergência iniciado.',
} as const;

const SIMPLE_CALLS = {
  alreadyReturning: 'Bateria crítica. Pouso na base já em curso.',
  returning: 'Bateria crítica. Pouso na base iniciado.',
  landing: 'Bateria crítica. Pouso de emergência iniciado.',
} as const;

export type BatteryCalls = typeof ADVANCED_CALLS | typeof INTERMEDIATE_CALLS | typeof SIMPLE_CALLS;

/** What the station says on a critical charge, by the profile being flown and the path actually taken. */
export function batteryCalls(mode: MissionMode): BatteryCalls {
  return mode === 'simple' ? SIMPLE_CALLS : mode === 'intermediate' ? INTERMEDIATE_CALLS : ADVANCED_CALLS;
}

export const BATTERY_CALLS = ADVANCED_CALLS;
```

(O teste "keeps the advanced wording" confere `toEqual`; o tipo literal de `BATTERY_CALLS` muda de `{...} as const` para a mesma forma, sem mudança de valor.) `runCriticalBatteryReturn(action, hooks, mode = 'advanced')` usa `const calls = batteryCalls(mode)` no lugar de `BATTERY_CALLS`. Em `App.tsx`, o ponto que chama `runCriticalBatteryReturn` (próximo à linha 496) passa `launchedMode ?? readMissionMode(params.working ?? params.committed)`. A regra `failsafeAction` não muda (decisão 12).

- [ ] **Step 6: Rodar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/copilotPhrases.test.ts src/lib/batteryReturn.test.ts src/lib/batteryFailsafe.test.ts src/hooks/useFlightNarration.test.ts src/hooks/useFlightNarration.parameters.test.tsx src/lib/milestoneStream.test.ts src/lib/narrationQueue.test.ts 2>&1 | tail -25 && npm run typecheck 2>&1 | tail -8
```

Expected: PASS. Os testes antigos de pool (6 variantes por pool, histórico, `FIXED_POOLS`) continuam valendo para os pools novos, que têm 6 variantes.

- [ ] **Step 7: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): speak a copilot repertoire that matches the mission mode`.

---

## Task 7: Atalho Ctrl/Cmd+C na Cabine

**Files:**
- Modify: `bebop_mission_control/src/components/cockpit/CockpitScreen.tsx`
- Test: `bebop_mission_control/src/components/cockpit/CockpitScreen.test.tsx`
- Create: `bebop_mission_control/src/lib/abortShortcut.ts`, `bebop_mission_control/src/lib/abortShortcut.test.ts`

**Interfaces:**
- Produces: `isAbortShortcut(event: Pick<KeyboardEvent, 'key' | 'code' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey' | 'repeat'>): 'escape' | 'copy' | null`, em `src/lib/abortShortcut.ts`.

- [ ] **Step 1: Testes puros que falham** — `src/lib/abortShortcut.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { isAbortShortcut } from './abortShortcut';

const key = (over: Partial<Parameters<typeof isAbortShortcut>[0]>) =>
  isAbortShortcut({ key: '', code: '', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, repeat: false, ...over });

describe('isAbortShortcut', () => {
  it('recognises Escape', () => {
    expect(key({ key: 'Escape', code: 'Escape' })).toBe('escape');
  });

  it('recognises Ctrl+C and Cmd+C', () => {
    expect(key({ key: 'c', code: 'KeyC', ctrlKey: true })).toBe('copy');
    expect(key({ key: 'c', code: 'KeyC', metaKey: true })).toBe('copy');
  });

  it('reads the physical key, so a non-QWERTY layout still aborts', () => {
    expect(key({ key: 'ç', code: 'KeyC', ctrlKey: true })).toBe('copy');
    expect(key({ key: 'С', code: 'KeyC', ctrlKey: true })).toBe('copy');
  });

  it('ignores a plain C, and C with Shift or Alt (inspector, accents)', () => {
    expect(key({ key: 'c', code: 'KeyC' })).toBeNull();
    expect(key({ key: 'C', code: 'KeyC', ctrlKey: true, shiftKey: true })).toBeNull();
    expect(key({ key: 'c', code: 'KeyC', ctrlKey: true, altKey: true })).toBeNull();
  });

  it('ignores a held key', () => {
    expect(key({ key: 'c', code: 'KeyC', ctrlKey: true, repeat: true })).toBeNull();
    expect(key({ key: 'Escape', code: 'Escape', repeat: true })).toBeNull();
  });

  it('ignores other Ctrl combinations', () => {
    expect(key({ key: 'v', code: 'KeyV', ctrlKey: true })).toBeNull();
    expect(key({ key: 'x', code: 'KeyX', ctrlKey: true })).toBeNull();
  });
});
```

Nota: o Escape existente não ignorava `repeat`. Ignorá-lo é intencional (mesma proteção contra múltiplos pousos). Se algum teste atual de Escape dispara o evento com `repeat: true`, ajustá-lo (improvável).

- [ ] **Step 2: Testes de comportamento da Cabine que falham** — em `CockpitScreen.test.tsx`, seguindo o padrão de renderização e das props mínimas do arquivo (ele já testa o atalho Escape; copiar esse teste como molde), acrescentar:

```tsx
describe('Ctrl+C abort shortcut', () => {
  const press = (init: KeyboardEventInit & { target?: Element }) => {
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    (init.target ?? document.body).dispatchEvent(event);
    return event;
  };

  it.each(['running', 'arming'] as const)('aborts with Ctrl+C while the mission is %s, exactly like the button', (state) => {
    const onAbort = renderCockpit({ missionState: state });
    const event = press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it('aborts with Cmd+C as well', () => {
    const onAbort = renderCockpit({ missionState: 'running' });
    press({ key: 'c', code: 'KeyC', metaKey: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('does nothing, and leaves copy alone, when the abort is not available', () => {
    const onAbort = renderCockpit({ missionState: 'idle', flyingState: 0 });
    const event = press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it('leaves Ctrl+C to a text field', () => {
    const onAbort = renderCockpit({ missionState: 'running' });
    const input = document.createElement('input');
    document.body.appendChild(input);
    press({ key: 'c', code: 'KeyC', ctrlKey: true, target: input });
    expect(onAbort).not.toHaveBeenCalled();
    input.remove();
  });

  it('fires once for a held key', () => {
    const onAbort = renderCockpit({ missionState: 'running' });
    press({ key: 'c', code: 'KeyC', ctrlKey: true });
    press({ key: 'c', code: 'KeyC', ctrlKey: true, repeat: true });
    press({ key: 'c', code: 'KeyC', ctrlKey: true, repeat: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('does not capture Ctrl+Shift+C', () => {
    const onAbort = renderCockpit({ missionState: 'running' });
    press({ key: 'C', code: 'KeyC', ctrlKey: true, shiftKey: true });
    expect(onAbort).not.toHaveBeenCalled();
  });

  it('stops listening when the cockpit unmounts', () => {
    const onAbort = renderCockpit({ missionState: 'running' });
    unmountCockpit();
    press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).not.toHaveBeenCalled();
  });

  it.each(['simple', 'intermediate', 'advanced'] as const)('aborts the same way in the %s mode', (mode) => {
    const onAbort = renderCockpit({ missionState: 'running', missionMode: mode });
    press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });
});
```

(`renderCockpit(overrides)` devolve o `vi.fn()` passado como `onAbort`, e `unmountCockpit()` desmonta a raiz; ambos seguem o harness que o arquivo já usa para o teste do Escape; se o harness não tiver uma prop de modo, o último teste só confirma que o atalho não depende dela: remover o argumento `missionMode` e manter a repetição com o modo dentro do documento, se o componente o receber.)

- [ ] **Step 3: Rodar e ver falhar** (`npx vitest run src/lib/abortShortcut.test.ts src/components/cockpit/CockpitScreen.test.tsx`).

- [ ] **Step 4: Implementar** — `src/lib/abortShortcut.ts`:

```ts
/**
 * Keyboard shortcuts that abort the mission from the cockpit.
 *
 * Escape (R11) and Ctrl/Cmd+C. The physical key (`code`) is read for C so a
 * layout that types another character on that key still aborts. A held key
 * never fires twice, and Ctrl+Shift+C (the inspector) and Alt combinations are
 * not captured.
 */
export type AbortShortcut = 'escape' | 'copy';

export function isAbortShortcut(
  event: Pick<KeyboardEvent, 'key' | 'code' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey' | 'repeat'>
): AbortShortcut | null {
  if (event.repeat) return null;
  if (event.key === 'Escape') return 'escape';
  if (event.code === 'KeyC' && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey) {
    return 'copy';
  }
  return null;
}
```

`CockpitScreen.tsx`: importar `isAbortShortcut` e reescrever o efeito do atalho (o comentário R11 passa a citar os dois atalhos):

```tsx
  // R11: Escape and Ctrl/Cmd+C fire onAbort exactly as the button does, while
  // the abort is available. Ignored inside text fields and the diagnostics
  // terminal, where Ctrl+C is copy / interrupt. When the abort is not
  // available the event is left alone, so copy still works on the ground.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isAbortShortcut(event) === null) return;
      if (isTextInputOrTerminal(event.target)) return;
      if (abortEnabled(missionState, telemetry.flying_state, landProgress)) {
        event.preventDefault();
        onAbort();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [missionState, telemetry.flying_state, landProgress, onAbort]);
```

`onAbort` já é `() => void land()` em `App.tsx` (comanda `mission.abort()` e o pouso, o mesmo do botão); não há caminho novo de aborto.

- [ ] **Step 5: Verificar o menu do Electron** — a aplicação usa o menu padrão (`autoHideMenuBar: true`, sem `Menu.setApplicationMenu`), cujo item Editar > Copiar registra Ctrl+C. Na Linux/Windows o evento chega à página antes do menu, e `preventDefault()` no `keydown` cancela o acelerador; confirmar no Step 6 (verificação manual) que a Cabine recebe o evento. Se a verificação falhar (o menu consome o Ctrl+C antes da página), tratar no processo principal: em `electron/main.cjs`, no `webContents.on('before-input-event', (event, input) => ...)` da janela principal, para `input.type === 'keyDown' && input.control && input.code === 'KeyC' && !input.shift && !input.alt`, enviar `webContents.send('bmg:abort-shortcut')` e `event.preventDefault()` somente quando o renderer tiver reportado a Cabine ativa; o preload expõe `onAbortShortcut`, e a Cabine assina junto do listener acima. Só implementar esse desvio se a verificação do Step 6 mostrar que ele é necessário, e então com teste no `mainHarness`.

- [ ] **Step 6: Rodar e verificar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/abortShortcut.test.ts src/components/cockpit src/lib/abortAvailability.test.ts 2>&1 | tail -20 && npm run typecheck 2>&1 | tail -8
```

Verificação manual (Task 9, Step 5): em bancada, com o BMG aberto e a missão em contagem ou em voo simulado, pressionar Ctrl+C na Cabine com o foco fora de campos de texto e confirmar o aborto e o pouso simulado; no pré-voo (sem missão), selecionar texto e copiar com Ctrl+C continua funcionando.

- [ ] **Step 7: Checkpoint (sem executar)** Mensagem proposta: `feat(bmg): abort the mission with Ctrl+C from the cockpit`.

---

## Task 8: Paridade de operação entre os modos no frontend

**Files:**
- Create: `bebop_mission_control/src/lib/modeParity.test.ts`, `bebop_mission_control/src/lib/modeIsolation.guard.test.ts`

Objetivo: travar em teste que bloqueio de pré-voo, aborto, finalização, limpeza de ciclo e bateria não dependem do modo, e que o modo só é lido nos pontos autorizados.

- [ ] **Step 1: Teste de paridade** — `src/lib/modeParity.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { abortEnabled } from './flightState';
import { failsafeAction } from './batteryFailsafe';
import { preflightLocked } from './navigationLock';
import { computeFinishLock } from './finishLock'; // conferir o nome real da função exportada por finishLock.ts
import type { MissionState } from '../types/mission';

const STATES: MissionState[] = ['idle', 'arming', 'running', 'aborting', 'finished', 'finished_unconfirmed', 'aborted', 'faulted'];

describe('operation rules do not depend on the mission mode', () => {
  it('declares no mode in the signature of the lock, abort, finish and failsafe rules', () => {
    // The functions take the mission state, the flying state and the stage: nothing else.
    expect(preflightLocked.length).toBe(2);
    expect(abortEnabled.length).toBeLessThanOrEqual(3);
    expect(failsafeAction.length).toBe(2);
  });

  it.each(STATES)('locks the pre-flight tab the same way in state %s', (state) => {
    for (const ran of [false, true]) {
      expect(preflightLocked(state, ran)).toBe(preflightLocked(state, ran));
    }
  });

  it('asks for the stage 5 jump from stages 2 to 4 and lands in place from stage 1, for every mode', () => {
    for (const stage of [2, 3, 4]) expect(failsafeAction(stage, true)).toBe('rtl');
    expect(failsafeAction(5, true)).toBe('none');
    expect(failsafeAction(1, true)).toBe('land');
    expect(failsafeAction(3, false)).toBe('land');
  });

  it('keeps the abort available while flying and unavailable on the ground', () => {
    expect(abortEnabled('running', 2)).toBe(true);
    expect(abortEnabled('arming', null)).toBe(true);
    expect(abortEnabled('idle', 0)).toBe(false);
  });
});
```

(Conferir os nomes e assinaturas reais em `flightState.ts`, `finishLock.ts` e `batteryFailsafe.ts`; se `computeFinishLock` tiver outro nome, usar o real, ou remover a importação não usada. O teste `locks the pre-flight tab...` compara a função com ela mesma por desenho: documenta que o resultado vem só do estado; a trava real contra regressão é o teste de guarda abaixo.)

- [ ] **Step 2: Guarda de isolamento** — `src/lib/modeIsolation.guard.test.ts`:

```ts
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '..');

/** The only modules that may read the mission mode of a document. */
const ALLOWED = new Set([
  'lib/missionMode.ts',
  'lib/parameterSchema.ts',
  'lib/launchDocument.ts',
  'lib/parameterConflicts.ts',
  'lib/parameterModes.ts',
  'lib/copilotPhrases.ts',
  'lib/batteryReturn.ts',
  'hooks/useMissionParameters.ts',
  'hooks/useModeEcho.ts',
  'hooks/useCopilot.ts',
  'components/preflight/ParameterSheet.tsx',
  'components/preflight/PreflightScreen.tsx',
  'components/cockpit/StageBar.tsx',
  'App.tsx',
]);

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

describe('the mission mode is read only where the profile matters', () => {
  it('keeps the lock, abort, finish and failsafe rules free of the mode', () => {
    const offenders = walk(SRC)
      .map((file) => relative(SRC, file))
      .filter((file) => !ALLOWED.has(file))
      .filter((file) => /readMissionMode|parseMissionMode|mission_mode|MissionMode\b/.test(readFileSync(join(SRC, file), 'utf-8')));
    expect(offenders).toEqual([]);
  });
});
```

Se o teste listar arquivos legítimos criados por este plano ou pelo anterior (ex.: `types/bmg.ts`), decidir caso a caso: tipos puros entram na lista; qualquer regra de operação (`navigationLock.ts`, `finishLock.ts`, `flightState.ts`, `batteryFailsafe.ts`, `useFinishLock.ts`, `useLandProgress.ts`) nunca entra.

- [ ] **Step 3: Paridade de backend** — já coberta em `test/test_mode_parity.py` (Task 3). Acrescentar a esse arquivo o teste do Step 5 intermediário/simples com o aborto no `Step 5`:

```python
@pytest.mark.parametrize("mode", [MissionMode.INTERMEDIATE, MissionMode.SIMPLE])
def test_an_abort_during_the_final_stage_still_commands_the_landing(mode):
    from test_recovery_and_touchdown import LandingDrone, landing_ctx
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone()
    ctx = landing_ctx(drone)
    ctx.params.mission_mode = mode.value
    ctx.emergency_event.set()

    ClosedLoopRTLStep(mode=mode).execute(ctx)

    assert drone.land_calls >= 1
```

(Usar os nomes reais do `LandingDrone`, como na Task 8 do plano anterior.)

- [ ] **Step 4: Rodar**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npx vitest run src/lib/modeParity.test.ts src/lib/modeIsolation.guard.test.ts src/lib/finishLock.test.ts src/hooks/useFinishLock.test.tsx src/hooks/navigationLock.test.tsx 2>&1 | tail -15
cd .. && source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mode_parity.py -q 2>&1 | tail -8
```

Expected: PASS.

- [ ] **Step 5: Checkpoint (sem executar)** Mensagem proposta: `test(bmg): lock operation rules to be independent of the mission mode`.

---

## Task 9: Validação completa e relatório

**Files:** nenhum arquivo de produto.

- [ ] **Step 1: Suítes**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py -q 2>&1 | tail -6
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/ -q --ignore=test/test_fly_path_emulated.py --ignore=test/test_fly_path_totem.py --ignore=test/test_mission_modes_emulated.py 2>&1 | tail -15
cd bebop_mission_control && npm test 2>&1 | tail -20 && npm run typecheck 2>&1 | tail -8 && npm run build 2>&1 | tail -8
```

Expected: 100% verde. Falha de temporização: reexecutar isolada; persistindo, comparar com `.superpowers/baseline-consistency/`.

- [ ] **Step 2: Voos emulados, um lote por vez**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop
pgrep -fa "fake_bebop|test_fly_path" || echo "no emulated batch running"
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_mission_modes_emulated.py -v 2>&1 | tail -25
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_fly_path_emulated.py test/test_fly_path_totem.py -q 2>&1 | tail -10
```

Acrescentar ao `test_mission_modes_emulated.py` (parametrizado nos 3 modos) o teste da decolagem nativa:

```python
def test_the_simple_profile_takes_off_natively_without_the_corrective_ascent(run):
    mode, result = run
    text = "\n".join(line.text for line in result.lines)
    if mode == "simple":
        assert "Simple profile: native firmware takeoff" in text
        assert "Closing a" not in text and "altitude deficit" not in text
    else:
        assert "Simple profile: native firmware takeoff" not in text
```

- [ ] **Step 3: Ciclo `--no-fly` por modo, com as falas** (CLI, sem hardware; um modo por vez):

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/mvp_mission_bebop
source /home/jv/ros2_ws/bin/nectar-activate
for MODE in simple intermediate advanced; do
  echo "=== $MODE ==="
  python3 -m mvp_mission_bebop.mission --no-fly --mode "$MODE" --countdown 3 \
    --params-json '{"kinematics":{"target_altitude_m":1.0,"hover_duration_sec":1.0},"timeouts":{"search_timeout_sec":10,"tracking_timeout_sec":6},"rtl":{"timeout_sec":12,"centering_timeout_sec":6,"touchdown_timeout_sec":8}}' \
    2>&1 | grep -E "STEP [1-5]:|MILESTONE mission\.(mode|takeoff|scan_start|approaching|landing|touchdown|rtl_start)|native firmware takeoff|Closing a|Yaw scan|Intermediate profile|Simple profile|Traceback|ERROR" | head -40
  echo "exit: ${PIPESTATUS[0]}"
done
```

Expected por modo: cinco marcadores `[STEP N:`; um `MILESTONE mission.mode` com o modo; Simples com `native firmware takeoff`, `mission.takeoff` com `"native": true`, `Yaw scan engaged`, sem `Closing a` e sem `mission.rtl_start`; Intermediária com `Intermediate profile: no return leg` e `mission.landing` com `"at_base": false`, sem `mission.rtl_start`; Avançada com `mission.rtl_start`. Altura acima da nativa no Simples deve abortar na partida:

```bash
python3 -m mvp_mission_bebop.mission --no-fly --mode simple --params-json '{"kinematics":{"target_altitude_m":1.4}}' 2>&1 | tail -3
```

Expected: `SystemExit` com `mission_mode: kinematics.target_altitude_m=1.4 exceeds the native takeoff height`.

- [ ] **Step 4: Falas de cada modo na estação** — com o BMG em bancada (ferramenta de browser/Electron do projeto; `npm run` do dev server conforme `package.json`): para cada modo, iniciar uma missão `--no-fly` e anotar, do log da estação, as frases enfileiradas pela fila de narração. Critério: Simples fala decolagem nativa, varredura por rotação, centralização angular e pouso na base, e nenhuma frase de velocidade, cruzeiro ou retorno; Intermediária fala o pouso no local ao fim do estágio 4 e nenhuma de retorno; Avançada mantém o repertório completo. Registrar as listas no relatório.

- [ ] **Step 5: Verificação interativa no BMG (bancada)**
  1. Trocar entre os três modos no pré-voo, editar a altura e salvar um favorito em cada um; trocar de volta e confirmar que cada modo restaura os valores e o favorito próprios, e que o Simples mostra a altura limitada a 1,0 m e não mostra "Margem do Teto de Altitude".
  2. Reiniciar o BMG e confirmar que o último modo e os valores usados voltam.
  3. Iniciar uma missão em cada modo e pressionar Ctrl+C na Cabine (foco fora de campos de texto): a missão aborta e o pouso simulado é comandado, como no botão "ABORTAR MISSÃO". Repetir com Cmd+C se houver teclado Mac.
  4. No pré-voo, selecionar um texto e copiar com Ctrl+C: o clipboard recebe o texto e nenhum aborto é disparado.
  5. Confirmar que o seletor trava ao clicar em INICIAR e que "Finalizar Missão" só libera com a aeronave no solo e permite novo início imediato (`endCycle`), nos três modos.
  6. Forçar a divergência: com o BMG parado, editar `mission_config.json` para `"mission_mode": "expert"` e tentar lançar: o lançamento é recusado com a mensagem de modo inválido e nada é spawnado.
  Registrar capturas de tela.

- [ ] **Step 6: Relatório final ao usuário** com: tabela A1 a A9 com o resultado de cada item (corrigido, já estava correto, ou limitação), arquivos alterados por tarefa, resultado de cada suíte (contagem e tempo), as linhas dos ciclos `--no-fly` e das falas por modo, divergências da linha de base, as decisões 4, 5 e 11 com o que foi medido, e o risco aberto de que o sinal do yaw e a leitura de `horizontal_speed` durante o giro só se confirmam em hardware. Perguntar ao usuário se autoriza os commits propostos (lista dos checkpoints, na ordem das tarefas) e o `git push origin main`.

---

## Self-Review (feita pelo arquiteto)

**1. Cobertura do pedido**

| Pedido | Tarefa |
|---|---|
| 1. Modo repassado sem ambiguidade (Electron, params-json, CLI); backend executa o modo; sem fallback silencioso | 1 (A1, A2) |
| 1. Simples: decolagem pela rotina nativa, sem `_ascend`, estabiliza na cota | 2 |
| 1. Intermediário sem RTL e pouso no local; Simples só yaw, centralização ArUco | já implementados no plano anterior; provados pelo marco `mission.mode` e pelos emulados | 1, 9 |
| 2. Marcos `[STEP 1..5]` nos 3 modos, StageBar sincronizada | A4: coberto; teste emulado | 9 |
| 2. Falas coerentes por modo (Intermediária sem retorno; Simples sem velocidade/cruzeiro; Avançada completa); fila filtra | 6 |
| 3. Bloqueio de pré-voo, Abortar, Finalizar/endCycle, supervisores e failsafes iguais nos 3 modos | 3 (saúde no retorno do yaw), 8 (paridade e guarda), 9 (verificação interativa) |
| 4. Parâmetros de trabalho e favorito isolados por modo, com chaves `bmg.working-parameters.<modo>` e `bmg.operator-preset.<modo>`; restauração na troca | 5 |
| 4. Filtro por modo; Simples sem "Margem do Teto" | 4 (mais o filtro do plano anterior) |
| 5. Ctrl+C na Cabine com a mesma ação do botão | 7 |
| 6. Testes Vitest (Ctrl+C, isolamento, margem no Simples, frases por modo) e pytest completo; bancada dos 3 modos | 4, 5, 6, 7, 9 |
| Relatório técnico final | 9, Step 6 |

Divergências do texto literal do pedido, todas nas Decisões: o Electron não passa `--mode` (o standby o anularia); o hook de narração é `useFlightNarration` dentro de `useCopilot.ts` (não existe `useFlightNarration.ts`); o Simples mantém "Altitude de Voo", limitada a 1,0 m, por decisão do usuário.

**2. Placeholders:** quatro pontos dependem de nomes que só o código vivo confirma: `computeFinishLock` e as assinaturas reais em `modeParity.test.ts`, o harness `mountReady`/`renderCockpit` dos testes de hook e de Cabine, `HISTORY_WINDOW` exportada, e o `harness()` do teste de narração. Cada um traz o arquivo onde o padrão já existe, o que fazer se o nome diferir e a asserção obrigatória. Não há "TBD" nem passo sem conteúdo.

**3. Consistência de tipos:** `parseMissionMode` (Task 1) é usado nas Tasks 5, 6 e 8; `normalizeForMode` (Task 4) é usado por `switchModeDocument` (Task 5); `MissionMode` (TS e Python) tem os mesmos três valores; `NATIVE_HOVER_HEIGHT_M`, `SIMPLE_CEILING_MARGIN_M` (Task 2) têm espelho `SIMPLE_ALTITUDE_RANGE_M.max` e a margem de fábrica lida de `factory` (Task 4), ambos presos por teste; o marco `mission.mode` com `{"mode": ...}` é emitido (Task 1) e consumido por `useModeEcho` (Task 1) e `useFlightNarration` (Task 6); `poolForMilestone(key, payload, mode)` e `phraseForMilestone(key, payload, parameters, options, mode)` têm o modo como último argumento, sem quebrar chamadores.

**4. Review Focus:** itens 1 (Task 1), 2 (Task 2), 3 (Task 5), 4 (Tasks 1 e 6) e 5 (Task 7) têm teste dono.
