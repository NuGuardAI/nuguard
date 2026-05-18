"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SqliteColumnCompiler = void 0;
const MonkeyPatchable_1 = require("../../MonkeyPatchable");
class SqliteColumnCompiler extends MonkeyPatchable_1.MonkeyPatchable.Sqlite3ColumnCompiler {
    enu(allowed) {
        const values = allowed.map(v => `'${String(v).replace(/'/g, "''")}'`).join(', ');
        return `text check (${this.formatter.wrap(this.args[0])} in (${values}))`;
    }
}
exports.SqliteColumnCompiler = SqliteColumnCompiler;
