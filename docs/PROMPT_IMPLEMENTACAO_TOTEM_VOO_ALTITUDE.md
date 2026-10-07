# Prompt de Implementacao - Voo a 1,8 m sobre o piso com decolagem e pouso em totem de 1 m

> Reformulado em 2026-10-07 por decisao do usuario. **Escopo: somente o voo.** O RTL (busca reversa,
> centralizacao ArUco e `_touchdown`) permanece como esta implementado. O protocolo de RTL em dois
> estagios foi retirado. Nenhum arquivo de codigo foi alterado na auditoria; este documento e a unica
> saida.

Voce vai trabalhar no repositorio MVP-Bebop em `/home/jv/ros2_ws/src/mvp_mission_bebop/` (raiz do pacote
ROS 2; os dois `CLAUDE.md` valem integralmente: tipagem estrita, docstrings NumPy/reST, tolerancia zero a
emojis e a comentarios obvios, Conventional Commits em ingles tecnico). O nectar-sdk esta em
`/home/jv/ros2_ws/src/nectar-sdk` e o driver em `/home/jv/ros2_ws/src/ros2_bebop_driver`. Ambos sao
somente leitura: o comportamento do firmware e contornado dentro de `mvp_mission_bebop`.

## 0. Regras de execucao

1. **Check-in.** `git status`. A arvore ja contem alteracoes nao commitadas em `bebop_mission_control/`
   que nao sao deste trabalho: nao toque nelas. So rode `git pull --ff-only origin main` com a arvore
   limpa; senao, nao puxe e avise. Registre a linha de base:
   `python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py` e `python3 -m pytest test/ -q`.
2. **Sem commit e sem push** sem autorizacao explicita do usuario neste pedido (regra de memoria do
   usuario; prevalece sobre o check-out do CLAUDE.md). Deixe tudo na arvore, liste os arquivos e proponha
   os commits da secao 8. Espere aprovacao.
3. Releia o equivalente no nectar-sdk antes de cada modulo. Os modulos novos nao tem equivalente no SDK;
   o padrao interno de referencia e `controllers/altitude_hold.py` + `estimation/detection_filter.py`.
4. **Contrato com o GCS imutavel:** marcadores `[STEP N: ...]` inalterados; `MILESTONE_KEYS`
   (`telemetry/milestones.py`) e um registro fechado ligado a `copilotPhrases.ts`: **nao crie chaves
   novas**. Eventos novos vao como linhas de log (`[PLATFORM ...]`, `[EDGE_GUARD ...]`, `[ALT_PROBE ...]`).
   `mission_config.json` so ganha campos novos com default; nada existente muda de nome ou tipo.
5. Em todo codigo novo: `vyaw == 0.0`; `vx <= 0` na busca reversa preservado; nada de relogio de parede em
   `controllers/` e `estimation/`; nada de acesso a nos ROS fora de `telemetry/` e `mission.py`.
6. `steps/rtl.py` nao deve mudar de comportamento. As unicas edicoes permitidas nele estao marcadas P1 na
   secao 2.9 e sao opcionais.

Convencao de evidencia (a mesma de `docs/superpowers/specs/2026-09-24-altitude-yaw-hover-hardening-design.md`):
**[FATO]** lido na fonte citada; **[SUPOSICAO]** inferido do comportamento do firmware, nao observado neste
repositorio; **[A CONFIRMAR]** exige ensaio de campo. A base T4H - Knowledge foi consultada e nao cobre este
projeto; as fontes sao locais (codigo, driver, e o XML do ARSDK em
`/home/jv/ros2_ws/build/ros2_parrot_arsdk/parrot_arsdk/packages/arsdk-xml/xml/ardrone3.xml`).

---

## 1. Requisito e relatorio tecnico

### 1.1 Requisito de projeto (prevalece sobre qualquer outro numero)

O drone decola do topo de um totem de **1,0 m** e deve voar sempre a **1,8 m sobre o PISO**, isto e,
**0,8 m sobre o topo do totem**, sem perder altura ao sair do totem nem ao voltar a ele para pousar. A
decolagem sobe so ~0,8 m sobre o totem (o teto do local nao admite 2,8 m sobre o piso).

### 1.2 O que `relative_altitude` e (fatos)

- **[FATO]** `/bebop/odom.pose.position.z` nao e sensor: e a integral de velocidade vertical,
  `current.z += vz * dt` (`ros2_bebop_driver/src/telemetry_state.cpp:37-51`, `dt` limitado a 0.5 s),
  alimentada por `ARDrone3PilotingStateSpeedChanged` (`bebop_driver_node.cpp`, `publishOdometry`,
  ~473-519), publicada so quando chega amostra nova (~5 Hz). A origem de `z` e o instante em que o driver
  subiu.
- **[FATO]** `z0` e calibrado em solo antes do takeoff (`steps/takeoff.py:229-246`,
  `telemetry/odometry.py:388-454`) e `relative_altitude = z - z0` (`odometry.py:352-371`). Com o drone no
  totem, **a referencia de controle ja e o topo do totem**. O governor (`controllers/altitude_hold.py`),
  o teto (`odometry.py:375-386`) e o touchdown (`touchdown_altitude_m`) trabalham nesse referencial e
  nao precisam saber que o totem existe, **exceto pelo requisito 1.1 e pelo item 1.3(3) abaixo**.
- **[FATO]** `AltitudePlausibilityFilter` (`estimation/altitude_plausibility.py:82-122`, aplicado em
  `odometry.py:269-281`) limita a **posicao** `z`. Como `z` e uma integral, um spike de `vz` deixa um
  degrau permanente; o filtro segura 2 amostras e adota o degrau na 3a (`reject_streak = 3`). Os testes
  atuais (`test_odometry_plausibility.py`) injetam um spike absoluto que o driver real nao produz. Alem
  disso o filtro mede `dt` pela chegada do callback (`time.monotonic()`), nao pelo `header.stamp`, e
  `twist.linear.z` passa sem filtro (`odometry.py:283-291`).
