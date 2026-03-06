"use client";

import { useState } from "react";

const API = "/api/v1";

type Session = {
  session_id: string;
  theta: number;
  grade: number;
  total_questions: number;
};

type Question = {
  question: string;
  options: string[];
  answer: string;
  dok_level: number;
  dok_label?: string;
};

type NextResponse = {
  done: boolean;
  node_id?: string;
  standard?: {
    code: string;
    description: string;
    grade_level: string;
    difficulty: number;
    jurisdiction: string;
  };
  question?: Question;
  message?: string;
};

type AnswerResponse = {
  q_number: number;
  is_done: boolean;
  theta: number;
  theta_delta: number;
  beta: number;
  p_correct: number;
  is_correct: boolean;
};

type HeatMapNode = {
  id: string;
  code: string;
  description: string;
  beta: number;
};

type HeatMap = {
  theta_final: number;
  ability_label: string;
  frontier_count: number;
  ancestor_count: number;
  frontier: HeatMapNode[];
  ancestors: HeatMapNode[];
  next_best_actions: HeatMapNode[];
};

const GRADES = Array.from({ length: 8 }, (_, i) => ({
  id: i + 1,
  label: `Grade ${i + 1}`,
  ages: `${i + 6}–${i + 7}`,
}));

function ThetaBar({ theta, max = 9 }: { theta: number; max?: number }) {
  const pct = Math.min(100, (theta / max) * 100);
  const color =
    theta >= 7 ? "bg-green-500" :
    theta >= 5 ? "bg-blue-500" :
    theta >= 3 ? "bg-amber-500" : "bg-red-400";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-gray-500">
        <span>Ability θ</span>
        <span className="font-mono font-bold text-gray-900">{theta.toFixed(2)}</span>
      </div>
      <div className="h-2.5 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-2.5 rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between text-xs text-gray-400">
        <span>Gr 1</span><span>Gr 4–5</span><span>Gr 8+</span>
      </div>
    </div>
  );
}

