# Especificação Técnica V4: BMG Mission Control — Remoção de Logo e Integração de Chave de API de Mapa

> **Projeto:** Tech for Humans — Bebop Mission Control (BMG)  
> **Caminho do Frontend:** `/home/joaomoreira/ros2_ws/bebop_mission_control`  
> **Versão:** 4.0 (Ajustes de logo do vídeo e suporte à chave de API de mapas)

---

## 1. Ajustes Imediatos

### 1.1. Remoção da Logo Sobreposta (`PreflightScreen.tsx`)
- **Instrução:** No arquivo `src/components/preflight/PreflightScreen.tsx`, **retirar totalmente o componente `<Wordmark />` do cabeçalho**.
- **Motivo:** O vídeo de fundo (`/assets/Tech4aiMVPvideo.mp4`) já renderiza a logo "Tech for Humans" com alta qualidade e animação no canto superior esquerdo. Deixar apenas um espaço vazio (`<span aria-hidden />`) na coluna esquerda do header permite que a logo original do vídeo brilhe limpa e sem nenhuma sobreposição ou duplicação.

### 1.2. Integração de Chave de API para o Mapa Tático (`TacticalMap.tsx`)
> Superado: o mapa tático usa apenas os tiles offline servidos de `public/tiles` (ver `docs/PLANO_MAPA_OFFLINE_EXPOCENTER.md`); não há chave de API. A seção inteira é histórica.

- **Instrução:**
  1. No arquivo `src/components/cockpit/TacticalMap.tsx`, implementar suporte prioritário a provedores de mapa com **chave de API**:
     - **MapTiler Dark / Dataviz:** `https://api.maptiler.com/maps/dataviz-dark/{z}/{x}/{y}.png?key=${apiKey}`
     - **Mapbox Dark:** `https://api.mapbox.com/styles/v1/mapbox/dark-v11/tiles/256/{z}/{x}/{y}?access_token=${apiKey}`
  2. (removido: leitura da chave de API)
  3. Quando a chave estiver configurada:
     - O mapa carrega 100% dos tiles com altíssima velocidade, sem limites agressivos de requisição e com o tema dark perfeito e nativo do provedor (sem blocos pretos ou erros de carregamento).
  4. Manter o fallback inteligente para CartoDB Dark e OSM caso nenhuma chave seja informada.
