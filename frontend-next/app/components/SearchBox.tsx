"use client";

import { useState } from "react";
import AISummaryBar from "./AISummaryBar";

type Hit = {
  id?: string;
  title?: string;
  url?: string;
  summary?: string;
  content?: string;
  source?: string;
  language?: string;
  country?: string;
  category?: string;
};

export default function SearchBox() {
  const [q, setQ] = useState("");
  const [lastSearchedQuery, setLastSearchedQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAiSummary, setShowAiSummary] = useState(false);
  const [error, setError] = useState("");

  async function search() {
    if (!q.trim()) return;
    setLoading(true);
    setError("");

    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ q, limit: 10 }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || data.message || "Search failed");
      }

      setHits(data.hits || []);
      setLastSearchedQuery(q); // deze gaat naar AISummaryBar
    } catch (err: any) {
      setError(err.message || "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section style={{ marginTop: 32 }}>
      <div style={{ display: "flex", gap: 12, marginBottom: 24, alignItems: "center" }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()}
          placeholder="Zoek naar ECB, rijksoverheid, privacy..."
          style={{
            flex: 1,
            padding: "14px 16px",
            borderRadius: 12,
            border: "1px solid #d0d7de",
            fontSize: 16,
          }}
        />
        <button
          onClick={search}
          disabled={loading}
          style={{
            padding: "14px 20px",
            borderRadius: 12,
            border: "none",
            background: "#01696f",
            color: "white",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {loading ? "Zoeken..." : "Zoeken"}
        </button>
        {/* Toggle AI summary button */}
        {showAiSummary ? (
          <button
            onClick={() => setShowAiSummary(false)}
            style={{
              padding: "8px 12px",
              borderRadius: 8,
              border: "none",
              background: "#c0392b",
              color: "white",
              fontWeight: 600,
            }}
          >
            Verberg AI‑samenvatting
          </button>
        ) : (
          <button
            onClick={() => setShowAiSummary(true)}
            style={{
              padding: "8px 12px",
              borderRadius: 8,
              border: "none",
              background: "#01696f",
              color: "white",
              fontWeight: 600,
            }}
          >
            Toon AI‑samenvatting
          </button>
        )}
      </div>
      {error && (
        <p style={{ color: "#b42318", marginBottom: 16 }}>{error}</p>
      )}
      {/* Render AI summary directly under the menu bar when enabled */}
      {showAiSummary && <AISummaryBar query={lastSearchedQuery} />}
      <div style={{ display: "grid", gap: 16 }}>
        {hits.map((hit, i) => (
          <article
            key={hit.id || hit.url || i}
            style={{
              padding: 18,
              border: "1px solid #e5e7eb",
              borderRadius: 14,
              background: "#fff",
            }}
          >
            <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 6 }}>
              {[hit.source, hit.language, hit.country, hit.category]
                .filter(Boolean)
                .join(" · ")}
            </div>

            <h3 style={{ margin: "0 0 8px", fontSize: 20 }}>
              <a
                href={hit.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "#01696f", textDecoration: "none" }}
              >
                {hit.title || hit.url || "Ongetiteld resultaat"}
              </a>
            </h3>

            <p style={{ margin: 0, color: "#4b5563", lineHeight: 1.6 }}>
              {hit.summary ||
                hit.content?.slice(0, 260) ||
                "Geen snippet beschikbaar."}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}