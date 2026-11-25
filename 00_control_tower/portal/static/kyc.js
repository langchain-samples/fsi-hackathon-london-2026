/* KYC tab: the KYC process map as a working screen.

   Loaded before the portal's main script and called from its poll(). Uses the portal's helpers and state:
   paint, esc, para, words, post, pick, show, $, fieldNames, state, selected, tab, shown.
   Data: GET /api/kyc/pipeline and GET /api/kyc/case/{id} (portal/kyc_tab.py). Decisions go through the same
   POST /api/action/{id} as the Dashboard queue, so there is still one door for a human decision. */

const KYC_FILTERS = [["all", "All"], ["approval", "Waiting for a decision"], ["new", "New"], ["onboarded", "Onboarded"],
                     ["reassessment_due", "Re-assessment due"], ["refresh_due", "Refresh due"], ["rejected", "Rejected"]];
const KYC_STAGE = {new: "New application", approval: "Waiting for a decision", onboarded: "Onboarded", rejected: "Rejected",
                   reassessment_due: "Re-assessment due", refresh_due: "Refresh due"};
const KYC_STATUS = {done: "Done", active: "Active", current: "Next", waiting: "Waiting for a human", todo: "Not started"};
const KYC_ORDER = {approval: 0, refresh_due: 1, reassessment_due: 2, new: 3, onboarded: 4, rejected: 5};
let kycFilter = localStorage.getItem("kycFilter") || "all", kycPipe = null, kycCase = null, kycLoading = false;

async function kycGet(url) { const r = await fetch(url); return r.ok ? r.json() : null; }

function kycSetFilter(f) {
  kycFilter = kycFilter === f && f !== "all" ? "all" : f;
  localStorage.setItem("kycFilter", kycFilter);
  shown["#kyc-flow"] = shown["#kyc-filters"] = shown["#kyc-cases"] = null;
  kycPaint();
}

const kycDecide = (id, decision) =>
  post(`/api/action/${encodeURIComponent(id)}`, {decision, note: document.getElementById("kyc-note-" + id)?.value || ""});

async function kycRender() {
  if (tab !== "kyc" || kycLoading) return;
  kycLoading = true;
  try {
    kycPipe = await kycGet("/api/kyc/pipeline");
    kycCase = selected ? await kycGet(`/api/kyc/case/${encodeURIComponent(selected)}`) : null;
  } finally { kycLoading = false; }
  kycPaint();
}

function kycPaint() {
  if (!kycPipe) return;
  paint("#kyc-flow", JSON.stringify([kycPipe.stages, kycFilter, kycPipe.live_policy]), kycFlow(kycPipe));
  paint("#kyc-filters", JSON.stringify([kycPipe.stages, kycFilter]), KYC_FILTERS.map(([id, label]) => {
    const n = id === "all" ? kycPipe.cases.length : (kycPipe.stages.find(s => s.id === id)?.n ?? 0);
    return `<button class="kyc-chip ${kycFilter === id ? "on" : ""}" onclick="kycSetFilter('${id}')">${esc(label)} · ${n}</button>`;
  }).join(""));
  const b = kycPipe.bulk || {}, busy = !!state.status.running;
  paint("#kyc-bulk", JSON.stringify([b, busy]),
    `<button class="act ${b.new ? "next" : ""}" ${busy || !b.new ? "disabled" : ""} onclick="post('/api/kyc/screen-new')"
       title="Screens every new application, one by one. High-risk cases wait for you in the action queue.">Screen all new (${b.new || 0})</button>
     <button class="act ${b.reassessment_due ? "next" : ""}" ${busy || !b.reassessment_due ? "disabled" : ""} onclick="post('/api/kyc/reassess-due')"
       title="Re-assesses every client last assessed under an older policy.">Re-assess all due (${b.reassessment_due || 0})</button>`);
  kycCases();
  const actions = state.actions.filter(a => a.customer_id === selected && a.pillar === "kyc");
  paint("#kyc-case", JSON.stringify([kycCase, !!state.status.running, actions]), kycCaseHtml(kycCase, actions));
}

// ---- The map, with how many cases sit at each stage

function kycNode(s, live) {
  const n = s.n ?? 0, hot = s.count && n && ["new", "approval", "reassessment_due", "refresh_due"].includes(s.id);
  const value = s.count ? `<span class="c ${n ? "" : "zero"}">${n}</span>`
              : `<span class="c">${s.id === "trigger" ? "live: " + esc(live) : "automatic"}</span>`;
  return `<button class="kyc-node ${s.count ? "count" : "pass"} ${kycFilter === s.id ? "on" : ""} ${hot ? "hot" : ""}"
    ${s.count ? `onclick="kycSetFilter('${esc(s.id)}')" title="Show these cases"` : "disabled"}>
    <span class="m">${esc(s.map)}</span><span class="l">${esc(s.label)}</span>${value}</button>`;
}

