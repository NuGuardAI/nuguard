import React, { createContext, useRef, useState } from "react";
import run from "../gemini";
import { useNavigate } from "react-router-dom";

export const datacontext = createContext();

function UserContext({ children }) {
    const isListening = useRef(false);
    const isPausedForTTS = useRef(false);
    const recognitionRef = useRef(null);
    const [messages, setMessages] = useState([]);
    const [status, setStatus] = useState("Idle");
    const [error, setError] = useState(null);
    const [isMicActive, setIsMicActive] = useState(false); // Track real-time mic status
    const navigate = useNavigate();

    const BACKEND_URL = import.meta.env.VITE_BACKEND_URL;

    function speak(text) {
        if (!window.speechSynthesis) {
            console.error("Speech Synthesis not supported.");
            return;
        }

        // Always cancel previous speech to avoid queueing or weird interruptions
        window.speechSynthesis.cancel();

        const text_speak = new SpeechSynthesisUtterance(text);
        
        // Use a ref to keep a reference to the utterance. 
        // Some browsers garbage collect it mid-speech if not referenced.
        window.currentUtterance = text_speak; 

        text_speak.volume = 1;
        text_speak.rate = 1;
        text_speak.pitch = 1;
        text_speak.lang = "en-GB";

        // Stop recognition before speaking
        if (isListening.current && recognitionRef.current) {
            isPausedForTTS.current = true;
            console.log("Stopping voice recognition for TTS...");
            try {
                recognitionRef.current.stop();
            } catch (e) {
                console.warn("Recognition already stopped or error:", e);
            }
        }
        setStatus("Speaking");

        text_speak.onerror = (event) => {
            if (event.error === 'interrupted') {
                console.log("Speech was interrupted.");
                return;
            }
            console.error("SpeechSynthesisUtterance error", event);
            setStatus("Idle");
            isPausedForTTS.current = false;
        };

        text_speak.onend = () => {
            console.log("Speech ended");
            isPausedForTTS.current = false;
            window.currentUtterance = null;
            if (isListening.current && recognitionRef.current) {
                console.log("Restarting recognition after TTS...");
                try {
                    recognitionRef.current.start();
                    setStatus("Listening");
                } catch (e) {
                    console.error("Failed to restart recognition after TTS:", e);
                    // If it fails immediately (e.g. still stopping), try again in 300ms
                    setTimeout(() => {
                        if (isListening.current && !isPausedForTTS.current) {
                            try { recognitionRef.current.start(); setStatus("Listening"); } catch (err) {}
                        }
                    }, 300);
                }
            } else {
                setStatus("Idle");
            }
        };

        window.speechSynthesis.speak(text_speak);
    }

    const aiResponseRef = useRef(null);

    const isProcessing = useRef(false);

    async function aiResponse(prompt) {
        if (!prompt || !prompt.trim()) return;
        if (isProcessing.current) {
            console.log("Already processing a request, ignoring:", prompt);
            return;
        }
        
        isProcessing.current = true;
        console.log("aiResponse triggered with:", prompt);
        setMessages(prev => [...prev, { sender: "Patient", text: prompt }]);

        try {
            console.log("Calling Gemini for:", prompt);
            setStatus("Thinking...");
            const text = await run(prompt);
            console.log("Gemini response:", text);
            let cleanedText = text.replace(/^Agent:\s*/i, "").trim();

            setMessages(prev => [...prev, { sender: "Assistant", text: cleanedText }]);
            speak(cleanedText);
        } catch (err) {
            console.error("AI Response error:", err);
            setError("Failed to get response from AI assistant. Please try again.");
            setStatus("Idle");
        } finally {
            isProcessing.current = false;
        }
    }

    // Keep the ref updated with the latest aiResponse function
    React.useEffect(() => {
        aiResponseRef.current = aiResponse;
    });

    React.useEffect(() => {
        if (!recognitionRef.current) {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                console.error("Speech Recognition is not supported in this browser.");
                setError("Speech Recognition is not supported in this browser. Ensure you are using Chrome/Edge and accessing via HTTPS or localhost.");
                return;
            }
            const recognition = new SpeechRecognition();
            recognition.continuous = true;
            recognition.interimResults = false;
            recognition.lang = "en-US";

            recognition.onstart = () => {
                setStatus("Listening");
                setIsMicActive(true);
                console.log("Recognition lifecycle: started");
            };

            recognition.onresult = (e) => {
                // Get all results since the last event index
                let fullTranscript = "";
                for (let i = e.resultIndex; i < e.results.length; i++) {
                    if (e.results[i].isFinal) {
                        fullTranscript += e.results[i][0].transcript;
                    }
                }
                
                if (fullTranscript.trim()) {
                    console.log("Patient said (onresult):", fullTranscript);
                    if (aiResponseRef.current) {
                        aiResponseRef.current(fullTranscript);
                    }
                }
            };

            recognition.onend = () => {
                console.log("Recognition lifecycle: ended");
                setIsMicActive(false);

                // Auto-restart if we intended to be listening and not in the middle of a TTS pause
                if (isListening.current && !isPausedForTTS.current) {
                    console.log("Unexpected end of recognition, restarting...");
                    try {
                        recognition.start();
                    } catch (e) {
                        console.warn("Failed to restart recognition immediately in onend:", e);
                        // Retry with backup delay
                        setTimeout(() => {
                            if (isListening.current && !isPausedForTTS.current) {
                                try { recognition.start(); } catch (err) {}
                            }
                        }, 500);
                    }
                } else if (!isListening.current) {
                    setStatus("Idle");
                }
            };

            recognition.onerror = (e) => {
                console.error("Recognition lifecycle error:", e.error);
                
                if (e.error === "no-speech") {
                    // This is common and usually just means a long pause. Continuous mode handle this better but onend might fire.
                    return;
                }
                
                if (e.error === "not-allowed") {
                    setError("Microphone access was denied. Please check your browser permissions.");
                    isListening.current = false;
                } else if (e.error === "network") {
                    setError("Network error occurred during speech recognition.");
                    isListening.current = false;
                } else if (e.error === "aborted") {
                    console.log("Recognition was aborted.");
                } else {
                    // For other fatal errors, we should stop trying to listen
                    // isListening.current = false; // Optional: keep it true to allow automatic restart if transient
                }
                
                setStatus("Idle");
            };

            recognitionRef.current = recognition;
        }
    }, []);

    function connect() {
        if (!recognitionRef.current) {
             setError("Speech recognition is not available.");
             return;
        }
        if (isMicActive) {
            console.log("Mic is already active");
            return;
        }
        
        console.log("Connect button clicked, starting mic...");
        isListening.current = true;
        isPausedForTTS.current = false;
        setError(null);
        
        try {
            recognitionRef.current.start();
            console.log("Mic start() command sent");
        } catch (e) {
            console.error("Critical failure during mic start:", e);
            if (e.message.includes("already started")) {
                setIsMicActive(true);
                setStatus("Listening");
            } else {
                setError(`Failed to start microphone: ${e.message}`);
                isListening.current = false;
            }
        }
    }

    async function disconnect() {
        console.log("Disconnect button clicked, cleaning up...");
        isListening.current = false;
        isPausedForTTS.current = false;
        
        try {
            if (recognitionRef.current) {
                recognitionRef.current.stop();
            }
        } catch (e) {
            console.error("Error stopping recognition:", e);
        }
        
        window.speechSynthesis.cancel();
        setStatus("Idle");
        setIsMicActive(false);
        console.log("Mic and Speech stopped");

        console.log("Full Conversation Messages:", messages);

        const patientMessages = messages
            .filter(msg => msg.sender === "Patient")
            .map(msg => msg.text)
            .join(" ");

        const symptomPhrases = patientMessages
            .split(/[.?!]/)
            .map(s => s.trim())
            .filter(Boolean);

        console.log("Extracted Phrases:", symptomPhrases);

        if (symptomPhrases.length === 0) {
            console.warn("No symptoms detected to send to LangGraph.");
            setError("No symptoms detected. Please speak your symptoms before disconnecting.");
            setStatus("Idle");
            return;
        }

        try {
            console.log("Sending phrases to LangGraph:", symptomPhrases);
            setError(null);
            setStatus("Analyzing symptoms...");
            const response = await fetch(`${BACKEND_URL}/run_langgraph`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ phrases: symptomPhrases }),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || "Analysis failed");
            }

            const data = await response.json();
            console.log("Received LangGraph response:", data);
            
            if (!data.doctors || data.doctors.length === 0) {
                 console.warn("No doctors found for these symptoms.");
            }
            
            navigate("/recommendation", { state: data });

        } catch (error) {
            console.error("Error during LangGraph execution:", error);
            setError(`Diagnosis failed: ${error.message}. Please try again.`);
            setStatus("Idle");
        }
    }

    function clearError() {
        setError(null);
    }

    React.useEffect(() => {
        return () => {
            window.speechSynthesis.cancel();
            if (recognitionRef.current) {
                try {
                    recognitionRef.current.stop();
                } catch (e) {}
            }
        };
    }, []);

    const value = {
        connect,
        disconnect,
        aiResponse,
        messages,
        status,
        error,
        clearError,
        isMicActive,
    };

    return (
        <datacontext.Provider value={value}>
            {children}
        </datacontext.Provider>
    );
}

export default UserContext;
