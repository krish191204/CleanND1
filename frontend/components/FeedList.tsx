'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { api, CardListResponse } from '@/lib/api';
import { NewsCardItem } from './NewsCard';
import { Filter, RefreshCw, Loader2, AlertTriangle, Database } from 'lucide-react';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function FeedList() {
  const [minLevel, setMinLevel] = useState<'high' | 'medium' | 'low' | 'unverified'>('high');
  const [handle, setHandle] = useState('');
  const { data, error, isLoading, mutate } = useSWR<CardListResponse>(
    `/api/feed?limit=50&min_level=${minLevel}${handle ? `&handle=${encodeURIComponent(handle)}` : ''}`,
    fetcher,
    { refreshInterval: 10000 }
  );

  const [feedback, setFeedback] = useState<Record<string, { up: number; down: number }>>({});
  useEffect(() => {
    if (!data?.items?.length) return;
    const ids = data.items.map((c) => c.id).join(',');
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/feedback/aggregates?tweet_ids=${encodeURIComponent(ids)}`)
      .then((r) => r.json())
      .then((map) => setFeedback(map || {}))
      .catch(() => {});
  }, [data]);

  const ingest = async (n: number, seed: number) => {
    await api.ingestMock(n, seed);
    mutate();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap card">
        <Filter size={14} className="text-ink-400" />
        <span className="text-xs text-ink-400">Show:</span>
        <select
          value={minLevel}
          onChange={(e) => setMinLevel(e.target.value as any)}
          className="bg-ink-800 border border-ink-700 text-ink-100 text-sm rounded-md px-2 py-1"
        >
          <option value="high">High credibility only (default)</option>
          <option value="medium">Medium + High</option>
          <option value="low">Low + Medium + High</option>
          <option value="unverified">All passed items</option>
        </select>
        <input
          type="text"
          placeholder="@handle filter"
          value={handle}
          onChange={(e) => setHandle(e.target.value)}
          className="bg-ink-800 border border-ink-700 text-ink-100 text-sm rounded-md px-2 py-1 w-40"
        />
        <button
          onClick={() => mutate()}
          className="btn btn-ghost text-xs"
          title="Refresh"
        >
          <RefreshCw size={12} /> Refresh
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => ingest(15, 42)} className="btn btn-ghost text-xs">
            <Database size={12} /> +15 mock
          </button>
          <button onClick={() => ingest(30, Math.floor(Math.random() * 1000))} className="btn btn-ghost text-xs">
            <Database size={12} /> +30 random
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="card text-ink-400 text-sm flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" /> Loading feed…
        </div>
      )}

      {error && (
        <div className="card border-cred-low/40 bg-cred-low/10 text-cred-low text-sm flex items-start gap-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold">Could not load feed</p>
            <p className="text-xs opacity-80">Make sure the backend is running on {process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}.</p>
          </div>
        </div>
      )}

      {!isLoading && data && data.items.length === 0 && (
        <div className="card text-center py-12">
          <p className="text-ink-300 font-medium">No news surfaced yet.</p>
          <p className="text-ink-500 text-sm mt-1">Click <span className="text-cred-high">+15 mock</span> to inject sample tweets.</p>
        </div>
      )}

      <div className="space-y-3">
        {data?.items.map((c) => (
          <NewsCardItem key={c.id} card={c} feedbackCounts={feedback[c.id]} />
        ))}
      </div>
    </div>
  );
}