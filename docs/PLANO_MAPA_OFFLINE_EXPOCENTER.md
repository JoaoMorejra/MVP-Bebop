# Mapa Satélite Offline do Transamerica Expo Center

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restaurar o mapa tático com imagem de satélite real do Transamerica Expo Center, com zoom completo (botões, roda, pinch), servido 100% do disco, sem nenhuma requisição externa em tempo de execução, preservando o rodapé fixo (base, cidade, localização).

**Architecture:** Os tiles de satélite (XYZ, JPEG 256 px) ficam versionados em `bebop_mission_control/public/tiles/{z}/{x}/{y}.jpg`. O Vite os copia para `dist/`, onde o servidor estático do Electron (`electron/main.cjs`, porta fixa 47823) já os serve na mesma origem; em `npm run dev` o Vite os serve do mesmo caminho. `TacticalMap.tsx` passa a ter uma única fonte local; a fonte remota, a chave embutida, a sonda de 45 s e o modo `grid` forçado saem. A grade `LocalGrid` continua apenas como fallback se os tiles não existirem (clone sem tiles). Um script em `scripts/offline_tiles.py` calcula o conjunto exato de tiles necessários, os obtém uma única vez (online) e verifica a cobertura (offline).

**Tech Stack:** TypeScript/React/Vitest, Electron, Python 3 (stdlib, mais Pillow e GDAL `gdal2tiles` se a fonte for um raster), Vite `publicDir`.

**Spec:** Prompt do usuário de 2026-10-24 ("Arquitetura e Plano de Implementação do Mapa Satélite Offline"), resumido em Global Constraints. Substitui a parte de basemap da Task 7 de `docs/superpowers/plans/2026-10-24-bmg-offline-feira.md` (o rodapé daquela tarefa permanece válido).

Caminhos relativos a `/home/jv/ros2_ws/src/mvp_mission_bebop`; `bebop_mission_control/` abreviado como `BMG/`.

## Diagnóstico

**Causa do erro.** A Task 7 do plano anterior tratou "sem internet" como "sem imagem": adicionou `"basemap": "grid"` e `gridOnly()` e forçou `basemapUsable = false`. O requisito original era manter o mapa e cair na grade só quando faltasse tile. Erro do plano, não do implementador.

**Estado atual do working tree (alterações não commitadas, ler antes de editar):**
- `BMG/config/site-anchor.json`: tem `"city": "São Paulo"` (manter) e `"basemap": "grid"` (remover).
- `BMG/electron/siteAnchor.cjs`: devolve `city` (manter) e `basemap` (remover); o docstring cita `basemap: "grid"` (remover o trecho).
- `BMG/src/lib/siteAnchor.ts`: `MAP_ANCHOR` com `basemap` (remover o campo, manter `city`). `siteAnchor.test.ts` linhas 18, 50, 63, 66 citam `basemap`.
- `BMG/src/components/cockpit/TacticalMap.tsx`: `gridOnly()` (linha 119) usado nas linhas 154, 218, 223, 288; `useReverseGeocode` já removido (manter removido: depende de internet).
- `BMG/src/components/cockpit/TacticalMap.test.tsx`: mock hoistado `anchor.basemap` (linhas 11-19, 40, 66).

**Como o mapa funcionava antes.** `TILE_SOURCES` tinha uma única fonte remota: MapTiler `satellite-v2` (zoom nativo 20, sobrezoom local até 22) ou Mapbox se a chave começa com `pk.`. O mapa fica fixo na base (sem pan), centrado; `fixedView` usa `tileZoom(display, maxZoom)` = `z = min(floor(display), maxZoom)` com `scale = 2^(display - z)`. Zoom de exibição em `[MIN_DISPLAY_ZOOM = 15, MAX_DISPLAY_ZOOM = 22]` (`src/lib/mapView.ts`). O quadro padrão enquadra 300 m no lado curto.

