"use client";

import { useEffect, useState } from "react";

type AISummaryBarProps = {
  query: string;
};

type AISummaryResponse = {
  summary: string;
  followUpQuestion: string;
  conversationId: string;
};

export default function AISummaryBar({ query }: AISummaryBarProps) {
  const [data, setData] = useState<AISummaryResponse | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [followUpInput, setFollowUpInput] = useState("");

  useEffect(() => {
    if (!query.trim()) return;

    const fetchSummary = async () => {
      setLoading(true);
      try {
        const res = await fetch("/api/ai/summary", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            q: query,
            limit: 4,
            conversationId,
          }),
        });
        // Verify the response is JSON before parsing
        const contentType = res.headers.get("content-type") || "";
        let json: any = null;
        if (contentType.includes("application/json")) {
          json = await res.json();
        } else {
          const text = await res.text();
          console.error("AI summary returned non‑JSON response:", text);
        }

        if (res.ok && json) {
          setData(json);
          if (json.conversationId) {
            setConversationId(json.conversationId);
          }
        } else if (json) {
          console.error("AI summary error:", json.error || json.message);
        }
      } catch (err) {
        console.error("AI summary fetch failed:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchSummary();
  }, [query]);

// ...

const handleFollowUp = async (e: React.FormEvent) => {
  e.preventDefault();
  if (!followUpInput.trim()) return;

  setLoading(true);
  try {
    const res = await fetch("/api/ai/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        q: followUpInput,
        limit: 5,
        conversationId,
      }),
    });
    const json = await res.json();
    if (res.ok) {
      setData(json);
      if (json.conversationId) {
        setConversationId(json.conversationId);
      }
      setFollowUpInput("");
    } else {
      console.error("AI follow-up error:", json.error || json.message);
    }
  } finally {
    setLoading(false);
  }
};

if (!query.trim()) return null;

return (
  <div
    style={{
      marginBottom: 24,
      padding: 16,
      borderRadius: 14,
      background: "#f0f9ff",
      border: "1px solid #bae6fd",
      position: "relative",
    }}
  >
    {/* Loading-indicator linksboven, ook bij vervolgvragen */}
    {loading && (
      <div
        style={{
          position: "absolute",
          top: 8,
          right: 12,
          fontSize: 12,
          color: "#0369a1",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "999px",
            background: "#0369a1",
            animation: "pulse 1s ease-in-out infinite",
          }}
        />
        <span>AI is aan het nadenken…</span>
      </div>
    )}

    {data && (
      <>
        <div
          style={{
            marginBottom: 8,
            whiteSpace: "pre-line",
            color: "#0f172a",
          }}
        >
          {data.summary}
        </div>

        <div
          style={{
            marginBottom: 8,
            color: "#0369a1",
            fontSize: 14,
          }}
        >
          {data.followUpQuestion}
        </div>

        <form
          onSubmit={handleFollowUp}
          style={{ display: "flex", gap: 8, marginTop: 4 }}
        >
          <input
            value={followUpInput}
            onChange={(e) => setFollowUpInput(e.target.value)}
            placeholder="Wat wil je nog meer vragen of weten?"
            style={{
              flex: 1,
              padding: "8px 10px",
              borderRadius: 10,
              border: "1px solid #d0d7de",
              fontSize: 14,
            }}
          />
          <button
            type="submit"
            disabled={loading || !followUpInput.trim()}
            style={{
              padding: "8px 14px",
              borderRadius: 10,
              border: "none",
              background: "#01696f",
              color: "white",
              fontWeight: 600,
              cursor: "pointer",
              fontSize: 14,
              opacity: loading ? 0.8 : 1,
            }}
          >
            Vraag
          </button>
        </form>
      </>
    )}
  </div>
);
}