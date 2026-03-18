export function WarmAuthority() {
  const pills = ["D1 powerhouses", "NESCAC schools", "STEM + swimming", "Southeast D2", "Small liberal arts"];
  return (
    <div style={{ fontFamily: "'DM Sans', sans-serif", background: "#FAF8F3", minHeight: "100vh", color: "#1C1008" }}>
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: "#FEFCF7", borderBottom: "1px solid #E4D9C8",
        height: 56, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 1.5rem",
      }}>
        <span style={{ fontFamily: "'Playfair Display', serif", fontWeight: 800, fontSize: "1.15rem", color: "#2D6A4F", letterSpacing: "-0.01em" }}>
          Lane4
        </span>
        <div style={{ display: "flex", gap: 0 }}>
          {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
            <span key={t} style={{
              padding: "0 1.1rem", height: 56, display: "inline-flex", alignItems: "center",
              fontSize: "0.84rem", fontWeight: i === 0 ? 700 : 500,
              color: i === 0 ? "#2D6A4F" : "#8A7560",
              borderBottom: i === 0 ? "2px solid #2D6A4F" : "2px solid transparent",
              cursor: "pointer",
            }}>{t}</span>
          ))}
        </div>
      </nav>

      <div style={{ maxWidth: 680, margin: "0 auto", padding: "3rem 1.5rem 5rem" }}>
        <div style={{ marginBottom: "0.3rem" }}>
          <span style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.12em", color: "#B08D57" }}>
            Swim Recruiting Advisor
          </span>
        </div>
        <h1 style={{
          fontFamily: "'Playfair Display', serif", fontWeight: 700,
          fontSize: "1.75rem", color: "#1C1008", marginBottom: "0.6rem", lineHeight: 1.25,
        }}>
          Find your schools
        </h1>
        <p style={{ fontSize: "0.9rem", color: "#8A7560", marginBottom: "1.75rem", lineHeight: 1.6 }}>
          Tell us what you're looking for — we'll match your times and academics to programs where you'll thrive.
        </p>

        <div style={{ display: "flex", gap: "0.6rem", marginBottom: "1rem" }}>
          <input
            placeholder="School name or ask anything…"
            style={{
              flex: 1, padding: "11px 16px",
              border: "1.5px solid #D9CCB8", borderRadius: 10,
              fontFamily: "'DM Sans', sans-serif", fontSize: "0.92rem",
              color: "#1C1008", background: "#FEFCF7",
              outline: "none",
            }}
          />
          <button style={{
            padding: "11px 20px", borderRadius: 10, border: "none",
            background: "#2D6A4F", color: "white",
            fontFamily: "'DM Sans', sans-serif", fontSize: "0.88rem", fontWeight: 600,
            cursor: "pointer", whiteSpace: "nowrap",
          }}>Search</button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginBottom: "2.5rem" }}>
          {pills.map(p => (
            <span key={p} style={{
              padding: "6px 14px", borderRadius: 20,
              border: "1.5px solid #D9CCB8", background: "#FEFCF7",
              fontSize: "0.8rem", fontWeight: 500, color: "#6B5744", cursor: "pointer",
            }}>{p}</span>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.85rem", marginBottom: "2rem" }}>
          {[
            { v: "324", l: "Programs" },
            { v: "D1–D3", l: "All divisions" },
            { v: "NAIA", l: "Included" },
          ].map(({ v, l }) => (
            <div key={l} style={{
              background: "#FEFCF7", border: "1.5px solid #E4D9C8",
              borderRadius: 12, padding: "1rem 1.1rem", textAlign: "center",
            }}>
              <div style={{ fontFamily: "'Playfair Display', serif", fontWeight: 700, fontSize: "1.4rem", color: "#2D6A4F", marginBottom: 2 }}>{v}</div>
              <div style={{ fontSize: "0.72rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.07em", color: "#A08060" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "#2D6A4F", borderRadius: 14, padding: "1.25rem 1.4rem",
          display: "flex", alignItems: "flex-start", gap: "1rem",
        }}>
          <div style={{ fontSize: "1.4rem" }}>🏊</div>
          <div>
            <div style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", color: "#A8D5BE", marginBottom: "0.3rem" }}>
              How it works
            </div>
            <div style={{ fontFamily: "'Playfair Display', serif", fontWeight: 600, fontSize: "1rem", color: "white", lineHeight: 1.45 }}>
              Enter your times and GPA. We rank every program by how well your profile fits — swim-first, academics second.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
