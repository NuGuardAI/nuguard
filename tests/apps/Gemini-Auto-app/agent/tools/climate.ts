import { FunctionTool } from '@google/adk';
import { z } from 'zod';
import type { ToolContext } from '../types.js';

export function createClimateTools(ctx: ToolContext): FunctionTool[] {
  return [
    new FunctionTool({
      name: 'adjustTemperature',
      description: 'Adjust the vehicle cabin temperature for driver, passenger, or all zones.',
      parameters: z.object({
        temp: z.number().describe('Target temperature in Celsius.'),
        zone: z.enum(['driver', 'passenger', 'all']).optional()
          .describe('Climate zone to adjust. Defaults to all.'),
      }),
      execute: ({ temp, zone }) => {
        ctx.updates.temp = temp;
        return `Climate set to ${temp}°C${zone && zone !== 'all' ? ` for ${zone}` : ''}.`;
      },
    }),

    new FunctionTool({
      name: 'checkVehicleStatus',
      description:
        'Query real-time onboard diagnostics: tire pressure, fuel, battery, engine, or all systems.',
      parameters: z.object({
        system: z.enum(['tires', 'fuel', 'engine', 'battery', 'all'])
          .describe('Which vehicle system to inspect.'),
      }),
      execute: ({ system }) => {
        const vs = ctx.vehicleState;
        const [fl, fr, rl, rr] = vs.tirePressure;

        if (system === 'tires') {
          const low = vs.tirePressure.filter(p => p < 30);
          ctx.updates.tirePressure = [fl, fr, Math.min(rl, 27), rr];
          return low.length > 0
            ? `⚠️ Low tire pressure. FL:${fl} FR:${fr} RL:${Math.min(rl, 27)} RR:${rr} PSI`
            : `Tires OK. FL:${fl} FR:${fr} RL:${rl} RR:${rr} PSI`;
        }
        if (system === 'fuel') {
          return vs.fuel < 20
            ? `⚠️ Fuel low: ${vs.fuel}%. Refuel soon.`
            : `Fuel: ${vs.fuel}%.`;
        }
        if (system === 'battery') return `Battery: ${vs.battery}%.`;
        if (system === 'engine') return 'Engine: nominal. No fault codes.';

        // system === 'all'
        ctx.updates.tirePressure = [fl, fr, Math.min(rl, 27), rr];
        return [
          `Fuel: ${vs.fuel}%`,
          `Battery: ${vs.battery}%`,
          `Engine: nominal`,
          `Tires FL:${fl} FR:${fr} RL:${Math.min(rl, 27)} RR:${rr} PSI`,
        ].join(' | ');
      },
    }),
  ];
}
