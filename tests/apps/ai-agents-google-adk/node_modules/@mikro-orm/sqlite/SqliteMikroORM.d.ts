import { MikroORM, type Options, type IDatabaseDriver, type EntityManager, type EntityManagerType } from '@mikro-orm/core';
import { SqliteDriver } from './SqliteDriver';
import type { SqlEntityManager } from '@mikro-orm/knex';
/**
 * @inheritDoc
 */
export declare class SqliteMikroORM<EM extends EntityManager = SqlEntityManager> extends MikroORM<SqliteDriver, EM> {
    private static DRIVER;
    /**
     * @inheritDoc
     */
    static init<D extends IDatabaseDriver = SqliteDriver, EM extends EntityManager = D[typeof EntityManagerType] & EntityManager>(options?: Options<D, EM>): Promise<MikroORM<D, EM>>;
    /**
     * @inheritDoc
     */
    static initSync<D extends IDatabaseDriver = SqliteDriver, EM extends EntityManager = D[typeof EntityManagerType] & EntityManager>(options: Options<D, EM>): MikroORM<D, EM>;
}
export type SqliteOptions = Options<SqliteDriver>;
export declare function defineSqliteConfig(options: SqliteOptions): Options<SqliteDriver, SqlEntityManager<SqliteDriver> & EntityManager<IDatabaseDriver<import("@mikro-orm/core").Connection>>>;
