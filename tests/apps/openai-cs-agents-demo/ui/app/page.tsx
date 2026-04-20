"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { AgentPanel } from "@/components/agent-panel";
import { Chat } from "@/components/Chat";
import type { Agent, AgentEvent, GuardrailCheck, Message } from "@/lib/types";
import { callChatAPI } from "@/lib/api";
import type { BasicAuthCredentials } from "@/lib/api";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [currentAgent, setCurrentAgent] = useState<string>("");
  const [guardrails, setGuardrails] = useState<GuardrailCheck[]>([]);
  const [context, setContext] = useState<Record<string, any>>({});
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [auth, setAuth] = useState<BasicAuthCredentials | null>(null);
  const [username, setUsername] = useState("avery");
  const [password, setPassword] = useState("avery-pass");
  const [authError, setAuthError] = useState("");
  // Loading state while awaiting assistant response
  const [isLoading, setIsLoading] = useState(false);

  const bootConversation = async (credentials: BasicAuthCredentials) => {
    const data = await callChatAPI("", "", credentials);
    if (!data) return false;
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
    return true;
  };

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAuthError("");
    setIsLoading(true);
    const credentials = { username, password };
    const ok = await bootConversation(credentials);
    setIsLoading(false);
    if (!ok) {
      setAuthError("Invalid username or password.");
      return;
    }
    setAuth(credentials);
  };

  // Send a user message
  const handleSendMessage = async (content: string) => {
    if (!auth) return;
    const userMsg: Message = {
      id: Date.now().toString(),
      content,
      role: "user",
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    const data = await callChatAPI(content, conversationId ?? "", auth);

    if (!data) {
      setIsLoading(false);
      return; // Handle API error
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
    // Update guardrails state
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

  if (!auth) {
    return (
      <main className="flex h-screen items-center justify-center bg-gray-100 p-4">
        <form
          onSubmit={handleLogin}
          className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
        >
          <h1 className="mb-1 text-lg font-semibold text-zinc-900">
            Airline customer login
          </h1>
          <p className="mb-4 text-sm text-zinc-500">
            Try a seeded account such as avery / avery-pass.
          </p>
          <label className="mb-3 block text-sm text-zinc-700">
            Username
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
            />
          </label>
          <label className="mb-4 block text-sm text-zinc-700">
            Password
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>
          {authError && <p className="mb-3 text-sm text-red-600">{authError}</p>}
          <button
            className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            disabled={isLoading}
            type="submit"
          >
            {isLoading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </main>
    );
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
      <Chat
        messages={messages}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
      />
    </main>
  );
}
