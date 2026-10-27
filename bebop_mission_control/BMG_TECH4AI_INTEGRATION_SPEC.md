# BMG x Tech4AI: Especificacao de Integracao do Agent Drone Inferencia Visual

Status: PROPOSTA, aguardando confirmacao antes de qualquer codigo.
Escopo: `bebop_mission_control/` (Electron + streamer). Nenhuma alteracao em `mvp_mission_bebop/` (ROS 2) nem no repositorio do agente.

---

## 0. Decisoes em aberto (bloqueiam a implementacao)

A analise do codigo e da base T4H - Knowledge mostrou quatro pontos que contradizem as premissas do pedido. Cada um tem uma recomendacao; a implementacao so inicia com a resposta.

| # | Achado | Impacto | Recomendacao |
|---|--------|---------|--------------|
| D1 | `processar_ocorrencia.py` chama a **API 1 (VLM)** em `INFERENCIA_BASE_URL` + `/v1/inferencias/sinistro` (timeout 20 s, latencia alvo p95 15 s). `docs/PROJECT.md` secao 8 registra que a API 1 **ainda nao existe**. Os 96 testes passam porque o HTTP e simulado (`tests/harness.py`). | O modo local **nao e offline** e **nao fecha em 2 s**: a action precisa alcançar a API 1, que no campo so e acessivel pelo 4G do tethering. O que fica local e a execucao da action (validacao, classificacao, chamados, HTML). | Definir qual endpoint serve de API 1 (producao, homologacao ou stub). Para bancada sem internet, o runner aceita `--inference-stub <json>` (resposta fixa no schema da secao 8.1), usado so em teste e ensaio, nunca em voo. Meta realista: overhead local menor que 2 s, total dominado pela API 1. |
| D2 | A API v2 entrega a resposta **somente por webhook** HTTP para `config.webhook_url` (resposta em ate 30 s, assinatura HMAC). Nao existe endpoint de polling de resposta (endpoints: open, close, message, files/upload, files/{id}). Com CGNAT o notebook nao recebe conexao de entrada. | "Somente requisicoes outbound" nao e suficiente para o modo nuvem. | Recomendado: receptor de webhook em `127.0.0.1` exposto por **tunel outbound** (cloudflared ou equivalente) com URL publica configurada em `BMG_T4AI_WEBHOOK_PUBLIC_URL`. Alternativa: relay em nuvem que guarda o webhook e o notebook consulta (exige servico novo). A spec cobre o tunel; o relay troca apenas o Modulo 3.3. |
| D3 | A action e as skills do agente estao **nao versionadas** (`git status` do agente: `??` em `agent/actions/processar_ocorrencia/`, `agent/skills/`, `docs/PROJECT.md`). | Modo nuvem so funciona com o agente sincronizado na plataforma (workflow em `.github/`). Modo local depende de um diretorio fora do monorepo. | Commitar e sincronizar o agente (fora deste escopo). O runner registra o sha256 da action usada em cada despacho para rastreabilidade. |
| D4 | O agente redige as mensagens de chamado via `LLMClient.completion`; sem LLM a action cai no template (`origem_mensagem = "TEMPLATE"`, comportamento ja testado). | Local v1 nao tem LLM de plataforma (exigiria `llm_config.base_url`/`api_key` do gateway). | Local v1 roda com LLM desligado (templates). LLM de plataforma fica para evolucao e e parametrizado, nao codificado. |

Achados adicionais no BMG que esta spec corrige:

- `ensureDroneRoute()` (`electron/main.cjs`, ~L1396) trata `ip route get 192.168.42.1` com saida como "rota existe". Sob tethering o comando **tambem tem saida quando o trafego sai pela default do celular**, entao a funcao retorna `true` sem validar a interface. Correcao no Modulo 1.
- Nao existe canal de falha do relatorio para o renderer. O PRD do agente (secao 3) exige exibir a mensagem de falha no lugar do relatorio. Entra no Modulo 4.

---

## 1. Arquitetura

