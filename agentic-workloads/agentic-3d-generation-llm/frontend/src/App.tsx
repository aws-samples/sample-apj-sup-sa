import '@google/model-viewer';
import { useEffect, useState } from 'react';
import { listModels } from './api';
import { GalleryDetailPage } from './pages/GalleryDetailPage';
import { GalleryPage } from './pages/GalleryPage';
import { GeneratePage } from './pages/GeneratePage';
import { HowItWorksPage } from './pages/HowItWorksPage';
import { ImageTo3DPage } from './pages/ImageTo3DPage';
import { Link, usePath } from './router';
import type { Model } from './types';

const NAV = [
  { href: '/', label: 'Generate', match: (p: string) => p === '/' },
  { href: '/image-to-3d', label: 'Image to 3D', match: (p: string) => p === '/image-to-3d' },
  { href: '/gallery', label: 'Gallery', match: (p: string) => p === '/gallery' || p.startsWith('/gallery/') },
  { href: '/how-it-works', label: 'How it Works', match: (p: string) => p === '/how-it-works' },
];

function Page({ path, models }: { path: string; models: Model[] }) {
  const detail = path.match(/^\/gallery\/([0-9a-f-]{36})\/?$/i);
  if (detail) return <GalleryDetailPage jobId={detail[1]} models={models} />;
  if (path === '/gallery' || path === '/gallery/') return <GalleryPage />;
  if (path === '/how-it-works') return <HowItWorksPage />;
  if (path === '/image-to-3d') return <ImageTo3DPage models={models} />;
  if (path === '/') return <GeneratePage models={models} />;
  return (
    <p className="muted">
      Page not found. <Link href="/">Go to Generate</Link>.
    </p>
  );
}

export default function App() {
  const path = usePath();
  const [models, setModels] = useState<Model[]>([]);

  useEffect(() => {
    listModels().then(setModels).catch(() => undefined);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>3D Generation with LLMs</h1>
          <p className="muted">One prompt or photo, four models. Each writes a Blender script on Amazon Bedrock AgentCore, and Blender turns it into a GLB.</p>
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={n.match(path) ? 'active' : undefined} aria-current={n.match(path) ? 'page' : undefined}>
              {n.label}
            </Link>
          ))}
          {/* A full page load, not an app route: the CloudFront sign-in gate handles /signout. */}
          <a className="signout" href="/signout">Sign out</a>
        </nav>
      </header>
      <main>
        <Page path={path} models={models} />
      </main>
    </div>
  );
}
