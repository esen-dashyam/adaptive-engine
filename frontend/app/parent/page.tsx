"use client";

import { useState, useRef, useEffect } from "react";

const API = "/api/v1";

const SUBJECTS = [
  { id: "math",    label: "Mathematics" },
  { id: "english", label: "English Language Arts" },
];

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string; border: string }> = {
  doing_great:   { label: "Doing great",   color: "text-emerald-700", bg: "bg-emerald-50", border: "border-emerald-200" },
  on_track:      { label: "On track",      color: "text-indigo-700",  bg: "bg-indigo-50",  border: "border-indigo-200"  },
  needs_support: { label: "Needs support", color: "text-amber-700",   bg: "bg-amber-50",   border: "border-amber-200"   },
  not_started:   { label: "Not started",   color: "text-slate-500",   bg: "bg-slate-50",   border: "border-slate-200"   },
};

const SEVERITY_DOT: Record<string, string> = {
  minor: "bg-yellow-400", moderate: "bg-orange-400", significant: "bg-red-500",
};

const PARENT_PROMPTS = [
  "What can I do at home to help?",
  "How serious are these gaps?",
  "What should we focus on this week?",
  "Is my child falling behind?",
  "How do I make learning fun for them?",
];

type LastAssessmentSnapshot = {
  assessment_id: string;
  score: number;
  total: number;
  correct: number;
  grade: string;
  subject: string;
  failed_standard_codes: string[];
  failed_standards: { code: string; question: string }[];
  timestamp: string;
};

type ParentSummary = {
  has_data: boolean;
  overall_status: string;
  headline: string;
  performance_summary: string;
  strengths: { topic: string; detail: string }[];
  focus_areas: { topic: string; plain_explanation: string; severity: string; home_activity: string }[];
  next_milestone: string;
  encouragement: string;
};

type ChatMsg = { role: string; content: string };

