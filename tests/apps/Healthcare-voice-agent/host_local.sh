#!/bin/bash

# Exit on error
set -e

echo "🚀 Preparing Healthcare Voice Agent for local hosting..."

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed. Please install Docker and try again."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Error: Docker Compose is not installed. Please install it and try again."
    exit 1
fi

# Create root .env for frontend if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating root .env file..."
    touch .env
fi

# Create backend .env if it doesn't exist
if [ ! -f backend/.env ]; then
    echo "📝 Creating backend/.env file..."
    if [ -f backend/.env.example ]; then
        cp backend/.env.example backend/.env
    else
        touch backend/.env
    fi
fi

# Function to check and prompt for API keys
check_key() {
    local env_file=$1
    local key_name=$2
    local prompt_msg=$3

    if ! grep -q "^$key_name=" "$env_file" || [ -z "$(grep "^$key_name=" "$env_file" | cut -d'=' -f2)" ]; then
        echo "⚠️  $key_name is missing in $env_file"
        read -p "$prompt_msg: " user_key
        if [ ! -z "$user_key" ]; then
            # Remove existing key if any
            sed -i "/^$key_name=/d" "$env_file"
            echo "$key_name=$user_key" >> "$env_file"
        fi
    fi
}

echo "--- Configuration ---"
check_key ".env" "VITE_GEMINI_API_KEY" "Enter your Gemini API Key (for frontend)"
check_key "backend/.env" "OPENAI_API_KEY" "Enter your OpenAI API Key (for backend)"

# Export keys for docker-compose substitution
export VITE_GEMINI_API_KEY=$(grep "^VITE_GEMINI_API_KEY=" .env | cut -d'=' -f2)
export OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" backend/.env | cut -d'=' -f2)

echo "🏗️  Building and starting containers..."
docker-compose up --build -d

echo ""
echo "✅ Application is starting!"
echo "📍 Frontend (local dev): http://localhost:5431"
echo "📍 Frontend/API (Docker): http://localhost:8080"
echo "📊 Database: localhost:5432 (User: fastapi_user, DB: healthcare)"
echo ""
echo "To view logs, run: docker-compose logs -f"
echo "To stop the application, run: docker-compose down"
