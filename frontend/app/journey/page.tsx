"use client";

import { useState } from "react";

const API = "/api/v1";

// ── Types ─────────────────────────────────────────────────────────────────────

type TrajectoryRow = {
  grade: string;
  grade_name: string;
  standards_total: number;
  standards_attempted: number;
  standards_mastered: number;
  coverage_pct: number;
  mastery_pct: number;
  grade_status: string;
};

type Gap = {
  node_id: string;
  code: string;
  description: string;
  subject: string;
  p_mastery: number;
  nano_weight: number;
  blocked_count: number;
};

type SkillNode = {
  identifier: string;
  code: string;
  description: string;
  grade: string;
  subject: string;
  mastery: number;
  attempts: number;
  skillType: "gap" | "strength" | "recent" | "other";
};

// ── Main map geometry ──────────────────────────────────────────────────────────

const MAP_W = 640;
const ROW_H = 190;
const NODE_R = 46;
const TROPHY_Y = 80 + 8 * ROW_H + 60;
const TOTAL_H = TROPHY_Y + 80;
const CAMP_X = 530;

function nodeX(i: number) { return i % 2 === 0 ? 168 : 390; }
function nodeY(i: number) { return 80 + i * ROW_H; }

// ── Detail map geometry ────────────────────────────────────────────────────────

const DETAIL_W = 560;
const DETAIL_R = 26;
const DETAIL_ROW = 120;
const MAX_SHOWN = 10;

function detailNodeX(i: number) { return i % 2 === 0 ? 130 : 320; }
function detailNodeY(i: number) { return 60 + i * DETAIL_ROW; }

// Camp is always to the far right, aligned with its detour grade
const CAMP_X_MAIN = 530;

// ── Grade metadata ─────────────────────────────────────────────────────────────

const GRADE_META: Record<string, { emoji: string; tagline: string; plain: string }> = {
  K1: { emoji: "🌱", tagline: "First Steps",      plain: "Counting, letter sounds, shapes" },
  K2: { emoji: "🌿", tagline: "Taking Root",      plain: "Reading, adding & subtracting" },
  K3: { emoji: "🌳", tagline: "Growing Strong",   plain: "Multiplication, chapter books" },
  K4: { emoji: "⛵", tagline: "Setting Sail",     plain: "Fractions, essays, multi-digit math" },
  K5: { emoji: "🗺️", tagline: "Explorer",         plain: "Decimals, geometry, research" },
  K6: { emoji: "🏔️", tagline: "Summit Seeker",    plain: "Ratios, beginning algebra" },
  K7: { emoji: "⭐", tagline: "Star Climber",     plain: "Negative numbers, probability" },
  K8: { emoji: "🚀", tagline: "Ready for Launch", plain: "Linear equations, statistics" },
};

function plainGap(desc: string): string {
  const d = (desc || "").toLowerCase();
  if (d.includes("phonem")) return "Hearing sounds in words";
  if (d.includes("fraction")) return "Parts of a whole (½, ¾…)";
  if (d.includes("multipli")) return "Multiplication facts";
  if (d.includes("place value")) return "What each digit means";
  if (d.includes("fluency")) return "Reading smoothly";
  if (d.includes("decimal")) return "Numbers like 3.14";
  if (d.includes("algebra") || d.includes("equation")) return "Solving for unknowns";
  if (d.includes("geometry") || d.includes("shape")) return "Shapes and angles";
  if (d.includes("ratio")) return "Comparing amounts";
  if (d.includes("division")) return "Splitting into equal groups";
  return desc.length > 52 ? desc.slice(0, 49) + "…" : desc;
}

function skillEmoji(desc: string): string {
  const d = (desc || "").toLowerCase();
  if (d.includes("fraction") || d.includes("decimal")) return "🔢";
  if (d.includes("multipli") || d.includes("division")) return "✖️";
  if (d.includes("addition") || d.includes("subtract")) return "➕";
  if (d.includes("geometry") || d.includes("shape") || d.includes("angle")) return "📐";
  if (d.includes("algebra") || d.includes("equation")) return "🔡";
  if (d.includes("statistic") || d.includes("data") || d.includes("graph")) return "📊";
  if (d.includes("probability")) return "🎲";
  if (d.includes("phonem") || d.includes("phonics") || d.includes("sound")) return "🔤";
  if (d.includes("reading") || d.includes("comprehension")) return "📖";
  if (d.includes("writing") || d.includes("essay")) return "✍️";
  if (d.includes("vocabulary") || d.includes("word")) return "💬";
  if (d.includes("grammar")) return "📝";
  if (d.includes("place value")) return "🧮";
  if (d.includes("ratio")) return "⚖️";
  return "⭐";
}

