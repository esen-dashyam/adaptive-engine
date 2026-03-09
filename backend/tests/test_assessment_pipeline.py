"""
Unit tests for the Adaptive Learning Engine assessment pipeline.

Tests are organized by component:
  1. Rasch IRT math (no external deps)
  2. IRT Selector (no external deps)
  3. BKT Fitter (no external deps)
  4. AssessmentState model
  5. Vertex LLM JSON parsing (no external deps)
  6. score_answers agent node (no external deps)
  7. update_rasch agent node (no external deps)
  8. assessment_agent nodes with mocked Neo4j
  9. evaluation_agent nodes with mocked Neo4j
 10. LCA agent with mocked Neo4j
 11. Orchestrator graph construction
"""

from __future__ import annotations

import json
import math
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so imports work without installing
# ---------------------------------------------------------------------------
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# ===========================================================================
# 1. Rasch IRT math
# ===========================================================================

class TestRaschMath(unittest.TestCase):
    """Pure math functions in agents/rasch.py"""

    def setUp(self):
        from backend.app.agents.rasch import (
            p_correct, fisher_information, update_theta,
            grade_to_difficulty, RaschSession,
        )
        self.p_correct = p_correct
        self.fisher_information = fisher_information
        self.update_theta = update_theta
        self.grade_to_difficulty = grade_to_difficulty
        self.RaschSession = RaschSession

    # --- p_correct ---
    def test_p_correct_equal_theta_beta_is_half(self):
        """When θ == β, P(correct) == 0.5 (Rasch model definition)."""
        p = self.p_correct(0.0, 0.0)
        self.assertAlmostEqual(p, 0.5, places=5)

    def test_p_correct_high_theta_near_one(self):
        """Strong student facing easy item → P approaches 1."""
        p = self.p_correct(3.0, -3.0)
        self.assertGreater(p, 0.95)

    def test_p_correct_low_theta_near_zero(self):
        """Weak student facing hard item → P approaches 0."""
        p = self.p_correct(-3.0, 3.0)
        self.assertLess(p, 0.05)

    def test_p_correct_bounded(self):
        """P(correct) is always in (0, 1)."""
        for theta in [-5.0, -2.0, 0.0, 2.0, 5.0]:
            for beta in [-3.0, 0.0, 3.0]:
                p = self.p_correct(theta, beta)
                self.assertGreaterEqual(p, 0.0)
                self.assertLessEqual(p, 1.0)

    # --- fisher_information ---
    def test_fisher_info_maximum_at_theta_equals_beta(self):
        """Fisher Information is maximised when θ == β (value = 0.25)."""
        info = self.fisher_information(0.0, 0.0)
        self.assertAlmostEqual(info, 0.25, places=5)

    def test_fisher_info_decreases_away_from_beta(self):
        info_at = self.fisher_information(0.0, 0.0)
        info_off = self.fisher_information(2.0, 0.0)
        self.assertGreater(info_at, info_off)

    # --- update_theta ---
    def test_theta_increases_after_correct(self):
        theta0 = 0.0
        theta1 = self.update_theta(theta0, 0.0, correct=True)
        self.assertGreater(theta1, theta0)

    def test_theta_decreases_after_incorrect(self):
        theta0 = 0.0
        theta1 = self.update_theta(theta0, 0.0, correct=False)
        self.assertLess(theta1, theta0)

    def test_theta_clamped_to_bounds(self):
        from backend.app.agents.rasch import THETA_MIN, THETA_MAX
        theta = self.update_theta(THETA_MAX, -5.0, correct=True)
        self.assertLessEqual(theta, THETA_MAX)
        theta = self.update_theta(THETA_MIN, 5.0, correct=False)
        self.assertGreaterEqual(theta, THETA_MIN)

    # --- grade_to_difficulty ---
    def test_grade_difficulty_ordering(self):
        """Higher grade should have higher difficulty logit."""
        b1 = self.grade_to_difficulty("1")
        b5 = self.grade_to_difficulty("5")
        b8 = self.grade_to_difficulty("8")
        self.assertLess(b1, b5)
        self.assertLess(b5, b8)

    def test_prereq_easier_than_target(self):
        b_target = self.grade_to_difficulty("3", dok_level=2, category="target")
        b_prereq = self.grade_to_difficulty("3", dok_level=2, category="prerequisite")
        self.assertLess(b_prereq, b_target)

    def test_dok_offset(self):
        b_dok1 = self.grade_to_difficulty("5", dok_level=1)
        b_dok3 = self.grade_to_difficulty("5", dok_level=3)
        self.assertLess(b_dok1, b_dok3)

    def test_k_prefix_grade(self):
        """Grades like 'K3' should be treated as 'K'."""
        b = self.grade_to_difficulty("k", dok_level=2)
        self.assertEqual(b, -3.0)

    # --- RaschSession ---
    def test_rasch_session_tracks_theta(self):
        session = self.RaschSession(initial_theta=0.0)
        self.assertEqual(session.theta, 0.0)
        session.record("q1", 0.0, correct=True)
        self.assertNotEqual(session.theta, 0.0)

    def test_rasch_session_history_grows(self):
        session = self.RaschSession(initial_theta=0.0)
        session.record("q1", 0.0, True)
        session.record("q2", 0.5, False)
        self.assertEqual(len(session.history), 2)

    def test_rasch_se_decreases_with_more_answers(self):
        session = self.RaschSession(initial_theta=0.0)
        session.record("q1", 0.0, True)
        se_1 = session.se
        session.record("q2", 0.3, False)
        se_2 = session.se
        self.assertLess(se_2, se_1)

    def test_rasch_to_dict(self):
        session = self.RaschSession(initial_theta=0.0)
        session.record("q1", 0.0, True)
        d = session.to_dict()
        self.assertIn("theta", d)
        self.assertIn("se", d)
        self.assertIn("grade_equivalent", d)
        self.assertIn("n_items", d)
        self.assertEqual(d["n_items"], 1)

    def test_grade_equivalent_is_string(self):
        session = self.RaschSession(initial_theta=0.0)
        session.record("q1", 0.0, True)
        ge = session.grade_equivalent
        self.assertIsInstance(ge, str)
        self.assertTrue(ge.startswith("Grade"))


