"use client";

import { useState, useRef, useEffect, useCallback } from "react";

const API = "/api/v1";

const GRADES = [
  { id: "all", label: "All Grades" },
  ...Array.from({ length: 8 }, (_, i) => ({
    id: `K${i + 1}`,
    label: `Grade ${i + 1}`,
  })),
];

const SUBJECTS = [
  { id: "all",     label: "All Subjects" },
  { id: "math",    label: "Mathematics" },
  { id: "english", label: "English Language Arts" },
];

type MasteryItem = {
  identifier: string; code: string; description: string;
  grade: string; subject: string; mastery: number; attempts: number;
};

type MasteryContext = {
  student_id: string; has_history: boolean;
  total_assessed: number; total_in_kg: number; mean_mastery: number | null;
  gaps: MasteryItem[]; strengths: MasteryItem[]; recent: MasteryItem[];
  grade_breakdown: Record<string, { count: number; mean_mastery: number }>;
};

type Message = { role: "user" | "assistant"; content: string };

const SUGGESTIONS = [
  "What are my biggest knowledge gaps?",
  "Explain my weakest concept",
  "What should I study next?",
  "Give me a practice problem",
  "Show me my strengths",
  "How am I progressing overall?",
];

function MasteryBar({ value, size = "md" }: { value: number; size?: "sm" | "md" }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.7 ? "bg-green-500" : value >= 0.45 ? "bg-amber-400" : "bg-red-400";
  const h = size === "sm" ? "h-1" : "h-1.5";
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-14 ${h} bg-gray-200 rounded-full overflow-hidden`}>
        <div className={`${h} ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-500 tabular-nums">{pct}%</span>
    </div>
  );
}

function MessageBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0 mt-0.5 shadow-sm">
          AI
        </div>
      )}
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap shadow-sm ${
          isUser
            ? "bg-blue-600 text-white rounded-br-sm"
            : "bg-white border border-gray-200 text-gray-800 rounded-bl-sm"
        }`}
      >
        {msg.content}
      </div>
      {isUser && (
        <div className="w-8 h-8 bg-gray-200 rounded-full flex items-center justify-center text-gray-600 text-xs font-bold flex-shrink-0 mt-0.5">
          You
        </div>
      )}
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex gap-3 justify-start">
      <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0 shadow-sm">
        AI
      </div>
      <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-3 flex gap-1.5 items-center shadow-sm">
        <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
        <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
      </div>
    </div>
  );
}

export default function TutorPage() {
  const [studentId, setStudentId] = useState("student_001");
  const [grade, setGrade]         = useState("all");
  const [subject, setSubject]     = useState("all");

  const [messages, setMessages]         = useState<Message[]>([]);
  const [input, setInput]               = useState("");
  const [chatLoading, setChatLoading]   = useState(false);
  const [contextLoading, setCtxLoading] = useState(false);
  const [mastery, setMastery]           = useState<MasteryContext | null>(null);
  const [sidebarTab, setSidebarTab]     = useState<"gaps" | "strengths" | "grades">("gaps");
  const [sidebarOpen, setSidebarOpen]   = useState(true);

  const chatEndRef   = useRef<HTMLDivElement>(null);
  const inputRef     = useRef<HTMLTextAreaElement>(null);
  const loadedForRef = useRef("");

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const loadContext = useCallback(async (sid: string, g: string, s: string) => {
    const key = `${sid}|${g}|${s}`;
    if (loadedForRef.current === key) return;
    loadedForRef.current = key;
    setCtxLoading(true);
    try {
      const res = await fetch(`${API}/chat/context/${encodeURIComponent(sid)}?grade=${g}&subject=${s}`);
      const data: MasteryContext = await res.json();
      setMastery(data);
    } catch {
      setMastery(null);
    } finally {
      setCtxLoading(false);
    }
  }, []);

  useEffect(() => {
    loadContext(studentId, grade, subject);
  }, [studentId, grade, subject, loadContext]);

  async function sendMessage(override?: string) {
    const text = (override ?? input).trim();
    if (!text || chatLoading) return;
    const userMsg: Message = { role: "user", content: text };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setChatLoading(true);

    try {
      const res = await fetch(`${API}/chat/standalone`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id:      studentId,
          grade,
          subject,
          message:         text,
          history:         messages,
          mastery_context: mastery ?? {},
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Chat failed");
      const data = await res.json();
      setMessages(prev => [...prev, { role: "assistant", content: data.content }]);
    } catch (e: any) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: `Sorry, I couldn't respond right now: ${e.message}`,
      }]);
    } finally {
      setChatLoading(false);
      inputRef.current?.focus();
    }
  }

  function startConversation() {
    if (messages.length > 0) return;
    const greeting = mastery?.has_history
      ? "Hi! Give me a personalised overview of my mastery profile — highlight my biggest gaps and what I should focus on right now."
      : "Hi! I'm new here. Can you explain how this works and suggest what assessment I should start with?";
    sendMessage(greeting);
  }

  const hasMastery = mastery?.has_history;
  const meanPct    = mastery?.mean_mastery != null ? Math.round(mastery.mean_mastery * 100) : null;

  return (
    <div className="flex gap-0 -mx-6 -my-8 h-[calc(100vh-73px)]">

      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <aside className="w-72 flex-shrink-0 bg-white border-r border-gray-200 flex flex-col overflow-hidden">
          {/* Profile header */}
          <div className="p-4 border-b border-gray-100 space-y-3">
            <div>
              <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Student</label>
              <input
                value={studentId}
                onChange={e => { setStudentId(e.target.value); loadedForRef.current = ""; }}
                onBlur={() => loadContext(studentId, grade, subject)}
                className="w-full mt-1 border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Grade</label>
                <select
                  value={grade}
                  onChange={e => setGrade(e.target.value)}
                  className="w-full mt-1 border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {GRADES.map(g => <option key={g.id} value={g.id}>{g.label}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Subject</label>
                <select
                  value={subject}
                  onChange={e => setSubject(e.target.value)}
                  className="w-full mt-1 border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {SUBJECTS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </div>
            </div>
          </div>

          {/* Mastery overview */}
          <div className="p-4 border-b border-gray-100">
            {contextLoading ? (
              <div className="text-xs text-gray-400 animate-pulse">Loading mastery profile…</div>
            ) : hasMastery ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-gray-500">Overall Mastery</span>
                  <span className={`text-sm font-bold ${
                    (meanPct ?? 0) >= 70 ? "text-green-600" :
                    (meanPct ?? 0) >= 45 ? "text-amber-600" : "text-red-600"
                  }`}>{meanPct}%</span>
                </div>
                <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className={`h-2 rounded-full ${
                      (meanPct ?? 0) >= 70 ? "bg-green-500" :
                      (meanPct ?? 0) >= 45 ? "bg-amber-400" : "bg-red-400"
                    }`}
                    style={{ width: `${meanPct}%` }}
                  />
                </div>
                <div className="flex gap-3 text-xs text-gray-500 mt-1">
                  <span>{mastery.total_assessed} assessed</span>
                  <span>{mastery.gaps.length} gaps</span>
                  <span>{mastery.strengths.length} strengths</span>
                </div>
              </div>
            ) : (
              <div className="text-xs text-gray-400 leading-relaxed">
                No assessment history yet.{" "}
                <a href="/assessment" className="text-blue-600 underline">Take an assessment</a> to unlock personalised insights.
              </div>
            )}
          </div>

          {/* Tab switcher */}
          {hasMastery && (
            <>
              <div className="flex border-b border-gray-100">
                {(["gaps", "strengths", "grades"] as const).map(tab => (
                  <button
                    key={tab}
                    onClick={() => setSidebarTab(tab)}
                    className={`flex-1 py-2 text-xs font-medium capitalize transition-colors ${
                      sidebarTab === tab
                        ? "text-blue-600 border-b-2 border-blue-600 bg-blue-50"
                        : "text-gray-500 hover:text-gray-700"
                    }`}
                  >
                    {tab === "gaps" ? `Gaps (${mastery.gaps.length})` :
                     tab === "strengths" ? `Strong (${mastery.strengths.length})` : "By Grade"}
                  </button>
                ))}
              </div>

              <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {sidebarTab === "gaps" && (
                  mastery.gaps.length === 0
                    ? <p className="text-xs text-gray-400 text-center py-4">No gaps — great work!</p>
                    : mastery.gaps.map((g, i) => (
                      <button
                        key={i}
                        onClick={() => sendMessage(`Explain ${g.code} — ${g.description}. How can I improve my mastery of this concept?`)}
                        className="w-full text-left p-2.5 rounded-xl border border-gray-100 hover:border-red-200 hover:bg-red-50 transition-colors group"
                      >
                        <div className="flex items-start justify-between gap-1">
                          <span className="text-xs font-semibold text-gray-800 group-hover:text-red-700">{g.code}</span>
                          <MasteryBar value={g.mastery} size="sm" />
                        </div>
                        <p className="text-xs text-gray-500 mt-0.5 line-clamp-2 leading-tight">{g.description}</p>
                      </button>
                    ))
                )}
                {sidebarTab === "strengths" && (
                  mastery.strengths.length === 0
                    ? <p className="text-xs text-gray-400 text-center py-4">Complete an assessment to discover your strengths.</p>
                    : mastery.strengths.map((s, i) => (
                      <button
                        key={i}
                        onClick={() => sendMessage(`Tell me about ${s.code} — ${s.description}. What comes next after mastering this?`)}
                        className="w-full text-left p-2.5 rounded-xl border border-gray-100 hover:border-green-200 hover:bg-green-50 transition-colors group"
                      >
                        <div className="flex items-start justify-between gap-1">
                          <span className="text-xs font-semibold text-gray-800 group-hover:text-green-700">{s.code}</span>
                          <MasteryBar value={s.mastery} size="sm" />
                        </div>
                        <p className="text-xs text-gray-500 mt-0.5 line-clamp-2 leading-tight">{s.description}</p>
                      </button>
                    ))
                )}
                {sidebarTab === "grades" && (
                  Object.keys(mastery.grade_breakdown).length === 0
                    ? <p className="text-xs text-gray-400 text-center py-4">No data yet.</p>
                    : Object.entries(mastery.grade_breakdown)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([g, v]) => (
                          <button
                            key={g}
                            onClick={() => sendMessage(`Give me a summary of my Grade ${g} mastery and what I should focus on for Grade ${g}.`)}
                            className="w-full text-left p-2.5 rounded-xl border border-gray-100 hover:border-blue-200 hover:bg-blue-50 transition-colors"
                          >
                            <div className="flex items-center justify-between">
                              <span className="text-xs font-semibold text-gray-700">Grade {g}</span>
                              <MasteryBar value={v.mean_mastery} size="sm" />
                            </div>
                            <p className="text-xs text-gray-400 mt-0.5">{v.count} standards assessed</p>
                          </button>
                        ))
                )}
              </div>
            </>
          )}

          {/* New chat button */}
          <div className="p-3 border-t border-gray-100">
            <button
              onClick={() => { setMessages([]); loadedForRef.current = ""; loadContext(studentId, grade, subject); }}
              className="w-full text-xs text-gray-500 hover:text-gray-700 py-2 rounded-lg hover:bg-gray-50 transition-colors"
            >
              + New conversation
            </button>
          </div>
        </aside>
      )}

      {/* ── Main chat ── */}
      <div className="flex-1 flex flex-col overflow-hidden bg-gray-50 min-w-0">

        {/* Chat topbar */}
        <div className="bg-white border-b border-gray-200 px-5 py-3 flex items-center gap-3 flex-shrink-0">
          <button
            onClick={() => setSidebarOpen(o => !o)}
            className="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded-lg hover:bg-gray-100"
            title="Toggle sidebar"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center text-white text-xs font-bold">AI</div>
            <div>
              <span className="font-semibold text-gray-900 text-sm">AI Tutor</span>
              <span className="text-xs text-gray-400 ml-2">Gemini 2.5 Pro · KG-grounded</span>
            </div>
          </div>
          {hasMastery && meanPct != null && (
            <div className="ml-auto flex items-center gap-2 text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-full px-3 py-1">
              <span className={`w-2 h-2 rounded-full ${meanPct >= 70 ? "bg-green-500" : meanPct >= 45 ? "bg-amber-400" : "bg-red-400"}`} />
              {studentId} · {meanPct}% mastery · {mastery!.gaps.length} gap{mastery!.gaps.length !== 1 ? "s" : ""}
            </div>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-6 py-12">
              <div className="w-16 h-16 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center text-white text-2xl font-bold shadow-lg">
                AI
              </div>
              <div className="space-y-2">
                <h2 className="text-xl font-bold text-gray-900">Your Personal AI Tutor</h2>
                <p className="text-gray-500 text-sm max-w-md">
                  Ask me anything about your learning gaps, request concept explanations,
                  or get practice problems tailored to your exact mastery level.
                </p>
              </div>
              {contextLoading ? (
                <div className="text-xs text-gray-400 animate-pulse">Loading your profile…</div>
              ) : (
                <button
                  onClick={startConversation}
                  className="bg-blue-600 text-white px-6 py-2.5 rounded-xl font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm"
                >
                  Start conversation
                </button>
              )}
              <div className="grid grid-cols-2 gap-2 w-full max-w-lg">
                {SUGGESTIONS.map(s => (
                  <button
                    key={s}
                    onClick={() => sendMessage(s)}
                    className="text-left text-xs p-3 bg-white border border-gray-200 rounded-xl hover:border-blue-300 hover:bg-blue-50 transition-colors text-gray-600 hover:text-blue-700"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <MessageBubble key={i} msg={msg} />
          ))}

          {chatLoading && <TypingIndicator />}
          <div ref={chatEndRef} />
        </div>

        {/* Quick suggestions (after first response) */}
        {messages.length >= 2 && !chatLoading && (
          <div className="px-6 py-2 flex gap-2 overflow-x-auto flex-shrink-0 border-t border-gray-100 bg-white">
            {SUGGESTIONS.slice(0, 4).map(s => (
              <button
                key={s}
                onClick={() => sendMessage(s)}
                className="flex-shrink-0 text-xs px-3 py-1.5 rounded-full bg-gray-100 text-gray-600 hover:bg-blue-50 hover:text-blue-700 transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Input */}
        <div className="bg-white border-t border-gray-200 px-6 py-4 flex-shrink-0">
          <div className="flex gap-3 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
              placeholder="Ask about your gaps, request an explanation, get a practice problem… (Enter to send, Shift+Enter for newline)"
              disabled={chatLoading}
              rows={1}
              className="flex-1 border border-gray-200 rounded-2xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 bg-gray-50 resize-none overflow-hidden leading-relaxed"
              style={{ minHeight: "44px" }}
            />
            <button
              onClick={() => sendMessage()}
              disabled={chatLoading || !input.trim()}
              className="bg-blue-600 text-white w-11 h-11 rounded-xl flex items-center justify-center hover:bg-blue-700 disabled:opacity-40 transition-colors flex-shrink-0"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </button>
          </div>
          <p className="text-xs text-gray-400 mt-2 text-center">
            Powered by Gemini 2.5 Pro · Grounded in your live knowledge graph mastery
          </p>
        </div>
      </div>
    </div>
  );
}
