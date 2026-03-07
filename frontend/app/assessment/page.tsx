"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const API = "/api/v1";

const GRADES = Array.from({ length: 8 }, (_, i) => ({
  id: `K${i + 1}`, label: `Grade ${i + 1}`, ages: `${i + 6}–${i + 7}`,
}));

const SUBJECTS = [
  { id: "math",    label: "Math",                  desc: "Numbers, operations, geometry" },
  { id: "english", label: "English Language Arts", desc: "Reading, writing, vocabulary" },
];

const STATES = [
  { abbrev: "Multi-State", name: "Common Core" },
  { abbrev: "TX", name: "Texas (TEKS)" },
  { abbrev: "CA", name: "California" },
  { abbrev: "FL", name: "Florida (B.E.S.T.)" },
  { abbrev: "NY", name: "New York" },
  { abbrev: "GA", name: "Georgia" },
  { abbrev: "NC", name: "North Carolina" },
  { abbrev: "OH", name: "Ohio" },
];

type Question = {
  id: string; question: string; options: string[];
  answer: string; dok_level: number; category: string;
  node_ref: string; standard_code: string; standard_description: string;
  beta?: number;
};

type Assessment = {
  assessment_id: string; grade: string; subject: string;
  state: string; framework: string; estimated_minutes: number;
  num_questions: number; prerequisite_count: number; target_count: number;
  questions: Question[];
};

type EvalResult = {
  score: number; correct: number; total: number;
  grade_status: string; prerequisite_score: number | null;
  target_score: number | null; gap_count: number;
  gaps?: any[];
  gap_exercises: any[];
  recommendations: any[];
  bkt_updates: { node: string; mastery: number }[];
  theta?: number;
  theta_history?: number[];
  hard_blocked_count?: number;
  misconceptions?: any[];
  session_narrative?: string;
  focus_concept?: string;
};

const STATUS_LABEL: Record<string, string> = {
  above: "Above grade level", at: "At grade level",
  approaching: "Almost there", below: "Needs more practice",
};
const STATUS_COLOR: Record<string, string> = {
  above: "text-emerald-700 bg-emerald-50 border-emerald-200",
  at:    "text-indigo-700 bg-indigo-50 border-indigo-200",
  approaching: "text-amber-700 bg-amber-50 border-amber-200",
  below: "text-red-700 bg-red-50 border-red-200",
};


