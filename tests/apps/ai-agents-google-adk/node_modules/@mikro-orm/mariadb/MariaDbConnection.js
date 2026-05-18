"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MariaDbConnection = void 0;
const knex_1 = require("@mikro-orm/knex");
class MariaDbConnection extends knex_1.MySqlConnection {
    createKnex() {
        this.client = this.createKnexClient(knex_1.MariaDbKnexDialect);
        this.connected = true;
    }
    getConnectionOptions() {
        const ret = super.getConnectionOptions();
        ret.insertIdAsNumber = true;
        ret.checkDuplicate = false;
        return ret;
    }
}
exports.MariaDbConnection = MariaDbConnection;