```text
Estagio 4 (mission.py)
  grava, atomicamente, em MISSION_DIR:
    accident_raw_<ts>.png          (sem overlay do detector)
    accident_inspected_<ts>.jpg    (anotada; NUNCA enviada ao agente)
    accident_metadata_<ts>.json    (contem inference_package)
          |
          v   fs.watch (main.cjs: watchMissionDirectory, debounce 400 ms, espera do sidecar)
  announce(stamp)  --->  send('bmg:raw-evidence-ready')            [existente]
          |
          +--->  tech4aiDispatcher.dispatch(capture)               [NOVO, assincrono, nao bloqueia]
                   |
        mode=local |                         | mode=cloud
                   v                         v
   spawn  <agent>/venv/bin/python     POST /conversation/open (webhook_url = URL do tunel)
          streamer/agent_runner.py    POST /files/upload (multipart, raw png)
            exec(processar_ocorrencia) POST /conversation/{sid}/message (type=image, caption)
            ActionContext em memoria   receptor 127.0.0.1:<porta> <== tunel outbound <== webhook Tech4AI
            API 1 via HttpClient       baixa document.url (ou GET /files/{id})
                   |                         |
                   +-----------+-------------+
                               v
         MISSION_DIR/relatorio_<occurrence_id>.html   (escrita atomica)
                               |
                               v   fs.watch (existente)
         send('bmg:report-ready')  ->  ForensicPanel abre <iframe src=/evidence/...>

  Falha (qualquer modo): MISSION_DIR/t4ai_falha_<id>.json + send('bmg:report-failed')   [NOVO]
```

Principios:

1. O despachante **nunca emite `bmg:report-ready`**. O gatilho continua sendo o arquivo `relatorio_*.html` aparecendo em `MISSION_DIR`, observado pelo watcher existente. Um unico caminho para o frontend, igual nos dois modos.
2. O despachante **nunca bloqueia nem derruba o voo**. Toda falha vira log, arquivo de falha e evento; nenhuma excecao sobe para o `main.cjs`.
3. Valor de telemetria nao e corrigido nem completado (regra do agente): a legenda enviada e exatamente `inference_package.caption` do sidecar.
4. Somente `accident_raw_*.png` vai ao agente. Se so existir a anotada, o despacho e pulado com log (o detector desenhou na imagem; o VLM nao deve ve-la).
5. Concorrencia 1: fila serial de despachos, e o runner local roda com `nice -n 10`, para nao competir com a malha de controle e o YOLO durante o RTL.

O que **nao muda**: contrato `[STEP N: ...]` em stdout, `mission_config.json`, `inference_package.py`, rota `/evidence/`, `ForensicPanel` (exceto o consumo opcional do evento de falha), `watchMissionDirectory` (so ganha uma chamada), qualquer arquivo de `mvp_mission_bebop/`.

---

## 2. Modulo 1: Roteamento de rede no Linux

Arquivos novos: `electron/networkRouting.cjs`, `electron/networkRouting.d.cts`, `src/lib/networkRouting.test.ts` (vitest, mesmo padrao dos modulos `.cjs` existentes).
Arquivo alterado: `electron/main.cjs` (`ensureDroneRoute`, pontos de `nmcli device wifi connect` nas linhas ~1348 e ~1621).

### 2.1 Topologia e invariantes

| Interface | Papel | Rede |
|-----------|-------|------|
| Wi-Fi (`wlp3s0` nesta maquina) | Associada ao AP do Bebop | `192.168.42.0/24`, gateway `192.168.42.1` (so o drone) |
| Tethering USB (`usb0`, `enx*`, driver `rndis_host`/`cdc_ncm`/`ipheth`) | Internet via celular | CGNAT, somente saida |

Invariantes que o BMG passa a verificar e, quando possivel, impor:

- I1: `ip route get 1.1.1.1` resolve com `dev` igual a interface do tethering.
- I2: `ip route get 192.168.42.1` resolve com `dev` igual a interface Wi-Fi e **sem `via`** (rota on-link).
- I3: nenhuma rota default aponta para `192.168.42.1`.

### 2.2 Causa e correcao persistente

O DHCP do Bebop publica `192.168.42.1` como gateway. O NetworkManager instala uma default por essa interface; vence a de menor metrica. Nesta maquina o Wi-Fi domestico ja usa metrica 50 (`ip route`: `default via 192.168.1.1 dev wlp3s0 ... metric 50`), entao confiar nos defaults do NM (ethernet 100, Wi-Fi 600) nao e seguro.

Perfil do Bebop (nome do perfil = SSID criado por `nmcli device wifi connect`, filtrado por `BEBOP_SSID_RE`), aplicado **sem root** (perfis do proprio usuario):

```bash
nmcli connection modify "<SSID_BEBOP>" \
  ipv4.never-default yes \
  ipv4.ignore-auto-dns yes \
  ipv4.route-metric 700 \
  ipv6.method disabled
nmcli device reapply <wifi_dev>      # aplica sem derrubar a associacao
```

Perfil do tethering (uma vez, no preflight, quando detectado):

```bash
nmcli connection modify "<PERFIL_TETHER>" ipv4.route-metric 50 ipv4.never-default no
nmcli device reapply <tether_dev>
```

