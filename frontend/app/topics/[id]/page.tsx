'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ArrowLeft, Loader2, AlertTriangle, Filter } from 'lucide-react';
import { api, TopicDetailResponse, TweetType } from '@/lib/api';
import { NewsCardItem } from '@/components/NewsCard';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const TYPE_FILTERS: { label: string; value: TweetType | 'all' }[] = [
  { label: 'All',         value: 'all' },
  { label: 'Announcement', value: 'announcement' },
  { label: 'Opinion',      value: 'opinion' },
  { label: 'Reports',      value: 'news_report' },
  { label: 'Analysis',     value: 'analysis' },
];

/**
 * TopicDetailView — the page that shows when you click a topic card on
 * the Feed tab. Anchor tweet at the top, then a filter bar (All /
 * Announcements / Opinions / Reports / Analysis), then the remaining
 * tweets in the cluster sorted by final_score desc.
 */
export default function TopicDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id as string;
  const [typeFilter, setTypeFilter] = useState<TweetType | 'all'>('all');
  const { data, error, isLoading, mutate } = useSWR<TopicDetailResponse>(
    id ? `/api/topics/${id}/tweets${typeFilter !== 'all' ? `?tweet_type=${typeFilter}` : ''}` : null,
    fetcher,
    { refreshInterval: 30000 },
  );

  return (
    <div className="space-y-4">
      <Link href="/" className="btn btn-ghost text-xs">
        <ArrowLeft size={12} /> Back to feed
      </Link>

      {isLoading && (
        <div className="card text-ink-400 text-sm flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" /> Loading topic…
        </div>
      )}

      {error && (
        <div className="card border-cred-low/40 bg-cred-low/10 text-cred-low text-sm flex items-start gap-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold">Could not load topic</p>
            <p className="text-xs opacity-80">Topic id={id}.</p>
          </div>
        </div>
      )}

      {data && (
        <>
          {/* Header: topic label + breakdown */}
          <div className="card">
            <div className="flex items-baseline gap-3">
              <h1 className="text-xl font-semibold text-ink-100">
                {data.topic.label || '(unlabeled cluster)'}
              </h1>
              <span className="text-sm text-ink-500">
                {data.topic.tweet_count} tweet{data.topic.tweet_count === 1 ? '' : 's'}
              </span>
            </div>
          </div>

          {/* Anchor tweet first */}
          {data.topic.anchor && data.tweets[0]?.id === data.topic.anchor.id && (
            <NewsCardItem
              key={data.topic.anchor.id}
              card={data.topic.anchor}
              feedbackCounts={undefined}
            />
          )}
          {/* If the anchor isn't in the filtered list, show it at the top separately */}
          {data.topic.anchor &&
            data.tweets[0]?.id !== data.topic.anchor.id && (
              <div>
                <div className="text-xs text-ink-500 mb-1">Anchor</div>
                <NewsCardItem
                  key={data.topic.anchor.id}
                  card={data.topic.anchor}
                  feedbackCounts={undefined}
                />
              </div>
            )}

          {/* Filter bar */}
          <div className="flex items-center gap-2 flex-wrap card">
            <Filter size={14} className="text-ink-400" />
            <span className="text-xs text-ink-400">Filter by type:</span>
            {TYPE_FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => setTypeFilter(f.value)}
                className={`btn text-xs ${
                  typeFilter === f.value
                    ? 'btn-primary'
                    : 'btn-ghost'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {/* Remaining tweets */}
          <div className="space-y-3">
            {data.tweets
              .filter((t) => t.id !== data.topic.anchor?.id)
              .map((card) => (
                <NewsCardItem key={card.id} card={card} feedbackCounts={undefined} />
              ))}
          </div>
        </>
      )}
    </div>
  );
}
