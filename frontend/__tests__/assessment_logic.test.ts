/**
 * Frontend unit tests for the assessment pipeline UI logic.
 *
 * Tests pure functions extracted from assessment/page.tsx — no browser/DOM needed:
 *   1. Grade & subject constants
 *   2. Option letter extraction (what is stored as the student's answer)
 *   3. Option text extraction (what is displayed to the student)
 *   4. Answer correctness check  (frontend pre-compute before submitting)
 *   5. Score display helpers
 *   6. API request payload shape (generate + evaluate)
 *   7. API response validation (shape guard)
 *   8. `EvalResult` field presence guard
 *   9. Grade label rendering
 *  10. Status badge mapping
 */

// ── Inline replicas of the constants and helpers from assessment/page.tsx ───

const GRADES = Array.from({ length: 8 }, (_, i) => ({
  id: `K${i + 1}`,
  label: `Grade ${i + 1}`,
  ages: `${i + 6}–${i + 7}`,
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

const STATUS_LABEL: Record<string, string> = {
  above: "Above grade level", at: "At grade level",
  approaching: "Almost there", below: "Needs more practice",
};

// ── Helpers mirroring what the component does ─────────────────────────────

/** Extract the single-letter key from an option string like "A. Some text" */
function extractLetter(opt: string): string {
  return opt.charAt(0);
}

/** Extract display text from "A. Some answer text" → "Some answer text" */
function extractText(opt: string): string {
  return opt.substring(3);
}

/** Whether student's stored letter matches the question's correct answer */
function isCorrect(selectedLetter: string | undefined, correctAnswer: string): boolean {
  return (selectedLetter ?? "") === correctAnswer;
}

/** Build the generate-assessment request body */
function buildGeneratePayload(opts: {
  grade: string; subject: string; studentId: string;
  state: string; numQuestions?: number;
}) {
  return {
    grade: opts.grade,
    subject: opts.subject,
    student_id: opts.studentId,
    state: opts.state,
    num_questions: opts.numQuestions ?? 15,
  };
}

/** Build the evaluate-assessment request body */
function buildEvaluatePayload(opts: {
  assessmentId: string; studentId: string; grade: string;
  subject: string; state: string;
  questions: Array<{
    id: string; question: string; options: string[]; answer: string;
    dok_level: number; beta?: number; node_ref: string; category: string;
    standard_code: string; standard_description: string;
  }>;
  answers: Record<string, string>;
}) {
  const payload = opts.questions.map(q => ({
    question_id: q.id,
    question: q.question,
    options: q.options,
    answer: q.answer,
    dok_level: q.dok_level,
    beta: q.beta ?? 0,
    node_ref: q.node_ref,
    category: q.category,
    standard_code: q.standard_code,
    standard_description: q.standard_description,
    student_answer: opts.answers[q.id] || "",
    is_correct: opts.answers[q.id] === q.answer,
  }));
  return {
    assessment_id: opts.assessmentId,
    student_id: opts.studentId,
    grade: opts.grade,
    subject: opts.subject,
    state: opts.state,
    answers: payload,
  };
}

/** Guard: does the API response have the required Assessment fields? */
function isValidAssessmentResponse(data: any): boolean {
  return (
    typeof data === "object" && data !== null &&
    typeof data.assessment_id === "string" &&
    Array.isArray(data.questions) &&
    typeof data.num_questions === "number" &&
    typeof data.framework === "string"
  );
}

/** Guard: does the API response have the required EvalResult fields? */
function isValidEvalResponse(data: any): boolean {
  return (
    typeof data === "object" && data !== null &&
    typeof data.score === "number" &&
    typeof data.correct === "number" &&
    typeof data.total === "number" &&
    typeof data.grade_status === "string" &&
    Array.isArray(data.bkt_updates)
  );
}

/** Map score fraction to grade_status string (mirrors the backend logic) */
function scoreToStatus(score: number): string {
  if (score >= 0.85) return "above";
  if (score >= 0.70) return "at";
  if (score >= 0.50) return "approaching";
  return "below";
}

/** Grade id to display label: "K3" → "Grade 3" */
function gradeIdToLabel(gradeId: string): string {
  return gradeId.replace("K", "Grade ");
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. GRADE / SUBJECT / STATE CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

describe("GRADES constant", () => {
  test("has exactly 8 grades", () => {
    expect(GRADES).toHaveLength(8);
  });

  test("IDs run K1 through K8", () => {
    const ids = GRADES.map(g => g.id);
    expect(ids).toEqual(["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8"]);
  });

  test("labels run Grade 1 through Grade 8", () => {
    const labels = GRADES.map(g => g.label);
    expect(labels).toEqual([
      "Grade 1", "Grade 2", "Grade 3", "Grade 4",
      "Grade 5", "Grade 6", "Grade 7", "Grade 8",
    ]);
  });

  test("age ranges start at 7 for K1 (age 6-7)", () => {
    expect(GRADES[0].ages).toBe("6–7");
  });

  test("age ranges end at 13-14 for K8", () => {
    expect(GRADES[7].ages).toBe("13–14");
  });
});

describe("SUBJECTS constant", () => {
  test("has exactly 2 subjects", () => {
    expect(SUBJECTS).toHaveLength(2);
  });

  test("first subject is math", () => {
    expect(SUBJECTS[0].id).toBe("math");
  });

  test("second subject is english", () => {
    expect(SUBJECTS[1].id).toBe("english");
  });
});

describe("STATES constant", () => {
  test("Multi-State is always first", () => {
    expect(STATES[0].abbrev).toBe("Multi-State");
  });

  test("contains TX (Texas)", () => {
    const tx = STATES.find(s => s.abbrev === "TX");
    expect(tx).toBeDefined();
    expect(tx!.name).toBe("Texas (TEKS)");
  });

  test("has at least 5 states", () => {
    expect(STATES.length).toBeGreaterThanOrEqual(5);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. OPTION LETTER EXTRACTION
// ═══════════════════════════════════════════════════════════════════════════

describe("extractLetter", () => {
  test("extracts A from 'A. some text'", () => {
    expect(extractLetter("A. some text")).toBe("A");
  });

  test("extracts B from 'B. another answer'", () => {
    expect(extractLetter("B. another answer")).toBe("B");
  });

  test("extracts C from 'C. third option'", () => {
    expect(extractLetter("C. third option")).toBe("C");
  });

  test("extracts D from 'D. last option'", () => {
    expect(extractLetter("D. last option")).toBe("D");
  });

  test("returns empty string for empty option", () => {
    expect(extractLetter("")).toBe("");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. OPTION TEXT EXTRACTION
// ═══════════════════════════════════════════════════════════════════════════

describe("extractText", () => {
  test("strips 'A. ' prefix from option text", () => {
    expect(extractText("A. Some answer here")).toBe("Some answer here");
  });

  test("strips 'B. ' prefix", () => {
    expect(extractText("B. Another answer")).toBe("Another answer");
  });

  test("handles numeric answers", () => {
    expect(extractText("A. 42")).toBe("42");
  });

  test("handles options with periods in the answer text", () => {
    expect(extractText("A. 3.14 is pi")).toBe("3.14 is pi");
  });

  test("handles long option text", () => {
    const long = "A. " + "x".repeat(200);
    expect(extractText(long)).toBe("x".repeat(200));
  });

  test("returns empty string for 3-char option", () => {
    expect(extractText("A. ")).toBe("");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. ANSWER CORRECTNESS CHECK
// ═══════════════════════════════════════════════════════════════════════════

describe("isCorrect", () => {
  test("returns true when letters match", () => {
    expect(isCorrect("A", "A")).toBe(true);
  });

  test("returns false when letters differ", () => {
    expect(isCorrect("B", "A")).toBe(false);
  });

  test("returns false when answer is undefined (unanswered)", () => {
    expect(isCorrect(undefined, "A")).toBe(false);
  });

  test("is case-sensitive (frontend stores uppercase only)", () => {
    // The frontend always stores uppercase letters, so this should never happen
    // but we verify the function behavior
    expect(isCorrect("a", "A")).toBe(false);
  });

  test("works for all option letters", () => {
    for (const letter of ["A", "B", "C", "D"]) {
      expect(isCorrect(letter, letter)).toBe(true);
      const others = ["A", "B", "C", "D"].filter(l => l !== letter);
      for (const other of others) {
        expect(isCorrect(letter, other)).toBe(false);
      }
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. SCORE DISPLAY HELPERS
// ═══════════════════════════════════════════════════════════════════════════

describe("scoreToStatus", () => {
  test("score >= 0.85 is 'above'", () => {
    expect(scoreToStatus(0.85)).toBe("above");
    expect(scoreToStatus(1.00)).toBe("above");
    expect(scoreToStatus(0.90)).toBe("above");
  });

  test("score >= 0.70 and < 0.85 is 'at'", () => {
    expect(scoreToStatus(0.70)).toBe("at");
    expect(scoreToStatus(0.75)).toBe("at");
    expect(scoreToStatus(0.84)).toBe("at");
  });

  test("score >= 0.50 and < 0.70 is 'approaching'", () => {
    expect(scoreToStatus(0.50)).toBe("approaching");
    expect(scoreToStatus(0.60)).toBe("approaching");
    expect(scoreToStatus(0.69)).toBe("approaching");
  });

  test("score < 0.50 is 'below'", () => {
    expect(scoreToStatus(0.0)).toBe("below");
    expect(scoreToStatus(0.49)).toBe("below");
  });

  test("STATUS_LABEL has entries for all four statuses", () => {
    for (const status of ["above", "at", "approaching", "below"]) {
      expect(STATUS_LABEL[status]).toBeDefined();
      expect(STATUS_LABEL[status].length).toBeGreaterThan(0);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 6. API REQUEST PAYLOAD SHAPE — GENERATE
// ═══════════════════════════════════════════════════════════════════════════

describe("buildGeneratePayload", () => {
  test("includes all required fields", () => {
    const payload = buildGeneratePayload({
      grade: "K3", subject: "math", studentId: "s001", state: "Multi-State",
    });
    expect(payload).toHaveProperty("grade", "K3");
    expect(payload).toHaveProperty("subject", "math");
    expect(payload).toHaveProperty("student_id", "s001");
    expect(payload).toHaveProperty("state", "Multi-State");
    expect(payload).toHaveProperty("num_questions", 15);
  });

  test("defaults num_questions to 15", () => {
    const payload = buildGeneratePayload({
      grade: "K5", subject: "english", studentId: "s001", state: "TX",
    });
    expect(payload.num_questions).toBe(15);
  });

  test("respects custom num_questions", () => {
    const payload = buildGeneratePayload({
      grade: "K5", subject: "math", studentId: "s001", state: "CA", numQuestions: 20,
    });
    expect(payload.num_questions).toBe(20);
  });

  test("passes grade and state through unchanged", () => {
    const payload = buildGeneratePayload({
      grade: "K8", subject: "math", studentId: "test", state: "NY",
    });
    expect(payload.grade).toBe("K8");
    expect(payload.state).toBe("NY");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 7. API REQUEST PAYLOAD SHAPE — EVALUATE
// ═══════════════════════════════════════════════════════════════════════════

describe("buildEvaluatePayload", () => {
  const mockQuestions = [
    {
      id: "q1", question: "What is 2+2?", options: ["A. 3", "B. 4", "C. 5", "D. 6"],
      answer: "B", dok_level: 1, beta: -0.5, node_ref: "n1",
      category: "prerequisite", standard_code: "2.OA.1", standard_description: "Add",
    },
    {
      id: "q2", question: "Solve for x: x+1=5", options: ["A. 3", "B. 4", "C. 5", "D. 6"],
      answer: "B", dok_level: 2, beta: 0.0, node_ref: "n2",
      category: "target", standard_code: "3.OA.2", standard_description: "Equations",
    },
  ];

  test("top-level fields are correct", () => {
    const payload = buildEvaluatePayload({
      assessmentId: "abc-123", studentId: "s001", grade: "K3",
      subject: "math", state: "TX", questions: mockQuestions, answers: { q1: "B", q2: "A" },
    });
    expect(payload.assessment_id).toBe("abc-123");
    expect(payload.student_id).toBe("s001");
    expect(payload.grade).toBe("K3");
    expect(payload.subject).toBe("math");
    expect(payload.state).toBe("TX");
  });

  test("answers array has one entry per question", () => {
    const payload = buildEvaluatePayload({
      assessmentId: "x", studentId: "s", grade: "K5", subject: "math",
      state: "Multi-State", questions: mockQuestions, answers: { q1: "B", q2: "A" },
    });
    expect(payload.answers).toHaveLength(2);
  });

  test("student_answer is populated from answers dict", () => {
    const payload = buildEvaluatePayload({
      assessmentId: "x", studentId: "s", grade: "K5", subject: "math",
      state: "Multi-State", questions: mockQuestions, answers: { q1: "B", q2: "C" },
    });
    expect(payload.answers[0].student_answer).toBe("B");
    expect(payload.answers[1].student_answer).toBe("C");
  });

  test("student_answer defaults to empty string when not answered", () => {
    const payload = buildEvaluatePayload({
      assessmentId: "x", studentId: "s", grade: "K5", subject: "math",
      state: "Multi-State", questions: mockQuestions, answers: {},
    });
    expect(payload.answers[0].student_answer).toBe("");
    expect(payload.answers[1].student_answer).toBe("");
  });

  test("is_correct is true when student answer matches correct answer", () => {
    const payload = buildEvaluatePayload({
      assessmentId: "x", studentId: "s", grade: "K5", subject: "math",
      state: "Multi-State", questions: mockQuestions, answers: { q1: "B", q2: "B" },
    });
    expect(payload.answers[0].is_correct).toBe(true);  // q1 answer is B, student chose B
    expect(payload.answers[1].is_correct).toBe(true);  // q2 answer is B, student chose B
  });

  test("is_correct is false when student answer is wrong", () => {
    const payload = buildEvaluatePayload({
      assessmentId: "x", studentId: "s", grade: "K5", subject: "math",
      state: "Multi-State", questions: mockQuestions, answers: { q1: "A", q2: "C" },
    });
    expect(payload.answers[0].is_correct).toBe(false);
    expect(payload.answers[1].is_correct).toBe(false);
  });

  test("beta defaults to 0 when not present on question", () => {
    const qWithoutBeta = [{ ...mockQuestions[0] }];
    delete (qWithoutBeta[0] as any).beta;
    const payload = buildEvaluatePayload({
      assessmentId: "x", studentId: "s", grade: "K5", subject: "math",
      state: "Multi-State", questions: qWithoutBeta, answers: { q1: "B" },
    });
    expect(payload.answers[0].beta).toBe(0);
  });

  test("correct_answer (q.answer) is passed through", () => {
    const payload = buildEvaluatePayload({
      assessmentId: "x", studentId: "s", grade: "K5", subject: "math",
      state: "Multi-State", questions: mockQuestions, answers: { q1: "B", q2: "A" },
    });
    expect(payload.answers[0].answer).toBe("B");  // q1 correct answer
    expect(payload.answers[1].answer).toBe("B");  // q2 correct answer
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 8. API RESPONSE VALIDATION — GENERATE
// ═══════════════════════════════════════════════════════════════════════════

describe("isValidAssessmentResponse", () => {
  const validResponse = {
    assessment_id: "abc-123",
    grade: "K3", subject: "math", state: "Multi-State",
    framework: "CCSS", estimated_minutes: 25,
    num_questions: 12, prerequisite_count: 3, target_count: 9,
    questions: [{ id: "q1", question: "Test?", options: ["A. a", "B. b", "C. c", "D. d"], answer: "A" }],
  };

  test("accepts a valid response", () => {
    expect(isValidAssessmentResponse(validResponse)).toBe(true);
  });

  test("rejects null", () => {
    expect(isValidAssessmentResponse(null)).toBe(false);
  });

  test("rejects missing assessment_id", () => {
    const { assessment_id, ...rest } = validResponse;
    expect(isValidAssessmentResponse(rest)).toBe(false);
  });

  test("rejects non-array questions", () => {
    expect(isValidAssessmentResponse({ ...validResponse, questions: "not-an-array" })).toBe(false);
  });

  test("rejects missing num_questions", () => {
    const { num_questions, ...rest } = validResponse;
    expect(isValidAssessmentResponse(rest)).toBe(false);
  });

  test("rejects missing framework", () => {
    const { framework, ...rest } = validResponse;
    expect(isValidAssessmentResponse(rest)).toBe(false);
  });

  test("accepts response with empty questions array", () => {
    // Shape is valid even with 0 questions (backend handles this with 500)
    expect(isValidAssessmentResponse({ ...validResponse, questions: [], num_questions: 0 })).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 9. API RESPONSE VALIDATION — EVALUATE
// ═══════════════════════════════════════════════════════════════════════════

describe("isValidEvalResponse", () => {
  const validEval = {
    score: 0.75, correct: 9, total: 12,
    grade_status: "at", prerequisite_score: 0.80, target_score: 0.72,
    gap_count: 2, gap_exercises: [], recommendations: [], bkt_updates: [],
    theta: 0.5, theta_history: [], hard_blocked_count: 0, misconceptions: [],
    session_narrative: "Great job!", focus_concept: "Fractions",
  };

  test("accepts a valid eval response", () => {
    expect(isValidEvalResponse(validEval)).toBe(true);
  });

  test("rejects null", () => {
    expect(isValidEvalResponse(null)).toBe(false);
  });

  test("rejects missing score", () => {
    const { score, ...rest } = validEval;
    expect(isValidEvalResponse(rest)).toBe(false);
  });

  test("rejects missing grade_status", () => {
    const { grade_status, ...rest } = validEval;
    expect(isValidEvalResponse(rest)).toBe(false);
  });

  test("rejects non-array bkt_updates", () => {
    expect(isValidEvalResponse({ ...validEval, bkt_updates: null })).toBe(false);
  });

  test("score of 0 is valid", () => {
    expect(isValidEvalResponse({ ...validEval, score: 0, correct: 0 })).toBe(true);
  });

  test("score of 1 is valid", () => {
    expect(isValidEvalResponse({ ...validEval, score: 1.0, correct: 12 })).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 10. GRADE LABEL RENDERING
// ═══════════════════════════════════════════════════════════════════════════

describe("gradeIdToLabel", () => {
  test("K1 → 'Grade 1'", () => {
    expect(gradeIdToLabel("K1")).toBe("Grade 1");
  });

  test("K8 → 'Grade 8'", () => {
    expect(gradeIdToLabel("K8")).toBe("Grade 8");
  });

  test("K3 → 'Grade 3'", () => {
    expect(gradeIdToLabel("K3")).toBe("Grade 3");
  });

  test("all grade IDs produce distinct labels", () => {
    const labels = GRADES.map(g => gradeIdToLabel(g.id));
    const unique = new Set(labels);
    expect(unique.size).toBe(GRADES.length);
  });

  test("all grade labels contain the word 'Grade'", () => {
    GRADES.forEach(g => {
      expect(gradeIdToLabel(g.id)).toContain("Grade");
    });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 11. ANSWER COUNT / PROGRESS TRACKING
// ═══════════════════════════════════════════════════════════════════════════

describe("answer progress tracking", () => {
  test("answered count equals number of keys in answers dict", () => {
    const answers: Record<string, string> = { q1: "A", q2: "B", q3: "C" };
    expect(Object.keys(answers).length).toBe(3);
  });

  test("submit button should be disabled until all questions answered", () => {
    const answers: Record<string, string> = { q1: "A" };
    const total = 3;
    const answered = Object.keys(answers).length;
    const disabled = answered < total;
    expect(disabled).toBe(true);
  });

  test("submit button enabled when all questions answered", () => {
    const answers: Record<string, string> = { q1: "A", q2: "B", q3: "C" };
    const total = 3;
    const answered = Object.keys(answers).length;
    const disabled = answered < total;
    expect(disabled).toBe(false);
  });

  test("progress percentage computed correctly", () => {
    const answered = 6;
    const total = 12;
    const pct = (answered / total) * 100;
    expect(pct).toBe(50);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 12. GOTUTOR NAVIGATION PARAMS
// ═══════════════════════════════════════════════════════════════════════════

describe("goToTutor navigation params", () => {
  function buildTutorUrl(studentId: string, grade: string, subject: string): string {
    const params = new URLSearchParams({
      student_id: studentId,
      grade: grade.replace("K", ""),
      subject,
    });
    return `/tutor?${params.toString()}`;
  }

  test("K3 grade becomes '3' in the URL", () => {
    const url = buildTutorUrl("s001", "K3", "math");
    expect(url).toContain("grade=3");
  });

  test("K1 grade becomes '1' in the URL", () => {
    const url = buildTutorUrl("s001", "K1", "math");
    expect(url).toContain("grade=1");
  });

  test("student_id is included in params", () => {
    const url = buildTutorUrl("emma_001", "K5", "english");
    expect(url).toContain("student_id=emma_001");
  });

  test("subject is included in params", () => {
    const url = buildTutorUrl("s001", "K5", "english");
    expect(url).toContain("subject=english");
  });

  test("URL starts with /tutor", () => {
    const url = buildTutorUrl("s001", "K4", "math");
    expect(url.startsWith("/tutor")).toBe(true);
  });
});