`ipv4.never-default yes` e o que garante I3. `ipv4.ignore-auto-dns yes` impede o DNS do drone (sem internet) de ser usado pelo resolvedor.

### 2.3 API do modulo (funcoes puras testaveis + fabrica com `exec` injetado)

```text
parseIpRouteGet(stdout) -> { dev: string|null, via: string|null, src: string|null }
detectTetherDevice(ipLinkOrNmcliOutput) -> string|null     // por driver/prefixo, nunca por nome fixo
evaluateRouting({ droneRoute, internetRoute, wifiDevice, tetherDevice, droneIp })
    -> { ok: boolean, issues: Array<'DRONE_VIA_TETHER'|'INTERNET_VIA_DRONE'|'NO_INTERNET_ROUTE'|'NO_TETHER'> }
buildHardeningCommands({ ssid, wifiDevice, tetherProfile, tetherDevice })
    -> Array<{ command: string, args: string[] }>           // sem shell, sem interpolacao de string
createNetworkRouting({ exec, log }) -> {
    verify(): Promise<RoutingReport>,                        // le I1..I3, nao altera nada
    harden(ssid): Promise<{ applied: boolean, report: RoutingReport }>
}
```

Regras:

- `ssid` entra como elemento de `args` (sem `exec` de string), para impedir injecao via SSID hostil.
- `harden()` e idempotente e so chama `nmcli` se `verify()` apontar problema.
- `verify()` tem timeout curto (3 s) e nunca lanca; erro de `ip`/`nmcli` ausente vira `issues: ['NO_TETHER']` ou relatorio parcial com o motivo no log.

### 2.4 Mudancas em `main.cjs`

1. `ensureDroneRoute()`: substituir o teste "saida nao vazia" por `parseIpRouteGet` + `dev === wifiDevice && via === null`. Se falhar, tentar `harden()`; so entao o fallback `ip route add <ip>/32 dev <dev>` e a mensagem com `sudo` ja existentes.
2. Apos cada `nmcli device wifi connect` bem-sucedido em SSID que casa `BEBOP_SSID_RE`: `networkRouting.harden(ssid)`.
3. Preflight de missao e `bmg:ensure-link`: executar `verify()` e registrar no log do driver (`recordLog`) as linhas `[BMG] Rede: drone via <dev>; internet via <dev>`. Divergencia de I1 gera **aviso**, nao bloqueio (voo nao depende de internet).
4. Novo IPC `bmg:network-status` retornando o `RoutingReport` (consumido pelo dispatcher e, opcionalmente, pelo painel de diagnostico).

### 2.5 Criterios de aceitacao do modulo

- Com Wi-Fi no Bebop e tethering ativo: `ip route get 1.1.1.1` mostra o tethering; `ip route get 192.168.42.1` mostra o Wi-Fi sem `via`; `ip route | grep ^default` nao contem `192.168.42.1`.
- `curl -sS --max-time 5 https://api.tech4.ai` alcanca a rede (qualquer status HTTP) com o Wi-Fi no Bebop.
- Video RTP e ping ao drone continuam funcionando.
- Testes unitarios cobrem: saida de `ip route get` com e sem `via`; default roubada pelo drone; ausencia de tethering; SSID com caracteres de shell (`;`, `$()`, aspas) chegando intacto em `args`.

---

## 3. Modulo 2: Runner Python local (`streamer/agent_runner.py`)

Arquivos novos: `streamer/agent_runner.py`, `test/test_agent_runner.py`.
Estilo: docstrings NumPy/reST (`Parameters`, `Returns`, `Raises`), `typing` estrito, `Final`, validacao defensiva de entrada, sem comentarios obvios (padrao Black Bee). Sem dependencia do ROS: roda no **venv do agente**, nao no `nectar-activate` (o `actions_sdk 1.7.0` e `httpx 0.27.2` estao la; Python 3.12 nos dois).

### 3.1 Interface de linha de comando

```text
<AGENT_DIR>/venv/bin/python streamer/agent_runner.py \
    --metadata  <MISSION_DIR>/accident_metadata_<ts>.json \
    [--image    <MISSION_DIR>/accident_raw_<ts>.png] \
    [--output-dir <MISSION_DIR>] \
    --agent-dir <AGENT_DIR> \
    [--inference-stub <resposta_api1.json>] \
    [--timeout-s 60]
```

Segredos por ambiente (nunca por argv, que aparece em `ps`): `INFERENCIA_BASE_URL`, `INFERENCIA_API_KEY`.

### 3.2 Entradas e validacao

