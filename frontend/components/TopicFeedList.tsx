'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { Newspaper, RefreshCw, Loader2, AlertTriangle, Database } from 'lucide-react';
import { api, TopicListResponse } from '@/lib/api';
import { CredibilityBadge } from './CredibilityBadge';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

/**
 * Topic-only feed (Layer B Addition 6, simplified) — the dashboard's
 * "Topics" tab. One card per topic (the topic's label + anchor tweet
 * preview + credibility badge + size). No inline cluster expansion, no
 * drilldown. Singletons (topics of 1 tweet) are filtered out at the
 * API layer — they're surfaced in the "Flat" tab instead, flagged
 * "Single source" by the news card component.
 */
export function TopicFeedList() {
  const { data, error, isLoading, mutate } = useSWR<TopicListResponse>(
    `/api/topics?limit=50`,
    fetcher,
    { refreshInterval: 15000 },
  );

  const ingest = async (n: number, seed: number) => {
    await api.ingestMock(n, seed);
    mutate();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap card">
        <Newspaper size={14} className="text-ink-400" />
        <span className="text-xs text-ink-400">Topics:</span>
        <span className="text-xs text-ink-500">
          {data?.items?.length ?? 0} topic{data?.items?.length === 1 ? '' : 's'}
        </span>
        <button
          onClick={() => mutate()}
          className="btn btn-ghost text-xs"
          title="Refresh"
        >
          <RefreshCw size={12} /> Refresh
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => ingest(20, 42)} className="btn btn-ghost text-xs">
            <Database size={12} /> +20 mock
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="card text-ink-400 text-sm flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" /> Loading topics…
        </div>
      )}

      {error && (
        <div className="card border-cred-low/40 bg-cred-low/10 text-cred-low text-sm flex items-start gap-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold">Could not load topics</p>
            <p className="text-xs opacity-80">
              Make sure the backend is running on {process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}.
            </p>
          </div>
        </div>
      )}

      {!isLoading && data && data.items.length === 0 && (
        <div className="card text-center py-12">
          <p className="text-ink-300 font-medium">No topics yet.</p>
          <p className="text-ink-500 text-sm mt-1">
            Click <span className="text-cred-high">+20 mock</span> to inject sample tweets — they&apos;ll
            be clustered by the topic grouper.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {data?.items.map((topic) => {
          const anchor = topic.anchor;
          const breakdown = topic.tweet_type_breakdown || {};
          const breakdownChips = Object.entries(breakdown)
            .filter(([, n]) => (n as number) > 0)
            .sort((a, b) => (b[1] as number) - (a[1] as number))
            .map(([k, n]) => `${n} ${k.replace('_', ' ')}`)
            .slice(0, 4);
          return (
            <article key={topic.id} className="card">
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-3 mb-1">
                    <h3 className="font-semibold text-ink-100 truncate">
                      {topic.label || '(unlabeled cluster)'}
                    </h3>
                    <span className="text-xs text-ink-500 shrink-0">
                      {topic.tweet_count} tweet{topic.tweet_count === 1 ? '' : 's'}
                    </span>
                  </div>
                  {anchor && (
                    <div className="text-sm text-ink-300 mt-1">
                      <span className="text-ink-500 mr-1">@{anchor.handle}:</span>
                      {anchor.headline?.slice(0, 120) || anchor.summary?.slice(0, 120)}
                    </div>
                  )}
                  {breakdownChips.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {breakdownChips.map((c, i) => (
                        <span
                          key={i}
                          className="text-xs px-1.5 py-0.5 rounded bg-ink-800 text-ink-300"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                {anchor && (
                  <div className="shrink-0 w-24 flex flex-col items-end gap-1">
                    <CredibilityBadge level={anchor.credibility_level} />
                    {anchor.tweet_type && anchor.tweet_type !== 'unknown' && (
                      <span className="text-xs text-ink-500 capitalize">
                        {anchor.tweet_type.replace('_', ' ')}
                      </span>
                    )}
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
