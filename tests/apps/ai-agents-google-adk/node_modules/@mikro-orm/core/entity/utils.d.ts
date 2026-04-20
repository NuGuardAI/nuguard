import type { EntityMetadata, PopulateOptions } from '../typings';
import { LoadStrategy, ReferenceKind } from '../enums';
/**
 * @internal
 */
export declare function expandDotPaths<Entity>(meta: EntityMetadata<Entity>, populate?: readonly (string | PopulateOptions<Entity>)[], normalized?: boolean): PopulateOptions<Entity>[];
/**
 * Returns the loading strategy based on the provided hint.
 * If `BALANCED` strategy is used, it will return JOINED if the property is a to-one relation.
 * @internal
 */
export declare function getLoadingStrategy(strategy: LoadStrategy | `${LoadStrategy}`, kind: ReferenceKind): LoadStrategy.SELECT_IN | LoadStrategy.JOINED;
