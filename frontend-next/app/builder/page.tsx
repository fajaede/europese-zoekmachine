// app/builder/page.tsx
'use client';

import { useState } from 'react';
import Link from 'next/link';

type SiteBrief = {
  target_audience?: string;
  keywords?: string[];
  core_value?: string;
};

type BuilderResponse = {
  site_brief: SiteBrief;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  site_content: Record<string, any>; // Keeping this as any for now as the structure is highly dynamic
  html: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  validation_report: Record<string, any>;
};

export default function BuilderPage() {
  const [siteType, setSiteType] = useState('general_business');
  const [industry, setIndustry] = useState('');
  const [style, setStyle] = useState('');
  const [description, setDescription] = useState('');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BuilderResponse | null>(null);
  const [viewMode, setViewMode] = useState<'preview' | 'html' | 'brief'>('preview');

  async function handleSubmit(e: React.FormEvent, endpoint: '/api/builder/generate' | '/api/builder/generate-medium') {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          site_type: siteType,
          industry,
          style,
          description,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.error ?? 'Er is een fout opgetreden bij het genereren.');
        setResult(null);
        return;
      }

      setResult(data as BuilderResponse);
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Een onbekende fout is opgetreden.');
      }
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative min-h-screen bg-slate-50 px-4 py-8 md:py-12 overflow-hidden flex flex-col justify-between">
      {/* Ambient background glows */}
      <div className="absolute inset-0 -z-10 h-full w-full bg-[radial-gradient(#e2e8f0_1px,transparent_1px)] [background-size:20px_20px] opacity-70"></div>
      <div className="absolute top-[-10%] left-[-10%] h-[600px] w-[600px] rounded-full bg-orange-100/40 blur-[100px] -z-10"></div>
      <div className="absolute bottom-[-10%] right-[-10%] h-[600px] w-[600px] rounded-full bg-blue-100/30 blur-[120px] -z-10"></div>

      <div className="w-full max-w-7xl mx-auto flex-1 flex flex-col relative z-10">
        {/* Navigation & Header */}
        <header className="mb-8">
          <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-orange-600 transition-colors mb-4 font-semibold group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="transform group-hover:-translate-x-0.5 transition-transform">
              <path d="m15 18-6-6 6-6" />
            </svg>
            Terug naar Zoeken
          </Link>
          <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
            <div>
              <h1 className="text-4xl md:text-5xl font-black text-slate-900 tracking-tight mb-2">
                fajaede<span className="bg-gradient-to-r from-orange-500 to-amber-500 bg-clip-text text-transparent">Builder</span>
              </h1>
              <p className="text-slate-600 max-w-2xl text-sm md:text-base leading-relaxed">
                Ontwerp direct complete, professionele HTML-websites op basis van een korte beschrijving met behulp van kunstmatige intelligentie.
              </p>
            </div>
            {result && (
              <button
                onClick={() => {
                  if (result) {
                    navigator.clipboard.writeText(result.html);
                    alert('HTML broncode gekopieerd naar klembord!');
                  }
                }}
                className="inline-flex items-center gap-2 bg-slate-900 hover:bg-slate-800 text-white px-5 py-2.5 rounded-full text-sm font-semibold transition-all shadow-sm cursor-pointer self-start md:self-end"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
                  <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
                </svg>
                Kopieer HTML
              </button>
            )}
          </div>
        </header>

        {/* Main Dashboard Layout */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start flex-1 w-full">
          
          {/* Left Column: Form Controls */}
          <div className="lg:col-span-5 xl:col-span-4 bg-white/80 backdrop-blur-md p-6 md:p-8 rounded-3xl border border-slate-200/60 shadow-[0_8px_30px_rgb(0,0,0,0.02)] flex flex-col gap-6">
            <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2 border-b border-slate-100 pb-3">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f97316" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
              </svg>
              Website Details
            </h2>

            <form onSubmit={(e) => handleSubmit(e, '/api/builder/generate')} className="flex flex-col gap-5">
              {/* Site Type */}
              <div className="flex flex-col">
                <label className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                  Type Website
                </label>
                <select
                  name="siteType"
                  value={siteType}
                  onChange={(e) => setSiteType(e.target.value)}
                  className="w-full px-4 py-3 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 text-slate-700 text-sm font-semibold transition-all cursor-pointer"
                >
                  <option value="general_business">Algemene Bedrijfswebsite</option>
                  <option value="law_firm">Advocatenkantoor</option>
                  <option value="restaurant">Restaurant</option>
                  <option value="webshop">Webshop / E-commerce</option>
                  <option value="portfolio">Persoonlijk Portfolio</option>
                  <option value="medical">Medische Praktijk / Kliniek</option>
                </select>
              </div>

              {/* Industry */}
              <div className="flex flex-col">
                <label className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                  Sector / Branche
                </label>
                <input
                  type="text"
                  name="industry"
                  value={industry}
                  onChange={(e) => setIndustry(e.target.value)}
                  placeholder="Bijv. IT Consultancy, Horeca, Transport"
                  className="w-full px-4 py-3 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 text-slate-700 text-sm transition-all"
                  required
                />
              </div>

              {/* Style */}
              <div className="flex flex-col">
                <label className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                  Design Stijl
                </label>
                <input
                  type="text"
                  name="style"
                  value={style}
                  onChange={(e) => setStyle(e.target.value)}
                  placeholder="Bijv. modern, minimalistisch, warm, luxe"
                  className="w-full px-4 py-3 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 text-slate-700 text-sm transition-all"
                  required
                />
              </div>

              {/* Description */}
              <div className="flex flex-col">
                <label className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                  Bedrijfsbeschrijving
                </label>
                <textarea
                  name="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Beschrijf wat het bedrijf doet, hun belangrijkste diensten, locatie en unieke eigenschappen..."
                  rows={5}
                  className="w-full px-4 py-3 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 text-slate-700 text-sm transition-all resize-none leading-relaxed"
                  required
                />
              </div>

              {/* Error Alert */}
              {error && (
                <div className="p-4 bg-red-50 border border-red-100 rounded-2xl text-xs text-red-600 flex items-start gap-2.5 leading-relaxed">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="flex-shrink-0 mt-0.5">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" x2="12" y1="8" y2="12" />
                    <line x1="12" x2="12.01" y1="16" y2="16" />
                  </svg>
                  <span>{error}</span>
                </div>
              )}

              {/* Submit Button */}
              <div className="flex flex-col gap-3 mt-2">
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white font-bold py-3.5 px-6 rounded-full shadow-md hover:shadow-lg disabled:from-slate-400 disabled:to-slate-400 transition-all flex items-center justify-center gap-2 cursor-pointer text-sm md:text-base"
                >
                  {loading ? (
                    <>
                      <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                      </svg>
                      Genereren…
                    </>
                  ) : (
                    <>
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 3v12" />
                        <path d="m8 11 4 4 4-4" />
                        <path d="M8 5H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-4" />
                      </svg>
                      Genereer Volledige Website (AI HTML)
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={(e) => handleSubmit(e, '/api/builder/generate-medium')}
                  disabled={loading}
                  className="w-full bg-slate-700 hover:bg-slate-600 text-white font-bold py-2.5 px-6 rounded-full shadow-sm disabled:bg-slate-400 transition-all flex items-center justify-center gap-2 cursor-pointer text-xs"
                >
                  Snel genereren (Template-based)
                </button>
              </div>
            </form>
          </div>

          {/* Right Column: Live Preview & HTML Code */}
          <div className="lg:col-span-7 xl:col-span-8 flex flex-col h-full">
            {/* Header controls for Preview Window */}
            <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
                <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                  {result ? 'Resultaat beschikbaar' : 'Wacht op invoer'}
                </span>
              </div>
              
              {/* Tab Selector */}
              <div className="bg-slate-200/60 p-1 rounded-full flex gap-1">
                <button
                  onClick={() => setViewMode('preview')}
                  className={`px-4 py-1.5 rounded-full text-xs font-bold transition-all cursor-pointer ${
                    viewMode === 'preview'
                      ? 'bg-white text-slate-800 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  Live Preview
                </button>
                <button
                  onClick={() => setViewMode('html')}
                  className={`px-4 py-1.5 rounded-full text-xs font-bold transition-all cursor-pointer ${
                    viewMode === 'html'
                      ? 'bg-white text-slate-800 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  HTML Code
                </button>
                <button
                  onClick={() => setViewMode('brief')}
                  className={`px-4 py-1.5 rounded-full text-xs font-bold transition-all cursor-pointer ${
                    viewMode === 'brief'
                      ? 'bg-white text-slate-800 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  Briefing
                </button>
              </div>
            </div>

            {/* Browser Preview Window */}
            <div className="w-full bg-slate-900 border border-slate-200/80 rounded-2xl overflow-hidden shadow-lg flex flex-col h-[75vh] min-h-[600px]">
              {/* Browser Header Bar */}
              <div className="bg-slate-100 border-b border-slate-200 px-4 py-3 flex items-center gap-4 flex-shrink-0">
                {/* Traffic lights dots */}
                <div className="flex gap-1.5 flex-shrink-0">
                  <span className="w-3 h-3 rounded-full bg-rose-400"></span>
                  <span className="w-3 h-3 rounded-full bg-amber-400"></span>
                  <span className="w-3 h-3 rounded-full bg-emerald-400"></span>
                </div>
                {/* Fake Address bar */}
                <div className="flex-1 bg-white border border-slate-200 rounded-lg py-1 px-3 text-center text-xs text-slate-400 truncate select-none font-mono">
                  {result ? `fajaedebuilder.ai/sites/${siteType}` : 'fajaedebuilder.ai/preview-site'}
                </div>
              </div>

              {/* Browser Canvas Body */}
              <div className="flex-1 bg-white relative overflow-hidden h-full">
                {!result ? (
                  /* Placeholder State when not generated */
                  <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-8 bg-slate-50/50">
                    <div className="w-20 h-20 bg-orange-50 border border-orange-100 text-orange-500 rounded-3xl flex items-center justify-center mb-6 shadow-[0_4px_20px_rgb(0,0,0,0.02)]">
                      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <rect width="18" height="18" x="3" y="3" rx="2" />
                        <path d="M21 9H3" />
                        <path d="M21 14H3" />
                        <path d="M9 21V9" />
                      </svg>
                    </div>
                    <h3 className="text-xl font-bold text-slate-800 mb-2">Jouw AI-website verschijnt hier</h3>
                    <p className="text-slate-500 max-w-sm text-sm leading-relaxed">
                      Vul links de details van het MKB-bedrijf in en klik op de knop om direct een volledige, responsive website te genereren.
                    </p>
                  </div>
                ) : (
                  <>
                    {/* Active Preview */}
                    {viewMode === 'preview' ? (
                      <iframe
                        title="Website preview"
                        srcDoc={result.html}
                        sandbox="allow-same-origin"
                        className="w-full h-full border-none bg-white"
                      />
                    ) : (
                      /* Active Code View */
                      <pre className="w-full h-full bg-slate-950 text-slate-200 p-6 overflow-auto text-xs md:text-sm font-mono leading-relaxed select-text">
                        <code>{result.html}</code>
                      </pre>
                    )}
                    {/* Active Briefing View */}
                    {viewMode === 'brief' && (
                      <pre className="w-full h-full bg-slate-50 text-slate-800 p-6 overflow-auto text-xs md:text-sm font-mono leading-relaxed select-text">
                        <code>{JSON.stringify(result.site_brief, null, 2)}</code>
                      </pre>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>

        </div>

        {/* Footer info */}
        {result && result.site_brief && (
          <div className="mt-8 bg-white/70 backdrop-blur-sm p-6 rounded-3xl border border-slate-200/60 shadow-[0_4px_20px_rgb(0,0,0,0.02)]">
            <h3 className="text-sm font-bold text-slate-500 uppercase tracking-wider mb-4">Gegenereerde SEO Briefing</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-sm text-slate-600">
              <div>
                <span className="font-bold text-slate-800 block mb-1">Doelgroep</span>
                {result.site_brief.target_audience || 'Geen details'}
              </div>
              <div>
                <span className="font-bold text-slate-800 block mb-1">Keywords</span>
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {(result.site_brief.keywords || []).map((k: string, i: number) => (
                    <span key={i} className="px-2 py-0.5 bg-slate-100 text-slate-700 text-xs rounded font-semibold border border-slate-200">
                      {k}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <span className="font-bold text-slate-800 block mb-1">Kernboodschap</span>
                {result.site_brief.core_value || 'Geen details'}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}