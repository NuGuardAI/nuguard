"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.UnicodeCharacterType = void 0;
const UnicodeStringType_1 = require("./UnicodeStringType");
class UnicodeCharacterType extends UnicodeStringType_1.UnicodeStringType {
    getColumnType(prop, platform) {
        const length = prop.length === -1 ? 'max' : (prop.length ?? this.getDefaultLength(platform));
        return `nchar(${length})`;
    }
    getDefaultLength(platform) {
        return platform.getDefaultCharLength();
    }
}
exports.UnicodeCharacterType = UnicodeCharacterType;
