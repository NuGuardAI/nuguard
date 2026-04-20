from enum import Enum as PyEnum
from typing import List, Optional

from google.adk.agents import LlmAgent, SequentialAgent
from pydantic import BaseModel, Field


class ConsultantTypeEnum(PyEnum):
    PSYCHOLOGIST = "psychologist"
    PSYCHIATRIST = "psychiatrist"
    THERAPIST = "therapist"
    NUTRITIONIST = "nutritionist"
    PERSONAL_TRAINER = "personal_trainer"
    LIFE_COACH = "life_coach"
    FINANCIAL_ADVISOR = "financial_advisor"
    BUSINESS_COACH = "business_coach"
    CAREER_COACH = "career_coach"
    GENERAL_HELPER = "general_helper"


class ProblemAnalysis(BaseModel):
    consultant_type: ConsultantTypeEnum
    identified_issues_summary: str = Field(
        description="A brief summary of the core issues identified from the user's query."
    )


class ConsultationResp(BaseModel):
    consultant_type: ConsultantTypeEnum
    identified_issues_summary: str
    suitability_explanation: str
    key_questions_to_consider: List[str]
    initial_actionable_steps: List[str]
    disclaimer: str


problem_analyzer_instructions = """
You are an expert AI assistant that analyzes user queries to understand their core problem and recommend an appropriate type of consultant.
Based on the user's query, identify the primary issues the user is facing.
Then, determine the most suitable consultant type
Output a JSON object with the recommended 'consultant_type' and a concise 'identified_issues_summary'.
If the query is too vague or doesn't clearly fit a specialist, recommend 'general_helper'.
Focus on the main problem.
"""

problem_analyzer_agent = LlmAgent(
    name="ProblemAnalyzerAgent",
    model="gemini-2.0-flash",
    instruction=problem_analyzer_instructions,
    output_schema=ProblemAnalysis,
    output_key="problem_analysis_result",
)

advice_generator_instructions = """
You are a helpful AI assistant that provides initial guidance based on a recommended consultant type and identified user issues.

Based on the provided input {problem_analysis_result} generate the following in a structured JSON format:
1.  'suitability_explanation': A brief (1-2 sentences) explanation of why a the suggested consultation type is suitable for these issues.
2.  'key_questions_to_consider': 2-3 insightful questions the user might want to reflect on or ask the consultant.
3.  'initial_actionable_steps': 1-2 very general, safe, and constructive initial steps or resources the user could explore.
    - For 'psychologist', 'psychiatrist', 'therapist': Suggest things like "Consider journaling your feelings" or "Look into mindfulness exercises." AVOID DIAGNOSING OR PRESCRIBING.
    - For 'nutritionist': Suggest "Start by tracking your current eating habits for a few days" or "Explore resources on balanced diets from reputable health organizations." AVOID SPECIFIC DIET PLANS.
    - For 'personal_trainer': Suggest "Think about your fitness goals" or "Consider starting with a 10-15 minute daily walk." AVOID SPECIFIC WORKOUT ROUTINES.
    - For 'financial_advisor': Suggest "Gather information about your current income and expenses" or "Identify your short-term and long-term financial goals." AVOID SPECIFIC INVESTMENT ADVICE.
    - For other coaches: Provide general questions about goals and initial small steps.
    - For 'general_helper': Suggest clarifying their needs further or seeking general problem-solving resources.
4.  'disclaimer': Always include the standard disclaimer: "This is AI-generated guidance and not a substitute for professional advice. Please consult with a qualified professional for your specific needs."
"""

advice_generator_agent = LlmAgent(
    name="AdviceGeneratorAgent",
    model="gemini-2.0-flash",
    instruction=advice_generator_instructions,
    input_schema=ProblemAnalysis,
    output_schema=ConsultationResp,
    output_key="final_consultation_response",
)

root_agent = SequentialAgent(
    name="StructuredConsultationAgent",
    sub_agents=[problem_analyzer_agent, advice_generator_agent],
)
