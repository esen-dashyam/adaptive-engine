"use client";

import { useState, useRef, useEffect } from "react";

const API = "/api/v1";

const GRADES = Array.from({ length: 8 }, (_, i) => ({
  id: `K${i + 1}`, label: `Grade ${i + 1}`, ages: `${i + 6}-${i + 7}`,
}));

const SUBJECTS = [
  { id: "math",    label: "Mathematics",           emoji: "🔢" },
  { id: "english", label: "English Language Arts",  emoji: "📖" },
];

const STATES = [
  { abbrev: "Multi-State", name: "Common Core (Multi-State)" },
  { abbrev: "TX", name: "Texas (TEKS)" },
  { abbrev: "CA", name: "California (CA CCSS)" },
  { abbrev: "FL", name: "Florida (B.E.S.T.)" },
  { abbrev: "NY", name: "New York (NGLS)" },
  { abbrev: "GA", name: "Georgia (GSE)" },
  { abbrev: "NC", name: "North Carolina" },
  { abbrev: "OH", name: "Ohio Learning Standards" },
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
};


export default function AssessmentPage() {
  const [step, setStep]         = useState<"config" | "taking" | "results">("config");
  const [studentId, setStudentId] = useState("student_001");
  const [grade, setGrade]       = useState("K3");
  const [subject, setSubject]   = useState("math");
  const [state, setState]       = useState("Multi-State");
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [answers, setAnswers]   = useState<Record<string, string>>({});
  const [results, setResults]             = useState<EvalResult | null>(null);
  const [activeGapTab, setActiveGapTab]   = useState(0);
  const [geminiRequired, setGeminiRequired] = useState(false);
  const [chatMessages, setChatMessages] = useState<{role: string, content: string}[]>([]);
  const [chatInput, setChatInput]       = useState("");
  const [chatLoading, setChatLoading]   = useState(false);
  const [chatOpen, setChatOpen]         = useState(false);
  const [showDetails, setShowDetails]   = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  async function startAssessment() {
    setLoading(true); setError(null); setGeminiRequired(false);
    try {
      const res = await fetch(`${API}/assessment/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grade, subject, student_id: studentId, state, num_questions: 15 }),
      });
      if (res.status === 503) {
        let body: any = {};
        try { body = await res.json(); } catch {}
        if (body?.detail?.gemini_required) { setGeminiRequired(true); return; }
      }
      if (!res.ok) {
        let detail = "Generation failed";
        try { const body = await res.json(); detail = body?.detail || detail; } catch {}
        throw new Error(detail);
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
      const answersPayload = assessment.questions.map((q) => ({
        question_id:          q.id,
        question:             q.question,
        options:              q.options,
        answer:               q.answer,
        dok_level:            q.dok_level,
        beta:                 q.beta ?? 0,
        node_ref:             q.node_ref,
        category:             q.category,
        standard_code:        q.standard_code,
        standard_description: q.standard_description,
        student_answer:       answers[q.id] || "",
        is_correct:           answers[q.id] === q.answer,
      }));

      const res = await fetch(`${API}/assessment/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          assessment_id: assessment.assessment_id,
          student_id:    studentId,
          grade, subject, state,
          answers:       answersPayload,
        }),
      });
      if (!res.ok) {
        let detail = "Evaluation failed";
        try { const body = await res.json(); detail = body?.detail || detail; } catch {}
        throw new Error(detail);
      }
      const data: EvalResult = await res.json();
      setResults(data);
      setChatMessages([]);
      setStep("results");
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  // Auto-launch AI analysis when results arrive
  useEffect(() => {
    if (step === "results" && results) {
      setChatOpen(true);
      setShowDetails(false);
      launchInitialAnalysis();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, results]);

  async function sendChatMessage(messageOverride?: string, isSystem?: boolean) {
    const msg = (messageOverride ?? chatInput).trim();
    if (!msg || !results) return;
    if (!isSystem) {
      setChatMessages(prev => [...prev, { role: "user", content: msg }]);
    }
    setChatInput("");
    setChatLoading(true);
    try {
      const history = isSystem ? [] : chatMessages;
      const res = await fetch(`${API}/chat/tutor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id: studentId,
          grade, subject,
          message: msg,
          history,
          context: results,
        }),
      });
      if (!res.ok) {
        let detail = "Chat failed";
        try { const body = await res.json(); detail = body?.detail || detail; } catch {}
        throw new Error(detail);
      }
      const data = await res.json();
      setChatMessages(prev => [...prev, { role: "assistant", content: data.content }]);
    } catch (e: any) {
      setChatMessages(prev => [...prev, {
        role: "assistant",
        content: `I'm having trouble connecting right now. ${e.message}`,
      }]);
    } finally {
      setChatLoading(false);
    }
  }

  // Called once when results arrive — sends the initial analysis silently (no user bubble)
  function launchInitialAnalysis() {
    if (chatMessages.length === 0 && !chatLoading) {
      sendChatMessage(
        "Analyse my assessment results. Cover: (1) my overall score and what it means for my grade level, " +
        "(2) my top knowledge gaps — what they are and why each one matters, " +
        "(3) any misconceptions I showed, " +
        "(4) my 3 most important next learning steps with a concrete first action for each. " +
        "Be specific, use my actual standard codes, and keep the tone encouraging.",
        true // isSystem — don't add a user bubble
      );
    }
  }

  const answered = Object.keys(answers).length;
  const total    = assessment?.questions.length || 0;

  // ── Config step ──────────────────────────────────────────────────────────
  if (step === "config") {
    return (
      <div className="max-w-2xl mx-auto space-y-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Start Assessment</h1>
          <p className="text-gray-500 mt-1">
            ~15 questions · ~25 minutes · BKT-adaptive · Gemini-generated
          </p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm">{error}</div>
        )}

        {geminiRequired && (
          <div className="bg-white rounded-2xl border border-orange-200 p-6 space-y-4">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 bg-orange-100 rounded-xl flex items-center justify-center flex-shrink-0 text-xl">🔑</div>
              <div>
                <h2 className="font-bold text-gray-900 text-lg">Gemini API Key Required</h2>
                <p className="text-gray-500 text-sm mt-1">
                  The assessment engine uses Google Gemini to generate real curriculum questions
                  aligned to your Knowledge Graph standards. Without it, the engine has nothing to generate from.
                </p>
              </div>
            </div>
            <ol className="space-y-3 text-sm text-gray-700">
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-blue-600 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">1</span>
                <span>Get a free key at{" "}
                  <a href="https://aistudio.google.com/app/apikey" target="_blank"
                     className="text-blue-600 underline font-medium">aistudio.google.com/app/apikey</a>
                </span>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-blue-600 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">2</span>
                <span>Open <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">.env</code> in the project root</span>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-blue-600 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">3</span>
                <span>Add <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">GEMINI_API_KEY=your_key_here</code></span>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-blue-600 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">4</span>
                <span>Restart the backend and try again</span>
              </li>
            </ol>
            <div className="bg-gray-50 rounded-xl p-3 text-xs text-gray-500 font-mono">
              GEMINI_API_KEY=AIza...
            </div>
          </div>
        )}

        <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-6">
          {/* Student ID */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Student ID</label>
            <input
              value={studentId}
              onChange={e => setStudentId(e.target.value)}
              placeholder="student_001"
              className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Grade */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Grade Level</label>
            <div className="grid grid-cols-4 gap-2">
              {GRADES.map(g => (
                <button
                  key={g.id}
                  onClick={() => setGrade(g.id)}
                  className={`py-3 rounded-xl text-sm font-medium transition-all border ${
                    grade === g.id
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-gray-700 border-gray-200 hover:border-blue-300"
                  }`}
                >
                  <div className="font-semibold">{g.label}</div>
                  <div className="text-xs opacity-70">{g.ages}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Subject */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Subject</label>
            <div className="grid grid-cols-2 gap-3">
              {SUBJECTS.map(s => (
                <button
                  key={s.id}
                  onClick={() => setSubject(s.id)}
                  className={`py-4 rounded-xl text-sm font-medium transition-all border ${
                    subject === s.id
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-gray-700 border-gray-200 hover:border-blue-300"
                  }`}
                >
                  <div className="text-2xl mb-1">{s.emoji}</div>
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {/* State */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Standards Framework</label>
            <select
              value={state}
              onChange={e => setState(e.target.value)}
              className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {STATES.map(s => (
                <option key={s.abbrev} value={s.abbrev}>{s.name}</option>
              ))}
            </select>
          </div>

          <button
            onClick={startAssessment}
            disabled={loading}
            className="w-full bg-blue-600 text-white py-3.5 rounded-xl font-semibold text-base hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? "Generating adaptive assessment…" : "Generate Assessment"}
          </button>
        </div>
      </div>
    );
  }

  // ── Taking step ──────────────────────────────────────────────────────────
  if (step === "taking" && assessment) {
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        {/* Header */}
        <div className="bg-white rounded-2xl border border-gray-200 p-5 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-gray-900 text-lg">
              {assessment.framework} · {assessment.grade} · {assessment.subject.charAt(0).toUpperCase() + assessment.subject.slice(1)}
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              {assessment.prerequisite_count} prerequisite · {assessment.target_count} grade-level · ~{assessment.estimated_minutes} min
            </p>
          </div>
          <div className="text-right">
            <div className="text-2xl font-bold text-blue-600">{answered}/{total}</div>
            <div className="text-xs text-gray-400">answered</div>
          </div>
        </div>

        {/* Progress bar */}
        <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
          <div
            className="h-2 bg-blue-500 rounded-full transition-all"
            style={{ width: `${(answered / total) * 100}%` }}
          />
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm">{error}</div>
        )}

        {/* Questions */}
        <div className="space-y-6">
          {assessment.questions.map((q, idx) => (
            <div key={q.id} className="bg-white rounded-2xl border border-gray-200 p-6">
              <div className="flex items-center gap-2 mb-4">
                <span className="w-7 h-7 bg-blue-100 text-blue-700 rounded-full flex items-center justify-center text-sm font-bold">
                  {idx + 1}
                </span>
                <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                  q.category === "prerequisite"
                    ? "bg-amber-100 text-amber-700"
                    : "bg-green-100 text-green-700"
                }`}>
                  {q.category === "prerequisite" ? "Prerequisite" : "Grade-Level"} · DOK {q.dok_level}
                </span>
                <span className="text-xs text-gray-400 ml-auto">{q.standard_code}</span>
              </div>

              <p className="text-gray-900 font-medium text-base leading-relaxed mb-5">{q.question}</p>

              <div className="grid grid-cols-1 gap-2">
                {q.options.map((opt) => {
                  const letter = opt.charAt(0);
                  const selected = answers[q.id] === letter;
                  return (
                    <button
                      key={opt}
                      onClick={() => setAnswers(prev => ({ ...prev, [q.id]: letter }))}
                      className={`flex items-center gap-3 p-4 rounded-xl text-left transition-all border text-sm ${
                        selected
                          ? "bg-blue-50 border-blue-500 text-blue-900"
                          : "border-gray-200 hover:border-blue-300 hover:bg-blue-50 text-gray-700"
                      }`}
                    >
                      <span className={`w-7 h-7 rounded-full flex items-center justify-center font-bold text-xs flex-shrink-0 ${
                        selected ? "bg-blue-500 text-white" : "bg-gray-100 text-gray-600"
                      }`}>{letter}</span>
                      <span>{opt.substring(3)}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        <button
          onClick={submitAssessment}
          disabled={loading || answered < total}
          className="w-full bg-blue-600 text-white py-4 rounded-xl font-semibold text-base hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loading
            ? "Evaluating & generating remediation exercises…"
            : answered < total
            ? `Answer all questions to submit (${total - answered} remaining)`
            : "Submit Assessment"}
        </button>
      </div>
    );
  }

  // ── Results step ─────────────────────────────────────────────────────────
  if (step === "results" && results) {
    const pct = (v: number | null | undefined) =>
      v != null ? `${Math.round(v * 100)}%` : "—";

    const statusColor: Record<string, string> = {
      above:       "bg-green-500",
      at:          "bg-blue-500",
      approaching: "bg-amber-500",
      below:       "bg-red-500",
    };
    const statusLabel: Record<string, string> = {
      above: "Above Grade Level", at: "At Grade Level",
      approaching: "Approaching Grade Level", below: "Below Grade Level",
    };
    const thetaLabel = (t: number) => {
      if (t >= 1.5)  return "Advanced";
      if (t >= 0.5)  return "Above Average";
      if (t >= -0.5) return "On Grade Level";
      if (t >= -1.5) return "Slightly Below";
      return "Needs Support";
    };

    const PROMPTS = [
      "What are my biggest knowledge gaps and how do I fix them?",
      "Walk me through a practice problem for my weakest area.",
      "What should I study this week?",
      "Explain why I got those questions wrong.",
      "How close am I to the next grade level?",
    ];

    return (
      <div className="max-w-3xl mx-auto space-y-4">

        {/* ── Header row ── */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">Assessment Results</h1>
          <button
            onClick={() => { setStep("config"); setResults(null); setAssessment(null); setChatMessages([]); setChatOpen(false); }}
            className="text-sm text-blue-600 hover:underline font-medium"
          >
            New Assessment
          </button>
        </div>

        {/* ── Compact stat bar ── */}
        <div className="bg-white rounded-2xl border border-gray-200 p-4 flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className={`w-2.5 h-2.5 rounded-full ${statusColor[results.grade_status] ?? "bg-gray-400"}`} />
            <span className="font-semibold text-gray-900 text-sm">{statusLabel[results.grade_status] ?? results.grade_status}</span>
          </div>
          <div className="h-4 w-px bg-gray-200" />
          <span className="text-sm text-gray-700"><span className="font-bold text-gray-900">{pct(results.score)}</span> overall ({results.correct}/{results.total})</span>
          <div className="h-4 w-px bg-gray-200" />
          <span className="text-sm text-gray-500">Prereqs <span className="font-semibold text-amber-600">{pct(results.prerequisite_score)}</span></span>
          <span className="text-sm text-gray-500">Grade-level <span className="font-semibold text-blue-600">{pct(results.target_score)}</span></span>
          {results.theta != null && (
            <>
              <div className="h-4 w-px bg-gray-200" />
              <span className="text-sm text-gray-500">
                θ <span className="font-semibold text-purple-600">{results.theta >= 0 ? "+" : ""}{results.theta.toFixed(2)}</span>
                <span className="text-gray-400 ml-1">({thetaLabel(results.theta)})</span>
              </span>
            </>
          )}
          {results.gap_count > 0 && (
            <>
              <div className="h-4 w-px bg-gray-200" />
              <span className="text-sm text-red-600 font-medium">
                {results.gap_count} gap{results.gap_count !== 1 ? "s" : ""}
                {(results.hard_blocked_count ?? 0) > 0 && ` · ${results.hard_blocked_count} blocked`}
              </span>
            </>
          )}
        </div>

        {/* ── AI Tutor — primary interface ── */}
        <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden shadow-sm">
          {/* Chat header */}
          <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-xl flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
                AI
              </div>
              <div>
                <div className="font-semibold text-gray-900 text-sm">AI Tutor</div>
                <div className="text-xs text-gray-400">Gemini 2.5 Pro · grounded in your results</div>
              </div>
            </div>
            {chatLoading && (
              <div className="flex gap-1 items-center text-xs text-gray-400">
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                <span className="ml-1">Thinking…</span>
              </div>
            )}
          </div>

          {/* Messages */}
          <div className="h-[460px] overflow-y-auto p-5 space-y-5 bg-gray-50">
            {/* Initial typing indicator (before first message arrives) */}
            {chatMessages.length === 0 && chatLoading && (
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0">AI</div>
                <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5 items-center shadow-sm">
                  <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            )}

            {chatMessages.map((msg, i) => (
              <div key={i} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                {msg.role === "assistant" && (
                  <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0 mt-0.5">AI</div>
                )}
                <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap shadow-sm ${
                  msg.role === "user"
                    ? "bg-blue-600 text-white rounded-tr-sm"
                    : "bg-white border border-gray-200 text-gray-800 rounded-tl-sm"
                }`}>
                  {msg.content}
                </div>
                {msg.role === "user" && (
                  <div className="w-8 h-8 bg-gray-200 rounded-full flex items-center justify-center text-gray-600 text-xs flex-shrink-0 mt-0.5 font-semibold">
                    {studentId.charAt(0).toUpperCase()}
                  </div>
                )}
              </div>
            ))}

            {/* Typing indicator mid-conversation */}
            {chatLoading && chatMessages.length > 0 && (
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0">AI</div>
                <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5 items-center shadow-sm">
                  <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Suggested prompts — shown after first AI message lands */}
          {chatMessages.length >= 1 && !chatLoading && (
            <div className="px-4 py-2.5 flex gap-2 overflow-x-auto border-t border-gray-100 bg-white">
              {PROMPTS.map(s => (
                <button
                  key={s}
                  onClick={() => sendChatMessage(s)}
                  className="flex-shrink-0 text-xs px-3 py-1.5 rounded-full border border-gray-200 bg-white text-gray-600 hover:bg-blue-50 hover:text-blue-700 hover:border-blue-200 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div className="p-3 border-t border-gray-100 bg-white flex gap-2">
            <input
              value={chatInput}
              onChange={e => setChatInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(); } }}
              placeholder="Ask about your gaps, request a practice problem, or ask anything…"
              disabled={chatLoading}
              className="flex-1 border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 bg-gray-50"
            />
            <button
              onClick={() => sendChatMessage()}
              disabled={chatLoading || !chatInput.trim()}
              className="bg-blue-600 text-white px-5 py-2.5 rounded-xl font-semibold text-sm hover:bg-blue-700 disabled:opacity-40 transition-colors flex-shrink-0"
            >
              Send
            </button>
          </div>
        </div>

        {/* ── Show Details toggle ── */}
        <button
          onClick={() => setShowDetails(v => !v)}
          className="w-full text-sm text-gray-500 hover:text-gray-700 py-2 flex items-center justify-center gap-1.5 transition-colors"
        >
          <span>{showDetails ? "▲ Hide" : "▼ Show"} full report — gaps, exercises, mastery updates</span>
        </button>

        {showDetails && (
          <div className="space-y-4">

            {/* Misconceptions */}
            {results.misconceptions && results.misconceptions.length > 0 && (
              <div className="bg-red-50 border border-red-200 rounded-2xl p-5 space-y-3">
                <h3 className="font-semibold text-red-800 text-sm">Detected Misconceptions</h3>
                {results.misconceptions.map((m: any, i: number) => (
                  <div key={i} className="flex items-start gap-3 text-sm">
                    <span className="w-5 h-5 bg-red-500 text-white rounded-full flex items-center justify-center text-xs flex-shrink-0 mt-0.5 font-bold">!</span>
                    <div>
                      <span className="font-medium text-red-900">{m.standard_code}</span>
                      <span className="text-red-700 ml-2">{m.misconception}</span>
                      {m.affected_standards?.length > 0 && (
                        <div className="text-red-400 text-xs mt-0.5">Affects: {m.affected_standards.join(", ")}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Remediation exercises */}
            {results.gap_exercises && results.gap_exercises.length > 0 && (
              <div className="bg-white rounded-2xl border border-gray-200 p-5 space-y-4">
                <div>
                  <h3 className="font-semibold text-gray-900 text-sm">Remediation Exercises</h3>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Targeted practice for {results.gap_exercises.length} knowledge gap{results.gap_exercises.length !== 1 ? "s" : ""}
                  </p>
                </div>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {results.gap_exercises.map((plan: any, i: number) => (
                    <button
                      key={i}
                      onClick={() => setActiveGapTab(i)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors flex-shrink-0 ${
                        activeGapTab === i ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                      }`}
                    >
                      {plan.standard_code || `Gap ${i + 1}`}
                      {plan.hard_blocked && <span className="ml-1 text-red-300">⚠</span>}
                    </button>
                  ))}
                </div>
                {results.gap_exercises[activeGapTab] && (() => {
                  const plan = results.gap_exercises[activeGapTab];
                  const exs: any[] = plan.exercises || [];
                  return (
                    <div className="space-y-3">
                      {plan.concept_explanation && (
                        <div className="bg-blue-50 border border-blue-200 rounded-xl p-3 text-sm text-blue-800">
                          <span className="font-medium">Concept: </span>{plan.concept_explanation}
                        </div>
                      )}
                      {plan.misconception && (
                        <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-xs text-red-700">
                          <span className="font-medium">Misconception: </span>{plan.misconception}
                        </div>
                      )}
                      {exs.map((ex: any, j: number) => (
                        <div key={j} className="border border-gray-200 rounded-xl p-4 space-y-2">
                          <div className="flex items-center gap-2">
                            <span className="w-6 h-6 bg-gray-900 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">{j + 1}</span>
                            <span className="text-xs text-gray-400 uppercase tracking-wide">{ex.type} · DOK {ex.dok_level}</span>
                          </div>
                          <p className="font-medium text-gray-900 text-sm leading-relaxed">{ex.question}</p>
                          {ex.hint && (
                            <div className="bg-amber-50 rounded-lg p-2 text-xs text-amber-800">
                              <span className="font-medium">Hint: </span>{ex.hint}
                            </div>
                          )}
                          {ex.answer && (
                            <div className="bg-green-50 border border-green-200 rounded-lg p-2 text-xs text-green-800">
                              <span className="font-medium">Answer: </span>{ex.answer}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>
            )}

            {/* Recommendations */}
            {results.recommendations && results.recommendations.length > 0 && (
              <div className="bg-white rounded-2xl border border-gray-200 p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold text-gray-900 text-sm">Next Learning Steps</h3>
                  <span className="text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded-full">ZPD Frontier</span>
                </div>
                <div className="space-y-2">
                  {results.recommendations.map((rec: any, i: number) => (
                    <div key={i} className={`flex items-start gap-3 p-3 rounded-xl border text-sm ${
                      rec.difficulty === "accessible" ? "bg-green-50 border-green-200" :
                      rec.difficulty === "stretch"    ? "bg-purple-50 border-purple-200" :
                      "bg-blue-50 border-blue-200"
                    }`}>
                      <div className="w-7 h-7 rounded-full bg-white border border-gray-200 flex items-center justify-center text-xs font-bold text-gray-700 flex-shrink-0">
                        {rec.rank ?? i + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold text-gray-900 text-xs">{rec.standard_code}</span>
                          <span className="text-xs px-1.5 py-0.5 rounded-full bg-white border border-gray-200 text-gray-400">
                            {rec.difficulty ?? "challenging"}
                          </span>
                          {rec.estimated_minutes && <span className="text-xs text-gray-400">~{rec.estimated_minutes} min</span>}
                          {rec.success_prob != null && <span className="text-xs text-gray-400">{Math.round(rec.success_prob * 100)}% ready</span>}
                        </div>
                        <p className="text-gray-600 text-xs mt-0.5 leading-relaxed">{rec.description}</p>
                        {rec.why_now && <p className="text-gray-500 text-xs mt-0.5 italic">{rec.why_now}</p>}
                        {rec.how_to_start && (
                          <p className="text-gray-700 text-xs mt-0.5">
                            <span className="font-medium">Start: </span>{rec.how_to_start}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* BKT mastery updates */}
            {results.bkt_updates && results.bkt_updates.length > 0 && (
              <div className="bg-white rounded-2xl border border-gray-200 p-5">
                <h3 className="font-semibold text-gray-900 text-sm mb-1">Mastery Updated</h3>
                <p className="text-xs text-gray-500 mb-3">{results.bkt_updates.length} skill states saved to graph</p>
                <div className="grid grid-cols-2 gap-2 max-h-40 overflow-y-auto">
                  {results.bkt_updates.map((u: any, i: number) => {
                    const label = (u.node ?? u.node_identifier ?? "").split(".").pop() ?? "—";
                    const mastery = u.mastery ?? u.p_mastery ?? 0;
                    return (
                      <div key={i} className="flex items-center justify-between p-2 bg-gray-50 rounded-lg text-xs">
                        <span className="text-gray-600 truncate max-w-[110px]" title={u.node ?? u.node_identifier}>{label}</span>
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          <div className="w-14 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className={`h-1.5 rounded-full ${mastery >= 0.7 ? "bg-green-500" : mastery >= 0.4 ? "bg-amber-400" : "bg-red-400"}`}
                              style={{ width: `${Math.round(mastery * 100)}%` }}
                            />
                          </div>
                          <span className="font-medium text-gray-700">{Math.round(mastery * 100)}%</span>
                        </div>
                      </div>
                    );
                  })}
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
