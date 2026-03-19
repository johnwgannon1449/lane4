export function DarkPolished() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{ fontFamily: "'Space Grotesk', sans-serif", background: "#080C18", minHeight: "100vh", color: "#E2E8F0" }}>
      <style>{`
        @keyframes dp-pulse {
          0%, 100% { opacity: 1; box-shadow: 0 0 6px #06B6D4, 0 0 12px rgba(6,182,212,0.4); }
          50%       { opacity: 0.6; box-shadow: 0 0 3px #06B6D4, 0 0 6px rgba(6,182,212,0.2); }
        }
      `}</style>

      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "rgba(6,9,18,0.96)", backdropFilter: "blur(20px)",
        borderBottom: "1px solid rgba(59,130,246,0.1)",
        height: 56, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.5rem",
      }}>
        <span style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 800, fontSize: "1.1rem",
          background: "linear-gradient(90deg, #3B82F6, #06B6D4)",
          WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          letterSpacing: "0.05em",
        }}>LANE4</span>
        <div style={{ display: "flex", gap: 4 }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "5px 14px", borderRadius: 20,
              fontSize: "0.82rem", fontWeight: i === 0 ? 700 : 400,
              color: i === 0 ? "#fff" : "#475569",
              background: i === 0 ? "linear-gradient(135deg, #1D4ED8, #0891B2)" : "transparent",
              cursor: "pointer", letterSpacing: "0.02em",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 700, margin: "0 auto", padding: "2.5rem 1.5rem 5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.25rem" }}>
          <div style={{
            width: 8, height: 8, borderRadius: "50%", background: "#06B6D4",
            animation: "dp-pulse 2.2s ease-in-out infinite",
          }} />
          <span style={{ fontSize: "0.72rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.12em", color: "#06B6D4" }}>
            NCAA Swim Recruiting Intelligence
          </span>
        </div>

        <h1 style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 700,
          fontSize: "2.5rem", color: "#F1F5F9", marginBottom: "0.5rem", lineHeight: 1.12, letterSpacing: "-0.02em",
        }}>
          Find your programs.
        </h1>
        <p style={{ fontSize: "0.9rem", color: "#94A3B8", marginBottom: "2rem", lineHeight: 1.65 }}>
          324 programs scored against your times and academic profile. No guesswork.
        </p>

        <div style={{
          background: "#0B1120", border: "1px solid rgba(59,130,246,0.25)",
          borderRadius: 12, padding: "6px",
          boxShadow: "0 0 28px rgba(59,130,246,0.1), 0 0 0 1px rgba(59,130,246,0.05)",
          display: "flex", gap: "6px", marginBottom: "1rem",
        }}>
          <input
            placeholder="School, conference, or ask anything…"
            style={{
              flex: 1, boxSizing: "border-box",
              padding: "11px 14px", border: "none", borderRadius: 8,
              fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.9rem",
              color: "#E2E8F0", background: "transparent", outline: "none",
            }}
          />
          <button style={{
            padding: "11px 24px", borderRadius: 8, border: "none",
            background: "linear-gradient(135deg, #1D4ED8, #0891B2)",
            color: "white", fontFamily: "'Space Grotesk', sans-serif",
            fontSize: "0.88rem", fontWeight: 700, cursor: "pointer",
            boxShadow: "0 0 20px rgba(29,78,216,0.4)",
            letterSpacing: "0.03em",
          }}>Search</button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "2.25rem" }}>
          {pills.map((p, i) => (
            <span key={p} style={{
              padding: "5px 13px", borderRadius: 6,
              border: `1px solid ${i === 0 ? "rgba(59,130,246,0.5)" : "rgba(59,130,246,0.2)"}`,
              background: i === 0 ? "rgba(59,130,246,0.15)" : "rgba(59,130,246,0.05)",
              fontSize: "0.78rem", fontWeight: 500,
              color: i === 0 ? "#93C5FD" : "#64748B",
              cursor: "pointer",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.75rem", marginBottom: "2rem" }}>
          {[
            { v: "324", l: "Programs indexed",   c: "#3B82F6", glow: "rgba(59,130,246,0.15)" },
            { v: "15+", l: "Conferences covered", c: "#06B6D4", glow: "rgba(6,182,212,0.15)"  },
            { v: "2026", l: "Championship data",  c: "#8B5CF6", glow: "rgba(139,92,246,0.15)" },
          ].map(({ v, l, c, glow }) => (
            <div key={l} style={{
              background: "#0B1120", border: "1px solid rgba(255,255,255,0.05)",
              borderRadius: 10, padding: "1rem 1.1rem",
              borderTop: `2px solid ${c}`,
              boxShadow: `0 6px 24px ${glow}`,
            }}>
              <div style={{ fontFamily: "'Oxanium', monospace", fontWeight: 700, fontSize: "1.5rem", color: c, marginBottom: 4 }}>{v}</div>
              <div style={{ fontSize: "0.72rem", fontWeight: 500, color: "#94A3B8", letterSpacing: "0.03em" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "#0B1120", border: "1px solid rgba(59,130,246,0.18)",
          borderLeft: "3px solid #3B82F6",
          borderRadius: 10, padding: "1rem 1.3rem",
        }}>
          <div style={{
            fontFamily: "'JetBrains Mono', monospace", fontSize: "0.68rem",
            color: "#06B6D4", marginBottom: "0.5rem", letterSpacing: "0.08em",
          }}>// getting started</div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.78rem", color: "#64748B", lineHeight: 1.8 }}>
            <span style={{ color: "#3B82F6" }}>→</span> Enter your times + GPA in <span style={{ color: "#06B6D4" }}>Profile</span><br />
            <span style={{ color: "#3B82F6" }}>→</span> Every school scores against your events<br />
            <span style={{ color: "#3B82F6" }}>→</span> Swim fit + admissions, side by side
          </div>
        </div>
      </div>
    </div>
  );
}
