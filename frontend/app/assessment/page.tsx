"use client";

import { useState, useRef, useEffect } from "react";

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

const CHAT_PROMPTS = [
  "What should I focus on this week?",
  "Walk me through one of my wrong answers.",
  "Give me a practice problem for my weakest area.",
  "How close am I to the next level?",
  "Explain why this concept matters.",
];

export default function AssessmentPage() {
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
  const [activeTab, setActiveTab] = useState(0);
  const [geminiError, setGeminiError] = useState(false);
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  useEffect(() => {
    if (step === "results" && results && chatMessages.length === 0) {
      sendChatMessage(
        "Analyse my assessment results. Tell me: (1) what my score means, " +
        "(2) the 2-3 most important things I need to work on and why, " +
        "(3) one concrete first step I can take right now. " +
        "Be encouraging, specific, and use simple language.",
        true
      );
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, results]);

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
        let d = "Evaluation failed";
        try { const b = await res.json(); d = b?.detail || d; } catch {}
        throw new Error(d);
      }
      const data: EvalResult = await res.json();
      setResults(data); setChatMessages([]); setStep("results");
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function sendChatMessage(override?: string, silent?: boolean) {
    const msg = (override ?? chatInput).trim();
    if (!msg || !results) return;
    if (!silent) setChatMessages(p => [...p, { role: "user", content: msg }]);
    setChatInput(""); setChatLoading(true);
    try {
      const res = await fetch(`${API}/chat/tutor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id: studentId, grade, subject,
          message: msg,
          history: silent ? [] : chatMessages,
          context: results,
        }),
      });
      if (!res.ok) throw new Error("Chat unavailable");
      const data = await res.json();
      setChatMessages(p => [...p, { role: "assistant", content: data.content }]);
    } catch (e: any) {
      setChatMessages(p => [...p, { role: "assistant", content: "I'm having trouble connecting. Please try again." }]);
    } finally { setChatLoading(false); }
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

    return (
      <div className="max-w-2xl mx-auto space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-slate-900">
            {name}&apos;s Results
          </h1>
          <button onClick={() => { setStep("config"); setResults(null); setAssessment(null); setChatMessages([]); }}
            className="text-sm text-indigo-600 hover:underline font-medium">
            New assessment
          </button>
        </div>

        {/* Score card */}
        <div className="bg-white rounded-2xl border border-slate-200 p-5 flex items-center gap-5">
          <div className="relative w-20 h-20 flex-shrink-0">
            <svg className="w-20 h-20 -rotate-90" viewBox="0 0 80 80">
              <circle cx="40" cy="40" r="32" stroke="#e2e8f0" strokeWidth="8" fill="none" />
              <circle cx="40" cy="40" r="32" stroke="#6366f1" strokeWidth="8" fill="none"
                strokeDasharray={`${2 * Math.PI * 32}`}
                strokeDashoffset={`${2 * Math.PI * 32 * (1 - scorePct / 100)}`}
                strokeLinecap="round" />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-lg font-bold text-slate-900">{scorePct}%</span>
            </div>
          </div>
          <div className="flex-1">
            <div className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full border mb-2 ${STATUS_COLOR[results.grade_status] ?? "text-slate-600 bg-slate-50 border-slate-200"}`}>
              <span className="w-1.5 h-1.5 rounded-full bg-current opacity-60" />
              {STATUS_LABEL[results.grade_status] ?? results.grade_status}
            </div>
            <p className="text-slate-700 text-sm leading-relaxed">
              {results.correct} out of {results.total} correct.
              {results.session_narrative ? ` ${results.session_narrative}` : ""}
            </p>
            {results.focus_concept && (
              <p className="text-xs text-indigo-600 mt-1.5 font-medium">Focus: {results.focus_concept}</p>
            )}
          </div>
        </div>

        {/* AI Chat */}
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-3">
            <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-xl flex items-center justify-center text-white text-xs font-bold">AI</div>
            <div>
              <p className="text-sm font-semibold text-slate-900">Your AI Tutor</p>
              <p className="text-xs text-slate-400">Gemini · personalised to your results</p>
            </div>
            {chatLoading && (
              <div className="ml-auto flex gap-1">
                {[0, 150, 300].map(d => (
                  <span key={d} className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
                ))}
              </div>
            )}
          </div>

          <div className="h-[420px] overflow-y-auto p-4 space-y-4 bg-slate-50">
            {chatMessages.length === 0 && chatLoading && (
              <div className="flex items-start gap-2.5">
                <div className="w-7 h-7 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0">AI</div>
                <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5 items-center shadow-sm">
                  {[0, 150, 300].map(d => <span key={d} className="w-2 h-2 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />)}
                </div>
              </div>
            )}

            {chatMessages.map((m, i) => (
              <div key={i} className={`flex gap-2.5 ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                {m.role === "assistant" && (
                  <div className="w-7 h-7 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0 mt-0.5">AI</div>
                )}
                <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
                  m.role === "user" ? "bg-indigo-600 text-white rounded-tr-sm" : "bg-white border border-slate-200 text-slate-800 rounded-tl-sm shadow-sm"
                }`}>{m.content}</div>
                {m.role === "user" && (
                  <div className="w-7 h-7 bg-slate-200 rounded-full flex items-center justify-center text-slate-600 text-xs font-bold flex-shrink-0 mt-0.5">
                    {name.charAt(0).toUpperCase()}
                  </div>
                )}
              </div>
            ))}

            {chatLoading && chatMessages.length > 0 && (
              <div className="flex items-start gap-2.5">
                <div className="w-7 h-7 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0">AI</div>
                <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5 shadow-sm">
                  {[0, 150, 300].map(d => <span key={d} className="w-2 h-2 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />)}
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {chatMessages.length >= 1 && !chatLoading && (
            <div className="px-3 py-2 border-t border-slate-100 bg-white flex gap-2 overflow-x-auto">
              {CHAT_PROMPTS.map(p => (
                <button key={p} onClick={() => sendChatMessage(p)}
                  className="flex-shrink-0 text-xs px-3 py-1.5 rounded-full border border-slate-200 text-slate-500 hover:bg-indigo-50 hover:text-indigo-700 hover:border-indigo-200 transition-colors">
                  {p}
                </button>
              ))}
            </div>
          )}

          <div className="p-3 border-t border-slate-100 bg-white flex gap-2">
            <input value={chatInput} onChange={e => setChatInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(); } }}
              placeholder="Ask anything about your results…"
              disabled={chatLoading}
              className="flex-1 border border-slate-200 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-slate-50 disabled:opacity-50" />
            <button onClick={() => sendChatMessage()} disabled={chatLoading || !chatInput.trim()}
              className="bg-indigo-600 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-indigo-700 disabled:opacity-40 transition-colors">
              Send
            </button>
          </div>
        </div>

        {/* Details toggle */}
        <button onClick={() => setShowDetails(v => !v)}
          className="w-full text-xs text-slate-400 hover:text-slate-600 py-2 flex items-center justify-center gap-1 transition-colors">
          {showDetails ? "▲ Hide" : "▼ Show"} full report (exercises, gaps, recommendations)
        </button>

        {showDetails && (
          <div className="space-y-4">
            {/* Practice exercises */}
            {results.gap_exercises?.length > 0 && (
              <div className="bg-white rounded-2xl border border-slate-200 p-5">
                <h3 className="font-semibold text-slate-900 text-sm mb-3">Practice Exercises</h3>
                <div className="flex gap-2 overflow-x-auto pb-2 mb-3">
                  {results.gap_exercises.map((p: any, i: number) => (
                    <button key={i} onClick={() => setActiveTab(i)}
                      className={`flex-shrink-0 text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                        activeTab === i ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                      }`}>
                      {p.standard_code || `Exercise ${i + 1}`}
                    </button>
                  ))}
                </div>
                {(() => {
                  const plan = results.gap_exercises[activeTab];
                  if (!plan) return null;
                  return (
                    <div className="space-y-3">
                      {plan.concept_explanation && (
                        <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-3 text-sm text-indigo-800">
                          <span className="font-medium">Concept: </span>{plan.concept_explanation}
                        </div>
                      )}
                      {(plan.exercises || []).map((ex: any, j: number) => (
                        <div key={j} className="border border-slate-200 rounded-xl p-4 space-y-2">
                          <div className="flex items-center gap-2">
                            <span className="w-6 h-6 bg-slate-900 text-white rounded-full flex items-center justify-center text-xs font-bold">{j + 1}</span>
                          </div>
                          <p className="text-sm text-slate-900 font-medium">{ex.question}</p>
                          {ex.hint && <p className="text-xs text-amber-700 bg-amber-50 rounded-lg p-2"><span className="font-medium">Hint: </span>{ex.hint}</p>}
                          {ex.answer && <p className="text-xs text-emerald-700 bg-emerald-50 rounded-lg p-2"><span className="font-medium">Answer: </span>{ex.answer}</p>}
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>
            )}

            {/* Recommendations */}
            {results.recommendations?.length > 0 && (
              <div className="bg-white rounded-2xl border border-slate-200 p-5">
                <h3 className="font-semibold text-slate-900 text-sm mb-3">What to Learn Next</h3>
                <div className="space-y-2">
                  {results.recommendations.map((r: any, i: number) => (
                    <div key={i} className="flex items-start gap-3 p-3 rounded-xl bg-slate-50 border border-slate-200 text-sm">
                      <span className="w-6 h-6 rounded-full bg-white border border-slate-300 flex items-center justify-center text-xs font-bold text-slate-600 flex-shrink-0">{r.rank ?? i + 1}</span>
                      <div>
                        <p className="font-medium text-slate-900 text-xs">{r.standard_code}</p>
                        <p className="text-slate-500 text-xs mt-0.5 leading-relaxed">{r.description}</p>
                        {r.why_now && <p className="text-indigo-600 text-xs mt-1 italic">{r.why_now}</p>}
                        {r.decision_reasoning && <p className="text-slate-400 text-xs mt-1">{r.decision_reasoning}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return null;
}