function toPlainEnglish(desc: string): string {
  const d = (desc || "").toLowerCase();
  if (d.includes("phonem")) return "Hearing sounds in words";
  if (d.includes("phonics")) return "Letter-sound patterns";
  if (d.includes("fraction")) return "Parts of a whole";
  if (d.includes("multipli")) return "Times tables";
  if (d.includes("place value")) return "What each digit means";
  if (d.includes("fluency")) return "Reading smoothly";
  if (d.includes("decimal")) return "Numbers with decimals";
  if (d.includes("algebra") || d.includes("equation")) return "Solving for unknowns";
  if (d.includes("geometry") || d.includes("shape")) return "Shapes & angles";
  if (d.includes("ratio")) return "Comparing amounts";
  if (d.includes("division")) return "Dividing into groups";
  if (d.includes("addition") || d.includes("subtract")) return "Adding & subtracting";
  if (d.includes("probability")) return "Chances & likelihood";
  if (d.includes("statistic") || d.includes("data")) return "Reading data & graphs";
  if (d.includes("word problem")) return "Math in real life";
  if (d.includes("vocabulary")) return "Building vocabulary";
  if (d.includes("comprehension") || d.includes("reading")) return "Understanding texts";
  if (d.includes("writing")) return "Writing skills";
  if (d.includes("grammar")) return "Grammar rules";
  return desc.length > 42 ? desc.slice(0, 39) + "…" : desc;
}

// ── Node state ─────────────────────────────────────────────────────────────────

type NodeState = "mastered" | "active" | "detour" | "locked";

function resolveState(row: TrajectoryRow, isActive: boolean, hasGaps: boolean): NodeState {
  if (row.grade_status === "not_started") return "locked";
  if (row.mastery_pct >= 75) return "mastered";
  if (isActive && hasGaps) return "detour";
  if (isActive) return "active";
  if (row.grade_status === "below" && row.standards_attempted > 0) return "detour";
  return "locked";
}

function skillNodeState(s: SkillNode): NodeState {
  if (s.attempts === 0) return "locked";
  if (s.mastery >= 0.75) return "mastered";
  if (s.skillType === "gap") return "detour";
  return "active";
}

// ── Colours ────────────────────────────────────────────────────────────────────

const STATE_COLOR: Record<NodeState, {
  fill1: string; fill2: string; stroke: string; glow: string; badge: string; badgeBg: string;
}> = {
  mastered: {
    fill1: "#facc15", fill2: "#f59e0b", stroke: "#fbbf24",
    glow: "drop-shadow(0 0 14px rgba(251,191,36,0.8))",
    badge: "Mastered", badgeBg: "bg-yellow-100 text-yellow-800 border-yellow-300",
  },
  active: {
    fill1: "#6366f1", fill2: "#7c3aed", stroke: "#818cf8",
    glow: "drop-shadow(0 0 14px rgba(99,102,241,0.8))",
    badge: "Here Now", badgeBg: "bg-indigo-100 text-indigo-800 border-indigo-300",
  },
  detour: {
    fill1: "#f59e0b", fill2: "#ea580c", stroke: "#fbbf24",
    glow: "drop-shadow(0 0 14px rgba(245,158,11,0.7))",
    badge: "Gap Fix", badgeBg: "bg-amber-100 text-amber-800 border-amber-300",
  },
  locked: {
    fill1: "#cbd5e1", fill2: "#94a3b8", stroke: "#94a3b8",
    glow: "",
    badge: "Upcoming", badgeBg: "bg-slate-100 text-slate-500 border-slate-300",
  },
};

function segColor(a: NodeState): string {
  if (a === "mastered") return "#fbbf24";
  if (a === "active" || a === "detour") return "#6366f1";
  return "#cbd5e1";
}

// ── SVG Helpers ────────────────────────────────────────────────────────────────

function pathD(x1: number, y1: number, x2: number, y2: number): string {
  const cy = (y1 + y2) / 2;
  return `M ${x1},${y1} C ${x1},${cy} ${x2},${cy} ${x2},${y2}`;
}

function campPathD(nx: number, ny: number): string {
  const midX = (nx + CAMP_X_MAIN) / 2;
  return `M ${nx},${ny} C ${midX},${ny} ${midX},${ny + 30} ${CAMP_X_MAIN},${ny + 30}`;
}

// ── Components ─────────────────────────────────────────────────────────────────

function MapBackground({ w, h }: { w: number; h: number }) {
  const dots = [];
  for (let x = 20; x < w; x += 40) {
    for (let y = 20; y < h; y += 40) {
      dots.push(<circle key={`${x}-${y}`} cx={x} cy={y} r={1} fill="#e2e8f0" opacity={0.6} />);
    }
  }
  return <>{dots}</>;
}

