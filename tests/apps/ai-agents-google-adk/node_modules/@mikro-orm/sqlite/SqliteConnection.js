"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SqliteConnection = void 0;
const knex_1 = require("@mikro-orm/knex");
class SqliteConnection extends knex_1.BaseSqliteConnection {
    createKnex() {
        this.client = this.createKnexClient(knex_1.SqliteKnexDialect);
        this.connected = true;
    }
    transformRawResult(res, method) {
        if (method === 'get') {
            return res[0];
        }
        if (method === 'all') {
            return res;
        }
        if (Array.isArray(res)) {
            return {
                insertId: res[res.length - 1]?.id ?? 0,
                affectedRows: res.length,
                row: res[0],
                rows: res,
            };
        }
        return {
            insertId: res.lastID,
            affectedRows: res.changes,
        };
    }
}
exports.SqliteConnection = SqliteConnection;