- **[FATO]** `/bebop/states/altitude` (`AltitudeChanged`) e definido pelo ARSDK como "the altitude above
  the take off point" (`ardrone3.xml:876-887`): ja e referenciado ao totem. O driver o publica; nenhum
  codigo de `mvp_mission_bebop` o assina (so `bebop_mission_control/streamer/telemetry_bridge.py`).
  **[A CONFIRMAR]** se o firmware o calcula com sonar (contaminado pelo degrau do totem) ou so com
  barometro/inercial.

### 1.3 Consequencias do requisito 1.1 no codigo atual

1. **`target_altitude_m` precisa significar "altura sobre o piso".** Com `platform.height_m = 1.0`, o alvo
   no referencial de controle e `target_altitude_m - platform.height_m = 0.8`. O operador continua
   digitando 1.8 e o copiloto continua falando "1,8 metros" (`telemetry/mission_parameters.py`; nao mudar).
   Com `height_m = 0.0` (default) tudo e identico ao atual.
   Usos mapeados de `target_altitude_m` (classificar cada um; `grep -rn "target_altitude" mvp_mission_bebop/`
   antes de fechar):
   - **controle (usa a relativa):** `steps/takeoff.py:347` (argumento de `drone.takeoff`),
     `steps/takeoff.py:573` (`_ascend`), `telemetry/odometry.py:384` (`altitude_ceiling`),
     `mission.py:1034` e `1040` (simulador e governor), `config_audit.py:130-138` (piso do envelope);
   - **semantico/AGL (usa a absoluta):** `steps/takeoff.py:81` (payload do milestone), `mission.py:408`
     (`--height`), `mission.py:465` (log), `telemetry/mission_parameters.py`.
2. **Decolagem [FATO + SUPOSICAO].** O firmware encerra o launch profile a ~1,0 m do ponto de decolagem,
   ignorando o argumento (`actuators/simulator.py`, `FIRMWARE_HOVER_ALTITUDE_M`, derivada de observacao em
   voo). Com alvo relativo 0.8 o drone chega ~0.2 m ACIMA; `_ascend` ja pula quando
   `altitude >= target - deadband` e o governor desce (descida e a direcao livre, 0.2 m/s). O teto
   relativo e 0.8 + 0.6 = 1.4 m (2.4 m sobre o piso).
3. **IBVS e projecao ao solo tratam `relative_altitude` como altura sobre o plano do alvo (o piso).**
   **[FATO]** `steps/tracking.py:245`, `controllers/visual_servoing.py` (`self._altitude_m`, `_project`,
   `min_altitude_for_ibvs_m`, `nadir_range_threshold_m`) e `steps/search.py:350` (`project_to_ground`).
   Com `z0` no totem veriam 0.8 m quando a altura real e 1.8 m: distancia ao solo subestimada 2.25x e
   Stage 3 incorreto. **Corrigir (P0):** `OdometrySnapshot` ganha `floor_offset_m: float = 0.0`
   (= `platform.height_m`, preenchido em `snapshot()`) e a propriedade
   `height_above_floor_m = relative_altitude + floor_offset_m`; passe-a ao IBVS (`tracking.py:245`), a
   `project_to_ground` (`search.py:350`) e ao payload de `context.py:565` (campo adicional; manter
   `relative_altitude_m`). Governor, teto, touchdown e RTL continuam em `relative_altitude`.
4. **Teto do local.** `platform.venue_ceiling_agl_m: Optional[float] = None` (sobre o piso; **o usuario
   informa**). Teto efetivo relativo =
   `min(target_rel + margin + allowance_ativo, venue_ceiling_agl_m - height_m)`; a auditoria avisa se
   `target + margin >= venue_ceiling_agl_m - 0.2`.

### 1.4 Auditoria de autoridade vertical por step (o que ja mantem altitude e o que nao)

| Step | Caminho vertical atual | Situacao |
|---|---|---|
| 1 Takeoff | `_stabilize`/`_ascend` (climb_window, perfil proprio, `takeoff.py:642-704`); `_finalize` congela a origem horizontal (`freeze_hover_takeoff_origin`, `takeoff.py:772+`) | Sem governor durante `_stabilize` (firmware em hover, sobre o totem: coerente). Nao ha portao de "altitude assentada no alvo" antes de translacionar |
| 2 Search | `_command` chama `governor.compute_vz` com `vx` e `clamp_kinematics` dentro de `altitude_hold_window` (`search.py:98-111`, `302-307`) | OK; e aqui que o drone **sai do totem** |
| 3 Tracking | `_apply`/`_hold` com governor (`tracking.py:363-377`) | OK |
| 4 Inspection | `_hold` com governor (`inspection.py:404-414`) | OK |
| 5 RTL | `_cruise_backward`, `_transmit_centering`, `_emit` com governor (`rtl.py:1019`, `1175`, `1662`); `_halt_translation` envia `vz = 0.0` literal (`rtl.py:1189`) e `_assert_land` zera o Twist antes de `land()` (`rtl.py:1646`) | OK na busca/centralizacao; e aqui que o drone **reentra no totem**. `_halt_translation` solta o eixo vertical por alguns ciclos |
| Entre steps | `runner` nao envia nada; o Twist anterior fica travado no driver | Aceitavel (ciclo curto) |

Ponto estrutural **[FATO]** (`bebop.cpp:196-214`, spec 2026-09-24 secao 3): o `do_hover` do firmware so
engata com roll, pitch, yaw e gaz todos zero. Sempre que o governor devolve `vz == 0` (deadband ou
quantizacao `int8(v*100)`) **e** a translacao e zero, quem segura a altitude e o firmware, com o sonar.

### 1.5 Fisica do degrau do totem [SUPOSICAO; modelo para projeto, validado em F-2]

