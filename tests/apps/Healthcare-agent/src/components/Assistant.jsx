import { useState, useRef, useContext, useEffect } from "react";
import "./Assistant.css";
import { datacontext } from "../context/UserContext";

export default function Assistant() {
  const {connect, disconnect, aiResponse, messages, status, error, clearError, isMicActive}=useContext(datacontext)
  const [inputText, setInputText] = useState("");
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleManualSubmit = (e) => {
    e.preventDefault();
    if (inputText.trim()) {
      aiResponse(inputText);
      setInputText("");
    }
  };
  
  return (
    <div className="assistant-wrapper">
      <div className="left-panel">
        <div className="assistant-header">🩺 AI Medical Assistant</div>
        
        {error && (
          <div className="error-banner" style={{ backgroundColor: '#ffe6e6', padding: '10px', color: '#d93025', display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '10px', borderRadius: '5px' }}>
            <span>⚠️ {error}</span>
            <button onClick={clearError} style={{ background: 'none', border: 'none', cursor: 'pointer', fontWeight: 'bold' }}>✕</button>
          </div>
        )}

        <div className="assistant-chat">
          <img
            src="/assistant.jpg"
            alt="Medical Assistant"
            className="assistant-image"
          />
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: '10px' }}>
             {isMicActive && <div className="mic-active-badge" style={{ color: 'red', fontWeight: 'bold', fontSize: '0.8rem' }}>🎙️ MIC ACTIVE</div>}
             {status !== "Idle" && <div className="status-indicator" style={{ fontStyle: 'italic', color: '#0066cc', fontWeight: 'bold' }}>● {status}</div>}
          </div>
        </div>

        <div className="assistant-footer">
          <button onClick={connect} className="btn-connect">Connect Voice</button>
          <button onClick={disconnect} className="btn-disconnect">Disconnect & Analyze</button>
        </div>
      </div>

      {/* Message Section */}
      <div className="right-panel">
        <div className="messages-container" style={{ display: 'flex', flexDirection: 'column' }}>
          {messages.length > 0 ? (
            messages.map((message, index) => (
              <div key={index} className={`message ${message.sender.toLowerCase()}`}>
                <strong>{message.sender}: </strong>
                <span>{message.text}</span>
              </div>
            ))
          ) : (
            <p className="no-messages">No messages yet. Speak or type your symptoms below.</p>
          )}
          <div ref={messagesEndRef} />
        </div>
        
        <form onSubmit={handleManualSubmit} className="manual-input-form" style={{ marginTop: '10px', display: 'flex', gap: '5px' }}>
          <input 
            type="text" 
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="Type your symptoms here..."
            style={{ flexGrow: 1, padding: '10px', borderRadius: '5px', border: '1px solid #ccc' }}
          />
          <button type="submit" style={{ backgroundColor: '#0066cc', color: 'white' }}>Send</button>
        </form>
      </div>
    </div>
  );
}
