"use client";

import { useEffect, useState } from "react";
import { AgentPanel } from "@/components/agent-panel";
import { Chat } from "@/components/Chat";
import { LoginForm } from "@/components/login-form";
import type { Agent, AgentEvent, GuardrailCheck, Message } from "@/lib/types";
import { callChatAPI, callLogoutAPI } from "@/lib/api";

interface AuthUser {
  name: string;
  account_number: string;
  email: string;
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [currentAgent, setCurrentAgent] = useState<string>("");
  const [guardrails, setGuardrails] = useState<GuardrailCheck[]>([]);
  const [context, setContext] = useState<Record<string, any>>({});
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Auth state
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);

  const handleLogin = (token: string, user: AuthUser) => {
    setAuthToken(token);
    setAuthUser(user);
  };

  const handleLogout = async () => {
    if (authToken) await callLogoutAPI(authToken);
    setAuthToken(null);
    setAuthUser(null);
    setMessages([]);
    setEvents([]);
    setAgents([]);
    setConversationId(null);
    setContext({});
  };

  // Boot the conversation once authenticated
  useEffect(() => {
    if (!authToken) return;
    (async () => {
      const data = await callChatAPI("", conversationId ?? "", authToken);
      if (!data) return;
      setConversationId(data.conversation_id);
      setCurrentAgent(data.current_agent);
      setContext(data.context);
      const initialEvents = (data.events || []).map((e: any) => ({
        ...e,
        timestamp: e.timestamp ?? Date.now(),
      }));
      setEvents(initialEvents);
      setAgents(data.agents || []);
      setGuardrails(data.guardrails || []);
      if (Array.isArray(data.messages)) {
        setMessages(
          data.messages.map((m: any) => ({
            id: Date.now().toString() + Math.random().toString(),
            content: m.content,
            role: "assistant",
            agent: m.agent,
            timestamp: new Date(),
          }))
        );
      }
    })();
  }, [authToken]);

  // Send a user message
  const handleSendMessage = async (content: string) => {
    const userMsg: Message = {
      id: Date.now().toString(),
      content,
      role: "user",
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    const data = await callChatAPI(content, conversationId ?? "", authToken ?? undefined);

    if (!data) {
      setIsLoading(false);
      return;
    }

    if (!conversationId) setConversationId(data.conversation_id);
    setCurrentAgent(data.current_agent);
    setContext(data.context);
    if (data.events) {
      const stamped = data.events.map((e: any) => ({
        ...e,
        timestamp: e.timestamp ?? Date.now(),
      }));
      setEvents((prev) => [...prev, ...stamped]);
    }
    if (data.agents) setAgents(data.agents);
    if (data.guardrails) setGuardrails(data.guardrails);

    if (data.messages) {
      const responses: Message[] = data.messages.map((m: any) => ({
        id: Date.now().toString() + Math.random().toString(),
        content: m.content,
        role: "assistant",
        agent: m.agent,
        timestamp: new Date(),
      }));
      setMessages((prev) => [...prev, ...responses]);
    }

    setIsLoading(false);
  };

  if (!authToken) {
    return <LoginForm onLogin={handleLogin} />;
  }

  return (
    <main className="flex h-screen gap-2 bg-gray-100 p-2">
      <AgentPanel
        agents={agents}
        currentAgent={currentAgent}
        events={events}
        guardrails={guardrails}
        context={context}
      />
      <div className="flex flex-1 flex-col min-w-0">
        {/* User bar */}
        <div className="flex items-center justify-between rounded-lg bg-white border px-4 py-2 mb-2 text-sm">
          <span className="text-gray-600">
            Signed in as <span className="font-semibold text-gray-900">{authUser?.name}</span>
            <span className="ml-2 text-gray-400 font-mono text-xs">#{authUser?.account_number}</span>
          </span>
          <button
            onClick={handleLogout}
            className="text-xs text-gray-500 hover:text-gray-900 underline underline-offset-2 transition-colors"
          >
            Sign out
          </button>
        </div>
        <Chat
          messages={messages}
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
        />
      </div>
    </main>
  );
}
