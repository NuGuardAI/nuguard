import { motion, AnimatePresence } from "motion/react";
import { Mic, MicOff, Send, Volume2, VolumeX, Globe } from "lucide-react";
import { useState, useRef, useEffect } from "react";
import { chatWithAgent } from "../services/gemini";
import { getPreferences, savePreferences, SUPPORTED_LANGUAGES, type UserPreferences } from "../services/userPreferences";
import type { VehicleState, CalendarEvent } from "../services/agent-types";

interface Message {
  role: 'user' | 'model';
  content: string;
  sources?: { uri: string; title: string }[];
}

interface AssistantUIProps {
  onVehicleUpdate: (updates: Partial<VehicleState>, calendarEvents?: CalendarEvent[]) => void;
  vehicleState: VehicleState;
  googleUser: { name: string; email: string; picture: string } | null;
  googleAccessToken?: string;
}

export default function AssistantUI({
  onVehicleUpdate,
  vehicleState,
  googleUser,
  googleAccessToken,
}: AssistantUIProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isListening, setIsListening] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [prefs, setPrefs] = useState<UserPreferences>(() => getPreferences());
  const [showLangMenu, setShowLangMenu] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<unknown>(null);
  const langMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    return () => {
      (recognitionRef.current as { stop?: () => void } | null)?.stop?.();
      window.speechSynthesis?.cancel();
    };
  }, []);

  useEffect(() => {
    if (!showLangMenu) return;
    const handler = (e: MouseEvent) => {
      if (langMenuRef.current && !langMenuRef.current.contains(e.target as Node)) {
        setShowLangMenu(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showLangMenu]);

  const speak = (text: string) => {
    if (!prefs.ttsEnabled || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = prefs.language;
    utter.rate = 1.0;
    const voices = window.speechSynthesis.getVoices();
    const langBase = prefs.language.split('-')[0];
    const match =
      voices.find(v => v.lang === prefs.language) ||
      voices.find(v => v.lang.startsWith(langBase));
    if (match) utter.voice = match;
    window.speechSynthesis.speak(utter);
  };

  const handleSend = async (text: string) => {
    if (!text.trim() || isProcessing) return;

    const userMsg = text.trim();
    setInput("");
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setIsProcessing(true);

    try {
      console.log("[ADK] Sending:", userMsg);
      const { text: responseText, sources, vehicleUpdates, calendarEvents } =
        await chatWithAgent(userMsg, vehicleState, prefs.language, googleAccessToken);
      console.log("[ADK] Response:", { responseText, vehicleUpdates });

      // Apply state updates to App
      if (Object.keys(vehicleUpdates ?? {}).length > 0 || calendarEvents) {
        onVehicleUpdate(vehicleUpdates ?? {}, calendarEvents);
      }

      const displayText = responseText || "Done.";
      setMessages(prev => [...prev, { role: 'model', content: displayText, sources }]);
      speak(displayText);
    } catch (err: unknown) {
      const raw: string = (err instanceof Error ? err.message : String(err)) ?? '';
      let friendlyMsg: string;
      if (raw.includes('GEMINI_API_KEY') || raw.includes('missing') || raw.toLowerCase().includes('api key')) {
        friendlyMsg = '⚠️ Gemini API key missing. Set VITE_GEMINI_API_KEY in your .env file.';
      } else if (raw.includes('404') || raw.includes('NOT_FOUND') || raw.includes('not found')) {
        friendlyMsg = `⚠️ Model not found. Check VITE_GEMINI_MODEL in your .env file.\n\nDetail: ${raw}`;
      } else if (raw.includes('PERMISSION_DENIED') || raw.includes('403') || raw.includes('API_KEY_INVALID')) {
        friendlyMsg = '⚠️ Permission denied. Verify your Gemini API key at aistudio.google.com.';
      } else if (raw.includes('RESOURCE_EXHAUSTED') || raw.includes('429') || raw.includes('quota')) {
        friendlyMsg = '⚠️ API quota exceeded. Please wait a moment and try again.';
      } else if (raw.includes('Failed to fetch') || raw.includes('NetworkError') || raw.includes('net::ERR')) {
        friendlyMsg = '⚠️ Cannot reach agent server. Run `npm run dev:server` in a separate terminal.';
      } else if (raw.includes('AbortError') || raw.includes('aborted') || raw.includes('timeout')) {
        friendlyMsg = '⚠️ Request timed out. Please try again.';
      } else if (raw.includes('sign in') || raw.includes('Google sign')) {
        friendlyMsg = `⚠️ ${raw}`;
      } else {
        friendlyMsg = `⚠️ ${raw}`;
      }
      setMessages(prev => [...prev, { role: 'model', content: friendlyMsg }]);
      speak('Sorry, I encountered an error.');
    } finally {
      setIsProcessing(false);
    }
  };

  const handleMicClick = () => {
    window.speechSynthesis?.cancel();
    if (isListening) {
      (recognitionRef.current as { stop?: () => void } | null)?.stop?.();
      setIsListening(false);
      return;
    }
    const SR = (window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown }).SpeechRecognition ||
               (window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
    if (!SR) {
      setIsListening(prev => !prev);
      return;
    }
    const rec = new (SR as new () => {
      lang: string; continuous: boolean; interimResults: boolean;
      onresult: (e: unknown) => void; onerror: (e: unknown) => void; onend: () => void;
      start: () => void;
    })();
    rec.lang = prefs.language;
    rec.continuous = false;
    rec.interimResults = true;
    rec.onresult = (e: unknown) => {
      const ev = e as { results: { [i: number]: { [j: number]: { transcript: string }; isFinal: boolean }; length: number } };
      const transcript = Array.from({ length: ev.results.length }, (_, i) => ev.results[i][0].transcript).join('');
      setInput(transcript);
      if (ev.results[ev.results.length - 1].isFinal) {
        setIsListening(false);
        handleSend(transcript);
      }
    };
    rec.onerror = (e: unknown) => {
      setIsListening(false);
      setInput('');
      const code: string = (e as { error?: string })?.error ?? '';
      let errMsg: string | null = null;
      if (code === 'not-allowed' || code === 'service-not-allowed') {
        errMsg = '⚠️ Microphone access denied. Allow microphone access in your browser settings.';
      } else if (code === 'audio-capture') {
        errMsg = '⚠️ No microphone detected. Connect a microphone and try again.';
      } else if (code === 'network') {
        errMsg = '⚠️ Voice recognition requires a network connection.';
      }
      if (errMsg) setMessages(prev => [...prev, { role: 'model', content: errMsg! }]);
    };
    rec.onend = () => setIsListening(false);
    recognitionRef.current = rec;
    rec.start();
    setIsListening(true);
  };

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-[#151619] border-r border-[#2A2B2F] overflow-hidden">
      {/* Visualizer Area */}
      <div className="flex-shrink-0 h-48 flex items-center justify-center border-b border-[#2A2B2F] relative overflow-hidden bg-gradient-to-b from-[#1E1F23] to-[#151619]">
        <div className="relative">
          <motion.div
            animate={{
              scale: isListening ? [1, 1.2, 1] : 1,
              opacity: isListening ? [0.6, 1, 0.6] : 0.6,
            }}
            transition={{ repeat: Infinity, duration: 1.5 }}
            className={`w-24 h-24 rounded-full border-2 ${isListening ? 'border-blue-500 shadow-[0_0_20px_rgba(59,130,246,0.5)]' : 'border-[#3A3B40]'} flex items-center justify-center`}
          >
            {isProcessing ? (
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
                className="w-8 h-8 border-2 border-t-blue-500 border-transparent rounded-full"
              />
            ) : (
              <Mic className={isListening ? "text-blue-500" : "text-[#3A3B40]"} size={32} />
            )}
          </motion.div>
          {isListening && (
            <div className="absolute inset-x-[-40px] top-1/2 flex items-center justify-between gap-1 opacity-50">
              {[1, 2, 3, 4, 5, 4, 3, 2, 1].map((h, i) => (
                <motion.div
                  key={i}
                  animate={{ height: [8, h * 10, 8] }}
                  transition={{ repeat: Infinity, duration: 0.5, delay: i * 0.1 }}
                  className="w-1 bg-blue-500 rounded-full"
                />
              ))}
            </div>
          )}
        </div>
        <div className="absolute top-4 left-4 flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
          <span className="text-[10px] font-mono uppercase text-[#8E9299] tracking-wider">System Online</span>
        </div>

        {/* Voice & Language Controls */}
        <div className="absolute top-3 right-3 flex items-center gap-1">
          <button
            onClick={() => {
              const updated = savePreferences({ ttsEnabled: !prefs.ttsEnabled });
              setPrefs(updated);
              if (!updated.ttsEnabled) window.speechSynthesis?.cancel();
            }}
            title={prefs.ttsEnabled ? 'Mute voice responses' : 'Enable voice responses'}
            className="p-1.5 rounded-lg text-[#8E9299] hover:text-white hover:bg-[#2A2B2F] transition-colors"
          >
            {prefs.ttsEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
          </button>
          <div ref={langMenuRef} className="relative">
            <button
              onClick={() => setShowLangMenu(prev => !prev)}
              title="Change language"
              className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-[#8E9299] hover:text-white hover:bg-[#2A2B2F] transition-colors"
            >
              <Globe size={12} />
              <span className="text-[9px] font-mono">
                {SUPPORTED_LANGUAGES.find(l => l.code === prefs.language)?.flag ?? '🌐'}
              </span>
            </button>
            <AnimatePresence>
              {showLangMenu && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95, y: -4 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95, y: -4 }}
                  transition={{ duration: 0.1 }}
                  className="absolute right-0 top-9 z-50 bg-[#1E1F23] border border-[#2A2B2F] rounded-xl p-1.5 shadow-2xl min-w-[140px]"
                >
                  {SUPPORTED_LANGUAGES.map(lang => (
                    <button
                      key={lang.code}
                      onClick={() => {
                        (recognitionRef.current as { stop?: () => void } | null)?.stop?.();
                        const updated = savePreferences({ language: lang.code });
                        setPrefs(updated);
                        setShowLangMenu(false);
                      }}
                      className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[11px] transition-colors text-left ${
                        prefs.language === lang.code
                          ? 'bg-blue-600/20 text-blue-400'
                          : 'text-[#8E9299] hover:bg-[#2A2B2F] hover:text-white'
                      }`}
                    >
                      <span>{lang.flag}</span>
                      <span className="font-mono">{lang.label}</span>
                    </button>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>

      {/* Chat Area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0 p-4 space-y-4 [&::-webkit-scrollbar]:w-1 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-[#3A3B40] [&::-webkit-scrollbar-thumb]:rounded-full">
        <AnimatePresence>
          {messages.length === 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-center mt-10"
            >
              <p className="text-[#8E9299] text-sm italic font-sans">"How can I help you today?"</p>
              <div className="mt-8 grid grid-cols-1 gap-2">
                {[
                  "Check my tire pressure",
                  "Find a nearby EV charging station",
                  "Add a stop at Starbucks",
                  "What's for dinner nearby?",
                  "Set my temp to 22 degrees",
                  "Play Muse",
                  ...(googleUser
                    ? [
                        "Show my upcoming events",
                        `Send an email to ${googleUser.email}`,
                        "Create a calendar event for tomorrow at 3pm",
                      ]
                    : [
                        "Show my upcoming calendar events",
                        "Send an email to a contact",
                      ]),
                ].map((hint, i) => (
                  <button
                    key={i}
                    onClick={() => handleSend(hint)}
                    className="text-[11px] text-[#8E9299] hover:text-white border border-[#2A2B2F] hover:bg-[#2A2B2F] rounded-lg py-2 px-3 transition-colors text-left font-mono"
                  >
                    "{hint}"
                  </button>
                ))}
              </div>
            </motion.div>
          )}
          {messages.map((m, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div className={`max-w-[85%] rounded-2xl text-xs overflow-hidden ${
                m.role === 'user'
                  ? 'bg-blue-600 text-white rounded-tr-none'
                  : 'bg-[#2A2B2F] text-[#E0E0E0] rounded-tl-none'
              }`}>
                <div className={`p-3 ${m.role === 'model' ? 'font-sans leading-relaxed' : ''}`}>{m.content}</div>
                {m.role === 'model' && m.sources && m.sources.length > 0 && (
                  <div className="border-t border-[#3A3B40] px-3 py-2 space-y-1">
                    <span className="text-[9px] text-[#8E9299] uppercase font-mono tracking-wider">Sources</span>
                    {m.sources.map((s, si) => (
                      <a
                        key={si}
                        href={s.uri}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={s.uri}
                        className="block text-[10px] text-blue-400 hover:text-blue-300 truncate transition-colors"
                      >
                        {s.title}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {/* Input Area */}
      <div className="flex-shrink-0 p-4 border-t border-[#2A2B2F] bg-[#1E1F23]">
        <div className="flex gap-2 items-center bg-[#151619] rounded-xl border border-[#2A2B2F] p-1 pr-2">
          <button
            onClick={handleMicClick}
            title={isListening ? 'Stop listening' : 'Start voice input'}
            className={`p-3 rounded-lg transition-colors ${isListening ? 'bg-blue-500/20 text-blue-500' : 'text-[#8E9299] hover:bg-[#2A2B2F]'}`}
          >
            {isListening ? <Mic size={20} /> : <MicOff size={20} />}
          </button>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend(input)}
            placeholder="Ask anything..."
            className="flex-1 bg-transparent border-none outline-none text-sm text-white placeholder-[#8E9299] py-3 px-1"
          />
          <button
            onClick={() => handleSend(input)}
            disabled={!input.trim() || isProcessing}
            className="p-2 text-blue-500 disabled:text-[#3A3B40] transition-colors"
          >
            <Send size={20} />
          </button>
        </div>
      </div>
    </div>
  );
}
