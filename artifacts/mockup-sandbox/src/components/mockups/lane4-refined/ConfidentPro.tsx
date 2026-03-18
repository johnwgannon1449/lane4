export function ConfidentPro() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{ fontFamily: "'DM Sans', sans-serif", background: "#F8FAFC", minHeight: "100vh", color: "#0F172A" }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "white", borderBottom: "1px solid #E2E8F0",
        height: 56, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 2rem",
      }}>
        <span style={{ fontFamily: "'Merriweather', serif", fontWeight: 900, fontSize: "1.2rem", color: "#0F172A", letterSpacing: "-0.01em" }}>
          Lane<span style={{ color: "#2563EB" }}>4</span>
          <span style={{ fontFamily: "'DM Sans', sans-serif", fontSize: "0.62rem", fontWeight: 700, letterSpacing: "0.12em", color: "#94A3B8", textTransform: "uppercase", marginLeft: 8 }}>Swim Recruiting</span>
        </span>
        <div style={{ display: "flex", gap: "0.25rem" }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "7px 16px", borderRadius: 8,
              fontSize: "0.84rem", fontWeight: 600,
              color: i === 0 ? "white" : "#64748B",
              background: i === 0 ? "#2563EB" : "transparent",
              cursor: "pointer",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 720, margin: "0 auto", padding: "3rem 2rem 5rem" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "1rem", marginBottom: "1.75rem" }}>
          <h1 style={{
            fontFamily: "'Merriweather', serif", fontWeight: 900,
            fontSize: "1.75rem", color: "#0F172A", letterSpacing: "-0.02em", margin: 0,
          }}>
            Find your schools
          </h1>
          <span style={{
            fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em",
            color: "#2563EB", padding: "3px 9px", border: "1.5px solid #BFDBFE",
            borderRadius: 6, background: "#EFF6FF",
          }}>
            324 programs
          </span>
        </div>

        <div style={{
          background: "white", border: "1.5px solid #E2E8F0", borderRadius: 14,
          padding: "1.1rem 1.2rem", marginBottom: "1.5rem",
          boxShadow: "0 4px 16px rgba(15,23,42,0.07)",
        }}>
          <div style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.09em", color: "#94A3B8", marginBottom: "0.65rem" }}>
            Search programs
          </div>
          <div style={{ display: "flex", gap: "0.55rem" }}>
            <input
              placeholder="School name, conference, or ask anything…"
              style={{
                flex: 1, padding: "10px 14px",
                border: "1.5px solid #E2E8F0", borderRadius: 8,
                fontFamily: "'DM Sans', sans-serif", fontSize: "0.9rem",
                color: "#0F172A", background: "#F8FAFC",
                outline: "none",
              }}
            />
            <button style={{
              padding: "10px 20px", borderRadius: 8, border: "none",
              background: "#2563EB", color: "white",
              fontFamily: "'DM Sans', sans-serif", fontSize: "0.85rem", fontWeight: 700,
              cursor: "pointer",
            }}>Search</button>
          </div>
        </div>

        <div style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.09em", color: "#94A3B8", marginBottom: "0.6rem" }}>
          Quick searches
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.45rem", marginBottom: "2.25rem" }}>
          {pills.map(p => (
            <span key={p} style={{
              padding: "6px 13px", borderRadius: 7,
              border: "1.5px solid #E2E8F0", background: "white",
              fontSize: "0.79rem", fontWeight: 500, color: "#475569", cursor: "pointer",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "0.75rem", marginBottom: "2rem" }}>
          {[
            { v: "324", l: "Programs" },
            { v: "D1–D3", l: "Divisions" },
            { v: "NAIA", l: "Included" },
            { v: "2026", l: "Data year" },
          ].map(({ v, l }) => (
            <div key={l} style={{
              background: "white", border: "1.5px solid #E2E8F0",
              borderRadius: 10, padding: "0.75rem 0.9rem", textAlign: "center",
            }}>
              <div style={{ fontFamily: "'Merriweather', serif", fontWeight: 900, fontSize: "1.25rem", color: "#0F172A", marginBottom: 2 }}>{v}</div>
              <div style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "#94A3B8" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          borderLeft: "3px solid #2563EB", paddingLeft: "1.25rem",
          marginBottom: "1rem",
        }}>
          <div style={{ fontFamily: "'Merriweather', serif", fontWeight: 700, fontSize: "0.95rem", color: "#0F172A", marginBottom: "0.3rem" }}>
            Swim fit + admissions, together
          </div>
          <div style={{ fontSize: "0.85rem", color: "#64748B", lineHeight: 1.6 }}>
            Your times determine a projected championship place at each program. Your GPA and SAT determine admission likelihood. Both scores appear for every school.
          </div>
        </div>
      </div>
    </div>
  );
}