# ===========================================================================
# 2. IRT Selector
# ===========================================================================

class TestIRTSelector(unittest.TestCase):
    """Tests for agents/irt_selector.py"""

    def setUp(self):
        from backend.app.agents.irt_selector import (
            rank_nodes_by_information,
            assign_difficulties,
            build_prerequisite_map,
            select_next_node,
        )
        self.rank_nodes = rank_nodes_by_information
        self.assign_difficulties = assign_difficulties
        self.build_prerequisite_map = build_prerequisite_map
        self.select_next_node = select_next_node

        self.sample_nodes = [
            {"identifier": "n1", "grade": "3", "dok_level": 2, "category": "target"},
            {"identifier": "n2", "grade": "5", "dok_level": 2, "category": "target"},
            {"identifier": "n3", "grade": "2", "dok_level": 1, "category": "prerequisite"},
        ]

    def test_rank_returns_all_nodes(self):
        ranked = self.rank_nodes(0.0, self.sample_nodes)
        self.assertEqual(len(ranked), 3)

    def test_rank_is_sorted_descending(self):
        ranked = self.rank_nodes(0.0, self.sample_nodes)
        infos = [info for info, _ in ranked]
        self.assertEqual(infos, sorted(infos, reverse=True))

    def test_rank_returns_tuples(self):
        ranked = self.rank_nodes(0.0, self.sample_nodes)
        for item in ranked:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)
            self.assertIsInstance(item[0], float)
            self.assertIsInstance(item[1], dict)

    def test_assign_difficulties_keys(self):
        diffs = self.assign_difficulties(self.sample_nodes)
        self.assertIn("n1", diffs)
        self.assertIn("n2", diffs)
        self.assertIn("n3", diffs)

    def test_assign_difficulties_values_are_floats(self):
        diffs = self.assign_difficulties(self.sample_nodes)
        for v in diffs.values():
            self.assertIsInstance(v, float)

    def test_assign_difficulties_skips_nodes_without_identifier(self):
        nodes = [{"grade": "3", "dok_level": 2, "category": "target"}]  # no identifier
        diffs = self.assign_difficulties(nodes)
        self.assertEqual(diffs, {})

    def test_build_prerequisite_map_empty(self):
        pmap = self.build_prerequisite_map(self.sample_nodes)
        self.assertIsInstance(pmap, dict)

    def test_build_prerequisite_map_prerequisite_for(self):
        nodes = [
            {"identifier": "parent", "prerequisite_for": ["child"]},
            {"identifier": "child"},
        ]
        pmap = self.build_prerequisite_map(nodes)
        self.assertIn("child", pmap)
        self.assertIn("parent", pmap["child"])

    def test_build_prerequisite_map_prerequisites(self):
        nodes = [
            {"identifier": "child", "prerequisites": ["parent"]},
            {"identifier": "parent"},
        ]
        pmap = self.build_prerequisite_map(nodes)
        self.assertIn("child", pmap)
        self.assertIn("parent", pmap["child"])

    def test_select_next_node_returns_best(self):
        node = self.select_next_node(
            theta=0.0,
            candidates=self.sample_nodes,
            already_asked=set(),
            failed_node_ids=set(),
            prerequisite_map={},
        )
        self.assertIsNotNone(node)
        self.assertIn("identifier", node)

    def test_select_next_node_skips_already_asked(self):
        already_asked = {"n1", "n2", "n3"}
        node = self.select_next_node(
            theta=0.0,
            candidates=self.sample_nodes,
            already_asked=already_asked,
            failed_node_ids=set(),
            prerequisite_map={},
        )
        self.assertIsNone(node)

    def test_select_next_node_skips_blocked(self):
        # n2 has n1 as prerequisite, n1 was failed → n2 should be blocked
        nodes = [
            {"identifier": "n1", "grade": "3", "dok_level": 2, "category": "target"},
            {"identifier": "n2", "grade": "5", "dok_level": 2, "category": "target"},
        ]
        pmap = {"n2": ["n1"]}
        node = self.select_next_node(
            theta=0.0,
            candidates=nodes,
            already_asked={"n1"},
            failed_node_ids={"n1"},
            prerequisite_map=pmap,
        )
        # n2 is blocked because n1 (its prereq) was failed; n1 is already asked → None
        self.assertIsNone(node)


