import type { Platform, EntityProperty } from '@mikro-orm/core';
import { UnicodeStringType } from './UnicodeStringType';
export declare class UnicodeCharacterType extends UnicodeStringType {
    getColumnType(prop: EntityProperty, platform: Platform): string;
    getDefaultLength(platform: Platform): number;
}
