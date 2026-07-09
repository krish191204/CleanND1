'use client';

import { useEffect, useState } from 'react';
import { FeedList } from '@/components/FeedList';
import { TopicFeedList } from '@/components/TopicFeedList';
import { Sidebar } from '@/components/Sidebar';
import { ReviewQueueView } from '@/components/ReviewQueueView';
import { MetricsView } from '@/components/MetricsView';
import { ShieldCheck, Newspaper, Inbox, BarChart3 } from 'lucide-react';

type Tab = 'feed-topics' | 'feed-flat' | 'review' | 'metrics';

export default function HomePage() {
  const [tab, setTab] = useState<Tab>('feed-topics');

  return (
    <div className="min-h-screen">
      <header className="border-b border-ink-800 bg-ink-950/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cred-high to-cred-medium flex items-center justify-center">
              <ShieldCheck size={18} className="text-ink-950" />
            </div>
            <div>
              <h1 className="text-base font-semibold text-ink-50">CleanND</h1>
              <p className="text-[10px] text-ink-500 -mt-0.5">cleaned news from X</p>
            </div>
          </div>

          <nav className="ml-6 flex items-center gap-1">
            <TabButton active={tab === 'feed-topics'} onClick={() => setTab('feed-topics')} icon={<Newspaper size={14} />}>Topics</TabButton>
            <TabButton active={tab === 'feed-flat'} onClick={() => setTab('feed-flat')} icon={<Newspaper size={14} />}>Flat</TabButton>
            <TabButton active={tab === 'review'} onClick={() => setTab('review')} icon={<Inbox size={14} />}>Review queue</TabButton>
            <TabButton active={tab === 'metrics'} onClick={() => setTab('metrics')} icon={<BarChart3 size={14} />}>Metrics</TabButton>
          </nav>

          <div className="ml-auto text-xs text-ink-500 hidden sm:block">
            v0.1 · 5-stage pipeline · active learning
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">
        {tab === 'feed-topics' && (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-6">
            <TopicFeedList />
            <Sidebar />
          </div>
        )}
        {tab === 'feed-flat' && (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-6">
            <FeedList />
            <Sidebar />
          </div>
        )}
        {tab === 'review' && (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-6">
            <ReviewQueueView />
            <Sidebar />
          </div>
        )}
        {tab === 'metrics' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <MetricsView />
            <FeedbackPanel />
          </div>
        )}
      </main>
    </div>
  );
}

function TabButton({
  active, onClick, children, icon,
}: { active: boolean; onClick: () => void; children: React.ReactNode; icon: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 rounded-md text-sm font-medium flex items-center gap-1.5 transition ${
        active
          ? 'bg-ink-800 text-ink-50'
          : 'text-ink-400 hover:text-ink-200 hover:bg-ink-900'
      }`}
    >
      {icon} {children}
    </button>
  );
}

function AboutCard() {
  return (
    <div className="card space-y-3 text-sm text-ink-300">
      <h3 className="text-base font-semibold text-ink-100">How CleanND works</h3>
      <p>
        Tweets flow through 5 stages: API filter → text clean → bot detection → relevance/quality →
        credibility. Uncertain items go to a human review queue. Labels are fed back into the bot
        classifier (nightly) so the system improves with use.
      </p>
      <ul className="list-disc list-inside text-xs space-y-1 text-ink-400">
        <li>Stage 1 — API filter: min followers, account age, language, hashtag/URL spam</li>
        <li>Stage 2 — Text clean: lowercase, dedup (MinHash), tokenize, lemmatize</li>
        <li>Stage 3 — Bot detect: RF + heuristics + optional DistilBERT</li>
        <li>Stage 4 — Relevance: sentence-transformers + burst detection</li>
        <li>Stage 5 — Credibility: domain whitelist, source verification, propagation</li>
      </ul>
    </div>
  );
}

function FeedbackPanel() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    const tick = async () => {
      try {
        const r = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/feedback/summary`).then((r) => r.json());
        setData(r);
      } catch {}
    };
    tick();
    const id = setInterval(tick, 8000);
    return () => clearInterval(id);
  }, []);
  if (!data || data.total === 0) {
    return (
      <div className="card">
        <h3 className="text-base font-semibold text-ink-100 flex items-center gap-2">
          <span>👎</span> Feedback
        </h3>
        <p className="text-sm text-ink-400 mt-2">
          Click 👍 or 👎 on any news card to record feedback. Signals are fed into the nightly
          retrain to improve the bot classifier.
        </p>
      </div>
    );
  }
  return (
    <div className="card">
      <h3 className="text-base font-semibold text-ink-100">Feedback signals</h3>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
        <Stat label="👍" value={data.up} accent="text-cred-high" />
        <Stat label="👎" value={data.down} accent="text-cred-low" />
        <Stat label="Total" value={data.total} />
      </dl>
      {data.recent && data.recent.length > 0 && (
        <div className="mt-4">
          <h4 className="text-xs uppercase text-ink-500 tracking-wide mb-2">Recent</h4>
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {data.recent.slice(0, 8).map((f: any) => (
              <div key={f.id} className="text-xs flex items-center gap-2 text-ink-300">
                <span className={f.signal === 'up' ? 'text-cred-high' : 'text-cred-low'}>
                  {f.signal === 'up' ? '👍' : '👎'}
                </span>
                <span className="text-ink-500">tweet {f.tweet_id.slice(0, 10)}…</span>
                <span className="text-ink-500 ml-auto">{new Date(f.created_at).toLocaleTimeString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value?: number; accent?: string }) {
  return (
    <div>
      <dt className="text-ink-500 text-xs">{label}</dt>
      <dd className={`text-ink-100 font-mono text-base ${accent || ''}`}>
        {value !== undefined ? value : '—'}
      </dd>
    </div>
  );
}