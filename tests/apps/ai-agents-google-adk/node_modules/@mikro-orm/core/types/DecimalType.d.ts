import { Type } from './Type';
import type { Platform } from '../platforms';
import type { EntityProperty } from '../typings';
/**
 * Type that maps an SQL DECIMAL to a JS string or number.
 */
export declare class DecimalType<Mode extends 'number' | 'string' = 'string'> extends Type<JSTypeByMode<Mode>, string> {
    mode?: Mode | undefined;
    constructor(mode?: Mode | undefined);
    convertToJSValue(value: string): JSTypeByMode<Mode>;
    compareValues(a: string, b: string): boolean;
    private format;
    getColumnType(prop: EntityProperty, platform: Platform): string;
    compareAsType(): string;
}
type JSTypeByMode<Mode extends 'number' | 'string'> = Mode extends 'number' ? number : string;
export {};