1. Le `accident_metadata_*.json`. Exige `inference_package.ready == true` e `inference_package.message.image.contains_detector_overlay == false`; caso contrario sai com codigo 2.
2. Legenda: `inference_package.caption` (fallback `telemetry_caption`). Serializada com `json.dumps(..., ensure_ascii=False)`. **Nenhum campo e preenchido ou corrigido.**
3. Imagem: `--image` ou `inference_package.message.image.filename` relativo ao diretorio do sidecar. Aceita `.png`/`.jpg`/`.jpeg`; mime derivado da extensao; arquivo vazio ou inexistente sai com 2.
4. `--agent-dir` deve conter `agent/actions/processar_ocorrencia/processar_ocorrencia.py`; calcula e emite o `sha256` do arquivo (D3).

### 3.3 Execucao da action (mesmo contrato do motor da plataforma)

O motor executa a action por `exec()` com `ctx` e `secrets` no namespace; `tests/harness.py` reproduz isso. O runner replica, sem importar de `tests/` (o runner nao pode depender de pacote de teste do agente nem altera o agente):

- `ActionContext(client_slug="tech4ai", conversation_id=<occurrence_id>, messages=[Message(role="user", content=<legenda>)], initial_state={"files": [{"fileId": <uuid>, "fileName", "mimeType", "fileType": "image"}]}, state_protected_keys=["files"], file_storage=<MemoryFileStorage>, ...)`.
- `MemoryFileStorage(FileStorageInterface)`: `upload_file` guarda `(filename, bytes)`; `get_file_url` devolve `http://127.0.0.1:<porta>/<file_id>`.
- Entrega da imagem a `HttpClient.get`: **servidor HTTP loopback efêmero** em thread, servindo os bytes da imagem. Evita o monkeypatch de `HttpClient._get_client` (API privada, usada so no harness). Se o spike mostrar que o `HttpClient` recusa loopback, o fallback e o patch de `_get_client` com `httpx.Client(transport=...)`, protegido por verificacao de existencia do atributo no startup (falha explicita se o SDK mudar).
- `secrets = {"INFERENCIA_BASE_URL", "INFERENCIA_API_KEY"}` do ambiente. Ausentes: a action ja responde `MOTIVO_INFERENCIA_INDISPONIVEL`; o runner repassa como falha (codigo 3), sem inventar.
- LLM: `LLMClient.completion` substituido por funcao que levanta `LLMClientError("llm desabilitado no modo local")`. A action captura e usa template (D4). Nenhuma chamada de rede de LLM.
- `--inference-stub`: roteia o POST de `/v1/inferencias/sinistro` para a resposta do arquivo (somente teste e ensaio sem internet). O resto do trafego HTTP e real.
- Timeout global (`--timeout-s`, padrao 60): `signal.alarm` no processo; estouro sai com 4.

### 3.4 Saidas

- Sucesso: pega o ultimo `upload_file` (`filename`, `bytes`), valida que `filename` casa `^relatorio_[A-Za-z0-9_-]{1,64}\.html$` ou e `relatorio_inspecao_sem_identificador.html`, compara com `inference_package.expected_report` (divergencia vira aviso no JSON, nao erro), grava `--output-dir/<filename>` por **arquivo temporario com prefixo `.` e `os.replace`**. O watcher do BMG ignora o temporario (so reage a `relatorio_*.html`).
- Falha da action (`ResponseStatus.ERROR`): grava `t4ai_falha_<occurrence_id|sem_id>.json` com `{ "motivo", "mensagem", "detalhe", "occurrence_id" }` (a `message` ja vem com o prefixo `Falha tecnica na inspecao.`) e sai com 3.
- Ultima linha de stdout, uma unica linha JSON:

```json
{"status":"ok","report":"relatorio_OC_20261021_092549.html","elapsed_s":3.2,"agent_sha256":"...","message_origin":{"SAMU":"TEMPLATE","PM":"TEMPLATE"},"warnings":[]}
```

| Codigo | Significado |
|--------|-------------|
| 0 | Relatorio gravado |
| 2 | Entrada invalida (sidecar, imagem, agent-dir) |
| 3 | A action concluiu com falha (arquivo `t4ai_falha_*.json` gravado) |
| 4 | Falha do runner (SDK ausente, timeout, excecao inesperada) |

Logs de diagnostico em stderr, secos e sem emoji; `imagem_base64` e a chave nunca sao logadas.

### 3.5 Testes (`test/test_agent_runner.py`)

Pulam (`pytest.mark.skipif`) quando `BMG_T4AI_AGENT_DIR/venv/bin/python` nao existe. Rodam o runner por `subprocess` com o python do agente e um servidor falso de API 1 em loopback:

