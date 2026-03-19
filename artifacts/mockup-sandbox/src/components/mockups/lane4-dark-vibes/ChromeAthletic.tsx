export function ChromeAthletic() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{
      fontFamily: "'Space Grotesk', sans-serif",
      background: "#0C0D10", minHeight: "100vh", color: "#CBD5E1",
    }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "rgba(12,13,16,0.97)", backdropFilter: "blur(16px)",
        borderBottom: "1px solid #1C1E24",
        height: 54, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.5rem",
      }}>
        <span style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 700, fontSize: "1.05rem",
          background: "linear-gradient(90deg, #94A3B8, #F1F5F9, #94A3B8)",
          WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          letterSpacing: "0.06em",
        }}>LANE4</span>
        <div style={{ display: "flex", gap: 0 }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "0 1rem", height: 54, display: "inline-flex", alignItems: "center",
              fontSize: "0.8rem", fontWeight: i === 0 ? 600 : 400,
              color: i === 0 ? "#E2E8F0" : "#2A2D36",
              borderBottom: i === 0 ? "1px solid #64748B" : "1px solid transparent",
              cursor: "pointer", letterSpacing: "0.03em",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 700, margin: "0 auto", padding: "2.75rem 1.5rem 5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.25rem" }}>
          <div style={{ width: 7, height: 7, borderRadius: "50%", background: "#94A3B8" }} />
          <span style={{ fontSize: "0.7rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.12em", color: "#4B5563" }}>
            NCAA Swim Recruiting Intelligence
          </span>
        </div>

        <h1 style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 700,
          fontSize: "2.4rem",
          background: "linear-gradient(160deg, #F1F5F9 0%, #94A3B8 60%, #64748B 100%)",
          WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          marginBottom: "0.5rem", lineHeight: 1.12, letterSpacing: "-0.02em",
        }}>
          Find your programs.
        </h1>
        <p style={{ fontSize: "0.88rem", color: "#2A2D36", marginBottom: "2rem", lineHeight: 1.65 }}>
          324 programs scored against your times and academic profile. No guesswork.
        </p>

        <div style={{
          background: "#111318", border: "1px solid #1C1E24",
          borderRadius: 10, padding: "5px",
          display: "flex", gap: "5px", marginBottom: "1rem",
        }}>
          <input
            placeholder="School, conference, or ask anything…"
            style={{
              flex: 1, boxSizing: "border-box",
              padding: "11px 14px", border: "none", borderRadius: 7,
              fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.9rem",
              color: "#CBD5E1", background: "transparent", outline: "none",
            }}
          />
          <button style={{
            padding: "11px 22px", borderRadius: 7, border: "none",
            background: "linear-gradient(135deg, #374151, #4B5563)",
            color: "#F1F5F9", fontFamily: "'Space Grotesk', sans-serif",
            fontSize: "0.88rem", fontWeight: 700, cursor: "pointer",
            letterSpacing: "0.03em",
          }}>Search</button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "2.25rem" }}>
          {pills.map((p, i) => (
            <span key={p} style={{
              padding: "5px 12px", borderRadius: 5,
              border: `1px solid ${i === 0 ? "#374151" : "#1C1E24"}`,
              background: i === 0 ? "#1C1E24" : "transparent",
              fontSize: "0.78rem", fontWeight: 500,
              color: i === 0 ? "#E2E8F0" : "#2A2D36",
              cursor: "pointer",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.75rem", marginBottom: "2rem" }}>
          {[
            { v: "324",  l: "Programs indexed",   accent: "#4B5563" },
            { v: "15+",  l: "Conferences covered", accent: "#4B5563" },
            { v: "2026", l: "Championship data",   accent: "#64748B" },
          ].map(({ v, l, accent }) => (
            <div key={l} style={{
              background: "#111318", border: "1px solid #1C1E24",
              borderRadius: 8, padding: "1rem 1.1rem",
            }}>
              <div style={{
                fontFamily: "'Oxanium', monospace", fontWeight: 700,
                fontSize: "1.5rem", marginBottom: 4,
                background: "linear-gradient(120deg, #E2E8F0, #94A3B8)",
                WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
              }}>{v}</div>
              <div style={{ fontSize: "0.7rem", fontWeight: 500, color: "#2A2D36", letterSpacing: "0.04em" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "#111318", border: "1px solid #1C1E24",
          borderLeft: "2px solid #374151",
          borderRadius: 8, padding: "1.1rem 1.3rem",
          fontFamily: "'JetBrains Mono', monospace", fontSize: "0.78rem",
          color: "#2A2D36", lineHeight: 1.8,
        }}>
          <span style={{ color: "#64748B" }}>→</span> Enter your times + GPA in <span style={{ color: "#94A3B8" }}>Profile</span><br />
          <span style={{ color: "#64748B" }}>→</span> Every school scores against your events<br />
          <span style={{ color: "#64748B" }}>→</span> Swim fit + admissions, side by side
        </div>
      </div>
    </div>
  );
}
