# GitHub Copilot Instructions - Healthcare Voice Agent

You are an expert AI software engineer specializing in healthcare technology, AI-driven voice applications, and full-stack development. Follow these guidelines when assisting with this project.

## 🏗 Project Architecture
- **Frontend**: React (Vite-based) using functional components and hooks. State management uses `UserContext` and `localStorage` for session persistence (e.g., `user_id`).
- **Backend**: FastAPI (Python) for the REST API.
- **Database**: PostgreSQL. Business logic should reside in **Stored Procedures** located in `sql/functions/`.
- **AI/LLM**: LangGraph for agent orchestration, integrated with Google Gemini and OpenAI GPT-4.
- **Microservices**: Deployed via Docker with a `docker-compose.yml` for local development.

## � Environment & Configuration
- **Backend**: Uses `.env` for database credentials (`DB_NAME`, `DB_USER`, etc.) and `FRONTEND_ORIGIN`. Config is managed in `backend/config.py`.
- **Frontend**: Uses `.env.local` for `VITE_BACKEND_URL` and other service keys.

## �💻 Coding Standards

### Backend (FastAPI / Python)
- **Database Access**: Direct connection using `psycopg2` via `backend/db.py`. Always use cursors and call stored procedures using `cur.execute("SELECT * FROM sp_name(%s)", (param,))`.
- **Logging**: Use the standard `logging` library. Log all major actions, especially database interactions and AI agent steps.
- **Response Format**: Use `jsonable_encoder` from `fastapi.encoders` to ensure complex objects (like dates) are serialized correctly.
- **Dependency Management**: Add new requirements to `backend/requirements.txt`.

### Frontend (React / JavaScript)
- **Styling**: Each component should have its own corresponding CSS file (e.g., `Dashboard.jsx` -> `Dashboard.css`).
- **Data Fetching**: Use standard `fetch` API. Base URL should come from `import.meta.env.VITE_BACKEND_URL`.
- **Navigation**: Use `react-router-dom`.

### Database (PostgreSQL)
- **Functions**: All data-modifying or complex query logic must be in `sql/functions/` as stored procedures.
- **Schema**: Maintain `sql/schema.sql` for table definitions and `init-db.sh` for initialization.

## 🩺 Healthcare AI Context
- This agent performs triage, mapping symptoms to specialists, and booking appointments.
- **Voice Pipeline**: Web Speech API handles STT, backend/LangGraph handles intent, and Gemini/GPT-4 handles medical reasoning.
- **Safety**: Always include disclaimers when providing medical recommendations. Ensure AI responses are grounded in the `sp_get_specialists` results.

## 📂 File Structure
- `backend/`: FastAPI application code.
- `src/`: React frontend application code.
- `sql/`: Database schema and stored procedures.
- `public/`: Static assets for the frontend.
- `image/`: Documentation and architecture diagrams.
