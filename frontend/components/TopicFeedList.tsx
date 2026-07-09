'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { Newspaper, RefreshCw, Loader2, AlertTriangle, Database } from 'lucide-react';
import { api, TopicListResponse } from '@/lib/api';
import { CredibilityBadge } from './CredibilityBadge';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

/**
 * Topic-grouped feed (Layer B Addition 6) — replaces the flat FeedList
 * in the dashboard's "Feed" tab. Each topic card shows:
 *   - TF-IDF label (top 3 terms joined by ' · ')
 *   - tweet count
 *   - anchor tweet preview (avatar + first 120 chars + credibility badge)
 *   - tweet_type breakdown chips ("3 Announcements · 7 Opinions · 2 Reports")
 *
 * Clicking a card navigates to /topics/[id] for the detail view.
 */
export function TopicFeedList() {
  const [minLevel, setMinLevel] = useState<'high' | 'medium' | 'low'>('medium');
  const { data, error, isLoading, mutate } = useSWR<TopicListResponse>(
    `/api/topics?limit=50`,
    fetcher,
    { refreshInterval: 15000 },
  );

  const [feedback, setFeedback] = useState<Record<string, { up: number; down: number }>>({});
  useEffect(() => {
    if (!data?.items?.length) return;
    // Aggregate feedback isn't keyed on topics yet — only fetch for
    // anchor cards in the visible topics.
    const anchorIds = data.items
      .map((t) => t.anchor_tweet_id)
      .filter((id): id is string => !!id);
    if (anchorIds.length === 0) return;
    fetch(
      `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/feedback/aggregates?tweet_ids=${encodeURIComponent(anchorIds.join(','))}`,
    )
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
        <Newspaper size={14} className="text-ink-400" />
        <span className="text-xs text-ink-400">Topics:</span>
        <span className="text-xs text-ink-500">
          {data?.items?.length ?? 0} cluster{data?.items?.length === 1 ? '' : 's'}
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
            <Database size={12} /> +20 mock (will cluster)
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
            Click <span className="text-cred-high">+20 mock</span> to inject sample tweets — they'll be
            clustered by the topic grouper.
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
            <Link key={topic.id} href={`/topics/${topic.id}`} className="block">
              <article className="card hover:border-cred-medium/40 transition-colors">
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
            </Link>
          );
        })}
      </div>
    </div>
  );
}
