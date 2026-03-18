export function DarkAthletic() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{ fontFamily: "'Space Grotesk', sans-serif", background: "#080C18", minHeight: "100vh", color: "#E2E8F0" }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "rgba(12,17,35,0.92)", backdropFilter: "blur(12px)",
        borderBottom: "1px solid rgba(59,130,246,0.15)",
        height: 54, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.5rem",
      }}>
        <span style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 800, fontSize: "1.1rem",
          background: "linear-gradient(90deg, #3B82F6, #06B6D4)",
          WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          letterSpacing: "0.05em",
        }}>
          LANE4
        </span>
        <div style={{ display: "flex", gap: 0 }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "0 1rem", height: 54, display: "inline-flex", alignItems: "center",
              fontSize: "0.82rem", fontWeight: i === 0 ? 700 : 400,
              color: i === 0 ? "#3B82F6" : "#64748B",
              borderBottom: i === 0 ? "2px solid #3B82F6" : "2px solid transparent",
              cursor: "pointer", letterSpacing: "0.03em",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 700, margin: "0 auto", padding: "2.5rem 1.5rem 5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.25rem" }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#06B6D4", boxShadow: "0 0 8px #06B6D4" }} />
          <span style={{ fontSize: "0.72rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.12em", color: "#06B6D4" }}>
            NCAA Swim Recruiting Intelligence
          </span>
        </div>

        <h1 style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 700,
          fontSize: "2rem", color: "#F1F5F9", marginBottom: "0.5rem", lineHeight: 1.2, letterSpacing: "-0.01em",
        }}>
          Find your programs.
        </h1>
        <p style={{ fontSize: "0.88rem", color: "#64748B", marginBottom: "1.75rem", lineHeight: 1.65 }}>
          324 programs scored against your times and academic profile. No guesswork.
        </p>

        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
          <div style={{ flex: 1, position: "relative" }}>
            <input
              placeholder="School, conference, or ask anything…"
              style={{
                width: "100%", boxSizing: "border-box",
                padding: "12px 16px",
                border: "1px solid rgba(59,130,246,0.3)", borderRadius: 8,
                fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.9rem",
                color: "#E2E8F0", background: "#0F172A",
                outline: "none",
              }}
            />
          </div>
          <button style={{
            padding: "12px 22px", borderRadius: 8, border: "none",
            background: "linear-gradient(135deg, #2563EB, #3B82F6)",
            color: "white", fontFamily: "'Space Grotesk', sans-serif",
            fontSize: "0.88rem", fontWeight: 700, cursor: "pointer",
            boxShadow: "0 0 16px rgba(59,130,246,0.3)",
          }}>Search</button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "2.25rem" }}>
          {pills.map(p => (
            <span key={p} style={{
              padding: "5px 12px", borderRadius: 6,
              border: "1px solid rgba(99,102,241,0.3)", background: "rgba(99,102,241,0.06)",
              fontSize: "0.78rem", fontWeight: 500, color: "#94A3B8", cursor: "pointer",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.75rem", marginBottom: "2rem" }}>
          {[
            { v: "324", l: "Programs indexed", c: "#3B82F6" },
            { v: "15+", l: "Conferences covered", c: "#06B6D4" },
            { v: "2026", l: "Championship data", c: "#8B5CF6" },
          ].map(({ v, l, c }) => (
            <div key={l} style={{
              background: "#0F172A", border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 10, padding: "1rem 1.1rem",
              borderTop: `2px solid ${c}`,
            }}>
              <div style={{ fontFamily: "'Oxanium', monospace", fontWeight: 700, fontSize: "1.5rem", color: c, marginBottom: 2 }}>{v}</div>
              <div style={{ fontSize: "0.72rem", fontWeight: 500, color: "#475569", letterSpacing: "0.03em" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "#0F172A", border: "1px solid rgba(59,130,246,0.2)",
          borderLeft: "3px solid #3B82F6",
          borderRadius: 10, padding: "1.1rem 1.3rem",
          fontFamily: "'JetBrains Mono', monospace", fontSize: "0.78rem",
          color: "#64748B", lineHeight: 1.7,
        }}>
          <span style={{ color: "#3B82F6" }}>→</span> Enter your times + GPA in <span style={{ color: "#06B6D4" }}>Profile</span><br />
          <span style={{ color: "#3B82F6" }}>→</span> Every school scores against your events<br />
          <span style={{ color: "#3B82F6" }}>→</span> Swim fit + admissions, side by side
        </div>
      </div>
    </div>
  );
}