export default function RaschPage() {
  const [step, setStep] = useState<"setup" | "taking" | "results">("setup");
  const [studentId, setStudentId] = useState("student_001");
  const [grade, setGrade] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Session state
  const [session, setSession] = useState<Session | null>(null);
  const [next, setNext] = useState<NextResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [startTime, setStartTime] = useState<number>(Date.now());
  const [lastAnswer, setLastAnswer] = useState<AnswerResponse | null>(null);
  const [currentTheta, setCurrentTheta] = useState(3.0);
  const [qCount, setQCount] = useState(0);
  const [heatmap, setHeatmap] = useState<HeatMap | null>(null);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);

  // ── Step 1: Start session ───────────────────────────────────────────────────
  async function startSession() {
    setLoading(true); setError(null);
    try {
      // 1a. Create the session
      const res = await fetch(`${API}/rasch/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ student_id: studentId, grade }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Failed to start session");
      const data: Session = await res.json();
      setSession(data);
      setCurrentTheta(data.theta);
      setQCount(0);

      // 1b. Fetch first question — must succeed before we transition screens
      const nres = await fetch(`${API}/rasch/${data.session_id}/next`);
      if (!nres.ok) throw new Error((await nres.json()).detail || "Failed to load first question");
      const ndata: NextResponse = await nres.json();
      setNext(ndata);
      setStartTime(Date.now());

      // Only switch screens once we have something to show
      setStep("taking");
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  // ── Step 2: Fetch next question ─────────────────────────────────────────────
  async function fetchNextQuestion(sid: string) {
    setLoading(true); setError(null); setSelected(null); setLastAnswer(null);
    try {
      const res = await fetch(`${API}/rasch/${sid}/next`);
      if (!res.ok) throw new Error((await res.json()).detail || "Failed to get question");
      const data: NextResponse = await res.json();
      setNext(data);
      setStartTime(Date.now());
      if (data.done) await finalizeSession(sid);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  // ── Step 3: Submit answer ───────────────────────────────────────────────────
  async function submitAnswer() {
    if (!session || !next?.node_id || !selected) return;
    const timeSec = (Date.now() - startTime) / 1000;
    const isCorrect = selected === next.question?.answer;

    setLoading(true); setError(null);
    try {
      const res = await fetch(`${API}/rasch/${session.session_id}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          node_id: next.node_id,
          is_correct: isCorrect,
          time_seconds: timeSec,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Failed to submit");
      const data: AnswerResponse = await res.json();
      setLastAnswer(data);
      setCurrentTheta(data.theta);
      setQCount(data.q_number);

      if (data.is_done) {
        await finalizeSession(session.session_id);
      }
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function nextQuestion() {
    if (!session) return;
    await fetchNextQuestion(session.session_id);
  }

  // ── Step 4: Finalise + heat map + LLM analysis ─────────────────────────────
  async function finalizeSession(sid: string) {
    try {
      const res = await fetch(`${API}/rasch/${sid}/heatmap`);
      if (!res.ok) return;
      const data: HeatMap = await res.json();
      setHeatmap(data);
      setStep("results");
      // Kick off analysis in the background after showing results
      fetchAnalysis(sid);
    } catch (e: any) { setError(e.message); }
  }

  async function fetchAnalysis(sid: string) {
    setAnalysisLoading(true);
    try {
      const res = await fetch(`${API}/rasch/${sid}/analysis`);
      if (!res.ok) return;
      const data = await res.json();
      setAnalysis(data.analysis ?? null);
    } catch { /* non-critical */ }
    finally { setAnalysisLoading(false); }
  }

  // ── Setup screen ────────────────────────────────────────────────────────────
  if (step === "setup") {
    return (
      <div className="max-w-xl mx-auto space-y-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Rasch Diagnostic</h1>
          <p className="text-gray-500 mt-1">
            15-question adaptive diagnostic · IRT Rasch 1PL · real-time ability estimate
          </p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm">{error}</div>
        )}

        <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Student ID</label>
            <input
              value={studentId}
              onChange={e => setStudentId(e.target.value)}
              className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Grade Level</label>
            <div className="grid grid-cols-4 gap-2">
              {GRADES.map(g => (
                <button
                  key={g.id}
                  onClick={() => setGrade(g.id)}
                  className={`py-3 rounded-xl text-sm font-medium border transition-all ${
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

          <div className="bg-blue-50 rounded-xl p-4 text-sm text-blue-800 space-y-1">
            <div className="font-semibold mb-1">How the Rasch engine works</div>
            <div>· θ₀ = {grade}.0 (Grade {grade} starting ability)</div>
            <div>· Each question selected where item difficulty β ≈ θ</div>
            <div>· K = 1.2 (Q1–5) then K = 0.6 (Q6–15) for precision</div>
            <div>· Fast correct answers on hard items boost θ by +0.15</div>
          </div>

          <button
            onClick={startSession}
            disabled={loading}
            className="w-full bg-blue-600 text-white py-3.5 rounded-xl font-semibold hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? "Starting session…" : "Begin Diagnostic"}
          </button>
        </div>
      </div>
    );
  }

  // ── Assessment screen ────────────────────────────────────────────────────────
  if (step === "taking" && !next) {
    // Transitioning — should be brief; show spinner rather than blank page
    return (
      <div className="max-w-2xl mx-auto flex flex-col items-center justify-center min-h-64 gap-4">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm max-w-sm text-center">
            {error}
            <button onClick={() => setStep("setup")} className="block mx-auto mt-2 text-blue-600 hover:underline text-xs">
              Back to setup
            </button>
          </div>
        )}
      </div>
    );
  }

  if (step === "taking" && next) {
    const q = next.question;
    const showFeedback = !!lastAnswer;

    return (
      <div className="max-w-2xl mx-auto space-y-5">
        {/* Header bar */}
        <div className="bg-white rounded-2xl border border-gray-200 p-5 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-600">
              Question {qCount}/{session?.total_questions ?? 15}
            </span>
            {lastAnswer && (
              <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                lastAnswer.is_correct
                  ? "bg-green-100 text-green-700"
                  : "bg-red-100 text-red-700"
              }`}>
                {lastAnswer.is_correct ? "✓ Correct" : "✗ Incorrect"} · Δθ {lastAnswer.theta_delta > 0 ? "+" : ""}{lastAnswer.theta_delta.toFixed(3)}
              </span>
            )}
          </div>
          <ThetaBar theta={currentTheta} />
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm">{error}</div>
        )}

        {/* Standard chip */}
        {next.standard && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs bg-gray-100 text-gray-600 px-2.5 py-1 rounded-full font-mono">
              {next.standard.code}
            </span>
            <span className="text-xs text-gray-400">
              β = {next.standard.difficulty?.toFixed(1)} · {next.standard.jurisdiction}
            </span>
          </div>
        )}

        {/* Question card */}
        {q ? (
          <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-5">
            <p className="text-gray-900 font-medium text-base leading-relaxed">{q.question}</p>

            <div className="space-y-2">
              {q.options.map(opt => {
                const letter = opt.charAt(0);
                const isSelected = selected === letter;
                const isCorrectOpt = showFeedback && letter === q.answer;
                const isWrongSelected = showFeedback && isSelected && !lastAnswer?.is_correct;

                return (
                  <button
                    key={opt}
                    onClick={() => !showFeedback && setSelected(letter)}
                    disabled={showFeedback}
                    className={`w-full flex items-center gap-3 p-4 rounded-xl text-left text-sm border transition-all ${
                      isCorrectOpt
                        ? "bg-green-50 border-green-400 text-green-900"
                        : isWrongSelected
                        ? "bg-red-50 border-red-400 text-red-900"
                        : isSelected
                        ? "bg-blue-50 border-blue-500 text-blue-900"
                        : "border-gray-200 hover:border-blue-300 text-gray-700"
                    }`}
                  >
                    <span className={`w-7 h-7 rounded-full flex items-center justify-center font-bold text-xs flex-shrink-0 ${
                      isCorrectOpt ? "bg-green-500 text-white"
                      : isWrongSelected ? "bg-red-400 text-white"
                      : isSelected ? "bg-blue-500 text-white"
                      : "bg-gray-100 text-gray-600"
                    }`}>{letter}</span>
                    <span>{opt.substring(3)}</span>
                    {isCorrectOpt && <span className="ml-auto text-green-600 font-bold">✓</span>}
                  </button>
                );
              })}
            </div>

            {!showFeedback ? (
              <button
                onClick={submitAnswer}
                disabled={!selected || loading}
                className="w-full bg-blue-600 text-white py-3 rounded-xl font-semibold hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {loading ? "Evaluating…" : "Submit Answer"}
              </button>
            ) : (
              <button
                onClick={nextQuestion}
                disabled={loading}
                className="w-full bg-gray-900 text-white py-3 rounded-xl font-semibold hover:bg-gray-800 disabled:opacity-50 transition-colors"
              >
                {loading ? "Loading next question…" : "Next Question →"}
              </button>
            )}
          </div>
        ) : (
          <div className="bg-white rounded-2xl border border-gray-200 p-8 text-center space-y-4">
            <p className="text-gray-400">
              {loading ? "Generating question…" : next.done ? "Assessment complete" : "Question unavailable for this standard"}
            </p>
            {!loading && !next.done && (
              <button
                onClick={nextQuestion}
                className="text-sm text-blue-600 hover:underline font-medium"
              >
                Skip to next question →
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  // ── Results / Heat-map screen ────────────────────────────────────────────────
  if (step === "results" && heatmap) {
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold text-gray-900">Diagnostic Results</h1>
          <button
            onClick={() => { setStep("setup"); setHeatmap(null); setSession(null); setNext(null); setAnalysis(null); }}
            className="text-sm text-blue-600 hover:underline font-medium"
          >
            New Assessment
          </button>
        </div>

        {/* Ability summary */}
        <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-4xl font-bold text-gray-900">θ = {heatmap.theta_final.toFixed(2)}</div>
              <div className="text-gray-500 mt-1">{heatmap.ability_label}</div>
            </div>
            <div className="text-right text-sm text-gray-500 space-y-1">
              <div>{heatmap.frontier_count} standards potentially mastered</div>
              <div>{heatmap.ancestor_count} prerequisites confirmed</div>
              <div>{heatmap.next_best_actions.length} next learning targets</div>
            </div>
          </div>
          <ThetaBar theta={heatmap.theta_final} />
        </div>

        {/* LLM Diagnostic Analysis */}
        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 text-lg mb-1">Diagnostic Report</h2>
          <p className="text-sm text-gray-500 mb-4">
            AI analysis of this student&apos;s strengths and learning gaps
          </p>
          {analysisLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map(i => (
                <div key={i} className="h-4 bg-gray-100 rounded-full animate-pulse" style={{ width: `${70 + i * 10}%` }} />
              ))}
              <p className="text-xs text-gray-400 mt-2">Generating analysis…</p>
            </div>
          ) : analysis ? (
            <div className="prose prose-sm max-w-none text-gray-700 space-y-3">
              {analysis.split("\n\n").map((para, i) => {
                // Bold the section headers (**HEADER**)
                const parts = para.split(/(\*\*[^*]+\*\*)/g);
                return (
                  <p key={i} className="leading-relaxed">
                    {parts.map((part, j) =>
                      part.startsWith("**") && part.endsWith("**")
                        ? <strong key={j} className="text-gray-900">{part.slice(2, -2)}</strong>
                        : part
                    )}
                  </p>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-gray-400 italic">
              Analysis unavailable — ensure GEMINI_API_KEY is set in .env
            </p>
          )}
        </div>

        {/* Next best actions */}
        {heatmap.next_best_actions.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-200 p-6">
            <h2 className="font-semibold text-gray-900 text-lg mb-1">Next Learning Targets</h2>
            <p className="text-sm text-gray-500 mb-4">
              Standards just beyond your current ability — your Zone of Proximal Development
            </p>
            <div className="space-y-2">
              {heatmap.next_best_actions.map((n, i) => (
                <div key={i} className="flex items-start gap-3 p-3 bg-blue-50 border border-blue-200 rounded-xl">
                  <span className="font-mono text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded mt-0.5 flex-shrink-0">
                    {n.code}
                  </span>
                  <span className="text-sm text-gray-700 leading-snug">{n.description}</span>
                  <span className="ml-auto text-xs text-gray-400 flex-shrink-0">β {n.beta?.toFixed(1)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Mastered frontier */}
        {heatmap.frontier.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-200 p-6">
            <h2 className="font-semibold text-gray-900 text-lg mb-1">Mastered Standards</h2>
            <p className="text-sm text-gray-500 mb-4">
              Standards at or below your ability level — marked at 85% mastery in Neo4j
            </p>
            <div className="grid grid-cols-1 gap-2 max-h-64 overflow-y-auto">
              {heatmap.frontier.map((n, i) => (
                <div key={i} className="flex items-center gap-2 text-sm p-2 bg-green-50 rounded-lg border border-green-200">
                  <span className="font-mono text-xs text-green-700 flex-shrink-0">{n.code}</span>
                  <span className="text-gray-600 truncate">{n.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Confirmed prerequisites */}
        {heatmap.ancestors.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-200 p-6">
            <h2 className="font-semibold text-gray-900 text-lg mb-1">Confirmed Prerequisites</h2>
            <p className="text-sm text-gray-500 mb-4">
              Foundational standards inferred as mastered — marked at 98% in Neo4j
            </p>
            <div className="grid grid-cols-1 gap-2 max-h-48 overflow-y-auto">
              {heatmap.ancestors.map((n, i) => (
                <div key={i} className="flex items-center gap-2 text-sm p-2 bg-gray-50 rounded-lg border border-gray-200">
                  <span className="font-mono text-xs text-gray-500 flex-shrink-0">{n.code}</span>
                  <span className="text-gray-600 truncate">{n.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  return null;
}
