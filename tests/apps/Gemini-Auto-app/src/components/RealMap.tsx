import { APIProvider, Map, AdvancedMarker, Pin, useMapsLibrary, useMap } from '@vis.gl/react-google-maps';
import { useEffect, useRef, useState } from 'react';
import { resolveLocation } from '../services/gemini';

const API_KEY = process.env.GOOGLE_MAPS_PLATFORM_KEY || '';

const ORIGIN_LATLNG = { lat: 37.8087, lng: -122.4098 }; // Pier 39, San Francisco

interface RouteUpdateData {
  distance: string;
  duration: string;
}

interface RealMapProps {
  destination: string | null;
  stops?: string[];
  onRouteUpdate?: (data: RouteUpdateData | null) => void;
}

function RouteDisplay({ destination, stops, onRouteUpdate, onError }: {
  destination: string | null;
  stops?: string[];
  onRouteUpdate?: (data: RouteUpdateData | null) => void;
  onError?: (msg: string | null) => void;
}) {
  const map = useMap();
  const routesLib = useMapsLibrary('routes');
  const rendererRef = useRef<any>(null);
  const [routeData, setRouteData] = useState<RouteUpdateData | null>(null);

  useEffect(() => () => {
    rendererRef.current?.setMap(null);
  }, []);

  useEffect(() => {
    if (!routesLib || !map) return;
    let cancelled = false;

    if (!destination) {
      rendererRef.current?.setMap(null);
      rendererRef.current = null;
      setRouteData(null);
      onRouteUpdate?.(null);
      onError?.(null);
      return;
    }

    const runRoute = async () => {
      const attemptRoute = async (dest: string): Promise<boolean> => {
        try {
          const { DirectionsService, DirectionsRenderer } = routesLib as any;
          const service = new DirectionsService();

          const result = await service.route({
            origin: ORIGIN_LATLNG,
            destination: dest,
            waypoints: stops?.map((s: string) => ({ location: s, stopover: true })) ?? [],
            travelMode: 'DRIVING',
          });

          if (cancelled) return true;

          rendererRef.current?.setMap(null);
          const renderer = new DirectionsRenderer({
            map,
            suppressMarkers: true,
            polylineOptions: { strokeColor: '#3B82F6', strokeWeight: 5, strokeOpacity: 0.85 },
          });
          renderer.setDirections(result);
          rendererRef.current = renderer;

          const legs: any[] = result.routes[0].legs;
          const totalDistM = legs.reduce((s: number, l: any) => s + l.distance.value, 0);
          const totalDurS = legs.reduce((s: number, l: any) => s + l.duration.value, 0);
          const distMiles = (totalDistM * 0.000621371).toFixed(1);
          const durText =
            totalDurS < 3600
              ? `${Math.round(totalDurS / 60)} min`
              : `${Math.floor(totalDurS / 3600)} hr ${Math.round((totalDurS % 3600) / 60)} min`;

          const data = { distance: `${distMiles} mi`, duration: durText };
          setRouteData(data);
          onRouteUpdate?.(data);
          onError?.(null);
          return true;
        } catch {
          return false;
        }
      };

      // First attempt with the destination as-is
      const ok = await attemptRoute(destination);
      if (cancelled) return;
      if (!ok) {
        // Fallback: ask Gemini to resolve a precise address and retry
        console.warn(`Route failed for "${destination}", asking Gemini for a precise address…`);
        const resolved = await resolveLocation(destination);
        if (cancelled) return;
        const retryOk = resolved !== destination && await attemptRoute(resolved);
        if (cancelled) return;
        if (!retryOk) {
          rendererRef.current?.setMap(null);
          rendererRef.current = null;
          setRouteData(null);
          onRouteUpdate?.(null);
          onError?.(`Could not find a route to "${destination}". Try a more specific address.`);
        }
      }
    };

    runRoute();
    return () => { cancelled = true; };
  }, [destination, stops, routesLib, map]);

  if (!routeData) return null;
  return (
    <div className="absolute bottom-4 left-4 right-4 flex justify-between gap-4 z-10">
      <div className="bg-[#151619]/80 backdrop-blur-md border border-[#2A2B2F] p-3 rounded-xl flex-1">
        <span className="text-[9px] text-[#8E9299] uppercase font-mono block">Distance</span>
        <span className="text-xl font-light">{routeData.distance}</span>
      </div>
      <div className="bg-[#151619]/80 backdrop-blur-md border border-[#2A2B2F] p-3 rounded-xl flex-1 text-right">
        <span className="text-[9px] text-[#8E9299] uppercase font-mono block">ETA</span>
        <span className="text-xl font-light">{routeData.duration}</span>
      </div>
    </div>
  );
}

export default function RealMap({ destination, stops, onRouteUpdate }: RealMapProps) {
  const [routeError, setRouteError] = useState<string | null>(null);

  useEffect(() => { setRouteError(null); }, [destination]);

  return (
    <APIProvider apiKey={API_KEY} version="weekly">
      <div className="flex-1 rounded-24 border border-[#2A2B2F] bg-[#0D0E10] relative overflow-hidden h-full">
        <Map
          defaultCenter={{ lat: 37.7749, lng: -122.4194 }}
          defaultZoom={12}
          mapId="bf50a94b3304a5e"
          disableDefaultUI={true}
          gestureHandling={'greedy'}
          style={{ width: '100%', height: '100%' }}
        >
          <AdvancedMarker position={{ lat: 37.7749, lng: -122.4194 }}>
            <Pin background="#3B82F6" glyphColor="#fff" />
          </AdvancedMarker>
          <RouteDisplay destination={destination} stops={stops} onRouteUpdate={onRouteUpdate} onError={setRouteError} />
        </Map>

        {routeError && (
          <div className="absolute inset-x-3 bottom-3 z-10 bg-[#1E1F23]/95 backdrop-blur-md border border-red-500/40 rounded-xl px-3 py-2 flex items-start gap-2">
            <span className="text-red-400 text-xs mt-0.5 shrink-0">⚠️</span>
            <span className="text-[11px] text-red-300 leading-snug">{routeError}</span>
          </div>
        )}
      </div>
    </APIProvider>
  );
}
