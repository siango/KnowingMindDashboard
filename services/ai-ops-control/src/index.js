const express = require("express");
const app = express();

app.use(express.json({ limit: "1mb" }));

// READYZ ONLY (policy): must exist
app.get("/readyz", (_req, res) => res.status(200).json({ ok: true, service: "kms-ai-ops-control" }));

// Explicitly forbid /healthz: must be 404 (no route registered)
app.post("/v1/commands", (req, res) => {
  // Minimal validation to avoid garbage in SSOT queue
  const body = req.body || {};
  const cmd = {
    id: String(body.id || ""),
    created_at: body.created_at || new Date().toISOString(),
    owner: body.owner || "unknown",
    device_target: body.device_target || "termux-main",
    approved: String(body.approved || "NO"),
    kind: body.kind || "NOOP",
    payload: body.payload || {}
  };

  if (!cmd.id) return res.status(400).json({ ok: false, error: "missing id" });
  if (!["YES", "NO"].includes(cmd.approved)) return res.status(400).json({ ok: false, error: "approved must be YES/NO" });

  // In this phase, Control API only ACKs. SSOT write happens from Termux loop (source of truth is GCS).
  // This keeps Cloud Run stateless and reduces permission surface.
  return res.status(200).json({ ok: true, accepted: true, cmd });
});

const port = process.env.PORT || 8080;
app.listen(port, () => console.log(`[kms-ai-ops-control] listening on ${port}`));
