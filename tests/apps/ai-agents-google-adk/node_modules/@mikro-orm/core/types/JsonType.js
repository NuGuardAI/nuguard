"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.JsonType = void 0;
const Type_1 = require("./Type");
class JsonType extends Type_1.Type {
    convertToDatabaseValue(value, platform, context) {
        if (value == null) {
            return value;
        }
        return platform.convertJsonToDatabaseValue(value, context);
    }
    convertToJSValueSQL(key, platform) {
        return key + platform.castJsonValue(this.prop);
    }
    convertToDatabaseValueSQL(key, platform) {
        return key + platform.castColumn(this.prop);
    }
    convertToJSValue(value, platform, context) {
        const isJsonColumn = ['json', 'jsonb', platform.getJsonDeclarationSQL()].includes(this.prop.columnTypes[0]);
        const isObjectEmbedded = this.prop.embedded && this.prop.object;
        if ((platform.convertsJsonAutomatically() || isObjectEmbedded) && isJsonColumn && !context?.force) {
            return value;
        }
        return platform.convertJsonToJSValue(value, context);
    }
    getColumnType(prop, platform) {
        return platform.getJsonDeclarationSQL();
    }
    ensureComparable(meta, prop) {
        return !prop.embedded || !meta.properties[prop.embedded[0]].object;
    }
    compareAsType() {
        return 'any';
    }
    get runtimeType() {
        return 'any';
    }
}
exports.JsonType = JsonType;