function GradeNode({ row, idx, state, isActive, onClick }: {
  row: TrajectoryRow; idx: number; state: NodeState; isActive: boolean;
  onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const cfg = STATE_COLOR[state];
  const meta = GRADE_META[row.grade] ?? { emoji: "📍", tagline: row.grade_name, plain: "" };
  const cx = nodeX(idx);
  const cy = nodeY(idx);

  return (
    <g
      transform={`translate(${cx},${cy})`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onClick}
      style={{ cursor: "pointer" }}
    >
      {/* Outer glow ring */}
      {state !== "locked" && (
        <circle r={NODE_R + 8} fill="none" stroke={cfg.stroke} strokeWidth={2}
          strokeDasharray={state === "active" ? "6 4" : undefined}
          opacity={0.5}>
          {state === "active" && (
            <animateTransform attributeName="transform" type="rotate"
              from="0" to="360" dur="8s" repeatCount="indefinite" />
          )}
        </circle>
      )}

      {/* Hover ring indicator */}
      {hovered && (
        <circle r={NODE_R + 14} fill="none" stroke={cfg.stroke} strokeWidth={2} opacity={0.3}
          strokeDasharray="4 3" />
      )}

      {/* Main circle */}
      <defs>
        <radialGradient id={`ng-${idx}`} cx="35%" cy="30%">
          <stop offset="0%" stopColor={cfg.fill1} />
          <stop offset="100%" stopColor={cfg.fill2} />
        </radialGradient>
      </defs>
      <circle r={NODE_R}
        fill={`url(#ng-${idx})`}
        stroke={cfg.stroke}
        strokeWidth={hovered ? 3.5 : (state === "locked" ? 1.5 : 2.5)}
        style={cfg.glow ? { filter: cfg.glow } : undefined}
        opacity={state === "locked" ? 0.55 : 1}
      />

      {/* Pulse ring for active */}
      {(state === "active" || state === "detour") && (
        <circle r={NODE_R} fill="none" stroke={cfg.stroke} strokeWidth={3} opacity={0.4}>
          <animate attributeName="r" values={`${NODE_R};${NODE_R + 18};${NODE_R}`} dur="2.5s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.5;0;0.5" dur="2.5s" repeatCount="indefinite" />
        </circle>
      )}

      {/* Emoji */}
      <text textAnchor="middle" dominantBaseline="middle" fontSize={state === "locked" ? 22 : 26}
        opacity={state === "locked" ? 0.5 : 1} y={-4}>
        {meta.emoji}
      </text>

      {/* Mastery % for attempted grades */}
      {row.standards_attempted > 0 && (
        <text textAnchor="middle" dominantBaseline="middle"
          fontSize={10} fontWeight="700"
          fill={state === "mastered" ? "#78350f" : state === "locked" ? "#94a3b8" : "white"}
          y={18} opacity={state === "locked" ? 0.5 : 1}>
          {Math.round(row.mastery_pct)}%
        </text>
      )}

      {/* Grade label below node */}
      <text textAnchor="middle" y={NODE_R + 18} fontSize={11} fontWeight="700"
        fill={state === "locked" ? "#94a3b8" : "#1e293b"} opacity={state === "locked" ? 0.5 : 1}>
        {row.grade_name}
      </text>
      <text textAnchor="middle" y={NODE_R + 31} fontSize={10}
        fill={state === "locked" ? "#cbd5e1" : "#64748b"} opacity={state === "locked" ? 0.5 : 1}>
        {meta.tagline}
      </text>

      {/* "Tap to explore" hint */}
      <text textAnchor="middle" y={NODE_R + 44} fontSize={8.5}
        fill={hovered ? (state === "locked" ? "#94a3b8" : "#6366f1") : "transparent"}
        fontWeight="600">
        tap to explore →
      </text>

      {/* Stars for mastered */}
      {state === "mastered" && (
        <>
          <text x={-NODE_R - 2} y={-NODE_R + 4} fontSize={12}>✦</text>
          <text x={NODE_R - 6}  y={-NODE_R + 4} fontSize={12}>✦</text>
        </>
      )}

      {/* "YOU ARE HERE" pin for active */}
      {isActive && state !== "mastered" && (
        <>
          <circle cx={0} cy={-NODE_R - 18} r={8} fill="#6366f1" />
          <text textAnchor="middle" x={0} y={-NODE_R - 14} fontSize={8} fill="white" fontWeight="bold">▼</text>
        </>
      )}

      {/* Tooltip */}
      {hovered && (
        <foreignObject x={NODE_R + 8} y={-28} width={210} height={140} style={{ overflow: "visible" }}>
          <div className="bg-slate-900 text-white text-xs rounded-2xl px-4 py-3 shadow-2xl w-52">
            <p className="font-bold text-sm mb-0.5">{row.grade_name} — {meta.tagline}</p>
            <p className="text-slate-300 leading-relaxed mb-2">{meta.plain}</p>
            <div className="border-t border-slate-700 pt-2 flex justify-between text-slate-400 text-[11px]">
              <span>{row.standards_mastered}/{row.standards_total} skills done</span>
              {row.standards_attempted > 0 && (
                <span className={row.mastery_pct >= 75 ? "text-yellow-400" : row.mastery_pct >= 50 ? "text-indigo-400" : "text-amber-400"}>
                  {Math.round(row.mastery_pct)}%
                </span>
              )}
            </div>
          </div>
        </foreignObject>
      )}
    </g>
  );
}

function TrainingCamp({ gaps, campY, detourNodeX, detourNodeY }: {
  gaps: Gap[]; campY: number; detourNodeX: number; detourNodeY: number;
}) {
  const shown = gaps.slice(0, 4);

  return (
    <>
      <path
        d={campPathD(detourNodeX + NODE_R, detourNodeY)}
        fill="none"
        stroke="#f59e0b"
        strokeWidth={2.5}
        strokeDasharray="7 4"
        markerEnd="url(#arrowAmber)"
      />
      <path
        d={`M ${CAMP_X_MAIN},${campY + 160} C ${CAMP_X_MAIN},${campY + 200} ${detourNodeX},${detourNodeY + 120} ${detourNodeX},${detourNodeY + 120}`}
        fill="none"
        stroke="#f59e0b"
        strokeWidth={1.5}
        strokeDasharray="5 4"
        opacity={0.5}
      />
      <foreignObject x={CAMP_X_MAIN - 10} y={campY - 10} width={130} height={shown.length * 52 + 80} style={{ overflow: "visible" }}>
        <div className="bg-amber-50 border-2 border-amber-300 rounded-2xl p-3 shadow-xl w-32">
          <div className="flex items-center gap-1.5 mb-2">
            <div className="w-5 h-5 bg-amber-400 rounded-md flex items-center justify-center text-white text-[10px] font-bold">!</div>
            <div>
              <p className="text-[10px] font-bold text-amber-800 uppercase tracking-wide leading-none">Training</p>
              <p className="text-[10px] font-bold text-amber-800 uppercase tracking-wide leading-none">Camp</p>
            </div>
          </div>
          <div className="space-y-1.5">
            {shown.map((gap, i) => (
              <GapMiniCard key={i} gap={gap} />
            ))}
          </div>
          {gaps.length > 4 && (
            <p className="text-[10px] text-amber-500 text-center mt-1.5">+{gaps.length - 4} more</p>
          )}
          <p className="text-[9px] text-amber-600 mt-2 font-medium text-center">loops back when done</p>
        </div>
      </foreignObject>
    </>
  );
}

function GapMiniCard({ gap }: { gap: Gap }) {
  const [hovered, setHovered] = useState(false);
  const pct = Math.round((gap.p_mastery || 0) * 100);
  const plain = plainGap(gap.description);
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="relative"
    >
      {hovered && (
        <div className="absolute right-full mr-2 top-0 z-50 w-48 bg-slate-900 text-white text-xs rounded-xl px-3 py-2 shadow-2xl pointer-events-none">
          <p className="font-semibold mb-1">{gap.code}</p>
          <p className="text-slate-300 leading-relaxed">{plain}</p>
          <p className="text-amber-400 mt-1">Mastery: {pct}%</p>
        </div>
      )}
      <div className="flex items-center gap-1.5 p-1.5 bg-white rounded-lg border border-amber-200 cursor-default">
        <div className="w-5 h-5 rounded-md bg-amber-400 flex items-center justify-center text-[9px] text-white font-bold flex-shrink-0">{pct}</div>
        <p className="text-[10px] text-slate-700 leading-tight line-clamp-2">{plain}</p>
      </div>
    </div>
  );
}


