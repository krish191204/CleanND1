'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { api, MLMetricsResponse } from '@/lib/api';
import { LineChart, Activity } from 'lucide-react';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function MetricsView() {
  const { data, error, isLoading } = useSWR<MLMetricsResponse>(
    '/api/ml/metrics',
    fetcher,
    { refreshInterval: 15000 }
  );

  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="text-sm font-semibold text-ink-100 flex items-center gap-2">
          <Activity size={14} /> Model metrics over time
        </h3>
        {isLoading && <p className="text-xs text-ink-400 mt-2">Loading…</p>}
        {error && <p className="text-xs text-cred-low mt-2">Could not load metrics.</p>}
        {data && <MetricsTable metrics={data} />}
      </div>
    </div>
  );
}

function MetricsTable({ metrics }: { metrics: MLMetricsResponse }) {
  const bot = metrics.bot_classifier || [];
  const cred = metrics.credibility || [];

  if (bot.length === 0 && cred.length === 0) {
    return (
      <p className="text-xs text-ink-400 mt-2">
        No metrics yet. Label some reviews and trigger a retrain.
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-4">
      <MetricSection name="Bot classifier" rows={bot} />
      <MetricSection name="Credibility" rows={cred} />
    </div>
  );
}

function MetricSection({ name, rows }: { name: string; rows: any[] }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h4 className="text-xs text-ink-500 uppercase tracking-wide mb-1">{name}</h4>
      <div className="space-y-1">
        {rows.map((r, i) => (
          <div
            key={i}
            className="flex items-center justify-between text-xs font-mono bg-ink-800 px-2 py-1 rounded"
          >
            <span className="text-ink-300">
              {r.metric} <span className="text-ink-500">v{r.version}</span>
            </span>
            <span className="text-ink-100">{(r.value * 100).toFixed(1)}%</span>
            <span className="text-ink-500">n={r.sample_size}</span>
          </div>
        ))}
      </div>
    </div>
  );
}