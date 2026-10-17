"""Google Gemini API integration for explanation refinement and AI Tutor assistant.

Preserves deterministic mathematical verification and existing diagnostic decisions
as the source of truth, using Gemini exclusively for natural-language quality
enhancement and broad educational conversations.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("edumind.gemini")

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    genai = None  # type: ignore
    types = None  # type: ignore
    APIError = Exception  # type: ignore


DEFAULT_MODEL = "gemini-flash-lite-latest"
FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-3.8-flash", "gemini-flash-latest", "gemini-pro-latest"]

EXPLANATION_SYSTEM_INSTRUCTION = """You are EduMind AI's explanation refinement specialist for middle-school mathematics.
Your task is to take an already-diagnosed mathematical mistake and the verified learning intervention, and turn it into a clearer, more natural, supportive, student-friendly explanation.

STRICT CONSTRAINTS:
1. Deterministic mathematical verification and rule/ML diagnosis provided to you are absolute truth. NEVER invent or change the correct answer, the student's answer, or the misconception diagnosis.
2. If diagnosis status is "targeted" with a diagnosed pattern and supporting evidence, explain why that specific error is incorrect and how to fix it.
3. If diagnosis status is "general" or evidence is uncertain, provide an uncertainty-aware, general review of the method. Do NOT guess an unsupported misconception.
4. Use clear, encouraging, student-friendly language suitable for the student's difficulty level.
5. Explain the concept and show the correct method step-by-step.
6. Stay aligned with the existing intervention's steps and worked example.
7. NEVER add unsolicited diagnostic follow-up questions, quizzes, or prompts at the end (e.g. do NOT ask "What is 1/2 + 1/4?" or "Can you try another one?").
8. Format your response cleanly:
WHY: <Refined 2-3 sentences explaining what happened and why it is mathematically incorrect>
STEPS:
1. <First step>
2. <Second step>
3. <Third step>
"""

TUTOR_SYSTEM_INSTRUCTION = """You are EduMind AI Tutor, an inquisitive, supportive educational guide. You help students explore concepts both inside and outside their regular syllabus. You are NOT just a math tutor; you help learners discover out-of-the-box topics across science, technology, computer science (like relational algebra, databases, algorithms), astronomy, history, philosophy, logic, and everyday curiosities. Answer the student's actual question directly using clear, age-appropriate language based on the selected difficulty level. Explain concepts step by step when useful, use examples and real-world analogies when appropriate, and encourage independent understanding without forcing a quiz. Do not automatically end responses with follow-up questions, suggested quizzes, or offers to explain more. Ask for clarification only when genuinely necessary. Be honest about uncertainty, correct mistakes transparently, and never claim that a response has been verified by a teacher when it has not."""

# Trailing questions pattern to safeguard against unsolicited quiz/follow-up loops
TRAILING_QUESTION_PATTERN = re.compile(
    r"(?:\n|\r|\s)*(?:(?:Before we move on,?\s*)?(?:let me ask you|can you tell me|what do you think|do you want to|would you like|shall we try|do you have any questions|are you ready to).*?\?+\s*)+$",
    re.IGNORECASE | re.DOTALL,
)


