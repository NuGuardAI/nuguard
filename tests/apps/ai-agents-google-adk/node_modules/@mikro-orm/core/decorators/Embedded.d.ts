import type { AnyEntity, EntityName } from '../typings';
import type { PropertyOptions } from './Property';
export declare function Embedded<Owner extends object, Target>(type?: EmbeddedOptions<Owner, Target> | (() => EntityName<Target> | EntityName<Target>[]), options?: EmbeddedOptions<Owner, Target>): (target: AnyEntity, propertyName: string) => any;
/** With `absolute` the prefix is set at the root of the entity (regardless of the nesting level) */
export type EmbeddedPrefixMode = 'absolute' | 'relative';
export interface EmbeddedOptions<Owner, Target> extends PropertyOptions<Owner> {
    entity?: string | (() => EntityName<Target> | EntityName<Target>[]);
    prefix?: string | boolean;
    prefixMode?: EmbeddedPrefixMode;
    object?: boolean;
    array?: boolean;
}