Modelo de sonar de "eco mais proximo". Seja `E` a distancia do ponto de decolagem a borda do totem ao
longo da trilha de saida, `s` o deslocamento ao longo da trilha a partir do ponto de decolagem, `h` a
altura sobre o topo do totem (0.8) e `a` o meio-angulo efetivo do feixe. O totem permanece dentro do feixe
enquanto `s <= E + h*tan(a)`; ate la a leitura varia continuamente de `h` a `h/cos(a)`; em
`s* = E + h*tan(a)` a leitura salta **+1.0 m** (de 0.8 para 1.8 m). Na volta (reentrada, `s` decrescendo) o
salto e de **-1.0 m** na mesma posicao `s*` (o feixe passa a alcancar a borda do totem). Com `a = 20 graus`,
`s* = E + 0.29 m`.

O que o firmware faz com o degrau de leitura:

| Desfecho | Efeito | Como o codigo atual reage |
|---|---|---|
| **M1 (provavel)** | Saida: acha que subiu 1 m e **desce ate restaurar a altura do sonar** (de 1.8 m para ~0.8 m sobre o piso, 0.2 m abaixo do topo do totem). Reentrada (falso climb): sobe ~1 m | O filtro de plausibilidade passa (movimento real); governor corrige a <= 0.25 m/s (subida) e 0.2 m/s (descida); `horizontal_scale` estrangula o cruzeiro a 0.35. **Na reentrada, 1.8 m relativos > teto 1.4 m: `is_ceiling_breached` dispara `trigger_emergency_land` no ar sobre a borda** (`rtl.py:1671-1683`) |
| **M2** | Spike de `vz` no firmware integrado em `odom.z` | Degrau permanente em `relative_altitude` (defeito do filtro, 1.2): toda a missao seguinte voa com a referencia errada |
| **M3** | Firmware insensivel | Nada |

Nao se sabe qual dos tres ocorre. **A guarda deste documento reduz e limita o efeito de M1/M2; nao o
elimina por construcao.** O criterio de aceite e medido em F-2, nao prometido.

### 1.6 Estrategia (camadas; cada uma e testavel sozinha e entrega inerte ate ser habilitada)

- **L0 Referencia sobre o piso (P0):** 1.3 (itens 1, 3, 4). Sem isto o Stage 3 e incorreto e o teto e errado.
- **L1 Sonda de altitude (P0, so log):** assinar `/bebop/states/altitude` e logar `s`, `z_rel`, `fw_alt`,
  `vz`, estado do totem. E o instrumento que decide todos os [A CONFIRMAR].
- **L2 Reconhecimento de saida e entrada do totem (P0):** `PlatformMonitor` (2.3) infere, a partir da
  posicao ao longo da trilha, se o drone esta SOBRE o totem, SAINDO, FORA ou ENTRANDO, e anuncia as
  transicoes por log. Para o RTL, a posicao vem da odometria; opcionalmente do proprio marcador (2.9, P1).
- **L3 Guarda de borda (P0):** durante SAINDO/ENTRANDO a autoridade vertical sobe, a deadband fecha, a
  translacao para se o erro vertical passar de 15 cm, o teto ganha folga controlada (so na janela, sempre
  limitado pelo teto do local) e um feedforward de saida/entrada, inerte por default, e aplicado a partir
  dos numeros medidos em F-2 (2.4).
- **L4 Assentamento pre-saida (P0):** o Stage 2 nao comeca a translacionar ate o drone estar assentado
  em 0.8 m relativos (2.8).
- **L5 Estimador vertical no dominio de `vz` (P1, por flag, default `legacy`):** substitui o gate de
  posicao defeituoso (2.6).

---

## 2. Especificacao

### 2.1 Configuracao: nova secao `platform` em `MissionParameters`

`parameters.py`: `@dataclass class PlatformConfig` adicionada a `MissionParameters`
(`platform: PlatformConfig = field(default_factory=PlatformConfig)`); `update_from_dict` ignora chaves
desconhecidas e carrega secoes novas sem mudanca. Docstring NumPy/reST com "Safe envelope" por campo.

| Campo | Default | Envelope | Observacao |
|---|---|---|---|
| `height_m` | `0.0` | `>= 0` | altura do totem; 1.0 neste projeto |
| `venue_ceiling_agl_m` | `None` | `> target_altitude_m` | teto do local sobre o piso (**usuario informa**) |
| `edge_ahead_m` | `None` | `> 0` | distancia do ponto de decolagem a borda do totem ao longo da trilha de saida (**usuario mede com trena**); `None` desliga L2/L3 |
| `edge_uncertainty_m` | `0.15` | `>= 0` | folga da odometria horizontal sobre 1 a 3 m |
| `sonar_half_angle_deg` | `20.0` | `(5, 45)` | [A CONFIRMAR]; define `s*` |
| `guard_lead_m` | `0.40` | `>= 0` | a guarda comeca antes de `s* - uncertainty` |
| `guard_extent_m` | `1.0` | `> 0` | a guarda segue ativa alem de `s* + uncertainty` |
| `guard_climb_authority` | `0.20` | `<= 0.30` | normalizado; global e `governor.max_climb_speed = 0.10` |
| `guard_descent_cap` | `0.16` | `<= 0.30` | normalizado; global `governor.max_descent_speed = 0.08` |
| `guard_kp_scale` | `1.5` | `[1, 3]` | multiplica `governor.kp` |
| `guard_deadband_m` | `0.01` | `>= 0` | deadband (ambos os lados) dentro da guarda |
| `guard_stop_error_m` / `guard_resume_error_m` | `0.15` / `0.08` | resume < stop | histerese da parada de translacao |
| `guard_vertical_shaper` | `True` | bool | renderiza `vz` sub-quantico por sigma-delta (evita `gaz == 0` exato) |
| `ceiling_allowance_m` | `0.0` | `>= 0` | folga de teto SO com a guarda ativa; sugerido `0.4` [A CONFIRMAR] |
| `sag_feedforward_mps` | `0.0` | `>= 0` | subida de antecipacao ao SAIR (inerte ate F-2) |
| `rise_feedforward_mps` | `0.0` | `>= 0` | descida de antecipacao ao ENTRAR (inerte ate F-2) |
| `feedforward_sec` | `1.5` | `> 0` | duracao dos dois feedforwards apos a transicao |
| `departure_settle_window_sec` | `1.0` | `> 0` | tempo dentro da deadband para liberar o Stage 2 |
| `departure_settle_timeout_sec` | `8.0` | `> 0` | teto da espera; esgotar nao e falha |

