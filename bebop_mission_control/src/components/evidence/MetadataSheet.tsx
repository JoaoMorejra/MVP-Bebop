import React from 'react';
import type { EvidenceItem } from '../../types/bmg';
import { stampLabel } from '../../lib/format';

interface MetadataSheetProps {
  item: EvidenceItem;
}

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-baseline justify-between gap-4 border-b border-strut-soft py-2 last:border-b-0">
    <span className="shrink-0 text-2xs text-haze-deep">{label}</span>
    <span className="tnum min-w-0 text-right font-mono text-xs text-frost">{children}</span>
  </div>
);

/**
 * The forensic sidecar, read back as written.
 *
 * Every figure here comes from `accident_metadata_*.json`, produced by the
 * mission at the moment of capture. Nothing is recomputed in the interface —
 * the chain of custody runs from the airframe to this sheet.
 */
export const MetadataSheet: React.FC<MetadataSheetProps> = ({ item }) => {
  const meta = item.metadata;
  const odom = meta?.odometry;
  const detections = meta?.detections ?? [];

  return (
    <div className="scroll-thin h-full overflow-y-auto px-3 pb-3">
      <section className="pt-1">
        <h3 className="py-2 text-2xs text-haze">Registro</h3>
        <Row label="Identificador">{item.stamp}</Row>
        <Row label="Capturada em">{stampLabel(item.stamp)}</Row>
        {meta?.captured_at_utc ? (
          <Row label="UTC">{meta.captured_at_utc.replace(/\.\d+/, '').replace('T', ' ')}</Row>
        ) : null}
        {meta?.frame ? (
          <Row label="Quadro">{`${meta.frame.width} × ${meta.frame.height} px`}</Row>
        ) : null}
        {meta?.gimbal_tilt_deg !== undefined ? (
          <Row label="Inclinação do gimbal">{`${meta.gimbal_tilt_deg.toFixed(1)}°`}</Row>
        ) : null}
        {meta?.mission?.elapsed_sec !== undefined ? (
          <Row label="Tempo de missão">{`${meta.mission.elapsed_sec.toFixed(1)} s`}</Row>
        ) : null}
        {meta?.mission?.no_fly !== undefined ? (
          <Row label="Modo">{meta.mission.no_fly ? 'Bancada' : 'Voo real'}</Row>
        ) : null}
      </section>

      {odom ? (
        <section>
          <h3 className="py-2 pt-4 text-2xs text-haze">Posição no instante da captura</h3>
          {odom.x_m !== undefined ? <Row label="Leste da origem">{`${odom.x_m.toFixed(3)} m`}</Row> : null}
          {odom.y_m !== undefined ? <Row label="Norte da origem">{`${odom.y_m.toFixed(3)} m`}</Row> : null}
          {odom.relative_altitude_m !== undefined ? (
            <Row label="Altitude relativa">{`${odom.relative_altitude_m.toFixed(3)} m`}</Row>
          ) : null}
          {odom.raw_altitude_m !== undefined ? (
            <Row label="Altitude bruta">{`${odom.raw_altitude_m.toFixed(3)} m`}</Row>
          ) : null}
          {odom.ground_reference_m !== undefined ? (
            <Row label="Referência de solo">{`${odom.ground_reference_m.toFixed(3)} m`}</Row>
          ) : null}
          {odom.yaw_rad !== undefined ? (
            <Row label="Proa">{`${((odom.yaw_rad * 180) / Math.PI).toFixed(1)}°`}</Row>
          ) : null}
          {odom.speed_mps !== undefined ? (
            <Row label="Velocidade">{`${odom.speed_mps.toFixed(3)} m/s`}</Row>
          ) : null}
          {odom.launch_origin ? (
            <Row label="Origem do voo">
              {`${(odom.launch_origin.x_m ?? 0).toFixed(2)}, ${(odom.launch_origin.y_m ?? 0).toFixed(2)} m`}
            </Row>
          ) : null}
        </section>
      ) : null}

      <section>
        <h3 className="py-2 pt-4 text-2xs text-haze">
          Detecções {detections.length > 0 ? `(${detections.length})` : ''}
        </h3>
        {detections.length === 0 ? (
          <p className="py-2 text-2xs leading-relaxed text-haze-deep">
            {meta
              ? 'A captura foi registrada sem alvo confirmado no quadro.'
              : 'Sem arquivo de metadados. Ative o registro pericial nos parâmetros de inspeção.'}
          </p>
        ) : (
          <ul className="flex flex-col gap-2 py-1">
            {detections.map((d, i) => (
              <li key={`${d.class_name}-${i}`} className="border border-strut-soft p-2">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-frost">{d.class_name}</span>
                  <span className="tnum font-mono text-xs text-mint">
                    {(d.confidence * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="tnum mt-1 font-mono text-[10px] leading-relaxed text-haze-deep">
                  caixa [{d.bbox_xyxy.map((v) => Math.round(v)).join(', ')}]
                  <br />
                  centro [{d.center_px.map((v) => Number(v).toFixed(0)).join(', ')}] · área{' '}
                  {d.area_px} px²
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {meta?.mission ? (
        <section>
          <h3 className="py-2 pt-4 text-2xs text-haze">Configuração vigente</h3>
          {meta.mission.confidence_threshold !== undefined ? (
            <Row label="Limiar de confiança">{meta.mission.confidence_threshold.toFixed(2)}</Row>
          ) : null}
          {meta.mission.target_classes ? (
            <Row label="Classes de interesse">{meta.mission.target_classes.join(', ')}</Row>
          ) : null}
        </section>
      ) : null}
    </div>
  );
};