function kycFlow(p) {
  const at = id => kycNode(p.stages.find(s => s.id === id), p.live_policy), arrow = '<span class="kyc-arrow">→</span>';
  return `<div class="kyc-lane-title">Onboarding</div>
    <div class="kyc-lane">${["new", "profile", "screening", "risk", "approval"].map(at).join(arrow)}${arrow}
      <div class="kyc-ends">${at("onboarded")}${at("rejected")}</div></div>
    <div class="kyc-lane-title">Ongoing monitoring</div>
    <div class="kyc-lane">${["trigger", "reassessment_due", "refresh_due"].map(at).join(arrow)}${arrow}
      <div class="kyc-loop">Back to START: the full KYC process runs again under the live policy.</div></div>`;
}

// ---- Case list

function kycCases() {
  if (!kycPipe) return;
  const q = ($("#kyc-find")?.value || "").trim().toLowerCase();
  const rows = kycPipe.cases
    .filter(c => (kycFilter === "all" || c.stage === kycFilter) && (!q || `${c.name} ${c.customer_id}`.toLowerCase().includes(q)))
    .sort((a, b) => (KYC_ORDER[a.stage] - KYC_ORDER[b.stage]) || a.customer_id.localeCompare(b.customer_id));
  const rating = c => c.rating ? `<span class="rating r-${esc(c.rating)}">${esc(c.rating.toUpperCase())}</span>${
    c.previous && c.previous.rating !== c.rating ? ` <span class="by">was ${esc(c.previous.rating)}</span>` : ""}` : "";
  paint("#kyc-cases", JSON.stringify([rows, selected]), rows.length ? `<table><thead><tr><th>Customer</th><th>Stage</th>
    <th>Rating</th><th>Assessed under</th><th>Next</th></tr></thead><tbody>${rows.map(c =>
    `<tr class="pick ${c.customer_id === selected ? "sel" : ""}" onclick="pick('${esc(c.customer_id)}')">
      <td>${esc(c.name || c.customer_id)} <span class="by">${esc(c.customer_id)}${c.has_card ? " · card" : ""}</span></td>
      <td><span class="kyc-st ${esc(c.stage)}">${esc(KYC_STAGE[c.stage] || words(c.stage))}</span></td>
      <td>${rating(c)}</td><td class="by">${esc(c.policy || "")}</td><td class="by">${esc(c.next_action)}</td></tr>`).join("")}
    </tbody></table>` : '<div class="empty">No cases at this stage.</div>');
}

// ---- Case file, step by step through the map

function kycDecideBox(a) {
  const id = esc(a.id);
  return `<div class="kyc-decide"><b>${esc(a.kind)}</b>: ${esc(a.question)}
    <ul class="reasons" style="color:var(--quiet);font-size:12.5px">${a.detail.map(d => `<li>${esc(d)}</li>`).join("")}</ul>
    <div class="row"><input id="kyc-note-${id}" placeholder="Reviewer note (optional)">
      <button class="btn ok" onclick="kycDecide('${id}','approve')">Approve</button>
      ${a.reject ? `<button class="btn no" onclick="kycDecide('${id}','${esc(a.reject)}')">Reject</button>` : ""}</div></div>`;
}

function kycFields(obj) {
  const rows = Object.entries(obj).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length));
  return rows.length ? `<table><tbody>${rows.map(([k, v]) => `<tr><td>${esc(words(k))}</td><td>${
    esc(Array.isArray(v) ? v.map(x => typeof x === "object" ? Object.values(x).join(", ") : x).join("; ") : v)}</td></tr>`).join("")}
    </tbody></table>` : "";
}