# ===========================================================================
# 3. BKT Fitter
# ===========================================================================

class TestBKTFitter(unittest.TestCase):
    """Tests for student/bkt_fitter.py"""

    def setUp(self):
        from backend.app.student.bkt_fitter import (
            fit_skill, _emit, _forward, _backward,
            DEFAULT_P_INIT, DEFAULT_P_TRANSIT, DEFAULT_P_SLIP, DEFAULT_P_GUESS,
            FittedBKTParams,
        )
        self.fit_skill = fit_skill
        self._emit = _emit
        self._forward = _forward
        self._backward = _backward
        self.DEFAULT_P_INIT = DEFAULT_P_INIT
        self.DEFAULT_P_TRANSIT = DEFAULT_P_TRANSIT
        self.DEFAULT_P_SLIP = DEFAULT_P_SLIP
        self.DEFAULT_P_GUESS = DEFAULT_P_GUESS
        self.FittedBKTParams = FittedBKTParams

    def test_emit_correct_mastered(self):
        """P(correct | mastered) = 1 - slip"""
        p = self._emit(True, state=1, p_slip=0.1, p_guess=0.25)
        self.assertAlmostEqual(p, 0.9)

    def test_emit_incorrect_mastered(self):
        """P(incorrect | mastered) = slip"""
        p = self._emit(False, state=1, p_slip=0.1, p_guess=0.25)
        self.assertAlmostEqual(p, 0.1)

    def test_emit_correct_unmastered(self):
        """P(correct | unmastered) = guess"""
        p = self._emit(True, state=0, p_slip=0.1, p_guess=0.25)
        self.assertAlmostEqual(p, 0.25)

    def test_emit_incorrect_unmastered(self):
        """P(incorrect | unmastered) = 1 - guess"""
        p = self._emit(False, state=0, p_slip=0.1, p_guess=0.25)
        self.assertAlmostEqual(p, 0.75)

    def test_forward_returns_correct_length(self):
        seq = [True, False, True, True]
        alpha, scales = self._forward(
            seq, p_init=0.1, p_transit=0.1, p_slip=0.08, p_guess=0.25
        )
        self.assertIsNotNone(alpha)
        self.assertEqual(len(alpha), len(seq))
        self.assertEqual(len(scales), len(seq))

    def test_forward_alpha_sums_to_one(self):
        seq = [True, False, True]
        alpha, scales = self._forward(
            seq, p_init=0.1, p_transit=0.1, p_slip=0.08, p_guess=0.25
        )
        for row in alpha:
            self.assertAlmostEqual(sum(row), 1.0, places=5)

    def test_backward_returns_correct_length(self):
        seq = [True, False, True, True]
        alpha, scales = self._forward(
            seq, p_init=0.1, p_transit=0.1, p_slip=0.08, p_guess=0.25
        )
        beta = self._backward(seq, p_transit=0.1, p_slip=0.08, p_guess=0.25, scales=scales)
        self.assertEqual(len(beta), len(seq))

    def test_fit_skill_too_few_sequences(self):
        """With fewer than 2 usable sequences, returns defaults."""
        result = self.fit_skill([[True]])  # only 1 sequence with length 1
        self.assertEqual(result.p_init, self.DEFAULT_P_INIT)

    def test_fit_skill_returns_fitted_params(self):
        sequences = [
            [True, True, True, False, True],
            [False, True, True, True, True],
            [True, False, True, True, False],
        ]
        result = self.fit_skill(sequences)
        self.assertIsInstance(result, self.FittedBKTParams)
        self.assertGreater(result.n_sequences, 0)

    def test_fit_skill_params_in_valid_range(self):
        sequences = [
            [True, True, False, True, True],
            [False, False, True, True, True],
        ]
        result = self.fit_skill(sequences)
        self.assertGreater(result.p_init, 0.0)
        self.assertLessEqual(result.p_init, 0.5)
        self.assertGreater(result.p_slip, 0.0)
        self.assertLessEqual(result.p_slip, 0.35)
        self.assertGreater(result.p_guess, 0.0)
        self.assertLessEqual(result.p_guess, 0.45)

    def test_fit_skill_all_correct_high_guess(self):
        """All-correct sequences → model sees high performance."""
        sequences = [[True] * 5 for _ in range(5)]
        result = self.fit_skill(sequences)
        # After all-correct data, p_guess should be fairly high
        self.assertGreater(result.p_guess, 0.1)

    def test_default_constants_sensible(self):
        self.assertGreater(self.DEFAULT_P_INIT, 0.0)
        self.assertLess(self.DEFAULT_P_INIT, 1.0)
        self.assertGreater(self.DEFAULT_P_TRANSIT, 0.0)
        self.assertLess(self.DEFAULT_P_TRANSIT, 1.0)