**Servidor estático.** `createStaticServer` serve `DIST_DIR` e responde `index.html` (200) para qualquer caminho inexistente. Para um tile ausente isso devolve HTML como imagem: o `<img>` falha, mas via 200 e sem cache. `/tiles/` precisa de ramo próprio com 404.

**Achados que exigem atenção do usuário:**
1. **Licença da fonte.** Os termos do MapTiler Cloud proíbem baixar tiles em massa e costurá-los em um mapa próprio, e exigem acordo por escrito para exportar conteúdo para uso fora do app ([MapTiler Cloud Terms](https://www.maptiler.com/cloud/terms/)); exceções vêm de plano customizado (sales@maptiler.com). Mapbox e Esri têm restrições análogas de cache offline (não verificadas aqui). Baixar tiles do MapTiler com a chave atual para empacotá-los provavelmente viola o contrato.
2. **Fonte alternativa limpa.** A Prefeitura de São Paulo publica ortofotos pelo GeoSampa, com download das "Ortofotos 2017" por área e dados abertos ([GeoSampa 2.0](https://gestaourbana.prefeitura.sp.gov.br/noticias/prefeitura-de-sao-paulo-lanca-geosampa-2-0-mapa-digital-da-cidade-ganha-versao-responsiva-para-celulares-e-tablets/)). A busca não trouxe a licença exata, a resolução nem a cobertura da área do Expo Center: verificar no portal (e com geosampa@prefeitura.sp.gov.br) antes de usar. Outra via sem restrição: ortomosaico próprio gerado com o Bebop sobre o local.
3. **Chave embutida.** A chave MapTiler (prefixo `gfpp`) estava em texto claro em `TacticalMap.tsx` e no histórico do Git. Remover do código e rotacionar a chave no painel do MapTiler.

## Decisões (padrão adotado entre parênteses)

- **D-A - Origem da imagem (decisão do usuário, bloqueante da Task 2).** Opções: (1) ortofoto do GeoSampa, recortada localmente em tiles (recomendada: aberta e reprodutível); (2) MapTiler/Mapbox/Esri com autorização escrita ou plano que permita exportar; (3) ortomosaico próprio do Bebop. O script é agnóstico à origem (adaptadores `raster` e `xyz`). O implementador NÃO baixa nada antes de o usuário escolher.
- **D-B - Tiles versionados no Git** (sim): ~250 a 450 tiles, estimados em 10 a 25 MB. Cabem no repo e fazem o handover entre computadores (`git pull`) funcionar sem passo extra, ao contrário do cache de áudio.
- **D-C - Fontes remotas removidas do runtime** (sim): sem fonte remota, sem chave, sem sonda de rede. Reintroduzir só por variável de build explícita, fora deste escopo.
- **D-D - Faixa de zoom** (z17 a z20 de tile, exibição 17 a 22): a base fica centrada e o painel mostra algumas centenas de metros, então z15 e z16 (visão de bairro) não agregam e exigiriam imagem de uma área de vários km. `MIN_DISPLAY_ZOOM` sobe de 15 para o menor nível com cobertura completa (17 por padrão; 18 se a fonte não cobrir a área de z17). Z21 e z22 são sobrezoom do z20 (já implementado), com perda de nitidez aceita.

## Cobertura de tiles

Base: `lat -23.6488913`, `lng -46.7187124` (`config/site-anchor.json`). O mapa não faz pan, então basta um quadrado de tiles centrado na base por nível. Para o viewport máximo `V` px (parâmetro `--viewport-px`, padrão 1280; medir a largura real do painel do mapa em 1920x1080 e em 4K antes de fixar), o conjunto por nível é o mesmo que o laço de `tiles` em `TacticalMap.tsx` pode pedir, com 1 tile de margem: `half = V/2 + 256` px, `x0 = floor(cx - half/256)`, `x1 = floor(cx + half/256)`, idem em y, com `(cx, cy)` de `project(lat, lng, z)`.

| z | metros por px | tile (m) | tiles (V = 1920, referência medida) |
|---|---|---|---|
| 15 | 4,38 | 1120 | 121 |
| 16 | 2,19 | 560 | 100 |
| 17 | 1,09 | 280 | 110 |
| 18 | 0,55 | 140 | 121 |
| 19 | 0,27 | 70 | 100 |
| 20 | 0,14 | 35 | 100 |

Com `V = 1280` espera-se 49 a 64 tiles por nível; z17 a z20 somam ~200 a 260 tiles. O comando `plan` do script imprime os números reais. Convenção XYZ (y para baixo, como `project()` em `mapView.ts`), não TMS: `gdal2tiles` exige `--xyz`.

## Global Constraints

- Sem emojis em código, comentários, logs, documentação e commits. Sem comentários redundantes. Python com type hints e docstrings NumPy; TypeScript strict, sem `any`.
- Mapa sem requisição externa em runtime: nenhuma URL `http(s)://` fora de `127.0.0.1`/`localhost` em `<img>`, `fetch` ou CSS do mapa.
- Rodapé preservado, texto exato: `BASE FIXA: -23.648891, -46.718712`, `Cidade: São Paulo`, `Localização: Transamerica Expo Center` (valores de `MAP_ANCHOR`).
- Zoom: botões `+`/`-` (`ZOOM_STEP` 0,5), roda do mouse (`wheelZoom`) e pinch (`pinchZoom`) funcionais; botão de recentrar restaura o enquadramento padrão.
- Não editar `nectar-sdk`. Contrato com o GCS (`[STEP N: ...]`, `mission_config.json`) intocado.
- Commits Conventional Commits em inglês; **nenhum commit ou push sem autorização explícita do usuário** nesta sessão. O working tree tem muitas alterações não commitadas de outras tarefas: commitar só os arquivos desta tarefa, por caminho, e nunca `git add -A`.
- Registrar atribuição da imagem exigida pela licença da fonte escolhida (D-A) no mapa e no manifesto.

## Review Focus

1. Clone sem tiles (ou tile ausente por cobertura): o mapa cai na grade `LocalGrid` sem tela preta e sem laço de requisições; tile ausente na borda com os demais carregados não derruba o mapa inteiro para a grade (regra existente "um tile que falha depois de outro ter carregado é ignorado"). Teste na Task 4.
2. Painel maior que o viewport de planejamento (monitor 4K): bordas sem imagem. O script `verify` e um teste Vitest comparam a cobertura do manifesto com o laço real de `TacticalMap` para larguras de 640 a 1920; declarar o limite no manifesto.
3. `GET /tiles/inexistente.jpg` no Electron deve ser 404 com `Content-Type` de imagem ou vazio, nunca `index.html`. Teste na Task 3.
4. Sobrezoom (display 21 a 22) a partir do z20: tiles com `scale` 2 a 4 sem costuras entre vizinhos (posições em pixel inteiro). Teste na Task 4.
5. Atribuição da imagem exigida pela fonte: texto no mapa igual ao do `manifest.json` (teste de igualdade).

---

### Task 0: Baseline e escolha da fonte (bloqueante)

**Files:** nenhum.

- [ ] **Step 1: Estado e baseline**

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop && git status --short | wc -l
cd bebop_mission_control && npx vitest run src/components/cockpit/TacticalMap.test.tsx src/lib/siteAnchor.test.ts src/lib/mapView.test.ts
```
Expected: passa (com o modo grade atual). Registrar o resultado.

- [ ] **Step 2: Perguntar ao usuário a decisão D-A e esperar a resposta.** Não baixar tiles nem instalar nada antes. Se a escolha for GeoSampa, confirmar com o usuário a área baixada, o CRS (esperado SIRGAS 2000 / UTM 23S, EPSG:31983, a confirmar no metadado) e a licença; se for MapTiler/Mapbox/Esri, exigir confirmação de que há autorização escrita para exportar; se for ortomosaico próprio, perguntar onde está o GeoTIFF.

- [ ] **Step 3: Verificar ferramentas**

```bash
which gdal2tiles.py gdalwarp gdal_translate; python3 -c "import PIL; print(PIL.__version__)"
```
Expected: Pillow presente. Se faltar GDAL e a fonte for raster, propor instalação ao usuário (`apt install gdal-bin`), sem instalar sozinho.

---

### Task 1: Reverter o modo grade forçado

**Files:**
- Modify: `BMG/config/site-anchor.json`, `BMG/electron/siteAnchor.cjs`, `BMG/src/lib/siteAnchor.ts`, `BMG/src/components/cockpit/TacticalMap.tsx` (apenas `gridOnly` e usos), `BMG/src/lib/siteAnchor.test.ts`, `BMG/src/components/cockpit/TacticalMap.test.tsx`

**Interfaces:** `readSiteAnchorFile(file)` devolve `{ latitude, longitude, accuracyM, name, city }` (sem `basemap`). `MAP_ANCHOR` vira `{ latitude, longitude, name, city }`.

- [ ] **Step 1: Write the failing tests**
  - `siteAnchor.test.ts`: remover `basemap` das expectativas e acrescentar `it('has no basemap switch')`: `expect(shipped).not.toHaveProperty('basemap')` e `expect(MAP_ANCHOR).not.toHaveProperty('basemap')`; o `toEqual` de "defaults the name and accuracy" passa a `{ latitude, longitude, accuracyM: 25, name: 'local configurado', city: null }`.
  - `TacticalMap.test.tsx`: remover o mock `anchor.basemap` e o `afterEach` que o reinicia; novo `it('requests tiles on first render')`: com o ambiente jsdom, `container.querySelectorAll('img').length > 0` logo após `render`.

- [ ] **Step 2: Run to verify they fail**

```bash
cd bebop_mission_control && npx vitest run src/lib/siteAnchor.test.ts src/components/cockpit/TacticalMap.test.tsx
```
Expected: FAIL (`basemap` ainda presente; sem `<img>` de tile).

- [ ] **Step 3: Implement** a remoção de `basemap` nos quatro arquivos listados, de `gridOnly()` e de todas as suas condições (`!gridOnly() &&`, `gridOnly() ||`, inicialização de `tilesOk`). Manter `city`, o rodapé e a remoção de `useReverseGeocode`.

- [ ] **Step 4: Run tests**

```bash
cd bebop_mission_control && npx vitest run src/lib/siteAnchor.test.ts src/components/cockpit
```
Expected: PASS.

- [ ] **Step 5: Commit** (se autorizado), só estes arquivos: `fix(bmg-map): restore satellite basemap by dropping forced grid mode`

---

### Task 2: Pipeline de tiles (`scripts/offline_tiles.py`)

**Files:**
- Create: `scripts/offline_tiles.py`, `test/test_offline_tiles.py`
- Create (saída, versionada): `BMG/public/tiles/{z}/{x}/{y}.jpg`, `BMG/public/tiles/manifest.json`

**Interfaces (Python, NumPy docstrings, `ValueError`/`TypeError` defensivos):**
```python
class TileKey(NamedTuple):
    z: int; x: int; y: int

def project(lat: float, lng: float, zoom: int) -> Tuple[float, float]
def required_tiles(lat: float, lng: float, zooms: Sequence[int], viewport_px: int = 1280) -> List[TileKey]
def verify_coverage(root: pathlib.Path, required: Sequence[TileKey]) -> List[TileKey]   # faltantes
```
`project` espelha `project()` de `BMG/src/lib/mapView.ts`. Subcomandos (códigos de saída 0 ok, 1 cobertura incompleta, 2 uso/rede):
- `plan [--zooms 17-20] [--viewport-px 1280]`: imprime tiles por nível e total, sem rede. Lê lat/lng de `BMG/config/site-anchor.json`.
- `raster --src FILE [--srs EPSG:31983]`: reprojeta para EPSG:3857 com `gdalwarp`, corta com `gdal2tiles.py --xyz -z <min>-<max>` e recodifica em JPEG qualidade 80 (Pillow), mantendo só os tiles de `required_tiles`.
- `xyz --url-template TEMPLATE [--rate 4]`: só para fonte com autorização (D-A.2). Chave lida da variável de ambiente `BMG_TILE_KEY`, nunca de arquivo versionado; limite de `--rate` requisições por segundo, retry com recuo, retoma o que já existe.
- `verify`: confere `required_tiles` contra o disco e o `manifest.json`; falha com a lista de faltantes.
Em todo subcomando de escrita, gerar `BMG/public/tiles/manifest.json`: `{ "schema": "bmg.tiles.v1", "source": str, "license": str, "attribution": str, "anchor": {lat, lng}, "minZoom": int, "maxZoom": int, "viewportPx": int, "count": int, "bytes": int }`.

- [ ] **Step 1: Write the failing tests** (`test_offline_tiles.py`, sem rede):
  - `test_project_matches_known_tile`: `project(-23.6488913, -46.7187124, 17)` com tolerância de 1e-6 contra o valor calculado independentemente por `mercantile`-free fórmula fechada (copiar a fórmula do TS para o teste e conferir o par `(x, y)` em z17 e z20).
  - `test_required_tiles_square_and_contains_base`: para z17 a z20 e V 1280, o conjunto é um retângulo contíguo, contém `floor(project(base))` e cresce de forma coerente com `half = V/2 + 256`.
  - `test_required_counts_match_documented_reference`: com V 1920 e z15 a z20, os totais por nível são 121, 100, 110, 121, 100, 100.
  - `test_verify_reports_missing`: pasta temporária com metade dos tiles -> lista exata dos faltantes; completa -> lista vazia.
  - `test_manifest_schema`: manifesto escrito tem todas as chaves e `count == len(required)`.
  - `test_xyz_reads_key_from_env_only`: sem `BMG_TILE_KEY` o subcomando `xyz` sai com 2 e não abre conexão (socket monkeypatched para falhar).

- [ ] **Step 2: Run to verify they fail**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && python3 -m pytest test/test_offline_tiles.py -q
```
Expected: FAIL (módulo inexistente).

- [ ] **Step 3: Implement** `scripts/offline_tiles.py`. Reutilizar `gdal2tiles.py` e Pillow; nenhuma dependência nova de Python no pacote ROS.

- [ ] **Step 4: Run tests e `plan`**

```bash
python3 -m pytest test/test_offline_tiles.py -q && python3 scripts/offline_tiles.py plan
```
Expected: PASS; `plan` imprime a contagem por nível.

- [ ] **Step 5: Obter os tiles (ação com internet, depois de D-A resolvida).** Executar o subcomando da fonte escolhida, depois `python3 scripts/offline_tiles.py verify` (código 0) e `du -sh bebop_mission_control/public/tiles`. Abrir 3 tiles (z17, z19, z20) com a ferramenta de imagem e confirmar que mostram o complexo do Expo Center e que não são placeholders ("API key required", marca d'água, quadro cinza). Se o z17 não estiver coberto pela fonte, repetir `plan` com `--zooms 18-20` e registrar `minZoom` 18 no manifesto.

- [ ] **Step 6: Commit** (se autorizado): `feat(bmg-map): add offline tile pipeline and Expo Center satellite tiles`

---

### Task 3: Servir `/tiles/` pelo Electron e pelo Vite

**Files:**
- Modify: `BMG/electron/main.cjs` (função `createStaticServer`, junto ao ramo `/evidence/`)
- Test: `BMG/src/lib/tileServing.test.ts` (novo; usar o padrão de `mainHarness.ts`/`launchReadinessMain.test.ts` para exercitar o servidor, ou extrair o ramo para uma função pura em `BMG/electron/tileRoute.cjs` testável)

**Interfaces:** `resolveTilePath(distDir: string, urlPath: string): string | null` em `electron/tileRoute.cjs`: aceita apenas `/tiles/<z>/<x>/<y>.jpg` e `/tiles/manifest.json` (inteiros não negativos, sem `..`, sem barras extras), devolve o caminho absoluto sob `distDir/tiles` ou `null`. O ramo do servidor responde 200 com `Content-Type: image/jpeg` e `Cache-Control: public, max-age=86400`, ou 404 sem corpo (nunca `index.html`).

- [ ] **Step 1: Write the failing tests**: `resolveTilePath` aceita `/tiles/18/95432/145123.jpg`; rejeita `/tiles/../main.cjs`, `/tiles/18/1/2.png`, `/tiles/a/b/c.jpg`, `/tiles/18/1/2.jpg/extra`; servidor devolve 404 para tile inexistente e 200 com `image/jpeg` para tile existente numa pasta temporária.
- [ ] **Step 2: Run to verify they fail**: `cd bebop_mission_control && npx vitest run src/lib/tileServing.test.ts` -> FAIL.
- [ ] **Step 3: Implement** a função e o ramo. Confirmar que `vite build` copia `public/tiles` para `dist/tiles` e que o Vite dev serve `/tiles/...` (ambos por `publicDir`, sem configuração nova).
- [ ] **Step 4: Run tests e build**: `npx vitest run src/lib/tileServing.test.ts && npm run build && ls dist/tiles | head` -> PASS; `dist/tiles/manifest.json` existe.
- [ ] **Step 5: Commit** (se autorizado): `feat(bmg-electron): serve local map tiles with strict path resolution`

---

### Task 4: `TacticalMap` com fonte local e zoom completo

**Files:**
- Modify: `BMG/src/components/cockpit/TacticalMap.tsx`, `BMG/src/lib/mapView.ts` (apenas `MIN_DISPLAY_ZOOM`, conforme D-D e o `minZoom` do manifesto)
- Test: `BMG/src/components/cockpit/TacticalMap.test.tsx`, `BMG/src/lib/mapView.test.ts`, `BMG/src/lib/tileCoverage.test.ts` (novo)

**Interfaces:**
- `TILE_SOURCES` passa a ter uma entrada: `{ id: 'local-satellite', url: (_s, z, x, y) => `/tiles/${z}/${x}/${y}.jpg`, subdomains: ['a'], maxZoom: 20 (igual ao `maxZoom` do manifesto), filter: IMAGERY_FILTER, attribution: TILE_ATTRIBUTION }`. `export const TILE_ATTRIBUTION: string` igual ao `attribution` do `manifest.json` (teste de igualdade, no padrão de `siteAnchor.test.ts`).
- Remover: `MAP_API_KEY`, as ramificações Mapbox e MapTiler, `TILE_PROBE_INTERVAL_MS` e o efeito da sonda, `SOURCE_FAILURE_BUDGET` por fonte (a contagem de falhas vira um limite único para cair na grade). `TILE_LOAD_TIMEOUT_MS` cai para 2000: com fonte local a ausência de resposta significa tiles faltando.
- `tilesOk` inicia `null` e o grid de tiles é montado de imediato (carrega em milissegundos do disco); o primeiro `onLoad` o torna `true`. O rótulo de status deixa de dizer "mapa online": `mapa local` / `grade offline`.
- Zoom: nenhuma mudança de comportamento em `zoomBy`, `wheelZoom`, `pinchZoom`, recentrar; `MIN_DISPLAY_ZOOM` ajustado ao `minZoom` do manifesto.

- [ ] **Step 1: Write the failing tests**
  - `TacticalMap.test.tsx`: `it('draws only same-origin tile URLs')`: todos os `img.src` dos tiles casam `/^http:\/\/localhost(:\d+)?\/tiles\/\d+\/\d+\/\d+\.jpg$/` (jsdom resolve a URL relativa), e `fetch` espiado nunca é chamado; `it('keeps the satellite layer and hides the grid once a tile loads')`: disparar `load` em um `<img>` -> `queryByText(/GRADE TÁTICA LOCAL/)` ausente; `it('falls back to the grid when every tile fails')`: disparar `error` em todos -> grade presente, sem `<img>` de tile e sem novas requisições depois disso; `it('ignores a failing tile after another loaded')`: um `load` seguido de um `error` mantém `tilesOk` verdadeiro; `it('zoom in and out change the scale')`: clicar em "Aproximar o mapa" e "Afastar o mapa" muda o `scaleLabel` e respeita os limites; `it('wheel and pinch zoom')`: `wheel` com `deltaY` negativo aproxima; dois ponteiros afastando aproximam.
  - Sobrezoom: com `manualZoom` 21,5 as posições `left/top/size` dos tiles vizinhos são inteiras e `left(i+1) - left(i) === size(i)` (sem costura).
  - `tileCoverage.test.ts` (Vitest, Node): lê `public/tiles/manifest.json`; para larguras de painel de 640 a `manifest.viewportPx` e cada zoom de exibição de `MIN_DISPLAY_ZOOM` a `MAX_DISPLAY_ZOOM` em passos de 0,5, calcula o conjunto de tiles que o laço de `tiles` pediria (`fixedView` + a mesma aritmética) e afirma que todos existem em `public/tiles`. Pular com mensagem clara se o diretório não existir (clone sem tiles).
  - `mapView.test.ts`: `MIN_DISPLAY_ZOOM` igual ao `minZoom` do manifesto.
- [ ] **Step 2: Run to verify they fail**: `npx vitest run src/components/cockpit/TacticalMap.test.tsx src/lib/tileCoverage.test.ts src/lib/mapView.test.ts` -> FAIL.
- [ ] **Step 3: Implement** as mudanças acima. Antes de remover a chave, `grep -rn "gfpp[A-Za-z0-9]\{16\}\|VITE_MAP\|VITE_MAPTILER\|VITE_MAPBOX" BMG docs` e tratar cada ocorrência (inclusive `.env*` e docs que mandam definir a chave).
- [ ] **Step 4: Run tests e build**: `npx vitest run src/components/cockpit src/lib && npm run build` -> PASS, `build exit=0`, sem erro de TypeScript.
- [ ] **Step 5: Commit** (se autorizado): `feat(bmg-map): render offline satellite tiles from local storage`

---

### Task 5: Rodapé preservado (regressão)

**Files:** `BMG/src/components/cockpit/TacticalMap.test.tsx` (somente testes).

- [ ] **Step 1:** Garantir os testes `footer shows the fixed base, city and location` (textos exatos acima) e `does not move the base coordinates with the aircraft` presentes e passando com o mapa de satélite (já existem da Task 7 do plano anterior; adaptá-los se o mock removido os afetava).
- [ ] **Step 2:** `npx vitest run src/components/cockpit/TacticalMap.test.tsx` -> PASS.

---

### Task 6: Verificação ponta a ponta offline e handover

**Files:** `docs/CHECKLIST_VOO_REAL.md` (seção "Mapa offline").

- [ ] **Step 1: Suítes completas**

```bash
source /home/jv/ros2_ws/bin/nectar-activate && cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_offline_tiles.py -q
cd bebop_mission_control && npm test && npm run build; echo "build exit=$?"
```
Expected: verde, `build exit=0`. Falhas pré-existentes de outras tarefas são reportadas à parte.

- [ ] **Step 2: Verificação visual sem rede.** Com a máquina sem rota para a internet (o usuário desliga a rede; o agente não altera configuração de rede), rodar `npm run dev` e abrir o cockpit no navegador embutido. Conferir com capturas: (a) mapa de satélite do Expo Center desde o primeiro quadro, sem grade; (b) zoom pelos botões `+`/`-`, roda e pinch, de `MIN_DISPLAY_ZOOM` a 22, sem quadrados pretos nem costuras; (c) recentrar; (d) rodapé com os três textos exatos; (e) painel estreito e largo; (f) `read_network_requests`: nenhuma requisição fora de `localhost` e nenhuma falha 404 de tile nas faixas de zoom permitidas; (g) `read_console_messages`: sem erros. Repetir contra o app Electron empacotado (`npm run start:app`), porque lá o servidor é o `createStaticServer`.

- [ ] **Step 3: Cenário de falha.** Renomear temporariamente `dist/tiles` e confirmar que o mapa cai para a grade sem tela preta; restaurar.

- [ ] **Step 4: Documentar** em `docs/CHECKLIST_VOO_REAL.md`: origem e licença da imagem, comando `python3 scripts/offline_tiles.py verify`, faixa de zoom coberta, limite de largura de painel e como regerar os tiles.

- [ ] **Step 5: Fechar** (somente com autorização): commits atômicos por tarefa, `git status` sem arquivos desta tarefa pendentes.

---

## Self-review

| Requisito do prompt | Tarefa |
|---|---|
| Reverter `basemap: "grid"` e `gridOnly()` | 1 |
| Imagem de satélite real do Expo Center, offline, de disco | 2, 3, 4 |
| Bounding box, zooms e contagem de tiles | seção "Cobertura de tiles", Task 2 `plan` |
| Local de armazenamento e script de obtenção | 2 (`public/tiles`, `scripts/offline_tiles.py`) |
| `TILE_SOURCES` local, sobrezoom, `tilesOk` imediato, sem timeouts de rede | 4 |
| Zoom completo (botões, roda, pinch) | 4 |
| Rodapé fixo preservado | 5 |
| Testes Vitest e verificação visual | 4, 6 |

Nomes consistentes entre tarefas: `required_tiles`/`verify_coverage` (2) usados em `verify`; `resolveTilePath` (3); `TILE_ATTRIBUTION`, `manifest.minZoom`/`maxZoom`/`viewportPx` (2, 4); `MIN_DISPLAY_ZOOM` (4).

## Pendências

- Resultado de D-A (origem da imagem) e a licença correspondente: registrar em `manifest.json` e no checklist.
- Rotacionar a chave MapTiler exposta no Git.

### Estado em 2026-10-25

- D-A: o usuário escolheu a fonte usada antes (MapTiler satellite-v2), com atribuição padrão do provedor (`© MapTiler © OpenStreetMap contributors`). A autorização escrita do MapTiler para exportar/cachear tiles não foi confirmada: a Task 2 Step 5 (download) não foi executada e `bebop_mission_control/public/tiles/` não existe. Sem a autorização, a alternativa é GeoSampa ou ortomosaico próprio via `raster` (exige `sudo apt install gdal-bin python3-gdal`, a cargo do usuário).
- Chave: removida do código, de `src/vite-env.d.ts` e das docs; continua no histórico do Git e em `.superpowers/baseline-*`. Rotacionar no painel do MapTiler e usar uma chave nova apenas via `BMG_TILE_KEY`.
- Cobertura: `required_tiles` passou a ser a união da fórmula deste plano com o laço real de `TacticalMap` (a fórmula sozinha deixava uma coluna/linha de borda descoberta com painel de 1793 a 1920 px). Com `--viewport-px 1280` e z17-20: 64 por nível, 256 no total. Com 1920 e z15-20: 121/100/110/121/119/119 (700); a fórmula do plano (121/100/110/121/100/100) segue fixada no teste como limite inferior. `MIN_DISPLAY_ZOOM` passou a 17 no TS e no script.
- Fallback: a grade só aparece quando todos os tiles da tela falham sem nenhum carregar, ou após 2 s sem carga; é definitiva até remontar o painel (sem nova requisição ao mudar o zoom).
- Testes que dependem de `public/tiles/manifest.json` (atribuição igual ao manifesto, `MIN_DISPLAY_ZOOM == minZoom`, cobertura por largura de painel) pulam com mensagem até os tiles existirem.
- Fora de escopo, anterior a este plano: `electron/main.cjs` faz `decodeURIComponent(req.url)` sem `try/catch`; uma URL com `%` malformado lança exceção não tratada no processo principal.
- Não verificado: imagem real dos tiles (z17, z19, z20 mostrando o Expo Center), `verify` com código 0, `dist/tiles` após o build, `/tiles/` servido pelo Vite dev, conferência visual sem rede no navegador embutido e no app Electron (Task 6 Steps 2 e 3).