1. Cenario feliz com sidecar real de `mvp_mission_bebop/accident_metadata_*.json` (copiado para `tmp_path`): `relatorio_<id>.html` gravado, HTML autocontido (sem `http://` externo, sem `<script>`), exit 0.
2. `ready=false` ou `contains_detector_overlay=true`: exit 2, nada gravado.
3. API 1 retornando 500: exit 3 e `t4ai_falha_*.json`.
4. Sidecar sem `occurrence_id`: gera `relatorio_inspecao_sem_identificador.html` e `pending` preservado (campo nao inventado).
5. Gravacao atomica: nenhum arquivo `relatorio_*.html` parcial observavel durante a escrita.
6. Segredos ausentes do stdout/stderr.

### 3.6 Criterios de aceitacao do modulo

- `python3 -m pytest test/test_agent_runner.py` passa (ou pula com motivo explicito quando o venv do agente nao existe).
- Overhead local medido (tempo total menos tempo da chamada a API 1) menor que 2 s com `--inference-stub`.

---

## 4. Modulo 3: Despachante assincrono (`electron/tech4aiDispatcher.cjs`)

Arquivos novos: `electron/tech4aiDispatcher.cjs`, `electron/tech4aiDispatcher.d.cts`, `electron/tech4aiConfig.cjs` (+ `.d.cts`), `src/lib/tech4aiDispatcher.test.ts`, `src/lib/tech4aiConfig.test.ts`.
Sem dependencia npm nova: `fetch` e `FormData`/`Blob` nativos do Electron 28 (Node 18+), `http`, `crypto`, `child_process`.

### 4.1 Configuracao (`tech4aiConfig.cjs`, pura)

`loadTech4aiConfig(env) -> Tech4aiConfig`, com validacao defensiva e erro tipado por campo. Carrega antes um `.env` simples (`parseEnvFile(text)`: `CHAVE=valor`, `#` comentario, aspas opcionais; sem expansao). Precedencia: variavel de ambiente real sobre `.env`. Sem dependencia `dotenv`.

Config exposta ao log com segredos mascarados (`****` + 4 ultimos caracteres).

### 4.2 Contrato do despachante

```text
createTech4aiDispatcher({ config, missionDir, spawnImpl, fetchImpl, now, log, emit, fsImpl, networkRouting })
  .dispatch(capture: { stamp, metadataPath, rawPath, occurrenceId? }) -> Promise<DispatchResult>
  .status() -> { mode, inFlight, last: DispatchResult|null }
  .dispose() -> Promise<void>          // mata runner filho, fecha receptor, cancela esperas
DispatchResult = { stamp, mode, outcome: 'ok'|'failed'|'skipped', report?: string, reason?: string, elapsedMs: number }
```

Comportamento comum:

- **Dedup** por `stamp` (mapa de despachos em andamento/concluidos) e por existencia de `relatorio_<id>.html` no diretorio.
- **Fila serial** (promessa encadeada): um despacho por vez.
- **Pre-validacao** (`skipped`, com motivo): sidecar ilegivel, `inference_package.ready !== true`, `rawPath` nulo (so anotada existe), modo `off`.
- Nome de relatorio sempre normalizado com `path.basename` e regex `^relatorio_[A-Za-z0-9_-]{1,64}\.html$`; fora disso usa `inference_package.expected_report`. Escrita sempre dentro de `missionDir`.
- Falha: grava `t4ai_falha_<id>.json`, chama `emit('bmg:report-failed', { occurrenceId, message })`, registra em `recordLog('driver', ...)`. Nunca lanca.

### 4.3 Modo local

```text
spawn('nice', ['-n','10', <BMG_T4AI_AGENT_DIR>/venv/bin/python, STREAMER_DIR/agent_runner.py,
               '--metadata', ..., '--image', ..., '--output-dir', missionDir, '--agent-dir', <dir>,
               '--timeout-s', <BMG_T4AI_LOCAL_TIMEOUT_S>],
      { env: { PATH, HOME, INFERENCIA_BASE_URL, INFERENCIA_API_KEY }, cwd: missionDir, shell: false })
```

- Ambiente **minimo**, sem `getNectarEnv()` (evita `PYTHONPATH`/`LD_LIBRARY_PATH` do ROS contaminarem o venv do agente).
- Interpreta o ultimo JSON de stdout e o codigo de saida (tabela 3.4). Codigo 3: o runner ja gravou `t4ai_falha_*.json`; o despachante apenas emite o evento. Timeout externo = `timeout_s + 5`, depois `SIGKILL`.
- Antes de despachar, `networkRouting.verify()`; sem rota de internet o despacho continua (a API 1 pode ser interna) e o motivo aparece se a action falhar.

