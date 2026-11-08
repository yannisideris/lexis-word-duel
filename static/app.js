"use strict";
const $ = (s) => document.querySelector(s);
const COLS = "ABCDEFGHIJKLMNO";
const LABEL = { TW: "3W", DW: "2W", TL: "3L", DL: "2L" };
let CFG = null;
let AI = "AI";
let S = null;               // game state (saved in localStorage)
let temp = [];              // [{r,c,letter,blank,idx}] human tiles not yet played
let selected = null;        // rack index
let exchangeMode = false, exchangeSel = new Set();
let busy = false, previewTimer = null;

const save = () => { try { localStorage.setItem("lexis-game", JSON.stringify(S)); } catch (e) {} };
const cellName = (r, c) => COLS[c] + (r + 1);
const val = (ch) => (ch === "?" || ch === ch.toLowerCase()) ? 0 : CFG.values[ch];
const boardRows = () => S.board.map((row) => row.join(""));

function newGame() {
  const bag = [];
  for (const [ch, n] of Object.entries(CFG.counts)) for (let i = 0; i < n; i++) bag.push(ch);
  for (let i = bag.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [bag[i], bag[j]] = [bag[j], bag[i]]; }
  S = { board: Array.from({ length: 15 }, () => Array(15).fill(".")), bag, racks: { human: [], ai: [] },
        scores: { human: 0, ai: 0 }, turn: "human", scoreless: 0, over: false, last: [], log: [], lastHuman: "" };
  draw("human"); draw("ai");
  temp = []; selected = null;
  log("sys", "New game. You start: your first word must cover the ★ (H8).");
  save(); render();
}

function draw(who) {
  while (S.racks[who].length < 7 && S.bag.length) S.racks[who].push(S.bag.pop());
}

function log(kind, html, details) { S.log.push({ kind, html, details: details || null }); }

// ---------------------------------------------------------------- rendering
function render() {
  const board = $("#board");
  board.innerHTML = "";
  const tempAt = new Map(temp.map((t) => [t.r * 15 + t.c, t]));
  const lastSet = new Set(S.last.map(([r, c]) => r * 15 + c));
  for (let r = 0; r < 15; r++) for (let c = 0; c < 15; c++) {
    const cell = document.createElement("div");
    const prem = CFG.premiums[r][c];
    cell.className = "cell " + prem + (r === 7 && c === 7 ? " center" : "");
    cell.title = cellName(r, c);
    const ch = S.board[r][c], t = tempAt.get(r * 15 + c);
    if (ch !== ".") cell.appendChild(tileEl(ch.toUpperCase(), ch !== ch.toUpperCase(), lastSet.has(r * 15 + c) ? "last" : ""));
    else if (t) cell.appendChild(tileEl(t.letter, t.blank, "temp"));
    else if (prem && !(r === 7 && c === 7)) cell.textContent = LABEL[prem];
    cell.onclick = () => onCell(r, c);
    cell.ondragover = (e) => e.preventDefault();
    cell.ondrop = (e) => { e.preventDefault(); selected = +e.dataTransfer.getData("text"); onCell(r, c); };
    board.appendChild(cell);
  }
  const rack = $("#rack");
  rack.innerHTML = "";
  const used = new Set(temp.map((t) => t.idx));
  S.racks.human.forEach((ch, i) => {
    if (used.has(i)) return;
    const el = tileEl(ch === "?" ? "" : ch, ch === "?", (selected === i ? "sel " : "") + (exchangeSel.has(i) ? "ex" : ""));
    el.draggable = !exchangeMode;
    el.ondragstart = (e) => e.dataTransfer.setData("text", String(i));
    el.onclick = () => onRack(i);
    rack.appendChild(el);
  });
  $("#score-human b").textContent = S.scores.human;
  $("#score-ai b").textContent = S.scores.ai;
  $("#bag-count").textContent = S.bag.length;
  $("#score-human").classList.toggle("active", S.turn === "human" && !S.over);
  $("#score-ai").classList.toggle("active", S.turn === "ai" && !S.over);
  const mine = S.turn === "human" && !S.over && !busy;
  $("#btn-play").disabled = !mine || exchangeMode || !temp.length;
  $("#btn-recall").disabled = !mine || !temp.length;
  $("#btn-pass").disabled = !mine || exchangeMode;
  $("#btn-exchange").disabled = !mine || S.bag.length < 7 || temp.length > 0;
  $("#btn-exchange").textContent = exchangeMode ? (exchangeSel.size ? `Swap ${exchangeSel.size}` : "Cancel") : "Exchange";
  renderLog();
}

function tileEl(letter, blank, cls) {
  const el = document.createElement("div");
  el.className = "tile " + (blank ? "blank " : "") + (cls || "");
  el.textContent = letter;
  if (!blank && letter) { const s = document.createElement("sub"); s.textContent = CFG.values[letter]; el.appendChild(s); }
  return el;
}