`MissionParameters.relative_target_altitude_m` (propriedade, nao campo):
`kinematics.target_altitude_m - platform.height_m`; levanta `ValueError` se
`< kinematics.takeoff_settle_min_altitude_m`. `config_audit.py` ganha uma checagem por envelope violado da
tabela acima e a do teto do local.

`mission_config.json` (perfil do totem): **manter `kinematics.target_altitude_m = 1.8`**; adicionar a secao
`platform` com `height_m = 1.0` e `edge_ahead_m`, `venue_ceiling_agl_m` com os valores informados pelo
usuario (se ainda desconhecidos: `null` e relatar que L2/L3 e o teto do local estao desligados),
`ceiling_allowance_m = 0.4`; `calibration.altitude_estimator` permanece `"legacy"` ate F-2. Nao altere as
velocidades ja afinadas. Nota: o arquivo atual tem `max_horizontal_speed = 0.05` e
`forward_cruise_velocity = 0.15`; a velocidade real de cruzeiro e o clamp de 0.05, e e ela que define por
quanto tempo a borda do totem atravessa o feixe do sonar (registrar em F-2).

### 2.2 `OdometrySupervisor` (telemetria; ponto unico de integracao)

`telemetry/odometry.py`. Construtor ganha `platform_cfg: Optional[PlatformConfig] = None` como **4o
argumento opcional** (ha ~10 pontos de construcao em testes e em `mission.py:706`; nao quebre nenhum).

- `altitude_ceiling` -> teto efetivo da secao 1.3(4): `target_rel + margin + allowance_ativo`, limitado por
  `venue_ceiling_agl_m - height_m`. `allowance_ativo = platform.ceiling_allowance_m` somente com a guarda
  ativa (le o relatorio do `PlatformMonitor`), senao 0.
- `OdometrySnapshot` (dataclass congelada): campos novos **ao fim, com default**:
  `floor_offset_m: float = 0.0`, `along_track_m: Optional[float] = None`,
  `platform_state: str = "unknown"`, `guard_active: bool = False`, `firmware_altitude_m: Optional[float] = None`;
  propriedade `height_above_floor_m`. Preenchidos em `snapshot()` na mesma aquisicao de lock.
- `along_track_m`: deslocamento ao longo da trilha de saida a partir da origem congelada em
  `freeze_hover_takeoff_origin` = `-ex_body` de `body_frame_launch_error()` (positivo para frente da
  origem). `None` antes da origem existir.
- `PlatformMonitor` e atualizado dentro de `_store_sample` (vale tambem para o simulador de bancada, que
  usa `inject_synthetic_sample`), nao no callback de ROS.
- `altitude_state_callback(msg: Float32)`: ignora NaN (o driver publica NaN sem link); guarda o valor e o
  instante monotonico em `firmware_altitude_m`.
- Sonda: linha `[ALT_PROBE t= s= state= z_rel= z_est= fw_alt= vz= guard=]` a 1 Hz, controlada por
  `calibration.altitude_probe_enabled` (default `True`; so log).
- `mission.py` (~860-865): assinar `/{namespace}/states/altitude` (`Float32`,
  `QoSProfile(depth=1, durability=TRANSIENT_LOCAL)`) apenas com `simulator is None`; passar `params.platform`
  ao construtor (linha 706); construir o governor com `params.relative_target_altitude_m` (linhas 1034/1040).

### 2.3 `estimation/platform_monitor.py` (puro; sem ROS nem relogio)

```python
class PlatformState(str, Enum):
    UNKNOWN = "unknown"   # sem origem congelada ou edge_ahead_m None
    ON = "on"             # sobre o totem, sonar le o totem
    LEAVING = "leaving"   # trilha de saida, dentro da janela da guarda
    OFF = "off"           # fora do totem, sonar le o piso
    ENTERING = "entering" # trilha de retorno, dentro da janela da guarda

@dataclass(frozen=True)
class PlatformReport:
    state: PlatformState
    guard_active: bool
    along_track_m: Optional[float]
    edge_step_m: Optional[float]      # s* (posicao esperada do degrau de sonar)
    direction: int                    # +1 afastando, -1 voltando, 0 desconhecido
    seconds_since_transition: Optional[float]

class PlatformMonitor:
    def __init__(self, cfg: PlatformConfig) -> None
    def update(self, along_track_m: Optional[float], height_above_plane_m: float, dt: float) -> PlatformReport
    def override_along_track(self, along_track_m: float, ttl_sec: float) -> None   # P1: posicao vinda do marcador
    def reset(self) -> None
```

Regras:
- `s* = edge_ahead_m + h * tan(sonar_half_angle)` com `h = max(height_above_plane_m, 0.3)`.
- Janela da guarda: `[s* - edge_uncertainty - guard_lead, s* + edge_uncertainty + guard_extent]`, com
  histerese de 0.05 m nas duas bordas.
- Direcao: sinal de `s_k - s_{k-1}` filtrado por 3 amostras (ignora ruido abaixo de 0.02 m).
- Maquina: `ON` ate entrar na janela afastando -> `LEAVING`; ao passar de `s* + uncertainty` -> `OFF`;
  `OFF` ate entrar na janela voltando -> `ENTERING`; ao ficar `s <= edge_ahead_m - uncertainty` -> `ON`.
  `guard_active = state in {LEAVING, ENTERING}`.
- Cada transicao emite uma linha unica: `[PLATFORM state=LEAVING s=1.12 s_star=1.19 h=0.82 dir=+1]`.
  Entradas nao finitas -> `UNKNOWN`, sem excecao.
- `along_track_m is None` ou `edge_ahead_m is None` -> `UNKNOWN`, `guard_active = False`.

Limitacao honesta a registrar no codigo: o modelo e unidimensional (ao longo da trilha). O Stage 3 pode
deslocar o drone lateralmente; ao voltar, o RTL reverte a trilha, mas o erro lateral acumulado pode
antecipar ou atrasar a borda. Isto e medido em F-3.

