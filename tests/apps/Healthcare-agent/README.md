# 🩺 AI Healthcare Voice Assistant

A comprehensive full-stack healthcare application that leverages AI-powered voice interactions for patient triage, symptom analysis, specialist mapping, and appointment booking. Built with modern technologies including FastAPI, PostgreSQL, React, and integrated with leading AI models.

---

## 🔄 System Architecture & User Flow

### System Architecture Overview
![System Architecture](image/Healthcare%20AI%20Agent/System%20Architecture.png)

### Component Architecture
![Component Architecture](image/Healthcare%20AI%20Agent/component%20architecture.png)

### Data Flow Architecture
![Data Flow Architecture](image/Healthcare%20AI%20Agent/dataflow%20architecture.png)

### Technology Stack Architecture
![Tech Stack Architecture](image/Healthcare%20AI%20Agent/techstack%20architecture.png)

### User Flow Diagrams
![User Flow 1](image/Healthcare%20AI%20Agent/userflow1.png)

![User Flow 2](image/Healthcare%20AI%20Agent/userflow2.png)

![User Flow 3](image/Healthcare%20AI%20Agent/userflow3.png)

## 🔍 Detailed Component Interaction Flow

### 1. **User Authentication Flow**
```
User Input (Email/Password) → FastAPI Login Endpoint → PostgreSQL sp_login_user → JWT Token/Session
```

### 2. **Voice Processing Pipeline**
```
Microphone → Web Speech API → Text Conversion → UI Sync (isMicActive) → LangGraph Processing
```

### 3. **AI Analysis Workflow**
```
Raw Symptoms → GPT-4 Normalization → DuckDuckGo (Prognosis) → Specialist Mapping → Doctor Recommendations
```

### 4. **Database Integration Pattern**
```
FastAPI Endpoints → PostgreSQL Stored Procedures → Data Retrieval → JSON Response → Frontend Display
```

### 5. **Appointment Booking Chain**
```
Doctor Selection → Patient Details Fetch → Slot Validation → Appointment Creation → Payment Processing
```

---

## 🏗️ Technical Architecture Overview

The technical architecture is visualized through the comprehensive diagrams above, showing the integration between Frontend (React), Backend (FastAPI), Database (PostgreSQL), and External Services (AI APIs, Payment Gateway).

---

## 🌟 Overview

This healthcare AI assistant streamlines the patient care journey by providing:
- **Intelligent Voice Triage**: Natural language symptom collection and analysis
- **AI-Powered Diagnosis**: Advanced symptom normalization and specialist recommendation
- **Smart Doctor Matching**: Automated healthcare provider lookup based on specialization
- **Seamless Booking**: Integrated appointment scheduling with payment processing
- **Conversational Interface**: Intuitive voice-enabled user experience

---

## ✨ Key Features

### 🎤 Voice-Enabled Interaction
- Real-time speech-to-text conversion using Web Speech API
- Natural language processing for symptom collection
- Voice-guided patient triage workflow

### 🤖 AI-Powered Medical Intelligence
- Integration with Gemini and GPT-4 for symptom analysis
- LangGraph-based symptom normalization and multi-step agent flow
- **Automated Prognosis**: Real-time web search (DuckDuckGo) on reputable sites like **WebMD** and **Mayo Clinic**
- Intelligent specialist mapping and recommendations with medical disclaimers

### 🏥 Healthcare Management
- Comprehensive doctor database covering Family Medicine, Cardiology, Psychiatry, Gastroenterology, Orthopedics, and more
- PostgreSQL-powered efficient data retrieval via stored procedures
- Automated appointment scheduling system with real-time slot selection

### 💳 Payment Integration
- Secure payment processing via Razorpay
- Test mode support for development
- Transaction management and tracking

---

## 🛠️ Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Frontend** | React, CSS, JavaScript | User interface and voice interactions |
| **Backend** | FastAPI, Python | API services and business logic |
| **AI/ML** | Gemini, GPT-4, LangGraph | Natural language processing and AI agents |
| **Database** | PostgreSQL | Data persistence and stored procedures |
| **Voice** | Web Speech API | Speech recognition and synthesis |
| **Payments** | Razorpay | Payment processing and gateway |
| **Deployment** | Uvicorn, Vite | Development and production servers |

---

## 📂 Project Architecture

```
healthcare-ai-assistant/
├── backend/                    # FastAPI backend services
│   ├── main.py                # Application entry and SPA server
│   ├── langgraph_llm_agents.py # LangGraph AI orchestration
│   ├── db.py                  # Database connection pool
│   ├── models.py              # Pydantic data models
│   └── config.py              # Configuration management
│
├── src/                        # React frontend application (Vite)
│   ├── components/            # UI Components (Assistant, Dashboard, etc.)
│   ├── context/               # UserContext for Voice/AI state
│   ├── App.jsx                # Login/Landing page
│   └── main.jsx               # Application entry
│
├── sql/                       # Database schema and functions
│   ├── schema.sql             # Table definitions & Seed data
│   └── functions/             # PostgreSQL stored procedures
│       ├── sp_login_user.sql
│       ├── sp_get_specialists.sql
│       ├── sp_create_appointment.sql
│       └── sp_get_doctors_by_specialists.sql
│
├── Dockerfile                 # Multi-stage build (Node + Python)
├── docker-compose.yml         # Container orchestration
├── host_local.sh              # One-click automation script
└── README.md                 # Project documentation
```

