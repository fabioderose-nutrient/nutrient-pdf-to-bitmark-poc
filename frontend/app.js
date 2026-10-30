/* PDF to bitmark: frontend. Vanilla JS, no build step. */

const $ = (id) => document.getElementById(id);
// Nutrient Web SDK, loaded from the CDN in index.html. Keep both versions in sync.
const SDK_BASE_URL = "https://cdn.cloud.pspdfkit.com/pspdfkit-web@1.6.0/";
let webLicenseKey = "";     // from /api/config; empty = evaluation mode
let instance = null;        // Nutrient Web SDK instance
let viewerJob = null;
let highlights = [];
let current = null;         // last result
let pollTimer = null;

/* ----------------------------------------------------------- helpers */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function toast(msg, isError) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " err" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 4000);
}
function setStatus(state, text) {
  const pill = $("statusPill");
  pill.className = "pill " + state;
  pill.textContent = text;
}

/* ----------------------------------------------------------- tabs */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".tab-body").forEach((b) => b.classList.toggle("active", b.id === "tab-" + tab.dataset.tab));
  });
});

/* ----------------------------------------------------------- setup */

async function init() {
  const [samples, config] = await Promise.all([
    fetch("/api/samples").then((r) => r.json()),
    fetch("/api/config").then((r) => r.json()),
  ]);
  const sel = $("sampleSelect");
  sel.innerHTML = samples.map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");
  $("quizProvider").textContent = `(${config.quizProvider} · ${config.quizModel})`;
  webLicenseKey = config.webLicenseKey || "";
  if (!config.licensed) toast("NUTRIENT_LICENSE_KEY is not set: the Nutrient SDK runs in evaluation mode.");
  $("fileInput").addEventListener("change", () => { if ($("fileInput").files[0]) sel.value = ""; });
  $("convertForm").addEventListener("submit", (e) => { e.preventDefault(); convert(); });
  $("copyMarkup").addEventListener("click", () => {
    navigator.clipboard.writeText($("markup").textContent).then(() => toast("Markup copied"));
  });
}

/* ----------------------------------------------------------- convert */

async function convert() {
  const fd = new FormData();
  const file = $("fileInput").files[0];
  if (file) fd.append("file", file);
  else fd.append("sample", $("sampleSelect").value);
  fd.append("quiz_on", $("quizToggle").checked ? "1" : "0");

  $("convertBtn").disabled = true;
  setStatus("running", "Converting");
  $("log").innerHTML = "";
  $("statsBar").hidden = true;
  await clearHighlights();

  let job;
  try {
    const res = await fetch("/api/convert", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    job = await res.json();
  } catch (err) {
    setStatus("error", "Failed");
    $("convertBtn").disabled = false;
    toast(err.message, true);
    return;
  }
  openInViewer(job.id);
  poll(job.id);
}

function poll(id) {
  clearTimeout(pollTimer);
  const tick = async () => {
    const data = await fetch(`/api/job/${id}`).then((r) => r.json());
    renderLog(data.log || []);
    if (data.status === "running") {
      const last = data.log[data.log.length - 1];
      setStatus("running", last ? last.message.slice(0, 48) : "Converting");
      pollTimer = setTimeout(tick, 900);
      return;
    }
    $("convertBtn").disabled = false;
    if (data.status === "error") {
      setStatus("error", "Failed");
      toast(data.error || "conversion failed", true);
      return;
    }
    current = { id, ...data.result };
    setStatus("done", `${current.stats.bits} bits`);
    renderResult(current);
  };
  tick();
}

function renderLog(log) {
  $("log").innerHTML = log.map((l) => `<li><time>${l.t.toFixed(1)}s</time>${esc(l.message)}</li>`).join("");
}

/* ----------------------------------------------------------- result */

function renderResult(r) {
  const v = r.validation || {};
  const types = Object.entries(v.byType || {}).map(([k, n]) => `${n} ${k}`).join(", ");
  $("statsBar").hidden = false;
  $("statsBar").innerHTML = `
    <span><b>${r.stats.pages}</b> page(s)</span>
    <span><b>${r.stats.elements}</b> layout elements</span>
    <span><b>${r.stats.bits}</b> bits</span>
    <span><b>${r.stats.seconds}s</b> total</span>
    <span>${v.valid ? '<span class="ok">Valid bitmark ' + esc(v.bitmarkVersion || "") + "</span>" : '<span class="bad">Parser reported ' + (v.problems || []).length + " problem(s)</span>"}</span>
    <span title="${esc(types)}">${esc(types)}</span>`;

  $("markup").innerHTML = highlightMarkup(r.markup);
  $("downloadMarkup").href = `/jobs/${r.id}/book.bitmark`;
  $("downloadJson").href = `/jobs/${r.id}/book.json`;
  $("parserInfo").textContent = v.parser || "";
  fetch(`/jobs/${r.id}/book.json`).then((x) => x.text()).then((t) => { $("json").textContent = t; });

  const host = $("bits");
  host.innerHTML = r.bits.map((b) => renderBit(b, r)).join("");
  host.querySelectorAll(".bit[data-id]").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".choice, .flash")) return;
      selectBit(card.dataset.id);
    });
  });
  host.querySelectorAll(".choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".bit");
      card.querySelectorAll(".choice").forEach((c) => c.classList.add(c.dataset.correct === "1" ? "right" : (c === btn ? "wrong" : "")));
    });
  });
  host.querySelectorAll(".flash").forEach((f) => f.addEventListener("click", () => f.classList.toggle("flipped")));
}

