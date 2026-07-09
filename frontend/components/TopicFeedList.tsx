'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { Newspaper, RefreshCw, Loader2, AlertTriangle, Database, ChevronDown, ChevronUp, Filter } from 'lucide-react';
import { api, TopicListResponse, TopicDetailResponse, TweetType } from '@/lib/api';
import { CredibilityBadge } from './CredibilityBadge';
import { NewsCardItem } from './NewsCard';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

/**
 * Topic-grouped feed (Layer B Addition 6) — the dashboard's "Topics" tab.
 * One card per topic (label + anchor + size). Clicking a card expands
 * an inline drilldown of the cluster's tweets (filtered by tweet_type).
 * The drilldown is a real cluster, not just topic cards — the dashboard
 * surfaces the actual corroborating tweets so you can see the evidence
 * behind each topic.
 */
export function TopicFeedList() {
  const { data, error, isLoading, mutate } = useSWR<TopicListResponse>(
    `/api/topics?limit=50`,
    fetcher,
    { refreshInterval: 15000 },
  );

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<TweetType | 'all'>('all');
  const [expandedData, setExpandedData] = useState<TopicDetailResponse | null>(null);
  const [expandedLoading, setExpandedLoading] = useState(false);

  const handleToggle = async (topicId: string) => {
    if (expandedId === topicId) {
      setExpandedId(null);
      setExpandedData(null);
      return;
    }
    setExpandedId(topicId);
    setExpandedLoading(true);
    setTypeFilter('all');
    try {
      const data = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/topics/${topicId}/tweets`,
      ).then((r) => r.json());
      setExpandedData(data);
    } finally {
      setExpandedLoading(false);
    }
  };

  const handleTypeFilter = async (tf: TweetType | 'all') => {
    if (!expandedId) return;
    setTypeFilter(tf);
    setExpandedLoading(true);
    try {
      const data = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/topics/${expandedId}/tweets${tf !== 'all' ? `?tweet_type=${tf}` : ''}`,
      ).then((r) => r.json());
      setExpandedData(data);
    } finally {
      setExpandedLoading(false);
    }
  };

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
          const isExpanded = expandedId === topic.id;
          return (
            <article key={topic.id} className="card">
              <button
                onClick={() => handleToggle(topic.id)}
                className="w-full text-left"
              >
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
                  <div className="shrink-0 self-center">
                    {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </div>
                </div>
              </button>

              {isExpanded && (
                <div className="mt-4 pt-4 border-t border-ink-800">
                  {expandedLoading && (
                    <div className="text-ink-400 text-sm flex items-center gap-2">
                      <Loader2 size={14} className="animate-spin" /> Loading cluster tweets…
                    </div>
                  )}
                  {!expandedLoading && expandedData && (
                    <ClusterDrilldown
                      topicLabel={expandedData.topic.label || '(unlabeled)'}
                      tweets={expandedData.tweets}
                      typeFilter={typeFilter}
                      onTypeFilter={handleTypeFilter}
                    />
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}

const TYPE_FILTERS: { label: string; value: TweetType | 'all' }[] = [
  { label: 'All',          value: 'all' },
  { label: 'Announcement', value: 'announcement' },
  { label: 'Opinion',      value: 'opinion' },
  { label: 'Reports',      value: 'news_report' },
  { label: 'Analysis',     value: 'analysis' },
];

function ClusterDrilldown({
  topicLabel, tweets, typeFilter, onTypeFilter,
}: {
  topicLabel: string;
  tweets: any[];
  typeFilter: TweetType | 'all';
  onTypeFilter: (tf: TweetType | 'all') => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Filter size={14} className="text-ink-400" />
        <span className="text-xs text-ink-400">Cluster · {topicLabel} · filter:</span>
        {TYPE_FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => onTypeFilter(f.value)}
            className={`btn text-xs ${
              typeFilter === f.value ? 'btn-primary' : 'btn-ghost'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="space-y-2">
        {tweets.length === 0 && (
          <div className="text-ink-500 text-sm italic">No tweets in this cluster.</div>
        )}
        {tweets.map((card) => (
          <NewsCardItem key={card.id} card={card} feedbackCounts={undefined} />
        ))}
      </div>
    </div>
  );
}
