import type { EntityData, EntityMetadata, FilterQuery } from '../typings';
import type { UpsertOptions } from '../drivers/IDatabaseDriver';
import type { RawQueryFragment } from '../utils/RawQueryFragment';
/** @internal */
export declare function getOnConflictFields<T>(meta: EntityMetadata<T> | undefined, data: EntityData<T>, uniqueFields: (keyof T)[] | RawQueryFragment, options: UpsertOptions<T>): (keyof T)[];
/** @internal */
export declare function getOnConflictReturningFields<T, P extends string>(meta: EntityMetadata<T> | undefined, data: EntityData<T>, uniqueFields: (keyof T)[] | RawQueryFragment, options: UpsertOptions<T, P>): (keyof T)[] | '*';
/** @internal */
export declare function getWhereCondition<T extends object>(meta: EntityMetadata<T>, onConflictFields: (keyof T)[] | RawQueryFragment | undefined, data: EntityData<T>, where: FilterQuery<T>): {
    where: FilterQuery<T>;
    propIndex: number | false;
};