function highlightMarkup(src) {
  return esc(src)
    .replace(/\[\.[^\]]+\]/g, (m) => `<span class="k">${m}</span>`)
    .replace(/\[@[^\]]+\]/g, (m) => `<span class="p">${m}</span>`)
    .replace(/\[(#{1,3}|!|\?|%|&amp;)[^\]]*\]/g, (m) => `<span class="t">${m}</span>`)
    .replace(/\[([+\-])[^\]]*\]/g, (m) => `<span class="c">${m}</span>`);
}

/* bitmark++ body text -> simple HTML for the preview */
function bodyHtml(markup) {
  const body = markup.replace(/^\[\.[^\]]*\](\[[^\]]*\])*\n?/, "");
  const lines = body.split("\n");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += list === "ul" ? "</ul>" : "</ol>"; list = null; } };
  for (const raw of lines) {
    const line = raw.replace(/^\^/, "");
    if (/^•1 /.test(raw)) { flushPara(); if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; } html += `<li>${inline(line.slice(3))}</li>`; }
    else if (/^• /.test(raw)) { flushPara(); if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; } html += `<li>${inline(line.slice(2))}</li>`; }
    else if (!line.trim()) { flushPara(); closeList(); }
    else { closeList(); para.push(line); }
  }
  flushPara(); closeList();
  return html;
}
function inline(t) {
  return esc(t).replace(/\*\^\*/g, "**").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/__(.+?)__/g, "<i>$1</i>");
}
function tag(markup, re) { const m = markup.match(re); return m ? m[1] : ""; }

function renderBit(b, r) {
  const type = `<span class="bit-type${b.generated ? " gen" : ""}">${esc(b.type)}</span>`;
  const idLine = `<div class="bit-id">[@id:${esc(b.id)}]${b.page ? ` · page ${b.page}` : ""}</div>`;
  const wrap = (cls, inner) => `<article class="bit ${cls}" data-id="${esc(b.id)}">${type}${inner}${idLine}</article>`;
  switch (b.type) {
    case "book":
      return wrap("book", `<h1>${esc(r.book.title)}</h1>${r.book.subtitle ? `<p>${esc(r.book.subtitle)}</p>` : ""}`);
    case "toc":
      return "";
    case "chapter":
      return wrap(`chapter l${b.level || 1}`, `<h2>${esc(b.title || tag(b.markup, /\[#([^\]]*)\]/))}</h2>`);
    case "article":
      return wrap("article", bodyHtml(b.markup));
    case "remark":
      return wrap("remark", `<h4>${esc(tag(b.markup, /\[#([^\]]*)\]/))}</h4>${bodyHtml(b.markup)}`);
    case "image": {
      const cap = tag(b.markup, /\[@caption:([^\]]*)\]/);
      return wrap("image", `<figure style="margin:0"><img src="/jobs/${r.id}/${esc(b.src)}" alt="${esc(tag(b.markup, /\[@alt:([^\]]*)\]/))}" loading="lazy">${cap ? `<figcaption>${esc(cap)}</figcaption>` : ""}</figure>`);
    }
    case "table":
      return wrap("table", tableHtml(b.markup));
    case "multiple-choice-1": {
      const q = tag(b.markup, /\[!([^\]]*)\]/);
      const choices = [...b.markup.matchAll(/\[([+\-])([^\]]*)\]/g)].map((m) => ({ ok: m[1] === "+", text: m[2] }));
      shuffle(choices);
      return wrap("quiz", `<p class="q">${esc(q)}</p>` + choices.map((c) => `<button type="button" class="choice" data-correct="${c.ok ? 1 : 0}">${esc(c.text)}</button>`).join("") + (b.evidence ? `<div class="evidence">Evidence: “${esc(b.evidence)}”</div>` : ""));
    }
    case "true-false-1": {
      const m = b.markup.match(/\[([+\-])([^\]]*)\]/);
      const isTrue = m && m[1] === "+";
      return wrap("quiz", `<p class="q">${esc(m ? m[2] : "")}</p><button type="button" class="choice" data-correct="${isTrue ? 1 : 0}">True</button><button type="button" class="choice" data-correct="${isTrue ? 0 : 1}">False</button>` + (b.evidence ? `<div class="evidence">Evidence: “${esc(b.evidence)}”</div>` : ""));
    }
    case "flashcard-1": {
      const parts = b.markup.split("\n====\n")[1] || "";
      const [front, back] = parts.split("\n--\n");
      return wrap("quiz", `<div class="flash"><div class="face front">${inline(front || "")}</div><div class="face back">${inline((back || "").replace(/\n====$/, ""))}</div><div class="tip">Click to flip</div></div>`);
    }
    default:
      return wrap("article", `<pre class="code" style="padding:0">${esc(b.markup)}</pre>`);
  }
}

