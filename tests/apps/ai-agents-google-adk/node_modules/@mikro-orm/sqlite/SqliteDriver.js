"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SqliteDriver = void 0;
const knex_1 = require("@mikro-orm/knex");
const SqliteConnection_1 = require("./SqliteConnection");
const SqlitePlatform_1 = require("./SqlitePlatform");
class SqliteDriver extends knex_1.AbstractSqlDriver {
    constructor(config) {
        super(config, new SqlitePlatform_1.SqlitePlatform(), SqliteConnection_1.SqliteConnection, ['knex', 'sqlite3']);
    }
}
exports.SqliteDriver = SqliteDriver;
