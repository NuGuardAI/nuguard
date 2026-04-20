"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.PostgreSqlColumnCompiler = void 0;
const MonkeyPatchable_1 = require("../../MonkeyPatchable");
class PostgreSqlColumnCompiler extends MonkeyPatchable_1.MonkeyPatchable.PostgresColumnCompiler {
    enu(allowed, options) {
        options = options || {};
        if (options.useNative) {
            return super.enu(allowed, options);
        }
        const values = allowed.map(v => `'${String(v).replace(/'/g, "''")}'`).join(', ');
        return `text check (${this.formatter.wrap(this.args[0])} in (${values}))`;
    }
}
exports.PostgreSqlColumnCompiler = PostgreSqlColumnCompiler;
