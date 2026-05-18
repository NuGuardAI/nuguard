# Stage 1: Build the React frontend
FROM node:20 AS frontend-builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
# Set environment variable for build time
ARG VITE_GEMINI_API_KEY
ENV VITE_GEMINI_API_KEY=$VITE_GEMINI_API_KEY
ENV VITE_BACKEND_URL=""
RUN npm run build

# Stage 2: Setup the Python backend
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies for psycopg2 and potentially sentence-transformers
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend code
COPY backend/ ./backend/

# Copy built frontend to a location where FastAPI can serve it
COPY --from=frontend-builder /app/dist ./dist

# Set working directory to backend so imports like 'from config import Config' work
WORKDIR /app/backend

# Default Port for Cloud Run
ENV PORT=8080

EXPOSE 8080

# Run uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
