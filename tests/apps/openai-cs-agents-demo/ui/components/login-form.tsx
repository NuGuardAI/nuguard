"use client";

import { useState } from "react";
import { callLoginAPI } from "@/lib/api";

interface LoginFormProps {
  onLogin: (token: string, user: { name: string; account_number: string; email: string }) => void;
}

const DEMO_USERS = [
  { username: "alice",  hint: "Alice Johnson – 2 flights (Delta, United)" },
  { username: "bob",    hint: "Bob Smith – 2 flights (American, Southwest)" },
  { username: "carol",  hint: "Carol White – 1 flight (JetBlue)" },
  { username: "david",  hint: "David Brown – 2 flights (Delta, American)" },
  { username: "eva",    hint: "Eva Martinez – 1 flight (United)" },
];

export function LoginForm({ onLogin }: LoginFormProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]     = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await callLoginAPI(username.trim(), password);
      if (!data) {
        setError("Invalid username or password.");
        return;
      }
      onLogin(data.token, { name: data.name, account_number: data.account_number, email: data.email });
    } catch {
      setError("Login failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const fillDemo = (u: string) => {
    setUsername(u);
    setPassword(`${u}123`);
    setError("");
  };

  return (
    <div className="flex h-screen items-center justify-center bg-gray-100">
      <div className="w-full max-w-md rounded-xl border bg-white shadow-md p-8 space-y-6">
        {/* Header */}
        <div className="text-center space-y-1">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/openai_logo.svg" alt="OpenAI" className="mx-auto h-8 mb-3" />
          <h1 className="text-2xl font-semibold tracking-tight">Airline Customer Service</h1>
          <p className="text-sm text-gray-500">Sign in to manage your flights</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1">
            <label className="block text-sm font-medium text-gray-700" htmlFor="username">
              Username
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-black focus:outline-none focus:ring-1 focus:ring-black"
              placeholder="e.g. alice"
            />
          </div>
          <div className="space-y-1">
            <label className="block text-sm font-medium text-gray-700" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-black focus:outline-none focus:ring-1 focus:ring-black"
              placeholder="e.g. alice123"
            />
          </div>

          {error && (
            <p className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-600">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-black px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50 transition-colors"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {/* Demo accounts */}
        <div className="space-y-2">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Demo accounts</p>
          <div className="divide-y divide-gray-100 rounded-md border border-gray-200 overflow-hidden text-sm">
            {DEMO_USERS.map(({ username: u, hint }) => (
              <button
                key={u}
                type="button"
                onClick={() => fillDemo(u)}
                className="flex w-full items-center justify-between px-3 py-2 hover:bg-gray-50 transition-colors text-left"
              >
                <span className="font-mono font-medium text-gray-800">{u} / {u}123</span>
                <span className="text-xs text-gray-400 ml-2 truncate">{hint}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
