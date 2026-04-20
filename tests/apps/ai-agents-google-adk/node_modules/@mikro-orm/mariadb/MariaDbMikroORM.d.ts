import { MikroORM, type Options, type IDatabaseDriver, type EntityManager, type EntityManagerType } from '@mikro-orm/core';
import { MariaDbDriver } from './MariaDbDriver';
import type { SqlEntityManager } from '@mikro-orm/knex';
/**
 * @inheritDoc
 */
export declare class MariaDbMikroORM<EM extends EntityManager = SqlEntityManager> extends MikroORM<MariaDbDriver, EM> {
    private static DRIVER;
    /**
     * @inheritDoc
     */
    static init<D extends IDatabaseDriver = MariaDbDriver, EM extends EntityManager = D[typeof EntityManagerType] & EntityManager>(options?: Options<D, EM>): Promise<MikroORM<D, EM>>;
    /**
     * @inheritDoc
     */
    static initSync<D extends IDatabaseDriver = MariaDbDriver, EM extends EntityManager = D[typeof EntityManagerType] & EntityManager>(options: Options<D, EM>): MikroORM<D, EM>;
}
export type MariaDbOptions = Options<MariaDbDriver>;
export declare function defineMariaDbConfig(options: MariaDbOptions): Options<MariaDbDriver, SqlEntityManager<MariaDbDriver> & EntityManager<IDatabaseDriver<import("@mikro-orm/core").Connection>>>;