### 2.4 Guarda de borda: autoridade vertical e teto

A guarda e aplicada **sem tocar nos steps**, pelos tres pontos por onde todo comando vertical ja passa:

1. **`AltitudeHoldGovernor`** (`controllers/altitude_hold.py`) recebe, no construtor, um
   `guard_source: Optional[Callable[[], PlatformReport]]` e uma `PlatformConfig`; no inicio de cada
   `compute_vz` le o relatorio. Com `guard_active`:
   - limites de saida: `max_climb_speed -> guard_climb_authority`, `max_descent_speed -> guard_descent_cap`;
     `kp -> kp * guard_kp_scale`; `deadband_m` e `climb_deadband_m -> guard_deadband_m`. Tudo passa pelo
     perfil jerk-limited (nunca em degrau) e os `output_limits` novos do `FilteredPID` sao aplicados sem
     resetar o integrador;
   - feedforward: nos `feedforward_sec` seguintes a uma transicao, soma `+sag_feedforward_mps` (LEAVING)
     ou `-rise_feedforward_mps` (ENTERING), convertido por `calibration.to_normalized(..., axis="vertical")`,
     ao demand antes do perfil. Ambos 0.0 por default;
   - `guard_vertical_shaper`: a saida vertical passa por `QuantizedCommandShaper` (o mesmo padrao do `vx`
     no `search.py`) para que uma demanda sub-quantica vire pulsos de +/-0.01 em vez de `gaz == 0` exato;
   - `horizontal_scale()` retorna 0.0 enquanto `|erro vertical| > guard_stop_error_m` e so volta a escala
     normal abaixo de `guard_resume_error_m` (histerese). Todos os steps ja multiplicam a demanda
     horizontal por `horizontal_scale()` antes de perfilar (`search.py:255-258`, `rtl.py:1012-1013`,
     `tracking.py:311`), entao nenhum step muda.
   Fora da guarda, tudo volta aos valores globais, inclusive por excecao (`try/finally` no calculo). Com
   `guard_source = None` ou `edge_ahead_m = None` o governor e identico ao atual.
   `AltitudeAntiClimbGovernor` (modo `hold_enabled = false`) nao recebe a guarda; documentar.
2. **`FailsafeSupervisor.clamp_kinematics`** (`telemetry/failsafe.py:220-288`): o teto de `vz > 0` dentro de
   `altitude_hold_window` passa a `max(window_ceiling, guard_climb_authority)` **somente** quando
   `odom_supervisor` reporta guarda ativa e a janela esta aberta. Nunca fora de uma janela. Novo metodo
   `FailsafeSupervisor.guard_hold_boost() -> float` para teste.
3. **`OdometrySupervisor.altitude_ceiling`** (2.2): allowance so com a guarda ativa e limitado pelo teto do
   local. `is_ceiling_breached` continua exigindo `ceiling_breach_streak = 3` amostras.

Pisos de seguranca (testar): a guarda nunca comanda `vz > guard_climb_authority`; nunca desativa o teto
efetivo; nunca atua sem janela de hold aberta; nunca altera `vx`/`vy` alem de reduzir a escala; `vyaw`
permanece 0.0.

### 2.5 Politica na reentrada e no pouso

- **[FATO]** O falso climb de reentrada e o caso mais perigoso (1.5, M1): 1.8 m relativos contra teto 1.4 m
  dispara emergencia no ar. A resposta e a guarda (descida ate `guard_descent_cap` = 0.16 normalizado =
  0.4 m/s com `vertical_normalized_to_mps = 2.5`) e o allowance de teto limitado pelo teto do local. Se
  mesmo assim a falha for acionada, o comportamento de `_check_health` permanece o atual (pouso de
  emergencia): nao o enfraqueca.
- **Reconhecer a entrada para pousar:** a transicao `ENTERING -> ON` e registrada por log
  (`[PLATFORM state=ON ...]`); `snapshot.platform_state` fica disponivel para o `_touchdown`.
- **Touchdown (P1-c, opcional):** em `_touchdown(..., at_base=False)`, com `platform.height_m > 0` e
  `platform_state == OFF`, o limiar de "grounded" passa a `touchdown_altitude_m - platform.height_m` (o piso
  real esta 1.0 m abaixo do plano de `z0`). Com `at_base=True` ou `height_m = 0.0` nada muda.

### 2.6 `estimation/vertical_estimator.py` (P1; flag `calibration.altitude_estimator`)

Substitui o gate de posicao por um gate no dominio de `vz` com integracao propria.
`CalibrationConfig` ganha: `altitude_estimator: str = "legacy"` (`"legacy"` | `"vz_gated"`, validar com
`ValueError`), `vz_max_speed_mps = 1.5`, `vz_max_accel_mps2 = 5.0` (**nao** reutilize os 0.40 do governor:
descrevem o perfil comandado, nao a reacao do firmware), `vz_reject_streak = 3`,
`altitude_probe_enabled = True`, `altitude_anchor_enabled = False`, `altitude_anchor_tau_sec = 6.0`,
`altitude_anchor_gate_m = 0.25`.

`VerticalStateEstimator` (puro): `reset(z)`, `update_velocity(vz_mps, stamp_sec) -> z_est`,
`update_anchor(alt_m, stamp_sec)`. Gate: `|vz| <= vz_max_speed` e `|vz - vz_prev|/dt <= vz_max_accel`;
violacao segura `vz_prev` (nao zero) e conta; `vz_reject_streak` seguidas aceita o novo regime (agora seguro,
pois o estado integrado nao carrega o glitch). `z += vz_trusted*dt`, `dt = clamp(stamp_k - stamp_{k-1}, 0, 0.5)`
(mesmo teto do driver); `dt <= 0` e amostra ausente; semente: primeiro `pose.z`; `header.stamp` zerado ou
nao crescente cai no tempo monotonico de chegada. Ancora opcional: `nu = fw_alt - z_est`; se `|nu| <= gate`,
`z_est += (1 - exp(-dt/tau)) * nu`; senao nao corrige e sinaliza `datum_disagreement` apos 3 s persistentes.
Em `odometry_callback`: `"vz_gated"` usa o estimador; `"legacy"` mantem o ramo atual bit a bit.
`inject_synthetic_sample` continua isento. A calibracao de z0 usa o `z` armazenado.

