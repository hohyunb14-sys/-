#!/usr/bin/env node
/**
 * scan.mjs — ~/agency 를 읽어 state.js 를 만든다.
 *
 *   읽기 전용입니다. 어떤 파일도 지우거나 고치지 않습니다.
 *   쓰는 파일은 이 스크립트 옆의 state.js 하나뿐입니다.
 *
 *   사용:  node scan.mjs            (~/agency 를 읽음)
 *          AGENCY_DIR=/경로 node scan.mjs
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AGENCY = process.env.AGENCY_DIR || path.join(os.homedir(), "agency");
const OUT = path.join(HERE, "state.js");
const PALETTE = ["#E0A458", "#6FB89A", "#9A87D4", "#D98BA8", "#7FC4C9"];

function readIf(p) { try { return fs.readFileSync(p, "utf8"); } catch { return null; } }
function listDirs(p) {
  try { return fs.readdirSync(p, { withFileTypes: true }).filter(d => d.isDirectory()).map(d => d.name).sort(); }
  catch { return []; }
}
function listMd(p) {
  try {
    return fs.readdirSync(p).filter(f => /\.(md|markdown)$/i.test(f) && f.toUpperCase() !== "CLAUDE.MD").sort();
  } catch { return []; }
}

/* --- `- 키: 값` 줄 파싱 --- */
function fields(text) {
  const out = {};
  (text || "").split("\n").forEach(line => {
    const m = line.match(/^\s*[-*]\s*([^:：]{1,20})\s*[:：]\s*(.+?)\s*$/);
    if (m) out[m[1].trim()] = m[2].trim();
  });
  return out;
}
/* --- 앞머리(--- ... ---) 파싱 --- */
function frontMatter(text) {
  const m = (text || "").match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return {};
  const out = {};
  m[1].split("\n").forEach(line => {
    const kv = line.match(/^\s*([^:：]{1,20})\s*[:：]\s*(.*)$/);
    if (kv) out[kv[1].trim()] = kv[2].trim();
  });
  return out;
}
function splitList(v) {
  return String(v || "").split(/[,、·]/).map(s => s.trim()).filter(Boolean);
}
function pick(o, ...keys) { for (const k of keys) if (o[k]) return o[k]; return ""; }