# ===========================================================================
# 4. AssessmentState model
# ===========================================================================

class TestAssessmentState(unittest.TestCase):
    """Tests for agent/state.py"""

    def setUp(self):
        from backend.app.agent.state import AssessmentState
        self.AssessmentState = AssessmentState

    def test_default_state(self):
        state = self.AssessmentState()
        self.assertEqual(state.student_id, "")
        self.assertEqual(state.theta, 0.0)
        self.assertEqual(state.score, 0.0)
        self.assertEqual(state.questions, [])
        self.assertEqual(state.results, [])
        self.assertEqual(state.gaps, [])

    def test_custom_state(self):
        state = self.AssessmentState(
            student_id="s123",
            grade="5",
            subject="math",
            theta=1.5,
        )
        self.assertEqual(state.student_id, "s123")
        self.assertEqual(state.theta, 1.5)
        self.assertEqual(state.grade, "5")

    def test_state_update(self):
        state = self.AssessmentState()
        updated = state.model_copy(update={"theta": 2.0, "score": 0.8})
        self.assertEqual(updated.theta, 2.0)
        self.assertEqual(updated.score, 0.8)
        self.assertEqual(state.theta, 0.0)  # original unchanged

    def test_state_serialization(self):
        state = self.AssessmentState(student_id="abc", grade="3")
        d = state.model_dump()
        self.assertIn("student_id", d)
        self.assertIn("grade", d)
        self.assertIn("theta", d)


# ===========================================================================
# 5. VertexLLM JSON parsing (no external deps)
# ===========================================================================

class TestVertexLLMParsing(unittest.TestCase):
    """Tests for VertexLLM._parse_json — pure parsing, no API calls."""

    def setUp(self):
        from backend.app.agents.vertex_llm import VertexLLM
        self.parse = VertexLLM._parse_json

    def test_parse_plain_array(self):
        text = '[{"id": "1", "q": "test"}]'
        result = self.parse(text)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)

    def test_parse_plain_dict(self):
        text = '{"key": "value"}'
        result = self.parse(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["key"], "value")

    def test_parse_markdown_fenced(self):
        text = "```json\n[{\"id\": \"1\"}]\n```"
        result = self.parse(text)
        self.assertIsInstance(result, list)

    def test_parse_empty_string_returns_none(self):
        result = self.parse("")
        self.assertIsNone(result)

    def test_parse_none_returns_none(self):
        result = self.parse(None)
        self.assertIsNone(result)

    def test_parse_invalid_json_returns_none(self):
        result = self.parse("not valid json at all")
        self.assertIsNone(result)

    def test_parse_nested_json(self):
        text = '[{"id": "1", "options": ["A. foo", "B. bar"]}]'
        result = self.parse(text)
        self.assertIsInstance(result, list)
        self.assertIn("options", result[0])

    def test_parse_json_with_whitespace(self):
        text = '  \n  [{"id": "x"}]  \n  '
        result = self.parse(text)
        self.assertIsInstance(result, list)

    def test_parse_multiline_json(self):
        text = json.dumps([{"question": "line1\nline2", "answer": "A"}])
        result = self.parse(text)
        self.assertIsInstance(result, list)

    def test_generate_json_unwraps_dict_wrapper(self):
        """generate_json should unwrap {"questions": [...]} to [...]."""
        from backend.app.agents.vertex_llm import VertexLLM
        llm = VertexLLM()
        wrapped = json.dumps({"questions": [{"id": "1"}]})
        with patch.object(llm, 'generate', return_value=wrapped):
            result = llm.generate_json("prompt")
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["id"], "1")


