import { Type } from './Type';
import type { Platform } from '../platforms';
import type { EntityProperty } from '../typings';
/**
 * This type will automatically convert string values returned from the database to native JS bigints (default)
 * or numbers (safe only for values up to `Number.MAX_SAFE_INTEGER`), or strings, depending on the `mode`.
 */
export declare class BigIntType<Mode extends 'bigint' | 'number' | 'string' = 'bigint'> extends Type<JSTypeByMode<Mode> | null | undefined, string | null | undefined> {
    mode?: Mode | undefined;
    constructor(mode?: Mode | undefined);
    convertToDatabaseValue(value: JSTypeByMode<Mode> | null | undefined): string | null | undefined;
    convertToJSValue(value: string | bigint | null | undefined): JSTypeByMode<Mode> | null | undefined;
    toJSON(value: JSTypeByMode<Mode> | null | undefined): JSTypeByMode<Mode> | null | undefined;
    getColumnType(prop: EntityProperty, platform: Platform): string;
    compareAsType(): string;
    compareValues(a: string, b: string): boolean;
}
type JSTypeByMode<Mode extends 'bigint' | 'number' | 'string'> = Mode extends 'bigint' ? bigint : Mode extends 'number' ? number : string;
export {};