function renderLog() {
  const box = $("#log");
  box.innerHTML = "";
  for (const e of S.log) {
    const d = document.createElement("div");
    d.className = "entry " + e.kind;
    d.innerHTML = e.html;
    if (e.details && e.details.length) {
      const det = document.createElement("details");
      det.innerHTML = `<summary>${AI}'s turn details</summary>` + e.details.map((x) => `<div>${esc(x)}</div>`).join("");
      d.appendChild(det);
    }
    box.appendChild(d);
  }
  box.scrollTop = box.scrollHeight;
}

const esc = (s) => String(s).replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));

function status(text, thinking) {
  const el = $("#status");
  el.textContent = text;
  el.classList.toggle("thinking", !!thinking);
}

// ---------------------------------------------------------------- human input
function onRack(i) {
  if (S.turn !== "human" || busy || S.over) return;
  if (exchangeMode) { exchangeSel.has(i) ? exchangeSel.delete(i) : exchangeSel.add(i); render(); return; }
  selected = selected === i ? null : i;
  render();
}

async function onCell(r, c) {
  if (S.turn !== "human" || busy || S.over || exchangeMode) return;
  const ti = temp.findIndex((t) => t.r === r && t.c === c);
  if (ti >= 0) { temp.splice(ti, 1); render(); preview(); return; }
  if (selected === null || S.board[r][c] !== ".") return;
  const ch = S.racks.human[selected];
  let letter = ch, blank = false;
  if (ch === "?") {
    letter = await askBlank();
    if (!letter) return;
    blank = true;
  }
  temp.push({ r, c, letter, blank, idx: selected });
  selected = null;
  render(); preview();
}

function askBlank() {
  return new Promise((resolve) => {
    const dlg = $("#blank-dlg"), inp = $("#blank-input");
    inp.value = "";
    dlg.onclose = () => resolve(/^[a-z]$/i.test(inp.value) ? inp.value.toUpperCase() : null);
    dlg.showModal();
  });
}

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

function preview() {
  clearTimeout(previewTimer);
  const el = $("#preview");
  if (!temp.length) { el.textContent = ""; el.className = ""; return; }
  previewTimer = setTimeout(async () => {
    try {
      const r = await api("/api/check", { board: boardRows(), placements: temp.map(({ r, c, letter, blank }) => ({ r, c, letter, blank })) });
      el.className = r.ok ? "ok" : "bad";
      el.textContent = r.ok ? `${r.words.map((w) => w.word).join(", ")}: +${r.score}` : r.error;
    } catch (e) { el.textContent = ""; }
  }, 150);
}

async function playHuman() {
  if (busy || !temp.length) return;
  busy = true; render();
  try {
    const placements = temp.map(({ r, c, letter, blank }) => ({ r, c, letter, blank }));
    const res = await api("/api/check", { board: boardRows(), placements });
    if (!res.ok) { $("#preview").className = "bad"; $("#preview").textContent = res.error; return; }
    for (const t of temp) S.board[t.r][t.c] = t.blank ? t.letter.toLowerCase() : t.letter;
    const used = new Set(temp.map((t) => t.idx));
    S.racks.human = S.racks.human.filter((_, i) => !used.has(i));
    S.scores.human += res.score;
    S.last = temp.map((t) => [t.r, t.c]);
    const main = res.words[0];
    S.lastHuman = `${main.word} at ${main.start} ${main.dir}, ${res.score} pts`;
    log("human", `<b>You</b>: ${res.words.map((w) => esc(w.word)).join(", ")} +${res.score}${temp.length === 7 ? " (all 7 tiles, +50)" : ""}`);
    temp = []; S.scoreless = 0;
    draw("human");
    $("#preview").textContent = "";
    endTurn("human");
  } catch (e) { status("Error: " + e.message); }
  finally { busy = false; render(); }
}

function passHuman() {
  temp = []; S.scoreless++; S.lastHuman = "passed";
  log("human", "<b>You</b> passed.");
  endTurn("human");
}

function exchangeHuman() {
  if (!exchangeMode) { exchangeMode = true; exchangeSel.clear(); selected = null; render(); return; }
  if (exchangeSel.size) {
    const out = [...exchangeSel].map((i) => S.racks.human[i]);
    S.racks.human = S.racks.human.filter((_, i) => !exchangeSel.has(i));
    draw("human");
    S.bag.push(...out); shuffleBag();
    S.scoreless++; S.lastHuman = `exchanged ${out.length} tiles`;
    log("human", `<b>You</b> exchanged ${out.length} tile(s).`);
    exchangeMode = false; exchangeSel.clear();
    endTurn("human");
    return;
  }
  exchangeMode = false; render();
}

function shuffleBag() {
  for (let i = S.bag.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [S.bag[i], S.bag[j]] = [S.bag[j], S.bag[i]]; }
}

// ---------------------------------------------------------------- turns & end
function endTurn(who) {
  if (checkEnd(who)) { save(); render(); return; }
  S.turn = who === "human" ? "ai" : "human";
  save(); render();
  if (S.turn === "ai") aiTurn();
  else status("Your turn.");
}