### 2.7 (reservado)

Sem conteudo: o regulador de altura visual e o `governor.retarget` pertenciam ao RTL em dois estagios e
foram retirados.

### 2.8 Assentamento pre-saida (L4) e fronteira Stage 1 -> Stage 2

Em `steps/search.py`, no inicio de `execute`, **dentro** da janela de hold e **antes** do loop de cruzeiro,
quando `platform.edge_ahead_m is not None`: `_settle_before_departure(ctx)` roda o governor sem translacao
(`vx = vy = 0`, `vz` do governor pelo mesmo `_command`/`_hold` ja usado) ate
`|relative_altitude - relative_target| <= governor.deadband_m` por `departure_settle_window_sec`, no maximo
`departure_settle_timeout_sec`. Esgotar o teto nao e falha: loga e segue (o `horizontal_scale` ja
estrangula o cruzeiro enquanto o erro for grande). Justificativa: o firmware encerra o launch profile a
~1.0 m e o alvo e 0.8 m; cruzar a borda com 0.2 m de erro mistura dois efeitos que F-2 precisa separar.
Linha de log `[PLATFORM departure_settle err=... elapsed=...]`.

### 2.9 RTL: fora de escopo; ganchos P1 opcionais

- **P1-a:** `rtl.py::_halt_translation` (`rtl.py:1182-1191`) hoje envia `vz = 0.0` literal. Trocar por
  `ctx.governor.compute_vz(...)` + `clamp_kinematics`, como as demais fases, para nao soltar o eixo vertical
  perto da borda. Mudanca de poucas linhas; os testes existentes de comandos devem continuar verdes.
- **P1-b:** em `_center_over_marker`, quando o marcador e observado, chamar
  `ctx.odom_supervisor.platform_monitor.override_along_track(-command.ex_body_m, ttl_sec=0.5)` (o marcador
  esta no ponto de decolagem, premissa de projeto do Stage 1). Torna o reconhecimento de ENTERING/ON
  independente da odometria horizontal durante a centralizacao.
- **P1-c:** a troca do limiar de "grounded" do `_touchdown` (2.5).
Nao ha mais nenhuma edicao esperada em `steps/rtl.py`.

---

## 3. Mapa do que muda

| Arquivo | Acao |
|---|---|
| `parameters.py` | `PlatformConfig`, `MissionParameters.platform`, `relative_target_altitude_m`, campos de `CalibrationConfig` (2.6) |
| `config_audit.py` | checagens de envelope de `platform.*` e do teto do local; piso de `target_altitude_m` aplicado a relativa |
| `estimation/platform_monitor.py` | NOVO (2.3) |
| `estimation/vertical_estimator.py` | NOVO, P1 (2.6) |
| `telemetry/odometry.py` | 2.2 |
| `controllers/altitude_hold.py` | guarda de borda (2.4) |
| `telemetry/failsafe.py` | `guard_hold_boost`, teto de `clamp_kinematics` (2.4) |
| `steps/tracking.py`, `steps/search.py`, `context.py` | `height_above_floor_m` (1.3-3); assentamento pre-saida (2.8) |
| `steps/takeoff.py`, `mission.py` | usos de `target_altitude_m` reclassificados (1.3-1); assinatura de `states/altitude`; construtores |
| `mission_config.json` | secao `platform` (2.1) |
| `steps/rtl.py` | somente P1 (2.9) |
| `test/` e `test/support/fake_bebop/` | secoes 4 e 5 |

---

## 4. Testes (obrigatorios; nomes sugeridos, comportamento exigido)

Padrao do repositorio: `dt` explicito, sem relogio de parede, sem ROS. Nao altere o comportamento dos
testes existentes; os campos novos entram com default que os preserva.

- `test/test_platform_config.py`: `relative_target_altitude_m = 0.8` com 1.8/1.0 e igual a `target` com
  `height_m = 0`; recusa relativa abaixo de `takeoff_settle_min_altitude_m`; `MissionParameters()`
  round-trip JSON com a secao nova; `mission_config.json` carrega; `config_audit` acusa cada envelope violado
  e nao acusa os defaults; teto do local clampa `altitude_ceiling` (`min(...)`, ex.: target 1.8, platform 1.0,
  margin 0.6, allowance 0.5 ativo, venue 2.6 -> `min(1.9, 1.6) = 1.6`; sem venue -> 1.9; allowance inativo -> 1.4).
- `test/test_floor_reference.py`: `height_above_floor_m = relative + floor_offset_m`; IBVS com
  `floor_offset_m = 1.0` e `relative_altitude = 0.8` projeta o mesmo que `relative = 1.8` sem offset;
  `min_altitude_for_ibvs_m` e `nadir_range_threshold_m` avaliados em AGL; `drone.takeoff` e `_ascend` usam a
  relativa; payload do milestone `mission.takeoff` segue em 1.8; firmware que sobe a 1.0 m relativos nao
  dispara `_ascend` nem rompe o teto de 1.4 m.
- `test/test_platform_monitor.py`: sequencia saida (`ON -> LEAVING -> OFF`) e retorno
  (`OFF -> ENTERING -> ON`) com `s` sintetico, uma linha de log por transicao; histerese (ruido de +/-0.04 m
  em torno de uma borda nao gera chattering); `s*` cresce com `h` e com `sonar_half_angle_deg`; `None`/NaN ->
  `UNKNOWN` sem excecao; `edge_ahead_m = None` -> `guard_active = False`; `override_along_track` expira por TTL.
