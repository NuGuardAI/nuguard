import { type Dictionary, type EntityMetadata, type EntityProperty, type Primary, type Transaction } from '@mikro-orm/core';
import { type AbstractSqlDriver } from './AbstractSqlDriver';
export declare class PivotCollectionPersister<Entity extends object> {
    private readonly meta;
    private readonly driver;
    private readonly ctx?;
    private readonly schema?;
    private readonly loggerContext?;
    private readonly inserts;
    private readonly upserts;
    private readonly deletes;
    private readonly batchSize;
    private order;
    constructor(meta: EntityMetadata<Entity>, driver: AbstractSqlDriver, ctx?: Transaction | undefined, schema?: string | undefined, loggerContext?: Dictionary | undefined);
    enqueueUpdate(prop: EntityProperty<Entity>, insertDiff: Primary<Entity>[][], deleteDiff: Primary<Entity>[][] | boolean, pks: Primary<Entity>[], isInitialized?: boolean): void;
    private enqueueInsert;
    private enqueueUpsert;
    private createInsertStatement;
    private enqueueDelete;
    private collectStatements;
    execute(): Promise<void>;
}
