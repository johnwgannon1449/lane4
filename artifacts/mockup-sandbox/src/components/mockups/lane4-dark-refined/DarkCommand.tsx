export function DarkCommand() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{
      fontFamily: "'Space Grotesk', sans-serif",
      background: "radial-gradient(ellipse 120% 90% at 50% 30%, #0E1E40 0%, #040810 65%)",
      minHeight: "100vh", color: "#E2E8F0",
    }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "rgba(4,8,16,0.92)", backdropFilter: "blur(20px)",
        borderBottom: "1px solid rgba(59,130,246,0.08)",
        height: 52, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.5rem",
      }}>
        <span style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 900, fontSize: "1.15rem",
          background: "linear-gradient(90deg, #60A5FA, #22D3EE)",
          WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          letterSpacing: "0.08em",
        }}>LANE4</span>
        <div style={{ display: "flex", gap: 0 }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "0 1rem", height: 52, display: "inline-flex", alignItems: "center",
              fontSize: "0.8rem", fontWeight: i === 0 ? 700 : 400,
              color: i === 0 ? "#60A5FA" : "#334155",
              borderBottom: i === 0 ? "2px solid #3B82F6" : "2px solid transparent",
              cursor: "pointer", letterSpacing: "0.04em",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 680, margin: "0 auto", padding: "3.25rem 1.5rem 5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.5rem" }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#22D3EE", boxShadow: "0 0 10px #22D3EE, 0 0 20px rgba(34,211,238,0.3)" }} />
          <span style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.15em", color: "#22D3EE" }}>
            NCAA Swim Recruiting Intelligence
          </span>
        </div>

        <h1 style={{
          fontFamily: "'Oxanium', monospace", fontWeight: 800,
          fontSize: "3rem", lineHeight: 1.05, letterSpacing: "-0.03em",
          background: "linear-gradient(140deg, #93C5FD 0%, #22D3EE 55%, #A78BFA 100%)",
          WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          marginBottom: "0.75rem",
        }}>
          Find your programs.
        </h1>
        <p style={{ fontSize: "0.88rem", color: "#475569", marginBottom: "2.25rem", lineHeight: 1.7 }}>
          324 programs scored against your times and academic profile. No guesswork.
        </p>

        <div style={{ position: "relative", marginBottom: "1rem" }}>
          <div style={{
            position: "absolute", inset: -2,
            borderRadius: 15,
            background: "linear-gradient(135deg, rgba(59,130,246,0.35), rgba(34,211,238,0.25))",
            filter: "blur(10px)", zIndex: 0,
          }} />
          <div style={{
            position: "relative", zIndex: 1,
            background: "#09102A", border: "1px solid rgba(59,130,246,0.3)",
            borderRadius: 12, padding: "6px",
            display: "flex", gap: "6px",
          }}>
            <input
              placeholder="School, conference, or ask anything…"
              style={{
                flex: 1, boxSizing: "border-box",
                padding: "13px 16px", border: "none", borderRadius: 8,
                fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.92rem",
                color: "#E2E8F0", background: "transparent", outline: "none",
              }}
            />
            <button style={{
              padding: "13px 28px", borderRadius: 8, border: "none",
              background: "linear-gradient(135deg, #1D4ED8, #0891B2)",
              color: "white", fontFamily: "'Space Grotesk', sans-serif",
              fontSize: "0.88rem", fontWeight: 800, cursor: "pointer",
              letterSpacing: "0.05em",
            }}>Search</button>
          </div>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "2.5rem" }}>
          {pills.map(p => (
            <span key={p} style={{
              padding: "4px 12px", borderRadius: 5,
              border: "1px solid rgba(59,130,246,0.18)", background: "rgba(9,16,42,0.8)",
              fontSize: "0.76rem", fontWeight: 500, color: "#475569", cursor: "pointer",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.75rem", marginBottom: "2rem" }}>
          {[
            { v: "324", l: "Programs indexed",   g: "linear-gradient(135deg, rgba(29,78,216,0.28), rgba(29,78,216,0.06))", b: "#3B82F6" },
            { v: "15+", l: "Conferences covered", g: "linear-gradient(135deg, rgba(8,145,178,0.28), rgba(8,145,178,0.06))", b: "#22D3EE" },
            { v: "2026", l: "Championship data",  g: "linear-gradient(135deg, rgba(109,40,217,0.28), rgba(109,40,217,0.06))", b: "#A78BFA" },
          ].map(({ v, l, g, b }) => (
            <div key={l} style={{
              background: g, border: `1px solid ${b}28`,
              borderRadius: 12, padding: "1.1rem 1.2rem",
            }}>
              <div style={{ fontFamily: "'Oxanium', monospace", fontWeight: 800, fontSize: "1.65rem", color: b, marginBottom: 6 }}>{v}</div>
              <div style={{ fontSize: "0.72rem", fontWeight: 500, color: "#64748B", letterSpacing: "0.04em" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "rgba(9,16,42,0.9)", border: "1px solid rgba(59,130,246,0.14)",
          borderRadius: 10, overflow: "hidden",
        }}>
          <div style={{
            padding: "0.55rem 1.2rem",
            borderBottom: "1px solid rgba(59,130,246,0.1)",
            background: "rgba(29,78,216,0.1)",
            fontFamily: "'Oxanium', monospace", fontSize: "0.7rem",
            color: "#22D3EE", letterSpacing: "0.12em", fontWeight: 700,
          }}>LANE4 SYSTEM</div>
          <div style={{ padding: "1rem 1.2rem", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.78rem", color: "#64748B", lineHeight: 1.9 }}>
            <span style={{ color: "#3B82F6" }}>→</span> Enter your times + GPA in <span style={{ color: "#22D3EE" }}>Profile</span><br />
            <span style={{ color: "#3B82F6" }}>→</span> Every school scores against your events<br />
            <span style={{ color: "#3B82F6" }}>→</span> Swim fit + admissions, side by side
          </div>
        </div>
      </div>
    </div>
  );
}