- `test/test_edge_guard.py`: com `guard_source = None` o governor e identico ao atual (mesma serie de `vz`
  que o teste existente); dentro da guarda: limites, `kp` e deadband trocados e restaurados na saida (inclusive
  por excecao); sem degrau de `vz` maior que `max_accel*dt` na ativacao e na desativacao; `horizontal_scale() == 0.0`
  com erro > 0.15 e histerese de retomada em 0.08; shaper vertical: demanda de 0.004 produz pulsos de 0.01 em
  vez de zeros exatos; feedforward so por `feedforward_sec` e so se > 0; nenhum `vz > guard_climb_authority`
  chega ao `move_velocity`; sem janela de hold aberta a guarda nao concede subida; teto efetivo respeitado.
  Planta simples (integrador) que reproduz M1: afundamento de 1.0 m a 0.3 m/s disparado ao cruzar `s*`; a
  perda maxima transiente com a guarda e estritamente menor que sem ela, e a translacao para enquanto o erro
  for > 0.15 m; falso climb na reentrada (+1.0 m): pico abaixo do teto efetivo com `allowance`, e **acima do
  teto sem a guarda** (teste de regressao do risco 1.5-M1).
- `test/test_failsafe.py` (acrescentar): `guard_hold_boost()` e 0 fora da guarda e sem janela;
  `clamp_kinematics` usa o boost so com guarda ativa e janela aberta.
- `test/test_vertical_estimator.py` (P1): spike unico de `vz` (6 m/s por 1 amostra) deixa `z_est`
  inalterado; regime sustentado e aceito apos `vz_reject_streak`; `dt <= 0` ignorado; **teste de regressao do
  defeito 1.2**: o filtro legado de posicao, alimentado pela integral de um spike de `vz`, adota o offset
  apos 3 amostras, enquanto o estimador novo nao; ancora dentro do gate converge com `tau`, fora nao corrige
  e sinaliza apos 3 s; `"legacy"` reproduz bit a bit `test_odometry_plausibility.py` (arquivo nao muda);
  `"vz_gated"` usa `header.stamp`; `inject_synthetic_sample` segue isento.
- `test/test_departure_settle.py`: com `edge_ahead_m` definido o Stage 2 nao translada (`vx == 0` em todos
  os comandos) ate o erro ficar dentro da deadband por `departure_settle_window_sec`; esgotar
  `departure_settle_timeout_sec` segue sem falha; com `edge_ahead_m = None` o passo e identico ao atual.
- `test/test_aruco_rtl.py` (P1-a/P1-c, se implementados): `_halt_translation` envia `vz` do governor; limiar de
  grounded com platform OFF; os testes existentes permanecem verdes.
- Contratos (`test/test_contracts.py` e afins): `MILESTONE_KEYS` inalterado; `[STEP N: ...]` inalterados;
  nenhuma chave nova de milestone.
- Regressao global: `python3 -m pytest test/ -q` 100% verde contra a linha de base do check-in.

---

## 5. Emulador e validacao em bancada

### 5.1 Emulador (`test/support/fake_bebop/plant.py`) - modelar o totem e o sonar

**[FATO]** `plant.py` nao tem solo, plataforma nem sonar (`z = 0` e o chao e a velocidade vertical segue o
comando); sem isso nada valida L2/L3 de ponta a ponta. Adicionar a `PlantConfig`, **inertes por default**
(os testes atuais nao mudam):

- `platform_height_m = 0.0`, `platform_edge_ahead_m = 1.0`, `sonar_half_angle_deg = 20.0`;
- `sonar_hold_gain = 0.0` (1/s): com `gaz == 0` quantizado e o drone em voo, a velocidade vertical alvo e
  `-gain * (range - range_ref)`, saturada em `+/-sonar_hold_max_mps = 0.5`; `range_ref` e a leitura do sonar no
  instante em que `gaz` voltou a zero. Qualquer `gaz != 0` desliga o hold (o firmware segue a velocidade
  comandada). Isto reproduz M1 so quando o comando vertical e exatamente zero;
- leitura do sonar pelo modelo de eco mais proximo da secao 1.5 (com `z` medido sobre o topo do totem; o
  piso esta em `z = -platform_height_m` fora do totem). `states/altitude` publicado por esse mesmo sonar
  (para exercitar a ancora) ou por `z` verdadeiro (opcao `fw_altitude_source = "sonar" | "inertial"`).

Teste do proprio plant em `test_fake_bebop_plant.py`: defaults reproduzem os testes atuais; com
`sonar_hold_gain > 0`, `gaz == 0` e o drone cruzando `s*`, a altitude cai ~1.0 m; com `gaz != 0` (qualquer
valor) nao cai.

Novo `test/test_fly_path_totem.py` (usa `EmulatedFlight` como `test_fly_path_emulated.py`): missao completa,
`platform.height_m = 1.0`, `target_altitude_m = 1.8`, plant com `sonar_hold_gain > 0`:
- exit 0, steps `[1,2,3,4,5]`, nenhum milestone novo;
- decolagem a ~0.8 m relativos (sem subir 1.8), sem romper o teto do local;
- linhas `[PLATFORM ...]` na ordem `LEAVING, OFF` (Stage 2) e `ENTERING, ON` (Stage 5);
- perda maxima de altura sobre o piso durante a saida **menor com a guarda ligada que desligada** e dentro
  da meta da secao 7 para a planta de ganho conhecido;
- na reentrada, sem `trigger_emergency_land` por teto e pico de altura abaixo do teto efetivo.