function kycStepBody(s, c, actions) {
  switch (s.key) {
    case "start":
      return s.application ? `<details class="kyc-more"><summary>Application form (client-supplied text)</summary>
        <div class="kyc-quote">${esc(s.application)}</div></details>`
        : s.status === "current" ? '<span class="meta">Not screened yet. Screening runs steps 3 to 6 in one go.</span>' : "";
    case "profile":
      return kycFields(s.profile);
    case "screen":
      if (s.status !== "done") return "";
      return (s.hits.length ? `<table><thead><tr><th>List</th><th>Matched name</th><th class="num">Score</th><th>Detail</th></tr></thead>
        <tbody>${s.hits.map(h => `<tr><td class="kyc-hit">${esc(String(h.list).toUpperCase())}</td><td>${esc(h.matched_name)}</td>
        <td class="num">${esc(h.score)}</td><td>${esc(h.details)}${h.date_of_birth_matches === undefined ? ""
          : h.date_of_birth_matches ? " · date of birth matches" : " · date of birth differs"}</td></tr>`).join("")}</tbody></table>`
        : '<span class="meta">No match on the sanctions or PEP list.</span>')
        + s.connections.map(r => `<div style="margin-top:4px">${esc(r.text)}${para(r.paragraph)}</div>`).join("");
    case "risk":
      return s.rating ? `<div><span class="rating r-${esc(s.rating)}">${esc(s.rating.toUpperCase())}</span>
        <span class="meta">under ${esc(s.policy)}${s.previous && s.previous.rating !== s.rating
          ? ` · was ${esc(s.previous.rating.toUpperCase())} under ${esc(s.previous.policy)}` : ""}</span></div>
        <ul class="reasons">${s.reasons.map(r => `<li>${esc(r.text)}${para(r.paragraph)}</li>`).join("")}</ul>` : "";
    case "approval": {
      const box = actions.find(a => a.id === s.case_id);
      let html = s.route ? `<div>${esc(s.route)}${s.outcome && s.outcome !== "waiting"
        ? ` · <b class="st-${s.outcome === "approved" ? "approved" : "rejected"}">${esc(s.outcome)}</b>` : ""}</div>` : "";
      if (s.notes) html += `<div class="meta">Reviewer note: ${esc(s.notes)}</div>`;
      if (box) html += kycDecideBox(box);
      else if (s.outcome === "waiting" && s.case_id) html += '<div class="meta">Waiting in the Dashboard action queue.</div>';
      if (s.memo) html += `<details class="kyc-more"><summary>Decision memo</summary><div class="kyc-quote">${esc(s.memo)}</div></details>`;
      return html;
    }
    case "monitoring": {
      if (s.status === "todo") return '<span class="meta">Starts once the client is onboarded. A policy change or transaction alerts send them back through the process.</span>';
      let html = `<div>Assessed under <b>${esc(s.assessed_policy || "nothing yet")}</b> · live policy <b>${esc(s.live_policy)}</b></div>`;
      if (s.due_changes.length) html += `<div class="meta" style="margin-top:4px">Changed controls:</div><ul class="reasons">${
        s.due_changes.map(d => `<li>${esc(fieldNames[d.field] || words(d.field))}${para(d.paragraph)}</li>`).join("")}</ul>`;
      if (s.refresh) html += `<div class="flag" style="margin-top:6px">Transaction monitoring flagged a KYC refresh: ${esc(s.refresh.reason)}</div>`;
      html += actions.filter(a => /^(reassess|refresh):/.test(a.id)).map(kycDecideBox).join("");
      if (s.user_id) html += `<div style="margin-top:6px"><a href="#" onclick="show('tm');return false">Card payments for ${esc(s.user_id)} →</a></div>`;
      return html;
    }
    default:
      return "";
  }
}

function kycCaseHtml(c, actions) {
  if (!c) return '<div class="panel empty">Pick a case to follow it through the process map.</div>';
  const busy = !!state.status.running, off = !c.runnable, deciding = c.stage === "approval";
  const why = off ? "Offline rehearsal covers the scripted clients only" : deciding ? "Answer the open decision first" : "";
  const steps = c.steps.map(s => `<li class="kyc-step ${esc(s.status)}"><div class="kyc-dot">${esc(s.map)}</div><div>
      <div class="t">${esc(s.title)} <span class="s">${esc(KYC_STATUS[s.status] || s.status)}</span></div>
      ${(b => b ? `<div class="kyc-body">${b}</div>` : "")(kycStepBody(s, c, actions))}</div></li>`).join("");
  const history = c.history.length ? `<div class="hist-title">Client file</div><ul class="hist" style="max-height:220px">${
    c.history.slice().reverse().map(e => `<li class="${e.pillar === "lifecycle" ? "life" : ""}"><span class="by">${
      new Date(e.ts * 1000).toLocaleString()}</span> ${esc(e.what)}${para(e.paragraph)}</li>`).join("")}</ul>` : "";
  return `<div class="panel cust">
    <div class="cust-head"><span class="name">${esc(c.name || c.customer_id)}</span>
      <span class="meta">${esc(c.customer_id)}${c.user_id ? " · card " + esc(c.user_id) : " · no card account"}</span><span class="spacer"></span>
      ${c.previous && c.previous.rating !== c.rating ? `<span class="meta">was <b>${esc(c.previous.rating.toUpperCase())}</b> under ${esc(c.previous.policy)} →</span>` : ""}
      <span class="rating r-${c.rating ? esc(c.rating) : "none"}">${c.rating ? esc(c.rating.toUpperCase()) : "NOT SCREENED"}</span></div>
    <div class="meta"><span class="kyc-st ${esc(c.stage)}">${esc(KYC_STAGE[c.stage] || words(c.stage))}</span>&nbsp; Next: <b>${esc(c.next_action)}</b></div>
    <div class="acts"><button class="act ${["new", "reassessment_due"].includes(c.stage) ? "next" : ""}" ${busy || why ? "disabled" : ""}
      title="${esc(why)}" onclick="post('/api/client/${esc(c.customer_id)}/onboard')">${c.rating ? "Screen again under " + esc(c.live_policy) : "Screen this application"}</button></div>
    <ol class="kyc-steps">${steps}</ol>
    <div class="kyc-note">Map steps ${c.not_used.map(n => `${esc(n.map)} (${esc(n.title.toLowerCase())})`).join(", ")} are for document-based onboarding.
      A retail application is one form, so they are not used here.</div>
    ${history}</div>`;
}

// Render as soon as the KYC tab is opened, not on the next poll.
document.addEventListener("click", e => { if (e.target.closest?.('.tab[data-tab="kyc"]')) setTimeout(kycRender, 0); });
