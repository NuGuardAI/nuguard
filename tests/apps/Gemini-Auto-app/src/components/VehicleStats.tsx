import { motion } from "motion/react";
import { useState, useEffect, useRef } from "react";
import { Thermometer, Fuel, Gauge, Music, Info, AlertTriangle, Play, Pause, SkipBack, SkipForward, Navigation, Calendar, Mail } from "lucide-react";
import type { GoogleUser } from "../services/google-auth";
import type { CalendarEvent } from "../services/google-apis";
import type { EmailMessage } from "../services/agent-types";

interface VehicleStatsProps {
  stats: {
    fuel: number;
    temp: number;
    tirePressure: number[];
    music: { playing: boolean; track: string; artist: string; provider?: string };
    battery: number;
    destination: string | null;
    stops: string[];
    navMetadata: { distance: string, eta: string, nextTurn?: string } | null;
    weather: { temp: number; condition: string; location: string };
  };
  onTogglePlay: () => void;
  onNextTrack: () => void;
  onPrevTrack: () => void;
  googleUser: GoogleUser | null;
  upcomingEvents: CalendarEvent[];
  recentEmails: EmailMessage[];
}

function formatEventTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const isTomorrow = d.toDateString() === tomorrow.toDateString();
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  if (isToday) return `Today ${time}`;
  if (isTomorrow) return `Tomorrow ${time}`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ` ${time}`;
}

function formatEmailFrom(from: string): string {
  const match = from.match(/^"?([^"<]+)"?\s*</);
  return (match ? match[1].trim() : from.replace(/[<>]/g, '').trim()) || from;
}

function formatEmailDate(dateStr: string): string {
  try {
    const diff = Date.now() - new Date(dateStr).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  } catch { return ''; }
}

// ── Speedometer SVG ──────────────────────────────────────────────────────────
// Arc: 225° to 465° (=105°) clockwise = 240° total, measured from 12 o'clock.
function SpeedometerSVG({ speed, maxSpeed = 120 }: { speed: number; maxSpeed?: number }) {
  const cx = 80, cy = 70, r = 50;
  const startAngle = 225, totalArc = 240;
  const ratio = Math.min(Math.max(speed / maxSpeed, 0), 1);

  const pt = (deg: number, radius = r) => ({
    x: cx + radius * Math.sin((deg * Math.PI) / 180),
    y: cy - radius * Math.cos((deg * Math.PI) / 180),
  });

  const arcD = (from: number, to: number, radius = r) => {
    const s = pt(from, radius), e = pt(to, radius);
    const span = ((to - from) % 360 + 360) % 360;
    return `M${s.x.toFixed(2)},${s.y.toFixed(2)} A${radius},${radius} 0 ${span > 180 ? 1 : 0} 1 ${e.x.toFixed(2)},${e.y.toFixed(2)}`;
  };

  const fillEndAngle = startAngle + ratio * totalArc;
  const needleTip = pt(fillEndAngle, r - 14);
  const nb1 = pt(fillEndAngle - 6, 8);
  const nb2 = pt(fillEndAngle + 6, 8);

  const strokeColor = speed > maxSpeed * 0.8 ? '#EF4444' : speed > maxSpeed * 0.55 ? '#F59E0B' : '#3B82F6';

  const ticks = Array.from({ length: 9 }, (_, i) => {
    const angle = startAngle + (i / 8) * totalArc;
    return { o: pt(angle, r - 2), i: pt(angle, i % 2 === 0 ? r - 11 : r - 7), major: i % 2 === 0 };
  });

  return (
    <svg viewBox="0 0 160 108" className="w-full h-full">
      {/* Background track */}
      <path d={arcD(startAngle, startAngle + totalArc)} fill="none" stroke="#2A2B2F" strokeWidth="7" strokeLinecap="round" />
      {/* Speed fill */}
      {ratio > 0.01 && (
        <path d={arcD(startAngle, fillEndAngle)} fill="none" stroke={strokeColor} strokeWidth="7" strokeLinecap="round" />
      )}
      {/* Tick marks */}
      {ticks.map((t, i) => (
        <line key={i} x1={t.o.x} y1={t.o.y} x2={t.i.x} y2={t.i.y}
          stroke={t.major ? '#6B7280' : '#3A3B40'} strokeWidth={t.major ? 1.5 : 0.8} />
      ))}
      {/* Needle */}
      <polygon points={`${needleTip.x},${needleTip.y} ${nb1.x},${nb1.y} ${nb2.x},${nb2.y}`} fill={strokeColor} />
      {/* Hub */}
      <circle cx={cx} cy={cy} r={5} fill={strokeColor} />
      <circle cx={cx} cy={cy} r={2.5} fill="#151619" />
    </svg>
  );
}

