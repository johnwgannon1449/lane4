export function CrispElevated() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{ fontFamily: "'Figtree', 'DM Sans', sans-serif", background: "#F8FAFC", minHeight: "100vh", color: "#0F172A" }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "white", borderBottom: "1px solid #E2E8F0",
        height: 52, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.25rem",
        boxShadow: "0 1px 4px rgba(15,23,42,0.05)",
      }}>
        <span style={{ fontFamily: "'Plus Jakarta Sans', serif", fontWeight: 800, fontSize: "1.1rem", color: "#0F172A", letterSpacing: "-0.02em" }}>
          Lane<span style={{ color: "#2563EB" }}>4</span>
        </span>
        <div style={{ display: "flex", gap: 0 }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "0 1rem", height: 52, display: "inline-flex", alignItems: "center",
              fontSize: "0.84rem", fontWeight: i === 0 ? 700 : 600,
              color: i === 0 ? "#2563EB" : "#64748B",
              borderBottom: i === 0 ? "2px solid #2563EB" : "2px solid transparent",
              cursor: "pointer",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 680, margin: "0 auto", padding: "2.25rem 1.25rem 5rem" }}>
        <h1 style={{
          fontFamily: "'Plus Jakarta Sans', sans-serif", fontWeight: 800,
          fontSize: "1.5rem", color: "#0F172A", marginBottom: "0.2rem", letterSpacing: "-0.02em",
        }}>
          Find your schools
        </h1>
        <p style={{ fontSize: "0.84rem", color: "#64748B", marginBottom: "1.5rem" }}>
          Match your swim times and GPA against 324 programs.
        </p>

        <div style={{ display: "flex", gap: "0.55rem", marginBottom: "0.85rem" }}>
          <input
            placeholder="School name or ask anything…"
            style={{
              flex: 1, padding: "11px 15px",
              border: "1.5px solid #E2E8F0", borderRadius: 10,
              fontFamily: "inherit", fontSize: "0.9rem",
              color: "#0F172A", background: "white",
              outline: "none",
              boxShadow: "0 2px 8px rgba(15,23,42,0.05), 0 0 0 0 rgba(37,99,235,0)",
              transition: "border-color 0.15s, box-shadow 0.15s",
            }}
          />
          <button style={{
            padding: "11px 18px", borderRadius: 10, border: "none",
            background: "#2563EB", color: "white",
            fontFamily: "inherit", fontSize: "0.85rem", fontWeight: 700,
            cursor: "pointer",
            boxShadow: "0 2px 8px rgba(37,99,235,0.3)",
          }}>Search</button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.45rem", marginBottom: "2rem" }}>
          {pills.map(p => (
            <span key={p} style={{
              padding: "6px 13px", borderRadius: 20,
              border: "1.5px solid #E2E8F0", background: "white",
              fontSize: "0.79rem", fontWeight: 500, color: "#475569", cursor: "pointer",
              boxShadow: "0 1px 3px rgba(15,23,42,0.05)",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.75rem", marginBottom: "1.75rem" }}>
          {[
            { v: "324", l: "Programs" },
            { v: "D1–D3", l: "All divisions" },
            { v: "NAIA", l: "Included" },
          ].map(({ v, l }) => (
            <div key={l} style={{
              background: "white", border: "1.5px solid #E2E8F0",
              borderRadius: 12, padding: "0.9rem 1rem", textAlign: "center",
              boxShadow: "0 2px 8px rgba(15,23,42,0.05)",
            }}>
              <div style={{ fontFamily: "'Plus Jakarta Sans', sans-serif", fontWeight: 800, fontSize: "1.35rem", color: "#2563EB", marginBottom: 2 }}>{v}</div>
              <div style={{ fontSize: "0.7rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "#94A3B8" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "#0F172A", borderRadius: 14, padding: "1.2rem 1.4rem",
          boxShadow: "0 4px 20px rgba(15,23,42,0.2)",
        }}>
          <div style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", color: "#93C5FD", marginBottom: "0.4rem" }}>
            The honest advisor
          </div>
          <div style={{ fontFamily: "'Plus Jakarta Sans', sans-serif", fontWeight: 700, fontSize: "1.05rem", color: "white", lineHeight: 1.45 }}>
            Every school gets a swim fit score and an admission likelihood — based on your actual numbers, not wishful thinking.
          </div>
        </div>
      </div>
    </div>
  );
}