# ===========================================================================
# 6. score_answers agent node
# ===========================================================================

class TestScoreAnswers(unittest.TestCase):
    """Tests for evaluation_agent.score_answers — no external deps."""

    def setUp(self):
        from backend.app.agents.evaluation_agent import score_answers
        from backend.app.agent.state import AssessmentState
        self.score_answers = score_answers
        self.AssessmentState = AssessmentState

    def _make_state(self, questions, submitted_answers):
        return self.AssessmentState(
            questions=questions,
            submitted_answers=submitted_answers,
        )

    def test_all_correct(self):
        questions = [
            {"id": "q1", "answer": "A", "question": "Q1", "options": [], "category": "target", "dok_level": 2, "standard_code": "1.OA.1", "node_ref": "n1", "beta": 0.0},
        ]
        answers = [{"question_id": "q1", "selected_answer": "A"}]
        state = self._make_state(questions, answers)
        result = self.score_answers(state)
        self.assertAlmostEqual(result["score"], 1.0)
        self.assertTrue(result["results"][0]["is_correct"])

    def test_all_incorrect(self):
        questions = [
            {"id": "q1", "answer": "A", "question": "Q1", "options": [], "category": "target", "dok_level": 2, "standard_code": "1.OA.1", "node_ref": "n1", "beta": 0.0},
        ]
        answers = [{"question_id": "q1", "selected_answer": "B"}]
        state = self._make_state(questions, answers)
        result = self.score_answers(state)
        self.assertAlmostEqual(result["score"], 0.0)
        self.assertFalse(result["results"][0]["is_correct"])

    def test_case_insensitive_answers(self):
        questions = [
            {"id": "q1", "answer": "A", "question": "Q1", "options": [], "category": "target", "dok_level": 2, "standard_code": "1.OA.1", "node_ref": "n1", "beta": 0.0},
        ]
        answers = [{"question_id": "q1", "selected_answer": "a"}]
        state = self._make_state(questions, answers)
        result = self.score_answers(state)
        self.assertTrue(result["results"][0]["is_correct"])

    def test_mixed_correct(self):
        questions = [
            {"id": "q1", "answer": "A", "question": "Q1", "options": [], "category": "target", "dok_level": 2, "standard_code": "1.OA.1", "node_ref": "n1", "beta": 0.0},
            {"id": "q2", "answer": "B", "question": "Q2", "options": [], "category": "target", "dok_level": 2, "standard_code": "1.OA.2", "node_ref": "n2", "beta": 0.0},
        ]
        answers = [
            {"question_id": "q1", "selected_answer": "A"},
            {"question_id": "q2", "selected_answer": "C"},
        ]
        state = self._make_state(questions, answers)
        result = self.score_answers(state)
        self.assertAlmostEqual(result["score"], 0.5)

    def test_empty_answers(self):
        state = self._make_state([], [])
        result = self.score_answers(state)
        self.assertEqual(result["results"], [])
        self.assertAlmostEqual(result["score"], 0.0)

    def test_unknown_question_id_handled(self):
        """Answering a question_id not in questions should not crash."""
        state = self._make_state([], [{"question_id": "unknown", "selected_answer": "A"}])
        result = self.score_answers(state)
        self.assertIsInstance(result["results"], list)

    def test_results_have_required_keys(self):
        questions = [
            {"id": "q1", "answer": "A", "question": "Q1", "options": ["A. x"], "category": "target", "dok_level": 2, "standard_code": "1.OA.1", "node_ref": "n1", "beta": -0.5},
        ]
        answers = [{"question_id": "q1", "selected_answer": "A"}]
        state = self._make_state(questions, answers)
        result = self.score_answers(state)
        r = result["results"][0]
        for key in ["question_id", "is_correct", "correct_answer", "student_answer", "beta", "node_ref"]:
            self.assertIn(key, r)


