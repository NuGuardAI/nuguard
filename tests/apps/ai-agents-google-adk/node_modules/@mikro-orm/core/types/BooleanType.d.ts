import { Type } from './Type';
import type { Platform } from '../platforms';
import type { EntityProperty } from '../typings';
export declare class BooleanType extends Type<boolean | null | undefined, boolean | null | undefined> {
    getColumnType(prop: EntityProperty, platform: Platform): string;
    compareAsType(): string;
    convertToJSValue(value: boolean | null | undefined): boolean | null | undefined;
    ensureComparable(): boolean;
}
