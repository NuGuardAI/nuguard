import { type Platform, Type } from '@mikro-orm/core';
export declare class UnicodeString {
    readonly value: string;
    constructor(value: string);
    valueOf(): string;
    toString(): string;
    toJSON(): string;
    [Symbol.toPrimitive](): string;
}
export declare class UnicodeStringType extends Type<string | null, string | null> {
    getColumnType(prop: {
        length?: number;
    }, platform: Platform): string;
    convertToJSValue(value: string | null | UnicodeString): string | null;
    convertToDatabaseValue(value: string | null): string | null;
    get runtimeType(): string;
    toJSON(value: string | null | UnicodeString): string | null;
    getDefaultLength(platform: Platform): number;
}
