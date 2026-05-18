"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SqlitePlatform = void 0;
// @ts-ignore
const sqlstring_sqlite_1 = require("sqlstring-sqlite");
const knex_1 = require("@mikro-orm/knex");
const SqliteSchemaHelper_1 = require("./SqliteSchemaHelper");
const SqliteExceptionConverter_1 = require("./SqliteExceptionConverter");
class SqlitePlatform extends knex_1.BaseSqlitePlatform {
    schemaHelper = new SqliteSchemaHelper_1.SqliteSchemaHelper(this);
    exceptionConverter = new SqliteExceptionConverter_1.SqliteExceptionConverter();
    escape(value) {
        return (0, sqlstring_sqlite_1.escape)(value, true, this.timezone);
    }
}
exports.SqlitePlatform = SqlitePlatform;