// ── Skill detail node (used in grade drill-down) ───────────────────────────────

function SkillDetailNode({ skill, idx, state }: { skill: SkillNode; idx: number; state: NodeState }) {
  const [hovered, setHovered] = useState(false);
  const cfg = STATE_COLOR[state];
  const cx = detailNodeX(idx);
  const cy = detailNodeY(idx);
  const emoji = skillEmoji(skill.description);
  const plain = toPlainEnglish(skill.description);
  const pct = Math.round((skill.mastery || 0) * 100);
  const isRight = idx % 2 === 1;

  return (
    <g transform={`translate(${cx},${cy})`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{ cursor: "default" }}>

      {/* Glow ring */}
      {state !== "locked" && (
        <circle r={DETAIL_R + 5} fill="none" stroke={cfg.stroke} strokeWidth={1.5} opacity={0.4}>
          {state === "active" && (
            <animate attributeName="r" values={`${DETAIL_R};${DETAIL_R + 12};${DETAIL_R}`} dur="2.5s" repeatCount="indefinite" />
          )}
        </circle>
      )}

      {/* Gradient def */}
      <defs>
        <radialGradient id={`dng-${idx}`} cx="35%" cy="30%">
          <stop offset="0%" stopColor={cfg.fill1} />
          <stop offset="100%" stopColor={cfg.fill2} />
        </radialGradient>
      </defs>

      {/* Main circle */}
      <circle r={DETAIL_R}
        fill={`url(#dng-${idx})`}
        stroke={cfg.stroke}
        strokeWidth={state === "locked" ? 1.5 : 2}
        style={cfg.glow ? { filter: cfg.glow } : undefined}
        opacity={state === "locked" ? 0.5 : 1}
      />

      {/* Emoji */}
      <text textAnchor="middle" dominantBaseline="middle"
        fontSize={state === "locked" ? 13 : 15}
        opacity={state === "locked" ? 0.5 : 1} y={-3}>
        {emoji}
      </text>

      {/* Mastery % */}
      {skill.attempts > 0 && (
        <text textAnchor="middle" dominantBaseline="middle" fontSize={9} fontWeight="700"
          fill={state === "mastered" ? "#78350f" : state === "locked" ? "#94a3b8" : "white"}
          y={12} opacity={state === "locked" ? 0.5 : 1}>
          {pct}%
        </text>
      )}

      {/* Stars for mastered */}
      {state === "mastered" && (
        <>
          <text x={-DETAIL_R - 2} y={-DETAIL_R + 2} fontSize={9}>✦</text>
          <text x={DETAIL_R - 5}  y={-DETAIL_R + 2} fontSize={9}>✦</text>
        </>
      )}

      {/* Label — alternates left/right to match zigzag */}
      <text
        textAnchor={isRight ? "end" : "start"}
        x={isRight ? -(DETAIL_R + 8) : (DETAIL_R + 8)}
        y={0}
        fontSize={10} fontWeight="600"
        fill={state === "locked" ? "#94a3b8" : "#1e293b"}
        opacity={state === "locked" ? 0.5 : 1}>
        {plain.length > 24 ? plain.slice(0, 21) + "…" : plain}
      </text>

      {/* Tooltip */}
      {hovered && (
        <foreignObject
          x={isRight ? -(DETAIL_R + 210) : (DETAIL_R + 8)}
          y={-28}
          width={190}
          height={120}
          style={{ overflow: "visible" }}>
          <div className="bg-slate-900 text-white text-xs rounded-xl px-3 py-2.5 shadow-2xl w-44">
            <p className="font-bold text-sm mb-0.5">{plain}</p>
            <p className="text-slate-500 text-[10px] mb-1.5">{skill.code}</p>
            <div className="border-t border-slate-700 pt-1.5 space-y-0.5 text-slate-300 text-[11px]">
              {skill.attempts > 0 ? (
                <>
                  <div className="flex justify-between">
                    <span>Mastery</span>
                    <span className={pct >= 75 ? "text-yellow-400 font-bold" : pct >= 50 ? "text-indigo-400 font-bold" : "text-amber-400 font-bold"}>{pct}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Attempts</span>
                    <span className="text-white">{skill.attempts}</span>
                  </div>
                  {skill.skillType === "gap" && (
                    <p className="text-amber-400 mt-1 font-medium">Gap — needs practice</p>
                  )}
                  {skill.skillType === "strength" && (
                    <p className="text-yellow-400 mt-1 font-medium">Strength area</p>
                  )}
                </>
              ) : (
                <p className="text-slate-400">Not started yet</p>
              )}
            </div>
          </div>
        </foreignObject>
      )}
    </g>
  );
}

