"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.rawKnex = rawKnex;
const core_1 = require("@mikro-orm/core");
const QueryBuilder_1 = require("./QueryBuilder");
function rawKnex(sql, params) {
    if (core_1.Utils.isObject(sql) && 'toSQL' in sql) {
        const query = sql.toSQL();
        return (0, core_1.raw)(query.sql, query.bindings);
    }
    if (sql instanceof QueryBuilder_1.QueryBuilder) {
        const query = sql.toQuery()._sql;
        return (0, core_1.raw)(query.sql, query.bindings);
    }
    return (0, core_1.raw)(sql, params);
}