export default function AssessmentPage() {
  const router = useRouter();
  const [step, setStep]           = useState<"config" | "taking" | "results">("config");
  const [studentId, setStudentId] = useState("student_001");
  const [childName, setChildName] = useState("");
  const [grade, setGrade]         = useState("K3");
  const [subject, setSubject]     = useState("math");
  const [state, setState]         = useState("Multi-State");
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [answers, setAnswers]     = useState<Record<string, string>>({});
  const [results, setResults]     = useState<EvalResult | null>(null);
  const [geminiError, setGeminiError] = useState(false);

  async function startAssessment() {
    setLoading(true); setError(null); setGeminiError(false);
    try {
      const res = await fetch(`${API}/assessment/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grade, subject, student_id: studentId, state, num_questions: 15 }),
      });
      if (res.status === 503) {
        let b: any = {};
        try { b = await res.json(); } catch {}
        if (b?.detail?.gemini_required) { setGeminiError(true); return; }
      }
      if (!res.ok) {
        let d = "Could not generate assessment";
        try { const b = await res.json(); d = b?.detail || d; } catch {}
        throw new Error(d);
      }
      const data: Assessment = await res.json();
      setAssessment(data); setAnswers({}); setStep("taking");
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function submitAssessment() {
    if (!assessment) return;
    setLoading(true); setError(null);
    try {
      const payload = assessment.questions.map(q => ({
        question_id: q.id, question: q.question, options: q.options,
        answer: q.answer, dok_level: q.dok_level, beta: q.beta ?? 0,
        node_ref: q.node_ref, category: q.category,
        standard_code: q.standard_code, standard_description: q.standard_description,
        student_answer: answers[q.id] || "",
        is_correct: answers[q.id] === q.answer,
      }));
      const res = await fetch(`${API}/assessment/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ assessment_id: assessment.assessment_id, student_id: studentId, grade, subject, state, answers: payload }),
      });
      if (!res.ok) {
        let d = `Evaluation failed (HTTP ${res.status})`;
        try {
          const b = await res.json();
          if (b?.detail) d = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail);
        } catch {}
        throw new Error(d);
      }
      const data: EvalResult = await res.json();
      setResults(data); setStep("results");
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  function goToTutor() {
    const params = new URLSearchParams({
      student_id: studentId,
      grade: grade.replace("K", ""),
      subject,
    });
    router.push(`/tutor?${params.toString()}`);
  }

  const answered = Object.keys(answers).length;
  const total    = assessment?.questions.length || 0;

  // ── Config ───────────────────────────────────────────────────────────────
  if (step === "config") {
    return (
      <div className="max-w-xl mx-auto">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-slate-900">Start Your Assessment</h1>
          <p className="text-slate-500 mt-1 text-sm">15 questions · ~25 minutes · adapts to your level</p>
        </div>

        {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-xl p-3 text-red-700 text-sm">{error}</div>}

        {geminiError && (
          <div className="mb-6 bg-white border border-amber-200 rounded-2xl p-5 space-y-3">
            <p className="font-semibold text-slate-900">API key needed</p>
            <p className="text-slate-500 text-sm">Add your <code className="bg-slate-100 px-1 rounded">GEMINI_API_KEY</code> to the <code className="bg-slate-100 px-1 rounded">.env</code> file and restart the backend.</p>
          </div>
        )}

        <div className="bg-white rounded-2xl border border-slate-200 p-6 space-y-6">
          {/* Name + ID */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Your name</label>
              <input value={childName} onChange={e => setChildName(e.target.value)}
                placeholder="Emma"
                className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Student ID</label>
              <input value={studentId} onChange={e => setStudentId(e.target.value)}
                placeholder="student_001"
                className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
            </div>
          </div>

          {/* Grade */}
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-2 uppercase tracking-wide">Grade level</label>
            <div className="grid grid-cols-4 gap-2">
              {GRADES.map(g => (
                <button key={g.id} onClick={() => setGrade(g.id)}
                  className={`py-2.5 rounded-xl text-sm font-medium transition-all border ${
                    grade === g.id ? "bg-indigo-600 text-white border-indigo-600" : "border-slate-200 text-slate-700 hover:border-indigo-300"
                  }`}>
                  <div>{g.label}</div>
                  <div className="text-xs opacity-60">{g.ages}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Subject */}
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-2 uppercase tracking-wide">Subject</label>
            <div className="grid grid-cols-2 gap-3">
              {SUBJECTS.map(s => (
                <button key={s.id} onClick={() => setSubject(s.id)}
                  className={`py-4 rounded-xl text-sm font-medium transition-all border text-left px-4 ${
                    subject === s.id ? "bg-indigo-600 text-white border-indigo-600" : "border-slate-200 text-slate-700 hover:border-indigo-300"
                  }`}>
                  <div className="font-semibold">{s.label}</div>
                  <div className={`text-xs mt-0.5 ${subject === s.id ? "text-indigo-200" : "text-slate-400"}`}>{s.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* State */}
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-2 uppercase tracking-wide">Standards</label>
            <select value={state} onChange={e => setState(e.target.value)}
              className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white">
              {STATES.map(s => <option key={s.abbrev} value={s.abbrev}>{s.name}</option>)}
            </select>
          </div>

          <button onClick={startAssessment} disabled={loading}
            className="w-full bg-indigo-600 text-white py-3 rounded-xl font-semibold text-sm hover:bg-indigo-700 disabled:opacity-50 transition-colors">
            {loading ? "Building your assessment…" : "Start Assessment"}
          </button>
        </div>
      </div>
    );
  }

  // ── Taking ───────────────────────────────────────────────────────────────
  if (step === "taking" && assessment) {
    return (
      <div className="max-w-2xl mx-auto space-y-5">
        {/* Progress header */}
        <div className="bg-white rounded-2xl border border-slate-200 p-4 flex items-center justify-between">
          <div>
            <p className="font-semibold text-slate-900 text-sm">
              {assessment.grade.replace("K", "Grade ")} · {assessment.subject.charAt(0).toUpperCase() + assessment.subject.slice(1)}
            </p>
            <p className="text-xs text-slate-400 mt-0.5">{assessment.num_questions} questions · ~{assessment.estimated_minutes} min</p>
          </div>
          <div className="text-right">
            <div className="text-2xl font-bold text-indigo-600">{answered}<span className="text-slate-300 text-lg">/{total}</span></div>
            <div className="text-xs text-slate-400">answered</div>
          </div>
        </div>

        <div className="h-1.5 bg-slate-200 rounded-full overflow-hidden">
          <div className="h-1.5 bg-indigo-500 rounded-full transition-all" style={{ width: `${(answered / total) * 100}%` }} />
        </div>

        {error && <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-red-700 text-sm">{error}</div>}

        {assessment.questions.map((q, idx) => (
          <div key={q.id} className="bg-white rounded-2xl border border-slate-200 p-5">
            <div className="flex items-center gap-2 mb-4">
              <span className="w-6 h-6 bg-indigo-100 text-indigo-700 rounded-full flex items-center justify-center text-xs font-bold">{idx + 1}</span>
              <span className="text-xs text-slate-400">{q.standard_code}</span>
            </div>
            <p className="text-slate-900 font-medium text-sm leading-relaxed mb-4">{q.question}</p>
            <div className="space-y-2">
              {q.options.map(opt => {
                const letter = opt.charAt(0);
                const sel = answers[q.id] === letter;
                return (
                  <button key={opt} onClick={() => setAnswers(p => ({ ...p, [q.id]: letter }))}
                    className={`flex items-center gap-3 w-full p-3.5 rounded-xl text-left transition-all border text-sm ${
                      sel ? "bg-indigo-50 border-indigo-400 text-indigo-900" : "border-slate-200 hover:border-indigo-300 text-slate-700"
                    }`}>
                    <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                      sel ? "bg-indigo-500 text-white" : "bg-slate-100 text-slate-500"
                    }`}>{letter}</span>
                    <span>{opt.substring(3)}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        <button onClick={submitAssessment} disabled={loading || answered < total}
          className="w-full bg-indigo-600 text-white py-3.5 rounded-xl font-semibold text-sm hover:bg-indigo-700 disabled:opacity-50 transition-colors">
          {loading ? "Analysing your answers…" : answered < total ? `${total - answered} question${total - answered !== 1 ? "s" : ""} remaining` : "Submit Assessment"}
        </button>
      </div>
    );
  }

  // ── Results ──────────────────────────────────────────────────────────────
  if (step === "results" && results) {
    const scorePct = Math.round(results.score * 100);
    const name = childName || studentId;
    const circumference = 2 * Math.PI * 40;

    return (
      <div className="max-w-xl mx-auto flex flex-col items-center justify-center min-h-[70vh] space-y-6 text-center">

        {/* Score ring */}
        <div className="relative w-40 h-40">
          <svg className="w-40 h-40 -rotate-90" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="40" stroke="#e2e8f0" strokeWidth="10" fill="none" />
            <circle cx="50" cy="50" r="40" stroke="#6366f1" strokeWidth="10" fill="none"
              strokeDasharray={circumference}
              strokeDashoffset={circumference * (1 - scorePct / 100)}
              strokeLinecap="round"
              style={{ transition: "stroke-dashoffset 1s ease" }}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-4xl font-bold text-slate-900">{scorePct}%</span>
            <span className="text-xs text-slate-400 mt-0.5">{results.correct}/{results.total}</span>
          </div>
        </div>

        {/* Status badge */}
        <div>
          <div className={`inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-1.5 rounded-full border mb-3 ${STATUS_COLOR[results.grade_status] ?? "text-slate-600 bg-slate-50 border-slate-200"}`}>
            <span className="w-2 h-2 rounded-full bg-current opacity-70" />
            {STATUS_LABEL[results.grade_status] ?? results.grade_status}
          </div>
          <h1 className="text-2xl font-bold text-slate-900 mb-2">Assessment Complete</h1>
          <p className="text-slate-500 text-sm max-w-sm mx-auto leading-relaxed">
            {results.session_narrative ||
              `You answered ${results.correct} of ${results.total} questions correctly.`}
          </p>
          {results.focus_concept && (
            <p className="text-xs text-indigo-600 mt-2 font-medium">
              Focus next: {results.focus_concept}
            </p>
          )}
        </div>

        {/* Quick stats */}
        <div className="grid grid-cols-3 gap-3 w-full">
          {[
            { label: "Gaps found", value: results.gap_count ?? 0, color: "text-red-600" },
            { label: "Mastery updates", value: results.bkt_updates?.length ?? 0, color: "text-indigo-600" },
            { label: "Exercises ready", value: results.gap_exercises?.length ?? 0, color: "text-emerald-600" },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-white border border-slate-200 rounded-xl p-3">
              <p className={`text-xl font-bold ${color}`}>{value}</p>
              <p className="text-xs text-slate-400 mt-0.5">{label}</p>
            </div>
          ))}
        </div>

        {/* CTA */}
        <div className="w-full space-y-3">
          <button
            onClick={goToTutor}
            className="w-full bg-indigo-600 text-white py-4 rounded-2xl font-bold text-base hover:bg-indigo-700 transition-colors flex items-center justify-center gap-3 shadow-md shadow-indigo-200"
          >
            <span>Talk to your AI Tutor</span>
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
          <button
            onClick={() => { setStep("config"); setResults(null); setAssessment(null); }}
            className="w-full py-3 rounded-2xl text-slate-500 text-sm font-medium border border-slate-200 hover:bg-slate-50 transition-colors"
          >
            Take another assessment
          </button>
        </div>
      </div>
    );
  }

  return null;
}
