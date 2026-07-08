import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'CleanND — Clean News Dashboard',
  description: 'Real-time news from X/Twitter, cleaned of bots and spam.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}