import { CriteriaNode } from './CriteriaNode';
import type { ICriteriaNodeProcessOptions, IQueryBuilder } from '../typings';
/**
 * @internal
 */
export declare class ScalarCriteriaNode<T extends object> extends CriteriaNode<T> {
    process(qb: IQueryBuilder<T>, options?: ICriteriaNodeProcessOptions): any;
    willAutoJoin(qb: IQueryBuilder<T>, alias?: string, options?: ICriteriaNodeProcessOptions): boolean;
    private shouldJoin;
}
