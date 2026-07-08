'use client';

import { useEffect, useState } from 'react';
import { api, PipelineStats } from '@/lib/api';
import { TrendingUp, Database, Filter, ThumbsUp, ThumbsDown } from 'lucide-react';

interface TopicStat { topic: string; count: number }

const BEATS = [
  { id: 'breaking', label: 'Breaking', emoji: '🔴' },
  { id: 'world', label: 'World', emoji: '🌍' },
  { id: 'tech', label: 'Tech', emoji: '💻' },
  { id: 'finance', label: 'Markets', emoji: '📈' },
  { id: 'science', label: 'Science', emoji: '🔬' },
];

interface FeedbackSummary {
  total: number;
  up: number;
  down: number;
  recent?: any[];
}

export function Sidebar({ onPick }: { onPick?: (handle: string) => void }) {
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [topics, setTopics] = useState<TopicStat[]>([]);
  const [feedback, setFeedback] = useState<FeedbackSummary | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.stats();
        if (alive) setStats(s);
        const r = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/feedback/summary`).then((r) => r.json());
        if (alive) setFeedback(r);
      } catch {}
    };
    tick();
    const id = setInterval(tick, 10000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  return (
    <aside className="space-y-4">
      <div className="card">
        <h3 className="text-sm font-semibold text-ink-100 flex items-center gap-2">
          <Database size={14} /> Pipeline
        </h3>
        <dl className="mt-3 grid grid-cols-2 gap-y-2 gap-x-3 text-sm">
          <Stat label="Surfaced" value={stats?.surfaced} accent="text-cred-high" />
          <Stat label="Ingested" value={stats?.ingested} />
          <Stat label="In review" value={stats?.in_review_queue} accent="text-cred-medium" />
          <Stat label="Approved" value={undefined} subtitle="(see /review)" />
        </dl>
      </div>

      {feedback && feedback.total > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-ink-100 flex items-center gap-2">
            <ThumbsUp size={14} /> Your feedback
          </h3>
          <dl className="mt-3 grid grid-cols-3 gap-y-2 gap-x-3 text-sm">
            <Stat label="👍" value={feedback.up} accent="text-cred-high" />
            <Stat label="👎" value={feedback.down} accent="text-cred-low" />
            <Stat label="Total" value={feedback.total} />
          </dl>
          <p className="text-xs text-ink-500 mt-2">
            Retraining will use these signals to improve the bot classifier.
          </p>
        </div>
      )}

      <div className="card">
        <h3 className="text-sm font-semibold text-ink-100 flex items-center gap-2">
          <Filter size={14} /> Beats
        </h3>
        <ul className="mt-2 space-y-1">
          {BEATS.map((b) => (
            <li key={b.id}>
              <button
                onClick={() => onPick?.(b.id)}
                className="w-full text-left px-2 py-1.5 rounded-md text-sm text-ink-200 hover:bg-ink-800 flex items-center gap-2"
                title={`Ingest for ${b.id} beat`}
              >
                <span>{b.emoji}</span> {b.label}
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h3 className="text-sm font-semibold text-ink-100 flex items-center gap-2">
          <TrendingUp size={14} /> Why this is clean
        </h3>
        <ul className="mt-2 text-xs text-ink-300 space-y-1.5">
          <li>5-stage pipeline filters noise</li>
          <li>Bot detection: heuristic + ML ensemble</li>
          <li>Source credibility tiers (color-coded)</li>
          <li>Human-in-the-loop review queue</li>
          <li>👎 feedback retrains the bot model</li>
        </ul>
      </div>
    </aside>
  );
}

function Stat({ label, value, accent, subtitle }: { label: string; value?: number; accent?: string; subtitle?: string }) {
  return (
    <div>
      <dt className="text-ink-500 text-xs">{label}</dt>
      <dd className={`text-ink-100 font-mono text-base ${accent || ''}`}>
        {value !== undefined ? value : '—'}
        {subtitle && <span className="text-ink-500 text-xs ml-1">{subtitle}</span>}
      </dd>
    </div>
  );
}