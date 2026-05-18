"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.BetterSqliteKnexDialect = void 0;
const SqliteTableCompiler_1 = require("./SqliteTableCompiler");
const SqliteColumnCompiler_1 = require("./SqliteColumnCompiler");
const MonkeyPatchable_1 = require("../../MonkeyPatchable");
class BetterSqliteKnexDialect extends MonkeyPatchable_1.MonkeyPatchable.BetterSqlite3Dialect {
    _driver() {
        return require('better-sqlite3');
    }
    tableCompiler() {
        // eslint-disable-next-line prefer-rest-params
        return new SqliteTableCompiler_1.SqliteTableCompiler(this, ...arguments);
    }
    columnCompiler() {
        // eslint-disable-next-line prefer-rest-params
        return new SqliteColumnCompiler_1.SqliteColumnCompiler(this, ...arguments);
    }
}
exports.BetterSqliteKnexDialect = BetterSqliteKnexDialect;
