"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MsSqlMikroORM = void 0;
exports.defineMsSqlConfig = defineMsSqlConfig;
const core_1 = require("@mikro-orm/core");
const MsSqlDriver_1 = require("./MsSqlDriver");
/**
 * @inheritDoc
 */
class MsSqlMikroORM extends core_1.MikroORM {
    static DRIVER = MsSqlDriver_1.MsSqlDriver;
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
exports.MsSqlMikroORM = MsSqlMikroORM;
/* istanbul ignore next */
function defineMsSqlConfig(options) {
    return (0, core_1.defineConfig)({ driver: MsSqlDriver_1.MsSqlDriver, ...options });
}
