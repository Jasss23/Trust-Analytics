const h = React.createElement;
const HERO_ID = "q1_gtv_idr_by_asset_oct_2025";
const AUDIT_ID = "q5_gtv_mom_trend_oct_dec_2025";

const DEFAULT_QUESTION = "Which asset class should we prioritise for next month's growth plan?";

const ACTIONS = [
  { key: "ppt", label: "Generate PPT", detail: "Main slide + evidence appendix", icon: "presentation" },
  { key: "csv", label: "Download CSV", detail: "Displayed result rows", icon: "download" },
  { key: "copy", label: "Copy summary", detail: "4-line executive brief", icon: "copy" },
  { key: "email", label: "Draft email", detail: "Leadership-ready note", icon: "mail" },
  { key: "review", label: "Evidence room", detail: "SQL, sources, QA", icon: "shield" },
];

function money(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  return n.toLocaleString();
}

function titleCase(text) {
  return String(text || "").replace(/\b\w/g, s => s.toUpperCase());
}

function navigate(path) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function usePath() {
  const [path, setPath] = React.useState(window.location.pathname);
  React.useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function shapePayload(question, fields) {
  return {
    question_text: question,
    business_objective: fields.businessObjective,
    period: fields.period,
    segment: fields.segment,
    dimension: fields.dimension,
    audience: fields.audience,
    desired_output: fields.desiredOutput,
  };
}

function App() {
  const path = usePath();
  if (path.startsWith("/review/")) return h(Layout, { active: "review" }, h(ReviewPage, { id: path.split("/").pop() }));
  if (path.startsWith("/handoff/")) return h(Layout, { active: "handoff" }, h(HandoffPage, { id: path.split("/").pop() }));
  if (path.startsWith("/analysis/")) return h(Layout, { active: "analysis" }, h(AnalysisPage, { id: path.split("/").pop() }));
  if (path.startsWith("/library")) return h(Layout, { active: "library" }, h(LibraryPage));
  return h(Layout, { active: "ask" }, h(AskWorkspace));
}

function Layout({ active, children }) {
  return h("div", { className: "shell" },
    h("header", { className: "topbar" },
      h("div", { className: "topbar-left" },
        h("a", { className: "brand", href: "/", onClick: e => { e.preventDefault(); navigate("/"); } },
          h("span", { className: "brand-mark" }, h(Icon, { name: "shield" })),
          h("span", { className: "brand-copy" },
            h("strong", null, "Trust Analytics"),
            h("small", null, "Decision packs from verified SQL")
          )
        ),
        h("span", { className: "brand-divider" }),
        h("button", { className: "workspace-switch", type: "button", onClick: () => navigate("/") },
          "Ask workspace",
          h(Icon, { name: "chevron" })
        ),
        h("span", { className: "verified-chip" }, h(Icon, { name: "checkCircle" }), "SQL-backed")
      ),
      h("div", { className: "topbar-right" },
        h("nav", { className: "nav" },
          h(NavLink, { active: active === "ask", href: "/" }, "Ask"),
          h(NavLink, { active: active === "library", href: "/library" }, "Library"),
          h(NavLink, { active: active === "analysis", href: `/analysis/${HERO_ID}` }, "Build pack"),
          h(NavLink, { active: active === "review", href: `/review/${HERO_ID}` }, "Evidence room")
        ),
        h("span", { className: "user-menu" },
          h("span", { className: "avatar" }, "MK"),
          h("span", null,
            h("strong", null, "Maya Kim"),
            h("small", null, "BD Chief of Staff")
          ),
          h(Icon, { name: "chevron" })
        )
      )
    ),
    h("main", { className: "page" }, children)
  );
}

function NavLink({ href, active, children }) {
  return h("a", {
    className: active ? "nav-link active" : "nav-link",
    href,
    onClick: e => { e.preventDefault(); navigate(href); }
  }, children);
}

function Icon({ name }) {
  const common = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
  const paths = {
    presentation: [h("path", { d: "M3 4h18" }), h("path", { d: "M4 4v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4" }), h("path", { d: "M12 16v4" }), h("path", { d: "m8 20 4-4 4 4" })],
    download: [h("path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }), h("path", { d: "M7 10l5 5 5-5" }), h("path", { d: "M12 15V3" })],
    copy: [h("rect", { x: 9, y: 9, width: 13, height: 13, rx: 2 }), h("path", { d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" })],
    mail: [h("rect", { x: 3, y: 5, width: 18, height: 14, rx: 2 }), h("path", { d: "m3 7 9 6 9-6" })],
    shield: [h("path", { d: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" }), h("path", { d: "m9 12 2 2 4-5" })],
    database: [h("ellipse", { cx: 12, cy: 5, rx: 9, ry: 3 }), h("path", { d: "M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5" }), h("path", { d: "M3 12c0 1.7 4 3 9 3s9-1.3 9-3" })],
    warning: [h("path", { d: "m21.7 18-8.5-15a1.4 1.4 0 0 0-2.4 0L2.3 18a1.4 1.4 0 0 0 1.2 2h17a1.4 1.4 0 0 0 1.2-2Z" }), h("path", { d: "M12 8v4" }), h("path", { d: "M12 16h.01" })],
    check: [h("path", { d: "M20 6 9 17l-5-5" })],
    checkCircle: [h("path", { d: "M9 12l2 2 4-5" }), h("circle", { cx: 12, cy: 12, r: 9 })],
    info: [h("circle", { cx: 12, cy: 12, r: 9 }), h("path", { d: "M12 11v5" }), h("path", { d: "M12 8h.01" })],
    arrow: [h("path", { d: "M5 12h14" }), h("path", { d: "m12 5 7 7-7 7" })],
    search: [h("circle", { cx: 11, cy: 11, r: 8 }), h("path", { d: "m21 21-4.3-4.3" })],
    close: [h("path", { d: "M18 6 6 18" }), h("path", { d: "m6 6 12 12" })],
    chevron: [h("path", { d: "m6 9 6 6 6-6" })],
    target: [h("circle", { cx: 12, cy: 12, r: 8 }), h("circle", { cx: 12, cy: 12, r: 3 }), h("path", { d: "M12 2v3" }), h("path", { d: "M12 19v3" }), h("path", { d: "M2 12h3" }), h("path", { d: "M19 12h3" })],
    calendar: [h("rect", { x: 3, y: 5, width: 18, height: 16, rx: 2 }), h("path", { d: "M16 3v4" }), h("path", { d: "M8 3v4" }), h("path", { d: "M3 11h18" })],
    users: [h("path", { d: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" }), h("circle", { cx: 9, cy: 7, r: 4 }), h("path", { d: "M22 21v-2a4 4 0 0 0-3-3.87" }), h("path", { d: "M16 3.13a4 4 0 0 1 0 7.75" })],
    spark: [h("path", { d: "M12 3l1.7 4.6L18 9.2l-4.3 1.7L12 16l-1.7-5.1L6 9.2l4.3-1.6L12 3Z" }), h("path", { d: "M19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8L19 14Z" }), h("path", { d: "M5 14l.7 1.8L7.5 16.5l-1.8.7L5 19l-.7-1.8-1.8-.7 1.8-.7L5 14Z" })],
    table: [h("rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }), h("path", { d: "M3 10h18" }), h("path", { d: "M9 4v16" })],
    file: [h("path", { d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" }), h("path", { d: "M14 2v6h6" })],
  };
  return h("svg", common, ...(paths[name] || paths.check));
}

function Status({ status, showDescription = false }) {
  const tone = status?.tone || "review";
  return h("div", { className: `status-block tone-${tone}` },
    h("span", { className: "status-dot" }),
    h("span", null,
      h("strong", null, status?.label || "Loading"),
      showDescription ? h("small", null, status?.description || "") : null
    )
  );
}

function AskWorkspace() {
  const [question, setQuestion] = React.useState(DEFAULT_QUESTION);
  const [fields, setFields] = React.useState({
    businessObjective: "",
    period: "",
    segment: "",
    dimension: "",
    audience: "",
    desiredOutput: "",
  });
  const [shape, setShape] = React.useState(null);
  const [analyses, setAnalyses] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [run, setRun] = React.useState(null);

  React.useEffect(() => { api("/api/analyses").then(setAnalyses); }, []);
  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(() => {
      api("/api/question/shape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(shapePayload(question, fields))
      }).then(data => {
        if (!cancelled) setShape(data);
      }).finally(() => {
        if (!cancelled) setLoading(false);
      });
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [question, JSON.stringify(fields)]);

  React.useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(() => {
      api(`/api/runs/${run.id}`).then(data => {
        setRun(data);
        if (data.status === "completed" && data.resultId) {
          clearInterval(timer);
          navigate(`/analysis/${data.resultId}`);
        }
        if (data.status === "failed" || data.status === "needs_clarification") {
          clearInterval(timer);
        }
      }).catch(() => clearInterval(timer));
    }, 650);
    return () => clearInterval(timer);
  }, [run?.id, run?.status]);

  const setField = (key, value) => setFields(current => ({ ...current, [key]: value }));
  const confirmField = key => {
    const value = shape?.fields?.[key];
    if (value) setField(key, value);
  };
  const validate = async () => {
    const data = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_text: question, fields })
    });
    setRun(data);
  };
  const suggestions = [
    ["target", "Focus on growth priority", () => setField("businessObjective", "Prioritise the next growth focus")],
    ["warning", "Add source caveat", () => setField("desiredOutput", "Decision pack with source caveat")],
    ["table", "Compare by asset class", () => setField("dimension", "Asset class")],
    ["search", "Show examples", () => navigate("/library")],
  ];
  const objectiveValue = fields.businessObjective || shape?.fields?.businessObjective || "";
  const periodValue = fields.period || shape?.fields?.period || "";
  const segmentValue = fields.segment || shape?.fields?.segment || "";
  const dimensionValue = fields.dimension || shape?.fields?.dimension || "";
  const audienceValue = fields.audience || shape?.fields?.audience || "";
  const outputValue = fields.desiredOutput || shape?.fields?.desiredOutput || "";
  const states = shape?.fieldStates || {};
  const ready = Boolean(shape?.quality?.ready) && run?.status !== "running";

  return h(React.Fragment, null,
    h("section", { className: "copilot-workspace" },
      h(FlowRail),
      h("div", { className: "ask-main" },
        h("div", { className: "workspace-title" },
          h("div", null,
            h("span", { className: "kicker" }, "Ask"),
            h("h1", null, "Ask a business question")
          ),
          h("button", { className: "ghost-button", onClick: () => navigate("/library") }, h(Icon, { name: "search" }), "Examples")
        ),
        h("label", { className: "command-card" },
          h("span", { className: "sr-label" }, "Business question"),
          h("textarea", {
            className: "command-input",
            value: question,
            onChange: e => setQuestion(e.target.value),
            rows: 4,
            placeholder: "Ask what you need to decide, in plain language."
          }),
          h("div", { className: "command-footer" },
            h("span", { className: "copilot-state" }, h(Icon, { name: "check" }), "AI copilot active"),
            h("span", { className: "shape-state" }, loading ? "Shaping question..." : "Confirm inferred fields, then validate live"),
            h("button", { type: "button", className: "send-button", disabled: !ready, onClick: e => { e.preventDefault(); validate(); } }, h(Icon, { name: "arrow" }))
          )
        ),
        h("div", { className: "suggestion-row" },
          h(Icon, { name: "spark" }),
          suggestions.map(([, item, onClick]) => h("button", { className: "suggestion-chip", key: item, onClick },
            item
          ))
        ),
        h("div", { className: "field-stack" },
          h(FieldRow, {
            icon: "target",
            label: "Business objective",
            mode: "select",
            value: objectiveValue,
            placeholder: "Select business objective",
            options: shape?.suggestedChips?.businessObjective || [],
            state: states.businessObjective,
            onConfirm: () => confirmField("businessObjective"),
            onPick: v => setField("businessObjective", v),
            onClear: () => setField("businessObjective", "")
          }),
          h(FieldRow, {
            icon: "calendar",
            label: "Time period",
            mode: "segmented",
            value: periodValue,
            options: ["Last 30 days", "Last 90 days", "Current quarter", periodValue || "October 2025"],
            state: states.period,
            onConfirm: () => confirmField("period"),
            onPick: v => setField("period", v)
          }),
          h(FieldRow, {
            icon: "users",
            label: "Object / segment",
            mode: "tokens",
            value: segmentValue,
            options: shape?.suggestedChips?.segment || [],
            tokens: [segmentValue || "Completed trading activity"],
            state: states.segment,
            onConfirm: () => confirmField("segment"),
            onClear: () => setField("segment", ""),
            onPick: v => setField("segment", v)
          })
        ),
        h("details", { className: "context-drawer", open: true },
          h("summary", null, "Additional context", h("span", null, "optional")),
          h("div", { className: "field-stack compact" },
            h(FieldRow, {
              icon: "table",
              label: "Dimension",
              mode: "optional",
              value: dimensionValue,
              placeholder: "Select dimension (e.g., region, industry, product)",
              options: shape?.suggestedChips?.dimension || [],
              state: states.dimension,
              onConfirm: () => confirmField("dimension"),
              onPick: v => setField("dimension", v)
            }),
            h(FieldRow, {
              icon: "users",
              label: "Audience",
              mode: "optional",
              value: audienceValue,
              placeholder: "Select decision audience",
              options: shape?.suggestedChips?.audience || [],
              state: states.audience,
              onConfirm: () => confirmField("audience"),
              onPick: v => setField("audience", v)
            }),
            h(FieldRow, {
              icon: "presentation",
              label: "Desired output",
              mode: "optional",
              value: outputValue,
              placeholder: "Select output format (e.g., deck, report, extract)",
              options: shape?.suggestedChips?.desiredOutput || [],
              state: states.desiredOutput,
              onConfirm: () => confirmField("desiredOutput"),
              onPick: v => setField("desiredOutput", v)
            })
          )
        ),
        h("div", { className: "verified-bar" },
          h("span", { className: "verified-icon" }, h(Icon, { name: "database" })),
          h("strong", null, "Comparable example:"),
          h("span", null, shape?.recommendedAnalysisTitle || "Verified SQL analysis"),
          h("i", null),
          h("span", null, "Accounts warehouse"),
          h("i", null),
          h("span", null, "CRM + product usage"),
          h("button", { className: "bar-chevron", type: "button", onClick: () => navigate("/library") }, h(Icon, { name: "chevron" }))
        )
      ),
      h("aside", { className: "inspector-panel" },
        h(QuestionQuality, { shape, loading }),
        h("div", { className: "inspector-section" },
          h("div", { className: "section-minihead" },
            h("span", { className: "kicker" }, "Verified analysis path"),
            h("span", { className: "status-mini" }, "SQL-backed")
          ),
          h("strong", null, shape?.recommendedAnalysisTitle || "Asset-class growth priority pack"),
          h("p", null, shape?.verifiedPath?.reason || "Matched to a known-good evidence pack."),
          h("div", { className: "trust-badges" },
            ["Verified joins", "PII-safe", "Reusable"].map(item => h("span", { key: item }, item))
          )
        ),
        h(RunTimeline, { run, shape }),
        h(ArtifactPreview, { shape }),
        h("button", { className: "primary-wide build-cta", disabled: !ready, onClick: validate },
          h(Icon, { name: "presentation" }),
          run?.status === "running" ? "Validating live run..." : "Validate analysis"
        ),
        h("button", { className: "secondary-wide", onClick: () => navigate(`/review/${shape?.recommendedAnalysisId || HERO_ID}`) },
          h(Icon, { name: "shield" }),
          "Open evidence room"
        )
      )
    ),
    h("section", { className: "path-section" },
      h("div", { className: "section-heading" },
        h("div", null,
          h("span", { className: "kicker" }, "Verified paths"),
          h("h2", null, "Seed cases and successful validated asks live in Library")
        )
      ),
      h("div", { className: "path-grid" },
        analyses.map(a => h(VerifiedPathCard, { key: a.id, analysis: a, selected: shape?.recommendedAnalysisId === a.id }))
      )
    )
  );
}

function FlowRail() {
  const items = [
    ["1", "Ask", "Define the business question"],
    ["2", "Shape", "Add context and constraints"],
    ["3", "Validate", "Check logic and data quality"],
    ["4", "Package", "Build decision pack"],
  ];
  return h("aside", { className: "flow-rail" },
    items.map((item, index) => h("div", { className: index === 0 ? "rail-step active" : "rail-step", key: item[1] },
      h("span", { className: "rail-number" }, item[0]),
      h("span", null,
        h("strong", null, item[1]),
        h("small", null, item[2])
      )
    ))
  );
}

function FieldRow({ icon, label, mode = "select", value, placeholder, options = [], tokens = [], state, onPick, onClear, onConfirm }) {
  const displayValue = value || placeholder || "Not set";
  const uniqueOptions = Array.from(new Set(options.filter(Boolean)));
  const status = state?.status || (value ? "confirmed" : "missing");
  const visibleOptions = uniqueOptions.filter(option => option !== value).slice(0, 4);
  return h("div", { className: "field-row" },
    h("div", { className: "field-label" },
      h(Icon, { name: icon || "target" }),
      h("span", null, label),
      h(Icon, { name: "info" })
    ),
    h("div", { className: "field-control-wrap" },
      h(FieldControl, { mode, value, displayValue, options: uniqueOptions, tokens, onPick, onClear }),
      visibleOptions.length ? h("div", { className: "inline-options visible" },
        visibleOptions.map(option => h("button", {
          type: "button",
          key: option,
          className: "chip",
          onClick: () => onPick(option)
        }, option))
      ) : null,
      h("div", { className: `field-state ${status}` },
        h("span", null, status.replace("_", " ")),
        status === "inferred" ? h("button", { type: "button", onClick: onConfirm }, "Confirm") : null,
        status === "missing" ? h("small", null, "Add it here or make the question more explicit.") : null
      )
    )
  );
}

function FieldControl({ mode, value, displayValue, options, tokens, onPick, onClear }) {
  if (mode === "segmented") {
    const segments = Array.from(new Set(options.filter(Boolean))).slice(0, 4);
    return h("div", { className: "segmented-control" },
      segments.map(segment => h("button", {
        className: segment === value ? "segment active" : "segment",
        key: segment,
        onClick: () => onPick(segment),
        type: "button"
      }, segment)),
      h("button", { className: "calendar-button", type: "button", onClick: () => onPick(value || "October 2025") }, h(Icon, { name: "calendar" }))
    );
  }
  if (mode === "tokens") {
    return h("button", { className: "token-control", type: "button", onClick: () => onPick(options[0] || tokens[0]) },
      h("span", { className: "token-list" },
        tokens.filter(Boolean).slice(0, 3).map(token => h("span", { className: "input-token", key: token },
          token,
          h("span", { className: "token-x", onClick: e => { e.stopPropagation(); onClear?.(); } }, h(Icon, { name: "close" }))
        ))
      ),
      h(Icon, { name: "chevron" })
    );
  }
  if (mode === "optional") {
    return h("div", { className: value ? "optional-control filled" : "optional-control empty" },
      h("button", { type: "button", onClick: () => onPick(options[0] || value) },
        h("span", null, displayValue),
        h(Icon, { name: "chevron" })
      ),
      value && options.length ? h("div", { className: "inline-options" },
        options.slice(0, 2).map(option => h("button", {
          type: "button",
          key: option,
          className: option === value ? "chip selected" : "chip",
          onClick: () => onPick(option)
        }, option))
      ) : null
    );
  }
  return h("button", { className: "select-control", type: "button", onClick: () => onPick(options[0] || value || displayValue) },
    h(Icon, { name: "target" }),
    h("span", { className: value ? "" : "placeholder" }, displayValue),
    value ? h("span", { className: "clear-control", onClick: e => { e.stopPropagation(); onClear?.(); } }, h(Icon, { name: "close" })) : null,
    h(Icon, { name: "chevron" })
  );
}

function QuestionQuality({ shape, loading }) {
  const rows = [
    ["Business objective", shape?.fieldStates?.businessObjective?.status],
    ["Time period", shape?.fieldStates?.period?.status],
    ["Object / segment", shape?.fieldStates?.segment?.status],
    ["Comparison dimension", shape?.fieldStates?.dimension?.status],
    ["Decision audience", shape?.fieldStates?.audience?.status],
    ["Output format", shape?.fieldStates?.desiredOutput?.status],
  ];
  return h("div", { className: "inspector-section quality-section" },
    h("div", { className: "section-minihead" },
      h("span", { className: "kicker" }, "Question quality"),
      h("span", { className: "quality-score" }, loading ? "..." : `${shape?.quality?.score || 0}%`)
    ),
    h("div", { className: "quality-summary" },
      h("strong", null, shape?.quality?.label || "Shaping question"),
      h("p", null, shape?.canonicalQuestion || "The portal will infer the safest verified analysis path.")
    ),
    h("div", { className: "quality-list" },
      rows.map(([label, status]) => {
        const ok = status && status !== "missing";
        return h("div", { className: ok ? "quality-row ok" : "quality-row warn", key: label },
        h(Icon, { name: ok ? "check" : "warning" }),
        h("span", null, label),
        h("em", null, ok ? String(status).replace("_", " ") : "Missing")
      );
      })
    )
  );
}

function RunTimeline({ run, shape }) {
  const needs = run?.clarificationNeeds?.length ? run.clarificationNeeds : shape?.clarificationNeeds || [];
  return h("div", { className: "inspector-section run-section" },
    h("div", { className: "section-minihead" },
      h("span", { className: "kicker" }, "Validate"),
      h("span", { className: "status-mini" }, run?.status || shape?.quality?.label || "Ready")
    ),
    needs.length ? h("div", { className: "clarify-list" },
      needs.map(item => h("p", { key: item.key }, h(Icon, { name: "warning" }), item.message || `${item.label} is required.`))
    ) : null,
    run?.error ? h("p", { className: "run-error" }, `${run.error.type}: ${run.error.message}`) : null,
    h("div", { className: "stage-list" },
      (run?.stages || [
        { label: "Question shaping", state: shape?.quality?.ready ? "done" : "current" },
        { label: "Planner/source derivation", state: "pending" },
        { label: "SQL execution", state: "pending" },
        { label: "Pre-flight checks", state: "pending" },
        { label: "QA reconciliation", state: "pending" },
        { label: "Pack projection", state: "pending" },
      ]).map(stage => h("div", { className: `stage-row ${stage.state}`, key: stage.label },
        h("span", null),
        h("strong", null, stage.label),
        h("em", null, stage.state)
      ))
    )
  );
}

function ArtifactPreview({ shape }) {
  const artifacts = [
    ["presentation", "Executive slide", "PPT from Asset-class growth priority pack", "Draft"],
    ["download", "CSV extract", "Rows from displayed result table", "Ready"],
    ["mail", "Email draft", "Summary for leadership", "Draft"],
    ["shield", "Evidence appendix", "Queries, sources, and definitions", "Ready"],
  ];
  return h("div", { className: "inspector-section artifact-section" },
    h("div", { className: "section-minihead" },
      h("span", { className: "kicker" }, "Decision pack"),
      h("span", { className: "status-mini" }, "4 artifacts")
    ),
    artifacts.map(item => h("div", { className: "artifact-row", key: item[1] },
      h("span", { className: `artifact-icon ${item[0]}` }, h(Icon, { name: item[0] })),
      h("span", null,
        h("strong", null, item[1]),
        h("small", null, item[2])
      ),
      h("em", { className: item[3] === "Ready" ? "ready" : "" }, item[3])
    ))
  );
}

function ChipField({ label, value, options, onPick }) {
  return h("div", { className: "chip-field" },
    h("div", { className: "chip-head" },
      h("span", null, label),
      value ? h("strong", null, value) : h("em", null, "Needs input")
    ),
    h("div", { className: "chip-row" },
      options.slice(0, 4).map(option => h("button", {
        key: option,
        className: option === value ? "chip selected" : "chip",
        onClick: () => onPick(option)
      }, option))
    )
  );
}

function VerifiedPathCard({ analysis, selected }) {
  const route = analysis.audit?.required ? `/handoff/${analysis.id}` : `/analysis/${analysis.id}`;
  return h("article", { className: selected ? "path-card selected" : "path-card" },
    h("div", { className: "path-card-top" },
      h(Status, { status: analysis.status }),
      selected ? h("span", { className: "selected-mark" }, h(Icon, { name: "check" }), "Matched") : null
    ),
    h("h3", null, analysis.question),
    h("p", null, analysis.headline),
    h("button", { className: "secondary", onClick: () => navigate(route) },
      analysis.audit?.required ? "Open audit brief" : "Open pack"
    )
  );
}

function LibraryPage() {
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  React.useEffect(() => {
    api("/api/library").then(setItems).finally(() => setLoading(false));
  }, []);
  if (loading) return h(LoadingPanel, { label: "Opening success library..." });
  const seed = items.filter(item => item.source === "seed");
  const ask = items.filter(item => item.source === "ask");
  return h(React.Fragment, null,
    h("section", { className: "subpage-hero library-hero" },
      h("div", null,
        h("span", { className: "kicker" }, "Library"),
        h("h1", null, "Successful analyses and seed demo stories."),
        h("p", null, "Open a decision pack first, then drill into evidence when an analyst needs to challenge the result.")
      ),
      h("button", { onClick: () => navigate("/") }, h(Icon, { name: "arrow" }), "Ask new question")
    ),
    h(LibrarySection, { title: "Seed demo stories", items: seed }),
    h(LibrarySection, { title: "Validated free asks", items: ask, empty: "Validated free Ask runs will appear here after a live run succeeds." })
  );
}

function LibrarySection({ title, items, empty }) {
  return h("section", { className: "library-section" },
    h("div", { className: "section-heading" },
      h("div", null,
        h("span", { className: "kicker" }, `${items.length} cases`),
        h("h2", null, title)
      )
    ),
    items.length ? h("div", { className: "library-grid" },
      items.map(item => h(LibraryCard, { key: `${item.source}-${item.id}`, item }))
    ) : h("div", { className: "empty-library" }, empty || "No cases yet.")
  );
}

function LibraryCard({ item }) {
  const route = `/analysis/${item.id}`;
  return h("article", { className: "library-card" },
    h("div", { className: "path-card-top" },
      h(Status, { status: item.status }),
      h("span", { className: "status-mini" }, item.source === "seed" ? "Seed" : "Ask")
    ),
    h("h3", null, item.decisionPack?.title || item.metricName),
    h("p", { className: "question-line small" }, item.question),
    h("p", null, item.headline),
    item.status?.label === "Audit required" ? h("p", { className: "audit-label" }, "Outputs are available with audit-required labeling.") : null,
    h("div", { className: "library-actions" },
      h("button", { onClick: () => navigate(route) }, "Open pack", h(Icon, { name: "arrow" })),
      h("button", { className: "secondary", onClick: () => navigate(`/review/${item.id}`) }, "Evidence")
    )
  );
}

function FlowSteps({ steps = [] }) {
  return h("div", { className: "flow-steps compact" },
    steps.map((step, index) => h("button", {
      className: `flow-step ${step.state}`,
      key: step.label,
      onClick: () => document.getElementById(step.label.toLowerCase())?.scrollIntoView({ behavior: "smooth", block: "start" })
    },
      h("span", { className: "step-index" }, String(index + 1).padStart(2, "0")),
      h("span", null,
        h("strong", null, step.label),
        h("small", null, step.detail)
      )
    ))
  );
}

function AnalysisPage({ id }) {
  const [analysis, setAnalysis] = React.useState(null);
  const [draftOpen, setDraftOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const [generated, setGenerated] = React.useState(false);
  React.useEffect(() => {
    setAnalysis(null);
    api(`/api/analysis/${id}`).then(setAnalysis);
  }, [id]);
  if (!analysis) return h(LoadingPanel, { label: "Preparing decision pack..." });

  const copySummary = async () => {
    await navigator.clipboard.writeText(analysis.executiveSummary.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return h(React.Fragment, null,
    h("section", { className: "pack-workspace" },
      h("div", { className: "pack-main" },
        h("div", { className: "reliability-row" },
          h(Status, { status: analysis.status }),
          h("span", { className: analysis.fromCache ? "reliability-badge cache" : "reliability-badge live" },
            h(Icon, { name: analysis.fromCache ? "database" : "check" }),
            analysis.fromCache ? "Verified cached result" : "Live run result"
          )
        ),
        analysis.fromCache && analysis.liveError ? h("p", { className: "cache-note" }, `Showing nearest verified analysis path. ${analysis.liveError}`) : null,
        analysis.status?.label === "Audit required" ? h("p", { className: "cache-note audit" }, "Audit required: exports remain available, but every output must carry this caveat and evidence appendix.") : null,
        h("section", { className: "decision-summary", id: "assess" },
          h("span", { className: "kicker" }, "Decision summary"),
          h("p", { className: "question-line" }, analysis.question),
          h("h1", null, analysis.headline),
          h("p", { className: "recommendation-text" }, analysis.recommendation)
        ),
        h("section", { className: "chart-card focus-chart", id: "package" },
          h("div", { className: "module-head" },
            h("div", null,
              h("span", { className: "kicker" }, "Evidence-backed result"),
              h("h2", null, analysis.chart.type === "line" ? "Trend view" : "Asset-class contribution")
            ),
            h("span", { className: "meta" }, analysis.chart.unit || "")
          ),
          h(Chart, { chart: analysis.chart }),
          h("p", { className: "insight" }, analysis.chartInsight)
        ),
        h("section", { className: "context-strip" },
          h(Boundary, { label: "Use this for", value: analysis.useThisFor, tone: "ready" }),
          h(Boundary, { label: "Do not use for", value: analysis.doNotUseFor, tone: "caution" }),
          h("div", { className: "caveat-box" },
            h("strong", null, "Source caveat"),
            h("span", null, analysis.sourceCaveat)
          )
        )
      ),
      h("aside", { className: "pack-actions", id: "review" },
        h("span", { className: "kicker" }, analysis.decisionPack?.title || "Decision pack"),
        h("h2", null, "Package this analysis"),
        h("p", null, analysis.recommendedUse),
        h(FlowSteps, { steps: analysis.workflowSteps || [] }),
        h("div", { className: "action-stack" },
          ACTIONS.map(action => h(ActionButton, {
            key: action.key,
            action,
            analysis,
            copied,
            generated,
            onGenerated: () => setGenerated(true),
            onCopy: copySummary,
            onEmail: () => setDraftOpen(true)
          }))
        )
      )
    ),
    draftOpen ? h(EmailDrawer, { analysis, onClose: () => setDraftOpen(false) }) : null
  );
}

function ActionButton({ action, analysis, copied, generated, onGenerated, onCopy, onEmail }) {
  if (action.key === "ppt") {
    return h("a", { className: "rail-action primary", href: `/api/analysis/${analysis.id}/deck.pptx`, onClick: onGenerated },
      h("span", { className: "action-icon" }, h(Icon, { name: generated ? "check" : action.icon })),
      h("span", null, h("strong", null, generated ? "PPT generated" : action.label), h("small", null, action.detail))
    );
  }
  if (action.key === "csv") {
    return h("a", { className: "rail-action", href: `/api/analysis/${analysis.id}/export.csv` },
      h("span", { className: "action-icon" }, h(Icon, { name: action.icon })),
      h("span", null, h("strong", null, action.label), h("small", null, action.detail))
    );
  }
  if (action.key === "copy") {
    return h("button", { className: "rail-action", onClick: onCopy },
      h("span", { className: "action-icon" }, h(Icon, { name: copied ? "check" : action.icon })),
      h("span", null, h("strong", null, copied ? "Copied" : action.label), h("small", null, action.detail))
    );
  }
  if (action.key === "email") {
    return h("button", { className: "rail-action", onClick: onEmail },
      h("span", { className: "action-icon" }, h(Icon, { name: action.icon })),
      h("span", null, h("strong", null, action.label), h("small", null, action.detail))
    );
  }
  return h("button", { className: "rail-action", onClick: () => navigate(`/review/${analysis.id}`) },
    h("span", { className: "action-icon" }, h(Icon, { name: action.icon })),
    h("span", null, h("strong", null, action.label), h("small", null, action.detail))
  );
}

function Boundary({ label, value, tone }) {
  return h("div", { className: `boundary ${tone}` },
    h("strong", null, label),
    h("p", null, value)
  );
}

function ConfidenceGrid({ confidence }) {
  const items = [
    ["Business status", confidence.business],
    ["Correctness", confidence.correctness],
    ["Source reliability", confidence.sourceReliability],
    ["Ambiguity", confidence.ambiguity],
  ];
  return h("div", { className: "confidence-grid" },
    items.map(([label, value]) => h("div", { className: "confidence-item", key: label },
      h("span", null, label),
      h("strong", null, value)
    ))
  );
}

function Chart({ chart }) {
  if (chart.type === "line") {
    const max = Math.max(...chart.values, 1);
    return h("div", { className: "line-chart" },
      chart.values.map((value, i) => h("div", { className: "line-point", key: chart.labels[i] },
        h("span", { className: "line-value" }, money(value)),
        h("span", { className: "line-stem", style: { height: `${Math.max(24, value / max * 190)}px` } }),
        h("span", { className: "line-dot" }),
        h("span", { className: "bar-label" }, chart.labels[i])
      ))
    );
  }
  const max = Math.max(...chart.values, 1);
  return h("div", { className: "chart" },
    chart.values.map((value, i) => h("button", { className: "bar-row", key: chart.labels[i] },
      h("span", { className: "bar-label" }, titleCase(chart.labels[i])),
      h("span", { className: "bar-track" }, h("span", { className: "bar-fill", style: { width: `${value / max * 100}%` } })),
      h("span", { className: "bar-value" }, money(value))
    ))
  );
}

function EmailDrawer({ analysis, onClose }) {
  const text = `Subject: ${analysis.emailDraft.subject}\n\n${analysis.emailDraft.body}`;
  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
  };
  return h("aside", { className: "drawer" },
    h("div", { className: "drawer-head" },
      h("div", null,
        h("span", { className: "kicker" }, "Pack output"),
        h("h2", null, "Email draft")
      ),
      h("button", { className: "icon-button", onClick: onClose, title: "Close" }, h(Icon, { name: "close" }))
    ),
    h("p", { className: "muted" }, "Prepared as a concise leadership update. No email is sent from this public demo."),
    h("textarea", { readOnly: true, value: text }),
    h("button", { className: "primary-wide", onClick: copy }, copied ? "Draft copied" : "Copy draft", h(Icon, { name: copied ? "check" : "copy" }))
  );
}

function ReviewPage({ id }) {
  const [analysis, setAnalysis] = React.useState(null);
  const [tab, setTab] = React.useState("source");
  React.useEffect(() => { api(`/api/analysis/${id}`).then(setAnalysis); }, [id]);
  if (!analysis) return h(LoadingPanel, { label: "Opening evidence room..." });
  const ev = analysis.analystEvidence;
  const source = ev.source || {};
  return h(React.Fragment, null,
    h("section", { className: "subpage-hero evidence-hero" },
      h("div", null,
        h("span", { className: "kicker" }, "Evidence room"),
        h("h1", null, "Why this answer is defensible."),
        h("p", null, analysis.question)
      ),
      h(Status, { status: analysis.status, showDescription: true })
    ),
    h("section", { className: "evidence-shell" },
      h("div", { className: "tabs" },
        ["source", "sql", "qa", "challenge"].map(item => h("button", { className: tab === item ? "tab active" : "tab", onClick: () => setTab(item), key: item }, titleCase(item)))
      ),
      tab === "source" ? h("div", { className: "evidence-card" },
        h("span", { className: "kicker" }, "Source provenance"),
        h("h2", null, source.primary_table || "Unknown source"),
        h("p", null, source.why_chosen || "No source rationale available."),
        h(SourceTable, { rows: analysis.sourceComparison })
      ) : null,
      tab === "sql" ? h("div", { className: "evidence-card" },
        h("span", { className: "kicker" }, "Executed SQL"),
        h("pre", null, ev.sql)
      ) : null,
      tab === "qa" ? h("div", { className: "evidence-card" },
        h("span", { className: "kicker" }, "Quality checks"),
        h(ConfidenceGrid, { confidence: analysis.confidence })
      ) : null,
      tab === "challenge" ? h("div", { className: "evidence-card" },
        h("span", { className: "kicker" }, "Challenge notes"),
        h("ul", { className: "evidence-list" }, analysis.executiveSummary.map((item, idx) => h("li", { key: idx }, item)))
      ) : null
    )
  );
}

function SourceTable({ rows }) {
  return h("table", { className: "table" },
    h("thead", null, h("tr", null, h("th", null, "Source"), h("th", null, "Delta"), h("th", null, "Notes"))),
    h("tbody", null, rows.map((r, i) => h("tr", { key: i },
      h("td", null, r.source),
      h("td", null, r.deltaVsPrimary == null ? "n/a" : `${Number(r.deltaVsPrimary).toFixed(2)}%`),
      h("td", null, r.notes || "")
    )))
  );
}

function HandoffPage({ id }) {
  const [analysis, setAnalysis] = React.useState(null);
  React.useEffect(() => { api(`/api/analysis/${id}`).then(setAnalysis); }, [id]);
  if (!analysis) return h(LoadingPanel, { label: "Preparing audit brief..." });
  const audit = analysis.audit;
  return h(React.Fragment, null,
    h("section", { className: "subpage-hero blocked" },
      h("div", null,
        h("span", { className: "kicker" }, "Audit brief"),
        h("h1", null, "Decision blocked until source conflict is resolved."),
        h("p", null, audit.reason || analysis.status.description)
      ),
      h(Status, { status: analysis.status, showDescription: true })
    ),
    h("section", { className: "audit-grid" },
      h("div", { className: "audit-panel" },
        h("span", { className: "kicker" }, "Blocking source"),
        h("h2", null, "Monthly summary conflict"),
        h("p", null, "The December monthly-summary total diverges from the canonical completed-transaction mart.")
      ),
      h("div", { className: "audit-panel" },
        h("span", { className: "kicker" }, "Safe interim use"),
        h("h2", null, "Analyst discussion only"),
        h("p", null, "Use the canonical daily mart for working analysis, but do not package the trend for leadership.")
      ),
      h("div", { className: "audit-panel span-2" },
        h("span", { className: "kicker" }, "Resolution checklist"),
        h("ol", { className: "checklist" }, (audit.nextActions || []).map((a, i) => h("li", { key: i }, h(Icon, { name: "check" }), h("span", null, a)))),
        h("button", { onClick: () => navigate(`/review/${id}`) }, "Open evidence room", h(Icon, { name: "arrow" }))
      )
    )
  );
}

function LoadingPanel({ label }) {
  return h("div", { className: "loading-panel" },
    h("span", { className: "loader" }),
    h("strong", null, label)
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
