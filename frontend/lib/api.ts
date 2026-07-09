// Shared API client + types for the dashboard.

export type CredibilityLevel = 'high' | 'medium' | 'low' | 'unverified';
export type TweetType = 'announcement' | 'opinion' | 'news_report' | 'analysis' | 'unknown';

export interface NewsCard {
  id: string;
  headline: string;
  summary: string;
  handle: string;
  display_name: string;
  profile_image_url?: string | null;
  verified: boolean;
  timestamp: string;
  media: string[];
  credibility_level: CredibilityLevel;
  credibility_score: number;
  human_verified: boolean;
  why_shown: string[];
  url: string;
  tweet_type: TweetType;
  topic_id?: string | null;
  is_clustered: boolean;
}

export interface CardListResponse {
  items: NewsCard[];
  next_cursor: number | null;
  total: number;
}

export interface TopicSummary {
  id: string;
  label: string;
  anchor_tweet_id: string;
  anchor: NewsCard | null;
  tweet_count: number;
  first_seen_at: string;
  last_activity_at: string;
  tweet_type_breakdown: Record<string, number>;
}

export interface TopicListResponse {
  items: TopicSummary[];
  next_cursor: number | null;
  total: number;
}

export interface TopicDetailResponse {
  topic: TopicSummary;
  tweets: NewsCard[];
}

export interface ReviewItem {
  id: string;
  tweet_id: string;
  snapshot: any;
  model_bot_score: number;
  model_credibility: number;
  model_relevance: number;
  uncertainty_margin: number;
  label: string | null;
  category: string | null;
  notes: string | null;
  labeler_id: string | null;
  labeled_at: string | null;
  created_at: string | null;
}

export interface ReviewQueueResponse {
  items: ReviewItem[];
  stats: {
    total: number;
    labeled: number;
    unlabeled: number;
    approved: number;
    rejected: number;
  };
}

export interface PipelineStats {
  ingested: number;
  passed_api_filter: number;
  passed_cleaning: number;
  passed_bot_filter: number;
  passed_relevance: number;
  passed_credibility: number;
  surfaced: number;
  in_review_queue: number;
  last_run_at: string | null;
}

export interface MLMetricsResponse {
  bot_classifier: Array<{
    metric: string;
    value: number;
    recorded_at: string;
    version: string;
    sample_size: number;
  }>;
  credibility: Array<{
    metric: string;
    value: number;
    recorded_at: string;
    version: string;
    sample_size: number;
  }>;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText} — ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetchJSON<{ status: string; time: string; env: string }>('/api/health'),
  feed: (params: { limit?: number; min_credibility?: number; handle?: string } = {}) => {
    const qs = new URLSearchParams();
    qs.set('limit', String(params.limit ?? 50));
    if (params.min_credibility !== undefined) qs.set('min_credibility', String(params.min_credibility));
    if (params.handle) qs.set('handle', params.handle);
    return fetchJSON<CardListResponse>(`/api/feed?${qs.toString()}`);
  },
  topics: (limit = 50) => fetchJSON<TopicListResponse>(`/api/topics?limit=${limit}`),
  topicDetail: (id: string, tweetType?: string) => {
    const qs = new URLSearchParams();
    if (tweetType) qs.set('tweet_type', tweetType);
    return fetchJSON<TopicDetailResponse>(`/api/topics/${id}/tweets?${qs.toString()}`);
  },
  stats: () => fetchJSON<PipelineStats>('/api/stats'),
  reviewQueue: (limit = 25) => fetchJSON<ReviewQueueResponse>(`/api/review/queue?limit=${limit}`),
  labelReview: (id: string, body: { label: string; category?: string; notes?: string; labeler_id?: string }) =>
    fetchJSON<{ status: string; review_id: string; label: string }>(
      `/api/review/${id}/label`,
      { method: 'POST', body: JSON.stringify(body) }
    ),
  ingestMock: (n = 30, seed = 42) =>
    fetchJSON<any>(`/api/ingest/mock?n=${n}&seed=${seed}`, { method: 'POST' }),
  ingest: (body: { beat?: string; query?: string; max_results?: number }) =>
    fetchJSON<any>(`/api/ingest`, { method: 'POST', body: JSON.stringify(body) }),
  retrain: () => fetchJSON<{ status: string }>(`/api/ml/retrain`, { method: 'POST' }),
  mlMetrics: () => fetchJSON<MLMetricsResponse>(`/api/ml/metrics`),
};