# ===========================================================================
# 7. update_rasch agent node
# ===========================================================================

class TestUpdateRasch(unittest.TestCase):
    """Tests for evaluation_agent.update_rasch — no external deps."""

    def setUp(self):
        from backend.app.agents.evaluation_agent import update_rasch
        from backend.app.agent.state import AssessmentState
        self.update_rasch = update_rasch
        self.AssessmentState = AssessmentState

    def _make_state_with_results(self, results, theta=0.0):
        return self.AssessmentState(results=results, theta=theta)

    def test_theta_increases_after_all_correct(self):
        results = [
            {"question_id": "q1", "is_correct": True,  "beta": 0.0},
            {"question_id": "q2", "is_correct": True,  "beta": 0.0},
            {"question_id": "q3", "is_correct": True,  "beta": 0.0},
        ]
        state = self._make_state_with_results(results)
        out = self.update_rasch(state)
        self.assertGreater(out["theta"], 0.0)

    def test_theta_decreases_after_all_incorrect(self):
        results = [
            {"question_id": "q1", "is_correct": False, "beta": 0.0},
            {"question_id": "q2", "is_correct": False, "beta": 0.0},
            {"question_id": "q3", "is_correct": False, "beta": 0.0},
        ]
        state = self._make_state_with_results(results)
        out = self.update_rasch(state)
        self.assertLess(out["theta"], 0.0)

    def test_results_have_theta_keys(self):
        results = [{"question_id": "q1", "is_correct": True, "beta": 0.0}]
        state = self._make_state_with_results(results)
        out = self.update_rasch(state)
        self.assertIn("theta_before", out["results"][0])
        self.assertIn("theta_after",  out["results"][0])

    def test_theta_history_length(self):
        results = [
            {"question_id": "q1", "is_correct": True,  "beta": 0.0},
            {"question_id": "q2", "is_correct": False, "beta": 0.5},
        ]
        state = self._make_state_with_results(results)
        out = self.update_rasch(state)
        self.assertEqual(len(out["theta_history"]), 2)

    def test_empty_results(self):
        state = self._make_state_with_results([])
        out = self.update_rasch(state)
        self.assertEqual(out["theta"], 0.0)
        self.assertEqual(out["theta_history"], [])


# ===========================================================================
# 8. assessment_agent nodes with mocked Neo4j
# ===========================================================================

class TestSelectStandardsIRT(unittest.TestCase):
    """Tests for assessment_agent.select_standards_irt with mocked Neo4j."""

    def _make_state(self, grade="5", subject="math", theta=0.0, state_jur="Multi-State"):
        from backend.app.agent.state import AssessmentState
        return AssessmentState(
            grade=grade, subject=subject, theta=theta,
            state_jurisdiction=state_jur, student_id="test_student"
        )

    def _make_node(self, identifier, code="5.NBT.A.1", description="A standard desc " * 3):
        return {
            "identifier": identifier,
            "statementCode": code,
            "description": description,
            "gradeLevelList": ["5"],
        }

    def _make_mock_session(self, target_nodes, prereq_nodes, edges=None):
        mock_session = MagicMock()

        call_count = [0]
        def run_side_effect(query, **kwargs):
            call_count[0] += 1
            mock_result = MagicMock()
            # Last call is the edge query
            if "BUILDS_TOWARDS" in query and "RETURN a.identifier" in query:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
                mock_result.data = MagicMock(return_value=[])
                return mock_result
            # Target grade queries
            grade = kwargs.get("grade", "")
            if grade == "5":
                mock_result.__iter__ = MagicMock(return_value=iter([
                    MagicMock(data=lambda: n) for n in target_nodes
                ]))
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([
                    MagicMock(data=lambda: n) for n in prereq_nodes
                ]))
            return mock_result

        mock_session.run = MagicMock(side_effect=run_side_effect)
        return mock_session

    def test_returns_all_nodes_key(self):
        from backend.app.agents.assessment_agent import select_standards_irt

        target_nodes = [self._make_node(f"n{i}", f"5.NBT.A.{i}") for i in range(5)]
        prereq_nodes = [self._make_node(f"p{i}", f"4.NBT.A.{i}") for i in range(3)]

        mock_driver = MagicMock()
        mock_session = self._make_mock_session(target_nodes, prereq_nodes)
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("backend.app.agents.assessment_agent._neo4j", return_value=mock_driver):
            state = self._make_state()
            result = select_standards_irt(state)

        self.assertIn("all_nodes", result)
        self.assertIn("target_standards", result)
        self.assertIn("prerequisite_standards", result)
        self.assertIn("question_difficulties", result)

    def test_no_standards_returns_error(self):
        from backend.app.agents.assessment_agent import select_standards_irt

        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_session.run.return_value = MagicMock(__iter__=MagicMock(return_value=iter([])))
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("backend.app.agents.assessment_agent._neo4j", return_value=mock_driver):
            state = self._make_state()
            result = select_standards_irt(state)

        self.assertIn("error", result)

    def test_question_difficulties_are_floats(self):
        from backend.app.agents.assessment_agent import select_standards_irt

        target_nodes = [self._make_node(f"n{i}") for i in range(4)]
        prereq_nodes = [self._make_node(f"p{i}") for i in range(2)]

        mock_driver = MagicMock()
        mock_session = self._make_mock_session(target_nodes, prereq_nodes)
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("backend.app.agents.assessment_agent._neo4j", return_value=mock_driver):
            result = select_standards_irt(self._make_state())

        for v in result.get("question_difficulties", {}).values():
            self.assertIsInstance(v, float)


