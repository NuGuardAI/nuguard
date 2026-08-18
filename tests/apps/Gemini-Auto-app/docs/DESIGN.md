# Design Document: Gemini In-Car Information System

## 1. Architectural Overview
The system follows a modular frontend architecture, designed for real-time interaction and extensible for peripheral device integration.

### 1.1 Tech Stack
- **Framework**: React 19 (Vite)
- **Styling**: Tailwind CSS (Mobile-first, dark-mode optimized)
- **Animation Engine**: `motion` (for high-fidelity dashboard transitions)
- **Intelligence**: Google Gemini (via `@google/genai`)
- **Iconography**: Lucide React

### 1.2 Platform & Context (ADK)
This system is built as an **ADK-compatible Applet**, leveraging the environment's specific capabilities:
- **Secret-Driven Logic**: The app dynamically switches between simulated and real-world modes based on environment variables managed by the AI Studio platform.
- **Gemini Native**: Direct access to `process.env.GEMINI_API_KEY` provided by the runtime, enabling sophisticated function-calling without custom backend boilerplate.
- **Port Management**: Hard-coded to port 3000 to comply with ADK infrastructure routing requirements.

## 2. Technical Design

### 2.1 Component Structure
- **AssistantUI**: Manages the conversational state, voice visualization (via `motion`), and interaction history.
- **VehicleStats**: Dashboard component that visualizes telematics (Fuel, Temperature, Tire Pressure) and media state.
- **MapSimulator**: A dual-mode map component that defaults to a stylized HUD simulation but upgrades to a real Google Maps instance if a platform key is detected.

### 2.2 Integration Strategy (Real vs. Simulated)
The system uses a "Secret-First" integration patterns:
- **Google Maps**: If `GOOGLE_MAPS_PLATFORM_KEY` is present, `RealMap.tsx` is mounted within the map container, providing actual routing and traffic data.
- **Weather API**: If `OPENWEATHER_API_KEY` is present, the app calls the OpenWeather API for real-time localized forecasts; otherwise, it degrades gracefully to a "Current Region" simulation.
- **Gemini**: Requires `GEMINI_API_KEY`. It utilizes function calling for vehicle controls and **Google Search Retrieval** for real-time external knowledge (grounding).
- **Media System**: Controlled via `MediaService.ts`, which abstracts between local state management and external streaming APIs (e.g., Spotify) based on the presence of `VITE_SPOTIFY_CLIENT_ID`.

### 2.3 Function Calling Interface
The AI agent is equipped with a specific toolset defined in `gemini.ts`:
- `adjustTemperature(temp, zone)`
- `playMusic(query)`
- `navigateTo(destination)`
- `addStop(location)`
- `findNearbyService(serviceType, query)`
- `checkVehicleStatus(system)`
- `getWeather(location)`

## 3. User Interface (UI) Design

### 3.1 Aesthetic & Visual Identity
- **Mood**: High-tech, technical dashboard, legible, and authoritative.
- **Color Palette**: 
  - Primary: `#0D0E10` (Dashboard Black)
  - Accents: `#3B82F6` (Electric Blue), `#EF4444` (Critical Alert Red)
- **Typography**: 
  - **Inter**: For general UI and conversational text.
  - **JetBrains Mono**: For telemetry data, system logs, and "technical" metadata to evoke a vehicle-software feel.

### 3.2 Animation Strategy
- **Orchestration**: Staggered entrance for dashboard modules.
- **Interaction Feedback**: The "Pulse" animation for the voice assistant varies its scale and opacity based on the processing state.
- **Transitions**: Slide-in effects for new navigation destinations and status alerts.

## 4. Scalability & Future Design
- **OnStar Integration**: The design reserves space for a direct bypass to human agents, triggered by high-risk diagnostic flags or specific intent detection.
- **Multi-Zone Audio**: The architecture allows for independent media/climate control per cockpit zone (e.g., Passenger vs. Driver).
