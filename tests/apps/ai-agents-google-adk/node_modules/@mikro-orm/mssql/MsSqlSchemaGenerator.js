"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MsSqlSchemaGenerator = void 0;
const knex_1 = require("@mikro-orm/knex");
class MsSqlSchemaGenerator extends knex_1.SchemaGenerator {
    static register(orm) {
        orm.config.registerExtension('@mikro-orm/schema-generator', () => new MsSqlSchemaGenerator(orm.em));
    }
    async clearDatabase(options) {
        // truncate by default, so no value is considered as true
        /* istanbul ignore if */
        if (options?.truncate === false) {
            return super.clearDatabase(options);
        }
        // https://stackoverflow.com/questions/253849/cannot-truncate-table-because-it-is-being-referenced-by-a-foreign-key-constraint
        for (const meta of this.getOrderedMetadata(options?.schema).reverse()) {
            const res = await this.driver.nativeDelete(meta.className, {}, options);
            if (meta.getPrimaryProps().some(pk => pk.autoincrement)) {
                const tableName = this.driver.getTableName(meta, { schema: options?.schema }, false);
                await this.execute(`dbcc checkident ('${tableName}', reseed, ${res.affectedRows > 0 ? 0 : 1})`, {
                    ctx: this.em?.getTransactionContext(),
                });
            }
        }
        this.clearIdentityMap();
    }
    async getDropSchemaSQL(options = {}) {
        return super.getDropSchemaSQL({ dropForeignKeys: true, ...options });
    }
}
exports.MsSqlSchemaGenerator = MsSqlSchemaGenerator;