# ===========================================================================
# 9. evaluate update_bkt with mocked Neo4j
# ===========================================================================

class TestUpdateBKT(unittest.TestCase):
    """Tests for evaluation_agent.update_bkt with mocked Neo4j."""

    def _make_state(self, results):
        from backend.app.agent.state import AssessmentState
        return AssessmentState(
            student_id="test_student",
            results=results,
            misconception_weights={},
        )

    def _make_mock_neo4j(self, p_mastery=0.5, p_slip=0.08, p_guess=0.25, p_transit=0.10):
        mock_driver = MagicMock()
        mock_session = MagicMock()

        def run_side_effect(query, **kwargs):
            mock_result = MagicMock()
            if "RETURN coalesce(sk.p_mastery" in query:
                mock_result.single.return_value = {
                    "p_mastery": p_mastery,
                    "p_slip": p_slip,
                    "p_guess": p_guess,
                    "p_transit": p_transit,
                }
            elif "RETURN coalesce(r.attempts,0)" in query:
                mock_result.single.return_value = {"att": 2, "cor": 1}
            else:
                mock_result.single.return_value = None
            return mock_result

        mock_session.run = MagicMock(side_effect=run_side_effect)
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        return mock_driver

    def test_mastery_updates_populated(self):
        from backend.app.agents.evaluation_agent import update_bkt

        results = [
            {"question_id": "q1", "is_correct": True,  "node_ref": "n1", "standard_code": "5.NBT.1",
             "beta": 0.0, "mastery_before": 0.0, "mastery_after": 0.0},
        ]
        state = self._make_state(results)
        mock_driver = self._make_mock_neo4j()

        with patch("backend.app.agents.evaluation_agent._neo4j", return_value=mock_driver):
            out = update_bkt(state)

        self.assertIn("mastery_updates", out)
        self.assertIn("n1", out["mastery_updates"])

    def test_mastery_increases_after_correct(self):
        from backend.app.agents.evaluation_agent import update_bkt

        results = [
            {"question_id": "q1", "is_correct": True, "node_ref": "n1", "standard_code": "5.NBT.1",
             "beta": 0.0, "mastery_before": 0.0, "mastery_after": 0.0},
        ]
        state = self._make_state(results)
        mock_driver = self._make_mock_neo4j(p_mastery=0.4)

        with patch("backend.app.agents.evaluation_agent._neo4j", return_value=mock_driver):
            out = update_bkt(state)

        new_mastery = out["mastery_updates"].get("n1", 0.0)
        self.assertGreater(new_mastery, 0.4)

    def test_skips_node_without_node_ref(self):
        from backend.app.agents.evaluation_agent import update_bkt

        results = [
            {"question_id": "q1", "is_correct": True, "node_ref": "", "standard_code": "5.NBT.1",
             "beta": 0.0, "mastery_before": 0.0, "mastery_after": 0.0},
        ]
        state = self._make_state(results)
        mock_driver = self._make_mock_neo4j()

        with patch("backend.app.agents.evaluation_agent._neo4j", return_value=mock_driver):
            out = update_bkt(state)

        self.assertEqual(out["mastery_updates"], {})

    def test_results_get_mastery_fields(self):
        from backend.app.agents.evaluation_agent import update_bkt

        results = [
            {"question_id": "q1", "is_correct": True, "node_ref": "n1", "standard_code": "5.NBT.1",
             "beta": 0.0, "mastery_before": 0.0, "mastery_after": 0.0},
        ]
        state = self._make_state(results)
        mock_driver = self._make_mock_neo4j()

        with patch("backend.app.agents.evaluation_agent._neo4j", return_value=mock_driver):
            out = update_bkt(state)

        r = out["results"][0]
        self.assertIn("mastery_before", r)
        self.assertIn("mastery_after", r)


