import type { Dictionary, EntityMetadata, EntityProperty, FilterDef, FilterQuery } from '../typings';
import type { Platform } from '../platforms';
import type { MetadataStorage } from '../metadata/MetadataStorage';
import type { FilterOptions } from '../drivers/IDatabaseDriver';
/** @internal */
export declare class QueryHelper {
    static readonly SUPPORTED_OPERATORS: string[];
    static processParams(params: unknown): any;
    static processObjectParams<T extends object>(params?: T): T;
    /**
     * converts `{ account: { $or: [ [Object], [Object] ] } }`
     * to `{ $or: [ { account: [Object] }, { account: [Object] } ] }`
     */
    static liftGroupOperators<T extends object>(where: Dictionary, meta: EntityMetadata<T>, metadata: MetadataStorage, key?: string): string | undefined;
    static inlinePrimaryKeyObjects<T extends object>(where: Dictionary, meta: EntityMetadata<T>, metadata: MetadataStorage, key?: string): boolean;
    static processWhere<T extends object>(options: ProcessWhereOptions<T>): FilterQuery<T>;
    static getActiveFilters(entityName: string, options: FilterOptions | undefined, filters: Dictionary<FilterDef>): FilterDef[];
    static mergePropertyFilters(propFilters: FilterOptions | undefined, options: FilterOptions | undefined): FilterOptions | undefined;
    static isFilterActive(entityName: string, filterName: string, filter: FilterDef, options: Dictionary<boolean | Dictionary>): boolean;
    static processCustomType<T extends object>(prop: EntityProperty<T>, cond: FilterQuery<T>, platform: Platform, key?: string, fromQuery?: boolean): FilterQuery<T>;
    private static isSupportedOperator;
    private static processJsonCondition;
    private static getValueType;
    static findProperty<T>(fieldName: string, options: ProcessWhereOptions<T>): EntityProperty<T> | undefined;
    /**
     * Converts entity references for composite FK properties into flat arrays
     * of correctly-ordered join column values, before processParams flattens them
     * incorrectly due to shared FK columns.
     */
    private static convertCompositeEntityRefs;
    /**
     * Extracts values for a FK's join columns from an entity by traversing the FK chain.
     * Handles shared FK columns (e.g., tenant_id referenced by multiple FKs) correctly.
     */
    private static extractJoinColumnValues;
    /**
     * Extracts the value for a specific column from an entity by finding which PK property
     * owns that column and recursively traversing FK references.
     */
    private static extractColumnValue;
}
interface ProcessWhereOptions<T> {
    where: FilterQuery<T>;
    entityName: string;
    metadata: MetadataStorage;
    platform: Platform;
    aliased?: boolean;
    aliasMap?: Dictionary<string>;
    convertCustomTypes?: boolean;
    root?: boolean;
    type?: 'where' | 'orderBy';
}
export {};