export default function VehicleStats({ stats, onTogglePlay, onNextTrack, onPrevTrack, googleUser, upcomingEvents, recentEmails }: VehicleStatsProps) {
  const [speed, setSpeed] = useState(52);
  const [odometer, setOdometer] = useState(24856.3);
  const speedTargetRef = useRef(52);
  const speedCurrentRef = useRef(52);

  useEffect(() => {
    const changeTarget = setInterval(() => {
      speedTargetRef.current = Math.floor(Math.random() * 85) + 15;
    }, 3500);
    const smooth = setInterval(() => {
      speedCurrentRef.current += (speedTargetRef.current - speedCurrentRef.current) * 0.08;
      const s = Math.round(speedCurrentRef.current);
      setSpeed(s);
      setOdometer(prev => +(prev + s / 36000).toFixed(1));
    }, 100);
    return () => { clearInterval(changeTarget); clearInterval(smooth); };
  }, []);

  return (
    <div className="p-6 grid grid-cols-2 gap-4 flex-1 min-h-0 bg-[#0D0E10] text-[#E0E0E0] overflow-y-auto [&::-webkit-scrollbar]:w-1 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-[#3A3B40] [&::-webkit-scrollbar-thumb]:rounded-full">

      {/* ── Speedometer + Odometer ── */}
      <motion.div
        initial={{ opacity: 0, y: -12 }}
        animate={{ opacity: 1, y: 0 }}
        className="col-span-2 bg-[#151619] rounded-24 border border-[#2A2B2F] p-4 flex items-center gap-4"
      >
        <div className="w-32 h-[5.5rem] flex-shrink-0">
          <SpeedometerSVG speed={speed} />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono mb-1">Speed</h3>
          <div className="flex items-end gap-1.5 leading-none">
            <motion.span
              key={speed}
              initial={{ opacity: 0.4 }}
              animate={{ opacity: 1 }}
              className="text-4xl font-light tabular-nums"
            >
              {speed}
            </motion.span>
            <span className="text-sm text-[#8E9299] mb-1">mph</span>
          </div>
          <div className="mt-3 border-t border-[#2A2B2F] pt-2">
            <span className="text-[9px] text-[#8E9299] uppercase font-mono tracking-widest block">Odometer</span>
            <span className="text-sm font-mono text-[#E0E0E0] tabular-nums">
              {odometer.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} mi
            </span>
          </div>
        </div>
      </motion.div>
      {/* Weather Block */}
      {stats.destination && (
        <motion.div 
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="col-span-2 bg-blue-600/10 rounded-24 border border-blue-500/20 p-4 flex justify-between items-center group relative overflow-hidden"
        >
          <div className="absolute top-0 right-0 p-2 opacity-10">
            <Navigation size={48} className="text-blue-500" />
          </div>
          <div className="relative z-10">
            <h3 className="text-[10px] uppercase tracking-widest text-blue-400 font-mono mb-1">Active Navigation</h3>
            <p className="text-lg font-semibold text-white truncate max-w-[200px]">{stats.destination}</p>
            <div className="flex gap-3 mt-1">
              <span className="text-xs text-[#8E9299] font-mono">{stats.navMetadata?.distance}</span>
              <span className="text-xs text-blue-400 font-mono">{stats.navMetadata?.eta} ARRIVAL</span>
            </div>
          </div>
          <button className="bg-blue-600 hover:bg-blue-500 text-white p-2 rounded-xl transition-colors shadow-lg shadow-blue-500/20">
             <Navigation size={18} />
          </button>
        </motion.div>
      )}

      <div className="col-span-2 bg-gradient-to-br from-[#1E1F23] to-[#151619] rounded-24 border border-[#2A2B2F] p-4 flex justify-between items-center group">
         <div>
            <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono mb-1">Local Forecast</h3>
            <p className="text-xl font-light">{stats.weather.location}</p>
            <p className="text-sm text-blue-400 capitalize">{stats.weather.condition}</p>
         </div>
         <div className="text-right">
            <p className="text-3xl font-light tracking-tighter">{stats.weather.temp}°<span className="text-base text-[#8E9299]">C</span></p>
         </div>
      </div>

      {/* Fuel/Battery Section */}
      <div className="col-span-2 bg-[#151619] rounded-24 border border-[#2A2B2F] p-4 flex flex-col justify-between h-40 relative overflow-hidden group">
        <div className="flex justify-between items-start z-10">
          <div>
            <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono mb-1">Energy Balance</h3>
            <p className="text-3xl font-light">{stats.fuel}% <span className="text-xs text-[#8E9299]">Remaining</span></p>
          </div>
          <Fuel className="text-blue-500 opacity-50 group-hover:opacity-100 transition-opacity" size={24} />
        </div>
        <div className="h-2 bg-[#2A2B2F] rounded-full overflow-hidden mt-4 z-10">
          <motion.div 
            initial={{ width: 0 }}
            animate={{ width: `${stats.fuel}%` }}
            className={`h-full ${stats.fuel < 20 ? 'bg-red-500' : 'bg-blue-500'}`}
          />
        </div>
        {/* Glow Effect */}
        <div className="absolute -right-10 -bottom-10 w-40 h-40 bg-blue-500/10 blur-[60px] rounded-full pointer-events-none"></div>
      </div>

      {/* Temperature */}
      <div className="bg-[#151619] rounded-24 border border-[#2A2B2F] p-4 flex flex-col justify-between group">
        <div className="flex justify-between items-start">
          <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono">Climate</h3>
          <Thermometer className="text-orange-500 opacity-50 group-hover:opacity-100 transition-opacity" size={18} />
        </div>
        <div className="mt-4">
          <p className="text-2xl font-light tracking-tighter">{stats.temp}°C</p>
          <span className="text-[10px] text-[#8E9299] uppercase font-mono tracking-tight">Active Zone: All</span>
        </div>
      </div>

      {/* Tire Pressure */}
      <div className="bg-[#151619] rounded-24 border border-[#2A2B2F] p-4 flex flex-col justify-between group">
        <div className="flex justify-between items-start">
          <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono">Chassis</h3>
          <Gauge className="text-green-500 opacity-50 group-hover:opacity-100 transition-opacity" size={18} />
        </div>
        <div className="grid grid-cols-2 gap-2 mt-4">
          {stats.tirePressure.map((p, i) => (
            <div key={i} className="flex flex-col">
              <span className="text-[9px] text-[#8E9299] uppercase font-mono">T{i+1}</span>
              <span className={`text-xs font-mono ${p < 30 ? 'text-red-400' : 'text-white'}`}>{p} PSI</span>
            </div>
          ))}
        </div>
      </div>

      {/* Media Player */}
      <div className="col-span-2 bg-[#151619] rounded-24 border border-[#2A2B2F] p-6 flex flex-col gap-5 group shadow-lg">
        <div className="flex items-center gap-5">
          <div className="w-16 h-16 bg-[#2A2B2F] rounded-12 flex items-center justify-center relative overflow-hidden flex-shrink-0 shadow-inner">
            {stats.music.playing ? (
              <motion.div 
                animate={{ 
                  scale: [1, 1.15, 1],
                  rotate: [0, 5, -5, 0]
                }} 
                transition={{ repeat: Infinity, duration: 3 }}
                className="absolute inset-0 bg-gradient-to-br from-purple-600/40 to-blue-600/40" 
              />
            ) : null}
            <Music className={`${stats.music.playing ? 'text-white' : 'text-[#8E9299]'} z-10`} size={28} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono font-bold">Audio System</h3>
              {stats.music.provider === 'spotify' && (
                <motion.span 
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="px-2 py-0.5 rounded-full bg-[#1DB954]/10 text-[9px] text-[#1DB954] font-bold uppercase tracking-wider border border-[#1DB954]/20"
                >
                  Spotify
                </motion.span>
              )}
            </div>
            <p className="text-base font-semibold truncate text-white leading-tight">{stats.music.track}</p>
            <p className="text-xs text-[#8E9299] truncate font-medium">{stats.music.artist}</p>
          </div>
        </div>

        {/* Progress Bar */}
        <div className="space-y-2">
          <div className="h-1.5 bg-[#2A2B2F] rounded-full overflow-hidden cursor-pointer">
            <motion.div 
              initial={{ width: "30%" }}
              animate={{ width: stats.music.playing ? '75%' : '45%' }}
              transition={{ duration: 15, ease: "linear", repeat: Infinity }}
              className="h-full bg-gradient-to-r from-blue-500 to-indigo-500"
            />
          </div>
          <div className="flex justify-between text-[10px] text-[#8E9299] font-mono">
            <span>2:15</span>
            <span>4:10</span>
          </div>
        </div>

        {/* Controls */}
        <div className="flex justify-center items-center gap-8">
          <button 
            onClick={onPrevTrack}
            className="text-[#8E9299] hover:text-white transition-all hover:scale-110 active:scale-90"
          >
            <SkipBack size={24} fill="currentColor" className="opacity-80" />
          </button>
          <button 
            onClick={onTogglePlay}
            className="w-12 h-12 bg-white text-black rounded-full flex items-center justify-center hover:scale-105 active:scale-95 transition-all shadow-[0_0_20px_rgba(255,255,255,0.2)]"
          >
            {stats.music.playing ? <Pause size={24} fill="currentColor" /> : <Play size={24} className="ml-1" fill="currentColor" />}
          </button>
          <button 
            onClick={onNextTrack}
            className="text-[#8E9299] hover:text-white transition-all hover:scale-110 active:scale-90"
          >
            <SkipForward size={24} fill="currentColor" className="opacity-80" />
          </button>
        </div>
      </div>

      {/* Notifications / Alerts */}
      <div className="col-span-2 mt-4 space-y-2">
         {stats.fuel < 20 && (
           <motion.div 
             initial={{ opacity: 0, x: -20 }}
             animate={{ opacity: 1, x: 0 }}
             className="bg-red-500/10 border border-red-500/20 p-3 rounded-xl flex items-center gap-3"
           >
             <AlertTriangle className="text-red-500" size={18} />
             <p className="text-[11px] text-red-200 uppercase tracking-tight font-mono">Critical Status: Low Fuel. Nearest station 1.2mi.</p>
           </motion.div>
         )}
         <div className="bg-blue-500/10 border border-blue-500/20 p-3 rounded-xl flex items-center gap-3">
             <Info className="text-blue-500" size={18} />
             <p className="text-[11px] text-blue-200 uppercase tracking-tight font-mono">System update available. Schedule for tonight?</p>
         </div>
      </div>

      {/* Google Calendar & Gmail Section */}
      <div className="col-span-2 mt-4">
        <div className="bg-[#151619] rounded-24 border border-[#2A2B2F] p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Calendar size={14} className="text-blue-400" />
              <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono">
                {googleUser ? 'Upcoming Events' : 'Google Connected'}
              </h3>
            </div>
            {googleUser && (
              <div className="flex items-center gap-1.5">
                <Mail size={12} className="text-[#8E9299]" />
                <span className="text-[9px] text-[#8E9299] font-mono truncate max-w-[120px]">{googleUser.email}</span>
              </div>
            )}
          </div>

          {!googleUser ? (
            <p className="text-[11px] text-[#8E9299] font-mono">
              Ask the assistant to "send an email" or "show my calendar" to connect Google.
            </p>
          ) : upcomingEvents.length === 0 ? (
            <p className="text-[11px] text-[#8E9299] font-mono">No upcoming events. Ask me to create one.</p>
          ) : (
            <div className="space-y-2">
              {upcomingEvents.map(event => (
                <motion.div
                  key={event.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex gap-3 items-start bg-[#1E1F23] rounded-xl p-2.5 border border-[#2A2B2F]"
                >
                  <div className="w-1 self-stretch rounded-full bg-blue-500/60 flex-shrink-0" />
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-white truncate">{event.summary}</p>
                    <p className="text-[10px] text-blue-400 font-mono mt-0.5">{formatEventTime(event.start)}</p>
                    {event.location && (
                      <p className="text-[10px] text-[#8E9299] truncate mt-0.5">{event.location}</p>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Recent Emails */}
      {googleUser && (
        <div className="col-span-2 mt-4">
          <div className="bg-[#151619] rounded-24 border border-[#2A2B2F] p-4">
            <div className="flex items-center gap-2 mb-3">
              <Mail size={14} className="text-blue-400" />
              <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono">Recent Emails</h3>
            </div>
            {recentEmails.length === 0 ? (
              <p className="text-[11px] text-[#8E9299] font-mono">No emails loaded yet.</p>
            ) : (
              <div className="space-y-2">
                {recentEmails.map(email => (
                  <motion.div
                    key={email.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`flex gap-3 items-start rounded-xl p-2.5 border transition-colors ${
                      email.isUnread
                        ? 'bg-blue-600/5 border-blue-500/20'
                        : 'bg-[#1E1F23] border-[#2A2B2F]'
                    }`}
                  >
                    <div className={`w-1 self-stretch rounded-full flex-shrink-0 ${email.isUnread ? 'bg-blue-500' : 'bg-[#3A3B40]'}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className={`text-[11px] truncate font-mono ${email.isUnread ? 'text-white font-semibold' : 'text-[#8E9299]'}`}>
                          {formatEmailFrom(email.from)}
                        </span>
                        <span className="text-[9px] text-[#8E9299] font-mono shrink-0">{formatEmailDate(email.date)}</span>
                      </div>
                      <p className={`text-[11px] truncate mt-0.5 ${email.isUnread ? 'text-[#E0E0E0]' : 'text-[#8E9299]'}`}>
                        {email.subject}
                      </p>
                      <p className="text-[10px] text-[#6B7280] truncate mt-0.5">{email.snippet}</p>
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