function rackValue(rack) { return rack.reduce((s, ch) => s + val(ch), 0); }

function checkEnd(who) {
  const other = who === "human" ? "ai" : "human";
  if (!S.bag.length && !S.racks[who].length) {
    const bonus = rackValue(S.racks[other]);
    S.scores[who] += bonus; S.scores[other] -= bonus;
    log("sys", `${who === "human" ? "You" : AI} used every tile: +${bonus} from the opponent's rack.`);
  } else if (S.scoreless >= 6) {
    S.scores.human -= rackValue(S.racks.human); S.scores.ai -= rackValue(S.racks.ai);
    log("sys", "Six scoreless turns in a row: game ends.");
  } else return false;
  S.over = true;
  const h = S.scores.human, a = S.scores.ai;
  const verdict = h > a ? "You win!" : h < a ? `${AI} wins.` : "Draw.";
  log("sys", `Final: you ${h} · ${AI} ${a}. ${verdict}`);
  status(`Game over. ${verdict}`);
  return true;
}

async function aiTurn() {
  busy = true;
  status(`${AI} is thinking`, true); render();
  try {
    const res = await api("/api/ai-move", {
      board: boardRows(), rack: S.racks.ai.join(""), scores: S.scores, bag_left: S.bag.length,
      last_human: S.lastHuman, thinking: $("#thinking").checked,
    });
    const meta = [`${res.calls} call(s), ${(res.ms / 1000).toFixed(1)}s via ${[...new Set(res.providers || [])].join(", ") || "-"}`,
                  ...(res.tokens && res.tokens.length ? [`tokens per call: ${res.tokens.join(", ")}`] : []), ...(res.trace || [])];
    const who = res.model && res.model !== AI ? `<b>${AI}</b> <small class="model">${esc(res.model)}</small>` : `<b>${AI}</b>`;
    if (res.type === "play") {
      for (const p of res.placements) {
        S.board[p.r][p.c] = p.blank ? p.letter.toLowerCase() : p.letter;
        const k = S.racks.ai.indexOf(p.blank ? "?" : p.letter);
        if (k >= 0) S.racks.ai.splice(k, 1);
      }
      S.scores.ai += res.score; S.scoreless = 0;
      S.last = res.placements.map((p) => [p.r, p.c]);
      log("ai", `${who}: ${res.words.map((w) => esc(w.word)).join(", ")} at ${res.start} ${res.dir} +${res.score}`
        + (res.placements.length === 7 ? " (all 7 tiles, +50)" : ""), meta);
      draw("ai");
    } else if (res.type === "exchange") {
      const out = [];
      for (const ch of res.tiles) { const k = S.racks.ai.indexOf(ch); if (k >= 0) out.push(...S.racks.ai.splice(k, 1)); }
      draw("ai"); S.bag.push(...out); shuffleBag(); S.scoreless++;
      log("ai", `${who} exchanged ${out.length} tile(s).`, meta);
    } else if (res.type === "pass") {
      S.scoreless++;
      log("ai", `${who} passed.`, meta);
    } else {
      throw new Error(res.error || "no move");
    }
    busy = false;
    endTurn("ai");
  } catch (e) {
    busy = false;
    log("sys", `${AI} could not move (${esc(e.message)}). Press "Retry".`);
    status(`${AI}'s providers did not answer.`);
    const btn = document.createElement("button");
    btn.textContent = "Retry"; btn.style.marginLeft = "8px";
    btn.onclick = () => aiTurn();
    $("#status").appendChild(btn);
    save(); render();
  }
}

// ---------------------------------------------------------------- boot
async function boot() {
  CFG = await (await fetch("/api/config")).json();
  AI = CFG.opponent || "AI";
  document.querySelectorAll(".ai-name").forEach((el) => { el.textContent = AI; });
  $("#thinking").checked = CFG.thinking_default;
  try { S = JSON.parse(localStorage.getItem("lexis-game") || "null"); } catch (e) { S = null; }
  if (!S || !S.board) newGame();
  else { render(); if (S.over) status("Game over."); else if (S.turn === "ai") aiTurn(); else status("Your turn."); }
  if (!CFG.providers.length) log("sys", "No AI provider is configured on the server.");
  $("#btn-play").onclick = playHuman;
  $("#btn-recall").onclick = () => { temp = []; selected = null; render(); preview(); };
  $("#btn-shuffle").onclick = () => {
    if (temp.length) return;
    const r = S.racks.human;
    for (let i = r.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [r[i], r[j]] = [r[j], r[i]]; }
    selected = null; save(); render();
  };
  $("#btn-pass").onclick = passHuman;
  $("#btn-exchange").onclick = exchangeHuman;
  $("#btn-new").onclick = () => { if (confirm("Start a new game?")) { exchangeMode = false; newGame(); status("Your turn."); } };
}
boot();
