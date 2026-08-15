/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect } from "react";
import AssistantUI from "./components/AssistantUI";
import VehicleStats from "./components/VehicleStats";
import MapSimulator from "./components/MapSimulator";
import { Settings, Bell, Radio, LogIn, LogOut } from "lucide-react";

import { initGoogleAuth, requestAccessToken, clearToken, fetchUserProfile, type GoogleUser } from "./services/google-auth";
import { getPreferences, savePreferences } from "./services/userPreferences";
import { listRecentEmails } from "./services/google-apis";
import type { VehicleState, CalendarEvent, EmailMessage } from "./services/agent-types";

export default function App() {
  const _savedPrefs = getPreferences();
  const [vehicleState, setVehicleState] = useState<VehicleState>({
    fuel: 18,
    temp: _savedPrefs.temp ?? 21,
    tirePressure: [32, 32, 28, 31],
    music: _savedPrefs.music ?? { playing: true, track: "Starlight", artist: "Muse", provider: 'simulated' },
    battery: 85,
    destination: null,
    stops: [],
    navMetadata: null,
    weather: { temp: 22, condition: "Sunny", location: "Current Location" },
  });

  const [googleUser, setGoogleUser] = useState<GoogleUser | null>(null);
  const [googleAccessToken, setGoogleAccessToken] = useState<string | undefined>(undefined);
  const [upcomingEvents, setUpcomingEvents] = useState<CalendarEvent[]>([]);
  const [recentEmails, setRecentEmails] = useState<EmailMessage[]>([]);

  useEffect(() => {
    initGoogleAuth();
  }, []);

  const handleGoogleSignIn = async () => {
    try {
      const token = await requestAccessToken();
      setGoogleAccessToken(token);
      const user = await fetchUserProfile(token);
      setGoogleUser(user);
      listRecentEmails(5).then(setRecentEmails).catch(e => console.warn('Email fetch failed:', e));
    } catch (err) {
      console.error("Google sign-in failed:", err);
    }
  };

  const handleGoogleSignOut = () => {
    clearToken();
    setGoogleUser(null);
    setGoogleAccessToken(undefined);
    setUpcomingEvents([]);
    setRecentEmails([]);
  };

  /** Applied when the ADK agent returns tool-driven state changes. */
  const handleVehicleUpdate = (updates: Partial<VehicleState>, calendarEvents?: CalendarEvent[]) => {
    if (Object.keys(updates).length > 0) {
      setVehicleState(prev => ({ ...prev, ...updates }));
      if (updates.temp !== undefined) savePreferences({ temp: updates.temp });
      if (updates.music) savePreferences({ music: updates.music as ReturnType<typeof getPreferences>['music'] });
    }
    if (calendarEvents) {
      setUpcomingEvents(calendarEvents);
    }
  };

  const handleTogglePlay = () => {
    setVehicleState(prev => ({
      ...prev,
      music: { ...prev.music, playing: !prev.music.playing },
    }));
  };

  const handleNextTrack = () => {
    setVehicleState(prev => ({
      ...prev,
      music: { ...prev.music, track: "Next Track", artist: "Artist" },
    }));
  };

  const handlePrevTrack = () => {
    setVehicleState(prev => ({
      ...prev,
      music: { ...prev.music, track: "Previous Track", artist: "Artist" },
    }));
  };

  return (
    <div className="flex h-screen w-full bg-[#0D0E10] text-[#E0E0E0] font-sans selection:bg-blue-500/30 overflow-hidden">
      {/* Sidebar Rail */}
      <nav className="w-16 border-r border-[#2A2B2F] flex flex-col items-center py-8 gap-8 bg-[#151619]">
        <div className="p-2 bg-blue-600 rounded-xl shadow-[0_0_15px_rgba(59,130,246,0.5)]">
          <Radio className="text-white" size={24} />
        </div>
        {googleUser ? (
          <div className="flex flex-col items-center gap-1 group">
            <button
              onClick={handleGoogleSignOut}
              title={`Signed in as ${googleUser.email}\nClick to sign out`}
              className="relative w-9 h-9 rounded-full overflow-hidden border-2 border-green-500/50 hover:border-red-500/50 transition-colors"
            >
              <img src={googleUser.picture} alt={googleUser.name} className="w-full h-full object-cover" referrerPolicy="no-referrer" />
              <div className="absolute inset-0 flex items-center justify-center bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity">
                <LogOut size={14} className="text-red-400" />
              </div>
            </button>
            <div className="w-2 h-2 rounded-full bg-green-500"></div>
          </div>
        ) : (
          <button
            onClick={handleGoogleSignIn}
            title="Sign in with Google"
            className="p-2 text-[#8E9299] hover:text-white transition-colors"
          >
            <LogIn size={24} />
          </button>
        )}
        <button className="relative p-2 text-[#8E9299] hover:text-white transition-colors">
          <Bell size={24} />
          <div className="absolute top-2 right-2 w-2 h-2 bg-red-500 rounded-full border border-[#151619]"></div>
        </button>
        <div className="flex-1"></div>
        <button className="p-2 text-[#8E9299] hover:text-white transition-colors">
          <Settings size={24} />
        </button>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 flex overflow-hidden">
        {/* Left: Assistant interaction */}
        <section className="w-1/3 flex flex-col overflow-hidden">
          <AssistantUI
            onVehicleUpdate={handleVehicleUpdate}
            vehicleState={vehicleState}
            googleUser={googleUser}
            googleAccessToken={googleAccessToken}
          />
        </section>

        {/* Center: Vehicle Telematics */}
        <section className="w-1/3 flex flex-col border-r border-[#2A2B2F] overflow-hidden">
          <VehicleStats
            stats={vehicleState}
            onTogglePlay={handleTogglePlay}
            onNextTrack={handleNextTrack}
            onPrevTrack={handlePrevTrack}
            googleUser={googleUser}
            upcomingEvents={upcomingEvents}
            recentEmails={recentEmails}
          />
        </section>

        {/* Right: Maps & Navigation */}
        <section className="w-1/3 flex flex-col overflow-hidden">
          <MapSimulator
            destination={vehicleState.destination}
            stops={vehicleState.stops}
            navMetadata={vehicleState.navMetadata}
            onRouteUpdate={(data) => {
              if (data) {
                setVehicleState(prev => ({
                  ...prev,
                  navMetadata: { distance: data.distance, eta: data.duration },
                }));
              }
            }}
          />
        </section>
      </main>

      {/* Status Bar */}
      <div className="fixed top-0 right-0 p-4 flex items-center gap-4 bg-[#151619]/20 backdrop-blur-sm rounded-bl-2xl border-l border-b border-[#2A2B2F] z-50">
        <div className="flex flex-col items-end">
          <span className="text-[10px] font-mono text-[#8E9299]">GEMINI AUTO PLATFORM</span>
          <span className="text-[10px] font-mono text-[#3B82F6]">v3.1-PROXIMA · ADK</span>
        </div>
      </div>
    </div>
  );
}
