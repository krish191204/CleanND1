'use client';

import { CredibilityLevel } from '@/lib/api';
import { CREDIBILITY_COLORS, CREDIBILITY_LABEL } from '@/lib/utils';

export function CredibilityBadge({
  level,
  score,
  humanVerified,
}: {
  level: CredibilityLevel;
  score?: number;
  humanVerified?: boolean;
}) {
  return (
    <span
      className={`badge-cred-${level} chip`}
      title={`Credibility: ${level}${score !== undefined ? ` (${(score * 100).toFixed(0)}%)` : ''}`}
    >
      <span
        className="inline-block w-1.5 h-1.5 rounded-full"
        style={{ background: CREDIBILITY_COLORS[level] }}
      />
      {CREDIBILITY_LABEL[level]}
      {score !== undefined && (
        <span className="opacity-70 ml-1">{(score * 100).toFixed(0)}%</span>
      )}
      {humanVerified && (
        <span className="ml-1 text-cred-high">✓ verified</span>
      )}
    </span>
  );
}