# ===========================================================================
# 10. LCA agent
# ===========================================================================

class TestLCAAgent(unittest.TestCase):
    """Tests for agents/lca_agent.py"""

    def setUp(self):
        from backend.app.agents.lca_agent import find_lca
        self.find_lca = find_lca

    def _make_driver(self, row_data):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.single.return_value = row_data
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        return mock_driver

    def test_returns_none_when_no_ancestor(self):
        driver = self._make_driver(None)
        result = self.find_lca(driver, student_id="s1", node_id="n1")
        self.assertIsNone(result)

    def test_returns_dict_when_ancestor_found(self):
        row = {
            "node_id": "ancestor_n",
            "code": "3.OA.A.1",
            "description": "Multiply",
            "hops": 2,
            "p_mastery": 0.97,
        }
        driver = self._make_driver(row)
        result = self.find_lca(driver, student_id="s1", node_id="n1")
        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "3.OA.A.1")
        self.assertEqual(result["hops"], 2)

    def test_handles_neo4j_exception_gracefully(self):
        mock_driver = MagicMock()
        mock_driver.session.side_effect = Exception("Connection refused")
        result = self.find_lca(mock_driver, student_id="s1", node_id="n1")
        self.assertIsNone(result)


# ===========================================================================
# 11. Orchestrator graph construction
# ===========================================================================

class TestOrchestratorGraphs(unittest.TestCase):
    """Tests for orchestrator.py — graph builds compile without errors."""

    def test_build_phase_a_compiles(self):
        from backend.app.agents.orchestrator import build_phase_a
        graph = build_phase_a()
        compiled = graph.compile()
        self.assertIsNotNone(compiled)

    def test_build_phase_b_compiles(self):
        from backend.app.agents.orchestrator import build_phase_b
        graph = build_phase_b()
        compiled = graph.compile()
        self.assertIsNotNone(compiled)

    def test_get_phase_a_singleton(self):
        from backend.app.agents.orchestrator import get_phase_a
        g1 = get_phase_a()
        g2 = get_phase_a()
        self.assertIs(g1, g2)

    def test_get_phase_b_singleton(self):
        from backend.app.agents.orchestrator import get_phase_b
        g1 = get_phase_b()
        g2 = get_phase_b()
        self.assertIs(g1, g2)


# ===========================================================================
# 12. _parse_grade_subject helper
# ===========================================================================

class TestParseGradeSubject(unittest.TestCase):
    """Tests for assessment_agent._parse_grade_subject."""

    def setUp(self):
        from backend.app.agents.assessment_agent import _parse_grade_subject
        self.parse = _parse_grade_subject

    def test_k5_math_multistate(self):
        grade_num, prereq_grade, subject_name, jurisdiction = self.parse("K5", "math", "Multi-State")
        self.assertEqual(grade_num, "5")
        self.assertEqual(prereq_grade, "4")
        self.assertEqual(subject_name, "Mathematics")
        self.assertEqual(jurisdiction, "Multi-State")

    def test_state_abbreviation_expanded(self):
        _, _, _, jurisdiction = self.parse("3", "math", "TX")
        self.assertEqual(jurisdiction, "Texas")

    def test_unknown_state_passes_through(self):
        _, _, _, jurisdiction = self.parse("3", "math", "ZZ")
        self.assertEqual(jurisdiction, "ZZ")

    def test_english_subject(self):
        _, _, subject_name, _ = self.parse("4", "english", "Multi-State")
        self.assertEqual(subject_name, "English Language Arts")

    def test_grade_1_prereq_is_1(self):
        """Grade 1's prereq grade should clamp to 1, not 0."""
        _, prereq_grade, _, _ = self.parse("1", "math", "Multi-State")
        self.assertEqual(prereq_grade, "1")

    def test_invalid_grade_defaults_to_1(self):
        grade_num, _, _, _ = self.parse("abc", "math", "Multi-State")
        self.assertEqual(grade_num, "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
