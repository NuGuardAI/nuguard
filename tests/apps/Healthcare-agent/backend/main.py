from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
import logging
import os
import numpy as np
from config import Config
from db import get_db_connection
from models import LoginRequest, AppointmentRequest
from preprocess import preprocess_text
from queries import *
from langgraph_llm_agents import build_graph
from models import PatientDetailsResponse
from models import MedicalHistoryResponse

app = FastAPI()
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        Config.FRONTEND_ORIGIN,
        Config.FRONTEND_ORIGIN.replace("localhost", "127.0.0.1"),
        Config.FRONTEND_ORIGIN.replace("127.0.0.1", "localhost"),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize models
model = None
index = None
terms = []

import time

def get_db():
    max_retries = 3
    retry_delay = 2
    for i in range(max_retries):
        try:
            conn = get_db_connection()
            return conn
        except Exception as e:
            logger.error(f"Attempt {i+1} failed to connect to database: {e}")
            if i < max_retries - 1:
                time.sleep(retry_delay)
    return None

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

@app.post("/login")
def login(request: LoginRequest):
    logger.info(f"Login attempt: {request.email}")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Database connection failed")
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM sp_login_user(%s::TEXT, %s::TEXT)", (request.email, request.password))
        user = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Database error during login: {e}")
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail="Login failed")

    if user:
        return JSONResponse(content={"message": "Login successful", "user_id": user[0]})
    
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/patient-details/{user_id}", response_model=PatientDetailsResponse)
def get_patient_details(user_id: int):
    logger.info(f"Fetching patient details for user_id={user_id}")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Database connection failed")
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM sp_get_patient_details(%s);", (user_id,))
        row = cur.fetchone()
        logger.info(f"Fetched row: {row}")
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"DB error in patient-details: {e}")
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    if row:
        return jsonable_encoder({
            "name": row[0], "date_of_birth": row[1], "gender": row[2], "contact_number": row[3],
            "medical_record_number": row[4], "blood_group": row[5], "marital_status": row[6], "id": row[7]
        })
    raise HTTPException(status_code=404, detail="Patient not found")


@app.get("/medical-history/{user_id}", response_model=MedicalHistoryResponse)
def get_medical_history(user_id: int):
    logger.info(f"Fetching medical history for user_id={user_id}")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Database connection failed")
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM sp_get_patient_id(%s);", (user_id,))
        patient_row = cur.fetchone()
        logger.info(f"Patient row: {patient_row}")
        if not patient_row:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Patient not found")
        patient_id = patient_row[0]

        cur.execute("SELECT * FROM sp_get_medical_history(%s);", (patient_id,))
        row = cur.fetchone()
        logger.info(f"Medical history row: {row}")
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"DB error in medical-history: {e}")
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    if row:
        return jsonable_encoder({
            "past_diagnoses": row[0], "surgeries": row[1], "hospital_admissions": row[2],
            "immunization_records": row[3], "family_medical_history": row[4], "lifestyle_factors": row[5]
        })
    raise HTTPException(status_code=404, detail="Medical history not found")

@app.post("/normalize")
async def normalize(request: Request):
    data = await request.json()
    phrases = data.get("phrases", [])
    results = []
    for phrase in phrases:
        cleaned = preprocess_text(phrase)
        if not cleaned:
            continue
        emb = model.encode([cleaned])
        D, I = index.search(np.array(emb), 1)
        distance = D[0][0]
        match = terms[I[0][0]]
        if distance > 1.0:
            continue
        results.append({"original": phrase, "cleaned": cleaned, "match": match, "score": float(distance)})
    return {"results": results}

@app.post("/run_langgraph")
async def run_langgraph(request: Request):
    try:
        data = await request.json()
        logger.debug(f"Received LangGraph request data: {data}")
    except Exception as e:
        logger.error(f"Failed to parse JSON in run_langgraph: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    phrases = data.get("phrases", [])
    if not phrases:
        logger.warning("No phrases provided in run_langgraph request")
        raise HTTPException(status_code=400, detail="No phrases provided.")
    
    logger.info(f"Running LangGraph with {len(phrases)} phrases")
    graph = build_graph()
    try:
        final_state = graph.invoke({"phrases": phrases})
        logger.debug(f"LangGraph final state: {final_state}")
        return {
            "phrases": final_state.get("phrases", []),
            "normalized_symptoms": final_state.get("normalized_symptoms", []),
            "specialists": final_state.get("specialists", []),
            "recommended_specialists": final_state.get("recommended_specialists", []),
            "prognosis": final_state.get("prognosis", ""),
            "doctors": final_state.get("doctors", [])
        }
    except Exception as e:
        logger.error(f"Error executing LangGraph: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/appointments")
def create_appointment(req: AppointmentRequest):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=503, detail="Database connection failed")
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM sp_create_appointment(%s, %s, %s, %s)", (req.patient_id, req.doctor_id, req.slot_id, req.reason))
        appointment_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Appointment created", "appointment_id": appointment_id}
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
# Serve static files from the React app
dist_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "dist"))

if os.path.exists(dist_path):
    logger.info(f"Serving static files from: {dist_path}")
    
    # We'll use a single catch-all for all static files and SPA routing
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # 1. Check if the path is explicitly for an API (that failed to match established routes)
        # Note: most of our APIs are not prefixed with api/, but /api/health is.
        if full_path.startswith("api/"):
             logger.warning(f"404 for API path: {full_path}")
             raise HTTPException(status_code=404, detail="API route not found")

        # Resolve and validate paths to prevent directory traversal
        resolved_dist = os.path.realpath(dist_path)

        def safe_join(base: str, *parts: str) -> str | None:
            candidate = os.path.realpath(os.path.join(base, *parts))
            if not candidate.startswith(resolved_dist + os.sep) and candidate != resolved_dist:
                return None
            return candidate

        # 2. Check if it's a file in the dist directory
        file_path = safe_join(resolved_dist, full_path)
        if file_path and os.path.isfile(file_path):
            return FileResponse(file_path)
            
        # 3. Check if it's a file in dist/assets (for bundled files)
        # This handles cases where assets are requested without the leading /assets/ or 
        # if the absolute leading slash in index.html is missing.
        assets_path = safe_join(resolved_dist, "assets", full_path)
        if assets_path and os.path.isfile(assets_path):
            return FileResponse(assets_path)

        # 4. SPA routing: for anything else, serve index.html
        index_path = os.path.join(resolved_dist, "index.html")
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        
        logger.error(f"Frontend build not found at: {index_path}")
        raise HTTPException(status_code=404, detail="Frontend build not found")
else:
    logger.warning(f"Static files directory not found: {dist_path}")