export default function ParentPage() {
  const [studentId, setStudentId]   = useState("");
  const [childName, setChildName]   = useState("");
  const [subject, setSubject]       = useState("math");
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState<string | null>(null);
  const [summary, setSummary]       = useState<ParentSummary | null>(null);
  const [masteryCtx, setMasteryCtx] = useState<any>(null);
  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
  const [chatInput, setChatInput]   = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [lastAssessment, setLastAssessment] = useState<LastAssessmentSnapshot | null>(null);
  const [existingFeedback, setExistingFeedback] = useState<{accurate: string; notes: string} | null>(null);
  const [feedbackChoice, setFeedbackChoice] = useState<"yes" | "somewhat" | "no" | null>(null);
  const [feedbackNotes, setFeedbackNotes] = useState("");
  const [feedbackSaved, setFeedbackSaved] = useState(false);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  async function loadReport() {
    if (!studentId.trim()) { setError("Please enter your child's student ID."); return; }
    setLoading(true); setError(null); setSummary(null); setChatMessages([]);
    setLastAssessment(null); setExistingFeedback(null); setFeedbackChoice(null);
    setFeedbackNotes(""); setFeedbackSaved(false);
    try {
      // 1. Load mastery context + last assessment in parallel
      const [ctxRes, lastRes] = await Promise.all([
        fetch(`${API}/chat/context/${encodeURIComponent(studentId)}?subject=${subject}`),
        fetch(`${API}/assessment/student/${encodeURIComponent(studentId)}/last_result`),
      ]);
      if (!ctxRes.ok) throw new Error("Could not load student data");
      const ctx = await ctxRes.json();
      setMasteryCtx(ctx);

      if (lastRes.ok) {
        const lastData = await lastRes.json();
        if (lastData.has_data) {
          setLastAssessment(lastData.snapshot);
          if (lastData.parent_feedback) setExistingFeedback(lastData.parent_feedback);
        }
      }

      // 2. Generate parent-friendly summary
      const sumRes = await fetch(`${API}/chat/parent_summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id: studentId,
          child_name: childName || studentId,
          subject,
          grade: "all",
          mastery_context: ctx,
        }),
      });
      if (!sumRes.ok) throw new Error("Could not generate report");
      const sumData: ParentSummary = await sumRes.json();
      setSummary(sumData);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function submitFeedback(choice: "yes" | "somewhat" | "no") {
    setFeedbackChoice(choice);
    if (choice === "yes") {
      // Save immediately for yes
      await saveFeedback(choice, "");
    }
  }

  async function saveFeedback(choice: string, notes: string) {
    try {
      await fetch(`${API}/assessment/parent_feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id: studentId,
          assessment_id: lastAssessment?.assessment_id ?? "",
          accurate: choice,
          parent_notes: notes,
        }),
      });
      setFeedbackSaved(true);
    } catch {}
  }

  async function sendChat(override?: string) {
    const msg = (override ?? chatInput).trim();
    if (!msg || !masteryCtx) return;
    setChatMessages(p => [...p, { role: "user", content: msg }]);
    setChatInput(""); setChatLoading(true);
    try {
      const res = await fetch(`${API}/chat/parent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id: studentId,
          child_name: childName || studentId,
          subject, grade: "all",
          message: msg,
          history: chatMessages,
          mastery_context: masteryCtx,
        }),
      });
      if (!res.ok) throw new Error("Chat unavailable");
      const data = await res.json();
      setChatMessages(p => [...p, { role: "assistant", content: data.content }]);
    } catch {
      setChatMessages(p => [...p, { role: "assistant", content: "I'm having trouble connecting. Please try again." }]);
    } finally { setChatLoading(false); }
  }

  const name = childName || studentId || "your child";
  const status = summary ? STATUS_CONFIG[summary.overall_status] ?? STATUS_CONFIG.on_track : null;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Parent Dashboard</h1>
        <p className="text-slate-500 text-sm mt-1">See how your child is doing and how you can help.</p>
      </div>

      {/* Lookup form */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5 space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Child&apos;s name</label>
            <input value={childName} onChange={e => setChildName(e.target.value)}
              placeholder="Emma"
              className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Student ID</label>
            <input value={studentId} onChange={e => setStudentId(e.target.value)}
              placeholder="student_001"
              onKeyDown={e => e.key === "Enter" && loadReport()}
              className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500" />
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">Subject</label>
          <div className="grid grid-cols-2 gap-2">
            {SUBJECTS.map(s => (
              <button key={s.id} onClick={() => setSubject(s.id)}
                className={`py-2 rounded-xl text-sm font-medium transition-all border ${
                  subject === s.id ? "bg-emerald-600 text-white border-emerald-600" : "border-slate-200 text-slate-700 hover:border-emerald-300"
                }`}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button onClick={loadReport} disabled={loading}
          className="w-full bg-emerald-600 text-white py-2.5 rounded-xl font-semibold text-sm hover:bg-emerald-700 disabled:opacity-50 transition-colors">
          {loading ? "Loading report…" : "View Report"}
        </button>
      </div>

      {/* Summary report */}
      {summary && (
        <>
          {/* Status headline */}
          <div className={`rounded-2xl border p-5 ${status?.bg} ${status?.border}`}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`text-xs font-bold uppercase tracking-wide px-2.5 py-1 rounded-full border ${status?.bg} ${status?.border} ${status?.color}`}>
                {status?.label}
              </span>
            </div>
            <h2 className={`text-lg font-bold mb-1 ${status?.color}`}>{summary.headline}</h2>
            <p className="text-sm text-slate-600 leading-relaxed">{summary.performance_summary}</p>
          </div>

          {/* Strengths */}
          {summary.strengths?.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200 p-5">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-7 h-7 bg-emerald-100 rounded-lg flex items-center justify-center">
                  <svg className="w-4 h-4 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <h3 className="font-semibold text-slate-900 text-sm">What {name} is doing well</h3>
              </div>
              <div className="space-y-3">
                {summary.strengths.map((s, i) => (
                  <div key={i} className="flex items-start gap-3">
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 mt-1.5 flex-shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-slate-900">{s.topic}</p>
                      <p className="text-sm text-slate-500 mt-0.5 leading-relaxed">{s.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Focus areas */}
          {summary.focus_areas?.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200 p-5">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-7 h-7 bg-amber-100 rounded-lg flex items-center justify-center">
                  <svg className="w-4 h-4 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
                <h3 className="font-semibold text-slate-900 text-sm">Where {name} needs support</h3>
              </div>
              <div className="space-y-4">
                {summary.focus_areas.map((f, i) => (
                  <div key={i} className="border border-slate-200 rounded-xl p-4 space-y-3">
                    <div className="flex items-center gap-2">
                      <div className={`w-2 h-2 rounded-full ${SEVERITY_DOT[f.severity] ?? "bg-slate-400"}`} />
                      <p className="text-sm font-semibold text-slate-900">{f.topic}</p>
                    </div>
                    <p className="text-sm text-slate-600 leading-relaxed">{f.plain_explanation}</p>
                    <div className="bg-indigo-50 rounded-xl p-3">
                      <p className="text-xs font-semibold text-indigo-700 mb-1">What you can do at home</p>
                      <p className="text-sm text-indigo-800 leading-relaxed">{f.home_activity}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Next milestone */}
          {summary.next_milestone && (
            <div className="bg-slate-50 rounded-2xl border border-slate-200 p-4 flex items-start gap-3">
              <div className="w-7 h-7 bg-indigo-100 rounded-lg flex items-center justify-center flex-shrink-0">
                <svg className="w-4 h-4 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Next milestone</p>
                <p className="text-sm text-slate-700 leading-relaxed">{summary.next_milestone}</p>
              </div>
            </div>
          )}

          {/* Encouragement */}
          {summary.encouragement && (
            <div className="text-center py-2">
              <p className="text-sm text-slate-500 italic">{summary.encouragement}</p>
            </div>
          )}

          {/* Assessment Accuracy Confirmation */}
          {lastAssessment && (
            <div className="bg-white rounded-2xl border border-slate-200 p-5 space-y-4">
              <div className="flex items-center gap-2">
                <div className="w-7 h-7 bg-amber-100 rounded-lg flex items-center justify-center">
                  <svg className="w-4 h-4 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                  </svg>
                </div>
                <div>
                  <h3 className="font-semibold text-slate-900 text-sm">Recent Assessment — Does this seem accurate?</h3>
                  <p className="text-xs text-slate-400">{new Date(lastAssessment.timestamp).toLocaleDateString()} · {lastAssessment.correct}/{lastAssessment.total} correct ({Math.round(lastAssessment.score * 100)}%)</p>
                </div>
              </div>

              {lastAssessment.failed_standards.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Topics {name} struggled with</p>
                  {lastAssessment.failed_standards.slice(0, 4).map((f, i) => (
                    <div key={i} className="flex items-start gap-2 bg-red-50 rounded-xl px-3 py-2">
                      <span className="text-xs font-bold text-red-500 flex-shrink-0 mt-0.5">{f.code}</span>
                      <span className="text-xs text-slate-600 leading-relaxed line-clamp-2">{f.question}</span>
                    </div>
                  ))}
                  {lastAssessment.failed_standards.length > 4 && (
                    <p className="text-xs text-slate-400 pl-1">+{lastAssessment.failed_standards.length - 4} more</p>
                  )}
                </div>
              )}

              {existingFeedback || feedbackSaved ? (
                <div className="bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3 text-sm text-emerald-700 font-medium">
                  {existingFeedback?.accurate === "yes" || feedbackChoice === "yes"
                    ? "You confirmed this assessment looks accurate."
                    : existingFeedback?.accurate === "somewhat" || feedbackChoice === "somewhat"
                    ? "Thanks — noted that this is partially accurate."
                    : "Thanks — we've noted this doesn't match what you see at home."}
                </div>
              ) : (
                <>
                  <p className="text-sm text-slate-700">As {name}&apos;s parent, does this match what you see at home?</p>
                  <div className="grid grid-cols-3 gap-2">
                    {([
                      { id: "yes", label: "Yes, accurate", color: "border-emerald-300 hover:bg-emerald-50 hover:text-emerald-700" },
                      { id: "somewhat", label: "Somewhat", color: "border-amber-300 hover:bg-amber-50 hover:text-amber-700" },
                      { id: "no", label: "Not really", color: "border-red-300 hover:bg-red-50 hover:text-red-700" },
                    ] as const).map(opt => (
                      <button key={opt.id}
                        onClick={() => submitFeedback(opt.id)}
                        className={`py-2 rounded-xl text-xs font-semibold border transition-colors text-slate-600 ${opt.color} ${feedbackChoice === opt.id ? "ring-2 ring-offset-1 ring-current" : ""}`}>
                        {opt.label}
                      </button>
                    ))}
                  </div>
                  {(feedbackChoice === "somewhat" || feedbackChoice === "no") && !feedbackSaved && (
                    <div className="space-y-2">
                      <textarea
                        value={feedbackNotes}
                        onChange={e => setFeedbackNotes(e.target.value)}
                        placeholder={`What do you think ${name} actually struggles with or does well?`}
                        rows={3}
                        className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 resize-none"
                      />
                      <button
                        onClick={() => saveFeedback(feedbackChoice, feedbackNotes)}
                        className="w-full bg-emerald-600 text-white py-2 rounded-xl text-sm font-semibold hover:bg-emerald-700 transition-colors">
                        Submit feedback
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Parent chat */}
          <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-3">
              <div className="w-8 h-8 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-xl flex items-center justify-center text-white text-xs font-bold">AI</div>
              <div>
                <p className="text-sm font-semibold text-slate-900">Ask the AI Advisor</p>
                <p className="text-xs text-slate-400">Get guidance about {name}&apos;s learning in plain language</p>
              </div>
              {chatLoading && (
                <div className="ml-auto flex gap-1">
                  {[0, 150, 300].map(d => <span key={d} className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />)}
                </div>
              )}
            </div>

            <div className="h-[320px] overflow-y-auto p-4 space-y-4 bg-slate-50">
              {chatMessages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-center gap-2">
                  <p className="text-sm text-slate-500">Ask anything about {name}&apos;s progress.</p>
                  <p className="text-xs text-slate-400">No jargon — just clear, practical answers.</p>
                </div>
              )}

              {chatMessages.map((m, i) => (
                <div key={i} className={`flex gap-2.5 ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  {m.role === "assistant" && (
                    <div className="w-7 h-7 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0 mt-0.5">AI</div>
                  )}
                  <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
                    m.role === "user" ? "bg-emerald-600 text-white rounded-tr-sm" : "bg-white border border-slate-200 text-slate-800 rounded-tl-sm shadow-sm"
                  }`}>{m.content}</div>
                  {m.role === "user" && (
                    <div className="w-7 h-7 bg-slate-200 rounded-full flex items-center justify-center text-slate-600 text-xs font-bold flex-shrink-0 mt-0.5">
                      {(childName || "P").charAt(0).toUpperCase()}
                    </div>
                  )}
                </div>
              ))}

              {chatLoading && chatMessages.length > 0 && (
                <div className="flex items-start gap-2.5">
                  <div className="w-7 h-7 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-full flex items-center justify-center text-white text-xs flex-shrink-0">AI</div>
                  <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5 shadow-sm">
                    {[0, 150, 300].map(d => <span key={d} className="w-2 h-2 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />)}
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {chatMessages.length === 0 && (
              <div className="px-3 py-2 border-t border-slate-100 bg-white flex gap-2 overflow-x-auto">
                {PARENT_PROMPTS.map(p => (
                  <button key={p} onClick={() => sendChat(p)}
                    className="flex-shrink-0 text-xs px-3 py-1.5 rounded-full border border-slate-200 text-slate-500 hover:bg-emerald-50 hover:text-emerald-700 hover:border-emerald-200 transition-colors">
                    {p}
                  </button>
                ))}
              </div>
            )}

            <div className="p-3 border-t border-slate-100 bg-white flex gap-2">
              <input value={chatInput} onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
                placeholder={`Ask about ${name}'s learning…`}
                disabled={chatLoading}
                className="flex-1 border border-slate-200 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-slate-50 disabled:opacity-50" />
              <button onClick={() => sendChat()} disabled={chatLoading || !chatInput.trim()}
                className="bg-emerald-600 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-emerald-700 disabled:opacity-40 transition-colors">
                Ask
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
