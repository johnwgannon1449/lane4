export function NightAthletic() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{
      fontFamily: "'Space Grotesk', sans-serif",
      background: "#00101E", minHeight: "100vh", color: "#E2E8F0",
    }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "#00101E", borderBottom: "3px solid #EF4444",
        height: 56, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.5rem",
      }}>
        <span style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 900, fontSize: "1.2rem",
          color: "#F8FAFC", letterSpacing: "0.1em",
        }}>LANE<span style={{ color: "#EF4444" }}>4</span></span>
        <div style={{ display: "flex", gap: 0 }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "0 1rem", height: 56, display: "inline-flex", alignItems: "center",
              fontSize: "0.8rem", fontWeight: i === 0 ? 800 : 500,
              color: i === 0 ? "#EF4444" : "#1E3A52",
              cursor: "pointer", letterSpacing: "0.04em",
            }}>{t.toUpperCase()}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 700, margin: "0 auto", padding: "2.75rem 1.5rem 5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
          <div style={{ width: 10, height: 10, borderRadius: "50%", background: "#EF4444", boxShadow: "0 0 10px #EF4444" }} />
          <span style={{ fontSize: "0.7rem", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.14em", color: "#EF4444" }}>
            Live — NCAA Swim Recruiting
          </span>
        </div>

        <h1 style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 900,
          fontSize: "2.75rem", color: "#F8FAFC", marginBottom: "0.5rem",
          lineHeight: 1.05, letterSpacing: "-0.02em",
        }}>
          Find your<br />programs.
        </h1>
        <p style={{ fontSize: "0.88rem", color: "#2D5E7A", marginBottom: "2rem", lineHeight: 1.6, fontWeight: 500 }}>
          324 programs. Scored against your times. Honest recruiting badges.
        </p>

        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
          <div style={{ flex: 1, position: "relative" }}>
            <input
              placeholder="School, conference, or anything…"
              style={{
                width: "100%", boxSizing: "border-box",
                padding: "13px 16px",
                border: "2px solid #0A2035", borderRadius: 6,
                fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.9rem",
                color: "#E2E8F0", background: "#020D18",
                outline: "none",
              }}
            />
          </div>
          <button style={{
            padding: "13px 24px", borderRadius: 6, border: "none",
            background: "#EF4444",
            color: "white", fontFamily: "'Oxanium', monospace",
            fontSize: "0.9rem", fontWeight: 800, cursor: "pointer",
            letterSpacing: "0.08em",
          }}>GO</button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "2.5rem" }}>
          {pills.map((p, i) => (
            <span key={p} style={{
              padding: "5px 13px", borderRadius: 4,
              border: `1px solid ${i === 0 ? "#EF4444" : "#0A2035"}`,
              background: i === 0 ? "rgba(239,68,68,0.1)" : "#020D18",
              fontSize: "0.78rem", fontWeight: 600,
              color: i === 0 ? "#FCA5A5" : "#1E3A52",
              cursor: "pointer", letterSpacing: "0.02em",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.5rem", marginBottom: "2rem" }}>
          {[
            { v: "324",  l: "Programs",     c: "#EF4444" },
            { v: "15+",  l: "Conferences",  c: "#F8FAFC" },
            { v: "2026", l: "Season",       c: "#F8FAFC" },
          ].map(({ v, l, c }) => (
            <div key={l} style={{
              background: "#020D18", border: "1px solid #0A2035",
              borderRadius: 6, padding: "1.1rem 1.1rem",
              position: "relative", overflow: "hidden",
            }}>
              <div style={{
                position: "absolute", top: 0, left: 0, right: 0, height: 3,
                background: c === "#EF4444" ? "#EF4444" : "#1E3A52",
              }} />
              <div style={{ fontFamily: "'Oxanium', monospace", fontWeight: 900, fontSize: "1.8rem", color: c, marginBottom: 2, letterSpacing: "-0.02em" }}>{v}</div>
              <div style={{ fontSize: "0.7rem", fontWeight: 700, color: "#1E3A52", letterSpacing: "0.08em", textTransform: "uppercase" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "#020D18", border: "1px solid #0A2035",
          borderLeft: "4px solid #EF4444",
          borderRadius: 6, padding: "1.1rem 1.3rem",
          fontFamily: "'JetBrains Mono', monospace", fontSize: "0.78rem",
          color: "#1E3A52", lineHeight: 1.8,
        }}>
          <span style={{ color: "#EF4444" }}>01</span> Enter your times + GPA in <span style={{ color: "#94A3B8" }}>Profile</span><br />
          <span style={{ color: "#EF4444" }}>02</span> Every school scores against your events<br />
          <span style={{ color: "#EF4444" }}>03</span> Swim fit + admissions, side by side
        </div>
      </div>
    </div>
  );
}