function tableHtml(markup) {
  const body = markup.replace(/^\[\.[^\]]*\](\[[^\]]*\])*\n?/, "");
  const rows = body.split(/\n===\n|^===\n|\n===$/).filter((r) => r.trim());
  let html = "<table>";
  for (const row of rows) {
    const cells = row.split("\n==\n");
    const isHeader = cells.every((c) => /^\[#/.test(c.trim()));
    html += "<tr>" + cells.map((c) => isHeader ? `<th>${esc(c.replace(/^\[#|\]$/g, ""))}</th>` : `<td>${inline(c)}</td>`).join("") + "</tr>";
  }
  return html + "</table>";
}

function shuffle(a) { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } }

/* ----------------------------------------------------------- viewer */

async function openInViewer(jobId) {
  if (viewerJob === jobId && instance) return;
  $("viewerEmpty").style.display = "none";
  if (instance) { PSPDFKit.unload("#viewer"); instance = null; }
  highlights = [];
  viewerJob = null;
  try {
    const options = {
      container: "#viewer",
      document: `/api/pdf/${jobId}`,
      baseUrl: SDK_BASE_URL,
    };
    if (webLicenseKey) options.licenseKey = webLicenseKey;
    instance = await PSPDFKit.load(options);
    viewerJob = jobId;
  } catch (err) {
    $("viewerEmpty").style.display = "";
    $("viewerEmpty").querySelector("h3").textContent = "Viewer unavailable";
    $("viewerEmpty").querySelector("p").textContent = err.message;
  }
}

async function clearHighlights() {
  if (!instance || !highlights.length) return;
  try { await instance.delete(highlights); } catch { /* gone */ }
  highlights = [];
}

async function selectBit(id) {
  document.querySelectorAll(".bit").forEach((c) => c.classList.toggle("active", c.dataset.id === id));
  const b = current && current.bits.find((x) => x.id === id);
  if (!b || !b.box || !b.page || !instance) return;
  await clearHighlights();
  const meta = current.pages.find((p) => p.page === b.page);
  const info = instance.pageInfoForIndex(b.page - 1);
  if (!meta || !info) return;
  const sx = info.width / meta.width, sy = info.height / meta.height;
  const rect = new PSPDFKit.Geometry.Rect({ left: b.box.x * sx, top: b.box.y * sy, width: b.box.width * sx, height: b.box.height * sy });
  const ann = new PSPDFKit.Annotations.RectangleAnnotation({
    pageIndex: b.page - 1, boundingBox: rect.grow(4),
    strokeColor: new PSPDFKit.Color({ r: 3, g: 105, b: 161 }), strokeWidth: 2,
    fillColor: new PSPDFKit.Color({ r: 3, g: 105, b: 161 }), opacity: 0.18,
  });
  const created = await instance.create(ann);
  highlights = created;
  instance.setViewState((vs) => vs.set("currentPageIndex", b.page - 1));
  try { instance.jumpToRect(b.page - 1, rect.grow(60)); } catch { /* optional */ }
}

init().catch((err) => toast(err.message, true));