### 4.4 Modo nuvem (API v2, base `https://api.tech4.ai/agent/v2`, header `x-client-key`)

Sequencia por captura (campos conforme `T4H - Knowledge`: `areas/tecnologia/tech4ai/apis/{conversas,mensagens-e-webhook,arquivos}.md`):

1. `POST /conversation/open` com `{ agent_id, version_type: "HOMOL"|"PROD", session_id: <uuid v4>, data: { occurrence_id }, config: { inactivity_time: 600, webhook_url: <PUBLICA>/t4ai/webhook/<token> } }`. `data.occurrence_id` e a chave externa da sessao, como no PRD do agente. Sem `occurrence_id` valido, omite `data.occurrence_id` (nao gera identificador).
2. `POST /files/upload` multipart (`session_id`, `file` = `accident_raw_<ts>.png`) -> `file_id`.
3. `POST /conversation/{session_id}/message` com `{ type: "image", image: { id: <file_id>, caption: <inference_package.caption serializado> } }`. Esperado `202`; `409` (duplicada) e tratado como ja enviado.
4. Aguarda o webhook correspondente ao `session_id` (timeout `BMG_T4AI_CLOUD_TIMEOUT_S`, padrao 120 s):
   - `status: "done"` com item `document` em `response.content`: sucesso.
   - Item `text` iniciando com `Inspecao em processamento.` (mensagem de `processing_webhook_message`): intermediaria, continua aguardando.
   - Item `text` iniciando com `Falha tecnica na inspecao.` ou `status: "fail"`: falha; o texto vira `message` do evento.
5. Sucesso: baixa `document.url` imediatamente (URL expira; `expiration_time` no payload). Se a URL ja tiver expirado ou falhar, `GET /files/{document.id}` -> `file_url` (valida 10 min) e baixa. Limite de 5 MB, `Content-Type` `text/html`, escrita atomica (temporario `.` + `rename`).
6. `POST /conversation/{session_id}/close` com `{ closed_by: "CLIENT" }` em `finally`, tolerando `400 Session already closed` (a action emite `EndChat`).

Retentativa: erros de rede e `5xx` nos passos 1 a 3 com backoff exponencial (1 s, 2 s, 4 s, ate 3 tentativas). `4xx` nao e retentado (`401` = chave invalida; `404` = agente nao encontrado ou nao sincronizado, ver D3).

### 4.5 Receptor de webhook (modo nuvem, D2)

- `http.createServer` em `127.0.0.1:BMG_T4AI_WEBHOOK_PORT` (padrao `8787`), iniciado sob demanda no primeiro despacho nuvem e encerrado em `dispose()`.
- Rota unica `POST /t4ai/webhook/<token>`; `<token>` aleatorio (`crypto.randomBytes(16)`) gerado por execucao e embutido no `webhook_url`. Outras rotas: `404`.
- Valida `X-Tech4AI-Signature-256` conforme a documentacao: JSON recanonicalizado com chaves ordenadas recursivamente, sem espacos, nao-ASCII escapado como `\uXXXX` (equivalente a `json.dumps(sort_keys=True, separators=(",",":"))`), HMAC-SHA256 com `x-client-key` como segredo, `crypto.timingSafeEqual`. Invalida: `401`. Corpo maximo 1 MB.
- Responde `200` imediatamente (limite do lado deles: 30 s) e entrega o payload ao despacho que aguarda aquele `session_id`; `session_id` desconhecido: `200` e descarte com log (nao vaza informacao).
- Exposicao: o receptor fica na internet via tunel; a defesa e token na rota + HMAC + corpo limitado + processamento apenas de `session_id` ativo.
- Tunel: nao embutido na v1. Operacao manual antes do voo: `cloudflared tunnel --url http://127.0.0.1:8787` (ou tunel nomeado com hostname fixo) e a URL em `BMG_T4AI_WEBHOOK_PUBLIC_URL`. Modo nuvem sem essa variavel falha na validacao de config com mensagem objetiva.

### 4.6 Criterios de aceitacao do modulo

- Teste unitario do despachante com `spawnImpl` e `fetchImpl` falsos cobre: dedup por `stamp`; fila serial; `skipped` por sidecar ausente, `ready=false` e so-anotada; nome de relatorio malicioso (`../x.html`, `relatorio_a/b.html`) normalizado ou rejeitado; codigos de saida 0/2/3/4 do runner; timeout com `SIGKILL`.
- Teste do receptor: assinatura valida (vetor gerado pelo exemplo Python da documentacao, incluindo texto com acento), assinatura invalida, token errado, corpo grande demais, `session_id` desconhecido.
- Teste do fluxo nuvem com servidor falso: `open -> upload -> message -> webhook(processando) -> webhook(document) -> download -> close`, e o ramo de falha por texto com prefixo `Falha tecnica`.
- Nenhum segredo em log ou em `dispatch status`.

