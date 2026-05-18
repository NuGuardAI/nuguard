import { type AnyEntity, type EntityMetadata } from '@mikro-orm/core';
import { QueryBuilder } from '@mikro-orm/knex';
/**
 * @inheritDoc
 */
export declare class MariaDbQueryBuilder<Entity extends object = AnyEntity, RootAlias extends string = never, Hint extends string = never, Context extends object = never> extends QueryBuilder<Entity, RootAlias, Hint, Context> {
    protected wrapPaginateSubQuery(meta: EntityMetadata): void;
}
