"use client";

import { useState, useRef, useEffect, useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";

const API = "/api/v1";

// ── Types ─────────────────────────────────────────────────────────────────────

type Exercise = {
  order: number;
  type: string;
  question: string;
  hint: string;
  answer: string;
  explanation: string;
  dok_level: number;
};

type ExerciseSet = {
  node_identifier: string;
  standard_code: string;
  concept: string;
  nanopoint_tag: string;
  concept_explanation: string;
  exercises: Exercise[];
  student_theta: number;
  p_mastery: number;
  dok_target: number;
};

type ChatMsg = {
  role: "user" | "ai" | "system";
  content: string;
  phi?: number;
  isPivot?: boolean;
  showActions?: boolean;   // show "Got it / Still confused" buttons after this message
};

type ExerciseChatResponse = {
  phi: number;
  reason: string;
  gap_tag: string | null;
  p_mastery_before: number;
  p_mastery_after: number;
  pivot_needed: boolean;
  pivot_node: string | null;
  bridge_instruction: string | null;
};

// ── Sub-components ────────────────────────────────────────────────────────────

function PhiBadge({ phi }: { phi: number }) {
  if (phi >= 0.7)  return <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium">Great understanding</span>;
  if (phi >= 0.4)  return <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">Partial understanding</span>;
  if (phi >= 0)    return <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 font-medium">Neutral</span>;
  if (phi >= -0.4) return <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium">Struggling</span>;
  return <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">Need to step back</span>;
}

function MasteryDelta({ before, after }: { before: number; after: number }) {
  const delta = after - before;
  const sign  = delta >= 0 ? "+" : "";
  const color = delta >= 0 ? "text-emerald-600" : "text-red-500";
  return (
    <span className={`text-xs font-mono tabular-nums ${color}`}>
      {Math.round(before * 100)}% → {Math.round(after * 100)}%
      <span className="ml-1 opacity-70">({sign}{Math.round(delta * 100)}%)</span>
    </span>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

function ExercisesPageInner() {
  const searchParams = useSearchParams();
  const router       = useRouter();

  const studentId      = searchParams.get("student_id") || "student_001";
  const nodeIdentifier = searchParams.get("node")        || "";
  const standardCode   = searchParams.get("code")        || "";
  const concept        = searchParams.get("concept")     || standardCode;
  const grade          = searchParams.get("grade")       || "K5";
  const subject        = searchParams.get("subject")     || "math";

  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [exerciseSet, setExerciseSet] = useState<ExerciseSet | null>(null);

  const [exIdx, setExIdx]           = useState(0);          // current exercise index
  const [phase, setPhase]           = useState<"chat" | "self_check" | "done">("chat");
  const [messages, setMessages]     = useState<ChatMsg[]>([]);
  const [input, setInput]           = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [showHint, setShowHint]       = useState(false);
  const [showAnswer, setShowAnswer]   = useState(false);
  const [pivotInfo, setPivotInfo]     = useState<{ node: string; instruction: string } | null>(null);
  const [masteryNow, setMasteryNow]   = useState<number | null>(null);
  const [detectedGaps, setDetectedGaps] = useState<string[]>([]); // sub-skill gaps flagged by LLM

  const startTime  = useRef<number>(Date.now());
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef   = useRef<HTMLTextAreaElement>(null);

  // Scroll chat to bottom whenever messages change
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Reset per-exercise state when moving to a new exercise
  useEffect(() => {
    setMessages([]);
    setShowHint(false);
    setShowAnswer(false);
    setPivotInfo(null);
    setPhase("chat");
    startTime.current = Date.now();
  }, [exIdx]);

  // Fetch exercise set on mount
  const loadExercises = useCallback(async () => {
    if (!nodeIdentifier || !standardCode) {
      setError("Missing exercise parameters. Please go back to the tutor.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/exercises/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id:     studentId,
          node_identifier: nodeIdentifier,
          standard_code:  standardCode,
          concept,
          grade,
          subject,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      const data: ExerciseSet = await res.json();
      setExerciseSet(data);
      setMasteryNow(data.p_mastery);
      // Greet the student
      setMessages([{
        role: "ai",
        content: `Let's practice **${data.concept}**!\n\n${data.concept_explanation}\n\nTake your time with Exercise 1. Type your thoughts, working, or answer below — I'll give you real-time feedback. Hit "Show Hint" if you get stuck.`,
      }]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [studentId, nodeIdentifier, standardCode, concept, grade, subject]);

  useEffect(() => { loadExercises(); }, [loadExercises]);

  const currentExercise = exerciseSet?.exercises[exIdx] ?? null;
  const totalExercises  = exerciseSet?.exercises.length ?? 0;

  // ── Send chat message ─────────────────────────────────────────────────────
  async function sendChat() {
    const text = input.trim();
    if (!text || chatLoading || !exerciseSet || !currentExercise) return;

    const userMsg: ChatMsg = { role: "user", content: text };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setChatLoading(true);

    const time_ms = Date.now() - startTime.current;

    try {
      const res = await fetch(`${API}/assessment/exercise_chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id:     studentId,
          node_identifier: exerciseSet.node_identifier,
          standard_code:  exerciseSet.standard_code,
          concept:        exerciseSet.concept,
          exercise_text:  currentExercise.question,
          nanopoint_tag:  exerciseSet.nanopoint_tag,
          chat_message:   text,
          answer:         "",
          correct:        null,
          time_ms,
          beta:           0.0,
        }),
      });

      if (!res.ok) throw new Error(`Chat error: HTTP ${res.status}`);
      const data: ExerciseChatResponse = await res.json();

      // Update live mastery display
      setMasteryNow(data.p_mastery_after);

      // Accumulate detected sub-skill gaps (deduplicated, max 5 shown)
      if (data.gap_tag) {
        setDetectedGaps(prev => {
          if (prev.includes(data.gap_tag!)) return prev;
          return [...prev, data.gap_tag!].slice(-5);
        });
      }

      // Build AI reply — always show action buttons after each AI response
      const reply = data.reason || "Keep going — you're thinking through it!";
      const aiMsg: ChatMsg = {
        role: "ai",
        content: reply,
        phi: data.phi,
        showActions: true,
      };
      // Clear showActions from all previous messages so only the latest has buttons
      setMessages(prev => [
        ...prev.map(m => ({ ...m, showActions: false })),
        aiMsg,
      ]);

      // Handle pivot
      if (data.pivot_needed && data.bridge_instruction) {
        setPivotInfo({
          node: data.pivot_node || "",
          instruction: data.bridge_instruction,
        });
      }
    } catch (e: any) {
      setMessages(prev => [...prev, {
        role: "ai",
        content: `Sorry, I couldn't process that: ${e.message}`,
      }]);
    } finally {
      setChatLoading(false);
      inputRef.current?.focus();
    }
  }

  // ── Submit final answer (self-check) ─────────────────────────────────────
  async function submitAnswer(gotItRight: boolean) {
    if (!exerciseSet || !currentExercise) return;
    const time_ms = Date.now() - startTime.current;

    try {
      await fetch(`${API}/assessment/exercise_complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id:      studentId,
          standard_code:   exerciseSet.standard_code,
          node_identifier: exerciseSet.node_identifier,
          exercise_id:     `${exerciseSet.standard_code}_ex${currentExercise.order}`,
          question_text:   currentExercise.question,
          correct:         gotItRight,
          selected_answer: gotItRight ? currentExercise.answer : "incorrect",
          correct_answer:  currentExercise.answer,
          dok_level:       currentExercise.dok_level,
          question_type:   currentExercise.type,
          difficulty_beta: 0.0,
        }),
      });
    } catch {
      // Non-blocking — BKT update is best-effort
    }

    // Add confirmation message then advance
    const feedbackMsg: ChatMsg = {
      role: "ai",
      content: gotItRight
        ? exIdx + 1 < totalExercises
          ? `Excellent work! Your mastery is improving. Let's move to Exercise ${exIdx + 2}.`
          : "You completed all exercises! Amazing effort. Your mastery has been updated."
        : exIdx + 1 < totalExercises
          ? `No worries — this is how we learn! Let's look at Exercise ${exIdx + 2} and keep building.`
          : "Nice work sticking with it! Check the answer explanation above, then try the concept again in the tutor.",
    };
    setMessages(prev => [...prev, feedbackMsg]);

    setTimeout(() => {
      if (exIdx + 1 < totalExercises) {
        setExIdx(i => i + 1);
      } else {
        setPhase("done");
      }
    }, 1200);
  }

  // ── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center space-y-3">
          <div className="w-12 h-12 border-4 border-indigo-200 border-t-indigo-600 rounded-full animate-spin mx-auto" />
          <p className="text-slate-500 text-sm">Preparing your exercises…</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="max-w-md text-center space-y-4 p-6">
          <p className="text-red-600 font-semibold">Something went wrong</p>
          <p className="text-slate-500 text-sm">{error}</p>
          <button onClick={() => router.back()} className="px-4 py-2 bg-indigo-600 text-white rounded-xl text-sm hover:bg-indigo-700 transition-colors">
            Go back
          </button>
        </div>
      </div>
    );
  }

  // ── Done ──────────────────────────────────────────────────────────────────
  if (phase === "done" || !currentExercise) {
    const finalMastery = masteryNow ?? exerciseSet?.p_mastery ?? 0;
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="max-w-md w-full mx-auto text-center space-y-6 p-6">
          <div className="w-20 h-20 bg-emerald-100 rounded-full flex items-center justify-center mx-auto">
            <svg className="w-10 h-10 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Practice Complete!</h1>
            <p className="text-slate-500 text-sm mt-2">
              You worked through all {totalExercises} exercises on <span className="font-semibold">{exerciseSet?.concept}</span>.
            </p>
          </div>
          <div className="bg-white border border-slate-200 rounded-2xl p-4">
            <p className="text-xs text-slate-500 uppercase tracking-wide font-medium mb-1">Mastery updated</p>
            <p className="text-3xl font-bold text-indigo-600">{Math.round(finalMastery * 100)}%</p>
            <p className="text-xs text-slate-400 mt-1">{exerciseSet?.standard_code}</p>
          </div>
          <div className="space-y-2">
            <button
              onClick={() => router.push(`/tutor?student_id=${studentId}&grade=${grade.replace("K","")}&subject=${subject}`)}
              className="w-full py-3 bg-indigo-600 text-white rounded-xl font-semibold text-sm hover:bg-indigo-700 transition-colors"
            >
              Back to AI Tutor
            </button>
            <button
              onClick={() => { setExIdx(0); setPhase("chat"); loadExercises(); }}
              className="w-full py-3 border border-slate-200 text-slate-600 rounded-xl text-sm hover:bg-slate-50 transition-colors"
            >
              Try another set of exercises
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Exercise session ──────────────────────────────────────────────────────
  const masteryPct = Math.round((masteryNow ?? exerciseSet?.p_mastery ?? 0) * 100);

  return (
    <div className="flex flex-col h-screen bg-slate-50 overflow-hidden">

      {/* ── Header ── */}
      <div className="bg-white border-b border-slate-200 px-5 py-3 flex items-center gap-4 flex-shrink-0">
        <button
          onClick={() => router.back()}
          className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-slate-900 truncate">
            {exerciseSet?.concept}
          </p>
          <p className="text-xs text-slate-400">{exerciseSet?.standard_code} · Exercise {exIdx + 1}/{totalExercises}</p>
        </div>

        {/* Mastery pill */}
        <div className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full border ${
          masteryPct >= 70 ? "text-emerald-700 bg-emerald-50 border-emerald-200" :
          masteryPct >= 45 ? "text-amber-700 bg-amber-50 border-amber-200" :
          "text-red-700 bg-red-50 border-red-200"
        }`}>
          <span className="w-2 h-2 rounded-full bg-current opacity-70" />
          {masteryPct}% mastery
        </div>
      </div>

      {/* ── Progress bar ── */}
      <div className="h-1 bg-slate-200 flex-shrink-0">
        <div
          className="h-1 bg-indigo-500 transition-all duration-500"
          style={{ width: `${((exIdx + 1) / totalExercises) * 100}%` }}
        />
      </div>

      {/* ── Body: exercise card + chat ── */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 min-h-0">

        {/* Exercise card */}
        <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-7 h-7 bg-indigo-100 text-indigo-700 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">
              {exIdx + 1}
            </span>
            <span className="text-xs text-slate-400 uppercase tracking-wide font-medium">
              {currentExercise.type.replace("_", " ")} · DOK {currentExercise.dok_level}
            </span>
          </div>

          <p className="text-slate-900 text-sm leading-relaxed font-medium">
            {currentExercise.question}
          </p>

          {/* Hint */}
          <div className="mt-4">
            <button
              onClick={() => setShowHint(h => !h)}
              className="text-xs text-indigo-600 hover:text-indigo-800 font-medium flex items-center gap-1 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
              {showHint ? "Hide hint" : "Show hint"}
            </button>
            {showHint && (
              <div className="mt-2 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2 text-xs text-amber-800 leading-relaxed">
                {currentExercise.hint}
              </div>
            )}
          </div>

          {/* Show Answer (self-check) */}
          <div className="mt-3">
            <button
              onClick={() => { setShowAnswer(a => !a); if (!showAnswer) setPhase("self_check"); }}
              className="text-xs text-slate-500 hover:text-slate-700 font-medium flex items-center gap-1 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
              </svg>
              {showAnswer ? "Hide answer" : "Show answer & check"}
            </button>

            {showAnswer && (
              <div className="mt-3 space-y-3">
                <div className="bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3">
                  <p className="text-xs font-semibold text-emerald-700 mb-1">Answer</p>
                  <p className="text-sm text-emerald-900 leading-relaxed">{currentExercise.answer}</p>
                  {currentExercise.explanation && (
                    <p className="text-xs text-emerald-700 mt-2 opacity-80">{currentExercise.explanation}</p>
                  )}
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => submitAnswer(true)}
                    className="flex-1 py-2.5 bg-emerald-600 text-white rounded-xl text-sm font-semibold hover:bg-emerald-700 transition-colors"
                  >
                    I got it right
                  </button>
                  <button
                    onClick={() => submitAnswer(false)}
                    className="flex-1 py-2.5 border border-slate-200 text-slate-600 rounded-xl text-sm font-semibold hover:bg-slate-50 transition-colors"
                  >
                    Still confused
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Detected sub-skill gaps strip */}
        {detectedGaps.length > 0 && (
          <div className="bg-amber-50 border border-amber-200 rounded-2xl p-3.5">
            <div className="flex items-center gap-2 mb-2">
              <svg className="w-4 h-4 text-amber-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
              <span className="text-xs font-semibold text-amber-800">Targeting your specific gaps</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {detectedGaps.map(tag => (
                <span key={tag} className="text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full font-medium border border-amber-200">
                  {tag}
                </span>
              ))}
            </div>
            <p className="text-xs text-amber-600 mt-2 leading-tight">
              Next exercise set will be built around these sub-skills until you master them.
            </p>
          </div>
        )}

        {/* Pivot card */}
        {pivotInfo && (
          <div className="bg-blue-50 border border-blue-200 rounded-2xl p-4 space-y-2">
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-blue-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p className="text-sm font-semibold text-blue-800">Let's step back for a moment</p>
            </div>
            <p className="text-sm text-blue-700 leading-relaxed">{pivotInfo.instruction}</p>
            <button
              onClick={() => setPivotInfo(null)}
              className="text-xs text-blue-600 underline hover:text-blue-800"
            >
              Got it — let me try again
            </button>
          </div>
        )}

        {/* Chat messages */}
        <div className="space-y-3">
          {messages.map((msg, i) => {
            const isUser = msg.role === "user";
            return (
              <div key={i} className={`flex gap-2.5 ${isUser ? "justify-end" : "justify-start"}`}>
                {!isUser && (
                  <div className="w-7 h-7 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0 mt-0.5">
                    AI
                  </div>
                )}
                <div className="max-w-[85%] space-y-2">
                  <div className={`rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
                    isUser
                      ? "bg-indigo-600 text-white rounded-br-sm"
                      : "bg-white border border-slate-200 text-slate-800 rounded-bl-sm shadow-sm"
                  }`}>
                    {msg.content}
                  </div>
                  {!isUser && msg.phi !== undefined && (
                    <div className="flex items-center gap-2 px-1">
                      <PhiBadge phi={msg.phi} />
                    </div>
                  )}
                  {/* Inline action buttons — only on the latest AI message */}
                  {!isUser && msg.showActions && (
                    <div className="flex gap-2 px-1">
                      <button
                        onClick={() => submitAnswer(true)}
                        className="flex-1 py-2 bg-emerald-600 text-white rounded-xl text-xs font-semibold hover:bg-emerald-700 transition-colors"
                      >
                        I got it ✓
                      </button>
                      <button
                        onClick={() => setShowAnswer(true)}
                        className="flex-1 py-2 border border-slate-200 text-slate-600 rounded-xl text-xs font-semibold hover:bg-slate-50 transition-colors"
                      >
                        Show answer
                      </button>
                    </div>
                  )}
                </div>
                {isUser && (
                  <div className="w-7 h-7 bg-slate-200 rounded-full flex items-center justify-center text-slate-600 text-xs font-bold flex-shrink-0 mt-0.5">
                    You
                  </div>
                )}
              </div>
            );
          })}

          {chatLoading && (
            <div className="flex gap-2.5 justify-start">
              <div className="w-7 h-7 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
                AI
              </div>
              <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-sm px-4 py-3 flex gap-1.5 items-center shadow-sm">
                <span className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>
      </div>

      {/* ── Chat input ── */}
      <div className="bg-white border-t border-slate-200 px-4 py-3 flex-shrink-0">
        <div className="flex gap-2 items-end">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
            }}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
            placeholder="Type your working, thoughts, or answer… (Enter to send)"
            disabled={chatLoading}
            rows={1}
            className="flex-1 border border-slate-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50 bg-slate-50 resize-none overflow-hidden leading-relaxed"
            style={{ minHeight: "42px" }}
          />
          <button
            onClick={sendChat}
            disabled={chatLoading || !input.trim()}
            className="bg-indigo-600 text-white w-10 h-10 rounded-xl flex items-center justify-center hover:bg-indigo-700 disabled:opacity-40 transition-colors flex-shrink-0"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
        <p className="text-xs text-slate-400 mt-1.5 text-center">
          Your responses are analysed in real-time · mastery updates after every message
        </p>
      </div>
    </div>
  );
}

export default function ExercisesPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-10 h-10 border-4 border-indigo-200 border-t-indigo-600 rounded-full animate-spin" />
      </div>
    }>
      <ExercisesPageInner />
    </Suspense>
  );
}
