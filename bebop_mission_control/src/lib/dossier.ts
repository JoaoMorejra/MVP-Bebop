import type { EvidenceItem } from '../types/bmg';
import { stampLabel } from './format';

/**
 * Builds a self-contained forensic dossier.
 *
 * The image placeholders are replaced by the main process with data URIs read
 * straight from the mission working directory, so the exported file carries the
 * evidence rather than pointing at it and can be filed on its own.
 */
export function buildDossier(item: EvidenceItem, context: { operator: string }): string {
  const meta = item.metadata;
  const odom = meta?.odometry;
  const detections = meta?.detections ?? [];

  const esc = (value: unknown) =>
    String(value ?? '—').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string);

  const row = (label: string, value: unknown) =>
    `<tr><th>${esc(label)}</th><td>${esc(value)}</td></tr>`;

  const detectionRows = detections
    .map(
      (d) => `<tr>
        <td>${esc(d.class_name)}</td>
        <td class="num">${(d.confidence * 100).toFixed(1)}%</td>
        <td class="num">${d.bbox_xyxy.map((v) => Math.round(v)).join(', ')}</td>
        <td class="num">${d.center_px.map((v) => Number(v).toFixed(0)).join(', ')}</td>
        <td class="num">${esc(d.area_px)}</td>
      </tr>`
    )
    .join('');

  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Dossiê pericial ${esc(item.stamp)}</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 40px;
    font: 14px/1.55 "Helvetica Neue", Arial, sans-serif;
    color: #16191c; background: #fff; max-width: 1000px; margin-inline: auto;
  }
  header { border-bottom: 2px solid #16191c; padding-bottom: 16px; margin-bottom: 28px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: #5c666f; font-size: 13px; }
  h2 { font-size: 13px; margin: 32px 0 10px; color: #5c666f; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e3e1dc; vertical-align: top; }
  th { width: 220px; color: #5c666f; font-weight: 500; }
  td.num, th.num { font-family: "SFMono-Regular", Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }
  .plates { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 10px; }
  figure { margin: 0; }
  figure img { width: 100%; display: block; border: 1px solid #c9c6bf; background: #f4f2ee; }
  figcaption { font-size: 11px; color: #5c666f; margin-top: 6px; }
  footer { margin-top: 44px; padding-top: 14px; border-top: 1px solid #e3e1dc; font-size: 11px; color: #5c666f; }
  @media print { body { padding: 0; } .plates { break-inside: avoid; } }
</style>
</head>
<body>
<header>
  <h1>Dossiê de inspeção aérea pericial</h1>
  <p class="sub">Registro ${esc(item.stamp)} · Parrot Bebop 2 · Estação BMG</p>
</header>

<h2>Registro fotográfico em dupla fidelidade</h2>
<div class="plates">
  <figure>
    <img src="{{RAW_IMAGE}}" alt="Imagem bruta do sensor">
    <figcaption>Imagem bruta, PNG sem perdas, sem qualquer processamento.</figcaption>
  </figure>
  <figure>
    <img src="{{ANNOTATED_IMAGE}}" alt="Imagem anotada pela rede neural">
    <figcaption>Imagem anotada pela rede YOLOv8 no instante da captura.</figcaption>
  </figure>
</div>

<h2>Identificação</h2>
<table>
  ${row('Identificador do registro', item.stamp)}
  ${row('Data e hora local', stampLabel(item.stamp))}
  ${row('Carimbo UTC', meta?.captured_at_utc)}
  ${row('Arquivo bruto', meta?.raw_image)}
  ${row('Arquivo anotado', meta?.annotated_image)}
  ${row('Resolução do quadro', meta?.frame ? `${meta.frame.width} × ${meta.frame.height} px` : undefined)}
  ${row('Inclinação do gimbal', meta?.gimbal_tilt_deg !== undefined ? `${meta.gimbal_tilt_deg.toFixed(1)}°` : undefined)}
  ${row('Modo de operação', meta?.mission?.no_fly === undefined ? undefined : meta.mission.no_fly ? 'Bancada, motores desligados' : 'Voo real')}
  ${row('Tempo decorrido de missão', meta?.mission?.elapsed_sec !== undefined ? `${meta.mission.elapsed_sec.toFixed(1)} s` : undefined)}
</table>

<h2>Posição no instante da captura</h2>
<table>
  ${row('Deslocamento leste da origem', odom?.x_m !== undefined ? `${odom.x_m.toFixed(3)} m` : undefined)}
  ${row('Deslocamento norte da origem', odom?.y_m !== undefined ? `${odom.y_m.toFixed(3)} m` : undefined)}
  ${row('Altitude relativa ao solo', odom?.relative_altitude_m !== undefined ? `${odom.relative_altitude_m.toFixed(3)} m` : undefined)}
  ${row('Altitude bruta do sensor', odom?.raw_altitude_m !== undefined ? `${odom.raw_altitude_m.toFixed(3)} m` : undefined)}
  ${row('Referência de solo calibrada', odom?.ground_reference_m !== undefined ? `${odom.ground_reference_m.toFixed(3)} m` : undefined)}
  ${row('Proa', odom?.yaw_rad !== undefined ? `${((odom.yaw_rad * 180) / Math.PI).toFixed(1)}°` : undefined)}
  ${row('Velocidade', odom?.speed_mps !== undefined ? `${odom.speed_mps.toFixed(3)} m/s` : undefined)}
  ${row('Origem do voo', odom?.launch_origin ? `${(odom.launch_origin.x_m ?? 0).toFixed(2)}, ${(odom.launch_origin.y_m ?? 0).toFixed(2)} m` : undefined)}
</table>

<h2>Detecções registradas</h2>
${
  detections.length === 0
    ? '<p class="sub">A captura foi registrada sem alvo confirmado no quadro.</p>'
    : `<table>
  <thead><tr><th>Classe</th><th class="num">Confiança</th><th class="num">Caixa x1 y1 x2 y2</th><th class="num">Centro</th><th class="num">Área px²</th></tr></thead>
  <tbody>${detectionRows}</tbody>
</table>`
}

<h2>Configuração de visão vigente</h2>
<table>
  ${row('Limiar de confiança', meta?.mission?.confidence_threshold?.toFixed(2))}
  ${row('Classes de interesse', meta?.mission?.target_classes?.join(', '))}
</table>

<footer>
  Documento gerado pela estação BMG em ${esc(new Date().toLocaleString('pt-BR'))} por ${esc(context.operator)}.
  As imagens acima estão embutidas neste arquivo exatamente como gravadas pela aeronave.
</footer>
</body>
</html>`;
}