/* ---------------- 방 ---------------- */
function readRooms() {
  const base = path.join(AGENCY, "rooms");
  return listDirs(base).map((key, i) => {
    const dir = path.join(base, key);
    const md = readIf(path.join(dir, "CLAUDE.md")) || "";
    const f = fields(md);
    const title = (md.match(/^#\s+(.+)$/m) || [, key])[1].trim();
    const items = listMd(dir).map(name => {
      const raw = readIf(path.join(dir, name)) || "";
      const fm = frontMatter(raw);
      const label = pick(fm, "결과물", "제목", "title") ||
        name.replace(/\.(md|markdown)$/i, "").replace(/^\d{4}-\d{2}-\d{2}[_-]?/, "").replace(/[-_]/g, " ");
      return {
        file: name,
        label: label.slice(0, 24),
        tags: splitList(pick(fm, "태그", "tags")).slice(0, 4),
        by: pick(fm, "작성", "by"),
        from: pick(fm, "출처", "from"),
        constraints: splitList(pick(fm, "원칙", "constraints")),
        mtime: (() => { try { return fs.statSync(path.join(dir, name)).mtimeMs; } catch { return 0; } })()
      };
    }).sort((a, b) => a.mtime - b.mtime);
    const extras = (() => {
      try { return fs.readdirSync(dir).filter(n => !/\.(md|markdown)$/i.test(n)).length; }
      catch { return 0; }
    })();
    return {
      key, name: title,
      motto: pick(f, "모토", "motto") || title.replace(/실$/, ""),
      principle: pick(f, "원칙", "principle") || "아직 정하지 않았습니다.",
      refuse: splitList(pick(f, "안 하는 것", "안하는 것", "refuse")),
      voice: pick(f, "말투", "voice") || "",
      color: pick(f, "색", "color") || PALETTE[i % PALETTE.length],
      order: parseInt(pick(f, "자리", "order"), 10) || (i + 1),
      items, extras
    };
  }).sort((a, b) => a.order - b.order);
}

/* ---------------- 사람 ---------------- */
function readTeam() {
  const md = readIf(path.join(AGENCY, "team.md")) || "";
  const rows = [];
  md.split("\n").forEach(line => {
    if (!line.trim().startsWith("|")) return;
    const c = line.split("|").map(s => s.trim());
    if (c.length < 6) return;
    const [, name, rank, role, room] = c;
    if (!name || /^-+$/.test(name) || name === "이름") return;
    rows.push({ name, rank: rank || "실장", role: role || "", room });
  });
  return rows;
}

/* ---------------- 보관함 (회의록 / 부서) ---------------- */
function readArchive(folder, kind) {
  const dir = path.join(AGENCY, folder);
  return listMd(dir).map(name => {
    const fm = frontMatter(readIf(path.join(dir, name)) || "");
    return {
      file: name, kind,
      label: (pick(fm, "결과물", "제목", "title") ||
        name.replace(/\.(md|markdown)$/i, "").replace(/^\d{4}-\d{2}-\d{2}[_-]?/, "")).slice(0, 24),
      tags: splitList(pick(fm, "태그", "tags")).slice(0, 4),
      constraints: splitList(pick(fm, "원칙", "constraints")),
      from: pick(fm, "출처", "from")
    };
  });
}

/* ---------------- 오늘 (근무일 / 휴무일) ---------------- */
function readToday() {
  const d = new Date();
  const stamp = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  const raw = readIf(path.join(AGENCY, "daily", stamp + ".md"));
  if (!raw) return { date: stamp, mode: null, found: false };
  const v = pick(frontMatter(raw), "근무", "mode");
  return { date: stamp, mode: /휴무/.test(v) ? "off" : /근무/.test(v) ? "work" : null, found: true, label: v };
}

/* ---------------- 실제 토큰 사용량 ---------------- */
function readTokens() {
  const root = path.join(os.homedir(), ".claude", "projects");
  if (!fs.existsSync(root)) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const byProject = {};
  let total = 0, todayTotal = 0, files = 0;

  const walk = dir => {
    let ents = [];
    try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of ents) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { walk(full); continue; }
      if (!e.name.endsWith(".jsonl")) continue;
      files++;
      let text = "";
      try { text = fs.readFileSync(full, "utf8"); } catch { continue; }
      const proj = path.basename(path.dirname(full));
      for (const line of text.split("\n")) {
        if (!line || line.indexOf("usage") < 0) continue;
        let o; try { o = JSON.parse(line); } catch { continue; }
        const u = (o.message && o.message.usage) || o.usage;
        if (!u) continue;
        const n = (u.input_tokens || 0) + (u.output_tokens || 0) +
          (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0);
        if (!n) continue;
        total += n;
        byProject[proj] = (byProject[proj] || 0) + n;
        const ts = Date.parse(o.timestamp || o.time || "");
        if (!isNaN(ts) && ts >= today.getTime()) todayTotal += n;
      }
    }
  };
  walk(root);
  if (!files) return null;
  const top = Object.keys(byProject).map(k => ({ name: k.replace(/^-+/, "").slice(-28), tokens: byProject[k] }))
    .sort((a, b) => b.tokens - a.tokens).slice(0, 4);
  return { total, today: todayTotal, byProject: top, files };
}

/* ---------------- 실행 ---------------- */
if (!fs.existsSync(AGENCY)) {
  console.error("폴더가 없습니다: " + AGENCY);
  console.error("install.sh 를 먼저 실행하거나 AGENCY_DIR 를 지정하세요.");
  process.exit(1);
}

const state = {
  scannedAt: new Date().toISOString(),
  agencyDir: AGENCY,
  rooms: readRooms(),
  team: readTeam(),
  meetings: readArchive("meetings", "minutes"),
  depts: readArchive("depts", "dept"),
  today: readToday(),
  tokens: readTokens()
};

fs.writeFileSync(OUT, "window.AGENCY_STATE = " + JSON.stringify(state, null, 1) + ";\n", "utf8");

const n = state.rooms.reduce((a, r) => a + r.items.length, 0);
console.log("읽음  " + AGENCY);
console.log("  방      " + state.rooms.length + "개 — " + state.rooms.map(r => r.name + "(" + r.items.length + ")").join(", "));
console.log("  사람    " + state.team.length + "명");
console.log("  회의록  " + state.meetings.length + "건 · 부서 산출물 " + state.depts.length + "건");
console.log("  오늘    " + (state.today.found ? (state.today.label || "표시 없음") : "daily/" + state.today.date + ".md 없음"));
console.log("  토큰    " + (state.tokens
  ? state.tokens.today.toLocaleString("en-US") + " (오늘) / " + state.tokens.total.toLocaleString("en-US") + " (전체), 로그 " + state.tokens.files + "개"
  : "~/.claude/projects 를 못 찾음 — 추정치로 표시됩니다"));
console.log("");
console.log("씀    " + OUT + "  (결과물 " + n + "개)");
console.log("이제  office-map.html 을 브라우저로 여세요.");
