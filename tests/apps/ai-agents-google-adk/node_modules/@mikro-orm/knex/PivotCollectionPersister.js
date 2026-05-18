"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.PivotCollectionPersister = void 0;
class InsertStatement {
    keys;
    data;
    order;
    constructor(keys, data, order) {
        this.keys = keys;
        this.data = data;
        this.order = order;
    }
    getHash() {
        return JSON.stringify(this.data);
    }
    getData() {
        const data = {};
        this.keys.forEach((key, idx) => data[key] = this.data[idx]);
        return data;
    }
}
class DeleteStatement {
    keys;
    cond;
    constructor(keys, cond) {
        this.keys = keys;
        this.cond = cond;
    }
    getHash() {
        return JSON.stringify(this.cond);
    }
    getCondition() {
        const cond = {};
        this.keys.forEach((key, idx) => cond[key] = this.cond[idx]);
        return cond;
    }
}
class PivotCollectionPersister {
    meta;
    driver;
    ctx;
    schema;
    loggerContext;
    inserts = new Map();
    upserts = new Map();
    deletes = new Map();
    batchSize;
    order = 0;
    constructor(meta, driver, ctx, schema, loggerContext) {
        this.meta = meta;
        this.driver = driver;
        this.ctx = ctx;
        this.schema = schema;
        this.loggerContext = loggerContext;
        this.batchSize = this.driver.config.get('batchSize');
    }
    enqueueUpdate(prop, insertDiff, deleteDiff, pks, isInitialized = true) {
        if (insertDiff.length) {
            if (isInitialized) {
                this.enqueueInsert(prop, insertDiff, pks);
            }
            else {
                this.enqueueUpsert(prop, insertDiff, pks);
            }
        }
        if (deleteDiff === true || (Array.isArray(deleteDiff) && deleteDiff.length)) {
            this.enqueueDelete(prop, deleteDiff, pks);
        }
    }
    enqueueInsert(prop, insertDiff, pks) {
        for (const fks of insertDiff) {
            const statement = this.createInsertStatement(prop, fks, pks);
            const hash = statement.getHash();
            if (prop.owner || !this.inserts.has(hash)) {
                this.inserts.set(hash, statement);
            }
        }
    }
    enqueueUpsert(prop, insertDiff, pks) {
        for (const fks of insertDiff) {
            const statement = this.createInsertStatement(prop, fks, pks);
            const hash = statement.getHash();
            if (prop.owner || !this.upserts.has(hash)) {
                this.upserts.set(hash, statement);
            }
        }
    }
    createInsertStatement(prop, fks, pks) {
        const data = prop.owner ? [...fks, ...pks] : [...pks, ...fks];
        const keys = prop.owner
            ? [...prop.inverseJoinColumns, ...prop.joinColumns]
            : [...prop.joinColumns, ...prop.inverseJoinColumns];
        return new InsertStatement(keys, data, this.order++);
    }
    enqueueDelete(prop, deleteDiff, pks) {
        if (deleteDiff === true) {
            const statement = new DeleteStatement(prop.joinColumns, pks);
            this.deletes.set(statement.getHash(), statement);
            return;
        }
        for (const fks of deleteDiff) {
            const data = prop.owner ? [...fks, ...pks] : [...pks, ...fks];
            const keys = prop.owner
                ? [...prop.inverseJoinColumns, ...prop.joinColumns]
                : [...prop.joinColumns, ...prop.inverseJoinColumns];
            const statement = new DeleteStatement(keys, data);
            this.deletes.set(statement.getHash(), statement);
        }
    }
    collectStatements(statements) {
        const items = [];
        for (const statement of statements.values()) {
            items[statement.order] = statement.getData();
        }
        return items.filter(Boolean);
    }
    async execute() {
        if (this.deletes.size > 0) {
            const deletes = [...this.deletes.values()];
            for (let i = 0; i < deletes.length; i += this.batchSize) {
                const chunk = deletes.slice(i, i + this.batchSize);
                const cond = { $or: [] };
                for (const item of chunk) {
                    cond.$or.push(item.getCondition());
                }
                await this.driver.nativeDelete(this.meta.className, cond, {
                    ctx: this.ctx,
                    schema: this.schema,
                    loggerContext: this.loggerContext,
                });
            }
        }
        if (this.inserts.size > 0) {
            const filtered = this.collectStatements(this.inserts);
            for (let i = 0; i < filtered.length; i += this.batchSize) {
                const chunk = filtered.slice(i, i + this.batchSize);
                await this.driver.nativeInsertMany(this.meta.className, chunk, {
                    ctx: this.ctx,
                    schema: this.schema,
                    convertCustomTypes: false,
                    processCollections: false,
                    loggerContext: this.loggerContext,
                });
            }
        }
        if (this.upserts.size > 0) {
            const filtered = this.collectStatements(this.upserts);
            for (let i = 0; i < filtered.length; i += this.batchSize) {
                const chunk = filtered.slice(i, i + this.batchSize);
                await this.driver.nativeUpdateMany(this.meta.className, [], chunk, {
                    ctx: this.ctx,
                    schema: this.schema,
                    convertCustomTypes: false,
                    processCollections: false,
                    upsert: true,
                    onConflictAction: 'ignore',
                    loggerContext: this.loggerContext,
                });
            }
        }
    }
}
exports.PivotCollectionPersister = PivotCollectionPersister;
