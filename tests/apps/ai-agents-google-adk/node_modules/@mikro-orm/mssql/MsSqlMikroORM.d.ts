import { MikroORM, type Options, type IDatabaseDriver, type EntityManager, type EntityManagerType } from '@mikro-orm/core';
import { MsSqlDriver } from './MsSqlDriver';
import type { SqlEntityManager } from '@mikro-orm/knex';
/**
 * @inheritDoc
 */
export declare class MsSqlMikroORM<EM extends EntityManager = SqlEntityManager> extends MikroORM<MsSqlDriver, EM> {
    private static DRIVER;
    /**
     * @inheritDoc
     */
    static init<D extends IDatabaseDriver = MsSqlDriver, EM extends EntityManager = D[typeof EntityManagerType] & EntityManager>(options?: Options<D, EM>): Promise<MikroORM<D, EM>>;
    /**
     * @inheritDoc
     */
    static initSync<D extends IDatabaseDriver = MsSqlDriver, EM extends EntityManager = D[typeof EntityManagerType] & EntityManager>(options: Options<D, EM>): MikroORM<D, EM>;
}
export type MsSqlOptions = Options<MsSqlDriver>;
export declare function defineMsSqlConfig(options: MsSqlOptions): Options<MsSqlDriver, SqlEntityManager<MsSqlDriver> & EntityManager<IDatabaseDriver<import("@mikro-orm/core").Connection>>>;
