import type { MilestoneMessage } from './milestones.cjs';

export declare function deferBenchSpawn(
  countdownSec: number,
  hooks: { emit: (message: MilestoneMessage) => void; spawn: () => void }
): () => boolean;