---

## 5. Modulo 4: Gatilho em `watchMissionDirectory` (`electron/main.cjs`)

### 5.1 Alteracoes

1. Topo de `main.cjs`: `const { createTech4aiDispatcher } = require('./tech4aiDispatcher.cjs');` e `const { loadTech4aiConfig, loadEnvFile } = require('./tech4aiConfig.cjs');`.
2. Instancia unica, apos a resolucao de `CONFIG_PATH`/`MISSION_DIR`: `tech4aiDispatcher = createTech4aiDispatcher({ config, missionDir: MISSION_DIR, ..., emit: send, log: (text) => recordLog('driver', { type: 'stdout', text }) })`. Config invalida: log claro e dispatcher em modo `off` (a estacao continua operando).
3. Em `watchMissionDirectory().announce(attempt)`, **imediatamente apos** `send('bmg:raw-evidence-ready', ...)` e somente quando `rawReady`:

```js
tech4aiDispatcher
  .dispatch({ stamp, metadataPath, rawPath, occurrenceId: telemetry.occurrenceId })
  .catch((err) => console.warn('[BMG] Tech4AI dispatch:', err.message));
```

   Reaproveitar o objeto de telemetria ja calculado por `readCaptureTelemetry(metadataPath)` (hoje espalhado no `send`; extrair para uma constante local, sem mudar o payload do evento).
4. Se `!rawReady` (so anotada) ou sidecar ausente apos `SIDECAR_WAIT_ATTEMPTS`: nao despacha; `recordLog` com o motivo.
5. Encerramento: no `before-quit`/`will-quit` existente, `await tech4aiDispatcher.dispose()`.
6. Novos handlers: `ipcMain.handle('bmg:t4ai-status', () => tech4aiDispatcher.status())`.
7. Novo evento `bmg:report-failed` `{ occurrenceId?: string, message: string }`: `electron/preload.cjs` (`onReportFailed: subscribe('bmg:report-failed')`), `src/types/bmg.ts` (tipo do evento e `onReportFailed` em `BmgAPI`), e consumo em `ForensicPanel.tsx`: ao receber falha do `occurrenceId` esperado, `ReportModal` exibe a mensagem no lugar de `report-waiting` (PRD do agente, secao 3). Mudanca minima e com teste vitest; **sujeita a confirmacao** por tocar o frontend.

### 5.2 Riscos e mitigacoes

| Risco | Mitigacao |
|-------|-----------|
| `fs.watch` dispara varias vezes por captura | `announce` ja usa debounce por `stamp`; dedup no despachante cobre repeticao tardia |
| Despacho durante o RTL compete por CPU | `nice -n 10`, fila serial, runner sem ROS |
| Captura em bancada gera despacho real (custo, dados) | Modo `off` por padrao em ambiente sem `BMG_T4AI_MODE`; bancada usa `--inference-stub` |
| Relatorio parcial lido pelo iframe | Escrita atomica em ambos os modos; watcher ja aguarda arquivo nao vazio |
| HTML de terceiro no iframe | Ja sandboxed no `ReportModal`; despachante so grava nome validado e limita tamanho |

---

## 6. Modulo 5: Configuracao (`.env`) e criterios de aceitacao globais

### 6.1 `bebop_mission_control/.env.example` (versionado; `.env` ja esta no `.gitignore`)

```dotenv
# off | local | cloud
BMG_T4AI_MODE=local

# Modo local
BMG_T4AI_AGENT_DIR=/home/jv/agent-tech4ai-agent-drone-inferencia-visual
BMG_T4AI_LOCAL_TIMEOUT_S=60
INFERENCIA_BASE_URL=
INFERENCIA_API_KEY=

# Modo nuvem (API v2)
BMG_T4AI_API_BASE=https://api.tech4.ai/agent/v2
BMG_T4AI_CLIENT_KEY=
BMG_T4AI_AGENT_ID=
BMG_T4AI_VERSION_TYPE=HOMOL
BMG_T4AI_WEBHOOK_PORT=8787
BMG_T4AI_WEBHOOK_PUBLIC_URL=
BMG_T4AI_CLOUD_TIMEOUT_S=120
```