// ── Grade detail view (zoomed-in map) ─────────────────────────────────────────

function GradeDetailView({ row, skills, loading, onBack }: {
  row: TrajectoryRow;
  skills: SkillNode[] | null;
  loading: boolean;
  onBack: () => void;
}) {
  const meta = GRADE_META[row.grade] ?? { emoji: "📍", tagline: row.grade_name, plain: "" };
  const shown = (skills || []).slice(0, MAX_SHOWN);
  const skillStates = shown.map(s => skillNodeState(s));
  const detailH = shown.length > 0
    ? detailNodeY(shown.length - 1) + DETAIL_R + 70
    : 180;

  // Summary counts
  const masteredCount = (skills || []).filter(s => s.mastery >= 0.75 && s.attempts > 0).length;
  const gapCount      = (skills || []).filter(s => s.skillType === "gap").length;
  const inProgressCnt = (skills || []).filter(s => s.attempts > 0 && s.mastery < 0.75 && s.skillType !== "gap").length;

  return (
    <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm">

      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-gradient-to-r from-slate-50 to-indigo-50">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-indigo-600 font-medium transition-colors px-3 py-1.5 rounded-lg hover:bg-white border border-transparent hover:border-slate-200"
          >
            ← Back to Map
          </button>
          <div className="w-px h-5 bg-slate-200" />
          <span className="text-2xl">{meta.emoji}</span>
          <div>
            <p className="font-bold text-slate-900">{row.grade_name} — {meta.tagline}</p>
            <p className="text-xs text-slate-500">{meta.plain}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-center">
            <p className="text-lg font-bold text-yellow-600">{masteredCount}</p>
            <p className="text-[10px] text-slate-400 uppercase tracking-wide">mastered</p>
          </div>
          {inProgressCnt > 0 && (
            <div className="text-center">
              <p className="text-lg font-bold text-indigo-600">{inProgressCnt}</p>
              <p className="text-[10px] text-slate-400 uppercase tracking-wide">in progress</p>
            </div>
          )}
          {gapCount > 0 && (
            <div className="text-center">
              <p className="text-lg font-bold text-amber-600">{gapCount}</p>
              <p className="text-[10px] text-slate-400 uppercase tracking-wide">gaps</p>
            </div>
          )}
        </div>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex flex-col items-center justify-center h-48 text-slate-400 gap-3">
          <div className="w-8 h-8 border-3 border-indigo-200 border-t-indigo-600 rounded-full animate-spin" style={{ borderWidth: 3 }} />
          <p className="text-sm">Zooming into {row.grade_name}…</p>
        </div>
      ) : !skills || skills.length === 0 ? (
        <div className="p-10 text-center">
          <div className="text-5xl mb-3">{meta.emoji}</div>
          <p className="font-semibold text-slate-700 text-lg">
            {row.grade_status === "not_started"
              ? "This grade is still ahead on the path"
              : "No tracked concepts yet"}
          </p>
          <p className="text-sm text-slate-400 mt-2 max-w-xs mx-auto">
            {row.grade_status === "not_started"
              ? `${row.grade_name} will unlock as your child progresses. It covers: ${meta.plain}.`
              : "Complete more assessments to see individual skill progress here."}
          </p>
          {row.standards_total > 0 && (
            <p className="mt-4 text-xs text-slate-400">
              {row.standards_total} skills total · {row.standards_mastered} mastered
            </p>
          )}
        </div>
      ) : (
        <>
          {/* Mini adventure map */}
          <div className="overflow-y-auto" style={{ maxHeight: 600 }}>
            <svg
              viewBox={`0 0 ${DETAIL_W} ${detailH}`}
              width="100%"
              style={{ display: "block", background: "linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%)" }}
            >
              <defs>
                <marker id="darrowGold" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
                  <path d="M0,0 L6,3 L0,6 Z" fill="#fbbf24" />
                </marker>
                <marker id="darrowIndigo" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
                  <path d="M0,0 L6,3 L0,6 Z" fill="#6366f1" />
                </marker>
              </defs>

              {/* Background dots */}
              <MapBackground w={DETAIL_W} h={detailH} />

              {/* Grade label */}
              <text x={16} y={28} fontSize={11} fontWeight="700" fill="#64748b" opacity={0.7}>
                {row.grade_name} Skills
              </text>

              {/* Path segments */}
              {shown.map((_, i) => {
                if (i === shown.length - 1) return null;
                const x1 = detailNodeX(i), y1 = detailNodeY(i);
                const x2 = detailNodeX(i + 1), y2 = detailNodeY(i + 1);
                const st = skillStates[i];
                return (
                  <path key={i}
                    d={pathD(x1, y1, x2, y2)}
                    fill="none"
                    stroke={segColor(st)}
                    strokeWidth={st === "locked" ? 2 : 3.5}
                    strokeDasharray={st === "locked" ? "6 6" : undefined}
                    strokeLinecap="round"
                    opacity={st === "locked" ? 0.3 : 0.75}
                    markerEnd={st === "mastered" ? "url(#darrowGold)" : st !== "locked" ? "url(#darrowIndigo)" : undefined}
                  />
                );
              })}

              {/* Skill nodes */}
              {shown.map((skill, i) => (
                <SkillDetailNode key={skill.identifier} skill={skill} idx={i} state={skillStates[i]} />
              ))}
            </svg>
          </div>

          {/* Footer */}
          <div className="px-6 py-3 border-t border-slate-100 flex items-center justify-between bg-slate-50">
            <p className="text-xs text-slate-400">
              Showing {shown.length} of {skills.length} tracked concept{skills.length !== 1 ? "s" : ""}
              {skills.length < row.standards_total && ` (${row.standards_total} total in curriculum)`}
            </p>
            <div className="flex items-center gap-3 text-[11px]">
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-full bg-yellow-400 inline-block" /> Mastered
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-full bg-indigo-500 inline-block" /> In progress
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-400 inline-block" /> Gap
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function JourneyPage() {
  const [studentId, setStudentId] = useState("student_001");
  const [childName, setChildName] = useState("");
  const [subject, setSubject] = useState("math");
  const [loading, setLoading] = useState(false);
  const [trajectory, setTrajectory] = useState<TrajectoryRow[] | null>(null);
  const [gaps, setGaps] = useState<Gap[] | null>(null);
  const [activeGrade, setActiveGrade] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Drill-down state
  const [view, setView] = useState<"map" | "grade">("map");
  const [selectedRow, setSelectedRow] = useState<TrajectoryRow | null>(null);
  const [gradeSkills, setGradeSkills] = useState<SkillNode[] | null>(null);
  const [gradeLoading, setGradeLoading] = useState(false);

  async function loadJourney() {
    if (!studentId.trim()) { setError("Please enter a student ID."); return; }
    setLoading(true); setError(null); setTrajectory(null); setGaps(null);
    setView("map"); setSelectedRow(null); setGradeSkills(null);
    try {
      const [tRes, gRes] = await Promise.all([
        fetch(`${API}/assessment/student/${encodeURIComponent(studentId)}/trajectory?subject=${subject}&state=Multi-State`),
        fetch(`${API}/students/${encodeURIComponent(studentId)}/gaps?subject=${subject}`),
      ]);
      if (!tRes.ok) throw new Error("Could not load journey — check the student ID.");
      const t = await tRes.json();
      const g = gRes.ok ? await gRes.json() : { blocking_gaps: [] };
      setTrajectory(t.trajectory || []);
      setGaps(g.blocking_gaps || []);
      setActiveGrade(t.active_grade || null);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function openGrade(row: TrajectoryRow) {
    setSelectedRow(row);
    setView("grade");
    setGradeSkills(null);
    setGradeLoading(true);
    try {
      const res = await fetch(`${API}/chat/context/${encodeURIComponent(studentId)}?subject=${subject}`);
      if (!res.ok) { setGradeSkills([]); return; }
      const data = await res.json();

      const all = new Map<string, SkillNode>();
      (data.gaps || []).forEach((s: any) =>
        all.set(s.identifier, { ...s, skillType: "gap" as const }));
      (data.recent || []).forEach((s: any) => {
        if (!all.has(s.identifier)) all.set(s.identifier, { ...s, skillType: "recent" as const });
      });
      (data.strengths || []).forEach((s: any) => {
        if (!all.has(s.identifier)) all.set(s.identifier, { ...s, skillType: "strength" as const });
      });

      const filtered = [...all.values()].filter(s => s.grade === row.grade);
      setGradeSkills(filtered);
    } catch {
      setGradeSkills([]);
    } finally {
      setGradeLoading(false);
    }
  }

  function backToMap() {
    setView("map");
    setSelectedRow(null);
    setGradeSkills(null);
  }

  const name = childName || studentId || "your child";
  const hasDetour = !!(gaps && gaps.length > 0);

  const nodeStates: NodeState[] = (trajectory || []).map((row) => {
    const isActive = row.grade === activeGrade;
    return resolveState(row, isActive, hasDetour && isActive);
  });

  const campIdx = (() => {
    const active = (trajectory || []).findIndex(r => r.grade === activeGrade);
    if (active >= 0 && nodeStates[active] === "detour") return active;
    return nodeStates.findIndex(s => s === "detour");
  })();

  return (
    <div className="max-w-4xl mx-auto space-y-6">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Learning Journey Map</h1>
        <p className="text-slate-500 text-sm mt-1">
          Your child&apos;s live adventure through the curriculum.
          {trajectory
            ? " Click any grade node to zoom in and see its concepts."
            : " Hover any grade to see what they're learning — in plain language."}
        </p>
      </div>

      {/* Lookup form */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Child&apos;s name</label>
            <input value={childName} onChange={e => setChildName(e.target.value)} placeholder="Emma"
              className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Student ID</label>
            <input value={studentId} onChange={e => setStudentId(e.target.value)} placeholder="student_001"
              onKeyDown={e => e.key === "Enter" && loadJourney()}
              className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Subject</label>
            <div className="flex gap-2">
              {[{ id: "math", label: "Math" }, { id: "english", label: "ELA" }].map(s => (
                <button key={s.id} onClick={() => setSubject(s.id)}
                  className={`flex-1 py-2 rounded-xl text-sm font-medium transition-all border ${
                    subject === s.id ? "bg-indigo-600 text-white border-indigo-600" : "border-slate-200 text-slate-700 hover:border-indigo-300"
                  }`}>{s.label}</button>
              ))}
            </div>
          </div>
        </div>
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button onClick={loadJourney} disabled={loading}
          className="w-full bg-indigo-600 text-white py-2.5 rounded-xl font-semibold text-sm hover:bg-indigo-700 disabled:opacity-50 transition-colors">
          {loading ? "Loading map…" : "View Adventure Map"}
        </button>
      </div>

      {/* GPS reroute banner — only on main map view */}
      {view === "map" && trajectory && hasDetour && (
        <div className="bg-gradient-to-r from-amber-500 to-orange-500 rounded-2xl p-5 text-white shadow-lg">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 bg-white/20 rounded-xl flex items-center justify-center flex-shrink-0 text-xl">🧭</div>
            <div>
              <p className="font-bold text-base">GPS is Rerouting</p>
              <p className="text-amber-100 text-sm mt-0.5 leading-relaxed">
                The engine detected {gaps!.length} gap{gaps!.length !== 1 ? "s" : ""} on {name}&apos;s main path.
                A <strong className="text-white">Training Camp detour</strong> has been activated — like a GPS routing around traffic.
                Once gaps are cleared, {name} continues down the main highway.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Grade detail view (drill-down) ── */}
      {view === "grade" && selectedRow && (
        <GradeDetailView
          row={selectedRow}
          skills={gradeSkills}
          loading={gradeLoading}
          onBack={backToMap}
        />
      )}

      {/* ── Main Adventure Map ── */}
      {view === "map" && trajectory && (
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm">
          {/* Map header bar */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-slate-50">
            <div>
              <p className="font-bold text-slate-900">{name}&apos;s Adventure Path</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {subject === "math" ? "Mathematics" : "English Language Arts"} · Grades 1–8 · Click a grade to zoom in
              </p>
            </div>
            {activeGrade && (
              <div className="text-right">
                <p className="text-xs text-slate-400 uppercase tracking-wide">Current grade</p>
                <p className="font-bold text-indigo-700">
                  {GRADE_META[activeGrade]?.tagline ?? activeGrade}
                </p>
              </div>
            )}
          </div>

          {/* Scrollable SVG map */}
          <div className="overflow-y-auto overflow-x-hidden">
            <svg
              viewBox={`0 0 ${MAP_W} ${TOTAL_H}`}
              width="100%"
              style={{ display: "block", background: "linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%)" }}
            >
              {/* Arrow markers */}
              <defs>
                <marker id="arrowAmber" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
                  <path d="M0,0 L6,3 L0,6 Z" fill="#f59e0b" />
                </marker>
                <marker id="arrowGold" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
                  <path d="M0,0 L6,3 L0,6 Z" fill="#fbbf24" />
                </marker>
                <marker id="arrowIndigo" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
                  <path d="M0,0 L6,3 L0,6 Z" fill="#6366f1" />
                </marker>
              </defs>

              {/* Background dot grid */}
              <MapBackground w={MAP_W} h={TOTAL_H} />

              {/* Highway path segments */}
              {trajectory.map((row, i) => {
                if (i === trajectory.length - 1) return null;
                const x1 = nodeX(i), y1 = nodeY(i);
                const x2 = nodeX(i + 1), y2 = nodeY(i + 1);
                const color = segColor(nodeStates[i]);
                const isLocked = nodeStates[i] === "locked" && nodeStates[i + 1] === "locked";

                return (
                  <path key={i}
                    d={pathD(x1, y1, x2, y2)}
                    fill="none"
                    stroke={color}
                    strokeWidth={isLocked ? 2 : 4}
                    strokeDasharray={isLocked ? "6 6" : undefined}
                    strokeLinecap="round"
                    opacity={isLocked ? 0.3 : 0.85}
                  />
                );
              })}

              {/* Path from last grade to trophy */}
              {trajectory.length > 0 && (() => {
                const last = trajectory.length - 1;
                const x1 = nodeX(last), y1 = nodeY(last);
                return (
                  <path
                    d={pathD(x1, y1, MAP_W / 2, TROPHY_Y)}
                    fill="none"
                    stroke={segColor(nodeStates[last])}
                    strokeWidth={nodeStates[last] === "locked" ? 2 : 4}
                    strokeDasharray={nodeStates[last] === "locked" ? "6 6" : undefined}
                    strokeLinecap="round"
                    opacity={nodeStates[last] === "locked" ? 0.3 : 0.85}
                  />
                );
              })()}

              {/* Training camp branch */}
              {hasDetour && campIdx >= 0 && gaps && (
                <TrainingCamp
                  gaps={gaps}
                  campY={nodeY(campIdx) - 10}
                  detourNodeX={nodeX(campIdx)}
                  detourNodeY={nodeY(campIdx)}
                />
              )}

              {/* Grade nodes */}
              {trajectory.map((row, i) => (
                <GradeNode
                  key={row.grade}
                  row={row}
                  idx={i}
                  state={nodeStates[i]}
                  isActive={row.grade === activeGrade}
                  onClick={() => openGrade(row)}
                />
              ))}

              {/* Trophy (finish line) */}
              <g transform={`translate(${MAP_W / 2}, ${TROPHY_Y})`}>
                <circle r={32} fill="url(#trophyGrad)" stroke="#a855f7" strokeWidth={2.5}
                  style={{ filter: "drop-shadow(0 0 10px rgba(168,85,247,0.5))" }} />
                <defs>
                  <radialGradient id="trophyGrad" cx="35%" cy="30%">
                    <stop offset="0%" stopColor="#a855f7" />
                    <stop offset="100%" stopColor="#6d28d9" />
                  </radialGradient>
                </defs>
                <text textAnchor="middle" dominantBaseline="middle" fontSize={24}>🏆</text>
                <text textAnchor="middle" y={46} fontSize={11} fontWeight="700" fill="#6d28d9">High School Ready</text>
              </g>

              {/* Map title */}
              <text x={18} y={30} fontSize={11} fontWeight="700" fill="#64748b" opacity={0.7}>
                Adventure Path
              </text>
            </svg>
          </div>
        </div>
      )}

      {/* Legend */}
      {view === "map" && trajectory && (
        <div className="bg-white rounded-2xl border border-slate-200 p-4">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Map Legend</p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { emoji: "✦", bg: "bg-gradient-to-br from-yellow-400 to-amber-500", ring: "ring-2 ring-yellow-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]", label: "Mastered",    desc: "Goal reached" },
              { emoji: "▶", bg: "bg-gradient-to-br from-indigo-500 to-purple-600", ring: "ring-2 ring-indigo-400 shadow-[0_0_8px_rgba(99,102,241,0.6)]",  label: "Here Now",   desc: "Active grade" },
              { emoji: "!",  bg: "bg-gradient-to-br from-amber-400 to-orange-500", ring: "ring-2 ring-amber-400  shadow-[0_0_8px_rgba(245,158,11,0.6)]",  label: "Gap Detour", desc: "Training camp active" },
              { emoji: "🔒", bg: "bg-gradient-to-br from-slate-200 to-slate-300",  ring: "ring-1 ring-slate-200",                                          label: "Upcoming",   desc: "Locked ahead" },
            ].map(item => (
              <div key={item.label} className="flex items-center gap-2.5">
                <div className={`w-9 h-9 rounded-full ${item.bg} ${item.ring} flex items-center justify-center text-sm flex-shrink-0`}>
                  <span className="text-white font-bold text-xs">{item.emoji}</span>
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-700">{item.label}</p>
                  <p className="text-[11px] text-slate-400">{item.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!trajectory && !loading && (
        <div className="text-center py-16 text-slate-400">
          <div className="text-5xl mb-4">🗺️</div>
          <p className="text-lg font-medium text-slate-600">Ready to explore?</p>
          <p className="text-sm mt-1">Enter a student ID to reveal the adventure map.</p>
        </div>
      )}
    </div>
  );
}
