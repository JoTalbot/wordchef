import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Word Chef — готовь слова, подавай блюда',
  description:
    'Мультиплеерная игра-кухня на словах: собирай блюда из букв, лови комбо, держи жар и рискуй с приправой.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  themeColor: '#14080a',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