---

## 🚀 Quick Start Guide (Recommended)

The easiest way to run the application is using the provided automation script which handles environment setup, API key prompts, and Docker orchestration.

### Prerequisites

- **Docker** and **Docker Compose**
- **Bash environment** (Linux, macOS, or WSL)
- API Keys: **OpenAI** and **Google Gemini**

### 1. ⚡ One-Click Startup

Run the following command in your terminal:

```bash
chmod +x host_local.sh
./host_local.sh
```

**What this script does:**
- Checks for Docker/Compose installation.
- Prompts for missing API keys (OpenAI, Gemini).
- Creates necessary `.env` and `.env.local` files.
- Builds the multi-stage Docker image (React build + Python server).
- Starts PostgreSQL and the Application containers.
- Initializes the database schema and stored procedures automatically.

### 2. 🔌 Access the App

Once the containers are running:
- **Frontend & Backend**: `http://localhost:8080`
- **Database**: `localhost:5432`

---

## 🛠️ Manual Development Setup (Optional)

If you prefer to run the components separately without Docker:

### 1. 🗄️ Database
- Install PostgreSQL.
- Run `sql/schema.sql` and all scripts in `sql/functions/`.

### 2. 🔧 Backend
- `cd backend`
- `pip install -r requirements.txt`
- Set environment variables in `.env`.
- `uvicorn main:app --reload --port 8800`

### 3. 🎨 Frontend
- `npm install`
- Set `VITE_BACKEND_URL=http://localhost:8800` in `.env.local`.
- `npm run dev` (Runs on `http://localhost:5173`)

### 4. 💳 Payment Setup (Optional)

1. Create a [Razorpay account](https://razorpay.com/)
2. Navigate to API Keys section in dashboard
3. Copy the Key ID and Key Secret
4. Update the payment configuration in `frontend/components/Recommendation.jsx`

---

## 🔑 API Keys Setup

### OpenAI API Key
1. Visit [OpenAI Platform](https://platform.openai.com/)
2. Create an account and navigate to API Keys
3. Generate a new secret key
4. Add to backend `.env` file

### Google Gemini API Key
1. Go to [Google AI Studio](https://makersuite.google.com/)
2. Create a new project or select existing
3. Generate API key
4. Add to both backend `.env` and frontend `.env.local`

### Razorpay Configuration
1. Sign up at [Razorpay Dashboard](https://dashboard.razorpay.com/)
2. Switch to Test Mode for development
3. Copy API keys from Settings > API Keys
4. Configure in frontend environment

---

## 🏃‍♂️ Running the Application (Quickest)

1. **Execute**: `./host_local.sh`
2. **Login**: Use test credentials `john@google.com` / `user2` (or check `sql/schema.sql` for others).
3. **Voice Interaction**: Ensure you use **Chrome or Edge** for the best Web Speech API support. Give microphone permissions when prompted.
4. **Analysis**: Speak your symptoms, then click **Disconnect & Analyze** to see the AI agent's specialist recommendations and prognosis.

---

## 🧪 Testing

### Backend API Testing
```bash
curl http://localhost:8000/health
```

### Database Connection Testing
```bash
python -c "from backend.db.connection import get_db_connection; print('DB Connected!' if get_db_connection() else 'DB Connection Failed!')"
```

---

## 🚀 Deployment

### Backend Deployment
- Configure production database credentials
- Set up environment variables on hosting platform
- Deploy using platforms like Heroku, Railway, or DigitalOcean

### Frontend Deployment
- Build production bundle: `npm run build`
- Deploy to Vercel, Netlify, or similar platforms
- Update CORS settings in backend for production domain

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🆘 Support

For support and questions:
- Create an issue in the GitHub repository
- Check existing documentation and FAQs
- Review the troubleshooting section below

---

## 🔧 Troubleshooting

### Common Issues

**Database Connection Error**
- Verify PostgreSQL is running
- Check database credentials in `.env`
- Ensure database and user exist

**Voice Recognition Not Working**
- Use HTTPS or localhost only
- Check browser microphone permissions
- Verify Web Speech API support

**API Key Errors**
- Validate API keys are correctly set
- Check for trailing spaces or quotes
- Verify API key permissions and quotas

## 🧪 Automated Testing
The project includes an end-to-end test suite using **Playwright** that mocks the Web Speech API to test the full logic flow.

1. Ensure the app is running: `./host_local.sh`
2. Run the tests:
   ```bash
   npm test
   ```
   *Note: On the first run, you may need to install Playwright browsers: `npx playwright install`*

---

## 🔮 Future Enhancements

- [ ] Multi-language support
- [ ] Mobile application development
- [ ] Advanced AI model integration
- [ ] Telemedicine video consultation
- [ ] Electronic health records integration
- [ ] Real-time chat support
- [ ] Advanced analytics dashboard

---

**Built with ❤️ for better healthcare accessibility**
