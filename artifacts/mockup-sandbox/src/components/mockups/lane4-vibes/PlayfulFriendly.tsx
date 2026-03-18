export function PlayfulFriendly() {
  const pills = ["D1 powerhouses 🏆", "NESCAC schools", "STEM + swimming 🧪", "Southeast D2", "Small liberal arts 📚"];
  return (
    <div style={{ fontFamily: "'Poppins', sans-serif", background: "#FFFFFF", minHeight: "100vh", color: "#1E293B" }}>
      <div style={{ background: "linear-gradient(135deg, #EFF6FF 0%, #F0FDFA 100%)", paddingBottom: "0.5px" }}>
        <nav style={{
          height: 56, display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "0 1.5rem", maxWidth: 900, margin: "0 auto",
        }}>
          <span style={{ fontFamily: "'Poppins', sans-serif", fontWeight: 800, fontSize: "1.1rem" }}>
            <span style={{ color: "#0EA5E9" }}>Lane</span><span style={{ color: "#0F766E" }}>4</span>
            <span style={{ fontSize: "0.75rem", marginLeft: 6, background: "#ECFDF5", color: "#059669", padding: "2px 7px", borderRadius: 20, fontWeight: 700 }}>BETA</span>
          </span>
          <div style={{ display: "flex", gap: 4 }}>
            {["Explore", "My List", "Reminders", "Profile"].map((t, i) => (
              <span key={t} style={{
                padding: "6px 14px", height: 36, display: "inline-flex", alignItems: "center",
                fontSize: "0.82rem", fontWeight: 600,
                color: i === 0 ? "#0EA5E9" : "#64748B",
                background: i === 0 ? "rgba(14,165,233,0.1)" : "transparent",
                borderRadius: 20, cursor: "pointer",
              }}>{t}</span>
            ))}
          </div>
        </nav>

        <div style={{ maxWidth: 680, margin: "0 auto", padding: "2.5rem 1.5rem 3.5rem", textAlign: "center" }}>
          <div style={{ fontSize: "2.5rem", marginBottom: "0.5rem" }}>🏊‍♀️</div>
          <h1 style={{
            fontFamily: "'Poppins', sans-serif", fontWeight: 800,
            fontSize: "1.9rem", marginBottom: "0.6rem", lineHeight: 1.2,
            background: "linear-gradient(135deg, #0EA5E9, #0F766E)",
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          }}>
            Find your perfect swim program
          </h1>
          <p style={{ fontSize: "0.92rem", color: "#64748B", marginBottom: "1.75rem", lineHeight: 1.65, maxWidth: 480, margin: "0 auto 1.75rem" }}>
            We match your times and academics to programs where you'll compete and thrive. 324 schools, all divisions.
          </p>

          <div style={{ display: "flex", gap: "0.5rem", maxWidth: 540, margin: "0 auto 1rem", padding: "0 0.5rem" }}>
            <input
              placeholder="School name or ask anything…"
              style={{
                flex: 1, padding: "12px 18px",
                border: "2px solid #BAE6FD", borderRadius: 999,
                fontFamily: "'Poppins', sans-serif", fontSize: "0.9rem",
                color: "#1E293B", background: "white",
                outline: "none",
                boxShadow: "0 4px 20px rgba(14,165,233,0.1)",
              }}
            />
            <button style={{
              padding: "12px 22px", borderRadius: 999, border: "none",
              background: "linear-gradient(135deg, #0EA5E9, #0891B2)",
              color: "white", fontFamily: "'Poppins', sans-serif",
              fontSize: "0.88rem", fontWeight: 700, cursor: "pointer",
              boxShadow: "0 4px 16px rgba(14,165,233,0.35)",
            }}>Search ✨</button>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.45rem", justifyContent: "center", marginBottom: "2.25rem", padding: "0 0.5rem" }}>
            {pills.map(p => (
              <span key={p} style={{
                padding: "7px 15px", borderRadius: 999,
                border: "1.5px solid #BAE6FD", background: "white",
                fontSize: "0.79rem", fontWeight: 500, color: "#0369A1", cursor: "pointer",
                boxShadow: "0 2px 8px rgba(14,165,233,0.08)",
              }}>{p}</span>
            ))}
          </div>
        </div>
      </div>

      <div style={{ maxWidth: 680, margin: "0 auto", padding: "2rem 1.5rem" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "0.85rem", marginBottom: "1.75rem" }}>
          {[
            { emoji: "🎯", v: "324", l: "Programs scored" },
            { emoji: "📊", v: "D1–NAIA", l: "All divisions" },
            { emoji: "🔓", v: "Free", l: "Always" },
          ].map(({ emoji, v, l }) => (
            <div key={l} style={{
              background: "white", border: "2px solid #E0F2FE",
              borderRadius: 16, padding: "1.1rem 1rem", textAlign: "center",
              boxShadow: "0 4px 16px rgba(14,165,233,0.07)",
            }}>
              <div style={{ fontSize: "1.4rem", marginBottom: 4 }}>{emoji}</div>
              <div style={{ fontFamily: "'Poppins', sans-serif", fontWeight: 800, fontSize: "1.25rem", color: "#0EA5E9", marginBottom: 2 }}>{v}</div>
              <div style={{ fontSize: "0.72rem", fontWeight: 600, color: "#64748B" }}>{l}</div>
            </div>
          ))}
        </div>

        <div style={{
          background: "linear-gradient(135deg, #F0FDFA, #EFF6FF)",
          border: "2px solid #BAE6FD", borderRadius: 18,
          padding: "1.5rem 1.75rem",
          display: "flex", alignItems: "center", gap: "1.25rem",
        }}>
          <div style={{ fontSize: "2rem", flexShrink: 0 }}>⚡️</div>
          <div>
            <div style={{ fontFamily: "'Poppins', sans-serif", fontWeight: 700, fontSize: "1rem", color: "#0F172A", marginBottom: "0.25rem" }}>
              How it works
            </div>
            <div style={{ fontSize: "0.85rem", color: "#475569", lineHeight: 1.6 }}>
              Add your times and GPA in Profile → get honest swim fit + admission scores for every program → save your favorites to My List
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
