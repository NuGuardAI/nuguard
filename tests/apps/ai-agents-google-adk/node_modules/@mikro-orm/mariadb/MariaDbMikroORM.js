"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MariaDbMikroORM = void 0;
exports.defineMariaDbConfig = defineMariaDbConfig;
const core_1 = require("@mikro-orm/core");
const MariaDbDriver_1 = require("./MariaDbDriver");
/**
 * @inheritDoc
 */
class MariaDbMikroORM extends core_1.MikroORM {
    static DRIVER = MariaDbDriver_1.MariaDbDriver;
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
exports.MariaDbMikroORM = MariaDbMikroORM;
/* istanbul ignore next */
function defineMariaDbConfig(options) {
    return (0, core_1.defineConfig)({ driver: MariaDbDriver_1.MariaDbDriver, ...options });
}