### 5.2 Validacao em bancada (`--no-fly`) - EXECUTAR e relatar a saida

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_aruco_rtl.py test/test_contracts.py -q
python3 -m pytest test/test_fly_path_emulated.py test/test_fly_path_totem.py -q
python3 scripts/bench_rehearsal.py
python3 -m pytest test/ -q
```
- `bench_rehearsal.py complete`: exit 0, `[STEP 1..5]` em ordem, `COMPLETE_MILESTONES` na ordem existente
  (nenhum milestone novo), sem sobreposicao de fala.
- Sob `--no-fly` real o `KinematicSimulator` nao tem sonar, solo nem totem: o `PlatformMonitor` roda (usa a
  odometria sintetica), mas nao ha degrau. Isto valida a **maquina de estados e a ausencia de regressao**,
  nao a reacao do firmware. Relate esse limite; nao declare que a bancada validou a manutencao de altitude.

---

## 6. Procedimento de campo (entregar como secao nova em `docs/CHECKLIST_VOO_REAL.md`)

Pre-requisito: totem de 1.0 m, medir `edge_ahead_m` (ponto de decolagem -> borda ao longo da trilha de saida)
e o teto do local. Marcador ID 8 no topo do totem, no ponto de decolagem. Sonda ativa.

- **F-0 Decolagem:** registrar a altura em que o firmware encerra o launch profile (esperado ~1.0 m sobre o
  totem) e o tempo ate o governor assentar em 0.8 m. Confirmar que nao passa de `venue_ceiling_agl_m`.
- **F-1 Linha de base:** pairar 60 s a 0.8 m sobre o totem; `z_rel` e `fw_alt` com divergencia estavel
  < 0.10 m; ruido de ambos.
- **F-2 Saida do totem (criterio de aceite):** cruzeiro de 2 m para fora e retorno parando antes do totem, 3
  repeticoes **com a guarda DESLIGADA** (`edge_ahead_m = null`) para medir o efeito puro, depois 3 com a
  guarda ligada, e 3 com `guard_vertical_shaper = false` (A/B do `gaz == 0`). Registrar a **perda maxima
  transiente de altura sobre o piso**, o tempo ate +/-0.10 m, o `vz` minimo (taxa de afundamento do
  firmware), em que `s` o degrau de `fw_alt` ocorre vs `s*` previsto (valida `sonar_half_angle_deg`) e a
  velocidade horizontal real na borda. Repetir sobre uma bancada. **Meta: perda transiente <= 0.25 m
  com a guarda ligada.** Se o firmware afundar mais rapido que a autoridade de subida da guarda
  (0.2 normalizado = 0.5 m/s), a meta nao e atingivel por software e o relatorio deve dizer isso.
- **F-3 Reentrada:** retorno cruzando a borda, com o RTL como esta; registrar o pico de `z_rel` (falso
  climb), se `[PLATFORM state=ENTERING]` ocorreu antes do degrau, o erro lateral acumulado (limitacao 2.3) e
  comparar o pico com o teto efetivo. Dimensiona `ceiling_allowance_m` e `rise_feedforward_mps`.
- **F-4 Feedforwards:** a partir de F-2/F-3, ajustar `sag_feedforward_mps` e `rise_feedforward_mps` e
  repetir 3 vezes; manter apenas se reduzirem a perda.
- **Decisoes:** divergencia `z_rel` vs `fw_alt` estavel e sem degrau de `vz` -> manter
  `altitude_estimator = "legacy"`; degrau/spike de `vz` na borda -> promover `"vz_gated"`; `fw_alt` imune
  ao sonar e `z_rel` com deriva -> habilitar `altitude_anchor_enabled`.

---

## 7. Criterios de aceite

1. `python3 -m pytest test/` 100% verde, incluindo os testes da secao 4; nenhum teste existente alterado
   alem dos P1 de 2.9, se implementados.
2. Com `platform.height_m = 0.0` e `platform.edge_ahead_m = None` a missao e identica a anterior (mesmos
   comandos nos testes existentes).
3. Nenhum milestone novo; marcadores `[STEP N: ...]` intactos; `mission_config.json` compativel.
4. Com `height_m = 1.0` e `target_altitude_m = 1.8`: decolagem a 0.8 m relativos, IBVS em AGL, nenhum
   comando ultrapassa `venue_ceiling_agl_m`, `[PLATFORM ...]` na ordem LEAVING, OFF, ENTERING, ON no
   emulador.
5. **A perda transiente de altura ao sair da borda e REPORTADA com a medicao de F-2, nao prometida.**
6. Relatorio final do implementador lista: arquivos alterados, linha de base vs final da suite, saida de
   `bench_rehearsal.py`, o limite da bancada (5.2), os valores de `edge_ahead_m` e do teto do local usados
   (ou `null`), e os itens [A CONFIRMAR] que dependem de campo. Proponha os commits da secao 8 e aguarde
   autorizacao.

## 8. Commits propostos (atomicos; cada um com testes verdes; nao commitar sem autorizacao)

1. `feat(kinematics): platform config and altitude above floor semantics` (2.1, 1.3-1, 1.3-4 + testes)
2. `fix(vision): drive IBVS and ground projection from height above floor` (1.3-3 + testes)
3. `feat(telemetry): subscribe to states/altitude and log altitude probe` (2.2 sonda + testes)
4. `feat(estimation): platform monitor for takeoff pad departure and re-entry` (2.3 + testes)
5. `feat(controllers): platform edge guard in altitude hold and failsafe` (2.4 + testes)
6. `feat(search): settle altitude before departure from the platform` (2.8 + testes)
7. `feat(estimation): vz-domain vertical estimator behind altitude_estimator flag` (2.6 + testes; P1)
8. `chore(config): platform profile and envelope audit` (2.1)
9. `test(emulator): platform and sonar hold model, totem flight` (5.1)
10. `fix(rtl): govern the vertical axis in _halt_translation` (2.9 P1-a/b/c, opcional)

## 9. Fora de escopo (nao implementar)

- RTL em dois estagios, regulador de altura visual, troca de tilt do estagio fino, re-ancora de datum
  pelo marcador e analise de FOV: **retirados por decisao do usuario** (resultado da auditoria entregue na
  conversa: a meia altura de 0.4 m nao e viavel com esta camera).
- Descida terminal controlada pela missao; qualquer mudanca no driver, no nectar-sdk ou nos parametros do
  firmware (altitude maxima, velocidade vertical maxima); troca do sinal de `tilt` no SDK.

## 10. Higiene observada (nao alterar sem pedido)

- `mvp_mission_bebop/.mission_config.c_zwc3s7.tmp` e dezenas de `accident_metadata_*.json` no diretorio do
  pacote sao artefatos de execucao, nao fonte.
- O `CLAUDE.md` raiz cita `/home/jv/ros2_ws` (correto nesta maquina); docs antigos citam
  `/home/joaomoreira/...` (outra maquina).