Regras de validacao (`loadTech4aiConfig`): `mode` fora do conjunto e erro; `local` exige `AGENT_DIR` existente e `venv/bin/python` executavel; `cloud` exige `CLIENT_KEY`, `AGENT_ID` (UUID), `VERSION_TYPE` em `HOMOL|PROD`, `WEBHOOK_PUBLIC_URL` com `https:`; timeouts inteiros positivos; `PROD` emite aviso de log. `x-client-key` e `INFERENCIA_API_KEY` nunca vao para argv, log, IPC ou evento. O `agent_id` esta em `agent-manifest.yml` do agente (nao copiar para o repositorio).

### 6.2 Plano de execucao (apos confirmacao), um commit atomico por item

1. `feat(bmg-net)`: `networkRouting.cjs` + testes + correcao de `ensureDroneRoute`.
2. `feat(bmg-t4ai)`: `agent_runner.py` + `test_agent_runner.py`.
3. `feat(bmg-t4ai)`: `tech4aiConfig.cjs` + `.env.example` + testes.
4. `feat(bmg-t4ai)`: `tech4aiDispatcher.cjs` (local) + testes.
5. `feat(bmg-t4ai)`: modo nuvem + receptor de webhook + testes.
6. `feat(bmg-core)`: gatilho em `watchMissionDirectory`, IPC, evento de falha, `ForensicPanel`.

Commits e push somente com autorizacao explicita (regra da sessao), apesar do check-out do `CLAUDE.md`.

### 6.3 Criterios de aceitacao globais

Build e tipos:

```bash
cd /home/jv/ros2_ws/src/mvp_mission_bebop/bebop_mission_control
npm run typecheck
npm run build        # tsc && vite build
npm test             # vitest run, sem regressao nos testes existentes
```

Backend:

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_contracts.py test/test_aruco_rtl.py test/test_agent_runner.py
```

Bancada `--no-fly` (sem motores; Estagio 4 grava `accident_*`), modo local com stub, sem internet:

1. `BMG_T4AI_MODE=local` e API 1 simulada por `--inference-stub` (ou servidor loopback) configurados.
2. Rodar a missao em bancada pela estacao (o launcher usa `--no-fly`); aguardar a captura do Estagio 4.
3. Esperado: `relatorio_<occurrence_id>.html` em `MISSION_DIR` em menos de 2 s de overhead local; `bmg:report-ready` disparado; o modal do `ForensicPanel` abre o `<iframe>` com o laudo; `occurrence_id`, latitude, longitude e horario do relatorio iguais aos do sidecar.
4. Repetir com API 1 retornando 500: surge `t4ai_falha_<id>.json`, o evento `bmg:report-failed` e a mensagem aparece no modal; nenhum `relatorio_*.html` e criado; a missao termina normalmente.
5. Repetir com `BMG_T4AI_MODE=off`: nenhum processo filho, nenhum arquivo novo, comportamento atual preservado.

Rede (campo, com celular em tethering USB e Wi-Fi no Bebop):

6. Invariantes I1 a I3 do Modulo 1 verdadeiras; `bmg:network-status` reporta `ok`.
7. Modo local com API 1 real (quando existir) e modo nuvem com tunel ativo: relatorio recebido; sessao fechada na API v2.

Regressao:

8. `git status` do monorepo limpo ao final, nenhum arquivo solto, nenhum emoji em codigo, comentarios, logs ou commits.

---

## 7. Referencias

- Agente: `/home/jv/agent-tech4ai-agent-drone-inferencia-visual` (`docs/PROJECT.md` secoes 3, 8 e 9; `agent/actions/processar_ocorrencia/processar_ocorrencia.py`; `tests/harness.py`).
- T4H - Knowledge, API v2: `areas/tecnologia/tech4ai/apis/visao-geral-api-v2.md`, `conversas.md`, `mensagens-e-webhook.md`, `arquivos.md` (revisao ate 2027-03-10, sem `docs_stale`).
- BMG: `electron/main.cjs` (`watchMissionDirectory`, `readCaptureTelemetry`, `ensureDroneRoute`, `bmg:get-report-url`), `src/components/cockpit/ForensicPanel.tsx`, `mvp_mission_bebop/telemetry/inference_package.py`.
- Nectar SDK (`/home/jv/ros2_ws/src/nectar-sdk`): busca por `nmcli`, `webhook`, `NetworkManager` e `ip route` em `*.py`, `*.cjs` e `*.sh` sem ocorrencias. Nao ha abstracao reutilizavel para rede, despacho HTTP ou execucao de actions. A busca e por palavra-chave; deve ser repetida de forma dirigida (`nectar/`, `scripts/`) no inicio do Modulo 2 e do Modulo 3.
