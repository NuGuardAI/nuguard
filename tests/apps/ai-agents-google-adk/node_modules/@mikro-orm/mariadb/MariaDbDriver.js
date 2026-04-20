"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MariaDbDriver = void 0;
const core_1 = require("@mikro-orm/core");
const knex_1 = require("@mikro-orm/knex");
const MariaDbConnection_1 = require("./MariaDbConnection");
const MariaDbPlatform_1 = require("./MariaDbPlatform");
const MariaDbQueryBuilder_1 = require("./MariaDbQueryBuilder");
class MariaDbDriver extends knex_1.AbstractSqlDriver {
    autoIncrementIncrement;
    constructor(config) {
        super(config, new MariaDbPlatform_1.MariaDbPlatform(), MariaDbConnection_1.MariaDbConnection, ['knex', 'mariadb']);
    }
    async getAutoIncrementIncrement(ctx) {
        if (this.autoIncrementIncrement == null) {
            // the increment step may differ when running a cluster, see https://github.com/mikro-orm/mikro-orm/issues/3828
            const res = await this.connection.execute(`show variables like 'auto_increment_increment'`, [], 'get', ctx, { enabled: false });
            /* istanbul ignore next */
            this.autoIncrementIncrement = res?.Value ? +res?.Value : 1;
        }
        return this.autoIncrementIncrement;
    }
    async nativeInsertMany(entityName, data, options = {}) {
        options.processCollections ??= true;
        const res = await super.nativeInsertMany(entityName, data, options);
        const pks = this.getPrimaryKeyFields(entityName);
        const ctx = options.ctx;
        const autoIncrementIncrement = await this.getAutoIncrementIncrement(ctx);
        data.forEach((item, idx) => res.rows[idx] = { [pks[0]]: item[pks[0]] ?? res.insertId + (idx * autoIncrementIncrement) });
        res.row = res.rows[0];
        return res;
    }
    async nativeUpdateMany(entityName, where, data, options = {}) {
        const res = await super.nativeUpdateMany(entityName, where, data, options);
        const pks = this.getPrimaryKeyFields(entityName);
        const ctx = options.ctx;
        const autoIncrementIncrement = await this.getAutoIncrementIncrement(ctx);
        let i = 0;
        const rows = where.map(cond => {
            if (res.insertId != null && core_1.Utils.isEmpty(cond)) {
                return { [pks[0]]: res.insertId + (i++ * autoIncrementIncrement) };
            }
            if (cond[pks[0]] == null) {
                return undefined;
            }
            return { [pks[0]]: cond[pks[0]] };
        });
        if (rows.every(i => i !== undefined)) {
            res.rows = rows;
        }
        res.row = res.rows[0];
        return res;
    }
    createQueryBuilder(entityName, ctx, preferredConnectionType, convertCustomTypes, loggerContext, alias, em) {
        // do not compute the connectionType if EM is provided as it will be computed from it in the QB later on
        const connectionType = em ? preferredConnectionType : this.resolveConnectionType({ ctx, connectionType: preferredConnectionType });
        const qb = new MariaDbQueryBuilder_1.MariaDbQueryBuilder(entityName, this.metadata, this, ctx, alias, connectionType, em, loggerContext);
        if (!convertCustomTypes) {
            qb.unsetFlag(core_1.QueryFlag.CONVERT_CUSTOM_TYPES);
        }
        return qb;
    }
}
exports.MariaDbDriver = MariaDbDriver;
