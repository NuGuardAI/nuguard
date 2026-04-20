import os
import psycopg2
import logging
from typing import List, TypedDict
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from duckduckgo_search import DDGS
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from db import get_db_connection

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Medical Disclaimer
DISCLAIMER = "\n\n**DISCLAIMER:** This information is for educational purposes and does not constitute medical advice. Please consult with a healthcare professional for a formal diagnosis."

# Shared LangGraph state definition
class AgentState(TypedDict):
    phrases: List[str]
    normalized_symptoms: List[str]
    prognosis: str
    specialists: List[str]
    recommended_specialists: List[str]
    doctors: List[dict]

# Initialize GPT-4
llm = None
def get_llm():
    global llm
    if llm is None:
        llm = ChatOpenAI(
            model="gpt-4",
            temperature=0.2,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
    return llm

# Normalize Agent using GPT-4
def normalize_agent(state: AgentState) -> AgentState:
    logger.info("GPT-4 Normalize Agent running...")
    phrases = state.get("phrases", [])
    if not phrases:
        logger.warning("No phrases to normalize.")
        return {"normalized_symptoms": []}

    prompt = (
        "You are a medical assistant. Normalize the following patient symptom phrases "
        "into a list of clinical symptom terms. Only output comma-separated clinical terms.\n"
        f"Patient phrases: {phrases}"
    )
    messages = [
        SystemMessage(content="You are a helpful medical assistant."),
        HumanMessage(content=prompt)
    ]
    
    try:
        model = get_llm()
        if not model:
            raise ValueError("LLM not initialized. Check OPENAI_API_KEY.")
            
        response = model.invoke(messages)
        raw_output = response.content
        normalized = [term.strip().lower() for term in raw_output.split(",") if term.strip()]
        logger.info(f"Normalized symptoms: {normalized}")
        return {"normalized_symptoms": normalized}
    except Exception as e:
        logger.error(f"Error in normalize_agent: {e}")
        # Fallback: use raw phrases but cleaned up
        fallback = [p.strip().lower() for p in phrases if p.strip()]
        logger.info(f"Using fallback normalization: {fallback}")
        return {"normalized_symptoms": fallback}

# Prognosis Search Agent (using DuckDuckGo)
def prognosis_search_agent(state: AgentState) -> AgentState:
    logger.info("Searching for prognosis based on symptoms...")
    symptoms = state.get("normalized_symptoms", [])
    if not symptoms:
        return {"prognosis": "No symptoms provided for prognosis."}

    query = f"prognosis for {', '.join(symptoms)} site:webmd.com OR site:mayoclinic.org"
    
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            search_results = "\n".join([f"{r['title']}: {r['body']}" for r in results])

        prompt = (
            f"Based on these symptoms: {', '.join(symptoms)} and the following search results:\n"
            f"{search_results}\n\n"
            "Provide a concise possible prognosis or explanation for these symptoms. "
            "Mention that these are potential causes found on reputable sites like WebMD and Mayo Clinic. "
            "Be very brief and emphasize it is not a diagnosis."
        )
        messages = [
            SystemMessage(content="You are a helpful medical assistant."),
            HumanMessage(content=prompt)
        ]
        model = get_llm()
        response = model.invoke(messages)
        prognosis_text = response.content + DISCLAIMER
        logger.info(f"Prognosis generated: {prognosis_text[:100]}...")
        return {"prognosis": prognosis_text}
    except Exception as e:
        logger.error(f"Error in prognosis_search_agent: {e}")
        return {"prognosis": f"Could not retrieve prognosis at this time.{DISCLAIMER}"}

# Specialist Lookup Agent (via stored procedure)
def specialist_lookup_agent(state: AgentState) -> AgentState:
    logger.info(f"Looking up specialists for: {state.get('normalized_symptoms', [])}")
    normalized = state.get("normalized_symptoms", [])
    if not normalized:
        logger.warning("No normalized symptoms to look up")
        return {"specialists": []}

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        logger.debug(f"Executing sp_get_specialists with {normalized}")
        cur.execute("SELECT * FROM sp_get_specialists(%s)", (normalized,))
        specialists = [row[0] for row in cur.fetchall()]
        logger.info(f"Found specialists: {specialists}")
        cur.close()
        conn.close()
        return {"specialists": specialists}
    except Exception as e:
        logger.error(f"Error in specialist_lookup_agent: {e}")
        return {"specialists": []}

# LLM-Based Specialist Recommender Agent
def recommend_specialists_agent(state: AgentState) -> AgentState:
    logger.info("Recommending best specialists using GPT-4...")
    symptoms = state.get("normalized_symptoms", [])
    specialists = state.get("specialists", [])
    if not specialists or not symptoms:
        logger.warning("Missing documentation or symptoms for recommendation.")
        return {"recommended_specialists": []}

    prompt = (
        f"You are a medical assistant. A patient reported the following symptoms: {', '.join(symptoms)}.\n"
        f"The following specialists are available: {', '.join(specialists)}.\n"
        "From this list, which 1 or 2 specialists would be most suitable to consult first?\n"
        "Only return the recommended specialist names as a comma-separated list."
    )
    messages = [
        SystemMessage(content="You are an intelligent medical assistant that triages patients."),
        HumanMessage(content=prompt)
    ]
    
    try:
        model = get_llm()
        if not model:
            raise ValueError("LLM not initialized.")
            
        response = model.invoke(messages)
        raw_output = response.content
        recommended = [name.strip() for name in raw_output.split(",") if name.strip() in specialists]
        logger.info(f"Recommended specialists: {recommended}")
        return {"recommended_specialists": recommended}
    except Exception as e:
        logger.error(f"Error in recommend_specialists_agent: {e}")
        # Fallback: just return the first two found specialists
        fallback = specialists[:2]
        logger.info(f"Using fallback recommendations: {fallback}")
        return {"recommended_specialists": fallback}

# Doctor Info Agent (via stored procedure)
def fetch_doctor_details_agent(state: AgentState) -> AgentState:
    recommended = state.get("recommended_specialists", [])
    logger.info(f"Fetching doctor info for: {recommended}")
    if not recommended:
        logger.warning("No recommended specialists to fetch doctors for.")
        return {"doctors": []}

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sp_get_doctors_by_specialists(%s)", (recommended,))
        doctor_rows = cur.fetchall()
        doctors = []
        for row in doctor_rows:
            doctors.append({
                "doctor_id": row[0],
                "name": row[1],
                "specialization": row[2],
                "rating": float(row[3]) if row[3] is not None else 0.0,
                "fees": int(row[4]) if row[4] else 0,
                "hospital": row[5],
                "next_available_date": str(row[6]) if row[6] else "Not available",
                "start_time": str(row[7]) if row[7] else "N/A",
                "end_time": str(row[8]) if row[8] else "N/A",
                "slot_id": row[9]
            })
        logger.info(f"Fetched {len(doctors)} doctors.")
        cur.close()
        conn.close()
        return {"doctors": doctors}
    except Exception as e:
        logger.error(f"Error in fetch_doctor_details_agent: {e}")
        return {"doctors": []}

# Build LangGraph flow
def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("normalize_agent", normalize_agent)
    builder.add_node("prognosis_search_agent", prognosis_search_agent)
    builder.add_node("specialist_lookup_agent", specialist_lookup_agent)
    builder.add_node("recommend_specialists_agent", recommend_specialists_agent)
    builder.add_node("fetch_doctor_details_agent", fetch_doctor_details_agent)

    builder.set_entry_point("normalize_agent")
    builder.add_edge("normalize_agent", "prognosis_search_agent")
    builder.add_edge("prognosis_search_agent", "specialist_lookup_agent")
    builder.add_edge("specialist_lookup_agent", "recommend_specialists_agent")
    builder.add_edge("recommend_specialists_agent", "fetch_doctor_details_agent")
    builder.add_edge("fetch_doctor_details_agent", END)
    return builder.compile()