class GeminiService:
    """Manages Gemini API calls for explanation refinement and conversational tutoring."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._explicit_key = api_key is not None
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY", "")
        self.api_key = api_key.strip()
        if model is None:
            model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self.model = model.strip()
        self._client: Any = None
        if self.api_key and GENAI_AVAILABLE:
            try:
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning("Failed to initialize Gemini Client: %s", type(e).__name__)
                self._client = None

    def _refresh_config(self) -> None:
        """Picks up updated GEMINI_API_KEY or GEMINI_MODEL dynamically if modified in .env."""
        if self._explicit_key:
            return
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            pass
        current_env_key = os.environ.get("GEMINI_API_KEY", "").strip()
        current_env_model = os.environ.get("GEMINI_MODEL", "").strip()
        if current_env_key and current_env_key != self.api_key:
            self.api_key = current_env_key
            if GENAI_AVAILABLE:
                try:
                    self._client = genai.Client(api_key=self.api_key)
                except Exception as e:
                    logger.warning("Failed to initialize Gemini Client with new key: %s", type(e).__name__)
                    self._client = None
        if current_env_model:
            self.model = current_env_model

    def is_available(self) -> bool:
        """Returns True if the Gemini client is configured and ready."""
        self._refresh_config()
        return bool(self.api_key and self._client is not None)

    # -------------------------------------------------------------------------
    # PART A: EXPLANATION REFINEMENT
    # -------------------------------------------------------------------------
    def refine_explanation(
        self,
        question_text: str,
        display_text: str,
        student_answer: str,
        correct_answer: str,
        diagnosis_status: str,
        pattern_id: str | None,
        pattern_name: str | None,
        evidence: list[str],
        existing_intervention: dict[str, Any],
        difficulty: str = "Intermediate",
    ) -> dict[str, Any]:
        """Refines the wording of an existing reviewed intervention using Gemini.

        Always falls back gracefully to existing_intervention on failure, timeout,
        or absence of API key.
        """
        if not self.is_available():
            out = dict(existing_intervention)
            out["gemini_refined"] = False
            return out

        evidence_str = "\n".join(f"- {e}" for e in evidence) if evidence else "- No specific pattern match"
        existing_steps = "\n".join(
            f"{i+1}. {s}" for i, s in enumerate(existing_intervention.get("steps", []))
        )
        existing_why = existing_intervention.get("why", "")

        user_prompt = (
            f"Please refine this mathematical learning explanation:\n\n"
            f"Question: {question_text}\n"
            f"{f'Visual notation: {display_text}' if display_text else ''}\n"
            f"Student's submitted answer: {student_answer}\n"
            f"Verified correct answer: {correct_answer}\n"
            f"Diagnosis status: {diagnosis_status}\n"
            f"Diagnosed misconception: {pattern_name or 'None (general review)'} ({pattern_id or 'General'})\n"
            f"Diagnostic evidence collected:\n{evidence_str}\n\n"
            f"Current reviewed explanation:\n"
            f"Why: {existing_why}\n"
            f"Steps:\n{existing_steps}\n"
            f"Hint: {existing_intervention.get('hint', '')}\n\n"
            f"Target learner difficulty: {difficulty}\n"
        )

        try:
            config = types.GenerateContentConfig(
                system_instruction=EXPLANATION_SYSTEM_INSTRUCTION,
                temperature=0.3,
                max_output_tokens=700,
            )
            models_to_try = [self.model] + [m for m in FALLBACK_MODELS if m != self.model]
            raw_text = ""
            for mdl in models_to_try:
                try:
                    response = self._client.models.generate_content(
                        model=mdl,
                        contents=user_prompt,
                        config=config,
                    )
                    if response and response.text:
                        raw_text = response.text.strip()
                        self.model = mdl
                        break
                except Exception as ex:
                    if "404" in str(ex) or "not found" in str(ex).lower():
                        continue
                    raise

            if not raw_text:
                raise ValueError("Empty response from Gemini")

            # Parse refined Why and Steps
            refined_why = ""
            refined_steps: list[str] = []

            why_match = re.search(r"WHY:\s*(.+?)(?=(?:STEPS:|$))", raw_text, re.DOTALL | re.IGNORECASE)
            if why_match:
                refined_why = why_match.group(1).strip()

            steps_match = re.search(r"STEPS:\s*(.+)$", raw_text, re.DOTALL | re.IGNORECASE)
            if steps_match:
                steps_block = steps_match.group(1).strip()
                lines = [re.sub(r"^\d+\.\s*", "", l).strip() for l in steps_block.splitlines() if l.strip()]
                if len(lines) >= 2:
                    refined_steps = lines

            # Clean trailing unsolicited questions if any
            if refined_why:
                refined_why = TRAILING_QUESTION_PATTERN.sub("", refined_why).strip()

            out = dict(existing_intervention)
            out["gemini_refined"] = True
            out["original_why"] = existing_why
            if refined_why:
                out["why"] = refined_why
            if refined_steps:
                out["steps"] = refined_steps

            return out

        except Exception as e:
            logger.warning("Gemini explanation refinement failed, using fallback: %s", type(e).__name__)
            out = dict(existing_intervention)
            out["gemini_refined"] = False
            return out

    # -------------------------------------------------------------------------
    # PART B: AI TUTOR CHAT
    # -------------------------------------------------------------------------
    def tutor_chat(
        self,
        message: str,
        difficulty: str = "Intermediate",
        mode: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Generates a conversational educational response for EduMind AI Tutor.

        Strictly enforces:
        - Age-appropriate language based on selected difficulty.
        - Direct answers without appending unsolicited follow-up questions.
        - Support for concepts inside or outside the current fraction syllabus.
        """
        if not self.is_available():
            raise RuntimeError(
                "EduMind AI Tutor is not currently configured with a valid GEMINI_API_KEY on the server."
            )

        # Style guidance based on mode
        mode_instruction = ""
        if mode == "step-by-step":
            mode_instruction = " Provide a structured, numbered step-by-step breakdown."
        elif mode == "analogy":
            mode_instruction = " Include an intuitive, relatable real-world analogy to make the concept tangible."
        elif mode == "flashcards":
            mode_instruction = " Summarize key definitions and core takeaway facts clearly."
        elif mode == "hints":
            mode_instruction = " Provide 3 tiered progressive hints (from subtle guidance to explicit method)."

        system_instruction = (
            f"{TUTOR_SYSTEM_INSTRUCTION}\n"
            f"The learner has selected {difficulty} difficulty level. Adapt vocabulary and depth accordingly."
            f"{mode_instruction}\n"
            f"IMPORTANT: Answer the question directly and finish naturally. DO NOT append questions or quizzes at the end."
        )

        # Build contents from history and current message
        contents: list[Any] = []
        if history:
            for turn in history[-10:]:  # Keep recent context
                role = "user" if turn.get("role") == "user" else "model"
                content_text = turn.get("content", "").strip()
                if content_text:
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[types.Part.from_text(text=content_text)],
                        )
                    )

        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=message.strip())],
            )
        )

        models_to_try = [self.model] + [m for m in FALLBACK_MODELS if m != self.model]
        last_error = None
        raw_reply = ""

        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.6,
                max_output_tokens=1000,
            )
            for mdl in models_to_try:
                try:
                    response = self._client.models.generate_content(
                        model=mdl,
                        contents=contents,
                        config=config,
                    )
                    if response and response.text:
                        raw_reply = response.text.strip()
                        self.model = mdl
                        break
                except Exception as e:
                    err_str = str(e)
                    last_error = e
                    if "404" in err_str or "not found" in err_str.lower() or "429" in err_str or "quota" in err_str.lower() or "resource_exhausted" in err_str.lower() or "403" in err_str:
                        continue
                    break

            if not raw_reply:
                if last_error:
                    err_str = str(last_error)
                    if "429" in err_str or "quota" in err_str.lower():
                        raise RuntimeError("Gemini API quota exceeded. Please check your quota/billing at Google AI Studio.") from last_error
                    if "403" in err_str or "permission_denied" in err_str.lower():
                        raise RuntimeError("Gemini API access denied. Please verify your API key permissions at Google AI Studio.") from last_error
                    raise last_error
                raise ValueError("Empty reply from Gemini")

            # Post-processing: Remove any trailing questions/quizzes to strictly adhere to rules
            cleaned_reply = TRAILING_QUESTION_PATTERN.sub("", raw_reply).strip()
            return cleaned_reply or raw_reply

        except RuntimeError:
            raise
        except APIError as e:
            logger.error("Gemini API error during tutor chat: %s", type(e).__name__)
            raise RuntimeError("EduMind AI Tutor encountered a service error. Please try again shortly.") from e
        except Exception as e:
            logger.error("Unexpected error during tutor chat: %s", type(e).__name__)
            raise RuntimeError("EduMind AI Tutor could not process the request. Please try again.") from e
