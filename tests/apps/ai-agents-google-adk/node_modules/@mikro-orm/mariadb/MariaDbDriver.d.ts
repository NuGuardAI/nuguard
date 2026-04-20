import { type AnyEntity, type Configuration, type ConnectionType, type EntityDictionary, type LoggingOptions, type NativeInsertUpdateManyOptions, type QueryResult, type Transaction, type FilterQuery, type UpsertManyOptions } from '@mikro-orm/core';
import { AbstractSqlDriver, type Knex, type SqlEntityManager } from '@mikro-orm/knex';
import { MariaDbConnection } from './MariaDbConnection';
import { MariaDbPlatform } from './MariaDbPlatform';
import { MariaDbQueryBuilder } from './MariaDbQueryBuilder';
export declare class MariaDbDriver extends AbstractSqlDriver<MariaDbConnection, MariaDbPlatform> {
    protected autoIncrementIncrement?: number;
    constructor(config: Configuration);
    private getAutoIncrementIncrement;
    nativeInsertMany<T extends object>(entityName: string, data: EntityDictionary<T>[], options?: NativeInsertUpdateManyOptions<T>): Promise<QueryResult<T>>;
    nativeUpdateMany<T extends object>(entityName: string, where: FilterQuery<T>[], data: EntityDictionary<T>[], options?: NativeInsertUpdateManyOptions<T> & UpsertManyOptions<T>): Promise<QueryResult<T>>;
    createQueryBuilder<T extends AnyEntity<T>>(entityName: string, ctx?: Transaction<Knex.Transaction>, preferredConnectionType?: ConnectionType, convertCustomTypes?: boolean, loggerContext?: LoggingOptions, alias?: string, em?: SqlEntityManager): MariaDbQueryBuilder<T, any, any, any>;
}
