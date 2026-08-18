# Gemini In-Car Information System

This is a next-generation in-car assistant built with React, Tailwind CSS, and Google Gemini. It demonstrates a high-fidelity "technical dashboard" interface capable of managing vehicle systems through natural language.

## 🚀 Getting Started

### Prerequisites
- **Node.js** (v18 or higher)
- **npm** (or yarn/pnpm)
- **Google Gemini API Key**: Obtain one from [Google AI Studio](https://aistudio.google.com/app/apikey).

### Installation

1. **Clone the repository**:
   ```bash
   git clone <your-repo-url>
   cd <project-folder>
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Configure Environment Variables**:
   Copy the example environment file and add your keys:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and add your `GEMINI_API_KEY`. You can also optionally add `GOOGLE_MAPS_PLATFORM_KEY`, `OPENWEATHER_API_KEY`, and `VITE_SPOTIFY_CLIENT_ID` to enable "Real-World" modes.

### Running the App

- **Development Mode**: Starts the local dev server with Hot Module Replacement.
  ```bash
  npm run dev
  ```
  The app will typically be available at `http://localhost:3000`.

- **Production Build**: Compiles and optimizes the app for deployment.
  ```bash
  npm run build
  npm run preview
  ```

## 🛠 Features

- **Gemini Assistant (Grounding Enabled)**: The assistant uses **Google Search Grounding** to provide real-time information (e.g., current gas prices, traffic news).
- **Intelligent Telematics**: Real-time simulation of fuel/battery, tire pressure, and engine status.
- **Media Integration**: Support for **Spotify Web API** logic. Switching to "Real-World" mode by providing a client ID.
- **Dynamic Map**: Modular HUD that upgrades to a real Google Map if an API key is provided.
- **Function Calling**: The AI can autonomously execute actions like:
  - `adjustTemperature`: Set cabin climate.
  - `navigateTo` / `addStop`: Manage routes.
  - `findNearbyService`: Locate charging stations, gas stations, or restaurants.
  - `playMusic`: Control the infotainment system.

## 📁 Documentation
Detailed design and functional requirements can be found in the `/docs` folder:
- [Requirements](./docs/REQUIREMENTS.md)
- [Design Architecture](./docs/DESIGN.md)

## 🏗 Built With
- **React 19** & **Vite**
- **Tailwind CSS**
- **Framer Motion** (for the voice visualizer and dashboard transitions)
- **Google Generative AI SDK** (@google/genai)
- **Lucide React** (Icons)
