# Especificação Técnica do Agente: AssistenteVisualDrone
**Papel no Sistema:** Inspetor de Inferência Visual e Resposta a Emergências  
**Plataforma de Voo:** Parrot Bebop / ROS 2 (`mvp_mission_bebop`)  
**Status do Módulo de Voo:** Operacional / Implementado  

---

## 1. Visão Geral e Propósito

O **`AssistenteVisualDrone`** (ou `agente_assistente_visual_drone`) é o agente de software responsável pela **inspeção visual automatizada, avaliação de danos e tomada de decisão emergencial** a partir de imagens aéreas capturadas pelo drone Parrot Bebop.

Com o código de controle de voo, navegação por waypoints e aproximação ao alvo já implementados e funcionais, a responsabilidade deste agente tem início quando o drone atinge a posição da ocorrência. Ele opera como o **analista/inspetor do sistema**, recebendo a imagem da cena, processando a inferência de inteligência artificial sobre o sinistro e acionando autonomamente os órgãos competentes de socorro (**SAMU**, **Polícia Militar** ou **ambos**).

---

## 2. Fluxo de Dados: Do Voo do Bebop à Chegada da Imagem no Agente

```
┌────────────────────────────────────────────────────────┐
│  Módulo de Navegação / Voo (Pronto / Implementado)      │
│  - Aproximação do local do acidente                    │
│  - Estabilização em Hovering sobre o alvo              │
│  - Ajuste de Gimbal Tilt (ex: -69° para visão zenital) │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Driver do Bebop / Stream de Câmera                    │
│  - Stream de vídeo H.264 / Driver ROS 2                │
│  - Tópico de imagem (/image_raw ou câmera integrada)   │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Extração e Pré-processamento do Snapshot              │
│  - Congelamento do frame na estabilização do drone     │
│  - Correção de distorção ótica (deswarping fisheye)    │
│  - Sincronização com Telemetria (GPS, Altitude, Tempo) │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│         ASSISTENTE VISUAL DRONE (Este Agente)          │
│  1. Recebe Frame + Metadados da Missão                 │
│  2. Executa Inferência de Inteligência Artificial      │
│  3. Avalia Gravidade, Veículos e Vítimas               │
│  4. Aplica Regras de Negócio e Aciona Emergências      │
└────────────────────────────────────────────────────────┘
```

### Detalhamento do Pipeline de Extração:
1. **Gatilho de Ponto de Inspeção:** Ao alcançar as coordenadas do sinistro, o controlador de voo coloca o drone em modo de espera estática (*hovering*) e posiciona a câmera verticalmente (gimbal tilt negativo).
2. **Captura do Frame:** O sistema consome o frame do fluxo de vídeo da câmera frontal do Bebop, selecionando a imagem com maior estabilidade.
3. **Composição do Pacote de Inspeção:** A imagem bruta é associada aos metadados de telemetria atuais (`altitude_m`, coordenadas, orientação do gimbal e `timestamp`) para contextualizar a escala física dos objetos na pista.
4. **Disponibilização para o Agente:** O pacote é submetido ao agente via chamada de serviço ou evento no pipeline de missão.

---

## 3. Pipeline de Inferência e Avaliação do Sinistro

Ao receber a imagem da inspeção, o agente executa a inferência para extrair três eixos de diagnóstico:

### 3.1. Gravidade do Acidente
* **Classificação de Severidade:** Categorização da ocorrência em *Leve*, *Moderada*, *Grave* ou *Crítica*.
* **Condições do Local:** Detecção de pistas bloqueadas, presença de múltiplos veículos envolvidos, dispersão de peças/destroços e riscos iminentes (fumaça, fogo ou vazamento de combustível).

### 3.2. Estado do(s) Veículo(s)
* **Tipologia:** Identificação das classes envolvidas (automóveis, motos, bicicletas, veículos pesados).
* **Deformação Estrutural:** Avaliação visual de integridade:
  * Amassados leves / colisões de baixa energia;
  * Capotamento / tombamento lateral;
  * Deformação severa de habitáculo/cabine (intrusão estrutural).

### 3.3. Estado do Acidentado / Vítimas
* **Detecção de Pessoas:** Identificação de pessoas no raio do acidente.
* **Indicadores Críticos:** 
  * Vítima caída ao solo / projetada do veículo;
  * Indícios de vítimas presas às ferragens ou imobilizadas no interior do veículo;
  * Transeuntes conscientes prestando auxílio vs. pessoas desfalecidas.

---

## 4. Matriz de Decisão e Despacho de Comandos

A partir do resultado analítico da inferência, o agente atua como despachador operacional, decidindo quais frentes de socorro devem ser acionadas:

| Cenário Inspecionado | Diagnóstico da Inferência | Ação Executada pelo Agente |
| :--- | :--- | :--- |
| **Danos Materiais Leves** | Veículo com danos superficiais; sem vítimas feridas; condutores de pé conversando. | **Acionar Polícia / Trânsito**<br>*(Prioridade Média - Desobstrução e Registro)* |
| **Acidente com Vítimas em Solo** | Queda de motociclista/ciclista ou pedestre caído na pista; ferimentos aparentes. | **Acionar SAMU + Polícia**<br>*(Prioridade Alta - Socorro Médico e Isolamento)* |
| **Colisão Grave com Presos em Ferragens** | Veículo capotado; intrusão total de cabine; ocupantes retidos/inconscientes. | **Acionar SAMU + Bombeiros + Polícia**<br>*(Prioridade Máxima / Código Vermelho)* |

---

## 5. Estrutura Técnica de I/O do Agente

### 5.1. Entrada (Dados recebidos após o voo)
* **Imagem de Inspeção:** Frame capturado em formato RGB/PNG/JPEG.
* **Metadados do Bebop:**
  ```json
  {
    "timestamp": "20261006_181000",
    "altitude_relativa_m": 1.2,
    "gimbal_tilt_deg": -69.0,
    "coordenadas": {
      "latitude": -23.55052,
      "longitude": -46.633308
    }
  }
  ```

### 5.2. Saída (Comandos e Decisão gerada pelo Agente)
O agente gera e propaga uma resposta estruturada para os sistemas de notificação e despacho:

```json
{
  "agente": "assistente_visual_drone",
  "status": "INSPECAO_FINALIZADA",
  "diagnostico": {
    "gravidade": "GRAVE",
    "veiculos_envolvidos": ["motocicleta", "automovel"],
    "danos_veiculo": "COLISAO_FRONTAL_COM_DEFORMACAO",
    "vitimas_detectadas": true,
    "vitimas_ao_solo": 1
  },
  "decisao_acionamento": {
    "acionar_samu": true,
    "acionar_policia": true,
    "acionar_bombeiros": false,
    "nivel_prioridade": "CODIGO_VERMELHO",
    "justificativa": "Motociclista ao solo inconsciente apos colisao com veiculo de passeio."
  },
  "comandos_despachados": [
    "CMD_DISPATCH_SAMU_UTI_MOVEL",
    "CMD_DISPATCH_POLICIA_ISOLAMENTO_LOCAL"
  ]
}
```

---

## 6. Resumo da Responsabilidade Operacional

> **Conclusão:** O código de voo entrega o drone com precisão ao local da ocorrência. Uma vez posicionado, o **`AssistenteVisualDrone`** assume o controle cognitivo: extrai a imagem, executa a inferência diagnóstica sobre veículos e vítimas, decide a necessidade de apoio emergencial (SAMU, Polícia ou ambos) e despacha imediatamente os alertas para atendimento ao sinistro.
