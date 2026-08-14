import { motion } from "motion/react";
import { Navigation, MapPin } from "lucide-react";
import RealMap from "./RealMap";

const GOOGLE_MAPS_KEY = process.env.GOOGLE_MAPS_PLATFORM_KEY;

interface MapSimulatorProps {
  destination: string | null;
  stops?: string[];
  navMetadata: { distance: string, eta: string } | null;
  onRouteUpdate?: (data: { distance: string; duration: string } | null) => void;
}

export default function MapSimulator({ destination, stops, navMetadata, onRouteUpdate }: MapSimulatorProps) {
  const hasRealMap = Boolean(GOOGLE_MAPS_KEY && GOOGLE_MAPS_KEY !== '');

  return (
    <div className="flex-1 min-h-0 bg-[#151619] p-6 flex flex-col border-l border-[#2A2B2F]">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-[10px] uppercase tracking-widest text-[#8E9299] font-mono">Route Guidance</h3>
        <Navigation className={`${destination ? 'text-blue-500 animate-pulse' : 'text-[#2A2B2F]'}`} size={16} />
      </div>

      {hasRealMap ? (
        <RealMap destination={destination} stops={stops} onRouteUpdate={onRouteUpdate} />
      ) : (
        <div className="flex-1 rounded-24 border border-[#2A2B2F] bg-[#0D0E10] relative overflow-hidden group">
          {/* Grid Lines */}
          <div className="absolute inset-0 grid grid-cols-8 grid-rows-12 opacity-10 pointer-events-none">
             {Array.from({ length: 96 }).map((_, i) => (
                <div key={i} className="border-[0.5px] border-[#8E9299]"></div>
             ))}
          </div>

          {/* Scanning Effect */}
          <motion.div 
            animate={{ top: ['0%', '100%', '0%'] }}
            transition={{ duration: 10, repeat: Infinity, ease: "linear" }}
            className="absolute left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-blue-500/20 to-transparent z-10"
          />

          {/* Path Line */}
          <svg className="absolute inset-0 w-full h-full" viewBox="0 0 400 600" preserveAspectRatio="none">
             <motion.path 
               initial={{ pathLength: 0, opacity: 0 }}
               animate={{ 
                 pathLength: destination ? 1 : 0,
                 opacity: destination ? 0.6 : 0 
               }}
               transition={{ duration: 1.5, ease: "easeOut" }}
               d="M 50 550 Q 100 500 150 400 T 200 300 T 300 200 T 335 110"
               stroke="#3B82F6"
               strokeWidth="6"
               fill="none"
               strokeLinecap="round"
               className="drop-shadow-[0_0_12px_rgba(59,130,246,0.8)]"
             />
             
             {/* Ghost path for depth */}
             <motion.path 
               animate={{ opacity: destination ? 0.1 : 0 }}
               d="M 50 550 Q 100 500 150 400 T 200 300 T 300 200 T 335 110"
               stroke="white"
               strokeWidth="10"
               fill="none"
               className="blur-xl"
             />
          </svg>

          {/* POI Markers (Decorative) */}
          <div className="absolute inset-0 pointer-events-none opacity-20">
             <div className="absolute top-1/4 left-1/3 w-1 h-1 bg-white rounded-full"></div>
             <div className="absolute top-1/2 left-2/3 w-1 h-1 bg-white rounded-full"></div>
             <div className="absolute bottom-1/3 right-1/4 w-1 h-1 bg-white rounded-full"></div>
          </div>

          {/* Current Position Marker */}
          <motion.div 
            className="absolute left-8 bottom-12 flex flex-col items-center"
          >
            <div className="relative">
                <div className="absolute -inset-4 bg-blue-500/20 rounded-full blur-xl animate-pulse"></div>
                <div className="w-4 h-4 bg-blue-500 rounded-full border-2 border-white shadow-[0_0_15px_rgba(59,130,246,0.8)] relative z-10"></div>
            </div>
          </motion.div>

          {/* Destination Marker */}
          {destination && (
            <motion.div 
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              className="absolute top-20 right-16 flex flex-col items-center"
            >
              <div className="relative">
                  <div className="absolute -inset-6 bg-red-500/30 rounded-full blur-2xl animate-pulse"></div>
                  <MapPin className="text-red-500 relative z-10" size={28} />
              </div>
              <div className="mt-2 bg-[#151619] border border-red-500/50 text-white text-[10px] px-3 py-1 rounded-lg uppercase font-mono font-bold whitespace-nowrap shadow-xl backdrop-blur-md">
                 {destination}
              </div>
              {navMetadata && (
                <div className="mt-1 text-[9px] text-[#8E9299] font-mono">
                    {navMetadata.distance}
                </div>
              )}
            </motion.div>
          )}

          {/* HUD Overlay */}
          <div className="absolute bottom-4 left-4 right-4 flex flex-col gap-2">
             {destination && (
               <motion.div 
                 initial={{ y: 20, opacity: 0 }}
                 animate={{ y: 0, opacity: 1 }}
                 className="bg-blue-600/10 backdrop-blur-xl border border-blue-500/30 p-2 rounded-lg flex items-center gap-3 mb-2"
               >
                  <div className="p-2 bg-blue-500 rounded-lg">
                     <Navigation size={14} className="text-white" />
                  </div>
                  <div>
                    <div className="text-[8px] text-blue-400 uppercase font-bold tracking-wider">Next Turn</div>
                    <div className="text-[11px] font-medium">Turn Right on Harrison Blvd</div>
                  </div>
               </motion.div>
             )}

             <div className="flex justify-between gap-4">
                <div className="bg-[#151619]/90 backdrop-blur-md border border-[#2A2B2F] p-3 rounded-xl flex-1 group-hover:border-blue-500/30 transition-colors">
                  <span className="text-[9px] text-[#8E9299] uppercase font-mono block">Cruise</span>
                  <span className="text-xl font-light">65 <span className="text-xs text-[#8E9299]">MPH</span></span>
                </div>
                <div className="bg-[#151619]/90 backdrop-blur-md border border-[#2A2B2F] p-3 rounded-xl flex-1 text-right group-hover:border-blue-500/30 transition-colors">
                  <span className="text-[9px] text-[#8E9299] uppercase font-mono block">Arrival</span>
                  <span className="text-xl font-light text-blue-400">
                    {navMetadata?.eta || "--"} <span className="text-xs text-[#8E9299]">MIN</span>
                  </span>
                </div>
             </div>
          </div>
        </div>
      )}

      {/* Location Breadcrumb */}
      <div className="mt-4 flex items-center gap-2 text-[#8E9299]">
         <MapPin size={12} />
         <span className="text-[10px] font-mono uppercase tracking-tight">
             {hasRealMap ? "Real-time Positioning Active" : "Current: Main St. / 4th Ave. Intersection"}
         </span>
      </div>
    </div>
  );
}
