import type { AnyEntity, FormulaCallback } from '../typings';
import type { PropertyOptions } from './Property';
export declare function Formula<T extends object>(formula: string | FormulaCallback<T>, options?: FormulaOptions<T>): (target: AnyEntity, propertyName: string) => any;
export interface FormulaOptions<T> extends PropertyOptions<T> {
}
