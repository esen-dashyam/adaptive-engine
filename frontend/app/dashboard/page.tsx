"use client";

import { useState, useEffect } from "react";

const API = "/api/v1";

type TrajectoryRow = {
  grade: string; grade_name: string;
  standards_total: number; standards_attempted: number; standards_mastered: number;
  coverage_pct: number; mastery_pct: number; grade_status: string;
};

type Gap = {
  node_id: string; code: string; description: string;
  subject: string; p_mastery: number; nano_weight: number; blocked_count: number;
};

export default function DashboardPage() {
  const [studentId, setStudentId] = useState("student_001");
  const [subject,   setSubject]   = useState("math");
  const [loading,   setLoading]   = useState(false);
  const [trajectory, setTrajectory] = useState<TrajectoryRow[] | null>(null);
  const [gaps,       setGaps]       = useState<Gap[] | null>(null);
  const [error,      setError]      = useState<string | null>(null);

  async function loadDashboard() {
    setLoading(true); setError(null);
    try {
      const [tRes, gRes] = await Promise.all([
        fetch(`${API}/assessment/student/${studentId}/trajectory?subject=${subject}&state=Multi-State`),
        fetch(`${API}/students/${studentId}/gaps?subject=${subject === "math" ? "math" : "english"}`),
      ]);

      if (tRes.ok) {
        const t = await tRes.json();
        setTrajectory(t.trajectory || []);
      }
      if (gRes.ok) {
        const g = await gRes.json();
        setGaps(g.blocking_gaps || []);
      }
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  const statusColor: Record<string, string> = {
    above:        "bg-green-100 text-green-700",
    at:           "bg-blue-100 text-blue-700",
    approaching:  "bg-amber-100 text-amber-700",
    below:        "bg-red-100 text-red-700",
    not_started:  "bg-gray-100 text-gray-500",
  };

  const masteryColor = (pct: number) =>
    pct >= 85 ? "bg-green-500" : pct >= 65 ? "bg-blue-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Student Dashboard</h1>
        <p className="text-gray-500 mt-1">K1–K8 mastery trajectory · BKT nano weights · blocking gaps</p>
      </div>

      {/* Controls */}
      <div className="bg-white rounded-2xl border border-gray-200 p-6">
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Student ID</label>
            <input
              value={studentId}
              onChange={e => setStudentId(e.target.value)}
              className="border border-gray-200 rounded-xl px-4 py-2.5 text-sm w-48 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Subject</label>
            <div className="flex gap-2">
              {[
                { id: "math",    label: "Math",    emoji: "🔢" },
                { id: "english", label: "ELA",     emoji: "📖" },
              ].map(s => (
                <button
                  key={s.id}
                  onClick={() => setSubject(s.id)}
                  className={`px-4 py-2.5 rounded-xl text-sm font-medium transition-all border ${
                    subject === s.id
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-gray-700 border-gray-200 hover:border-blue-300"
                  }`}
                >
                  {s.emoji} {s.label}
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={loadDashboard}
            disabled={loading}
            className="bg-blue-600 text-white px-6 py-2.5 rounded-xl font-medium text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? "Loading…" : "Load Dashboard"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm">{error}</div>
      )}

      {/* Grade trajectory */}
      {trajectory && (
        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 text-xl mb-6">Grade K1–K8 Trajectory</h2>
          <div className="space-y-3">
            {trajectory.map(row => (
              <div key={row.grade} className="flex items-center gap-4">
                <div className="w-16 text-sm font-semibold text-gray-700 flex-shrink-0">
                  {row.grade_name}
                </div>
                <div className="flex-1">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-gray-500">
                      {row.standards_mastered}/{row.standards_total} mastered
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusColor[row.grade_status] || "bg-gray-100 text-gray-500"}`}>
                      {row.grade_status.replace("_", " ")}
                    </span>
                  </div>
                  <div className="flex gap-1.5">
                    {/* Coverage bar */}
                    <div className="flex-1 h-3 bg-gray-100 rounded-full overflow-hidden" title={`Coverage: ${row.coverage_pct}%`}>
                      <div
                        className="h-3 bg-gray-400 rounded-full transition-all"
                        style={{ width: `${row.coverage_pct}%` }}
                      />
                    </div>
                    {/* Mastery bar */}
                    <div className="flex-1 h-3 bg-gray-100 rounded-full overflow-hidden" title={`Mastery: ${row.mastery_pct}%`}>
                      <div
                        className={`h-3 rounded-full transition-all ${masteryColor(row.mastery_pct)}`}
                        style={{ width: `${row.mastery_pct}%` }}
                      />
                    </div>
                  </div>
                  <div className="flex gap-1.5 mt-1">
                    <span className="flex-1 text-xs text-gray-400 text-center">Coverage {row.coverage_pct}%</span>
                    <span className="flex-1 text-xs text-gray-400 text-center">Mastery {row.mastery_pct}%</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 flex items-center gap-4 text-xs text-gray-400">
            <span className="flex items-center gap-1"><span className="w-3 h-3 bg-gray-400 rounded-full inline-block"/> Coverage (attempted/total)</span>
            <span className="flex items-center gap-1"><span className="w-3 h-3 bg-blue-500 rounded-full inline-block"/> Mastery (BKT P≥0.85)</span>
          </div>
        </div>
      )}

      {/* Blocking gaps */}
      {gaps && (
        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 text-xl mb-2">Blocking Knowledge Gaps</h2>
          <p className="text-sm text-gray-500 mb-6">
            Standards where low mastery blocks the most downstream concepts
          </p>
          {gaps.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              <div className="text-4xl mb-2">🎉</div>
              <p className="font-medium text-gray-600">No blocking gaps detected</p>
              <p className="text-sm mt-1">Complete more assessments to track progress</p>
            </div>
          ) : (
            <div className="space-y-3">
              {gaps.map((gap, i) => (
                <div key={i} className="flex items-start gap-4 p-4 bg-red-50 border border-red-200 rounded-xl">
                  <div className="flex-shrink-0 mt-0.5">
                    <div className="w-9 h-9 bg-red-100 rounded-lg flex items-center justify-center">
                      <span className="text-red-600 font-bold text-sm">{gap.blocked_count}</span>
                    </div>
                    <div className="text-xs text-red-400 text-center mt-0.5">blocked</div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-gray-900 text-sm">{gap.code}</span>
                      <span className="text-xs text-gray-500 bg-white border border-gray-200 px-2 py-0.5 rounded-full">
                        nano: {gap.nano_weight}/100
                      </span>
                    </div>
                    <p className="text-sm text-gray-700 leading-snug line-clamp-2">{gap.description}</p>
                  </div>
                  <div className="flex-shrink-0 text-right">
                    <div className="text-lg font-bold text-red-600">
                      {Math.round((gap.p_mastery || 0) * 100)}%
                    </div>
                    <div className="text-xs text-gray-400">mastery</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!trajectory && !loading && (
        <div className="text-center py-16 text-gray-400">
          <div className="text-5xl mb-4">📊</div>
          <p className="text-lg font-medium text-gray-600">Enter a student ID and click Load Dashboard</p>
          <p className="text-sm mt-1">Data is loaded from Neo4j SKILL_STATE relationships</p>
        </div>
      )}
    </div>
  );
}
