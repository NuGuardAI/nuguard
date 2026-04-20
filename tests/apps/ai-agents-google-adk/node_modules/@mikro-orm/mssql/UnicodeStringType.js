"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.UnicodeStringType = exports.UnicodeString = void 0;
const core_1 = require("@mikro-orm/core");
class UnicodeString {
    value;
    constructor(value) {
        this.value = value;
    }
    valueOf() {
        return this.value;
    }
    toString() {
        return this.value;
    }
    toJSON() {
        return this.value;
    }
    [Symbol.toPrimitive]() {
        return this.value;
    }
}
exports.UnicodeString = UnicodeString;
class UnicodeStringType extends core_1.Type {
    getColumnType(prop, platform) {
        const length = prop.length === -1 ? 'max' : (prop.length ?? this.getDefaultLength(platform));
        return `nvarchar(${length})`;
    }
    convertToJSValue(value) {
        /* istanbul ignore if */
        if (value instanceof UnicodeString) {
            return value.value;
        }
        return value;
    }
    convertToDatabaseValue(value) {
        if (typeof value === 'string') {
            return new UnicodeString(value);
        }
        return value;
    }
    get runtimeType() {
        return 'string';
    }
    toJSON(value) {
        return this.convertToJSValue(value);
    }
    getDefaultLength(platform) {
        return platform.getDefaultVarcharLength();
    }
}
exports.UnicodeStringType = UnicodeStringType;
