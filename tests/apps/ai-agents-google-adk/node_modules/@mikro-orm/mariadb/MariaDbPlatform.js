"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MariaDbPlatform = void 0;
const knex_1 = require("@mikro-orm/knex");
const MariaDbSchemaHelper_1 = require("./MariaDbSchemaHelper");
const MariaDbExceptionConverter_1 = require("./MariaDbExceptionConverter");
class MariaDbPlatform extends knex_1.MySqlPlatform {
    schemaHelper = new MariaDbSchemaHelper_1.MariaDbSchemaHelper(this);
    exceptionConverter = new MariaDbExceptionConverter_1.MariaDbExceptionConverter();
    getDefaultCharset() {
        return 'utf8mb4';
    }
    convertJsonToDatabaseValue(value, context) {
        return JSON.stringify(value);
    }
}
exports.MariaDbPlatform = MariaDbPlatform;
