"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SqliteMikroORM = void 0;
exports.defineSqliteConfig = defineSqliteConfig;
const core_1 = require("@mikro-orm/core");
const SqliteDriver_1 = require("./SqliteDriver");
/**
 * @inheritDoc
 */
class SqliteMikroORM extends core_1.MikroORM {
    static DRIVER = SqliteDriver_1.SqliteDriver;
    /**
     * @inheritDoc
     */
    static async init(options) {
        return super.init(options);
    }
    /**
     * @inheritDoc
     */
    static initSync(options) {
        return super.initSync(options);
    }
}
exports.SqliteMikroORM = SqliteMikroORM;
/* istanbul ignore next */
function defineSqliteConfig(options) {
    return (0, core_1.defineConfig)({ driver: SqliteDriver_1.SqliteDriver, ...options });
}
