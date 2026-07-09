'use client';

import { NewsCard as NewsCardT } from '@/lib/api';
import { CredibilityBadge } from './CredibilityBadge';
import { FeedbackButtons } from './FeedbackButtons';
import { relTime } from '@/lib/utils';
import { ExternalLink, Sparkles, AlertTriangle, MessageCircle } from 'lucide-react';

const WHY_LABEL: Record<string, string> = {
  trending_now: 'Trending now',
  low_bot_probability: 'Likely human',
  verified_account: 'Verified account',
  domain_whitelisted: 'Trusted source',
  co_corroborated_burst: 'Corroborated',
};

// Layer B Addition 1: when a tweet was NOT clustered into any topic,
// surface a 'single source' warning. The dashboard may have hundreds
// of these alongside a few tight clusters — the user needs to know
// the singleton isn't corroborated.
const TYPE_LABEL: Record<string, string> = {
  announcement: 'Announcement',
  opinion: 'Opinion',
  news_report: 'Report',
  analysis: 'Analysis',
};

export function NewsCardItem({
  card,
  feedbackCounts,
}: {
  card: NewsCardT;
  feedbackCounts?: { up: number; down: number };
}) {
  return (
    <article className="card animate-fade-in">
      <div className="flex items-start gap-3">
        <Avatar
          handle={card.handle}
          src={card.profile_image_url}
          display={card.display_name}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-semibold text-ink-100 truncate">
              {card.display_name}
            </span>
            <span className="text-ink-500 truncate">@{card.handle}</span>
            {card.verified && (
              <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-cred-high text-white text-[10px] font-bold">
                ✓
              </span>
            )}
            <span className="text-ink-500">·</span>
            <time className="text-ink-500 text-xs">{relTime(card.timestamp)}</time>
          </div>

          <h2 className="mt-2 text-base font-semibold text-ink-50 leading-snug">
            {card.headline}
          </h2>
          {card.summary && card.summary !== card.headline && (
            <p className="mt-1 text-sm text-ink-300 leading-relaxed line-clamp-3">
              {card.summary}
            </p>
          )}

          {card.media?.length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2">
              {card.media.slice(0, 4).map((m, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={i}
                  src={m}
                  alt=""
                  className="rounded-md border border-ink-800 object-cover w-full h-32"
                />
              ))}
            </div>
          )}

          <div className="mt-3 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <CredibilityBadge
                level={card.credibility_level}
                score={card.credibility_score}
                humanVerified={card.human_verified}
              />
              {card.tweet_type && card.tweet_type !== 'unknown' && (
                <span
                  className="chip bg-ink-800 text-ink-200 border border-ink-600"
                  title={`Heuristic tweet type: ${TYPE_LABEL[card.tweet_type] || card.tweet_type}`}
                >
                  <MessageCircle size={10} />
                  {TYPE_LABEL[card.tweet_type] || card.tweet_type}
                </span>
              )}
              {card.is_clustered === false && (
                <span
                  className="chip bg-cred-low/15 text-cred-low border border-cred-low/40"
                  title="This tweet was not grouped with any other tweet. Treat with caution — single source, no corroboration from the topic clustering pass."
                >
                  <AlertTriangle size={10} />
                  Single source
                </span>
              )}
              {card.why_shown?.map((w) => (
                <span
                  key={w}
                  className="chip bg-ink-800 text-ink-300 border border-ink-700"
                  title={WHY_LABEL[w] || w}
                >
                  <Sparkles size={10} />
                  {WHY_LABEL[w] || w}
                </span>
              ))}
              <FeedbackButtons
                tweetId={card.id}
                initial={feedbackCounts ?? { up: 0, down: 0 }}
              />
            </div>
            <a
              href={card.url || '#'}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-ink-400 hover:text-ink-200 inline-flex items-center gap-1"
            >
              View on X <ExternalLink size={12} />
            </a>
          </div>
        </div>
      </div>
    </article>
  );
}

function Avatar({
  handle,
  src,
  display,
}: {
  handle: string;
  src?: string | null;
  display: string;
}) {
  const initials = (display || handle).slice(0, 2).toUpperCase();
  const hue = hashHue(handle);
  if (src) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={src}
        alt=""
        className="w-10 h-10 rounded-full object-cover border border-ink-700"
      />
    );
  }
  return (
    <div
      className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold text-ink-900 border border-ink-700 shrink-0"
      style={{ background: `hsl(${hue}, 70%, 70%)` }}
    >
      {initials}
    </div>
  );
}

function hashHue(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) >>> 0;
  }
  return h % 360;
}