export function VoidPrecision() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{
      fontFamily: "'JetBrains Mono', monospace",
      background: "#020305", minHeight: "100vh", color: "#CBD5E1",
    }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "#020305", borderBottom: "1px solid #0F172A",
        height: 50, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.5rem",
      }}>
        <span style={{
          fontWeight: 700, fontSize: "0.95rem", color: "#F8FAFC",
          letterSpacing: "0.18em",
        }}>LANE4</span>
        <div style={{ display: "flex", gap: 0 }}>
          {["EXPLORE", "LIST", "ALERTS", "PROFILE"].map((t, i) => (
            <span key={t} style={{
              padding: "0 0.9rem", height: 50, display: "inline-flex", alignItems: "center",
              fontSize: "0.68rem", fontWeight: 700,
              color: i === 0 ? "#22D3EE" : "#1E293B",
              borderBottom: i === 0 ? "1px solid #22D3EE" : "1px solid transparent",
              cursor: "pointer", letterSpacing: "0.12em",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 680, margin: "0 auto", padding: "2.5rem 1.5rem 5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "2rem" }}>
          <span style={{ fontSize: "0.65rem", color: "#1E293B", letterSpacing: "0.14em" }}>SYS:ONLINE</span>
          <span style={{ width: 1, height: 10, background: "#1E293B" }} />
          <span style={{ fontSize: "0.65rem", color: "#22D3EE", letterSpacing: "0.14em" }}>NCAA.SWIM.INTEL</span>
          <span style={{ width: 1, height: 10, background: "#1E293B" }} />
          <span style={{ fontSize: "0.65rem", color: "#1E293B", letterSpacing: "0.14em" }}>324 PROGRAMS</span>
        </div>

        <h1 style={{
          fontWeight: 700, fontSize: "2.4rem", color: "#F8FAFC",
          marginBottom: "0.4rem", lineHeight: 1.1, letterSpacing: "-0.02em",
        }}>
          Find your programs.
        </h1>
        <p style={{ fontSize: "0.78rem", color: "#334155", marginBottom: "2rem", lineHeight: 1.8 }}>
          Every school scored against your times + academic profile. Honest output only.
        </p>

        <div style={{
          border: "1px solid #0F172A",
          borderRadius: 4, overflow: "hidden", marginBottom: "1rem",
          display: "flex",
        }}>
          <div style={{
            padding: "10px 12px", background: "#0A0D15",
            borderRight: "1px solid #0F172A",
            fontWeight: 700, fontSize: "0.82rem", color: "#22D3EE",
            display: "flex", alignItems: "center",
          }}>▶</div>
          <input
            placeholder="school, conference, or ask anything"
            style={{
              flex: 1, boxSizing: "border-box",
              padding: "10px 14px", border: "none",
              fontFamily: "'JetBrains Mono', monospace", fontSize: "0.82rem",
              color: "#94A3B8", background: "#0A0D15",
              outline: "none", letterSpacing: "0.01em",
            }}
          />
          <button style={{
            padding: "10px 20px", border: "none", borderLeft: "1px solid #0F172A",
            background: "#0F172A", color: "#22D3EE",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: "0.75rem", fontWeight: 700, cursor: "pointer",
            letterSpacing: "0.1em",
          }}>RUN</button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem", marginBottom: "2.5rem" }}>
          {pills.map(p => (
            <span key={p} style={{
              padding: "4px 10px", borderRadius: 2,
              border: "1px solid #0F172A",
              fontSize: "0.7rem", fontWeight: 400, color: "#334155", cursor: "pointer",
              letterSpacing: "0.02em",
            }}>{p}</span>
          ))}
        </div>

        <div style={{
          display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "1px",
          background: "#0F172A", marginBottom: "2rem",
          border: "1px solid #0F172A", borderRadius: 2,
        }}>
          {[
            { v: "324", l: "programs indexed",   c: "#22D3EE" },
            { v: "15+", l: "conferences covered", c: "#F8FAFC" },
            { v: "2026", l: "championship data",  c: "#F8FAFC" },
          ].map(({ v, l, c }) => (
            <div key={l} style={{ background: "#020305", padding: "1.1rem 1rem" }}>
              <div style={{ fontWeight: 700, fontSize: "1.6rem", color: c, marginBottom: 4, letterSpacing: "-0.02em" }}>{v}</div>
              <div style={{ fontSize: "0.62rem", color: "#1E293B", letterSpacing: "0.08em" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{ border: "1px solid #0F172A", borderRadius: 2, overflow: "hidden" }}>
          <div style={{
            padding: "0.5rem 1rem", background: "#0A0D15",
            borderBottom: "1px solid #0F172A",
            fontSize: "0.65rem", color: "#22D3EE", letterSpacing: "0.12em",
          }}>USAGE</div>
          <div style={{ padding: "1rem", fontSize: "0.76rem", color: "#334155", lineHeight: 1.9 }}>
            <span style={{ color: "#22D3EE" }}>01</span>  enter your times + GPA in <span style={{ color: "#94A3B8" }}>Profile</span><br />
            <span style={{ color: "#22D3EE" }}>02</span>  every school scores against your events<br />
            <span style={{ color: "#22D3EE" }}>03</span>  swim fit + admissions, side by side
          </div>
        </div>
      </div>
    </div>
  );
}
